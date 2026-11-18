// SPDX-License-Identifier: Apache-2.0
/* G2D vg_lite driver: context, command buffers, state shadow, submission, queries (vg_lite_drv.h). */
#include <string.h>

#include "vg_lite_drv.h"

drv_t g_drv;

/* Driver lock (v2.0, C-1): drv_api.c takes it around every public call. Single-threaded backends need nothing;
 * an RTOS / OS backend overrides these weak no-ops (sw/hal/g2d_hal.h). */
__attribute__((weak)) void g2d_hal_lock(void) {}
__attribute__((weak)) void g2d_hal_unlock(void) {}

static const uint32_t k_st_reset[G2D_ST_SIZE] = G2D_ST_RESET_INIT;
static const uint32_t k_st_wmask[G2D_ST_SIZE] = G2D_ST_WMASK_INIT;
static uint32_t g_cmd_bytes = DRV_CMD_DEFAULT;   /* vg_lite_set_command_buffer_size, before vg_lite_init */

/* ------------------------------------------------------------------------------------------------ */
/* CSR access                                                                                       */
/* ------------------------------------------------------------------------------------------------ */

uint32_t drv_rd(uint32_t off) { return g_drv.hal->reg_read(g_drv.hal->ctx, off); }
void drv_wr(uint32_t off, uint32_t v) { g_drv.hal->reg_write(g_drv.hal->ctx, off, v); }

static uint32_t status(void) { return drv_rd(G2D_CSR_STATUS); }

vg_lite_error_t drv_check_open(void)
{
    if (!g_drv.open)
        return VG_LITE_NO_CONTEXT;
    return g_drv.fault ? VG_LITE_GENERIC_IO : VG_LITE_SUCCESS;   /* sticky fault (drv_quiesce) */
}

/* Timeouts (v2.0, C-6): elapsed time from the HAL clock if it has one, else counted wait intervals. */
typedef struct {
    uint64_t t0;
    uint32_t waited;
} drv_tmo_t;

static void tmo_start(drv_tmo_t *t)
{
    const g2d_hal_t *h = g_drv.hal;
    t->waited = 0;
    t->t0 = h->time_us ? h->time_us(h->ctx) : 0;
}

static int tmo_expired(const drv_tmo_t *t)
{
    const g2d_hal_t *h = g_drv.hal;
    if (h->time_us)
        return h->time_us(h->ctx) - t->t0 >= DRV_TIMEOUT_US;
    return t->waited >= DRV_TIMEOUT_US;
}

static void tmo_wait(drv_tmo_t *t)
{
    g_drv.hal->wait(g_drv.hal->ctx, DRV_WAIT_US);
    t->waited += DRV_WAIT_US;
}

/* The GPU is verifiably stopped: no soft reset in progress, kick queue empty, nothing executing. */
static int gpu_stopped(void)
{
    uint32_t st = status();
    return !G2D_FIELD_GET(G2D_CSR_STATUS_SOFT_RESET_BUSY, st) && G2D_FIELD_GET(G2D_CSR_STATUS_IDLE, st);
}

/* Soft reset (arch.md §2.2) and reset of everything the driver knows about the GPU. */
static vg_lite_error_t gpu_soft_reset(void)
{
    drv_tmo_t tmo;
    tmo_start(&tmo);
    drv_wr(G2D_CSR_CTRL, G2D_FIELD_SET(G2D_CSR_CTRL_SOFT_RESET, 1));
    while (G2D_FIELD_GET(G2D_CSR_STATUS_SOFT_RESET_BUSY, status())) {
        if (tmo_expired(&tmo))
            return VG_LITE_TIMEOUT;
        tmo_wait(&tmo);
    }
    /* the state registers are back at their reset values; fences restart at 0 */
    memcpy(g_drv.st_gpu, k_st_reset, sizeof g_drv.st_gpu);
    memset(g_drv.st_known, 1, sizeof g_drv.st_known);
    g_drv.seq = drv_rd(G2D_CSR_FENCE_SEQ);
    drv_wr(G2D_CSR_IRQ_STATUS, 0xFFFFFFFFu);
    return VG_LITE_SUCCESS;
}

/* ------------------------------------------------------------------------------------------------ */
/* Command buffers                                                                                  */
/* ------------------------------------------------------------------------------------------------ */

/* A buffer the GPU has finished (fence passed, or the GPU stopped by a reset): the CPU may now read its targets,
 * so their lines are invalidated (v1.6, non-coherent systems); then its scratch is released. */
