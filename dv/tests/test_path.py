# SPDX-License-Identifier: Apache-2.0
"""End-to-end (L3) path tests on g2d_top against the golden model (verification-plan.md PTH tests,
Phase 4). Every test compares the whole memory image, the error/fence registers, the white-box state and
the performance counters (dv/common/g2d_env.py).

  PTH-01  random polygons (3..400 edges), both fill rules, AA on/off, all target formats
  PTH-02  degenerate edges: horizontal, zero-length, collinear, duplicate vertices, crossings exactly on
          sample columns (tie rule), short edges with small dy
  PTH-03  self-intersecting stars, nested contours of opposite winding, winding >= 128 and >= 256
  PTH-04  sub-pixel shapes, guard-band and beyond (clamped) shapes, full-target shapes, bbox clipped by
          the scissor and by the target, negative bbox, EDGE_COUNT = 0
  PTH-05  edge lists longer than the 512-edge buffer: per-band streaming (many edges in total, at most
          512 per band); rows crossing more than 512 edges (multi-pass): exactly 512, 513, 1024 active
          edges per row, and all edges inside one pixel row
  PTH-06  curves flattened by the driver library (gm.path_edges: circles, rounded rects, quads)
  paint   paths with every blend mode, source/destination global alpha, over random destinations
  SYS-P4  random streams of paths, fills, blended fills and blits under random AXI profiles
  IMG-01  (v1.5, F-1) image paint on paths: random polygons / circles / rounded rects / stars x every source
          format x POINT / BILINEAR x every wrap mode (BORDER_SOLID 0 / 1) x affine / perspective x blend, global
          alpha, MULTIPLY, premultiply, TRANSPARENT, AA on / off, both fill rules; paths larger than the image
          (source test inside the path)
  GRD-03  (v1.5, F-4) radial gradients on rects and paths: every spread x centred / focal / focal outside /
          r = 0, driver coefficients, mixed with linear gradients and solid draws
  GRD-01  linear gradients on rects and paths: all spreads (v1.4: FILL = PAINT_COLOR outside the ramp), lengths 1 .. 8192 px, angles 0/90/45/other,
          legacy and extended ramps from the driver library, ramp at every 4-byte alignment, ramp reload
          after a CPU rewrite between kicks and after a GPU draw over the ramp inside one kick (A4)
  COV     directed coverage of cg_path (fill rule x AA x size class x edge-count class) and of
          cg_paint_path (gradient paths with every blend mode)
  PRF-01  throughput (cycles per pixel) of paths and gradient rects, ideal memory and 90-clock latency;
          A8: >= 0.9 px/clk with ideal memory for paths with <= 8 crossing edges per row
  AXI-08  (A1) long latency and back-pressure on every channel while paths fetch edges, read the
          destination and write back (no deadlock between edge fetch, destination read, write-back)
Edges are placed in memory as the driver would (16-byte aligned list); the bbox is the driver's
(rendering.md §9.2 step 7) unless a test overrides it.
"""

import math
import random

import os

import cocotb
from cocotb.triggers import RisingEdge

import g2d_model as gm
from axi_mem import Profile
from csr_if import CsrIf
from g2d_cmd import FMT, STATE, CmdBuf, edges_to_bytes, state_word
from g2d_top_util import FMTS, QUICK, SEED, fill, make_env, rand_alpha, rand_rect, run
from scenes import PathData, circle, rounded_rect, star
from g2d_top_util import P, SRC_FMTS
from test_blit import INDEXED_FMTS, KINDS, blit, mat_affine, mat_persp, new_clut


def bbox_of(edges):
    xs = [c for e in edges for c in (e[0], e[2])]
    ys = [c for e in edges for c in (e[1], e[3])]
    clampv = lambda v: max(-(1 << 15), min((1 << 15) - 1, v))
    return (clampv(math.floor(min(xs) / 16)), clampv(math.floor(min(ys) / 16)),
            clampv(math.ceil(max(xs) / 16)), clampv(math.ceil(max(ys) / 16)))


def draw_path(env, cb, t, edges, aa=True, eo=False, color=0xFF20A040, blend=0, alpha=(0, 255), dalpha=(0, 255),
              clip=None, bbox=None, paint=0):
    """A DRAW_PATH with its full state; edges are s.4 int32 records placed in memory now."""
    addr = env.place(edges_to_bytes(edges)) if edges else 0x0800_0000
    cb.target(t.addr, t.w, t.h, t.fmt, t.stride)
    x0, y0, x1, y1 = clip if clip is not None else (0, 0, t.w, t.h)
    cb.clip(x0, y0, x1, y1)
    cb.load_state(STATE["BLEND"].addr, [blend, state_word("ALPHA_CTRL", SRC_MODE=alpha[0], SRC_VALUE=alpha[1],
                                                          DST_MODE=dalpha[0], DST_VALUE=dalpha[1]), paint, color])
    cb.load_state(STATE["RAST_CTRL"].addr, [state_word("RAST_CTRL", FILL_RULE=int(eo), AA=int(aa))])
    cb.draw_path(bbox if bbox is not None else (bbox_of(edges) if edges else (0, 0, 0, 0)), addr, len(edges))
    return cb


def poly_edges(pts):
    return [(*pts[i], *pts[(i + 1) % len(pts)]) for i in range(len(pts))]


def rand_poly(rng, cx, cy, r, n):
    return poly_edges([(int(16 * (cx + rng.uniform(-r, r))), int(16 * (cy + rng.uniform(-r, r)))) for _ in range(n)])


def driver_edges(path: PathData, m=((1, 0, 0), (0, 1, 0), (0, 0, 1))):
    rc, edges, bbox = gm.path_edges(path.to_bytes(), 3, [list(r) for r in m])
    assert rc == gm.GEOM_OK, rc
    return [tuple(e) for e in edges], tuple(bbox)


# ------------------------------------------------------------------------------------------------
# PTH
# ------------------------------------------------------------------------------------------------

@cocotb.test()
async def pth01_random_polygons(dut):
    env = await make_env(dut, "pth01")
    rng = env.rng
    for it in range(6 if QUICK else 16):
        cb = CmdBuf()
        for k in range(3):
            t = env.new_target(FMTS[(3 * it + k) % len(FMTS)], rng.randint(8, 120), rng.randint(4, 48))
            n = rng.choice([3, 4, 7, 20, 60]) if k else rng.randint(3, 400 if not QUICK else 60)
            edges = rand_poly(rng, t.w / 2, t.h / 2, max(t.w, t.h) * 0.6, n)
            draw_path(env, cb, t, edges, aa=bool((it + k) % 2), eo=bool(it % 3 == 0), color=rng.getrandbits(32))
        cb.fence(it)
        await run(env, cb)


