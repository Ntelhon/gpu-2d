// SPDX-License-Identifier: Apache-2.0
/*
 * G2D driver geometry (spec/rendering.md §9-10). C99, shared by the vg_lite driver and the golden-model
 * test harness so that both produce identical hardware inputs.
 *
 * Determinism: compile with -ffp-contract=off and without -ffast-math. Only + - * /, sqrt, floor and
 * ceil are used (all correctly rounded in IEEE-754), so results are identical on every conforming
 * platform (host x86-64, CVA6).
 */
#ifndef G2D_GEOM_H
#define G2D_GEOM_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Return codes. */
#define G2D_GEOM_OK 0
#define G2D_GEOM_EMPTY 1        /* valid input, nothing to draw */
#define G2D_GEOM_INVALID (-1)   /* maps to VG_LITE_INVALID_ARGUMENT */
#define G2D_GEOM_NOT_SUPPORT (-2) /* maps to VG_LITE_NOT_SUPPORT */
#define G2D_GEOM_NOMEM (-3)     /* maps to VG_LITE_OUT_OF_RESOURCES */

/* Path coordinate formats, same values as vg_lite_format_t. */
#define G2D_PATH_S8 0
#define G2D_PATH_S16 1
#define G2D_PATH_S32 2
#define G2D_PATH_FP32 3

/* Guard band (rendering.md §9.2 step 4) and flattening tolerance (step 3). */
#define G2D_GUARD 16383.0
#define G2D_FLATTEN_TOL 0.03125
#define G2D_FLATTEN_MAX_SEG 256

typedef struct {
  int32_t x0, y0, x1, y1; /* half-open [x0,x1) x [y0,y1) */
} g2d_rect_t;

/* Round half away from zero, evaluated in double (§9.1). */
double g2d_rha(double v);

/* 3x3 inverse (§9.3 step 2). Returns G2D_GEOM_INVALID if det == 0. */
int g2d_geom_inverse(const double m[3][3], double inv[3][3]);

/* sin / cos of an angle in degrees from a polynomial (rendering.md §9.1: never libm); exact for multiples of 90. */
void g2d_geom_sincos_deg(double deg, double *s, double *c);

/* atan2(y, x) in degrees, (-180, 180], from an own series (rendering.md §9.1: never libm); 0 for (0, 0). */
double g2d_geom_atan2_deg(double y, double x);

/* ---------------------------------------------------------------------------------------------- */
/* Paths (§9.2)                                                                                   */
/* ---------------------------------------------------------------------------------------------- */

/* Receives one s.4 edge. Return non-zero to abort (propagated as G2D_GEOM_NOMEM). */
typedef int (*g2d_edge_sink_t)(void *ctx, int32_t x0, int32_t y0, int32_t x1, int32_t y1);

/*
 * Decodes VGLite path data (length in bytes, format G2D_PATH_*; arcs §9.2.1, BREAK), transforms it by m (affine;
 * or projective: flattened against the projection, clipped at the near plane and projected, §9.8), flattens,
 * guard-band clips, quantizes to s.4 and emits non-horizontal edges to sink.
 * bbox receives the pixel bounding box of the emitted edges (§9.2 step 7; {0,0,0,0} if none).
 * Returns G2D_GEOM_OK, G2D_GEOM_EMPTY (no edges), or an error.
 */
int g2d_geom_path_edges(const void *data, uint32_t length, int format, const double m[3][3],
                        g2d_edge_sink_t sink, void *ctx, g2d_rect_t *bbox);

/* ---------------------------------------------------------------------------------------------- */
/* Strokes (§9.7)                                                                                 */
/* ---------------------------------------------------------------------------------------------- */

#define G2D_CAP_BUTT 0
#define G2D_CAP_ROUND 1
#define G2D_CAP_SQUARE 2
#define G2D_JOIN_MITER 0
#define G2D_JOIN_ROUND 1
#define G2D_JOIN_BEVEL 2

