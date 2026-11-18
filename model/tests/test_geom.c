// SPDX-License-Identifier: Apache-2.0
/* Unit tests for sw/lib/geom (spec/rendering.md §9-10). */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "g2d_geom.h"

static int failures = 0, checks = 0;
#define CHECK(c)                                                                  \
  do {                                                                            \
    ++checks;                                                                     \
    if (!(c)) {                                                                   \
      if (failures++ < 20) printf("%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); \
    }                                                                             \
  } while (0)

static const double kId[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};

typedef struct {
  int n;
  int32_t e[4096][4];
} edges_t;

static int sink(void *ctx, int32_t x0, int32_t y0, int32_t x1, int32_t y1) {
  edges_t *es = (edges_t *)ctx;
  if (es->n >= 4096) return 1;
  es->e[es->n][0] = x0;
  es->e[es->n][1] = y0;
  es->e[es->n][2] = x1;
  es->e[es->n][3] = y1;
  es->n++;
  return 0;
}

/* Path builder for S32 data: one 4-byte element per opcode and per coordinate. */
typedef struct {
  int32_t w[512];
  int n;
} pb_t;
static void op(pb_t *p, int code, int nargs, const int32_t *args) {
  int i;
  p->w[p->n++] = code;
  for (i = 0; i < nargs; ++i) p->w[p->n++] = args[i];
}

static void test_rha(void) {
  CHECK(g2d_rha(0.5) == 1.0 && g2d_rha(-0.5) == -1.0 && g2d_rha(1.49) == 1.0 && g2d_rha(-2.5) == -3.0);
  CHECK(g2d_rha(0.0) == 0.0 && g2d_rha(-0.49) == 0.0);
}

static void test_inverse(void) {
  const double m[3][3] = {{2, 0.5, 10}, {-0.25, 3, -7}, {0.001, 0.002, 1}};
  double inv[3][3];
  int i, j, k;
  CHECK(g2d_geom_inverse(m, inv) == G2D_GEOM_OK);
  for (i = 0; i < 3; ++i)
    for (j = 0; j < 3; ++j) {
      double s = 0;
      for (k = 0; k < 3; ++k) s += m[i][k] * inv[k][j];
      CHECK(fabs(s - (i == j)) < 1e-12);
    }
  {
    const double sing[3][3] = {{1, 2, 3}, {2, 4, 6}, {0, 0, 1}};
    CHECK(g2d_geom_inverse(sing, inv) == G2D_GEOM_INVALID);
  }
}

static void test_path_square(void) {
  pb_t p = {{0}, 0};
  edges_t es;
  g2d_rect_t bb;
  int32_t a[2];
  es.n = 0;
  a[0] = 1; a[1] = 2; op(&p, 0x02, 2, a);   /* MOVE 1,2 */
  a[0] = 5; a[1] = 2; op(&p, 0x04, 2, a);   /* LINE 5,2 */
  a[0] = 4; op(&p, 0x0E, 1, a);             /* VLINE_REL +4 -> 5,6 */
  a[0] = 1; op(&p, 0x0B, 1, a);             /* HLINE 1 -> 1,6 */
  op(&p, 0x01, 0, a);                       /* CLOSE */
  op(&p, 0x00, 0, a);                       /* END */
  CHECK(g2d_geom_path_edges(p.w, (uint32_t)(p.n * 4), G2D_PATH_S32, kId, sink, &es, &bb) == G2D_GEOM_OK);
  /* two vertical edges, horizontals dropped; s.4 coordinates */
  CHECK(es.n == 2);
  CHECK(es.e[0][0] == 80 && es.e[0][1] == 32 && es.e[0][2] == 80 && es.e[0][3] == 96);
  CHECK(es.e[1][0] == 16 && es.e[1][1] == 96 && es.e[1][2] == 16 && es.e[1][3] == 32);
  CHECK(bb.x0 == 1 && bb.y0 == 2 && bb.x1 == 5 && bb.y1 == 6);
}

static void test_path_formats_and_matrix(void) {
  /* Same triangle in S8, S16 and FP32 with a translate+scale matrix gives identical edges. */
  const double m[3][3] = {{2, 0, 10.25}, {0, 2, -3.5}, {0, 0, 1}};
  int8_t d8[] = {0x02, 0, 0, 0x04, 10, 0, 0x04, 0, 10, 0x00};
  int16_t d16[] = {0x02, 0, 0, 0x04, 10, 0, 0x04, 0, 10, 0x00};
  uint32_t dfp[10];
  float fv[] = {0, 0, 10, 0, 0, 10};
  edges_t a, b, c;
  g2d_rect_t bb;
  dfp[0] = 0x02; memcpy(&dfp[1], &fv[0], 8);
  dfp[3] = 0x04; memcpy(&dfp[4], &fv[2], 8);
  dfp[6] = 0x04; memcpy(&dfp[7], &fv[4], 8);
  dfp[9] = 0x00;
  a.n = b.n = c.n = 0;
  CHECK(g2d_geom_path_edges(d8, sizeof d8, G2D_PATH_S8, m, sink, &a, &bb) == G2D_GEOM_OK);
  CHECK(g2d_geom_path_edges(d16, sizeof d16, G2D_PATH_S16, m, sink, &b, &bb) == G2D_GEOM_OK);
  CHECK(g2d_geom_path_edges(dfp, sizeof dfp, G2D_PATH_FP32, m, sink, &c, &bb) == G2D_GEOM_OK);
  CHECK(a.n == 2 && b.n == 2 && c.n == 2);
  CHECK(memcmp(a.e, b.e, sizeof(a.e[0]) * 2) == 0 && memcmp(a.e, c.e, sizeof(a.e[0]) * 2) == 0);
  /* (10,0)*2 + (10.25,-3.5) = (30.25, -3.5) -> s.4 (484, -56) */
  CHECK(a.e[0][0] == 484 && a.e[0][1] == -56);
  /* perspective path matrix (rendering.md §9.8): w = 2 everywhere is the affine scale 1/2, exactly */
  {
    const double pm[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 2}}, hm[3][3] = {{0.5, 0, 0}, {0, 0.5, 0}, {0, 0, 1}};
    edges_t p2, h2;
    p2.n = h2.n = 0;
    CHECK(g2d_geom_path_edges(d8, sizeof d8, G2D_PATH_S8, pm, sink, &p2, &bb) == G2D_GEOM_OK);
    CHECK(g2d_geom_path_edges(d8, sizeof d8, G2D_PATH_S8, hm, sink, &h2, &bb) == G2D_GEOM_OK);
    CHECK(p2.n == h2.n && p2.n > 0 && memcmp(p2.e, h2.e, sizeof(p2.e[0]) * p2.n) == 0);
  }
  /* arcs decode (5 arguments), unknown opcode invalid, truncated data invalid */
  {
    int8_t arc[] = {0x02, 0, 0, 0x13, 1, 1, 0, 5, 5, 0x00};
    int8_t bad[] = {0x02, 0, 0, 0x7F, 0x00};
    int8_t trunc[] = {0x02, 0, 0, 0x06, 1, 2};
    CHECK(g2d_geom_path_edges(arc, sizeof arc, G2D_PATH_S8, kId, sink, &a, &bb) == G2D_GEOM_OK);
    CHECK(g2d_geom_path_edges(bad, sizeof bad, G2D_PATH_S8, kId, sink, &a, &bb) == G2D_GEOM_INVALID);
    CHECK(g2d_geom_path_edges(trunc, sizeof trunc, G2D_PATH_S8, kId, sink, &a, &bb) == G2D_GEOM_INVALID);
  }
}

static void test_atan2(void) {
  /* own series (§9.1) against libm (host only): within 1e-12 degrees; exact axes */
  static const double v[][2] = {{0, 1}, {1, 1}, {1, 0}, {1, -1}, {0, -1}, {-1, -1}, {-1, 0}, {-1, 1},
                                {0.3, 2}, {-5, 0.01}, {1e-9, -1}, {7, -3}, {-0.2679, 1}, {0.2680, 1}};
  size_t i;
  for (i = 0; i < sizeof v / sizeof v[0]; ++i) {
    double r = g2d_geom_atan2_deg(v[i][0], v[i][1]), e = atan2(v[i][0], v[i][1]) * (180.0 / 3.14159265358979323846);
    CHECK(fabs(r - e) < 1e-12);
  }
  CHECK(g2d_geom_atan2_deg(0, 1) == 0.0 && g2d_geom_atan2_deg(1, 0) == 90.0 && g2d_geom_atan2_deg(0, -1) == 180.0);
  CHECK(g2d_geom_atan2_deg(0, 0) == 0.0);
}

static void test_flatten(void) {
  /* Quad from (0,0) via (50,100) to (100,0): d = |P0-2P1+P2| = 200 -> n = ceil(sqrt(200/(4/32))) = 40 */
  pb_t p = {{0}, 0};
  edges_t es;
  g2d_rect_t bb;
  int32_t a[4] = {0, 0, 0, 0};
  int i;
  es.n = 0;
  op(&p, 0x02, 2, a);
  a[0] = 50; a[1] = 100; a[2] = 100; a[3] = 0;
  op(&p, 0x06, 4, a);
  op(&p, 0x00, 0, a);
  CHECK(g2d_geom_path_edges(p.w, (uint32_t)(p.n * 4), G2D_PATH_S32, kId, sink, &es, &bb) == G2D_GEOM_OK);
  CHECK(es.n == 40); /* 40 curve segments + closing edge, which is horizontal (dropped) */
  CHECK(es.e[0][0] == 0 && es.e[0][1] == 0);
  CHECK(es.e[39][2] == 1600 && es.e[39][3] == 0); /* endpoint exact */
  /* every flattened point is within tolerance of the true curve at the same t (uniform t) */
  for (i = 1; i < 40; ++i) {
    double t = i / 40.0, x = 2 * (1 - t) * t * 50 + t * t * 100, y = 2 * (1 - t) * t * 100;
    CHECK(fabs(es.e[i][0] / 16.0 - x) <= 1.0 / 32 && fabs(es.e[i][1] / 16.0 - y) <= 1.0 / 32);
  }
}

static void test_guard_clip(void) {
  /* Triangle far outside the guard band: all emitted coordinates within ±16383 px */
  pb_t p = {{0}, 0};
  edges_t es;
  g2d_rect_t bb;
  int32_t a[2];
  int i;
  es.n = 0;
  a[0] = -100000; a[1] = 100; op(&p, 0x02, 2, a);
  a[0] = 100; a[1] = 50000; op(&p, 0x04, 2, a);
  a[0] = 200; a[1] = 50; op(&p, 0x04, 2, a);
  op(&p, 0x00, 0, a);
  CHECK(g2d_geom_path_edges(p.w, (uint32_t)(p.n * 4), G2D_PATH_S32, kId, sink, &es, &bb) == G2D_GEOM_OK);
  CHECK(es.n >= 3);
  for (i = 0; i < es.n; ++i) {
    int k;
    for (k = 0; k < 4; ++k) CHECK(es.e[i][k] >= -16383 * 16 && es.e[i][k] <= 16383 * 16);
  }
}

static void test_blit_setup(void) {
  g2d_rect_t src = {0, 0, 64, 32}, clip = {0, 0, 1280, 720};
  g2d_blit_setup_t s;
  /* translate by (10.5, 20) */
  const double m[3][3] = {{1, 0, 10.5}, {0, 1, 20}, {0, 0, 1}};
  CHECK(g2d_geom_blit_setup(m, &src, &clip, &s) == G2D_GEOM_OK);
  CHECK(s.persp == 0 && s.xf[0] == 65536 && s.xf[1] == 0 && s.xf[2] == -688128 && s.xf[4] == 65536 &&
        s.xf[5] == -1310720 && s.xf[6] == 0 && s.xf[7] == 0 && s.xf[8] == (1 << 30));
  CHECK(s.draw.x0 == 9 && s.draw.y0 == 19 && s.draw.x1 == 76 && s.draw.y1 == 53);
  /* fully clipped */
  {
    const double far[3][3] = {{1, 0, 5000}, {0, 1, 0}, {0, 0, 1}};
    CHECK(g2d_geom_blit_setup(far, &src, &clip, &s) == G2D_GEOM_EMPTY);
  }
  /* perspective: normalisation gives max |w| = 1 at the corners */
  {
    const double pm[3][3] = {{1, 0, 100}, {0, 1, 100}, {0.002, 0, 1}};
    double maxw = 0;
    int c;
    CHECK(g2d_geom_blit_setup(pm, &src, &clip, &s) == G2D_GEOM_OK);
    CHECK(s.persp == 1);
    for (c = 0; c < 4; ++c) {
      double px = (c & 1) ? s.draw.x1 - 0.5 : s.draw.x0 + 0.5, py = (c & 2) ? s.draw.y1 - 0.5 : s.draw.y0 + 0.5;
      double w = (s.xf[6] * px + s.xf[7] * py + s.xf[8]) / 1073741824.0;
      if (fabs(w) > maxw) maxw = fabs(w);
    }
    CHECK(fabs(maxw - 1.0) < 1e-6);
  }
  /* singular */
  {
    const double sing[3][3] = {{0, 0, 0}, {0, 1, 0}, {0, 0, 1}};
    CHECK(g2d_geom_blit_setup(sing, &src, &clip, &s) == G2D_GEOM_INVALID);
  }
}

static void test_gradients(void) {
  int32_t p, q;
  int64_t r;
  uint32_t ramp[256], colors[2] = {0xFF000000u, 0xFFFFFFFFu}, stops[2] = {0, 255};
  int n;
  /* legacy, identity matrix: x_g = x */
  CHECK(g2d_geom_grad_coeffs(kId, 0, 0, 0, 0, 0, &p, &q, &r) == G2D_GEOM_OK);
  CHECK(p == 65536 && q == 0 && r == 0);
  /* extended 0..100 along x: x_g = 2.56 x */
  CHECK(g2d_geom_grad_coeffs(kId, 1, 0, 0, 100, 0, &p, &q, &r) == G2D_GEOM_OK);
  CHECK(p == (int32_t)g2d_rha(2.56 * 65536) && q == 0 && r == 0);
  CHECK(g2d_geom_grad_coeffs(kId, 1, 5, 5, 5, 5, &p, &q, &r) == G2D_GEOM_INVALID);
  /* legacy ramp black->white over 0..255: channel = n */
  CHECK(g2d_geom_ramp_legacy(2, colors, stops, ramp) == G2D_GEOM_OK);
  for (n = 0; n < 256; ++n) CHECK(ramp[n] == (0xFF000000u | (uint32_t)n << 16 | (uint32_t)n << 8 | (uint32_t)n));
  /* vg_lite_color_t is ABGR: pure red 0xFF0000FF -> ARGB 0xFFFF0000 */
  {
    uint32_t red = 0xFF0000FFu, st = 0;
    CHECK(g2d_geom_ramp_legacy(1, &red, &st, ramp) == G2D_GEOM_OK && ramp[0] == 0xFFFF0000u && ramp[255] == 0xFFFF0000u);
  }
  /* extended ramp: two stops at 0 and 1, transparent -> opaque white, premultiplied */
  {
    g2d_ramp_stop_t s2[2] = {{0, 1, 1, 1, 0}, {1, 1, 1, 1, 1}};
    CHECK(g2d_geom_ramp_ext(2, s2, 1, ramp) == G2D_GEOM_OK);
    for (n = 0; n < 256; ++n) {
      uint32_t a = ramp[n] >> 24, c = ramp[n] & 0xFF;
      CHECK(a == c && a == (uint32_t)g2d_rha((n + 0.5) / 256 * 255));
    }
  }
}

int main(void) {
  test_rha();
  test_inverse();
  test_path_square();
  test_path_formats_and_matrix();
  test_atan2();
  test_flatten();
  test_guard_clip();
  test_blit_setup();
  test_gradients();
  printf("geom tests: %d checks, %d failures\n", checks, failures);
  return failures ? 1 : 0;
}
