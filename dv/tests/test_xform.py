# SPDX-License-Identifier: Apache-2.0
"""L2 bench of g2d_xform (rendering.md §7.1) against the golden model's xform_map.

Expected: (u, v) = gm.xform_map(XF, persp, x, y), or None when W <= 0. With cfg_wrap_i = 0 (WRAP = NONE) the
unit reports ok = 1 exactly when the model gives 0 <= u, v < 2^29 (the only values that can pass the source
test, §7.4); with cfg_wrap_i = 1 (v1.4 wrap modes) exactly when -2^31 <= u, v < 2^31. When ok, u, v (signed
32-bit) must be equal. Every case runs in both modes. Cases:
- boundaries: affine U at 2^30 - 1 / 2^30 / -1, perspective 2U = W - 1 / W, W = 0 / 1 / -1, W >= 2^32
- random affine maps (scale, rotation, shear, translation) and random perspective warps over the
  full 8192 x 8192 coordinate range
- raw random coefficients (mostly out of range; checks that out-of-range never produces ok)
- v1.4 wrap boundaries: perspective U = -2W - 1 / -2W / 2W - 1 / 2W, affine U at +-2^32
"""

import math
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

import g2d_model as gm
from stream import run_stream

FULL = bool(int(os.environ.get("G2D_FULL", "0")))
TAG_W = 16
LIM = 1 << 29


def s32(v):
    v &= 0xFFFF_FFFF
    return v - (1 << 32) if v >> 31 else v


def clamp32(v):
    return max(-(1 << 31), min((1 << 31) - 1, int(v)))


async def start(dut):
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_ni.value = 0
    dut.clr_i.value = 0
    dut.in_valid_i.value = 0
    dut.out_ready_i.value = 0
    await Timer(25, unit="ns")
    dut.rst_ni.value = 1
    await RisingEdge(dut.clk_i)


def drive(dut, v):
    x, y, tag = v
    dut.in_x_i.value = x
    dut.in_y_i.value = y
    dut.in_tag_i.value = tag


def sample(dut):
    return int(dut.out_ok_o.value), s32(int(dut.out_u_o.value)), s32(int(dut.out_v_o.value)), int(dut.out_tag_o.value)


async def check1(dut, rng, xf, persp, pts, wrap, p_in, p_out):
    dut.cfg_wrap_i.value = int(wrap)
    vecs = [(x, y, i & 0xFFFF) for i, (x, y) in enumerate(pts)]
    outs = await run_stream(dut, vecs, drive, sample, rng, p_in, p_out)
    n_ok = 0
    lo, hi = (-(1 << 31), 1 << 31) if wrap else (0, LIM)
    for i, ((x, y), (ok, u, v, tag)) in enumerate(zip(pts, outs)):
        assert tag == i & 0xFFFF
        m = gm.xform_map(xf, persp, x, y)
        exp_ok = m is not None and lo <= m[0] < hi and lo <= m[1] < hi
        assert ok == int(exp_ok), f"xf {xf} persp {persp} wrap {wrap} ({x},{y}): ok {ok}, model {m}"
        if ok:
            n_ok += 1
            assert (u, v) == m, f"xf {xf} persp {persp} wrap {wrap} ({x},{y}): got ({u},{v}), model {m}"
    return n_ok


async def check(dut, rng, xf, persp, pts, p_in=1.0, p_out=1.0):
    """Both wrap modes; returns the WRAP = NONE ok count."""
    xf = [s32(c) for c in xf]
    val = 0
    for i, c in enumerate(xf):
        val |= (c & 0xFFFF_FFFF) << (32 * i)
    dut.cfg_xf_i.value = val
    dut.cfg_persp_i.value = int(persp)
    await check1(dut, rng, xf, persp, pts, True, p_in, p_out)
    return await check1(dut, rng, xf, persp, pts, False, p_in, p_out)


def rand_pts(rng, n, w=8192, h=8192):
    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    return corners + [(rng.randrange(w), rng.randrange(h)) for _ in range(n)]


