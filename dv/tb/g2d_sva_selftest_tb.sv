// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// SVA support self-test (Phase 5): a valid/ready stream with the properties the protocol checkers use.
// test_sva_support.py drives a legal stream (must pass) and violations (each must stop the simulation
// with the named property), so every construct is shown to be evaluated by Verilator, not only parsed.

module g2d_sva_selftest_tb (
  input  logic       clk,
  input  logic       rst_n,
  input  logic       v,
  input  logic       r,
  input  logic [7:0] d
);
  // valid held and payload stable while stalled: |=>, $stable, disable iff
  p_hold: assert property (@(posedge clk) disable iff (!rst_n) (v && !r) |=> (v && $stable(d)))
    else $error("p_hold violated");
  // a payload of 0xFF is never presented two cycles after a handshake: |-> ##2, $past
  p_seq: assert property (@(posedge clk) disable iff (!rst_n) (v && r) |-> ##2 !(v && d == 8'hFF))
    else $error("p_seq violated");
  // $rose / $past consistency
  p_rose: assert property (@(posedge clk) disable iff (!rst_n) $rose(v) |-> !$past(v))
    else $error("p_rose violated");
  c_hs: cover property (@(posedge clk) disable iff (!rst_n) v && r);
endmodule
