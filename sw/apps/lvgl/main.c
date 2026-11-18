// SPDX-License-Identifier: Apache-2.0
/* LVGL host demo on the G2D stack (Phase 6): renders frames of an LVGL demo with simulated time and dumps
 * them, so the VG-Lite build (our vg_lite driver on the software backend) and the software-renderer
 * build produce comparable screenshots (sw/tests/test_lvgl.py), and the recorder (G2D_RECORD) captures
 * the GPU command stream for replay on the RTL (SYS-06).
 *
 *   lvgl_demo DEMO W H FRAMES OUT_PREFIX DUMP_EVERY
 *     DEMO: widgets | benchmark | vector | formats;  dumps OUT_PREFIX_<frame>.raw every DUMP_EVERY frames and at the end
 *   G2D_FB_INFO=<file>: writes the frame buffer's address, stride and size (v1.2, FPGA VGA demo)
 *
 * Time is simulated (lv_tick_set_cb): every frame advances 33 ms, so both builds see identical states.
 * The VG-Lite build passes pointers as GPU addresses (LVGL's lv_vg_lite_buffer_from_draw_buf), so the
 * program is linked non-PIE and the software backend runs in identity mode with malloc kept off mmap:
 * every buffer the GPU reads or writes lies below 4 GB. */
#include <malloc.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lvgl.h"
#include "demos/lv_demos.h"
#if LV_USE_DRAW_VG_LITE
#include "vg_lite.h"
#include "vg_lite_g2d.h"
#endif

/* Live RTL backend only (dv/live): GPU performance counters; weak default elsewhere. */
__attribute__((weak)) int g2d_hal_perf(uint64_t out[8]) { (void)out; return 0; }

static uint32_t g_ms;
static uint32_t tick_cb(void) { return g_ms; }

static uint8_t *g_frame;      /* last flushed frame, tightly packed */
static int32_t g_w, g_h;
static uint32_t g_bpp;

static void flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *px)
{
    int32_t y, w = lv_area_get_width(area);
    uint32_t stride = lv_draw_buf_width_to_stride((uint32_t)g_w, lv_display_get_color_format(disp));
    /* full render mode: px is the whole frame with the draw buffer's stride */
    for (y = area->y1; y <= area->y2; y++)
        memcpy(g_frame + ((size_t)y * (size_t)g_w + (size_t)area->x1) * g_bpp,
               px + (size_t)(y - area->y1) * stride, (size_t)w * g_bpp);
    lv_display_flush_ready(disp);
}

static void dump(const char *prefix, int frame)
{
    char name[512];
    FILE *f;
    snprintf(name, sizeof name, "%s_%04d.raw", prefix, frame);
    f = fopen(name, "wb");
    if (!f) {
        perror(name);
        exit(2);
    }
    fwrite(g_frame, g_bpp, (size_t)g_w * (size_t)g_h, f);
    fclose(f);
}


/* "formats": one image per source format the G2D driver converts on the CPU (A4, ARGB1555, ARGB4444,
 * ARGB2222; rendering.md §9.6) and an ARGB8888 control, drawn 1:1 and scaled by 1.5 on a grey background,
 * so the VG-Lite build can be compared with LVGL's software renderer decoding the same bytes. */
#define FMT_W 48
#define FMT_H 40
static uint8_t g_fmt_data[5][FMT_H * FMT_W * 4] __attribute__((aligned(64)));
static lv_image_dsc_t g_fmt_dsc[5];

static void formats_demo(void)
{
    static const lv_color_format_t cfs[5] = {LV_COLOR_FORMAT_A4, LV_COLOR_FORMAT_ARGB1555, LV_COLOR_FORMAT_ARGB4444,
                                             LV_COLOR_FORMAT_ARGB2222, LV_COLOR_FORMAT_ARGB8888};
    lv_obj_t *scr = lv_screen_active();
    uint32_t s = 0x12345u;
    int k, x, y;
    lv_obj_set_style_bg_color(scr, lv_color_hex(0x8090a0), 0);
    for (k = 0; k < 5; k++) {
        uint32_t bpp = lv_color_format_get_bpp(cfs[k]), stride = (FMT_W * bpp + 7) / 8;
        uint8_t *d = g_fmt_data[k];
        for (y = 0; y < FMT_H; y++)
            for (x = 0; x < (int)stride; x++) {
                /* smooth gradients plus noise, alpha ramp across the image */
                s ^= s << 13; s ^= s >> 17; s ^= s << 5;
                d[y * stride + x] = (uint8_t)((x * 5 + y * 3) ^ (s >> 28));
            }
        lv_image_dsc_t *dsc = &g_fmt_dsc[k];
        memset(dsc, 0, sizeof *dsc);
        dsc->header.magic = LV_IMAGE_HEADER_MAGIC;
        dsc->header.cf = cfs[k];
        dsc->header.w = FMT_W;
        dsc->header.h = FMT_H;
        dsc->header.stride = stride;
        dsc->data = d;
        dsc->data_size = stride * FMT_H;
        lv_obj_t *img = lv_image_create(scr);
        lv_image_set_src(img, dsc);
        lv_obj_set_pos(img, 8 + k * 60, 8);
        if (cfs[k] == LV_COLOR_FORMAT_A4)
            lv_obj_set_style_image_recolor_opa(img, LV_OPA_COVER, 0), lv_obj_set_style_image_recolor(img, lv_color_hex(0xffd040), 0);
        lv_obj_t *big = lv_image_create(scr);
        lv_image_set_src(big, dsc);
        lv_image_set_scale(big, 384);
        lv_obj_set_pos(big, 8 + k * 60, 80);
        if (cfs[k] == LV_COLOR_FORMAT_A4)
            lv_obj_set_style_image_recolor_opa(big, LV_OPA_COVER, 0), lv_obj_set_style_image_recolor(big, lv_color_hex(0x40d0ff), 0);
    }
}

