// SPDX-License-Identifier: Apache-2.0
/* vg_lite driver unit tests on the software backend (golden model). Exact expectations only: every
 * result is derived by hand from spec/rendering.md (formats §3, clear §11, clipping §6, path coverage of
 * pixel-aligned shapes §8, ramp §10). API scenes against the independent reference: sw/tests/test_scenes.py. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "g2d_geom.h"
#include "vg_lite.h"

static int g_fail = 0, g_checks = 0;

#define CHECK(cond, ...)                                    \
    do {                                                    \
        g_checks++;                                         \
        if (!(cond)) {                                      \
            g_fail++;                                       \
            printf("FAIL %s:%d: ", __FILE__, __LINE__);     \
            printf(__VA_ARGS__);                            \
            printf("\n");                                   \
        }                                                   \
    } while (0)

#define OK(call) CHECK((call) == VG_LITE_SUCCESS, "%s", #call)

static vg_lite_buffer_t make(int w, int h, vg_lite_buffer_format_t f)
{
    vg_lite_buffer_t b;
    memset(&b, 0, sizeof b);
    b.width = w;
    b.height = h;
    b.format = f;
    OK(vg_lite_allocate(&b));
    return b;
}

static uint8_t *px(vg_lite_buffer_t *b, int x, int y, int bpp)
{
    return (uint8_t *)b->memory + (size_t)y * (size_t)b->stride + (size_t)x * (size_t)bpp;
}

static uint32_t rd32(vg_lite_buffer_t *b, int x, int y)
{
    uint8_t *p = px(b, x, y, 4);
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void t_info(void)
{
    vg_lite_info_t info;
    char name[16];
    vg_lite_uint32_t id = 0, rev = 0, reg = 0;
    OK(vg_lite_get_info(&info));
    CHECK(vg_lite_get_product_info(name, &id, &rev) == 3 && strcmp(name, "G2D") == 0, "product name");
    CHECK(id == 0x47324420u, "chip id 0x%x", id);
    OK(vg_lite_get_register(0x000, &reg));
    CHECK(reg == 0x47324420u, "ID register 0x%x", reg);
    CHECK(vg_lite_query_feature(gcFEATURE_BIT_VG_SCISSOR) == 1, "scissor feature");
    CHECK(vg_lite_query_feature(gcFEATURE_BIT_VG_RADIAL_GRADIENT) == 1, "radial gradient (v1.5)");
    CHECK(vg_lite_query_feature(gcFEATURE_BIT_VG_IM_REPEAT_REFLECT) == 1, "repeat / reflect spread");
}

/* BGRA8888 bytes of vg_lite_color_t c (A<<24|B<<16|G<<8|R): B, G, R, A. */
static uint32_t bgra(vg_lite_color_t c)
{
    uint32_t r = c & 0xFF, g = (c >> 8) & 0xFF, b = (c >> 16) & 0xFF, a = c >> 24;
    return b | (g << 8) | (r << 16) | (a << 24);
}

static void t_clear(void)
{
    vg_lite_buffer_t t = make(64, 32, VG_LITE_BGRA8888);
    vg_lite_rectangle_t r = {10, 4, 20, 8};
    int x, y, bad = 0;
    CHECK(t.stride == 256, "stride align(64*4, 64) = %d", t.stride);
    OK(vg_lite_clear(&t, NULL, 0x80402010u));
    OK(vg_lite_clear(&t, &r, 0xFF00FF00u));
    /* scissor: a clear honours it (rendering.md §11) */
    OK(vg_lite_set_scissor(0, 20, 8, 24));
    OK(vg_lite_clear(&t, NULL, 0xFF0000FFu));
    OK(vg_lite_disable_scissor());
    OK(vg_lite_finish());
    for (y = 0; y < 32; y++)
        for (x = 0; x < 64; x++) {
            uint32_t want = bgra(0x80402010u);
            if (x >= 10 && x < 30 && y >= 4 && y < 12) want = bgra(0xFF00FF00u);
            if (x < 8 && y >= 20 && y < 24) want = bgra(0xFF0000FFu);
            bad += rd32(&t, x, y) != want;
        }
    CHECK(bad == 0, "clear: %d wrong pixels", bad);
    OK(vg_lite_free(&t));
}

