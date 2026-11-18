// SPDX-License-Identifier: Apache-2.0
/* G2D vg_lite driver: clear, blit, copy, scissor, global alpha, colour key (api-subset.md §2-3,
 * rendering.md §5-7, §9.3, §11). */
#include <string.h>

#include "g2d_geom.h"
#include "vg_lite_drv.h"

#define FS(reg, field, v) G2D_FIELD_SET(G2D_ST_##reg##_##field, v)

static int32_t clampi(int32_t v, int32_t lo, int32_t hi) { return v < lo ? lo : v > hi ? hi : v; }

vg_lite_error_t drv_geom_err(int rc)
{
    switch (rc) {
    case G2D_GEOM_OK:
    case G2D_GEOM_EMPTY:       return VG_LITE_SUCCESS;
    case G2D_GEOM_NOT_SUPPORT: return VG_LITE_NOT_SUPPORT;
    case G2D_GEOM_NOMEM:       return VG_LITE_OUT_OF_RESOURCES;
    default:                   return VG_LITE_INVALID_ARGUMENT;
    }
}

void drv_matrix_d(const vg_lite_matrix_t *m, double d[3][3])
{
    int r, c;
    for (r = 0; r < 3; r++)
        for (c = 0; c < 3; c++)
            d[r][c] = m ? (double)m->m[r][c] : (r == c ? 1.0 : 0.0);
}

/* Scissor ∩ target, or the whole target (rendering.md §6). */
void drv_clip_rect(const vg_lite_buffer_t *t, int32_t *x0, int32_t *y0, int32_t *x1, int32_t *y1)
{
    *x0 = 0;
    *y0 = 0;
    *x1 = t->width;
    *y1 = t->height;
    if (g_drv.scissor_on) {
        *x0 = clampi(g_drv.sc_x0, 0, t->width);
        *y0 = clampi(g_drv.sc_y0, 0, t->height);
        *x1 = clampi(g_drv.sc_x1, *x0, t->width);
        *y1 = clampi(g_drv.sc_y1, *y0, t->height);
    }
}

void drv_clip_g2d(const vg_lite_buffer_t *t, g2d_rect_t *r)
{
    drv_clip_rect(t, &r->x0, &r->y0, &r->x1, &r->y1);
}

void drv_state_target(const vg_lite_buffer_t *t)
{
    int32_t x0, y0, x1, y1;
    g_drv.tgt_cpu = t->memory;                 /* recorded in the command buffer at emission (drv_emit_draw) */
    g_drv.tgt_bytes = (uint32_t)t->stride * (uint32_t)t->height;
    drv_state(G2D_ST_TGT_ADDR, t->address);
    drv_state(G2D_ST_TGT_STRIDE, (uint32_t)t->stride);
    drv_state(G2D_ST_TGT_SIZE, FS(TGT_SIZE, W, t->width) | FS(TGT_SIZE, H, t->height));
    drv_state(G2D_ST_TGT_FMT, FS(TGT_FMT, FMT, drv_fmt(t->format)) | FS(TGT_FMT, DITHER, g_drv.dither ? 1 : 0) |
                              FS(TGT_FMT, FLIP_Y, g_drv.mirror ? 1 : 0));
    drv_clip_rect(t, &x0, &y0, &x1, &y1);
    /* mask layer (v1.6): every draw while it is on, except the driver's own mask operations */
    if (g_drv.mask_on && g_drv.mask_set && !g_drv.mask_suspend) {
        drv_state(G2D_ST_MASK_ADDR, g_drv.mask_addr);
        drv_state(G2D_ST_MASK_STRIDE, g_drv.mask_stride);
        drv_state(G2D_ST_MASK_SIZE, FS(MASK_SIZE, W, g_drv.mask_w) | FS(MASK_SIZE, H, g_drv.mask_h));
        drv_state(G2D_ST_MASK_ORIGIN, FS(MASK_ORIGIN, X, (uint32_t)g_drv.mask_x & 0xFFFFu) |
                                      FS(MASK_ORIGIN, Y, (uint32_t)g_drv.mask_y & 0xFFFFu));
        drv_state(G2D_ST_MASK_CTRL, FS(MASK_CTRL, EN, 1));
    } else {
        drv_state(G2D_ST_MASK_CTRL, 0);
    }
    drv_state(G2D_ST_CLIP_MIN, FS(CLIP_MIN, X0, x0) | FS(CLIP_MIN, Y0, y0));
    drv_state(G2D_ST_CLIP_MAX, FS(CLIP_MAX, X1, x1) | FS(CLIP_MAX, Y1, y1));
}

