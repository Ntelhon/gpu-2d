// SPDX-License-Identifier: Apache-2.0
/* G2D vg_lite driver: paths (api-subset.md §5, rendering.md §8-9.2). The driver transforms, flattens and
 * quantizes the path (sw/lib/geom) and hands the hardware a list of line edges per draw. */
#include <stdlib.h>
#include <string.h>

#include "g2d_geom.h"
#include "vg_lite_drv.h"

#define FS(reg, field, v) G2D_FIELD_SET(G2D_ST_##reg##_##field, v)

/* Coordinate elements per opcode (rendering.md §9.2; arcs carry 5, §9.2.1). -1: unknown opcode. */
static int op_args(uint8_t op)
{
    switch (op) {
    case VLC_OP_END: case VLC_OP_CLOSE: case VLC_OP_BREAK:                 return 0;
    case VLC_OP_MOVE: case VLC_OP_MOVE_REL: case VLC_OP_LINE: case VLC_OP_LINE_REL:
    case VLC_OP_SQUAD: case VLC_OP_SQUAD_REL:                              return 2;
    case VLC_OP_QUAD: case VLC_OP_QUAD_REL: case VLC_OP_SCUBIC: case VLC_OP_SCUBIC_REL: return 4;
    case VLC_OP_CUBIC: case VLC_OP_CUBIC_REL:                              return 6;
    case VLC_OP_HLINE: case VLC_OP_HLINE_REL: case VLC_OP_VLINE: case VLC_OP_VLINE_REL: return 1;
    case VLC_OP_SCCWARC: case VLC_OP_SCCWARC_REL: case VLC_OP_SCWARC: case VLC_OP_SCWARC_REL:
    case VLC_OP_LCCWARC: case VLC_OP_LCCWARC_REL: case VLC_OP_LCWARC: case VLC_OP_LCWARC_REL: return 5;
    default:                                                               return -1;
    }
}

static int elem_bytes(vg_lite_format_t f)
{
    switch (f) {
    case VG_LITE_S8:   return 1;
    case VG_LITE_S16:  return 2;
    case VG_LITE_S32:
    case VG_LITE_FP32: return 4;
    default:           return 0;
    }
}

vg_lite_error_t vg_lite_init_path(vg_lite_path_t *path, vg_lite_format_t format, vg_lite_quality_t quality,
                                  vg_lite_uint32_t length, vg_lite_pointer data, vg_lite_float_t min_x,
                                  vg_lite_float_t min_y, vg_lite_float_t max_x, vg_lite_float_t max_y)
{
    if (!path || !elem_bytes(format) || (length && !data))
        return VG_LITE_INVALID_ARGUMENT;
    if (quality != VG_LITE_HIGH && quality != VG_LITE_UPPER && quality != VG_LITE_MEDIUM && quality != VG_LITE_LOW)
        return VG_LITE_INVALID_ARGUMENT;
    memset(path, 0, sizeof *path);
    path->format = format;
    path->quality = quality;
    path->path_length = length;
    path->path = data;
    path->path_changed = 1;
    path->path_type = VG_LITE_DRAW_FILL_PATH;
    path->bounding_box[0] = min_x;
    path->bounding_box[1] = min_y;
    path->bounding_box[2] = max_x;
    path->bounding_box[3] = max_y;
    return VG_LITE_SUCCESS;
}

/* Arcs are accepted by vg_lite_init_path too; this is the same initialisation (assumption A2, api-subset.md §5). */
vg_lite_error_t vg_lite_init_arc_path(vg_lite_path_t *path, vg_lite_format_t format, vg_lite_quality_t quality,
                                      vg_lite_uint32_t length, vg_lite_pointer data, vg_lite_float_t min_x,
                                      vg_lite_float_t min_y, vg_lite_float_t max_x, vg_lite_float_t max_y)
{
    return vg_lite_init_path(path, format, quality, length, data, min_x, min_y, max_x, max_y);
}

/* The uploaded edge cache: header with the matrix it was built for, then the edges. */
typedef struct {
    double m[3][3];
    g2d_rect_t bbox;
    uint32_t count;
    uint32_t pad;
} edge_cache_t;
#define CACHE_HDR ((uint32_t)((sizeof(edge_cache_t) + 63u) & ~63u))

static void drop_cache(vg_lite_path_t *path)
{
    if (path->uploaded.handle) {
        int safe;
        (void)drv_quiesce(&safe);   /* draws in flight may read it (D-2) */
        if (safe)
            g_drv.hal->mem_free(g_drv.hal->ctx, path->uploaded.memory, path->uploaded.address);
        /* else: the GPU could not be confirmed stopped; the edge list is leaked on purpose */
    }
    path->uploaded.handle = NULL;
    path->uploaded.memory = NULL;
    path->uploaded.address = 0;
    path->uploaded.bytes = 0;
}

