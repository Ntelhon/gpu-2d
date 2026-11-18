/* SPDX-License-Identifier: GPL-2.0-or-later
 *
 * DOOM on G2D, host harness (step D1): doomgeneric + doom_g2d.c on our vg_lite driver with the golden-model HAL.
 * The clock is virtual (DG_SleepMs advances it, every frame adds one tic), so a run is deterministic: from the
 * start DOOM shows its title and plays the WAD's demos without input.
 *
 * After every present the frame on screen is checked against a CPU-only present of the same screen and palette:
 * palette entry -> BGR565 with the rendering.md §3.4 rounding, point sampling of the 2 x 2.4 scale
 * (source column x / 2, row floor((y + 0.5) / 2.4) = (2y + 1) * 5 / 24, never on a boundary).
 *
 * Environment: DOOM_FRAMES (frames to run, default 300), DOOM_CRC (file for one "frame crc32" line per frame),
 * DOOM_CHECK (0: skip the CPU comparison), DOOM_SNAP ("<frame>:<file.ppm>": that frame as a PPM image). Arguments are passed to DOOM (e.g. -iwad freedoom1.wad).
 * Exit status: 0 after DOOM_FRAMES frames, 2 on a mismatch.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "doom_g2d.h"
#include "doomgeneric.h"

static uint32_t g_ms;            /* virtual milliseconds */
static unsigned long g_max = 300;
static int g_check = 1;
static FILE *g_crc;
static unsigned long g_snap = ~0ul;
static const char *g_snap_file;

uint32_t DG_GetTicksMs(void) { return g_ms; }
void DG_SleepMs(uint32_t ms) { g_ms += ms; }
int DG_GetKey(int *pressed, unsigned char *key) { (void)pressed; (void)key; return 0; }

void doom_plat_show(const vg_lite_buffer_t *fb) { (void)fb; }
static uint32_t g_vsyncs;
uint32_t doom_plat_frame_count(void) { return ++g_vsyncs; }   /* no display: every query sees a new frame */
void doom_plat_screen_written(const vg_lite_buffer_t *screen) { (void)screen; }

static uint32_t crc32(const uint8_t *p, size_t n)
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

static uint16_t bgr565(uint32_t argb)   /* rendering.md §3.4: round(c * (2^n - 1) / 255) */
{
    uint32_t r = (argb >> 16) & 0xFFu, g = (argb >> 8) & 0xFFu, b = argb & 0xFFu;
    return (uint16_t)((((r * 31u + 127u) / 255u) << 11) | (((g * 63u + 127u) / 255u) << 5) | ((b * 31u + 127u) / 255u));
}

void doom_plat_frame_done(void)
{
    const vg_lite_buffer_t *fb = doom_g2d_front(), *scr = doom_g2d_screen();
    const uint32_t *clut = doom_g2d_clut();
    const uint8_t *s = (const uint8_t *)scr->memory;
    unsigned long n = doom_g2d_frames();
    int x, y;
    g_ms += 1000u / 35u + 1u;   /* one tic per frame */
    for (y = 0; y < FB_H; y++) {
        const uint16_t *row = (const uint16_t *)((const uint8_t *)fb->memory + (size_t)y * fb->stride);
        if (g_check) {
            const int sy = (2 * y + 1) * 5 / 24;
            for (x = 0; x < FB_W; x++) {
                const uint16_t want = bgr565(clut[s[sy * DOOM_W + x / 2]]);
                if (row[x] != want) {
                    printf("MISMATCH frame %lu pixel (%d, %d): g2d %04x cpu %04x (index %u)\n", n, x, y, row[x], want,
                           s[sy * DOOM_W + x / 2]);
                    exit(2);
                }
            }
        }
    }
    if (g_crc && fb->stride == FB_W * 2)   /* CRC of the whole frame (unpadded rows) */
        fprintf(g_crc, "%lu %08x\n", n, crc32((const uint8_t *)fb->memory, (size_t)FB_W * FB_H * 2u));
    if (n == g_snap) {
        FILE *f = fopen(g_snap_file, "wb");
        if (f) {
            fprintf(f, "P6\n%d %d\n255\n", FB_W, FB_H);
            for (y = 0; y < FB_H; y++)
                for (x = 0; x < FB_W; x++) {
                    const uint16_t p = ((const uint16_t *)((const uint8_t *)fb->memory + (size_t)y * fb->stride))[x];
                    const unsigned char rgb[3] = {(unsigned char)((p >> 11) * 255 / 31),
                                                  (unsigned char)(((p >> 5) & 63) * 255 / 63),
                                                  (unsigned char)((p & 31) * 255 / 31)};
                    fwrite(rgb, 1, 3, f);
                }
            fclose(f);
        }
    }
    if (n >= g_max) {
        printf("doom_host: %lu frames, %s\n", n, g_check ? "every frame bit-exact against the CPU present" : "unchecked");
        if (g_crc)
            fclose(g_crc);
        exit(0);
    }
}

int main(int argc, char **argv)
{
    const char *v;
    if ((v = getenv("DOOM_FRAMES")) != NULL)
        g_max = strtoul(v, NULL, 0);
    if ((v = getenv("DOOM_CHECK")) != NULL)
        g_check = atoi(v);
    if ((v = getenv("DOOM_SNAP")) != NULL && strchr(v, ':')) {
        g_snap = strtoul(v, NULL, 0);
        g_snap_file = strchr(v, ':') + 1;
    }
    if ((v = getenv("DOOM_CRC")) != NULL && !(g_crc = fopen(v, "w"))) {
        perror(v);
        return 1;
    }
    doomgeneric_Create(argc, argv);
    for (;;)
        doomgeneric_Tick();
}
