# SPDX-License-Identifier: Apache-2.0
"""The golden model's RTL-structured path rasterizer (model/src/raster.cpp, PathRaster: once-per-band
division, add-and-compare stepping, 256-px segments) against the two other forms of rendering.md §8:
  - gm.coverage_row: the per-row prefix-sum form with a direct ceil division per sample row
  - refimpl.pixel_coverage: the literal per-sample crossing test (independent Python)
and the model's edge load (clamp, orientation, drop, steps) against the spec transcription used by the
RTL edge-loader bench (dv/tests/test_edge_load.py, ref_edges).

Cases: several bands (rects taller than 16 rows) and segments (wider than 256 px), edges starting and
ending inside a band (extrapolated band start), near-horizontal edges (large steps), guard-band
coordinates, edges entirely left of the rect (base winding), winding beyond 255 (mod 256), both fill
rules, AA on and off.
"""

import os
import random
import sys
from pathlib import Path

import pytest

import g2d_model as gm
import refimpl as ref

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dv" / "tests"))
from test_edge_load import ref_edges  # noqa: E402

SEED = int(os.environ.get("SEED", "1"))


def rand_poly(rng, cx, cy, r, n):
    pts = [(int(16 * (cx + rng.uniform(-r, r))), int(16 * (cy + rng.uniform(-r, r)))) for _ in range(n)]
    return [(*pts[i], *pts[(i + 1) % n]) for i in range(n)]


def by_rows(cov, w, h):
    return [list(cov[y * w:(y + 1) * w]) for y in range(h)]


def check_vs_formula(edges, rect, aa, eo):
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    got = by_rows(gm.path_raster([list(e) for e in edges], list(rect), aa, eo), w, h)
    for y in range(h):
        exp = list(gm.coverage_row([list(e) for e in edges], y0 + y, x0, x1, aa, eo))
        assert got[y] == exp, f"row {y0 + y} (aa {aa} eo {eo}): first diff at x {x0 + next(i for i in range(w) if got[y][i] != exp[i])}"
    return got


@pytest.mark.parametrize("aa", [True, False])
@pytest.mark.parametrize("eo", [False, True])
def test_bands_segments_random(aa, eo):
    rng = random.Random(SEED * 7 + aa * 2 + eo)
    for _ in range(25):
        x0, y0 = rng.randint(-20, 300), rng.randint(-20, 200)
        w, h = rng.choice([1, 7, 255, 256, 257, 600]), rng.choice([1, 15, 16, 17, 40])
        rect = (x0, y0, x0 + w, y0 + h)
        edges = rand_poly(rng, x0 + w / 2, y0 + h / 2, max(w, h) * 0.7, rng.randint(3, 40))
        edges += rand_poly(rng, x0 - 30, y0 + h / 2, 20, 4)        # left of the rect: base winding
        check_vs_formula(edges, rect, aa, eo)


@pytest.mark.parametrize("aa", [True, False])
def test_extrapolation_and_steep_shallow(aa):
    """Edges starting/ending inside bands, near-horizontal edges (dy = 1/16 px, huge dx), vertical edges,
    guard-band coordinates and coordinates beyond it (clamped)."""
    rng = random.Random(SEED * 11 + aa)
    lim = (1 << 18) - 1
    for _ in range(30):
        x0, y0 = rng.randint(0, 100), rng.randint(0, 100)
        rect = (x0, y0, x0 + rng.randint(1, 300), y0 + rng.randint(1, 40))
        edges = []
        for _ in range(rng.randint(1, 12)):
            ya = 16 * rect[1] + rng.randint(-40, 16 * (rect[3] - rect[1]) + 40)
            kind = rng.random()
            if kind < 0.3:
                e = (rng.randint(-lim, lim), ya, rng.randint(-lim, lim), ya + rng.choice([1, -1, 2]))
            elif kind < 0.5:
                x = rng.randint(16 * rect[0] - 40, 16 * rect[2] + 40)
                e = (x, ya, x, ya + rng.randint(1, 600))
            elif kind < 0.6:
                e = tuple(rng.choice([-(1 << 31), (1 << 31) - 1, lim, -lim, rng.randint(-5000, 5000)]) for _ in range(4))
            else:
                e = (rng.randint(16 * rect[0] - 400, 16 * rect[2] + 400), ya,
                     rng.randint(16 * rect[0] - 400, 16 * rect[2] + 400), ya + rng.randint(-500, 500))
            edges.append(e)
        check_vs_formula(edges, rect, aa, rng.random() < 0.5)


