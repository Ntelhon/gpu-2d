// SPDX-License-Identifier: Apache-2.0
/* G2D bare-metal test on Cheshire (Phase 7): runs the vgl_scene scripts of scripts/fpga_scenes.py (linked in as
 * g2d_scenes[]) through the unchanged vg_lite driver and the bare-metal HAL, and prints every return code and a
 * CRC-32 of every dumped buffer on the UART. scripts/fpga_check.py compares the log with the same scripts on the
 * software backend (golden model), line by line.
 *
 * UART protocol (one item per line):
 *   G2D_BOOT core_hz=<measured CPU clock>   (printed before the first G2D register access)
 *   G2D_TEST_BEGIN <n scenes> id=<ID register> hwcfg=<HWCFG register>
 *   SCENE <k> <name>                then the vgl_scene output of that script: "rc <call> <code>",
 *                                   "buf <id> <gpu address> <stride>", "crc <id> <crc32>"
 *   SCENE_END <k> ret=<return value of the script runner> busy=<PERF_BUSY_CYCLES> draws=<PERF_DRAWS>
 *   IRQ_TEST plic=<pending before claim> claim=<claimed source> ok=<0|1>
 *   LVGL <name> kicks=<n> busy=<PERF_BUSY_CYCLES> err=<ERR_STATUS> pages=<n> bytes=<n> crc=<crc32>
 *                                   (v1.2) replay of a recorded LVGL frame (scripts/fpga_lvgl.py, g2d_lvgl_rec[]):
 *                                   CRC-32 over the GPU-written bytes of every CHECK page
 *   VGA_ON addr=<frame buffer> w=<w> h=<h> crc=<crc32 of the frame buffer> hold_ms=<n>
 *                                   (v1.2) Cheshire's axi_vga scans the LVGL frame out (640x480 RGB565, 60 Hz from the
 *                                   SoC clock / 2) for hold_ms, then is switched off until the end of the test; the
 *                                   SoC sim captures it from the pins (scripts/vga_check.py), the board shows it
 *   VGA_FINAL on                    after G2D_TEST_END: the frame stays on the monitor
 *   LAT_PROBE d=<extra DRAM delay> busy=<min>/<max> n=<repeats>
 *                                   (v1.2) busy cycles of a NOP-only command buffer: one command fetch round trip
 *                                   to DRAM (the LLC is all scratchpad after Cheshire's boot ROM) + fixed overhead
 *   SWEEP <N> ... SWEEP_END <N>     (v1.2) the scene set again with Cheshire's DRAM delay stage
 *                                   (USE_RAM_DELAY, cfg registers at G2D_CHS_CFG_BASE) at N cycles on AR and AW,
 *                                   each step with its LAT_PROBE; N from g2d_sweep_delays[] (fpga_scenes.py
 *                                   --sweep; empty list = no sweep)
 *   G2D_TEST_END fails=<script runner failures>
 *
 * Build: Makefile target fpga-sw (Cheshire's crt0 / dram.ld / UART driver from CHS_ROOT, newlib, SiFive GCC).
 *   G2D_UART_BAUD   UART rate (default 115200; the SoC simulation uses a faster rate)
 *   G2D_POOL_ADDR / G2D_POOL_BYTES   GPU memory pool (sw/hal/baremetal)
 *   G2D_VGA_HOLD_MS how long the VGA shows the LVGL frame before the test continues (board default 5000; the SoC sim
 *                   build uses 40, two frames)
 *   G2D_PROBE_REPEATS latency probe repeats (default 8; the SoC simulation build uses 2)
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "dif/clint.h"
#include "dif/uart.h"
#include "params.h"
#include "regs/cheshire.h"
#include "util.h"

#include "g2d_hal.h"
#include "g2d_regs.h"

#ifndef G2D_CSR_BASE
#define G2D_CSR_BASE 0x48000000u   /* fpga/cheshire/g2d_chs_pkg.sv G2dCsrBase */
#endif
#ifndef G2D_UART_BAUD
#define G2D_UART_BAUD 115200
#endif
#ifndef G2D_CHS_CFG_BASE
#define G2D_CHS_CFG_BASE 0x43000000u   /* Cheshire Genesys2 cfg registers (target/xilinx chs_xilinx_regs.hjson) */
#endif
#define G2D_CHS_DRAM_AW_DELAY 0x0Cu
#define G2D_CHS_DRAM_AR_DELAY 0x18u
#ifndef G2D_VGA_HOLD_MS
#define G2D_VGA_HOLD_MS 5000u
#endif
#ifndef G2D_PROBE_REPEATS
#define G2D_PROBE_REPEATS 8
#endif

