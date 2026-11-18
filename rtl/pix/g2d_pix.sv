// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// General pixel pipeline (arch.md §6.3): every DRAW_PATH, and every DRAW_RECT that is not a SOLID + blend
// NONE fill (those take the g2d_fill fast path). One pixel per clock.
//
//   g2d_walk (spans, pixels) ─► g2d_xform ─► g2d_tex_addr ─► g2d_src_cache ─► g2d_texel ─┐ paint
//        │ spans                                                         (read client src) │
//        ├─► g2d_dst_rd (read client dst, when the blend reads D) ──────────────► join ◄───┘
//        └─► g2d_wcomb ◄──────────────────────────────────────────────── g2d_blend ◄┘
//
// SOLID and LINEAR_GRAD pixels go through the same chain (their texel results are ignored), so all
// pixels stay in order with one join. LINEAR_GRAD: g2d_grad computes the ramp index right after the
// walker (rendering.md §10.1); the 8-bit index rides through the chain as its tag and g2d_ramp looks it
// up at the end (S = ramp word); gradient pixels wait until the ramp is loaded. IMAGE: S = texel unit output, cov = 255 when the pixel
// passes the source test (§7.4) and 0 otherwise (not written, rendering.md §4 step 1).
// DRAW_PATH: g2d_raster computes the coverage of the clipped bbox in the walker's pixel order (scanline),
// joined with the paint result (any paint source; IMAGE on paths since v1.5, F-1: coverage gated by the source
// test, so image pixels on paths sample in scanline order); the destination is always read for paths (cov < 255 at edges, Phase 4
// assumption A3); cov = 0 pixels are not written. A path with EDGE_COUNT = 0 is an empty draw.
// The destination is read when BLEND.MODE != NONE or for paths. Blits with a rotation/shear term or
// perspective walk 16x16 tiles, all other draws walk scanlines.
//
// The draw state is snapshotted when the draw is accepted (only while idle). The core guarantees that
// earlier writes are acknowledged before the draw is accepted (arch.md §5.3).
// stop_i (soft reset): the walker starts no new span, the join stops waiting for destination data, the
// cache for fills and gradient pixels for their ramp, so the pipeline drains; the draw ends without
// draw_done_o.

module g2d_pix
  import g2d_pkg::*;
  import g2d_state_pkg::*;
