// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Generic valid/ready handshake checker (arch.md §11: valid/ready on every internal stream).
// Simulation only, bound by dv/sva/g2d_binds.sv with explicit connections. While en_i (the producer is not
// being stopped by a soft reset or halt, which may legally withdraw a request), VALID is held and the
// payload stable until READY.

module g2d_hs_sva #(
  parameter int unsigned W = 8,
  parameter string       NAME = "stream"
) (
  input  logic         clk_i,
  input  logic         rst_ni,
  input  logic         en_i,
  input  logic         valid_i,
  input  logic         ready_i,
  input  logic [W-1:0] data_i
);
  // Armed by the first reset: before it the design holds random initial values (--x-initial unique).
  bit armed = 1'b0;
  always @(negedge rst_ni) armed <= 1'b1;

  p_hold: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed)
      (en_i && valid_i && !ready_i) |=> (!en_i || (valid_i && $stable(data_i))))
    else $error("p_hold (%s): valid dropped or payload changed while stalled", NAME);
endmodule
