// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Inverse transform (rendering.md §7.1) with an exact perspective divide. One pixel per clock, global
// stall pipeline of 3 + DIV_STAGES stages.
//
//   xh = 2x+1, yh = 2y+1
//   U = A*xh + B*yh + 2C,  V = D*xh + E*yh + 2F,  W = G*xh + H*yh + 2I       (exact, 49-bit signed)
//   affine:      u = U >> 1,  v = V >> 1
//   perspective: W <= 0 -> skipped; u = floor(U * 2^30 / W), v = floor(V * 2^30 / W)
//
// Outputs u, v as signed 32-bit s15.16 values with ok_o (rendering.md §7.4):
//   cfg_wrap_i = 0 (WRAP = NONE): ok iff u, v in [0, 2^29) (only those can pass the source test,
//                u >> 16 < sx1 <= 8192); exact for every input:
//                  affine:      0 <= U < 2^30
//                  perspective: W > 0, U >= 0 and 2U < W
//   cfg_wrap_i = 1 (v1.4 wrap modes): ok iff u, v in [-2^31, 2^31):
//                  affine:      -2^32 <= U < 2^32
//                  perspective: W > 0 and -2W <= U < 2W
// The perspective quotient is computed on |U| by a radix-2 restoring divider with divisor 4W and initial
// remainder |U| < 4W, one quotient bit per stage, DIV_STAGES = 32 stages: q = floor(|U| * 2^32 / 4W) =
// floor(|U| * 2^30 / W) <= 2^31. Then u = q for U >= 0, u = -q - (remainder != 0) = floor(U * 2^30 / W)
// for U < 0. ok_o = 0 pixels carry don't-care coordinates (cov = 0).
// cfg_* are static while pixels are in flight. tag_i is carried alongside.