int main(int argc, char **argv)
{
    int frames, every, i;
    lv_display_t *disp;
    void *buf;
    uint32_t stride, size;
    if (argc != 7) {
        fprintf(stderr, "usage: %s widgets|benchmark|vector|formats W H FRAMES OUT_PREFIX DUMP_EVERY\n", argv[0]);
        return 2;
    }
    g_w = atoi(argv[2]);
    g_h = atoi(argv[3]);
    frames = atoi(argv[4]);
    every = atoi(argv[6]);
    mallopt(M_MMAP_THRESHOLD, 1 << 30);    /* heap blocks stay in the brk heap, below 4 GB */
#if LV_USE_DRAW_VG_LITE
    setenv("G2D_MODEL_IDENTITY", "1", 1);
    if (vg_lite_init(0, 0) != VG_LITE_SUCCESS) {
        fprintf(stderr, "vg_lite_init failed\n");
        return 1;
    }
    /* v1.4: COLOR-pattern colour as a plain colour (rendering.md §7.5 BORDER_SOLID; default: texel) */
#if !defined(G2D_LV_TVG)   /* our driver only (not LVGL's vg_lite_tvg simulator) */
    if (getenv("G2D_PATTERN_COLOR_SOLID"))
        vg_lite_g2d_set_pattern_color_mode(VG_LITE_G2D_PATTERN_COLOR_SOLID);
#endif
#endif
    lv_init();
    lv_tick_set_cb(tick_cb);
    disp = lv_display_create(g_w, g_h);
    g_bpp = lv_color_format_get_size(lv_display_get_color_format(disp));
    stride = lv_draw_buf_width_to_stride((uint32_t)g_w, lv_display_get_color_format(disp));
    size = stride * (uint32_t)g_h;
    buf = aligned_alloc(LV_DRAW_BUF_ALIGN, (size + LV_DRAW_BUF_ALIGN - 1) / LV_DRAW_BUF_ALIGN * LV_DRAW_BUF_ALIGN);
    g_frame = calloc((size_t)g_w * (size_t)g_h, g_bpp);
    if (!buf || !g_frame || (uintptr_t)buf + size > ((uint64_t)1 << 32)) {
        fprintf(stderr, "frame buffer not below 4 GB\n");
        return 1;
    }
    lv_display_set_buffers(disp, buf, NULL, size, LV_DISPLAY_RENDER_MODE_FULL);
    if (getenv("G2D_FB_INFO")) {   /* v1.2: where the frame lives (GPU address in identity mode), for the VGA demo */
        FILE *fi = fopen(getenv("G2D_FB_INFO"), "w");
        if (fi) {
            fprintf(fi, "addr=0x%08lx stride=%u w=%d h=%d bpp=%u\n", (unsigned long)(uintptr_t)buf, (unsigned)stride,
                    (int)g_w, (int)g_h, (unsigned)g_bpp);
            fclose(fi);
        }
    }
    lv_display_set_flush_cb(disp, flush_cb);

    if (!strcmp(argv[1], "widgets"))
        lv_demo_widgets();
    else if (!strcmp(argv[1], "benchmark"))
        lv_demo_benchmark();
    else if (!strcmp(argv[1], "formats"))
        formats_demo();
    else if (!strcmp(argv[1], "vector"))
        lv_demo_vector_graphic_not_buffered();   /* lv_vector API: strokes, gradients, images, arcs */
    else {
        fprintf(stderr, "unknown demo %s\n", argv[1]);
        return 2;
    }
    FILE *stats = getenv("G2D_FRAME_STATS") ? fopen(getenv("G2D_FRAME_STATS"), "w") : NULL;
    uint64_t prev[8] = {0}, cur[8];
    if (stats)
        fprintf(stats, "frame busy_cycles draws pixels_out rd_beats wr_beats rd_stall wr_stall\n");
    for (i = 1; i <= frames; i++) {
        g_ms += 33;
        lv_timer_handler();
        lv_refr_now(disp);
#if LV_USE_DRAW_VG_LITE
        if (stats && (vg_lite_finish(), g2d_hal_perf(cur))) {   /* per-frame GPU work (live RTL backend) */
            int k;
            fprintf(stats, "%d", i);
            for (k = 0; k < 7; k++)
                fprintf(stats, " %llu", (unsigned long long)((cur[k] - prev[k]) & 0xFFFFFFFFull));
            fprintf(stats, "\n");
            for (k = 0; k < 8; k++)
                prev[k] = cur[k];
        }
#endif
        if ((every > 0 && i % every == 0) || i == frames)
            dump(argv[5], i);
    }
    if (stats)
        fclose(stats);
    lv_deinit();
#if LV_USE_DRAW_VG_LITE
    vg_lite_close();
#endif
    printf("rendered %d frames\n", frames);
    return 0;
}
