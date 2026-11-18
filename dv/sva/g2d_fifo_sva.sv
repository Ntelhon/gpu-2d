// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// An SRAM FIFO used with credits is never pushed when full (a push would be lost). Bound per user, not
// into every g2d_fifo_ram: in general a producer may present data to a full FIFO and wait.
module g2d_fifo_sva (
  input  logic clk_i,
  input  logic rst_ni,
  input  logic in_valid_i,
  input  logic in_ready_o
);
  // Armed by the first reset: before it the design holds random initial values (--x-initial unique).
  bit armed = 1'b0;
  always @(negedge rst_ni) armed <= 1'b1;

  p_no_overflow: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed) in_valid_i |-> in_ready_o)
    else $error("p_no_overflow (%m): push into a full SRAM FIFO");
endmodule
