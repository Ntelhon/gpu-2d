// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Pipelined exact integer square root (rendering.md §10.4): out = floor(sqrt(in)), digit by digit, one result bit
// per stage (restoring: two argument bits enter the remainder per stage, the trial subtrahend is 4·root + 1).
// Global stall: every stage advances when en_i is high, so the owner (g2d_grad) stalls it with its own
// pipeline; valid bits live in the owner. Latency STAGES = OUT_W cycles. TAG_W bits ride along.
//
//   rem_0 = 0, root_0 = 0; for k = OUT_W-1 .. 0:
//     rem  = (rem << 2) | in[2k+1:2k]
//     t    = (root << 2) | 1
//     rem >= t ? (rem -= t, root = 2·root + 1) : (root = 2·root)
//   Invariant: root² + rem = in >> 2k, rem ≤ 2·root (so rem fits OUT_W + 2 bits).

module g2d_isqrt #(
  parameter int unsigned OUT_W = 32,               // result width; the argument has 2·OUT_W bits
  parameter int unsigned TAG_W = 1
) (
  input  logic                 clk_i,
  input  logic                 en_i,
  input  logic [2*OUT_W-1:0]   in_i,
  input  logic [TAG_W-1:0]     in_tag_i,
  output logic [OUT_W-1:0]     out_o,
  output logic [TAG_W-1:0]     out_tag_o
);

  localparam int unsigned RW = OUT_W + 2;          // remainder width

  logic [OUT_W:0][2*OUT_W-1:0] arg_q;              // argument bits not yet consumed (shifted left)
  logic [OUT_W:0][RW-1:0]      rem_q;
  logic [OUT_W:0][OUT_W-1:0]   root_q;
  logic [OUT_W:0][TAG_W-1:0]   tag_q;

  assign arg_q[0]  = in_i;
  assign rem_q[0]  = '0;
  assign root_q[0] = '0;
  assign tag_q[0]  = in_tag_i;

  for (genvar k = 0; k < OUT_W; k++) begin : g_stage
    logic [RW-1:0]    rem_sh, trial, rem_d;
    logic [OUT_W-1:0] root_d;
    logic             ge;
    assign rem_sh = {rem_q[k][RW-3:0], arg_q[k][2*OUT_W-1 -: 2]};
    assign trial  = {root_q[k], 2'b01};
    assign ge     = rem_sh >= trial;
    assign rem_d  = ge ? rem_sh - trial : rem_sh;
    assign root_d = {root_q[k][OUT_W-2:0], ge};
    always_ff @(posedge clk_i) begin
      if (en_i) begin
        arg_q[k+1]  <= {arg_q[k][2*OUT_W-3:0], 2'b00};
        rem_q[k+1]  <= rem_d;
        root_q[k+1] <= root_d;
        tag_q[k+1]  <= tag_q[k];
      end
    end
  end

  assign out_o     = root_q[OUT_W];
  assign out_tag_o = tag_q[OUT_W];

  logic unused;
  assign unused = ^{arg_q[OUT_W], rem_q[OUT_W], rem_q[0][RW-1:RW-2], root_q[0][OUT_W-1]};

endmodule
