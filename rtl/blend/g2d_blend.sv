// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Blend unit (rendering.md §4 steps 3-6, §5.3 source global alpha, §5.5, §5.6, §3.3/§3.4). One pixel per
// clock, sixteen pipeline stages (v1.7) with a global stall (in_ready_o = output register free or accepted).
//
//   SI  input register (v1.7: cuts the paint-source mux in front of the blend unit, T-1)
//   SA  S' = global_alpha(S, src mode); D' = global_alpha(expand(fmt, D raw), dst mode); stencil: m(I.c, I.A),
//       m(I.A, P.A)
//   SB  stencil: ac = m(m(I.c, I.A), P.A)
//   S0  stencil: S = (aa, m(P.c, ac)); otherwise S' passes (v1.7: one 8-bit multiply per stage)
//   S1  per channel: the six products of §5.6 (m(S,~DA), m(S,D), m(D,~SA), m(S,DA), m(D,SA), m(S,SA))
//       and the LVGL operand X (sat(S+D), sat(D-S), |D-S|)
//   S2  m(X, SA) for the LVGL modes, final saturating adds / min / max
//   (v1.6, §5.7 blend families, cfg_family_i != 0: S1 also computes the OpenVG / SVG2 operands, the Porter-Duff
//    channel-mode operands and factors; S2 the numerators n, the result alpha ra and the Porter-Duff result;
//    Q1..Q8 divide n·255 + ra/2 by ra (restoring, one quotient bit per stage; the base result rides along)
//   S3  coverage blend: O = cov == 255 ? B : lerp8(D', B, cov)
//   S4  compress(fmt, O), with the §3.6 ordered dither when cfg_dither_i (Bayer value of the pixel's target
//       position in_dxy_i = {y & 3, x & 3})
//
// Draw configuration (cfg_*) is static while pixels are in flight. D raw is the stored destination pixel
// (right-aligned); it is ignored when the result does not depend on it (blend NONE with cov = 255).
// tag_i is carried alongside each pixel unchanged.

module g2d_blend
  import g2d_pkg::*;