vg_lite_error_t vg_lite_clear_path(vg_lite_path_t *path)
{
    if (!path)
        return VG_LITE_INVALID_ARGUMENT;
    if (g_drv.open)
        drop_cache(path);
    if (path->stroke)
        free(path->stroke->dash_pattern);   /* the driver's copy of the dash pattern */
    free(path->stroke);                 /* stroke parameters (vg_lite_set_stroke) */
    path->stroke = NULL;
    path->stroke_valid = 0;
    path->uploaded.property = 0;
    path->path = NULL;
    path->path_length = 0;
    return VG_LITE_SUCCESS;
}

/* Keeps the flattened edge list in GPU memory: reused by later draws with the same matrix
 * (api-subset.md §5). The edges are built at the first draw. */
vg_lite_error_t vg_lite_upload_path(vg_lite_path_t *path)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!path)
        return VG_LITE_INVALID_ARGUMENT;
    drop_cache(path);
    path->uploaded.property = 1;
    path->path_changed = 0;
    return VG_LITE_SUCCESS;
}

vg_lite_uint32_t vg_lite_get_path_length(vg_lite_uint8_t *opcode, vg_lite_uint32_t count, vg_lite_format_t format)
{
    uint32_t i, n = 0;
    int eb = elem_bytes(format);
    if (!opcode || !eb)
        return 0;
    for (i = 0; i < count; i++) {
        int a = op_args(opcode[i]);
        if (a < 0)
            return 0;
        n += (uint32_t)(1 + a);
    }
    return n * (uint32_t)eb;
}

/* Writes the opcodes and their coordinates (data: coordinate elements in the path's format, in order) into
 * the path's data buffer, which the caller sized with vg_lite_get_path_length. */