extern int vgl_scene_main(int argc, char **argv);
extern const char *const g2d_scenes[];
extern const char *const g2d_scene_names[];
extern const unsigned g2d_nscenes;
extern const uint8_t g2d_lvgl_rec[], g2d_lvgl_rec_end[];   /* scripts/fpga_lvgl.py (g2d_lvgl.S) */
extern const uint32_t g2d_lvgl_fb[5];                        /* frame buffer: address, stride, w, h, bytes/px */
extern const char g2d_lvgl_name[];
extern const unsigned g2d_sweep_delays[];
extern const unsigned g2d_nsweep;

/* Platform hooks and newlib syscalls: syscalls.c. */
extern const char *g2d_stdin;        /* stdin of the next script */
extern uint32_t g2d_rtc_hz;
void g2d_platform_cache_clean(const void *p, size_t n);
void g2d_platform_cache_invalidate(void *p, size_t n);
void g2d_platform_delay_us(uint32_t us);

/* ------------------------------------------------------------------------------------------ tests */

static uint32_t csr(uint32_t off) { return *(volatile uint32_t *)(uintptr_t)(G2D_CSR_BASE + off); }
static void csr_wr(uint32_t off, uint32_t v) { *(volatile uint32_t *)(uintptr_t)(G2D_CSR_BASE + off) = v; }

/* PLIC (Cheshire rv_plic, 59 sources, target 0 = hart 0 M mode): G2D is source G2D_PLIC_SRC. */
#ifndef G2D_PLIC_SRC
#define G2D_PLIC_SRC 58u
#endif
#define PLIC ((uintptr_t)&__plic_base_addr__)
static volatile uint32_t *plic_reg(uint32_t off) { return (volatile uint32_t *)(PLIC + off); }

/* The GPU interrupt reaches the PLIC: enable IRQ_ENABLE.IDLE, kick an empty (NOP) buffer, and claim it. */
static int irq_test(void)
{
    const g2d_hal_t *hal = g2d_hal_open();
    void *cpu;
    uint32_t gpu, pend, claim = 0;
    int ok, i;
    if (hal->mem_alloc(hal->ctx, 8, 64, &cpu, &gpu))
        return 0;
    ((volatile uint32_t *)cpu)[0] = 0;       /* NOP */
    ((volatile uint32_t *)cpu)[1] = 0;       /* NOP */
    g2d_platform_cache_clean(cpu, 8);
    *plic_reg(4u * G2D_PLIC_SRC) = 1;                                      /* priority 1 */
    *plic_reg(0x2000u + 4u * (G2D_PLIC_SRC / 32u)) |= 1u << (G2D_PLIC_SRC % 32u);  /* enable, target 0 */
    *plic_reg(0x200000u) = 0;                                              /* threshold 0 */
    csr_wr(G2D_CSR_IRQ_STATUS, 0xFFFFFFFFu);
    csr_wr(G2D_CSR_IRQ_ENABLE, G2D_CSR_IRQ_ENABLE_IDLE_MASK);
    csr_wr(G2D_CSR_CMD_ADDR, gpu);
    csr_wr(G2D_CSR_CMD_KICK, 8);
    for (i = 0; i < 1000 && !(csr(G2D_CSR_IRQ_STATUS) & G2D_CSR_IRQ_STATUS_IDLE_MASK); i++)
        g2d_platform_delay_us(10);
    g2d_platform_delay_us(10);                                             /* PLIC gateway */
    pend = (*plic_reg(0x1000u + 4u * (G2D_PLIC_SRC / 32u)) >> (G2D_PLIC_SRC % 32u)) & 1u;
    claim = *plic_reg(0x200004u);
    if (claim)
        *plic_reg(0x200004u) = claim;                                      /* complete */
    csr_wr(G2D_CSR_IRQ_STATUS, 0xFFFFFFFFu);
    csr_wr(G2D_CSR_IRQ_ENABLE, 0);
    hal->mem_free(hal->ctx, cpu, gpu);
    ok = pend == 1u && claim == G2D_PLIC_SRC;
    printf("IRQ_TEST plic=%u claim=%u ok=%d\n", (unsigned)pend, (unsigned)claim, ok);
    return ok;
}

