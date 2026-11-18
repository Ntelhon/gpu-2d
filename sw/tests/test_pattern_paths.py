# SPDX-License-Identifier: Apache-2.0
"""v1.5 F-1: vg_lite_draw_pattern on arbitrary paths (rendering.md §9.5), through the driver and the software backend,
against independent float references (nothing of the driver's geometry or the model's sampling is shared):

  * coverage: the path is drawn with a 1 x 1 opaque white pattern image (PAD) over opaque black with blend NONE, so
    every colour channel equals the pixel coverage; reference = 16 x 16 supersampled float region (curves sampled
    densely, OpenVG Appendix A arcs, projective matrices through the inverse, test_paths_ext.py); criteria of
    rendering.md §12 (pixel |d| <= 56, inside / outside <= 16, mean over edge pixels <= 12);
  * image: the same path with a smooth random image under a rotating / scaling / perspective pattern matrix (PAD);
    on pixels the path covers fully (both by the reference and by the drawn coverage) the colour equals the float
    sample of the image at P^-1 (pixel centre): bilinear within 2.5 per channel (rendering.md §12), point-sampled
    exactly except within 1e-3 texel of a texel boundary;
  * every byte of both draws replays bit-exactly on the independent Python reference (test_scenes.Replay).
Shapes: star (polygon), quadratic and cubic Bézier regions, elliptical arc, circle and rounded rectangle (cubics /
quads); path matrices: identity, affine (rotation + scale), projective.
"""

from __future__ import annotations

import math
import random
import struct

import numpy as np
import pytest

import test_paths_ext as tpe
from test_scenes import E, Replay, Scene, check_rcs, check_replay, mat_str, run, SEED

import floatref  # noqa: E402  (dv/common, on the path through test_scenes)

W, H = tpe.W, tpe.H
NARGS = {0: 0, 1: 0, 2: 2, 4: 2, 6: 4, 8: 6, 0x13: 5, 0x15: 5, 0x17: 5, 0x19: 5}
IW = IH = 12
SEEDS = [SEED * 100 + k for k in range(2)]


def tokens(data: bytes):
    """FP32 path bytes (opcode words + float arguments) as vgl_scene path tokens."""
    out, pos = [], 0
    while pos < len(data):
        (op,) = struct.unpack_from("<I", data, pos)
        pos += 4
        out.append(f"@{op}")
        for _ in range(NARGS[op]):
            out.append(repr(struct.unpack_from("<f", data, pos)[0]))
            pos += 4
    return out


def extra_shapes(rng):
    """Circle (four cubics) and rounded rectangle (quads), with their float polygons."""
    cx, cy, r = rng.uniform(20, 44), rng.uniform(20, 44), rng.uniform(6, 18)
    k = 0.5522847498307936 * r
    P = lambda x, y: (tpe.f32(x), tpe.f32(y))  # noqa: E731
    segs = [((cx + r, cy + k), (cx + k, cy + r), (cx, cy + r)), ((cx - k, cy + r), (cx - r, cy + k), (cx - r, cy)),
            ((cx - r, cy - k), (cx - k, cy - r), (cx, cy - r)), ((cx + k, cy - r), (cx + r, cy - k), (cx + r, cy))]
    cmds, poly, cur = [("MOVE", *P(cx + r, cy))], [P(cx + r, cy)], P(cx + r, cy)
    for c1, c2, e in segs:
        c1, c2, e = P(*c1), P(*c2), P(*e)
        cmds.append(("CUBIC", *c1, *c2, *e))
        poly += floatref.bezier_points(cur, c1, c2, e, n=128)
        cur = e
    yield "circle", tpe.pbytes(cmds + [("END",)]), [poly]
    x0, y0 = tpe.f32(rng.uniform(4, 20)), tpe.f32(rng.uniform(4, 20))
    x1, y1 = tpe.f32(x0 + rng.uniform(16, 40)), tpe.f32(y0 + rng.uniform(16, 40))
    rr = tpe.f32(rng.uniform(1, 8))
    pts = [(x0 + rr, y0), (x1 - rr, y0), (x1, y0), (x1, y0 + rr), (x1, y1 - rr), (x1, y1), (x1 - rr, y1),
           (x0 + rr, y1), (x0, y1), (x0, y1 - rr), (x0, y0 + rr), (x0, y0), (x0 + rr, y0)]
    pts = [P(*p) for p in pts]
    cmds = [("MOVE", *pts[0]), ("LINE", *pts[1]), ("QUAD", *pts[2], *pts[3]), ("LINE", *pts[4]), ("QUAD", *pts[5], *pts[6]),
            ("LINE", *pts[7]), ("QUAD", *pts[8], *pts[9]), ("LINE", *pts[10]), ("QUAD", *pts[11], *pts[12]), ("END",)]
    poly = [pts[0], pts[1]] + floatref.bezier_points(pts[1], pts[2], pts[3], n=64) + [pts[4]] + \
        floatref.bezier_points(pts[4], pts[5], pts[6], n=64) + [pts[7]] + \
        floatref.bezier_points(pts[7], pts[8], pts[9], n=64) + [pts[10]] + \
        floatref.bezier_points(pts[10], pts[11], pts[12], n=64)
    yield "rrect", tpe.pbytes(cmds), [poly]


