// SPDX-License-Identifier: Apache-2.0
/* G2D HAL, bare-metal backend (Cheshire / CVA6, Phase 7): MMIO CSR access, a static GPU memory pool, polling.
 * Phase 6 compile-checks it with the SiFive riscv64 GCC (`make baremetal-check`); it runs in Phase 7.
 *
 * Build-time configuration (Phase 7 sets these from the Cheshire integration, OPEN_QUESTIONS Q-FPGA-6):
 *   G2D_CSR_BASE     CPU address of the AXI4-Lite CSR block (default 0x48000000, a placeholder)
 *   G2D_POOL_BYTES   size of the GPU memory pool (default 8 MB, 64-byte aligned)
 *   G2D_POOL_ADDR    if defined: the pool is this fixed address range (e.g. DRAM above the program image)
 *                    instead of an array in .bss (Cheshire's dram.ld gives the program only 8 MB)
 * GPU addresses are the low 32 bits of the CPU addresses; v2.0 (C-5): the HAL reports bits [63:32] of the pool
 * (mem_base_hi), so the pool may lie above 4 GB as long as it does not cross a 4 GB boundary. The pool must lie in
 * memory the GPU's AXI master reaches.
 *
 * Platform hooks (weak; a platform overrides them):
 *   g2d_platform_cache_clean(p, n)       write back CPU caches over [p, p+n) before the GPU reads it
 *   g2d_platform_cache_invalidate(p, n)  drop CPU cache lines over [p, p+n) before the CPU reads GPU output
 *   g2d_platform_delay_us(us)            busy-wait
 *   g2d_platform_time_us(&us)            v2.0 (C-6): monotonic microseconds; return 1, or 0 if there is no clock
 *                                        (default: 0, the driver then counts wait intervals)
 *   g2d_platform_wait_irq(timeout_us)    v2.0 (C-3): sleep until the G2D interrupt or the timeout (default: one
 *                                        polling interval, at most 10 us); sw/apps/baremetal/syscalls.c has the
 *                                        Cheshire PLIC + WFI version (G2D_HAL_IRQ=1)
 * The defaults are a full `fence` and a spin loop. On Cheshire (CVA6 cv64a6_imafdchsclic_sv39_wb: write-back
 * D-cache, DcacheFlushOnFence = 1, INVALIDATE_ON_FLUSH = 1; verified from the CVA6 source, Phase 7) a `fence`
 * writes back every dirty line and invalidates the whole D-cache, so the default hooks are correct there; the GPU
 * reaches DRAM through the same LLC as the CPU.
 */
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "g2d_hal.h"

#ifndef G2D_CSR_BASE
#define G2D_CSR_BASE 0x48000000u
#endif
#ifndef G2D_POOL_BYTES
#define G2D_POOL_BYTES (8u * 1024u * 1024u)
#endif
#ifndef G2D_POOL_BLOCKS
#define G2D_POOL_BLOCKS 256u   /* live allocations at most */
#endif

__attribute__((weak)) void g2d_platform_cache_clean(const void *p, size_t n)
{
    (void)p;
    (void)n;
    __asm__ volatile("fence" ::: "memory");
}

__attribute__((weak)) void g2d_platform_cache_invalidate(void *p, size_t n)
{
    (void)p;
    (void)n;
    __asm__ volatile("fence" ::: "memory");
}

__attribute__((weak)) void g2d_platform_delay_us(uint32_t us)
{
    volatile uint32_t i;
    for (i = 0; i < us * 50u; i++) {}   /* about 1 us per 50 iterations at 50 MHz; platforms override */
}

__attribute__((weak)) int g2d_platform_time_us(uint64_t *us)
{
    (void)us;
    return 0;
}

__attribute__((weak)) void g2d_platform_wait_irq(uint32_t timeout_us)
{
    g2d_platform_delay_us(timeout_us < 10u ? timeout_us : 10u);
}

#ifdef G2D_POOL_ADDR
#define g_pool ((uint8_t *)(uintptr_t)(G2D_POOL_ADDR))
#else
static uint8_t g_pool[G2D_POOL_BYTES] __attribute__((aligned(64)));
#endif
static struct {
    uint32_t off, size;
} g_blk[G2D_POOL_BLOCKS];   /* live allocations, sorted by offset */
static unsigned g_nblk;

