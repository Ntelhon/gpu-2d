// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Rect walker of the general pixel pipeline (arch.md §6.2, §6.3 stage 1).
//
// The draw rectangle is clipped against the target and the scissor (rendering.md §6, as g2d_fill), then
// cut into spans: runs of pixels of one row whose bytes lie in one 256-byte-aligned block, so a span
// covers at most 256 bytes. Traversal (cfg_tiles_i):
//   0: scanline order over the whole rectangle;
//   1: 16x16 destination tiles on the 16-aligned grid, row-major tiles, scanline inside a tile (a tile
//      row is one span unless it crosses a 256-byte boundary).
// Every span is pushed to the span outputs (destination reader when cfg_dst_i, write combiner) and to
// the pixel generator, which emits its pixels (x, y) one per clock in increasing x.
// Row addresses are kept incrementally (one multiply at setup), so the walker emits one span per clock.
//
// stop_i (soft reset): no new span is started; the pixels of spans already emitted are still produced,
// so the write combiner can finish them. done_o pulses once when the last pixel of a draw has been
// emitted (or at setup for an empty draw). A new draw may start when busy_o is low.

module g2d_walk (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  input  logic              stop_i,
  // draw
  input  logic              start_i,          // pulse: a draw with the configuration below starts
  output logic              busy_o,
  input  logic signed [15:0] cfg_px0_i,
  input  logic signed [15:0] cfg_py0_i,
  input  logic signed [15:0] cfg_px1_i,
  input  logic signed [15:0] cfg_py1_i,
  input  logic [13:0]       cfg_tw_i,
  input  logic [13:0]       cfg_th_i,
  input  logic [13:0]       cfg_cx0_i,
  input  logic [13:0]       cfg_cy0_i,
  input  logic [13:0]       cfg_cx1_i,
  input  logic [13:0]       cfg_cy1_i,
  input  logic [31:0]       cfg_addr_i,
  input  logic [31:0]       cfg_stride_i,     // row step, two's complement (negative: TGT_FMT.FLIP_Y, v1.6)
  input  logic [1:0]        cfg_lg_i,
  input  logic              cfg_tiles_i,
  input  logic              cfg_dst_i,
  input  logic              cfg_mask_i,       // v1.6: spans also go to the mask reader (rendering.md §6.1)
  input  logic              cfg_empty_i,      // force an empty draw (DRAW_PATH with EDGE_COUNT = 0)
  // spans to the destination reader and the write combiner
  output logic              dspan_valid_o,
  input  logic              dspan_ready_i,
  output logic              wspan_valid_o,
  input  logic              wspan_ready_i,
  output logic              mspan_valid_o,
  input  logic              mspan_ready_i,
  output logic [31:0]       span_addr_o,      // byte address of the first pixel
  output logic [8:0]        span_n_o,         // pixels, 1..256
  output logic [13:0]       span_x_o,         // first pixel (mask reader)
  output logic [13:0]       span_y_o,
  // pixels
  output logic              pix_valid_o,
  input  logic              pix_ready_i,
  output logic [13:0]       pix_x_o,
  output logic [13:0]       pix_y_o,
  // clipped rectangle, presented with rect_valid_o for one cycle when a non-empty walk starts
  output logic              rect_valid_o,
  output logic [13:0]       rect_x0_o,
  output logic [13:0]       rect_y0_o,
  output logic [13:0]       rect_x1_o,
  output logic [13:0]       rect_y1_o,
  // status
  output logic              done_o
);

  typedef enum logic [1:0] {S_IDLE, S_SETUP1, S_SETUP2, S_RUN} state_e;
  state_e st_q;

  // ---------------------------------------------------------------- setup: clip
  function automatic logic signed [16:0] smax(logic signed [16:0] a, logic signed [16:0] b);
    return (a > b) ? a : b;
  endfunction
  function automatic logic signed [16:0] smin(logic signed [16:0] a, logic signed [16:0] b);
    return (a < b) ? a : b;
  endfunction

  logic signed [16:0] rx0_d, ry0_d, rx1_d, ry1_d;
  assign rx0_d = smax(17'(cfg_px0_i), $signed({3'b0, cfg_cx0_i}));
  assign ry0_d = smax(17'(cfg_py0_i), $signed({3'b0, cfg_cy0_i}));
  assign rx1_d = smin(smin(17'(cfg_px1_i), $signed({3'b0, cfg_cx1_i})), $signed({3'b0, cfg_tw_i}));
  assign ry1_d = smin(smin(17'(cfg_py1_i), $signed({3'b0, cfg_cy1_i})), $signed({3'b0, cfg_th_i}));

  logic [13:0] rx0_q, ry0_q, rx1_q, ry1_q;
  logic        empty_q;

  // ---------------------------------------------------------------- traversal state
  logic [13:0] by0_q, by1_q;       // current band rows [by0, by1)
  logic [13:0] tx0_q, tx1_q;       // current tile columns [tx0, tx1)
  logic [13:0] y_q, x_q;
  logic [31:0] band_addr_q, row_addr_q;
  logic        last_span;

  // end of the 16-aligned tile containing coordinate c (passed as its tile index c >> 4), limited to lim
  function automatic logic [13:0] tile_end(logic [9:0] ct, logic [13:0] lim, logic tiles);
    logic [14:0] e;
    e = {1'b0, ct, 4'b0000} + 15'd16;
    return (!tiles || e >= 15'(lim)) ? lim : e[13:0];
  endfunction

  // span [x, se) of row y
  logic [31:0] saddr;
  logic [8:0]  to_bound;    // pixels to the next 256-byte boundary (1..256)
  logic [13:0] se;
  logic [14:0] se_b;
  assign saddr    = row_addr_q + (32'(x_q) << cfg_lg_i);
  assign to_bound = 9'((9'd256 - 9'(saddr[7:0])) >> cfg_lg_i);
  assign se_b     = 15'(x_q) + 15'(to_bound);
  assign se       = (se_b < 15'(tx1_q)) ? se_b[13:0] : tx1_q;

  // ---------------------------------------------------------------- span output
  logic span_valid, span_fire, pq_ready;
  assign span_valid    = (st_q == S_RUN) && !stop_i;
  logic d_ok, m_ok;
  assign d_ok          = ~cfg_dst_i | dspan_ready_i;
  assign m_ok          = ~cfg_mask_i | mspan_ready_i;
  assign dspan_valid_o = span_valid & cfg_dst_i & wspan_ready_i & m_ok & pq_ready;
  assign mspan_valid_o = span_valid & cfg_mask_i & wspan_ready_i & d_ok & pq_ready;
  assign wspan_valid_o = span_valid & d_ok & m_ok & pq_ready;
  assign span_fire     = span_valid & d_ok & m_ok & wspan_ready_i & pq_ready;
  assign span_x_o      = x_q;
  assign span_y_o      = y_q;
  assign span_addr_o   = saddr;
  assign span_n_o      = 9'(se - x_q);

  assign last_span = (se == tx1_q) && (y_q + 14'd1 == by1_q) && (tx1_q == rx1_q) && (by1_q == ry1_q);

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      st_q <= S_IDLE;
    end else if (clr_i) begin
      st_q <= S_IDLE;
    end else begin
      unique case (st_q)
        S_IDLE:   if (start_i) st_q <= S_SETUP1;
        S_SETUP1: st_q <= S_SETUP2;
        S_SETUP2: st_q <= (empty_q || stop_i) ? S_IDLE : S_RUN;
        S_RUN:    if (stop_i || (span_fire && last_span)) st_q <= S_IDLE;
        default:  st_q <= S_IDLE;
      endcase
    end
  end

  always_ff @(posedge clk_i) begin
    if (st_q == S_SETUP1) begin
      empty_q <= (rx1_d <= rx0_d) || (ry1_d <= ry0_d) || cfg_empty_i;
      rx0_q   <= rx0_d[13:0];
      ry0_q   <= ry0_d[13:0];
      rx1_q   <= rx1_d[13:0];
      ry1_q   <= ry1_d[13:0];
    end
    if (st_q == S_SETUP2) begin
      by0_q       <= ry0_q;
      by1_q       <= tile_end(ry0_q[13:4], ry1_q, cfg_tiles_i);
      tx0_q       <= rx0_q;
      tx1_q       <= tile_end(rx0_q[13:4], rx1_q, cfg_tiles_i);
      y_q         <= ry0_q;
      x_q         <= rx0_q;
      band_addr_q <= cfg_addr_i + 32'(ry0_q) * cfg_stride_i;
      row_addr_q  <= cfg_addr_i + 32'(ry0_q) * cfg_stride_i;
    end else if (span_fire) begin
      if (se != tx1_q) begin
        x_q <= se;                                       // next span of the same tile row
      end else if (y_q + 14'd1 != by1_q) begin
        x_q        <= tx0_q;                             // next row of the tile
        y_q        <= y_q + 14'd1;
        row_addr_q <= row_addr_q + cfg_stride_i;
      end else if (tx1_q != rx1_q) begin
        tx0_q      <= tx1_q;                             // next tile of the band
        tx1_q      <= tile_end(tx1_q[13:4], rx1_q, cfg_tiles_i);
        x_q        <= tx1_q;
        y_q        <= by0_q;
        row_addr_q <= band_addr_q;
      end else begin
        by0_q       <= by1_q;                            // next band
        by1_q       <= tile_end(by1_q[13:4], ry1_q, cfg_tiles_i);
        tx0_q       <= rx0_q;
        tx1_q       <= tile_end(rx0_q[13:4], rx1_q, cfg_tiles_i);
        x_q         <= rx0_q;
        y_q         <= by1_q;
        band_addr_q <= row_addr_q + cfg_stride_i;
        row_addr_q  <= row_addr_q + cfg_stride_i;
      end
    end
  end

  // ---------------------------------------------------------------- pixel generator
  typedef struct packed {
    logic [13:0] x;
    logic [13:0] y;
    logic [8:0]  n;
    logic        last;
  } pspan_t;

  pspan_t pq_out;
  logic   pq_valid, pq_pop;
  logic [2:0] pq_cnt;

  g2d_fifo #(.WIDTH($bits(pspan_t)), .DEPTH(4)) u_pq (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (span_fire),
    .in_ready_o  (pq_ready),
    .in_data_i   (pspan_t'{x: x_q, y: y_q, n: span_n_o, last: last_span}),
    .out_valid_o (pq_valid),
    .out_ready_i (pq_pop),
    .out_data_o  (pq_out),
    .count_o     (pq_cnt)
  );

  logic [8:0] k_q;     // pixel index within the head span
  logic       pix_fire, span_end;
  assign pix_valid_o = pq_valid;
  assign pix_x_o     = pq_out.x + 14'(k_q);
  assign pix_y_o     = pq_out.y;
  assign pix_fire    = pix_valid_o & pix_ready_i;
  assign span_end    = pix_fire && (k_q + 9'd1 == pq_out.n);
  assign pq_pop      = span_end;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)       k_q <= '0;
    else if (clr_i)    k_q <= '0;
    else if (span_end) k_q <= '0;
    else if (pix_fire) k_q <= k_q + 9'd1;
  end

  // done: last pixel of the draw emitted, or an empty draw at setup. A walk abandoned by stop_i reports
  // no done; busy_o stays high until the spans already emitted have produced their pixels.
  assign rect_valid_o = (st_q == S_SETUP2) && !empty_q && !stop_i;
  assign rect_x0_o    = rx0_q;
  assign rect_y0_o    = ry0_q;
  assign rect_x1_o    = rx1_q;
  assign rect_y1_o    = ry1_q;

  assign done_o = (span_end && pq_out.last) ||
                  (st_q == S_SETUP2 && empty_q && !stop_i);
  assign busy_o = (st_q != S_IDLE) || (pq_cnt != '0);

endmodule