static void t_copy_blit(void)
{
    vg_lite_buffer_t s = make(16, 8, VG_LITE_RGBA8888), t = make(40, 20, VG_LITE_BGRA8888);
    vg_lite_matrix_t m;
    int x, y, bad = 0;
    for (y = 0; y < 8; y++)
        for (x = 0; x < 16; x++) {
            uint8_t *p = px(&s, x, y, 4);
            p[0] = (uint8_t)(x * 16); p[1] = (uint8_t)(y * 32); p[2] = (uint8_t)(x + y); p[3] = 255;   /* R G B A */
        }
    OK(vg_lite_flush_mapped_buffer(&s));
    OK(vg_lite_clear(&t, NULL, 0));
    OK(vg_lite_copy_image(&t, &s, 3, 2, 0, 0, 16, 8));
    /* identity blit, point sampling, blend NONE: the same copy further right */
    vg_lite_identity(&m);
    vg_lite_translate(20.0f, 10.0f, &m);
    OK(vg_lite_blit(&t, &s, &m, VG_LITE_BLEND_NONE, 0, VG_LITE_FILTER_POINT));
    OK(vg_lite_finish());
    for (y = 0; y < 8; y++)
        for (x = 0; x < 16; x++) {
            uint8_t *q = px(&s, x, y, 4);
            uint32_t want = (uint32_t)q[2] | ((uint32_t)q[1] << 8) | ((uint32_t)q[0] << 16) | ((uint32_t)q[3] << 24);
            bad += rd32(&t, x + 3, y + 2) != want;
            bad += rd32(&t, x + 20, y + 10) != want;
        }
    CHECK(bad == 0, "copy / blit: %d wrong pixels", bad);
    CHECK(rd32(&t, 2, 2) == 0 && rd32(&t, 19, 2) == 0 && rd32(&t, 36, 10) == 0, "outside the copies untouched");
    OK(vg_lite_free(&s));
    OK(vg_lite_free(&t));
}

static void t_path(void)
{
    vg_lite_buffer_t t = make(32, 32, VG_LITE_BGRA8888);
    /* S16 square [8,24)^2: opcode elements MOVE x y LINE x y ... END */
    int16_t d[] = {VLC_OP_MOVE, 8, 8, VLC_OP_LINE, 24, 8, VLC_OP_LINE, 24, 24, VLC_OP_LINE, 8, 24, VLC_OP_CLOSE, VLC_OP_END};
    vg_lite_path_t p;
    int x, y, bad = 0;
    OK(vg_lite_init_path(&p, VG_LITE_S16, VG_LITE_HIGH, sizeof d, d, 8, 8, 24, 24));
    OK(vg_lite_clear(&t, NULL, 0xFF000000u));
    OK(vg_lite_draw(&t, &p, VG_LITE_FILL_NON_ZERO, NULL, VG_LITE_BLEND_NONE, 0xFF336699u));
    OK(vg_lite_finish());
    for (y = 0; y < 32; y++)
        for (x = 0; x < 32; x++) {
            int in = x >= 8 && x < 24 && y >= 8 && y < 24;
            bad += rd32(&t, x, y) != (in ? bgra(0xFF336699u) : bgra(0xFF000000u));
        }
    CHECK(bad == 0, "path square: %d wrong pixels", bad);
    OK(vg_lite_clear_path(&p));
    OK(vg_lite_free(&t));
}

static void t_grad(void)
{
    vg_lite_buffer_t t = make(256, 4, VG_LITE_BGRA8888);
    int16_t d[] = {VLC_OP_MOVE, 0, 0, VLC_OP_LINE, 256, 0, VLC_OP_LINE, 256, 4, VLC_OP_LINE, 0, 4, VLC_OP_END};
    vg_lite_path_t p;
    vg_lite_linear_gradient_t g;
    uint32_t colors[2] = {0xFF000000u, 0xFFFFFFFFu}, stops[2] = {0, 255}, ramp[256];
    int x, bad = 0;
    OK(vg_lite_init_path(&p, VG_LITE_S16, VG_LITE_HIGH, sizeof d, d, 0, 0, 256, 4));
    OK(vg_lite_init_grad(&g));
    OK(vg_lite_set_grad(&g, 2, colors, stops));
    OK(vg_lite_update_grad(&g));
    OK(vg_lite_draw_grad(&t, &p, VG_LITE_FILL_NON_ZERO, NULL, &g, VG_LITE_BLEND_NONE));
    OK(vg_lite_finish());
    g2d_geom_ramp_legacy(2, colors, stops, ramp);
    /* identity gradient matrix: x_g = x, ramp index floor(x + 0.5 - 0.5)... at the pixel centre: x */
    for (x = 0; x < 256; x++) {
        uint32_t a = ramp[x];   /* ARGB32 */
        uint32_t want = (a & 0xFF) | (((a >> 8) & 0xFF) << 8) | (((a >> 16) & 0xFF) << 16) | ((a >> 24) << 24);
        bad += rd32(&t, x, 1) != want;
    }
    CHECK(bad == 0, "gradient row: %d wrong pixels", bad);
    OK(vg_lite_clear_grad(&g));
    OK(vg_lite_clear_path(&p));
    OK(vg_lite_free(&t));
}