static int blend_code(vg_lite_blend_t b)
{
    switch (b) {
    case VG_LITE_BLEND_NONE:            return G2D_BLEND_NONE;
    case VG_LITE_BLEND_SRC_OVER:        return G2D_BLEND_SRC_OVER;
    case VG_LITE_BLEND_DST_OVER:        return G2D_BLEND_DST_OVER;
    case VG_LITE_BLEND_SRC_IN:          return G2D_BLEND_SRC_IN;
    case VG_LITE_BLEND_DST_IN:          return G2D_BLEND_DST_IN;
    case VG_LITE_BLEND_MULTIPLY:        return G2D_BLEND_MULTIPLY;
    case VG_LITE_BLEND_SCREEN:          return G2D_BLEND_SCREEN;
    case VG_LITE_BLEND_DARKEN:          return G2D_BLEND_DARKEN;
    case VG_LITE_BLEND_LIGHTEN:         return G2D_BLEND_LIGHTEN;
    case VG_LITE_BLEND_ADDITIVE:        return G2D_BLEND_ADDITIVE;
    case VG_LITE_BLEND_SUBTRACT:        return G2D_BLEND_SUBTRACT;
    case VG_LITE_BLEND_NORMAL_LVGL:     return G2D_BLEND_NORMAL_LVGL;
    case VG_LITE_BLEND_ADDITIVE_LVGL:   return G2D_BLEND_ADDITIVE_LVGL;
    case VG_LITE_BLEND_SUBTRACT_LVGL:   return G2D_BLEND_SUBTRACT_LVGL;
    case VG_LITE_BLEND_DIFFERENCE_LVGL: return G2D_BLEND_DIFFERENCE_LVGL;
    case VG_LITE_BLEND_MULTIPLY_LVGL:   return G2D_BLEND_MULTIPLY_LVGL;
    default:                            break;
    }
    /* v1.6 blend families (rendering.md §5.7): BLEND.FAMILY in bits 5:4 */
    if (!drv_has(DRV_FEAT_FAMILY) && (b >= OPENVG_BLEND_SRC || b == VG_LITE_PORTER_DUFF_BLEND))
        return -1;                       /* v1.7: blend families not built (NOT_SUPPORT) */
    if (b >= OPENVG_BLEND_SRC && b <= OPENVG_BLEND_ADDITIVE)
        return (int)(b - OPENVG_BLEND_SRC) | (1 << 4);
    switch (b) {
    case SVG2_BLEND_NORMAL:     return 0 | (2 << 4);
    case SVG2_BLEND_MULTIPLY:   return 1 | (2 << 4);
    case SVG2_BLEND_SCREEN:     return 2 | (2 << 4);
    case SVG2_BLEND_LIGHTEN:    return 3 | (2 << 4);
    case SVG2_BLEND_DARKEN:     return 4 | (2 << 4);
    case SVG2_BLEND_DIFFERENCE: return 5 | (2 << 4);
    case SVG2_BLEND_EXCLUSION:  return 6 | (2 << 4);
    case VG_LITE_PORTER_DUFF_BLEND: return 3 << 4;
    default:                    return -1;
    }
}

/* VG_LITE_PORTER_DUFF_BLEND parameters (rendering.md §5.7 family 3; UNVERIFIED interpretation of the enums). */
vg_lite_error_t vg_lite_blend_func(vg_lite_uint32_t factor_src_alpha, vg_lite_uint32_t factor_src_color,
                                   vg_lite_uint32_t factor_dst_alpha, vg_lite_uint32_t factor_dst_color,
                                   vg_lite_uint32_t final_equation_opcode, vg_lite_uint32_t srcchannelmode,
                                   vg_lite_uint32_t dstchannelmode)
{
    vg_lite_error_t e = drv_check_open();
    uint32_t scm = srcchannelmode == VG_LITE_CHANNEL_MODE_BYPASS ? 15u : srcchannelmode;
    uint32_t dcm = dstchannelmode == VG_LITE_CHANNEL_MODE_BYPASS ? 15u : dstchannelmode;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (factor_src_alpha > 8u || factor_dst_alpha > 8u || factor_src_color > 16u || factor_dst_color > 16u ||
        final_equation_opcode > 4u || (scm > 10u && scm != 15u) || (dcm > 10u && dcm != 15u))
        return VG_LITE_INVALID_ARGUMENT;
    g_drv.blend_func = FS(BLEND_FUNC, FSA, factor_src_alpha) | FS(BLEND_FUNC, FSC, factor_src_color) |
                       FS(BLEND_FUNC, FDA, factor_dst_alpha) | FS(BLEND_FUNC, FDC, factor_dst_color) |
                       FS(BLEND_FUNC, EQ, final_equation_opcode) | FS(BLEND_FUNC, SCM, scm) | FS(BLEND_FUNC, DCM, dcm);
    return VG_LITE_SUCCESS;
}

vg_lite_error_t drv_state_paint(vg_lite_blend_t blend, uint32_t paint_ctrl, uint32_t argb)
{
    int code = blend_code(blend);
    uint32_t samode = g_drv.src_amode;
    if (code < 0)
        return VG_LITE_NOT_SUPPORT;
    if (G2D_FIELD_GET(G2D_ST_PAINT_CTRL_STENCIL, paint_ctrl)) {
        /* §5.3.1: source global alpha folded into the paint alpha (the hardware needs SRC_MODE NORMAL) */
        uint32_t pa = argb >> 24;
        if (samode == 1)
            pa = g_drv.src_aval;
        else if (samode == 2)
            pa = (pa * g_drv.src_aval + 128u + ((pa * g_drv.src_aval + 128u) >> 8)) >> 8;
        argb = (argb & 0x00FFFFFFu) | (pa << 24);
        samode = 0;
    }
    drv_state(G2D_ST_BLEND, (uint32_t)code);                       /* MODE and (v1.6) FAMILY */
    if ((code >> 4) == 3)
        drv_state(G2D_ST_BLEND_FUNC, g_drv.blend_func);
    drv_state(G2D_ST_ALPHA_CTRL, FS(ALPHA_CTRL, SRC_MODE, samode) | FS(ALPHA_CTRL, DST_MODE, g_drv.dst_amode) |
                                 FS(ALPHA_CTRL, SRC_VALUE, g_drv.src_aval) | FS(ALPHA_CTRL, DST_VALUE, g_drv.dst_aval));
    /* v1.6b colour unit (§5.2.1 / §9.10): image draws in hardware, the solid draw colour here */
    drv_state_color(G2D_FIELD_GET(G2D_ST_PAINT_CTRL_SRC, paint_ctrl) == G2D_PAINT_IMAGE);
    if (G2D_FIELD_GET(G2D_ST_PAINT_CTRL_SRC, paint_ctrl) == G2D_PAINT_SOLID)
        argb = drv_color_apply(argb);
    drv_state(G2D_ST_PAINT_CTRL, paint_ctrl);
    drv_state(G2D_ST_PAINT_COLOR, argb);
    return VG_LITE_SUCCESS;
}

