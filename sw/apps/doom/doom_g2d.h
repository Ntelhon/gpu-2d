/* SPDX-License-Identifier: GPL-2.0-or-later
 * DOOM on G2D: interface between the video layer (doom_g2d.c) and the target part (host harness / bare metal). */
#ifndef DOOM_G2D_H
#define DOOM_G2D_H

#include <stdint.h>

#include "vg_lite.h"

#define DOOM_W 320
#define DOOM_H 200
#define FB_W 640
#define FB_H 480

/* video layer state, for the target part and the tests */
const vg_lite_buffer_t *doom_g2d_front(void);    /* the buffer on screen after the last DG_DrawFrame */
const vg_lite_buffer_t *doom_g2d_screen(void);   /* DOOM's 8-bit screen (the G2D source) */
const uint32_t *doom_g2d_clut(void);             /* the CLUT of the last present (ARGB) */
unsigned long doom_g2d_frames(void);

/* Board start-up diagnosis (DOOM_TRACE=1 builds): step n written to DOOM_CRUMB_ADDR and the D-cache flushed, so
 * OpenOCD can read how far start-up got (mdw 0x8fff0000) even when the CPU no longer halts. No-op otherwise. */
#ifndef DOOM_TRACE
#define DOOM_TRACE 0
#endif
#define DOOM_CRUMB_ADDR 0x8FFF0000u
#if DOOM_TRACE
#define DOOM_CRUMB(n)                                                          \
    do {                                                                       \
        *(volatile uint64_t *)(uintptr_t)DOOM_CRUMB_ADDR = (uint64_t)(n);      \
        __asm__ volatile("fence" ::: "memory");                                \
    } while (0)
#define DOOM_CRUMB_VAL(i, v)                                                    \
    do {                                                                        \
        *(volatile uint64_t *)(uintptr_t)(DOOM_CRUMB_ADDR + 8u * (i)) = (uint64_t)(v); \
        __asm__ volatile("fence" ::: "memory");                                 \
    } while (0)
#else
#define DOOM_CRUMB(n) do {} while (0)
#define DOOM_CRUMB_VAL(i, v) do {} while (0)
#endif

/* VGA diagnosis (board, 2026-11-05): 0 normal; 1 = no flips (the GPU still blits every frame into a buffer that is
 * not shown: memory load without start-address changes); 2 = no present at all (no GPU load, picture frozen) */
extern int doom_diag_mode;

/* target hooks */
void doom_plat_show(const vg_lite_buffer_t *fb);              /* make fb the scanned-out buffer */
uint32_t doom_plat_frame_count(void);                          /* vsync pulses so far (display frames) */
void doom_plat_screen_written(const vg_lite_buffer_t *screen); /* CPU wrote the screen: make it GPU visible */
void doom_plat_frame_done(void);                               /* after every present (stats, test checks) */

#endif
