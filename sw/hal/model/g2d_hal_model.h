// SPDX-License-Identifier: Apache-2.0
/* G2D HAL, software backend (golden model): extras for tests and the recorder format.
 *
 * Recording (G2D_RECORD=<file>), little-endian 32-bit words:
 *   MAGIC, VERSION, then records until END:
 *     MEM        addr, len, len bytes   pre-kick content of a 4 KB page the next kick touches (only when the
 *                                       replay has not seen that content yet)
 *     KICK       addr, size             CMD_ADDR / CMD_KICK
 *     SOFT_RESET                        CTRL.SOFT_RESET
 *     CHECK      addr, len, len bytes, len/8 mask bytes
 *                                       (version 2, after the last kick) per page, the bytes whose final value
 *                                       a replay produces itself (the GPU wrote them after the page's last MEM
 *                                       record), as the software backend computed them; bit i of the mask
 *                                       (byte i/8, bit i%8) marks byte i; pages in address order
 *     POST       addr, len, len bytes, len/8 mask bytes
 *                                       (version 2, only with G2D_RECORD_POST=1) after a KICK, per page that
 *                                       kick wrote: the bytes it wrote (mask) as the software backend left them
 * Only bytes the GPU wrote are compared: in identity mode a page can also hold host heap data that changes
 * during a kick without a GPU write (allocator activity), which a replay does not reproduce.
 *     END
 * A replay applies the records in order to a fresh GPU (RTL or model) whose memory starts at zero.
 * Version 1 files have no CHECK records.
 */
#ifndef G2D_HAL_MODEL_H
#define G2D_HAL_MODEL_H

#include <stddef.h>
#include <stdint.h>

#define G2D_MODEL_BASE 0x10000000u   /* GPU address of the first arena byte */

#define G2D_REC_MAGIC 0x43455232u    /* "2REC" */
#define G2D_REC_VERSION 2u
#define G2D_REC_MEM 1u
#define G2D_REC_KICK 2u
#define G2D_REC_SOFT_RESET 3u
#define G2D_REC_CHECK 4u
#define G2D_REC_POST 5u
#define G2D_REC_END 0xFFFFFFFFu

#ifdef __cplusplus
extern "C" {
#endif

/* The arena (CPU view of all GPU memory) after g2d_hal_open; NULL before and in identity mode. */
uint8_t *g2d_hal_model_arena(uint32_t *base, size_t *size);
/* Number of kicks the model executed. */
uint64_t g2d_hal_model_kicks(void);

/* g2d_hal_open / g2d_hal_close are reference-counted here: a test that opens the HAL itself keeps the model
 * (and its counters) alive across the driver's vg_lite_init / vg_lite_close.
 * Test controls (v1.6, driver robustness D-1..D-4, docs/FEATURES_LATER.md); all off after g2d_hal_open.
 * Stall: kicks are accepted but never executed (FENCE_SEQ stops, STATUS.IDLE stays 0), as a hung GPU.
 *   reset_works = 1: a soft reset clears the stall (the GPU is idle afterwards); 0: SOFT_RESET_BUSY never
 *   falls (the driver cannot confirm that the GPU stopped). stall = 0 ends the stall. */
void g2d_hal_model_stall(int stall, int reset_works);
/* The n-th next mem_alloc fails (n = 0: the next one; -1: none). */
void g2d_hal_model_fail_alloc(int n);
/* Live allocations (mem_alloc minus mem_free), for leak checks. */
int g2d_hal_model_live_allocs(void);
/* Non-coherent memory (arena mode only, before any allocation): mem_alloc / mem_cpu return a CPU view that the
 * GPU does not see; cache_clean copies a range CPU -> GPU, cache_invalidate GPU -> CPU, as a write-back cache with
 * range maintenance. Kicks run on the GPU view only. */
void g2d_hal_model_noncoherent(int on);
/* v1.7: the optional units of the modelled build (HWCFG.FEAT_*, bit 0 families .. 5 mask; default all). Set it
 * before vg_lite_init so the driver reads it. */
void g2d_hal_model_features(unsigned mask);
/* v2.0: HAL wait calls so far; a HAL clock (time_us) that advances us_per_wait per wait call (0: no clock, the
 * driver counts wait intervals); a mem_base_hi hook returning value (on = 0: none). */
uint64_t g2d_hal_model_waits(void);
void g2d_hal_model_clock(uint32_t us_per_wait);
void g2d_hal_model_base_hi(int on, uint32_t value);

#ifdef __cplusplus
}
#endif

#endif /* G2D_HAL_MODEL_H */
