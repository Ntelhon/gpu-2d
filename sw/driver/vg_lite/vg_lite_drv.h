// SPDX-License-Identifier: Apache-2.0
/* G2D vg_lite driver: internal interfaces (not part of the API).
 *
 * Structure (docs/PROGRAMMING.md):
 *   drv_core.c    context, command buffers, state shadow, submission, info / feature queries
 *   drv_buffer.c  buffer allocation, upload, map
 *   drv_draw.c    clear, blit, copy, scissor, global alpha, colour key
 *   drv_matrix.c  matrices (own sin / cos), perspective transform from four points
 *   drv_path.c    paths and path drawing (edges through sw/lib/geom)
 *   drv_grad.c    legacy and extended linear gradients
 *   drv_pattern.c image pattern fills (driver-only: rectangular paths as blits, rendering.md §9.5)
 *   vg_lite_stubs.c  generated: every other vg_lite.h function (NOT_SUPPORT / no-op)
 *
 * Submission: two command buffers in GPU memory. Commands go into the current one; flush appends a FENCE
 * with the next sequence number and kicks it. A buffer is reused only after FENCE_SEQ has passed its fence,
 * which also means every draw in it has finished (a FENCE waits for all earlier writes, arch.md §5.2).
 * Per-draw data the GPU reads (edge lists, ramps of transient objects) lives in a scratch arena owned by
 * the command buffer and is released with it.
 */
#ifndef VG_LITE_DRV_H
#define VG_LITE_DRV_H

#include <stddef.h>
#include <stdint.h>

#include "g2d_geom.h"
#include "g2d_hal.h"
#include "generated/g2d_regs.h"
#include "drv_api.h"      /* v2.0: public names -> drv__* (generated); the locking wrappers are in drv_api.c */
#include "vg_lite.h"
#include "vg_lite_g2d.h"

#define DRV_NBUF 2
#define DRV_WT_MAX 16            /* distinct targets per command buffer, invalidated when it retires (v1.6) */
#define DRV_SR_MAX 256           /* disjoint pieces of vg_lite_scissor_rects (v1.6) */
#define DRV_CMD_DEFAULT (64u * 1024u)        /* bytes per command buffer */
#define DRV_ARENA_DEFAULT (256u * 1024u)     /* scratch bytes per command buffer */
#define DRV_WAIT_US 100u                     /* one HAL wait interval */
#define DRV_TIMEOUT_US (10u * 1000u * 1000u) /* give up waiting for the GPU (simulated time on simulators) */

typedef struct drv_alloc {
    void *cpu;
    uint32_t gpu;
    uint32_t bytes;          /* header + payload: cleaned as a whole before the kick (D-1) */
    struct drv_alloc *next;
} drv_alloc_t;

typedef struct {
    uint32_t *cpu;          /* command words */
    uint32_t gpu;
    uint32_t used;          /* words */
    uint32_t fence;         /* sequence number of the FENCE that ends it once kicked */
    int busy;               /* kicked and not yet known to be consumed */
    uint8_t *arena_cpu;     /* scratch for this buffer's draws */
    uint32_t arena_gpu;
    uint32_t arena_used;
    drv_alloc_t *extra;     /* scratch allocations too large for the arena, freed with the buffer */
    uint32_t clut_gen;      /* generation of the CLUT copied into this buffer's arena (0: none) */
    uint32_t clut_gpu;      /* its GPU address */
    uint32_t cramp_src, cramp_gen, cramp_gpu;   /* colour-transformed ramp copy (v1.6b, drv_color.c) */
    struct { void *cpu; uint32_t bytes; } wt[DRV_WT_MAX];   /* targets its draws write (v1.6) */
    int nwt;
} drv_cmdbuf_t;

