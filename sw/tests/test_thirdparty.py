# SPDX-License-Identifier: Apache-2.0
"""Third-party cross-check with Skia (skia-python 87.6, BSD-3; docs/TOOLS.md, docs/VERIFICATION_MATRIX.md).

Our golden model (the code both RTL backends match bit-exactly) against an independent 2D renderer on the same
inputs. Every check also compares both with the ideal (real-valued) result, so a disagreement is attributed:
  * blends 0-10 (Skia kSrc, kSrcOver, kDstOver, kSrcIn, kDstIn, kMultiply, kScreen, kDarken, kLighten, kPlus,
    kDstOut on premultiplied 8-bit data): each within 1.5 of the ideal formula, mutually within 3;
  * image draws through affine and perspective matrices: nearest sampling equal on every pixel whose sample is not
    within 1e-3 texel of a texel boundary; bilinear each within 2.5 of the float reference (rendering.md §12) on
    pixels at least one texel inside the image, mutually within 5;
  * linear gradients with PAD / REPEAT / REFLECT (Skia kClamp / kRepeat / kMirror): each within 2.5 levels of the
    float ramp (our 256-entry ramp: index within 1), mutually within 5 (circular at the REPEAT seam);
  * filled paths: both against the exact area (64x64 supersampling) with the path criteria; mutually within the
    sum of the budgets, and equal (two samples) on pixels the exact shape covers fully or not at all.
The Skia-only patterns, strokes and LVGL images are in test_stroke.py and test_lvgl.py.
"""

from __future__ import annotations

import math
import os
import random
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "dv" / "common"), os.environ.get("G2D_MODEL_PATH", str(ROOT / "build" / "model"))]

import floatref  # noqa: E402
import g2d_model as gm  # noqa: E402
import scenes  # noqa: E402

skia = pytest.importorskip("skia")

SEED = int(os.environ.get("SEED", "1"))
FULL = os.environ.get("G2D_FULL") == "1"
PM = dict(colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kPremul_AlphaType)

SKIA_BLEND = {0: "kSrc", 1: "kSrcOver", 2: "kDstOver", 3: "kSrcIn", 4: "kDstIn", 5: "kMultiply", 6: "kScreen",
              7: "kDarken", 8: "kLighten", 9: "kPlus", 10: "kDstOut"}
IDEAL = {0: lambda S, D, SA, DA: S,
         1: lambda S, D, SA, DA: S + D * (1 - SA),
         2: lambda S, D, SA, DA: S * (1 - DA) + D,
         3: lambda S, D, SA, DA: S * DA,
         4: lambda S, D, SA, DA: D * SA,
         5: lambda S, D, SA, DA: S * (1 - DA) + D * (1 - SA) + S * D,
         6: lambda S, D, SA, DA: S + D - S * D,
         7: lambda S, D, SA, DA: np.minimum(S + D * (1 - SA), S * (1 - DA) + D),
         8: lambda S, D, SA, DA: np.maximum(S + D * (1 - SA), S * (1 - DA) + D),
         9: lambda S, D, SA, DA: np.minimum(S + D, 1),
         10: lambda S, D, SA, DA: D * (1 - SA)}


def rand_premul(rng, n):
    a = rng.integers(0, 256, n)
    c = (rng.random((n, 3)) * (a[:, None] + 1)).astype(np.int64).clip(0, 255)
    c = np.minimum(c, a[:, None])
    return np.concatenate([c, a[:, None]], 1).astype(np.uint8)        # R, G, B, A


@pytest.mark.parametrize("mode", sorted(SKIA_BLEND))
def test_blend_vs_skia(mode):
    rng = np.random.default_rng(SEED * 31 + mode)
    n = 64 * 64
    s, d = rand_premul(rng, n), rand_premul(rng, n)
    pack = lambda x: (x[:, 3].astype(np.uint32) << 24) | (x[:, 0].astype(np.uint32) << 16) | \
        (x[:, 1].astype(np.uint32) << 8) | x[:, 2].astype(np.uint32)  # noqa: E731
    out = gm.blend_many(mode, pack(s), pack(d)).astype(np.int64)
    ours = np.stack([(out >> 16) & 255, (out >> 8) & 255, out & 255, out >> 24], 1).astype(float)
    dst = d.reshape(64, 64, 4).copy()
    surf = skia.Surface(dst, **PM)
    surf.getCanvas().drawImage(skia.Image.fromarray(s.reshape(64, 64, 4).copy(), **PM), 0, 0,
                               skia.Paint(BlendMode=getattr(skia.BlendMode, SKIA_BLEND[mode])))
    sk = dst.reshape(n, 4).astype(float)
    S, D = s / 255.0, d / 255.0
    ideal = np.clip(IDEAL[mode](S, D, S[:, 3:4], D[:, 3:4]), 0, 1) * 255
    e_ours, e_sk, e_mut = np.abs(ours - ideal).max(), np.abs(sk - ideal).max(), np.abs(ours - sk).max()
    assert e_ours <= 1.5 and e_sk <= 1.5 and e_mut <= 3, (mode, e_ours, e_sk, e_mut)


