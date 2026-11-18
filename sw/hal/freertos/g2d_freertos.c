// SPDX-License-Identifier: Apache-2.0
/* G2D on FreeRTOS: driver lock, interrupt wait, optional tick clock (g2d_freertos.h). */
#include <stdint.h>

#include "FreeRTOS.h"
#include "semphr.h"
#include "task.h"

#include "g2d_freertos.h"
#include "g2d_hal.h"
#include "generated/g2d_regs.h"

static SemaphoreHandle_t g_lock, g_irq;
static const g2d_hal_t *g_hal;   /* for the interrupt acknowledge */
static StaticSemaphore_t g_lock_buf, g_irq_buf;

void g2d_freertos_init(void)
{
    if (!g_lock)
        g_lock = xSemaphoreCreateMutexStatic(&g_lock_buf);
    if (!g_irq)
        g_irq = xSemaphoreCreateBinaryStatic(&g_irq_buf);
    if (!g_hal)
        g_hal = g2d_hal_open();
}

/* Overrides the driver's weak no-op hooks (sw/driver/vg_lite/drv_core.c). Not for interrupt context. */
void g2d_hal_lock(void)
{
    if (xTaskGetSchedulerState() != taskSCHEDULER_NOT_STARTED)
        xSemaphoreTake(g_lock, portMAX_DELAY);
}

void g2d_hal_unlock(void)
{
    if (xTaskGetSchedulerState() != taskSCHEDULER_NOT_STARTED)
        xSemaphoreGive(g_lock);
}

/* The bare-metal HAL's wait (sw/hal/baremetal): block until the G2D interrupt or the timeout, at least one tick.
 * Before the scheduler runs (single-threaded start-up) it degrades to a short busy wait. */
void g2d_platform_delay_us(uint32_t us);

void g2d_platform_wait_irq(uint32_t timeout_us)
{
    TickType_t t;
    if (xTaskGetSchedulerState() == taskSCHEDULER_NOT_STARTED) {
        g2d_platform_delay_us(timeout_us < 10u ? timeout_us : 10u);
        return;
    }
    t = pdMS_TO_TICKS((timeout_us + 999u) / 1000u);
    xSemaphoreTake(g_irq, t ? t : 1);
}

/* The G2D interrupt is a level (IRQ_STATUS & IRQ_ENABLE, arch.md §7) that stays high until IRQ_STATUS is cleared; the
 * driver tracks progress through FENCE_SEQ / STATUS / ERR_STATUS, not IRQ_STATUS, so the handler acknowledges every
 * bit here. Without it the interrupt fires again right after the PLIC complete (a storm with interrupts enabled). A
 * waiting task that cleared FENCE before it checked the fence still sees the semaphore (no lost wake-up). */
void g2d_freertos_irq_from_isr(void)
{
    BaseType_t woken = pdFALSE;
    if (g_hal)
        g_hal->reg_write(g_hal->ctx, G2D_CSR_IRQ_STATUS, 0xFFFFFFFFu);
    if (g_irq) {
        xSemaphoreGiveFromISR(g_irq, &woken);
        portYIELD_FROM_ISR(woken);
    }
}

#if defined(G2D_FREERTOS_TIME) && G2D_FREERTOS_TIME
int g2d_platform_time_us(uint64_t *us)
{
    *us = (uint64_t)xTaskGetTickCount() * (1000000u / configTICK_RATE_HZ);
    return 1;
}
#endif