static void retire_buffer(drv_cmdbuf_t *b)
{
    int i;
    for (i = 0; i < b->nwt; i++)
        g_drv.hal->cache_invalidate(g_drv.hal->ctx, b->wt[i].cpu, b->wt[i].bytes);
    b->nwt = 0;
}

static void release_scratch(drv_cmdbuf_t *b)
{
    drv_alloc_t *a = b->extra;
    while (a) {
        drv_alloc_t *n = a->next;
        g_drv.hal->mem_free(g_drv.hal->ctx, a->cpu, a->gpu);
        a = n;
    }
    b->extra = NULL;
    b->arena_used = 0;
    b->clut_gen = 0;
    b->cramp_gpu = 0;      /* v1.6b: colour-transformed ramp copy */
}

static int fence_passed(uint32_t fence) { return (int32_t)(drv_rd(G2D_CSR_FENCE_SEQ) - fence) >= 0; }

/* Forget every kicked buffer and free its scratch: only once the GPU is known to be stopped. */
static void forget_buffers(void)
{
    int i;
    for (i = 0; i < DRV_NBUF; i++) {
        g_drv.cb[i].busy = 0;
        retire_buffer(&g_drv.cb[i]);
        release_scratch(&g_drv.cb[i]);
    }
}

/* After a halt: record the error, soft reset, and only then (the GPU stopped) forget every in-flight buffer.
 * Draws issued before the halt may still run until the reset (arch.md §8), so their scratch stays allocated
 * if the reset cannot be confirmed (sticky fault, v1.6 D-2). */
static vg_lite_error_t recover(void)
{
    g_drv.last_err_status = drv_rd(G2D_CSR_ERR_STATUS);
    if (gpu_soft_reset() != VG_LITE_SUCCESS || !gpu_stopped()) {
        g_drv.fault = 1;
        return VG_LITE_TIMEOUT;
    }
    forget_buffers();
    return VG_LITE_GENERIC_IO;
}

/* Wait until fence has passed (or the GPU halts / the wait times out). */
static vg_lite_error_t wait_fence(uint32_t fence)
{
    drv_tmo_t tmo;
    tmo_start(&tmo);
    for (;;) {
        uint32_t st;
        if (fence_passed(fence))
            return VG_LITE_SUCCESS;
        st = status();
        if (G2D_FIELD_GET(G2D_CSR_STATUS_HALTED, st))
            return recover();
        if (tmo_expired(&tmo))
            return VG_LITE_TIMEOUT;
        drv_wr(G2D_CSR_IRQ_STATUS, G2D_FIELD_SET(G2D_CSR_IRQ_STATUS_FENCE, 1));   /* re-arm the interrupt */
        if (fence_passed(fence))
            return VG_LITE_SUCCESS;
        tmo_wait(&tmo);
    }
}

static vg_lite_error_t buffer_free(drv_cmdbuf_t *b)
{
    vg_lite_error_t e = VG_LITE_SUCCESS;
    if (b->busy) {
        e = wait_fence(b->fence);
        if (e != VG_LITE_SUCCESS && e != VG_LITE_GENERIC_IO)
            return e;
        b->busy = 0;
        retire_buffer(b);
        release_scratch(b);
    }
    return e;
}

static vg_lite_error_t kick(drv_cmdbuf_t *b)
{
    uint32_t st;
    drv_tmo_t tmo;
    const g2d_hal_t *h = g_drv.hal;
    drv_alloc_t *a;
    h->cache_clean(h->ctx, b->cpu, b->used * 4u);
    if (b->arena_used)
        h->cache_clean(h->ctx, b->arena_cpu, b->arena_used);
    for (a = b->extra; a; a = a->next)         /* scratch beyond the arena (D-1: written after its allocation) */
        h->cache_clean(h->ctx, a->cpu, a->bytes);
    tmo_start(&tmo);
    for (;;) {
        st = status();
        if (G2D_FIELD_GET(G2D_CSR_STATUS_HALTED, st))
            return recover();
        if (G2D_FIELD_GET(G2D_CSR_STATUS_KICK_FREE, st))
            break;
        if (tmo_expired(&tmo))
            return VG_LITE_TIMEOUT;
        tmo_wait(&tmo);
    }
    drv_wr(G2D_CSR_CMD_ADDR, b->gpu);
    drv_wr(G2D_CSR_CMD_KICK, G2D_FIELD_SET(G2D_CSR_CMD_KICK_SIZE, b->used * 4u));
    b->busy = 1;
    return VG_LITE_SUCCESS;
}

