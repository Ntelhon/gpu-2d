# SPDX-License-Identifier: Apache-2.0
"""L2 bench of g2d_blend against the golden model's stage functions (verification-plan.md BLD-01, BLD-02
at block level; coverage blend for Phase 4).

Expected pixel = compress(fmt, coverage_blend(D', blend(mode, S', D'), cov)) with
S' = apply_global_alpha(S, src mode), D' = apply_global_alpha(expand(fmt, D raw), dst mode).

- blend_exhaustive (BLD-01): per mode, every (s, d) pair in the alpha channel (S.A = s, D.A = d) and in a
  colour channel with independent random alphas; ARGB8888 target, global alpha NORMAL, cov 255.
  G2D_FULL=1 sweeps all 65536 pairs per mode and channel class, otherwise a 1/16 stride plus corners.
- blend_alpha_formats (BLD-02): random S/D/cov for every mode x src/dst global-alpha mode x target format,
  including cov 0 / 255 / random and the global values 0 / 255.
- blend_dither (v1.4, rendering.md §3.6): every target format with dither on, random pixel positions
  (in_dxy_i), expected compress_dither.
- blend_families (v1.6, rendering.md §5.7): OpenVG modes 0-9, SVG2 modes 0-6, random Porter-Duff BLEND_FUNC words
  (every factor, equation and channel mode) and constants, with random global alpha, coverage and target format,
  against gm.blend_family; the 0 / 255 alpha corners (result alpha 0, n >= ra) included.
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

import g2d_model as gm
from g2d_cmd import FMT, TGT_FMTS
from stream import run_stream

FULL = bool(int(os.environ.get("G2D_FULL", "0")))
TAG_W = 16


def expected(mode, fmt, sm, sv, dm, dv, s, draw, cov, dxy=None):
    s2 = gm.apply_global_alpha(s, sm, sv)
    d2 = gm.apply_global_alpha(gm.expand(fmt, draw), dm, dv)
    o = gm.coverage_blend(d2, gm.blend(mode, s2, d2), cov)
    return gm.compress(fmt, o) if dxy is None else gm.compress_dither(fmt, o, dxy & 3, dxy >> 2)


async def start(dut):
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_ni.value = 0
    dut.clr_i.value = 0
    dut.in_valid_i.value = 0
    dut.out_ready_i.value = 0
    await Timer(25, unit="ns")
    dut.rst_ni.value = 1
    await RisingEdge(dut.clk_i)


def set_cfg(dut, mode, fmt, sm=0, sv=255, dm=0, dv=255, family=0, func=0, const=0, stencil=0):
    dut.cfg_mode_i.value = mode
    dut.cfg_stencil_i.value = stencil
    dut.cfg_family_i.value = family
    dut.cfg_func_i.value = func
    dut.cfg_const_i.value = const
    dut.cfg_fmt_i.value = fmt
    dut.cfg_src_amode_i.value = sm
    dut.cfg_src_aval_i.value = sv
    dut.cfg_dst_amode_i.value = dm
    dut.cfg_dst_aval_i.value = dv
    dut.cfg_dither_i.value = 0


def drive(dut, v):
    s, d, cov, tag = v[:4]
    dut.in_dxy_i.value = v[4] if len(v) > 4 else 0
    dut.in_s_i.value = s
    dut.in_d_i.value = d
    dut.in_cov_i.value = cov
    dut.in_tag_i.value = tag


def sample(dut):
    return int(dut.out_pix_o.value), int(dut.out_tag_o.value)


async def check_batch(dut, rng, cfg, vecs, p_in=1.0, p_out=1.0):
    mode, fmt, sm, sv, dm, dv = cfg
    set_cfg(dut, *cfg)
    tagged = [(s, d, cov, i & ((1 << TAG_W) - 1)) for i, (s, d, cov) in enumerate(vecs)]
    outs = await run_stream(dut, tagged, drive, sample, rng, p_in, p_out)
    nbytes = gm.fmt_bytes(fmt)
    mask = (1 << (8 * nbytes)) - 1
    for i, ((s, d, cov), (got, tag)) in enumerate(zip(vecs, outs)):
        assert tag == i & ((1 << TAG_W) - 1), f"order: output {i} has tag {tag}"
        exp = expected(mode, fmt, sm, sv, dm, dv, s, d & mask, cov)
        assert got & mask == exp, (f"mode {mode} fmt 0x{fmt:02x} ga ({sm},{sv}) ({dm},{dv}) S 0x{s:08x} "
                                   f"D 0x{d & mask:x} cov {cov}: got 0x{got & mask:x} expected 0x{exp:x}")


ARGB = FMT["ARGB8888"]


def argb_raw(a, r, g, b):
    """ARGB8888 (FMT 0x23) stored value: byte0 A, byte1 R, byte2 G, byte3 B."""
    return a | (r << 8) | (g << 16) | (b << 24)


@cocotb.test()
async def blend_exhaustive(dut):
    await start(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")))
    step = 1 if FULL else 16
    pairs = [(x, y) for x in range(256) for y in range(0, 256, 1)][::step]
    corners = [0, 1, 127, 128, 254, 255]
    pairs += [(x, y) for x in corners for y in corners]
    for mode in range(16):
        vecs = []
        for x, y in pairs:
            # alpha channel exhaustive: S.A = x, D.A = y
            r1, r2 = rng.getrandbits(24), rng.getrandbits(24)
            vecs.append(((x << 24) | r1, argb_raw(y, r2 >> 16 & 255, r2 >> 8 & 255, r2 & 255), 255))
            # colour channel (R) exhaustive with random alphas
            sa, da = rng.choice(corners + [rng.randrange(256)]), rng.choice(corners + [rng.randrange(256)])
            vecs.append(((sa << 24) | (x << 16) | rng.getrandbits(16),
                         argb_raw(da, y, rng.randrange(256), rng.randrange(256)), 255))
        await check_batch(dut, rng, (mode, ARGB, 0, 255, 0, 255), vecs)


@cocotb.test()
async def blend_alpha_formats(dut):
    await start(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")) + 17)
    n = 96 if FULL else 24
    for mode in range(16):
        for fmt in TGT_FMTS:
            for sm in range(3):
                for dm in range(3):
                    sv = rng.choice([0, 255, rng.randrange(256)])
                    dv = rng.choice([0, 255, rng.randrange(256)])
                    vecs = [(rng.getrandbits(32), rng.getrandbits(32), rng.choice([0, 255, rng.randrange(256)]))
                            for _ in range(n)]
                    await check_batch(dut, rng, (mode, fmt, sm, sv, dm, dv), vecs, p_in=0.8, p_out=0.7)


@cocotb.test()
async def blend_dither(dut):
    await start(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")) + 23)
    for fmt in TGT_FMTS:
        for mode in (0, 1, 11):
            set_cfg(dut, mode, fmt)
            dut.cfg_dither_i.value = 1
            vecs = [(rng.getrandbits(32), rng.getrandbits(32), rng.choice([255, rng.randrange(256)]), i,
                     rng.randrange(16)) for i in range(64)]
            outs = await run_stream(dut, vecs, drive, sample, rng, 0.8, 0.7)
            mask = (1 << (8 * gm.fmt_bytes(fmt))) - 1
            for (s, d, cov, i, dxy), (got, tag) in zip(vecs, outs):
                assert tag == i
                exp = expected(mode, fmt, 0, 255, 0, 255, s, d & mask, cov, dxy)
                assert got & mask == exp, f"fmt 0x{fmt:02x} mode {mode} dxy {dxy}: got {got & mask:#x} expected {exp:#x}"


@cocotb.test()
async def blend_throughput(dut):
    """One pixel per clock with both sides always ready, and a clear drops the pipeline contents."""
    await start(dut)
    rng = random.Random(3)
    set_cfg(dut, 5, ARGB)
    vecs = [(rng.getrandbits(32), rng.getrandbits(32), 255, i) for i in range(400)]
    t0 = cocotb.utils.get_sim_time("ns")
    await run_stream(dut, vecs, drive, sample, rng)
    cycles = (cocotb.utils.get_sim_time("ns") - t0) / 10
    assert cycles <= 400 + 20, cycles     # v1.7: 18 stages (v1.6: 13); one pixel per clock
    # clear with pixels in flight
    dut.in_valid_i.value = 1
    dut.out_ready_i.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk_i)
    dut.in_valid_i.value = 0
    dut.clr_i.value = 1
    await RisingEdge(dut.clk_i)
    dut.clr_i.value = 0
    dut.out_ready_i.value = 1
    for _ in range(16):
        await RisingEdge(dut.clk_i)
        assert int(dut.out_valid_o.value) == 0


@cocotb.test()
async def blend_families(dut):
    await start(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")) + 57)
    corner = [0, 1, 128, 254, 255]

    def px():
        return sum((rng.choice(corner) if rng.random() < 0.25 else rng.getrandbits(8)) << (8 * k) for k in range(4))

    cases = [(1, m) for m in range(10)] + [(2, m) for m in range(7)] + [(3, 0)] * (24 if FULL else 10)
    for fam, mode in cases:
        func = 0
        if fam == 3:
            func = (rng.randint(0, 8) | (rng.randint(0, 16) << 4) | (rng.randint(0, 8) << 9) | (rng.randint(0, 16) << 13) |
                    (rng.randint(0, 4) << 18) | (rng.choice(list(range(11)) + [15]) << 21) |
                    (rng.choice(list(range(11)) + [15]) << 25))
        const = px()
        fmt = rng.choice(list(TGT_FMTS))
        sm, dm = rng.choice([0, 0, 1, 2]), rng.choice([0, 0, 1, 2])
        sv, dv = rng.getrandbits(8), rng.getrandbits(8)
        set_cfg(dut, mode, fmt, sm, sv, dm, dv, fam, func, const)
        vecs = [(px(), px(), rng.choice([255, 255, 0, rng.getrandbits(8)]), i & 0xFFFF) for i in range(150 if FULL else 60)]
        outs = await run_stream(dut, vecs, drive, sample, rng, 0.8, 0.8)
        mask = (1 << (8 * gm.fmt_bytes(fmt))) - 1
        for (s, d, cov, tag), (got, gtag) in zip(vecs, outs):
            assert gtag == tag
            s2 = gm.apply_global_alpha(s, sm, sv)
            d2 = gm.apply_global_alpha(gm.expand(fmt, d & mask), dm, dv)
            exp = gm.compress(fmt, gm.coverage_blend(d2, gm.blend_family(fam, mode, func, s2, d2, const), cov))
            assert got & mask == exp, (fam, mode, hex(func), hex(const), hex(fmt), hex(s), hex(d & mask), cov,
                                       hex(got & mask), hex(exp))


def test_blend():
    import runner
    runner.run("g2d_blend", "test_blend", params={"TAG_W": TAG_W})


@cocotb.test()
async def blend_stencil(dut):
    """v1.6 stencil image mode (rendering.md §5.3.1): every §5.6 mode with the per-channel source alphas, random image
    values, paint colours (cfg_const_i), destinations, coverage, destination global alpha and target formats."""
    await start(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")) + 59)
    for mode in range(16):
        fmt = rng.choice(list(TGT_FMTS))
        dm, dv = rng.choice([0, 1, 2]), rng.getrandbits(8)
        p = rng.getrandbits(32) if mode % 4 else rng.choice([0xFFFFFFFF, 0x80FF8040])
        set_cfg(dut, mode, fmt, 0, 255, dm, dv, const=p, stencil=1)
        vecs = [(rng.getrandbits(32), rng.getrandbits(32), rng.choice([255, rng.getrandbits(8)]), i) for i in range(80)]
        outs = await run_stream(dut, vecs, drive, sample, rng, 0.8, 0.8)
        mask = (1 << (8 * gm.fmt_bytes(fmt))) - 1
        for (s, d, cov, tag), (got, gtag) in zip(vecs, outs):
            d2 = gm.apply_global_alpha(gm.expand(fmt, d & mask), dm, dv)
            exp = gm.compress(fmt, gm.coverage_blend(d2, gm.blend_stencil(mode, s, p, d2), cov))
            assert gtag == tag and got & mask == exp, (mode, hex(fmt), hex(s), hex(p), hex(d & mask), cov,
                                                       hex(got & mask), hex(exp))
