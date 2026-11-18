// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Pipelined signed floor division by a positive divisor: q = floor(n / d), r = n - q*d, 0 <= r < d.
// One division per clock, QW + 2 stages, global stall (in_ready_o = output register free or accepted).
//
// Input contract: d >= 1 and |n| < d * 2^QW (the quotient magnitude fits QW bits). Outside it ovf_o = 1
// and q/r are unspecified. The users (path rasterizer: edge step S*dx/dy, band start crossing) size QW
// from their value ranges, so ovf_o never fires there; it is checked by the testbench and a simulation
// assertion in the users.
//
//   S0  m = |n|; R0 = m >> QW (< d by the contract), the QW low bits of m are shifted in one per stage
//   Sk  restoring step: R = 2R + bit; if R >= d: R -= d, quotient bit = 1
//   Sx  sign fix: n >= 0: (q0, r0); n < 0: r0 = 0 -> (-q0, 0), else (-q0 - 1, d - r0)

module g2d_sdiv #(
  parameter int unsigned NW    = 44,   // dividend width (signed)
  parameter int unsigned DW    = 21,   // divisor width (unsigned, >= 1)
  parameter int unsigned QW    = 31,   // quotient magnitude bits
  parameter int unsigned TAG_W = 8
) (
  input  logic                    clk_i,
  input  logic                    rst_ni,
  input  logic                    clr_i,
  input  logic                    in_valid_i,
  output logic                    in_ready_o,
  input  logic signed [NW-1:0]    in_n_i,
  input  logic [DW-1:0]           in_d_i,
  input  logic [TAG_W-1:0]        in_tag_i,
  output logic                    out_valid_o,
  input  logic                    out_ready_i,
  output logic signed [QW:0]      out_q_o,
  output logic [DW-1:0]           out_r_o,
  output logic                    out_ovf_o,
  output logic [TAG_W-1:0]        out_tag_o
);

  localparam int unsigned NS = QW + 2;
  localparam int unsigned RW = DW + 1;          // remainder register: 2R + 1 < 2d

  logic [NS-1:0] v_q;
  logic          en;
  assign en          = ~v_q[NS-1] | out_ready_i;
  assign in_ready_o  = en;
  assign out_valid_o = v_q[NS-1];

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)    v_q <= '0;
    else if (clr_i) v_q <= '0;
    else if (en)    v_q <= {v_q[NS-2:0], in_valid_i};
  end

  // ---------------------------------------------------------------- S0
  logic [NW-1:0] mag;
  assign mag = in_n_i[NW-1] ? NW'(-in_n_i) : NW'(in_n_i);   // |n| (the most negative value stays exact)

  logic [QW:0][RW-1:0]  r_q;      // remainder entering stage k
  logic [QW:0][QW-1:0]  lo_q;     // low dividend bits still to shift in (MSB first)
  logic [QW:0][QW-1:0]  q_q;      // quotient bits so far
  logic [QW:0][DW-1:0]  d_q;
  logic [QW:0]          neg_q, ovf_q;
  logic [QW:0][TAG_W-1:0] tag_q;

  logic [NW-1:0] hi;
  assign hi = mag >> QW;

  always_ff @(posedge clk_i) begin
    if (en) begin
      r_q[0]   <= RW'(hi);
      lo_q[0]  <= mag[QW-1:0];
      q_q[0]   <= '0;
      d_q[0]   <= in_d_i;
      neg_q[0] <= in_n_i[NW-1];
      ovf_q[0] <= (hi >= NW'(in_d_i)) || (in_d_i == '0);
      tag_q[0] <= in_tag_i;
    end
  end

  // ---------------------------------------------------------------- restoring stages
  for (genvar k = 0; k < QW; k++) begin : g_st
    logic [RW:0] t;
    logic        b;
    assign t = {r_q[k], lo_q[k][QW-1]};
    assign b = t >= (RW + 1)'(d_q[k]);
    always_ff @(posedge clk_i) begin
      if (en) begin
        r_q[k+1]   <= b ? RW'(t - (RW + 1)'(d_q[k])) : RW'(t);
        lo_q[k+1]  <= {lo_q[k][QW-2:0], 1'b0};
        q_q[k+1]   <= {q_q[k][QW-2:0], b};
        d_q[k+1]   <= d_q[k];
        neg_q[k+1] <= neg_q[k];
        ovf_q[k+1] <= ovf_q[k];
        tag_q[k+1] <= tag_q[k];
      end
    end
  end

  // ---------------------------------------------------------------- sign fix
  logic          zero_r;
  logic [DW-1:0] r0;
  assign r0     = r_q[QW][DW-1:0];
  assign zero_r = (r0 == '0);

  always_ff @(posedge clk_i) begin
    if (en) begin
      if (!neg_q[QW]) begin
        out_q_o <= $signed({1'b0, q_q[QW]});
        out_r_o <= r0;
      end else if (zero_r) begin
        out_q_o <= -$signed({1'b0, q_q[QW]});
        out_r_o <= '0;
      end else begin
        out_q_o <= -$signed({1'b0, q_q[QW]}) - (QW + 1)'(1);
        out_r_o <= d_q[QW] - r0;
      end
      out_ovf_o <= ovf_q[QW];
      out_tag_o <= tag_q[QW];
    end
  end

  logic unused;
  assign unused = ^{lo_q[QW], r_q[QW][RW-1]};   // all bits shifted in; remainder < d fits DW bits

endmodule