/* Kick the current buffer (if it holds commands) and make the next one current. wait_all: also wait
 * until everything kicked so far has completed. */
vg_lite_error_t drv_flush(int wait_all)
{
    drv_cmdbuf_t *b = &g_drv.cb[g_drv.cur];
    vg_lite_error_t e = VG_LITE_SUCCESS;
    if (b->used) {
        g_drv.seq++;
        b->cpu[b->used++] = (G2D_OP_FENCE << 24) | 1u;   /* IRQ: wakes a waiting HAL */
        b->cpu[b->used++] = g_drv.seq;
        if (b->used & 1u)
            b->cpu[b->used++] = G2D_OP_NOP << 24;      /* kicks are a multiple of 8 bytes (commands.md §1) */
        b->fence = g_drv.seq;
        e = kick(b);
        if (e != VG_LITE_SUCCESS)
            return e;
        g_drv.cur = (g_drv.cur + 1) % DRV_NBUF;
        e = buffer_free(&g_drv.cb[g_drv.cur]);
        g_drv.cb[g_drv.cur].used = 0;
        if (e != VG_LITE_SUCCESS)
            return e;
    }
    if (wait_all) {
        int i;
        for (i = 0; i < DRV_NBUF; i++) {
            e = buffer_free(&g_drv.cb[i]);
            if (e != VG_LITE_SUCCESS)
                return e;
        }
    }
    return e;
}

vg_lite_error_t drv_quiesce(int *safe)
{
    vg_lite_error_t e;
    *safe = 0;
    if (g_drv.fault)
        return VG_LITE_GENERIC_IO;
    e = drv_flush(1);
    if (e == VG_LITE_SUCCESS) {
        *safe = 1;
        return e;
    }
    /* the wait failed (timeout, halt): the GPU may still use the memory; stop it and check before any free */
    if (!g_drv.fault && gpu_soft_reset() == VG_LITE_SUCCESS && gpu_stopped()) {
        forget_buffers();
        *safe = 1;
    } else {
        g_drv.fault = 1;
    }
    return e;
}

vg_lite_error_t drv_reserve(uint32_t n)
{
    drv_cmdbuf_t *b = &g_drv.cb[g_drv.cur];
    uint32_t cap = g_drv.cmd_bytes / 4u;
    /* + FENCE (2 words) + one NOP to keep the size a multiple of 8 bytes */
    if (n + 3u > cap)
        return VG_LITE_OUT_OF_RESOURCES;
    /* also a new buffer when this one's target list is full: the draw records its target at emission */
    if (b->used + n + 3u > cap || (b->used && b->nwt == DRV_WT_MAX)) {
        vg_lite_error_t e = drv_flush(0);
        if (e != VG_LITE_SUCCESS)
            return e;
    }
    return VG_LITE_SUCCESS;
}

uint32_t drv_draw_words(uint32_t n)
{
    /* state words (LOAD_STATE runs, at most two words per register) + per scissor piece the CLIP update and draw */
    return G2D_ST_SIZE * 2u + n + (g_drv.scissor_on && g_drv.nsr ? (uint32_t)g_drv.nsr * (n + 4u) : 0u);
}

static void note_target(void)
{
    drv_cmdbuf_t *b = &g_drv.cb[g_drv.cur];
    int i;
    if (!g_drv.tgt_cpu)
        return;
    for (i = 0; i < b->nwt; i++) {
        if (b->wt[i].cpu == g_drv.tgt_cpu) {
            if (g_drv.tgt_bytes > b->wt[i].bytes)
                b->wt[i].bytes = g_drv.tgt_bytes;
            return;
        }
    }
    b->wt[b->nwt].cpu = g_drv.tgt_cpu;     /* room: drv_reserve started a new buffer when the list was full */
    b->wt[b->nwt].bytes = g_drv.tgt_bytes;
    b->nwt++;
}

