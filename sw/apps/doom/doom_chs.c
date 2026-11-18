/* SPDX-License-Identifier: GPL-2.0-or-later
 *
 * DOOM on G2D, bare metal on Cheshire (Genesys2), step D4: the target part of the doomgeneric platform layer.
 *   time      CLINT mtime (RTC, CHS_REGS rtc_freq Hz)
 *   keyboard  chs_ps2 (USB keyboard in the Genesys2 HID port, PS/2 set 2, polled) through doom_keys.c; UART keys
 *             as a fallback (no key-up over a terminal: a key is released DOOM_UART_HOLD_MS after its last byte):
 *             arrows (ESC [ A..D) or w a s d move / turn, f or Ctrl fire, e or space use, , . strafe, Enter, Esc,
 *             y n, 1..7 weapons, Tab map
 *   video     axi_vga scans out the G2D frame buffer (640 x 480 BGR565, 60 Hz); chs_ps2's vsync counter tells when a
 *             flipped-out buffer is free again
 *   LLC       ways configured as scratchpad are switched to cache before DOOM starts (printed)
 *   stats     fps and the share of time in the present every DOOM_STATS_FRAMES frames over the UART
 * The WAD is in DRAM (doom_syscalls.c); the program arguments are fixed (-iwad doom.wad, plus DOOM_ARGS).
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dif/clint.h"
#include "dif/uart.h"
#include "params.h"
#include "regs/cheshire.h"
#include "util.h"

#include "doom_g2d.h"
#include "doom_keys.h"
#include "doomgeneric.h"
#include "doomkeys.h"

#ifndef DOOM_UART_BAUD
#define DOOM_UART_BAUD 115200u
#endif
#ifndef DOOM_HAVE_PS2          /* 1: the DOOM bitstream (chs_ps2 at DOOM_PS2_BASE); 0: standard bitstream, UART keys only
                                  (the address is unmapped there: an access would fault) */
#define DOOM_HAVE_PS2 1
#endif
#ifndef DOOM_PS2_BASE
#define DOOM_PS2_BASE 0x43001000u
#endif
#ifndef DOOM_STATS_FRAMES
#define DOOM_STATS_FRAMES 175u
#endif
#ifndef DOOM_UART_HOLD_MS
#define DOOM_UART_HOLD_MS 150u
#endif
#ifndef DOOM_LLC_CACHE        /* 1: switch scratchpad ways of the LLC to cache before DOOM starts */
#define DOOM_LLC_CACHE 1
#endif
#ifndef DOOM_MAX_FRAMES       /* SoC simulation: stop after this many frames (0 = run forever) */
#define DOOM_MAX_FRAMES 0u
#endif

extern void *__uart_base_addr__, *__vga_base_addr__;
#define LLC_CFG_BASE 0x03001000u   /* __llc_base_addr__ (Cheshire sw/link/cheshire_addrs.ldh) */
uint32_t g2d_rtc_hz;            /* used by the bare-metal HAL's timeouts */

static uint64_t g_rtc;          /* RTC ticks per second */

static uint64_t now_ticks(void) { return clint_get_mtime(); }

uint32_t DG_GetTicksMs(void) { return (uint32_t)(now_ticks() * 1000u / g_rtc); }

void DG_SleepMs(uint32_t ms) { clint_spin_until(now_ticks() + (uint64_t)ms * g_rtc / 1000u); }

/* ------------------------------------------------------------------------------------------------ chs_ps2 */
static volatile uint32_t *const ps2 = (volatile uint32_t *)(uintptr_t)DOOM_PS2_BASE;
static int g_have_ps2;

uint32_t doom_plat_frame_count(void)
{
    static uint32_t fake;
    return g_have_ps2 ? ps2[2] : ++fake;   /* without chs_ps2 (standard bitstream): never wait */
}

/* ------------------------------------------------------------------------------------------------ keys */
#define KQ 64
static struct { unsigned char pressed, key; } g_kq[KQ];
static unsigned g_kq_r, g_kq_w;
static doom_keys_t g_ps2_dec;

static void diag_toggle(int mode)
{
    doom_diag_mode = (doom_diag_mode == mode) ? 0 : mode;
    printf("DIAG mode %d (%s)\r\n", doom_diag_mode,
           doom_diag_mode == 1 ? "no flips, GPU still blits" : doom_diag_mode == 2 ? "no present, no GPU load" : "normal");
}

static void kq_push(int pressed, unsigned char key)
{
    /* VGA diagnosis keys, not passed to DOOM: F9 = mode 1, F10 = mode 2 (press again: normal) */
    if (key == KEY_F9 || key == KEY_F10) {
        if (pressed)
            diag_toggle(key == KEY_F9 ? 1 : 2);
        return;
    }
    if (g_kq_w - g_kq_r < KQ) {
        g_kq[g_kq_w % KQ].pressed = (unsigned char)pressed;
        g_kq[g_kq_w % KQ].key = key;
        g_kq_w++;
    }
}

