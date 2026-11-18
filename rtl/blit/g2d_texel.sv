// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Texel processing and filtering (rendering.md §5.2, §7.3, §5.3 image mode). Six-stage global-stall
// pipeline, four texels per clock.
//
//   S0  expand(SRC_FMT) each texel (v1.6b YUV: the assembled {A, Y, U, V} converted, rendering.md §3.7);
//       FORCE_OPAQUE -> A = 255
//   S1  colour key: the lowest-numbered enabled key whose RGB range holds the texel replaces its A
//   S2  SRC_PREMUL: R, G, B = mul8(c, A); border texels (WRAP = COLOR, §7.5) replaced by BORDER_COLOR;
//       bilinear weights from fx, fy
//   S3  per channel products c * w (4 texels)
//   S4  clamp((sum + 32768) >>> 16, 0, 255) (signed: Gaussian weights may be negative); v1.6b Gaussian: the four groups of a pixel (g2d_tex_seq) are summed first, only the
//       last group (in_last_i) leaves S4 (weights per group and texel from GAUSS_W, rendering.md §7.3)
//   C0  colour unit (v1.6b, rendering.md §5.2.1): gamma on R, G, B (sRGB tables, g2d_gamma_pkg)
//   C1  matrix products M[o][j] * c[j] (16, signed s7.8)
//   C2  o = clamp((sum + B[o] + 128) >>> 8); bypassed (the gamma output) when COLOR_CTRL.EN = 0
//   S5  IMG_MUL: every channel mul8(S.c, PAINT_COLOR.c)
// Texels: t[0] = (x0,y0), t[1] = (x1,y0), t[2] = (x0,y1), t[3] = (x1,y1), raw stored values right-aligned.
// POINT sampling arrives as fx = fy = 0 (weight 65536 on t[0]). pass_i is carried (the texels of a
// pixel that fails the source test are don't-care). Indexed sources arrive as their CLUT words (fmt_expand
// passes them through, rendering.md §3.5).

module g2d_texel
  import g2d_pkg::*;
  import g2d_gamma_pkg::*;