typedef struct {
  double width;       /* path space, > 0 */
  double miter_limit; /* miter length / width, values < 1 act as 1 */
  int cap, join;      /* G2D_CAP_*, G2D_JOIN_* */
  /* dashes (§9.7.1, OpenVG 1.1 §8.7.3): on / off lengths in path space; dash_count < 2 or NULL: solid; an odd
   * count ignores the last entry, negative entries count as 0, an all-zero pattern is solid */
  const double *dash;
  uint32_t dash_count;
  double dash_phase;  /* offset into the pattern, modulo its length */
  int dash_reset;     /* != 0: the phase restarts at every subpath; 0: the dash state carries over */
} g2d_stroke_t;

/* Dash boundaries per stroke draw before G2D_GEOM_NOMEM (bounds the work of tiny dash entries). */
#define G2D_DASH_MAX_STEPS (1u << 22)

/*
 * Edges of the stroke outline of the path (dashed if st->dash_count >= 2), to be filled with the NON_ZERO rule: the
 * union of one rectangle per segment, a join shape per interior vertex and a cap per open end (of every dash), all
 * of one orientation, built in path space, then transformed by m (affine, or projective: clipped at the near plane
 * and projected, §9.8), guard-band clipped and quantized as §9.2.
 */
int g2d_geom_stroke_edges(const void *data, uint32_t length, int format, const double m[3][3], const g2d_stroke_t *st,
                          g2d_edge_sink_t sink, void *ctx, g2d_rect_t *bbox);

/* ---------------------------------------------------------------------------------------------- */
/* Blit setup (§9.3)                                                                              */
/* ---------------------------------------------------------------------------------------------- */

typedef struct {
  int32_t xf[9];   /* XF_A..XF_I (A..F s15.16, G..I s1.30) */
  int persp;       /* PAINT_CTRL.PERSP */
  g2d_rect_t draw; /* DRAW_RECT payload, already intersected with the clip rect */
} g2d_blit_setup_t;

/* m: API matrix (source -> target), src: source rect, clip: scissor ∩ target. */
int g2d_geom_blit_setup(const double m[3][3], const g2d_rect_t *src, const g2d_rect_t *clip,
                        g2d_blit_setup_t *out);
/* The same without step 1: the draw rectangle is the whole clip (draws with a wrap mode, rendering.md §9.3,
 * §7.5; v1.4). */
int g2d_geom_blit_setup_full(const double m[3][3], const g2d_rect_t *src, const g2d_rect_t *clip,
                             g2d_blit_setup_t *out);

/* ---------------------------------------------------------------------------------------------- */
/* Gradients (§10.2-10.3)                                                                         */
/* ---------------------------------------------------------------------------------------------- */

/*
 * Linear function coefficients. gm: gradient matrix (gradient space -> target, must be affine).
 * ext == 0: legacy gradient (ramp along gradient-space x, 256 units); ext != 0: extended gradient
 * from (x0,y0) to (x1,y1) in gradient space. r receives the 48-bit GRAD_R value (sign-extended).
 */
/* Radial gradient (rendering.md §10.4): affine functions T, U, V (P, Q in s15.16 per half pixel as GRAD_P,
 * R the 48-bit constant) for gradient matrix gm (affine), centre (cx, cy), radius r, focal point (fx, fy). */
typedef struct {
  int32_t p[3], q[3];
  int64_t r[3];
} g2d_geom_radial_t;
int g2d_geom_radial_coeffs(const double gm[3][3], double cx, double cy, double r, double fx, double fy,
                           g2d_geom_radial_t *out);

int g2d_geom_grad_coeffs(const double gm[3][3], int ext, double x0, double y0, double x1, double y1,
                         int32_t *p, int32_t *q, int64_t *r);

/* Legacy ramp: colors are vg_lite_color_t (A<<24|B<<16|G<<8|R); ramp receives ARGB32. */
int g2d_geom_ramp_legacy(uint32_t count, const uint32_t *colors, const uint32_t *stops, uint32_t ramp[256]);

typedef struct {
  double stop, r, g, b, a; /* all in [0,1] */
} g2d_ramp_stop_t;

/* Extended ramp (float stops); premul != 0 multiplies RGB by A before quantisation. */
int g2d_geom_ramp_ext(uint32_t count, const g2d_ramp_stop_t *stops, int premul, uint32_t ramp[256]);

#ifdef __cplusplus
}
#endif

#endif /* G2D_GEOM_H */