vg_lite_error_t drv_emit_rect(int32_t x0, int32_t y0, int32_t x1, int32_t y1)
{
    uint32_t w[3];
    w[0] = G2D_OP_DRAW_RECT << 24;
    w[1] = ((uint32_t)(uint16_t)x0) | ((uint32_t)(uint16_t)y0 << 16);
    w[2] = ((uint32_t)(uint16_t)x1) | ((uint32_t)(uint16_t)y1 << 16);
    return drv_emit_draw(w, 3);
}

/* ------------------------------------------------------------------------------------------------ */
/* Clear (rendering.md §11)                                                                         */
/* ------------------------------------------------------------------------------------------------ */

vg_lite_error_t vg_lite_clear(vg_lite_buffer_t *target, vg_lite_rectangle_t *rect, vg_lite_color_t color)
{
    vg_lite_error_t e = drv_check_open();
    int32_t x0, y0, x1, y1;
    uint32_t sa, sv, da, dv;
    if (e != VG_LITE_SUCCESS)
        return e;
    if ((e = drv_check_target(target)) != VG_LITE_SUCCESS)
        return e;
    if (rect) {
        if (rect->width < 0 || rect->height < 0)
            return VG_LITE_INVALID_ARGUMENT;
        x0 = clampi(rect->x, -32768, 32767);
        y0 = clampi(rect->y, -32768, 32767);
        x1 = clampi(rect->x + rect->width, -32768, 32767);
        y1 = clampi(rect->y + rect->height, -32768, 32767);
    } else {
        x0 = 0;
        y0 = 0;
        x1 = target->width;
        y1 = target->height;
    }
    /* blend NONE, global alpha NORMAL; colour keys apply to image sources only */
    sa = g_drv.src_amode; sv = g_drv.src_aval; da = g_drv.dst_amode; dv = g_drv.dst_aval;
    g_drv.src_amode = g_drv.dst_amode = 0;
    g_drv.src_aval = g_drv.dst_aval = 255;
    drv_state_target(target);
    g_drv.color_suspend++;                 /* clears do not use the colour unit (§9.10) */
    (void)drv_state_paint(VG_LITE_BLEND_NONE, FS(PAINT_CTRL, SRC, G2D_PAINT_SOLID), drv_argb(color));
    g_drv.color_suspend--;
    g_drv.src_amode = sa; g_drv.src_aval = sv; g_drv.dst_amode = da; g_drv.dst_aval = dv;
    if ((e = drv_reserve(drv_draw_words(3))) != VG_LITE_SUCCESS)
        return e;
    return drv_emit_rect(x0, y0, x1, y1);
}

/* ------------------------------------------------------------------------------------------------ */
/* Blit (rendering.md §7, §9.3)                                                                     */
/* ------------------------------------------------------------------------------------------------ */

vg_lite_error_t drv_image_paint(const vg_lite_buffer_t *source, vg_lite_filter_t filter, vg_lite_blend_t blend, int copy,
                                uint32_t *paint)
{
    *paint = FS(PAINT_CTRL, SRC, G2D_PAINT_IMAGE);
    switch (copy ? VG_LITE_NORMAL_IMAGE_MODE : source->image_mode) {
    case VG_LITE_ZERO:
    case VG_LITE_NORMAL_IMAGE_MODE:   break;
    case VG_LITE_MULTIPLY_IMAGE_MODE:
    case VG_LITE_RECOLOR_MODE:        *paint |= FS(PAINT_CTRL, IMG_MUL, 1); break;   /* v1.6: RECOLOR = MULTIPLY (UNVERIFIED) */
    case VG_LITE_STENCIL_MODE:                                                       /* v1.6, rendering.md §5.3.1 */
        if (blend > VG_LITE_BLEND_MULTIPLY_LVGL)
            return VG_LITE_NOT_SUPPORT;   /* only the §5.6 blends */
        *paint |= FS(PAINT_CTRL, STENCIL, 1);
        break;
    case VG_LITE_NONE_IMAGE_MODE:     return VG_LITE_SUCCESS;   /* nothing is drawn: callers check drv_image_none */
    default:                          return VG_LITE_NOT_SUPPORT;
    }
    switch (filter) {
    case VG_LITE_FILTER_POINT:     break;
    case VG_LITE_FILTER_LINEAR:
    case VG_LITE_FILTER_BI_LINEAR: *paint |= FS(PAINT_CTRL, FILTER, 1); break;
    case VG_LITE_FILTER_GAUSSIAN:                                         /* v1.6b, rendering.md §7.3 / §9.10 */
        if (drv_fmt_yuv(drv_fmt(source->format)) || !drv_has(DRV_FEAT_GAUSS))
            return VG_LITE_NOT_SUPPORT;                                   /* not with YUV sources (§13.1) */
        *paint |= FS(PAINT_CTRL, GAUSS, 1);
        drv_state(G2D_ST_GAUSS_W, g_drv.gauss_w);
        drv_state(G2D_ST_GAUSS_W0, g_drv.gauss_w0);
        break;
    default:                       return VG_LITE_NOT_SUPPORT;
    }
    /* An A8 texel (also A4 / A2 / A1, v1.4) is premultiplied white (a, a, a, a) under the Porter-Duff blends
     * and plain white (a, 255, 255, 255) under the LVGL blends 11-15, which multiply S by S.A themselves
     * (OPEN_QUESTIONS Q-API-3, from LVGL v9.6: A8 glyphs use MULTIPLY with a premultiplied colour and SRC_OVER,
     * recoloured A8 / A4 images the same colour with NORMAL_LVGL; only this reading makes both correct).
     * Premultiplying the expanded (a, 255, 255, 255) gives (a, a, a, a) exactly, mul8(255, a) = a. */
    if (!copy && source->image_mode != VG_LITE_STENCIL_MODE && (source->apply_premult ||
                  ((drv_fmt(source->format) == G2D_FMT_A8 || drv_fmt(source->format) == G2D_FMT_A4 ||
                    drv_fmt(source->format) == G2D_FMT_A2 || drv_fmt(source->format) == G2D_FMT_A1) &&   /* OPENVG_A_* too */
                   blend <= VG_LITE_BLEND_SUBTRACT)))   /* the Porter-Duff modes 0-10; v1.6 families take S as non-premultiplied */
        *paint |= FS(PAINT_CTRL, SRC_PREMUL, 1);
    return VG_LITE_SUCCESS;
}

