// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Behavioural stand-in for a compiler-generated single-port SRAM macro (v1.9, B-2; simulation and lint only).
// Active-low chip enable, write enable and bit-write mask; Q holds its value during writes and while disabled.

module g2d_sram_1p #(
  parameter int unsigned WIDTH = 64,
  parameter int unsigned DEPTH = 64,
  localparam int unsigned AW = (DEPTH > 1) ? $clog2(DEPTH) : 1
) (
  input  logic             clk_i,
  input  logic             ceb_i,
  input  logic             web_i,
  input  logic [AW-1:0]    a_i,
  input  logic [WIDTH-1:0] d_i,
  input  logic [WIDTH-1:0] bweb_i,
  output logic [WIDTH-1:0] q_o
);

  logic [WIDTH-1:0] mem_q [DEPTH];

  always_ff @(posedge clk_i) begin
    if (!ceb_i) begin
      if (!web_i) mem_q[a_i] <= (mem_q[a_i] & bweb_i) | (d_i & ~bweb_i);
      else        q_o <= mem_q[a_i];
    end
  end

endmodule