def test_winding_mod_256():
    """300 coincident upward copies of one closed square: winding 300 = 44 (mod 256) inside."""
    sq = [(0, 0, 160, 0), (160, 0, 160, 160), (160, 160, 0, 160), (0, 160, 0, 0)]
    edges = sq * 300
    for aa in (True, False):
        for eo in (False, True):
            got = check_vs_formula(edges, (-2, -2, 14, 14), aa, eo)
            assert got[5][5] == (255 if not eo else 0)   # 300 mod 256 = 44: non-zero, even


@pytest.mark.parametrize("aa", [True, False])
def test_vs_literal_samples(aa):
    """Small rects against the literal per-sample test of §8.3 (independent reference)."""
    rng = random.Random(SEED * 13 + aa)
    for _ in range(12):
        rect = (rng.randint(-3, 5), rng.randint(-3, 5), 0, 0)
        rect = (rect[0], rect[1], rect[0] + rng.randint(1, 9), rect[1] + rng.randint(1, 20))
        edges = rand_poly(rng, rect[0] + 4, rect[1] + 8, 9, rng.randint(3, 9))
        eo = rng.random() < 0.5
        w = rect[2] - rect[0]
        got = by_rows(gm.path_raster([list(e) for e in edges], list(rect), aa, eo), w, rect[3] - rect[1])
        cl = [tuple(ref.clamp_coord(c) for c in e) for e in edges]
        for y in range(rect[1], rect[3]):
            for x in range(rect[0], rect[2]):
                assert got[y - rect[1]][x - rect[0]] == ref.pixel_coverage(cl, x, y, aa, eo), (x, y)


@pytest.mark.parametrize("aa", [True, False])
def test_load_vs_spec(aa):
    """Model edge load == the spec transcription used by the RTL edge-loader bench."""
    rng = random.Random(SEED * 17 + aa)
    for _ in range(20):
        rx1, ry0 = rng.randint(1, 400), rng.randint(0, 200)
        ry1 = ry0 + rng.randint(1, 100)
        edges = [tuple(rng.choice([rng.randint(-8000, 8000), rng.randint(-(1 << 31), (1 << 31) - 1)])
                       for _ in range(4)) for _ in range(rng.randint(1, 60))]
        got = gm.path_loaded([list(e) for e in edges], [0, ry0, rx1, ry1], aa)
        exp = ref_edges(edges, aa, rx1, ry0, ry1)
        assert [tuple(g[:5]) + ((1 if g[5] > 0 else 0),) + tuple(g[6:]) for g in got] == exp


@pytest.mark.parametrize("aa", [True, False])
def test_small_dy_and_ties(aa):
    """Remainder-exactness stimulus: an off-by-one remainder only changes a carry when it sits next to the
    step remainder, and moves a crossing pixel ~1 time in 32, so long polygon edges (large dy) almost
    never expose it. Many short edges (dy of 1..12 s.4 units, so r takes few values) with random slopes,
    and slopes whose crossings fall exactly on sample columns (ties), starting at random sample-row
    offsets inside the bands so the band start is extrapolated."""
    rng = random.Random(SEED * 19 + aa)
    rect = (0, 0, 64, 40)
    for _ in range(6):
        edges = []
        for _ in range(1500):
            x, y = rng.randint(-200, 16 * 64 + 200), rng.randint(-20, 16 * 40 + 20)
            dy = rng.choice([1, 2, 3, 4, 5, 7, 8, 12])
            if rng.random() < 0.5:
                dx = rng.randint(-3000, 3000)
            else:
                # crossing x = x + (Y - y)*dx/dy hits odd 1/32 sample columns exactly for these slopes
                dx = dy * rng.choice([1, -1, 3, -3, 16, -16]) // 2 * 2 + rng.choice([0, dy])
            e = (x, y, x + dx, y + dy) if rng.random() < 0.5 else (x + dx, y + dy, x, y)
            edges.append(e)
        check_vs_formula(edges, rect, aa, rng.random() < 0.5)