/* Many small draws: several command-buffer wraps, the last value of every pixel wins. */
static void t_cmdbuf_wrap(void)
{
    vg_lite_buffer_t t = make(64, 64, VG_LITE_BGRA8888);
    vg_lite_rectangle_t r;
    int i, bad = 0;
    for (i = 0; i < 20000; i++) {
        r.x = i % 64; r.y = (i / 64) % 64; r.width = 1; r.height = 1;
        OK(vg_lite_clear(&t, &r, (vg_lite_color_t)(0xFF000000u | (uint32_t)i)));
    }
    OK(vg_lite_finish());
    for (i = 20000 - 4096; i < 20000; i++) {
        vg_lite_color_t c = (vg_lite_color_t)(0xFF000000u | (uint32_t)i);
        bad += rd32(&t, i % 64, (i / 64) % 64) != bgra(c);
    }
    CHECK(bad == 0, "command buffer wrap: %d wrong pixels", bad);
    OK(vg_lite_free(&t));
}

static void t_matrix(void)
{
    vg_lite_matrix_t m;
    vg_lite_identity(&m);
    vg_lite_rotate(90.0f, &m);
    CHECK(m.m[0][0] == 0.0f && m.m[0][1] == -1.0f && m.m[1][0] == 1.0f && m.m[1][1] == 0.0f, "rotate 90 exact");
    vg_lite_identity(&m);
    vg_lite_rotate(30.0f, &m);
    CHECK(fabs(m.m[0][0] - 0.8660254f) < 1e-6 && fabs(m.m[1][0] - 0.5f) < 1e-6, "rotate 30");
    vg_lite_identity(&m);
    vg_lite_translate(5, 7, &m);
    vg_lite_scale(2, 3, &m);       /* m = T * S: a point is scaled first */
    CHECK(m.m[0][0] == 2 && m.m[1][1] == 3 && m.m[0][2] == 5 && m.m[1][2] == 7, "translate * scale");
}