vg_lite_error_t drv_emit_draw(const uint32_t *w, uint32_t n)
{
    vg_lite_error_t e;
    uint32_t k;
    int i;
    note_target();
    if ((e = drv_color_ramp()) != VG_LITE_SUCCESS)    /* v1.6b: gradients under the colour unit */
        return e;
    if (!g_drv.scissor_on || !g_drv.nsr) {
        if ((e = drv_emit_state()) != VG_LITE_SUCCESS)
            return e;
        for (k = 0; k < n; k++)
            drv_emit(w[k]);
        return VG_LITE_SUCCESS;
    }
    /* vg_lite_scissor_rects: the draw once per disjoint piece of the union, each clipped by the outer clip
     * (scissor bounding box ∩ target) the caller requested: every pixel at most once (rendering.md §6) */
    {
        uint32_t cmin = g_drv.st_want[G2D_ST_CLIP_MIN], cmax = g_drv.st_want[G2D_ST_CLIP_MAX];
        int32_t ox0 = (int32_t)G2D_FIELD_GET(G2D_ST_CLIP_MIN_X0, cmin), oy0 = (int32_t)G2D_FIELD_GET(G2D_ST_CLIP_MIN_Y0, cmin);
        int32_t ox1 = (int32_t)G2D_FIELD_GET(G2D_ST_CLIP_MAX_X1, cmax), oy1 = (int32_t)G2D_FIELD_GET(G2D_ST_CLIP_MAX_Y1, cmax);
        for (i = 0; i < g_drv.nsr; i++) {
            int32_t x0 = g_drv.sr[i].x0 > ox0 ? g_drv.sr[i].x0 : ox0, y0 = g_drv.sr[i].y0 > oy0 ? g_drv.sr[i].y0 : oy0;
            int32_t x1 = g_drv.sr[i].x1 < ox1 ? g_drv.sr[i].x1 : ox1, y1 = g_drv.sr[i].y1 < oy1 ? g_drv.sr[i].y1 : oy1;
            if (x1 <= x0 || y1 <= y0)
                continue;
            drv_state(G2D_ST_CLIP_MIN, G2D_FIELD_SET(G2D_ST_CLIP_MIN_X0, (uint32_t)x0) |
                                       G2D_FIELD_SET(G2D_ST_CLIP_MIN_Y0, (uint32_t)y0));
            drv_state(G2D_ST_CLIP_MAX, G2D_FIELD_SET(G2D_ST_CLIP_MAX_X1, (uint32_t)x1) |
                                       G2D_FIELD_SET(G2D_ST_CLIP_MAX_Y1, (uint32_t)y1));
            if ((e = drv_emit_state()) != VG_LITE_SUCCESS)
                return e;
            for (k = 0; k < n; k++)
                drv_emit(w[k]);
        }
    }
    return VG_LITE_SUCCESS;
}

void drv_emit(uint32_t w)
{
    drv_cmdbuf_t *b = &g_drv.cb[g_drv.cur];
    b->cpu[b->used++] = w;
}

vg_lite_error_t drv_scratch(uint32_t bytes, void **cpu, uint32_t *gpu)
{
    drv_cmdbuf_t *b = &g_drv.cb[g_drv.cur];
    uint32_t off = (b->arena_used + 63u) & ~63u;      /* 64-byte aligned: also cache-line friendly */
    if (bytes == 0)
        bytes = 16;
    if (off + bytes <= g_drv.arena_bytes) {
        b->arena_used = off + bytes;
        *cpu = b->arena_cpu + off;
        *gpu = b->arena_gpu + off;
        return VG_LITE_SUCCESS;
    } else {
        /* too large for the arena: a dedicated allocation owned by this buffer */
        void *c;
        uint32_t g;
        drv_alloc_t *node;
        size_t nsz = (sizeof(drv_alloc_t) + 63u) & ~(size_t)63u;
        if (g_drv.hal->mem_alloc(g_drv.hal->ctx, nsz + bytes, 64, &c, &g))
            return VG_LITE_OUT_OF_MEMORY;
        node = (drv_alloc_t *)c;
        node->cpu = c;
        node->gpu = g;
        node->bytes = (uint32_t)nsz + bytes;
        node->next = b->extra;
        b->extra = node;
        *cpu = (uint8_t *)c + nsz;
        *gpu = g + (uint32_t)nsz;
        return VG_LITE_SUCCESS;          /* cleaned with its payload at the kick (D-1) */
    }
}

/* ------------------------------------------------------------------------------------------------ */
/* State shadow                                                                                     */
/* ------------------------------------------------------------------------------------------------ */

void drv_state(unsigned idx, uint32_t value)
{
    g_drv.st_want[idx] = value & k_st_wmask[idx];
    g_drv.st_need[idx] = 1;
}