vg_lite_error_t vg_lite_append_path(vg_lite_path_t *path, vg_lite_uint8_t *opcode, vg_lite_pointer data,
                                    vg_lite_uint32_t seg_count)
{
    uint32_t i, need;
    int eb, a;
    uint8_t *dst;
    const uint8_t *src = (const uint8_t *)data;
    if (!path || !opcode || (!data && seg_count) || !path->path)
        return VG_LITE_INVALID_ARGUMENT;
    eb = elem_bytes(path->format);
    need = vg_lite_get_path_length(opcode, seg_count, path->format);
    if (!eb || (seg_count && !need) || need > path->path_length)
        return VG_LITE_INVALID_ARGUMENT;
    dst = (uint8_t *)path->path;
    for (i = 0; i < seg_count; i++) {
        memset(dst, 0, (size_t)eb);
        dst[0] = opcode[i];           /* the opcode is the low byte of its element (little endian) */
        dst += eb;
        a = op_args(opcode[i]);
        memcpy(dst, src, (size_t)a * (size_t)eb);
        dst += (size_t)a * (size_t)eb;
        src += (size_t)a * (size_t)eb;
    }
    path->path_length = need;
    path->path_changed = 1;
    path->append_path_flag = 1;
    drop_cache(path);
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_set_path_type(vg_lite_path_t *path, vg_lite_path_type_t path_type)
{
    if (!path)
        return VG_LITE_INVALID_ARGUMENT;
    if (path_type != VG_LITE_DRAW_FILL_PATH && path_type != VG_LITE_DRAW_ZERO &&
        path_type != VG_LITE_DRAW_STROKE_PATH && path_type != VG_LITE_DRAW_FILL_STROKE_PATH)
        return VG_LITE_INVALID_ARGUMENT;
    path->path_type = path_type;
    return VG_LITE_SUCCESS;
}

/* Stroke parameters (rendering.md §9.7): butt / round / square caps, miter / round / bevel joins, miter limit, dash
 * pattern and phase (§9.7.1; the driver keeps its own copy of the pattern; path->stroke->dash_reset, 0 after the
 * first vg_lite_set_stroke, may be set by the caller and is kept by later calls). The outline is built at draw time
 * for the draw's matrix (vg_lite_update_stroke only marks the parameters as applied). */
vg_lite_error_t vg_lite_set_stroke(vg_lite_path_t *path, vg_lite_cap_style_t cap_style, vg_lite_join_style_t join_style,
                                   vg_lite_float_t line_width, vg_lite_float_t miter_limit, vg_lite_float_t *dash_pattern,
                                   vg_lite_uint32_t pattern_count, vg_lite_float_t dash_phase, vg_lite_color_t color)
{
    vg_lite_float_t *pat = NULL, len = 0.0f;
    uint32_t i;
    if (!path || !(line_width > 0.0f) || (pattern_count && !dash_pattern))
        return VG_LITE_INVALID_ARGUMENT;
    if (cap_style != VG_LITE_CAP_BUTT && cap_style != VG_LITE_CAP_ROUND && cap_style != VG_LITE_CAP_SQUARE)
        return VG_LITE_INVALID_ARGUMENT;
    if (join_style != VG_LITE_JOIN_MITER && join_style != VG_LITE_JOIN_ROUND && join_style != VG_LITE_JOIN_BEVEL)
        return VG_LITE_INVALID_ARGUMENT;
    if (pattern_count) {
        if (!(pat = (vg_lite_float_t *)malloc(pattern_count * sizeof *pat)))
            return VG_LITE_OUT_OF_RESOURCES;
        for (i = 0; i < pattern_count; i++) {
            pat[i] = dash_pattern[i];
            len += dash_pattern[i] > 0.0f ? dash_pattern[i] : 0.0f;
        }
    }
    if (!path->stroke && !(path->stroke = (vg_lite_stroke_t *)calloc(1, sizeof *path->stroke))) {
        free(pat);
        return VG_LITE_OUT_OF_RESOURCES;
    }
    free(path->stroke->dash_pattern);
    path->stroke->dash_pattern = pat;
    path->stroke->pattern_count = pattern_count;
    path->stroke->dash_phase = dash_phase;
    path->stroke->pattern_length = len;
    path->stroke->cap_style = cap_style;
    path->stroke->join_style = join_style;
    path->stroke->line_width = line_width;
    path->stroke->half_width = line_width / 2.0f;
    path->stroke->miter_limit = miter_limit < 1.0f ? 1.0f : miter_limit;
    path->stroke_color = color;
    path->stroke_valid = 0;
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_update_stroke(vg_lite_path_t *path)
{
    if (!path)
        return VG_LITE_INVALID_ARGUMENT;
    if (!path->stroke)
        return VG_LITE_INVALID_ARGUMENT;   /* no vg_lite_set_stroke before */
    path->stroke_valid = 1;
    return VG_LITE_SUCCESS;
}

/* ------------------------------------------------------------------------------------------------ */
/* Edge generation                                                                                  */
/* ------------------------------------------------------------------------------------------------ */

typedef struct {
    int32_t *out;      /* NULL: count only */
    uint32_t n;
} sink_t;

static int sink(void *ctx, int32_t x0, int32_t y0, int32_t x1, int32_t y1)
{
    sink_t *s = (sink_t *)ctx;
    if (s->out) {
        int32_t *e = s->out + 4u * s->n;
        e[0] = x0; e[1] = y0; e[2] = x1; e[3] = y1;
    }
    s->n++;
    return s->n >= (1u << 24) ? 1 : 0;   /* EDGE_COUNT is 24 bits */
}

/* Stroke outline edges of path under m (rendering.md §9.7) into scratch memory. */
static vg_lite_error_t stroke_edges(vg_lite_path_t *path, double m[3][3], uint32_t *addr, uint32_t *count,
                                    g2d_rect_t *bbox)
{
    sink_t s = {NULL, 0};
    g2d_stroke_t st;
    double *dash = NULL;
    void *cpu;
    uint32_t gpu;
    vg_lite_error_t e = VG_LITE_SUCCESS;
    int rc;
    st.width = path->stroke->line_width;
    st.miter_limit = path->stroke->miter_limit;
    st.cap = path->stroke->cap_style == VG_LITE_CAP_ROUND ? G2D_CAP_ROUND :
             path->stroke->cap_style == VG_LITE_CAP_SQUARE ? G2D_CAP_SQUARE : G2D_CAP_BUTT;
    st.join = path->stroke->join_style == VG_LITE_JOIN_ROUND ? G2D_JOIN_ROUND :
              path->stroke->join_style == VG_LITE_JOIN_BEVEL ? G2D_JOIN_BEVEL : G2D_JOIN_MITER;
    st.dash = NULL;
    st.dash_count = 0;
    st.dash_phase = path->stroke->dash_phase;
    st.dash_reset = path->stroke->dash_reset != 0;
    if (path->stroke->pattern_count >= 2) {   /* float -> double, exactly (rendering.md §9.1) */
        uint32_t i;
        if (!(dash = (double *)malloc(path->stroke->pattern_count * sizeof *dash)))
            return VG_LITE_OUT_OF_RESOURCES;
        for (i = 0; i < path->stroke->pattern_count; i++)
            dash[i] = path->stroke->dash_pattern[i];
        st.dash = dash;
        st.dash_count = path->stroke->pattern_count;
    }
    rc = g2d_geom_stroke_edges(path->path, path->path_length, (int)path->format, DRV_CM(m), &st, sink, &s, bbox);
    *count = 0;
    if (rc == G2D_GEOM_EMPTY || (rc == G2D_GEOM_OK && s.n == 0))
        goto done;
    if (rc != G2D_GEOM_OK) {
        e = drv_geom_err(rc);
        goto done;
    }
    if ((e = drv_scratch(16u * s.n, &cpu, &gpu)) != VG_LITE_SUCCESS)
        goto done;
    *count = s.n;
    s.out = (int32_t *)cpu;
    s.n = 0;
    (void)g2d_geom_stroke_edges(path->path, path->path_length, (int)path->format, DRV_CM(m), &st, sink, &s, bbox);
    *addr = gpu;
done:
    free(dash);
    return e;
}

/* Edges of path under m in GPU memory: from the upload cache, or built into scratch memory of the current
 * command buffer. count 0: nothing to draw. */
static vg_lite_error_t path_edges(vg_lite_path_t *path, double m[3][3], uint32_t *addr, uint32_t *count,
                                  g2d_rect_t *bbox)
{
    sink_t s = {NULL, 0};
    int rc;
    void *cpu;
    uint32_t gpu;
    vg_lite_error_t e;
    if (path->uploaded.property && path->uploaded.handle) {
        const edge_cache_t *c = (const edge_cache_t *)path->uploaded.memory;
        if (!path->path_changed && memcmp(c->m, m, sizeof c->m) == 0) {
            *addr = path->uploaded.address + CACHE_HDR;
            *count = c->count;
            *bbox = c->bbox;
            return VG_LITE_SUCCESS;
        }
        drop_cache(path);
    }
    /* pass 1: count; pass 2: write (deterministic, same edges twice) */
    rc = g2d_geom_path_edges(path->path, path->path_length, (int)path->format, DRV_CM(m), sink, &s, bbox);
    if (rc == G2D_GEOM_EMPTY || (rc == G2D_GEOM_OK && s.n == 0)) {
        *count = 0;
        return VG_LITE_SUCCESS;
    }
    if (rc != G2D_GEOM_OK)
        return drv_geom_err(rc);
    *count = s.n;
    if (path->uploaded.property) {
        edge_cache_t *c;
        uint32_t bytes = CACHE_HDR + 16u * s.n;
        if (g_drv.hal->mem_alloc(g_drv.hal->ctx, bytes, 64, &cpu, &gpu))
            return VG_LITE_OUT_OF_MEMORY;
        c = (edge_cache_t *)cpu;
        memcpy(c->m, m, sizeof c->m);
        c->count = s.n;
        path->uploaded.handle = cpu;
        path->uploaded.memory = cpu;
        path->uploaded.address = gpu;
        path->uploaded.bytes = bytes;
        path->path_changed = 0;
        s.out = (int32_t *)((uint8_t *)cpu + CACHE_HDR);
        s.n = 0;
        (void)g2d_geom_path_edges(path->path, path->path_length, (int)path->format, DRV_CM(m), sink, &s, bbox);
        c->bbox = *bbox;
        g_drv.hal->cache_clean(g_drv.hal->ctx, cpu, bytes);
        *addr = gpu + CACHE_HDR;
        return VG_LITE_SUCCESS;
    }
    if ((e = drv_scratch(16u * s.n, &cpu, &gpu)) != VG_LITE_SUCCESS)
        return e;
    s.out = (int32_t *)cpu;
    s.n = 0;
    (void)g2d_geom_path_edges(path->path, path->path_length, (int)path->format, DRV_CM(m), sink, &s, bbox);
    *addr = gpu;
    return VG_LITE_SUCCESS;
}

/* Common path draw: the caller has requested the paint state (BLEND / ALPHA / PAINT_* and, for gradients,
 * GRAD_*); this adds target, rasterizer state and the DRAW_PATH. stroke: draw the stroke outline (NON_ZERO). */
static vg_lite_error_t draw_path_part(vg_lite_buffer_t *target, vg_lite_path_t *path, vg_lite_fill_t fill_rule,
                                      vg_lite_matrix_t *matrix, int stroke, const vg_lite_buffer_t *image);

vg_lite_error_t drv_draw_path(vg_lite_buffer_t *target, vg_lite_path_t *path, vg_lite_fill_t fill_rule,
                              vg_lite_matrix_t *matrix)
{
    if (path && (path->path_type == VG_LITE_DRAW_STROKE_PATH || path->path_type == VG_LITE_DRAW_FILL_STROKE_PATH))
        return VG_LITE_NOT_SUPPORT;   /* gradient strokes: the callers below handle solid strokes */
    return draw_path_part(target, path, fill_rule, matrix, 0, NULL);
}

vg_lite_error_t drv_draw_path_image(vg_lite_buffer_t *target, vg_lite_path_t *path, vg_lite_fill_t fill_rule,
                                    vg_lite_matrix_t *matrix, const vg_lite_buffer_t *image)
{
    return draw_path_part(target, path, fill_rule, matrix, 0, image);
}

static vg_lite_error_t draw_path_part(vg_lite_buffer_t *target, vg_lite_path_t *path, vg_lite_fill_t fill_rule,
                                      vg_lite_matrix_t *matrix, int stroke, const vg_lite_buffer_t *image)
{
    vg_lite_error_t e;
    double m[3][3];
    uint32_t addr = 0, count = 0;
    g2d_rect_t bb;
    int aa;
    if ((e = drv_check_target(target)) != VG_LITE_SUCCESS)
        return e;
    if (!path || (path->path_length && !path->path))
        return VG_LITE_INVALID_ARGUMENT;
    if (fill_rule != VG_LITE_FILL_NON_ZERO && fill_rule != VG_LITE_FILL_EVEN_ODD)
        return VG_LITE_INVALID_ARGUMENT;
    if (stroke) {
        if (!path->stroke || !path->stroke_valid)
            return VG_LITE_INVALID_ARGUMENT;   /* vg_lite_set_stroke + vg_lite_update_stroke first */
        fill_rule = VG_LITE_FILL_NON_ZERO;     /* the outline is a union of same-oriented polygons */
    }
    drv_matrix_d(matrix, m);                  /* affine or projective (rendering.md §9.8) */
    aa = path->quality != VG_LITE_LOW;
    /* command space first: scratch edges must belong to the buffer that holds the draw */
    if ((e = drv_reserve(drv_draw_words(5))) != VG_LITE_SUCCESS)
        return e;
    if (image && (e = drv_state_clut(image)) != VG_LITE_SUCCESS)
        return e;
    if ((e = (stroke ? stroke_edges(path, m, &addr, &count, &bb) : path_edges(path, m, &addr, &count, &bb))) !=
        VG_LITE_SUCCESS)
        return e;
    if (count == 0)
        return VG_LITE_SUCCESS;
    drv_state_target(target);
    drv_state(G2D_ST_RAST_CTRL, FS(RAST_CTRL, FILL_RULE, fill_rule == VG_LITE_FILL_EVEN_ODD) | FS(RAST_CTRL, AA, aa));
    {
        uint32_t w[5];
        w[0] = G2D_OP_DRAW_PATH << 24;
        w[1] = ((uint32_t)(uint16_t)bb.x0) | ((uint32_t)(uint16_t)bb.y0 << 16);
        w[2] = ((uint32_t)(uint16_t)bb.x1) | ((uint32_t)(uint16_t)bb.y1 << 16);
        w[3] = addr;
        w[4] = count;
        return drv_emit_draw(w, 5);
    }
}

vg_lite_error_t vg_lite_draw(vg_lite_buffer_t *target, vg_lite_path_t *path, vg_lite_fill_t fill_rule,
                             vg_lite_matrix_t *matrix, vg_lite_blend_t blend, vg_lite_color_t color)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!path)
        return VG_LITE_INVALID_ARGUMENT;
    /* FILL / ZERO: fill with color; STROKE: the outline with path->stroke_color; FILL_STROKE: both, fill first */
    if (path->path_type != VG_LITE_DRAW_STROKE_PATH) {
        if ((e = drv_state_paint(blend, FS(PAINT_CTRL, SRC, G2D_PAINT_SOLID), drv_argb(color))) != VG_LITE_SUCCESS ||
            (e = draw_path_part(target, path, fill_rule, matrix, 0, NULL)) != VG_LITE_SUCCESS)
            return e;
        if (path->path_type != VG_LITE_DRAW_FILL_STROKE_PATH)
            return VG_LITE_SUCCESS;
    }
    if ((e = drv_state_paint(blend, FS(PAINT_CTRL, SRC, G2D_PAINT_SOLID), drv_argb(path->stroke_color))) != VG_LITE_SUCCESS)
        return e;
    return draw_path_part(target, path, fill_rule, matrix, 1, NULL);
}