typedef struct {
    const g2d_hal_t *hal;
    int open;
    uint32_t cmd_bytes, arena_bytes;
    int cmd_user;           /* command buffers are the caller's (vg_lite_set_command_buffer): not freed */
    drv_cmdbuf_t cb[DRV_NBUF];
    int cur;
    uint32_t seq;           /* last fence sequence number used */
    uint32_t kick_depth;
    /* GPU state shadow: value the GPU holds (valid after a LOAD_STATE), and what the next draw needs */
    uint32_t st_gpu[G2D_ST_SIZE];
    uint8_t st_known[G2D_ST_SIZE];
    uint32_t st_want[G2D_ST_SIZE];
    uint8_t st_need[G2D_ST_SIZE];
    /* API state */
    int scissor_on;
    int32_t sc_x0, sc_y0, sc_x1, sc_y1;
    uint32_t src_amode, src_aval, dst_amode, dst_aval;
    uint32_t ckey[8];       /* CKEY0_LO..CKEY3_HI words */
    int dither;             /* vg_lite_enable_dither (rendering.md §3.6) */
    int pattern_solid;      /* vg_lite_g2d_set_pattern_color_mode(SOLID): PAINT_CTRL.BORDER_SOLID (§7.5) */
    uint32_t clut[256];     /* vg_lite_set_CLUT colours (ARGB), unset entries 0 */
    uint32_t clut_gen;      /* incremented by vg_lite_set_CLUT; copied per command buffer when used */
    uint32_t last_err_status; /* ERR_STATUS of the last recovered halt (debug) */
    int fault;              /* sticky: the GPU could not be confirmed stopped after a failure (D-2, D-4) */
    void *tgt_cpu;          /* target of the draw being built (drv_state_target) */
    uint32_t tgt_bytes;
    int nsr;                /* vg_lite_scissor_rects with n > 1: disjoint pieces of the union (0: not used) */
    /* mask layer (v1.6, rendering.md §6.1 / §9.9): the layer set by vg_lite_set_masklayer and its origin */
    int mask_on, mask_set, mask_suspend;
    uint32_t blend_func;    /* BLEND_FUNC word of vg_lite_blend_func (VG_LITE_PORTER_DUFF_BLEND, v1.6) */
    int mirror;             /* vg_lite_set_mirror(BOTTOM_TOP): TGT_FMT.FLIP_Y (rendering.md §5.8, v1.6) */
    uint32_t features;      /* v1.7: HWCFG.FEAT_* (bit 0 families, 1 Gaussian, 2 YUV, 3 colour unit, 4 radial, 5 mask) */
    uint32_t gauss_w, gauss_w0;   /* GAUSS_W / GAUSS_W0 of vg_lite_gaussian_filter (rendering.md §9.10, v1.6b) */
    /* colour unit (v1.6b, drv_color.c): API parameters, the composed integer matrix, suspension by clears */
    float pm[20], ct[8];
    int pm_set, pm_ch[4], ct_on, gamma, color_en, color_suspend;
    int auto_gamma;         /* OPENVG_* source / target colour spaces differ: set while an image draw is set up */
    uint32_t color_gen;
    int32_t cmat[4][4], cbias[4];
    uint32_t mask_addr, mask_stride, mask_w, mask_h;
    int32_t mask_x, mask_y;
    g2d_rect_t sr[DRV_SR_MAX];
} drv_t;

extern drv_t g_drv;

/* ---- core (drv_core.c) ---- */
vg_lite_error_t drv_check_open(void);
/* Reserve n command words in the current buffer (flushes and waits for a free buffer when full). */
vg_lite_error_t drv_reserve(uint32_t n);
void drv_emit(uint32_t w);
/* State shadow: request a state word for the next draw; drv_emit_state emits LOAD_STATE runs for
 * requested words that differ from what the GPU holds. */
void drv_state(unsigned idx, uint32_t value);
vg_lite_error_t drv_emit_state(void);
/* Scratch memory the GPU reads during a draw of the current command buffer (16-byte aligned). */
vg_lite_error_t drv_scratch(uint32_t bytes, void **cpu, uint32_t *gpu);
vg_lite_error_t drv_flush(int wait_all);
/* Waits until the GPU has finished everything kicked; if that fails, soft-resets it and checks that it is
 * idle (D-2, D-4, docs/FEATURES_LATER.md). *safe = 1 when GPU-visible memory may be freed or overwritten
 * now; otherwise the driver enters the sticky fault state (every later call fails with GENERIC_IO until
 * vg_lite_close) and the caller must leave that memory alone. Returns the error to report. */
vg_lite_error_t drv_quiesce(int *safe);
/* Command words a draw of n words needs (state plus, with scissor pieces, one draw per piece): what the caller
 * reserves with drv_reserve before it allocates any scratch. */
uint32_t drv_draw_words(uint32_t n);
/* Emits the requested state and the draw (n command words), once per scissor piece (v1.6), and records the
 * target in the current command buffer (invalidated in the CPU cache when the buffer retires, so the CPU reads
 * the GPU's pixels on non-coherent systems, docs/PROGRAMMING.md §1). Call after drv_reserve(drv_draw_words(n)). */
vg_lite_error_t drv_emit_draw(const uint32_t *w, uint32_t n);
uint32_t drv_rd(uint32_t off);
void drv_wr(uint32_t off, uint32_t v);

