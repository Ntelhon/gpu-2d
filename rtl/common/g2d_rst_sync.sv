// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Reset distribution stage (v1.7, arch.md §2): asynchronous assert, synchronous de-assert STAGES clock edges after
// rst_ni. The input reset is already synchronous to clk_i (the integrator's synchronizer); these flops only cut its
// fan-out so the recovery / removal paths to every flop meet timing: the output flop is replicated by the tools
// (MAX_FANOUT). STAGES = 0 passes rst_ni through. test_mode_i = 1 (v1.9, DFT scan) passes rst_ni through as well,
// so the reset of every flop behind it is controllable from the reset pin.

module g2d_rst_sync #(
  parameter int unsigned STAGES = 2
) (
  input  logic clk_i,
  input  logic rst_ni,
  input  logic test_mode_i,
  output logic rst_no
);

  if (STAGES == 0) begin : g_pass
    assign rst_no = rst_ni;
    logic unused;
    assign unused = ^{clk_i, test_mode_i};
  end else begin : g_pipe
    (* max_fanout = 64 *) logic [STAGES-1:0] q;
    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni) begin
        q <= '0;
      end else begin
        q[0] <= 1'b1;
        for (int i = 1; i < int'(STAGES); i++) q[i] <= q[i-1];
      end
    end
    assign rst_no = test_mode_i ? rst_ni : q[STAGES-1];
  end

endmodule
