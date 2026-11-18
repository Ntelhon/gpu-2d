// Copyright 2022 ETH Zurich and University of Bologna.
// Solderpad Hardware License, Version 0.51, see LICENSE for details.
// SPDX-License-Identifier: SHL-0.51
//
// Nicole Narr <narrn@student.ethz.ch>
// Christopher Reinwardt <creinwar@student.ethz.ch>
// Thomas Benz <tbenz@iis.ee.ethz.ch>
//
// MODIFIED for the G2D project (2026-11-05, fpga/cheshire/axi_vga_g2d): per-frame re-synchronisation of the fetcher
// at vertical sync (axi_vga.sv) with a fetcher hold input / idle output (axi_vga_fetcher.sv). Based on axi_vga
// v0.1.3 (3718b99). Changes are marked "G2D".
// 2026-11-06: prefetch. The fetcher used to request the next burst only when the last beat of the previous one reached
// the head of the buffer (~3 beats left), so every burst boundary had to cover the full memory latency in ~12 pixels
// and the buffer never filled. Now a burst is requested as soon as the buffer has room for a whole burst; the burst
// length is capped at BufferDepth / 4 beats so that at least 3/4 of the buffer runs ahead of the scan-out.

// 2026-11-11 (v1.8, Q-FPGA-11): real bursts. The burst splitter (one AR per 8-byte beat, each arbitrated separately
// against other managers' whole bursts in the SoC crossbar), its credit counter and the stream_join are removed: the
// fetcher's AR goes out as one burst of at most MaxBurstBeats; the committed-beat accounting guarantees buffer room
// for every accepted burst. The R buffer is a block-RAM FIFO (g2d_fifo_ram). Debug counters (underruns, minimum fill,
// worst read latency) in a register window at offset 0x40 above the vendor registers.

