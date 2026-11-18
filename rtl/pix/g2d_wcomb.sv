// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Write combiner (arch.md §6.3 stage 6). Collects the output pixels of one span (from g2d_walk: at most
// 256 bytes inside one 256-byte block) in one of two span buffers (SRAM data, flop byte strobes), then
// issues one write request covering the first..last written byte and supplies its beats with byte
// strobes (skipped pixels, cov = 0, have strobe 0). A span without written pixels issues nothing.
// One buffer fills while the other drains.
//
// Pixels arrive in span order with wr = 1 for pixels to write. The write engine contract (arch.md §4.3)
// is kept: a request, then exactly the beats covering it; the next request may be issued before the
// previous beats are all supplied. Under soft reset the combiner keeps going (the write engine drops the
// bursts), so every span it started completes. pixels_o pulses for every written pixel.

module g2d_wcomb #(
  parameter int unsigned DATA_W = 64,
  localparam int unsigned DB = DATA_W / 8,
  localparam int unsigned OFF_W = $clog2(DB)
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  input  logic [1:0]        cfg_lg_i,
  // spans
  input  logic              span_valid_i,
  output logic              span_ready_o,
  input  logic [31:0]       span_addr_i,
  input  logic [8:0]        span_n_i,
  // pixels (stored format, right-aligned)
  input  logic              pix_valid_i,
  output logic              pix_ready_o,
  input  logic [31:0]       pix_data_i,
  input  logic              pix_wr_i,
  // write producer
  output logic              wr_req_valid_o,
  input  logic              wr_req_ready_i,
  output logic [31:0]       wr_req_addr_o,
  output logic [15:0]       wr_req_len_o,
  output logic              wr_dat_valid_o,
  input  logic              wr_dat_ready_i,
  output logic [DATA_W-1:0] wr_dat_data_o,
  output logic [DB-1:0]     wr_dat_strb_o,
  // status
  output logic              idle_o,
  output logic              pixels_o,
  // v1.8: RAM read/write collision this cycle (DBG_STATUS.RAM_COLL, arch.md §9.1)
  output logic              ram_coll_o
);

  localparam int unsigned BW = 256 / DB;        // beats per buffer
  localparam int unsigned BI = $clog2(BW);

  typedef struct packed {
    logic [31:0] addr;
    logic [8:0]  n;
  } span_t;

  // ---------------------------------------------------------------- span queue
  // 32 spans: the walker (and so the source cache lookups) can run up to 32 spans ahead of the written
  // pixels; with 8, short tile spans limited the lookahead below the memory latency (Q-PERF-2).
  span_t      sq_out;
  logic       sq_valid, sq_pop;
  logic [5:0] sq_cnt;

  g2d_fifo #(.WIDTH($bits(span_t)), .DEPTH(32)) u_sq (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (span_valid_i),
    .in_ready_o  (span_ready_o),
    .in_data_i   (span_t'{addr: span_addr_i, n: span_n_i}),
    .out_valid_o (sq_valid),
    .out_ready_i (sq_pop),
    .out_data_o  (sq_out),
    .count_o     (sq_cnt)
  );

  // ---------------------------------------------------------------- buffers
  logic [1:0]           full_q;
  logic [1:0][255:0]    strb_q;
  logic [1:0][23:0]     blk_q;        // address bits 31:8 of the span
  logic [1:0][7:0]      first_q;      // first written byte offset
  logic [1:0][7:0]      lastb_q;      // last written byte offset
  logic [1:0]           any_q;

  // ---------------------------------------------------------------- fill side
  logic       fb_q, have_q;
  logic [7:0] off_q;          // byte offset of the next pixel in the block
  logic [8:0] k_q, n_q;
  logic       ins, last_ins, load, nfb;
  logic [7:0] pend;           // last byte of the pixel being inserted

  assign pix_ready_o = have_q;
  assign ins         = have_q & pix_valid_i;
  assign last_ins    = ins & (k_q + 9'd1 == n_q);
  assign nfb         = last_ins ? ~fb_q : fb_q;
  assign load        = sq_valid & (~have_q | last_ins) & ~full_q[nfb];
  assign sq_pop      = load;
  assign pend        = off_q + ((8'd1 << cfg_lg_i) - 8'd1);
  assign pixels_o    = ins & pix_wr_i;

  // RAM write: the pixel's bytes at its offset in the beat
  logic [DB-1:0]     ram_be;
  logic [DATA_W-1:0] ram_wd;
  logic [3:0]        pmask;
  assign pmask  = (cfg_lg_i == 2'd0) ? 4'b0001 : (cfg_lg_i == 2'd1) ? 4'b0011 : 4'b1111;
  assign ram_be = DB'(pmask) << off_q[OFF_W-1:0];
  assign ram_wd = DATA_W'(pix_data_i) << {off_q[OFF_W-1:0], 3'b000};

  // ---------------------------------------------------------------- drain side
  typedef enum logic [1:0] {D_IDLE, D_REQ, D_BEATS} dstate_e;
  dstate_e    ds_q;
  logic       db_q;
  logic [BI-1:0] rb_q, rb_last_q;   // next beat to read, last beat
  logic       rd_issue, inflight_q, free_buf;
  logic [DB-1:0] inflight_strb_q;
  logic       of_ready, of_valid;
  logic [1:0] of_cnt;
  logic [DATA_W-1:0] ram_rd;

  assign wr_req_valid_o = (ds_q == D_REQ);
  assign wr_req_addr_o  = {blk_q[db_q], first_q[db_q]};
  assign wr_req_len_o   = 16'(lastb_q[db_q] - first_q[db_q]);

  logic dat_fire;
  assign dat_fire = wr_dat_valid_o & wr_dat_ready_i;
  assign rd_issue = (ds_q == D_BEATS) && (2'(of_cnt) + 2'(inflight_q) - 2'(dat_fire) <= 2'd1);
  assign free_buf = (ds_q == D_IDLE && full_q[db_q] && !any_q[db_q]) ||
                    (rd_issue && rb_q == rb_last_q);

  g2d_ram_1r1w #(.WIDTH(DATA_W), .DEPTH(2 * BW), .BE_W(DB)) u_ram (
    .clk_i,
    .we_i    (ins & pix_wr_i),
    .waddr_i ({fb_q, off_q[7:OFF_W]}),
    .wdata_i (ram_wd),
    .wbe_i   (ram_be),
    .re_i    (rd_issue),
    .raddr_i ({db_q, rb_q}),
    .rdata_o (ram_rd),
    .coll_o  (ram_coll_o)
  );

  g2d_fifo #(.WIDTH(DATA_W + DB), .DEPTH(2)) u_of (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (inflight_q),
    .in_ready_o  (of_ready),
    .in_data_i   ({ram_rd, inflight_strb_q}),
    .out_valid_o (of_valid),
    .out_ready_i (wr_dat_ready_i),
    .out_data_o  ({wr_dat_data_o, wr_dat_strb_o}),
    .count_o     (of_cnt)
  );
  assign wr_dat_valid_o = of_valid;

  // ---------------------------------------------------------------- state
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      full_q     <= '0;
      strb_q     <= '0;
      any_q      <= '0;
      fb_q       <= 1'b0;
      have_q     <= 1'b0;
      ds_q       <= D_IDLE;
      db_q       <= 1'b0;
      inflight_q <= 1'b0;
    end else if (clr_i) begin
      full_q     <= '0;
      strb_q     <= '0;
      any_q      <= '0;
      fb_q       <= 1'b0;
      have_q     <= 1'b0;
      ds_q       <= D_IDLE;
      db_q       <= 1'b0;
      inflight_q <= 1'b0;
    end else begin
      // fill
      if (ins && pix_wr_i) begin
        for (int i = 0; i < 4; i++) begin
          if (pmask[i]) strb_q[fb_q][off_q + 8'(i)] <= 1'b1;
        end
        any_q[fb_q] <= 1'b1;
      end
      if (last_ins) begin
        full_q[fb_q] <= 1'b1;
        fb_q         <= ~fb_q;
      end
      if (load)          have_q <= 1'b1;
      else if (last_ins) have_q <= 1'b0;
      // drain
      inflight_q <= rd_issue;
      unique case (ds_q)
        D_IDLE:  if (full_q[db_q] && any_q[db_q]) ds_q <= D_REQ;
        D_REQ:   if (wr_req_ready_i) ds_q <= D_BEATS;
        D_BEATS: if (rd_issue && rb_q == rb_last_q) ds_q <= D_IDLE;
        default: ds_q <= D_IDLE;
      endcase
      if (free_buf) begin
        full_q[db_q] <= 1'b0;
        strb_q[db_q] <= '0;
        any_q[db_q]  <= 1'b0;
        db_q         <= ~db_q;
      end
    end
  end

  always_ff @(posedge clk_i) begin
    if (load) begin
      off_q <= sq_out.addr[7:0];
      k_q   <= '0;
      n_q   <= sq_out.n;
      blk_q[nfb] <= sq_out.addr[31:8];
    end else if (ins) begin
      off_q <= off_q + (8'd1 << cfg_lg_i);
      k_q   <= k_q + 9'd1;
    end
    if (ins && pix_wr_i) begin
      if (!any_q[fb_q]) first_q[fb_q] <= off_q;
      lastb_q[fb_q] <= pend;
    end
    if (ds_q == D_REQ && wr_req_ready_i) begin
      rb_q      <= first_q[db_q][7:OFF_W];
      rb_last_q <= lastb_q[db_q][7:OFF_W];
    end else if (rd_issue) begin
      rb_q <= rb_q + BI'(1);
    end
    if (rd_issue) inflight_strb_q <= strb_q[db_q][{rb_q, {OFF_W{1'b0}}} +: DB];
  end

  assign idle_o = (sq_cnt == '0) & ~have_q & ~|full_q & (ds_q == D_IDLE) & ~inflight_q & ~of_valid;

  logic unused;
  assign unused = of_ready;   // always 1 when inflight_q is set (rd_issue condition)

endmodule
