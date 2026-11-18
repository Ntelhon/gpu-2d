// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Gradient ramp index: linear (rendering.md §10.1) and radial (§10.4, v1.5). Global-stall pipeline; x, y pass
// through.
//
//   linear (cfg_radial_i = 0, 2 stages):
//     T = P*xh + Q*yh + R   (xh = 2x+1, yh = 2y+1; P, Q signed 32-bit, R signed 48-bit; exact in 51 bits)
//     i = T >> 17 (arithmetic)
//   radial (cfg_radial_i = 1, RL = 5 + 32 stages): U and V like T from their own coefficients,
//     u = sat32(U >> 9), v = sat32(V >> 9), S = isqrt(u*u + v*v) (exact, g2d_isqrt), i = ((T >> 9) + S) >> 8
//   spread of i: PAD: clamp(i, 0, 255); REPEAT: i & 255; REFLECT: k = i & 511, i = k < 256 ? k : 511 - k;
//   FILL (v1.4, Q-API-6): fill = i outside [0, 255] (the paint colour is used), otherwise as PAD
//
// The path is chosen per draw (cfg_radial_i is static while g2d_pix holds a draw, and the pipeline is empty
// between draws), so the two valid chains never hold pixels at the same time and output order is kept.

module g2d_grad (
  input  logic               clk_i,
  input  logic               rst_ni,
  input  logic               clr_i,
  input  logic [31:0]        cfg_p_i,
  input  logic [31:0]        cfg_q_i,
  input  logic [47:0]        cfg_r_i,
  input  logic               cfg_radial_i,
  input  logic [31:0]        cfg_up_i,
  input  logic [31:0]        cfg_uq_i,
  input  logic [47:0]        cfg_ur_i,
  input  logic [31:0]        cfg_vp_i,
  input  logic [31:0]        cfg_vq_i,
  input  logic [47:0]        cfg_vr_i,
  input  logic [1:0]         cfg_spread_i,
  input  logic               in_valid_i,
  output logic               in_ready_o,
  input  logic [13:0]        in_x_i,
  input  logic [13:0]        in_y_i,
  output logic               out_valid_o,
  input  logic               out_ready_i,
  output logic [13:0]        out_x_o,
  output logic [13:0]        out_y_o,
  output logic [7:0]         out_idx_o,
  output logic               out_fill_o
);

  localparam int unsigned SQ = 32;              // isqrt stages
  localparam int unsigned RL = 5 + SQ;          // radial latency

  logic [1:0]    vs_q;                          // linear chain
  logic [RL-1:0] vl_q;                          // radial chain
  logic          en;
  assign out_valid_o = vs_q[1] | vl_q[RL-1];
  assign en          = ~out_valid_o | out_ready_i;
  assign in_ready_o  = en;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      vs_q <= '0;
      vl_q <= '0;
    end else if (clr_i) begin
      vs_q <= '0;
      vl_q <= '0;
    end else if (en) begin
      vs_q <= {vs_q[0], in_valid_i & ~cfg_radial_i};
      vl_q <= {vl_q[RL-2:0], in_valid_i & cfg_radial_i};
    end
  end

  // spread of a signed ramp index (35 bits covers both paths)
  function automatic logic [8:0] spread(logic signed [34:0] i, logic [1:0] mode);   // {fill, idx}
    logic [8:0] k;
    logic [7:0] idx;
    logic       out;
    k   = i[8:0];
    out = i[34] || i > 35'sd255;
    unique case (mode)
      2'd1:    idx = i[7:0];                                  // REPEAT
      2'd2:    idx = k[8] ? 8'(9'd511 - k) : k[7:0];          // REFLECT
      default: idx = i[34] ? 8'd0 : out ? 8'd255 : i[7:0];    // PAD (FILL inside)
    endcase
    return {mode == 2'd3 && out, idx};
  endfunction

  // S0: products of all three affine functions
  logic signed [47:0] px_q, qy_q, upx_q, uqy_q, vpx_q, vqy_q;
  logic [13:0]        x0_q, y0_q;
  logic signed [15:0] xh, yh;
  assign xh = $signed({1'b0, in_x_i, 1'b1});
  assign yh = $signed({1'b0, in_y_i, 1'b1});
  always_ff @(posedge clk_i) begin
    if (en) begin
      px_q  <= 48'($signed(cfg_p_i) * xh);
      qy_q  <= 48'($signed(cfg_q_i) * yh);
      upx_q <= 48'($signed(cfg_up_i) * xh);
      uqy_q <= 48'($signed(cfg_uq_i) * yh);
      vpx_q <= 48'($signed(cfg_vp_i) * xh);
      vqy_q <= 48'($signed(cfg_vq_i) * yh);
      x0_q  <= in_x_i;
      y0_q  <= in_y_i;
    end
  end

  logic signed [50:0] t, u51, v51;
  assign t   = 51'(px_q) + 51'(qy_q) + 51'($signed(cfg_r_i));
  assign u51 = 51'(upx_q) + 51'(uqy_q) + 51'($signed(cfg_ur_i));
  assign v51 = 51'(vpx_q) + 51'(vqy_q) + 51'($signed(cfg_vr_i));

  // ---------------------------------------------------------------- linear: S1 shift, spread
  logic [8:0] lin;
  assign lin = spread(35'($signed(t[50:17])), cfg_spread_i);   // sign-extended: a part-select is unsigned

  logic [7:0]  lin_idx_q;
  logic        lin_fill_q;
  logic [13:0] lin_x_q, lin_y_q;
  always_ff @(posedge clk_i) begin
    if (en) begin
      lin_idx_q  <= lin[7:0];
      lin_fill_q <= lin[8];
      lin_x_q    <= x0_q;
      lin_y_q    <= y0_q;
    end
  end

  // ---------------------------------------------------------------- radial
  function automatic logic signed [31:0] sat32(logic signed [41:0] v);
    if (v > 42'sh0_7FFF_FFFF)       return 32'sh7FFF_FFFF;
    else if (v < -42'sh0_8000_0000) return 32'sh8000_0000;
    else                            return v[31:0];
  endfunction

  // R1: T >> 9, saturated U >> 9, V >> 9
  logic signed [41:0] t9_q;
  logic signed [31:0] u_q, v_q;
  logic [13:0]        x1_q, y1_q;
  always_ff @(posedge clk_i) begin
    if (en) begin
      t9_q <= t[50:9];
      u_q  <= sat32(u51[50:9]);
      v_q  <= sat32(v51[50:9]);
      x1_q <= x0_q;
      y1_q <= y0_q;
    end
  end

  // R2: squares (each <= 2^62)
  logic [62:0]        uu_q, vv_q;
  logic signed [41:0] t9_2_q;
  logic [13:0]        x2_q, y2_q;
  always_ff @(posedge clk_i) begin
    if (en) begin
      uu_q   <= 63'(u_q * u_q);
      vv_q   <= 63'(v_q * v_q);
      t9_2_q <= t9_q;
      x2_q   <= x1_q;
      y2_q   <= y1_q;
    end
  end

  // R3: sum (<= 2^63)
  logic [63:0]        qq_q;
  logic signed [41:0] t9_3_q;
  logic [13:0]        x3_q, y3_q;
  always_ff @(posedge clk_i) begin
    if (en) begin
      qq_q   <= 64'(uu_q) + 64'(vv_q);
      t9_3_q <= t9_2_q;
      x3_q   <= x2_q;
      y3_q   <= y2_q;
    end
  end

  // R4..R4+SQ-1: square root; T >> 9, x, y ride along as its tag
  logic [SQ-1:0] s_root;
  logic [41:0]   t9_s;
  logic [13:0]   x_s, y_s;
  g2d_isqrt #(.OUT_W(SQ), .TAG_W(42 + 28)) u_isqrt (
    .clk_i,
    .en_i      (en),
    .in_i      (qq_q),
    .in_tag_i  ({t9_3_q, x3_q, y3_q}),
    .out_o     (s_root),
    .out_tag_o ({t9_s, x_s, y_s})
  );

  // last radial stage: sum, shift, spread
  logic signed [42:0] g;
  logic [8:0]         rad;
  assign g   = 43'($signed(t9_s)) + 43'({11'd0, s_root});
  assign rad = spread(g[42:8], cfg_spread_i);

  logic [7:0]  rad_idx_q;
  logic        rad_fill_q;
  logic [13:0] rad_x_q, rad_y_q;
  always_ff @(posedge clk_i) begin
    if (en) begin
      rad_idx_q  <= rad[7:0];
      rad_fill_q <= rad[8];
      rad_x_q    <= x_s;
      rad_y_q    <= y_s;
    end
  end

  assign out_idx_o  = cfg_radial_i ? rad_idx_q : lin_idx_q;
  assign out_fill_o = cfg_radial_i ? rad_fill_q : lin_fill_q;
  assign out_x_o    = cfg_radial_i ? rad_x_q : lin_x_q;
  assign out_y_o    = cfg_radial_i ? rad_y_q : lin_y_q;

  logic unused;
  assign unused = ^{t[16:0], u51[8:0], v51[8:0], g[7:0]};

endmodule
