// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// PS/2 receiver and frame counter for the DOOM side project (docs/PHASES.md, step D3).
//
// The Genesys2 "USB HID" connector: a PIC24 turns a USB keyboard into a PS/2 device on ps2_clk / ps2_data
// (FPGA pins AD23 / AE20, pull-ups; Digilent Genesys-2 master XDC). Receive only (host-to-device commands such as
// the keyboard LEDs are not sent).
//
// PS/2 frame (device to host, data valid on the falling clock edge): start bit 0, 8 data bits LSB first, odd
// parity, stop bit 1. Both lines are synchronised (SYNC flops) and filtered (FILT samples equal). A frame whose
// start, parity or stop bit is wrong is dropped and counted; a frame left unfinished for TIMEOUT clocks is
// abandoned (resynchronisation after a glitch). Good bytes enter a FIFO of DEPTH entries; a byte arriving with the
// FIFO full is dropped and sets OVF.
//
// The frame counter counts the starts of the VGA vertical sync pulse (vsync_i, active level VSYNC_ACTIVE): software
// flipping axi_vga's start address knows the new buffer is on screen once the counter has moved on.
//
// Registers (32-bit, word offsets; reads have no side effect except DATA):
//   0x0 DATA    read: bit 8 = a byte was available, [7:0] the byte (popped); 0 when empty
//   0x4 STATUS  [7:0] bytes in the FIFO, bit 8 OVF (sticky), [23:16] frame errors (saturating); write: bit 8 = 1
//               clears OVF, bit 16 = 1 clears the error count
//   0x8 FRAMES  vertical sync pulses seen (wraps)
//   0xC ID      0x5032_0001 ("P2", version 1)
// Polled: no interrupt (DOOM reads the keyboard once per frame).

