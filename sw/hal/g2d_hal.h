// SPDX-License-Identifier: Apache-2.0
/* G2D hardware abstraction layer: the only interface between the vg_lite driver and a GPU instance.
 *
 * The driver talks to the GPU through its CSRs only (spec/regs.yaml) and to memory through buffers it
 * allocates here, so the same driver runs on every backend (Phase 6 plan):
 *   sw/hal/model      software backend: CSRs emulated over the C++ golden model, kicks execute at once
 *   dv/live           live RTL backend: Verilator model of g2d_top, AXI4-Lite master + AXI4 memory in C++
 *   sw/hal/baremetal  Cheshire (Phase 7): MMIO + polling
 *
 * Memory: GPU addresses are 32 bit (the IP adds MEM_BASE_HI above them, arch.md §4.2; the backend reports the
 * window through mem_base_hi, v2.0). A buffer from
 * mem_alloc is physically contiguous, visible to the GPU at *gpu and to the CPU at *cpu.
 * Coherence (docs/PROGRAMMING.md §1): the driver calls cache_clean on everything the GPU will read before
 * a kick, and cache_invalidate on GPU-written buffers before the CPU reads them; coherent backends make
 * both no-ops.
 */
#ifndef G2D_HAL_H
#define G2D_HAL_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct g2d_hal {
    void *ctx;
    /* CSR access, byte offsets from the AXI4-Lite base (G2D_CSR_* of g2d_regs.h). */
    uint32_t (*reg_read)(void *ctx, uint32_t off);
    void (*reg_write)(void *ctx, uint32_t off, uint32_t value);
    /* Contiguous GPU-visible memory. 0 on success. align is a power of two >= 64. */
    int (*mem_alloc)(void *ctx, size_t size, size_t align, void **cpu, uint32_t *gpu);
    void (*mem_free)(void *ctx, void *cpu, uint32_t gpu);
    /* CPU address of an existing GPU address range (vg_lite_map); NULL if not reachable. */
    void *(*mem_cpu)(void *ctx, uint32_t gpu, size_t size);
    void (*cache_clean)(void *ctx, const void *cpu, size_t size);
    void (*cache_invalidate)(void *ctx, void *cpu, size_t size);
    /* Wait for the GPU interrupt, or one polling interval on backends without one: returns after at most
     * timeout_us microseconds (simulated time on simulation backends). The driver re-reads the CSRs after
     * every return, so spurious wake-ups are fine. */
    void (*wait)(void *ctx, uint32_t timeout_us);
    /* v2.0, optional (NULL = not provided; older backends leave them out of their initializer):
     * time_us      monotonic time in microseconds. The driver's timeouts then measure time (audit C-6); without it
     *              they count wait intervals of DRV_WAIT_US.
     * mem_base_hi  bits [63:32] of every GPU address (all GPU memory lies in one 4 GB window, arch.md §4.2): the
     *              driver writes it to MEM_BASE_HI at vg_lite_init (audit C-5); without it the window is 0. */
    uint64_t (*time_us)(void *ctx);
    uint32_t (*mem_base_hi)(void *ctx);
} g2d_hal_t;

/* The backend linked into the program provides its HAL (selected at link time). NULL if it cannot open the
 * device. */
const g2d_hal_t *g2d_hal_open(void);
void g2d_hal_close(const g2d_hal_t *hal);

/* Driver lock (v2.0, docs/PROGRAMMING.md §6.1): every public vg_lite_* call runs between g2d_hal_lock() and
 * g2d_hal_unlock() (sw/driver/vg_lite/drv_api.c), so several threads / tasks may use the driver. The driver has weak
 * no-op definitions (single-threaded bare metal); a multi-threaded backend defines both, e.g. with a recursive-free
 * mutex (sw/hal/freertos). Not called from interrupt context. */
void g2d_hal_lock(void);
void g2d_hal_unlock(void);

#ifdef __cplusplus
}
#endif

#endif /* G2D_HAL_H */
