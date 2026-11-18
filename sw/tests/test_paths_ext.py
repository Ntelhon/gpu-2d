# SPDX-License-Identifier: Apache-2.0
"""v1.3 path features (rendering.md §9.2.1 arcs, §9.2 BREAK, §9.8 perspective path matrix) through sw/lib/geom and the
model rasterizer, against independent references:

  * arcs: the centre comes from OpenVG 1.1 Appendix A's unit-circle construction (findUnitCircles: two candidate
    centres, the one whose sweep matches the flags), not from the SVG F.6.5 formula the C code uses; the arc is
    then sampled densely in float (256 points per turn: chord error < 2e-3 px at r = 22) and the region is supersampled;
  * perspective: every supersample of the target is mapped back through the inverse matrix; it is covered iff the
    homogeneous w of its preimage is positive and the preimage lies inside the path-space region (polygon winding
    or stroke distance). Nothing of the driver's flatten / clip / project steps is shared;
  * BREAK: exact equality with the same path written with an explicit MOVE (assumption A3, UNVERIFIED);
  * blit vs path: a perspective rectangle path and a perspective blit of an opaque image agree on every pixel the
    path covers fully or not at all.
Tolerances: the path criteria of rendering.md §12 (pixel |d| <= 56, mean over edge pixels <= 12 per shape,
inside / outside pixels <= 16).
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
FULL = os.environ.get("G2D_FULL") == "1"
W, H = 64, 64
I3 = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
OP = dict(END=0, CLOSE=1, MOVE=2, MOVE_REL=3, LINE=4, LINE_REL=5, QUAD=6, CUBIC=8, BREAK=0x0A,
          SCCWARC=0x13, SCCWARC_REL=0x14, SCWARC=0x15, SCWARC_REL=0x16,
          LCCWARC=0x17, LCCWARC_REL=0x18, LCWARC=0x19, LCWARC_REL=0x1A)
ARC_OPS = ["SCCWARC", "SCWARC", "LCCWARC", "LCWARC"]


def f32(v):
    return struct.unpack("<f", struct.pack("<f", v))[0]


def pbytes(cmds):
    """FP32 path data from [(op name, args...)]."""
    out = b""
    for c in cmds:
        out += struct.pack("<I", OP[c[0]]) + struct.pack(f"<{len(c) - 1}f", *c[1:])
    return out


def fill_cov(data, m=I3, even_odd=False):
    rc, edges, bbox = gm.path_edges(data, 3, m)
    assert rc in (gm.GEOM_OK, gm.GEOM_EMPTY), rc
    return scenes.render_edges([tuple(e) for e in edges], bbox, W, H, even_odd).astype(float)


def stroke_cov(data, width, m=I3, cap=1, join=1, miter=4.0):
    rc, edges, bbox = gm.stroke_edges(data, 3, m, width, miter, cap, join)
    assert rc in (gm.GEOM_OK, gm.GEOM_EMPTY), rc
    return scenes.render_edges([tuple(e) for e in edges], bbox, W, H).astype(float)


def check(got, ref, what, mean_limit=12.0):
    d = np.abs(got - ref)
    edge = (ref > 0) & (ref < 255)
    assert d.max() <= 56, f"{what}: max |d| {d.max():.1f}"
    assert d[~edge].max(initial=0) <= 16, f"{what}: inside/outside |d| {d[~edge].max():.1f}"
    if edge.sum() >= 20:
        assert d[edge].mean() <= mean_limit, f"{what}: mean |d| on edge pixels {d[edge].mean():.2f}"
    return d


# ---- independent arc construction (OpenVG 1.1 Appendix A) ------------------------------------------------------

def arc_points(p0, p1, rh, rv, rot, large, ccw, per_turn=256):
    """Dense points of the arc from p0 to p1 (p0 excluded, p1 included), path space."""
    rh, rv = abs(rh), abs(rv)
    if p0 == p1 or rh == 0 or rv == 0:
        return [p1]
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))

    def to_unit(p):                     # rotate by -rot, scale by 1/(rh, rv)
        return ((p[0] * c + p[1] * s) / rh, (-p[0] * s + p[1] * c) / rv)

    def from_unit(q, rh, rv):
        x, y = q[0] * rh, q[1] * rv
        return (x * c - y * s, x * s + y * c)

    u0, u1 = to_unit(p0), to_unit(p1)
    dx, dy = u0[0] - u1[0], u0[1] - u1[1]
    dsq = dx * dx + dy * dy
    disc = 1.0 / dsq - 0.25
    if disc < 0:                        # too far apart: scale radii up until the points are a diameter apart
        k = math.sqrt(dsq) / 2
        rh, rv = rh * k, rv * k
        u0, u1 = (u0[0] / k, u0[1] / k), (u1[0] / k, u1[1] / k)
        cands = [((u0[0] + u1[0]) / 2, (u0[1] + u1[1]) / 2)]
    else:
        sq = math.sqrt(disc)
        xm, ym = (u0[0] + u1[0]) / 2, (u0[1] + u1[1]) / 2
        cands = [(xm + sq * dy, ym - sq * dx), (xm - sq * dy, ym + sq * dx)]
    best = None
    for cc in cands:
        a0 = math.atan2(u0[1] - cc[1], u0[0] - cc[0])
        a1 = math.atan2(u1[1] - cc[1], u1[0] - cc[0])
        d = (a1 - a0) % (2 * math.pi)   # counter-clockwise sweep in [0, 2 pi)
        sweep = d if ccw else d - 2 * math.pi
        if len(cands) == 1 or (abs(sweep) > math.pi) == bool(large):
            best = (cc, a0, sweep)
            break
    cc, a0, sweep = best
    n = max(2, int(abs(sweep) / (2 * math.pi) * per_turn))
    pts = []
    for i in range(1, n + 1):
        a = a0 + sweep * i / n
        pts.append(from_unit((cc[0] + math.cos(a), cc[1] + math.sin(a)), rh, rv))
    pts[-1] = p1
    return pts


def supersample(fn, ss=16, m=None):
    """Coverage 0..255 of the predicate fn(px, py) (path space). m: target -> path through the inverse of m,
    samples whose preimage has w <= 0 are outside."""
    ys, xs = np.mgrid[0:H * ss, 0:W * ss]
    px, py = (xs + 0.5) / ss, (ys + 0.5) / ss
    if m is None:
        ins = fn(px, py)
    else:
        inv = np.linalg.inv(np.asarray(m, float))
        X = inv[0, 0] * px + inv[0, 1] * py + inv[0, 2]
        Y = inv[1, 0] * px + inv[1, 1] * py + inv[1, 2]
        Z = inv[2, 0] * px + inv[2, 1] * py + inv[2, 2]
        ok = Z > 0
        Zs = np.where(ok, Z, 1.0)
        ins = ok & fn(X / Zs, Y / Zs)
    return ins.reshape(H, ss, W, ss).mean(axis=(1, 3)) * 255


def winding_inside(polys, even_odd=False):
    """Predicate: winding rule over closed float polygons."""
    def fn(px, py):
        wn = np.zeros(px.shape, int)
        for pts in polys:
            n = len(pts)
            for i in range(n):
                (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
                if y0 == y1:
                    continue
                up = y1 > y0
                lo, hi = (y0, y1) if up else (y1, y0)
                sel = (py >= lo) & (py < hi)
                xc = x0 + (py - y0) * (x1 - x0) / (y1 - y0)
                wn += np.where(sel & (xc <= px), 1 if up else -1, 0)
        return (wn % 2 != 0) if even_odd else (wn != 0)
    return fn


def distance_inside(polylines, hw):
    """Predicate: distance to any open polyline <= hw (round caps and joins)."""
    def fn(px, py):
        best = np.full(px.shape, np.inf)
        for p in polylines:
            if len(p) == 1:
                best = np.minimum(best, np.hypot(px - p[0][0], py - p[0][1]))
            for (ax, ay), (bx, by) in zip(p, p[1:]):
                dx, dy = bx - ax, by - ay
                L2 = dx * dx + dy * dy
                if L2 == 0:
                    continue
                t = np.clip(((px - ax) * dx + (py - ay) * dy) / L2, 0, 1)
                best = np.minimum(best, np.hypot(px - ax - t * dx, py - ay - t * dy))
        return best <= hw
    return fn


def rand_arc(rng):
    p0 = (f32(rng.uniform(14, 50)), f32(rng.uniform(14, 50)))
    ang, L = rng.uniform(0, 2 * math.pi), rng.uniform(4, 30)
    p1 = (f32(min(max(p0[0] + L * math.cos(ang), 6), 58)), f32(min(max(p0[1] + L * math.sin(ang), 6), 58)))
    rh, rv = f32(rng.uniform(3, 22)), f32(rng.uniform(3, 22))
    rot = f32(rng.choice([0.0, 90.0, rng.uniform(-400, 400)]))
    return p0, p1, rh, rv, rot


# ---- arcs ------------------------------------------------------------------------------------------------------

SEEDS = [SEED * 100 + k for k in range(3 if FULL else 1)]
SEED_IDS = [str(k) for k in range(len(SEEDS))]


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
@pytest.mark.parametrize("op", ARC_OPS)
def test_arc_fill_vs_appendix_a(op, seed):
    """Region closed by the arc and its chord; random radii (also too small: scaled up), rotations, both
    end-point orders; the reference is clipped to the image like the target."""
    rng = random.Random(seed * 31 + ARC_OPS.index(op))
    large, ccw = op.startswith("L"), "CCW" in op
    for _ in range(8 if FULL else 3):
        p0, p1, rh, rv, rot = rand_arc(rng)
        data = pbytes([("MOVE", *p0), (op, rh, rv, rot, *p1), ("END",)])
        poly = [p0] + arc_points(p0, p1, rh, rv, rot, large, ccw)
        check(fill_cov(data), supersample(winding_inside([poly])), f"{op} {p0}->{p1} r=({rh},{rv}) rot={rot}")


def test_arc_relative_and_full_ellipse():
    """REL arcs add the current point to (x, y) only; two half arcs make a full ellipse whose area matches
    pi * rh * rv; the clockwise ellipse (other sample points) meets the same criteria. Values exactly representable,
    so the relative and absolute forms are bit-identical."""
    cx, cy, rh, rv = 31.25, 29.5, 20.0, 12.0
    a = pbytes([("MOVE", cx - rh, cy), ("SCCWARC", rh, rv, 0.0, cx + rh, cy), ("SCCWARC", rh, rv, 0.0, cx - rh, cy),
                ("END",)])
    r = pbytes([("MOVE", cx - rh, cy), ("SCCWARC_REL", rh, rv, 0.0, 2 * rh, 0.0), ("SCCWARC_REL", rh, rv, 0.0, -2 * rh, 0.0),
                ("END",)])
    cw = pbytes([("MOVE", cx - rh, cy), ("SCWARC", rh, rv, 0.0, cx + rh, cy), ("SCWARC", rh, rv, 0.0, cx - rh, cy),
                 ("END",)])
    ga, gr, gc = fill_cov(a), fill_cov(r), fill_cov(cw)
    assert np.array_equal(ga, gr)
    ref = supersample(lambda x, y: ((x - cx) / rh) ** 2 + ((y - cy) / rv) ** 2 <= 1)
    for g, what in ((ga, "ccw"), (gc, "cw")):
        area = g.sum() / 255
        assert abs(area - math.pi * rh * rv) < 0.005 * math.pi * rh * rv, (what, area)
        check(g, ref, f"full ellipse {what}")


def test_arc_degenerate():
    """Coincident end points or a zero radius: a line (OpenVG 1.1 §8.4; with one zero radius OpenVG projects the
    arc onto the chord, drawn here as the chord itself, a listed simplification); radii too small: scaled up
    uniformly (a half ellipse); negative radii use their absolute values."""
    tri = pbytes([("MOVE", 10.0, 10.0), ("LINE", 50.0, 14.0), ("LINE", 30.0, 50.0), ("END",)])
    for rh, rv in ((0.0, 0.0), (0.0, 7.0), (9.0, 0.0)):
        arc = pbytes([("MOVE", 10.0, 10.0), ("SCCWARC", rh, rv, 30.0, 50.0, 14.0), ("LINE", 30.0, 50.0), ("END",)])
        assert np.array_equal(fill_cov(arc), fill_cov(tri)), (rh, rv)
    same = pbytes([("MOVE", 10.0, 10.0), ("SCCWARC", 5.0, 5.0, 0.0, 10.0, 10.0), ("LINE", 50.0, 14.0),
                   ("LINE", 30.0, 50.0), ("END",)])
    assert np.array_equal(fill_cov(same), fill_cov(tri))
    # chord 40 with radius 1: scaled to 20, a half disc either way
    for op, below in (("SCCWARC", False), ("SCWARC", True), ("LCCWARC", False), ("LCWARC", True)):
        d = pbytes([("MOVE", 12.0, 32.0), (op, -1.0, 1.0, 0.0, 52.0, 32.0), ("END",)])
        got = fill_cov(d)
        # counter-clockwise (increasing angle) from 180 to 360 degrees about (32,32) passes through (32, 12)
        ref = supersample(lambda x, y: ((x - 32) ** 2 + (y - 32) ** 2 <= 400) & ((y >= 32) if below else (y <= 32)))
        check(got, ref, f"scaled-up {op}")


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_arc_stroke_vs_distance(seed):
    """Round caps / joins stroke of a path with arcs and lines against the distance to the dense polyline."""
    rng = random.Random(seed * 13 + 5)
    for _ in range(4 if FULL else 2):
        p0, p1, rh, rv, rot = rand_arc(rng)
        op = rng.choice(ARC_OPS)
        p2 = (f32(rng.uniform(8, 56)), f32(rng.uniform(8, 56)))
        w = rng.choice([1.0, 3.0, rng.uniform(1, 8)])
        data = pbytes([("MOVE", *p0), (op, rh, rv, rot, *p1), ("LINE", *p2), ("END",)])
        line = [p0] + arc_points(p0, p1, rh, rv, rot, op.startswith("L"), "CCW" in op) + [p2]
        check(stroke_cov(data, w), supersample(distance_inside([line], w / 2)), f"stroke {op} w={w:.2f}")


def test_arc_affine_matrix():
    """Arcs under an affine matrix: flattened from the transformed radius bound, compared through the inverse."""
    rng = random.Random(SEED + 404)
    for _ in range(4 if FULL else 2):
        p0, p1, rh, rv, rot = rand_arc(rng)
        op = rng.choice(ARC_OPS)
        a, t = rng.uniform(0, 360), rng.uniform(0.5, 1.6)
        c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
        m = [[t * c, -s * 0.8, 32 - 32 * t * c + 32 * s * 0.8], [t * s, 0.8 * c, 32 - 32 * t * s - 25.6 * c], [0, 0, 1]]
        data = pbytes([("MOVE", *p0), (op, rh, rv, rot, *p1), ("END",)])
        poly = [p0] + arc_points(p0, p1, rh, rv, rot, op.startswith("L"), "CCW" in op)
        check(fill_cov(data, m), supersample(winding_inside([poly]), m=m), f"affine {op}")


# ---- BREAK (assumption A3, UNVERIFIED) ---------------------------------------------------------------------------

def test_break_is_open_subpath_end():
    """BREAK ends the subpath without closing it; the next op starts a new subpath at the current point. Fills
    close every subpath anyway; strokes leave the break open (caps, no join)."""
    a, b, c, d, e = (10.0, 10.0), (50.0, 12.0), (40.0, 40.0), (14.0, 52.0), (54.0, 56.0)
    brk = pbytes([("MOVE", *a), ("LINE", *b), ("LINE", *c), ("BREAK",), ("LINE", *d), ("LINE", *e), ("END",)])
    mov = pbytes([("MOVE", *a), ("LINE", *b), ("LINE", *c), ("MOVE", *c), ("LINE", *d), ("LINE", *e), ("END",)])
    assert gm.path_edges(brk, 3, I3) == gm.path_edges(mov, 3, I3)
    for cap in (0, 1, 2):
        assert gm.stroke_edges(brk, 3, I3, 3.0, 4.0, cap, 0) == gm.stroke_edges(mov, 3, I3, 3.0, 4.0, cap, 0)
    # without the break the vertex c gets a join instead of two caps
    cont = pbytes([("MOVE", *a), ("LINE", *b), ("LINE", *c), ("LINE", *d), ("LINE", *e), ("END",)])
    assert gm.stroke_edges(brk, 3, I3, 3.0, 4.0, 0, 0) != gm.stroke_edges(cont, 3, I3, 3.0, 4.0, 0, 0)


# ---- perspective path matrix (§9.8) -------------------------------------------------------------------------------

def rand_persp(rng, behind=False):
    """A projective matrix mapping the 64x64 path box roughly onto the image; behind=True puts the w = 0 line
    through the shape."""
    while True:
        g, h = rng.uniform(-0.012, 0.012), rng.uniform(-0.012, 0.012)
        if behind:
            # w = g x + h y + i vanishes on a line through the box centre region
            g, h = rng.choice([-1, 1]) * rng.uniform(0.02, 0.05), rng.uniform(-0.02, 0.02)
            i = -(g * rng.uniform(24, 40) + h * rng.uniform(24, 40))
        else:
            i = 1.0
        a, b, c = rng.uniform(0.7, 1.2), rng.uniform(-0.3, 0.3), rng.uniform(-4, 4)
        d, e, f = rng.uniform(-0.3, 0.3), rng.uniform(0.7, 1.2), rng.uniform(-4, 4)
        m = [[a, b, c], [d, e, f], [g, h, i]]
        if behind:
            # scale the numerator so the visible half lands on the image: x' = (..)/w with w ~ 0.3..1.5
            m = [[a * 0.6, b * 0.6, 20 * c], [d * 0.6, e * 0.6, 20 * f], [g, h, i]]
        if abs(np.linalg.det(np.asarray(m))) > 1e-6:
            return m


def shapes(rng):
    cx, cy, r = rng.uniform(22, 42), rng.uniform(22, 42), rng.uniform(8, 18)
    star = []
    for k in range(5):
        a = math.radians(-90 + 144 * k)
        star.append((f32(cx + r * math.cos(a)), f32(cy + r * math.sin(a))))
    yield "star", pbytes([("MOVE", *star[0])] + [("LINE", *p) for p in star[1:]] + [("END",)]), [star]
    q0, q1, q2 = (8.0, 50.0), (f32(rng.uniform(10, 54)), f32(rng.uniform(-10, 20))), (56.0, 46.0)
    from floatref import bezier_points
    yield "quad", pbytes([("MOVE", *q0), ("QUAD", *q1, *q2), ("END",)]), [[q0] + bezier_points(q0, q1, q2, n=128)]
    c1, c2 = (f32(rng.uniform(0, 64)), f32(rng.uniform(0, 64))), (f32(rng.uniform(0, 64)), f32(rng.uniform(0, 64)))
    yield "cubic", pbytes([("MOVE", 8.0, 8.0), ("CUBIC", *c1, *c2, 56.0, 56.0), ("LINE", 8.0, 56.0), ("END",)]), \
        [[(8.0, 8.0)] + bezier_points((8.0, 8.0), c1, c2, (56.0, 56.0), n=128) + [(8.0, 56.0)]]
    p0, p1, rh, rv, rot = rand_arc(rng)
    yield "arc", pbytes([("MOVE", *p0), ("LCCWARC", rh, rv, rot, *p1), ("END",)]), \
        [[p0] + arc_points(p0, p1, rh, rv, rot, True, True)]


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
@pytest.mark.parametrize("behind", [False, True], ids=["front", "behind"])
def test_persp_fill_vs_inverse(behind, seed):
    """Fills under random projective matrices, also with the w = 0 line crossing the shape (the part behind the
    eye is clipped away, §9.8); reference through the inverse matrix."""
    rng = random.Random(seed * 17 + behind)
    for _ in range(3 if FULL else 1):
        m = rand_persp(rng, behind)
        for name, data, polys in shapes(rng):
            for eo in (False, True):
                got = fill_cov(data, m, eo)
                ref = supersample(winding_inside(polys, eo), m=m)
                check(got, ref, f"persp {name} eo={eo} behind={behind} m={m}")


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_persp_stroke_vs_inverse(seed):
    """Round caps / joins strokes under perspective: pieces built in path space and projected; the reference is
    the path-space distance of each preimage."""
    rng = random.Random(seed * 19 + 3)
    for _ in range(3 if FULL else 1):
        m = rand_persp(rng)
        pts = [(f32(rng.uniform(6, 58)), f32(rng.uniform(6, 58))) for _ in range(rng.randint(2, 5))]
        w = rng.uniform(1.5, 7)
        data = pbytes([("MOVE", *pts[0])] + [("LINE", *p) for p in pts[1:]] + [("END",)])
        check(stroke_cov(data, w, m), supersample(distance_inside([pts], w / 2), m=m), f"persp stroke w={w:.2f}")
        p0, p1, rh, rv, rot = rand_arc(rng)
        data = pbytes([("MOVE", *p0), ("SCWARC", rh, rv, rot, *p1), ("END",)])
        line = [p0] + arc_points(p0, p1, rh, rv, rot, False, False)
        check(stroke_cov(data, w, m), supersample(distance_inside([line], w / 2), m=m), f"persp arc stroke w={w:.2f}")


def test_persp_degenerate():
    """w = 2 everywhere equals the affine scale 1/2 exactly; a matrix whose last row is zero draws nothing; a
    shape entirely behind the eye draws nothing."""
    data = pbytes([("MOVE", 4.0, 4.0), ("QUAD", 60.0, 4.0, 60.0, 60.0), ("LINE", 4.0, 60.0), ("END",)])
    rc0, _, _ = gm.path_edges(data, 3, [[1, 0, 0], [0, 1, 0], [0, 0, 0]])
    assert rc0 == gm.GEOM_EMPTY
    rc1, e1, _ = gm.path_edges(data, 3, [[1, 0, 0], [0, 1, 0], [0, 0, -1]])
    assert rc1 == gm.GEOM_EMPTY and not e1
    tri = pbytes([("MOVE", 4.0, 4.0), ("LINE", 60.0, 10.0), ("LINE", 20.0, 60.0), ("END",)])
    assert gm.path_edges(tri, 3, [[1, 0, 0], [0, 1, 0], [0, 0, 2]]) == gm.path_edges(tri, 3, [[0.5, 0, 0], [0, 0.5, 0], [0, 0, 1]])


def test_persp_blit_vs_path():
    """A perspective rectangle path and a perspective blit of an opaque image of the same size agree on every pixel
    the path covers fully (drawn by the blit) or not at all (not drawn)."""
    rng = random.Random(SEED + 9)
    for _ in range(4 if FULL else 2):
        m = rand_persp(rng)
        sw, sh = rng.randint(20, 50), rng.randint(20, 50)
        rect = pbytes([("MOVE", 0.0, 0.0), ("LINE", float(sw), 0.0), ("LINE", float(sw), float(sh)),
                       ("LINE", 0.0, float(sh)), ("END",)])
        cov = fill_cov(rect, m)
        src = np.full((sh, sw), 0xFFFFFFFF, np.uint32)
        blit, _ = scenes.render_blit(src, m, W, H, bilinear=False)
        drawn = (np.asarray(blit).reshape(H, W) >> 24) > 0
        assert not (drawn & (cov == 0)).any(), "blit outside the path"
        assert drawn[cov == 255].all(), "path interior not blitted"
