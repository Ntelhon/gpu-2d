// SPDX-License-Identifier: Apache-2.0
/* G2D on FreeRTOS on Cheshire (v2.0, docs/PROGRAMMING.md §7): NT tasks draw at once through the vg_lite driver
 * (FreeRTOS layer sw/hal/freertos: mutex around every call; the driver's waits block the task until the G2D
 * interrupt). The same sequences (sw/tests/freertos/g2d_seq.c) run first in one task as the reference. Output:
 *   FREERTOS_BEGIN tasks=<n> rounds=<r>
 *   FREERTOS_CRC <task> <crc>             (concurrent run; equal to the host test's CRCs for the same rounds)
 *   FREERTOS_IRQ <count>                  G2D interrupts taken
 *   FREERTOS_END fails=<n>
 * and exits with fails (the SoC testbench prints TB_RESULT PASS for 0). */
#include <stdio.h>
#include <stdlib.h>

#include "FreeRTOS.h"
#include "task.h"

#include "dif/clint.h"
#include "dif/uart.h"
#include "params.h"
#include "regs/cheshire.h"
#include "util.h"

#include "g2d_freertos.h"
#include "g2d_hal.h"
#include "g2d_seq.h"
#include "vg_lite.h"

#ifndef G2D_RTOS_TASKS
#define G2D_RTOS_TASKS 3
#endif
#ifndef G2D_SEQ_ROUNDS
#define G2D_SEQ_ROUNDS 3
#endif
#ifndef G2D_UART_BAUD
#define G2D_UART_BAUD 115200
#endif
#ifndef G2D_PLIC_SRC
#define G2D_PLIC_SRC 58u
#endif

extern uint32_t g2d_rtc_hz;                   /* sw/apps/baremetal/syscalls.c */
extern void freertos_risc_v_trap_handler(void);

static volatile uint32_t g_irqs;
static g2d_seq_job_t g_ref[G2D_RTOS_TASKS], g_par[G2D_RTOS_TASKS];

static volatile uint32_t *plic_reg(uint32_t off) { return (volatile uint32_t *)((uintptr_t)&__plic_base_addr__ + off); }

void g2d_rtos_assert(const char *f, int l)
{
    printf("FREERTOS_ASSERT %s:%d\n", f, l);
    exit(3);
}

/* Machine external interrupt (called by the RISC-V port's trap handler, FreeRTOS context saved). */
void freertos_risc_v_application_interrupt_handler(void)
{
    uint32_t claim = *plic_reg(0x200004u);
    if (claim == G2D_PLIC_SRC) {
        g_irqs++;
        g2d_freertos_irq_from_isr();
    }
    if (claim)
        *plic_reg(0x200004u) = claim;                                       /* complete */
}

void g2d_seq_yield(void) { taskYIELD(); }

static void worker(void *arg)
{
    g2d_seq_run((g2d_seq_job_t *)arg);
    ((g2d_seq_job_t *)arg)->done = 1;
    vTaskDelete(NULL);
}

static void controller(void *arg)
{
    int fails = 0;
    (void)arg;
    if (vg_lite_init(0, 0) != VG_LITE_SUCCESS) {
        printf("FREERTOS_END fails=1 (init)\n");
        exit(1);
    }
    printf("FREERTOS_STEP init irqs=%u\n", (unsigned)g_irqs);
    for (int i = 0; i < G2D_RTOS_TASKS; i++) {
        g_ref[i].id = i;
        g_ref[i].rounds = G2D_SEQ_ROUNDS;
        g2d_seq_run(&g_ref[i]);
        printf("FREERTOS_STEP ref %d crc %08x irqs=%u ticks=%u\n", i, (unsigned)g_ref[i].crc, (unsigned)g_irqs,
               (unsigned)xTaskGetTickCount());
    }
    for (int i = 0; i < G2D_RTOS_TASKS; i++) {
        g_par[i].id = i;
        g_par[i].rounds = G2D_SEQ_ROUNDS;
        xTaskCreate(worker, "draw", 4096, &g_par[i], 1, NULL);
    }
    for (;;) {
        int all = 1;
        for (int i = 0; i < G2D_RTOS_TASKS; i++)
            all &= g_par[i].done;
        if (all)
            break;
        vTaskDelay(1);
    }
    for (int i = 0; i < G2D_RTOS_TASKS; i++) {
        printf("FREERTOS_CRC %d %08x\n", i, (unsigned)g_par[i].crc);
        if (g_ref[i].errors || g_par[i].errors || g_ref[i].crc != g_par[i].crc) {
            printf("FREERTOS_FAIL task %d errors %d/%d crc %08x/%08x\n", i, g_ref[i].errors, g_par[i].errors,
                   (unsigned)g_ref[i].crc, (unsigned)g_par[i].crc);
            fails++;
        }
    }
    printf("FREERTOS_IRQ %u\n", (unsigned)g_irqs);
    fails += g_irqs == 0;                     /* the waits must have been woken by the G2D interrupt */
    vg_lite_close();
    printf("FREERTOS_END fails=%d\n", fails);
    exit(fails);
}

int main(void)
{
    uint64_t core_hz;
    g2d_rtc_hz = CHS_REGS->rtc_freq.f.ref_freq;
    core_hz = clint_get_core_freq(g2d_rtc_hz, 2500);
    uart_init(&__uart_base_addr__, (core_hz + 50000u) / 100000u * 100000u, G2D_UART_BAUD);
    printf("FREERTOS_BEGIN tasks=%d rounds=%d\n", G2D_RTOS_TASKS, G2D_SEQ_ROUNDS);
    *plic_reg(4u * G2D_PLIC_SRC) = 1;                                              /* priority 1 */
    *plic_reg(0x2000u + 4u * (G2D_PLIC_SRC / 32u)) |= 1u << (G2D_PLIC_SRC % 32u);   /* enable, target 0 */
    *plic_reg(0x200000u) = 0;                                                      /* threshold 0 */
    __asm__ volatile("csrw mtvec, %0" ::"r"((uintptr_t)freertos_risc_v_trap_handler));
    g2d_freertos_init();
    xTaskCreate(controller, "ctl", 4096, NULL, 2, NULL);
    printf("FREERTOS_STEP scheduler\n");
    vTaskStartScheduler();
    printf("FREERTOS_END fails=1 (scheduler returned)\n");
    return 1;
}