static uint32_t reg_read(void *ctx, uint32_t off)
{
    (void)ctx;
    return *(volatile uint32_t *)(uintptr_t)(G2D_CSR_BASE + off);
}

static void reg_write(void *ctx, uint32_t off, uint32_t v)
{
    (void)ctx;
    *(volatile uint32_t *)(uintptr_t)(G2D_CSR_BASE + off) = v;
}

/* First fit between live allocations (same policy as the software backend). */
static int mem_alloc(void *ctx, size_t size, size_t align, void **cpu, uint32_t *gpu)
{
    uint64_t pos = 0;
    unsigned i, k;
    (void)ctx;
    if (size == 0)
        size = 1;
    if (align < 64)
        align = 64;
    if (g_nblk == G2D_POOL_BLOCKS)
        return -1;
    for (i = 0; i < g_nblk; i++) {
        uint64_t start = (pos + align - 1) & ~(uint64_t)(align - 1);
        if (start + size <= g_blk[i].off) {
            pos = start;
            goto found;
        }
        pos = (uint64_t)g_blk[i].off + g_blk[i].size;
    }
    pos = (pos + align - 1) & ~(uint64_t)(align - 1);
    if (pos + size > G2D_POOL_BYTES)
        return -1;
found:
    for (k = g_nblk; k > i; k--)
        g_blk[k] = g_blk[k - 1];
    g_blk[i].off = (uint32_t)pos;
    g_blk[i].size = (uint32_t)size;
    g_nblk++;
    {
        uint8_t *p = g_pool + pos;
        /* v2.0: 64-bit stores inline (p is 64-byte aligned): the memset that the Cheshire runtime links in calls a
         * function per 32-bit word, minutes of SoC simulation for the driver's 640 KB at vg_lite_init */
        {
            volatile uint64_t *q = (volatile uint64_t *)(void *)p;
            size_t k, n8 = size / 8u;
            for (k = 0; k < n8; k++)
                q[k] = 0;
            for (k = n8 * 8u; k < size; k++)
                p[k] = 0;
        }
        *cpu = p;
        *gpu = (uint32_t)(uintptr_t)p;          /* low 32 bits; the window is mem_base_hi (v2.0) */
    }
    return 0;
}

static void mem_free(void *ctx, void *cpu, uint32_t gpu)
{
    uint32_t off = (uint32_t)((uint8_t *)cpu - g_pool);
    unsigned i;
    (void)ctx;
    (void)gpu;
    for (i = 0; i < g_nblk; i++)
        if (g_blk[i].off == off) {
            for (; i + 1 < g_nblk; i++)
                g_blk[i] = g_blk[i + 1];
            g_nblk--;
            return;
        }
}

static void *mem_cpu(void *ctx, uint32_t gpu, size_t size)
{
    (void)ctx;
    (void)size;
    return (void *)(uintptr_t)((((uint64_t)(uintptr_t)g_pool >> 32) << 32) | gpu);   /* in the pool's 4 GB window */
}

static void cache_clean(void *ctx, const void *p, size_t n)
{
    (void)ctx;
    g2d_platform_cache_clean(p, n);
}

static void cache_invalidate(void *ctx, void *p, size_t n)
{
    (void)ctx;
    g2d_platform_cache_invalidate(p, n);
}

static void hal_wait(void *ctx, uint32_t timeout_us)
{
    (void)ctx;
    /* the driver re-reads the CSRs after every return (spurious wake-ups are fine) */
    g2d_platform_wait_irq(timeout_us);
}

static uint64_t hal_time_us(void *ctx)
{
    uint64_t us = 0;
    (void)ctx;
    g2d_platform_time_us(&us);
    return us;
}

static uint32_t hal_mem_base_hi(void *ctx)
{
    (void)ctx;
    return (uint32_t)((uint64_t)(uintptr_t)g_pool >> 32);
}

static g2d_hal_t g_hal = {0, reg_read, reg_write, mem_alloc, mem_free, mem_cpu, cache_clean, cache_invalidate,
                          hal_wait, 0, hal_mem_base_hi};

const g2d_hal_t *g2d_hal_open(void)
{
    uint64_t us;
    g_hal.time_us = g2d_platform_time_us(&us) ? hal_time_us : 0;   /* only with a platform clock */
    return &g_hal;
}

void g2d_hal_close(const g2d_hal_t *hal) { (void)hal; }
