// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Checker of g2d_cdc_fifo (verification-plan.md CDC-01..03, arch.md §2.1). Simulation only, bound by
// dv/sva/g2d_binds.sv. In each domain: the registered gray pointer changes by at most one bit per clock; the
// FIFO never holds more than DEPTH words, also by the true (cross-domain, simulation-only) pointer difference,
// which shows that neither side ever passes the other; the destination holds valid and data while stalled.

module g2d_cdc_sva #(
  parameter int unsigned WIDTH     = 8,
  parameter int unsigned LOG_DEPTH = 1,
  localparam int unsigned DEPTH = 1 << LOG_DEPTH,
  localparam int unsigned PW    = LOG_DEPTH + 1
) (
  input logic             src_clk_i,
  input logic             src_rst_ni,
  input logic             dst_clk_i,
  input logic             dst_rst_ni,
  input logic [PW-1:0]    wbin_q,
  input logic [PW-1:0]    wgray_q,
  input logic [PW-1:0]    rbin_q,
  input logic [PW-1:0]    rgray_q,
  input logic [PW-1:0]    rbin_s,
  input logic             dst_valid_o,
  input logic             dst_ready_i,
  input logic [WIDTH-1:0] dst_data_o
);
  bit armed_s = 1'b0, armed_d = 1'b0;
  always @(negedge src_rst_ni) armed_s <= 1'b1;
  always @(negedge dst_rst_ni) armed_d <= 1'b1;

  logic [PW-1:0] true_cnt;
  assign true_cnt = wbin_q - rbin_q;

  p_wgray_step: assert property (@(posedge src_clk_i) disable iff (!src_rst_ni || !armed_s)
      $countones(wgray_q ^ $past(wgray_q)) <= 1)
    else $error("g2d_cdc_fifo: write gray pointer changed by more than one bit");
  p_wgray_bin: assert property (@(posedge src_clk_i) disable iff (!src_rst_ni || !armed_s)
      wgray_q == (wbin_q ^ (wbin_q >> 1)))
    else $error("g2d_cdc_fifo: write gray pointer does not match the binary pointer");
  p_src_view: assert property (@(posedge src_clk_i) disable iff (!src_rst_ni || !armed_s)
      (wbin_q - rbin_s) <= PW'(DEPTH))
    else $error("g2d_cdc_fifo: source sees more than DEPTH words");
  p_true_cnt_s: assert property (@(posedge src_clk_i) disable iff (!src_rst_ni || !dst_rst_ni || !armed_s)
      true_cnt <= PW'(DEPTH))
    else $error("g2d_cdc_fifo: overflow or underflow (true count %0d)", true_cnt);

  p_rgray_step: assert property (@(posedge dst_clk_i) disable iff (!dst_rst_ni || !armed_d)
      $countones(rgray_q ^ $past(rgray_q)) <= 1)
    else $error("g2d_cdc_fifo: read gray pointer changed by more than one bit");
  p_true_cnt_d: assert property (@(posedge dst_clk_i) disable iff (!src_rst_ni || !dst_rst_ni || !armed_d)
      true_cnt <= PW'(DEPTH))
    else $error("g2d_cdc_fifo: overflow or underflow (true count %0d)", true_cnt);
  p_hold: assert property (@(posedge dst_clk_i) disable iff (!dst_rst_ni || !armed_d)
      (dst_valid_o && !dst_ready_i) |=> (dst_valid_o && $stable(dst_data_o)))
    else $error("g2d_cdc_fifo: valid dropped or data changed while stalled");
endmodule
