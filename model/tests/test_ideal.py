# SPDX-License-Identifier: Apache-2.0
"""Stage functions vs ideal real-valued formulas (independent of rendering.md's integer definitions).

The bit-exact tests (test_stages.py) compare the model with an independent re-implementation of the same
integer spec, so a wrong spec passes them; B-6.1 (SCREEN) was such a case. These tests compare against the
mathematical intent instead, with the tolerance the integer definition allows:
  * bilinear filter vs real bilinear interpolation at the true fraction (8-bit weights: <= 1.5 levels);
  * gradient ramps (legacy and extended, with and without premultiplication) vs float interpolation (<= 0.5);
  * AA path coverage vs the exact area of the polygon inside each pixel (rendering.md §8.2 figures).
"""

import math
import os

import numpy as np
import pytest

import g2d_model as gm
import scenes as sc

SEED = int(os.environ.get("SEED", "1"))


def _ch(v, s):
    return (np.asarray(v, np.int64) >> s) & 0xFF


def test_bilinear_vs_ideal():
    """Two bounds, both derived (not fitted): at the quantised fraction (fx/256, fy/256) the result is the real
    bilinear value rounded (<= 0.5); at the true fraction f in [fx/256, (fx+1)/256) the 8-bit weights add
    < 255/256 per axis, so <= 0.5 + 2*255/256 = 2.49 (the blit tolerance of rendering.md §12 is 2.5)."""
    rng = np.random.default_rng(SEED)
    worst_q = worst_t = 0.0
    for _ in range(20000):
        t = [int(x) for x in rng.integers(0, 2**32, 4, dtype=np.uint64)]
        f, g = rng.random(), rng.random()
        fx, fy = int(f * 256), int(g * 256)
        got = gm.bilinear(t, fx, fy)
        for s in (24, 16, 8, 0):
            c00, c10, c01, c11 = (_ch(x, s) for x in t)
            def ideal(a, b):
                return c00 * (1 - a) * (1 - b) + c10 * a * (1 - b) + c01 * (1 - a) * b + c11 * a * b
            v = int(_ch(got, s))
            worst_q = max(worst_q, abs(v - ideal(fx / 256, fy / 256)))
            worst_t = max(worst_t, abs(v - ideal(f, g)))
    assert worst_q <= 0.5 + 1e-9, worst_q
    assert worst_t <= 0.5 + 2 * 255 / 256 + 1e-9, worst_t


def _ideal_ext(stops, premul):
    out = np.zeros((256, 4))
    pos = np.array([s[0] for s in stops])
    col = np.array([s[1:] for s in stops], float)            # r, g, b, a
    for n in range(256):
        t = (n + 0.5) / 256
        if t < pos[0]:
            c = col[0]
        elif t >= pos[-1]:
            c = col[-1]
        else:
            k = int(np.searchsorted(pos, t, side="right") - 1)
            c = col[k] + (col[k + 1] - col[k]) * (t - pos[k]) / (pos[k + 1] - pos[k])
        c = np.clip(c, 0, 1)
        if premul:
            c = np.array([c[0] * c[3], c[1] * c[3], c[2] * c[3], c[3]])
        out[n] = c * 255
    return out


@pytest.mark.parametrize("premul", [False, True])
def test_ramp_ext_vs_ideal(premul):
    rng = np.random.default_rng(SEED + 10 * premul)
    for _ in range(50):
        k = int(rng.integers(1, 9))
        pos = np.sort(rng.random(k))
        stops = [[float(p), *map(float, rng.random(4))] for p in pos]
        rc, ramp = gm.ramp_ext(stops, premul)
        assert rc == 0
        ideal = _ideal_ext(stops, premul)
        got = np.stack([_ch(ramp, 16), _ch(ramp, 8), _ch(ramp, 0), _ch(ramp, 24)], -1)   # r, g, b, a
        assert np.abs(got - ideal).max() <= 0.5 + 1e-9, np.abs(got - ideal).max()


def test_ramp_legacy_vs_ideal():
    rng = np.random.default_rng(SEED + 20)
    for _ in range(50):
        k = int(rng.integers(1, 17))
        stops = sorted(int(x) for x in rng.integers(0, 256, k))
        colors = [int(x) for x in rng.integers(0, 2**32, k, dtype=np.uint64)]
        rc, ramp = gm.ramp_legacy(colors, stops)
        assert rc == 0
        for n in range(256):
            if n <= stops[0]:
                wantc = [(colors[0] >> s) & 255 for s in (0, 8, 16, 24)]       # vg_lite_color_t: R G B A bytes
            elif n >= stops[-1]:
                wantc = [(colors[-1] >> s) & 255 for s in (0, 8, 16, 24)]
            else:
                j = max(i for i in range(k - 1) if stops[i] <= n < stops[i + 1]) if any(
                    stops[i] <= n < stops[i + 1] for i in range(k - 1)) else None
                d, f = stops[j + 1] - stops[j], n - stops[j]
                wantc = [((colors[j] >> s) & 255) + (((colors[j + 1] >> s) & 255) - ((colors[j] >> s) & 255)) * f / d
                         for s in (0, 8, 16, 24)]
            got = [_ch(ramp[n], 16), _ch(ramp[n], 8), _ch(ramp[n], 0), _ch(ramp[n], 24)]   # ARGB32 -> R G B A
            assert max(abs(int(g) - w) for g, w in zip(got, wantc)) <= 0.5 + 1e-9, (n, got, wantc)


