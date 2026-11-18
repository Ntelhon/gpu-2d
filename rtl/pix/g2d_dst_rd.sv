// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Destination reader (arch.md §6.3 stage 4). Reads the bytes of each span (from g2d_walk) through its
// read client into a beat buffer of BUF_BYTES (SRAM), ahead of the blend stage, and unpacks them into
// one stored destination pixel per clock, in span order.
//
// Credit-based: a span is requested only when the buffer has room for all its beats, so the read
// client never back-pressures R. Spans are at most 256 bytes inside one 256-byte block.
// stop_i (soft reset): no new request; idle_o ignores the contents (requests may have been dropped;
// beats of issued bursts still arrive and fit, by the credits). clr_i clears everything.

module g2d_dst_rd #(
  parameter int unsigned DATA_W    = 64,
  parameter int unsigned BUF_BYTES = 1024,
  parameter int unsigned SPANS     = 16,     // v1.7 (Q-PERF-2): spans queued / in flight (was 8)
  localparam int unsigned DB = DATA_W / 8,
  localparam int unsigned OFF_W = $clog2(DB)
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  input  logic              stop_i,
  input  logic [1:0]        cfg_lg_i,
  // spans
  input  logic              span_valid_i,
  output logic              span_ready_o,
  input  logic [31:0]       span_addr_i,
  input  logic [8:0]        span_n_i,
  // read client
  output logic              rd_req_valid_o,
  input  logic              rd_req_ready_i,
  output logic [31:0]       rd_req_addr_o,
  output logic [15:0]       rd_req_len_o,
  input  logic              rd_rsp_valid_i,
  output logic              rd_rsp_ready_o,
  input  logic [DATA_W-1:0] rd_rsp_data_i,
  // destination pixels
  output logic              pix_valid_o,
  input  logic              pix_ready_i,
  output logic [31:0]       pix_d_o,
  // status
  output logic              idle_o
);

  localparam int unsigned BEATS = BUF_BYTES / DB;
  localparam int unsigned CR_W  = $clog2(BEATS + 1);

  typedef struct packed {
    logic [31:0] addr;
    logic [8:0]  n;
  } span_t;

  // ---------------------------------------------------------------- request side
  span_t      sq_out;
  logic       sq_valid, req_fire;
  logic [$clog2(SPANS+1)-1:0] sq_cnt;
  logic [CR_W-1:0] credit_q;
  logic [10:0] bytes;
  logic [CR_W-1:0] beats;
  logic        uq_ready, beat_pop;

  g2d_fifo #(.WIDTH($bits(span_t)), .DEPTH(SPANS)) u_sq (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (span_valid_i),
    .in_ready_o  (span_ready_o),
    .in_data_i   (span_t'{addr: span_addr_i, n: span_n_i}),
    .out_valid_o (sq_valid),
    .out_ready_i (req_fire),
    .out_data_o  (sq_out),
    .count_o     (sq_cnt)
  );

  assign bytes = 11'(sq_out.n) << cfg_lg_i;
  assign beats = CR_W'((11'(sq_out.addr[OFF_W-1:0]) + bytes - 11'd1) >> OFF_W) + CR_W'(1);

  assign rd_req_valid_o = sq_valid & uq_ready & ~stop_i & (credit_q >= beats);
  assign rd_req_addr_o  = sq_out.addr;
  assign rd_req_len_o   = 16'(bytes - 11'd1);
  assign req_fire       = rd_req_valid_o & rd_req_ready_i;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)     credit_q <= CR_W'(BEATS);
    else if (clr_i)  credit_q <= CR_W'(BEATS);
    else             credit_q <= credit_q - (req_fire ? beats : '0) + CR_W'(beat_pop);
  end

  // ---------------------------------------------------------------- beat buffer
  logic              bf_valid, bf_in_ready;
  logic [DATA_W-1:0] bf_data;

  assign rd_rsp_ready_o = bf_in_ready;   // always 1: requests are credit-limited

  g2d_fifo_ram #(.WIDTH(DATA_W), .DEPTH(BEATS)) u_buf (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (rd_rsp_valid_i),
    .in_ready_o  (bf_in_ready),
    .in_data_i   (rd_rsp_data_i),
    .out_valid_o (bf_valid),
    .out_ready_i (beat_pop),
    .out_data_o  (bf_data)
  );

  // ---------------------------------------------------------------- unpacker
  span_t      uq_out;
  logic       uq_valid, uq_pop;
  logic [$clog2(SPANS+1)-1:0] uq_cnt;

  g2d_fifo #(.WIDTH($bits(span_t)), .DEPTH(SPANS)) u_uq (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (req_fire),
    .in_ready_o  (uq_ready),
    .in_data_i   (sq_out),
    .out_valid_o (uq_valid),
    .out_ready_i (uq_pop),
    .out_data_o  (uq_out),
    .count_o     (uq_cnt)
  );

  logic [8:0]       k_q;        // pixel index in the head span
  logic [OFF_W-1:0] o_q;        // byte offset of the next pixel in the head beat
  logic             first_q;    // next pixel is the first of its span (offset from the span address)
  logic [OFF_W-1:0] o;
  logic             pix_fire, last_pix, beat_end;
  logic [OFF_W:0]   o_next;

  assign o        = first_q ? uq_out.addr[OFF_W-1:0] : o_q;
  assign o_next   = {1'b0, o} + ((OFF_W + 1)'(1) << cfg_lg_i);
  assign last_pix = (k_q + 9'd1 == uq_out.n);
  assign beat_end = o_next[OFF_W] | last_pix;

  logic [DATA_W+31:0] sh;
  assign sh          = {32'd0, bf_data} >> {o, 3'b000};
  assign pix_d_o     = sh[31:0];
  assign pix_valid_o = uq_valid & bf_valid;
  assign pix_fire    = pix_valid_o & pix_ready_i;
  assign beat_pop    = pix_fire & beat_end;
  assign uq_pop      = pix_fire & last_pix;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      k_q     <= '0;
      o_q     <= '0;
      first_q <= 1'b1;
    end else if (clr_i) begin
      k_q     <= '0;
      o_q     <= '0;
      first_q <= 1'b1;
    end else if (pix_fire) begin
      k_q     <= last_pix ? 9'd0 : k_q + 9'd1;
      o_q     <= o_next[OFF_W-1:0];
      first_q <= last_pix;
    end
  end

  assign idle_o = stop_i | (sq_cnt == '0 && uq_cnt == '0 && credit_q == CR_W'(BEATS));

  logic unused;
  assign unused = ^{sh[DATA_W+31:32], uq_out.addr[31:OFF_W]};

endmodule
