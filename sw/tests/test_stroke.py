# SPDX-License-Identifier: Apache-2.0
"""Strokes (rendering.md §9.7): sw/lib/geom stroker + rasterizer against independent references.

  * round caps + round joins: the stroke is exactly {p : distance(p, polyline) <= w/2}, a definition that shares
    nothing with the polygon construction; coverage by 32x32 supersampling;
  * all cap / join combinations: point membership in the exact stroke region, coded here from the definitions
    (segment rectangles, square / round caps, miter (when 1 / sin(theta/2) <= limit) / bevel / round joins);
  * degenerate input: zero-length subpaths (dots), lone MOVE, zero-length and collinear segments, 180 degree
    reversals, closed two-point subpaths, the miter limit on both sides of its threshold, very thin strokes;
  * a third-party renderer: Skia (skia-python, if installed) with the same caps / joins / miter limit.
Tolerances: the path criteria of rendering.md §12 (pixel |d| <= 56, mean over edge pixels <= 12 per shape,
inside / outside pixels <= 16). Skia uses its own analytic AA, so the same criteria apply to it.
"""

from __future__ import annotations

import math
import os
import random
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "dv" / "common"), os.environ.get("G2D_MODEL_PATH", str(ROOT / "build" / "model"))]

import g2d_model as gm  # noqa: E402
import scenes  # noqa: E402

SEED = int(os.environ.get("SEED", "1"))
FULL = os.environ.get("G2D_FULL") == "1"    # short mode (make smoke): fewer shapes per test
CAPS = {"butt": 0, "round": 1, "square": 2}
JOINS = {"miter": 0, "round": 1, "bevel": 2}
W, H = 64, 64
I3 = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
OP_MOVE, OP_LINE, OP_CLOSE, OP_END = 2, 4, 1, 0


def f32(v):
    return struct.unpack("<f", struct.pack("<f", v))[0]


def path_bytes(subpaths):
    """FP32 path data for [(points, closed)] (MOVE, LINE..., CLOSE?, ... END)."""
    out = b""
    for pts, closed in subpaths:
        out += struct.pack("<I", OP_MOVE) + struct.pack("<2f", *pts[0])
        for p in pts[1:]:
            out += struct.pack("<I", OP_LINE) + struct.pack("<2f", *p)
        if closed:
            out += struct.pack("<I", OP_CLOSE)
    return out + struct.pack("<I", OP_END)


def model_coverage(subpaths, width, cap, join, miter=4.0, m=I3):
    rc, edges, bbox = gm.stroke_edges(path_bytes(subpaths), 3, m, width, miter, CAPS[cap], JOINS[join])
    assert rc in (gm.GEOM_OK, gm.GEOM_EMPTY), rc
    return scenes.render_edges([tuple(e) for e in edges], bbox, W, H).astype(float)


# ---- independent region membership ------------------------------------------------------------------

def clean(pts, closed):
    q = [tuple(map(f32, p)) for p in pts]
    out = []
    for p in q:
        if not out or p != out[-1]:
            out.append(p)
    if closed and len(out) > 1 and out[-1] == out[0]:
        out.pop()
    return out


def inside_region(px, py, subpaths, width, cap, join, miter):
    """Boolean array: samples (px, py) inside the exact stroke."""
    hw = width / 2
    ins = np.zeros(px.shape, bool)
    for pts, closed in subpaths:
        p = clean(pts, closed)
        if not p:
            continue
        if len(p) == 1:
            x, y = p[0]
            if cap == "round":
                ins |= (px - x) ** 2 + (py - y) ** 2 <= hw * hw
            elif cap == "square":
                ins |= (np.abs(px - x) <= hw) & (np.abs(py - y) <= hw)
            continue
        n = len(p)
        segs = [(p[k], p[(k + 1) % n]) for k in range(n if closed else n - 1)]
        for (ax, ay), (bx, by) in segs:
            dx, dy = bx - ax, by - ay
            L = math.hypot(dx, dy)
            ux, uy = dx / L, dy / L
            t = (px - ax) * ux + (py - ay) * uy
            d = -(px - ax) * uy + (py - ay) * ux
            ins |= (t >= 0) & (t <= L) & (np.abs(d) <= hw)
        verts = range(n) if closed else range(1, n - 1)
        for k in verts:
            P = np.array(p[k])
            a, b = np.array(p[(k - 1) % n]), np.array(p[(k + 1) % n])
            d0 = (P - a) / np.linalg.norm(P - a)
            d1 = (b - P) / np.linalg.norm(b - P)
            if join == "round":
                ins |= (px - P[0]) ** 2 + (py - P[1]) ** 2 <= hw * hw
                continue
            cr, dt = d0[0] * d1[1] - d0[1] * d1[0], d0 @ d1
            if cr == 0 and dt > 0:
                continue
            sg = -1 if cr > 0 else 1
            n0, n1 = np.array([-d0[1], d0[0]]), np.array([-d1[1], d1[0]])
            A, B = P + sg * hw * n0, P + sg * hw * n1
            poly = [P, A, B]
            if join == "miter" and 1 + dt > 0 and 1 / math.sqrt((1 + dt) / 2) <= max(miter, 1):
                poly = [P, A, P + sg * hw * (n0 + n1) / (1 + dt), B]
            ins |= in_convex(px, py, poly)
        if not closed:
            for E, F in ((p[0], p[1]), (p[-1], p[-2])):        # end E, its neighbour F
                ex, ey = E
                ux, uy = (ex - F[0]), (ey - F[1])
                L = math.hypot(ux, uy)
                ux, uy = ux / L, uy / L
                if cap == "round":
                    ins |= (px - ex) ** 2 + (py - ey) ** 2 <= hw * hw
                elif cap == "square":
                    t = (px - ex) * ux + (py - ey) * uy
                    d = -(px - ex) * uy + (py - ey) * ux
                    ins |= (t >= 0) & (t <= hw) & (np.abs(d) <= hw)
    return ins


