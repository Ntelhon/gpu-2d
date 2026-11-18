// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Behavioural stand-in for a compiler-generated two-port SRAM macro (v1.9, B-2; simulation and lint only, replaced
// by the memory compiler's model / macro in an ASIC flow). Port A reads, port B writes, one clock, active-low
// enables and bit-write mask; QA holds its value while port A is not enabled.

module g2d_sram_2p #(
  parameter int unsigned WIDTH = 64,
  parameter int unsigned DEPTH = 64,
  localparam int unsigned AW = (DEPTH > 1) ? $clog2(DEPTH) : 1
) (
  input  logic             clk_i,
  input  logic             ceb_a_i,
  input  logic [AW-1:0]    aa_i,
  output logic [WIDTH-1:0] qa_o,
  input  logic             ceb_b_i,
  input  logic [AW-1:0]    ab_i,
  input  logic [WIDTH-1:0] db_i,
  input  logic [WIDTH-1:0] bweb_i
);

  logic [WIDTH-1:0] mem_q [DEPTH];

  always_ff @(posedge clk_i) begin
    if (!ceb_b_i) mem_q[ab_i] <= (mem_q[ab_i] & bweb_i) | (db_i & ~bweb_i);
    if (!ceb_a_i) qa_o <= mem_q[aa_i];
  end

endmodule
