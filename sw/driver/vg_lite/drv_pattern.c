// SPDX-License-Identifier: Apache-2.0
/* G2D vg_lite driver: vg_lite_draw_pattern (rendering.md §9.5, OPEN_QUESTIONS Q-API-12).
 *
 * v1.5 (F-1): the image is the paint of a DRAW_PATH over the path's bounding box, for any path and any path
 * matrix (affine or projective, §9.8); pixel coverage = path coverage where the pixel passes the source test.
 * A path whose coverage is exactly a pixel rectangle (two opposite vertical edges with the same Y span, at
 * whole pixels, or anywhere without anti-aliasing) gives the same pixels as an image draw clipped to that
 * rectangle, so it stays one blit (no rasterizer pass). The pattern modes are the hardware wrap modes
 * (rendering.md §7.5): PAINT_CTRL.WRAP and BORDER_COLOR = the pattern colour (COLOR: a texel by default, a
 * plain colour after vg_lite_g2d_set_pattern_color_mode(SOLID), PAINT_CTRL.BORDER_SOLID).
 */
#include <string.h>

#include "vg_lite_drv.h"

#define FS(reg, field, v) G2D_FIELD_SET(G2D_ST_##reg##_##field, v)

/* ------------------------------------------------------------------------------------------------ */
/* Path -> pixel rectangle                                                                          */
/* ------------------------------------------------------------------------------------------------ */

typedef struct {
    int32_t e[3][4];
    uint32_t n;
} rect_sink_t;

static int rect_sink(void *ctx, int32_t x0, int32_t y0, int32_t x1, int32_t y1)
{
    rect_sink_t *s = (rect_sink_t *)ctx;
    if (s->n < 3) {                   /* more than two edges: not a rectangle; counted on for the bbox */
        s->e[s->n][0] = x0; s->e[s->n][1] = y0; s->e[s->n][2] = x1; s->e[s->n][3] = y1;
    }
    s->n++;
    return 0;
}

static int32_t ceil_div32(int64_t v) { return (int32_t)(v >= 0 ? (v + 31) / 32 : -((-v) / 32)); }

/* Pixels whose samples lie inside the edge pair (rendering.md §8.2-8.3): sample X (1/32 px) is inside iff
 * Xl <= X < Xr, and likewise for Y. Without AA the one sample is at 32p + 16; with AA the 16 samples of a
 * pixel are all inside or all outside only when the edges are at whole pixels, where the same formula
 * holds. Returns 1 when the coverage is exactly that pixel rectangle (possibly empty), 0 otherwise (also for
 * fractional borders with AA: their border pixels have partial coverage, drawn exactly by the path). */
static int path_rect(const rect_sink_t *s, int aa, g2d_rect_t *r)
{
    const int32_t *a = s->e[0], *b = s->e[1];
    int64_t xl, xr, ya, yb;
    if (s->n != 2 || a[0] != a[2] || b[0] != b[2] || a[0] == b[0])
        return 0;
    if ((a[1] < a[3]) == (b[1] < b[3]))           /* same direction: winding 2 to the right of both */
        return 0;
    ya = a[1] < a[3] ? a[1] : a[3];
    yb = a[1] < a[3] ? a[3] : a[1];
    if (ya != (b[1] < b[3] ? b[1] : b[3]) || yb != (b[1] < b[3] ? b[3] : b[1]))
        return 0;
    xl = a[0] < b[0] ? a[0] : b[0];
    xr = a[0] < b[0] ? b[0] : a[0];
    if (aa && ((xl | xr | ya | yb) & 15))
        return 0;
    r->x0 = ceil_div32(2 * xl - 16);
    r->x1 = ceil_div32(2 * xr - 16);
    r->y0 = ceil_div32(2 * ya - 16);
    r->y1 = ceil_div32(2 * yb - 16);
    return 1;
}

static void intersect(g2d_rect_t *r, const g2d_rect_t *c)
{
    if (r->x0 < c->x0) r->x0 = c->x0;
    if (r->y0 < c->y0) r->y0 = c->y0;
    if (r->x1 > c->x1) r->x1 = c->x1;
    if (r->y1 > c->y1) r->y1 = c->y1;
}

static int empty(const g2d_rect_t *r) { return r->x1 <= r->x0 || r->y1 <= r->y0; }

static uint32_t wrap_code(vg_lite_pattern_mode_t m)
{
    switch (m) {
    case VG_LITE_PATTERN_COLOR:  return G2D_WRAP_COLOR;
    case VG_LITE_PATTERN_PAD:    return G2D_WRAP_PAD;
    case VG_LITE_PATTERN_REPEAT: return G2D_WRAP_REPEAT;
    default:                     return G2D_WRAP_REFLECT;
    }
}

