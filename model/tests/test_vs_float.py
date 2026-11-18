# SPDX-License-Identifier: Apache-2.0
"""Driver geometry + golden model vs an independent float renderer (rendering.md §12 tolerances).

These are sanity checks of the fixed-point design and of sw/lib/geom, not bit-exact tests.
"""

import math

import numpy as np
import pytest

import floatref as fr
import scenes as sc

I3 = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def _path_errors(p, m=I3, eo=False, w=64, h=64):
    got, _ = sc.render_coverage(p, m, w, h, even_odd=eo)
    ref = fr.path_coverage(fr.polygons_to_edges(sc.transform_polys(p.float_polys, m)), w, h, eo, ss=32) * 255
    d = np.abs(got.astype(float) - ref)
    edge = (ref > 0) & (ref < 255)
    mse = float((d ** 2).mean())
    psnr = math.inf if mse == 0 else 10 * math.log10(255 ** 2 / mse)
    return d, edge, psnr


def _check_path(d, edge, psnr):
    assert d.max() <= 56, d.max()  # rendering.md §12: two edges per pixel x 28
    if edge.any():
        assert d[edge].mean() <= 12, d[edge].mean()   # rendering.md §12 (per shape)
    assert d[~edge].max() <= 16, d[~edge].max()
    assert psnr >= 38, psnr


@pytest.mark.parametrize("r", [1.7, 3.3, 10.7, 25.2])
def test_circles(r):
    _check_path(*_path_errors(sc.circle(sc.PathData(), 32.3, 31.7, r).end()))


@pytest.mark.parametrize("eo", [False, True])
def test_star_and_rounded_rect(eo):
    _check_path(*_path_errors(sc.star(sc.PathData(), 32, 32, 28).end(), eo=eo))
    _check_path(*_path_errors(sc.rounded_rect(sc.PathData(), 5.25, 7.5, 58.6, 40.1, 9).end(), eo=eo))


def test_ellipse_through_matrix():
    a = 0.37
    m = [[2 * math.cos(a), -0.7 * math.sin(a), 30], [2 * math.sin(a), 0.7 * math.cos(a), 20], [0, 0, 1]]
    _check_path(*_path_errors(sc.circle(sc.PathData(), 0, 0, 14).end(), m))


def test_random_polygons():
    rng = np.random.default_rng(3)
    for _ in range(30):
        p = sc.PathData()
        pts = rng.uniform(-4, 68, (rng.integers(3, 9), 2))
        p.move(*pts[0])
        for q in pts[1:]:
            p.line(*q)
        _check_path(*_path_errors(p.close().end(), eo=bool(rng.integers(2))))


def _blit_mats(rng, n):
    for k in range(n):
        a = rng.uniform(-math.pi, math.pi)
        s = rng.choice([0.37, 0.8, 1.0, 1.7, 3.1])
        persp = k % 3 == 0
        c, sn = math.cos(a) * s, math.sin(a) * s
        g, h = (rng.uniform(-0.004, 0.004), rng.uniform(-0.004, 0.004)) if persp else (0.0, 0.0)
        cx, cy, sx, sy = 48, 40, 12, 10
        yield [[c, -sn, cx - (c * sx - sn * sy)], [sn, c, cy - (sn * sx + c * sy)], [g, h, 1 - g * cx - h * cy]]


@pytest.mark.parametrize("smooth", [True, False])
def test_blits(smooth):
    rng = np.random.default_rng(5)
    src = sc.test_image(24, 20, seed=1, smooth=smooth)
    checked = 0
    for m in _blit_mats(rng, 30):
        inv = np.linalg.inv(np.array(m))
        ref, inside, u, v = fr.blit_bilinear(src, (0, 0, 24, 20), inv, 96, 80)
        got, _ = sc.render_blit(src, m, 96, 80, bilinear=True)
        ch = np.stack([(got >> s) & 255 for s in (24, 16, 8, 0)], -1).astype(float)
        interior = inside & (u > 1) & (u < 23) & (v > 1) & (v < 19)
        if interior.any():
            assert np.abs(ch - ref)[interior].max() <= 2.5
        gotp, _ = sc.render_blit(src, m, 96, 80, bilinear=False)
        safe = inside & (np.abs(u - np.round(u)) > 1e-3) & (np.abs(v - np.round(v)) > 1e-3)
        exp = src[np.clip(np.floor(v).astype(int), 0, 19), np.clip(np.floor(u).astype(int), 0, 23)]
        assert ((gotp != exp) & safe).sum() == 0
        # pixels outside the float source rect (not near its border) are not drawn
        outside = ~inside & ((u < -1e-3) | (u > 24.001) | (v < -1e-3) | (v > 20.001))
        assert (gotp[outside] == 0).all()
        checked += int(safe.sum())
    assert checked > 10000


