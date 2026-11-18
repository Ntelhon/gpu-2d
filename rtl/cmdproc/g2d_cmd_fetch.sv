// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Command fetch (arch.md §5.1). Pops a kick {addr, size}, checks it (rendering.md §13.2: SIZE == 0 or
// not a multiple of 8 -> CMD_DECODE; extent outside ADDR_WIN or wrapping -> ADDR_RANGE; ERR_CMD_ADDR =
// buffer address in both cases), then reads the buffer through its read client in MAX_BURST_BYTES-aligned
// chunks into an SRAM prefetch FIFO of 2 x MAX_BURST_BYTES (credit-based: a chunk is requested only when the
// FIFO has room for all of its beats, so the read client never back-pressures R).
//
// Output: one 32-bit command word per cycle with its byte address, the number of words left in the
// buffer including this one (`rem`), and `err` if its beat came back with an AXI error.
// The next kick is popped only after the parser has consumed the last word of the current buffer. A kick
// that fails its checks is popped only when the parser is also idle (parse_busy_i = 0: every command
// before it has executed, its draw issued), so its error is reported in command order (Phase 5 finding
// B-5.3); a valid kick is popped at once so its fetch overlaps the end of the previous buffer.
// stop_i: no kick pop, no new read request (outstanding data is still accepted). clr_i resets everything.
// pend_o: some requested beat has not left the prefetch FIFO yet (in flight or buffered).

