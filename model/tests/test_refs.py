# SPDX-License-Identifier: Apache-2.0
"""Committed reference vectors and images (model/tests/refs/).

They freeze the golden model's behaviour: any change to a result must be deliberate (regenerate with
G2D_UPDATE_REFS=1 and review the diff). The vectors are also the ready-made stimulus/expected pairs for
the RTL block tests of Phases 2-4.
"""

import json
import math
import os
import random
from pathlib import Path

import numpy as np
import pytest

import g2d_model as gm
import scenes as sc
from g2d_image import load_png_argb, save_png
from image_compare import compare

REFS = Path(__file__).parent / "refs"
UPDATE = os.environ.get("G2D_UPDATE_REFS") == "1"
FMTS = [0x00, 0x01, 0x02, 0x12, 0x03, 0x13, 0x23, 0x33, 0x43, 0x53, 0x63, 0x73]


def build_vectors():
    rng = random.Random(2026)
    v = {"blend": [], "expand": [], "compress": [], "coverage_blend": [], "xform": [], "bilinear": [],
         "coverage_row": [], "grad_index": []}
    for mode in range(16):
        for _ in range(48):
            s, d = rng.getrandbits(32), rng.getrandbits(32)
            v["blend"].append([mode, s, d, gm.blend(mode, s, d)])
    for fmt in FMTS:
        nb = gm.fmt_bytes(fmt)
        for _ in range(24):
            raw = rng.getrandbits(8 * nb)
            v["expand"].append([fmt, raw, gm.expand(fmt, raw)])
            c = rng.getrandbits(32)
            v["compress"].append([fmt, c, gm.compress(fmt, c)])
    for _ in range(64):
        d, b, c = rng.getrandbits(32), rng.getrandbits(32), rng.choice([1, 16, 128, 254, rng.randrange(256)])
        v["coverage_blend"].append([d, b, c, gm.coverage_blend(d, b, c)])
    for k in range(64):
        persp = k % 2 == 1
        xf = [rng.randint(-(4 << 16), 4 << 16), rng.randint(-(4 << 16), 4 << 16), rng.randint(-(2**28), 2**28),
              rng.randint(-(4 << 16), 4 << 16), rng.randint(-(4 << 16), 4 << 16), rng.randint(-(2**28), 2**28),
              rng.randint(-(2**18), 2**18) if persp else 0, rng.randint(-(2**18), 2**18) if persp else 0,
              rng.randint(2**28, 2**30)]
        x, y = rng.randrange(1280), rng.randrange(720)
        r = gm.xform_map(xf, persp, x, y)
        v["xform"].append([xf, persp, x, y, list(r) if r is not None else None])
    for _ in range(64):
        t = [rng.getrandbits(32) for _ in range(4)]
        fx, fy = rng.randrange(256), rng.randrange(256)
        v["bilinear"].append([t, fx, fy, gm.bilinear(t, fx, fy)])
    for k in range(24):
        n = rng.choice([3, 4, 6])
        pts = [(rng.randint(-16, 112), rng.randint(-16, 112)) for _ in range(n)]
        edges = [[*pts[i], *pts[(i + 1) % n]] for i in range(n)]
        aa, eo = k % 2 == 0, k % 4 >= 2
        rows = [list(gm.coverage_row(edges, py, -1, 8, aa, eo)) for py in range(-1, 8)]
        v["coverage_row"].append([edges, aa, eo, rows])
    for _ in range(64):
        p, q, r = rng.randint(-(2**24), 2**24), rng.randint(-(2**24), 2**24), rng.randint(-(2**40), 2**40)
        x, y, s = rng.randrange(1280), rng.randrange(720), rng.randrange(3)
        v["grad_index"].append([p, q, r, x, y, s, gm.grad_index(p, q, r, x, y, s)])
    # v1.4 (own generator, so the groups above stay unchanged): new formats, dither, wrap, FILL
    rng = random.Random(2027)
    v.update({"expand_v14": [], "compress_v14": [], "dither": [], "wrap": [], "grad_fill": []})
    clut = [rng.getrandbits(32) for _ in range(256)]
    v["clut"] = clut
    for fmt in (0x04, 0x14, 0x24, 0x34, 0x05, 0x15, 0x25, 0x35, 0x06, 0x16, 0x26, 0x36,
                0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E):
        bits = gm.fmt_bits(fmt)
        for _ in range(16):
            raw = rng.getrandbits(bits)
            v["expand_v14"].append([fmt, raw, gm.expand(fmt, raw, clut)])
            if gm.fmt_target_ok(fmt):
                c = rng.getrandbits(32)
                v["compress_v14"].append([fmt, c, gm.compress(fmt, c)])
    for fmt in (0x02, 0x12, 0x04, 0x15, 0x25, 0x16):
        for _ in range(16):
            c, x, y = rng.getrandbits(32), rng.randrange(16), rng.randrange(16)
            v["dither"].append([fmt, c, x, y, gm.compress_dither(fmt, c, x, y)])
    for mode in range(5):
        for _ in range(24):
            s0 = rng.randrange(8192)
            s1 = rng.randint(s0 + 1, min(8192, s0 + rng.choice([1, 3, 100, 8192])))
            x = rng.randint(-(2**15), 2**15)
            v["wrap"].append([mode, x, s0, s1, gm.wrap_coord(mode, x, s0, s1)])
    for _ in range(32):
        p, q, r = rng.randint(-(2**24), 2**24), rng.randint(-(2**24), 2**24), rng.randint(-(2**40), 2**40)
        x, y = rng.randrange(1280), rng.randrange(720)
        v["grad_fill"].append([p, q, r, x, y, gm.grad_index(p, q, r, x, y, 3)])
    return v