/* Busy cycles of one NOP-only command buffer: the only memory access is the command fetch (one AXI read). */
static uint32_t nop_kick(uint32_t gpu)
{
    unsigned i;
    /* IRQ_STATUS.IDLE is sticky ("entered idle"), so a read that reaches G2D before the kick took effect cannot
     * end the wait early. */
    csr_wr(G2D_CSR_IRQ_STATUS, G2D_CSR_IRQ_STATUS_IDLE_MASK);
    csr_wr(G2D_CSR_PERF_CTRL, G2D_CSR_PERF_CTRL_EN_MASK | G2D_CSR_PERF_CTRL_CLEAR_MASK);
    csr_wr(G2D_CSR_CMD_ADDR, gpu);
    csr_wr(G2D_CSR_CMD_KICK, 8);
    for (i = 0; i < 100000u && !(csr(G2D_CSR_IRQ_STATUS) & G2D_CSR_IRQ_STATUS_IDLE_MASK); i++)
        ;
    csr_wr(G2D_CSR_IRQ_STATUS, G2D_CSR_IRQ_STATUS_IDLE_MASK);
    csr_wr(G2D_CSR_PERF_CTRL, G2D_CSR_PERF_CTRL_EN_MASK | G2D_CSR_PERF_CTRL_SNAPSHOT_MASK);
    return csr(G2D_CSR_PERF_BUSY_CYCLES);
}

/* Latency probe (Q-FPGA-4): busy cycles of a NOP-only command buffer, minimum and maximum over the repeats. Cheshire's
 * boot ROM puts every LLC way into scratchpad mode (cheshire_bootrom.S: "set to all SPM"), so DRAM accesses bypass the
 * LLC and the command fetch always pays the full DRAM round trip (crossbar, LLC bypass, delay stage, MIG / model). */
static void lat_probe(unsigned delay)
{
    const g2d_hal_t *hal = g2d_hal_open();
    uint32_t gpu, v, lo = ~0u, hi = 0;
    void *cpu;
    unsigned r;
    if (hal->mem_alloc(hal->ctx, 64, 64, &cpu, &gpu)) {
        printf("LAT_PROBE d=%u error=alloc\n", delay);
        return;
    }
    for (r = 0; r < G2D_PROBE_REPEATS; r++) {
        ((volatile uint32_t *)cpu)[0] = 0;
        ((volatile uint32_t *)cpu)[1] = 0;
        g2d_platform_cache_clean(cpu, 8);
        v = nop_kick(gpu);
        if (v < lo) lo = v;
        if (v > hi) hi = v;
    }
    hal->mem_free(hal->ctx, cpu, gpu);
    printf("LAT_PROBE d=%u busy=%u/%u n=%u\n", delay, (unsigned)lo, (unsigned)hi, (unsigned)G2D_PROBE_REPEATS);
}

/* ------------------------------------------------------------------------------ LVGL frame replay */