#(
  parameter int unsigned TAG_W = 8
) (
  input  logic             clk_i,
  input  logic             rst_ni,
  input  logic             clr_i,
  // draw configuration
  input  logic [3:0]       cfg_mode_i,
  input  logic [1:0]       cfg_family_i,     // §5.7: 0 VG_LITE modes, 1 OpenVG, 2 SVG2, 3 Porter-Duff (v1.6)
  input  logic [28:0]      cfg_func_i,       // BLEND_FUNC (family 3)
  input  logic [31:0]      cfg_const_i,      // the Porter-Duff constant / stencil paint (PAINT_COLOR)
  input  logic             cfg_stencil_i,    // §5.3.1 stencil image mode (v1.6): in_s_i is the image value
  input  logic [7:0]       cfg_fmt_i,
  input  logic [1:0]       cfg_src_amode_i,
  input  logic [7:0]       cfg_src_aval_i,
  input  logic [1:0]       cfg_dst_amode_i,
  input  logic [7:0]       cfg_dst_aval_i,
  input  logic             cfg_dither_i,
  // pixel in
  input  logic             in_valid_i,
  output logic             in_ready_o,
  input  logic [31:0]      in_s_i,
  input  logic [31:0]      in_d_i,
  input  logic [7:0]       in_cov_i,
  input  logic [3:0]       in_dxy_i,
  input  logic [TAG_W-1:0] in_tag_i,
  // pixel out
  output logic             out_valid_o,
  input  logic             out_ready_i,
  output logic [31:0]      out_pix_o,
  output logic [TAG_W-1:0] out_tag_o
);

  localparam int unsigned NQ = 8;            // division stages (quotient < 256)
  localparam int unsigned NS = 10 + NQ;       // v1.7: + SI, SA, SB, S2P, S4P

  logic [NS-1:0] v_q;
  logic          en;
  assign en          = ~v_q[NS-1] | out_ready_i;
  assign in_ready_o  = en;
  assign out_valid_o = v_q[NS-1];

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)     v_q <= '0;
    else if (clr_i)  v_q <= '0;
    else if (en)     v_q <= {v_q[NS-2:0], in_valid_i};
  end

  function automatic logic [7:0] add8(logic [7:0] a, logic [7:0] b);
    logic [8:0] s;
    s = 9'(a) + 9'(b);
    return s[8] ? 8'hFF : s[7:0];
  endfunction
  function automatic logic [7:0] sub8(logic [7:0] a, logic [7:0] b);
    return (a > b) ? a - b : 8'd0;
  endfunction

  // ---- §5.7 blend family helpers (v1.6) ----
  function automatic logic [7:0] absd(logic [7:0] a, logic [7:0] b);
    return (a > b) ? a - b : b - a;
  endfunction
  // SVG2 separable blend function Bf(cb = d, cs = s)
  function automatic logic [7:0] svg_bf_f(logic [3:0] mode, logic [7:0] s, logic [7:0] d);
    unique case (mode)
      4'd0:    return s;
      4'd1:    return mul8(s, d);
      4'd2:    return add8(s, sub8(d, mul8(s, d)));
      4'd3:    return (s > d) ? s : d;
      4'd4:    return (s < d) ? s : d;
      4'd5:    return absd(d, s);
      default: return sub8(sub8(add8(s, d), mul8(s, d)), mul8(s, d));
    endcase
  endfunction
  // Porter-Duff channel mode (src: the source operand)
  function automatic logic [7:0] pd_cm(logic [3:0] cm, logic src, logic [7:0] cs, logic [7:0] cd, logic [7:0] as_,
                                       logic [7:0] ad);
    unique case (cm)
      4'd0:    return src ? mul8(cs, as_) : mul8(cd, ad);
      4'd1:    return cs;
      4'd2:    return cd;
      4'd3:    return add8(cs, cd);
      4'd4:    return sub8(cd, cs);
      4'd5:    return mul8(cs, cd);
      4'd6:    return absd(cd, cs);
      4'd7:    return add8(cs, sub8(cd, mul8(cs, cd)));
      4'd8:    return (cs > cd) ? cs : cd;
      4'd9:    return (cs < cd) ? cs : cd;
      4'd10:   return sub8(sub8(add8(cs, cd), mul8(cs, cd)), mul8(cs, cd));
      default: return src ? cs : cd;            // 15 BYPASS (11..14 rejected by the draw check)
    endcase
  endfunction
  function automatic logic [7:0] pd_fc(logic [4:0] f, logic [7:0] cs, logic [7:0] cd, logic [7:0] as_, logic [7:0] ad,
                                       logic [7:0] ac, logic [7:0] cc);
    unique case (f)
      5'd0:    return 8'd0;
      5'd1:    return 8'd255;
      5'd2:    return cs;
      5'd3:    return ~cs;
      5'd4:    return as_;
      5'd5:    return ~as_;
      5'd6:    return ad;
      5'd7:    return ~ad;
      5'd8:    return cd;
      5'd9:    return ~cd;
      5'd10:   return (as_ < ~ad) ? as_ : ~ad;
      5'd11:   return ac;
      5'd12:   return ~ac;
      5'd13:   return cc;
      5'd14:   return ~cc;
      5'd15:   return add8(~ad, mul8(ad, cd));
      default: return ~mul8(ad, cd);
    endcase
  endfunction
  function automatic logic [7:0] pd_fa(logic [3:0] f, logic [7:0] as_, logic [7:0] ad, logic [7:0] ac);
    unique case (f)
      4'd0:    return 8'd0;
      4'd1:    return 8'd255;
      4'd2:    return as_;
      4'd3:    return ~as_;
      4'd4:    return ad;
      4'd5:    return ~ad;
      4'd6:    return (as_ < ~ad) ? as_ : ~ad;
      4'd7:    return ac;
      default: return ~ac;
    endcase
  endfunction
  function automatic logic [7:0] pd_eq(logic [2:0] eq, logic [7:0] fs, logic [7:0] xs, logic [7:0] fd, logic [7:0] xd);
    unique case (eq)
      3'd0:    return add8(mul8(fs, xs), mul8(fd, xd));
      3'd1:    return sub8(mul8(fs, xs), mul8(fd, xd));
      3'd2:    return sub8(mul8(fd, xd), mul8(fs, xs));
      3'd3:    return (xs < xd) ? xs : xd;
      default: return (xs > xd) ? xs : xd;
    endcase
  endfunction

  // dither position, carried through S0..S3 and the division stages
  logic [NS-2:0][3:0] dxy_q;
  always_ff @(posedge clk_i) begin
    if (en) dxy_q <= {dxy_q[NS-3:0], in_dxy_i};
  end

  // ---------------------------------------------------------------- SI, SA, SB (v1.7)
  logic [31:0] si_s, si_d, sa_s, sa_d, sb_s, sb_d;
  logic [7:0]  si_cov, sa_cov, sb_cov;
  logic [TAG_W-1:0] si_tag, sa_tag, sb_tag;
  logic [2:0][7:0] sb_ac;
  always_ff @(posedge clk_i) begin
    if (en) begin
      si_s   <= in_s_i;
      si_d   <= in_d_i;
      si_cov <= in_cov_i;
      si_tag <= in_tag_i;
      // SA. §5.3.1 stencil: sa_s = (aa = m(I.A, P.A), m(I.c, I.A)); source global alpha is NORMAL then
      if (cfg_stencil_i) begin
        sa_s[31:24] <= mul8(si_s[31:24], cfg_const_i[31:24]);
        for (int k = 0; k < 3; k++) sa_s[8*k +: 8] <= mul8(si_s[8*k +: 8], si_s[31:24]);
      end else begin
        sa_s <= apply_global_alpha(si_s, cfg_src_amode_i, cfg_src_aval_i);
      end
      sa_d   <= apply_global_alpha(fmt_expand(cfg_fmt_i, si_d), cfg_dst_amode_i, cfg_dst_aval_i);
      sa_cov <= si_cov;
      sa_tag <= si_tag;
      // SB. stencil: ac = m(m(I.c, I.A), P.A)
      for (int k = 0; k < 3; k++) sb_ac[k] <= mul8(sa_s[8*k +: 8], cfg_const_i[31:24]);
      sb_s   <= sa_s;
      sb_d   <= sa_d;
      sb_cov <= sa_cov;
      sb_tag <= sa_tag;
    end
  end

  // ---------------------------------------------------------------- S0
  logic [31:0] s0_s, s0_d;
  logic [2:0][7:0] s0_ac;            // stencil channel alphas (§5.3.1)
  logic [7:0]  s0_cov;
  logic [TAG_W-1:0] s0_tag;

  always_ff @(posedge clk_i) begin
    if (en) begin
      if (cfg_stencil_i) begin
        // §5.3.1: S = (aa, m(P.c, ac))
        s0_s[31:24] <= sb_s[31:24];
        for (int k = 0; k < 3; k++) begin
          s0_ac[k]       <= sb_ac[k];
          s0_s[8*k +: 8] <= mul8(cfg_const_i[8*k +: 8], sb_ac[k]);
        end
      end else begin
        s0_s <= sb_s;
        for (int k = 0; k < 3; k++) s0_ac[k] <= '0;
      end
      s0_d   <= sb_d;
      s0_cov <= sb_cov;
      s0_tag <= sb_tag;
    end
  end

  // ---------------------------------------------------------------- S1 (per channel k: 0 B, 1 G, 2 R, 3 A)
  logic [3:0][7:0] p_sida, p_sd, p_disa, p_sda, p_dsa, p_ssa, x_lv;
  logic [2:0][7:0] p_dda, svg_bf;            // v1.6: dp = m(D, Da); SVG2 blend function Bf(cb, cs)
  logic [3:0][7:0] pd_xs, pd_xd, pd_fs, pd_fd;   // v1.6 Porter-Duff operands and factors
  logic [31:0] s1_s, s1_d;
  logic [3:0][7:0] s1_sak;          // per-channel source alpha (stencil), else S.A in every channel
  logic [7:0]  s1_cov;
  logic [TAG_W-1:0] s1_tag;

  logic [7:0] s0_sa, s0_da;
  logic [3:0][7:0] sak;              // the source alpha of each channel's equation (stencil: the channel alphas)
  assign s0_sa = s0_s[31:24];
  always_comb begin
    for (int k = 0; k < 3; k++) sak[k] = cfg_stencil_i ? s0_ac[k] : s0_sa;
    sak[3] = s0_sa;
  end
  assign s0_da = s0_d[31:24];

  always_ff @(posedge clk_i) begin
    if (en) begin
      for (int k = 0; k < 4; k++) begin
        p_sida[k] <= mul8(s0_s[8*k +: 8], ~s0_da);
        p_sd[k]   <= mul8(s0_s[8*k +: 8], s0_d[8*k +: 8]);
        p_disa[k] <= mul8(s0_d[8*k +: 8], ~sak[k]);
        p_sda[k]  <= mul8(s0_s[8*k +: 8], s0_da);
        p_dsa[k]  <= mul8(s0_d[8*k +: 8], sak[k]);
        p_ssa[k]  <= mul8(s0_s[8*k +: 8], sak[k]);
        pd_xs[k] <= pd_cm(cfg_func_i[24:21], 1'b1, s0_s[8*k +: 8], s0_d[8*k +: 8], s0_sa, s0_da);
        pd_xd[k] <= pd_cm(cfg_func_i[28:25], 1'b0, s0_s[8*k +: 8], s0_d[8*k +: 8], s0_sa, s0_da);
        if (k == 3) begin
          pd_fs[k] <= pd_fa(cfg_func_i[3:0], s0_sa, s0_da, cfg_const_i[31:24]);
          pd_fd[k] <= pd_fa(cfg_func_i[12:9], s0_sa, s0_da, cfg_const_i[31:24]);
        end else begin
          pd_fs[k] <= pd_fc(cfg_func_i[8:4], s0_s[8*k +: 8], s0_d[8*k +: 8], s0_sa, s0_da, cfg_const_i[31:24],
                            cfg_const_i[8*k +: 8]);
          pd_fd[k] <= pd_fc(cfg_func_i[17:13], s0_s[8*k +: 8], s0_d[8*k +: 8], s0_sa, s0_da, cfg_const_i[31:24],
                            cfg_const_i[8*k +: 8]);
          p_dda[k]  <= mul8(s0_d[8*k +: 8], s0_da);
          svg_bf[k] <= svg_bf_f(cfg_mode_i, s0_s[8*k +: 8], s0_d[8*k +: 8]);
        end
        unique case (cfg_mode_i)
          4'd12:   x_lv[k] <= add8(s0_s[8*k +: 8], s0_d[8*k +: 8]);
          4'd13:   x_lv[k] <= sub8(s0_d[8*k +: 8], s0_s[8*k +: 8]);
          default: x_lv[k] <= (s0_d[8*k +: 8] > s0_s[8*k +: 8]) ? s0_d[8*k +: 8] - s0_s[8*k +: 8]
                                                                 : s0_s[8*k +: 8] - s0_d[8*k +: 8];
        endcase
      end
      s1_s   <= s0_s;
      s1_sak <= sak;
      s1_d   <= s0_d;
      s1_cov <= s0_cov;
      s1_tag <= s0_tag;
    end
  end

  // ---------------------------------------------------------------- S2
  logic [31:0] s2_b, s2_d;
  logic [7:0]  s2_cov;
  logic [TAG_W-1:0] s2_tag;

  function automatic logic [7:0] blend_ch(logic [3:0] mode, logic [7:0] s, logic [7:0] d, logic [7:0] sida,
                                          logic [7:0] sd, logic [7:0] disa, logic [7:0] sda, logic [7:0] dsa,
                                          logic [7:0] ssa, logic [7:0] q);
    logic [7:0] so, dov;
    so  = add8(s, disa);
    dov = add8(sida, d);
    unique case (mode)
      4'd0:  return s;
      4'd1:  return so;
      4'd2:  return dov;
      4'd3:  return sda;
      4'd4:  return dsa;
      4'd5:  return add8(add8(sida, sd), disa);
      4'd6:  return add8(s, sub8(d, sd));  // S + (D - SD): no step saturates
      4'd7:  return (so < dov) ? so : dov;
      4'd8:  return (so > dov) ? so : dov;
      4'd9:  return add8(s, d);
      4'd10: return disa;
      4'd11: return add8(ssa, disa);
      default: return add8(q, disa);  // 12..15: m(X, SA) + m(D, ~SA)
    endcase
  endfunction


  // VG_LITE modes (family 0): the result in S2P (one multiply + adds), carried through S2 (v1.7)
  logic [31:0] s2_b_d;
  always_comb begin
    for (int k = 0; k < 4; k++) begin
      logic [7:0] q;
      q = mul8((cfg_mode_i == 4'd15) ? p_sd[k] : x_lv[k], s1_sak[k]);
      if (k == 3 && cfg_mode_i >= 4'd11) begin
        s2_b_d[8*k +: 8] = 8'hFF;   // LVGL modes: A = 255
      end else begin
        s2_b_d[8*k +: 8] = blend_ch(cfg_mode_i, s1_s[8*k +: 8], s1_d[8*k +: 8], p_sida[k], p_sd[k],
                                    p_disa[k], p_sda[k], p_dsa[k], p_ssa[k], q);
      end
    end
  end

  // ---------------------------------------------------------------- S2P (v1.7): family products
  // The products of the §5.7 numerators and of the Porter-Duff equation, so the adds of S2 start from flops.
  logic [2:0][7:0] f_sp, f_dp, f_spi, f_dpi, f_x, f_y, f_spdp, f_svg;
  logic [3:0][7:0] f_pfs, f_pfd, f_pxs, f_pxd;
  logic [7:0]      f_ra;
  logic [31:0]     f_b, f_d;
  logic [7:0]      f_cov;
  logic [TAG_W-1:0] f_tag;
  always_ff @(posedge clk_i) begin
    if (en) begin
      logic [7:0] sa1, da1, over;
      sa1  = s1_s[31:24];
      da1  = s1_d[31:24];
      over = add8(sa1, p_disa[3]);                           // Sa + m(Da, ~Sa)
      if (cfg_family_i == 2'd2) begin
        f_ra <= over;
      end else begin
        unique case (cfg_mode_i)
          4'd0:       f_ra <= sa1;
          4'd2:       f_ra <= add8(p_sida[3], da1);          // m(Sa, ~Da) + Da
          4'd3, 4'd4: f_ra <= p_sda[3];                      // m(Sa, Da)
          4'd9:       f_ra <= add8(sa1, da1);
          default:    f_ra <= over;
        endcase
      end
      for (int k = 0; k < 3; k++) begin
        f_sp[k]   <= p_ssa[k];
        f_dp[k]   <= p_dda[k];
        f_spi[k]  <= mul8(p_ssa[k], ~da1);
        f_dpi[k]  <= mul8(p_dda[k], ~sa1);
        f_x[k]    <= mul8(p_ssa[k], da1);
        f_y[k]    <= mul8(p_dda[k], sa1);
        f_spdp[k] <= mul8(p_ssa[k], p_dda[k]);
        f_svg[k]  <= mul8(p_sda[3], svg_bf[k]);
      end
      for (int k = 0; k < 4; k++) begin
        f_pfs[k] <= mul8(pd_fs[k], pd_xs[k]);
        f_pfd[k] <= mul8(pd_fd[k], pd_xd[k]);
        f_pxs[k] <= pd_xs[k];
        f_pxd[k] <= pd_xd[k];
      end
      f_b   <= s2_b_d;
      f_d   <= s1_d;
      f_cov <= s1_cov;
      f_tag <= s1_tag;
    end
  end

  // ---------------------------------------------------------------- S2 (families 1-3, v1.6, §5.7): adds
  logic [2:0][7:0] e_n;          // numerators (families 1, 2)
  logic [7:0]      e_ra;         // result alpha
  logic [31:0]     e_pd;         // Porter-Duff result (family 3)
  always_ff @(posedge clk_i) begin
    if (en) begin
      e_ra <= f_ra;
      for (int k = 0; k < 3; k++) begin
        logic [7:0] sp, dp, spi, dpi, x, y;
        sp  = f_sp[k];
        dp  = f_dp[k];
        spi = f_spi[k];
        dpi = f_dpi[k];
        x   = f_x[k];
        y   = f_y[k];
        if (cfg_family_i == 2'd2) begin
          e_n[k] <= add8(add8(spi, dpi), f_svg[k]);
        end else begin
          unique case (cfg_mode_i)
            4'd0:    e_n[k] <= sp;
            4'd1:    e_n[k] <= add8(sp, dpi);
            4'd2:    e_n[k] <= add8(spi, dp);
            4'd3:    e_n[k] <= x;
            4'd4:    e_n[k] <= y;
            4'd5:    e_n[k] <= add8(add8(f_spdp[k], spi), dpi);
            4'd6:    e_n[k] <= add8(sp, sub8(dp, f_spdp[k]));
            4'd7:    e_n[k] <= add8(add8((x < y) ? x : y, spi), dpi);
            4'd8:    e_n[k] <= add8(add8((x > y) ? x : y, spi), dpi);
            default: e_n[k] <= add8(sp, dp);
          endcase
        end
      end
      // §5.7 Porter-Duff equation on the registered products (pd_eq split: products in S2P)
      for (int k = 0; k < 4; k++) begin
        unique case (cfg_func_i[20:18])
          3'd0:    e_pd[8*k +: 8] <= add8(f_pfs[k], f_pfd[k]);
          3'd1:    e_pd[8*k +: 8] <= sub8(f_pfs[k], f_pfd[k]);
          3'd2:    e_pd[8*k +: 8] <= sub8(f_pfd[k], f_pfs[k]);
          3'd3:    e_pd[8*k +: 8] <= (f_pxs[k] < f_pxd[k]) ? f_pxs[k] : f_pxd[k];
          default: e_pd[8*k +: 8] <= (f_pxs[k] > f_pxd[k]) ? f_pxs[k] : f_pxd[k];
        endcase
      end
      s2_b   <= f_b;
      s2_d   <= f_d;
      s2_cov <= f_cov;
      s2_tag <= f_tag;
    end
  end

  // ---------------------------------------------------------------- Q1..Q8: B.c = (n·255 + ra/2) / ra
  // Restoring division with the quotient known to be below 256 when n < ra: the initial remainder is the high
  // byte of the dividend (< ra), then one dividend bit and one quotient bit per stage. n >= ra gives 255 and
  // ra = 0 gives 0 (flags), so the quotient of those cases is not used.
  // unpacked per stage: stage 0 is combinational, stages 1..NQ are registers
  logic [2:0][8:0]  q_rem [NQ+1];
  logic [2:0][7:0]  q_low [NQ+1];
  logic [2:0][7:0]  q_quo [NQ+1];
  logic [2:0]       q_big [NQ+1];
  logic [7:0]       q_ra  [NQ+1];
  logic [31:0]      q_b   [NQ+1];
  logic [31:0]      q_d   [NQ+1];
  logic [31:0]      q_pd  [NQ+1];
  logic [7:0]       q_cov [NQ+1];
  logic [TAG_W-1:0] q_tag [NQ+1];

  always_comb begin
    q_ra[0]  = e_ra;
    q_b[0]   = s2_b;
    q_d[0]   = s2_d;
    q_pd[0]  = e_pd;
    q_cov[0] = s2_cov;
    q_tag[0] = s2_tag;
    for (int k = 0; k < 3; k++) begin
      logic [15:0] dvd;
      dvd         = 16'(e_n[k]) * 16'd255 + 16'(e_ra >> 1);
      q_rem[0][k] = {1'b0, dvd[15:8]};
      q_low[0][k] = dvd[7:0];
      q_quo[0][k] = '0;
      q_big[0][k] = e_n[k] >= e_ra;
    end
  end

  for (genvar j = 0; j < NQ; j++) begin : g_div
    always_ff @(posedge clk_i) begin
      if (en) begin
        for (int k = 0; k < 3; k++) begin
          logic [8:0] r;
          r = {q_rem[j][k][7:0], q_low[j][k][7]};
          if (r >= {1'b0, q_ra[j]}) begin
            q_rem[j+1][k] <= r - {1'b0, q_ra[j]};
            q_quo[j+1][k] <= {q_quo[j][k][6:0], 1'b1};
          end else begin
            q_rem[j+1][k] <= r;
            q_quo[j+1][k] <= {q_quo[j][k][6:0], 1'b0};
          end
          q_low[j+1][k] <= {q_low[j][k][6:0], 1'b0};
          q_big[j+1][k] <= q_big[j][k];
        end
        q_ra[j+1]  <= q_ra[j];
        q_b[j+1]   <= q_b[j];
        q_d[j+1]   <= q_d[j];
        q_pd[j+1]  <= q_pd[j];
        q_cov[j+1] <= q_cov[j];
        q_tag[j+1] <= q_tag[j];
      end
    end
  end

  logic unused_div;
  assign unused_div = ^{q_rem[NQ], q_low[NQ]};   // the final remainder and shifted-out dividend are not needed

  // the blend result of the draw's family
  logic [31:0] bq;
  always_comb begin
    unique case (cfg_family_i)
      2'd0: bq = q_b[NQ];
      2'd3: bq = q_pd[NQ];
      default: begin
        bq[31:24] = q_ra[NQ];
        for (int k = 0; k < 3; k++)
          bq[8*k +: 8] = (q_ra[NQ] == 8'd0) ? 8'd0 : q_big[NQ][k] ? 8'd255 : q_quo[NQ][k];
      end
    endcase
  end

  // ---------------------------------------------------------------- S3: coverage blend
  logic [31:0] s3_o;
  logic [TAG_W-1:0] s3_tag;

  always_ff @(posedge clk_i) begin
    if (en) begin
      for (int k = 0; k < 4; k++) begin
        s3_o[8*k +: 8] <= (q_cov[NQ] == 8'hFF) ? bq[8*k +: 8]
                        : div255r(16'(bq[8*k +: 8]) * 16'(q_cov[NQ]) + 16'(q_d[NQ][8*k +: 8]) * 16'(8'd255 - q_cov[NQ]));
      end
      s3_tag <= q_tag[NQ];
    end
  end

  // ---------------------------------------------------------------- S4P (v1.7): per-channel rounding, S4: bits
  fmt_pk_t          s4_pk;
  logic [31:0]      s4_o;
  logic [TAG_W-1:0] s4_tag;
  always_ff @(posedge clk_i) begin
    if (en) begin
      s4_pk     <= fmt_pack(cfg_fmt_i, s3_o, cfg_dither_i, bayer(dxy_q[NS-3][1:0], dxy_q[NS-3][3:2]));
      s4_o      <= s3_o;
      s4_tag    <= s3_tag;
      out_pix_o <= fmt_assemble(cfg_fmt_i, s4_o, s4_pk);
      out_tag_o <= s4_tag;
    end
  end

endmodule