#(
  parameter int unsigned TAG_W = 8
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  // draw configuration
  input  logic [7:0]        cfg_fmt_i,
  input  logic              cfg_force_opaque_i,
  input  logic [7:0][31:0]  cfg_ckey_i,       // CKEY0_LO, CKEY0_HI, ..., CKEY3_HI (state words 0x10..0x17)
  input  logic              cfg_premul_i,
  input  logic              cfg_img_mul_i,
  input  logic [31:0]       cfg_paint_i,
  input  logic [31:0]       cfg_border_i,
  input  logic              cfg_color_en_i,   // COLOR_CTRL.EN (v1.6b)
  input  logic [1:0]        cfg_gamma_i,      // COLOR_CTRL.GAMMA: 0 none, 1 sRGB -> linear, 2 linear -> sRGB
  input  logic [3:0][3:0][15:0] cfg_cm_i,     // M[o][j], o / j = 0 A, 1 R, 2 G, 3 B (signed s7.8)
  input  logic [3:0][19:0]  cfg_cb_i,         // B[o] (signed, 1/256 channel steps)
  input  logic              cfg_yuv_i,        // YUV source (v1.6b)
  input  logic              cfg_yuv709_i,     // SRC_FMT.YUV_709
  input  logic              cfg_gauss_i,
  input  logic [15:0]       cfg_w0_i,         // GAUSS_W0 / GAUSS_W: signed s3.12 weights, any values (§7.3)
  input  logic [15:0]       cfg_w1_i,
  input  logic [15:0]       cfg_w2_i,
  // in
  input  logic              in_valid_i,
  output logic              in_ready_o,
  input  logic              in_pass_i,
  input  logic [3:0][31:0]  in_tex_i,
  input  logic [3:0]        in_border_i,
  input  logic [7:0]        in_fx_i,
  input  logic [7:0]        in_fy_i,
  input  logic [1:0]        in_grp_i,         // Gaussian group (v1.6b)
  input  logic              in_last_i,        // last group of the pixel (1 when not Gaussian)
  input  logic [TAG_W-1:0]  in_tag_i,
  // out
  output logic              out_valid_o,
  input  logic              out_ready_i,
  output logic              out_pass_o,
  output logic [31:0]       out_s_o,
  output logic              out_azero_o,      // filtered A = 0 before IMG_MUL (§5.2 step 6, TRANSPARENT)
  output logic [TAG_W-1:0]  out_tag_o
);

  localparam int unsigned NS = 9;

  logic [NS-1:0] v_q;
  logic          en;
  assign en          = ~v_q[NS-1] | out_ready_i;
  assign in_ready_o  = en;
  assign out_valid_o = v_q[NS-1];

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)    v_q <= '0;
    else if (clr_i) v_q <= '0;
    else if (en)    v_q <= {v_q[NS-2:4], v_q[3] & last_q[3], v_q[2:0], in_valid_i};   // S4 keeps non-last groups
  end

  // per-stage sideband: pass, fractions, tag
  logic [NS-2:0]            pass_q;
  logic [NS-2:0][TAG_W-1:0] tag_q;
  logic [1:0][7:0]          f0_q, f1_q;
  logic [3:0]               b0_q, b1_q;
  logic [3:0]               last_q;           // pixel's last group, per stage S0..S3
  logic [1:0][1:0]          grp_q;            // Gaussian group at S0, S1

  always_ff @(posedge clk_i) begin
    if (en) begin
      last_q <= {last_q[2:0], in_last_i};
      grp_q  <= {grp_q[0], in_grp_i};
      pass_q <= {pass_q[NS-3:0], in_pass_i};
      tag_q  <= {tag_q[NS-3:0], in_tag_i};
      f0_q   <= {in_fy_i, in_fx_i};
      f1_q   <= f0_q;
      b0_q   <= in_border_i;
      b1_q   <= b0_q;
    end
  end

  // ---------------------------------------------------------------- S0: expand, force opaque
  logic [3:0][31:0] t0;
  always_ff @(posedge clk_i) begin
    if (en) begin
      for (int k = 0; k < 4; k++) begin
        t0[k] <= cfg_yuv_i ? yuv_expand(in_tex_i[k], cfg_yuv709_i) : fmt_expand(cfg_fmt_i, in_tex_i[k]);
        if (cfg_force_opaque_i) t0[k][31:24] <= 8'hFF;
      end
    end
  end

  // ---------------------------------------------------------------- S1: colour key
  function automatic logic in_range(logic [7:0] c, logic [7:0] lo, logic [7:0] hi);
    return (c >= lo) && (c <= hi);
  endfunction

  logic [3:0][31:0] t1;
  always_ff @(posedge clk_i) begin
    if (en) begin
      for (int k = 0; k < 4; k++) begin
        logic [31:0] c;
        logic        hit;
        c   = t0[k];
        hit = 1'b0;
        for (int n = 0; n < 4; n++) begin
          logic [31:0] hi;
          logic [23:0] lo;
          lo = cfg_ckey_i[2 * n][23:0];
          hi = cfg_ckey_i[2 * n + 1];
          if (!hit && cfg_ckey_i[2 * n][31] && in_range(c[23:16], lo[23:16], hi[23:16]) &&
              in_range(c[15:8], lo[15:8], hi[15:8]) && in_range(c[7:0], lo[7:0], hi[7:0])) begin
            hit       = 1'b1;
            c[31:24]  = hi[31:24];
          end
        end
        t1[k] <= c;
      end
    end
  end

  // ---------------------------------------------------------------- S2: premultiply, weights
  logic [3:0][31:0] t2;
  // weights on the 1/65536 scale, signed 21 bit: bilinear 0..65536, Gaussian s3.12 x 16
  logic [3:0][20:0] w2;   // w00, w10, w01, w11
  logic [20:0]      gw0, gw1, gw2;
  assign gw0 = {{1{cfg_w0_i[15]}}, cfg_w0_i, 4'b0000};
  assign gw1 = {{1{cfg_w1_i[15]}}, cfg_w1_i, 4'b0000};
  assign gw2 = {{1{cfg_w2_i[15]}}, cfg_w2_i, 4'b0000};
  logic [8:0] fx9, fy9, ifx9, ify9;
  assign fx9  = 9'(f1_q[0]);
  assign fy9  = 9'(f1_q[1]);
  assign ifx9 = 9'd256 - fx9;
  assign ify9 = 9'd256 - fy9;

  always_ff @(posedge clk_i) begin
    if (en) begin
      for (int k = 0; k < 4; k++) begin
        t2[k] <= t1[k];
        if (b1_q[k]) begin
          t2[k] <= cfg_border_i;
        end else if (cfg_premul_i) begin
          t2[k][23:16] <= mul8(t1[k][23:16], t1[k][31:24]);
          t2[k][15:8]  <= mul8(t1[k][15:8],  t1[k][31:24]);
          t2[k][7:0]   <= mul8(t1[k][7:0],   t1[k][31:24]);
        end
      end
      if (cfg_gauss_i) begin
        // texels t00, t10, t01, t11 of the group's 2 x 2 (g2d_tex_seq): corners W2, edges W1, centre W0
        unique case (grp_q[1])
          2'd0:    w2 <= {gw0, gw1, gw1, gw2};
          2'd1:    w2 <= {21'd0, gw1, 21'd0, gw2};
          2'd2:    w2 <= {21'd0, 21'd0, gw1, gw2};
          default: w2 <= {21'd0, 21'd0, 21'd0, gw2};
        endcase
      end else begin
        w2[0] <= 21'(ifx9 * ify9);
        w2[1] <= 21'(fx9 * ify9);
        w2[2] <= 21'(ifx9 * fy9);
        w2[3] <= 21'(fx9 * fy9);
      end
    end
  end

  // ---------------------------------------------------------------- S3: products
  logic [3:0][3:0][29:0] p3;   // [channel][texel], signed
  always_ff @(posedge clk_i) begin
    if (en) begin
      for (int ch = 0; ch < 4; ch++) begin
        for (int k = 0; k < 4; k++) begin
          p3[ch][k] <= 30'($signed({1'b0, t2[k][8 * ch +: 8]}) * $signed(w2[k]));
        end
      end
    end
  end

  // ---------------------------------------------------------------- S4: sum (Gaussian: over the groups), round
  // sum <= 255 * 65536 + 32768 < 2^24 (the weights of all groups of a pixel sum to 65536)
  // signed sums (|sum| < 9 * 255 * 2^20 < 2^32); element selects of packed arrays are unsigned: $signed throughout
  logic [3:0][35:0] sum4, acc_q, acc_in, rnd4;
  logic [3:0][7:0]  sat4;
  logic [1:0]       grp3;
  always_comb begin
    for (int ch = 0; ch < 4; ch++) begin
      logic signed [19:0] q;
      acc_in[ch] = (grp3 == 2'd0) ? 36'd0 : acc_q[ch];
      sum4[ch]   = 36'($signed(acc_in[ch]) + 36'($signed(p3[ch][0])) + 36'($signed(p3[ch][1])) +
                      36'($signed(p3[ch][2])) + 36'($signed(p3[ch][3])));
      rnd4[ch]   = 36'($signed(sum4[ch]) + 36'sd32768);
      q          = 20'($signed(rnd4[ch]) >>> 16);
      sat4[ch]   = (q < 0) ? 8'd0 : (q > 20'sd255) ? 8'd255 : q[7:0];
    end
  end

  logic [31:0] s4;
  logic [1:0]  grp2;
  always_ff @(posedge clk_i) begin
    if (en) begin
      grp2 <= grp_q[1];
      grp3 <= grp2;
      for (int ch = 0; ch < 4; ch++) s4[8 * ch +: 8] <= sat4[ch];
      if (v_q[3]) acc_q <= sum4;
    end
  end

  // ---------------------------------------------------------------- C0: gamma (v1.6b)
  logic [31:0] g0, g1;
  logic        az0, az1, az2;
  always_ff @(posedge clk_i) begin
    if (en) begin
      g0[31:24] <= s4[31:24];
      for (int ch = 0; ch < 3; ch++) begin
        unique case (cfg_gamma_i)
          2'd1:    g0[8 * ch +: 8] <= g2d_gamma_lin(s4[8 * ch +: 8]);
          2'd2:    g0[8 * ch +: 8] <= g2d_gamma_srgb(s4[8 * ch +: 8]);
          default: g0[8 * ch +: 8] <= s4[8 * ch +: 8];
        endcase
      end
      az0 <= (s4[31:24] == 8'd0);       // TRANSPARENT: the filtered A, before the colour unit (§5.2 step 6)
    end
  end

  // ---------------------------------------------------------------- C1: matrix products
  // channel order of the matrix: 0 A, 1 R, 2 G, 3 B; in the ARGB32 word A is byte 3, B byte 0
  function automatic logic [7:0] chan(logic [31:0] c, int j);
    return c[8 * (3 - j) +: 8];
  endfunction
  logic signed [3:0][3:0][24:0] p1;
  always_ff @(posedge clk_i) begin
    if (en) begin
      for (int o = 0; o < 4; o++)
        for (int j = 0; j < 4; j++) p1[o][j] <= 25'($signed(cfg_cm_i[o][j])) * $signed({17'b0, chan(g0, j)});
      g1  <= g0;
      az1 <= az0;
    end
  end

  // ---------------------------------------------------------------- C2: sums, bias, clamp
  logic [31:0] c2;
  always_ff @(posedge clk_i) begin
    if (en) begin
      for (int o = 0; o < 4; o++) begin
        logic signed [27:0] s;
        logic signed [19:0] q;
        // element selects of a packed array are unsigned: sign-extend explicitly
        s = 28'($signed(p1[o][0])) + 28'($signed(p1[o][1])) + 28'($signed(p1[o][2])) + 28'($signed(p1[o][3])) +
            28'($signed(cfg_cb_i[o])) + 28'sd128;
        q = 20'(s >>> 8);
        c2[8 * (3 - o) +: 8] <= !cfg_color_en_i ? chan(g1, o) : (q < 0) ? 8'd0 : (q > 20'sd255) ? 8'd255 : q[7:0];
      end
      az2 <= az1;
    end
  end

  // ---------------------------------------------------------------- S5: image mode MULTIPLY
  always_ff @(posedge clk_i) begin
    if (en) begin
      for (int ch = 0; ch < 4; ch++) begin
        out_s_o[8 * ch +: 8] <= cfg_img_mul_i ? mul8(c2[8 * ch +: 8], cfg_paint_i[8 * ch +: 8]) : c2[8 * ch +: 8];
      end
      out_azero_o <= az2;
      out_pass_o <= pass_q[NS-2];
      out_tag_o  <= tag_q[NS-2];
    end
  end

  logic unused;
  always_comb begin
    unused = 1'b0;
    for (int n = 0; n < 4; n++) unused ^= ^cfg_ckey_i[2 * n][30:24];   // reserved CKEYn_LO bits
    for (int ch = 0; ch < 4; ch++) unused ^= ^{rnd4[ch][15:0], rnd4[ch][35]};   // below the rounding point; sign
  end

endmodule