/* SRC_FMT word: format, sub-byte order, TRANSPARENT (rendering.md §3.5, §5.2). */
static uint32_t src_fmt_word(const vg_lite_buffer_t *source, int copy)
{
    int f = drv_fmt(source->format);
    uint32_t hi_first;
    /* Sub-byte order: indexed sources follow index_endian (BIG = first pixel in the high bits); A4 / A2 / A1 / L4
     * put the first pixel in the high bits (LVGL's layout; VGLite's own A4 order is unverified, Q-API-15). */
    if (drv_fmt_indexed(f))
        hi_first = source->index_endian == VG_LITE_INDEX_BIG_ENDIAN;
    else
        hi_first = drv_fmt_bits(f) < 8u && (unsigned)source->format >= 256u;   /* OPENVG_A_1 / A_4: first pixel low */
    /* transparency_mode: OPAQUE does not change alpha (Q-API-11: LVGL sets it on every buffer, A8 glyphs
     * included; FORCE_OPAQUE stays 0); TRANSPARENT skips pixels whose sampled A is 0 (v1.4, UNVERIFIED) */
    return FS(SRC_FMT, FMT, f) | FS(SRC_FMT, SUB_ORDER, hi_first) |
           FS(SRC_FMT, TRANSPARENT, (!copy && source->transparency_mode == VG_LITE_IMAGE_TRANSPARENT) ? 1 : 0) |
           FS(SRC_FMT, YUV_709, drv_fmt_yuv(f) && source->yuv.yuv2rgb == VG_LITE_YUV709) |      /* v1.6b, §3.7 */
           FS(SRC_FMT, UV_SWAP, drv_fmt_yuv(f) && source->yuv.swizzle == VG_LITE_SWIZZLE_VU);
}

vg_lite_error_t drv_state_image(vg_lite_buffer_t *target, vg_lite_buffer_t *source, const g2d_rect_t *srect,
                                const int32_t xf[9], vg_lite_blend_t blend, vg_lite_color_t color, uint32_t paint,
                                int copy, uint32_t border)
{
    vg_lite_error_t e;
    int k;
    /* v1.6b: an OPENVG_* source in another colour space than the target is converted (OpenVG 1.1 §3.4) */
    g_drv.auto_gamma = drv_fmt_linear(source->format) == drv_fmt_linear(target->format) ? 0 :
                       drv_fmt_linear(source->format) ? 2 : 1;
    drv_state_target(target);
    drv_state(G2D_ST_SRC_ADDR, source->address);
    drv_state(G2D_ST_SRC_STRIDE, (uint32_t)source->stride);
    drv_state(G2D_ST_SRC_SIZE, FS(SRC_SIZE, W, source->width) | FS(SRC_SIZE, H, source->height));
    drv_state(G2D_ST_SRC_FMT, src_fmt_word(source, copy));
    if (drv_fmt_yuv(drv_fmt(source->format))) {      /* v1.6b: planes 1 (U V / U) and 2 (V / alpha) */
        int v = (drv_fmt(source->format) & 0xF) == 5;
        drv_state(G2D_ST_SRC_PLANE1_ADDR, source->yuv.uv_planar);
        drv_state(G2D_ST_SRC_PLANE2_ADDR, v ? source->yuv.v_planar : source->yuv.alpha_planar);
        drv_state(G2D_ST_SRC_PLANE_STRIDE, FS(SRC_PLANE_STRIDE, S1, source->yuv.uv_stride) |
                                           FS(SRC_PLANE_STRIDE, S2, v ? source->yuv.v_stride : source->yuv.alpha_stride));
    }
    drv_state(G2D_ST_BORDER_COLOR, G2D_FIELD_GET(G2D_ST_PAINT_CTRL_BORDER_SOLID, paint) ? drv_color_apply(border) : border);
    drv_state(G2D_ST_SRC_RECT_MIN, FS(SRC_RECT_MIN, X0, srect->x0) | FS(SRC_RECT_MIN, Y0, srect->y0));
    drv_state(G2D_ST_SRC_RECT_MAX, FS(SRC_RECT_MAX, X1, srect->x1) | FS(SRC_RECT_MAX, Y1, srect->y1));
    for (k = 0; k < 9; k++)
        drv_state(G2D_ST_XF_A + (unsigned)k, (uint32_t)xf[k]);
    for (k = 0; k < 8; k++)
        drv_state(G2D_ST_CKEY0_LO + (unsigned)k, copy ? 0u : g_drv.ckey[k]);
    if (copy) {
        uint32_t sa = g_drv.src_amode, sv = g_drv.src_aval, da = g_drv.dst_amode, dv = g_drv.dst_aval;
        g_drv.src_amode = g_drv.dst_amode = 0;
        g_drv.src_aval = g_drv.dst_aval = 255;
        e = drv_state_paint(VG_LITE_BLEND_NONE, paint, 0);
        g_drv.src_amode = sa; g_drv.src_aval = sv; g_drv.dst_amode = da; g_drv.dst_aval = dv;
    } else {
        e = drv_state_paint(blend, paint, drv_argb(color));
    }
    g_drv.auto_gamma = 0;
    return e;
}