module g2d_cmd_fetch #(
  parameter int unsigned DATA_W          = 64,
  parameter int unsigned MAX_BURST_BYTES = 256,
  localparam int unsigned DB = DATA_W / 8,
  localparam int unsigned WPB = DATA_W / 32
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  input  logic              stop_i,
  input  logic              parse_busy_i,   // the parser is still executing a command
  input  logic [31:0]       win_lo_i,
  input  logic [31:0]       win_hi_i,
  // kick queue
  input  logic              kick_valid_i,
  output logic              kick_ready_o,
  input  logic [31:0]       kick_addr_i,
  input  logic [23:0]       kick_size_i,
  // read client
  output logic              rd_req_valid_o,
  input  logic              rd_req_ready_i,
  output logic [31:0]       rd_req_addr_o,
  output logic [15:0]       rd_req_len_o,
  input  logic              rd_rsp_valid_i,
  output logic              rd_rsp_ready_o,
  input  logic [DATA_W-1:0] rd_rsp_data_i,
  input  logic [DB-1:0]     rd_rsp_strb_i,
  input  logic              rd_rsp_err_i,
  // word stream to the parser
  output logic              word_valid_o,
  input  logic              word_ready_i,
  output logic [31:0]       word_o,
  output logic [31:0]       word_addr_o,
  output logic [21:0]       word_rem_o,
  output logic              word_err_o,
  // errors and status
  output logic              err_decode_o,
  output logic              err_range_o,
  output logic [31:0]       err_addr_o,
  output logic [31:0]       err_cmd_addr_o,
  output logic              busy_o,
  output logic              pend_o          // command beats buffered or still in flight
);

  localparam int unsigned FIFO_BEATS = 2 * MAX_BURST_BYTES / DB;
  localparam int unsigned CB_W = $clog2(FIFO_BEATS + 1);
  localparam int unsigned MB_W = $clog2(MAX_BURST_BYTES);

  // ---------------------------------------------------------------- kick checks
  logic        run_q;          // a buffer is being fetched/consumed
  logic [32:0] kick_end;
  logic        bad_size, bad_range, kick_take;

  assign kick_end  = 33'(kick_addr_i) + 33'(kick_size_i) - 33'd1;
  assign bad_size  = (kick_size_i == '0) || (kick_size_i[2:0] != 3'b000);
  assign bad_range = kick_end[32] || kick_addr_i < win_lo_i || kick_end[31:0] > win_hi_i;
  assign kick_take = kick_valid_i & ~run_q & ~stop_i & (~(bad_size | bad_range) | ~parse_busy_i);
  assign kick_ready_o = kick_take;

  assign err_decode_o   = kick_take & bad_size;
  assign err_range_o    = kick_take & ~bad_size & bad_range;
  assign err_addr_o     = bad_size ? 32'd0 : kick_addr_i;
  assign err_cmd_addr_o = kick_addr_i;

  // ---------------------------------------------------------------- request issue
  logic [31:0]     req_addr_q;    // next byte to request
  logic [24:0]     req_left_q;    // bytes left to request
  logic [21:0]     out_left_q;    // words left to hand to the parser
  logic [31:0]     out_addr_q;
  logic [CB_W-1:0] credit_q;      // FIFO beats not yet promised to a request
  logic [MB_W:0]   to_bound;
  logic [24:0]     chunk;
  logic [CB_W-1:0] chunk_beats;
  logic [31:0]     chunk_end;
  logic            req_fire, beat_pop;

  assign to_bound    = (MB_W + 1)'(MAX_BURST_BYTES) - {1'b0, req_addr_q[MB_W-1:0]};
  assign chunk       = (req_left_q < 25'(to_bound)) ? req_left_q : 25'(to_bound);
  assign chunk_end   = req_addr_q + 32'(chunk) - 32'd1;
  // chunks start 8-byte aligned and stay inside one MAX_BURST_BYTES block
  assign chunk_beats = CB_W'((chunk_end[MB_W-1:$clog2(DB)] - req_addr_q[MB_W-1:$clog2(DB)])) + CB_W'(1);

  assign rd_req_valid_o = run_q & (req_left_q != '0) & ~stop_i & (credit_q >= chunk_beats);
  assign rd_req_addr_o  = req_addr_q;
  assign rd_req_len_o   = 16'(chunk - 25'd1);
  assign req_fire       = rd_req_valid_o & rd_req_ready_i;

  // ---------------------------------------------------------------- prefetch FIFO
  // entries: {data, per-word valid mask, err}
  logic                  bf_valid;
  logic [DATA_W+WPB:0]   bf_out;
  logic [WPB-1:0]        in_mask;
  logic                  bf_in_ready;

  for (genvar k = 0; k < WPB; k++) begin : g_mask
    assign in_mask[k] = rd_rsp_strb_i[4 * k];
  end

  assign rd_rsp_ready_o = bf_in_ready;  // never low in practice: requests are credit-limited

  // SRAM-backed (arch.md §6.4); holds at least FIFO_BEATS beats
  g2d_fifo_ram #(.WIDTH(DATA_W + WPB + 1), .DEPTH(FIFO_BEATS)) u_prefetch (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (rd_rsp_valid_i),
    .in_ready_o  (bf_in_ready),
    .in_data_i   ({rd_rsp_data_i, in_mask, rd_rsp_err_i}),
    .out_valid_o (bf_valid),
    .out_ready_i (beat_pop),
    .out_data_o  (bf_out)
  );

  // ---------------------------------------------------------------- word serializer
  logic [DATA_W-1:0] b_data;
  logic [WPB-1:0]    b_mask, rest_mask;
  logic              b_err;
  logic [WPB-1:0]    used_q;      // words of the head beat already handed out
  logic [$clog2(WPB)-1:0] sel;
  logic              found;

  assign {b_data, b_mask, b_err} = bf_out;
  assign rest_mask = b_mask & ~used_q;

  always_comb begin
    sel   = '0;
    found = 1'b0;
    for (int k = WPB - 1; k >= 0; k--) begin
      if (rest_mask[k]) begin
        sel   = k[$clog2(WPB)-1:0];
        found = 1'b1;
      end
    end
  end

  logic word_fire, last_in_beat;
  assign word_valid_o = bf_valid & found;
  assign word_o       = b_data[32 * sel +: 32];
  assign word_addr_o  = out_addr_q;
  assign word_rem_o   = out_left_q;
  assign word_err_o   = b_err;
  assign word_fire    = word_valid_o & word_ready_i;
  // the head beat is done when this word is its last valid one (or it has none)
  assign last_in_beat = ((rest_mask & ~(WPB'(1) << sel)) == '0);
  assign beat_pop     = bf_valid & (~found | (word_fire & last_in_beat));

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      run_q      <= 1'b0;
      req_addr_q <= '0;
      req_left_q <= '0;
      out_left_q <= '0;
      out_addr_q <= '0;
      credit_q   <= CB_W'(FIFO_BEATS);
      used_q     <= '0;
    end else if (clr_i) begin
      run_q      <= 1'b0;
      req_left_q <= '0;
      out_left_q <= '0;
      credit_q   <= CB_W'(FIFO_BEATS);
      used_q     <= '0;
    end else begin
      if (kick_take && !bad_size && !bad_range) begin
        run_q      <= 1'b1;
        req_addr_q <= kick_addr_i;
        req_left_q <= 25'(kick_size_i);
        out_addr_q <= kick_addr_i;
        out_left_q <= 22'(kick_size_i[23:2]);
      end else begin
        if (req_fire) begin
          req_addr_q <= chunk_end + 32'd1;
          req_left_q <= req_left_q - chunk;
        end
        if (word_fire) begin
          out_addr_q <= out_addr_q + 32'd4;
          out_left_q <= out_left_q - 22'd1;
          if (out_left_q == 22'd1) run_q <= 1'b0;
        end
      end
      credit_q <= credit_q - (req_fire ? chunk_beats : '0) + CB_W'(beat_pop);
      if (beat_pop)       used_q <= '0;
      else if (word_fire) used_q <= used_q | (WPB'(1) << sel);
    end
  end

  assign busy_o = run_q;
  assign pend_o = credit_q != CB_W'(FIFO_BEATS);   // credits return when a beat leaves the FIFO

  // Command words are 4-byte aligned, so one strobe bit per word is enough.
  logic unused;
  assign unused = ^rd_rsp_strb_i;

endmodule