static int st_dirty(unsigned i)
{
    return g_drv.st_need[i] && (!g_drv.st_known[i] || g_drv.st_gpu[i] != g_drv.st_want[i]);
}

vg_lite_error_t drv_emit_state(void)
{
    unsigned i = 0, words = 0;
    vg_lite_error_t e;
    for (i = 0; i < G2D_ST_SIZE; i++)
        words += st_dirty(i) ? 2u : 0u;                  /* upper bound: one header per word */
    e = drv_reserve(words);
    if (e != VG_LITE_SUCCESS)
        return e;
    i = 0;
    while (i < G2D_ST_SIZE) {
        unsigned j;
        if (!st_dirty(i)) {
            g_drv.st_need[i] = 0;
            i++;
            continue;
        }
        j = i;
        while (j < G2D_ST_SIZE && st_dirty(j))
            j++;
        drv_emit((G2D_OP_LOAD_STATE << 24) | ((j - i) << 16) | i);
        for (; i < j; i++) {
            drv_emit(g_drv.st_want[i]);
            g_drv.st_gpu[i] = g_drv.st_want[i];
            g_drv.st_known[i] = 1;
            g_drv.st_need[i] = 0;
        }
    }
    return VG_LITE_SUCCESS;
}

/* ------------------------------------------------------------------------------------------------ */
/* API: context                                                                                     */
/* ------------------------------------------------------------------------------------------------ */

vg_lite_error_t vg_lite_set_command_buffer_size(vg_lite_uint32_t size)
{
    if (g_drv.open)
        return VG_LITE_ALREADY_EXISTS;
    if (size < 4096u || size % 64u)
        return VG_LITE_INVALID_ARGUMENT;
    g_cmd_bytes = size;
    return VG_LITE_SUCCESS;
}

/* The caller's 64-byte aligned GPU memory (api-subset.md §1) replaces the driver's command buffers: it is split
 * into the two buffers the driver alternates between (size / 2 each, a multiple of 64 and at least 4 KB). The
 * driver first waits until the GPU is idle; the memory stays the caller's (vg_lite_close does not free it). */
vg_lite_error_t vg_lite_set_command_buffer(vg_lite_uint32_t physical, vg_lite_uint32_t size)
{
    vg_lite_error_t e = drv_check_open();
    uint32_t half = (size / 2u) & ~63u;
    uint8_t *cpu;
    int i;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (physical % 64u)
        return VG_LITE_NOT_ALIGNED;
    if (half < 4096u || physical + (uint64_t)size > 0x100000000ull)
        return VG_LITE_INVALID_ARGUMENT;
    if (!(cpu = (uint8_t *)g_drv.hal->mem_cpu(g_drv.hal->ctx, physical, size)))
        return VG_LITE_INVALID_ARGUMENT;   /* not memory the HAL can reach */
    {
        int safe;
        if ((e = drv_quiesce(&safe)) != VG_LITE_SUCCESS)
            return e;                      /* the old buffers stay as they are (D-2) */
    }
    for (i = 0; i < DRV_NBUF; i++) {
        if (!g_drv.cmd_user)
            g_drv.hal->mem_free(g_drv.hal->ctx, g_drv.cb[i].cpu, g_drv.cb[i].gpu);
        g_drv.cb[i].cpu = (uint32_t *)(cpu + (size_t)i * half);
        g_drv.cb[i].gpu = physical + (uint32_t)i * half;
        g_drv.cb[i].used = 0;
    }
    g_drv.cmd_bytes = half;
    g_drv.cmd_user = 1;
    g_drv.cur = 0;
    return VG_LITE_SUCCESS;
}

/* One memory pool (the HAL's): POOL_1 is accepted for every buffer type, POOL_2 is not supported. */
vg_lite_error_t vg_lite_set_memory_pool(vg_lite_buffer_type_t type, vg_lite_memory_pool_t pool)
{
    if (type != VG_LITE_COMMAND_BUFFER && type != VG_LITE_TESSELLATION_BUFFER && type != VG_LITE_RENDER_BUFFER)
        return VG_LITE_INVALID_ARGUMENT;
    if (pool == VG_LITE_MEMORY_POOL_1)
        return VG_LITE_SUCCESS;
    return pool == VG_LITE_MEMORY_POOL_2 ? VG_LITE_NOT_SUPPORT : VG_LITE_INVALID_ARGUMENT;
}