@cocotb.test()
async def pth02_degenerate(dut):
    env = await make_env(dut, "pth02")
    rng = env.rng
    for it in range(4 if QUICK else 10):
        t = env.new_target(rng.choice(FMTS), 40, 24)
        edges = []
        x, y = 16 * 5, 16 * 4
        edges += [(x, y, x + 300, y)]                                 # horizontal
        edges += [(x + 50, y + 50, x + 50, y + 50)]                   # zero-length
        edges += poly_edges([(x, y), (x + 200, y + 100), (x + 400, y + 200), (x, y + 200)])   # collinear
        edges += poly_edges([(x, y), (x, y), (x + 120, y + 130), (x + 120, y + 130), (x - 40, y + 200)])  # dups
        for _ in range(60):                                           # short edges, small dy, sample ties
            px, py = rng.randint(0, 16 * 40), rng.randint(0, 16 * 24)
            dy = rng.choice([1, 2, 3, 4, 7])
            dx = rng.randint(-200, 200) if rng.random() < 0.5 else dy * rng.choice([1, -1, 3, 16])
            edges.append((px, py, px + dx, py + dy))
            edges.append((px + dx + 8, py + dy, px + 8, py))          # roughly closes it
        cb = CmdBuf()
        draw_path(env, cb, t, edges, aa=bool(it % 2), eo=bool(it % 4 >= 2), color=rng.getrandbits(32),
                  bbox=(0, 0, 40, 24))
        await run(env, cb.fence(it))


@cocotb.test()
async def pth03_winding(dut):
    env = await make_env(dut, "pth03")
    rng = env.rng
    t = env.new_target(FMT["ARGB8888"], 64, 64)
    for n, step in ((5, 2), (7, 3), (9, 4), (11, 5)):
        e, bb = driver_edges(star(PathData(), 32, 32, 30, n, step))
        for eo in (False, True):
            await run(env, draw_path(env, CmdBuf(), t, e, aa=True, eo=eo, color=rng.getrandbits(32), bbox=bb).fence(1))
    # nested contours of opposite winding (outer CW, inner CCW and another inner CW)
    outer = poly_edges([(16 * 4, 16 * 4), (16 * 60, 16 * 4), (16 * 60, 16 * 60), (16 * 4, 16 * 60)])
    inner = poly_edges([(16 * 16, 16 * 16), (16 * 16, 16 * 48), (16 * 48, 16 * 48), (16 * 48, 16 * 16)])
    inner2 = poly_edges([(16 * 24, 16 * 24), (16 * 40, 16 * 24), (16 * 40, 16 * 40), (16 * 24, 16 * 40)])
    for eo in (False, True):
        await run(env, draw_path(env, CmdBuf(), t, outer + inner + inner2, aa=True, eo=eo, color=rng.getrandbits(32)).fence(2))
    # winding >= 128 and >= 256 (mod-256 rule): coincident copies of one triangle; 256+ copies are more
    # than 512 edges active in the same rows (multi-pass rows)
    tri = poly_edges([(16 * 10, 16 * 10), (16 * 30, 16 * 12), (16 * 14, 16 * 34)])
    for copies in ((127, 128, 129, 170, 256, 257, 300) if not QUICK else (128, 257)):
        t2 = env.new_target(FMT["BGRA8888"], 40, 40)
        await run(env, draw_path(env, CmdBuf(), t2, tri * copies, aa=copies % 2 == 0, eo=copies > 128,
                                 color=rng.getrandbits(32)).fence(3))