static void t_errors(void)
{
    vg_lite_buffer_t t = make(16, 16, VG_LITE_BGRA8888), bad;
    vg_lite_matrix_t sing;
    vg_lite_path_t p;
    int16_t d[] = {VLC_OP_MOVE, 0, 0, VLC_OP_LINE, 4, 0, VLC_OP_LINE, 4, 4, VLC_OP_END};
    memset(&bad, 0, sizeof bad);
    bad.width = 8; bad.height = 8; bad.format = VG_LITE_YUYV;
    CHECK(vg_lite_allocate(&bad) == VG_LITE_NOT_SUPPORT, "YUYV not supported");
    {
        /* v1.4: source-only formats are rejected as targets; 24-bit formats are converted sources only */
        vg_lite_buffer_t a4 = make(16, 4, VG_LITE_A4), i8 = make(16, 4, VG_LITE_INDEX_8), rgb = make(8, 4, VG_LITE_RGB888);
        vg_lite_uint32_t pal[4] = {0xFF0000FFu, 0xFF00FF00u, 0x80FF0000u, 0};
        CHECK(a4.stride == 64 && i8.stride == 64, "sub-byte / indexed allocation stride");
        CHECK(vg_lite_clear(&a4, NULL, 0) == VG_LITE_NOT_SUPPORT && vg_lite_clear(&i8, NULL, 0) == VG_LITE_NOT_SUPPORT,
              "A4 / I8 targets");
        CHECK(vg_lite_clear(&rgb, NULL, 0) == VG_LITE_NOT_SUPPORT, "RGB888 target");
        CHECK(vg_lite_set_CLUT(0, pal) == VG_LITE_INVALID_ARGUMENT && vg_lite_set_CLUT(257, pal) == VG_LITE_INVALID_ARGUMENT,
              "CLUT count");
        OK(vg_lite_set_CLUT(4, pal));
        OK(vg_lite_blit(&t, &i8, NULL, VG_LITE_BLEND_SRC_OVER, 0, VG_LITE_FILTER_BI_LINEAR));
        OK(vg_lite_blit(&t, &a4, NULL, VG_LITE_BLEND_SRC_OVER, 0, VG_LITE_FILTER_POINT));
        OK(vg_lite_blit(&t, &rgb, NULL, VG_LITE_BLEND_NONE, 0, VG_LITE_FILTER_POINT));
        OK(vg_lite_finish());
        OK(vg_lite_free(&a4));
        OK(vg_lite_free(&i8));
        OK(vg_lite_free(&rgb));
    }
    CHECK(vg_lite_clear(NULL, NULL, 0) == VG_LITE_INVALID_ARGUMENT, "clear NULL target");
    {
        /* v1.6b Gaussian (rendering.md §7.3 / §9.10): argument checks; a white centre texel on black, w0 = 0.5,
         * w1 = w2 = 1/16 (s3.12: W1 = W2 = 256, W0 = 2048): centre (255 * 2048 + 2048) >> 12 = 128, every other pixel
         * of the 3 x 3 sees it once at an edge or corner weight 256: (255 * 256 + 2048) >> 12 = 16 */
        vg_lite_buffer_t g = make(3, 3, VG_LITE_BGRA8888), d = make(3, 3, VG_LITE_BGRA8888);
        vg_lite_matrix_t id;
        int x, y;
        /* decision 2026-10-26: only NaN / Inf are rejected; other kernels are accepted and saturate */
        CHECK(vg_lite_gaussian_filter(NAN, 0.3f, 0.0f) == VG_LITE_INVALID_ARGUMENT, "gaussian: NaN");
        CHECK(vg_lite_gaussian_filter(0.0f, INFINITY, 0.0f) == VG_LITE_INVALID_ARGUMENT, "gaussian: Inf");
        CHECK(vg_lite_gaussian_filter(-0.1f, 0.3f, 0.0f) == VG_LITE_SUCCESS, "gaussian: negative weight accepted");
        CHECK(vg_lite_gaussian_filter(0.5f, 0.2f, 0.2f) == VG_LITE_SUCCESS, "gaussian: sum != 1 accepted");
        OK(vg_lite_gaussian_filter(0.5f, 0.0625f, 0.0625f));
        OK(vg_lite_clear(&g, NULL, 0xFF000000u));
        OK(vg_lite_finish());
        ((uint32_t *)((uint8_t *)g.memory + g.stride))[1] = 0xFFFFFFFFu;
        OK(vg_lite_flush_mapped_buffer(&g));
        OK(vg_lite_identity(&id));
        OK(vg_lite_blit(&d, &g, &id, VG_LITE_BLEND_NONE, 0, VG_LITE_FILTER_GAUSSIAN));
        OK(vg_lite_finish());
        for (y = 0; y < 3; y++)
            for (x = 0; x < 3; x++) {
                uint32_t c = (x == 1 && y == 1) ? 128u : 16u;
                CHECK(rd32(&d, x, y) == bgra(0xFF000000u | (c << 16) | (c << 8) | c), "gaussian 3 x 3 by hand");
            }
        /* sharpening kernel w0 = 5, w1 = -1 (unity gain, W0 = 20480, W1 = -4096 in 1/4096): the centre 5 * 255
         * saturates to 255, the edge pixels -255 to 0, the corners see 0; A = 255 * (5 - 4) = 255 everywhere */
        OK(vg_lite_gaussian_filter(5.0f, -1.0f, 0.0f));
        OK(vg_lite_blit(&d, &g, &id, VG_LITE_BLEND_NONE, 0, VG_LITE_FILTER_GAUSSIAN));
        OK(vg_lite_finish());
        for (y = 0; y < 3; y++)
            for (x = 0; x < 3; x++) {
                uint32_t c = (x == 1 && y == 1) ? 255u : 0u;
                CHECK(rd32(&d, x, y) == bgra(0xFF000000u | (c << 16) | (c << 8) | c), "sharpening kernel saturates");
            }
        OK(vg_lite_free(&g));
        OK(vg_lite_free(&d));
    }
    memset(&sing, 0, sizeof sing);
    CHECK(vg_lite_blit(&t, &t, &sing, VG_LITE_BLEND_NONE, 0, VG_LITE_FILTER_POINT) == VG_LITE_INVALID_ARGUMENT,
          "singular matrix (Q-API-8)");
    OK(vg_lite_init_path(&p, VG_LITE_S16, VG_LITE_HIGH, sizeof d, d, 0, 0, 4, 4));
    OK(vg_lite_set_path_type(&p, VG_LITE_DRAW_STROKE_PATH));                      /* strokes: rendering.md §9.7 */
    CHECK(vg_lite_draw(&t, &p, VG_LITE_FILL_NON_ZERO, NULL, VG_LITE_BLEND_NONE, 0) == VG_LITE_INVALID_ARGUMENT,
          "stroke draw without vg_lite_set_stroke / vg_lite_update_stroke");
    {
        vg_lite_float_t dash[3] = {2.0f, 1.0f, 5.0f};             /* dashes: rendering.md §9.7.1; odd count */
        CHECK(vg_lite_set_stroke(&p, VG_LITE_CAP_BUTT, VG_LITE_JOIN_MITER, 2.0f, 4.0f, NULL, 2, 0.0f, 0) ==
              VG_LITE_INVALID_ARGUMENT, "dash count without a pattern");
        OK(vg_lite_set_stroke(&p, VG_LITE_CAP_BUTT, VG_LITE_JOIN_MITER, 2.0f, 4.0f, dash, 3, 0.5f, 0));
        dash[0] = 99.0f;                                           /* the driver keeps its own copy */
        CHECK(p.stroke->dash_pattern != dash && p.stroke->dash_pattern[0] == 2.0f && p.stroke->pattern_count == 3 &&
              p.stroke->dash_phase == 0.5f && p.stroke->pattern_length == 8.0f, "dash pattern stored");
        OK(vg_lite_update_stroke(&p));
        OK(vg_lite_draw(&t, &p, VG_LITE_FILL_NON_ZERO, NULL, VG_LITE_BLEND_NONE, 0));
        CHECK(vg_lite_set_stroke(&p, VG_LITE_CAP_BUTT, VG_LITE_JOIN_MITER, 0.0f, 4.0f, NULL, 0, 0.0f, 0) ==
              VG_LITE_INVALID_ARGUMENT, "zero stroke width");
        OK(vg_lite_set_stroke(&p, VG_LITE_CAP_ROUND, VG_LITE_JOIN_ROUND, 2.0f, 4.0f, NULL, 0, 0.0f, 0xFF00FF00u));
        OK(vg_lite_update_stroke(&p));
        OK(vg_lite_draw(&t, &p, VG_LITE_FILL_NON_ZERO, NULL, VG_LITE_BLEND_NONE, 0));
    }
    CHECK(vg_lite_set_path_type(&p, 0x77) == VG_LITE_INVALID_ARGUMENT, "unknown path type");
    OK(vg_lite_set_path_type(&p, VG_LITE_DRAW_FILL_PATH));
    {
        vg_lite_matrix_t persp;
        vg_lite_identity(&persp);
        persp.m[2][0] = 0.01f;
        OK(vg_lite_draw(&t, &p, VG_LITE_FILL_NON_ZERO, &persp, VG_LITE_BLEND_NONE, 0));   /* rendering.md §9.8 */
    }
    OK(vg_lite_draw(&t, &p, VG_LITE_FILL_NON_ZERO, NULL, OPENVG_BLEND_SRC_OVER, 0));          /* v1.6, §5.7 */
    OK(vg_lite_draw(&t, &p, VG_LITE_FILL_NON_ZERO, NULL, SVG2_BLEND_EXCLUSION, 0));
    CHECK(vg_lite_draw(&t, &p, VG_LITE_FILL_NON_ZERO, NULL, (vg_lite_blend_t)0x3002, 0) == VG_LITE_NOT_SUPPORT,
          "SVG2 code without a mode (0x3002)");
    CHECK(vg_lite_blend_func(9, 0, 0, 0, 0, 0, 0) == VG_LITE_INVALID_ARGUMENT, "blend_func alpha factor");
    CHECK(vg_lite_blend_func(0, 0, 0, 0, 0, 11, 0) == VG_LITE_INVALID_ARGUMENT, "blend_func channel mode");
    OK(vg_lite_blend_func(VG_LITE_BLEND_FACTOR_ALPHA_ONE, VG_LITE_BLEND_FACTOR_COLOR_SRC_ALPHA,
                          VG_LITE_BLEND_FACTOR_ALPHA_INV_SRC_ALPHA, VG_LITE_BLEND_FACTOR_COLOR_INV_SRC_ALPHA,
                          VG_LITE_BLEND_FUNC_ADD, VG_LITE_CHANNEL_MODE_BYPASS, VG_LITE_CHANNEL_MODE_BYPASS));
    OK(vg_lite_draw(&t, &p, VG_LITE_FILL_NON_ZERO, NULL, VG_LITE_PORTER_DUFF_BLEND, 0));
    {
        /* v1.6: the union of overlapping rectangles, each pixel drawn once (a blended draw shows a double draw) */
        vg_lite_rectangle_t three[3] = {{0, 0, 6, 6}, {3, 3, 6, 6}, {12, 2, 2, 9}};
        vg_lite_rectangle_t all = {0, 0, 16, 16};
        int x, y, bad = 0;
        OK(vg_lite_scissor_rects(&t, 0, NULL));
        OK(vg_lite_clear(&t, &all, 0xFF000000u));
        OK(vg_lite_scissor_rects(&t, 3, three));
        OK(vg_lite_clear(&t, &all, 0x80404040u));                 /* blend NONE */
        {
            int16_t sq[] = {2, 0, 0, 4, 8, 0, 4, 8, 8, 4, 0, 8, 1, 0};    /* MOVE LINE LINE LINE CLOSE END: [0, 8)^2 */
            vg_lite_path_t ps;
            OK(vg_lite_init_path(&ps, VG_LITE_S16, VG_LITE_HIGH, sizeof sq, sq, 0, 0, 8, 8));
            OK(vg_lite_draw(&t, &ps, VG_LITE_FILL_NON_ZERO, NULL, VG_LITE_BLEND_ADDITIVE, 0x01010101u));
            OK(vg_lite_finish());
            OK(vg_lite_clear_path(&ps));
        }
        for (y = 0; y < 16; y++)
            for (x = 0; x < 16; x++) {
                int in = (x < 6 && y < 6) || (x >= 3 && x < 9 && y >= 3 && y < 9) || (x >= 12 && x < 14 && y >= 2 && y < 11);
                uint32_t v = rd32(&t, x, y), cov = in && x < 8 && y < 8;  /* the square covers [0, 8) x [0, 8) */
                uint32_t exp = !in ? 0xFF000000u : cov ? 0x81414141u : 0x80404040u;
                bad += v != exp;
            }
        CHECK(bad == 0, "scissor rects: %d wrong pixels", bad);
        CHECK(vg_lite_scissor_rects(&t, 2, NULL) == VG_LITE_INVALID_ARGUMENT, "NULL rects");
        OK(vg_lite_scissor_rects(&t, 0, NULL));                   /* off for the tests below */
    }
    CHECK(vg_lite_set_gamma(VG_LITE_GAMMA_NO_CONVERSION) == VG_LITE_SUCCESS &&
          vg_lite_set_gamma((vg_lite_gamma_conversion_t)7) == VG_LITE_INVALID_ARGUMENT, "gamma (v1.6b)");
    {
        /* vg_lite_blit2 (v1.6) = the two blits in order */
        vg_lite_buffer_t a = make(4, 4, VG_LITE_BGRA8888), b = make(4, 4, VG_LITE_BGRA8888);
        vg_lite_matrix_t m0, m1;
        OK(vg_lite_clear(&a, NULL, 0xFF0000FFu));
        OK(vg_lite_clear(&b, NULL, 0x8000FF00u));
        OK(vg_lite_identity(&m0));
        OK(vg_lite_identity(&m1));
        OK(vg_lite_translate(2.0f, 0.0f, &m1));
        OK(vg_lite_clear(&t, NULL, 0xFF000000u));
        OK(vg_lite_blit2(&t, &a, &b, &m0, &m1, VG_LITE_BLEND_NONE, VG_LITE_FILTER_POINT));
        OK(vg_lite_finish());
        CHECK(rd32(&t, 0, 0) == bgra(0xFF0000FFu) && rd32(&t, 1, 1) == bgra(0xFF0000FFu), "blit2 source0");
        CHECK(rd32(&t, 2, 0) == bgra(0x8000FF00u) && rd32(&t, 5, 3) == bgra(0x8000FF00u), "blit2 source1 over it");
        CHECK(rd32(&t, 6, 0) == bgra(0xFF000000u), "blit2 outside both");
        CHECK(vg_lite_blit2(&t, &a, NULL, &m0, &m1, VG_LITE_BLEND_NONE, VG_LITE_FILTER_POINT) == VG_LITE_INVALID_ARGUMENT,
              "blit2 NULL source");
        OK(vg_lite_free(&a));
        OK(vg_lite_free(&b));
    }
    CHECK(vg_lite_enable_dither() == VG_LITE_SUCCESS && vg_lite_disable_dither() == VG_LITE_SUCCESS, "dither (v1.4)");
    CHECK(vg_lite_query_feature(gcFEATURE_BIT_VG_IM_INDEX_FORMAT) && vg_lite_query_feature(gcFEATURE_BIT_VG_DITHER) &&
          vg_lite_query_feature(gcFEATURE_BIT_VG_INDEX_ENDIAN) && vg_lite_query_feature(gcFEATURE_BIT_VG_24BIT),
          "v1.4 feature bits, 24BIT since v1.6b");
    OK(vg_lite_finish());
    OK(vg_lite_free(&t));
}

