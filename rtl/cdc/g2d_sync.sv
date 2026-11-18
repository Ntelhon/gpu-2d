// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Synchronizer cell (arch.md §2.1): STAGES flops in the destination clock domain, reset to RESET_VALUE.
// The only cell a CDC signal passes through, so an ASIC flow can replace this module by a library synchronizer
// and constraints can find every crossing by its name (u_sync*/q_q). A multi-bit input is only allowed for a
// gray-coded bus (at most one bit changes between two samples).
//
// Simulation option G2D_CDC_JITTER (never in synthesis): when the input differs from the first stage, the first
// stage keeps its old value for that clock with probability 1/4. The whole bus is held, so it only ever shows a
// real past value of the input; this models a synchronizer that resolves after STAGES, STAGES + 1, ... cycles.

module g2d_sync #(
  parameter int unsigned     STAGES      = 2,
  parameter int unsigned     WIDTH       = 1,
  parameter logic [WIDTH-1:0] RESET_VALUE = '0
) (
  input  logic             clk_i,
  input  logic             rst_ni,
  input  logic [WIDTH-1:0] d_i,
  output logic [WIDTH-1:0] q_o
);

  if (STAGES < 2) begin : g_bad_stages
    $error("g2d_sync: STAGES must be >= 2");
  end

  logic [WIDTH-1:0] q_q [STAGES];
  logic [WIDTH-1:0] first_d;

`ifdef G2D_CDC_JITTER
  always_comb begin
    first_d = d_i;
    if (d_i != q_q[0] && ($urandom % 4) == 0) first_d = q_q[0];
  end
`else
  assign first_d = d_i;
`endif

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      for (int unsigned i = 0; i < STAGES; i++) q_q[i] <= RESET_VALUE;
    end else begin
      q_q[0] <= first_d;
      for (int unsigned i = 1; i < STAGES; i++) q_q[i] <= q_q[i-1];
    end
  end

  assign q_o = q_q[STAGES-1];

endmodule
