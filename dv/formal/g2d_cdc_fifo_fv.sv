// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Formal harness of rtl/cdc/g2d_cdc_fifo (verification-plan.md CDC-05), SymbiYosys multiclock mode:
// src_clk_i and dst_clk_i are free inputs, so every interleaving of the two clocks is covered.
// Every pushed word is its own sequence number (a push counter), so "every word that leaves equals the read
// counter" proves order, no loss and no duplication.
// Resets (arch.md §2.2): both are asserted from the start; each is released, synchronously to its own clock,
// at an edge where the free input *_rel_i is 1, and never asserted again. They are built from registers rather
// than assumptions, so no global clock is needed.

module g2d_cdc_fifo_fv #(
  parameter int unsigned LOG_DEPTH   = 1,
  parameter int unsigned SYNC_STAGES = 2,
  parameter int unsigned W           = 6
) (
  input logic src_clk_i,
  input logic dst_clk_i,
  input logic src_rel_i,
  input logic dst_rel_i,
  input logic src_valid_i,
  input logic dst_ready_i
);
  localparam int unsigned DEPTH = 1 << LOG_DEPTH;
  localparam int unsigned PW    = LOG_DEPTH + 1;

  logic src_rst_nq = 1'b0;
  logic dst_rst_nq = 1'b0;
  always_ff @(posedge src_clk_i) if (src_rel_i) src_rst_nq <= 1'b1;
  always_ff @(posedge dst_clk_i) if (dst_rel_i) dst_rst_nq <= 1'b1;

  logic         src_ready, dst_valid;
  logic [W-1:0] wcnt_q, rcnt_q, dst_data;

  g2d_cdc_fifo #(.WIDTH(W), .LOG_DEPTH(LOG_DEPTH), .SYNC_STAGES(SYNC_STAGES)) u_dut (
    .src_clk_i, .src_rst_ni (src_rst_nq), .src_valid_i, .src_ready_o (src_ready), .src_data_i (wcnt_q),
    .dst_clk_i, .dst_rst_ni (dst_rst_nq), .dst_valid_o (dst_valid), .dst_ready_i, .dst_data_o (dst_data)
  );

  always_ff @(posedge src_clk_i or negedge src_rst_nq)
    if (!src_rst_nq) wcnt_q <= '0;
    else if (src_valid_i && src_ready) wcnt_q <= wcnt_q + 1'b1;
  always_ff @(posedge dst_clk_i or negedge dst_rst_nq)
    if (!dst_rst_nq) rcnt_q <= '0;
    else if (dst_valid && dst_ready_i) rcnt_q <= rcnt_q + 1'b1;

  // ---- properties
  logic [PW-1:0] wgray_p, rgray_p;
  always_ff @(posedge src_clk_i or negedge src_rst_nq)
    if (!src_rst_nq) wgray_p <= '0;
    else wgray_p <= u_dut.wgray_q;
  always_ff @(posedge dst_clk_i or negedge dst_rst_nq)
    if (!dst_rst_nq) rgray_p <= '0;
    else rgray_p <= u_dut.rgray_q;

  logic [W-1:0] true_cnt;
  assign true_cnt = wcnt_q - rcnt_q;

  always_comb begin
    if (src_rst_nq && dst_rst_nq) begin
      // order, no loss, no duplication
      if (dst_valid) assert (dst_data == rcnt_q);
      // no overflow / underflow
      assert (true_cnt <= W'(DEPTH));
      // gray pointers step by at most one bit per clock of their domain
      assert ($countones(u_dut.wgray_q ^ wgray_p) <= 1);
      assert ($countones(u_dut.rgray_q ^ rgray_p) <= 1);
      // the binary pointers agree with the word counters
      assert (u_dut.wbin_q == wcnt_q[PW-1:0]);
      assert (u_dut.rbin_q == rcnt_q[PW-1:0]);
      // reachability: full, then everything read back
      cover (!src_ready && true_cnt == W'(DEPTH));
      cover (rcnt_q == W'(DEPTH + 2) && !dst_valid);
    end
  end
endmodule
