// SPDX-License-Identifier: Apache-2.0
/* v2.0 Linux HAL host test: the vg_lite driver with the Linux HAL (sw/hal/linux) over a fake /dev/g2d
 * (fake_g2d.c, the kernel driver's semantics over the golden model). Checks:
 *   - the device: INFO, one opener at a time (EBUSY), writes to the window registers refused (EPERM) and the window
 *     unchanged, the HAL's mem_cpu refuses addresses outside the pool;
 *   - drawing: the shared sequence (sw/tests/freertos/g2d_seq.c) gives the CRCs of the model HAL run (seq_ref, passed
 *     as arguments), run one after the other and in three threads at once (driver lock = the HAL's pthread mutex);
 *   - close and open again.
 * Usage: test_linux_hal <crc0> <crc1> <crc2>   (3 sequences of 3 rounds) */
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fake_g2d.h"
#include "g2d_hal.h"
#include "g2d_seq.h"
#include "generated/g2d_regs.h"
#include "uapi/g2d_ioctl.h"
#include "vg_lite.h"

enum { N = 3, ROUNDS = 3 };
static int g_fail, g_checks;
#define CHECK(c, ...)                                                                                               \
    do {                                                                                                            \
        g_checks++;                                                                                                 \
        if (!(c)) {                                                                                                 \
            g_fail++;                                                                                               \
            printf("FAIL %s:%d: ", __FILE__, __LINE__);                                                             \
            printf(__VA_ARGS__);                                                                                    \
            printf("\n");                                                                                           \
        }                                                                                                           \
    } while (0)

static g2d_seq_job_t g_par[N];

static void *worker(void *a)
{
    g2d_seq_run((g2d_seq_job_t *)a);
    return NULL;
}

int main(int argc, char **argv)
{
    uint32_t exp[N];
    const g2d_hal_t *hal;
    struct g2d_reg r;
    uint32_t win_lo;
    if (argc != 1 + N) {
        printf("usage: %s <crc0> <crc1> <crc2>\n", argv[0]);
        return 2;
    }
    for (int i = 0; i < N; i++)
        exp[i] = (uint32_t)strtoul(argv[1 + i], NULL, 16);

    hal = g2d_hal_open();
    CHECK(hal != NULL, "open");
    if (!hal)
        return 1;
    CHECK(fake_open("/dev/g2d", O_RDWR) < 0 && errno == EBUSY, "second open: EBUSY");
    CHECK(hal->reg_read(hal->ctx, G2D_CSR_ID) == 0x47324420u, "ID through REG_READ");
    win_lo = hal->reg_read(hal->ctx, G2D_CSR_ADDR_WIN_LO);
    r.off = G2D_CSR_ADDR_WIN_LO;
    r.value = 0;
    CHECK(fake_ioctl(42, G2D_IOC_REG_WRITE, &r) < 0 && errno == EPERM, "window write refused");
    CHECK(hal->reg_read(hal->ctx, G2D_CSR_ADDR_WIN_LO) == win_lo, "window unchanged");
    CHECK(hal->mem_cpu(hal->ctx, win_lo - 4096u, 16) == NULL, "mem_cpu outside the pool");

    CHECK(vg_lite_init(0, 0) == VG_LITE_SUCCESS, "vg_lite_init");
    for (int i = 0; i < N; i++) {
        g2d_seq_job_t j;
        memset(&j, 0, sizeof j);
        j.id = i;
        j.rounds = ROUNDS;
        g2d_seq_run(&j);
        CHECK(j.errors == 0 && j.crc == exp[i], "sequence %d: crc %08x, model %08x, errors %d", i, (unsigned)j.crc,
              (unsigned)exp[i], j.errors);
    }
    pthread_t th[N];
    for (int i = 0; i < N; i++) {
        g_par[i].id = i;
        g_par[i].rounds = ROUNDS;
        pthread_create(&th[i], NULL, worker, &g_par[i]);
    }
    for (int i = 0; i < N; i++) {
        pthread_join(th[i], NULL);
        CHECK(g_par[i].errors == 0 && g_par[i].crc == exp[i], "thread %d: crc %08x, model %08x", i,
              (unsigned)g_par[i].crc, (unsigned)exp[i]);
    }
    CHECK(vg_lite_close() == VG_LITE_SUCCESS, "vg_lite_close");
    g2d_hal_close(hal);
    hal = g2d_hal_open();
    CHECK(hal != NULL, "open again after close");
    g2d_hal_close(hal);
    printf("%s: %d checks, %d failed\n", g_fail ? "FAIL" : "PASS", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