def build_images():
    I3 = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    out = {}
    p = sc.PathData()
    sc.circle(p, 20.5, 22.25, 15)
    sc.star(p, 44, 40, 18)
    cov, _ = sc.render_coverage(p.end(), I3, 64, 64)
    out["path_nonzero"] = (np.uint32(0xFF000000) | (cov.astype(np.uint32) * 0x010101)).astype(np.uint32)
    cov, _ = sc.render_coverage(p, I3, 64, 64, even_odd=True)
    out["path_evenodd"] = (np.uint32(0xFF000000) | (cov.astype(np.uint32) * 0x010101)).astype(np.uint32)
    src = sc.test_image(24, 20, seed=1)
    a, s = 0.6, 1.9
    c, sn = math.cos(a) * s, math.sin(a) * s
    m = [[c, -sn, 32 - (c * 12 - sn * 10)], [sn, c, 32 - (sn * 12 + c * 10)], [0, 0, 1]]
    out["blit_rot_bilinear"], _ = sc.render_blit(src, m, 64, 64, bilinear=True)
    out["blit_rot_point"], _ = sc.render_blit(src, m, 64, 64, bilinear=False)
    mp = [[1.4, 0.2, 10], [0.1, 1.6, 6], [0.006, 0.004, 1.0]]
    out["blit_perspective"], _ = sc.render_blit(src, mp, 64, 64, bilinear=True)
    gmx = [[60 / 256 * math.cos(0.5), -math.sin(0.5), 4], [60 / 256 * math.sin(0.5), math.cos(0.5), 2], [0, 0, 1]]
    out["gradient_reflect"], _, _ = sc.render_gradient(
        gmx, 64, 64, [[0, 1, 0, 0, 1], [0.5, 0, 1, 0, 1], [1, 0, 0, 1, 1]], spread=2, ext=(0, 0, 256, 0))
    return out


def _vec_path():
    return REFS / "vectors.json"


@pytest.fixture(scope="module")
def vectors():
    v = build_vectors()
    if UPDATE:
        REFS.mkdir(exist_ok=True)
        _vec_path().write_text(json.dumps(v, separators=(",", ":")) + "\n")
    return v


def test_vectors_match_committed(vectors):
    committed = json.loads(_vec_path().read_text())
    for key in vectors:
        assert committed[key] == json.loads(json.dumps(vectors[key])), f"model changed: {key} (G2D_UPDATE_REFS=1)"


@pytest.mark.parametrize("name", ["path_nonzero", "path_evenodd", "blit_rot_bilinear", "blit_rot_point",
                                  "blit_perspective", "gradient_reflect"])
def test_images_match_committed(name):
    img = build_images()[name]
    path = REFS / f"{name}.png"
    if UPDATE:
        REFS.mkdir(exist_ok=True)
        save_png(path, img)
    rep = compare(load_png_argb(path), img)
    assert rep.exact, f"{name}: {rep.summary()} (G2D_UPDATE_REFS=1 to accept)"


def test_image_compare_reports():
    a = np.zeros((4, 4, 4), np.uint8)
    b = a.copy()
    b[1, 2, 0] = 3
    rep = compare(a, b, tol=2)
    assert not rep.exact and not rep.ok and rep.max_abs == 3 and rep.n_diff == 1 and rep.first_diffs[0][:2] == (1, 2)
    assert compare(a, b, tol=3).ok
    assert compare(a, a).exact and compare(a, a).psnr == math.inf
    assert not compare(a, np.zeros((4, 5, 4), np.uint8)).shape_ok