vg_lite_error_t drv_state_clut(const vg_lite_buffer_t *source)
{
    if (drv_fmt_indexed(drv_fmt(source->format))) {
        /* the CLUT (rendering.md §3.5), copied into this command buffer's arena once per vg_lite_set_CLUT;
         * after drv_reserve, so the draw and its table are in the same buffer */
        drv_cmdbuf_t *b = &g_drv.cb[g_drv.cur];
        if (b->clut_gen != g_drv.clut_gen) {
            void *cpu;
            vg_lite_error_t e;
            if ((e = drv_scratch(1024u, &cpu, &b->clut_gpu)) != VG_LITE_SUCCESS)
                return e;
            memcpy(cpu, g_drv.clut, 1024u);
            g_drv.hal->cache_clean(g_drv.hal->ctx, cpu, 1024u);
            b->clut_gen = g_drv.clut_gen;
        }
        drv_state(G2D_ST_CLUT_ADDR, b->clut_gpu);
    }
    return VG_LITE_SUCCESS;
}

vg_lite_error_t drv_emit_blit(vg_lite_buffer_t *target, vg_lite_buffer_t *source, const g2d_rect_t *srect,
                              const int32_t xf[9], const g2d_rect_t *draw, vg_lite_blend_t blend,
                              vg_lite_color_t color, uint32_t paint, int copy, uint32_t border)
{
    vg_lite_error_t e;
    if ((e = drv_state_image(target, source, srect, xf, blend, color, paint, copy, border)) != VG_LITE_SUCCESS)
        return e;
    if ((e = drv_reserve(drv_draw_words(3))) != VG_LITE_SUCCESS || (e = drv_state_clut(source)) != VG_LITE_SUCCESS)
        return e;
    return drv_emit_rect(draw->x0, draw->y0, draw->x1, draw->y1);
}

static vg_lite_error_t blit(vg_lite_buffer_t *target, vg_lite_buffer_t *source, const g2d_rect_t *srect,
                            vg_lite_matrix_t *matrix, vg_lite_blend_t blend, vg_lite_color_t color,
                            vg_lite_filter_t filter, int copy)
{
    vg_lite_error_t e = drv_check_open();
    double m[3][3];
    g2d_rect_t clip;
    g2d_blit_setup_t bs;
    uint32_t paint;
    int rc;
    if (e != VG_LITE_SUCCESS)
        return e;
    if ((e = drv_check_target(target)) != VG_LITE_SUCCESS)
        return e;
    if ((e = drv_check_buffer(source)) != VG_LITE_SUCCESS)
        return e;
    if (srect->x0 < 0 || srect->y0 < 0 || srect->x0 >= srect->x1 || srect->y0 >= srect->y1 ||
        srect->x1 > source->width || srect->y1 > source->height)
        return VG_LITE_INVALID_ARGUMENT;
    if ((e = drv_image_paint(source, filter, blend, copy, &paint)) != VG_LITE_SUCCESS)
        return e;
    if (!copy && source->image_mode == VG_LITE_NONE_IMAGE_MODE)
        return VG_LITE_SUCCESS;           /* v1.6: the image mode that draws nothing */
    drv_matrix_d(matrix, m);
    drv_clip_g2d(target, &clip);
    rc = g2d_geom_blit_setup(DRV_CM(m), srect, &clip, &bs);
    if (rc != G2D_GEOM_OK)
        return drv_geom_err(rc);       /* EMPTY: nothing to draw, success */
    if (bs.persp)
        paint |= FS(PAINT_CTRL, PERSP, 1);
    return drv_emit_blit(target, source, srect, bs.xf, &bs.draw, blend, color, paint, copy, 0);
}

vg_lite_error_t vg_lite_blit(vg_lite_buffer_t *target, vg_lite_buffer_t *source, vg_lite_matrix_t *matrix,
                             vg_lite_blend_t blend, vg_lite_color_t color, vg_lite_filter_t filter)
{
    g2d_rect_t r;
    if (!source)
        return VG_LITE_INVALID_ARGUMENT;
    r.x0 = 0; r.y0 = 0; r.x1 = source->width; r.y1 = source->height;
    return blit(target, source, &r, matrix, blend, color, filter, 0);
}