def skia_image_draw(src_argb, m, w, h, linear):
    """src_argb (h, w) ARGB32, straight opaque-or-premultiplied per caller; draws with matrix m (source texel space
    -> target) onto transparent black with kSrc."""
    sh, sw = src_argb.shape
    rgba = np.stack([(src_argb >> 16) & 255, (src_argb >> 8) & 255, src_argb & 255, src_argb >> 24], -1).astype(np.uint8)
    dst = np.zeros((h, w, 4), np.uint8)
    surf = skia.Surface(dst, **PM)
    c = surf.getCanvas()
    c.concat(skia.Matrix.MakeAll(*[float(v) for r in m for v in r]))
    paint = skia.Paint(BlendMode=skia.BlendMode.kSrc,
                       FilterQuality=skia.FilterQuality.kLow_FilterQuality if linear else skia.FilterQuality.kNone_FilterQuality)
    c.drawImage(skia.Image.fromarray(rgba.copy(), **PM), 0, 0, paint)
    return np.stack([dst[:, :, 3], dst[:, :, 0], dst[:, :, 1], dst[:, :, 2]], -1).astype(float)   # A R G B


def blit_mats(rng, n):
    for k in range(n):
        a, s = rng.uniform(-math.pi, math.pi), rng.choice([0.6, 1.0, 1.7, 2.6])
        c, sn = math.cos(a) * s, math.sin(a) * s
        g, h = (rng.uniform(-0.004, 0.004), rng.uniform(-0.004, 0.004)) if k % 3 == 2 else (0.0, 0.0)
        cx, cy, sx, sy = 48, 40, 12, 10
        yield [[c, -sn, cx - (c * sx - sn * sy)], [sn, c, cy - (sn * sx + c * sy)], [g, h, 1 - g * cx - h * cy]]


@pytest.mark.parametrize("linear", [False, True])
def test_image_draw_vs_skia(linear):
    rng = np.random.default_rng(SEED * 5 + int(linear))
    src = scenes.test_image(24, 20, seed=3, smooth=True) | np.uint32(0xFF000000)     # opaque: straight == premul
    checked = 0
    for m in blit_mats(rng, 6 if FULL else 3):
        inv = np.linalg.inv(np.array(m))
        got, _ = scenes.render_blit(src, m, 96, 80, bilinear=linear)
        ours = np.stack([(got >> s) & 255 for s in (24, 16, 8, 0)], -1).astype(float)
        sk = skia_image_draw(src, m, 96, 80, linear)
        ref, inside, u, v = floatref.blit_bilinear(src, (0, 0, 24, 20), inv, 96, 80)
        if linear:
            deep = inside & (u > 1) & (u < 23) & (v > 1) & (v < 19)
            e_o, e_s = np.abs(ours - ref)[deep].max(), np.abs(sk - ref)[deep].max()
            e_m = np.abs(ours - sk)[deep].max()
            assert e_o <= 2.5 and e_s <= 2.5 and e_m <= 5, (m, e_o, e_s, e_m)
            checked += int(deep.sum())
        else:
            safe = inside & (np.abs(u - np.round(u)) > 1e-3) & (np.abs(v - np.round(v)) > 1e-3) & \
                (u > 0.5) & (u < 23.5) & (v > 0.5) & (v < 19.5)
            assert (ours[safe] == sk[safe]).all(), (m, int((ours[safe] != sk[safe]).any(axis=1).sum()))
            checked += int(safe.sum())
    assert checked > 1000


