// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// A counter or credit never exceeds its maximum (a double return or a missed decrement would).
module g2d_max_sva #(
  parameter int unsigned W = 8,
  parameter int unsigned MAX = 1,
  parameter string       NAME = "counter"
) (
  input  logic         clk_i,
  input  logic         rst_ni,
  input  logic [W-1:0] value_i
);
  // Armed by the first reset: before it the design holds random initial values (--x-initial unique).
  bit armed = 1'b0;
  always @(negedge rst_ni) armed <= 1'b1;

  p_max: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed) int'(value_i) <= int'(MAX))
    else $error("p_max (%s): value %0d above its maximum %0d", NAME, value_i, MAX);
endmodule