vg_lite_error_t vg_lite_blit_rect(vg_lite_buffer_t *target, vg_lite_buffer_t *source, vg_lite_rectangle_t *rect,
                                  vg_lite_matrix_t *matrix, vg_lite_blend_t blend, vg_lite_color_t color,
                                  vg_lite_filter_t filter)
{
    g2d_rect_t r;
    if (!source || !rect || rect->width < 0 || rect->height < 0)
        return VG_LITE_INVALID_ARGUMENT;
    r.x0 = rect->x; r.y0 = rect->y; r.x1 = rect->x + rect->width; r.y1 = rect->y + rect->height;
    return blit(target, source, &r, matrix, blend, color, filter, 0);
}

/* Translate-only copy, blend NONE, point sampling, global alpha / colour keys / image options off. */
vg_lite_error_t vg_lite_copy_image(vg_lite_buffer_t *target, vg_lite_buffer_t *source, vg_lite_int32_t dx,
                                   vg_lite_int32_t dy, vg_lite_int32_t sx, vg_lite_int32_t sy,
                                   vg_lite_uint32_t width, vg_lite_uint32_t height)
{
    g2d_rect_t r;
    vg_lite_matrix_t m;
    if (!source || width > 8192u || height > 8192u)
        return VG_LITE_INVALID_ARGUMENT;
    if (width == 0 || height == 0)
        return drv_check_open();
    r.x0 = sx; r.y0 = sy; r.x1 = sx + (int32_t)width; r.y1 = sy + (int32_t)height;
    memset(&m, 0, sizeof m);
    m.m[0][0] = 1.0f; m.m[1][1] = 1.0f; m.m[2][2] = 1.0f;
    m.m[0][2] = (vg_lite_float_t)(dx - sx);
    m.m[1][2] = (vg_lite_float_t)(dy - sy);
    return blit(target, source, &r, &m, VG_LITE_BLEND_NONE, 0, VG_LITE_FILTER_POINT, 1);
}

/* ------------------------------------------------------------------------------------------------ */
/* Scissor, global alpha, colour key, dither                                                        */
/* ------------------------------------------------------------------------------------------------ */

vg_lite_error_t vg_lite_set_scissor(vg_lite_int32_t x, vg_lite_int32_t y, vg_lite_int32_t right, vg_lite_int32_t bottom)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (x == -1 && y == -1 && right == -1 && bottom == -1) {   /* rendering.md §6: disables it */
        g_drv.scissor_on = 0;
        return VG_LITE_SUCCESS;
    }
    g_drv.sc_x0 = x; g_drv.sc_y0 = y; g_drv.sc_x1 = right; g_drv.sc_y1 = bottom;
    g_drv.scissor_on = 1;
    g_drv.nsr = 0;                       /* one rectangle replaces any vg_lite_scissor_rects set */
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_enable_scissor(vg_lite_void)
{
    vg_lite_error_t e = drv_check_open();
    if (e == VG_LITE_SUCCESS)
        g_drv.scissor_on = 1;
    return e;
}

vg_lite_error_t vg_lite_disable_scissor(vg_lite_void)
{
    vg_lite_error_t e = drv_check_open();
    if (e == VG_LITE_SUCCESS)
        g_drv.scissor_on = 0;
    return e;
}

/* v1.6: the scissor is the union of the rectangles. The driver keeps it as disjoint pieces (each new rectangle minus
 * the pieces so far, cut into at most four rectangles per piece it overlaps) and emits every later draw once per
 * piece with CLIP_MIN / CLIP_MAX set to it (drv_emit_draw), so each pixel is drawn at most once; the single scissor
 * registers hold the bounding box (rendering.md §6). Up to DRV_SR_MAX pieces (else OUT_OF_RESOURCES). */
static int add_piece(int32_t x0, int32_t y0, int32_t x1, int32_t y1, int n)
{
    if (x1 <= x0 || y1 <= y0)
        return n;
    if (n < 0 || n >= DRV_SR_MAX)
        return -1;
    g_drv.sr[n].x0 = x0; g_drv.sr[n].y0 = y0; g_drv.sr[n].x1 = x1; g_drv.sr[n].y1 = y1;
    return n + 1;
}

