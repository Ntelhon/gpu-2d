// SPDX-License-Identifier: Apache-2.0
/* v1.6 driver robustness tests (docs/FEATURES_LATER.md "Driver robustness", D-1..D-4) on the software backend,
 * with the model HAL's test controls (sw/hal/model/g2d_hal_model.h):
 *   - allocator failure at every allocation of vg_lite_init: OUT_OF_MEMORY and no leak (D-3);
 *   - a hung GPU whose soft reset works: vg_lite_free reports the error, resets the GPU, then frees (D-2);
 *   - a hung GPU whose soft reset never completes: no free, no ramp rewrite, the error is reported, the sticky
 *     fault makes every later call fail, vg_lite_close leaves the GPU memory allocated (D-2, D-4);
 *   - a non-coherent memory model (the GPU sees only ranges the driver cleaned, the CPU sees GPU results only
 *     after an invalidate): a path whose edge list does not fit the scratch arena (an extra allocation, D-1) and
 *     reading the target after vg_lite_finish give exactly the coherent result. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "g2d_hal.h"
#include "g2d_hal_model.h"
#include "generated/g2d_regs.h"
#include "vg_lite.h"
#include "vg_lite_g2d.h"

static int g_fail = 0, g_checks = 0;

#define CHECK(cond, ...)                                    \
    do {                                                    \
        g_checks++;                                         \
        if (!(cond)) {                                      \
            g_fail++;                                       \
            printf("FAIL %s:%d: ", __FILE__, __LINE__);     \
            printf(__VA_ARGS__);                            \
            printf("\n");                                   \
        }                                                   \
    } while (0)

#define OK(call) CHECK((call) == VG_LITE_SUCCESS, "%s", #call)

static vg_lite_buffer_t make(int w, int h)
{
    vg_lite_buffer_t b;
    memset(&b, 0, sizeof b);
    b.width = w;
    b.height = h;
    b.format = VG_LITE_BGRA8888;
    OK(vg_lite_allocate(&b));
    return b;
}

/* D-3: fail each of the first allocations of vg_lite_init in turn. */
static void t_init_alloc_failure(void)
{
    const g2d_hal_t *hal = g2d_hal_open();          /* keeps the model alive across the driver's close */
    int k, base = g2d_hal_model_live_allocs(), n_ok = -1;
    for (k = 0; k < 8; k++) {
        vg_lite_error_t e;
        g2d_hal_model_fail_alloc(k);
        e = vg_lite_init(0, 0);
        g2d_hal_model_fail_alloc(-1);
        if (e == VG_LITE_SUCCESS) {                 /* fewer than k + 1 allocations: init is done */
            n_ok = k;
            OK(vg_lite_close());
            break;
        }
        CHECK(e == VG_LITE_OUT_OF_MEMORY, "init with allocation %d failing: %d", k, (int)e);
        CHECK(g2d_hal_model_live_allocs() == base, "init with allocation %d failing leaks %d allocations", k,
              g2d_hal_model_live_allocs() - base);
    }
    CHECK(n_ok >= 4, "vg_lite_init made fewer allocations than expected (%d)", n_ok);
    CHECK(g2d_hal_model_live_allocs() == base, "close after a good init leaks");
    g2d_hal_close(hal);
}

/* D-2: hung GPU, soft reset works: the free happens after the reset, the error is reported, the driver recovers. */
static void t_stall_reset_works(void)
{
    const g2d_hal_t *hal = g2d_hal_open();
    vg_lite_buffer_t a, b;
    vg_lite_error_t e;
    int live;
    OK(vg_lite_init(0, 0));
    a = make(16, 8);
    b = make(16, 8);
    OK(vg_lite_finish());
    g2d_hal_model_stall(1, 1);
    OK(vg_lite_clear(&a, NULL, 0xFF0000FFu));      /* queued; the GPU never executes it */
    live = g2d_hal_model_live_allocs();
    e = vg_lite_free(&a);
    CHECK(e != VG_LITE_SUCCESS, "free on a hung GPU must report the error");
    CHECK(g2d_hal_model_live_allocs() == live - 1, "freed after a confirmed reset");
    CHECK(a.handle == NULL, "buffer released");
    OK(vg_lite_clear(&b, NULL, 0xFF00FF00u));      /* the driver works again after the reset */
    OK(vg_lite_finish());
    CHECK(*(uint32_t *)b.memory == 0xFF00FF00u, "draw after recovery: 0x%08x", *(uint32_t *)b.memory);
    OK(vg_lite_free(&b));
    OK(vg_lite_close());
    g2d_hal_close(hal);
}

/* v2.0 (C-5, C-6): MEM_BASE_HI comes from the HAL's mem_base_hi hook; with a HAL clock the timeout on a hung GPU
 * is measured in time (DRV_TIMEOUT_US of HAL time), without one in counted wait intervals. The model clock
 * advances 400 us per wait call, so the timeout takes a quarter of the wait calls. */
