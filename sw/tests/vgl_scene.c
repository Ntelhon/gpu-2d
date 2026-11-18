// SPDX-License-Identifier: Apache-2.0
/* Scene runner for driver tests (sw/tests/test_scenes.py): reads a line-based script from stdin, runs
 * it through the vg_lite API on the linked backend and writes buffers on request. With G2D_RECORD set, the
 * software backend also records the command stream.
 *
 * Script (one command per line, '#' comments):
 *   buf ID W H FMT [STRIDE]                allocate (FMT: vg_lite_buffer_format_t value)
 *   fill ID SEED                           deterministic bytes (xorshift32) into every row
 *   coords ID                              32-bit pixels: bytes (x, y, 0x5A, 0xFF) (texel identity tests)
 *   load ID FILE                           raw rows from FILE (stride bytes each) into the buffer
 *   img ID IMAGE_MODE TRANSPARENCY APPLY_PREMULT
 *   endian ID E                            index_endian of an indexed buffer (v1.4)
 *   clut N C1..CN                          vg_lite_set_CLUT (v1.4)
 *   dither 0|1                             vg_lite_enable_dither / vg_lite_disable_dither (v1.4)
 *   gpaint COLOR                           paint_color of the next xgrad draws (SPREAD_FILL, v1.4; default 0)
 *   pcmode 0|1                             vg_lite_g2d_set_pattern_color_mode(TEXEL / SOLID) (v1.4)
 *   clear ID X Y W H COLOR                 W < 0: whole target (rect NULL)
 *   scissor X Y R B | noscissor
 *   salpha MODE VALUE | dalpha MODE VALUE
 *   ckey (EN LR LG LB A HR HG HB) x 4
 *   blit T S BLEND COLOR FILTER M00 M01 M02 M10 M11 M12 M20 M21 M22 [X Y W H]
 *   copy T S DX DY SX SY W H
 *   path T FMT QUALITY FILL BLEND COLOR M00..M22 N V1..VN       N elements; opcodes written @OP
 *   lgrad T FMT QUALITY FILL BLEND M00..M22 G00..G22 K C1..CK S1..SK N V1..VN
 *   xgrad T FMT QUALITY FILL BLEND SPREAD PREMUL M00..M22 G00..G22 X0 Y0 X1 Y1 K (STOP R G B A)xK N V1..VN
 *   stroke T FMT QUALITY BLEND TYPE COLOR SCOLOR CAP JOIN WIDTH MITER M00..M22 N V1..VN
 *                                          vg_lite_set_path_type(TYPE) + vg_lite_set_stroke + vg_lite_draw
 *   dstroke T FMT QUALITY BLEND TYPE COLOR SCOLOR CAP JOIN WIDTH MITER NPAT P1..PNPAT PHASE RESET M00..M22 N V1..VN
 *                                          as stroke, dashed (NPAT <= 16 entries, path->stroke->dash_reset = RESET)
 *   pattern T S FMT QUALITY FILL BLEND MODE PCOLOR COLOR FILTER M00..M22 P00..P22 N V1..VN
 *                                          vg_lite_draw_pattern: M path matrix, P pattern matrix
 *   finish
 *   dump ID FILE                           raw rows (stride bytes each)
 * Every API call prints "rc <name> <code>" so the test can check return codes.
 *
 * G2D_BAREMETAL (Phase 7, sw/apps/baremetal): stdin is an embedded script, `dump ID FILE` prints
 * "crc ID <crc32 of the stride * height bytes>" instead of writing FILE, `load` is not available, and the
 * buffers are freed before vg_lite_close so the runner can call the entry point once per script.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "vg_lite.h"
#include "vg_lite_g2d.h"

#define MAXBUF 16

static vg_lite_buffer_t g_buf[MAXBUF];
static vg_lite_color_t g_gpaint;   /* paint_color of xgrad draws (gpaint) */

static int rd_i(void) { long v; if (scanf("%ld", &v) != 1) { fprintf(stderr, "parse error\n"); exit(2); } return (int)v; }
static uint32_t rd_u(void) { unsigned long v; if (scanf("%lu", &v) != 1) { fprintf(stderr, "parse error\n"); exit(2); } return (uint32_t)v; }
static double rd_d(void) { double v; if (scanf("%lf", &v) != 1) { fprintf(stderr, "parse error\n"); exit(2); } return v; }

static void rd_m(vg_lite_matrix_t *m)
{
    int r, c;
    vg_lite_identity(m);
    for (r = 0; r < 3; r++)
        for (c = 0; c < 3; c++)
            m->m[r][c] = (vg_lite_float_t)rd_d();
}

static void rc(const char *name, vg_lite_error_t e) { printf("rc %s %d\n", name, (int)e); }

#ifdef G2D_BAREMETAL
/* CRC-32 (IEEE 802.3, reflected, as zlib.crc32) */
static uint32_t crc32_bytes(const uint8_t *p, size_t n)
{
    uint32_t c = 0xFFFFFFFFu;
    size_t i;
    int k;
    for (i = 0; i < n; i++) {
        c ^= p[i];
        for (k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1u)));
    }
    return ~c;
}
#endif