/* UART fallback: one held key at a time, released after DOOM_UART_HOLD_MS without a repeat */
static unsigned char g_uart_key;
static uint32_t g_uart_until;
static int g_esc;               /* 0, 1 = ESC seen, 2 = ESC [ seen */
static uint32_t g_esc_at;

static unsigned char uart_map(unsigned char c)
{
    switch (c) {
    case 'w': return KEY_UPARROW;
    case 's': return KEY_DOWNARROW;
    case 'a': return KEY_LEFTARROW;
    case 'd': return KEY_RIGHTARROW;
    case 'f': return KEY_FIRE;
    case 'e': case ' ': return KEY_USE;
    case ',': return KEY_STRAFE_L;
    case '.': return KEY_STRAFE_R;
    case '\r': case '\n': return KEY_ENTER;
    case '\t': return KEY_TAB;
    case 'y': case 'n': case '1': case '2': case '3': case '4': case '5': case '6': case '7': return c;
    default: return 0;
    }
}

static void uart_hold(unsigned char k, uint32_t t)
{
    if (!k)
        return;
    if (g_uart_key && g_uart_key != k)
        kq_push(0, g_uart_key);
    if (g_uart_key != k)
        kq_push(1, k);
    g_uart_key = k;
    g_uart_until = t + DOOM_UART_HOLD_MS;
}

static void poll_input(void)
{
    const uint32_t t = DG_GetTicksMs();
    int pressed;
    unsigned char key;
    if (g_have_ps2) {
        for (;;) {
            const uint32_t d = ps2[0];
            if (!(d & 0x100u))
                break;
            if (doom_keys_feed(&g_ps2_dec, (unsigned char)d, &pressed, &key))
                kq_push(pressed, key);
        }
    }
    while (uart_read_ready(&__uart_base_addr__)) {
        const unsigned char c = uart_read(&__uart_base_addr__);
        if (g_esc == 1 && c == '[') {
            g_esc = 2;
        } else if (g_esc == 2) {
            g_esc = 0;
            uart_hold(c == 'A' ? KEY_UPARROW : c == 'B' ? KEY_DOWNARROW : c == 'C' ? KEY_RIGHTARROW :
                      c == 'D' ? KEY_LEFTARROW : 0, t);
        } else if (c == 0x1B) {
            g_esc = 1;
            g_esc_at = t;
        } else if (c == 'z' || c == 'x') {
            g_esc = 0;
            diag_toggle(c == 'z' ? 1 : 2);   /* UART: z = F9, x = F10 */
        } else {
            g_esc = 0;
            uart_hold(uart_map(c), t);
        }
    }
    if (g_esc == 1 && (int32_t)(t - g_esc_at) > 50) {   /* a lone ESC: the menu key */
        g_esc = 0;
        kq_push(1, KEY_ESCAPE);
        kq_push(0, KEY_ESCAPE);
    }
    if (g_uart_key && (int32_t)(t - g_uart_until) > 0) {
        kq_push(0, g_uart_key);
        g_uart_key = 0;
    }
}

int DG_GetKey(int *pressed, unsigned char *key)
{
    if (g_kq_r == g_kq_w)
        poll_input();
    if (g_kq_r == g_kq_w)
        return 0;
    *pressed = g_kq[g_kq_r % KQ].pressed;
    *key = g_kq[g_kq_r % KQ].key;
    g_kq_r++;
#if DOOM_MAX_FRAMES > 0
    printf("DOOM_KEY %c%02x\r\n", *pressed ? '+' : '-', *key);   /* SoC simulation: keys reach the game */
#endif
    return 1;
}

/* ------------------------------------------------------------------------------------------------ video */
/* axi_vga registers (Cheshire sw/include/regs/axi_vga.h offsets) */
#define VGA_CONTROL 0x00u
#define VGA_CLK_DIV 0x04u
#define VGA_H_VISIBLE 0x08u
#define VGA_H_FRONT 0x0Cu
#define VGA_H_SYNC 0x10u
#define VGA_H_BACK 0x14u
#define VGA_V_VISIBLE 0x18u
#define VGA_V_FRONT 0x1Cu
#define VGA_V_SYNC 0x20u
#define VGA_V_BACK 0x24u
#define VGA_START_LO 0x28u
#define VGA_START_HI 0x2Cu
#define VGA_FRAME_SIZE 0x30u
#define VGA_BURST_LEN 0x34u

static void vga_wr(uint32_t off, uint32_t v) { *(volatile uint32_t *)((uintptr_t)&__vga_base_addr__ + off) = v; }
static int g_vga_on;

