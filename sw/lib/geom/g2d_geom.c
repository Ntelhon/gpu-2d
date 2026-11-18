// SPDX-License-Identifier: Apache-2.0
/* G2D driver geometry, see g2d_geom.h and spec/rendering.md §9-10. */
#include "g2d_geom.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

double g2d_rha(double v) { return v >= 0.0 ? floor(v + 0.5) : -floor(-v + 0.5); }

static double dmax(double a, double b) { return a > b ? a : b; }
static double dmin(double a, double b) { return a < b ? a : b; }
static double dclamp(double v, double lo, double hi) { return v < lo ? lo : (v > hi ? hi : v); }

/* sin and cos of x in [-pi/4, pi/4]: Taylor series to x^17 / x^16 (truncation error below 2^-60). */
static void sincos_small(double x, double *s, double *c) {
  double x2 = x * x;
  *s = x * (1.0 - x2 / 6.0 * (1.0 - x2 / 20.0 * (1.0 - x2 / 42.0 * (1.0 - x2 / 72.0 * (1.0 - x2 / 110.0 *
       (1.0 - x2 / 156.0 * (1.0 - x2 / 210.0 * (1.0 - x2 / 272.0))))))));
  *c = 1.0 - x2 / 2.0 * (1.0 - x2 / 12.0 * (1.0 - x2 / 30.0 * (1.0 - x2 / 56.0 * (1.0 - x2 / 90.0 *
       (1.0 - x2 / 132.0 * (1.0 - x2 / 182.0 * (1.0 - x2 / 240.0)))))));
}

void g2d_geom_sincos_deg(double deg, double *s, double *c) {
  double d = deg - 360.0 * floor(deg / 360.0);   /* [0, 360) */
  double q = floor((d + 45.0) / 90.0);           /* quadrant 0..4 */
  double r = d - 90.0 * q;                       /* [-45, 45) */
  double ss, cc;
  sincos_small(r * (3.14159265358979323846 / 180.0), &ss, &cc);
  switch ((int)q & 3) {
  case 0:  *s = ss;  *c = cc;  break;
  case 1:  *s = cc;  *c = -ss; break;
  case 2:  *s = -ss; *c = -cc; break;
  default: *s = -cc; *c = ss;  break;
  }
}

/* atan of t in [0, 1], radians: t > 2 - sqrt(3) is reduced by atan(t) = pi/6 + atan((sqrt(3) t - 1) / (sqrt(3) + t)),
 * then |t| <= 0.268 and the Taylor series to t^31 (truncation error below 2^-60). */
static double atan_unit(double t) {
  const double r3 = sqrt(3.0);
  double base = 0.0, t2, sum, term;
  int k;
  if (t > 2.0 - r3) {
    base = 3.14159265358979323846 / 6.0;
    t = (r3 * t - 1.0) / (r3 + t);
  }
  t2 = t * t;
  sum = 0.0;
  term = t;
  for (k = 0; k < 16; ++k) {
    sum = sum + (k & 1 ? -term : term) / (double)(2 * k + 1);
    term = term * t2;
  }
  return base + sum;
}

double g2d_geom_atan2_deg(double y, double x) {
  double ax = fabs(x), ay = fabs(y), a;
  if (ax == 0.0 && ay == 0.0) return 0.0;
  a = ay <= ax ? atan_unit(ay / ax) : 3.14159265358979323846 / 2.0 - atan_unit(ax / ay);
  a = a * (180.0 / 3.14159265358979323846);
  if (x < 0.0) a = 180.0 - a;
  return y < 0.0 ? -a : a;
}

static int64_t floor_div16(int64_t a) { return a >= 0 ? a / 16 : -((-a + 15) / 16); }
static int64_t ceil_div16(int64_t a) { return -floor_div16(-a); }

/* ---------------------------------------------------------------------------------------------- */
/* Inverse (§9.3 step 2): cofactors in the product order of the determinant formula.             */
/* ---------------------------------------------------------------------------------------------- */

int g2d_geom_inverse(const double m[3][3], double inv[3][3]) {
  double c00 = m[1][1] * m[2][2] - m[1][2] * m[2][1];
  double c01 = -(m[1][0] * m[2][2] - m[1][2] * m[2][0]);
  double c02 = m[1][0] * m[2][1] - m[1][1] * m[2][0];
  double c10 = -(m[0][1] * m[2][2] - m[0][2] * m[2][1]);
  double c11 = m[0][0] * m[2][2] - m[0][2] * m[2][0];
  double c12 = -(m[0][0] * m[2][1] - m[0][1] * m[2][0]);
  double c20 = m[0][1] * m[1][2] - m[0][2] * m[1][1];
  double c21 = -(m[0][0] * m[1][2] - m[0][2] * m[1][0]);
  double c22 = m[0][0] * m[1][1] - m[0][1] * m[1][0];
  double det = m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) -
               m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) +
               m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
  if (det == 0.0) return G2D_GEOM_INVALID;
  /* inv = transpose(cofactor) / det */
  inv[0][0] = c00 / det; inv[0][1] = c10 / det; inv[0][2] = c20 / det;
  inv[1][0] = c01 / det; inv[1][1] = c11 / det; inv[1][2] = c21 / det;
  inv[2][0] = c02 / det; inv[2][1] = c12 / det; inv[2][2] = c22 / det;
  return G2D_GEOM_OK;
}

/* ---------------------------------------------------------------------------------------------- */
/* Paths (§9.2)                                                                                   */
/* ---------------------------------------------------------------------------------------------- */

typedef struct {
  double x, y;
} pt_t;

typedef struct {
  pt_t *p;
  size_t n, cap;
} ptbuf_t;

static int ptbuf_push(ptbuf_t *b, double x, double y) {
  if (b->n == b->cap) {
    size_t cap = b->cap ? 2 * b->cap : 64;
    pt_t *np = (pt_t *)realloc(b->p, cap * sizeof(pt_t));
    if (!np) return G2D_GEOM_NOMEM;
    b->p = np;
    b->cap = cap;
  }
  b->p[b->n].x = x;
  b->p[b->n].y = y;
  b->n++;
  return G2D_GEOM_OK;
}

typedef struct {
  g2d_edge_sink_t sink;
  void *ctx;
  int any;
  int64_t minx, miny, maxx, maxy;
  ptbuf_t clip_a, clip_b; /* scratch for Sutherland-Hodgman */
} emit_t;

/* Sutherland-Hodgman against one bound. side: 0 x>=-G, 1 x<=G, 2 y>=-G, 3 y<=G. */
static int sh_inside(pt_t p, int side) {
  switch (side) {
    case 0: return p.x >= -G2D_GUARD;
    case 1: return p.x <= G2D_GUARD;
    case 2: return p.y >= -G2D_GUARD;
    default: return p.y <= G2D_GUARD;
  }
}

static pt_t sh_intersect(pt_t a, pt_t b, int side) {
  pt_t r;
  if (side < 2) {
    double X = side == 0 ? -G2D_GUARD : G2D_GUARD;
    r.x = X;
    r.y = a.y + (X - a.x) * (b.y - a.y) / (b.x - a.x);
  } else {
    double Y = side == 2 ? -G2D_GUARD : G2D_GUARD;
    r.y = Y;
    r.x = a.x + (Y - a.y) * (b.x - a.x) / (b.y - a.y);
  }
  return r;
}

static int sh_clip(const ptbuf_t *in, ptbuf_t *out, int side) {
  size_t i;
  out->n = 0;
  if (in->n == 0) return G2D_GEOM_OK;
  for (i = 0; i < in->n; ++i) {
    pt_t cur = in->p[i];
    pt_t prev = in->p[(i + in->n - 1) % in->n];
    int ci = sh_inside(cur, side), pi = sh_inside(prev, side);
    if (ci) {
      if (!pi) {
        pt_t x = sh_intersect(prev, cur, side);
        if (ptbuf_push(out, x.x, x.y)) return G2D_GEOM_NOMEM;
      }
      if (ptbuf_push(out, cur.x, cur.y)) return G2D_GEOM_NOMEM;
    } else if (pi) {
      pt_t x = sh_intersect(prev, cur, side);
      if (ptbuf_push(out, x.x, x.y)) return G2D_GEOM_NOMEM;
    }
  }
  return G2D_GEOM_OK;
}