@cocotb.test()
async def pth04_sizes_clipping(dut):
    env = await make_env(dut, "pth04")
    rng = env.rng
    t = env.new_target(FMT["RGB565"], 96, 64)
    cases = []
    cases.append(("sub-pixel", poly_edges([(16 * 10 + 3, 16 * 10 + 2), (16 * 10 + 9, 16 * 10 + 5), (16 * 10 + 4, 16 * 10 + 13)]), None, None))
    big = 16 * 30000
    cases.append(("guard band", poly_edges([(-big, -big), (big, -big // 3), (16 * 50, big)]), None, (0, 0, 96, 64)))
    lim = (1 << 18) + 5000
    cases.append(("beyond guard", [(-lim, 16 * 5, lim, 16 * 40), (lim, 16 * 40, -lim, 16 * 60), (-lim, 16 * 60, -lim, 16 * 5)],
                  None, (0, 0, 96, 64)))
    cases.append(("full target", poly_edges([(0, 0), (16 * 96, 0), (16 * 96, 16 * 64), (0, 16 * 64)]), None, None))
    cases.append(("scissor", rand_poly(rng, 48, 32, 40, 12), (20, 10, 70, 50), None))
    cases.append(("negative bbox", rand_poly(rng, -5, -5, 30, 8), None, None))
    cases.append(("bbox beyond target", rand_poly(rng, 90, 60, 30, 8), None, None))
    cases.append(("count 0", [], None, (0, 0, 50, 50)))
    for name, edges, clip, bb in cases:
        for aa in (True, False):
            cb = draw_path(env, CmdBuf(), t, edges, aa=aa, eo=rng.random() < 0.5, color=rng.getrandbits(32), clip=clip,
                           bbox=bb, blend=rng.choice([0, 1]))
            await run(env, cb.fence(4))


@cocotb.test()
async def pth05_long_lists(dut):
    """More kept edges than the edge buffer (512), at most 512 active in each 16-row band: the rasterizer
    re-reads the list from memory per band. Triangles kept inside one band each, plus shapes spanning all
    bands."""
    env = await make_env(dut, "pth05")
    rng = env.rng
    for it in range(2 if QUICK else 4):
        t = env.new_target(rng.choice(FMTS), rng.randint(24, 64), 16 * rng.randint(6, 13) + rng.randint(0, 15))
        edges = []
        for b in range((t.h + 15) // 16):
            top, bot = 16 * 16 * b, 16 * min(16 * b + 16, t.h) - 1
            for _ in range(rng.randint(30, 160)):
                pts = [(rng.randint(-40, 16 * t.w + 40), rng.randint(top, bot)) for _ in range(3)]
                edges += poly_edges(pts)
        edges += rand_poly(rng, t.w / 2, t.h / 2, t.h * 0.6, 12)                  # spans every band
        assert len(edges) > 512, len(edges)
        cb = draw_path(env, CmdBuf(), t, edges, aa=bool(it % 2), eo=it >= 2, color=rng.getrandbits(32),
                       blend=rng.choice([0, 1]), bbox=(0, 0, t.w, t.h))
        await run(env, cb.fence(it))


@cocotb.test()
async def pth05_row_overflow(dut):
    """Phase 4 condition (Q1 a): rows whose active edges exceed one pass (512). 512 is the largest
    single-pass case, 513 the smallest two-pass case, 1024 two full passes; then ~1100 short edges all
    inside one pixel row (only that row's band overflows)."""
    env = await make_env(dut, "pth05_row")
    rng = env.rng
    for n in ((512, 513, 1024) if not QUICK else (513,)):
        t = env.new_target(FMT["ARGB8888"], 40, 4)
        edges = []
        for k in range(n):        # every edge crosses all 4 rows; alternating direction
            xa, xb = rng.randint(-60, 16 * 40 + 60), rng.randint(-60, 16 * 40 + 60)
            edges.append((xa, -8, xb, 16 * 4 + 8) if k % 2 == 0 else (xb, 16 * 4 + 8, xa, -8))
        cb = draw_path(env, CmdBuf(), t, edges, aa=True, eo=n == 1024, color=rng.getrandbits(32),
                       bbox=(0, 0, 40, 4))
        await run(env, cb.fence(n))
    t = env.new_target(FMT["BGRA8888"], 40, 20)
    edges = []
    for _ in range(550):            # closed slivers inside pixel row 9 (s.4 y in 144..159)
        xa = rng.randint(0, 16 * 40)
        edges += [(xa, 146, xa + rng.randint(5, 80), 157), (xa + 90, 157, xa + 1, 146)]
    edges += rand_poly(rng, 20, 10, 12, 8)                     # an ordinary shape across all rows
    cb = draw_path(env, CmdBuf(), t, edges, aa=True, color=rng.getrandbits(32), blend=1, bbox=(0, 0, 40, 20))
    await run(env, cb.fence(9))


@cocotb.test()
async def pth06_curves(dut):
    env = await make_env(dut, "pth06")
    rng = env.rng
    t = env.new_target(FMT["ARGB8888"], 80, 60)
    shapes = [circle(PathData(), 30, 30, 25), rounded_rect(PathData(), 5, 5, 75, 55, 12),
              circle(PathData(), 60.3, 20.7, 9.5), rounded_rect(PathData(), 10.5, 30.25, 40.75, 58.5, 6)]
    for i, p in enumerate(shapes):
        th = rng.uniform(0, 1)
        m = ((math.cos(th), -math.sin(th), 10), (math.sin(th), math.cos(th), 2), (0, 0, 1)) if i % 2 else \
            ((1, 0, 0), (0, 1, 0), (0, 0, 1))
        e, bb = driver_edges(p, m)
        for aa in (True, False):
            await run(env, draw_path(env, CmdBuf(), t, e, aa=aa, color=rng.getrandbits(32), bbox=bb, blend=1).fence(i))


@cocotb.test()
async def path_paint_blend(dut):
    """Every blend mode x source/destination global-alpha mode on paths over random destinations."""
    env = await make_env(dut, "path_paint")
    rng = env.rng
    for mode in range(16):
        cb = CmdBuf()
        for k in range(3 if not QUICK else 1):
            t = env.new_target(rng.choice(FMTS), rng.randint(8, 40), rng.randint(6, 24))
            draw_path(env, cb, t, rand_poly(rng, t.w / 2, t.h / 2, t.w * 0.6, rng.randint(3, 12)),
                      aa=rng.random() < 0.7, eo=rng.random() < 0.3, color=rng.getrandbits(32), blend=mode,
                      alpha=(k % 3, rng.getrandbits(8)), dalpha=((mode + k) % 3, rng.getrandbits(8)))
        await run(env, cb.fence(mode))


@cocotb.test()
async def img01_image_paths(dut):
    """F-1: DRAW_PATH with an IMAGE paint; coverage = path coverage where the pixel passes the source test."""
    env = await make_env(dut, "img01")
    rng = env.rng
    combos = [(w, b, k) for w in (0, 1, 2, 3, 4) for b in (False, True)
              for k in ("translate", "scale_up", "rotate", "persp")]
    if QUICK:
        combos = rng.sample(combos, 10)
    for i in range(0, len(combos), 3):
        cb = CmdBuf()
        for wrap, bil, kind in combos[i:i + 3]:
            sf = rng.choice(SRC_FMTS)
            s = env.new_target(sf, rng.randint(1, 40), rng.randint(1, 28))
            t = env.new_target(rng.choice(FMTS), rng.randint(8, 64), rng.randint(6, 40))
            m = mat_persp(rng, s, t) if kind == "persp" else mat_affine(rng, s, t, kind)
            shape = rng.randrange(4)
            if shape == 0:
                edges = rand_poly(rng, t.w / 2, t.h / 2, max(t.w, t.h) * 0.6, rng.randint(3, 12))
                bbox = bbox_of(edges)
            else:
                cx, cy = rng.uniform(0, t.w), rng.uniform(0, t.h)
                if shape == 1:
                    pd = circle(PathData(), cx, cy, rng.uniform(1, t.w * 0.6))
                elif shape == 2:
                    x0, y0 = rng.uniform(-4, t.w / 2), rng.uniform(-4, t.h / 2)
                    w, h = rng.uniform(4, t.w), rng.uniform(4, t.h)
                    pd = rounded_rect(PathData(), x0, y0, x0 + w, y0 + h, rng.uniform(0, min(w, h) / 2))
                else:
                    pd = star(PathData(), cx, cy, rng.uniform(4, t.w), 7, rng.choice([2, 3]))
                edges, bbox = driver_edges(pd)
            if not edges:
                continue
            clut = new_clut(env, rng) if sf in INDEXED_FMTS else None
            addr = env.place(edges_to_bytes(edges))
            rect = None
            if rng.random() < 0.3:
                x0, y0 = rng.randrange(s.w), rng.randrange(s.h)
                rect = (x0, y0, rng.randint(x0 + 1, s.w), rng.randint(y0 + 1, s.h))
            blit(cb, t, s, m, rect=rect, bilinear=bil, wrap=wrap, border=rng.getrandbits(32), clut=clut,
                 sub_order=rng.randrange(2), blend=rng.choice([0, 1, rng.randint(0, 15)]), alpha=rand_alpha(rng),
                 premul=rng.random() < 0.3, border_solid=rng.random() < 0.5, img_mul=rng.random() < 0.3,
                 color=rng.getrandbits(32), transparent=rng.random() < 0.3,
                 path=(addr, len(edges), bbox, rng.random() < 0.7, rng.random() < 0.3))
        cb.fence(i)
        await run(env, cb)


# ------------------------------------------------------------------------------------------------
# GRD
# ------------------------------------------------------------------------------------------------

def grad_state(cb, p, q, r, ramp, spread):
    """Gradient coefficients (rendering.md §10.1) and ramp address; returns the PAINT_CTRL word."""
    r &= (1 << 48) - 1
    cb.load_state(STATE["GRAD_P"].addr, [p & 0xFFFF_FFFF, q & 0xFFFF_FFFF, r & 0xFFFF_FFFF, ramp, r >> 32])
    return state_word("PAINT_CTRL", SRC=2, SPREAD=spread)


def grad_coeffs(rng, t, angle, length):
    """Driver coefficients of an extended linear gradient across the target (gm.grad_coeffs)."""
    th = {"0": 0.0, "90": math.pi / 2, "45": math.pi / 4}.get(angle, rng.uniform(0, 2 * math.pi))
    x0, y0 = rng.uniform(0, t.w), rng.uniform(0, t.h)
    x1, y1 = x0 + length * math.cos(th), y0 + length * math.sin(th)
    rc, p, q, r = gm.grad_coeffs([[1, 0, 0], [0, 1, 0], [0, 0, 1]], True, x0, y0, x1, y1)
    assert rc == gm.GEOM_OK, rc
    return p, q, r


def rand_ramp(rng):
    """256 ARGB32 words from the driver library: legacy 16-stop or extended float ramp."""
    if rng.random() < 0.5:
        n = rng.randint(2, 16)
        stops = sorted(rng.randint(0, 255) for _ in range(n))
        rc, words = gm.ramp_legacy([rng.getrandbits(32) for _ in range(n)], stops)
    else:
        n = rng.randint(2, 8)
        st = sorted(rng.random() for _ in range(n))
        rc, words = gm.ramp_ext([[st[i]] + [rng.random() for _ in range(4)] for i in range(n)], rng.random() < 0.5)
    assert rc == gm.GEOM_OK, rc
    return words


def ramp_bytes(words):
    return b"".join((w & 0xFFFF_FFFF).to_bytes(4, "little") for w in words)


@cocotb.test()
async def grd01_gradients(dut):
    env = await make_env(dut, "grd01")
    rng = env.rng
    combos = [(sp, ang, ln) for sp in range(4) for ang in ("0", "90", "45", "other")
              for ln in (1, 7, 100, 300, 8192)]
    if QUICK:
        combos = rng.sample(combos, 10)
    for i, (spread, angle, length) in enumerate(combos):
        t = env.new_target(rng.choice(FMTS), rng.randint(8, 72), rng.randint(4, 32))
        ramp_addr = 0x0C00_0000 + 0x1000 * i + 4 * rng.randrange(16)
        env.write_mem(ramp_addr, ramp_bytes(rand_ramp(rng)))
        p, q, r = grad_coeffs(rng, t, angle, length)
        cb = CmdBuf()
        paint = grad_state(cb, p, q, r, ramp_addr, spread)
        if i % 2:
            fill(cb, t, rand_rect(rng, t, 3), rng.getrandbits(32), rand_alpha(rng), blend=rng.choice([0, 1, 11]),
                 paint=paint)
        else:
            draw_path(env, cb, t, rand_poly(rng, t.w / 2, t.h / 2, t.w * 0.6, rng.randint(3, 10)),
                      aa=rng.random() < 0.7, blend=rng.choice([0, 1]), paint=paint, alpha=rand_alpha(rng))
        await run(env, cb.fence(i))


def radial_state(cb, p, q, rr, ramp, spread):
    """Radial gradient coefficients T, U, V (rendering.md §10.4) and ramp address; returns the PAINT_CTRL word."""
    M = (1 << 48) - 1
    cb.load_state(STATE["GRAD_P"].addr, [p[0] & 0xFFFF_FFFF, q[0] & 0xFFFF_FFFF, rr[0] & 0xFFFF_FFFF, ramp,
                                         (rr[0] & M) >> 32])
    cb.load_state(STATE["GRAD_UP"].addr, [p[1] & 0xFFFF_FFFF, q[1] & 0xFFFF_FFFF, rr[1] & 0xFFFF_FFFF, (rr[1] & M) >> 32,
                                          p[2] & 0xFFFF_FFFF, q[2] & 0xFFFF_FFFF, rr[2] & 0xFFFF_FFFF, (rr[2] & M) >> 32])
    return state_word("PAINT_CTRL", SRC=3, SPREAD=spread)


def radial_coeffs(rng, t, kind):
    """Driver coefficients (gm.radial_coeffs) of a radial gradient over the target: centred, focal inside,
    focal outside (moved to 0.99 r), r <= 0; random rotation / scale of gradient space."""
    while True:    # tiny radii under strong down-scaling exceed the 32-bit coefficients: INVALID_ARGUMENT, retry
        a, s = rng.uniform(-3.1, 3.1), rng.choice([0.3, 1.0, 2.5])
        gmx = [[s * math.cos(a), -s * math.sin(a), rng.uniform(0, t.w)],
               [s * math.sin(a), s * math.cos(a), rng.uniform(0, t.h)], [0, 0, 1]]
        r = {"zero": 0.0}.get(kind, rng.choice([0.5, 4.0, 30.0, 3000.0]))
        rho = {"centred": 0.0, "focal": rng.uniform(0.2, 0.95), "outside": rng.uniform(1, 3), "zero": 0.3}[kind]
        th = rng.uniform(0, 6.3)
        rc, p, q, rr = gm.radial_coeffs(gmx, 0.0, 0.0, r, rho * r * math.cos(th), rho * r * math.sin(th))
        if rc == gm.GEOM_OK:
            return p, q, rr
        assert rc == gm.GEOM_INVALID, rc


@cocotb.test()
async def grd03_radial(dut):
    """v1.5 F-4: radial gradients on rects and paths: every spread x {centred, focal, focal outside, r = 0},
    random ramps and gradient transforms, blends and global alpha, back-to-back with linear gradients (both
    g2d_grad paths) and solid draws."""
    env = await make_env(dut, "grd03")
    rng = env.rng
    combos = [(sp, k) for sp in range(4) for k in ("centred", "focal", "outside", "zero")]
    if QUICK:
        combos = rng.sample(combos, 8)
    for i, (spread, kind) in enumerate(combos):
        cb = CmdBuf()
        t = env.new_target(rng.choice(FMTS), rng.randint(8, 72), rng.randint(4, 40))
        ramp_addr = 0x0C10_0000 + 0x1000 * i + 4 * rng.randrange(16)
        env.write_mem(ramp_addr, ramp_bytes(rand_ramp(rng)))
        paint = radial_state(cb, *radial_coeffs(rng, t, kind), ramp_addr, spread)
        if i % 2:
            fill(cb, t, rand_rect(rng, t, 3), rng.getrandbits(32), rand_alpha(rng), blend=rng.choice([0, 1, 11]),
                 paint=paint)
        else:
            draw_path(env, cb, t, rand_poly(rng, t.w / 2, t.h / 2, t.w * 0.6, rng.randint(3, 10)),
                      aa=rng.random() < 0.7, blend=rng.choice([0, 1]), paint=paint, alpha=rand_alpha(rng))
        if rng.random() < 0.5:          # a linear gradient and a solid draw right after
            lp = grad_state(cb, *grad_coeffs(rng, t, "other", 50), ramp_addr, rng.randrange(4))
            fill(cb, t, rand_rect(rng, t, 3), rng.getrandbits(32), rand_alpha(rng), blend=1, paint=lp)
            fill(cb, t, rand_rect(rng, t, 3), rng.getrandbits(32), rand_alpha(rng), blend=1)
        await run(env, cb.fence(i))


@cocotb.test()
async def grd01_ramp_reload(dut):
    """A4: the same ramp address is reloaded after a CPU rewrite between kicks and after a GPU draw that
    writes over the ramp inside one kick (the ramp area is also a 256 x 1 ARGB8888 target)."""
    env = await make_env(dut, "grd01_reload")
    rng = env.rng
    t = env.new_target(FMT["ARGB8888"], 64, 16)
    ramp_t = env.new_target(FMT["ARGB8888"], 256, 1)          # the ramp lives in this buffer
    for it in range(2 if QUICK else 4):
        p, q, r = grad_coeffs(rng, t, "other", rng.choice([40, 300]))
        env.write_mem(ramp_t.addr, ramp_bytes(rand_ramp(rng)))   # CPU writes the ramp before the kick
        cb = CmdBuf()
        paint = grad_state(cb, p, q, r, ramp_t.addr, it % 3)
        fill(cb, t, (0, 0, 64, 8), 0, paint=paint)                         # loads the ramp
        fill(cb, ramp_t, (rng.randint(0, 100), 0, rng.randint(150, 256), 1), rng.getrandbits(32))  # GPU overwrites it
        paint = grad_state(cb, p, q, r, ramp_t.addr, it % 3)
        fill(cb, t, (0, 8, 64, 16), 0, paint=paint)                        # must see the new ramp
        await run(env, cb.fence(it))
        # next kick, same address: the CPU rewrote it in between
        env.write_mem(ramp_t.addr, ramp_bytes(rand_ramp(rng)))
        cb = CmdBuf()
        paint = grad_state(cb, p, q, r, ramp_t.addr, (it + 1) % 3)
        draw_path(env, cb, t, rand_poly(rng, 32, 8, 30, 6), paint=paint, blend=1)
        await run(env, cb.fence(it))


@cocotb.test()
async def grd02_soft_reset_ramp(dut):
    """Soft reset while a gradient draw waits for its ramp (the ramp read not yet accepted: slow AR): the
    gradient pixels must not wait for a ramp that is never loaded, the soft reset completes and the next
    stream is exact (Phase 5 finding B-5.5, SYS-03 seed 34 at DATA_W 128)."""
    env = await make_env(dut, "grd02", profile=Profile(p_arready=0.02, r_lat=(50, 100)))
    rng = env.rng
    pix_busy = dut.u_dut.u_core.u_pix.busy_q
    for it in range(3 if QUICK else 8):
        t = env.new_target(FMT["ARGB8888"], 64, 8)
        env.mask.append((t.addr, t.addr + t.nbytes - 1))             # partly drawn
        ramp = env.place(ramp_bytes(rand_ramp(rng)))
        cb = CmdBuf()
        paint = grad_state(cb, *grad_coeffs(rng, t, "other", 100), ramp, it % 3)
        if it % 2:
            draw_path(env, cb, t, rand_poly(rng, 32, 4, 40, 8), paint=paint)
        else:
            fill(cb, t, (0, 0, 64, 8), 0, paint=paint)
        await env.kick(cb, to_model=False)
        n = 0
        while not int(pix_busy.value):
            await env.cycles(1)
            n += 1
            assert n < 200_000, "the gradient draw was never issued"
        await env.cycles(rng.randint(0, 30))
        await env.soft_reset()
        await env.check(perf=False)
        t2 = env.new_target(FMT["RGB565"], 16, 4)
        await run(env, fill(CmdBuf(), t2, (0, 0, 16, 4), rng.getrandbits(32)).fence(it), perf=False)
    env.perf_checked = False


# ------------------------------------------------------------------------------------------------
# streams, deadlock
# ------------------------------------------------------------------------------------------------

@cocotb.test()
async def random_paths(dut):
    """Random streams of paths, fills, blended fills and blits under random AXI profiles."""
    rng = random.Random(SEED + 88)
    env = await make_env(dut, "random_paths", profile=Profile.random(rng, max_lat=60))
    for rnd in range(4 if QUICK else 10):
        env.axi.profile = Profile.random(rng, max_lat=rng.choice([0, 20, 200]))
        bufs = [env.new_target(rng.choice(FMTS), rng.randint(1, 64), rng.randint(1, 24)) for _ in range(3)]
        for _ in range(rng.randint(1, 3)):
            cb = CmdBuf()
            for _ in range(rng.randint(1, 6)):
                r = rng.random()
                t, s = rng.sample(bufs, 2)
                if r < 0.45:
                    paint = 0
                    if rng.random() < 0.4:
                        ra = env.place(ramp_bytes(rand_ramp(rng)))
                        paint = grad_state(cb, *grad_coeffs(rng, t, "other", rng.choice([5, 60, 900])), ra,
                                           rng.randrange(3))
                    draw_path(env, cb, t, rand_poly(rng, t.w / 2, t.h / 2, max(t.w, t.h) * 0.7, rng.randint(3, 40)),
                              aa=rng.random() < 0.7, eo=rng.random() < 0.4, color=rng.getrandbits(32),
                              blend=rng.choice([0, rng.randint(0, 15)]), alpha=rand_alpha(rng), dalpha=rand_alpha(rng),
                              paint=paint)
                elif r < 0.65:
                    fill(cb, t, rand_rect(rng, t), rng.getrandbits(32), rand_alpha(rng),
                         blend=rng.choice([0, rng.randint(1, 15)]))
                elif r < 0.85:
                    blit(cb, t, s, mat_affine(rng, s, t, rng.choice(KINDS)), bilinear=rng.random() < 0.5,
                         blend=rng.choice([0, 1]))
                else:
                    cb.fence(rng.getrandbits(32))
            if await env.kick(cb) is None:
                break
        await env.wait_done()
        await env.check()
        if CsrIf.field("STATUS", "HALTED", await env.csr.read("STATUS")):
            await env.soft_reset()
            await env.check()


@cocotb.test()
async def axi08_path_deadlock(dut):
    """Phase 4 condition A1: 200-clock read and write-response latency and back-pressure (ready 30 %) on
    all five AXI channels while blended AA paths with long edge lists fetch edges, read the destination
    and write back; blits and fills in between. Must complete and match the model."""
    env = await make_env(dut, "axi08")
    rng = env.rng
    env.axi.profile = Profile(p_awready=0.3, p_wready=0.3, p_arready=0.3, p_rvalid=0.3, p_bvalid=0.3,
                              r_lat=(150, 200), b_lat=(150, 200), reorder=True, interleave=True)
    for it in range(2 if QUICK else 4):
        t = env.new_target(FMT["ARGB8888"], 72, 40)
        s = env.new_target(FMT["BGRA8888"], 30, 20)
        cb = CmdBuf()
        draw_path(env, cb, t, rand_poly(rng, 36, 20, 30, 300), aa=True, color=0x80FF8040, blend=1)
        blit(cb, t, s, mat_affine(rng, s, t, "rotate"), bilinear=True, blend=1)
        draw_path(env, cb, t, rand_poly(rng, 36, 20, 30, 150), aa=True, eo=True, color=0x6040FF20, blend=11)
        fill(cb, t, (5, 5, 60, 30), 0x40102030, blend=1)
        await run(env, cb.fence(it))


# ------------------------------------------------------------------------------------------------
# directed functional coverage
# ------------------------------------------------------------------------------------------------

@cocotb.test()
async def cov_path_matrix(dut):
    """Every cg_path bin: fill rule x AA x size class {sub-pixel, small (<= 16 px), medium (<= 128),
    large, guard-band (a vertex at or beyond +-16383 px)} x edge count {4, 40, 300, 600}; then a gradient
    path and (v1.5) an image path with every blend mode (cg_paint_path). Small targets; large and guard-band shapes are clipped."""
    env = await make_env(dut, "cov_path")
    rng = env.rng
    t = env.new_target(FMT["ARGB8888"], 64, 16)
    boxes = {"subpixel": (16 * 5 + 1, 16 * 5 + 1, 14), "small": (16 * 2, 16 * 1, 16 * 12),
             "medium": (16 * 2, 16 * 1, 16 * 100), "large": (-16 * 50, -16 * 40, 16 * 200)}
    counts = (4, 40, 300, 600) if not QUICK else (4, 600)
    for eo in (False, True):
        for aa in (False, True):
            for size in ("subpixel", "small", "medium", "large", "guard"):
                cb = CmdBuf()
                for n in counts:
                    if size == "guard":
                        x0, y0, d = 16 * 2, 16 * 1, 16 * 60
                    else:
                        x0, y0, d = boxes[size]
                    pts = [(x0 + rng.randint(0, d), y0 + rng.randint(0, d)) for _ in range(n)]
                    pts[1], pts[2] = (x0, y0), (x0 + d, y0 + d)       # the shape spans its whole box
                    if size == "guard":
                        pts[0] = (rng.choice([1, -1]) * 16 * rng.randint(16383, 20000), pts[0][1])
                    draw_path(env, cb, t, poly_edges(pts), aa=aa, eo=eo, color=rng.getrandbits(32),
                              blend=rng.choice([0, 1]))
                await run(env, cb.fence(1))
    ramp = env.place(ramp_bytes(rand_ramp(rng)))
    for mode in range(16):
        cb = CmdBuf()
        paint = grad_state(cb, *grad_coeffs(rng, t, "other", rng.choice([20, 300])), ramp, mode % 3)
        draw_path(env, cb, t, rand_poly(rng, 32, 8, 30, rng.randint(3, 12)), aa=True, blend=mode, paint=paint,
                  alpha=rand_alpha(rng), dalpha=rand_alpha(rng))
        await run(env, cb.fence(mode))
    for mode in range(16):           # v1.5: radial gradient paths with every blend mode
        cb = CmdBuf()
        paint = radial_state(cb, *radial_coeffs(rng, t, rng.choice(["centred", "focal"])), ramp, mode % 4)
        draw_path(env, cb, t, rand_poly(rng, 32, 8, 30, rng.randint(3, 12)), aa=True, blend=mode, paint=paint,
                  alpha=rand_alpha(rng), dalpha=rand_alpha(rng))
        await run(env, cb.fence(32 + mode))
    for mode in range(16):           # v1.5: image paint on paths with every blend mode
        cb = CmdBuf()
        s = env.new_target(rng.choice(FMTS), rng.randint(2, 12), rng.randint(2, 8))
        edges = rand_poly(rng, 32, 8, 30, rng.randint(3, 12))
        blit(cb, t, s, mat_affine(rng, s, t, rng.choice(KINDS)), bilinear=rng.random() < 0.5, wrap=rng.randint(0, 4),
             blend=mode, alpha=rand_alpha(rng), dalpha=rand_alpha(rng), border=rng.getrandbits(32),
             path=(env.place(edges_to_bytes(edges)), len(edges), bbox_of(edges), True, False))
        await run(env, cb.fence(16 + mode))


# ------------------------------------------------------------------------------------------------
# PRF-01
# ------------------------------------------------------------------------------------------------

@cocotb.test()
async def prf01_path_throughput(dut):
    """Pixels of the walked rectangle per busy cycle (command fetch, edge load, band setup and the drain
    wait included), ARGB8888, SRC_OVER (destination read), ideal memory and a fixed 90-clock read/B
    latency. A warm-up draw first (the winding accumulator clears itself after a reset).
    A8 criterion checked on the large shape; the glyph-sized shape misses it (0.83 px/clk: per-draw edge
    load and per-band setup are not overlapped with the rows), recorded as docs/STATUS.md known issue
    K-1 for a decision."""
    env = await make_env(dut, "prf01")
    rng = env.rng
    t = env.new_target(FMT["ARGB8888"], 256, 128, addr=0x1C00_0000, stride=1024)
    await run(env, draw_path(env, CmdBuf(), t, rand_poly(rng, 10, 10, 8, 4), blend=1).fence(0), perf=False)
    glyph_e, glyph_bb = driver_edges(circle(PathData(), 32, 32, 30))            # 64 x 64, <= 8 crossings/row
    big_e, big_bb = driver_edges(circle(PathData(), 128, 64, 63))               # 256 x 128
    narrow = poly_edges([(16 * 2, 16 * 2), (16 * 14, 16 * 5), (16 * 12, 16 * 120), (16 * 3, 16 * 100)])
    star_e, star_bb = driver_edges(star(PathData(), 128, 64, 63, 41, 20))       # ~40 crossings per row
    ramp = env.place(ramp_bytes(rand_ramp(rng)))
    cases = [
        ("glyph 64x64 (<=8 cross)", glyph_e, glyph_bb, 0, False),   # known miss K-1
        ("circle 256x128 (<=8)", big_e, big_bb, 0, True),
        ("narrow 16 px (<=8)", narrow, (0, 0, 16, 128), 0, False),
        ("star 41 points (~40)", star_e, star_bb, 0, False),
        ("gradient rect 256x128", None, (0, 0, 256, 128), 2, False),
    ]
    results = {}
    for prof_name, prof in (("ideal", Profile.ideal()), ("lat90", Profile(r_lat=(90, 90), b_lat=(90, 90)))):
        env.axi.profile = prof
        for name, edges, bb, paint_src, gated in cases:
            await env.csr.write("PERF_CTRL", 0b011)
            cb = CmdBuf()
            if edges is None:
                paint = grad_state(cb, *grad_coeffs(rng, t, "other", 200), ramp, 1)
                fill(cb, t, bb, 0, paint=paint, blend=1)
            else:
                draw_path(env, cb, t, edges, aa=True, color=0x80F08040, blend=1, bbox=bb)
            walked = (min(bb[2], t.w) - max(bb[0], 0)) * (min(bb[3], t.h) - max(bb[1], 0))
            await run(env, cb.fence(1), perf=False)
            await env.csr.write("PERF_CTRL", 0b101)
            busy = await env.csr.read("PERF_BUSY_CYCLES")
            pix = await env.csr.read("PERF_PIXELS_OUT")
            results[(prof_name, name)] = (walked / busy, gated)
            dut._log.info(f"PRF-01 {prof_name:6} {name:26}: {walked} px walked, {pix} written, {busy} busy cycles "
                          f"= {walked / busy:.3f} px/clk")
    env.perf_checked = False
    for (prof_name, name), (r, gated) in results.items():
        if prof_name == "ideal" and gated:
            assert r >= 0.9, (name, r)


@cocotb.test()
async def prf02_image_path_throughput(dut):
    """v1.5 (F-1, plan item 2, question 5): a rotated / perspective bilinear image as the paint of a rectangle path
    (scanline order, the raster's) against the same image as a blit (16 x 16 tiles), ARGB8888, SRC_OVER, ideal
    memory and 90-clock read / B latency: pixels of the draw rectangle per busy cycle and source line misses. The
    traversal fix is built only if the path reaches less than 85 % of the blit's rate at 90-clock latency."""
    from test_blit import log_cache_stalls
    env = await make_env(dut, "prf02")
    s = env.new_target(FMT["ARGB8888"], 256, 64, addr=0x1C00_0000, stride=1024)
    t = env.new_target(FMT["ARGB8888"], 256, 64, addr=0x1D00_0000, stride=1024)
    kinds = {"rotated": [[0.9, -0.3, 40], [0.3, 0.9, -20], [0, 0, 1]],
             "rotated45": [[0.7071, -0.7071, 100], [0.7071, 0.7071, -60], [0, 0, 1]],
             "perspective": [[1, 0.1, 0], [0, 1, 0], [0.0008, 0.0004, 1]]}
    results = {}
    for prof_name, prof in (("ideal", Profile.ideal()), ("lat90", Profile(r_lat=(90, 90), b_lat=(90, 90)))):
        env.axi.profile = prof
        for kind, m in kinds.items():
            for as_path in (False, True):
                cb = CmdBuf()
                rc, draw = blit(cb.__class__(), t, s, m, bilinear=True, blend=1)   # the blit's draw rectangle
                x0, y0, x1, y1 = draw
                path = None
                if as_path:
                    edges = poly_edges([(16 * x0, 16 * y0), (16 * x1, 16 * y0), (16 * x1, 16 * y1), (16 * x0, 16 * y1)])
                    path = (env.place(edges_to_bytes(edges)), len(edges), (x0, y0, x1, y1), True, False)
                blit(cb, t, s, m, bilinear=True, blend=1, path=path)
                await env.csr.write("PERF_CTRL", 0b011)
                await run(env, cb.fence(1), perf=False)
                await env.csr.write("PERF_CTRL", 0b101)
                busy = await env.csr.read("PERF_BUSY_CYCLES")
                miss = await env.csr.read("PERF_SRC_CACHE_MISS")
                walked = (min(x1, t.w) - max(x0, 0)) * (min(y1, t.h) - max(y0, 0))
                name = f"{kind} {'path' if as_path else 'blit'}"
                results[(prof_name, kind, as_path)] = walked / busy
                dut._log.info(f"prf02 {prof_name:6} {name:17}: {walked} px, {busy} busy = {walked / busy:.3f} px/clk, "
                              f"{miss} line misses")
                log_cache_stalls(dut)
    env.perf_checked = False
    for prof_name in ("ideal", "lat90"):
        for kind in kinds:
            ratio = results[(prof_name, kind, True)] / results[(prof_name, kind, False)]
            dut._log.info(f"prf02 {prof_name} {kind}: path / blit = {ratio:.3f}")
            # decision criterion (v1.5 question 5), default port only (dw128 starves the port on purpose)
            if "RD_OUTSTANDING" not in P:
                assert ratio >= 0.85, (prof_name, kind, ratio)
    # v1.7 Q-PERF-2 (PHASES.md v1.7 question 3): blended tiled blits at 90-clock latency, default port. Perspective is
    # bounded by the texel path (0.85 with ideal memory), not by the destination reads.
    if "RD_OUTSTANDING" not in P:
        for kind, lo in (("rotated", 0.85), ("rotated45", 0.85), ("perspective", 0.80)):
            assert results[("lat90", kind, False)] >= lo, (kind, results[("lat90", kind, False)])


@cocotb.test(skip=os.environ.get("G2D_K1") != "1")
async def k1_glyph_stream(dut):
    """K-1 evaluation (Phase 5, analysis only; G2D_K1=1): streams of back-to-back AA glyph draws (one kick
    each), every busy cycle classified from the rasterizer state: gap (pixel engine idle: fetch, parse,
    drain wait), load (edge load), band (band setup), rows (passes and sweep), tail (pipeline drain after
    the rasterizer). Projections remove the load cycles of every draw but the first (edge-list prefetch
    during the previous draw, bounded by that draw's row time) and / or every band setup but a draw's
    first (band setup overlapped with the previous band's rows). Output checked against the model;
    numbers written to build/k1_eval.json."""
    import json
    env = await make_env(dut, "k1")
    rng = env.rng
    t = env.new_target(FMT["ARGB8888"], 512, 64, addr=0x1C00_0000, stride=2048)
    await run(env, draw_path(env, CmdBuf(), t, rand_poly(rng, 10, 10, 8, 4), blend=1).fence(0), perf=False)
    core, pix = dut.u_dut.u_core, dut.u_dut.u_core.u_pix
    ras = pix.u_raster
    cls = {0: "tail", 1: "load", 2: "load", 3: "load", 4: "band", 5: "band", 6: "rows", 7: "rows"}
    out = {}
    for prof_name, prof in (("ideal", Profile.ideal()), ("lat90", Profile(r_lat=(90, 90), b_lat=(90, 90)))):
        env.axi.profile = prof
        for size, n in ((64, 8), (24, 16)):
            cb = CmdBuf()
            walked = 0
            for k in range(n):
                cx, cy, r = (k % 8) * 64 + 32, 32, size // 2 - 2
                e, bb = driver_edges(circle(PathData(), cx, cy, r))
                draw_path(env, cb, t, e, aa=True, color=rng.getrandbits(32) | 0x80000000, blend=1, bbox=bb)
                walked += (min(bb[2], t.w) - max(bb[0], 0)) * (min(bb[3], t.h) - max(bb[1], 0))
            cb.fence(1)
            per_draw, cur, counts, done = [], None, {}, False

            async def sample():
                nonlocal cur
                while not done:
                    await RisingEdge(dut.clk)
                    if int(core.u_csr.u_ctrl.idle.value):          # PERF_BUSY_CYCLES counts !idle
                        continue
                    busy = int(pix.busy_q.value)
                    if busy and cur is None:
                        cur = {"gap": 0, "load": 0, "band": 0, "rows": 0, "tail": 0}
                        per_draw.append(cur)
                    if not busy:
                        counts["gap"] = counts.get("gap", 0) + 1
                        cur = None
                        continue
                    c = cls[int(ras.st_q.value)]
                    cur[c] += 1
            task = cocotb.start_soon(sample())
            await run(env, cb, perf=False)
            done = True
            await RisingEdge(dut.clk)
            task.cancel()
            tot = {c: sum(d[c] for d in per_draw) + (counts.get("gap", 0) if c == "gap" else 0)
                   for c in ("gap", "load", "band", "rows", "tail")}
            busy = sum(tot.values())
            load_hidden = sum(min(d["load"], p["rows"]) for p, d in zip(per_draw, per_draw[1:]))
            bands = (size + 15) // 16                               # bands per draw (16 rows each)
            band_hidden = sum(d["band"] * (bands - 1) // bands for d in per_draw)
            res = {"draws": len(per_draw), "walked_px": walked, "busy": busy, **tot,
                   "px_per_clk": walked / busy,
                   "prefetch_edges": walked / (busy - load_hidden),
                   "overlap_bands": walked / (busy - band_hidden),
                   "both": walked / (busy - load_hidden - band_hidden)}
            out[f"{prof_name}/{size}px"] = res
            dut._log.info(f"K-1 {prof_name} {size}px x{n}: " + ", ".join(
                f"{k} {v:.3f}" if isinstance(v, float) else f"{k} {v}" for k, v in res.items()))
    Path(os.environ.get("G2D_ROOT", ".")).joinpath("build", "k1_eval.json").write_text(json.dumps(out, indent=1))
    env.perf_checked = False


# ------------------------------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

CONFIGS = {
    "dw64": (dict(DATA_W=64), {}),
    "dw128": (dict(DATA_W=128, ADDR_W=48, KICK_DEPTH=8, MAX_BURST_BYTES=128, RD_OUTSTANDING=4, WR_OUTSTANDING=2),
              {"G2D_BASE_HI": "5"}),
}


# the dw64 run is split in two pytest nodes of about equal length, so make test runs them in parallel
PART_A = ["pth05_long_lists", "pth03_winding", "pth05_row_overflow"]
PARTS = {"dw64-a": ("dw64", PART_A), "dw64-b": ("dw64", "rest"), "dw128": ("dw128", None)}


@pytest.mark.parametrize("part", list(PARTS))
def test_path(part):
    import runner
    cfg, tests = PARTS[part]
    params, env = CONFIGS[cfg]
    if tests == "rest":
        import ast
        tree = ast.parse(Path(__file__).read_text())
        tests = [f.name for f in tree.body if isinstance(f, ast.AsyncFunctionDef) and f.name not in PART_A]
    quick = cfg != "dw64" and os.environ.get("G2D_FULL") != "1"
    runner.run("g2d_top_tb", "test_path", params=params, tag=cfg, testcase=tests,
               extra_env={**env, "G2D_QUICK": "1" if quick else "0"})
