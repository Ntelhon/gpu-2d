// SPDX-License-Identifier: Apache-2.0
/* v2.0 FreeRTOS integration on the host (POSIX port of FreeRTOS V11.3.3, scripts/fetch_freertos.sh) with the
 * software backend (model HAL) and the FreeRTOS layer sw/hal/freertos (driver lock = FreeRTOS mutex).
 *   1. a reference run of NT drawing sequences in one task, one after the other (CRC per sequence);
 *   2. the same NT sequences in NT tasks of equal priority (time-sliced, preempted at every tick), each on its own
 *      target: every task's CRC must equal its reference;
 * Without the mutex the tasks interleave inside the driver (the tick preempts them) and the model HAL's state breaks.
 * The scheduler is ended with vTaskEndScheduler; the exit code is the result. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "FreeRTOS.h"
#include "task.h"

#include "g2d_freertos.h"
#include "g2d_hal.h"
#include "g2d_seq.h"
#include "vg_lite.h"

enum { NT_MAX = 8 };
static int NT = 4, ROUNDS = 30;   /* G2D_RTOS_TASKS / G2D_SEQ_ROUNDS from the environment */

void g2d_test_assert(const char *f, int l)
{
    printf("FAIL: configASSERT %s:%d\n", f, l);
    exit(2);
}

void g2d_platform_delay_us(uint32_t us) { (void)us; }   /* the model HAL never waits for hardware */

static g2d_seq_job_t g_ref[NT_MAX], g_par[NT_MAX];
static int g_rc = 1;

void g2d_seq_yield(void) { taskYIELD(); }

static void worker(void *arg)
{
    g2d_seq_run((g2d_seq_job_t *)arg);
    ((g2d_seq_job_t *)arg)->done = 1;
    vTaskDelete(NULL);
}

static void controller(void *arg)
{
    (void)arg;
    const g2d_hal_t *hal = g2d_hal_open();
    int fail = 0;
    if (!hal || vg_lite_init(0, 0) != VG_LITE_SUCCESS) {
        printf("FAIL: init\n");
        vTaskEndScheduler();
        return;
    }
    for (int i = 0; i < NT; i++) {
        g_ref[i].id = i;
        g_ref[i].rounds = ROUNDS;
        g2d_seq_run(&g_ref[i]);
    }
    for (int i = 0; i < NT; i++) {
        g_par[i].id = i;
        g_par[i].rounds = ROUNDS;
        xTaskCreate(worker, "draw", 65536, &g_par[i], 1, NULL);
    }
    for (;;) {
        int all = 1;
        for (int i = 0; i < NT; i++)
            all &= g_par[i].done;
        if (all)
            break;
        vTaskDelay(1);
    }
    for (int i = 0; i < NT; i++) {
        if (g_ref[i].errors || g_par[i].errors) {
            printf("FAIL: task %d errors %d / %d\n", i, g_ref[i].errors, g_par[i].errors);
            fail = 1;
        }
        if (g_ref[i].crc != g_par[i].crc) {
            printf("FAIL: task %d crc %08x alone, %08x with the other tasks\n", i, g_ref[i].crc, g_par[i].crc);
            fail = 1;
        }
    }
    vg_lite_close();
    g2d_hal_close(hal);
    for (int i = 0; i < NT; i++)
        printf("FREERTOS_CRC %d %08x\n", i, (unsigned)g_par[i].crc);
    printf("%s: %d tasks x %d rounds\n", fail ? "FAIL" : "PASS", NT, ROUNDS);
    g_rc = fail;
    vTaskEndScheduler();
}

int main(void)
{
    if (getenv("G2D_RTOS_TASKS"))
        NT = atoi(getenv("G2D_RTOS_TASKS"));
    if (getenv("G2D_SEQ_ROUNDS"))
        ROUNDS = atoi(getenv("G2D_SEQ_ROUNDS"));
    if (NT < 1 || NT > NT_MAX)
        NT = 4;
    g2d_freertos_init();
    xTaskCreate(controller, "ctl", 65536, NULL, 2, NULL);
    vTaskStartScheduler();
    return g_rc;
}