#define REC_MEM 1u
#define REC_KICK 2u
#define REC_SOFT_RESET 3u
#define REC_CHECK 4u
#define REC_POST 5u
#define REC_END 0xFFFFFFFFu

static uint32_t crc_tab[256];

static void crc_init(void)
{
    uint32_t i, c;
    int k;
    for (i = 0; i < 256; i++) {
        for (c = i, k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1u)));
        crc_tab[i] = c;
    }
}

static uint32_t rd32(const uint8_t *p) { return (uint32_t)p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }

/* 0 when idle with an empty kick queue, -1 on timeout, -2 when the GPU halted on an error (needs a soft reset). */
static int gpu_idle_wait(void)
{
    uint32_t depth = G2D_FIELD_GET(G2D_CSR_HWCFG_KICK_DEPTH, csr(G2D_CSR_HWCFG));
    unsigned i;
    for (i = 0; i < 2000000u; i++) {
        uint32_t st = csr(G2D_CSR_STATUS);
        if (st & G2D_CSR_STATUS_HALTED_MASK)
            return -2;
        if ((st & G2D_CSR_STATUS_IDLE_MASK) && G2D_FIELD_GET(G2D_CSR_STATUS_KICK_FREE, st) == depth)
            return 0;
        g2d_platform_delay_us(1);
    }
    return -1;
}

/* Soft reset as the driver does it (drv_core.c): kicks written while SOFT_RESET_BUSY could be lost. */
static int gpu_soft_reset(void)
{
    unsigned i;
    csr_wr(G2D_CSR_CTRL, G2D_CSR_CTRL_SOFT_RESET_MASK);
    for (i = 0; i < 100000u && (csr(G2D_CSR_STATUS) & G2D_CSR_STATUS_SOFT_RESET_BUSY_MASK); i++)
        g2d_platform_delay_us(1);
    csr_wr(G2D_CSR_IRQ_STATUS, 0xFFFFFFFFu);
    return (csr(G2D_CSR_STATUS) & G2D_CSR_STATUS_SOFT_RESET_BUSY_MASK) ? -1 : 0;
}

/* Replays the recording as test_sys06.py does on the RTL: MEM records are written in place (their addresses are
 * Cheshire DRAM, the demo was linked there), the GPU is idle before the CPU overwrites memory, kicks go to the kick
 * queue; at the end the GPU-written bytes of every CHECK page are CRC'd. */
