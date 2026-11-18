// SPDX-License-Identifier: Apache-2.0
/* G2D vg_lite driver: mask layers (v1.6, rendering.md §6.1 hardware, §9.9 driver; OpenVG 1.1 §7.2 operations).
 *
 * A layer is an A8 buffer. Operations on layers are ordinary draws into it, with the scissor and the active mask
 * suspended: CLEAR / FILL = clear to 0 / 255, SET = blit NONE, UNION = SCREEN, INTERSECT = DST_IN, SUBTRACT =
 * SUBTRACT (A8 texels are premultiplied white, so the blends act on the alpha values). vg_lite_render_masklayer
 * uses the path's coverage x the colour's alpha as the source: UNION / SUBTRACT draw the path into the layer (the
 * source is 0 outside the path, which leaves the layer unchanged); SET / INTERSECT, which also change pixels outside
 * the path, render it into a zeroed temporary layer and blend that over the whole layer. Masked draws: the layer
 * set by vg_lite_set_masklayer goes into MASK_* with every draw while vg_lite_enable_masklayer is on
 * (drv_state_target).
 */
#include <string.h>

#include "vg_lite_drv.h"

typedef struct {
    int scissor_on, nsr;
} drv_saved_t;

/* Mask operations draw unclipped and unmasked; the API state is restored afterwards. */
static void suspend(drv_saved_t *s)
{
    s->scissor_on = g_drv.scissor_on;
    s->nsr = g_drv.nsr;
    g_drv.scissor_on = 0;
    g_drv.nsr = 0;
    g_drv.mask_suspend++;
}

static void resume(const drv_saved_t *s)
{
    g_drv.scissor_on = s->scissor_on;
    g_drv.nsr = s->nsr;
    g_drv.mask_suspend--;
}

static int is_layer(const vg_lite_buffer_t *b)
{
    return b && b->handle && b->format == VG_LITE_A8;
}

vg_lite_error_t vg_lite_create_masklayer(vg_lite_buffer_t *masklayer, vg_lite_uint32_t width, vg_lite_uint32_t height)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!drv_has(DRV_FEAT_MASK))
        return VG_LITE_NOT_SUPPORT;      /* v1.7: mask layer not built */
    if (!masklayer || width < 1 || height < 1 || width > 8192 || height > 8192)
        return VG_LITE_INVALID_ARGUMENT;
    memset(masklayer, 0, sizeof *masklayer);
    masklayer->width = (vg_lite_int32_t)width;
    masklayer->height = (vg_lite_int32_t)height;
    masklayer->format = VG_LITE_A8;
    if ((e = vg_lite_allocate(masklayer)) != VG_LITE_SUCCESS)
        return e;
    return vg_lite_fill_masklayer(masklayer, NULL, 255);   /* "default pixel value 255" */
}

vg_lite_error_t vg_lite_fill_masklayer(vg_lite_buffer_t *masklayer, vg_lite_rectangle_t *rect, vg_lite_uint8_t value)
{
    vg_lite_error_t e = drv_check_open();
    drv_saved_t sv;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!is_layer(masklayer))
        return VG_LITE_INVALID_ARGUMENT;
    suspend(&sv);
    e = vg_lite_clear(masklayer, rect, (vg_lite_color_t)value << 24);    /* A8 target: the alpha byte */
    resume(&sv);
    return e;
}

static vg_lite_blend_t op_blend(vg_lite_mask_operation_t op)
{
    switch (op) {
    case VG_LITE_SET_MASK:       return VG_LITE_BLEND_NONE;
    case VG_LITE_UNION_MASK:     return VG_LITE_BLEND_SCREEN;
    case VG_LITE_INTERSECT_MASK: return VG_LITE_BLEND_DST_IN;
    default:                     return VG_LITE_BLEND_SUBTRACT;
    }
}

static int op_valid(vg_lite_mask_operation_t op)
{
    return op >= VG_LITE_CLEAR_MASK && op <= VG_LITE_SUBTRACT_MASK;
}

