// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Draw validation (rendering.md §13), evaluated on the live state at a draw command, before any of the
// draw's memory accesses. Two pipeline stages: start_i at cycle 0, results valid with done_o at
// cycle 2. The inputs must stay stable for those two cycles (the parser holds them).
//
// BAD_STATE (§13.1, ERR_ADDR = 0) wins over ADDR_RANGE (§13.2, ERR_ADDR = first failing extent in table
// order: target, source, CLUT, ramp, edges).

module g2d_draw_check
  import g2d_pkg::*;
  import g2d_state_pkg::*;
#(
  parameter logic [5:0] FEATURES = 6'b11_1111   // v1.7: optional units built; a draw needing an absent one is BAD_STATE
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              start_i,
  input  logic [STATE_SIZE-1:0][31:0] state_i,
  input  logic              is_path_i,
  input  logic [31:0]       edge_addr_i,
  input  logic [23:0]       edge_count_i,
  input  logic [31:0]       win_lo_i,
  input  logic [31:0]       win_hi_i,
  output logic              done_o,
  output logic              bad_state_o,
  output logic              addr_range_o,
  output logic [31:0]       err_addr_o
);

  // ---------------------------------------------------------------- field extraction
  logic [31:0] t_addr, s_addr, ramp, clut;
  logic [2:0]  wrap;
  logic [15:0] t_stride, s_stride;
  logic [13:0] t_w, t_h, s_w, s_h;
  logic [7:0]  t_fmt, s_fmt;
  logic [1:0]  src, a_src, a_dst;
  logic [13:0] sr_x0, sr_y0, sr_x1, sr_y1;
  logic [31:0] m_addr;
  logic [15:0] m_stride;
  logic [13:0] m_w, m_h;
  logic        mask_on;
  logic [1:0]  fam;
  // YUV planes (v1.6b, rendering.md §3.7 / §13): rows, row bytes, used flags of planes 1 and 2
  logic [31:0] p1_addr, p2_addr;
  logic [15:0] p1_stride, p2_stride;
  logic        yuv, p1_on, p2_on, p2_alpha;
  logic        sxs, sys;                     // chroma subsampling shifts
  logic [13:0] cw, chh, p1_rows, p2_rows;
  logic [16:0] p1_row, p2_row;
  assign yuv       = fmt_yuv(s_fmt);
  assign p1_addr   = state_i[G2D_ST_SRC_PLANE1_ADDR];
  assign p2_addr   = state_i[G2D_ST_SRC_PLANE2_ADDR];
  assign p1_stride = state_i[G2D_ST_SRC_PLANE_STRIDE][G2D_ST_SRC_PLANE_STRIDE_S1_LSB +: 16];
  assign p2_stride = state_i[G2D_ST_SRC_PLANE_STRIDE][G2D_ST_SRC_PLANE_STRIDE_S2_LSB +: 16];
  assign sxs       = s_fmt[5:4] <= 2'd1;
  assign sys       = s_fmt[5:4] == 2'd0;
  assign cw        = sxs ? (s_w + 14'd1) >> 1 : s_w;
  assign chh       = sys ? (s_h + 14'd1) >> 1 : s_h;
  assign p1_on     = yuv && s_fmt[3:0] != 4'h3;
  assign p2_alpha  = yuv && s_fmt[6];
  assign p2_on     = yuv && (s_fmt[3:0] == 4'h5 || s_fmt[6]);
  assign p1_rows   = chh;
  assign p1_row    = (s_fmt[3:0] == 4'h4) ? {2'b0, cw, 1'b0} : 17'(cw);
  assign p2_rows   = p2_alpha ? s_h : chh;
  assign p2_row    = p2_alpha ? 17'(s_w) : 17'(cw);

  // last byte of a plane: addr + stride (rows - 1) + row - 1
  function automatic logic [33:0] plane_end(logic [31:0] addr, logic [15:0] stride, logic [13:0] rows, logic [16:0] row);
    return 34'(addr) + 34'(30'(stride) * 30'(rows - 14'd1)) + 34'(row) - 34'd1;
  endfunction
  logic [3:0]  bmode;
  logic [28:0] bf;
  assign fam   = state_i[G2D_ST_BLEND][G2D_ST_BLEND_FAMILY_LSB +: 2];
  assign bmode = state_i[G2D_ST_BLEND][G2D_ST_BLEND_MODE_LSB +: 4];
  assign bf    = state_i[G2D_ST_BLEND_FUNC][28:0];

  // §13.1 / §5.7 (v1.6): codes inside the family's tables
  function automatic logic family_ok(logic [1:0] f, logic [3:0] m, logic [28:0] w);
    unique case (f)
      2'd1:    return m <= 4'd9;
      2'd2:    return m <= 4'd6;
      2'd3:    return w[3:0] <= 4'd8 && w[12:9] <= 4'd8 && w[8:4] <= 5'd16 && w[17:13] <= 5'd16 && w[20:18] <= 3'd4 &&
                      (w[24:21] <= 4'd10 || w[24:21] == 4'd15) && (w[28:25] <= 4'd10 || w[28:25] == 4'd15);
      default: return 1'b1;
    endcase
  endfunction
  assign m_addr   = state_i[G2D_ST_MASK_ADDR];
  assign m_stride = state_i[G2D_ST_MASK_STRIDE][15:0];
  assign m_w      = state_i[G2D_ST_MASK_SIZE][G2D_ST_MASK_SIZE_W_LSB +: 14];
  assign m_h      = state_i[G2D_ST_MASK_SIZE][G2D_ST_MASK_SIZE_H_LSB +: 14];
  assign mask_on  = state_i[G2D_ST_MASK_CTRL][G2D_ST_MASK_CTRL_EN_LSB];

  assign t_addr   = state_i[G2D_ST_TGT_ADDR];
  assign t_stride = state_i[G2D_ST_TGT_STRIDE][15:0];
  assign t_w      = state_i[G2D_ST_TGT_SIZE][G2D_ST_TGT_SIZE_W_LSB +: 14];
  assign t_h      = state_i[G2D_ST_TGT_SIZE][G2D_ST_TGT_SIZE_H_LSB +: 14];
  assign t_fmt    = state_i[G2D_ST_TGT_FMT][7:0];
  assign s_addr   = state_i[G2D_ST_SRC_ADDR];
  assign s_stride = state_i[G2D_ST_SRC_STRIDE][15:0];
  assign s_w      = state_i[G2D_ST_SRC_SIZE][G2D_ST_SRC_SIZE_W_LSB +: 14];
  assign s_h      = state_i[G2D_ST_SRC_SIZE][G2D_ST_SRC_SIZE_H_LSB +: 14];
  assign s_fmt    = state_i[G2D_ST_SRC_FMT][G2D_ST_SRC_FMT_FMT_LSB +: 8];
  assign ramp     = state_i[G2D_ST_GRAD_RAMP_ADDR];
  assign clut     = state_i[G2D_ST_CLUT_ADDR];
  assign wrap     = state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_WRAP_LSB +: 3];
  assign src      = state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_SRC_LSB +: 2];
  assign a_src    = state_i[G2D_ST_ALPHA_CTRL][G2D_ST_ALPHA_CTRL_SRC_MODE_LSB +: 2];
  assign a_dst    = state_i[G2D_ST_ALPHA_CTRL][G2D_ST_ALPHA_CTRL_DST_MODE_LSB +: 2];
  assign sr_x0    = state_i[G2D_ST_SRC_RECT_MIN][G2D_ST_SRC_RECT_MIN_X0_LSB +: 14];
  assign sr_y0    = state_i[G2D_ST_SRC_RECT_MIN][G2D_ST_SRC_RECT_MIN_Y0_LSB +: 14];
  assign sr_x1    = state_i[G2D_ST_SRC_RECT_MAX][G2D_ST_SRC_RECT_MAX_X1_LSB +: 14];
  assign sr_y1    = state_i[G2D_ST_SRC_RECT_MAX][G2D_ST_SRC_RECT_MAX_Y1_LSB +: 14];

  // Bytes per row of w pixels: ceil(w * bpp / 8) (v1.4: sub-byte sources; v1.6b YUV: the Y / packed plane).
  function automatic logic [16:0] row_bytes(logic [7:0] fmt, logic [13:0] w);
    logic [19:0] bits;
    if (fmt_yuv(fmt)) return (fmt[3:0] == 4'h3) ? {2'b0, w + 14'd1, 1'b0} & 17'h1FFFC : 17'(w);  // 4 ceil(w/2) / w
    bits = fmt_b24(fmt) ? 20'(w) * 20'd24 : 20'(w) << fmt_lg_bits(fmt);   // 24-bit sources (v1.6b)
    return 17'((bits + 20'd7) >> 3);
  endfunction

  // §13.1 buffer rules: valid FMT, 1 <= w,h <= 8192, address and stride aligned to the pixel size (any byte
  // for formats of 8 bits or fewer), stride >= ceil(w * bpp / 8).
  function automatic logic buffer_ok(logic [7:0] fmt, logic [13:0] w, logic [13:0] h, logic [1:0] addr,
                                     logic [15:0] stride);
    logic [1:0] lg;
    lg = (fmt_b24(fmt) || fmt_yuv(fmt)) ? 2'd0 : fmt_lg_bytes(fmt);   // 24-bit, YUV (v1.6b): any byte address
    if (fmt_yuv(fmt) && fmt[3:0] == 4'h3) lg = 2'd2;                 // YUY2: 4-byte pairs
    return fmt_valid(fmt) && w != 0 && w <= 14'd8192 && h != 0 && h <= 14'd8192 &&
           (lg == 2'd0 || (lg == 2'd1 && addr[0] == 1'b0 && stride[0] == 1'b0) ||
            (lg == 2'd2 && addr[1:0] == 2'b00 && stride[1:0] == 2'b00)) &&
           17'(stride) >= row_bytes(fmt, w);
  endfunction

  // Last byte of a buffer, 34 bits: addr + stride*(h-1) + ceil(w*bpp/8) - 1.
  function automatic logic [33:0] buffer_end(logic [7:0] fmt, logic [13:0] w, logic [13:0] h,
                                             logic [31:0] addr, logic [15:0] stride);
    logic [29:0] rows;
    rows = 30'(stride) * 30'(h - 14'd1);
    return 34'(addr) + 34'(rows) + 34'(row_bytes(fmt, w)) - 34'd1;
  endfunction

  // ---------------------------------------------------------------- stage 1
  logic        bad_d;
  logic [33:0] t_end_d, s_end_d, c_end_d, r_end_d, e_end_d, m_end_d, p1_end_d, p2_end_d;
  logic        chk_src_d, chk_clut_d, chk_ramp_d, chk_edge_d;

  always_comb begin
    logic img, grad, path_edges, idx;
    img        = (src == PAINT_IMAGE);
    grad       = (src == PAINT_GRAD) || (src == PAINT_RADIAL);   // v1.5: radial uses the ramp too
    idx        = img && fmt_indexed(s_fmt);
    path_edges = is_path_i && edge_count_i != '0;
    bad_d = !fmt_tgt_valid(t_fmt) || !buffer_ok(t_fmt, t_w, t_h, t_addr[1:0], t_stride) ||
            a_src > 2'd2 || a_dst > 2'd2 || !family_ok(fam, bmode, bf) ||
            (state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_STENCIL_LSB] &&          // §5.3.1 stencil (v1.6)
             (!img || fam != 2'd0 || a_src != 2'd0 || state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_IMG_MUL_LSB])) ||
            (img && !(buffer_ok(s_fmt, s_w, s_h, s_addr[1:0], s_stride) && sr_x0 < sr_x1 && sr_x1 <= s_w &&
                      sr_y0 < sr_y1 && sr_y1 <= s_h && wrap <= 3'd4)) ||
            (idx && clut[1:0] != 2'b00) ||
            (img && p1_on && 17'(p1_stride) < p1_row) || (img && p2_on && 17'(p2_stride) < p2_row) ||   // v1.6b YUV
            (img && yuv && s_fmt[3:0] == 4'h4 && (p1_addr[0] || p1_stride[0])) ||    // U V plane: 2-byte pairs
            (img && yuv && state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_GAUSS_LSB]) ||
            (img && state_i[G2D_ST_SRC_FMT][G2D_ST_SRC_FMT_YUV_FULL_LSB]) ||        // reserved full range (§3.7)
            (img && state_i[G2D_ST_COLOR_CTRL][G2D_ST_COLOR_CTRL_GAMMA_LSB +: 2] == 2'd3) ||   // §5.2.1 (v1.6b)
            // v1.7: units left out of this build (parameter FEATURES, rendering.md §13.1)
            (!FEATURES[FEAT_FAMILY] && fam != 2'd0) ||
            (!FEATURES[FEAT_GAUSS] && img && state_i[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_GAUSS_LSB]) ||
            (!FEATURES[FEAT_YUV] && img && yuv) ||
            (!FEATURES[FEAT_COLOR] && img && (state_i[G2D_ST_COLOR_CTRL][G2D_ST_COLOR_CTRL_EN_LSB] ||
                                               state_i[G2D_ST_COLOR_CTRL][G2D_ST_COLOR_CTRL_GAMMA_LSB +: 2] != 2'd0)) ||
            (!FEATURES[FEAT_RADIAL] && src == PAINT_RADIAL) ||
            (!FEATURES[FEAT_MASK] && mask_on) ||
            (grad && ramp[1:0] != 2'b00) ||
            (path_edges && edge_addr_i[3:0] != 4'h0) ||
            (mask_on && !buffer_ok(8'h00, m_w, m_h, m_addr[1:0], m_stride));   // v1.6: A8 mask layer
    t_end_d    = buffer_end(t_fmt, t_w, t_h, t_addr, t_stride);
    s_end_d    = buffer_end(s_fmt, s_w, s_h, s_addr, s_stride);
    c_end_d    = 34'(clut) + 34'd1023;
    r_end_d    = 34'(ramp) + 34'd1023;
    e_end_d    = 34'(edge_addr_i) + (34'(edge_count_i) << 4) - 34'd1;
    m_end_d    = buffer_end(8'h00, m_w, m_h, m_addr, m_stride);
    p1_end_d   = plane_end(p1_addr, p1_stride, p1_rows, p1_row);
    p2_end_d   = plane_end(p2_addr, p2_stride, p2_rows, p2_row);
    chk_src_d  = img;
    chk_clut_d = idx;
    chk_ramp_d = grad;
    chk_edge_d = path_edges;
  end

  logic        v1_q, bad_q;
  logic [33:0] t_end_q, s_end_q, c_end_q, r_end_q, e_end_q, m_end_q, p1_end_q, p2_end_q;
  logic        chk_mask_q, chk_p1_q, chk_p2_q;
  logic        chk_src_q, chk_clut_q, chk_ramp_q, chk_edge_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) v1_q <= 1'b0;
    else         v1_q <= start_i;
  end

  always_ff @(posedge clk_i) begin
    bad_q      <= bad_d;
    t_end_q    <= t_end_d;
    s_end_q    <= s_end_d;
    c_end_q    <= c_end_d;
    r_end_q    <= r_end_d;
    e_end_q    <= e_end_d;
    m_end_q    <= m_end_d;
    p1_end_q   <= p1_end_d;
    p2_end_q   <= p2_end_d;
    chk_p1_q   <= chk_src_d & p1_on;
    chk_p2_q   <= chk_src_d & p2_on;
    chk_mask_q <= mask_on;
    chk_src_q  <= chk_src_d;
    chk_clut_q <= chk_clut_d;
    chk_ramp_q <= chk_ramp_d;
    chk_edge_q <= chk_edge_d;
  end

  // ---------------------------------------------------------------- stage 2
  function automatic logic extent_ok(logic [31:0] first, logic [33:0] last, logic [31:0] lo, logic [31:0] hi);
    return last[33:32] == 2'b00 && first >= lo && last[31:0] <= hi;
  endfunction

  logic        range_d;
  logic [31:0] addr_d;
  always_comb begin
    range_d = 1'b1;
    addr_d  = '0;
    if (!extent_ok(t_addr, t_end_q, win_lo_i, win_hi_i))                     addr_d = t_addr;
    else if (chk_src_q && !extent_ok(s_addr, s_end_q, win_lo_i, win_hi_i))   addr_d = s_addr;
    else if (chk_p1_q && !extent_ok(p1_addr, p1_end_q, win_lo_i, win_hi_i))  addr_d = p1_addr;   // v1.6b YUV
    else if (chk_p2_q && !extent_ok(p2_addr, p2_end_q, win_lo_i, win_hi_i))  addr_d = p2_addr;
    else if (chk_clut_q && !extent_ok(clut, c_end_q, win_lo_i, win_hi_i))    addr_d = clut;
    else if (chk_ramp_q && !extent_ok(ramp, r_end_q, win_lo_i, win_hi_i))    addr_d = ramp;
    else if (chk_edge_q && !extent_ok(edge_addr_i, e_end_q, win_lo_i, win_hi_i)) addr_d = edge_addr_i;
    else if (chk_mask_q && !extent_ok(m_addr, m_end_q, win_lo_i, win_hi_i))   addr_d = m_addr;    // v1.6
    else                                                                      range_d = 1'b0;
  end

  logic v2_q, bad2_q, range_q;
  logic [31:0] addr_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) v2_q <= 1'b0;
    else         v2_q <= v1_q;
  end
  always_ff @(posedge clk_i) begin
    bad2_q  <= bad_q;
    range_q <= ~bad_q & range_d;
    addr_q  <= bad_q ? 32'd0 : addr_d;
  end

  assign done_o       = v2_q;
  assign bad_state_o  = bad2_q;
  assign addr_range_o = range_q;
  assign err_addr_o   = addr_q;

  logic unused;
  assign unused = ^{state_i};   // only some words and fields are checked

endmodule