/* Close, clip, quantize and emit one subpath (§9.2 steps 4-7). */
static int emit_subpath(emit_t *e, ptbuf_t *sp) {
  const ptbuf_t *poly = sp;
  size_t i;
  int need_clip = 0;
  int32_t px0 = 0, py0 = 0;
  if (sp->n < 2) return G2D_GEOM_OK;
  for (i = 0; i < sp->n; ++i) {
    if (fabs(sp->p[i].x) >= G2D_GUARD || fabs(sp->p[i].y) >= G2D_GUARD) {
      need_clip = 1;
      break;
    }
  }
  if (need_clip) {
    int side;
    const ptbuf_t *src = sp;
    ptbuf_t *dst = &e->clip_a;
    for (side = 0; side < 4; ++side) {
      if (sh_clip(src, dst, side)) return G2D_GEOM_NOMEM;
      src = dst;
      dst = (dst == &e->clip_a) ? &e->clip_b : &e->clip_a;
    }
    poly = src;
    if (poly->n < 2) return G2D_GEOM_OK;
  }
  for (i = 0; i <= poly->n; ++i) {
    const pt_t p = poly->p[i % poly->n];
    int32_t x = (int32_t)g2d_rha(p.x * 16.0), y = (int32_t)g2d_rha(p.y * 16.0);
    if (i > 0 && py0 != y) {
      if (e->sink(e->ctx, px0, py0, x, y)) return G2D_GEOM_NOMEM;
      if (!e->any) {
        e->minx = e->maxx = px0;
        e->miny = e->maxy = py0;
        e->any = 1;
      }
      if (px0 < e->minx) e->minx = px0;
      if (x < e->minx) e->minx = x;
      if (px0 > e->maxx) e->maxx = px0;
      if (x > e->maxx) e->maxx = x;
      if (py0 < e->miny) e->miny = py0;
      if (y < e->miny) e->miny = y;
      if (py0 > e->maxy) e->maxy = py0;
      if (y > e->maxy) e->maxy = y;
    }
    px0 = x;
    py0 = y;
  }
  return G2D_GEOM_OK;
}

static int op_args(unsigned op) {
  switch (op) {
    case 0x00: case 0x01: case 0x0A: return 0; /* END, CLOSE, BREAK */
    case 0x02: case 0x03: case 0x04: case 0x05: return 2;  /* MOVE, LINE (+REL) */
    case 0x06: case 0x07: return 4;            /* QUAD */
    case 0x08: case 0x09: return 6;            /* CUBIC */
    case 0x0B: case 0x0C: case 0x0D: case 0x0E: return 1;  /* HLINE, VLINE */
    case 0x0F: case 0x10: return 2;            /* SQUAD */
    case 0x11: case 0x12: return 4;            /* SCUBIC */
    case 0x13: case 0x14: case 0x15: case 0x16:
    case 0x17: case 0x18: case 0x19: case 0x1A: return 5; /* arcs: rh, rv, rot, x, y */
    default: return -1;                        /* unknown */
  }
}

static void xform_pt(const double m[3][3], double x, double y, double *ox, double *oy) {
  *ox = m[0][0] * x + m[0][1] * y + m[0][2];
  *oy = m[1][0] * x + m[1][1] * y + m[1][2];
}

/* Largest singular value of the 2x2 linear part of m: sqrt((F + sqrt(F^2 - 4 det^2)) / 2), F the squared
 * Frobenius norm. */
static double sigma_max(double a, double b, double c, double d) {
  double f = a * a + b * b + c * c + d * d, det = a * d - b * c, q = f * f - 4.0 * det * det;
  return sqrt((f + sqrt(q > 0.0 ? q : 0.0)) / 2.0);
}

/* Vertices of a full circle of radius r flattened with chord error <= tol: the fewest n in 8..1024 with
 * cos(180/n deg) >= 1 - tol / r, found by bisection (the error falls with n), 1024 if none (rendering.md §9.7). */
static int circle_n(double r, double tol) {
  double c = 1.0 - tol / r, sn, cs;
  int lo = 8, hi = 1024;
  g2d_geom_sincos_deg(180.0 / 8.0, &sn, &cs);
  if (cs < c) {
    while (lo + 1 < hi) {
      int mid = (lo + hi) / 2;
      g2d_geom_sincos_deg(180.0 / (double)mid, &sn, &cs);
      if (cs >= c) hi = mid; else lo = mid;
    }
    lo = hi;
  }
  return lo;
}

/* ---- perspective (rendering.md §9.8) ---- */

/* w = m20 x + m21 y + m22 of a path-space point. */
static double persp_w(const double m[3][3], double x, double y) { return m[2][0] * x + m[2][1] * y + m[2][2]; }

/* Near-plane threshold: points with w <= eps are behind or too close to the eye (clipped away). */
static double persp_eps(const double m[3][3]) {
  return dmax(fabs(m[2][0]), dmax(fabs(m[2][1]), fabs(m[2][2]))) / 1048576.0;
}

static void persp_pt(const double m[3][3], double x, double y, double *ox, double *oy) {
  double w = persp_w(m, x, y);
  *ox = (m[0][0] * x + m[0][1] * y + m[0][2]) / w;
  *oy = (m[1][0] * x + m[1][1] * y + m[1][2]) / w;
}

/* Clip the path-space polygon in against w >= eps (Sutherland-Hodgman, intersection p = a + t (b - a) with
 * t = (eps - wa) / (wb - wa)), then project every vertex into out. */
static int persp_poly(const double m[3][3], double eps, const ptbuf_t *in, ptbuf_t *out) {
  size_t i;
  out->n = 0;
  for (i = 0; i < in->n; ++i) {
    pt_t cur = in->p[i], prev = in->p[(i + in->n - 1) % in->n];
    double wc = persp_w(m, cur.x, cur.y), wp = persp_w(m, prev.x, prev.y), x, y;
    int ci = wc >= eps, pi = wp >= eps;
    if (ci != pi) {
      double t = (eps - wp) / (wc - wp);
      persp_pt(m, prev.x + t * (cur.x - prev.x), prev.y + t * (cur.y - prev.y), &x, &y);
      if (ptbuf_push(out, x, y)) return G2D_GEOM_NOMEM;
    }
    if (ci) {
      persp_pt(m, cur.x, cur.y, &x, &y);
      if (ptbuf_push(out, x, y)) return G2D_GEOM_NOMEM;
    }
  }
  return G2D_GEOM_OK;
}

/* Path walker (§9.2 steps 1-3): decodes the path and flattens it. Callbacks: begin(x, y) at the start of every
 * subpath (also the implicit one at (0,0)), point(x, y) for every flattened point of a drawing op (its last point
 * is the op's end point), and end(closed, nops) when a subpath ends: closed = 1 for CLOSE, 0 for MOVE / BREAK /
 * END; nops = drawing ops in it.
 * persp == 0: control points are transformed by the affine fm and flattened uniformly with tolerance tol
 * (§9.2 step 3; arcs §9.2.1). persp != 0: points stay in path space; curves and arcs are subdivided adaptively
 * until their projection by the projective fm is flat within tol (§9.8). */
typedef struct {
  int (*begin)(void *ctx, double x, double y);
  int (*point)(void *ctx, double x, double y);
  int (*end)(void *ctx, int closed, int nops);
  void *ctx;
} walk_t;

/* A curve piece in path space (persp mode): kind 1 quad, 2 cubic (control points c[]), 3 arc. */
typedef struct {
  int kind;
  double c[8];
  double cx, cy, rh, rv, co, si, th1, dth;
} curve_t;

static void curve_eval(const curve_t *k, double t, double *x, double *y) {
  double mt = 1.0 - t;
  if (k->kind == 1) {
    *x = (mt * mt) * k->c[0] + (2.0 * mt * t) * k->c[2] + (t * t) * k->c[4];
    *y = (mt * mt) * k->c[1] + (2.0 * mt * t) * k->c[3] + (t * t) * k->c[5];
  } else if (k->kind == 2) {
    *x = (mt * mt * mt) * k->c[0] + (3.0 * mt * mt * t) * k->c[2] + (3.0 * mt * t * t) * k->c[4] + (t * t * t) * k->c[6];
    *y = (mt * mt * mt) * k->c[1] + (3.0 * mt * mt * t) * k->c[3] + (3.0 * mt * t * t) * k->c[5] + (t * t * t) * k->c[7];
  } else {
    double sn, cs;
    g2d_geom_sincos_deg(k->th1 + k->dth * t, &sn, &cs);
    *x = k->cx + k->co * k->rh * cs - k->si * k->rv * sn;
    *y = k->cy + k->si * k->rh * cs + k->co * k->rv * sn;
  }
}

/* Adaptive subdivision of [t0, t1] (persp mode, §9.8): split at tm = (t0 + t1) / 2 while depth < 2, or depth < 8
 * and the projected piece is not flat: some but not all of the three points have w <= eps, or all have w > eps
 * and the projected mid point lies more than tol from the line through the projected end points. Emits the end
 * point of every final piece (p1 for the last one: the exact end point of the op). */