/* End of a submission (VG_LITE_END_FLAG) or frame (VG_LITE_FRAME_END_FLAG): flush; stall_flag also waits until the
 * GPU is idle (vg_lite_finish). The FENCE that ends the buffer raises the interrupt. */
vg_lite_error_t vg_lite_frame_delimiter(vg_lite_frame_flag_t flag, vg_lite_bool_t stall_flag)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (flag != VG_LITE_END_FLAG && flag != VG_LITE_FRAME_END_FLAG)
        return VG_LITE_INVALID_ARGUMENT;
    return drv_flush(stall_flag ? 1 : 0);
}

vg_lite_error_t vg_lite_init(vg_lite_uint32_t tess_width, vg_lite_uint32_t tess_height)
{
    int i;
    vg_lite_error_t e;
    (void)tess_width;   /* no tessellation buffer (api-subset.md §1) */
    (void)tess_height;
    if (g_drv.open)
        return VG_LITE_SUCCESS;
    memset(&g_drv, 0, sizeof g_drv);
    g_drv.hal = g2d_hal_open();
    if (!g_drv.hal)
        return VG_LITE_GENERIC_IO;
    if (drv_rd(G2D_CSR_ID) != G2D_CSR_ID_RESET) {
        g2d_hal_close(g_drv.hal);
        return VG_LITE_GENERIC_IO;
    }
    g_drv.kick_depth = G2D_FIELD_GET(G2D_CSR_HWCFG_KICK_DEPTH, drv_rd(G2D_CSR_HWCFG));
    g_drv.features = (drv_rd(G2D_CSR_HWCFG) >> G2D_CSR_HWCFG_FEAT_FAMILY_SHIFT) & 0x3Fu;   /* v1.7 */
    g_drv.cmd_bytes = g_cmd_bytes;
    g_drv.arena_bytes = DRV_ARENA_DEFAULT;
    /* each allocation is recorded at once, so the one cleanup path below frees exactly what exists (D-3) */
    for (i = 0; i < DRV_NBUF; i++) {
        void *c, *a;
        e = VG_LITE_OUT_OF_MEMORY;
        if (g_drv.hal->mem_alloc(g_drv.hal->ctx, g_drv.cmd_bytes, 64, &c, &g_drv.cb[i].gpu))
            goto fail;
        g_drv.cb[i].cpu = (uint32_t *)c;
        if (g_drv.hal->mem_alloc(g_drv.hal->ctx, g_drv.arena_bytes, 64, &a, &g_drv.cb[i].arena_gpu))
            goto fail;
        g_drv.cb[i].arena_cpu = (uint8_t *)a;
    }
    e = gpu_soft_reset();
    if (e != VG_LITE_SUCCESS)
        goto fail;
    drv_wr(G2D_CSR_IRQ_ENABLE, G2D_FIELD_SET(G2D_CSR_IRQ_ENABLE_FENCE, 1) | G2D_FIELD_SET(G2D_CSR_IRQ_ENABLE_ERROR, 1));
    if (g_drv.hal->mem_base_hi)                     /* v2.0 (C-5): the 4 GB window of all GPU memory */
        drv_wr(G2D_CSR_MEM_BASE_HI, g_drv.hal->mem_base_hi(g_drv.hal->ctx));
    g_drv.scissor_on = 0;
    g_drv.src_amode = g_drv.dst_amode = 0;
    g_drv.gauss_w = 512u | (256u << 16);     /* until vg_lite_gaussian_filter: [1 2 1] x [1 2 1] / 16 (v1.6b, */
    g_drv.gauss_w0 = 1024u;                  /* s3.12; assumption, OPEN_QUESTIONS Q-API-27) */
    g_drv.src_aval = g_drv.dst_aval = 255;
    g_drv.clut_gen = 1;                    /* an all-zero CLUT until vg_lite_set_CLUT */
    g_drv.open = 1;
    return VG_LITE_SUCCESS;

fail:   /* nothing was kicked: the GPU uses none of this memory */
    for (i = 0; i < DRV_NBUF; i++) {
        if (g_drv.cb[i].cpu)
            g_drv.hal->mem_free(g_drv.hal->ctx, g_drv.cb[i].cpu, g_drv.cb[i].gpu);
        if (g_drv.cb[i].arena_cpu)
            g_drv.hal->mem_free(g_drv.hal->ctx, g_drv.cb[i].arena_cpu, g_drv.cb[i].arena_gpu);
    }
    g2d_hal_close(g_drv.hal);
    memset(&g_drv, 0, sizeof g_drv);
    return e;
}