vg_lite_error_t vg_lite_scissor_rects(vg_lite_buffer_t *target, vg_lite_uint32_t nums, vg_lite_rectangle_t rect[])
{
    vg_lite_error_t e = drv_check_open();
    uint32_t k;
    int n = 0, i;
    int32_t bx0 = 0, by0 = 0, bx1 = 0, by1 = 0;
    (void)target;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (nums == 0) {
        g_drv.scissor_on = 0;
        g_drv.nsr = 0;
        return VG_LITE_SUCCESS;
    }
    if (!rect)
        return VG_LITE_INVALID_ARGUMENT;
    for (k = 0; k < nums; k++)
        if (rect[k].width < 0 || rect[k].height < 0)
            return VG_LITE_INVALID_ARGUMENT;
    if (nums == 1)
        return vg_lite_set_scissor(rect[0].x, rect[0].y, rect[0].x + rect[0].width, rect[0].y + rect[0].height);
    for (k = 0; k < nums && n >= 0; k++) {
        /* the parts of rect k not covered by the pieces so far: cut it against each piece in turn */
        static g2d_rect_t work[DRV_SR_MAX], next[DRV_SR_MAX];
        int nw = 0, nn;
        work[nw].x0 = rect[k].x; work[nw].y0 = rect[k].y;
        work[nw].x1 = rect[k].x + rect[k].width; work[nw].y1 = rect[k].y + rect[k].height;
        if (work[0].x1 > work[0].x0 && work[0].y1 > work[0].y0)
            nw = 1;
        for (i = 0; i < n && nw > 0; i++) {
            const g2d_rect_t *p = &g_drv.sr[i];
            int j;
            nn = 0;
            for (j = 0; j < nw; j++) {
                const g2d_rect_t *r = &work[j];
                int32_t ix0 = r->x0 > p->x0 ? r->x0 : p->x0, iy0 = r->y0 > p->y0 ? r->y0 : p->y0;
                int32_t ix1 = r->x1 < p->x1 ? r->x1 : p->x1, iy1 = r->y1 < p->y1 ? r->y1 : p->y1;
                if (ix1 <= ix0 || iy1 <= iy0) {         /* no overlap: keep it */
                    if (nn + 1 > DRV_SR_MAX)
                        return VG_LITE_OUT_OF_RESOURCES;
                    next[nn++] = *r;
                    continue;
                }
                if (nn + 4 > DRV_SR_MAX)
                    return VG_LITE_OUT_OF_RESOURCES;
                /* r minus the overlap: top band, bottom band, then left and right of the overlap's rows */
                if (iy0 > r->y0) { next[nn].x0 = r->x0; next[nn].y0 = r->y0; next[nn].x1 = r->x1; next[nn].y1 = iy0; nn++; }
                if (iy1 < r->y1) { next[nn].x0 = r->x0; next[nn].y0 = iy1; next[nn].x1 = r->x1; next[nn].y1 = r->y1; nn++; }
                if (ix0 > r->x0) { next[nn].x0 = r->x0; next[nn].y0 = iy0; next[nn].x1 = ix0; next[nn].y1 = iy1; nn++; }
                if (ix1 < r->x1) { next[nn].x0 = ix1; next[nn].y0 = iy0; next[nn].x1 = r->x1; next[nn].y1 = iy1; nn++; }
            }
            memcpy(work, next, (size_t)nn * sizeof work[0]);
            nw = nn;
        }
        for (i = 0; i < nw && n >= 0; i++)
            n = add_piece(work[i].x0, work[i].y0, work[i].x1, work[i].y1, n);
    }
    if (n < 0)
        return VG_LITE_OUT_OF_RESOURCES;
    for (i = 0; i < n; i++) {                       /* bounding box -> the single scissor registers */
        if (i == 0 || g_drv.sr[i].x0 < bx0) bx0 = g_drv.sr[i].x0;
        if (i == 0 || g_drv.sr[i].y0 < by0) by0 = g_drv.sr[i].y0;
        if (i == 0 || g_drv.sr[i].x1 > bx1) bx1 = g_drv.sr[i].x1;
        if (i == 0 || g_drv.sr[i].y1 > by1) by1 = g_drv.sr[i].y1;
    }
    if (n == 0) {                                   /* all empty: nothing may be drawn */
        e = vg_lite_set_scissor(0, 0, 0, 0);
        return e;
    }
    e = vg_lite_set_scissor(bx0, by0, bx1, by1);
    g_drv.nsr = n;
    return e;
}

static vg_lite_error_t alpha_mode(vg_lite_global_alpha_t m, uint32_t *out)
{
    switch (m) {
    case VG_LITE_NORMAL: *out = G2D_ALPHA_NORMAL; return VG_LITE_SUCCESS;
    case VG_LITE_GLOBAL: *out = G2D_ALPHA_GLOBAL; return VG_LITE_SUCCESS;
    case VG_LITE_SCALED: *out = G2D_ALPHA_SCALED; return VG_LITE_SUCCESS;
    default:             return VG_LITE_INVALID_ARGUMENT;
    }
}

vg_lite_error_t vg_lite_source_global_alpha(vg_lite_global_alpha_t alpha_mode_, vg_lite_uint8_t alpha_value)
{
    vg_lite_error_t e = drv_check_open();
    uint32_t m;
    if (e != VG_LITE_SUCCESS || (e = alpha_mode(alpha_mode_, &m)) != VG_LITE_SUCCESS)
        return e;
    g_drv.src_amode = m;
    g_drv.src_aval = alpha_value;
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_dest_global_alpha(vg_lite_global_alpha_t alpha_mode_, vg_lite_uint8_t alpha_value)
{
    vg_lite_error_t e = drv_check_open();
    uint32_t m;
    if (e != VG_LITE_SUCCESS || (e = alpha_mode(alpha_mode_, &m)) != VG_LITE_SUCCESS)
        return e;
    g_drv.dst_amode = m;
    g_drv.dst_aval = alpha_value;
    return VG_LITE_SUCCESS;
}

/* rendering.md §5.4; applies to image sources only (OPEN_QUESTIONS Q-API-2). */
vg_lite_error_t vg_lite_set_color_key(vg_lite_color_key4_t colorkey)
{
    vg_lite_error_t e = drv_check_open();
    int k;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!colorkey)
        return VG_LITE_INVALID_ARGUMENT;
    for (k = 0; k < 4; k++) {
        const vg_lite_color_key_t *c = &colorkey[k];
        g_drv.ckey[2 * k] = (uint32_t)c->low_b | ((uint32_t)c->low_g << 8) | ((uint32_t)c->low_r << 16) |
                            ((uint32_t)(c->enable ? 1u : 0u) << 31);
        g_drv.ckey[2 * k + 1] = (uint32_t)c->high_b | ((uint32_t)c->high_g << 8) | ((uint32_t)c->high_r << 16) |
                                ((uint32_t)c->alpha << 24);
    }
    return VG_LITE_SUCCESS;
}