module chs_ps2 #(
  parameter int unsigned DEPTH        = 64,         // FIFO entries (power of 2)
  parameter int unsigned SYNC         = 2,
  parameter int unsigned FILT         = 8,          // clk cycles a line must hold a value (glitch filter)
  parameter int unsigned TIMEOUT      = 100_000,    // clk cycles without a clock edge that abandon a frame (2 ms at 50 MHz)
  parameter bit          VSYNC_ACTIVE = 1'b0
) (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        ps2_clk_i,
  input  logic        ps2_data_i,
  input  logic        vsync_i,
  // register port: one access per req_i cycle, answered in the same cycle
  input  logic        req_i,
  input  logic        we_i,
  input  logic [3:2]  addr_i,
  input  logic [31:0] wdata_i,
  output logic [31:0] rdata_o
);

  localparam int unsigned AW = $clog2(DEPTH);
  localparam int unsigned FW = $clog2(FILT + 1);
  localparam int unsigned TW = $clog2(TIMEOUT + 1);

  // ---------------------------------------------------------------- synchronise and filter
  logic [SYNC-1:0] ck_s, dt_s, vs_s;
  logic            ck_f, dt_f;          // filtered levels
  logic [FW-1:0]   ck_n, dt_n;          // cycles the synchronised value differed from the filtered one

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      ck_s <= '1;
      dt_s <= '1;
      vs_s <= {SYNC{~VSYNC_ACTIVE}};
      ck_f <= 1'b1;
      dt_f <= 1'b1;
      ck_n <= '0;
      dt_n <= '0;
    end else begin
      ck_s <= {ck_s[SYNC-2:0], ps2_clk_i};
      dt_s <= {dt_s[SYNC-2:0], ps2_data_i};
      vs_s <= {vs_s[SYNC-2:0], vsync_i};
      if (ck_s[SYNC-1] == ck_f)          ck_n <= '0;
      else if (ck_n == FW'(FILT - 1)) begin
        ck_f <= ck_s[SYNC-1];
        ck_n <= '0;
      end else                           ck_n <= ck_n + 1'b1;
      if (dt_s[SYNC-1] == dt_f)          dt_n <= '0;
      else if (dt_n == FW'(FILT - 1)) begin
        dt_f <= dt_s[SYNC-1];
        dt_n <= '0;
      end else                           dt_n <= dt_n + 1'b1;
    end
  end

  logic ck_q, fall;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) ck_q <= 1'b1;
    else         ck_q <= ck_f;
  end
  assign fall = ck_q & ~ck_f;

  // ---------------------------------------------------------------- frame
  logic [3:0]    bit_q;       // bits received in this frame (0: idle)
  logic [8:0]    sh_q;        // data bits then parity, shifted in from the top
  logic [TW-1:0] to_q;
  logic          byte_ok, byte_bad;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      bit_q <= '0;
      sh_q  <= '0;
      to_q  <= '0;
    end else if (fall) begin
      to_q <= '0;
      if (bit_q == 4'd0) begin
        if (!dt_f) bit_q <= 4'd1;                 // start bit (a high "start" is ignored)
      end else if (bit_q == 4'd10) begin
        bit_q <= '0;                              // stop bit: byte_ok / byte_bad below
      end else begin
        sh_q  <= {dt_f, sh_q[8:1]};
        bit_q <= bit_q + 4'd1;
      end
    end else if (bit_q != 4'd0) begin
      if (to_q == TW'(TIMEOUT)) begin
        bit_q <= '0;                              // abandoned frame
        to_q  <= '0;
      end else begin
        to_q <= to_q + 1'b1;
      end
    end
  end

  // on the stop bit's falling edge: odd parity over data + parity, stop bit 1
  assign byte_ok  = fall && bit_q == 4'd10 && dt_f && ^sh_q;
  assign byte_bad = fall && bit_q == 4'd10 && !(dt_f && ^sh_q);

  // ---------------------------------------------------------------- FIFO
  logic [7:0]    mem_q [DEPTH];
  logic [AW-1:0] wp_q, rp_q;
  logic [AW:0]   cnt_q;
  logic          ovf_q, pop, push;
  logic [7:0]    err_q;

  assign push = byte_ok && cnt_q != (AW + 1)'(DEPTH);
  assign pop  = req_i && !we_i && addr_i == 2'd0 && cnt_q != '0;

  always_ff @(posedge clk_i) begin
    if (push) mem_q[wp_q] <= sh_q[7:0];
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      wp_q  <= '0;
      rp_q  <= '0;
      cnt_q <= '0;
      ovf_q <= 1'b0;
      err_q <= '0;
    end else begin
      if (push) wp_q <= wp_q + 1'b1;
      if (pop)  rp_q <= rp_q + 1'b1;
      cnt_q <= cnt_q + (AW + 1)'(push) - (AW + 1)'(pop);
      if (byte_ok && !push)                                                   ovf_q <= 1'b1;
      else if (req_i && we_i && addr_i == 2'd1 && wdata_i[8])                 ovf_q <= 1'b0;
      if (req_i && we_i && addr_i == 2'd1 && wdata_i[16])                     err_q <= '0;
      else if (byte_bad && err_q != 8'hFF)                                    err_q <= err_q + 1'b1;
    end
  end

  // ---------------------------------------------------------------- frame counter
  logic        vs_q;
  logic [31:0] frames_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      vs_q     <= ~VSYNC_ACTIVE;
      frames_q <= '0;
    end else begin
      vs_q <= vs_s[SYNC-1];
      if (vs_s[SYNC-1] == VSYNC_ACTIVE && vs_q != VSYNC_ACTIVE) frames_q <= frames_q + 1'b1;
    end
  end

  // ---------------------------------------------------------------- read data
  always_comb begin
    unique case (addr_i)
      2'd0:    rdata_o = (cnt_q != '0) ? {23'd0, 1'b1, mem_q[rp_q]} : '0;
      2'd1:    rdata_o = {8'd0, err_q, 7'd0, ovf_q, 8'(cnt_q)};
      2'd2:    rdata_o = frames_q;
      default: rdata_o = 32'h5032_0001;
    endcase
  end

  logic unused;
  assign unused = ^{wdata_i[31:17], wdata_i[15:9], wdata_i[7:0]};

endmodule