static uint64_t hung_wait_calls(void)
{
    vg_lite_buffer_t a = make(16, 8);
    uint64_t w0;
    vg_lite_error_t e;
    OK(vg_lite_finish());
    g2d_hal_model_stall(1, 1);
    OK(vg_lite_clear(&a, NULL, 0xFF0000FFu));
    w0 = g2d_hal_model_waits();
    e = vg_lite_finish();
    CHECK(e != VG_LITE_SUCCESS, "finish on a hung GPU must report the error");
    w0 = g2d_hal_model_waits() - w0;
    (void)vg_lite_free(&a);                        /* reports the hang again, then frees (D-2) */
    g2d_hal_model_stall(0, 1);
    return w0;
}

static void t_clock_and_base(void)
{
    const g2d_hal_t *hal = g2d_hal_open();
    uint64_t counted, timed;
    const uint64_t intervals = 10u * 1000u * 1000u / 100u;   /* DRV_TIMEOUT_US / DRV_WAIT_US */
    g2d_hal_model_base_hi(1, 0x5u);
    OK(vg_lite_init(0, 0));
    CHECK(hal->reg_read(hal->ctx, G2D_CSR_MEM_BASE_HI) == 0x5u, "MEM_BASE_HI from the HAL: 0x%x",
          (unsigned)hal->reg_read(hal->ctx, G2D_CSR_MEM_BASE_HI));
    counted = hung_wait_calls();
    g2d_hal_model_clock(400u);
    timed = hung_wait_calls();
    /* a hung finish runs into the timeout once per wait it makes (the same number in both runs) */
    CHECK(counted >= intervals && counted % intervals < 100u, "counted timeout: %llu waits", (unsigned long long)counted);
    CHECK(timed * 4u >= counted - 400u && timed * 4u <= counted + 400u, "timed timeout: %llu waits (counted %llu)",
          (unsigned long long)timed, (unsigned long long)counted);
    OK(vg_lite_close());
    g2d_hal_model_clock(0);
    g2d_hal_model_base_hi(0, 0);
    g2d_hal_close(hal);
}

/* v2.0 G2D extensions: memory attributes and throttle reach MEM_ATTR / MEM_QOS; out-of-range values and calls
 * before vg_lite_init are rejected without a register write. */
static void t_mem_api(void)
{
    const g2d_hal_t *hal = g2d_hal_open();
    vg_lite_g2d_axi_attr_t rd = {0xF, 0x2, 0x9}, wr = {0x7, 0x5, 0x3}, bad = {16, 0, 0};
    CHECK(vg_lite_g2d_set_mem_qos(1, 1, 1, 1) == VG_LITE_NO_CONTEXT, "before init");
    OK(vg_lite_init(0, 0));
    OK(vg_lite_g2d_set_mem_attr(&rd, &wr));
    CHECK(hal->reg_read(hal->ctx, G2D_CSR_MEM_ATTR) == 0x0357092Fu, "MEM_ATTR 0x%08x",
          (unsigned)hal->reg_read(hal->ctx, G2D_CSR_MEM_ATTR));
    CHECK(vg_lite_g2d_set_mem_attr(&bad, &wr) == VG_LITE_INVALID_ARGUMENT, "cache 16");
    CHECK(vg_lite_g2d_set_mem_attr(&rd, NULL) == VG_LITE_INVALID_ARGUMENT, "NULL");
    CHECK(hal->reg_read(hal->ctx, G2D_CSR_MEM_ATTR) == 0x0357092Fu, "unchanged after a rejected call");
    OK(vg_lite_g2d_set_mem_qos(3, 31, 0, 255));
    CHECK(hal->reg_read(hal->ctx, G2D_CSR_MEM_QOS) == 0xFF001F03u, "MEM_QOS 0x%08x",
          (unsigned)hal->reg_read(hal->ctx, G2D_CSR_MEM_QOS));
    CHECK(vg_lite_g2d_set_mem_qos(32, 0, 0, 0) == VG_LITE_INVALID_ARGUMENT, "rd_max 32");
    CHECK(vg_lite_g2d_set_mem_qos(0, 0, 256, 0) == VG_LITE_INVALID_ARGUMENT, "rd_gap 256");
    OK(vg_lite_g2d_set_mem_qos(0, 0, 0, 0));
    OK(vg_lite_close());
    g2d_hal_close(hal);
}