/* src blended into dst over r (the same position in both), mask and scissor already suspended */
static vg_lite_error_t blend_layer(vg_lite_buffer_t *dst, vg_lite_buffer_t *src, vg_lite_mask_operation_t op,
                                   const g2d_rect_t *r)
{
    double m[3][3] = {{1.0, 0.0, 0.0}, {0.0, 1.0, 0.0}, {0.0, 0.0, 1.0}};
    g2d_rect_t srect = *r, clip;
    g2d_blit_setup_t bs;
    uint32_t paint;
    vg_lite_error_t e;
    int rc;
    if (op == VG_LITE_CLEAR_MASK || op == VG_LITE_FILL_MASK) {
        vg_lite_rectangle_t rr;
        rr.x = r->x0; rr.y = r->y0; rr.width = r->x1 - r->x0; rr.height = r->y1 - r->y0;
        return vg_lite_clear(dst, &rr, op == VG_LITE_FILL_MASK ? 0xFF000000u : 0u);
    }
    if ((e = drv_image_paint(src, VG_LITE_FILTER_POINT, op_blend(op), 0, &paint)) != VG_LITE_SUCCESS)
        return e;
    drv_clip_g2d(dst, &clip);
    rc = g2d_geom_blit_setup(DRV_CM(m), &srect, &clip, &bs);
    if (rc != G2D_GEOM_OK)
        return drv_geom_err(rc);
    return drv_emit_blit(dst, src, &srect, bs.xf, &bs.draw, op_blend(op), 0, paint, 0, 0);
}

/* r = rect (NULL: the whole layer) clipped to both layers; 0 if empty */
static int layer_rect(const vg_lite_buffer_t *a, const vg_lite_buffer_t *b, const vg_lite_rectangle_t *rect,
                      g2d_rect_t *r)
{
    r->x0 = rect ? rect->x : 0;
    r->y0 = rect ? rect->y : 0;
    r->x1 = rect ? rect->x + rect->width : a->width;
    r->y1 = rect ? rect->y + rect->height : a->height;
    if (r->x0 < 0) r->x0 = 0;
    if (r->y0 < 0) r->y0 = 0;
    if (r->x1 > a->width) r->x1 = a->width;
    if (r->y1 > a->height) r->y1 = a->height;
    if (b && r->x1 > b->width) r->x1 = b->width;
    if (b && r->y1 > b->height) r->y1 = b->height;
    return r->x1 > r->x0 && r->y1 > r->y0;
}

vg_lite_error_t vg_lite_blend_masklayer(vg_lite_buffer_t *dst, vg_lite_buffer_t *src, vg_lite_mask_operation_t operation,
                                        vg_lite_rectangle_t *rect)
{
    vg_lite_error_t e = drv_check_open();
    drv_saved_t sv;
    g2d_rect_t r;
    vg_lite_buffer_t tmp;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!is_layer(dst) || !op_valid(operation) ||
        (operation != VG_LITE_CLEAR_MASK && operation != VG_LITE_FILL_MASK && !is_layer(src)))
        return VG_LITE_INVALID_ARGUMENT;
    if (rect && (rect->width < 0 || rect->height < 0))
        return VG_LITE_INVALID_ARGUMENT;
    if (!layer_rect(dst, (operation == VG_LITE_CLEAR_MASK || operation == VG_LITE_FILL_MASK) ? NULL : src, rect, &r))
        return VG_LITE_SUCCESS;
    suspend(&sv);
    if (src == dst && operation != VG_LITE_CLEAR_MASK && operation != VG_LITE_FILL_MASK) {
        /* a layer with itself: through a copy (a draw must not read what it writes, rendering.md §13.3) */
        memset(&tmp, 0, sizeof tmp);
        tmp.width = dst->width; tmp.height = dst->height; tmp.format = VG_LITE_A8;
        if ((e = vg_lite_allocate(&tmp)) == VG_LITE_SUCCESS) {
            if ((e = blend_layer(&tmp, src, VG_LITE_SET_MASK, &r)) == VG_LITE_SUCCESS)
                e = blend_layer(dst, &tmp, operation, &r);
            {
                vg_lite_error_t ef = vg_lite_free(&tmp);
                if (e == VG_LITE_SUCCESS)
                    e = ef;
            }
        }
    } else {
        e = blend_layer(dst, src, operation, &r);
    }
    resume(&sv);
    return e;
}