/* Path data from N elements in the path format (opcodes are elements too). */
static void *rd_path(vg_lite_format_t fmt, uint32_t *bytes)
{
    int n = rd_i(), i, eb = fmt == VG_LITE_S8 ? 1 : fmt == VG_LITE_S16 ? 2 : 4;
    uint8_t *d = (uint8_t *)calloc((size_t)n + 1, (size_t)eb);
    for (i = 0; i < n; i++) {
        char tok[64];
        double v;
        if (scanf("%63s", tok) != 1)
            exit(2);
        if (tok[0] == '@') {                   /* opcode element: an integer in every format */
            uint32_t op = (uint32_t)strtoul(tok + 1, NULL, 0);
            if (fmt == VG_LITE_S8) ((int8_t *)d)[i] = (int8_t)op;
            else if (fmt == VG_LITE_S16) ((int16_t *)d)[i] = (int16_t)op;
            else ((uint32_t *)d)[i] = op;
            continue;
        }
        v = strtod(tok, NULL);
        if (fmt == VG_LITE_S8) ((int8_t *)d)[i] = (int8_t)v;
        else if (fmt == VG_LITE_S16) ((int16_t *)d)[i] = (int16_t)v;
        else if (fmt == VG_LITE_S32) ((int32_t *)d)[i] = (int32_t)v;
        else ((float *)d)[i] = (float)v;
    }
    *bytes = (uint32_t)n * (uint32_t)eb;
    return d;
}