static unsigned lvgl_replay(void)
{
    const uint8_t *p = g2d_lvgl_rec, *end = g2d_lvgl_rec_end;
    uint32_t crc = 0xFFFFFFFFu, bytes = 0, pages = 0, kicks = 0, mems = 0, err, busy;
    int dirty = 0, pending = 0, checked = 0, bad = 0;
    if (p == end)
        return 0;
    if (end - p < 12 || rd32(p) != 0x43455232u || rd32(p + 4) != 2u) {
        printf("LVGL %s error=format\n", g2d_lvgl_name);
        return 1;
    }
    p += 8;
    bad |= gpu_soft_reset();
    csr_wr(G2D_CSR_PERF_CTRL, G2D_CSR_PERF_CTRL_EN_MASK | G2D_CSR_PERF_CTRL_CLEAR_MASK);
    while (p + 4 <= end && !bad) {
        uint32_t k = rd32(p), a, n;
        p += 4;
        if (k == REC_END)
            break;
        if (k == REC_SOFT_RESET) {
            int w = gpu_idle_wait();
            bad |= w == -1 ? -1 : 0;       /* a halt before a recorded soft reset is what the recording expects */
            bad |= gpu_soft_reset();
            pending = 0;
            continue;
        }
        a = rd32(p);
        n = rd32(p + 4);
        p += 8;
        if (k == REC_MEM) {
            if (pending) {                   /* the CPU does not overwrite memory the GPU may still use */
                bad |= gpu_idle_wait();
                pending = 0;
            }
            memcpy((void *)(uintptr_t)a, p, n);
            dirty = 1;
            p += n;
            if ((++mems & 63u) == 0)
                printf("LVGL_PROGRESS mem=%u kicks=%u\n", (unsigned)mems, (unsigned)kicks);
        } else if (k == REC_KICK) {
            unsigned i;
            if (dirty) {
                g2d_platform_cache_clean((void *)(uintptr_t)a, n);   /* fence: writes back the whole D-cache */
                dirty = 0;
            }
            for (i = 0; i < 2000000u && !G2D_FIELD_GET(G2D_CSR_STATUS_KICK_FREE, csr(G2D_CSR_STATUS)); i++)
                g2d_platform_delay_us(1);
            csr_wr(G2D_CSR_CMD_ADDR, a);
            csr_wr(G2D_CSR_CMD_KICK, n);
            pending = 1;
            kicks++;
        } else if (k == REC_CHECK || k == REC_POST) {
            const uint8_t *m = p + n, *mem = (const uint8_t *)(uintptr_t)a;
            uint32_t i;
            if (k == REC_CHECK) {
                if (!checked) {
                    printf("LVGL_PROGRESS mem=%u kicks=%u check\n", (unsigned)mems, (unsigned)kicks);
                    bad |= gpu_idle_wait();
                    g2d_platform_cache_invalidate((void *)(uintptr_t)a, n);
                    checked = 1;
                }
                for (i = 0; i < n; i++)
                    if (m[i >> 3] >> (i & 7) & 1u) {
                        crc = crc_tab[(crc ^ mem[i]) & 0xFFu] ^ (crc >> 8);
                        bytes++;
                    }
                pages++;
            }
            p += n + n / 8;
        } else {
            printf("LVGL %s error=record%u\n", g2d_lvgl_name, (unsigned)k);
            return 1;
        }
    }
    bad |= gpu_idle_wait();
    csr_wr(G2D_CSR_PERF_CTRL, G2D_CSR_PERF_CTRL_EN_MASK | G2D_CSR_PERF_CTRL_SNAPSHOT_MASK);
    busy = csr(G2D_CSR_PERF_BUSY_CYCLES);
    err = csr(G2D_CSR_ERR_STATUS);
    printf("LVGL %s kicks=%u busy=%u err=%08x pages=%u bytes=%u crc=%08x%s\n", g2d_lvgl_name, (unsigned)kicks,
           (unsigned)busy, (unsigned)err, (unsigned)pages, (unsigned)bytes, (unsigned)~crc,
           bad == 0 ? "" : bad & 1 ? " error=timeout" : " error=halted");
    return bad || err ? 1u : 0u;
}

/* ------------------------------------------------------------------------------------------ VGA */

/* axi_vga registers (Cheshire sw/include/regs/axi_vga.h offsets; base __vga_base_addr__) */
#define VGA_CONTROL 0x00u      /* bit 0 enable, bit 1 hsync polarity (1 = active high), bit 2 vsync polarity */
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

/* 640x480 at ~59.5 Hz: pixel clock = SoC clock / 2 (25.0 MHz from 50 MHz; VESA 25.175 MHz), VESA porches, negative
 * syncs. The SoC sim's VGA capture (fpga/sim/tb_g2d_chs.sv) uses the same numbers. */
static void vga_show(uint32_t addr, uint32_t w, uint32_t h)
{
    vga_wr(VGA_CONTROL, 0);
    vga_wr(VGA_CLK_DIV, 2);
    vga_wr(VGA_H_VISIBLE, w);
    vga_wr(VGA_H_FRONT, 16);
    vga_wr(VGA_H_SYNC, 96);
    vga_wr(VGA_H_BACK, 48);
    vga_wr(VGA_V_VISIBLE, h);
    vga_wr(VGA_V_FRONT, 10);
    vga_wr(VGA_V_SYNC, 2);
    vga_wr(VGA_V_BACK, 33);
    vga_wr(VGA_START_LO, addr);
    vga_wr(VGA_START_HI, 0);
    vga_wr(VGA_FRAME_SIZE, w * h * 2u);
    vga_wr(VGA_BURST_LEN, 15);       /* 16-beat bursts (the fetcher splits them at 4 KB pages) */
    vga_wr(VGA_CONTROL, 1);
}