vg_lite_error_t vg_lite_close(vg_lite_void)
{
    int i, safe;
    vg_lite_error_t e;
    if (!g_drv.open)
        return VG_LITE_SUCCESS;
    /* GPU-visible memory is freed only once the GPU is known to be stopped (D-4); otherwise it is left
     * allocated (an intentional leak) and the error is returned */
    e = drv_quiesce(&safe);
    if (safe) {
        for (i = 0; i < DRV_NBUF; i++) {
            release_scratch(&g_drv.cb[i]);
            if (g_drv.cb[i].cpu && !g_drv.cmd_user)
                g_drv.hal->mem_free(g_drv.hal->ctx, g_drv.cb[i].cpu, g_drv.cb[i].gpu);
            if (g_drv.cb[i].arena_cpu)
                g_drv.hal->mem_free(g_drv.hal->ctx, g_drv.cb[i].arena_cpu, g_drv.cb[i].arena_gpu);
        }
    }
    g2d_hal_close(g_drv.hal);
    memset(&g_drv, 0, sizeof g_drv);
    return safe ? VG_LITE_SUCCESS : e;
}

vg_lite_error_t vg_lite_flush(vg_lite_void)
{
    vg_lite_error_t e = drv_check_open();
    return e != VG_LITE_SUCCESS ? e : drv_flush(0);
}

vg_lite_error_t vg_lite_finish(vg_lite_void)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    return drv_flush(1);             /* retires every buffer: their targets are invalidated (retire_buffer) */
}

/* ------------------------------------------------------------------------------------------------ */
/* API: queries                                                                                     */
/* ------------------------------------------------------------------------------------------------ */

vg_lite_error_t vg_lite_get_info(vg_lite_info_t *info)
{
    if (!info)
        return VG_LITE_INVALID_ARGUMENT;
    info->api_version = VGLITE_API_VERSION_3_0;
    info->header_version = VGLITE_HEADER_VERSION;
    info->release_version = VGLITE_RELEASE_VERSION;
    info->reserved = 0;
    return VG_LITE_SUCCESS;
}

vg_lite_uint32_t vg_lite_get_product_info(vg_lite_char *name, vg_lite_uint32_t *chip_id, vg_lite_uint32_t *chip_rev)
{
    static const char k_name[] = "G2D";
    if (name)
        memcpy(name, k_name, sizeof k_name);
    if (chip_id)
        *chip_id = G2D_CSR_ID_RESET;
    if (chip_rev)
        *chip_rev = g_drv.open ? drv_rd(G2D_CSR_VERSION) : G2D_CSR_VERSION_RESET;
    return (vg_lite_uint32_t)(sizeof k_name - 1);
}

/* Feature bits are true only for what v1 implements (api-subset.md §1). */
vg_lite_uint32_t vg_lite_query_feature(vg_lite_feature_t feature)
{
    switch (feature) {
    case gcFEATURE_BIT_VG_SCISSOR:              /* one rectangle */
    case gcFEATURE_BIT_VG_GLOBAL_ALPHA:
    case gcFEATURE_BIT_VG_COLOR_KEY:
    case gcFEATURE_BIT_VG_LINEAR_GRADIENT_EXT:
    case gcFEATURE_BIT_VG_LVGL_SUPPORT:         /* blend modes 11-15 */
    case gcFEATURE_BIT_VG_HW_PREMULTIPLY:       /* apply_premult on source reads */
    case gcFEATURE_BIT_VG_IM_INPUT:
    case gcFEATURE_BIT_VG_IM_REPEAT_REFLECT:    /* REPEAT / REFLECT spread and image wrap (v1.4) */
    case gcFEATURE_BIT_VG_IM_INDEX_FORMAT:      /* v1.4: INDEX_1/2/4/8 with vg_lite_set_CLUT */
    case gcFEATURE_BIT_VG_INDEX_ENDIAN:         /* v1.4: index_endian honoured (SRC_FMT.SUB_ORDER) */
    case gcFEATURE_BIT_VG_DITHER:               /* v1.4: own ordered dither (rendering.md §3.6) */
    case gcFEATURE_BIT_VG_A124_A8L8:            /* v1.4 A1 / A2 / A4, v1.6 A8L8 sources */
    case gcFEATURE_BIT_VG_24BIT:                /* v1.6b: 24-bit sources in hardware (rendering.md §3.2) */
        return 1;
    /* v1.7: units that a build may leave out (HWCFG.FEAT_*) */
    case gcFEATURE_BIT_VG_RADIAL_GRADIENT:      /* v1.5: radial gradients with a focal point (rendering.md §10.4) */
        return drv_has(DRV_FEAT_RADIAL);
    case gcFEATURE_BIT_VG_GAUSSIAN_BLUR:        /* v1.6b: 3 x 3 Gaussian filter (rendering.md §7.3) */
        return drv_has(DRV_FEAT_GAUSS);
    case gcFEATURE_BIT_VG_YUV_INPUT:            /* v1.6b: NV12 / NV16 / NV24 / YV12 / YV16 / YV24 sources (§3.7) */
    case gcFEATURE_BIT_VG_YUY2_INPUT:           /* v1.6b: YUY2 */
    case gcFEATURE_BIT_VG_AYUV_INPUT:           /* v1.6b: ANV12 / AYUY2 */
        return drv_has(DRV_FEAT_YUV);
    case gcFEATURE_BIT_VG_GAMMA:                /* v1.6b: colour unit (rendering.md §5.2.1) */
    case gcFEATURE_BIT_VG_COLOR_TRANSFORMATION:
    case gcFEATURE_BIT_VG_PIXEL_MATRIX:
        return drv_has(DRV_FEAT_COLOR);
    default:
        return 0;
    }
}