# ---- AA coverage vs exact area ------------------------------------------------------------------------

def _clip_area(poly, x0, y0):
    """Area of a polygon inside the unit square at (x0, y0) (Sutherland-Hodgman, convex clip region)."""
    def clip(p, inside, cross):
        out = []
        for i in range(len(p)):
            a, b = p[i - 1], p[i]
            ia, ib = inside(a), inside(b)
            if ib:
                if not ia:
                    out.append(cross(a, b))
                out.append(b)
            elif ia:
                out.append(cross(a, b))
        return out
    def xcut(xc):
        return lambda a, b: (xc, a[1] + (b[1] - a[1]) * (xc - a[0]) / (b[0] - a[0]))
    def ycut(yc):
        return lambda a, b: (a[0] + (b[0] - a[0]) * (yc - a[1]) / (b[1] - a[1]), yc)
    p = poly
    for inside, cross in ((lambda q: q[0] >= x0, xcut(x0)), (lambda q: q[0] <= x0 + 1, xcut(x0 + 1)),
                          (lambda q: q[1] >= y0, ycut(y0)), (lambda q: q[1] <= y0 + 1, ycut(y0 + 1))):
        if not p:
            return 0.0
        p = clip(p, inside, cross)
    if len(p) < 3:
        return 0.0
    return abs(sum(p[i - 1][0] * p[i][1] - p[i][0] * p[i - 1][1] for i in range(len(p)))) / 2


def _edges_in_pixel(poly, x0, y0):
    """Number of polygon edges passing through the open unit square, and whether a vertex lies in it."""
    n = 0
    for i in range(len(poly)):
        (ax, ay), (bx, by) = poly[i - 1], poly[i]
        t0, t1 = 0.0, 1.0
        ok = True
        for p, q in ((-(bx - ax), ax - x0), (bx - ax, x0 + 1 - ax), (-(by - ay), ay - y0), (by - ay, y0 + 1 - ay)):
            if p == 0:
                if q < 0:
                    ok = False
            else:
                t = q / p
                if p < 0:
                    t0 = max(t0, t)
                else:
                    t1 = min(t1, t)
        n += ok and t0 < t1
    vert = any(x0 < x < x0 + 1 and y0 < y < y0 + 1 for x, y in poly)
    return n, vert


def test_coverage_vs_exact_area():
    """Random convex polygons with vertices on the s.4 grid (no driver quantisation). rendering.md §8.2 / §12:
    any pixel errs by at most 56. One-edge pixels of this population (rendering.md §8.2, Phase 6 measurement over
    400 seeds x 48 polygons): mean 6.27 (sd of the 48-polygon mean 0.12, max 6.63), max 28.9; asserted: mean
    <= 6.8 (> 4 sd margin), max <= 32 (two samples of 16). The first version averaged 12 polygons against 6.5
    and 28.5, figures of the random-edge experiment of §8.2 (a different population): it failed on seed 117.
    Discrimination: the rejected lattice pattern of §8.2 gives mean 7.5 / max 42 on this population."""
    rng = np.random.default_rng(SEED + 30)
    one_edge, worst_any = [], 0.0
    for _ in range(48):
        n = int(rng.integers(3, 7))
        ang = np.sort(rng.uniform(0, 2 * math.pi, n))
        r = rng.uniform(10, 28)
        poly = [(round((32 + r * math.cos(a)) * 16) / 16, round((32 + r * math.sin(a)) * 16) / 16) for a in ang]
        p = sc.PathData()
        p.move(*poly[0])
        for q in poly[1:]:
            p.line(*q)
        p.close().end()
        cov, _ = sc.render_coverage(p, [[1, 0, 0], [0, 1, 0], [0, 0, 1]], 64, 64)
        for y in range(64):
            for x in range(64):
                ne, vert = _edges_in_pixel(poly, x, y)
                if ne == 0:
                    continue
                exact = _clip_area(poly, x, y) * 255
                d = abs(int(cov[y, x]) - exact)
                worst_any = max(worst_any, d)
                if ne == 1 and not vert:
                    one_edge.append(d)
    one_edge = np.array(one_edge)
    assert worst_any <= 56, worst_any
    assert one_edge.max() <= 32, one_edge.max()
    assert one_edge.mean() <= 6.8, one_edge.mean()