/* vg_lite_set_command_buffer / _set_memory_pool / _frame_delimiter (api-subset.md §1): the caller's memory carries
 * the commands (wraps through both halves), errors are exact. Runs last: the buffer stays the driver's until
 * vg_lite_close. */
/* v1.6b (decision 2026-10-26): the closed formats never draw silently: VG_LITE_YUYV (layout not public; the first to
 * open once confirmed), the *_PLANAR 24-bit formats, the tiled YUV and ETC2 formats are VG_LITE_NOT_SUPPORT on
 * allocate, upload, blit and pattern draws, and the target is left unchanged. */
static void t_closed_formats(void)
{
    static const vg_lite_buffer_format_t closed[] = {
        VG_LITE_YUYV, VG_LITE_ABGR8565_PLANAR, VG_LITE_BGRA5658_PLANAR, VG_LITE_ARGB8565_PLANAR,
        VG_LITE_RGBA5658_PLANAR, VG_LITE_YUY2_TILED, VG_LITE_NV12_TILED, VG_LITE_ANV12_TILED, VG_LITE_AYUY2_TILED,
        VG_LITE_NV24_TILED, VG_LITE_RGBA8888_ETC2_EAC, VG_LITE_RGB888_ETC2_EAC};
    vg_lite_buffer_t t = make(8, 8, VG_LITE_BGRA8888), s = make(8, 8, VG_LITE_BGRA8888), c;
    vg_lite_matrix_t id;
    unsigned k;
    int x, y, same = 1;
    OK(vg_lite_identity(&id));
    OK(vg_lite_clear(&t, NULL, 0xFF123456u));
    OK(vg_lite_finish());
    for (k = 0; k < sizeof closed / sizeof closed[0]; k++) {
        vg_lite_buffer_t a;
        vg_lite_uint8_t *data[3] = {(vg_lite_uint8_t *)s.memory, (vg_lite_uint8_t *)s.memory, (vg_lite_uint8_t *)s.memory};
        vg_lite_uint32_t st[3] = {64, 64, 64};
        memset(&a, 0, sizeof a);
        a.width = 8;
        a.height = 8;
        a.format = closed[k];
        CHECK(vg_lite_allocate(&a) == VG_LITE_NOT_SUPPORT, "closed format %u: allocate", (unsigned)closed[k]);
        c = s;                           /* valid memory, closed format */
        c.format = closed[k];
        CHECK(vg_lite_upload_buffer(&c, data, st) == VG_LITE_NOT_SUPPORT, "closed format %u: upload", (unsigned)closed[k]);
        CHECK(vg_lite_blit(&t, &c, &id, VG_LITE_BLEND_NONE, 0, VG_LITE_FILTER_POINT) == VG_LITE_NOT_SUPPORT,
              "closed format %u: blit", (unsigned)closed[k]);
        CHECK(vg_lite_blit(&c, &s, &id, VG_LITE_BLEND_NONE, 0, VG_LITE_FILTER_POINT) == VG_LITE_NOT_SUPPORT,
              "closed format %u: as a target", (unsigned)closed[k]);
    }
    OK(vg_lite_finish());
    for (y = 0; y < 8; y++)
        for (x = 0; x < 8; x++)
            same &= rd32(&t, x, y) == bgra(0xFF123456u);
    CHECK(same, "closed formats: target unchanged");
    OK(vg_lite_free(&t));
    OK(vg_lite_free(&s));
}