def path_matrix(rng, kind):
    if kind == "identity":
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    if kind == "affine":
        a, s = rng.uniform(-0.6, 0.6), rng.uniform(0.8, 1.1)
        c, sn = s * math.cos(a), s * math.sin(a)
        m = [[c, -sn, 0.0], [sn, c, 0.0], [0.0, 0.0, 1.0]]
        m[0][2] = 32 - (c * 32 - sn * 32)
        m[1][2] = 32 - (sn * 32 + c * 32)
        return [[tpe.f32(v) for v in r] for r in m]
    return [[tpe.f32(v) for v in r] for r in tpe.rand_persp(rng)]


def pattern_matrix(rng, persp):
    """Image texel space -> target: the 12 x 12 image scaled about 5x, rotated, roughly over the target."""
    a, s = rng.uniform(0, 2 * math.pi), rng.uniform(4.0, 6.5)
    c, sn = s * math.cos(a), s * math.sin(a)
    m = [[c, -sn, 0.0], [sn, c, 0.0], [0.0, 0.0, 1.0]]
    m[0][2] = 32 - (c * 6 - sn * 6)
    m[1][2] = 32 - (sn * 6 + c * 6)
    if persp:
        m[2] = [rng.uniform(-0.01, 0.01), rng.uniform(-0.01, 0.01), 1.0]
    return [[tpe.f32(v) for v in r] for r in m]


def smooth_image(rng):
    """ARGB32 (IH, IW) opaque image with smooth gradients and a few sharp texels."""
    ys, xs = np.mgrid[0:IH, 0:IW]
    ch = []
    for _ in range(3):
        fx, fy, ph = rng.uniform(0.2, 0.9), rng.uniform(0.2, 0.9), rng.uniform(0, 6)
        ch.append(np.clip(127.5 + 127.5 * np.sin(fx * xs + fy * ys + ph), 0, 255).astype(np.uint32))
    img = (0xFF << 24) | (ch[0] << 16) | (ch[1] << 8) | ch[2]
    for _ in range(4):
        img[rng.randrange(IH), rng.randrange(IW)] = 0xFF000000 | rng.getrandbits(24)
    return img.astype(np.uint32)


def bgra_bytes(argb, stride):
    out = bytearray(stride * argb.shape[0])
    for y in range(argb.shape[0]):
        for x in range(argb.shape[1]):
            v = int(argb[y, x])
            out[y * stride + 4 * x:y * stride + 4 * x + 4] = bytes([v & 255, (v >> 8) & 255, (v >> 16) & 255, v >> 24])
    return bytes(out)


def img_of(dumps, addrs, d):
    return np.frombuffer(dumps[d], np.uint8).reshape(H, addrs[d][1])[:, :4 * W].reshape(H, W, 4).astype(np.int64)