/* Ordered 4x4 dither on targets with channels of fewer than 8 bits (rendering.md §3.6, our own definition). */
vg_lite_error_t vg_lite_enable_dither(vg_lite_void)
{
    vg_lite_error_t e = drv_check_open();
    if (e == VG_LITE_SUCCESS)
        g_drv.dither = 1;
    return e;
}

vg_lite_error_t vg_lite_disable_dither(vg_lite_void)
{
    vg_lite_error_t e = drv_check_open();
    if (e == VG_LITE_SUCCESS)
        g_drv.dither = 0;
    return e;
}

/* Colour table of the indexed formats (rendering.md §3.5): count entries (ARGB words), the rest 0. */
vg_lite_error_t vg_lite_set_CLUT(vg_lite_uint32_t count, vg_lite_uint32_t *colors)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!colors || count == 0 || count > 256u)
        return VG_LITE_INVALID_ARGUMENT;
    memset(g_drv.clut, 0, sizeof g_drv.clut);
    memcpy(g_drv.clut, colors, (size_t)count * 4u);
    g_drv.clut_gen++;
    return VG_LITE_SUCCESS;
}

/* v1.6 (rendering.md §5.8): BOTTOM_TOP stores target row y at row H - 1 - y for every later draw. */
vg_lite_error_t vg_lite_set_mirror(vg_lite_orientation_t orientation)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (orientation != VG_LITE_ORIENTATION_TOP_BOTTOM && orientation != VG_LITE_ORIENTATION_BOTTOM_TOP)
        return VG_LITE_INVALID_ARGUMENT;
    g_drv.mirror = orientation == VG_LITE_ORIENTATION_BOTTOM_TOP;
    return VG_LITE_SUCCESS;
}

/* v1.6 (api-subset.md §2): "copy two source images to the target buffer with transformation, blending and
 * filtering" as two blits, source0 with matrix0 then source1 with matrix1, the same blend and filter (assumption,
 * docs/PHASES.md v1.6 question 10). The call has no colour: a MULTIPLY-mode source is multiplied by white. */
vg_lite_error_t vg_lite_blit2(vg_lite_buffer_t *target, vg_lite_buffer_t *source0, vg_lite_buffer_t *source1,
                              vg_lite_matrix_t *matrix0, vg_lite_matrix_t *matrix1, vg_lite_blend_t blend,
                              vg_lite_filter_t filter)
{
    vg_lite_error_t e;
    if (!source0 || !source1)
        return VG_LITE_INVALID_ARGUMENT;
    if ((e = vg_lite_blit(target, source0, matrix0, blend, 0xFFFFFFFFu, filter)) != VG_LITE_SUCCESS)
        return e;
    return vg_lite_blit(target, source1, matrix1, blend, 0xFFFFFFFFu, filter);
}

/* v1.6b (rendering.md §9.10, decision 2026-10-26): the 3 x 3 weights of VG_LITE_FILTER_GAUSSIAN in 1/4096 (s3.12).
 * Only NaN / Inf are rejected; any other kernel is accepted (the hardware saturates the result). A unity-gain kernel
 * (sum within 1e-3 of 1) gets the centre weight implied, so it stays exact. G2D_DRV_STRICT_GAUSSIAN = 1 (build
 * option, for debugging) also rejects negative weights and sums other than 1. */
#ifndef G2D_DRV_STRICT_GAUSSIAN
#define G2D_DRV_STRICT_GAUSSIAN 0
#endif

static int32_t gauss_q(double w)
{
    double r = w * 4096.0;
    r = r < 0 ? -(double)(int64_t)(-r + 0.5) : (double)(int64_t)(r + 0.5);
    return r < -32768.0 ? -32768 : r > 32767.0 ? 32767 : (int32_t)r;
}

static int finite_f(double v)
{
    return v == v && v - v == 0.0;     /* not NaN, not +-Inf */
}

vg_lite_error_t vg_lite_gaussian_filter(vg_lite_float_t w0, vg_lite_float_t w1, vg_lite_float_t w2)
{
    vg_lite_error_t e = drv_check_open();
    double sum;
    int32_t a, b, c;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!finite_f(w0) || !finite_f(w1) || !finite_f(w2))
        return VG_LITE_INVALID_ARGUMENT;
    sum = (double)w0 + 4.0 * (double)w1 + 4.0 * (double)w2;
#if G2D_DRV_STRICT_GAUSSIAN
    if (w0 < 0.0f || w1 < 0.0f || w2 < 0.0f || !(sum >= 1.0 - 1e-3 && sum <= 1.0 + 1e-3))
        return VG_LITE_INVALID_ARGUMENT;
#endif
    a = gauss_q(w1);
    b = gauss_q(w2);
    c = (sum >= 1.0 - 1e-3 && sum <= 1.0 + 1e-3) ? 4096 - 4 * (a + b) : gauss_q(w0);
    if (c < -32768 || c > 32767)
        c = gauss_q(w0);
    g_drv.gauss_w = ((uint32_t)a & 0xFFFFu) | ((uint32_t)b << 16);
    g_drv.gauss_w0 = (uint32_t)c & 0xFFFFu;
    return VG_LITE_SUCCESS;
}