/* The LVGL frame on the VGA output when it is 640x480 RGB565 (the format axi_vga scans out). */
static int vga_test(int final)
{
    uint32_t addr = g2d_lvgl_fb[0], stride = g2d_lvgl_fb[1], w = g2d_lvgl_fb[2], h = g2d_lvgl_fb[3];
    uint32_t c = 0xFFFFFFFFu, i;
    const uint8_t *fb = (const uint8_t *)(uintptr_t)addr;
    if ((uintptr_t)g2d_lvgl_rec == (uintptr_t)g2d_lvgl_rec_end || w != 640u || h != 480u || g2d_lvgl_fb[4] != 2u || stride != w * 2u)
        return 0;
    if (final) {
        vga_show(addr, w, h);
        printf("VGA_FINAL on\n");
        return 0;
    }
    g2d_platform_cache_invalidate((void *)fb, stride * h);
    for (i = 0; i < stride * h; i++)
        c = crc_tab[(c ^ fb[i]) & 0xFFu] ^ (c >> 8);
    vga_show(addr, w, h);
    printf("VGA_ON addr=%08x w=%u h=%u crc=%08x hold_ms=%u\n", (unsigned)addr, (unsigned)w, (unsigned)h, (unsigned)~c,
           (unsigned)G2D_VGA_HOLD_MS);
    uart_write_flush(&__uart_base_addr__);
    for (i = 0; i < G2D_VGA_HOLD_MS; i++)
        g2d_platform_delay_us(1000);
    vga_wr(VGA_CONTROL, 0);
    return 0;
}

static unsigned run_scenes(void)
{
    char arg0[] = "vgl_scene";
    char *argv[] = {arg0, NULL};
    unsigned k, fails = 0;
    /* v1.8 debug registers (arch.md 9.1) exist from register map 0.3 on; an older bitstream answers them with a bus
     * error, so they are only touched when VERSION says they are there */
    const uint32_t ver = csr(G2D_CSR_VERSION);
    const int dbg = G2D_FPGA_DBG && (((ver & G2D_CSR_VERSION_MAJOR_MASK) >> G2D_CSR_VERSION_MAJOR_SHIFT) > 0u ||
                    ((ver & G2D_CSR_VERSION_MINOR_MASK) >> G2D_CSR_VERSION_MINOR_SHIFT) >= 3u);
    for (k = 0; k < g2d_nscenes; k++) {
        int ret;
        printf("SCENE %u %s\n", k, g2d_scene_names[k]);
        g2d_stdin = g2d_scenes[k];
        clearerr(stdin);
        csr_wr(G2D_CSR_PERF_CTRL, G2D_CSR_PERF_CTRL_EN_MASK | G2D_CSR_PERF_CTRL_CLEAR_MASK);
        if (dbg)
            csr_wr(G2D_CSR_DBG_CTRL, G2D_CSR_DBG_CTRL_SIG_CLEAR_MASK);
        ret = vgl_scene_main(1, argv);
        csr_wr(G2D_CSR_PERF_CTRL, G2D_CSR_PERF_CTRL_EN_MASK | G2D_CSR_PERF_CTRL_SNAPSHOT_MASK);
        /* wsig: CRC of every byte G2D wrote in this scene, to compare the board with the SoC simulation scene by
         * scene (Q-FPGA-10); coll: RAM read/write collisions seen (must be 0) */
        printf("SCENE_END %u ret=%d busy=%u draws=%u", k, ret, (unsigned)csr(G2D_CSR_PERF_BUSY_CYCLES),
               (unsigned)csr(G2D_CSR_PERF_DRAWS));
        if (dbg) {
            printf(" wsig=%08x coll=%02x", (unsigned)csr(G2D_CSR_WR_SIG), (unsigned)csr(G2D_CSR_DBG_STATUS));
            csr_wr(G2D_CSR_DBG_STATUS, 0x7Fu);
        }
        printf("\n");
        if (ret)
            fails++;
    }
    return fails;
}