static int flat_adapt(const walk_t *w, const double m[3][3], double eps, double tol, const curve_t *k, double t0,
                      pt_t p0, double t1, pt_t p1, int depth) {
  double tm = (t0 + t1) / 2.0;
  pt_t pm;
  int split = depth < 2, rc;
  curve_eval(k, tm, &pm.x, &pm.y);
  if (!split && depth < 8) {
    double w0 = persp_w(m, p0.x, p0.y), wm = persp_w(m, pm.x, pm.y), w1 = persp_w(m, p1.x, p1.y);
    int nin = (w0 > eps) + (wm > eps) + (w1 > eps);
    if (nin == 3) {
      double ax, ay, bx, by, qx, qy, dx, dy, l;
      persp_pt(m, p0.x, p0.y, &ax, &ay);
      persp_pt(m, pm.x, pm.y, &qx, &qy);
      persp_pt(m, p1.x, p1.y, &bx, &by);
      dx = bx - ax;
      dy = by - ay;
      l = sqrt(dx * dx + dy * dy);
      if (l == 0.0)
        split = sqrt((qx - ax) * (qx - ax) + (qy - ay) * (qy - ay)) > tol;
      else
        split = fabs(dx * (qy - ay) - dy * (qx - ax)) / l > tol;
    } else {
      split = nin != 0;
    }
  }
  if (!split) return w->point(w->ctx, p1.x, p1.y);
  if ((rc = flat_adapt(w, m, eps, tol, k, t0, p0, tm, pm, depth + 1)) != G2D_GEOM_OK) return rc;
  return flat_adapt(w, m, eps, tol, k, tm, pm, t1, p1, depth + 1);
}

/* Elliptical arc from (x0,y0) to (x1,y1) in path space (rendering.md §9.2.1, OpenVG 1.1 §8.4 and Appendix A
 * (SVG F.6.5 / F.6.6)). Returns 0 if it is drawn as a line (coincident end points or a zero radius), else 1 with
 * the centre form in k (kind 3). */
static int arc_center(double x0, double y0, double x1, double y1, double rh, double rv, double rot, int large,
                      int ccw, curve_t *k) {
  double dx2, dy2, xp, yp, lam, num, den, q, f, cxp, cyp, ux, uy, vx, vy;
  rh = fabs(rh);
  rv = fabs(rv);
  if ((x0 == x1 && y0 == y1) || rh == 0.0 || rv == 0.0) return 0;
  g2d_geom_sincos_deg(rot, &k->si, &k->co);
  dx2 = (x0 - x1) / 2.0;
  dy2 = (y0 - y1) / 2.0;
  xp = k->co * dx2 + k->si * dy2;
  yp = -k->si * dx2 + k->co * dy2;
  lam = (xp * xp) / (rh * rh) + (yp * yp) / (rv * rv);
  if (lam > 1.0) {                  /* end points too far apart: scale the radii up (F.6.6) */
    double s = sqrt(lam);
    rh = rh * s;
    rv = rv * s;
  }
  num = rh * rh * (rv * rv) - rh * rh * (yp * yp) - rv * rv * (xp * xp);
  den = rh * rh * (yp * yp) + rv * rv * (xp * xp);
  q = num / den;
  f = sqrt(q > 0.0 ? q : 0.0);
  if (large == ccw) f = -f;
  cxp = f * rh * yp / rv;
  cyp = -f * rv * xp / rh;
  k->kind = 3;
  k->rh = rh;
  k->rv = rv;
  k->cx = k->co * cxp - k->si * cyp + (x0 + x1) / 2.0;
  k->cy = k->si * cxp + k->co * cyp + (y0 + y1) / 2.0;
  ux = (xp - cxp) / rh;
  uy = (yp - cyp) / rv;
  vx = (-xp - cxp) / rh;
  vy = (-yp - cyp) / rv;
  k->th1 = g2d_geom_atan2_deg(uy, ux);
  k->dth = g2d_geom_atan2_deg(ux * vy - uy * vx, ux * vx + uy * vy);
  if (!ccw && k->dth > 0.0) k->dth = k->dth - 360.0;
  if (ccw && k->dth < 0.0) k->dth = k->dth + 360.0;
  return 1;
}

