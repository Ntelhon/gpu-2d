// SPDX-License-Identifier: Apache-2.0
/* v2.0 thread safety of the vg_lite driver (audit C-1, docs/PROGRAMMING.md §6.1) on the software backend.
 *
 * The test provides g2d_hal_lock / g2d_hal_unlock (a pthread mutex), overriding the driver's weak no-ops, and:
 *   1. runs NT drawing sequences one after the other (reference: a CRC per sequence);
 *   2. runs the same NT sequences in NT threads at once, each on its own target, and requires the same CRCs;
 *   3. checks the lock discipline: never taken twice by one call (no nesting), never more than one thread inside,
 *      and every public call that the threads made went through the lock.
 * Each sequence: clears and filled polygon paths with a per-thread colour and matrix, vg_lite_finish, CRC of the
 * target. Global driver state that the API keeps between calls (scissor, blend extras) is not used here: those
 * setters are process-wide (PROGRAMMING.md §6.1). */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "g2d_hal.h"
#include "vg_lite.h"

enum { NT = 4, ROUNDS = 40, W = 96, H = 64 };

static pthread_mutex_t g_mx = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t g_stat = PTHREAD_MUTEX_INITIALIZER;
static int g_inside = 0, g_max_inside = 0, g_nested = 0;
static long g_locks = 0;
static __thread int t_depth = 0;

void g2d_hal_lock(void)
{
    if (t_depth++ != 0) {
        pthread_mutex_lock(&g_stat);
        g_nested++;
        pthread_mutex_unlock(&g_stat);
        return;
    }
    pthread_mutex_lock(&g_mx);
    pthread_mutex_lock(&g_stat);
    g_locks++;
    if (++g_inside > g_max_inside)
        g_max_inside = g_inside;
    pthread_mutex_unlock(&g_stat);
}

void g2d_hal_unlock(void)
{
    if (--t_depth != 0)
        return;
    pthread_mutex_lock(&g_stat);
    g_inside--;
    pthread_mutex_unlock(&g_stat);
    pthread_mutex_unlock(&g_mx);
}

static uint32_t crc32(const uint8_t *p, size_t n, uint32_t c)
{
    c = ~c;
    while (n--) {
        c ^= *p++;
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1u)));
    }
    return ~c;
}

typedef struct {
    int id;
    uint32_t crc;
    int calls;
    int errors;
} job_t;

#define CALL(j, x)                                                                                                \
    do {                                                                                                          \
        (j)->calls++;                                                                                             \
        if ((x) != VG_LITE_SUCCESS)                                                                               \
            (j)->errors++;                                                                                        \
    } while (0)

static void *sequence(void *arg)
{
    job_t *j = (job_t *)arg;
    vg_lite_buffer_t t;
    vg_lite_path_t p;
    vg_lite_matrix_t m;
    int16_t data[16];
    memset(&t, 0, sizeof t);
    t.width = W;
    t.height = H;
    t.format = VG_LITE_BGRA8888;
    CALL(j, vg_lite_allocate(&t));
    uint32_t crc = 0;
    for (int r = 0; r < ROUNDS; r++) {
        int k = j->id * 1000 + r;
        vg_lite_rectangle_t rc = { (k * 7) % 40, (k * 5) % 30, 20 + k % 30, 10 + k % 20 };
        CALL(j, vg_lite_clear(&t, NULL, 0xFF000000u | (uint32_t)(k * 2654435761u >> 8)));
        CALL(j, vg_lite_clear(&t, &rc, 0xFF203040u + (uint32_t)j->id * 0x10u));
        /* triangle MOVE, LINE, LINE, CLOSE, END (S16 opcodes 2, 4, 4, 0 / end) */
        memset(data, 0, sizeof data);
        int16_t x0 = (int16_t)(5 + k % 50), y0 = (int16_t)(3 + k % 40);
        int16_t pts[] = { 2, x0, y0, 4, (int16_t)(x0 + 30), (int16_t)(y0 + 5), 4, (int16_t)(x0 + 8), (int16_t)(y0 + 20), 0 };
        memcpy(data, pts, sizeof pts);
        CALL(j, vg_lite_init_path(&p, VG_LITE_S16, VG_LITE_HIGH, (vg_lite_uint32_t)sizeof pts, data, 0, 0, W, H));
        CALL(j, vg_lite_identity(&m));
        CALL(j, vg_lite_translate((vg_lite_float_t)(j->id % 3), (vg_lite_float_t)(r % 4), &m));
        CALL(j, vg_lite_draw(&t, &p, VG_LITE_FILL_NON_ZERO, &m, VG_LITE_BLEND_SRC_OVER,
                             0x80000000u | (uint32_t)(j->id * 0x3F1F0Fu + (uint32_t)r)));
        CALL(j, vg_lite_finish());
        CALL(j, vg_lite_clear_path(&p));
        for (int y = 0; y < H; y++)
            crc = crc32((const uint8_t *)t.memory + (size_t)y * (size_t)t.stride, (size_t)W * 4u, crc);
    }
    CALL(j, vg_lite_free(&t));
    j->crc = crc;
    return NULL;
}

int main(void)
{
    const g2d_hal_t *hal = g2d_hal_open();
    int fail = 0;
    if (!hal || vg_lite_init(0, 0) != VG_LITE_SUCCESS) {
        printf("FAIL: init\n");
        return 1;
    }
    job_t ref[NT], par[NT];
    pthread_t th[NT];
    for (int i = 0; i < NT; i++) {
        memset(&ref[i], 0, sizeof ref[i]);
        ref[i].id = i;
        sequence(&ref[i]);
    }
    long locks_before = g_locks;
    for (int i = 0; i < NT; i++) {
        memset(&par[i], 0, sizeof par[i]);
        par[i].id = i;
        pthread_create(&th[i], NULL, sequence, &par[i]);
    }
    int calls = 0;
    for (int i = 0; i < NT; i++) {
        pthread_join(th[i], NULL);
        calls += par[i].calls;
        if (ref[i].errors || par[i].errors) {
            printf("FAIL: thread %d errors ref %d par %d\n", i, ref[i].errors, par[i].errors);
            fail = 1;
        }
        if (ref[i].crc != par[i].crc) {
            printf("FAIL: thread %d crc %08x (alone) != %08x (concurrent)\n", i, ref[i].crc, par[i].crc);
            fail = 1;
        }
    }
    long locks = g_locks - locks_before;
    if (g_nested) {
        printf("FAIL: lock taken again inside a locked call (%d times)\n", g_nested);
        fail = 1;
    }
    if (g_max_inside != 1) {
        printf("FAIL: %d threads inside the driver at once\n", g_max_inside);
        fail = 1;
    }
    if (locks != calls) {
        printf("FAIL: %ld locked calls for %d public calls\n", locks, calls);
        fail = 1;
    }
    vg_lite_close();
    g2d_hal_close(hal);
    printf("%s: %d threads x %d rounds, %d calls, crc[0] %08x\n", fail ? "FAIL" : "PASS", NT, ROUNDS, calls, par[0].crc);
    return fail;
}