vg_lite_error_t vg_lite_get_register(vg_lite_uint32_t address, vg_lite_uint32_t *result)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!result || address % 4u || address >= 4096u)
        return VG_LITE_INVALID_ARGUMENT;
    *result = drv_rd(address);
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_get_mem_size(vg_lite_uint32_t *size)
{
    if (!size)
        return VG_LITE_INVALID_ARGUMENT;
    *size = 0;   /* no driver-managed memory pool: buffers come from the HAL */
    return VG_LITE_SUCCESS;
}

/* v1.6: there is no tessellation buffer (the driver flattens paths on the CPU, api-subset.md §1): accepted, unused. */
vg_lite_error_t vg_lite_set_tess_buffer(vg_lite_uint32_t physical, vg_lite_uint32_t size)
{
    vg_lite_error_t e = drv_check_open();
    (void)physical;
    (void)size;
    return e;
}

/* ------------------------------------------------------------------------------------------------ */
/* v2.0 G2D extensions: memory-port attributes and throttle (CSR configuration, applied at once). */

vg_lite_error_t vg_lite_g2d_set_mem_attr(const vg_lite_g2d_axi_attr_t *read, const vg_lite_g2d_axi_attr_t *write)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!read || !write || read->cache > 15u || read->prot > 7u || read->qos > 15u || write->cache > 15u ||
        write->prot > 7u || write->qos > 15u)
        return VG_LITE_INVALID_ARGUMENT;
    drv_wr(G2D_CSR_MEM_ATTR,
           G2D_FIELD_SET(G2D_CSR_MEM_ATTR_AR_CACHE, read->cache) | G2D_FIELD_SET(G2D_CSR_MEM_ATTR_AR_PROT, read->prot) |
               G2D_FIELD_SET(G2D_CSR_MEM_ATTR_AR_QOS, read->qos) | G2D_FIELD_SET(G2D_CSR_MEM_ATTR_AW_CACHE, write->cache) |
               G2D_FIELD_SET(G2D_CSR_MEM_ATTR_AW_PROT, write->prot) | G2D_FIELD_SET(G2D_CSR_MEM_ATTR_AW_QOS, write->qos));
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_g2d_set_mem_qos(vg_lite_uint32_t rd_max, vg_lite_uint32_t wr_max, vg_lite_uint32_t rd_gap,
                                        vg_lite_uint32_t wr_gap)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (rd_max > 31u || wr_max > 31u || rd_gap > 255u || wr_gap > 255u)
        return VG_LITE_INVALID_ARGUMENT;
    drv_wr(G2D_CSR_MEM_QOS, G2D_FIELD_SET(G2D_CSR_MEM_QOS_RD_MAX, rd_max) | G2D_FIELD_SET(G2D_CSR_MEM_QOS_WR_MAX, wr_max) |
                                G2D_FIELD_SET(G2D_CSR_MEM_QOS_RD_GAP, rd_gap) | G2D_FIELD_SET(G2D_CSR_MEM_QOS_WR_GAP, wr_gap));
    return VG_LITE_SUCCESS;
}