static void dram_delay(unsigned n)
{
    *(volatile uint32_t *)(uintptr_t)(G2D_CHS_CFG_BASE + G2D_CHS_DRAM_AR_DELAY) = n;
    *(volatile uint32_t *)(uintptr_t)(G2D_CHS_CFG_BASE + G2D_CHS_DRAM_AW_DELAY) = n;
}

#ifndef G2D_FPGA_DBG         /* 0: never touch the v1.8 debug registers (wsig / coll per scene) */
#define G2D_FPGA_DBG 1
#endif
#ifndef G2D_LLC_CACHE        /* 1 (v1.8): switch the LLC's scratchpad ways to cache first, as DOOM does */
#define G2D_LLC_CACHE 0
#endif
#define LLC_CFG_BASE 0x03001000u   /* __llc_base_addr__ (Cheshire sw/link/cheshire_addrs.ldh) */

static void llc_to_cache(void)
{
    volatile uint32_t *llc = (volatile uint32_t *)(uintptr_t)LLC_CFG_BASE;
    llc[0] = 0;                    /* CFG_SPM_LOW / HIGH: no way as scratchpad */
    llc[1] = 0;
    llc[4] = 1;                    /* COMMIT_CFG: the LLC flushes the ways itself and stalls accesses meanwhile */
    printf("LLC cache mode: scratchpad ways now %08x%08x\n", (unsigned)llc[1], (unsigned)llc[0]);
}

int main(void)
{
    unsigned fails = 0;
    uint64_t core_hz;

    g2d_rtc_hz = CHS_REGS->rtc_freq.f.ref_freq;
    core_hz = clint_get_core_freq(g2d_rtc_hz, 2500);
    /* The measurement against the RTC can come out a little low (e.g. 49.99 MHz); Cheshire's uart_init truncates
     * freq / (16 * baud), which at fast simulation rates would give divisor 0. Round to 100 kHz first. */
    uart_init(&__uart_base_addr__, (core_hz + 50000u) / 100000u * 100000u, G2D_UART_BAUD);
    setvbuf(stdin, NULL, _IONBF, 0);
    setvbuf(stdout, NULL, _IOLBF, 0);

    printf("G2D_BOOT core_hz=%lu\n", (unsigned long)core_hz);   /* before the first G2D access */
    if (G2D_LLC_CACHE)
        llc_to_cache();
    {
        uint32_t id = csr(G2D_CSR_ID), hwcfg = csr(G2D_CSR_HWCFG);
        printf("G2D_TEST_BEGIN %u id=%08x hwcfg=%08x\n", g2d_nscenes, (unsigned)id, (unsigned)hwcfg);
    }
    crc_init();
    fails += run_scenes();
    fails += lvgl_replay();
    vga_test(0);
    if (!irq_test())
        fails++;
    lat_probe(0);
    if (g2d_nsweep) {
        unsigned j;
        for (j = 0; j < g2d_nsweep; j++) {
            printf("SWEEP %u\n", g2d_sweep_delays[j]);
            dram_delay(g2d_sweep_delays[j]);
            lat_probe(g2d_sweep_delays[j]);
            fails += run_scenes();
            fails += lvgl_replay();
            printf("SWEEP_END %u\n", g2d_sweep_delays[j]);
        }
        dram_delay(0);
    }
    printf("G2D_TEST_END fails=%u\n", fails);
    vga_test(1);
    uart_write_flush(&__uart_base_addr__);
    return (int)fails;
}