module g2d_xform #(
  parameter int unsigned TAG_W = 8,
  localparam int unsigned QW = 32,              // quotient / coordinate bits
  localparam int unsigned DIV_STAGES = QW
) (
  input  logic               clk_i,
  input  logic               rst_ni,
  input  logic               clr_i,
  // draw configuration: XF_A..XF_I (A..F s15.16, G..I s1.30), PAINT_CTRL.PERSP, PAINT_CTRL.WRAP != NONE
  input  logic [8:0][31:0]   cfg_xf_i,
  input  logic               cfg_persp_i,
  input  logic               cfg_wrap_i,
  // pixel in
  input  logic               in_valid_i,
  output logic               in_ready_o,
  input  logic [13:0]        in_x_i,
  input  logic [13:0]        in_y_i,
  input  logic [TAG_W-1:0]   in_tag_i,
  // out
  output logic               out_valid_o,
  input  logic               out_ready_i,
  output logic               out_ok_o,
  output logic [QW-1:0]      out_u_o,      // signed s15.16
  output logic [QW-1:0]      out_v_o,
  output logic [TAG_W-1:0]   out_tag_o
);

  localparam int unsigned NS = 3 + DIV_STAGES;
  localparam int unsigned PW = 48;   // product width: s32 * u15
  localparam int unsigned SW = 52;   // sums, divisor 4W and remainders (W < 2^49)

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

  // ---------------------------------------------------------------- S0: products
  logic signed [15:0] xh, yh;
  assign xh = $signed({1'b0, in_x_i, 1'b1});
  assign yh = $signed({1'b0, in_y_i, 1'b1});

  logic signed [PW-1:0] p_ax, p_by, p_dx, p_ey, p_gx, p_hy;
  logic signed [32:0]   c2, f2, i2;
  logic [TAG_W-1:0]     t0;

  always_ff @(posedge clk_i) begin
    if (en) begin
      p_ax <= PW'($signed(cfg_xf_i[0]) * xh);
      p_by <= PW'($signed(cfg_xf_i[1]) * yh);
      p_dx <= PW'($signed(cfg_xf_i[3]) * xh);
      p_ey <= PW'($signed(cfg_xf_i[4]) * yh);
      p_gx <= PW'($signed(cfg_xf_i[6]) * xh);
      p_hy <= PW'($signed(cfg_xf_i[7]) * yh);
      c2   <= {cfg_xf_i[2], 1'b0};
      f2   <= {cfg_xf_i[5], 1'b0};
      i2   <= {cfg_xf_i[8], 1'b0};
      t0   <= in_tag_i;
    end
  end

  // ---------------------------------------------------------------- S1: sums, range checks
  logic signed [SW-1:0] su, sv, sw;
  assign su = SW'(p_ax) + SW'(p_by) + SW'(c2);
  assign sv = SW'(p_dx) + SW'(p_ey) + SW'(f2);
  assign sw = SW'(p_gx) + SW'(p_hy) + SW'(i2);

  logic [SW-1:0] au, av, w2, w4;   // |U|, |V|, 2W, 4W
  assign au = su[SW-1] ? SW'(-su) : su;
  assign av = sv[SW-1] ? SW'(-sv) : sv;
  assign w2 = {sw[SW-2:0], 1'b0};
  assign w4 = {sw[SW-3:0], 2'b00};

  logic aff_ok, per_ok, aff_wok, per_wok, w_pos;
  // NONE, affine: 0 <= U < 2^30 and 0 <= V < 2^30
  assign aff_ok  = (su[SW-1:30] == '0) && (sv[SW-1:30] == '0);
  // NONE, perspective: W > 0, U >= 0, V >= 0, 2U < W, 2V < W (all non-negative here, so unsigned compares)
  assign w_pos   = !sw[SW-1] && (sw != '0);
  assign per_ok  = w_pos && !su[SW-1] && !sv[SW-1] &&
                   ({su[SW-2:0], 1'b0} < sw) && ({sv[SW-2:0], 1'b0} < sw);
  // wrap, affine: -2^32 <= U < 2^32 (same for V)
  assign aff_wok = (su[SW-1:32] == '0 || su[SW-1:32] == '1) && (sv[SW-1:32] == '0 || sv[SW-1:32] == '1);
  // wrap, perspective: W > 0 and -2W <= U < 2W, i.e. U >= 0: |U| < 2W; U < 0: |U| <= 2W
  assign per_wok = w_pos && (su[SW-1] ? au <= w2 : au < w2) && (sv[SW-1] ? av <= w2 : av < w2);

  // pipeline stage k (0 = after S1): remainders, divisor, partial quotients, ok, signs
  logic [DIV_STAGES:0][SW-1:0] ru_q, rv_q;
  logic [DIV_STAGES-1:0][SW-1:0] w_q;              // the output stage needs no divisor
  logic [DIV_STAGES:0][QW-1:0] qu_q, qv_q;
  logic [DIV_STAGES:0]         ok_q, nu_q, nv_q;
  logic [DIV_STAGES:0][TAG_W-1:0] tag_q;

  always_ff @(posedge clk_i) begin
    if (en) begin
      ok_q[0]  <= cfg_wrap_i ? (cfg_persp_i ? per_wok : aff_wok) : (cfg_persp_i ? per_ok : aff_ok);
      ru_q[0]  <= au;
      rv_q[0]  <= av;
      w_q[0]   <= w4;
      nu_q[0]  <= cfg_persp_i & su[SW-1];
      nv_q[0]  <= cfg_persp_i & sv[SW-1];
      qu_q[0]  <= cfg_persp_i ? '0 : su[QW:1];   // affine: u = U >> 1 (low 32 bits; in range when ok)
      qv_q[0]  <= cfg_persp_i ? '0 : sv[QW:1];
      tag_q[0] <= t0;
    end
  end

  // ---------------------------------------------------------------- divider stages
  // Stage k produces quotient bit QW-1-k. Remainder r < 4W < 2^51, so 2r fits SW bits.
  for (genvar k = 0; k < DIV_STAGES; k++) begin : g_div
    logic [SW-1:0] tu, tv;
    logic          bu, bv;
    assign tu = {ru_q[k][SW-2:0], 1'b0};
    assign tv = {rv_q[k][SW-2:0], 1'b0};
    assign bu = (tu >= w_q[k]);
    assign bv = (tv >= w_q[k]);
    always_ff @(posedge clk_i) begin
      if (en) begin
        qu_q[k+1]  <= qu_q[k];
        qv_q[k+1]  <= qv_q[k];
        if (cfg_persp_i) begin
          qu_q[k+1][QW-1-k] <= bu;
          qv_q[k+1][QW-1-k] <= bv;
        end
        ru_q[k+1]  <= bu ? tu - w_q[k] : tu;
        rv_q[k+1]  <= bv ? tv - w_q[k] : tv;
        ok_q[k+1]  <= ok_q[k];
        nu_q[k+1]  <= nu_q[k];
        nv_q[k+1]  <= nv_q[k];
        tag_q[k+1] <= tag_q[k];
      end
    end
    if (k < DIV_STAGES - 1) begin : g_w
      always_ff @(posedge clk_i) begin
        if (en) w_q[k+1] <= w_q[k];
      end
    end
  end

  // ---------------------------------------------------------------- output: sign of the perspective result
  // Restoring division of |U| * 2^QW by 4W: each stage doubles the remainder and subtracts 4W when
  // possible, so after QW stages q = floor(|U| * 2^30 / W). In affine mode the stages only carry the
  // values (the divider result is discarded). Negative U: floor(U * 2^30 / W) = -q - (remainder != 0).
  logic [QW-1:0] qu, qv;
  assign qu = qu_q[DIV_STAGES];
  assign qv = qv_q[DIV_STAGES];

  always_ff @(posedge clk_i) begin
    if (en) begin
      out_ok_o  <= ok_q[DIV_STAGES];
      out_u_o   <= nu_q[DIV_STAGES] ? QW'(-qu - QW'(ru_q[DIV_STAGES] != '0)) : qu;
      out_v_o   <= nv_q[DIV_STAGES] ? QW'(-qv - QW'(rv_q[DIV_STAGES] != '0)) : qv;
      out_tag_o <= tag_q[DIV_STAGES];
    end
  end

endmodule