@pytest.mark.parametrize("spread", [0, 1, 2])
def test_gradient_vs_skia(spread):
    rng = np.random.default_rng(SEED * 11 + spread)
    stops = [[0.0, 0.0, 0.0, 0.0, 1.0], [1.0, 1.0, 0.5, 0.0, 1.0]]           # straight, opaque
    for _ in range(4 if FULL else 2):
        a, L = rng.uniform(-math.pi, math.pi), rng.uniform(8, 40)
        x0, y0 = rng.uniform(0, 64), rng.uniform(0, 64)
        gmx = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        x1, y1 = x0 + L * math.cos(a), y0 + L * math.sin(a)
        out, _, _ = scenes.render_gradient(gmx, 64, 64, stops, spread=spread, ext=(x0, y0, x1, y1))
        ours = np.stack([(out >> 16) & 255, (out >> 8) & 255], -1).astype(float)          # R, G
        dst = np.zeros((64, 64, 4), np.uint8)
        surf = skia.Surface(dst, **PM)
        shader = skia.GradientShader.MakeLinear([skia.Point(x0, y0), skia.Point(x1, y1)],
                                                [skia.Color4f(0, 0, 0, 1).toColor(), skia.Color4f(1, 0.5, 0, 1).toColor()],
                                                [0.0, 1.0], [skia.TileMode.kClamp, skia.TileMode.kRepeat, skia.TileMode.kMirror][spread])
        surf.getCanvas().drawPaint(skia.Paint(Shader=shader, BlendMode=skia.BlendMode.kSrc, Dither=False))
        sk = dst[:, :, :2].astype(float)
        py, px = np.mgrid[0:64, 0:64] + 0.5
        t = ((px - x0) * (x1 - x0) + (py - y0) * (y1 - y0)) / (L * L)
        t = {0: np.clip(t, 0, 1), 1: t - np.floor(t), 2: 1 - np.abs((t % 2) - 1)}[spread]
        ref = np.stack([t * 255, t * 127.5], -1)
        def dist(x, y):
            d = np.abs(x - y)
            return np.minimum(d, np.array([255.0, 127.5]) - d) if spread == 1 else d
        e_o, e_s, e_m = dist(ours, ref).max(), dist(sk, ref).max(), dist(ours, sk).max()
        assert e_o <= 2.5 and e_s <= 2.5 and e_m <= 5, (spread, e_o, e_s, e_m)


def test_fill_vs_skia():
    rng = random.Random(SEED * 17)
    for k in range(8 if FULL else 3):
        n = rng.randint(3, 7)
        ang = sorted(rng.uniform(0, 2 * math.pi) for _ in range(n))
        r = rng.uniform(10, 28)
        pts = [(32 + r * math.cos(t) + rng.uniform(-4, 4), 32 + r * math.sin(t) + rng.uniform(-4, 4)) for t in ang]
        even_odd = k % 2 == 1
        p = scenes.PathData()
        p.move(*pts[0])
        for q in pts[1:]:
            p.line(*q)
        p.close().end()
        cov, _ = scenes.render_coverage(p, [[1, 0, 0], [0, 1, 0], [0, 0, 1]], 64, 64, even_odd=even_odd)
        ours = cov.astype(float)
        dst = np.zeros((64, 64, 4), np.uint8)
        surf = skia.Surface(dst, **PM)
        path = skia.Path()
        path.setFillType(skia.PathFillType.kEvenOdd if even_odd else skia.PathFillType.kWinding)
        f32 = lambda v: float(np.float32(v))  # noqa: E731
        path.moveTo(f32(pts[0][0]), f32(pts[0][1]))
        for q in pts[1:]:
            path.lineTo(f32(q[0]), f32(q[1]))
        path.close()
        surf.getCanvas().drawPath(path, skia.Paint(AntiAlias=True, Color=skia.ColorWHITE))
        sk = dst[:, :, 3].astype(float)
        poly = [(float(np.float32(x)), float(np.float32(y))) for x, y in pts]
        ref = floatref.path_coverage(floatref.polygons_to_edges([poly]), 64, 64, even_odd, ss=64) * 255
        edge = (ref > 0) & (ref < 255)
        for name, img in (("ours", ours), ("Skia", sk)):
            d = np.abs(img - ref)
            assert d.max() <= 56 and d[~edge].max(initial=0) <= 16, (name, d.max())
            if edge.sum() >= 20:
                assert d[edge].mean() <= 12, (name, d[edge].mean())
        dm = np.abs(ours - sk)
        assert dm[~edge].max(initial=0) <= 32 and dm.max() <= 112, dm.max()