void doom_plat_show(const vg_lite_buffer_t *fb)
{
    if (!g_vga_on) {   /* 640 x 480 at ~59.5 Hz: pixel clock SoC / 2, VESA porches, negative syncs (as g2d_fpga_test) */
        vga_wr(VGA_CONTROL, 0);
        vga_wr(VGA_CLK_DIV, 2);
        vga_wr(VGA_H_VISIBLE, FB_W);
        vga_wr(VGA_H_FRONT, 16);
        vga_wr(VGA_H_SYNC, 96);
        vga_wr(VGA_H_BACK, 48);
        vga_wr(VGA_V_VISIBLE, FB_H);
        vga_wr(VGA_V_FRONT, 10);
        vga_wr(VGA_V_SYNC, 2);
        vga_wr(VGA_V_BACK, 33);
        vga_wr(VGA_START_LO, (uint32_t)fb->address);
        vga_wr(VGA_START_HI, 0);
        vga_wr(VGA_FRAME_SIZE, (uint32_t)fb->stride * FB_H);
        /* axi_vga's fetcher issues its next burst only after the last beat of the previous one: one burst is ever
         * in flight, so the burst length (not the FIFO depth) bounds the latency it can bridge. 16 beats (~2.5 us
         * at 25 MHz RGB565) underran under DOOM's load and the picture slid; 256 beats (2 KB, ~40 us) fill the
         * 256-beat FIFO of the VgaBufferDepth = 256 bitstreams (2026-11-05). */
        vga_wr(VGA_BURST_LEN, 255);
        vga_wr(VGA_CONTROL, 1);
        g_vga_on = 1;
        return;
    }
    vga_wr(VGA_START_LO, (uint32_t)fb->address);   /* taken at axi_vga's next frame */
}

void doom_plat_screen_written(const vg_lite_buffer_t *screen)
{
    (void)screen;
    __asm__ volatile("fence" ::: "memory");   /* CVA6 write-back D-cache: flushed on fence (sw/hal/baremetal) */
}

static uint64_t g_t0, g_present;

#if DOOM_MAX_FRAMES > 0
/* SoC simulation end: the frame G2D (RTL) presented against a CPU present of the same screen and palette
 * (rendering.md §3.4 rounding, 2 x 2.4 point sampling, as the host harness), its CRC for the VGA pin capture, then
 * the testbench trigger (DOOM_VGA_TRIGGER), two display frames of hold and exit. */
#define DOOM_VGA_TRIGGER 0x90000000u
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

static void sim_stop(unsigned long n)
{
    const vg_lite_buffer_t *fb = doom_g2d_front(), *scr = doom_g2d_screen();
    const uint32_t *clut = doom_g2d_clut();
    const uint8_t *s = (const uint8_t *)scr->memory;
    long bad = -1;
    int x, y;
    __asm__ volatile("fence" ::: "memory");   /* write back + invalidate the D-cache: read what G2D wrote */
    for (y = 0; y < FB_H && bad < 0; y++) {
        const uint16_t *row = (const uint16_t *)((const uint8_t *)fb->memory + (size_t)y * fb->stride);
        const int sy = (2 * y + 1) * 5 / 24;
        for (x = 0; x < FB_W; x++) {
            const uint32_t c = clut[s[sy * DOOM_W + x / 2]];
            const uint32_t r = (c >> 16) & 0xFFu, g = (c >> 8) & 0xFFu, b = c & 0xFFu;
            const uint16_t want = (uint16_t)((((r * 31u + 127u) / 255u) << 11) | (((g * 63u + 127u) / 255u) << 5) |
                                             ((b * 31u + 127u) / 255u));
            if (row[x] != want) {
                bad = (long)y * FB_W + x;
                printf("DOOM_CHECK mismatch at (%d, %d): g2d %04x cpu %04x\r\n", x, y, row[x], want);
                break;
            }
        }
    }
    printf("DOOM_CHECK frames=%lu fb=%08x crc=%08x cpu_present=%s\r\n", n, (unsigned)fb->address,
           (unsigned)crc32((const uint8_t *)fb->memory, (size_t)fb->stride * FB_H), bad < 0 ? "MATCH" : "MISMATCH");
    *(volatile uint32_t *)(uintptr_t)DOOM_VGA_TRIGGER = 1;
    __asm__ volatile("fence" ::: "memory");
    DG_SleepMs(60);
    uart_write_flush(&__uart_base_addr__);
    exit(bad < 0 ? 0 : 1);
}
#endif

/* v1.8 (Q-FPGA-11): debug counters of the axi_vga fork (fpga/cheshire/axi_vga_g2d, window at 0x40), printed with the
 * fps line and cleared. A bitstream without them aliases 0x54 onto the vendor's H_BACK register: INFO[31:16] (burst
 * beats) is 0 there, so nothing is printed. */