@cocotb.test()
async def xform_boundaries(dut):
    await start(dut)
    rng = random.Random(1)
    one = 1 << 16
    # affine, A = B = D = E = 0: U = 2C, so u = C exactly; ok iff 0 <= C < 2^29 (and the same for V)
    for c in [0, 1, LIM - 1, LIM, -1, -(1 << 31), (1 << 31) - 1, LIM // 2]:
        for f in [0, LIM - 1, LIM, -1]:
            await check(dut, rng, [0, 0, c, 0, 0, f, 0, 0, 1 << 30], False, [(0, 0), (5, 9)])
    # affine with U crossing 2^30 inside the row: A = 2^16, C chosen so x = 8191 lands on the boundary
    await check(dut, rng, [one, 0, LIM - 8191 * one - one // 2, 0, one, 0, 0, 0, 1 << 30], False,
                [(x, 3) for x in range(8180, 8192)])
    # perspective, G = H = 0: W = 2I, U = 2C: ok iff 0 <= 2C... i.e. 2U < W  <=>  2C < I
    for i_ in [1, 2, 3, 1 << 20, (1 << 31) - 1, 0, -1, -(1 << 31)]:
        for c in sorted({0, i_ // 2 - 1, i_ // 2, (i_ + 1) // 2, -1, 1}):
            await check(dut, rng, [0, 0, c, 0, 0, c // 2, 0, 0, i_], True, [(0, 0), (100, 7)])
    # W crossing zero inside the rect (G < 0), and W >= 2^32 (outside the driver contract, still exact)
    await check(dut, rng, [one, 0, 0, 0, one, 0, -(1 << 18), 0, 1 << 30], True,
                [(x, 0) for x in range(4090, 4100)] + rand_pts(rng, 40))
    await check(dut, rng, [(1 << 31) - 1, (1 << 31) - 1, (1 << 31) - 1, (1 << 31) - 1, 5, 7,
                           (1 << 31) - 1, (1 << 31) - 1, (1 << 31) - 1], True, rand_pts(rng, 40))


@cocotb.test()
async def xform_wrap_boundaries(dut):
    """v1.4: the wrap-mode range -2^31 <= u < 2^31 at its edges (G = H = 0: W = 2I, U = 2C, so the test is
    -2I <= C < 2I; affine U = A*xh + 2C crossing +-2^32)."""
    await start(dut)
    rng = random.Random(4)
    for i_ in [1, 2, 3, 1000, 1 << 20, (1 << 29) - 1]:
        for c in [-2 * i_ - 1, -2 * i_, -2 * i_ + 1, -1, 0, 2 * i_ - 1, 2 * i_]:
            await check(dut, rng, [0, 0, c, 0, 0, -c, 0, 0, i_], True, [(0, 0)])
    # perspective with negative U in the row: odd remainders (floor of a negative quotient)
    await check(dut, rng, [-(3 << 16) + 1, 0, 12345, 0, 7 << 15, -99, 1 << 12, 0, 1 << 28], True,
                [(x, 2) for x in range(0, 300)])
    one = 1 << 16
    # affine: U = A * xh + 2C with A = 2^31 - 1 crosses 2^32 at small x
    for a in [(1 << 31) - 1, -(1 << 31), 3 << 28]:
        await check(dut, rng, [a, 0, rng.randrange(-(1 << 31), 1 << 31), 0, one, 0, 0, 0, 1 << 30], False,
                    [(x, 0) for x in range(0, 6)])


def affine(rng):
    s = math.exp(rng.uniform(math.log(0.1), math.log(10)))
    t = rng.uniform(0, 2 * math.pi) if rng.random() < 0.6 else rng.choice([0, math.pi / 2, math.pi, -math.pi / 2])
    sh = rng.uniform(-0.5, 0.5) if rng.random() < 0.3 else 0.0
    a, b = s * math.cos(t), -s * math.sin(t) + sh
    d, e = s * math.sin(t), s * math.cos(t)
    c = rng.uniform(-8192, 8192) * s + 4096
    f = rng.uniform(-8192, 8192) * s + 4096
    return [clamp32(round(v * 65536)) for v in (a, b, c, d, e, f)] + [0, 0, 1 << 30]


def perspective(rng):
    xf = affine(rng)
    g, h = rng.uniform(-1e-4, 1e-4), rng.uniform(-1e-4, 1e-4)
    i_ = rng.uniform(0.3, 1.0)
    return xf[:6] + [clamp32(round(g * (1 << 30))), clamp32(round(h * (1 << 30))), clamp32(round(i_ * (1 << 30)))]


@cocotb.test()
async def xform_random(dut):
    await start(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")) + 3)
    nm, npts = (60, 400) if FULL else (16, 120)
    ok_aff = ok_per = 0
    for _ in range(nm):
        ok_aff += await check(dut, rng, affine(rng), False, rand_pts(rng, npts), 0.8, 0.8)
        ok_per += await check(dut, rng, perspective(rng), True, rand_pts(rng, npts), 0.8, 0.8)
        await check(dut, rng, [rng.getrandbits(32) for _ in range(9)], rng.random() < 0.5, rand_pts(rng, 20))
    # stimulus guard: a real share of the pixels must land inside the coordinate range
    assert ok_aff > nm * npts // 20 and ok_per > nm * npts // 20, (ok_aff, ok_per)


@cocotb.test()
async def xform_throughput(dut):
    await start(dut)
    rng = random.Random(2)
    pts = rand_pts(rng, 500)
    xf = [s32(c) for c in perspective(rng)]
    dut.cfg_xf_i.value = sum((c & 0xFFFF_FFFF) << (32 * i) for i, c in enumerate(xf))
    dut.cfg_persp_i.value = 1
    t0 = cocotb.utils.get_sim_time("ns")
    await check1(dut, rng, xf, True, pts, False, 1.0, 1.0)
    cycles = (cocotb.utils.get_sim_time("ns") - t0) / 10
    assert cycles <= len(pts) + 40, cycles


def test_xform():
    import runner
    runner.run("g2d_xform", "test_xform", params={"TAG_W": TAG_W})
