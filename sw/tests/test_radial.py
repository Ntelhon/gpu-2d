# SPDX-License-Identifier: Apache-2.0
"""v1.5 F-4: radial gradients (rendering.md §10.4) through the driver and the software backend.

  * index vs the float function: every pixel's unspread ramp index, computed from the recorded GRAD_* state with the
    hardware formula (dv/common/refimpl.py), is within 1 of floor(256 g) with g OpenVG 1.1 §9.3.2's function in
    double (focal point outside / within 0.99 r of the circle moved to 0.99 r as the spec allows; r <= 0: g = 1);
    centred, focal, near-circle, outside focal points, under random gradient matrices (rotation, non-uniform scale,
    shear, translation), every spread mode;
  * every byte replays bit-exactly on the independent Python reference (test_scenes.Replay);
  * Skia (third party, skia-python): the two-point conical gradient from the focal point (radius 0) to the circle,
    black to white, PAD, is the same function; grey levels agree within 3 (ramp quantisation 1/256 + rounding)
    except on the 1-pixel ring where the ramp index changes.
"""

from __future__ import annotations

import math
import random

import numpy as np
import pytest

import refimpl
from test_scenes import E, Replay, Scene, check_rcs, check_replay, mat_str, run, SEED, path_tokens

W, H = 64, 48
SEEDS = [SEED * 100 + k for k in range(3)]
SPREADS = ["PAD", "REPEAT", "REFLECT", "FILL"]


def openvg_g(px, py, cx, cy, r, fx, fy):
    """OpenVG 1.1 §9.3.2 in double, with the focal point moved to 0.99 r when |f - c| > 0.99 r."""
    if not r > 0:
        return np.ones_like(px)
    fpx, fpy = fx - cx, fy - cy
    rho = math.hypot(fpx, fpy) / r
    if rho > 0.99:
        fpx, fpy = fpx * 0.99 / rho, fpy * 0.99 / rho
        fx, fy = cx + fpx, cy + fpy
    dx, dy = px - fx, py - fy
    num = dx * fpx + dy * fpy + np.sqrt(np.maximum(r * r * (dx * dx + dy * dy) - (dx * fpy - dy * fpx) ** 2, 0))
    return num / (r * r - (fpx * fpx + fpy * fpy))


def raw_index(p, q, rr, x, y):
    """The hardware formula of §10.4 before the spread (vectorised over x, y with Python ints per pixel)."""
    out = np.empty(x.shape, np.int64)
    for i in np.ndindex(x.shape):
        xi, yi = int(x[i]), int(y[i])
        f = [p[k] * (2 * xi + 1) + q[k] * (2 * yi + 1) + rr[k] for k in range(3)]
        sat = lambda v: max(-(1 << 31), min((1 << 31) - 1, v))  # noqa: E731
        u, v = sat(f[1] >> 9), sat(f[2] >> 9)
        out[i] = ((f[0] >> 9) + math.isqrt(u * u + v * v)) >> 8
    return out


def s32(v):
    return v - (1 << 32) if v & (1 << 31) else v


def s48(lo, hi):
    v = ((hi & 0xFFFF) << 32) | lo
    return v - (1 << 48) if v & (1 << 47) else v


def rand_gmat(rng):
    a, sx, sy, sh = rng.uniform(-3.1, 3.1), rng.uniform(0.4, 2.5), rng.uniform(0.4, 2.5), rng.uniform(-0.4, 0.4)
    c, s = math.cos(a), math.sin(a)
    return [[sx * c, -sy * s + sh, rng.uniform(10, 50)], [sx * s, sy * c, rng.uniform(8, 40)], [0.0, 0.0, 1.0]]


def ramp_tokens(rng, n):
    stops = sorted(rng.random() for _ in range(n))
    stops[0], stops[-1] = 0.0, 1.0
    return [f"{t!r} {rng.random()!r} {rng.random()!r} {rng.random()!r} {rng.random()!r}" for t in stops]


def radial_line(d, spread, gmat, prm, ramp, premul=1):
    el = path_tokens("S16", [(0, 0), (W, 0), (W, H), (0, H)])
    return (f"rgrad {d} {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} {E['VG_LITE_BLEND_NONE']} "
            f"{E[f'VG_LITE_GRADIENT_SPREAD_{spread}']} {premul} 1 0 0 0 1 0 0 0 1 {mat_str(gmat)} "
            f"{' '.join(repr(float(v)) for v in prm)} {len(ramp)} {' '.join(ramp)} {len(el)} {' '.join(el)}")