#define VGA_DBG_UNDERRUN_PX 0x40u
#define VGA_DBG_INFO 0x54u
static uint32_t vga_rd(uint32_t off) { return *(volatile uint32_t *)((uintptr_t)&__vga_base_addr__ + off); }

static void vga_stats(void)
{
    const uint32_t info = vga_rd(VGA_DBG_INFO);
    if (!g_vga_on || (info >> 16) == 0)
        return;
    printf("VGA: underrun px %lu in %lu of %lu frames, min fill %lu / %lu beats, max latency %lu clk\r\n",
           (unsigned long)vga_rd(VGA_DBG_UNDERRUN_PX), (unsigned long)vga_rd(VGA_DBG_UNDERRUN_PX + 4u),
           (unsigned long)vga_rd(VGA_DBG_UNDERRUN_PX + 16u), (unsigned long)vga_rd(VGA_DBG_UNDERRUN_PX + 8u),
           (unsigned long)(info & 0xFFFFu), (unsigned long)vga_rd(VGA_DBG_UNDERRUN_PX + 12u));
    vga_wr(VGA_DBG_UNDERRUN_PX, 0);   /* clear all counters */
}

void doom_plat_frame_done(void)
{
    const unsigned long n = doom_g2d_frames();
    if (n % DOOM_STATS_FRAMES == 0) {
        const uint64_t t = now_ticks();
        if (g_t0) {
            const uint64_t dt = t - g_t0;
            printf("DOOM frames %lu: %lu.%02lu fps\r\n", n, (unsigned long)(DOOM_STATS_FRAMES * g_rtc / dt),
                   (unsigned long)(DOOM_STATS_FRAMES * g_rtc * 100u / dt % 100u));
            vga_stats();
        }
        g_t0 = t;
    }
    (void)g_present;
#if DOOM_MAX_FRAMES > 0
    if (n >= DOOM_MAX_FRAMES)
        sim_stop(n);
#endif
}

/* ------------------------------------------------------------------------------------------------ start */
static void llc_to_cache(void)
{
    volatile uint32_t *llc = (volatile uint32_t *)(uintptr_t)LLC_CFG_BASE;
    const uint32_t lo = llc[0], hi = llc[1];   /* CFG_SPM_LOW / HIGH: one bit per way configured as scratchpad */
    printf("LLC: scratchpad ways %08x%08x", (unsigned)hi, (unsigned)lo);
    if (lo | hi) {
        llc[0] = 0;
        llc[1] = 0;
        llc[4] = 1;                            /* COMMIT_CFG: the LLC flushes the affected ways itself and stalls
                                                  accesses meanwhile (axi_llc_config) */
        printf(" -> cache (now %08x%08x)", (unsigned)llc[1], (unsigned)llc[0]);
    }
    printf("\r\n");
}

int main(void)
{
    static char *argv[] = {"doom", "-iwad", "doom.wad", NULL};
    uint64_t core_hz;
    DOOM_CRUMB(1);
    g2d_rtc_hz = CHS_REGS->rtc_freq.f.ref_freq;
    DOOM_CRUMB_VAL(1, g2d_rtc_hz);
    DOOM_CRUMB(2);
    g_rtc = g2d_rtc_hz;
    core_hz = clint_get_core_freq(g2d_rtc_hz, 2500);
    DOOM_CRUMB_VAL(2, core_hz);
    DOOM_CRUMB(3);
    uart_init(&__uart_base_addr__, (core_hz + 50000u) / 100000u * 100000u, DOOM_UART_BAUD);
    DOOM_CRUMB(4);
#if DOOM_TRACE
    *(volatile uint8_t *)(uintptr_t)&__uart_base_addr__ = 'U';   /* raw THR write, no printf / newlib */
    *(volatile uint8_t *)(uintptr_t)&__uart_base_addr__ = '\r';
    *(volatile uint8_t *)(uintptr_t)&__uart_base_addr__ = '\n';
    DOOM_CRUMB(5);
#endif
    printf("\r\nDOOM on Cheshire + G2D: core %lu Hz, RTC %lu Hz\r\n", (unsigned long)core_hz, (unsigned long)g_rtc);
    DOOM_CRUMB(6);
    DOOM_CRUMB(7);
    g_have_ps2 = DOOM_HAVE_PS2 && ps2[3] == 0x50320001u;
    DOOM_CRUMB(8);
    printf("keyboard: %s; UART keys: w a s d / arrows, f fire, e use, Enter, Esc\r\n",
           g_have_ps2 ? "PS/2 (Genesys2 USB HID port)" : "none (chs_ps2 not in this bitstream)");
    doom_keys_reset(&g_ps2_dec);
    if (DOOM_LLC_CACHE)
        llc_to_cache();
    DOOM_CRUMB(9);
    doomgeneric_Create(3, argv);
    for (;;)
        doomgeneric_Tick();
}