vg_lite_error_t vg_lite_render_masklayer(vg_lite_buffer_t *masklayer, vg_lite_mask_operation_t operation,
                                         vg_lite_path_t *path, vg_lite_fill_t fill_rule, vg_lite_color_t color,
                                         vg_lite_matrix_t *matrix)
{
    vg_lite_error_t e = drv_check_open();
    drv_saved_t sv;
    g2d_rect_t whole;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!is_layer(masklayer) || !op_valid(operation) || !path)
        return VG_LITE_INVALID_ARGUMENT;
    if (fill_rule != VG_LITE_FILL_NON_ZERO && fill_rule != VG_LITE_FILL_EVEN_ODD)
        return VG_LITE_INVALID_ARGUMENT;
    whole.x0 = 0; whole.y0 = 0; whole.x1 = masklayer->width; whole.y1 = masklayer->height;
    suspend(&sv);
    if (operation == VG_LITE_CLEAR_MASK || operation == VG_LITE_FILL_MASK) {
        e = blend_layer(masklayer, NULL, operation, &whole);
    } else if (operation == VG_LITE_UNION_MASK || operation == VG_LITE_SUBTRACT_MASK) {
        /* outside the path the source is 0: UNION / SUBTRACT leave the layer unchanged there */
        if ((e = drv_state_paint(op_blend(operation), G2D_FIELD_SET(G2D_ST_PAINT_CTRL_SRC, G2D_PAINT_SOLID),
                                 drv_argb(color & 0xFF000000u))) == VG_LITE_SUCCESS)
            e = drv_draw_path(masklayer, path, fill_rule, matrix);
    } else {
        /* SET / INTERSECT: the source over the whole layer (0 outside the path) through a zeroed temporary layer */
        vg_lite_buffer_t tmp;
        memset(&tmp, 0, sizeof tmp);
        tmp.width = masklayer->width; tmp.height = masklayer->height; tmp.format = VG_LITE_A8;
        if ((e = vg_lite_allocate(&tmp)) == VG_LITE_SUCCESS) {
            if ((e = vg_lite_clear(&tmp, NULL, 0)) == VG_LITE_SUCCESS &&
                (e = drv_state_paint(VG_LITE_BLEND_NONE, G2D_FIELD_SET(G2D_ST_PAINT_CTRL_SRC, G2D_PAINT_SOLID),
                                     drv_argb(color & 0xFF000000u))) == VG_LITE_SUCCESS &&
                (e = drv_draw_path(&tmp, path, fill_rule, matrix)) == VG_LITE_SUCCESS)
                e = blend_layer(masklayer, &tmp, operation, &whole);
            {
                vg_lite_error_t ef = vg_lite_free(&tmp);
                if (e == VG_LITE_SUCCESS)
                    e = ef;
            }
        }
    }
    resume(&sv);
    return e;
}

vg_lite_error_t vg_lite_set_masklayer(vg_lite_buffer_t *masklayer, vg_lite_int32_t x, vg_lite_int32_t y)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!is_layer(masklayer) || x < -32768 || x > 32767 || y < -32768 || y > 32767)
        return VG_LITE_INVALID_ARGUMENT;
    g_drv.mask_addr = masklayer->address;
    g_drv.mask_stride = (uint32_t)masklayer->stride;
    g_drv.mask_w = (uint32_t)masklayer->width;
    g_drv.mask_h = (uint32_t)masklayer->height;
    g_drv.mask_x = x;
    g_drv.mask_y = y;
    g_drv.mask_set = 1;
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_enable_masklayer(vg_lite_void)
{
    vg_lite_error_t e = drv_check_open();
    if (e == VG_LITE_SUCCESS && !drv_has(DRV_FEAT_MASK))
        return VG_LITE_NOT_SUPPORT;      /* v1.7: mask layer not built */
    if (e == VG_LITE_SUCCESS)
        g_drv.mask_on = 1;
    return e;
}

vg_lite_error_t vg_lite_disable_masklayer(vg_lite_void)
{
    vg_lite_error_t e = drv_check_open();
    if (e == VG_LITE_SUCCESS)
        g_drv.mask_on = 0;
    return e;
}

vg_lite_error_t vg_lite_destroy_masklayer(vg_lite_buffer_t *masklayer)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!is_layer(masklayer))
        return VG_LITE_INVALID_ARGUMENT;
    if (g_drv.mask_set && g_drv.mask_addr == masklayer->address) {   /* "free a masklayer and disable mask operation" */
        g_drv.mask_set = 0;
        g_drv.mask_on = 0;
    }
    return vg_lite_free(masklayer);
}