#(
  parameter int unsigned DATA_W          = 64,
  parameter int unsigned SRC_CACHE_BYTES = 8192,
  parameter logic [5:0]  FEATURES        = 6'b11_1111   // v1.7: optional units; an absent one is tied off here
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  input  logic              stop_i,
  // draw
  input  logic              draw_valid_i,
  output logic              draw_ready_o,
  input  logic              draw_path_i,
  input  logic [3:0][31:0]  draw_payload_i,
  input  logic [STATE_SIZE-1:0][31:0] state_i,
  // read client: destination
  output logic              dst_req_valid_o,
  input  logic              dst_req_ready_i,
  output logic [31:0]       dst_req_addr_o,
  output logic [15:0]       dst_req_len_o,
  input  logic              dst_rsp_valid_i,
  output logic              dst_rsp_ready_o,
  // read client: source
  output logic              src_req_valid_o,
  input  logic              src_req_ready_i,
  output logic [31:0]       src_req_addr_o,
  output logic [15:0]       src_req_len_o,
  input  logic              src_rsp_valid_i,
  output logic              src_rsp_ready_o,
  // read client: edges
  output logic              edge_req_valid_o,
  input  logic              edge_req_ready_i,
  output logic [31:0]       edge_req_addr_o,
  output logic [15:0]       edge_req_len_o,
  input  logic              edge_rsp_valid_i,
  output logic              edge_rsp_ready_o,
  // read client: mask layer (v1.6)
  output logic              mask_req_valid_o,
  input  logic              mask_req_ready_i,
  output logic [31:0]       mask_req_addr_o,
  output logic [15:0]       mask_req_len_o,
  input  logic              mask_rsp_valid_i,
  output logic              mask_rsp_ready_o,
  // read client: gradient ramp
  output logic              ramp_req_valid_o,
  input  logic              ramp_req_ready_i,
  output logic [31:0]       ramp_req_addr_o,
  output logic [15:0]       ramp_req_len_o,
  input  logic              ramp_rsp_valid_i,
  output logic              ramp_rsp_ready_o,
  input  logic [DATA_W-1:0] rsp_data_i,
  input  logic [DATA_W/8-1:0] rsp_strb_i,
  // ramp coherence (Phase 4 A4)
  input  logic              kick_i,           // a kicked buffer was popped
  input  logic              wr_i,             // the write engine accepted a request
  // write producer
  output logic              wr_req_valid_o,
  input  logic              wr_req_ready_i,
  output logic [31:0]       wr_req_addr_o,
  output logic [15:0]       wr_req_len_o,
  output logic              wr_dat_valid_o,
  input  logic              wr_dat_ready_i,
  output logic [DATA_W-1:0] wr_dat_data_o,
  output logic [DATA_W/8-1:0] wr_dat_strb_o,
  // status / performance
  output logic              idle_o,
  output logic              draw_done_o,
  output logic              pixel_o,         // a pixel was written this cycle
  output logic [2:0]        src_miss_o,
  // v1.8: RAM read/write collisions this cycle (DBG_STATUS.RAM_COLL, arch.md §9.1): [3:0] raster (edge buffer,
  // state buffers, active list, winding accumulator), [4] write combiner, [5] source cache, [6] gradient ramp
  output logic [6:0]        ram_coll_o
);

  logic [3:0] coll_raster;
  logic       coll_wcomb, coll_cache, coll_ramp;
  assign ram_coll_o = {coll_ramp, coll_cache, coll_wcomb, coll_raster};

  // ---------------------------------------------------------------- draw configuration snapshot
  logic [31:0] t_addr, s_addr, color;
  logic [15:0] t_stride, s_stride;
  logic [13:0] t_w, t_h, cx0, cy0, cx1, cy1, sx0, sy0, sx1, sy1;
  logic [7:0]  t_fmt, s_fmt, a_sval, a_dval;
  logic [1:0]  a_smode, a_dmode, src;
  logic [3:0]  mode;
  logic [1:0]  family;
  logic [28:0] bfunc;
  logic        img_mul, premul, bilin, persp, opaque;
  logic        sub_order, transp, dither, idx_src, flip, stencil, gauss;
  logic [15:0] gw0, gw1, gw2;
  logic        yuv709, uv_swap;
  logic        color_en;          // colour unit (v1.6b, rendering.md §5.2.1)
  logic [1:0]  gamma;
  logic [3:0][3:0][15:0] cmat;
  logic [3:0][19:0] cbias;
  logic [1:0][31:0] p_addr;      // YUV planes 1, 2 (v1.6b)
  logic [1:0][15:0] p_stride;
  logic [2:0]  wrap;
  logic        border_solid;
  logic [31:0] border, clut;
  logic signed [15:0] px0, py0, px1, py1;
  logic [8:0][31:0] xf;
  logic [7:0][31:0] ckey;
  // first state index of XF_A..XF_I and CKEY0_LO..CKEY3_HI (int, so the loop index is a plain constant sum)
  localparam int unsigned XfBase   = int'(G2D_ST_XF_A);
  localparam int unsigned CkeyBase = int'(G2D_ST_CKEY0_LO);
  localparam int unsigned ColorMBase = int'(G2D_ST_COLOR_M0);    // v1.6b
  localparam int unsigned ColorBBase = int'(G2D_ST_COLOR_B0);
  logic        image, dst_rd, tiles;
  logic        path, aa, eo, grad;
  logic [31:0] g_p, g_q, g_ramp;
  logic [47:0] g_r, g_ur, g_vr;
  logic [31:0] g_up, g_uq, g_vp, g_vq;
  logic        radial;
  logic        mask_on;
  logic [31:0] m_addr;
  logic [15:0] m_stride;
  logic [13:0] m_w, m_h;
  logic signed [15:0] m_ox, m_oy;
  logic [1:0]  g_spread;
  logic [31:0] e_addr;
  logic [23:0] e_count;

  logic busy_q, start_q, walk_done_q;
  logic accept;
  assign draw_ready_o = ~busy_q & ~stop_i;
  assign accept       = draw_valid_i & draw_ready_o;

  always_ff @(posedge clk_i) begin
    if (accept) begin
      t_addr   <= state_i[G2D_ST_TGT_ADDR];
      t_stride <= state_i[G2D_ST_TGT_STRIDE][15:0];
      t_w      <= state_i[G2D_ST_TGT_SIZE][G2D_ST_TGT_SIZE_W_LSB +: 14];
      t_h      <= state_i[G2D_ST_TGT_SIZE][G2D_ST_TGT_SIZE_H_LSB +: 14];
      t_fmt    <= state_i[G2D_ST_TGT_FMT][7:0];
      cx0      <= state_i[G2D_ST_CLIP_MIN][G2D_ST_CLIP_MIN_X0_LSB +: 14];
      cy0      <= state_i[G2D_ST_CLIP_MIN][G2D_ST_CLIP_MIN_Y0_LSB +: 14];
      cx1      <= state_i[G2D_ST_CLIP_MAX][G2D_ST_CLIP_MAX_X1_LSB +: 14];
      cy1      <= state_i[G2D_ST_CLIP_MAX][G2D_ST_CLIP_MAX_Y1_LSB +: 14];
      mode     <= state_i[G2D_ST_BLEND][G2D_ST_BLEND_MODE_LSB +: 4];
      family   <= {2{FEATURES[FEAT_FAMILY]}} & state_i[G2D_ST_BLEND][G2D_ST_BLEND_FAMILY_LSB +: 2];
      bfunc    <= state_i[G2D_ST_BLEND_FUNC][28:0];
      a_smode  <= state_i[G2D_ST_ALPHA_CTRL][G2D_ST_ALPHA_CTRL_SRC_MODE_LSB +: 2];
      a_dmode  <= state_i[G2D_ST_ALPHA_CTRL][G2D_ST_ALPHA_CTRL_DST_MODE_LSB +: 2];
      a_sval   <= state_i[G2D_ST_ALPHA_CTRL][G2D_ST_ALPHA_CTRL_SRC_VALUE_LSB +: 8];
      a_dval   <= state_i[G2D_ST_ALPHA_CTRL][G2D_ST_ALPHA_CTRL_DST_VALUE_LSB +: 8];
      src      <= state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_SRC_LSB +: 2];
      img_mul  <= state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_IMG_MUL_LSB];
      premul   <= state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_SRC_PREMUL_LSB];
      bilin    <= state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_FILTER_LSB];
      persp    <= state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_PERSP_LSB];
      color    <= state_i[G2D_ST_PAINT_COLOR];
      s_addr   <= state_i[G2D_ST_SRC_ADDR];
      s_stride <= state_i[G2D_ST_SRC_STRIDE][15:0];
      s_fmt    <= state_i[G2D_ST_SRC_FMT][G2D_ST_SRC_FMT_FMT_LSB +: 8];
      opaque   <= state_i[G2D_ST_SRC_FMT][G2D_ST_SRC_FMT_FORCE_OPAQUE_LSB];
      sub_order <= state_i[G2D_ST_SRC_FMT][G2D_ST_SRC_FMT_SUB_ORDER_LSB];
      transp   <= state_i[G2D_ST_SRC_FMT][G2D_ST_SRC_FMT_TRANSPARENT_LSB];
      dither   <= state_i[G2D_ST_TGT_FMT][G2D_ST_TGT_FMT_DITHER_LSB];
      flip     <= state_i[G2D_ST_TGT_FMT][G2D_ST_TGT_FMT_FLIP_Y_LSB];
      stencil  <= state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_STENCIL_LSB];
      gauss    <= state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_GAUSS_LSB];
      gw0      <= state_i[G2D_ST_GAUSS_W0][G2D_ST_GAUSS_W0_W0_LSB +: 16];
      gw1      <= state_i[G2D_ST_GAUSS_W][G2D_ST_GAUSS_W_W1_LSB +: 16];
      gw2      <= state_i[G2D_ST_GAUSS_W][G2D_ST_GAUSS_W_W2_LSB +: 16];
      yuv709   <= state_i[G2D_ST_SRC_FMT][G2D_ST_SRC_FMT_YUV_709_LSB];
      color_en <= FEATURES[FEAT_COLOR] & state_i[G2D_ST_COLOR_CTRL][G2D_ST_COLOR_CTRL_EN_LSB];
      gamma    <= {2{FEATURES[FEAT_COLOR]}} & state_i[G2D_ST_COLOR_CTRL][G2D_ST_COLOR_CTRL_GAMMA_LSB +: 2];
      for (int o = 0; o < 4; o++) begin
        cmat[o][0] <= state_i[ColorMBase + 2 * o][15:0];
        cmat[o][1] <= state_i[ColorMBase + 2 * o][31:16];
        cmat[o][2] <= state_i[ColorMBase + 2 * o + 1][15:0];
        cmat[o][3] <= state_i[ColorMBase + 2 * o + 1][31:16];
        cbias[o]   <= state_i[ColorBBase + o][19:0];
      end
      uv_swap  <= state_i[G2D_ST_SRC_FMT][G2D_ST_SRC_FMT_UV_SWAP_LSB];
      p_addr   <= {state_i[G2D_ST_SRC_PLANE2_ADDR], state_i[G2D_ST_SRC_PLANE1_ADDR]};
      p_stride <= {state_i[G2D_ST_SRC_PLANE_STRIDE][G2D_ST_SRC_PLANE_STRIDE_S2_LSB +: 16],
                   state_i[G2D_ST_SRC_PLANE_STRIDE][G2D_ST_SRC_PLANE_STRIDE_S1_LSB +: 16]};
      wrap     <= state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_WRAP_LSB +: 3];
      border_solid <= state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_BORDER_SOLID_LSB];
      border   <= state_i[G2D_ST_BORDER_COLOR];
      clut     <= state_i[G2D_ST_CLUT_ADDR];
      sx0      <= state_i[G2D_ST_SRC_RECT_MIN][G2D_ST_SRC_RECT_MIN_X0_LSB +: 14];
      sy0      <= state_i[G2D_ST_SRC_RECT_MIN][G2D_ST_SRC_RECT_MIN_Y0_LSB +: 14];
      sx1      <= state_i[G2D_ST_SRC_RECT_MAX][G2D_ST_SRC_RECT_MAX_X1_LSB +: 14];
      sy1      <= state_i[G2D_ST_SRC_RECT_MAX][G2D_ST_SRC_RECT_MAX_Y1_LSB +: 14];
      for (int k = 0; k < 9; k++) xf[k] <= state_i[XfBase + k];
      for (int k = 0; k < 8; k++) ckey[k] <= state_i[CkeyBase + k];
      path     <= draw_path_i;
      g_p      <= state_i[G2D_ST_GRAD_P];
      g_q      <= state_i[G2D_ST_GRAD_Q];
      g_r      <= {state_i[G2D_ST_GRAD_R_HI][15:0], state_i[G2D_ST_GRAD_R]};
      g_ramp   <= state_i[G2D_ST_GRAD_RAMP_ADDR];
      mask_on  <= FEATURES[FEAT_MASK] & state_i[G2D_ST_MASK_CTRL][G2D_ST_MASK_CTRL_EN_LSB];
      m_addr   <= state_i[G2D_ST_MASK_ADDR];
      m_stride <= state_i[G2D_ST_MASK_STRIDE][15:0];
      m_w      <= state_i[G2D_ST_MASK_SIZE][G2D_ST_MASK_SIZE_W_LSB +: 14];
      m_h      <= state_i[G2D_ST_MASK_SIZE][G2D_ST_MASK_SIZE_H_LSB +: 14];
      m_ox     <= $signed(state_i[G2D_ST_MASK_ORIGIN][G2D_ST_MASK_ORIGIN_X_LSB +: 16]);
      m_oy     <= $signed(state_i[G2D_ST_MASK_ORIGIN][G2D_ST_MASK_ORIGIN_Y_LSB +: 16]);
      g_up     <= state_i[G2D_ST_GRAD_UP];
      g_uq     <= state_i[G2D_ST_GRAD_UQ];
      g_ur     <= {state_i[G2D_ST_GRAD_UR_HI][15:0], state_i[G2D_ST_GRAD_UR]};
      g_vp     <= state_i[G2D_ST_GRAD_VP];
      g_vq     <= state_i[G2D_ST_GRAD_VQ];
      g_vr     <= {state_i[G2D_ST_GRAD_VR_HI][15:0], state_i[G2D_ST_GRAD_VR]};
      g_spread <= state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_SPREAD_LSB +: 2];
      aa       <= state_i[G2D_ST_RAST_CTRL][G2D_ST_RAST_CTRL_AA_LSB];
      eo       <= state_i[G2D_ST_RAST_CTRL][G2D_ST_RAST_CTRL_FILL_RULE_LSB];
      e_addr   <= draw_payload_i[2];
      e_count  <= draw_payload_i[3][23:0];
      px0      <= $signed(draw_payload_i[0][15:0]);
      py0      <= $signed(draw_payload_i[0][31:16]);
      px1      <= $signed(draw_payload_i[1][15:0]);
      py1      <= $signed(draw_payload_i[1][31:16]);
    end
  end

  assign image  = (src == PAINT_IMAGE);
  assign radial = FEATURES[FEAT_RADIAL] && (src == PAINT_RADIAL);
  assign grad   = (src == PAINT_GRAD) | radial;     // both use the ramp (rendering.md §10.1, §10.4)
  assign idx_src = image & fmt_indexed(s_fmt);
  assign dst_rd = (mode != 4'd0) | (family != 2'd0) | path | mask_on;   // a mask makes cov < 255 possible on any draw (§6.1)
  assign tiles  = image & ~path & ((xf[1] != '0) | (xf[3] != '0) | persp);   // paths: the raster's scanline order

  // ---------------------------------------------------------------- walker
  logic        walk_busy, walk_done;
  logic        dspan_valid, dspan_ready, wspan_valid, wspan_ready, mspan_valid, mspan_ready;
  logic [31:0] span_addr;
  logic [8:0]  span_n;
  logic [13:0] span_x, span_y;
  logic        wp_valid, wp_ready;
  logic [13:0] wp_x, wp_y;
  logic        rect_valid;
  logic [13:0] rect_x0, rect_y0, rect_x1, rect_y1;

  g2d_walk u_walk (
    .clk_i,
    .rst_ni,
    .clr_i,
    .stop_i,
    .start_i       (start_q),
    .busy_o        (walk_busy),
    .cfg_px0_i     (px0),
    .cfg_py0_i     (py0),
    .cfg_px1_i     (px1),
    .cfg_py1_i     (py1),
    .cfg_tw_i      (t_w),
    .cfg_th_i      (t_h),
    .cfg_cx0_i     (cx0),
    .cfg_cy0_i     (cy0),
    .cfg_cx1_i     (cx1),
    .cfg_cy1_i     (cy1),
    .cfg_addr_i    (flip ? t_addr + 32'(t_h - 14'd1) * 32'(t_stride) : t_addr),   // §5.8 FLIP_Y (v1.6)
    .cfg_stride_i  (flip ? -32'(t_stride) : 32'(t_stride)),
    .cfg_lg_i      (fmt_lg_bytes(t_fmt)),
    .cfg_tiles_i   (tiles),
    .cfg_dst_i     (dst_rd),
    .cfg_mask_i    (mask_on),
    .cfg_empty_i   (path && e_count == '0),
    .dspan_valid_o (dspan_valid),
    .dspan_ready_i (dspan_ready),
    .wspan_valid_o (wspan_valid),
    .wspan_ready_i (wspan_ready),
    .mspan_valid_o (mspan_valid),
    .mspan_ready_i (mspan_ready),
    .span_addr_o   (span_addr),
    .span_n_o      (span_n),
    .span_x_o      (span_x),
    .span_y_o      (span_y),
    .pix_valid_o   (wp_valid),
    .pix_ready_i   (wp_ready),
    .pix_x_o       (wp_x),
    .pix_y_o       (wp_y),
    .rect_valid_o  (rect_valid),
    .rect_x0_o     (rect_x0),
    .rect_y0_o     (rect_y0),
    .rect_x1_o     (rect_x1),
    .rect_y1_o     (rect_y1),
    .done_o        (walk_done)
  );

  // ---------------------------------------------------------------- paint chain
  // Tag carried from the gradient stage to the join (TW bits): {solid border pixel (set by g2d_tex_addr),
  // y & 3, x & 3 (dither position), FILL flag, ramp index}. g2d_tex_addr adds per texel the border flag and the sub-byte shift (cache tag), g2d_tex_split
  // the request mask and the last flag.
  localparam int unsigned TW  = 14;
  localparam int unsigned SUBW = 10;                 // sub-request (v1.6b): {plane[1:0], gmask[3:0], grp[1:0], emit, last}
  localparam int unsigned MTW = 4 + 3 + TW;          // merge output tag: {border, grp, last, tag}
  localparam int unsigned STW = 12 + 4 + SUBW + TW;  // split tag: {sh, border, sub, tag}
  localparam int unsigned CTW = 6 + STW;             // cache tag: {smask, last, hi, sh, border, tag}
  localparam int unsigned RPW = 2 + 32 + TW;         // ramp pass bits: {pass, azero, S, tag}

  logic        xf_valid, xf_ready, xf_ok;
  logic [31:0] xf_u, xf_v;
  logic [TW-1:0] gr_tag, xf_tag, ta_tag;
  logic [TW+SUBW-1:0] ta_tag_raw;
  logic [SUBW-1:0] ta_sub;
  // v1.6b sub-request sequencer output
  logic          sq_valid, sq_ready, sq_ok, sq_emit, sq_last;
  logic [31:0]   sq_u, sq_v;
  logic [TW-1:0] sq_tag;
  logic [1:0]    sq_plane, sq_grp;
  logic signed [1:0] sq_dx, sq_dy;
  logic [3:0]    sq_gmask;
  logic          gauss_eff;                   // indexed sources are point-sampled (rendering.md §3.5)
  logic [1:0]    te_grp;
  logic          te_last;
  logic        ta_valid, ta_ready, ta_pass, ta_solid;
  logic [3:0][31:0] ta_addr;
  logic [1:0]  ta_ypar;
  logic [7:0]  ta_fx, ta_fy;
  logic [3:0]  ta_border;
  logic [3:0][2:0] ta_sh;
  logic        sp_valid, sp_ready, sp_pass, sp_last, sp_hi;
  logic [3:0]  sp_smask;
  logic [3:0][31:0] sp_addr;
  logic [1:0]  sp_ypar;
  logic [7:0]  sp_fx, sp_fy;
  logic [STW-1:0] sp_tag;
  logic        sc_valid, sc_ready, sc_pass;
  logic [3:0][31:0] sc_tex;
  logic [7:0]  sc_fx, sc_fy;
  logic [CTW-1:0] sc_tag;
  logic        mg_valid, mg_ready, mg_pass;
  logic [3:0][31:0] mg_tex;
  logic [7:0]  mg_fx, mg_fy;
  logic [MTW-1:0] mg_tag;
  logic        te_valid, te_ready, te_pass;
  logic [3:0][31:0] te_tex;
  logic [3:0]  te_border;
  logic [7:0]  te_fx, te_fy;
  logic [TW-1:0] te_tag;
  logic        tx0_valid, tx0_ready, tx0_pass, tx0_azero;
  logic [31:0] tx0_s;
  logic [TW-1:0] tx0_tag;
  logic        cache_idle;
  logic        gr_valid, gr_ready, gr_fill;
  logic [13:0] gr_x, gr_y;
  logic [7:0]  gr_idx;

  g2d_grad u_grad (
    .clk_i,
    .rst_ni,
    .clr_i,
    .cfg_p_i      (g_p),
    .cfg_q_i      (g_q),
    .cfg_r_i      (g_r),
    .cfg_radial_i (radial),
    .cfg_up_i     (g_up),
    .cfg_uq_i     (g_uq),
    .cfg_ur_i     (g_ur),
    .cfg_vp_i     (g_vp),
    .cfg_vq_i     (g_vq),
    .cfg_vr_i     (g_vr),
    .cfg_spread_i (g_spread),
    .in_valid_i   (wp_valid),
    .in_ready_o   (wp_ready),
    .in_x_i       (wp_x),
    .in_y_i       (wp_y),
    .out_valid_o  (gr_valid),
    .out_ready_i  (gr_ready),
    .out_x_o      (gr_x),
    .out_y_o      (gr_y),
    .out_idx_o    (gr_idx),
    .out_fill_o   (gr_fill)
  );

  assign gr_tag = {1'b0, gr_y[1:0], gr_x[1:0], gr_fill, gr_idx};

  g2d_xform #(.TAG_W(TW)) u_xform (
    .clk_i,
    .rst_ni,
    .clr_i,
    .cfg_xf_i    (xf),
    .cfg_persp_i (persp),
    .cfg_wrap_i  (wrap != 3'd0),
    .in_valid_i  (gr_valid),
    .in_ready_o  (gr_ready),
    .in_x_i      (gr_x),
    .in_y_i      (gr_y),
    .in_tag_i    (gr_tag),
    .out_valid_o (xf_valid),
    .out_ready_i (xf_ready),
    .out_ok_o    (xf_ok),
    .out_u_o     (xf_u),
    .out_v_o     (xf_v),
    .out_tag_o   (xf_tag)
  );

  // YUV plane configuration (v1.6b, rendering.md §3.7): which planes, their texel size and subsampling
  logic       yuv, yuy2, cls_nv, cls_yv, sub_x, sub_y;
  logic [2:0] planes;
  logic [1:0] yuv_cls;
  assign yuv     = FEATURES[FEAT_YUV] && fmt_yuv(s_fmt);
  assign yuy2    = yuv && s_fmt[3:0] == 4'h3;
  assign cls_nv  = yuv && s_fmt[3:0] == 4'h4;
  assign cls_yv  = yuv && s_fmt[3:0] == 4'h5;
  assign sub_x   = s_fmt[5:4] <= 2'd1;
  assign sub_y   = s_fmt[5:4] == 2'd0;
  assign planes  = !yuv ? 3'b001 : yuy2 ? {s_fmt[6], 2'b01} : cls_nv ? {s_fmt[6], 2'b11} : 3'b111;
  assign yuv_cls = !yuv ? 2'd0 : yuy2 ? 2'd1 : cls_nv ? 2'd2 : 2'd3;

  assign gauss_eff = FEATURES[FEAT_GAUSS] & gauss & image & ~idx_src & ~yuv;

  // v1.7: register slice between the transform and the sequencer: breaks the combinational ready chain from the
  // source-cache stall back through texel addressing, transform, gradient and walker (timing, T-1)
  logic          xs_valid, xs_ready, xs_ok;
  logic [31:0]   xs_u, xs_v;
  logic [TW-1:0] xs_tag;
  g2d_spill_reg #(.WIDTH(1 + 64 + TW)) u_xform_out (
    .clk_i, .rst_ni, .clr_i,
    .in_valid_i  (xf_valid),
    .in_ready_o  (xf_ready),
    .in_data_i   ({xf_ok, xf_u, xf_v, xf_tag}),
    .out_valid_o (xs_valid),
    .out_ready_i (xs_ready),
    .out_data_o  ({xs_ok, xs_u, xs_v, xs_tag})
  );

  g2d_tex_seq #(.TAG_W(TW)) u_seq (
    .clk_i,
    .rst_ni,
    .clr_i,
    .cfg_gauss_i  (gauss_eff),
    .cfg_planes_i (planes),
    .in_valid_i   (xs_valid),
    .in_ready_o   (xs_ready),
    .in_ok_i      (xs_ok),
    .in_u_i       (xs_u),
    .in_v_i       (xs_v),
    .in_tag_i     (xs_tag),
    .out_valid_o  (sq_valid),
    .out_ready_i  (sq_ready),
    .out_ok_o     (sq_ok),
    .out_u_o      (sq_u),
    .out_v_o      (sq_v),
    .out_tag_o    (sq_tag),
    .out_plane_o  (sq_plane),
    .out_dx_o     (sq_dx),
    .out_dy_o     (sq_dy),
    .out_gmask_o  (sq_gmask),
    .out_grp_o    (sq_grp),
    .out_emit_o   (sq_emit),
    .out_last_o   (sq_last)
  );

  g2d_tex_addr #(.TAG_W(TW + SUBW)) u_tex_addr (
    .clk_i,
    .rst_ni,
    .clr_i,
    .start_i         (start_q & image),
    .cfg_bilinear_i  (bilin & ~idx_src),     // indexed sources are point-sampled (rendering.md §3.5)
    .cfg_wrap_i      (wrap),
    .cfg_border_solid_i (border_solid),
    .cfg_sx0_i       (sx0),
    .cfg_sy0_i       (sy0),
    .cfg_sx1_i       (sx1),
    .cfg_sy1_i       (sy1),
    .cfg_addr_i      (s_addr),
    .cfg_stride_i    (s_stride),
    .cfg_lg_bits_i   (yuv ? (yuy2 ? 3'd5 : 3'd3) : fmt_lg_bits(s_fmt)),
    .cfg_sub_order_i (sub_order),
    .cfg_p0_xs_i     (yuy2),
    .cfg_paddr_i     (p_addr),
    .cfg_pstride_i   (p_stride),
    .cfg_plg_i       ({3'd3, cls_nv ? 3'd4 : 3'd3}),
    .cfg_pxs_i       ({cls_yv & sub_x, sub_x}),
    .cfg_pys_i       ({cls_yv & sub_y, sub_y}),
    .cfg_yuy2_i      (yuy2),
    .cfg_b24_i       (fmt_b24(s_fmt)),
    .cfg_gauss_i     (gauss_eff),
    .in_valid_i      (sq_valid),
    .in_ready_o      (sq_ready),
    .in_ok_i         (sq_ok & image),       // SOLID / gradient: no texel lookups
    .in_u_i          (sq_u),
    .in_v_i          (sq_v),
    .in_dx_i         (sq_dx),
    .in_dy_i         (sq_dy),
    .in_plane_i      (sq_plane),
    .in_tag_i        ({sq_plane, sq_gmask, sq_grp, sq_emit, sq_last, sq_tag}),
    .out_valid_o     (ta_valid),
    .out_ready_i     (ta_ready),
    .out_pass_o      (ta_pass),
    .out_solid_o     (ta_solid),
    .out_addr_o      (ta_addr),
    .out_ypar_o      (ta_ypar),
    .out_fx_o        (ta_fx),
    .out_fy_o        (ta_fy),
    .out_border_o    (ta_border),
    .out_sh_o        (ta_sh),
    .out_tag_o       (ta_tag_raw)
  );
  assign ta_tag = {ta_solid | ta_tag_raw[TW-1], ta_tag_raw[TW-2:0]};   // the raw bit is 0 (from gr_tag)
  assign ta_sub = ta_tag_raw[TW +: SUBW];

  g2d_tex_split #(.TAG_W(STW)) u_split (
    .clk_i,
    .rst_ni,
    .clr_i,
    .cfg_b24_i   (fmt_b24(s_fmt)),
    .in_valid_i  (ta_valid),
    .in_ready_o  (ta_ready),
    .in_pass_i   (ta_pass),
    .in_gmask_i  (ta_sub[7:4]),
    .in_addr_i   (ta_addr),
    .in_ypar_i   (ta_ypar),
    .in_fx_i     (ta_fx),
    .in_fy_i     (ta_fy),
    .in_tag_i    ({ta_sh, ta_border, ta_sub, ta_tag}),
    .out_valid_o (sp_valid),
    .out_ready_i (sp_ready),
    .out_pass_o  (sp_pass),
    .out_smask_o (sp_smask),
    .out_last_o  (sp_last),
    .out_hi_o    (sp_hi),
    .out_addr_o  (sp_addr),
    .out_ypar_o  (sp_ypar),
    .out_fx_o    (sp_fx),
    .out_fy_o    (sp_fy),
    .out_tag_o   (sp_tag)
  );

  // v1.7: register slice in front of the source cache: its tag-stage stall no longer reaches the split, texel
  // addressing and sequencer combinationally (timing, T-1)
  localparam int unsigned CSW = 1 + 4 + 128 + 2 + 16 + 2 + STW;
  logic             cs_valid, cs_ready, cs_pass, cs_last, cs_hi;
  logic [3:0]       cs_smask;
  logic [3:0][31:0] cs_addr;
  logic [1:0]       cs_ypar;
  logic [7:0]       cs_fx, cs_fy;
  logic [STW-1:0]   cs_tag;
  g2d_spill_reg #(.WIDTH(CSW)) u_cache_in (
    .clk_i, .rst_ni, .clr_i,
    .in_valid_i  (sp_valid),
    .in_ready_o  (sp_ready),
    .in_data_i   ({sp_pass, sp_smask, sp_addr, sp_ypar, sp_fx, sp_fy, sp_last, sp_hi, sp_tag}),
    .out_valid_o (cs_valid),
    .out_ready_i (cs_ready),
    .out_data_o  ({cs_pass, cs_smask, cs_addr, cs_ypar, cs_fx, cs_fy, cs_last, cs_hi, cs_tag})
  );

  g2d_src_cache #(.DATA_W(DATA_W), .TAG_W(CTW), .CACHE_BYTES(SRC_CACHE_BYTES)) u_cache (
    .clk_i,
    .rst_ni,
    .clr_i,
    .stop_i,
    .inv_i          (start_q),
    .in_valid_i     (cs_valid),
    .in_ready_o     (cs_ready),
    .in_pass_i      (cs_pass),
    .in_smask_i     (cs_smask),
    .in_addr_i      (cs_addr),
    .in_ypar_i      (cs_ypar),
    .in_fx_i        (cs_fx),
    .in_fy_i        (cs_fy),
    .in_tag_i       ({cs_smask, cs_last, cs_hi, cs_tag}),
    .out_valid_o    (sc_valid),
    .out_ready_i    (sc_ready),
    .out_pass_o     (sc_pass),
    .out_tex_o      (sc_tex),
    .out_fx_o       (sc_fx),
    .out_fy_o       (sc_fy),
    .out_tag_o      (sc_tag),
    .rd_req_valid_o (src_req_valid_o),
    .rd_req_ready_i (src_req_ready_i),
    .rd_req_addr_o  (src_req_addr_o),
    .rd_req_len_o   (src_req_len_o),
    .rd_rsp_valid_i (src_rsp_valid_i),
    .rd_rsp_ready_o (src_rsp_ready_o),
    .rd_rsp_data_i  (rsp_data_i),
    .idle_o         (cache_idle),
    .miss_o         (src_miss_o),
    .ram_coll_o (coll_cache)
  );

  g2d_tex_merge #(.TAG_W(MTW)) u_merge (
    .clk_i,
    .rst_ni,
    .clr_i,
    .cfg_lg_bits_i (fmt_lg_bits(s_fmt)),
    .cfg_yuv_i       (yuv_cls),
    .cfg_yuv_alpha_i (s_fmt[6] & ~cls_yv),
    .cfg_uv_swap_i   (uv_swap),
    .in_valid_i    (sc_valid),
    .in_ready_o    (sc_ready),
    .in_pass_i     (sc_pass),
    .in_smask_i    (sc_tag[CTW-1 -: 4]),
    .in_last_i     (sc_tag[STW + 1]),
    .in_hi_i       (sc_tag[STW]),
    .in_emit_i     (sc_tag[TW + 1]),
    .in_plane_i    (sc_tag[TW + 8 +: 2]),
    .in_tex_i      (sc_tex),
    .in_sh_i       (sc_tag[STW-1 -: 12]),
    .in_fx_i       (sc_fx),
    .in_fy_i       (sc_fy),
    .in_tag_i      ({sc_tag[TW + SUBW +: 4], sc_tag[TW + 2 +: 2], sc_tag[TW], sc_tag[TW-1:0]}),
    .out_valid_o   (mg_valid),
    .out_ready_i   (mg_ready),
    .out_pass_o    (mg_pass),
    .out_tex_o     (mg_tex),
    .out_fx_o      (mg_fx),
    .out_fy_o      (mg_fy),
    .out_tag_o     (mg_tag)
  );

  // Ramp RAM routing (one lookup port, rendering.md §3.5 / §10.1):
  //   indexed source: merge -> ramp (CLUT lookup of texel t00's index) -> texel unit -> join
  //   otherwise:      merge -> texel unit -> ramp (gradient lookup of the tag's index) -> join
  // Gradient and indexed pixels wait until the ramp / CLUT of this draw is loaded, except under stop_i
  // (the load may never be issued; the pixels are discarded, B-5.5).
  logic           ramp_ready, ramp_idle, ramp_in_valid, ramp_in_ready, ramp_gate;
  logic           ro_valid, ro_ready, rp_valid, rp_ready;
  logic [7:0]     ramp_in_idx;
  logic [RPW-1:0] ramp_in_pass, ro_pass, rp_pass;
  logic [31:0]    ro_word, ramp_word;
  logic           rsrc_valid, rsrc_ready;   // the stream feeding the ramp

  assign ramp_gate     = ~(grad | idx_src) | ramp_ready | stop_i;
  assign rsrc_valid    = idx_src ? mg_valid : tx0_valid;
  assign ramp_in_valid = rsrc_valid & ramp_gate;
  assign rsrc_ready    = ramp_in_ready & ramp_gate;
  assign ramp_in_idx   = idx_src ? mg_tex[0][7:0] : tx0_tag[7:0];
  assign ramp_in_pass  = idx_src ? RPW'({mg_pass, mg_tag}) : {tx0_pass, tx0_azero, tx0_s, tx0_tag};

  g2d_ramp #(.DATA_W(DATA_W), .PASS_W(RPW)) u_ramp (
    .clk_i,
    .rst_ni,
    .clr_i,
    .stop_i,
    .kick_i,
    .wr_i,
    .need_i         (start_q & (grad | idx_src)),
    .need_addr_i    (grad ? g_ramp : clut),
    .ready_o        (ramp_ready),
    .rd_req_valid_o (ramp_req_valid_o),
    .rd_req_ready_i (ramp_req_ready_i),
    .rd_req_addr_o  (ramp_req_addr_o),
    .rd_req_len_o   (ramp_req_len_o),
    .rd_rsp_valid_i (ramp_rsp_valid_i),
    .rd_rsp_ready_o (ramp_rsp_ready_o),
    .rd_rsp_data_i  (rsp_data_i),
    .rd_rsp_strb_i  (rsp_strb_i),
    .in_valid_i     (ramp_in_valid),
    .in_ready_o     (ramp_in_ready),
    .in_idx_i       (ramp_in_idx),
    .in_pass_i      (ramp_in_pass),
    .out_valid_o    (ro_valid),
    .out_ready_i    (ro_ready),
    .out_word_o     (ro_word),
    .out_pass_o     (ro_pass),
    .idle_o         (ramp_idle),
    .ram_coll_o (coll_ramp)
  );

  // Register slice on the ramp output: g2d_ramp's in_ready depends on its out_ready, and the routing above
  // feeds its output back towards its input (through the texel unit), so the slice breaks that path.
  g2d_spill_reg #(.WIDTH(32 + RPW)) u_ramp_out (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (ro_valid),
    .in_ready_o  (ro_ready),
    .in_data_i   ({ro_word, ro_pass}),
    .out_valid_o (rp_valid),
    .out_ready_i (rp_ready),
    .out_data_o  ({ramp_word, rp_pass})
  );

  // texel unit input: the merge output, or (indexed) the CLUT word of the ramp output for all four texels
  assign te_valid  = idx_src ? rp_valid : mg_valid;
  assign te_pass   = idx_src ? rp_pass[MTW] : mg_pass;
  assign te_tex    = idx_src ? {4{ramp_word}} : mg_tex;
  assign te_border = idx_src ? rp_pass[MTW-1 -: 4] : mg_tag[MTW-1 -: 4];
  assign te_tag    = idx_src ? rp_pass[TW-1:0] : mg_tag[TW-1:0];
  assign te_grp    = idx_src ? rp_pass[TW + 1 +: 2] : mg_tag[TW + 1 +: 2];
  assign te_last   = idx_src ? rp_pass[TW] : mg_tag[TW];
  assign te_fx     = idx_src ? 8'd0 : mg_fx;
  assign te_fy     = idx_src ? 8'd0 : mg_fy;
  assign mg_ready  = idx_src ? rsrc_ready : te_ready;

  // Q-API-19 (b), v1.5: under SRC_PREMUL the border texel (WRAP = COLOR, BORDER_SOLID = 0) is premultiplied like
  // the image texels (rendering.md §5.2 step 4); a per-draw constant. BORDER_SOLID keeps BORDER_COLOR as given.
  logic [31:0] border_tex;
  assign border_tex = premul ? {border[31:24], mul8(border[23:16], border[31:24]), mul8(border[15:8], border[31:24]),
                                mul8(border[7:0], border[31:24])} : border;

  // v1.7: register slice in front of the texel unit, so its S0 (format expansion / YUV conversion) starts from a
  // flop instead of the merge / CLUT routing mux (timing, T-1)
  localparam int unsigned TSW = 1 + 128 + 4 + 8 + 8 + 2 + 1 + TW;
  logic             ts_valid, ts_ready, ts_pass, ts_last;
  logic [3:0][31:0] ts_tex;
  logic [3:0]       ts_border;
  logic [7:0]       ts_fx, ts_fy;
  logic [1:0]       ts_grp;
  logic [TW-1:0]    ts_tag;
  g2d_spill_reg #(.WIDTH(TSW), .SKID(1'b0)) u_texel_in (
    .clk_i, .rst_ni, .clr_i,
    .in_valid_i  (te_valid),
    .in_ready_o  (te_ready),
    .in_data_i   ({te_pass, te_tex, te_border, te_fx, te_fy, te_grp, te_last, te_tag}),
    .out_valid_o (ts_valid),
    .out_ready_i (ts_ready),
    .out_data_o  ({ts_pass, ts_tex, ts_border, ts_fx, ts_fy, ts_grp, ts_last, ts_tag})
  );

  g2d_texel #(.TAG_W(TW)) u_texel (
    .clk_i,
    .rst_ni,
    .clr_i,
    .cfg_fmt_i          (s_fmt),
    .cfg_force_opaque_i (opaque),
    .cfg_ckey_i         (ckey),
    .cfg_premul_i       (premul),
    .cfg_img_mul_i      (img_mul),
    .cfg_paint_i        (color),
    .cfg_border_i       (border_tex),
    .cfg_yuv_i          (yuv),
    .cfg_yuv709_i       (yuv709),
    .cfg_color_en_i     (color_en),
    .cfg_gamma_i        (gamma),
    .cfg_cm_i           (cmat),
    .cfg_cb_i           (cbias),
    .cfg_gauss_i        (gauss_eff),
    .cfg_w0_i           (gw0),
    .cfg_w1_i           (gw1),
    .cfg_w2_i           (gw2),
    .in_valid_i         (ts_valid),
    .in_ready_o         (ts_ready),
    .in_pass_i          (ts_pass),
    .in_tex_i           (ts_tex),
    .in_border_i        (ts_border),
    .in_fx_i            (ts_fx),
    .in_fy_i            (ts_fy),
    .in_grp_i           (ts_grp),
    .in_last_i          (ts_last),
    .in_tag_i           (ts_tag),
    .out_valid_o        (tx0_valid),
    .out_ready_i        (tx0_ready),
    .out_pass_o         (tx0_pass),
    .out_s_o            (tx0_s),
    .out_azero_o        (tx0_azero),
    .out_tag_o          (tx0_tag)
  );

  // paint result at the join: the texel unit output (indexed) or the ramp output
  logic          tx_valid, tx_ready, tx_pass, tx_azero, unused_idx;
  logic [31:0]   tx_s;
  logic [TW-1:0] tx_tag;
  assign tx_valid  = idx_src ? tx0_valid : rp_valid;
  assign tx_pass   = idx_src ? tx0_pass : rp_pass[RPW-1];
  assign tx_azero  = idx_src ? tx0_azero : rp_pass[RPW-2];
  assign tx_s      = idx_src ? tx0_s : rp_pass[TW +: 32];
  assign tx_tag    = idx_src ? tx0_tag : rp_pass[TW-1:0];
  assign tx0_ready = idx_src ? tx_ready : rsrc_ready;
  assign rp_ready  = idx_src ? te_ready : tx_ready;
  assign unused_idx = ^tx_tag[7:0];   // the ramp index, used before the join

  // ---------------------------------------------------------------- destination
  logic        dp_valid, dp_ready, dst_idle;
  logic [31:0] dp_d;

  g2d_dst_rd #(.DATA_W(DATA_W)) u_dst (
    .clk_i,
    .rst_ni,
    .clr_i,
    .stop_i,
    .cfg_lg_i       (fmt_lg_bytes(t_fmt)),
    .span_valid_i   (dspan_valid),
    .span_ready_o   (dspan_ready),
    .span_addr_i    (span_addr),
    .span_n_i       (span_n),
    .rd_req_valid_o (dst_req_valid_o),
    .rd_req_ready_i (dst_req_ready_i),
    .rd_req_addr_o  (dst_req_addr_o),
    .rd_req_len_o   (dst_req_len_o),
    .rd_rsp_valid_i (dst_rsp_valid_i),
    .rd_rsp_ready_o (dst_rsp_ready_o),
    .rd_rsp_data_i  (rsp_data_i),
    .pix_valid_o    (dp_valid),
    .pix_ready_i    (dp_ready),
    .pix_d_o        (dp_d),
    .idle_o         (dst_idle)
  );

  // ---------------------------------------------------------------- mask layer (v1.6, rendering.md §6.1)
  logic       mk_valid, mk_ready, mask_idle;
  logic [7:0] mk;

  g2d_mask_rd #(.DATA_W(DATA_W)) u_mask (
    .clk_i,
    .rst_ni,
    .clr_i,
    .stop_i,
    .cfg_addr_i     (m_addr),
    .cfg_stride_i   (m_stride),
    .cfg_w_i        (m_w),
    .cfg_h_i        (m_h),
    .cfg_ox_i       (m_ox),
    .cfg_oy_i       (m_oy),
    .span_valid_i   (mspan_valid),
    .span_ready_o   (mspan_ready),
    .span_x_i       (span_x),
    .span_y_i       (span_y),
    .span_n_i       (span_n),
    .rd_req_valid_o (mask_req_valid_o),
    .rd_req_ready_i (mask_req_ready_i),
    .rd_req_addr_o  (mask_req_addr_o),
    .rd_req_len_o   (mask_req_len_o),
    .rd_rsp_valid_i (mask_rsp_valid_i),
    .rd_rsp_ready_o (mask_rsp_ready_o),
    .rd_rsp_data_i  (rsp_data_i),
    .pix_valid_o    (mk_valid),
    .pix_ready_i    (mk_ready),
    .pix_m_o        (mk),
    .idle_o         (mask_idle)
  );

  // ---------------------------------------------------------------- path coverage
  logic       cv_valid, cv_ready, ras_busy, ras_idle;
  logic [7:0] cv;

  g2d_raster #(.DATA_W(DATA_W)) u_raster (
    .clk_i,
    .rst_ni,
    .clr_i,
    .stop_i,
    .start_i        (rect_valid & path),
    .cfg_addr_i     (e_addr),
    .cfg_count_i    (e_count),
    .cfg_aa_i       (aa),
    .cfg_eo_i       (eo),
    .cfg_rx0_i      (rect_x0),
    .cfg_ry0_i      (rect_y0),
    .cfg_rx1_i      (rect_x1),
    .cfg_ry1_i      (rect_y1),
    .rd_req_valid_o (edge_req_valid_o),
    .rd_req_ready_i (edge_req_ready_i),
    .rd_req_addr_o  (edge_req_addr_o),
    .rd_req_len_o   (edge_req_len_o),
    .rd_rsp_valid_i (edge_rsp_valid_i),
    .rd_rsp_ready_o (edge_rsp_ready_o),
    .rd_rsp_data_i  (rsp_data_i),
    .cov_valid_o    (cv_valid),
    .cov_ready_i    (cv_ready),
    .cov_o          (cv),
    .busy_o         (ras_busy),
    .idle_o         (ras_idle),
    .ram_coll_o (coll_raster)
  );

  // ---------------------------------------------------------------- join, blend
  logic        bl_in_valid, bl_in_ready, bl_valid, bl_ready, bl_wr;
  logic [31:0] bl_pix;
  logic        dst_ok, cov_ok, mk_ok, cov_on, img_skip;
  logic [7:0]  cov, cov0;

  // join of paint, destination (when read) and path coverage (paths); under stop_i the join waits for
  // neither (their producers may never deliver), so the pipeline drains
  assign dst_ok      = ~dst_rd | dp_valid | stop_i;
  assign cov_ok      = ~path | cv_valid | stop_i;
  assign mk_ok       = ~mask_on | mk_valid | stop_i;
  assign bl_in_valid = tx_valid & dst_ok & cov_ok & mk_ok;
  assign tx_ready    = bl_in_ready & dst_ok & cov_ok & mk_ok;
  assign dp_ready    = tx_valid & bl_in_ready & cov_ok & mk_ok & dst_rd & ~stop_i;
  assign cv_ready    = tx_valid & bl_in_ready & dst_ok & mk_ok & path & ~stop_i;
  assign mk_ready    = tx_valid & bl_in_ready & dst_ok & cov_ok & mask_on & ~stop_i;
  // IMAGE: cov = 0 for a pixel failing the source test, or (TRANSPARENT) with sampled A = 0 (§5.2 step 6)
  // BORDER_SOLID outside pixels (tag bit 13) take BORDER_COLOR as a plain colour: no TRANSPARENT skip (§7.5)
  // DRAW_PATH + IMAGE (F-1, v1.5): the path coverage where the pixel passes the same source tests, else 0
  assign img_skip    = image & (~tx_pass | (transp & tx_azero & ~tx_tag[13]));
  assign cov0        = img_skip ? 8'h00 : path ? cv : 8'hFF;
  assign cov         = mask_on ? mul8(cov0, mk) : cov0;      // §6.1: coverage x mask
  assign cov_on      = cov != 8'h00;

  g2d_blend #(.TAG_W(1)) u_blend (
    .clk_i,
    .rst_ni,
    .clr_i,
    .cfg_mode_i      (mode),
    .cfg_family_i    (family),
    .cfg_func_i      (bfunc),
    .cfg_const_i     (color),
    .cfg_stencil_i   (image & stencil),
    .cfg_fmt_i       (t_fmt),
    .cfg_src_amode_i (a_smode),
    .cfg_src_aval_i  (a_sval),
    .cfg_dst_amode_i (a_dmode),
    .cfg_dst_aval_i  (a_dval),
    .cfg_dither_i    (dither),
    .in_valid_i      (bl_in_valid),
    .in_ready_o      (bl_in_ready),
    .in_s_i          (grad ? (tx_tag[8] ? color : ramp_word) :                    // tag[8]: FILL outside
                      image ? (tx_tag[13] ? border : tx_s) : color),             // tag[13]: solid border
    .in_d_i          (dp_d),
    .in_cov_i        (cov),
    .in_dxy_i        (tx_tag[12:9]),
    .in_tag_i        (cov_on),
    .out_valid_o     (bl_valid),
    .out_ready_i     (bl_ready),
    .out_pix_o       (bl_pix),
    .out_tag_o       (bl_wr)
  );

  // ---------------------------------------------------------------- write combiner
  logic wc_idle;

  g2d_wcomb #(.DATA_W(DATA_W)) u_wcomb (
    .clk_i,
    .rst_ni,
    .clr_i,
    .cfg_lg_i       (fmt_lg_bytes(t_fmt)),
    .span_valid_i   (wspan_valid),
    .span_ready_o   (wspan_ready),
    .span_addr_i    (span_addr),
    .span_n_i       (span_n),
    .pix_valid_i    (bl_valid),
    .pix_ready_o    (bl_ready),
    .pix_data_i     (bl_pix),
    .pix_wr_i       (bl_wr),
    .wr_req_valid_o,
    .wr_req_ready_i,
    .wr_req_addr_o,
    .wr_req_len_o,
    .wr_dat_valid_o,
    .wr_dat_ready_i,
    .wr_dat_data_o,
    .wr_dat_strb_o,
    .idle_o         (wc_idle),
    .pixels_o       (pixel_o),
    .ram_coll_o (coll_wcomb)
  );

  // ---------------------------------------------------------------- draw control
  // Pixels between the walker and the write combiner (every pipeline stage in between).
  logic [10:0] inflight_q;
  logic        wp_fire, wc_fire, wp_fire_q, wc_fire_q;   // v1.7: counted one cycle later (timing, T-1)
  assign wp_fire = wp_valid & wp_ready;
  assign wc_fire = bl_valid & bl_ready;

  logic drained;
  assign drained = ~walk_busy & (inflight_q == '0) & ~wp_fire_q & ~wc_fire_q & wc_idle & cache_idle & dst_idle & ras_idle & ~ras_busy &
                   ramp_idle & mask_idle;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      busy_q      <= 1'b0;
      start_q     <= 1'b0;
      walk_done_q <= 1'b0;
      inflight_q  <= '0;
      wp_fire_q   <= 1'b0;
      wc_fire_q   <= 1'b0;
    end else if (clr_i) begin
      busy_q      <= 1'b0;
      start_q     <= 1'b0;
      walk_done_q <= 1'b0;
      inflight_q  <= '0;
      wp_fire_q   <= 1'b0;
      wc_fire_q   <= 1'b0;
    end else begin
      start_q    <= accept;
      wp_fire_q  <= wp_fire;
      wc_fire_q  <= wc_fire;
      inflight_q <= inflight_q + 11'(wp_fire_q) - 11'(wc_fire_q);
      if (accept)            busy_q <= 1'b1;
      else if (busy_q && !start_q && drained) busy_q <= 1'b0;
      if (accept)            walk_done_q <= 1'b0;
      else if (walk_done)    walk_done_q <= 1'b1;
    end
  end

  assign draw_done_o = busy_q & ~start_q & drained & walk_done_q;
  assign idle_o      = ~busy_q;

  logic unused;
  assign unused = ^{draw_payload_i[3][31:24], state_i};

endmodule