/* ---- common helpers ---- */
/* Hardware FMT code of a vg_lite format, -1 if the hardware cannot read it (rendering.md §3.2). */
int drv_fmt(vg_lite_buffer_format_t f);
unsigned drv_fmt_bits(int hw_fmt);
unsigned drv_fmt_bytes(int hw_fmt);         /* 1 for sub-byte formats */
int drv_fmt_target(int hw_fmt);             /* not a source-only (sub-byte / indexed) format */
int drv_fmt_indexed(int hw_fmt);
uint32_t drv_row_bytes(int hw_fmt, int32_t width);
int drv_fmt_yuv(int hw_fmt);
/* v1.7: optional hardware units of this build (rendering.md §13.1); a missing one makes its API NOT_SUPPORT */
#define DRV_FEAT_FAMILY 0x01u
#define DRV_FEAT_GAUSS  0x02u
#define DRV_FEAT_YUV    0x04u
#define DRV_FEAT_COLOR  0x08u
#define DRV_FEAT_RADIAL 0x10u
#define DRV_FEAT_MASK   0x20u
#define drv_has(f) ((g_drv.features & (f)) != 0u)
int drv_fmt_linear(vg_lite_buffer_format_t f);
/* colour unit (drv_color.c, v1.6b) */
int drv_color_active(void);
uint32_t drv_color_apply(uint32_t argb);
void drv_state_color(int image);
vg_lite_error_t drv_color_ramp(void);
void drv_yuv_planes(int hw_fmt, int32_t w, int32_t h, uint32_t row[2], uint32_t rows[2]);
/* ARGB32 from vg_lite_color_t (A<<24|B<<16|G<<8|R), rendering.md §9.4. */
uint32_t drv_argb(vg_lite_color_t c);
/* Validates a buffer for use as a source (rendering.md §13.1 rules, v1 fields); drv_check_target also
 * rejects the source-only formats. */
vg_lite_error_t drv_check_buffer(const vg_lite_buffer_t *b);
vg_lite_error_t drv_check_target(const vg_lite_buffer_t *b);
/* Target and clip state (scissor ∩ target, or the full target). */
void drv_state_target(const vg_lite_buffer_t *t);
/* BLEND, ALPHA_CTRL, PAINT_CTRL, PAINT_COLOR. */
vg_lite_error_t drv_state_paint(vg_lite_blend_t blend, uint32_t paint_ctrl, uint32_t argb);
void drv_clip_rect(const vg_lite_buffer_t *t, int32_t *x0, int32_t *y0, int32_t *x1, int32_t *y1);
void drv_clip_g2d(const vg_lite_buffer_t *t, g2d_rect_t *r);
/* One DRAW_RECT (state and draw through drv_emit_draw; the caller has reserved drv_draw_words(3)). */
vg_lite_error_t drv_emit_rect(int32_t x0, int32_t y0, int32_t x1, int32_t y1);
/* PAINT_CTRL of an image draw without PERSP: image mode, filter, premultiply (A8: depends on the blend, Q-API-3;
 * copy: plain copy). */
vg_lite_error_t drv_image_paint(const vg_lite_buffer_t *source, vg_lite_filter_t filter, vg_lite_blend_t blend, int copy,
                                uint32_t *paint);
/* Requests target, source, transform (XF_A..XF_I), colour-key and paint state and emits one image DRAW_RECT
 * over draw. Buffers and srect must already be validated. paint may carry PAINT_CTRL.WRAP; border is
 * BORDER_COLOR (ARGB) for WRAP = COLOR. */
vg_lite_error_t drv_emit_blit(vg_lite_buffer_t *target, vg_lite_buffer_t *source, const g2d_rect_t *srect,
                              const int32_t xf[9], const g2d_rect_t *draw, vg_lite_blend_t blend,
                              vg_lite_color_t color, uint32_t paint, int copy, uint32_t border);
/* The state part of drv_emit_blit (no command space reserved, no CLUT); drv_state_clut points CLUT_ADDR at this
 * command buffer's copy of the CLUT for an indexed source (call after drv_reserve, before drv_emit_state). */
vg_lite_error_t drv_state_image(vg_lite_buffer_t *target, vg_lite_buffer_t *source, const g2d_rect_t *srect,
                                const int32_t xf[9], vg_lite_blend_t blend, vg_lite_color_t color, uint32_t paint,
                                int copy, uint32_t border);
vg_lite_error_t drv_state_clut(const vg_lite_buffer_t *source);
void drv_matrix_d(const vg_lite_matrix_t *m, double d[3][3]);
vg_lite_error_t drv_geom_err(int rc);
/* A double[3][3] as the const matrix argument of sw/lib/geom (C99 needs the cast). */
#define DRV_CM(m) ((const double(*)[3])(m))
/* Path draw after the caller requested its paint state (drv_path.c). */
vg_lite_error_t drv_draw_path(vg_lite_buffer_t *target, vg_lite_path_t *path, vg_lite_fill_t fill_rule,
                              vg_lite_matrix_t *matrix);
/* The same with an IMAGE paint (F-1, v1.5): image is the source whose CLUT drv_state_clut places. */
vg_lite_error_t drv_draw_path_image(vg_lite_buffer_t *target, vg_lite_path_t *path, vg_lite_fill_t fill_rule,
                                    vg_lite_matrix_t *matrix, const vg_lite_buffer_t *image);
/* sin / cos of an angle in degrees without libm, exact at multiples of 90 (drv_matrix.c). */
void drv_sincos_deg(double deg, double *s, double *c);

#endif /* VG_LITE_DRV_H */