@pytest.mark.parametrize("seed", SEEDS, ids=[str(k) for k in range(len(SEEDS))])
@pytest.mark.parametrize("mkind", ["identity", "affine", "persp"])
def test_pattern_paths_vs_float(tmp_path, mkind, seed):
    rng = random.Random(seed * 7 + len(mkind))
    shapes = list(tpe.shapes(rng)) + list(extra_shapes(rng))
    sc = Scene()
    sc.buf(0, 1, 1, "BGRA8888", 64)
    (tmp_path / "white.bin").write_bytes(b"\xFF" * 64)
    sc.add(f"load 0 {tmp_path / 'white.bin'}")
    sc.buf(1, IW, IH, "BGRA8888", 64)
    src = smooth_image(rng)
    (tmp_path / "img.bin").write_bytes(bgra_bytes(src, 64))
    sc.add(f"load 1 {tmp_path / 'img.bin'}")
    for b in (0, 1):
        sc.add(f"img {b} {E['VG_LITE_NORMAL_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
    cases = []
    d = 2
    for name, data, polys in shapes:
        eo = rng.random() < 0.3
        m = path_matrix(rng, mkind)
        bil = rng.random() < 0.6
        pm = pattern_matrix(rng, rng.random() < 0.4)
        toks = tokens(data)
        fill = E["VG_LITE_FILL_EVEN_ODD"] if eo else E["VG_LITE_FILL_NON_ZERO"]
        for src_id in (0, 1):
            sc.buf(d, W, H, "BGRA8888")
            sc.add(f"clear {d} 0 0 -1 -1 {0xFF000000}")
            flt = E["VG_LITE_FILTER_BI_LINEAR"] if (bil and src_id) else E["VG_LITE_FILTER_POINT"]
            mat = pm if src_id else [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
            sc.add(f"pattern {d} {src_id} {E['VG_LITE_FP32']} {E['VG_LITE_HIGH']} {fill} {E['VG_LITE_BLEND_NONE']} "
                   f"{E['VG_LITE_PATTERN_PAD']} 0 0 {flt} {mat_str(m)} {mat_str(mat)} {len(toks)} {' '.join(toks)}")
            d += 1
        cases.append((name, polys, eo, m, bil, pm, d - 2))
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    checked = 0
    for name, polys, eo, m, bil, pm, dc in cases:
        what = f"{name} m={mkind} eo={eo}"
        cov_img = img_of(dumps, addrs, dc)
        assert (cov_img[..., 0] == cov_img[..., 1]).all() and (cov_img[..., 1] == cov_img[..., 2]).all()
        got = cov_img[..., 0].astype(float)
        ref = tpe.supersample(tpe.winding_inside(polys, eo), m=None if mkind == "identity" else m)
        tpe.check(got, ref, what)
        # image on the fully covered pixels
        col = img_of(dumps, addrs, dc + 1)
        inv = np.linalg.inv(np.asarray(pm, float))
        fl, _, u, v = floatref.blit_bilinear(src, (0, 0, IW, IH), inv, W, H)
        full = (got == 255) & (ref == 255)
        argb = np.stack([col[..., 3], col[..., 2], col[..., 1], col[..., 0]], axis=-1)
        if bil:
            dmax = np.abs(argb[full] - fl[full]).max(initial=0)
            assert dmax <= 2.5, f"{what}: bilinear |d| {dmax:.2f}"
        else:
            tu, tv = np.clip(np.floor(u), 0, IW - 1).astype(int), np.clip(np.floor(v), 0, IH - 1).astype(int)
            near = (np.abs(u - np.round(u)) < 1e-3) | (np.abs(v - np.round(v)) < 1e-3)
            ok = full & ~near
            exp = src[tv, tu]
            exp4 = np.stack([(exp >> s) & 255 for s in (24, 16, 8, 0)], axis=-1).astype(np.int64)
            bad = ok & (argb != exp4).any(axis=-1)
            assert not bad.any(), f"{what}: point-sampled pixels differ at {np.argwhere(bad)[:3].tolist()}"
        checked += int(full.sum())
    assert checked > 500
    check_replay(sc, addrs, dumps, Replay(rec))
