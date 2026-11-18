// SPDX-License-Identifier: Apache-2.0
/* G2D on FreeRTOS (v2.0, user 2026-11-12; docs/PROGRAMMING.md §7): a thin layer over a device HAL (bare metal on
 * Cheshire, the model HAL on the host) that makes the vg_lite driver usable from several tasks:
 *   g2d_hal_lock / g2d_hal_unlock   a FreeRTOS mutex around every public vg_lite_* call (§6.1)
 *   g2d_platform_wait_irq(us)       the driver's wait blocks the calling task on a binary semaphore that the G2D
 *                                   interrupt gives (g2d_freertos_irq_from_isr), with a timeout (other tasks run)
 *   g2d_platform_time_us(&us)       only with G2D_FREERTOS_TIME=1: the tick count (a platform clock is better)
 * Call g2d_freertos_init() once before the first vg_lite call (before or after vTaskStartScheduler). The platform's
 * interrupt handler calls g2d_freertos_irq_from_isr() for the G2D interrupt (after its PLIC claim); it acknowledges
 * the GPU (clears IRQ_STATUS, the level interrupt) and wakes the waiting task. */
#ifndef G2D_FREERTOS_H
#define G2D_FREERTOS_H

#ifdef __cplusplus
extern "C" {
#endif

void g2d_freertos_init(void);
void g2d_freertos_irq_from_isr(void);

#ifdef __cplusplus
}
#endif

#endif /* G2D_FREERTOS_H */