static void t_cmd_api(void)
{
    vg_lite_buffer_t cmd = make(1024, 16, VG_LITE_A8);      /* 16 KB of GPU memory: two 8 KB command buffers */
    vg_lite_buffer_t t = make(64, 64, VG_LITE_BGRA8888);
    vg_lite_rectangle_t r;
    int i, bad = 0;
    CHECK(vg_lite_set_memory_pool(VG_LITE_RENDER_BUFFER, VG_LITE_MEMORY_POOL_1) == VG_LITE_SUCCESS, "pool 1");
    CHECK(vg_lite_set_memory_pool(VG_LITE_COMMAND_BUFFER, VG_LITE_MEMORY_POOL_2) == VG_LITE_NOT_SUPPORT, "pool 2");
    CHECK(vg_lite_set_memory_pool((vg_lite_buffer_type_t)7, VG_LITE_MEMORY_POOL_1) == VG_LITE_INVALID_ARGUMENT, "type");
    CHECK(vg_lite_set_memory_pool(VG_LITE_RENDER_BUFFER, (vg_lite_memory_pool_t)5) == VG_LITE_INVALID_ARGUMENT, "pool");
    CHECK(vg_lite_set_command_buffer(cmd.address + 4u, 8192u) == VG_LITE_NOT_ALIGNED, "unaligned command buffer");
    CHECK(vg_lite_set_command_buffer(cmd.address, 8128u) == VG_LITE_INVALID_ARGUMENT, "halves below 4 KB");
    CHECK(vg_lite_frame_delimiter((vg_lite_frame_flag_t)9, 0) == VG_LITE_INVALID_ARGUMENT, "frame flag");
    OK(vg_lite_clear(&t, NULL, 0xFF000000u));
    OK(vg_lite_set_command_buffer(cmd.address, 16384u));
    for (i = 0; i < 20000; i++) {
        r.x = i % 64; r.y = (i / 64) % 64; r.width = 1; r.height = 1;
        OK(vg_lite_clear(&t, &r, (vg_lite_color_t)(0xFF000000u | (uint32_t)i)));
        if (i == 10000)
            OK(vg_lite_frame_delimiter(VG_LITE_END_FLAG, 0));
    }
    OK(vg_lite_frame_delimiter(VG_LITE_FRAME_END_FLAG, 1));
    for (i = 20000 - 4096; i < 20000; i++) {
        vg_lite_color_t c = (vg_lite_color_t)(0xFF000000u | (uint32_t)i);
        bad += rd32(&t, i % 64, (i / 64) % 64) != bgra(c);
    }
    CHECK(bad == 0, "user command buffer: %d wrong pixels", bad);
    {
        uint32_t w0 = ((const uint32_t *)cmd.memory)[0], op = w0 >> 24;
        CHECK(op != 0u, "commands were written into the caller's buffer (first word 0x%08x)", w0);
    }
    OK(vg_lite_free(&t));
}

int main(void)
{
    if (vg_lite_init(0, 0) != VG_LITE_SUCCESS) {
        printf("FAIL: vg_lite_init\n");
        return 1;
    }
    t_info();
    t_clear();
    t_closed_formats();
    t_copy_blit();
    t_path();
    t_grad();
    t_cmdbuf_wrap();
    t_matrix();
    t_errors();
    t_cmd_api();
    OK(vg_lite_close());
    printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