def in_convex(px, py, poly):
    poly = [np.asarray(v, float) for v in poly]
    area = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
               for i in range(len(poly)))
    if area == 0:
        return np.zeros(px.shape, bool)
    s = 1 if area > 0 else -1
    ok = np.ones(px.shape, bool)
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        ok &= s * ((b[0] - a[0]) * (py - a[1]) - (b[1] - a[1]) * (px - a[0])) >= 0
    return ok


def ref_coverage(subpaths, width, cap, join, miter=4.0, ss=32, distance=False):
    ys, xs = np.mgrid[0:H * ss, 0:W * ss]
    px, py = (xs + 0.5) / ss, (ys + 0.5) / ss
    if distance:          # round caps + joins: distance to the polyline (independent of the construction)
        hw = width / 2
        best = np.full(px.shape, np.inf)
        for pts, closed in subpaths:
            p = clean(pts, closed)
            if len(p) == 1:
                best = np.minimum(best, np.hypot(px - p[0][0], py - p[0][1]))
                continue
            n = len(p)
            for k in range(n if closed else n - 1):
                (ax, ay), (bx, by) = p[k], p[(k + 1) % n]
                dx, dy = bx - ax, by - ay
                t = np.clip(((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy), 0, 1)
                best = np.minimum(best, np.hypot(px - ax - t * dx, py - ay - t * dy))
        ins = best <= hw
    else:
        ins = inside_region(px, py, subpaths, width, cap, join, miter)
    return ins.reshape(H, ss, W, ss).mean(axis=(1, 3)) * 255


def check(got, ref, what, mean_limit=12.0):
    d = np.abs(got - ref)
    edge = (ref > 0) & (ref < 255)
    assert d.max() <= 56, f"{what}: max |d| {d.max():.1f}"
    assert d[~edge].max(initial=0) <= 16, f"{what}: inside/outside |d| {d[~edge].max():.1f}"
    if edge.sum() >= 20:
        assert d[edge].mean() <= mean_limit, f"{what}: mean |d| on edge pixels {d[edge].mean():.2f}"
    return d


def rand_poly(rng, n, closed):
    return [(rng.uniform(8, W - 8), rng.uniform(8, H - 8)) for _ in range(n)], closed


SEEDS = [SEED * 100 + k for k in range(3 if FULL else 1)]
SEED_IDS = [str(k) for k in range(len(SEEDS))]   # node ids independent of SEED (scripts/regress.py
                                                    # collects them once and runs them with every seed)


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_round_round_vs_distance(seed):
    rng = random.Random(seed)
    for _ in range(6 if FULL else 2):
        sp = [rand_poly(rng, rng.randint(2, 6), rng.random() < 0.4)]
        w = rng.choice([0.4, 1.0, 2.5, rng.uniform(1, 14)])
        got = model_coverage(sp, w, "round", "round")
        check(got, ref_coverage(sp, w, "round", "round", distance=True), f"round/round w={w:.2f} {sp}")


@pytest.mark.parametrize("cap", list(CAPS))
@pytest.mark.parametrize("join", list(JOINS))
def test_caps_joins_vs_region(cap, join):
    rng = random.Random(SEED * 7 + CAPS[cap] * 3 + JOINS[join])
    for _ in range(4 if FULL else 1):
        sp = [rand_poly(rng, rng.randint(2, 5), rng.random() < 0.3)]
        w, miter = rng.uniform(1.5, 10), rng.choice([1.0, 2.0, 4.0, 10.0])
        got = model_coverage(sp, w, cap, join, miter)
        check(got, ref_coverage(sp, w, cap, join, miter), f"{cap}/{join} w={w:.2f} miter={miter} {sp}")


def test_miter_limit_threshold():
    """A corner of turning angle phi has miter ratio 1/cos(phi/2): a limit just above it gives a miter, just
    below a bevel; the miter tip lies hw/cos(phi/2) from the vertex."""
    rng = random.Random(SEED + 77)
    for phi_deg in ((30, 60, 90, 120, 150) if FULL else (45, 135)):
        phi = math.radians(phi_deg)
        ratio = 1 / math.cos(phi / 2)
        # random sub-pixel position: the per-shape mean bound of rendering.md §12 assumes edges at random phases
        P = (32.0 + rng.random(), 40.0 + rng.random())
        a = (P[0] - 20, P[1])
        b = (P[0] + 20 * math.cos(phi), P[1] - 20 * math.sin(phi))
        sp = [([a, P, b], False)]
        for lim, kind in ((ratio * 1.01, "miter"), (ratio * 0.99, "bevel")):
            got = model_coverage(sp, 6.0, "butt", "miter", lim)
            check(got, ref_coverage(sp, 6.0, "butt", "miter", lim), f"phi={phi_deg} limit={lim:.3f} ({kind})")
        # the miter and the bevel differ near the tip exactly when the limit admits the miter
        m1 = model_coverage(sp, 6.0, "butt", "miter", ratio * 1.01).sum()
        m0 = model_coverage(sp, 6.0, "butt", "miter", ratio * 0.99).sum()
        assert m1 > m0, phi_deg


@pytest.mark.parametrize("cap", list(CAPS))
def test_degenerate(cap):
    cases = {
        "dot (zero-length subpath)": [([(20.0, 20.0), (20.0, 20.0)], False)],
        "closed dot": [([(30.0, 30.0)], True)],
        "zero-length segments": [([(10.0, 10.0), (10.0, 10.0), (30.0, 12.0), (30.0, 12.0), (40.0, 30.0)], False)],
        "collinear points": [([(8.0, 50.0), (20.0, 50.0), (35.0, 50.0), (56.0, 50.0)], False)],
        "reversal (180 degrees)": [([(10.0, 30.0), (50.0, 30.0), (20.0, 30.0)], False)],
        "closed two points": [([(12.0, 12.0), (50.0, 40.0)], True)],
        "short segments": [([(30.0, 30.0), (30.3, 30.1), (30.5, 30.6), (31.0, 30.2)], False)],
    }
    for name, sp in cases.items():
        for join in JOINS:
            for w in ((0.3, 4.0, 9.0) if FULL else (4.0,)):
                got = model_coverage(sp, w, cap, join)
                ref = ref_coverage(sp, w, cap, join)
                check(got, ref, f"{name} {cap}/{join} w={w}", mean_limit=16.0)
    # a lone MOVE is not stroked; a dot with butt caps draws nothing
    rc, edges, _ = gm.stroke_edges(struct.pack("<I2fI", OP_MOVE, 5.0, 5.0, OP_END), 3, I3, 4.0, 4.0, CAPS[cap], 0)
    assert rc == gm.GEOM_EMPTY and not edges
    if cap == "butt":
        assert model_coverage(cases["dot (zero-length subpath)"], 4.0, cap, "round").max() == 0


def test_transformed():
    """Stroked in path space, then transformed: a non-uniform scale gives an elliptical pen; compared with the
    region reference evaluated in path space through the inverse matrix."""
    rng = random.Random(SEED + 5)
    for _ in range(4 if FULL else 1):
        a, sx, sy = rng.uniform(0, math.pi), rng.uniform(0.5, 2.0), rng.uniform(0.5, 2.0)
        m = [[sx * math.cos(a), -sy * math.sin(a), 32.0], [sx * math.sin(a), sy * math.cos(a), 32.0], [0, 0, 1]]
        m = [[f32(v) for v in r] for r in m]
        pts = [(rng.uniform(-14, 14), rng.uniform(-14, 14)) for _ in range(4)]
        sp = [(pts, False)]
        cap, join = rng.choice(list(CAPS)), rng.choice(list(JOINS))
        got = model_coverage(sp, 3.0, cap, join, 4.0, m)
        inv = np.linalg.inv(np.asarray(m, float))
        ss = 32
        ys, xs = np.mgrid[0:H * ss, 0:W * ss]
        tx, ty = (xs + 0.5) / ss, (ys + 0.5) / ss
        px = inv[0, 0] * tx + inv[0, 1] * ty + inv[0, 2]
        py = inv[1, 0] * tx + inv[1, 1] * ty + inv[1, 2]
        ref = inside_region(px, py, sp, 3.0, cap, join, 4.0).reshape(H, ss, W, ss).mean(axis=(1, 3)) * 255
        check(got, ref, f"transformed {cap}/{join}")


AREA_ABS = 4.0 * 255     # absolute part of the area criterion against Skia, in coverage units (test_vs_skia)

try:
    import skia  # noqa: E402  (third-party cross-check, docs/TOOLS.md)
except ImportError:
    skia = None


def skia_stroke(sp, w, cap, join, miter):
    surf = skia.Surface(W, H)
    c = surf.getCanvas()
    c.clear(skia.ColorBLACK)
    paint = skia.Paint(AntiAlias=True, Color=skia.ColorWHITE, Style=skia.Paint.kStroke_Style, StrokeWidth=w,
                       StrokeMiter=miter,
                       StrokeCap={"butt": skia.Paint.kButt_Cap, "round": skia.Paint.kRound_Cap,
                                  "square": skia.Paint.kSquare_Cap}[cap],
                       StrokeJoin={"miter": skia.Paint.kMiter_Join, "round": skia.Paint.kRound_Join,
                                   "bevel": skia.Paint.kBevel_Join}[join])
    path = skia.Path()
    for pts, closed in sp:
        q = clean(pts, closed)
        path.moveTo(*q[0])
        for pt in q[1:]:
            path.lineTo(*pt)
        if closed:
            path.close()
    c.drawPath(path, paint)
    return surf.makeImageSnapshot().toarray()[:, :, 0].astype(float)


@pytest.mark.skipif(skia is None, reason="skia-python not installed")
@pytest.mark.parametrize("cap", list(CAPS))
@pytest.mark.parametrize("join", list(JOINS))
def test_vs_skia(cap, join):
    """Third-party renderer: Skia's stroker and analytic AA on the same polylines, as a cross-check of the stroke
    *geometry* (caps, joins, miter limit). Ours is held to the path criteria against the exact region (64x64
    supersampling), and on every pixel the exact stroke covers fully or not at all within two samples (32).
    Skia's AA has its own error: mean over edge pixels 6-13, and isolated excursions beyond the path bounds
    (> 56, or > 32 on a fully in / out pixel) up to 130 levels, mostly within 1 px of round caps / joins and where
    stroke parts overlap (Phase 6 measurement, 1620 random strokes, 60 seeds x 9 cap / join x 3: 56 shapes with
    excursions, at most 5 pixels each; at every one ours agreed with the exact region; docs/VERIFICATION_MATRIX.md
    D7). So against Skia: at every Skia excursion pixel ours must agree with the exact value (the excursion is
    Skia's), at most 8 excursion pixels per shape, and the total stroke area of ours and Skia within 2 %. A wrong
    join, cap or miter decision shared by ours and the exact reference changes a region and fails the area or the
    count check. (First version: Skia held to max 56 / 32 per pixel, from 2 seeds; it failed on seeds 112, 118.)
    The area check has an absolute part, AREA_ABS = 4 px of area (v1.3): on small shapes (~60 px) the 2 % alone
    failed on full-mode seeds 5, 6, 8 that regressions had never run, with ours within 1.3 % of the exact area and
    Skia off by up to 7.6 % (4.6 px). Measured over 1080 random strokes (seeds 1-40, every cap / join, 3 shapes each)
    and 360 dashed ones: at most 3.1 px beyond the relative part is needed; Skia's own absolute error against the
    exact area reaches 6.2 px."""
    rng = random.Random(SEED * 13 + CAPS[cap] * 3 + JOINS[join])
    for _ in range(3 if FULL else 1):
        sp = [rand_poly(rng, rng.randint(2, 5), rng.random() < 0.3)]
        w, miter = rng.uniform(1.5, 10), rng.choice([1.5, 4.0, 10.0])
        got = model_coverage(sp, w, cap, join, miter)
        sk = skia_stroke(sp, w, cap, join, miter)
        ref = ref_coverage(sp, w, cap, join, miter, ss=64)
        what = f"{cap}/{join} w={w:.2f} miter={miter} {sp}"
        check(got, ref, "ours vs exact " + what)
        full = (ref == 0) | (ref == 255)
        assert np.abs(got - ref)[full].max(initial=0) <= 32, "ours vs exact (fully in / out pixels) " + what
        dsk = np.abs(sk - ref)
        exc = (dsk > 56) | (full & (dsk > 32))
        assert np.abs(got - ref)[exc].max(initial=0) <= 32, "Skia excursion where ours disagrees too " + what
        assert exc.sum() <= 8, f"{exc.sum()} Skia excursion pixels " + what
        assert abs(got.sum() - sk.sum()) <= 0.02 * ref.sum() + AREA_ABS, "stroke area ours vs Skia " + what


# ---- through the driver (vgl_scene): stream replay, edge mapping, FILL_STROKE ---------------------------------

def test_strokes_through_driver(tmp_path):
    """vg_lite_set_stroke + vg_lite_draw for STROKE and FILL_STROKE paths: the recorded stream replays
    bit-exactly on the independent reference, the stroke draw's edges equal sw/lib/geom's (shared geometry,
    rendering.md §9.7), its paint colour is path->stroke_color, the fill of FILL_STROKE comes first with color."""
    import test_scenes as ts
    rng = random.Random(SEED + 900)
    sc = ts.Scene()
    sc.buf(0, W, H, "BGRA8888")
    sc.add(f"fill 0 {rng.getrandbits(31)}")
    calls = []
    for k in range(6):
        sp = [rand_poly(rng, rng.randint(2, 5), rng.random() < 0.4)]
        cap, join = rng.choice(list(CAPS)), rng.choice(list(JOINS))
        typ = rng.choice(["STROKE", "FILL_STROKE"])
        w, miter = round(rng.uniform(0.5, 8), 3), rng.choice([1.0, 4.0, 10.0])
        color, scolor = rng.getrandbits(32), rng.getrandbits(32)
        blend = rng.choice(ts.BLENDS)
        data = path_bytes(sp)
        toks = []
        for pts, closed in sp:
            toks += [f"@{OP_MOVE}", repr(f32(pts[0][0])), repr(f32(pts[0][1]))]
            for p_ in pts[1:]:
                toks += [f"@{OP_LINE}", repr(f32(p_[0])), repr(f32(p_[1]))]
            if closed:
                toks.append(f"@{OP_CLOSE}")
        toks.append(f"@{OP_END}")
        sc.add(f"stroke 0 {ts.E['VG_LITE_FP32']} {ts.E['VG_LITE_HIGH']} {blend} {ts.E[f'VG_LITE_DRAW_{typ}_PATH']} {color} "
               f"{scolor} {ts.E[f'VG_LITE_CAP_{cap.upper()}']} {ts.E[f'VG_LITE_JOIN_{join.upper()}']} {w!r} {miter!r} "
               f"1 0 0 0 1 0 0 0 1 {len(toks)} {' '.join(toks)}")
        calls.append((typ, data, w, miter, cap, join, color, scolor, blend))
    rcs, addrs, dumps, rec = ts.run(sc, tmp_path)
    ts.check_rcs(rcs)
    rep = ts.Replay(rec)
    ts.check_replay(sc, addrs, dumps, rep)
    draws = list(rep.draws)
    for typ, data, w, miter, cap, join, color, scolor, blend in calls:
        if typ == "FILL_STROKE":
            rc, fedges, _ = gm.path_edges(data, 3, I3)
            if rc == 0 and fedges:
                _, st, _, edges, _ = draws.pop(0)
                assert [tuple(e) for e in edges] == [tuple(e) for e in fedges]
                assert st["PAINT_COLOR"] == ts.argb(color) and st["BLEND"] == blend
        rc, sedges, bbox = gm.stroke_edges(data, 3, I3, f32(w), f32(miter), CAPS[cap], JOINS[join])
        if rc != 0 or not sedges:
            continue
        _, st, rect, edges, _ = draws.pop(0)
        assert [tuple(e) for e in edges] == [tuple(e) for e in sedges], "stroke edges"
        assert tuple(rect) == tuple(bbox)
        assert st["PAINT_COLOR"] == ts.argb(scolor) and st["BLEND"] == blend
        assert st["RAST_CTRL"] & 1 == 0, "strokes use NON_ZERO"
    assert not draws


def test_no_cracks():
    """B-6.3 regression: join and cap shapes that shared an edge with a segment rectangle, quantized separately,
    left slivers of up to 1/32 px (2 of 16 samples missing inside the stroke). The case that found it, then sharp
    turns with miter / bevel joins and square caps: no pixel the exact stroke covers fully may lose more than one
    sample."""
    cases = [([([(42.87791949917195, 28.18242951561899), (35.703178449465085, 48.352502615407225),
                 (31.5018258377168, 30.13312908340391)], False)], 8.29, "square", "bevel", 10.0)]
    rng = random.Random(SEED + 31)
    for _ in range(40 if FULL else 12):
        P = (rng.uniform(20, 44), rng.uniform(20, 44))
        a = rng.uniform(0, 2 * math.pi)
        b = a + math.pi + rng.uniform(-1.2, 1.2)                  # sharp turns
        L0, L1 = rng.uniform(8, 20), rng.uniform(8, 20)
        pts = [(P[0] + L0 * math.cos(a), P[1] + L0 * math.sin(a)), P, (P[0] + L1 * math.cos(b), P[1] + L1 * math.sin(b))]
        cases.append(([(pts, False)], rng.uniform(3, 10), rng.choice(["square", "butt"]), rng.choice(["miter", "bevel"]),
                      rng.choice([2.0, 10.0])))
    for sp, w, cap, join, miter in cases:
        got = model_coverage(sp, w, cap, join, miter)
        ref = ref_coverage(sp, w, cap, join, miter)
        inside = ref == 255
        assert (got[inside] >= 255 - 16).all(), f"crack: {cap}/{join} w={w:.2f} {sp}: min {got[inside].min()}"


# ---- dashes (rendering.md §9.7.1, OpenVG 1.1 §8.7.3-8.7.4) -------------------------------------------------------
# Independent reference: the "on" intervals of the pattern are enumerated in arc length from the phase (pattern
# coordinate = arc length + phase, modulo the pattern length), clipped to each subpath and cut out of the polyline;
# every piece is then an open subpath of the exact region above (caps at both ends, joins inside). The C code walks
# the polyline incrementally with a dash state instead.

def norm_pattern(pattern):
    pat = [max(f32(v), 0.0) for v in pattern[:len(pattern) // 2 * 2]]
    return pat if len(pat) >= 2 and sum(pat) > 0 else None


def on_intervals(pat, phase, L):
    """(s0, s1) arc-length intervals of the 'on' entries over [0, L]; zero-length entries give s0 == s1."""
    P = sum(pat)
    ph = phase % P
    offs, o = [], 0.0
    for k, d in enumerate(pat):
        if k % 2 == 0:
            offs.append((o, d))
        o += d
    out = []
    n = -1
    while n * P - ph <= L:
        for o, d in offs:
            s0, s1 = n * P + o - ph, n * P + o + d - ph
            if d == 0:
                if 0 <= s0 <= L:
                    out.append((s0, s0))
            elif s1 > 0 and s0 < L:
                out.append((max(s0, 0.0), min(s1, L)))
        n += 1
    return sorted(out)


def cut(p, closed, s0, s1):
    """Sub-polyline of p between arc lengths s0 <= s1 (closed: with the closing segment) and the tangent at s0."""
    segs = [(p[k], p[(k + 1) % len(p)]) for k in range(len(p) if closed else len(p) - 1)]
    out, acc, tan = [], 0.0, None
    for a, b in segs:
        L = math.dist(a, b)
        lo, hi = max(s0 - acc, 0.0), min(s1 - acc, L)
        u = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
        if lo <= hi and (lo < L or (s0 == s1 and s0 - acc == L and acc + L >= sum(math.dist(*q) for q in segs) - 1e-9)):
            q0 = (a[0] + u[0] * lo, a[1] + u[1] * lo)
            q1 = (a[0] + u[0] * hi, a[1] + u[1] * hi)
            if not out:
                out.append(q0)
                tan = u
            out.append(q1)
        acc += L
    return out, tan


def dash_region(px, py, subpaths, width, cap, join, miter, pattern, phase, reset):
    pat = norm_pattern(pattern)
    if pat is None:
        return inside_region(px, py, subpaths, width, cap, join, miter)
    hw = width / 2
    ins = np.zeros(px.shape, bool)
    ph = phase
    for pts, closed in subpaths:
        p = clean(pts, closed)
        if len(p) < 2:                          # dots are drawn as without dashes
            ins |= inside_region(px, py, [(pts, closed)], width, cap, join, miter)
            continue
        L = sum(math.dist(p[k], p[(k + 1) % len(p)]) for k in range(len(p) if closed else len(p) - 1))
        for s0, s1 in on_intervals(pat, ph, L):
            piece, (ux, uy) = cut(p, closed, s0, s1)
            piece = clean(piece, False)
            if len(piece) >= 2:
                ins |= inside_region(px, py, [(piece, False)], width, cap, join, miter)
            elif cap == "round":
                ins |= (px - piece[0][0]) ** 2 + (py - piece[0][1]) ** 2 <= hw * hw
            elif cap == "square":
                t = (px - piece[0][0]) * ux + (py - piece[0][1]) * uy
                d = -(px - piece[0][0]) * uy + (py - piece[0][1]) * ux
                ins |= (np.abs(t) <= hw) & (np.abs(d) <= hw)
        if not reset:
            ph += L
    return ins


def dash_cov(sp, width, cap, join, pattern, phase=0.0, reset=False, miter=4.0, m=I3):
    rc, edges, bbox = gm.stroke_edges(path_bytes(sp), 3, m, width, miter, CAPS[cap], JOINS[join],
                                      [f32(v) for v in pattern], f32(phase), reset)
    assert rc in (gm.GEOM_OK, gm.GEOM_EMPTY), rc
    return scenes.render_edges([tuple(e) for e in edges], bbox, W, H).astype(float)


def dash_ref(sp, width, cap, join, pattern, phase=0.0, reset=False, miter=4.0, ss=32):
    ys, xs = np.mgrid[0:H * ss, 0:W * ss]
    px, py = (xs + 0.5) / ss, (ys + 0.5) / ss
    ins = dash_region(px, py, sp, width, cap, join, miter, pattern, f32(phase), reset)
    return ins.reshape(H, ss, W, ss).mean(axis=(1, 3)) * 255


def rand_pattern(rng):
    n = rng.choice([2, 2, 3, 4, 5, 6])
    pat = [rng.choice([rng.uniform(0.5, 9), rng.uniform(2, 14), 0.0 if rng.random() < 0.15 else 3.0]) for _ in range(n)]
    if rng.random() < 0.15:
        pat[rng.randrange(n)] = -rng.uniform(0, 4)       # negative entries count as 0
    if sum(max(v, 0) for v in pat[:n // 2 * 2]) < 2:
        pat[0] = 4.0
    return pat


@pytest.mark.parametrize("cap", list(CAPS))
@pytest.mark.parametrize("join", list(JOINS))
def test_dash_vs_region(cap, join):
    """Random dashed strokes (odd counts, zero and negative entries, negative phases, closed subpaths, several
    subpaths with and without dash_reset) against the independent interval reference."""
    rng = random.Random(SEED * 29 + CAPS[cap] * 3 + JOINS[join])
    for _ in range(4 if FULL else 2):
        sp = [rand_poly(rng, rng.randint(2, 5), rng.random() < 0.3) for _ in range(rng.choice([1, 1, 2]))]
        w, miter = rng.uniform(1.0, 6.0), rng.choice([1.5, 4.0, 10.0])
        pat, phase, reset = rand_pattern(rng), rng.uniform(-30, 30), rng.random() < 0.5
        got = dash_cov(sp, w, cap, join, pat, phase, reset, miter)
        ref = dash_ref(sp, w, cap, join, pat, phase, reset, miter)
        check(got, ref, f"dash {cap}/{join} w={w:.2f} pat={pat} phase={phase:.2f} reset={reset} {sp}")


def test_dash_rules():
    """OpenVG 1.1 §8.7.3 rules, as exact equalities of the emitted edges: an odd count ignores the last entry;
    negative entries count as 0; a phase shifted by the pattern length is the same; an all-zero pattern and a
    pattern of fewer than two entries are solid; dash_reset matters only with several subpaths."""
    sp = [([(6.0, 10.0), (58.0, 14.0), (40.0, 52.0)], False), ([(8.0, 56.0), (30.0, 30.0)], False)]
    data = path_bytes(sp)

    def e(pat, phase=0.0, reset=False, cap=1):
        return gm.stroke_edges(data, 3, I3, 3.0, 4.0, cap, 1, pat, phase, reset)

    assert e([4.0, 3.0, 9.0]) == e([4.0, 3.0])
    assert e([-2.0, 3.0, 5.0, 2.0]) == e([0.0, 3.0, 5.0, 2.0])
    assert e([4.0, 3.0, 5.0, 2.0], 2.5) == e([4.0, 3.0, 5.0, 2.0], 2.5 + 14.0) == e([4.0, 3.0, 5.0, 2.0], 2.5 - 28.0)
    solid = e([])
    assert e([0.0, 0.0]) == solid and e([5.0]) == solid and e([0.0, -1.0, 7.0]) == solid
    assert e([4.0, 3.0], 1.0, True) != e([4.0, 3.0], 1.0, False)
    one = path_bytes(sp[:1])
    assert gm.stroke_edges(one, 3, I3, 3.0, 4.0, 1, 1, [4.0, 3.0], 1.0, True) == \
        gm.stroke_edges(one, 3, I3, 3.0, 4.0, 1, 1, [4.0, 3.0], 1.0, False)


@pytest.mark.parametrize("cap", list(CAPS))
def test_dash_dots(cap):
    """Zero-length dashes (OpenVG 1.1 §8.7.3): a disc for round caps, a square along the tangent for square caps,
    nothing for butt caps; the common dotted-line idiom [0, gap] with round caps."""
    rng = random.Random(SEED + 61)
    for _ in range(3 if FULL else 1):
        a = rng.uniform(0, math.pi)
        c = (rng.uniform(28, 36), rng.uniform(28, 36))
        sp = [([(c[0] - 26 * math.cos(a), c[1] - 26 * math.sin(a)), (c[0] + 26 * math.cos(a), c[1] + 26 * math.sin(a))],
               False)]
        pat, phase = [0.0, rng.uniform(5, 9)], rng.uniform(0.3, 2)
        got = dash_cov(sp, 4.0, cap, "round", pat, phase)
        if cap == "butt":
            assert got.max() == 0
            continue
        check(got, dash_ref(sp, 4.0, cap, "round", pat, phase), f"dots {cap} a={a:.2f}", mean_limit=16.0)


def test_dash_exact_fit():
    """A pattern that ends exactly at the end of an open subpath draws no extra dot there (rendering.md §9.7.1);
    a zero-length entry exactly at the end does."""
    sp = [([(10.0, 32.0), (40.0, 32.0)], False)]          # length 30
    fit = dash_cov(sp, 4.0, "round", "round", [10.0, 5.0])  # on [0,10], [15,25]; off [25,30]
    assert fit[28:36, 41:44].max() == 0, "no dot after the last off entry"
    dot = dash_cov(sp, 4.0, "round", "round", [10.0, 5.0, 0.0, 15.0])   # [0,10] on, [15,15] dot, then off [15,30]
    check(dot, dash_ref(sp, 4.0, "round", "round", [10.0, 5.0, 0.0, 15.0]), "zero entry inside")
    end = dash_cov(sp, 4.0, "round", "round", [30.0, 0.0, 0.0, 5.0])   # whole line, then a zero dash at 30
    check(end, dash_ref(sp, 4.0, "round", "round", [30.0, 0.0, 0.0, 5.0]), "zero entry at the end")


def test_dash_transformed_and_perspective():
    """Dashes are measured in path space (§9.7.1), so a matrix scales them with the path: compared through the
    inverse matrix, affine and projective (§9.8; preimages with w <= 0 are outside)."""
    rng = random.Random(SEED + 88)
    for persp in (False, True):
        for _ in range(2 if FULL else 1):
            g, h = (rng.uniform(-0.01, 0.01), rng.uniform(-0.01, 0.01)) if persp else (0.0, 0.0)
            a = rng.uniform(0, math.pi)
            m = [[1.3 * math.cos(a), -0.8 * math.sin(a), 32.0], [1.3 * math.sin(a), 0.8 * math.cos(a), 32.0], [g, h, 1.0]]
            m = [[f32(v) for v in r] for r in m]
            sp = [([(rng.uniform(-20, 20), rng.uniform(-20, 20)) for _ in range(3)], False)]
            cap, join = rng.choice(list(CAPS)), rng.choice(list(JOINS))
            pat, phase = rand_pattern(rng), rng.uniform(0, 10)
            got = dash_cov(sp, 2.5, cap, join, pat, phase, m=m)
            inv = np.linalg.inv(np.asarray(m, float))
            ss = 32
            ys, xs = np.mgrid[0:H * ss, 0:W * ss]
            tx, ty = (xs + 0.5) / ss, (ys + 0.5) / ss
            X = inv[0, 0] * tx + inv[0, 1] * ty + inv[0, 2]
            Y = inv[1, 0] * tx + inv[1, 1] * ty + inv[1, 2]
            Z = inv[2, 0] * tx + inv[2, 1] * ty + inv[2, 2]
            ins = (Z > 0) & dash_region(X / Z, Y / Z, sp, 2.5, cap, join, 4.0, pat, f32(phase), False)
            ref = ins.reshape(H, ss, W, ss).mean(axis=(1, 3)) * 255
            check(got, ref, f"dash persp={persp} {cap}/{join} pat={pat}")


def skia_dash(sp, w, cap, join, miter, pat, phase):
    surf = skia.Surface(W, H)
    c = surf.getCanvas()
    c.clear(skia.ColorBLACK)
    paint = skia.Paint(AntiAlias=True, Color=skia.ColorWHITE, Style=skia.Paint.kStroke_Style, StrokeWidth=w,
                       StrokeMiter=miter,
                       StrokeCap={"butt": skia.Paint.kButt_Cap, "round": skia.Paint.kRound_Cap,
                                  "square": skia.Paint.kSquare_Cap}[cap],
                       StrokeJoin={"miter": skia.Paint.kMiter_Join, "round": skia.Paint.kRound_Join,
                                   "bevel": skia.Paint.kBevel_Join}[join],
                       PathEffect=skia.DashPathEffect.Make(pat, phase))
    path = skia.Path()
    for pts, closed in sp:
        q = clean(pts, closed)
        path.moveTo(*q[0])
        for pt in q[1:]:
            path.lineTo(*pt)
    c.drawPath(path, paint)
    return surf.makeImageSnapshot().toarray()[:, :, 0].astype(float)


@pytest.mark.skipif(skia is None, reason="skia-python not installed")
@pytest.mark.parametrize("cap", list(CAPS))
def test_dash_vs_skia(cap):
    """Third-party cross-check with SkDashPathEffect (same on / off / phase convention) on open polylines with
    positive entries; criteria as test_vs_skia: ours held to the exact reference, Skia's excursions only where ours
    agrees with the exact value, and the stroke areas within 3 % + AREA_ABS (dashes add caps, where Skia's AA differs
    most; measured: at most 0.8 px beyond 3 % over 360 random dashed strokes)."""
    rng = random.Random(SEED * 37 + CAPS[cap])
    for _ in range(3 if FULL else 1):
        sp = [rand_poly(rng, rng.randint(2, 5), False)]
        w, join = rng.uniform(1.5, 6), rng.choice(list(JOINS))
        pat = [rng.uniform(2, 10) for _ in range(rng.choice([2, 4]))]
        phase = rng.uniform(0, 20)
        got = dash_cov(sp, w, cap, join, pat, phase)
        ref = dash_ref(sp, w, cap, join, pat, phase, ss=64)
        sk = skia_dash(sp, w, cap, join, 4.0, [f32(v) for v in pat], f32(phase))
        what = f"dash {cap}/{join} w={w:.2f} pat={pat} phase={phase:.2f} {sp}"
        check(got, ref, "ours vs exact " + what)
        full = (ref == 0) | (ref == 255)
        dsk = np.abs(sk - ref)
        exc = (dsk > 56) | (full & (dsk > 32))
        assert np.abs(got - ref)[exc].max(initial=0) <= 32, "Skia excursion where ours disagrees too " + what
        assert exc.sum() <= 12, f"{exc.sum()} Skia excursion pixels " + what
        assert abs(got.sum() - sk.sum()) <= 0.03 * ref.sum() + AREA_ABS, "stroke area ours vs Skia " + what