vg_lite_error_t vg_lite_draw_pattern(vg_lite_buffer_t *target, vg_lite_path_t *path, vg_lite_fill_t fill_rule,
                                     vg_lite_matrix_t *path_matrix, vg_lite_buffer_t *pattern_image,
                                     vg_lite_matrix_t *pattern_matrix, vg_lite_blend_t blend,
                                     vg_lite_pattern_mode_t pattern_mode, vg_lite_color_t pattern_color,
                                     vg_lite_color_t color, vg_lite_filter_t filter)
{
    vg_lite_error_t e = drv_check_open();
    double pm[3][3], m[3][3];
    rect_sink_t rs;
    g2d_rect_t R, clip, img;
    g2d_blit_setup_t bs;
    uint32_t paint;
    int rc, aa, is_rect;
    if (e != VG_LITE_SUCCESS)
        return e;
    if ((e = drv_check_target(target)) != VG_LITE_SUCCESS || !pattern_image ||
        (e = drv_check_buffer(pattern_image)) != VG_LITE_SUCCESS)
        return e != VG_LITE_SUCCESS ? e : VG_LITE_INVALID_ARGUMENT;
    if (!path || (path->path_length && !path->path))
        return VG_LITE_INVALID_ARGUMENT;
    if (fill_rule != VG_LITE_FILL_NON_ZERO && fill_rule != VG_LITE_FILL_EVEN_ODD)
        return VG_LITE_INVALID_ARGUMENT;
    if (pattern_mode != VG_LITE_PATTERN_COLOR && pattern_mode != VG_LITE_PATTERN_PAD &&
        pattern_mode != VG_LITE_PATTERN_REPEAT && pattern_mode != VG_LITE_PATTERN_REFLECT)
        return VG_LITE_INVALID_ARGUMENT;
    if (path->path_type != VG_LITE_DRAW_FILL_PATH && path->path_type != VG_LITE_DRAW_ZERO)
        return VG_LITE_NOT_SUPPORT;
    if ((e = drv_image_paint(pattern_image, filter, blend, 0, &paint)) != VG_LITE_SUCCESS)
        return e;
    if (pattern_image->image_mode == VG_LITE_NONE_IMAGE_MODE)
        return VG_LITE_SUCCESS;           /* v1.6: nothing is drawn */
    drv_matrix_d(path_matrix, m);             /* affine or projective (rendering.md §9.8) */
    aa = path->quality != VG_LITE_LOW;

    /* one geometry pass: is the coverage a pixel rectangle, and the bbox of the path */
    memset(&rs, 0, sizeof rs);
    rc = g2d_geom_path_edges(path->path, path->path_length, (int)path->format, DRV_CM(m), rect_sink, &rs, &R);
    if (rc == G2D_GEOM_EMPTY || (rc == G2D_GEOM_OK && rs.n == 0))
        return VG_LITE_SUCCESS;
    if (rc != G2D_GEOM_OK)
        return drv_geom_err(rc);
    is_rect = path_rect(&rs, aa, &R);         /* R: that rectangle; otherwise the path's bbox */
    drv_clip_g2d(target, &clip);
    intersect(&R, &clip);
    if (empty(&R))
        return VG_LITE_SUCCESS;

    img.x0 = 0; img.y0 = 0; img.x1 = pattern_image->width; img.y1 = pattern_image->height;
    drv_matrix_d(pattern_matrix, pm);
    /* the whole rectangle / bbox is drawn: the wrap mode decides every pixel the image does not cover */
    rc = g2d_geom_blit_setup_full(DRV_CM(pm), &img, &R, &bs);
    if (rc != G2D_GEOM_OK)
        return drv_geom_err(rc);
    paint |= FS(PAINT_CTRL, WRAP, wrap_code(pattern_mode)) | (bs.persp ? FS(PAINT_CTRL, PERSP, 1) : 0u) |
             FS(PAINT_CTRL, BORDER_SOLID, (pattern_mode == VG_LITE_PATTERN_COLOR && g_drv.pattern_solid) ? 1 : 0);
    if (is_rect)
        return drv_emit_blit(target, pattern_image, &img, bs.xf, &bs.draw, blend, color, paint, 0,
                             drv_argb(pattern_color));
    /* image paint on the path (F-1): the path's own edges and bbox, coverage x sampled image */
    if ((e = drv_state_image(target, pattern_image, &img, bs.xf, blend, color, paint, 0, drv_argb(pattern_color))) !=
        VG_LITE_SUCCESS)
        return e;
    return drv_draw_path_image(target, path, fill_rule, path_matrix, pattern_image);
}

vg_lite_error_t vg_lite_g2d_set_pattern_color_mode(vg_lite_g2d_pattern_color_mode_t mode)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (mode != VG_LITE_G2D_PATTERN_COLOR_TEXEL && mode != VG_LITE_G2D_PATTERN_COLOR_SOLID)
        return VG_LITE_INVALID_ARGUMENT;
    g_drv.pattern_solid = mode == VG_LITE_G2D_PATTERN_COLOR_SOLID;
    return VG_LITE_SUCCESS;
}