/* D-2 / D-4: hung GPU, soft reset never completes: nothing GPU-visible is freed or rewritten; sticky fault. */
static void t_stall_reset_fails(void)
{
    const g2d_hal_t *hal = g2d_hal_open();
    vg_lite_buffer_t a;
    vg_lite_ext_linear_gradient_t g;
    vg_lite_color_ramp_t ramp[2] = {{0.0f, 1.0f, 0.0f, 0.0f, 1.0f}, {1.0f, 0.0f, 0.0f, 1.0f, 1.0f}};
    vg_lite_linear_gradient_parameter_t lp = {0.0f, 0.0f, 16.0f, 0.0f};
    vg_lite_error_t e;
    uint8_t before[1024];
    int live, base = g2d_hal_model_live_allocs();
    OK(vg_lite_init(0, 0));
    a = make(16, 8);
    memset(&g, 0, sizeof g);
    OK(vg_lite_set_linear_grad(&g, 2, ramp, lp, VG_LITE_GRADIENT_SPREAD_PAD, 0));
    OK(vg_lite_update_linear_grad(&g));
    OK(vg_lite_finish());
    memcpy(before, g.image.memory, sizeof before);
    g2d_hal_model_stall(1, 0);
    OK(vg_lite_clear(&a, NULL, 0xFF0000FFu));
    ramp[0].red = 0.0f;                              /* a different ramp: the rewrite must not happen */
    OK(vg_lite_set_linear_grad(&g, 2, ramp, lp, VG_LITE_GRADIENT_SPREAD_PAD, 0));
    live = g2d_hal_model_live_allocs();
    e = vg_lite_update_linear_grad(&g);
    CHECK(e != VG_LITE_SUCCESS, "ramp update on a hung GPU must report the error");
    CHECK(memcmp(before, g.image.memory, sizeof before) == 0, "ramp rewritten while the GPU may read it (D-4)");
    e = vg_lite_free(&a);
    CHECK(e != VG_LITE_SUCCESS, "free must fail");
    CHECK(g2d_hal_model_live_allocs() == live, "memory freed although the GPU was not confirmed stopped (D-2)");
    CHECK(vg_lite_clear(&a, NULL, 0) == VG_LITE_GENERIC_IO, "sticky fault: later calls fail");
    e = vg_lite_close();
    CHECK(e != VG_LITE_SUCCESS, "close reports the fault");
    CHECK(g2d_hal_model_live_allocs() > base, "close must leave the GPU memory allocated (D-4)");
    g2d_hal_model_stall(0, 1);
    g2d_hal_close(hal);
}

/* D-1 + invalidate: the same frame with coherent and non-coherent memory. A star polygon with 20 000 edges (320 KB
 * of edges: more than the 256 KB scratch arena, so an extra allocation) and a clear. */
#define NV 20000
static void frame(int noncoherent, uint32_t *out, int w, int h)
{
    const g2d_hal_t *hal = g2d_hal_open();
    vg_lite_buffer_t t;
    vg_lite_path_t p;
    vg_lite_matrix_t m;
    float *data = (float *)malloc((size_t)(NV * 3 + 2) * sizeof(float));
    uint32_t *op;
    int i, y;
    if (noncoherent)
        g2d_hal_model_noncoherent(1);
    OK(vg_lite_init(0, 0));
    t = make(w, h);
    OK(vg_lite_clear(&t, NULL, 0xFF202020u));
    for (i = 0; i < NV; i++) {                       /* MOVE / LINE ops in FP32 path data (opcode as an element) */
        float r = (i & 1) ? 10.0f : 30.0f, a = 6.2831853f * (float)i / (float)NV;
        float c = 1.0f - a * a / 2.0f + a * a * a * a / 24.0f, s = a - a * a * a / 6.0f;   /* rough, any shape */
        op = (uint32_t *)&data[3 * i];
        *op = i == 0 ? 2u : 4u;
        data[3 * i + 1] = 32.0f + r * (a < 1.0f ? c : 0.5f);
        data[3 * i + 2] = 32.0f + r * (a < 1.0f ? s : 0.5f) + (float)(i % 17);
    }
    op = (uint32_t *)&data[3 * NV];
    *op = 0u;                                        /* END */
    OK(vg_lite_init_path(&p, VG_LITE_FP32, VG_LITE_HIGH, (vg_lite_uint32_t)((NV * 3 + 1) * sizeof(float)), data, 0, 0,
                         64, 64));
    OK(vg_lite_identity(&m));
    OK(vg_lite_draw(&t, &p, VG_LITE_FILL_EVEN_ODD, &m, VG_LITE_BLEND_SRC_OVER, 0xC000A0F0u));
    OK(vg_lite_finish());                            /* the CPU now reads the target (invalidated) */
    for (y = 0; y < h; y++)
        memcpy(out + (size_t)y * (size_t)w, (uint8_t *)t.memory + (size_t)y * (size_t)t.stride, (size_t)w * 4u);
    OK(vg_lite_clear_path(&p));
    OK(vg_lite_free(&t));
    OK(vg_lite_close());
    g2d_hal_close(hal);
    free(data);
}

static void t_noncoherent(void)
{
    enum { W = 64, H = 64 };
    static uint32_t a[W * H], b[W * H];
    int i, diff = 0, painted = 0;
    frame(0, a, W, H);
    frame(1, b, W, H);
    for (i = 0; i < W * H; i++) {
        diff += a[i] != b[i];
        painted += a[i] != 0xFF202020u;
    }
    CHECK(painted > 100, "the path draws something (%d pixels)", painted);
    CHECK(diff == 0, "non-coherent memory: %d pixels differ from the coherent run", diff);
}

int main(void)
{
    t_init_alloc_failure();
    t_stall_reset_works();
    t_clock_and_base();
    t_mem_api();
    t_stall_reset_fails();
    t_noncoherent();
    printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