def test_gradient_index():
    rng = np.random.default_rng(7)
    for _ in range(12):
        a, L = rng.uniform(0, math.pi), rng.uniform(3, 200)
        gmx = [[L / 256 * math.cos(a), -math.sin(a), rng.uniform(0, 64)],
               [L / 256 * math.sin(a), math.cos(a), rng.uniform(0, 64)], [0, 0, 1]]
        out, _, _ = sc.render_gradient(gmx, 64, 64, ([0xFF000000, 0xFFFFFFFF], [0, 255]))
        inv = np.linalg.inv(np.array(gmx))
        py, px = np.mgrid[0:64, 0:64] + 0.5
        idx = np.clip(np.floor(inv[0, 0] * px + inv[0, 1] * py + inv[0, 2]), 0, 255).astype(int)
        assert np.abs((out & 255).astype(int) - idx).max() <= 1


@pytest.mark.parametrize("spread", [0, 1, 2])
def test_gradient_spread_modes(spread):
    """Extended gradient with PAD/REPEAT/REFLECT vs float ramp coordinate (index within ±1, circular for REPEAT)."""
    rng = np.random.default_rng(11 + spread)
    ramp_stops = [[k / 255, 0, 0, k / 255, 1] for k in (0, 255)]  # blue channel = ramp index
    for _ in range(8):
        a, L = rng.uniform(-math.pi, math.pi), rng.uniform(4, 40)
        x0, y0 = rng.uniform(-50, 50), rng.uniform(-50, 50)
        gmx = [[math.cos(a), -math.sin(a), rng.uniform(0, 64)], [math.sin(a), math.cos(a), rng.uniform(0, 64)], [0, 0, 1]]
        ext = (x0, y0, x0 + L, y0)
        out, ramp, _ = sc.render_gradient(gmx, 64, 64, ramp_stops, spread=spread, ext=ext)
        inv = np.linalg.inv(np.array(gmx))
        py, px = np.mgrid[0:64, 0:64] + 0.5
        gx = inv[0, 0] * px + inv[0, 1] * py + inv[0, 2]
        i = np.floor(256 * (gx - x0) / L).astype(np.int64)
        if spread == 0:
            want = np.clip(i, 0, 255)
        elif spread == 1:
            want = i & 255
        else:
            k = i & 511
            want = np.where(k < 256, k, 511 - k)
        got = (out & 255).astype(np.int64)
        d = np.abs(got - want)
        if spread == 1:
            d = np.minimum(d, 256 - d)
        assert d.max() <= 1, (spread, d.max())


def test_perspective_openvg_subset():
    """OPEN_QUESTIONS Q-API-19 (c), v1.5: OpenVG 1.1 (§10.8) ignores MULTIPLY under a projective image transform and
    draws nothing unless every image corner has w > 0; ours multiplies and draws every pixel with w > 0 (an
    intentional superset, rendering.md §7.1). Where OpenVG defines the result (all corners w > 0, NORMAL image mode)
    ours equals it: every pixel whose centre maps into the image is drawn (point sampling exact away from texel
    boundaries, bilinear within §12's 2.5 inside the border), nothing else; MULTIPLY with white gives exactly
    NORMAL; and with corners behind the eye only pixels with w > 0 are drawn."""
    rng = np.random.default_rng(19)
    src = sc.test_image(24, 20, seed=3, smooth=True)
    n = 0
    for m in (mm for mm in _blit_mats(rng, 60) if mm[2][0] or mm[2][1]):
        ws = [m[2][0] * x + m[2][1] * y + m[2][2] for x in (0, 24) for y in (0, 20)]
        assert min(ws) > 0                         # _blit_mats keeps every corner in front of the eye
        inv = np.linalg.inv(np.array(m))
        ref, inside, u, v = fr.blit_bilinear(src, (0, 0, 24, 20), inv, 96, 80)
        got, _ = sc.render_blit(src, m, 96, 80, bilinear=True)
        white, _ = sc.render_blit(src, m, 96, 80, bilinear=True, img_mul=True, color=0xFFFFFFFF)
        assert (white == got).all()
        ch = np.stack([(got >> s) & 255 for s in (24, 16, 8, 0)], -1).astype(float)
        interior = inside & (u > 1) & (u < 23) & (v > 1) & (v < 19)
        assert np.abs(ch - ref)[interior].max(initial=0) <= 2.5
        gotp, _ = sc.render_blit(src, m, 96, 80, bilinear=False)
        safe = (np.abs(u - np.round(u)) > 1e-3) & (np.abs(v - np.round(v)) > 1e-3)
        drawn = gotp != 0                          # src has A > 0 everywhere, the target starts at 0
        assert ((drawn != inside) & safe).sum() == 0
        n += int(inside.sum())
    assert n > 5000
    # superset: corners behind the eye; OpenVG draws nothing, ours the pixels with w > 0
    m = [[1.0, 0.0, 10.0], [0.0, 1.0, 10.0], [-0.03, 0.0, 1.0]]   # w = 1 - 0.03 x: <= 0 for x >= 33.3
    inv = np.linalg.inv(np.array(m))
    px, py = np.meshgrid(np.arange(96) + 0.5, np.arange(80) + 0.5)
    W = inv[2][0] * px + inv[2][1] * py + inv[2][2]
    gotp, _ = sc.render_blit(src, m, 96, 80, bilinear=False)
    assert (gotp != 0).any() and (W[gotp != 0] > 0).all()