`include "common_cells/registers.svh"

/// Simple VGA IP capable of drawing frames from an external framebuffer.
module axi_vga #(
  parameter int unsigned RedWidth     = 5,
  parameter int unsigned GreenWidth   = 6,
  parameter int unsigned BlueWidth    = 5,
  parameter int unsigned HCountWidth  = 32,
  parameter int unsigned VCountWidth  = 32,
  parameter int unsigned AXIAddrWidth = 64,
  parameter int unsigned AXIDataWidth = 64,
  parameter int unsigned AXIIdWidth   = 2,
  parameter int unsigned AXIUserWidth = 1,
  parameter int unsigned AXIStrbWidth = 8,
  parameter int unsigned BufferDepth  = 16,
  parameter int unsigned MaxReadTxns  = 24,   // G2D: unused since v1.8 (no burst splitter); kept for Cheshire
  parameter int unsigned MaxBurstBeats = 64,  // G2D v1.8: longest scan-out burst (also capped at BufferDepth / 4)
  parameter type axi_req_t            = logic,
  parameter type axi_resp_t           = logic,
  parameter type axi_r_chan_t         = logic,
  parameter type reg_req_t            = logic,
  parameter type reg_resp_t           = logic
)(
  input logic                     clk_i,
  input logic                     rst_ni,

  input logic                     test_mode_en_i,

  // Regbus config ports
  input  reg_req_t                reg_req_i,
  output reg_resp_t               reg_rsp_o,

  // AXI Data ports
  output axi_req_t                axi_req_o,
  input  axi_resp_t               axi_resp_i,

  // VGA interface
  output logic                    hsync_o,
  output logic                    vsync_o,
  output logic [RedWidth-1:0]     red_o,
  output logic [GreenWidth-1:0]   green_o,
  output logic [BlueWidth-1:0]    blue_o
);

  logic [7:0] clk_div;
  logic [7:0] clk_cnt_d, clk_cnt_q;

  axi_vga_reg_pkg::axi_vga_reg2hw_t reg2hw;

  axi_req_t  axi_req;    // fetcher side
  axi_resp_t axi_resp;
  logic      fetch_en, fetch_hold, fetch_idle;   // G2D: frame re-sync

  // G2D: prefetch. committed = beats of the bursts the fetcher has issued that are not yet popped from the buffer
  // (in flight or buffered). A new burst may be requested while committed + BurstCap + 1 <= BufferDepth, so every
  // accepted burst has buffer room for all its beats and R never back-pressures the interconnect.
  if (BufferDepth < 4) begin : g_bad_depth   // G2D v1.8: e.g. 1024 wrapped to 0 by a 10-bit SoC config field
    $error("axi_vga (G2D fork): BufferDepth must be at least 4");
  end
  localparam int unsigned BurstBeats0 = (BufferDepth / 4 < MaxBurstBeats) ? BufferDepth / 4 : MaxBurstBeats;
  localparam int unsigned BurstBeats  = (BurstBeats0 > 256) ? 256 : (BurstBeats0 < 1) ? 1 : BurstBeats0;
  localparam int unsigned BurstCap    = BurstBeats - 1;   // AXI len
  localparam int unsigned CommitWidth = $clog2(BufferDepth + 32'd1) + 1;
  localparam int unsigned TsDepth     = 32;               // outstanding bursts timed (debug latency counter)
  logic [CommitWidth-1:0] committed_q, committed_d;
  logic [7:0]             burst_len_eff;
  logic                   fetch_room;
  logic [$clog2(TsDepth)-1:0] ts_usage;
  logic                   ts_full;
  assign burst_len_eff = (reg2hw.burst_len.q > 8'(BurstCap)) ? 8'(BurstCap) : reg2hw.burst_len.q;
  assign fetch_room    = ((32'(committed_q) + BurstCap + 1) <= BufferDepth) && !ts_full &&
                         (32'(ts_usage) < TsDepth - 2);

  logic [RedWidth-1:0]   red;
  logic [GreenWidth-1:0] green;
  logic [BlueWidth-1:0]  blue;
  logic valid, ready;

  // Clock divider constant
  assign clk_div = |reg2hw.clk_div.q ? reg2hw.clk_div.q : 1;

  // Cycle counter to scale the incoming clock
  assign clk_cnt_d = (clk_cnt_q < (clk_div-1)) ? clk_cnt_q + 8'b0000_0001 : 8'b0;

  // G2D v1.8: offsets 0x00..0x3F go to the vendor register file (it decodes only address bits 5:0, so anything above
  // would alias), 0x40..0x7F are the debug counters below, the rest of the 4 KB window answers with an error.
  reg_req_t  vreg_req;
  reg_resp_t vreg_rsp;
  logic      dbg_sel;
  logic [31:0] dbg_rdata;
  logic      dbg_err;
  assign dbg_sel = (reg_req_i.addr[11:6] != '0);
  always_comb begin
    vreg_req       = reg_req_i;
    vreg_req.valid = reg_req_i.valid & ~dbg_sel;
    reg_rsp_o      = vreg_rsp;
    if (dbg_sel) begin
      reg_rsp_o.ready = 1'b1;
      reg_rsp_o.rdata = dbg_rdata;
      reg_rsp_o.error = dbg_err;
    end
  end

  // Regbus register interface
  axi_vga_reg_top #(
    .reg_req_t      ( reg_req_t           ),
    .reg_rsp_t      ( reg_resp_t          ),
    .AW             ( 6                   )
  ) i_axi_vga_register_file (
    .clk_i,
    .rst_ni,
    .reg_req_i      ( vreg_req            ),
    .reg_rsp_o      ( vreg_rsp            ),
    // To HW
    .reg2hw         ( reg2hw              ), // Write
    // Config
    .devmode_i      ( '1                  )  // Explicit error for unmapped register access
  );

  // FSM managing the VGA signals
  axi_vga_timing_fsm #(
    .RedWidth       ( RedWidth            ),
    .GreenWidth     ( GreenWidth          ),
    .BlueWidth      ( BlueWidth           ),
    .HCountWidth    ( HCountWidth         ),
    .VCountWidth    ( VCountWidth         )
  ) i_axi_vga_timing_fsm (
    .clk_i,
    .rst_ni,

    .fsm_en_i       ( clk_cnt_q == 0      ),
    .reg2hw_i       ( reg2hw              ),

    // Data input
    .red_i          ( red                 ),
    .green_i        ( green               ),
    .blue_i         ( blue                ),
    .valid_i        ( valid               ),
    .ready_o        ( ready               ),

    // VGA interface
    .hsync_o,
    .vsync_o,
    .red_o,
    .green_o,
    .blue_o
  );

  axi_vga_fetcher #(
    .RedWidth       ( RedWidth            ),
    .GreenWidth     ( GreenWidth          ),
    .BlueWidth      ( BlueWidth           ),
    .AXIAddrWidth   ( AXIAddrWidth        ),
    .AXIDataWidth   ( AXIDataWidth        ),
    .AXIStrbWidth   ( AXIStrbWidth        ),
    .axi_req_t      ( axi_req_t           ),
    .axi_resp_t     ( axi_resp_t          )
  ) i_axi_vga_fetcher (
    .clk_i,
    .rst_ni,
    .enable_i       ( fetch_en            ),   // G2D: off while the frame re-sync drains
    .hold_i         ( fetch_hold          ),   // G2D
    .idle_o         ( fetch_idle          ),   // G2D
    .room_i         ( fetch_room          ),   // G2D: prefetch

    .axi_req_o      ( axi_req             ),
    .axi_resp_i     ( axi_resp            ),

    .start_addr_i   ( {reg2hw.start_addr_high.q, reg2hw.start_addr_low.q}),
    .frame_size_i   ( reg2hw.frame_size.q ),
    .burst_len_i    ( burst_len_eff       ),   // G2D: capped at BurstCap
    .red_o          ( red                 ),
    .green_o        ( green               ),
    .blue_o         ( blue                ),
    .valid_o        ( valid               ),
    .ready_i        ( ready               )
  );

  logic        buf_in_ready, buf_out_valid;
  axi_r_chan_t buf_out;

  // G2D v1.8: requests go straight to the manager port (the fetcher never writes: AW / W / B stay idle)
  always_comb begin
    axi_req_o         = axi_req;
    axi_req_o.r_ready = buf_in_ready;
  end
  always_comb begin
    axi_resp          = axi_resp_i;
    axi_resp.r_valid  = buf_out_valid;
    axi_resp.r        = buf_out;
  end

  // R buffer: block RAM (g2d_fifo_ram: BufferDepth entries in the RAM, two in its output stage)
  g2d_fifo_ram #(
    .WIDTH ( $bits(axi_r_chan_t) ),
    .DEPTH ( BufferDepth         )
  ) i_rbuf (
    .clk_i,
    .rst_ni,
    .clr_i       ( 1'b0               ),
    .in_valid_i  ( axi_resp_i.r_valid ),
    .in_ready_o  ( buf_in_ready       ),
    .in_data_i   ( axi_resp_i.r       ),
    .out_valid_o ( buf_out_valid      ),
    .out_ready_i ( axi_req.r_ready    ),
    .out_data_o  ( buf_out            )
  );

  // registers
  `FF(clk_cnt_q, clk_cnt_d, '0)

  // G2D: per-frame re-synchronisation. The timing FSM shows a pixel every visible clock whether the fetcher has one
  // or not, so a late beat used to shift the rest of the picture for good (the picture slid under memory load).
  // At the start of every vertical sync: HOLD (no new burst request) until the fetcher has no request pending, then
  // DRAIN (fetcher disabled: it accepts and drops every beat, reloads START_ADDR) until no beat is requested or
  // buffered for 16 cycles, then RUN again from the first pixel of the (new) frame. Vertical sync + back porch
  // (~35 lines, ~1.1 ms at 640x480) leave ample time; a request that never becomes idle skips the re-sync (watchdog).
  typedef enum logic [1:0] {RS_RUN, RS_HOLD, RS_DRAIN} resync_state_t;
  resync_state_t rs_q, rs_d;
  logic          vs_act, vs_act_q, vs_start;
  logic [15:0]   rs_cnt_q, rs_cnt_d;
  assign vs_act     = (vsync_o == reg2hw.control.vsync_pol.q);   // vertical sync pulse active
  assign vs_start   = vs_act && !vs_act_q;
  assign fetch_en   = reg2hw.control.enable.q & (rs_q != RS_DRAIN);
  assign fetch_hold = (rs_q != RS_RUN);
  always_comb begin
    rs_d     = rs_q;
    rs_cnt_d = rs_cnt_q + 16'd1;
    unique case (rs_q)
      RS_RUN: begin
        rs_cnt_d = '0;
        if (reg2hw.control.enable.q && vs_start) rs_d = RS_HOLD;
      end
      RS_HOLD: begin
        if (fetch_idle) begin
          rs_d     = RS_DRAIN;
          rs_cnt_d = '0;
        end else if (&rs_cnt_q) begin
          rs_d = RS_RUN;                       // watchdog: no re-sync this frame
        end
      end
      default: begin                           // RS_DRAIN
        if (committed_q != '0 || axi_req.ar_valid || axi_resp_i.r_valid || buf_out_valid) rs_cnt_d = '0;
        if (rs_cnt_q == 16'd16) rs_d = RS_RUN;
      end
    endcase
    if (!reg2hw.control.enable.q) rs_d = RS_RUN;
  end
  // G2D: prefetch accounting (burst accepted: + len + 1; beat popped from the buffer: - 1)
  logic ar_hs, r_in, r_pop;
  assign ar_hs = axi_req.ar_valid && axi_resp_i.ar_ready;
  assign r_in  = axi_resp_i.r_valid && buf_in_ready;
  assign r_pop = buf_out_valid && axi_req.r_ready;
  always_comb begin
    committed_d = committed_q;
    if (ar_hs) committed_d = committed_d + CommitWidth'(axi_req.ar.len) + 1'b1;
    if (r_pop) committed_d = committed_d - 1'b1;
  end
  `FF(committed_q, committed_d, '0)
`ifndef SYNTHESIS
`ifndef VERILATOR_LINT
  a_committed_bound: assert property (@(posedge clk_i) disable iff (!rst_ni) 32'(committed_q) <= BufferDepth);
  a_no_r_backpressure: assert property (@(posedge clk_i) disable iff (!rst_ni) axi_resp_i.r_valid |-> buf_in_ready);
`endif
`endif

  `FF(rs_q, rs_d, RS_RUN)
  `FF(rs_cnt_q, rs_cnt_d, '0)
  `FF(vs_act_q, vs_act, 1'b0)

  // ---------------------------------------------------------------- G2D v1.8: debug counters (Q-FPGA-11)
  //   0x40 UNDERRUN_PX     visible pixels shown without data (black instead of the frame buffer); write: clear all
  //   0x44 UNDERRUN_FRAMES frames with at least one such pixel
  //   0x48 MIN_FILL        fewest beats in the buffer while a pixel was shown (reset to BufferDepth by the clear)
  //   0x4C MAX_LAT         most clock cycles from an accepted AR to the first beat of that burst
  //   0x50 FRAMES          vertical sync pulses
  //   0x54 INFO            [15:0] BufferDepth, [31:16] burst beats
  // Counters saturate. Frame 0 after enabling the output underruns by construction (no data yet): clear after it.
  localparam int unsigned FillWidth = $clog2(BufferDepth + 32'd3);
  logic [31:0] dbg_upx_q, dbg_ufr_q, dbg_lat_q, dbg_frames_q, cyc_q;
  logic [FillWidth-1:0] fill_q, dbg_minfill_q;
  logic        frame_bad_q, first_q, dbg_clr;
  logic [31:0] ts_out, lat_now;
  logic        underrun;
  assign dbg_clr  = reg_req_i.valid && reg_req_i.write && dbg_sel && reg_req_i.addr[5:2] == 4'h0;
  assign underrun = ready && !valid;
  assign lat_now  = cyc_q - ts_out;

  fifo_v3 #(
    .FALL_THROUGH ( 1'b0    ),
    .DATA_WIDTH   ( 32      ),
    .DEPTH        ( TsDepth )
  ) i_ar_ts (
    .clk_i,
    .rst_ni,
    .flush_i    ( 1'b0                       ),
    .testmode_i ( test_mode_en_i             ),
    .full_o     ( ts_full                    ),
    .empty_o    ( /* unused */               ),
    .usage_o    ( ts_usage                   ),
    .data_i     ( cyc_q                      ),
    .push_i     ( ar_hs                      ),
    .data_o     ( ts_out                     ),
    .pop_i      ( r_in && first_q            )
  );

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      dbg_upx_q     <= '0;
      dbg_ufr_q     <= '0;
      dbg_lat_q     <= '0;
      dbg_frames_q  <= '0;
      dbg_minfill_q <= FillWidth'(BufferDepth);
      cyc_q         <= '0;
      fill_q        <= '0;
      frame_bad_q   <= 1'b0;
      first_q       <= 1'b1;
    end else begin
      cyc_q  <= cyc_q + 32'd1;
      fill_q <= fill_q + FillWidth'(r_in) - FillWidth'(r_pop);
      if (r_in) first_q <= axi_resp_i.r.last;
      if (dbg_clr) begin
        dbg_upx_q     <= '0;
        dbg_ufr_q     <= '0;
        dbg_lat_q     <= '0;
        dbg_frames_q  <= '0;
        dbg_minfill_q <= FillWidth'(BufferDepth);
        frame_bad_q   <= 1'b0;
      end else begin
        if (underrun && !(&dbg_upx_q)) dbg_upx_q <= dbg_upx_q + 32'd1;
        if (underrun) frame_bad_q <= 1'b1;
        if (vs_start) begin
          if (!(&dbg_frames_q)) dbg_frames_q <= dbg_frames_q + 32'd1;
          if (frame_bad_q && !(&dbg_ufr_q)) dbg_ufr_q <= dbg_ufr_q + 32'd1;
          frame_bad_q <= 1'b0;
        end
        if (ready && fill_q < dbg_minfill_q) dbg_minfill_q <= fill_q;
        if (r_in && first_q && lat_now > dbg_lat_q) dbg_lat_q <= lat_now;
      end
    end
  end

  always_comb begin
    dbg_err   = 1'b0;
    dbg_rdata = '0;
    if (reg_req_i.addr[11:6] != 6'd1) begin
      dbg_err = 1'b1;
    end else begin
      unique case (reg_req_i.addr[5:2])
        4'h0: dbg_rdata = dbg_upx_q;
        4'h1: dbg_rdata = dbg_ufr_q;
        4'h2: dbg_rdata = 32'(dbg_minfill_q);
        4'h3: dbg_rdata = dbg_lat_q;
        4'h4: dbg_rdata = dbg_frames_q;
        4'h5: dbg_rdata = {16'(BurstBeats), 16'(BufferDepth)};
        default: dbg_err = 1'b1;
      endcase
    end
  end


  /////////////////////
  // Some assertions //
  /////////////////////

  // Ensure a pixel is always smaller than or equal to a word
  assert property (@(posedge clk_i) AXIDataWidth >= (RedWidth + GreenWidth + BlueWidth)) else begin
    $error("AXIDataWidth has to be larger than or equal to the pixel width");
    $stop();
  end

  // Ensure the word width is a multiple of the pixel width
  assert property (@(posedge clk_i)
      (AXIDataWidth % (RedWidth + GreenWidth + BlueWidth)) == 0) else begin
    $error("AXIDataWidth has to be a multiple of the pixel width");
    $stop();
  end

endmodule