@pytest.mark.parametrize("seed", SEEDS, ids=[str(k) for k in range(len(SEEDS))])
def test_radial_vs_openvg(tmp_path, seed):
    rng = random.Random(seed)
    sc = Scene()
    cases = []
    d = 0
    for kind in ("centred", "focal", "near", "outside", "zero_r", "centred", "focal", "outside"):
        r = rng.uniform(3, 30) if kind != "zero_r" else rng.choice([0.0, -2.0])
        cx, cy = rng.uniform(-10, 10), rng.uniform(-10, 10)
        rho = {"centred": 0.0, "focal": rng.uniform(0.1, 0.9), "near": rng.uniform(0.991, 0.9999),
               "outside": rng.uniform(1.0, 4.0), "zero_r": 0.5}[kind]
        th = rng.uniform(0, 2 * math.pi)
        fx, fy = cx + rho * abs(r) * math.cos(th), cy + rho * abs(r) * math.sin(th)
        spread = rng.choice(SPREADS)
        gmat = rand_gmat(rng)
        sc.buf(d, W, H, "BGRA8888")
        sc.add(f"gpaint {rng.getrandbits(32)}")
        sc.add(radial_line(d, spread, gmat, (cx, cy, r, fx, fy), ramp_tokens(rng, rng.randint(2, 6))))
        cases.append((d, kind, (cx, cy, r, fx, fy), gmat))
        d += 1
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    rep = Replay(rec)
    draws = [dr for dr in rep.draws if dr[1]["PAINT_CTRL"] & 3 == 3]
    assert len(draws) == len(cases)
    ys, xs = np.mgrid[0:H, 0:W]
    for (dd, kind, prm, gmat), (_, st, _, _, _) in zip(cases, draws):
        p = [s32(st["GRAD_P"]), s32(st["GRAD_UP"]), s32(st["GRAD_VP"])]
        q = [s32(st["GRAD_Q"]), s32(st["GRAD_UQ"]), s32(st["GRAD_VQ"])]
        rr = [s48(st["GRAD_R"], st["GRAD_R_HI"]), s48(st["GRAD_UR"], st["GRAD_UR_HI"]), s48(st["GRAD_VR"], st["GRAD_VR_HI"])]
        got = raw_index(p, q, rr, xs, ys)
        inv = np.linalg.inv(np.asarray(gmat, float))
        px, py = xs + 0.5, ys + 0.5
        gx = inv[0, 0] * px + inv[0, 1] * py + inv[0, 2]
        gy = inv[1, 0] * px + inv[1, 1] * py + inv[1, 2]
        ref = np.floor(256 * openvg_g(gx, gy, *prm))
        far = np.abs(ref) > 2 ** 22               # beyond the documented range (§10.4): u / v saturate
        dmax = np.abs(got - ref)[~far].max()
        assert dmax <= 1, f"draw {dd} ({kind}, {prm}): index |d| {dmax}"
    check_replay(sc, addrs, dumps, rep)


skia = pytest.importorskip("skia") if __name__ != "__main__" else None


@pytest.mark.parametrize("focal", [False, True])
def test_radial_vs_skia(tmp_path, focal):
    """Skia's two-point conical gradient (focal point radius 0 -> circle) = OpenVG's radial function."""
    if skia is None:
        pytest.skip("skia")
    rng = random.Random(SEED * 31 + focal)
    cx, cy, r = 30.0, 22.0, 18.0
    fx, fy = (cx + 9.0, cy - 5.0) if focal else (cx, cy)
    sc = Scene()
    sc.buf(0, W, H, "BGRA8888")
    ramp = ["0.0 0.0 0.0 0.0 1.0", "1.0 1.0 1.0 1.0 1.0"]
    ident = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    sc.add(radial_line(0, "PAD", ident, (cx, cy, r, fx, fy), ramp, 0))
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    img = np.frombuffer(dumps[0], np.uint8).reshape(H, addrs[0][1])[:, :4 * W].reshape(H, W, 4).astype(int)
    dst = np.zeros((H, W, 4), np.uint8)
    surf = skia.Surface(dst, colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kPremul_AlphaType)
    shader = skia.GradientShader.MakeTwoPointConical(skia.Point(fx, fy), 0.0, skia.Point(cx, cy), r,
                                                     [skia.ColorBLACK, skia.ColorWHITE], [0.0, 1.0], skia.TileMode.kClamp)
    surf.getCanvas().drawPaint(skia.Paint(Shader=shader, BlendMode=skia.BlendMode.kSrc, Dither=False))
    ref = surf.toarray()[..., 0].astype(int)
    d = np.abs(img[..., 0] - ref)
    # pixels next to a change of our ramp index may round the other way
    edge = np.zeros_like(d, bool)
    gi = img[..., 0]
    edge[:, 1:] |= gi[:, 1:] != gi[:, :-1]
    edge[1:, :] |= gi[1:, :] != gi[:-1, :]
    assert d[~edge].max() <= 3, d[~edge].max()
    assert d.max() <= 5, d.max()
    _ = rng