#define E(n) {#n, (long)(n)}
static const struct { const char *name; long value; } k_enums[] = {
        E(VG_LITE_SUCCESS),
        E(VG_LITE_INVALID_ARGUMENT),
        E(VG_LITE_NOT_SUPPORT),
        E(VG_LITE_NOT_ALIGNED),
        E(VG_LITE_A8),
        E(VG_LITE_CLEAR_MASK), E(VG_LITE_FILL_MASK), E(VG_LITE_SET_MASK), E(VG_LITE_UNION_MASK),
        E(VG_LITE_INTERSECT_MASK), E(VG_LITE_SUBTRACT_MASK),
        E(VG_LITE_L8),
        E(VG_LITE_RGB565),
        E(VG_LITE_BGR565),
        E(VG_LITE_RGBA8888),
        E(VG_LITE_BGRA8888),
        E(VG_LITE_ARGB8888),
        E(VG_LITE_ABGR8888),
        E(VG_LITE_RGBX8888),
        E(VG_LITE_BGRX8888),
        E(VG_LITE_XRGB8888),
        E(VG_LITE_XBGR8888),
        E(VG_LITE_RGBA4444),
        E(VG_LITE_BGRA4444),
        E(VG_LITE_BGRA5551),
        E(VG_LITE_BGRA2222),
        E(VG_LITE_A4),
        E(VG_LITE_RGBA5551),
        E(VG_LITE_ARGB1555),
        E(VG_LITE_ABGR1555),
        E(VG_LITE_ARGB4444),
        E(VG_LITE_ABGR4444),
        E(VG_LITE_RGBA2222),
        E(VG_LITE_ARGB2222),
        E(VG_LITE_ABGR2222),
        E(VG_LITE_A2),
        E(VG_LITE_A1),
        E(VG_LITE_L4), E(VG_LITE_A8L8),
        E(OPENVG_sRGBX_8888), E(OPENVG_sRGBA_8888), E(OPENVG_sRGB_565), E(OPENVG_sRGBA_5551), E(OPENVG_sRGBA_4444), E(OPENVG_sL_8), E(OPENVG_A_8), E(OPENVG_sARGB_8888), E(OPENVG_sXRGB_8888), E(OPENVG_sARGB_1555), E(OPENVG_sARGB_4444), E(OPENVG_sBGRA_8888), E(OPENVG_sBGRX_8888), E(OPENVG_sBGR_565), E(OPENVG_sBGRA_5551), E(OPENVG_sBGRA_4444), E(OPENVG_sABGR_8888), E(OPENVG_sXBGR_8888), E(OPENVG_sABGR_1555), E(OPENVG_sABGR_4444), E(OPENVG_lRGBA_8888), E(OPENVG_BW_1), E(VG_LITE_YV24), E(VG_LITE_YV16), E(VG_LITE_YV12), E(VG_LITE_NV24), E(VG_LITE_NV16), E(VG_LITE_ANV12), E(VG_LITE_NV12), E(VG_LITE_AYUY2), E(VG_LITE_YUY2),
        E(VG_LITE_INDEX_1),
        E(VG_LITE_INDEX_2),
        E(VG_LITE_INDEX_4),
        E(VG_LITE_INDEX_8),
        E(VG_LITE_RGB888),
        E(VG_LITE_BGR888),
        E(VG_LITE_ARGB8565),
        E(VG_LITE_ABGR8565),
        E(VG_LITE_BGRA5658),
        E(VG_LITE_RGBA5658),
        E(VG_LITE_YUYV),
        E(VG_LITE_INDEX_LITTLE_ENDIAN),
        E(VG_LITE_INDEX_BIG_ENDIAN),
        E(VG_LITE_FILTER_POINT),
        E(VG_LITE_FILTER_LINEAR),
        E(VG_LITE_FILTER_BI_LINEAR),
        E(VG_LITE_FILTER_GAUSSIAN),
        E(VG_LITE_NORMAL),
        E(VG_LITE_GLOBAL),
        E(VG_LITE_SCALED),
        E(VG_LITE_S8),
        E(VG_LITE_S16),
        E(VG_LITE_S32),
        E(VG_LITE_FP32),
        E(VG_LITE_HIGH),
        E(VG_LITE_UPPER),
        E(VG_LITE_MEDIUM),
        E(VG_LITE_LOW),
        E(VG_LITE_FILL_EVEN_ODD),
        E(VG_LITE_FILL_NON_ZERO),
        E(VG_LITE_ZERO),
        E(VG_LITE_NORMAL_IMAGE_MODE),
        E(VG_LITE_MULTIPLY_IMAGE_MODE), E(VG_LITE_STENCIL_MODE), E(VG_LITE_RECOLOR_MODE), E(VG_LITE_NONE_IMAGE_MODE),
        E(VG_LITE_STENCIL_MODE),
        E(VG_LITE_IMAGE_OPAQUE),
        E(VG_LITE_IMAGE_TRANSPARENT),
        E(VG_LITE_GRADIENT_SPREAD_FILL),
        E(VG_LITE_GRADIENT_SPREAD_PAD),
        E(VG_LITE_GRADIENT_SPREAD_REPEAT),
        E(VG_LITE_GRADIENT_SPREAD_REFLECT),
        E(OPENVG_BLEND_SRC), E(OPENVG_BLEND_SRC_OVER), E(OPENVG_BLEND_DST_OVER), E(OPENVG_BLEND_SRC_IN),
        E(OPENVG_BLEND_DST_IN), E(OPENVG_BLEND_MULTIPLY), E(OPENVG_BLEND_SCREEN), E(OPENVG_BLEND_DARKEN),
        E(OPENVG_BLEND_LIGHTEN), E(OPENVG_BLEND_ADDITIVE), E(SVG2_BLEND_NORMAL), E(SVG2_BLEND_MULTIPLY),
        E(SVG2_BLEND_SCREEN), E(SVG2_BLEND_LIGHTEN), E(SVG2_BLEND_DARKEN), E(SVG2_BLEND_DIFFERENCE),
        E(SVG2_BLEND_EXCLUSION), E(VG_LITE_PORTER_DUFF_BLEND),
        E(VLC_OP_END),
        E(VLC_OP_CLOSE),
        E(VLC_OP_MOVE),
        E(VLC_OP_MOVE_REL),
        E(VLC_OP_LINE),
        E(VLC_OP_LINE_REL),
        E(VLC_OP_QUAD),
        E(VLC_OP_QUAD_REL),
        E(VLC_OP_CUBIC),
        E(VLC_OP_CUBIC_REL),
        E(VLC_OP_HLINE),
        E(VLC_OP_VLINE),
        E(VLC_OP_SQUAD),
        E(VLC_OP_SCUBIC),
        E(VLC_OP_SCCWARC),
        E(VG_LITE_BLEND_NONE),
        E(VG_LITE_BLEND_SRC_OVER),
        E(VG_LITE_BLEND_DST_OVER),
        E(VG_LITE_BLEND_SRC_IN),
        E(VG_LITE_BLEND_DST_IN),
        E(VG_LITE_BLEND_MULTIPLY),
        E(VG_LITE_BLEND_SCREEN),
        E(VG_LITE_BLEND_DARKEN),
        E(VG_LITE_BLEND_LIGHTEN),
        E(VG_LITE_BLEND_ADDITIVE),
        E(VG_LITE_BLEND_SUBTRACT),
        E(VG_LITE_BLEND_NORMAL_LVGL),
        E(VG_LITE_BLEND_ADDITIVE_LVGL),
        E(VG_LITE_BLEND_SUBTRACT_LVGL),
        E(VG_LITE_BLEND_DIFFERENCE_LVGL),
        E(VG_LITE_BLEND_MULTIPLY_LVGL),
        E(VG_LITE_PATTERN_COLOR),
        E(VG_LITE_CAP_BUTT),
        E(VG_LITE_CAP_ROUND),
        E(VG_LITE_CAP_SQUARE),
        E(VG_LITE_JOIN_MITER),
        E(VG_LITE_JOIN_ROUND),
        E(VG_LITE_JOIN_BEVEL),
        E(VG_LITE_DRAW_FILL_PATH),
        E(VG_LITE_DRAW_STROKE_PATH),
        E(VG_LITE_DRAW_FILL_STROKE_PATH),
        E(VG_LITE_PATTERN_PAD),
        E(VG_LITE_PATTERN_REPEAT),
        E(VG_LITE_PATTERN_REFLECT),
};
#undef E

