// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Mask layer reader (v1.6, rendering.md §6.1): one 8-bit mask value per pixel of the walker's spans, in span order.
//
// A target span (x, y, n) maps to mask row my = y - OY and columns [x - OX, x - OX + n). The part inside the mask
// buffer ([0, W) x [0, H)) is read through an embedded destination reader (A8, read client "mask"); pixels outside
// it get 0 without a read. Per span a descriptor {zl, nread, zr} (zeros left, bytes read, zeros right) drives the
// output. Spans are accepted one per clock after one registered stage (row address multiply).
// stop_i / clr_i: as g2d_dst_rd (idle_o ignores data that will never be requested under stop_i).

module g2d_mask_rd #(
  parameter int unsigned DATA_W    = 64,
  parameter int unsigned BUF_BYTES = 512
) (
  input  logic               clk_i,
  input  logic               rst_ni,
  input  logic               clr_i,
  input  logic               stop_i,
  input  logic [31:0]        cfg_addr_i,
  input  logic [15:0]        cfg_stride_i,
  input  logic [13:0]        cfg_w_i,
  input  logic [13:0]        cfg_h_i,
  input  logic signed [15:0] cfg_ox_i,
  input  logic signed [15:0] cfg_oy_i,
  // spans (target coordinates)
  input  logic               span_valid_i,
  output logic               span_ready_o,
  input  logic [13:0]        span_x_i,
  input  logic [13:0]        span_y_i,
  input  logic [8:0]         span_n_i,
  // read client
  output logic               rd_req_valid_o,
  input  logic               rd_req_ready_i,
  output logic [31:0]        rd_req_addr_o,
  output logic [15:0]        rd_req_len_o,
  input  logic               rd_rsp_valid_i,
  output logic               rd_rsp_ready_o,
  input  logic [DATA_W-1:0]  rd_rsp_data_i,
  // mask values
  output logic               pix_valid_o,
  input  logic               pix_ready_i,
  output logic [7:0]         pix_m_o,
  output logic               idle_o
);

  typedef struct packed {
    logic [8:0] zl;
    logic [8:0] nr;
    logic [8:0] zr;
  } desc_t;

  // ---------------------------------------------------------------- S0 (v1.7): span input register slice, so the
  // descriptor arithmetic starts from a flop (timing, T-1)
  logic        sp_valid, sp_ready;
  logic [13:0] sp_x, sp_y;
  logic [8:0]  sp_n;
  g2d_spill_reg #(.WIDTH(37), .SKID(1'b0)) u_span_in (
    .clk_i, .rst_ni, .clr_i,
    .in_valid_i  (span_valid_i),
    .in_ready_o  (span_ready_o),
    .in_data_i   ({span_x_i, span_y_i, span_n_i}),
    .out_valid_o (sp_valid),
    .out_ready_i (sp_ready),
    .out_data_o  ({sp_x, sp_y, sp_n})
  );

  // ---------------------------------------------------------------- S1: span -> descriptor + read span
  logic signed [16:0] my, mx0, mx1;
  logic signed [16:0] lo, hi;
  logic               row_in, any;
  assign my     = 17'(signed'({1'b0, sp_y})) - 17'(cfg_oy_i);
  assign mx0    = 17'(signed'({1'b0, sp_x})) - 17'(cfg_ox_i);
  assign mx1    = mx0 + 17'(signed'({1'b0, sp_n}));
  assign row_in = (my >= 17'sd0) && (my < 17'(signed'({1'b0, cfg_h_i})));
  assign lo     = (mx0 < 17'sd0) ? 17'sd0 : (mx0 > 17'(signed'({1'b0, cfg_w_i}))) ? 17'(signed'({1'b0, cfg_w_i})) : mx0;
  assign hi     = (mx1 < 17'sd0) ? 17'sd0 : (mx1 > 17'(signed'({1'b0, cfg_w_i}))) ? 17'(signed'({1'b0, cfg_w_i})) : mx1;
  assign any    = row_in && (hi > lo);

  logic        s1_valid_q, s1_ready, s1_rd_q;
  desc_t       s1_desc_q;
  logic [31:0] s1_addr_q;
  assign sp_ready = ~s1_valid_q | s1_ready;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)              s1_valid_q <= 1'b0;
    else if (clr_i)           s1_valid_q <= 1'b0;
    else if (sp_ready)        s1_valid_q <= sp_valid;
  end

  always_ff @(posedge clk_i) begin
    if (sp_ready && sp_valid) begin
      s1_rd_q   <= any;
      s1_desc_q <= any ? desc_t'{zl: 9'(lo - mx0), nr: 9'(hi - lo), zr: 9'(mx1 - hi)}
                       : desc_t'{zl: sp_n, nr: 9'd0, zr: 9'd0};
      s1_addr_q <= cfg_addr_i + 32'(my[13:0]) * 32'(cfg_stride_i) + 32'(lo[13:0]);
    end
  end

  // ---------------------------------------------------------------- descriptor queue and byte reader
  logic  dq_in_ready, dq_valid, dq_pop, rs_ready;
  desc_t dq_out;
  logic [3:0] dq_cnt;
  assign s1_ready = dq_in_ready & (~s1_rd_q | rs_ready);

  g2d_fifo #(.WIDTH($bits(desc_t)), .DEPTH(8)) u_dq (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (s1_valid_q & (~s1_rd_q | rs_ready)),
    .in_ready_o  (dq_in_ready),
    .in_data_i   (s1_desc_q),
    .out_valid_o (dq_valid),
    .out_ready_i (dq_pop),
    .out_data_o  (dq_out),
    .count_o     (dq_cnt)
  );

  logic        bp_valid, bp_ready, rd_idle;
  logic [31:0] bp_d;
  g2d_dst_rd #(.DATA_W(DATA_W), .BUF_BYTES(BUF_BYTES)) u_rd (
    .clk_i,
    .rst_ni,
    .clr_i,
    .stop_i,
    .cfg_lg_i       (2'd0),
    .span_valid_i   (s1_valid_q & s1_rd_q & dq_in_ready),
    .span_ready_o   (rs_ready),
    .span_addr_i    (s1_addr_q),
    .span_n_i       (s1_desc_q.nr),
    .rd_req_valid_o,
    .rd_req_ready_i,
    .rd_req_addr_o,
    .rd_req_len_o,
    .rd_rsp_valid_i,
    .rd_rsp_ready_o,
    .rd_rsp_data_i,
    .pix_valid_o    (bp_valid),
    .pix_ready_i    (bp_ready),
    .pix_d_o        (bp_d),
    .idle_o         (rd_idle)
  );

  // ---------------------------------------------------------------- output: zl zeros, nr bytes, zr zeros
  logic [8:0] k_q;
  logic [9:0] total, k_end_rd;
  logic       in_rd, last, fire;
  assign total    = 10'(dq_out.zl) + 10'(dq_out.nr) + 10'(dq_out.zr);
  assign k_end_rd = 10'(dq_out.zl) + 10'(dq_out.nr);
  assign in_rd    = (10'(k_q) >= 10'(dq_out.zl)) && (10'(k_q) < k_end_rd);
  assign last     = (10'(k_q) + 10'd1 == total);
  // v1.7: output register slice, so the join's coverage x mask multiply starts from a flop (timing, T-1)
  logic       mo_valid, mo_ready;
  logic [7:0] mo_m;
  assign mo_valid = dq_valid & (~in_rd | bp_valid);
  assign mo_m     = in_rd ? bp_d[7:0] : 8'h00;
  assign fire     = mo_valid & mo_ready;
  g2d_spill_reg #(.WIDTH(8), .SKID(1'b0)) u_out (
    .clk_i, .rst_ni, .clr_i,
    .in_valid_i  (mo_valid),
    .in_ready_o  (mo_ready),
    .in_data_i   (mo_m),
    .out_valid_o (pix_valid_o),
    .out_ready_i (pix_ready_i),
    .out_data_o  (pix_m_o)
  );
  assign bp_ready    = fire & in_rd;
  assign dq_pop      = fire & last;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)      k_q <= '0;
    else if (clr_i)   k_q <= '0;
    else if (dq_pop)  k_q <= '0;
    else if (fire)    k_q <= k_q + 9'd1;
  end

  assign idle_o = ~sp_valid & ~s1_valid_q & (dq_cnt == '0) & rd_idle & ~pix_valid_o;

  logic unused;
  assign unused = ^{bp_d[31:8], my[16:14], lo[16:14]};

endmodule
