// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Source-cache lock counts never reach their maximum (a decrement below 0 would wrap there).
module g2d_lock_sva #(
  parameter int unsigned N = 128,
  parameter int unsigned LOCK_W = 8
) (
  input  logic                     clk_i,
  input  logic                     rst_ni,
  input  logic [N-1:0][LOCK_W-1:0] locks_i
);
  // Armed by the first reset: before it the design holds random initial values (--x-initial unique).
  bit armed = 1'b0;
  always @(negedge rst_ni) armed <= 1'b1;

  always_ff @(posedge clk_i) begin
    if (rst_ni && armed) begin
      for (int i = 0; i < int'(N); i++) begin
        if (locks_i[i] == '1) $error("p_lock (%m): lock count of way %0d wrapped or saturated", i);
      end
    end
  end
endmodule