#ifdef G2D_BAREMETAL
#define main vgl_scene_main
#endif
int main(int argc, char **argv)
{
    char cmd[32];
    vg_lite_error_t e;
    if (argc > 1 && !strcmp(argv[1], "enums")) {   /* enum values for the Python side */
        size_t i;
        for (i = 0; i < sizeof k_enums / sizeof k_enums[0]; i++)
            printf("%s %ld\n", k_enums[i].name, k_enums[i].value);
        return 0;
    }
    e = vg_lite_init(0, 0);
    rc("init", e);
    if (e != VG_LITE_SUCCESS)
        return 1;
    while (scanf("%31s", cmd) == 1) {
        if (cmd[0] == '#') {
            int ch;
            while ((ch = getchar()) != '\n' && ch != EOF) {}
        } else if (!strcmp(cmd, "buf")) {
            int id = rd_i();
            memset(&g_buf[id], 0, sizeof g_buf[id]);
            g_buf[id].width = rd_i();
            g_buf[id].height = rd_i();
            g_buf[id].format = (vg_lite_buffer_format_t)rd_i();
            g_buf[id].stride = rd_i();
            rc("allocate", vg_lite_allocate(&g_buf[id]));
            printf("buf %d %u %d\n", id, (unsigned)g_buf[id].address, (int)g_buf[id].stride);
        } else if (!strcmp(cmd, "fill")) {
            int id = rd_i(), y;
            uint32_t s = rd_u() | 1u;
            vg_lite_buffer_t *b = &g_buf[id];
            for (y = 0; y < b->height; y++) {
                int x;
                for (x = 0; x < b->stride; x++) {
                    s ^= s << 13; s ^= s >> 17; s ^= s << 5;
                    ((uint8_t *)b->memory)[(size_t)y * (size_t)b->stride + (size_t)x] = (uint8_t)(s >> 24);
                }
            }
            {   /* v1.6b YUV: the chroma / alpha planes too (same generator, continued) */
                uint8_t *pm[3] = {(uint8_t *)b->yuv.uv_memory, (uint8_t *)b->yuv.v_memory, (uint8_t *)b->yuv.alpha_memory};
                uint32_t ps[3] = {b->yuv.uv_stride, b->yuv.v_stride, b->yuv.alpha_stride};
                uint32_t ph[3] = {b->yuv.uv_height, b->yuv.v_height, b->yuv.alpha_height};
                int k;
                for (k = 0; k < 3; k++) {
                    size_t i;
                    if (!pm[k])
                        continue;
                    for (i = 0; i < (size_t)ps[k] * ph[k]; i++) {
                        s ^= s << 13; s ^= s >> 17; s ^= s << 5;
                        pm[k][i] = (uint8_t)(s >> 24);
                    }
                    rc("flush_mapped", vg_lite_flush_mapped_buffer(b));
                }
            }
            rc("flush_mapped", vg_lite_flush_mapped_buffer(b));
        } else if (!strcmp(cmd, "coords")) {
            int id = rd_i(), x, y;
            vg_lite_buffer_t *b = &g_buf[id];
            for (y = 0; y < b->height; y++)
                for (x = 0; x < b->width; x++) {
                    uint8_t *q = (uint8_t *)b->memory + (size_t)y * (size_t)b->stride + 4u * (size_t)x;
                    q[0] = (uint8_t)x; q[1] = (uint8_t)y; q[2] = 0x5A; q[3] = 0xFF;
                }
            rc("flush_mapped", vg_lite_flush_mapped_buffer(b));
        } else if (!strcmp(cmd, "load")) {
            int id = rd_i();
            char file[512];
            FILE *f;
#ifdef G2D_BAREMETAL
            (void)id;
            (void)f;
            fprintf(stderr, "load is not available on bare metal\n");
            return 2;
#endif
            if (scanf("%511s", file) != 1 || !(f = fopen(file, "rb")))
                return 2;
            if (fread(g_buf[id].memory, 1, (size_t)g_buf[id].stride * (size_t)g_buf[id].height, f) == 0) {
                fclose(f);
                return 2;
            }
            fclose(f);
            rc("flush_mapped", vg_lite_flush_mapped_buffer(&g_buf[id]));
        } else if (!strcmp(cmd, "img")) {
            int id = rd_i();
            g_buf[id].image_mode = (vg_lite_image_mode_t)rd_i();
            g_buf[id].transparency_mode = (vg_lite_transparency_t)rd_i();
            g_buf[id].apply_premult = (vg_lite_uint8_t)rd_i();
        } else if (!strcmp(cmd, "endian")) {
            int id = rd_i();
            g_buf[id].index_endian = (vg_lite_index_endian_t)rd_i();
        } else if (!strcmp(cmd, "clut")) {
            uint32_t pal[256], n = rd_u(), i;
            for (i = 0; i < n && i < 256u; i++) pal[i] = rd_u();
            rc("clut", vg_lite_set_CLUT(n, pal));
        } else if (!strcmp(cmd, "dither")) {
            rc("dither", rd_i() ? vg_lite_enable_dither() : vg_lite_disable_dither());
        } else if (!strcmp(cmd, "pcmode")) {
            rc("pcmode", vg_lite_g2d_set_pattern_color_mode((vg_lite_g2d_pattern_color_mode_t)rd_i()));
        } else if (!strcmp(cmd, "gpaint")) {
            g_gpaint = rd_u();
        } else if (!strcmp(cmd, "clear")) {
            int id = rd_i();
            vg_lite_rectangle_t r;
            r.x = rd_i(); r.y = rd_i(); r.width = rd_i(); r.height = rd_i();
            rc("clear", vg_lite_clear(&g_buf[id], r.width < 0 ? NULL : &r, rd_u()));
        } else if (!strcmp(cmd, "scissor")) {
            int x = rd_i(), y = rd_i(), r = rd_i(), b = rd_i();
            rc("scissor", vg_lite_set_scissor(x, y, r, b));
        } else if (!strcmp(cmd, "mirror")) {           /* mirror 0|1: vg_lite_set_mirror TOP_BOTTOM / BOTTOM_TOP */
            int v = rd_i();
            rc("set_mirror", vg_lite_set_mirror(v ? VG_LITE_ORIENTATION_BOTTOM_TOP : VG_LITE_ORIENTATION_TOP_BOTTOM));
        } else if (!strcmp(cmd, "pmatrix")) {          /* pmatrix m0..m19 ea er eg eb: vg_lite_set_pixel_matrix (v1.6b) */
            vg_lite_pixel_matrix_t pm;
            vg_lite_pixel_channel_enable_t ce;
            int k;
            for (k = 0; k < 20; k++)
                pm[k] = (vg_lite_float_t)rd_d();
            ce.enable_a = (vg_lite_uint8_t)rd_i();
            ce.enable_r = (vg_lite_uint8_t)rd_i();
            ce.enable_g = (vg_lite_uint8_t)rd_i();
            ce.enable_b = (vg_lite_uint8_t)rd_i();
            rc("pixel_matrix", vg_lite_set_pixel_matrix(pm, &ce));
        } else if (!strcmp(cmd, "ctrans")) {           /* ctrans as ab rs rb gs gb bs bb: vg_lite_set_color_transform */
            vg_lite_color_transform_t ct;
            ct.a_scale = (vg_lite_float_t)rd_d(); ct.a_bias = (vg_lite_float_t)rd_d();
            ct.r_scale = (vg_lite_float_t)rd_d(); ct.r_bias = (vg_lite_float_t)rd_d();
            ct.g_scale = (vg_lite_float_t)rd_d(); ct.g_bias = (vg_lite_float_t)rd_d();
            ct.b_scale = (vg_lite_float_t)rd_d(); ct.b_bias = (vg_lite_float_t)rd_d();
            rc("color_transform", vg_lite_set_color_transform(&ct));
        } else if (!strcmp(cmd, "cton")) {
            rc("color_transform_on", rd_i() ? vg_lite_enable_color_transform() : vg_lite_disable_color_transform());
        } else if (!strcmp(cmd, "gamma")) {            /* gamma 0|1|2: vg_lite_set_gamma */
            rc("gamma", vg_lite_set_gamma((vg_lite_gamma_conversion_t)rd_i()));
        } else if (!strcmp(cmd, "gauss")) {            /* gauss w0 w1 w2: vg_lite_gaussian_filter (v1.6b) */
            double w0 = rd_d(), w1 = rd_d();
            rc("gaussian_filter", vg_lite_gaussian_filter((vg_lite_float_t)w0, (vg_lite_float_t)w1, (vg_lite_float_t)rd_d()));
        } else if (!strcmp(cmd, "bfunc")) {            /* bfunc fsa fsc fda fdc eq scm dcm: vg_lite_blend_func (v1.6) */
            uint32_t a[7];
            int i;
            for (i = 0; i < 7; i++)
                a[i] = rd_u();
            rc("blend_func", vg_lite_blend_func(a[0], a[1], a[2], a[3], a[4], a[5], a[6]));
        } else if (!strcmp(cmd, "mcreate")) {          /* mask layers (v1.6): mcreate id w h */
            int id = rd_i(), w = rd_i(), h = rd_i();
            rc("create_masklayer", vg_lite_create_masklayer(&g_buf[id], (vg_lite_uint32_t)w, (vg_lite_uint32_t)h));
            printf("buf %d %u %d\n", id, (unsigned)g_buf[id].address, (int)g_buf[id].stride);
        } else if (!strcmp(cmd, "mfill")) {            /* mfill id x y w h value (w < 0: whole layer) */
            int id = rd_i();
            vg_lite_rectangle_t r;
            int v;
            r.x = rd_i(); r.y = rd_i(); r.width = rd_i(); r.height = rd_i();
            v = rd_i();
            rc("fill_masklayer", vg_lite_fill_masklayer(&g_buf[id], r.width < 0 ? NULL : &r, (vg_lite_uint8_t)v));
        } else if (!strcmp(cmd, "mblend")) {           /* mblend dst src op x y w h */
            int d = rd_i(), s = rd_i(), op = rd_i();
            vg_lite_rectangle_t r;
            r.x = rd_i(); r.y = rd_i(); r.width = rd_i(); r.height = rd_i();
            rc("blend_masklayer", vg_lite_blend_masklayer(&g_buf[d], &g_buf[s], (vg_lite_mask_operation_t)op,
                                                          r.width < 0 ? NULL : &r));
        } else if (!strcmp(cmd, "mrender")) {          /* mrender id op fmt quality fill color m path */
            int id = rd_i(), op = rd_i();
            vg_lite_format_t fmt = (vg_lite_format_t)rd_i();
            vg_lite_quality_t q = (vg_lite_quality_t)rd_i();
            vg_lite_fill_t fill = (vg_lite_fill_t)rd_i();
            uint32_t color = rd_u();
            vg_lite_matrix_t m;
            vg_lite_path_t p;
            void *data;
            uint32_t bytes;
            rd_m(&m);
            data = rd_path(fmt, &bytes);
            rc("init_path", vg_lite_init_path(&p, fmt, q, bytes, data, 0, 0, 0, 0));
            rc("render_masklayer", vg_lite_render_masklayer(&g_buf[id], (vg_lite_mask_operation_t)op, &p, fill, color, &m));
            rc("finish", vg_lite_finish());
            vg_lite_clear_path(&p);
            free(data);
        } else if (!strcmp(cmd, "mset")) {             /* mset id x y */
            int id = rd_i(), x = rd_i(), y = rd_i();
            rc("set_masklayer", vg_lite_set_masklayer(&g_buf[id], x, y));
        } else if (!strcmp(cmd, "mon")) {
            rc("enable_masklayer", vg_lite_enable_masklayer());
        } else if (!strcmp(cmd, "moff")) {
            rc("disable_masklayer", vg_lite_disable_masklayer());
        } else if (!strcmp(cmd, "srects")) {            /* srects n (x y w h)*n: vg_lite_scissor_rects (v1.6) */
            vg_lite_rectangle_t r[64];
            int n = rd_i(), i;
            for (i = 0; i < n && i < 64; i++) {
                r[i].x = rd_i(); r[i].y = rd_i(); r[i].width = rd_i(); r[i].height = rd_i();
            }
            rc("scissor_rects", vg_lite_scissor_rects(&g_buf[0], (vg_lite_uint32_t)n, n ? r : NULL));
        } else if (!strcmp(cmd, "noscissor")) {
            rc("noscissor", vg_lite_disable_scissor());
        } else if (!strcmp(cmd, "salpha")) {
            int m = rd_i();
            rc("salpha", vg_lite_source_global_alpha((vg_lite_global_alpha_t)m, (vg_lite_uint8_t)rd_i()));
        } else if (!strcmp(cmd, "dalpha")) {
            int m = rd_i();
            rc("dalpha", vg_lite_dest_global_alpha((vg_lite_global_alpha_t)m, (vg_lite_uint8_t)rd_i()));
        } else if (!strcmp(cmd, "ckey")) {
            vg_lite_color_key4_t k;
            int i;
            for (i = 0; i < 4; i++) {
                k[i].enable = (vg_lite_uint8_t)rd_i(); k[i].low_r = (vg_lite_uint8_t)rd_i();
                k[i].low_g = (vg_lite_uint8_t)rd_i(); k[i].low_b = (vg_lite_uint8_t)rd_i();
                k[i].alpha = (vg_lite_uint8_t)rd_i(); k[i].high_r = (vg_lite_uint8_t)rd_i();
                k[i].high_g = (vg_lite_uint8_t)rd_i(); k[i].high_b = (vg_lite_uint8_t)rd_i();
            }
            rc("ckey", vg_lite_set_color_key(k));
        } else if (!strcmp(cmd, "blit")) {
            int t = rd_i(), s = rd_i(), blend = rd_i();
            uint32_t color = rd_u();
            int filter = rd_i();
            vg_lite_matrix_t m;
            int ch;
            rd_m(&m);
            while ((ch = getchar()) == ' ' || ch == '\t') {}
            if (ch != '\n' && ch != EOF) {
                vg_lite_rectangle_t r;
                ungetc(ch, stdin);
                r.x = rd_i(); r.y = rd_i(); r.width = rd_i(); r.height = rd_i();
                rc("blit_rect", vg_lite_blit_rect(&g_buf[t], &g_buf[s], &r, &m, (vg_lite_blend_t)blend, color,
                                                  (vg_lite_filter_t)filter));
            } else {
                rc("blit", vg_lite_blit(&g_buf[t], &g_buf[s], &m, (vg_lite_blend_t)blend, color, (vg_lite_filter_t)filter));
            }
        } else if (!strcmp(cmd, "copy")) {
            int t = rd_i(), s = rd_i(), dx = rd_i(), dy = rd_i(), sx = rd_i(), sy = rd_i(), w = rd_i(), h = rd_i();
            rc("copy", vg_lite_copy_image(&g_buf[t], &g_buf[s], dx, dy, sx, sy, (vg_lite_uint32_t)w, (vg_lite_uint32_t)h));
        } else if (!strcmp(cmd, "stroke") || !strcmp(cmd, "dstroke")) {
            /* dstroke: stroke plus "npat pat... phase reset" after the miter limit (dashes, rendering.md §9.7.1) */
            int t = rd_i();
            vg_lite_format_t fmt = (vg_lite_format_t)rd_i();
            vg_lite_quality_t q = (vg_lite_quality_t)rd_i();
            vg_lite_blend_t blend = (vg_lite_blend_t)rd_i();
            vg_lite_path_type_t type = (vg_lite_path_type_t)rd_i();
            uint32_t color = rd_u(), scolor = rd_u();
            vg_lite_cap_style_t capst = (vg_lite_cap_style_t)rd_i();
            vg_lite_join_style_t joinst = (vg_lite_join_style_t)rd_i();
            double width = rd_d(), miter = rd_d(), phase = 0.0;
            vg_lite_float_t pat[16];
            int npat = 0, reset = 0, i;
            vg_lite_matrix_t m;
            vg_lite_path_t p;
            void *data;
            uint32_t bytes;
            if (cmd[0] == 'd') {
                npat = rd_i();
                if (npat < 0 || npat > 16)
                    exit(2);
                for (i = 0; i < npat; i++)
                    pat[i] = (vg_lite_float_t)rd_d();
                phase = rd_d();
                reset = rd_i();
            }
            rd_m(&m);
            data = rd_path(fmt, &bytes);
            rc("init_path", vg_lite_init_path(&p, fmt, q, bytes, data, 0, 0, 0, 0));
            rc("set_path_type", vg_lite_set_path_type(&p, type));
            rc("set_stroke", vg_lite_set_stroke(&p, capst, joinst, (vg_lite_float_t)width, (vg_lite_float_t)miter,
                                                npat ? pat : NULL, (vg_lite_uint32_t)npat, (vg_lite_float_t)phase, scolor));
            if (p.stroke)
                p.stroke->dash_reset = (vg_lite_uint8_t)reset;
            rc("update_stroke", vg_lite_update_stroke(&p));
            rc("draw", vg_lite_draw(&g_buf[t], &p, VG_LITE_FILL_NON_ZERO, &m, blend, color));
            rc("finish", vg_lite_finish());
            vg_lite_clear_path(&p);
            free(data);
        } else if (!strcmp(cmd, "pattern")) {
            int t = rd_i(), src = rd_i();
            vg_lite_format_t fmt = (vg_lite_format_t)rd_i();
            vg_lite_quality_t q = (vg_lite_quality_t)rd_i();
            vg_lite_fill_t fill = (vg_lite_fill_t)rd_i();
            vg_lite_blend_t blend = (vg_lite_blend_t)rd_i();
            vg_lite_pattern_mode_t mode = (vg_lite_pattern_mode_t)rd_i();
            uint32_t pcolor = rd_u(), color = rd_u();
            vg_lite_filter_t filter = (vg_lite_filter_t)rd_i();
            vg_lite_matrix_t m, pm;
            vg_lite_path_t p;
            void *data;
            uint32_t bytes;
            rd_m(&m);
            rd_m(&pm);
            data = rd_path(fmt, &bytes);
            rc("init_path", vg_lite_init_path(&p, fmt, q, bytes, data, 0, 0, 0, 0));
            rc("draw_pattern", vg_lite_draw_pattern(&g_buf[t], &p, fill, &m, &g_buf[src], &pm, blend, mode, pcolor,
                                                    color, filter));
            vg_lite_clear_path(&p);
            free(data);
        } else if (!strcmp(cmd, "path") || !strcmp(cmd, "lgrad") || !strcmp(cmd, "xgrad") || !strcmp(cmd, "rgrad")) {
            int t = rd_i();
            vg_lite_format_t fmt = (vg_lite_format_t)rd_i();
            vg_lite_quality_t q = (vg_lite_quality_t)rd_i();
            vg_lite_fill_t fill = (vg_lite_fill_t)rd_i();
            vg_lite_blend_t blend = (vg_lite_blend_t)rd_i();
            vg_lite_matrix_t m, gm;
            vg_lite_path_t p;
            void *data;
            uint32_t bytes;
            if (!strcmp(cmd, "path")) {
                uint32_t color = rd_u();
                rd_m(&m);
                data = rd_path(fmt, &bytes);
                rc("init_path", vg_lite_init_path(&p, fmt, q, bytes, data, 0, 0, 0, 0));
                rc("draw", vg_lite_draw(&g_buf[t], &p, fill, &m, blend, color));
            } else if (!strcmp(cmd, "lgrad")) {
                vg_lite_linear_gradient_t g;
                uint32_t colors[16], stops[16];
                int k, i;
                rd_m(&m);
                rd_m(&gm);
                k = rd_i();
                for (i = 0; i < k; i++) colors[i] = rd_u();
                for (i = 0; i < k; i++) stops[i] = rd_u();
                data = rd_path(fmt, &bytes);
                rc("init_path", vg_lite_init_path(&p, fmt, q, bytes, data, 0, 0, 0, 0));
                rc("init_grad", vg_lite_init_grad(&g));
                rc("set_grad", vg_lite_set_grad(&g, (vg_lite_uint32_t)k, colors, stops));
                *vg_lite_get_grad_matrix(&g) = gm;
                rc("update_grad", vg_lite_update_grad(&g));
                rc("draw_grad", vg_lite_draw_grad(&g_buf[t], &p, fill, &m, &g, blend));
                rc("finish", vg_lite_finish());
                rc("clear_grad", vg_lite_clear_grad(&g));
            } else if (!strcmp(cmd, "rgrad")) {
                /* rgrad t fmt q fill blend spread premul m gm cx cy r fx fy k (stop r g b a)*k path */
                vg_lite_radial_gradient_t g;
                vg_lite_color_ramp_t ramp[256];
                vg_lite_radial_gradient_parameter_t rp;
                int spread = rd_i(), premul = rd_i(), k, i;
                rd_m(&m);
                rd_m(&gm);
                rp.cx = (vg_lite_float_t)rd_d(); rp.cy = (vg_lite_float_t)rd_d(); rp.r = (vg_lite_float_t)rd_d();
                rp.fx = (vg_lite_float_t)rd_d(); rp.fy = (vg_lite_float_t)rd_d();
                k = rd_i();
                for (i = 0; i < k; i++) {
                    ramp[i].stop = (vg_lite_float_t)rd_d(); ramp[i].red = (vg_lite_float_t)rd_d();
                    ramp[i].green = (vg_lite_float_t)rd_d(); ramp[i].blue = (vg_lite_float_t)rd_d();
                    ramp[i].alpha = (vg_lite_float_t)rd_d();
                }
                data = rd_path(fmt, &bytes);
                memset(&g, 0, sizeof g);
                rc("init_path", vg_lite_init_path(&p, fmt, q, bytes, data, 0, 0, 0, 0));
                rc("set_radial_grad", vg_lite_set_radial_grad(&g, (vg_lite_uint32_t)k, ramp, rp,
                                                              (vg_lite_gradient_spreadmode_t)spread, (vg_lite_uint8_t)premul));
                *vg_lite_get_radial_grad_matrix(&g) = gm;
                rc("update_radial_grad", vg_lite_update_radial_grad(&g));
                rc("draw_radial_grad", vg_lite_draw_radial_grad(&g_buf[t], &p, fill, &m, &g, g_gpaint, blend, VG_LITE_FILTER_LINEAR));
                rc("finish", vg_lite_finish());
                rc("clear_radial_grad", vg_lite_clear_radial_grad(&g));
            } else {
                vg_lite_ext_linear_gradient_t g;
                vg_lite_color_ramp_t ramp[256];
                vg_lite_linear_gradient_parameter_t lp;
                int spread = rd_i(), premul = rd_i(), k, i;
                rd_m(&m);
                rd_m(&gm);
                lp.X0 = (vg_lite_float_t)rd_d(); lp.Y0 = (vg_lite_float_t)rd_d();
                lp.X1 = (vg_lite_float_t)rd_d(); lp.Y1 = (vg_lite_float_t)rd_d();
                k = rd_i();
                for (i = 0; i < k; i++) {
                    ramp[i].stop = (vg_lite_float_t)rd_d(); ramp[i].red = (vg_lite_float_t)rd_d();
                    ramp[i].green = (vg_lite_float_t)rd_d(); ramp[i].blue = (vg_lite_float_t)rd_d();
                    ramp[i].alpha = (vg_lite_float_t)rd_d();
                }
                data = rd_path(fmt, &bytes);
                memset(&g, 0, sizeof g);
                rc("init_path", vg_lite_init_path(&p, fmt, q, bytes, data, 0, 0, 0, 0));
                rc("set_linear_grad", vg_lite_set_linear_grad(&g, (vg_lite_uint32_t)k, ramp, lp,
                                                              (vg_lite_gradient_spreadmode_t)spread, (vg_lite_uint8_t)premul));
                *vg_lite_get_linear_grad_matrix(&g) = gm;
                rc("update_linear_grad", vg_lite_update_linear_grad(&g));
                rc("draw_linear_grad", vg_lite_draw_linear_grad(&g_buf[t], &p, fill, &m, &g, g_gpaint, blend, VG_LITE_FILTER_POINT));
                rc("finish", vg_lite_finish());
                rc("clear_linear_grad", vg_lite_clear_linear_grad(&g));
            }
            rc("finish", vg_lite_finish());   /* the path data is ours: no draw may still need it */
            vg_lite_clear_path(&p);
            free(data);
        } else if (!strcmp(cmd, "yuvload")) {          /* yuvload ID S0 S1 S2 F0 F1 F2: vg_lite_upload_buffer (v1.6b) */
            int id = rd_i(), k;
            vg_lite_uint32_t st[3];
            vg_lite_uint8_t *data[3] = {NULL, NULL, NULL};
            char file[512];
            for (k = 0; k < 3; k++)
                st[k] = (vg_lite_uint32_t)rd_i();
            for (k = 0; k < 3; k++) {
                FILE *f;
                long n;
                if (scanf("%511s", file) != 1)
                    return 2;
                if (!strcmp(file, "-"))
                    continue;
                if (!(f = fopen(file, "rb")))
                    return 2;
                fseek(f, 0, SEEK_END);
                n = ftell(f);
                fseek(f, 0, SEEK_SET);
                data[k] = (vg_lite_uint8_t *)malloc((size_t)n + 1u);
                if (!data[k] || fread(data[k], 1, (size_t)n, f) != (size_t)n) {
                    fclose(f);
                    return 2;
                }
                fclose(f);
            }
            rc("upload", vg_lite_upload_buffer(&g_buf[id], data, st));
            for (k = 0; k < 3; k++)
                free(data[k]);
        } else if (!strcmp(cmd, "yuvfmt")) {           /* yuvfmt ID STD SWZ: yuv.yuv2rgb, yuv.swizzle (v1.6b) */
            int id = rd_i(), std = rd_i();
            g_buf[id].yuv.yuv2rgb = std ? VG_LITE_YUV709 : VG_LITE_YUV601;
            g_buf[id].yuv.swizzle = rd_i() ? VG_LITE_SWIZZLE_VU : VG_LITE_SWIZZLE_UV;
        } else if (!strcmp(cmd, "finish")) {
            rc("finish", vg_lite_finish());
        } else if (!strcmp(cmd, "dump")) {
            int id = rd_i();
            char file[512];
            FILE *f;
            if (scanf("%511s", file) != 1)
                return 2;
#ifdef G2D_BAREMETAL
            (void)f;
            printf("crc %d %08x\n", id,
                   (unsigned)crc32_bytes((const uint8_t *)g_buf[id].memory,
                                         (size_t)g_buf[id].stride * (size_t)g_buf[id].height));
            continue;
#endif
            f = fopen(file, "wb");
            if (!f)
                return 2;
            fwrite(g_buf[id].memory, 1, (size_t)g_buf[id].stride * (size_t)g_buf[id].height, f);
            fclose(f);
        } else {
            fprintf(stderr, "unknown command %s\n", cmd);
            return 2;
        }
    }
#ifdef G2D_BAREMETAL
    {
        int i;
        for (i = 0; i < MAXBUF; i++)
            if (g_buf[i].handle || g_buf[i].memory)
                vg_lite_free(&g_buf[i]);
        memset(g_buf, 0, sizeof g_buf);
    }
#endif
    rc("close", vg_lite_close());
    return 0;
}
