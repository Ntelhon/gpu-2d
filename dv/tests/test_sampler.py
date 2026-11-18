# SPDX-License-Identifier: Apache-2.0
"""L2 benches of the blit sampler blocks (verification-plan.md BLD-02, BLD-03 at block level).

g2d_tex_addr (rendering.md §3.5, §7.3, §7.4, §7.5) against a direct Python transcription of the spec, with the
golden model's wrap_coord for the wrap modes:
  source test (WRAP = NONE), POINT texel, BILINEAR x0/y0/fractions, neighbours clamped (NONE / PAD / COLOR
  address) or wrapped (REPEAT / REFLECT), COLOR border flags, byte addresses and sub-byte shifts for every
  pixel size (1..32 bits) and both SUB_ORDER values. Cases: u, v on texel centres/borders, the rect edges,
  rects from the origin (u - 2^15 < 0), 1x1 and 1-wide rects, rect at 8191, periods 1..8192 (REFLECT up to
  16384), negative and far-away coordinates (wrap), random; ok = 0; back-to-back draws (reciprocal reload).
g2d_texel (§5.2, §7.3, §5.3 MULTIPLY) against the golden model's expand / color_key / premultiply /
  bilinear / multiply_color: every source format (sub-byte right-aligned, indexed as the CLUT word),
  FORCE_OPAQUE, 0-4 colour keys with overlapping ranges (priority), boundary values lo/hi, lo > hi (never
  matches), SRC_PREMUL, IMG_MUL, border texels (BORDER_COLOR), the A = 0 flag, fractions 0 / 255 / random.
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

import g2d_model as gm
from g2d_cmd import FMT, FMT_BITS, INDEXED_FMTS
from stream import run_stream

FULL = bool(int(os.environ.get("G2D_FULL", "0")))
TAG_W = 16


async def start(dut):
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_ni.value = 0
    dut.clr_i.value = 0
    dut.in_valid_i.value = 0
    dut.out_ready_i.value = 0
    if hasattr(dut, "start_i"):
        dut.start_i.value = 0
    await Timer(25, unit="ns")
    dut.rst_ni.value = 1
    await RisingEdge(dut.clk_i)


def pack(words, w=32):
    v = 0
    for i, x in enumerate(words):
        v |= (x & ((1 << w) - 1)) << (w * i)
    return v


# ------------------------------------------------------------------------------------------------
# g2d_tex_addr
# ------------------------------------------------------------------------------------------------

def tex_ref(u, v, bil, wrap, rect, addr, stride, lgb, sub_hi, solid=0):
    """(solid flag, addresses, ypar, fx, fy, border flags, shifts) or None when the pixel does not pass.
    solid: COLOR with BORDER_SOLID (§7.5): addressing as NONE, the flag marks a centre outside the rectangle."""
    sx0, sy0, sx1, sy1 = rect
    tx, ty = u >> 16, v >> 16
    solid_out = 0
    if wrap == 1 and solid:
        solid_out = int(not (sx0 <= tx < sx1 and sy0 <= ty < sy1))
        wrap_addr = 0
    else:
        wrap_addr = wrap
    if wrap == 0 and not (sx0 <= tx < sx1 and sy0 <= ty < sy1):
        return None
    if not bil:
        cxs, cys, fx, fy = (tx, tx), (ty, ty), 0, 0
    else:
        up, vp = u - 32768, v - 32768
        x0, y0 = up >> 16, vp >> 16
        fx, fy = (up >> 8) & 255, (vp >> 8) & 255
        cxs, cys = (x0, x0 + 1), (y0, y0 + 1)

    def mapc(c, lo, hi):
        m = gm.wrap_coord(wrap_addr, c, lo, hi)
        return (min(max(c, lo), hi - 1), True) if m is None else (m, False)

    xs = [mapc(c, sx0, sx1) for c in cxs]
    ys = [mapc(c, sy0, sy1) for c in cys]
    bits = 1 << lgb
    a, border, sh = [], 0, []
    for j in (0, 1):
        for i in (0, 1):
            colbits = xs[i][0] << lgb
            a.append((addr + ys[j][0] * stride + (colbits >> 3)) & 0xFFFF_FFFF)
            o = colbits & 7
            sh.append(0 if bits >= 8 else (8 - bits - o if sub_hi else o))
            border |= int(xs[i][1] or ys[j][1]) << (2 * j + i)
    return solid_out, a, (ys[0][0] & 1) | ((ys[1][0] & 1) << 1), fx, fy, border, sh


def rand_rect(rng, wrap):
    kind = rng.random()
    if kind < 0.2:      # 1x1 or thin rects
        sx0, sy0 = rng.randrange(8192), rng.randrange(8192)
        sx1, sy1 = sx0 + 1, min(8192, sy0 + rng.choice([1, 2]))
    elif kind < 0.3:    # rect touching 8192
        sx0, sy0, sx1, sy1 = rng.randrange(8000, 8191), rng.randrange(8000, 8191), 8192, 8192
    elif kind < 0.4 and wrap:   # full-range periods
        sx0, sy0, sx1, sy1 = 0, 0, 8192, rng.choice([8192, 4097])
    elif kind < 0.65:   # whole image from the origin (the common case; u' < 0 near the left/top edge)
        sx0, sy0, sx1, sy1 = 0, 0, rng.randrange(1, 2048), rng.randrange(1, 2048)
    else:
        sx0, sy0 = rng.randrange(4096), rng.randrange(4096)
        sx1, sy1 = sx0 + rng.randrange(1, 4096), sy0 + rng.randrange(1, 4096)
    return sx0, sy0, sx1, sy1


@cocotb.test()
async def tex_addr(dut):
    await start(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")) + 5)
    nrect = 150 if FULL else 50
    for it in range(nrect):
        bil = it % 2
        wrap = rng.choice([0, 0, 1, 2, 3, 3, 4, 4])
        lgb = rng.randrange(6)
        sub_hi = rng.randrange(2)
        solid = rng.randrange(2)
        rect = sx0, sy0, sx1, sy1 = rand_rect(rng, wrap)
        addr, stride = rng.getrandbits(32) & ~((1 << max(0, lgb - 3)) - 1), rng.randrange(1, 1 << 16)
        for name, val in (("bilinear", bil), ("wrap", wrap), ("sx0", sx0), ("sy0", sy0), ("sx1", sx1), ("sy1", sy1),
                          ("addr", addr), ("stride", stride), ("lg_bits", lgb), ("sub_order", sub_hi),
                          ("border_solid", solid)):
            getattr(dut, f"cfg_{name}_i").value = val
        # v1.6b inputs this bench does not exercise (24-bit, Gaussian, YUV planes): driven to their neutral values
        for name, val in (("b24", 0), ("gauss", 0), ("p0_xs", 0), ("paddr", 0), ("pstride", 0), ("plg", 0),
                          ("pxs", 0), ("pys", 0), ("yuy2", 0)):
            getattr(dut, f"cfg_{name}_i").value = val
        dut.in_dx_i.value, dut.in_dy_i.value, dut.in_plane_i.value = 0, 0, 0
        # draw start: the reciprocals of REPEAT / REFLECT are computed now (in_ready low meanwhile)
        dut.start_i.value = 1
        await RisingEdge(dut.clk_i)
        dut.start_i.value = 0
        pts = []
        frac = [0, 1, 32767, 32768, 32769, 65535]
        n = max(sx1 - sx0, sy1 - sy0)
        for _ in range(150):
            r = rng.random()
            if wrap and r < 0.3:      # anywhere in the wrap range, including far negative
                u = rng.randrange(-(1 << 31), 1 << 31)
                v = rng.randrange(-(1 << 31), 1 << 31)
            elif wrap and r < 0.6:    # around the seams: k periods away from the rect edges
                k = rng.randint(-3, 3)
                u = ((rng.choice([sx0, sx1 - 1, sx1]) + k * (sx1 - sx0)) << 16) + rng.choice(frac)
                v = ((rng.choice([sy0, sy1 - 1, sy1]) + k * (sy1 - sy0)) << 16) + rng.choice(frac)
                u = max(-(1 << 31), min((1 << 31) - 1, u))
                v = max(-(1 << 31), min((1 << 31) - 1, v))
            elif r < 0.8:
                u = (rng.choice([sx0, sx1 - 1, sx1, max(sx0 - 1, 0), min(sx1 + 1, 8191)]) << 16) + rng.choice(frac)
                v = (rng.choice([sy0, sy1 - 1, sy1, max(sy0 - 1, 0), min(sy1 + 1, 8191)]) << 16) + rng.choice(frac)
            else:
                u = (rng.randrange(sx0, sx1) << 16) + rng.randrange(65536)
                v = (rng.randrange(sy0, sy1) << 16) + rng.randrange(65536)
            if not wrap:
                u, v = min(u, (1 << 29) - 1), min(v, (1 << 29) - 1)
            pts.append((int(rng.random() < 0.95), u, v))
        del n

        def drive(d, p):
            ok, u, v, tag = p
            d.in_ok_i.value, d.in_u_i.value, d.in_v_i.value, d.in_tag_i.value = ok, u & 0xFFFF_FFFF, v & 0xFFFF_FFFF, tag

        def sample(d):
            a = int(d.out_addr_o.value)
            sh = int(d.out_sh_o.value)
            return (int(d.out_pass_o.value), int(d.out_solid_o.value), [(a >> (32 * i)) & 0xFFFF_FFFF for i in range(4)],
                    int(d.out_ypar_o.value), int(d.out_fx_o.value), int(d.out_fy_o.value),
                    int(d.out_border_o.value), [(sh >> (3 * i)) & 7 for i in range(4)], int(d.out_tag_o.value))

        vecs = [(ok, u, v, i) for i, (ok, u, v) in enumerate(pts)]
        outs = await run_stream(dut, vecs, drive, sample, rng, 0.85, 0.8)
        for (ok, u, v), (ps, so, a, yp, fx, fy, bd, sh, tag), i in zip(pts, outs, range(len(pts))):
            assert tag == i
            exp = tex_ref(u, v, bil, wrap, rect, addr, stride, lgb, sub_hi, solid) if ok else None
            ctx = f"bil {bil} wrap {wrap} solid {solid} lgb {lgb} sub {sub_hi} rect {rect} u {u:#x} v {v:#x}"
            assert ps == int(exp is not None), f"pass: {ctx} ok {ok}"
            if exp is not None:
                got = (so, a, yp, fx, fy, bd, sh)
                assert got == exp, f"{ctx}: got {got}, expected {exp}"


# ------------------------------------------------------------------------------------------------
# g2d_texel
# ------------------------------------------------------------------------------------------------

def texel_ref(cfg, tex, border, fx, fy):
    """(S, A == 0 before IMG_MUL)."""
    fmt, opaque, ckey, premul, img_mul, paint, bcol = cfg
    keys = [(bool(ckey[2 * n] >> 31), ckey[2 * n] & 0xFFFFFF, ckey[2 * n + 1] & 0xFFFFFF, ckey[2 * n + 1] >> 24)
            for n in range(4)]
    cs = []
    mask = (1 << FMT_BITS[fmt]) - 1
    for k, raw in enumerate(tex):
        if border >> k & 1:
            cs.append(bcol)
            continue
        c = raw if fmt in INDEXED_FMTS else gm.expand(fmt, raw & mask)
        if opaque:
            c |= 0xFF << 24
        c = gm.color_key(c, keys)
        if premul:
            c = gm.premultiply(c)
        cs.append(c)
    s = gm.bilinear(cs, fx, fy)
    return (gm.multiply_color(s, paint) if img_mul else s), int(s >> 24 == 0)


def rand_keys(rng, sample_rgb):
    words = []
    nkeys = rng.choice([0, 1, 2, 4])
    for n in range(4):
        en = n < nkeys
        base = rng.choice(sample_rgb) if sample_rgb and rng.random() < 0.8 else rng.getrandbits(24)
        lo, hi = 0, 0
        for sh in (0, 8, 16):
            c = base >> sh & 255
            kind = rng.random()
            if kind < 0.15:
                l, h = c, c                     # exact boundary
            elif kind < 0.25:
                l, h = c + 1, c                 # lo > hi: never matches (unless wrapped)
                l = min(l, 255)
            else:
                l, h = max(0, c - rng.randrange(40)), min(255, c + rng.randrange(40))
            lo |= l << sh
            hi |= h << sh
        words += [(int(en) << 31) | lo, (rng.randrange(256) << 24) | hi]
    return words


@cocotb.test()
async def texel(dut):
    await start(dut)
    dut.cfg_gauss_i.value, dut.in_grp_i.value, dut.in_last_i.value = 0, 0, 1   # v1.6b: one group per pixel
    dut.cfg_w0_i.value, dut.cfg_w1_i.value, dut.cfg_w2_i.value = 0, 0, 0
    dut.cfg_yuv_i.value, dut.cfg_yuv709_i.value = 0, 0                         # v1.6b: no YUV, colour unit off
    dut.cfg_color_en_i.value, dut.cfg_gamma_i.value, dut.cfg_cm_i.value, dut.cfg_cb_i.value = 0, 0, 0, 0
    rng = random.Random(int(os.environ.get("SEED", "1")) + 9)
    reps = 6 if FULL else 2
    for fmt in FMT.values():
        for _ in range(reps):
            for opaque in (0, 1):
                # texel values from a small palette so colour keys hit and neighbours repeat; the texel unit
                # gets sub-byte texels right-aligned and indexed ones as their CLUT word
                bits = FMT_BITS[fmt]
                palette = [rng.getrandbits(bits if fmt not in INDEXED_FMTS else 32) for _ in range(6)]
                palette[0] &= 0x00FFFFFF   # an A = 0 texel (indexed / 32-bit formats)
                sample_rgb = [(p if fmt in INDEXED_FMTS else gm.expand(fmt, p)) & 0xFFFFFF for p in palette]
                cfg = (fmt, opaque, rand_keys(rng, sample_rgb), rng.randrange(2), rng.randrange(2),
                       rng.getrandbits(32), rng.choice([0, rng.getrandbits(32)]))
                dut.cfg_fmt_i.value = fmt
                dut.cfg_force_opaque_i.value = opaque
                dut.cfg_ckey_i.value = pack(cfg[2])
                dut.cfg_premul_i.value = cfg[3]
                dut.cfg_img_mul_i.value = cfg[4]
                dut.cfg_paint_i.value = cfg[5]
                dut.cfg_border_i.value = cfg[6]
                vecs = []
                for i in range(80):
                    tex = [rng.choice(palette) if rng.random() < 0.7 else rng.getrandbits(bits) for _ in range(4)]
                    fx = rng.choice([0, 255, 128, rng.randrange(256)])
                    fy = rng.choice([0, 255, 128, rng.randrange(256)])
                    border = rng.choice([0, 0, 0, rng.randrange(16)])
                    vecs.append((rng.randrange(2), tex, border, fx, fy, i))

                def drive(d, p):
                    ps, tex, border, fx, fy, tag = p
                    d.in_pass_i.value, d.in_tex_i.value, d.in_border_i.value = ps, pack(tex), border
                    d.in_fx_i.value, d.in_fy_i.value, d.in_tag_i.value = fx, fy, tag

                def sample(d):
                    return (int(d.out_pass_o.value), int(d.out_s_o.value), int(d.out_azero_o.value),
                            int(d.out_tag_o.value))

                outs = await run_stream(dut, vecs, drive, sample, rng, 0.85, 0.8)
                for (ps, tex, border, fx, fy, i), (gp, s, az, tag) in zip(vecs, outs):
                    assert tag == i and gp == ps
                    exp, exp_az = texel_ref(cfg, tex, border, fx, fy)
                    assert (s, az) == (exp, exp_az), (
                        f"fmt 0x{fmt:02x} cfg {cfg[1]} {[hex(k) for k in cfg[2]]} premul {cfg[3]} mul {cfg[4]} "
                        f"paint {cfg[5]:#x} border {border:x}/{cfg[6]:#x} tex {[hex(t) for t in tex]} f ({fx},{fy}): "
                        f"got {s:#010x}/{az} expected {exp:#010x}/{exp_az}")


# Gaussian groups of g2d_tex_seq: texel slots (t00, t10, t01, t11) -> position 3 j + i in the 3 x 3 block
GAUSS_SLOTS = [(0, 1, 3, 4), (2, None, 5, None), (6, 7, None, None), (8, None, None, None)]


@cocotb.test()
async def texel_gauss(dut):
    """v1.6b Gaussian (rendering.md §7.3): four groups per pixel (g2d_tex_seq order), only the last one leaves the
    unit; every format, colour keys, premultiply, border texels, MULTIPLY, random weights incl. W0 = 0 and all-centre,
    texels in the unused slots random (their weight is 0); random in / out stalls."""
    await start(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")) + 61)
    dut.cfg_gauss_i.value = 1
    dut.cfg_yuv_i.value, dut.cfg_yuv709_i.value = 0, 0
    dut.cfg_color_en_i.value, dut.cfg_gamma_i.value, dut.cfg_cm_i.value, dut.cfg_cb_i.value = 0, 0, 0, 0
    for fmt in [f for f in FMT.values() if f not in INDEXED_FMTS]:
        bits = FMT_BITS[fmt]
        palette = [rng.getrandbits(bits) for _ in range(6)]
        sample_rgb = [gm.expand(fmt, p) & 0xFFFFFF for p in palette]
        cfg = (fmt, rng.randrange(2), rand_keys(rng, sample_rgb), rng.randrange(2), rng.randrange(2),
               rng.getrandbits(32), rng.getrandbits(32))
        # signed s3.12 weights (v1.6b decision 2026-10-26: any values, saturated): unity, sharpening, gain, extremes
        w1 = rng.choice([0, 512, -4096, rng.randint(-32768, 32767)])
        w2 = rng.choice([0, 256, rng.randint(-32768, 32767)])
        w0 = rng.choice([4096 - 4 * (w1 + w2), rng.randint(-32768, 32767), -32768, 32767])
        w0 = max(-32768, min(32767, w0))
        for name, val in (("fmt", fmt), ("force_opaque", cfg[1]), ("ckey", pack(cfg[2])), ("premul", cfg[3]),
                          ("img_mul", cfg[4]), ("paint", cfg[5]), ("border", cfg[6]), ("w0", w0 & 0xFFFF), ("w1", w1 & 0xFFFF), ("w2", w2 & 0xFFFF)):
            getattr(dut, f"cfg_{name}_i").value = val
        vecs, pix = [], []
        for i in range(24):
            nine = [rng.choice(palette) if rng.random() < 0.7 else rng.getrandbits(bits) for _ in range(9)]
            bord = [rng.random() < 0.1 for _ in range(9)]
            ps = rng.randrange(2)
            for g, slots in enumerate(GAUSS_SLOTS):
                tex = [nine[k] if k is not None else rng.getrandbits(bits) for k in slots]
                border = sum((1 << n) for n, k in enumerate(slots) if k is not None and bord[k])
                vecs.append((ps, tex, border, g, int(g == 3), i))
            pix.append((ps, nine, bord, i))

        def drive(d, p):
            ps, tex, border, g, last, tag = p
            d.in_pass_i.value, d.in_tex_i.value, d.in_border_i.value = ps, pack(tex), border
            d.in_fx_i.value, d.in_fy_i.value, d.in_tag_i.value = rng.getrandbits(8), rng.getrandbits(8), tag
            d.in_grp_i.value, d.in_last_i.value = g, last

        def sample(d):
            return (int(d.out_pass_o.value), int(d.out_s_o.value), int(d.out_azero_o.value), int(d.out_tag_o.value))

        outs = await run_stream(dut, vecs, drive, sample, rng, 0.85, 0.8, n_out=len(pix))
        keys = [(bool(cfg[2][2 * n] >> 31), cfg[2][2 * n] & 0xFFFFFF, cfg[2][2 * n + 1] & 0xFFFFFF,
                 cfg[2][2 * n + 1] >> 24) for n in range(4)]
        for (ps, nine, bord, i), (gp, s, az, tag) in zip(pix, outs):
            cs = []
            for raw, b in zip(nine, bord):
                c = cfg[6] if b else gm.expand(fmt, raw & ((1 << bits) - 1)) | (0xFF << 24 if cfg[1] else 0)
                if not b:
                    c = gm.color_key(c, keys)
                    c = gm.premultiply(c) if cfg[3] else c
                cs.append(c)
            g = gm.gaussian(cs, w0, w1, w2)
            exp = gm.multiply_color(g, cfg[5]) if cfg[4] else g
            assert tag == i and gp == ps and (s, az) == (exp, int(g >> 24 == 0)), (hex(fmt), w1, w2, hex(s), hex(exp))
    dut.cfg_gauss_i.value = 0


def test_tex_addr():
    import runner
    runner.run("g2d_tex_addr", "test_sampler", params={"TAG_W": TAG_W}, testcase="tex_addr")


def test_texel():
    import runner
    runner.run("g2d_texel", "test_sampler", params={"TAG_W": TAG_W}, testcase=["texel", "texel_gauss"])
