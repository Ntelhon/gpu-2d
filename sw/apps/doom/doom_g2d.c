/* SPDX-License-Identifier: GPL-2.0-or-later
 *
 * DOOM on G2D: the doomgeneric platform layer's video part (side project DOOM, docs/PHASES.md, GPU level L2).
 * doomgeneric is built with CMAP256 (8-bit screen, 320 x 200): its screen buffer is a G2D source buffer of format
 * INDEX_8, DOOM's palette is the CLUT, and one blit scales the screen by 2 x 2.4 (point sampled) into a 640 x 480
 * BGR565 back buffer (axi_vga's pixel order: B in bits 4:0, R in bits 15:11), which then becomes the front buffer.
 * The CPU does no per-pixel work for the present.
 *
 * The target-specific part (host test harness or bare metal on Cheshire) provides DG_GetTicksMs, DG_SleepMs,
 * DG_GetKey and the doom_plat_* hooks of doom_g2d.h.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "doom_g2d.h"
#include "doomgeneric.h"
#include "i_video.h"   /* CMAP256: colors[] (DOOM's palette, gamma applied), palette_changed */
#include "vg_lite.h"

static vg_lite_buffer_t g_src;            /* DOOM's screen, INDEX_8 */
#define NFB 3
static vg_lite_buffer_t g_fb[NFB];        /* BGR565 */
static uint32_t g_left_at[NFB];           /* frame count when the buffer stopped being the one shown */
static int g_front;                       /* index of the buffer on screen */
static vg_lite_matrix_t g_mat;
static uint32_t g_clut[256];
static unsigned long g_frames;
int doom_diag_mode;

static void die(const char *what, vg_lite_error_t e)
{
    printf("doom_g2d: %s failed (%d)\n", what, (int)e);
    exit(1);
}

const vg_lite_buffer_t *doom_g2d_front(void) { return &g_fb[g_front]; }
const vg_lite_buffer_t *doom_g2d_screen(void) { return &g_src; }
const uint32_t *doom_g2d_clut(void) { return g_clut; }
unsigned long doom_g2d_frames(void) { return g_frames; }

void DG_Init(void)
{
    vg_lite_error_t e;
    int i;
    printf("doom_g2d: vg_lite_init\n");
    DOOM_CRUMB(10);
    if ((e = vg_lite_init(0, 0)) != VG_LITE_SUCCESS)
        die("vg_lite_init", e);
    memset(&g_src, 0, sizeof g_src);
    g_src.width = DOOM_W;
    g_src.height = DOOM_H;
    g_src.format = VG_LITE_INDEX_8;
    if ((e = vg_lite_allocate(&g_src)) != VG_LITE_SUCCESS)
        die("vg_lite_allocate (screen)", e);
    if (g_src.stride != DOOM_W) {      /* doomgeneric copies its rows contiguously */
        printf("doom_g2d: screen stride %d != %d\n", (int)g_src.stride, DOOM_W);
        exit(1);
    }
    /* doomgeneric_Create allocated its own buffer before DG_Init: DOOM draws into the GPU source instead */
    free(DG_ScreenBuffer);
    DG_ScreenBuffer = (pixel_t *)g_src.memory;
    memset(g_src.memory, 0, (size_t)DOOM_W * DOOM_H);
    for (i = 0; i < NFB; i++) {
        memset(&g_fb[i], 0, sizeof g_fb[i]);
        g_fb[i].width = FB_W;
        g_fb[i].height = FB_H;
        g_fb[i].format = VG_LITE_BGR565;
        if ((e = vg_lite_allocate(&g_fb[i])) != VG_LITE_SUCCESS)
            die("vg_lite_allocate (frame buffer)", e);
        if ((e = vg_lite_clear(&g_fb[i], NULL, 0xFF000000u)) != VG_LITE_SUCCESS)
            die("vg_lite_clear", e);
    }
    printf("doom_g2d: buffers allocated, clearing\n");
    DOOM_CRUMB(11);
    if ((e = vg_lite_finish()) != VG_LITE_SUCCESS)
        die("vg_lite_finish", e);
    DOOM_CRUMB(12);
    printf("doom_g2d: video ready (%d frame buffers at 0x%08x..)\n", NFB, (unsigned)g_fb[0].address);
    /* 320 x 200 -> 640 x 480: x2 horizontally, x2.4 vertically (DOOM's 4:3 pixel aspect) */
    vg_lite_identity(&g_mat);
    vg_lite_scale((vg_lite_float_t)FB_W / DOOM_W, (vg_lite_float_t)FB_H / DOOM_H, &g_mat);
    g_front = 0;
    doom_plat_show(&g_fb[0]);
}

void DG_DrawFrame(void)
{
    vg_lite_error_t e;
    if (palette_changed || g_frames == 0) {
        int i;
        for (i = 0; i < 256; i++)
            g_clut[i] = 0xFF000000u | ((uint32_t)colors[i].r << 16) | ((uint32_t)colors[i].g << 8) | colors[i].b;
        if ((e = vg_lite_set_CLUT(256, g_clut)) != VG_LITE_SUCCESS)
            die("vg_lite_set_CLUT", e);
        palette_changed = false;
    }
    const int back = (g_front + 1) % NFB;
    if (doom_diag_mode == 2) {          /* diagnosis: no present (no GPU load) */
        g_frames++;
        doom_plat_frame_done();
        return;
    }
    doom_plat_screen_written(&g_src);   /* bare metal: write the CPU's screen bytes back for the GPU */
    while ((int32_t)(doom_plat_frame_count() - g_left_at[back]) <= 0)
        ;                               /* still scanned out (normally free: replaced two flips ago) */
    if ((e = vg_lite_blit(&g_fb[back], &g_src, &g_mat, VG_LITE_BLEND_NONE, 0, VG_LITE_FILTER_POINT)) !=
        VG_LITE_SUCCESS)
        die("vg_lite_blit", e);
    if ((e = vg_lite_finish()) != VG_LITE_SUCCESS)
        die("vg_lite_finish", e);
    if (doom_diag_mode != 1) {          /* diagnosis mode 1: blit, but keep showing the same buffer */
        doom_plat_show(&g_fb[back]);
        g_left_at[g_front] = doom_plat_frame_count();
        g_front = back;
    }
    g_frames++;
    doom_plat_frame_done();
}

void DG_SetWindowTitle(const char *title) { (void)title; }