static int walk_path(const void *data, uint32_t length, int format, const double fm[3][3], double tol, int persp,
                     const walk_t *w) {
  static const double ident[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
  const double (*xm)[3] = persp ? ident : fm;  /* transform of the emitted points */
  const double eps = persp ? persp_eps(fm) : 0.0;
  const uint8_t *d = (const uint8_t *)data;
  size_t esz, pos = 0;
  double cx = 0, cy = 0, sx = 0, sy = 0; /* current and subpath start, path space */
  double lcx = 0, lcy = 0;               /* last control point, path space */
  int last_kind = 0;                     /* 1 quad, 2 cubic */
  int nops = 0, rc;

  switch (format) {
    case G2D_PATH_S8: esz = 1; break;
    case G2D_PATH_S16: esz = 2; break;
    case G2D_PATH_S32: case G2D_PATH_FP32: esz = 4; break;
    default: return G2D_GEOM_INVALID;
  }
  {
    double tx, ty;
    xform_pt(xm, 0, 0, &tx, &ty);
    if ((rc = w->begin(w->ctx, tx, ty)) != G2D_GEOM_OK) return rc;
  }

  while (pos + esz <= length) {
    unsigned op = d[pos];
    int nargs = op_args(op), k;
    double a[6];
    pos += esz;
    if (nargs < 0) return G2D_GEOM_INVALID;
    if (pos + (size_t)nargs * esz > length) return G2D_GEOM_INVALID;
    for (k = 0; k < nargs; ++k) {
      const uint8_t *q = d + pos + (size_t)k * esz;
      switch (format) {
        case G2D_PATH_S8: a[k] = (double)(int8_t)q[0]; break;
        case G2D_PATH_S16: { int16_t v; memcpy(&v, q, 2); a[k] = (double)v; break; }
        case G2D_PATH_S32: { int32_t v; memcpy(&v, q, 4); a[k] = (double)v; break; }
        default: { float v; memcpy(&v, q, 4); a[k] = (double)v; break; }
      }
    }
    pos += (size_t)nargs * esz;

    if (op == 0x00) break; /* END */
    if (op == 0x01 || op == 0x0A) { /* CLOSE: line to start, new subpath at the start; BREAK: end, new at current */
      double tx, ty;
      if ((rc = w->end(w->ctx, op == 0x01, nops)) != G2D_GEOM_OK) return rc;
      nops = 0;
      if (op == 0x01) {
        cx = sx;
        cy = sy;
      } else {
        sx = cx;
        sy = cy;
      }
      xform_pt(xm, cx, cy, &tx, &ty);
      if ((rc = w->begin(w->ctx, tx, ty)) != G2D_GEOM_OK) return rc;
      last_kind = 0;
      continue;
    }
    {
      const int rel = (op == 0x03 || op == 0x05 || op == 0x07 || op == 0x09 || op == 0x0C || op == 0x0E ||
                       op == 0x10 || op == 0x12 || (op >= 0x13 && !(op & 1)));
      const double ox = rel ? cx : 0.0, oy = rel ? cy : 0.0;
      double p1x = 0, p1y = 0, p2x = 0, p2y = 0, p3x = 0, p3y = 0; /* path space */
      int kind = 0;
      curve_t arc;
      switch (op) {
        case 0x02: case 0x03: { /* MOVE */
          double tx, ty;
          if ((rc = w->end(w->ctx, 0, nops)) != G2D_GEOM_OK) return rc;
          nops = 0;
          cx = sx = a[0] + ox;
          cy = sy = a[1] + oy;
          xform_pt(xm, cx, cy, &tx, &ty);
          if ((rc = w->begin(w->ctx, tx, ty)) != G2D_GEOM_OK) return rc;
          last_kind = 0;
          continue;
        }
        case 0x04: case 0x05: p1x = a[0] + ox; p1y = a[1] + oy; kind = 0; p3x = p1x; p3y = p1y; break;
        case 0x0B: case 0x0C: p3x = a[0] + ox; p3y = cy; kind = 0; break;
        case 0x0D: case 0x0E: p3x = cx; p3y = a[0] + oy; kind = 0; break;
        case 0x06: case 0x07: p1x = a[0] + ox; p1y = a[1] + oy; p3x = a[2] + ox; p3y = a[3] + oy; kind = 1; break;
        case 0x0F: case 0x10:
          p1x = last_kind == 1 ? 2.0 * cx - lcx : cx;
          p1y = last_kind == 1 ? 2.0 * cy - lcy : cy;
          p3x = a[0] + ox; p3y = a[1] + oy; kind = 1; break;
        case 0x08: case 0x09:
          p1x = a[0] + ox; p1y = a[1] + oy; p2x = a[2] + ox; p2y = a[3] + oy;
          p3x = a[4] + ox; p3y = a[5] + oy; kind = 2; break;
        case 0x11: case 0x12:
          p1x = last_kind == 2 ? 2.0 * cx - lcx : cx;
          p1y = last_kind == 2 ? 2.0 * cy - lcy : cy;
          p2x = a[0] + ox; p2y = a[1] + oy; p3x = a[2] + ox; p3y = a[3] + oy; kind = 2; break;
        default: /* 0x13..0x1A arcs: large for LCCW / LCW, counter-clockwise for SCCW / LCCW */
          p3x = a[3] + ox;
          p3y = a[4] + oy;
          kind = arc_center(cx, cy, p3x, p3y, a[0], a[1], a[2], op >= 0x17, op <= 0x14 || op == 0x17 || op == 0x18,
                            &arc) ? 3 : 0;
          break;
      }
      nops++;
      if (persp) {
        /* path-space points; curves subdivided against the projection (§9.8), lines stay lines */
        if (kind != 0) {
          pt_t q0, q3;
          if (kind == 1 || kind == 2) {
            arc.kind = kind;
            arc.c[0] = cx; arc.c[1] = cy; arc.c[2] = p1x; arc.c[3] = p1y;
            if (kind == 1) {
              arc.c[4] = p3x; arc.c[5] = p3y;
            } else {
              arc.c[4] = p2x; arc.c[5] = p2y; arc.c[6] = p3x; arc.c[7] = p3y;
            }
          }
          q0.x = cx; q0.y = cy; q3.x = p3x; q3.y = p3y;
          if ((rc = flat_adapt(w, fm, eps, tol, &arc, 0.0, q0, 1.0, q3, 0)) != G2D_GEOM_OK) return rc;
        } else if ((rc = w->point(w->ctx, p3x, p3y)) != G2D_GEOM_OK) {
          return rc;
        }
      } else {
        /* Transform control points (affine) and flatten (§9.2 step 3). */
        double P0x, P0y, P1x, P1y, P2x, P2y, P3x, P3y;
        xform_pt(fm, cx, cy, &P0x, &P0y);
        xform_pt(fm, p3x, p3y, &P3x, &P3y);
        if (kind == 1) {
          double dx, dy, dd, n;
          int i, ni;
          xform_pt(fm, p1x, p1y, &P1x, &P1y);
          dx = P0x - 2.0 * P1x + P3x;
          dy = P0y - 2.0 * P1y + P3y;
          dd = sqrt(dx * dx + dy * dy);
          n = dclamp(ceil(sqrt(dd / (4.0 * tol))), 1.0, (double)G2D_FLATTEN_MAX_SEG);
          ni = (int)n;
          for (i = 1; i < ni; ++i) {
            double t = (double)i / (double)ni, mt = 1.0 - t;
            double bx = (mt * mt) * P0x + (2.0 * mt * t) * P1x + (t * t) * P3x;
            double by = (mt * mt) * P0y + (2.0 * mt * t) * P1y + (t * t) * P3y;
            if ((rc = w->point(w->ctx, bx, by)) != G2D_GEOM_OK) return rc;
          }
        } else if (kind == 2) {
          double ax, ay, bx2, by2, mm, n;
          int i, ni;
          xform_pt(fm, p1x, p1y, &P1x, &P1y);
          xform_pt(fm, p2x, p2y, &P2x, &P2y);
          ax = P0x - 2.0 * P1x + P2x;
          ay = P0y - 2.0 * P1y + P2y;
          bx2 = P1x - 2.0 * P2x + P3x;
          by2 = P1y - 2.0 * P2y + P3y;
          mm = dmax(sqrt(ax * ax + ay * ay), sqrt(bx2 * bx2 + by2 * by2));
          n = dclamp(ceil(sqrt(3.0 * mm / (4.0 * tol))), 1.0, (double)G2D_FLATTEN_MAX_SEG);
          ni = (int)n;
          for (i = 1; i < ni; ++i) {
            double t = (double)i / (double)ni, mt = 1.0 - t;
            double qx = (mt * mt * mt) * P0x + (3.0 * mt * mt * t) * P1x + (3.0 * mt * t * t) * P2x + (t * t * t) * P3x;
            double qy = (mt * mt * mt) * P0y + (3.0 * mt * mt * t) * P1y + (3.0 * mt * t * t) * P2y + (t * t * t) * P3y;
            if ((rc = w->point(w->ctx, qx, qy)) != G2D_GEOM_OK) return rc;
          }
        } else if (kind == 3) {
          /* arc: segments of a full circle of radius sigma_max(fm) * max(rh, rv) at tolerance tol (circle_n),
           * scaled by the sweep: k = clamp(ceil(n * |dth| / 360), 1, 1024); points at th1 + dth * i / k */
          double r = sigma_max(fm[0][0], fm[0][1], fm[1][0], fm[1][1]) * dmax(arc.rh, arc.rv), n;
          int i, ni;
          n = r > 0.0 ? dclamp(ceil((double)circle_n(r, tol) * fabs(arc.dth) / 360.0), 1.0, 1024.0) : 1.0;
          ni = (int)n;
          for (i = 1; i < ni; ++i) {
            double qx, qy, sn, cs;
            g2d_geom_sincos_deg(arc.th1 + arc.dth * (double)i / (double)ni, &sn, &cs);
            qx = arc.cx + arc.co * arc.rh * cs - arc.si * arc.rv * sn;
            qy = arc.cy + arc.si * arc.rh * cs + arc.co * arc.rv * sn;
            xform_pt(fm, qx, qy, &P1x, &P1y);
            if ((rc = w->point(w->ctx, P1x, P1y)) != G2D_GEOM_OK) return rc;
          }
        }
        if ((rc = w->point(w->ctx, P3x, P3y)) != G2D_GEOM_OK) return rc;
      }
      if (kind == 1) {
        lcx = p1x;
        lcy = p1y;
      } else if (kind == 2) {
        lcx = p2x;
        lcy = p2y;
      }
      cx = p3x;
      cy = p3y;
      last_kind = kind == 3 ? 0 : kind;
    }
  }
  return w->end(w->ctx, 0, nops);
}

static void finish_bbox(const emit_t *e, g2d_rect_t *bbox) {
  int64_t bx0 = floor_div16(e->minx), by0 = floor_div16(e->miny);
  int64_t bx1 = ceil_div16(e->maxx), by1 = ceil_div16(e->maxy);
  bbox->x0 = (int32_t)(bx0 < -32768 ? -32768 : bx0);
  bbox->y0 = (int32_t)(by0 < -32768 ? -32768 : by0);
  bbox->x1 = (int32_t)(bx1 > 32767 ? 32767 : bx1);
  bbox->y1 = (int32_t)(by1 > 32767 ? 32767 : by1);
}

/* ---- fill: every subpath is a closed polygon in target space ---- */
typedef struct {
  emit_t e;
  ptbuf_t sp;
  const double (*m)[3]; /* perspective only: clip + project each subpath (§9.8) */
  double eps;
  ptbuf_t proj;
} fill_t;

static int fill_begin(void *ctx, double x, double y) {
  fill_t *f = (fill_t *)ctx;
  f->sp.n = 0;
  return ptbuf_push(&f->sp, x, y);
}
static int fill_point(void *ctx, double x, double y) { return ptbuf_push(&((fill_t *)ctx)->sp, x, y); }
static int fill_end(void *ctx, int closed, int nops) {
  fill_t *f = (fill_t *)ctx;
  int rc;
  (void)closed;
  (void)nops;
  if (!f->m) return emit_subpath(&f->e, &f->sp);
  if (f->sp.n < 2) return G2D_GEOM_OK;
  if ((rc = persp_poly(f->m, f->eps, &f->sp, &f->proj)) != G2D_GEOM_OK) return rc;
  return emit_subpath(&f->e, &f->proj);
}

int g2d_geom_path_edges(const void *data, uint32_t length, int format, const double m[3][3],
                        g2d_edge_sink_t sink, void *ctx, g2d_rect_t *bbox) {
  fill_t f;
  walk_t w;
  int rc, persp = m[2][0] != 0.0 || m[2][1] != 0.0 || m[2][2] != 1.0;
  memset(&f, 0, sizeof(f));
  f.e.sink = sink;
  f.e.ctx = ctx;
  bbox->x0 = bbox->y0 = bbox->x1 = bbox->y1 = 0;
  if (format != G2D_PATH_S8 && format != G2D_PATH_S16 && format != G2D_PATH_S32 && format != G2D_PATH_FP32)
    return G2D_GEOM_INVALID;
  if (persp) {
    f.m = m;
    f.eps = persp_eps(m);
    if (f.eps == 0.0) return G2D_GEOM_EMPTY;   /* w = 0 everywhere */
  }
  w.begin = fill_begin;
  w.point = fill_point;
  w.end = fill_end;
  w.ctx = &f;
  rc = walk_path(data, length, format, m, G2D_FLATTEN_TOL, persp, &w);
  free(f.sp.p);
  free(f.proj.p);
  free(f.e.clip_a.p);
  free(f.e.clip_b.p);
  if (rc != G2D_GEOM_OK) return rc;
  if (!f.e.any) return G2D_GEOM_EMPTY;
  finish_bbox(&f.e, bbox);
  return G2D_GEOM_OK;
}

/* ---------------------------------------------------------------------------------------------- */
/* Strokes (§9.7)                                                                                 */
/* ---------------------------------------------------------------------------------------------- */

typedef struct {
  emit_t e;
  const double (*m)[3];
  g2d_stroke_t st;
  double hw, tol;  /* half width and flattening tolerance, path space (affine) */
  int persp;       /* projective matrix: pieces are clipped and projected (§9.8) */
  double eps;
  ptbuf_t sp;      /* current subpath (path space) */
  ptbuf_t poly;    /* one outline polygon (path space, then target space) */
  ptbuf_t proj;    /* projected polygon (perspective) */
  /* dashes (§9.7.1): normalised pattern (even count, entries >= 0, total > 0), phase, reset; state */
  double *dpat, dtotal, dphase, drem;
  uint32_t dcount, di, dsteps;
  int dreset, dstarted;
  ptbuf_t dash;    /* points of the current dash */
  /* v2.0 (FEATURES_LATER D-5): unit circle of the last disc's vertex count (cos, sin per vertex), reused while n
   * stays the same; the same sin / cos values as before, so the outline is bit-identical */
  double *unit;
  int unit_n, unit_cap;
} stroke_t;

static int stroke_begin(void *ctx, double x, double y) {
  stroke_t *s = (stroke_t *)ctx;
  s->sp.n = 0;
  return ptbuf_push(&s->sp, x, y);
}
static int stroke_point(void *ctx, double x, double y) { return ptbuf_push(&((stroke_t *)ctx)->sp, x, y); }

/* Emit the polygon in s->poly with positive orientation (signed area > 0 in path space); zero area: skipped. */
static int stroke_emit_poly(stroke_t *s) {
  double a = 0.0;
  size_t i, n = s->poly.n;
  if (n < 3) return G2D_GEOM_OK;
  for (i = 0; i < n; ++i) {
    const pt_t p = s->poly.p[i], q = s->poly.p[(i + 1) % n];
    a += p.x * q.y - q.x * p.y;
  }
  if (a == 0.0) return G2D_GEOM_OK;
  if (a < 0.0)
    for (i = 0; i < n / 2; ++i) {
      pt_t t = s->poly.p[i];
      s->poly.p[i] = s->poly.p[n - 1 - i];
      s->poly.p[n - 1 - i] = t;
    }
  if (s->persp) {
    int rc = persp_poly(s->m, s->eps, &s->poly, &s->proj);
    return rc != G2D_GEOM_OK ? rc : emit_subpath(&s->e, &s->proj);
  }
  for (i = 0; i < n; ++i) {
    double x, y;
    xform_pt(s->m, s->poly.p[i].x, s->poly.p[i].y, &x, &y);
    s->poly.p[i].x = x;
    s->poly.p[i].y = y;
  }
  return emit_subpath(&s->e, &s->poly);
}

static int poly4(stroke_t *s, double ax, double ay, double bx, double by, double cx, double cy, double dx, double dy) {
  s->poly.n = 0;
  if (ptbuf_push(&s->poly, ax, ay) || ptbuf_push(&s->poly, bx, by) || ptbuf_push(&s->poly, cx, cy) ||
      ptbuf_push(&s->poly, dx, dy))
    return G2D_GEOM_NOMEM;
  return stroke_emit_poly(s);
}

/* Path-space flattening tolerance of a disc of radius r at (x, y): tol / s (affine, §9.7); under a projective
 * matrix 1/32 over the largest local scale (sigma_max of the projection's Jacobian) at the corners of the disc's
 * bounding square, or 0 (use the maximum vertex count) if a corner has w <= eps (§9.8). */
static double disc_tol(const stroke_t *s, double x, double y, double r) {
  const double (*m)[3] = s->m;
  double smax = 0.0;
  int k;
  if (!s->persp) return s->tol;
  for (k = 0; k < 4; ++k) {
    double px = x + (k & 1 ? r : -r), py = y + (k & 2 ? r : -r), w = persp_w(m, px, py), u, v, j;
    if (!(w > s->eps)) return 0.0;
    persp_pt(m, px, py, &u, &v);
    j = sigma_max((m[0][0] - u * m[2][0]) / w, (m[0][1] - u * m[2][1]) / w, (m[1][0] - v * m[2][0]) / w,
                  (m[1][1] - v * m[2][1]) / w);
    smax = dmax(smax, j);
  }
  return smax > 0.0 ? G2D_FLATTEN_TOL / smax : 0.0;
}

/* Disc of radius hw: circle_n vertices (rendering.md §9.7); sin / cos from the polynomial (§9.1). */
static int disc(stroke_t *s, double x, double y) {
  double r = s->hw, t = disc_tol(s, x, y, r);
  int i, n = t > 0.0 ? circle_n(r, t) : 1024;
  if (s->unit_n != n) {
    if (n > s->unit_cap) {
      double *u = (double *)realloc(s->unit, 2u * (size_t)n * sizeof(double));
      if (!u) return G2D_GEOM_NOMEM;
      s->unit = u;
      s->unit_cap = n;
    }
    for (i = 0; i < n; ++i) g2d_geom_sincos_deg(360.0 * (double)i / (double)n, &s->unit[2 * i + 1], &s->unit[2 * i]);
    s->unit_n = n;
  }
  s->poly.n = 0;
  for (i = 0; i < n; ++i)
    if (ptbuf_push(&s->poly, x + r * s->unit[2 * i], y + r * s->unit[2 * i + 1])) return G2D_GEOM_NOMEM;
  return stroke_emit_poly(s);
}

/* Unit direction of a -> b (the points differ). */
static void unit_dir(pt_t a, pt_t b, double *ux, double *uy) {
  double dx = b.x - a.x, dy = b.y - a.y, l = sqrt(dx * dx + dy * dy);
  *ux = dx / l;
  *uy = dy / l;
}

/* Join at vertex P between incoming direction d0 and outgoing d1 (unit); L0, L1: the segment lengths.
 * Join and cap shapes never share an edge with a segment rectangle: an edge on the same line, quantized
 * separately, could leave a sliver of up to 1/32 px between them (a crack). They start e inside the adjacent
 * rectangles instead (P - e d0, P + e d1; e = min(hw, L0, L1) / 2), which only overlaps area the stroke
 * covers anyway. */
static int join(stroke_t *s, pt_t P, double d0x, double d0y, double d1x, double d1y, double L0, double L1) {
  double cr = d0x * d1y - d0y * d1x, dt = d0x * d1x + d0y * d1y, hw = s->hw, sg;
  double n0x = -d0y, n0y = d0x, n1x = -d1y, n1y = d1x;   /* left normals */
  double e = 0.5 * dmin(hw, dmin(L0, L1));
  double b0x = P.x - e * d0x, b0y = P.y - e * d0y, b1x = P.x + e * d1x, b1y = P.y + e * d1y;
  if (s->st.join == G2D_JOIN_ROUND) return disc(s, P.x, P.y);
  if (cr == 0.0 && dt > 0.0)                                /* straight on: a patch across the seam */
    return poly4(s, b0x + hw * n0x, b0y + hw * n0y, b1x + hw * n1x, b1y + hw * n1y, b1x - hw * n1x, b1y - hw * n1y,
                 b0x - hw * n0x, b0y - hw * n0y);
  sg = cr > 0.0 ? -1.0 : 1.0;                               /* outer side: right of a left turn */
  {
    double ax = P.x + sg * hw * n0x, ay = P.y + sg * hw * n0y, bx = P.x + sg * hw * n1x, by = P.y + sg * hw * n1y;
    double lim = s->st.miter_limit < 1.0 ? 1.0 : s->st.miter_limit;
    int miter = s->st.join == G2D_JOIN_MITER && 1.0 + dt > 0.0 && 1.0 / sqrt((1.0 + dt) / 2.0) <= lim;
    s->poly.n = 0;
    if (ptbuf_push(&s->poly, b0x, b0y) || ptbuf_push(&s->poly, ax, ay)) return G2D_GEOM_NOMEM;
    if (miter) {                                            /* miter tip: P + hw (n0 + n1) / (1 + n0.n1) */
      double k = sg * hw / (1.0 + dt);
      if (ptbuf_push(&s->poly, P.x + k * (n0x + n1x), P.y + k * (n0y + n1y))) return G2D_GEOM_NOMEM;
    }
    if (ptbuf_push(&s->poly, bx, by) || ptbuf_push(&s->poly, b1x, b1y)) return G2D_GEOM_NOMEM;
    return stroke_emit_poly(s);                             /* bevel (also a miter over the limit) or miter */
  }
}

/* Cap at end point P whose segment (length L) points out of the stroke along d. */
static int cap(stroke_t *s, pt_t P, double dx, double dy, double L) {
  double hw = s->hw, nx = -dy * hw, ny = dx * hw, e = 0.5 * dmin(hw, L);
  if (s->st.cap == G2D_CAP_ROUND) return disc(s, P.x, P.y);
  if (s->st.cap == G2D_CAP_SQUARE)                          /* from e inside the segment to hw beyond P */
    return poly4(s, P.x + nx - dx * e, P.y + ny - dy * e, P.x + nx + dx * hw, P.y + ny + dy * hw, P.x - nx + dx * hw,
                 P.y - ny + dy * hw, P.x - nx - dx * e, P.y - ny - dy * e);
  return G2D_GEOM_OK;
}

static double seg_len(pt_t a, pt_t b) { return sqrt((b.x - a.x) * (b.x - a.x) + (b.y - a.y) * (b.y - a.y)); }

/* Pieces of one polyline p[0..n-1] without zero-length segments, n >= 2 (§9.7 step 3). */
static int stroke_poly(stroke_t *s, const pt_t *p, size_t n, int closed) {
  size_t nseg = closed ? n : n - 1, k;
  int rc;
  for (k = 0; k < nseg; ++k) {                              /* segment rectangles */
    pt_t a = p[k], b = p[(k + 1) % n];
    double ux, uy, nx, ny;
    unit_dir(a, b, &ux, &uy);
    nx = -uy * s->hw;
    ny = ux * s->hw;
    if ((rc = poly4(s, a.x + nx, a.y + ny, b.x + nx, b.y + ny, b.x - nx, b.y - ny, a.x - nx, a.y - ny)) != G2D_GEOM_OK)
      return rc;
  }
  for (k = closed ? 0 : 1; k < (closed ? n : n - 1); ++k) {   /* joins */
    double d0x, d0y, d1x, d1y;
    unit_dir(p[(k + n - 1) % n], p[k], &d0x, &d0y);
    unit_dir(p[k], p[(k + 1) % n], &d1x, &d1y);
    if ((rc = join(s, p[k], d0x, d0y, d1x, d1y, seg_len(p[(k + n - 1) % n], p[k]), seg_len(p[k], p[(k + 1) % n]))) !=
        G2D_GEOM_OK)
      return rc;
  }
  if (!closed) {                                            /* caps */
    double ux, uy;
    unit_dir(p[1], p[0], &ux, &uy);
    if ((rc = cap(s, p[0], ux, uy, seg_len(p[0], p[1]))) != G2D_GEOM_OK) return rc;
    unit_dir(p[n - 2], p[n - 1], &ux, &uy);
    if ((rc = cap(s, p[n - 1], ux, uy, seg_len(p[n - 2], p[n - 1]))) != G2D_GEOM_OK) return rc;
  }
  return G2D_GEOM_OK;
}

/* A dot at P (zero-length subpath or dash): a disc for round caps, for square caps the square of side w whose
 * sides follow the unit tangent (ux, uy) ((1, 0) for a zero-length subpath, OpenVG 1.1 §8.7.4), nothing for butt. */
static int stroke_dot(stroke_t *s, pt_t P, double ux, double uy) {
  double ax = ux * s->hw, ay = uy * s->hw, nx = -ay, ny = ax;
  if (s->st.cap == G2D_CAP_ROUND) return disc(s, P.x, P.y);
  if (s->st.cap == G2D_CAP_SQUARE)
    return poly4(s, P.x - ax + nx, P.y - ay + ny, P.x + ax + nx, P.y + ay + ny, P.x + ax - nx, P.y + ay - ny,
                 P.x - ax - nx, P.y - ay - ny);
  return G2D_GEOM_OK;
}

/* One dash: the points in s->dash, open; zero-length segments dropped; a single point is a dot along (ux, uy). */
static int dash_emit(stroke_t *s, double ux, double uy) {
  pt_t *p = s->dash.p;
  size_t n = 0, i;
  for (i = 0; i < s->dash.n; ++i)
    if (n == 0 || p[i].x != p[n - 1].x || p[i].y != p[n - 1].y) p[n++] = p[i];
  s->dash.n = 0;
  if (n == 0) return G2D_GEOM_OK;
  return n == 1 ? stroke_dot(s, p[0], ux, uy) : stroke_poly(s, p, n, 0);
}

/* Dash state at the start of a subpath (§9.7.1): the phase taken modulo the pattern length, then entries skipped
 * while the phase passes them (an entry ending exactly at the phase is passed unless it has length 0). */
static void dash_start(stroke_t *s) {
  double ph = s->dphase - s->dtotal * floor(s->dphase / s->dtotal);
  uint32_t i = 0;
  while (ph > s->dpat[i] || (ph == s->dpat[i] && s->dpat[i] > 0.0)) {
    ph = ph - s->dpat[i];
    i = (i + 1) % s->dcount;
  }
  s->di = i;
  s->drem = s->dpat[i] - ph;
}

/* Split the polyline p[0..n-1] (closed: with the closing segment) into dashes (§9.7.1, OpenVG 1.1 §8.7.3-8.7.4):
 * along each segment a -> b (length L, unit direction u) the dash boundaries lie at a + u * pos; the state (entry
 * index, remaining length) carries over segments and, without dash_reset, subpaths. */
static int stroke_dashed(stroke_t *s, const pt_t *p, size_t n, int closed) {
  size_t nseg = n == 1 ? 0 : (closed ? n : n - 1), k;
  double ux = 1.0, uy = 0.0;
  int rc;
  if (s->dreset || !s->dstarted) dash_start(s);
  s->dstarted = 1;
  s->dash.n = 0;
  if (!(s->di & 1) && ptbuf_push(&s->dash, p[0].x, p[0].y)) return G2D_GEOM_NOMEM;
  for (k = 0; k < nseg; ++k) {
    pt_t a = p[k], b = p[(k + 1) % n];
    double L = seg_len(a, b), pos = 0.0;
    unit_dir(a, b, &ux, &uy);
    while (s->drem <= L - pos) {
      pt_t q;
      if (++s->dsteps > G2D_DASH_MAX_STEPS) return G2D_GEOM_NOMEM;
      pos = pos + s->drem;
      if (pos == L) {
        q = b;
      } else {
        q.x = a.x + ux * pos;
        q.y = a.y + uy * pos;
      }
      if (ptbuf_push(&s->dash, q.x, q.y)) return G2D_GEOM_NOMEM;
      if (!(s->di & 1)) {                                   /* an "on" entry ends: emit the dash */
        if ((rc = dash_emit(s, ux, uy)) != G2D_GEOM_OK) return rc;
      } else {                                              /* an "off" entry ends: a dash starts at q */
        s->dash.n = 0;
        if (ptbuf_push(&s->dash, q.x, q.y)) return G2D_GEOM_NOMEM;
      }
      s->di = (s->di + 1) % s->dcount;
      s->drem = s->dpat[s->di];
      if (pos == L) break;
    }
    if (pos < L) s->drem = s->drem - (L - pos);
    if (!(s->di & 1) && ptbuf_push(&s->dash, b.x, b.y)) return G2D_GEOM_NOMEM;
  }
  /* an unfinished "on" entry ends with the subpath, except one of positive length that would start exactly at
   * its end (a single point, nothing of the entry used): no dot there */
  if (!(s->di & 1) && !(s->drem > 0.0 && s->drem == s->dpat[s->di] && s->dash.n > 0 &&
                        s->dash.p[0].x == s->dash.p[s->dash.n - 1].x && s->dash.p[0].y == s->dash.p[s->dash.n - 1].y))
    return dash_emit(s, ux, uy);
  s->dash.n = 0;
  return G2D_GEOM_OK;
}

static int stroke_end(void *ctx, int closed, int nops) {
  stroke_t *s = (stroke_t *)ctx;
  pt_t *p = s->sp.p;
  size_t n = 0, i;
  if ((nops == 0 && !closed) || s->sp.n == 0) return G2D_GEOM_OK;   /* a lone MOVE is not stroked; "M Z" is a dot */
  for (i = 0; i < s->sp.n; ++i)                             /* drop zero-length segments */
    if (n == 0 || p[i].x != p[n - 1].x || p[i].y != p[n - 1].y) p[n++] = p[i];
  if (closed && n > 1 && p[n - 1].x == p[0].x && p[n - 1].y == p[0].y) n--;
  if (n == 1) return stroke_dot(s, p[0], 1.0, 0.0);         /* zero-length subpath: a dot, dashes or not */
  return s->dcount ? stroke_dashed(s, p, n, closed) : stroke_poly(s, p, n, closed);
}

int g2d_geom_stroke_edges(const void *data, uint32_t length, int format, const double m[3][3], const g2d_stroke_t *st,
                          g2d_edge_sink_t sink, void *ctx, g2d_rect_t *bbox) {
  static const double ident[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
  stroke_t s;
  walk_t w;
  double sc;
  int rc;
  memset(&s, 0, sizeof(s));
  bbox->x0 = bbox->y0 = bbox->x1 = bbox->y1 = 0;
  if (format != G2D_PATH_S8 && format != G2D_PATH_S16 && format != G2D_PATH_S32 && format != G2D_PATH_FP32)
    return G2D_GEOM_INVALID;
  s.persp = m[2][0] != 0.0 || m[2][1] != 0.0 || m[2][2] != 1.0;
  if (!(st->width > 0.0) || st->cap < 0 || st->cap > 2 || st->join < 0 || st->join > 2) return G2D_GEOM_INVALID;
  /* path-space tolerance: tol / s, s = Frobenius norm of the linear part (>= its largest singular value) */
  sc = sqrt(m[0][0] * m[0][0] + m[0][1] * m[0][1] + m[1][0] * m[1][0] + m[1][1] * m[1][1]);
  if (s.persp) {
    s.eps = persp_eps(m);
    if (s.eps == 0.0) return G2D_GEOM_EMPTY;
  } else if (sc == 0.0) {
    return G2D_GEOM_EMPTY;
  }
  s.e.sink = sink;
  s.e.ctx = ctx;
  s.m = m;
  s.st = *st;
  s.hw = st->width / 2.0;
  s.tol = sc > 0.0 ? G2D_FLATTEN_TOL / sc : 0.0;
  if (st->dash_count >= 2 && st->dash) {           /* dashes (§9.7.1): even count, negative entries -> 0 */
    uint32_t i;
    s.dcount = st->dash_count & ~1u;
    if (!(s.dpat = (double *)malloc(s.dcount * sizeof(double)))) return G2D_GEOM_NOMEM;
    for (i = 0; i < s.dcount; ++i) {
      s.dpat[i] = st->dash[i] > 0.0 ? st->dash[i] : 0.0;   /* also NaN -> 0 */
      s.dtotal = s.dtotal + s.dpat[i];
    }
    if (!(s.dtotal > 0.0) || s.dtotal > 1e300) s.dcount = 0;   /* all-zero (or overflowing) pattern: solid */
    s.dphase = st->dash_phase == st->dash_phase && fabs(st->dash_phase) < 1e300 ? st->dash_phase : 0.0;
    s.dreset = st->dash_reset;
  }
  w.begin = stroke_begin;
  w.point = stroke_point;
  w.end = stroke_end;
  w.ctx = &s;
  /* affine: path space with tol / s; perspective: path space, curves flat within tol after projection (§9.8) */
  rc = s.persp ? walk_path(data, length, format, m, G2D_FLATTEN_TOL, 1, &w)
               : walk_path(data, length, format, ident, s.tol, 0, &w);
  free(s.sp.p);
  free(s.poly.p);
  free(s.proj.p);
  free(s.dash.p);
  free(s.dpat);
  free(s.unit);
  free(s.e.clip_a.p);
  free(s.e.clip_b.p);
  if (rc != G2D_GEOM_OK) return rc;
  if (!s.e.any) return G2D_GEOM_EMPTY;
  finish_bbox(&s.e, bbox);
  return G2D_GEOM_OK;
}

/* ---------------------------------------------------------------------------------------------- */
/* Blit setup (§9.3)                                                                              */
/* ---------------------------------------------------------------------------------------------- */

static int to_fix(double v, double scale, int32_t *out) {
  double r = g2d_rha(v * scale);
  if (!(r >= -2147483648.0 && r <= 2147483647.0)) return G2D_GEOM_INVALID; /* also rejects NaN */
  *out = (int32_t)r;
  return G2D_GEOM_OK;
}

static int blit_setup(const double m[3][3], const g2d_rect_t *src, const g2d_rect_t *clip, int full,
                      g2d_blit_setup_t *out) {
  double inv[3][3];
  double minx = 0, miny = 0, maxx = 0, maxy = 0;
  int corner, any_behind = full, k;
  g2d_rect_t r;

  /* Step 1: draw rectangle (full: the whole clip). */
  for (corner = 0; corner < 4 && !full; ++corner) {
    double sxv = (corner & 1) ? src->x1 : src->x0;
    double syv = (corner & 2) ? src->y1 : src->y0;
    double w = m[2][0] * sxv + m[2][1] * syv + m[2][2];
    double x, y;
    if (w <= 0.0) {
      any_behind = 1;
      break;
    }
    x = (m[0][0] * sxv + m[0][1] * syv + m[0][2]) / w;
    y = (m[1][0] * sxv + m[1][1] * syv + m[1][2]) / w;
    if (corner == 0) {
      minx = maxx = x;
      miny = maxy = y;
    } else {
      minx = dmin(minx, x); maxx = dmax(maxx, x);
      miny = dmin(miny, y); maxy = dmax(maxy, y);
    }
  }
  r = *clip;
  if (!any_behind) {
    double fx0 = floor(minx) - 1.0, fy0 = floor(miny) - 1.0, fx1 = ceil(maxx) + 1.0, fy1 = ceil(maxy) + 1.0;
    if (fx0 > r.x0) r.x0 = fx0 >= r.x1 ? r.x1 : (int32_t)fx0;
    if (fy0 > r.y0) r.y0 = fy0 >= r.y1 ? r.y1 : (int32_t)fy0;
    if (fx1 < r.x1) r.x1 = fx1 <= r.x0 ? r.x0 : (int32_t)fx1;
    if (fy1 < r.y1) r.y1 = fy1 <= r.y0 ? r.y0 : (int32_t)fy1;
  }
  if (r.x1 <= r.x0 || r.y1 <= r.y0) return G2D_GEOM_EMPTY;
  out->draw = r;

  /* Step 2: inverse. */
  if (g2d_geom_inverse(m, inv) != G2D_GEOM_OK) return G2D_GEOM_INVALID;

  /* Step 3: normalisation. */
  if (inv[2][0] == 0.0 && inv[2][1] == 0.0) {
    double s = inv[2][2];
    int i, j;
    for (i = 0; i < 3; ++i)
      for (j = 0; j < 3; ++j) inv[i][j] = inv[i][j] / s;
    out->persp = 0;
    out->xf[6] = 0;
    out->xf[7] = 0;
    out->xf[8] = 1 << 30;
  } else {
    double s = 0.0;
    int i, j;
    for (corner = 0; corner < 4; ++corner) {
      double px = (corner & 1) ? (double)r.x1 - 0.5 : (double)r.x0 + 0.5;
      double py = (corner & 2) ? (double)r.y1 - 0.5 : (double)r.y0 + 0.5;
      s = dmax(s, fabs(inv[2][0] * px + inv[2][1] * py + inv[2][2]));
    }
    if (s == 0.0) return G2D_GEOM_EMPTY;
    for (i = 0; i < 3; ++i)
      for (j = 0; j < 3; ++j) inv[i][j] = inv[i][j] / s;
    out->persp = 1;
    for (k = 0; k < 3; ++k)
      if (to_fix(inv[2][k], 1073741824.0, &out->xf[6 + k])) return G2D_GEOM_INVALID;
  }

  /* Step 4: rows u and v in s15.16. */
  for (k = 0; k < 3; ++k) {
    if (to_fix(inv[0][k], 65536.0, &out->xf[k])) return G2D_GEOM_INVALID;
    if (to_fix(inv[1][k], 65536.0, &out->xf[3 + k])) return G2D_GEOM_INVALID;
  }
  return G2D_GEOM_OK;
}

int g2d_geom_blit_setup(const double m[3][3], const g2d_rect_t *src, const g2d_rect_t *clip,
                        g2d_blit_setup_t *out) {
  return blit_setup(m, src, clip, 0, out);
}

int g2d_geom_blit_setup_full(const double m[3][3], const g2d_rect_t *src, const g2d_rect_t *clip,
                             g2d_blit_setup_t *out) {
  return blit_setup(m, src, clip, 1, out);
}

/* ---------------------------------------------------------------------------------------------- */
/* Gradients (§10.2-10.3)                                                                         */
/* ---------------------------------------------------------------------------------------------- */

int g2d_geom_grad_coeffs(const double gm[3][3], int ext, double x0, double y0, double x1, double y1,
                         int32_t *p, int32_t *q, int64_t *r) {
  double inv[3][3], a, b, c, rr;
  if (gm[2][0] != 0.0 || gm[2][1] != 0.0 || gm[2][2] != 1.0) return G2D_GEOM_NOT_SUPPORT;
  if (g2d_geom_inverse(gm, inv) != G2D_GEOM_OK) return G2D_GEOM_INVALID;
  if (!ext) {
    a = inv[0][0];
    b = inv[0][1];
    c = inv[0][2];
  } else {
    double dx = x1 - x0, dy = y1 - y0, l2 = dx * dx + dy * dy;
    if (l2 == 0.0) return G2D_GEOM_INVALID;
    a = 256.0 * (dx * inv[0][0] + dy * inv[1][0]) / l2;
    b = 256.0 * (dx * inv[0][1] + dy * inv[1][1]) / l2;
    c = 256.0 * (dx * (inv[0][2] - x0) + dy * (inv[1][2] - y0)) / l2;
  }
  if (to_fix(a, 65536.0, p) || to_fix(b, 65536.0, q)) return G2D_GEOM_INVALID;
  rr = g2d_rha(c * 131072.0);
  if (!(rr >= -140737488355328.0 && rr <= 140737488355327.0)) return G2D_GEOM_INVALID; /* 48-bit */
  *r = (int64_t)rr;
  return G2D_GEOM_OK;
}

/* Radial gradient (rendering.md §10.4): T, U, V with T + |(U, V)| = 2^17 * 256 * g(p), OpenVG 1.1 §9.3.2's g in a
 * frame rotated so that the focal offset lies on +x. Focal point moved to 0.99 r when it is farther out. */
int g2d_geom_radial_coeffs(const double gm[3][3], double cx, double cy, double r, double fx, double fy,
                           g2d_geom_radial_t *out) {
  double inv[3][3], fpx, fpy, rho, kap, ex, ey, k, sv, a1, b1, c1, a2, b2, c2;
  double a[3], b[3], c[3];
  int n;
  if (gm[2][0] != 0.0 || gm[2][1] != 0.0 || gm[2][2] != 1.0) return G2D_GEOM_NOT_SUPPORT;
  if (g2d_geom_inverse(gm, inv) != G2D_GEOM_OK) return G2D_GEOM_INVALID;
  if (!(r > 0.0)) { /* r <= 0 (or NaN): g = 1 everywhere */
    for (n = 0; n < 3; ++n) {
      out->p[n] = 0;
      out->q[n] = 0;
      out->r[n] = 0;
    }
    out->r[0] = (int64_t)1 << 25; /* T = 2^17 * 256 */
    return G2D_GEOM_OK;
  }
  fpx = fx - cx;
  fpy = fy - cy;
  rho = sqrt(fpx * fpx + fpy * fpy) / r;
  if (rho > 0.99) { /* outside / too close to the circle: to 0.99 r on the centre-focus line */
    fpx = fpx * (0.99 / rho);
    fpy = fpy * (0.99 / rho);
    fx = cx + fpx;
    fy = cy + fpy;
    kap = 0.99;
  } else {
    kap = rho;
  }
  if (fpx == 0.0 && fpy == 0.0) {
    ex = 1.0;
    ey = 0.0;
  } else {
    double l = sqrt(fpx * fpx + fpy * fpy);
    ex = fpx / l;
    ey = fpy / l;
  }
  /* dx' = (qx - fx) ex + (qy - fy) ey, dy' = -(qx - fx) ey + (qy - fy) ex, q = inv * p */
  a1 = inv[0][0] * ex + inv[1][0] * ey;
  b1 = inv[0][1] * ex + inv[1][1] * ey;
  c1 = (inv[0][2] - fx) * ex + (inv[1][2] - fy) * ey;
  a2 = -inv[0][0] * ey + inv[1][0] * ex;
  b2 = -inv[0][1] * ey + inv[1][1] * ex;
  c2 = -(inv[0][2] - fx) * ey + (inv[1][2] - fy) * ex;
  k = 256.0 / (r * (1.0 - kap * kap));
  sv = sqrt(1.0 - kap * kap);
  a[0] = k * kap * a1; b[0] = k * kap * b1; c[0] = k * kap * c1; /* T */
  a[1] = k * a1;       b[1] = k * b1;       c[1] = k * c1;       /* U */
  a[2] = k * sv * a2;  b[2] = k * sv * b2;  c[2] = k * sv * c2;  /* V */
  for (n = 0; n < 3; ++n) {
    double rr;
    if (to_fix(a[n], 65536.0, &out->p[n]) || to_fix(b[n], 65536.0, &out->q[n])) return G2D_GEOM_INVALID;
    rr = g2d_rha(c[n] * 131072.0);
    if (!(rr >= -140737488355328.0 && rr <= 140737488355327.0)) return G2D_GEOM_INVALID; /* 48-bit */
    out->r[n] = (int64_t)rr;
  }
  return G2D_GEOM_OK;
}

static uint32_t vg_to_argb(uint32_t c) {
  uint32_t a = (c >> 24) & 0xFF, b = (c >> 16) & 0xFF, g = (c >> 8) & 0xFF, r = c & 0xFF;
  return (a << 24) | (r << 16) | (g << 8) | b;
}

int g2d_geom_ramp_legacy(uint32_t count, const uint32_t *colors, const uint32_t *stops, uint32_t ramp[256]) {
  uint32_t n, k;
  if (count < 1 || count > 16) return G2D_GEOM_INVALID;
  for (k = 0; k < count; ++k) {
    if (stops[k] > 255 || (k > 0 && stops[k] < stops[k - 1])) return G2D_GEOM_INVALID;
  }
  for (n = 0; n < 256; ++n) {
    uint32_t out;
    if (n <= stops[0]) {
      out = vg_to_argb(colors[0]);
    } else if (n >= stops[count - 1]) {
      out = vg_to_argb(colors[count - 1]);
    } else {
      uint32_t c0, c1, d, f, ch;
      k = 0;
      while (k + 1 < count && stops[k + 1] <= n) ++k; /* largest k with stops[k] <= n < stops[k+1] */
      c0 = vg_to_argb(colors[k]);
      c1 = vg_to_argb(colors[k + 1]);
      d = stops[k + 1] - stops[k];
      f = n - stops[k];
      out = 0;
      for (ch = 0; ch < 32; ch += 8) {
        uint32_t v0 = (c0 >> ch) & 0xFF, v1 = (c1 >> ch) & 0xFF;
        out |= ((v0 * (d - f) + v1 * f + d / 2) / d) << ch;
      }
    }
    ramp[n] = out;
  }
  return G2D_GEOM_OK;
}

static uint32_t q8(double c) { return (uint32_t)g2d_rha(dclamp(c, 0.0, 1.0) * 255.0); }

int g2d_geom_ramp_ext(uint32_t count, const g2d_ramp_stop_t *s, int premul, uint32_t ramp[256]) {
  uint32_t n, k;
  if (count < 1 || count > 256) return G2D_GEOM_INVALID;
  for (k = 0; k < count; ++k) {
    if (!(s[k].stop >= 0.0 && s[k].stop <= 1.0) || (k > 0 && s[k].stop < s[k - 1].stop)) return G2D_GEOM_INVALID;
  }
  for (n = 0; n < 256; ++n) {
    double t = ((double)n + 0.5) / 256.0;
    double r, g, b, a;
    if (t < s[0].stop) {
      r = s[0].r; g = s[0].g; b = s[0].b; a = s[0].a;
    } else if (t >= s[count - 1].stop) {
      r = s[count - 1].r; g = s[count - 1].g; b = s[count - 1].b; a = s[count - 1].a;
    } else {
      double dt, ds;
      k = 0;
      while (k + 1 < count && s[k + 1].stop <= t) ++k;
      dt = t - s[k].stop;
      ds = s[k + 1].stop - s[k].stop;
      r = s[k].r + (s[k + 1].r - s[k].r) * dt / ds;
      g = s[k].g + (s[k + 1].g - s[k].g) * dt / ds;
      b = s[k].b + (s[k + 1].b - s[k].b) * dt / ds;
      a = s[k].a + (s[k + 1].a - s[k].a) * dt / ds;
    }
    if (premul) {
      r = r * a;
      g = g * a;
      b = b * a;
    }
    ramp[n] = (q8(a) << 24) | (q8(r) << 16) | (q8(g) << 8) | q8(b);
  }
  return G2D_GEOM_OK;
}
