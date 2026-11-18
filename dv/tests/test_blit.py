# SPDX-License-Identifier: Apache-2.0
"""End-to-end (L3) tests of the general pixel pipeline (g2d_pix) on g2d_top against the golden model
(verification-plan.md BLD and BLT tests, Phase 3). Every test compares the whole memory image, the
error/fence registers, the white-box state and the performance counters (dv/common/g2d_env.py).

  BLD-01/02  solid rects with every blend mode x target format, source/destination global alpha, scissor,
             over random destination contents
  BLD-03     image options: colour keys, FORCE_OPAQUE, SRC_PREMUL, image MULTIPLY, with every blend mode
  BLT-01     translate copies, every source x target format pair
  BLT-02     scale, 90-degree and free rotation, shear; point and bilinear
  BLT-03     perspective warps including w <= 0 regions
  BLT-04     source sub-rects, 1x1 sources, bilinear clamping, scissor
  BLT-05     source and target in one buffer, unaligned addresses/strides
  BLT-06     cache stress: large rotated 4x downscale
  CMD-05     render-to-texture chains without fences (draw ordering, arch.md §5.3)
  v1.4       FMT-01 every source format incl. sub-byte (both SUB_ORDER) and indexed (CLUT) into every target
             format; WRP-01 wrap modes COLOR / PAD / REPEAT / REFLECT x filters x affine / perspective, sub-rect
             tiles, seams; DTH-01 dithered fills and blits; TRN-01 TRANSPARENT; CLT-01 CLUT / ramp reuse and
             reload (kick, GPU write, shared address)
  SYS-P3     random streams of fills, blends, blits under random AXI profiles; soft reset mid-blit
  throughput (informal, assumption A8)
Blit coefficients come from the driver geometry (gm.blit_setup, rendering.md §9.3).
"""

import math
import os
import random

import cocotb

import g2d_model as gm
from axi_mem import Profile
from csr_if import CsrIf
from g2d_cmd import FMT, FMT_BYTES, STATE, CmdBuf, state_word
from g2d_env import Target
from g2d_cmd import INDEXED_FMTS
from g2d_top_util import FMTS, QUICK, SEED, SRC_FMTS, P, fill, make_env, rand_alpha, rand_rect, run


# ------------------------------------------------------------------------------------------------
# BLD
# ------------------------------------------------------------------------------------------------

@cocotb.test()
async def bld01_solid_modes(dut):
    """Every blend mode x every target format: random rect/scissor/colour, source and destination global
    alpha, over random destination contents (new_target randomises the target and its surroundings)."""
    env = await make_env(dut, "bld01")
    rng = env.rng
    for mode in range(16):
        cb = CmdBuf()
        fmts = FMTS if not QUICK else rng.sample(FMTS, 4)
        for i, fmt in enumerate(fmts):
            t = env.new_target(fmt, rng.randint(1, 70), rng.randint(1, 9))
            clip = (rng.randint(0, t.w), rng.randint(0, t.h), rng.randint(0, t.w + 5), rng.randint(0, t.h + 5)) \
                if rng.random() < 0.3 else None
            # the 12 formats cycle through the 9 (source, destination) global-alpha mode pairs
            alpha = (i % 3, rng.choice([0, 255, rng.getrandbits(8)]))
            dalpha = ((i // 3) % 3, rng.choice([0, 255, rng.getrandbits(8)]))
            fill(cb, t, rand_rect(rng, t, margin=4), rng.getrandbits(32), alpha, clip=clip, blend=mode, dalpha=dalpha)
        cb.fence(mode)
        await run(env, cb)


# ------------------------------------------------------------------------------------------------
# blit helpers
# ------------------------------------------------------------------------------------------------

def paint_word(bilinear=False, persp=False, img_mul=False, premul=False, wrap=0, border_solid=False):
    return state_word("PAINT_CTRL", SRC=1, IMG_MUL=int(img_mul), SRC_PREMUL=int(premul), FILTER=int(bilinear),
                      PERSP=int(persp), WRAP=wrap, BORDER_SOLID=int(border_solid))


def blit(cb, t, s, m, rect=None, clip=None, blend=0, bilinear=False, color=0xFFFF_FFFF, alpha=(0, 255),
         dalpha=(0, 255), img_mul=False, premul=False, opaque=False, ckeys=None, wrap=0, border=0,
         transparent=False, sub_order=0, clut=None, dither=False, border_solid=False, path=None, stencil=False,
         gauss=None, planes=None, yuv709=False, uv_swap=False, cunit=None):
    """One blit with its full state, coefficients from the driver geometry (gm.blit_setup, rendering.md
    §9.3). m: 3x3 source -> target matrix. Returns (geometry result code, draw rect); no draw unless GEOM_OK.
    v1.4: wrap (PAINT_CTRL.WRAP; the draw then covers the whole scissor), BORDER_COLOR, TRANSPARENT,
    SUB_ORDER, CLUT_ADDR (indexed sources), target dither. v1.5 (F-1): path = (edge addr, count, bbox, aa,
    even_odd) draws the image as the paint of a DRAW_PATH over that bbox instead of the blit's DRAW_RECT."""
    sx0, sy0, sx1, sy1 = rect if rect is not None else (0, 0, s.w, s.h)
    c = clip if clip is not None else (0, 0, t.w, t.h)
    cl = [max(c[0], 0), max(c[1], 0), min(c[2], t.w), min(c[3], t.h)]
    rc, xf, persp, draw = gm.blit_setup(m, [sx0, sy0, sx1, sy1], cl, bool(wrap))   # wrap: draw = the clip
    if rc != gm.GEOM_OK:
        return rc, None
    cb.target(t.addr, t.w, t.h, t.fmt, t.stride, dither=dither)
    cb.clip(*c)
    cb.source(s.addr, s.w, s.h, s.fmt, s.stride, rect=(sx0, sy0, sx1, sy1), force_opaque=opaque,
              sub_order=sub_order, transparent=transparent, planes=planes, yuv709=yuv709, uv_swap=uv_swap)
    cb.load_state(STATE["BORDER_COLOR"].addr, [border, clut if clut is not None else 0])
    cb.xform([x & 0xFFFF_FFFF for x in xf])
    cb.load_state(STATE["CKEY0_LO"].addr, list(ckeys) if ckeys is not None else [0] * 8)
    cb.load_state(STATE["BLEND"].addr, [blend, state_word("ALPHA_CTRL", SRC_MODE=alpha[0], SRC_VALUE=alpha[1],
                                                          DST_MODE=dalpha[0], DST_VALUE=dalpha[1]),
                                        paint_word(bilinear, persp, img_mul, premul, wrap, border_solid) |
                                        (int(stencil) << 12) | (int(gauss is not None) << 13), color])
    # v1.6b colour unit (rendering.md §5.2.1): cunit = (en, gamma, m[16] s7.8 row-major A R G B, b[4]); else off
    en, gam, m, b = cunit if cunit is not None else (0, 0, [0] * 16, [0] * 4)
    cb.load_state(STATE["COLOR_CTRL"].addr, [en | (gam << 1)] +
                  [(m[2 * k] & 0xFFFF) | ((m[2 * k + 1] & 0xFFFF) << 16) for k in range(8)] + [x & 0xFFFFF for x in b])
    if gauss is not None:                   # v1.6b: Gaussian, gauss = (W0, W1, W2) signed s3.12 (rendering.md §7.3)
        cb.load_state(STATE["GAUSS_W"].addr, [(gauss[1] & 0xFFFF) | ((gauss[2] & 0xFFFF) << 16), gauss[0] & 0xFFFF])
    if path is None:
        cb.draw_rect(*draw)
    else:
        eaddr, count, bbox, aa, eo = path
        cb.load_state(STATE["RAST_CTRL"].addr, [state_word("RAST_CTRL", FILL_RULE=int(eo), AA=int(aa))])
        cb.draw_path(bbox, eaddr, count)
    return rc, draw


def new_clut(env, rng):
    """A 256-entry CLUT (1 KB, 4-byte aligned) with some A = 0 entries."""
    c = env.new_target(FMT["BGRA8888"], 256, 1, randomize=False)
    words = [rng.getrandbits(32) & (0x00FF_FFFF if rng.random() < 0.15 else 0xFFFF_FFFF) for _ in range(256)]
    env.write_mem(c.addr, b"".join(w.to_bytes(4, "little") for w in words))
    return c.addr


def mat_affine(rng, s, t, kind):
    """Source -> target matrix of the given kind, roughly centring the source in the target."""
    cx, cy = s.w / 2, s.h / 2
    tx, ty = t.w / 2 + rng.uniform(-t.w / 4, t.w / 4), t.h / 2 + rng.uniform(-t.h / 4, t.h / 4)
    if kind == "translate":
        ox, oy = rng.randint(-s.w // 2, t.w - s.w // 2), rng.randint(-s.h // 2, t.h - s.h // 2)
        if rng.random() < 0.3:
            ox, oy = ox + rng.choice([0.25, 0.5, 0.75, rng.random()]), oy + rng.random()
        return [[1, 0, ox], [0, 1, oy], [0, 0, 1]]
    a, b, c, d = 1.0, 0.0, 0.0, 1.0
    if kind == "scale_up":
        a, d = rng.uniform(1.1, 4), rng.uniform(1.1, 4)
    elif kind == "scale_down":
        a, d = rng.uniform(0.2, 0.9), rng.uniform(0.2, 0.9)
    elif kind == "rot90":
        k = rng.randrange(1, 4)    # 90, 180, 270 degrees (0 would be a translate)
        a, b, c, d = [(0, -1, 1, 0), (-1, 0, 0, -1), (0, 1, -1, 0)][k - 1]
    elif kind == "rotate":
        th, sc = rng.uniform(0, 2 * math.pi), rng.uniform(0.5, 2)
        a, b, c, d = sc * math.cos(th), -sc * math.sin(th), sc * math.sin(th), sc * math.cos(th)
    elif kind == "shear":
        b, c = rng.uniform(-0.7, 0.7), rng.uniform(-0.3, 0.3)
    return [[a, b, tx - a * cx - b * cy], [c, d, ty - c * cx - d * cy], [0, 0, 1]]


def mat_persp(rng, s, t):
    m = mat_affine(rng, s, t, rng.choice(["rotate", "scale_down", "translate"]))
    g, h = rng.uniform(-1.5, 1.5) / max(s.w, 8), rng.uniform(-1.5, 1.5) / max(s.h, 8)
    # keep w > 0 over most of the source: w = g*x + h*y + 1 at the corners
    m[2] = [g * 0.5, h * 0.5, 1.0]
    return m


KINDS = ["translate", "scale_up", "scale_down", "rot90", "rotate", "shear"]


# ------------------------------------------------------------------------------------------------
# BLT
# ------------------------------------------------------------------------------------------------

@cocotb.test()
async def blt01_copy_formats(dut):
    """Translate-only copies (blend NONE, point and bilinear) for every source x target format pair."""
    env = await make_env(dut, "blt01")
    rng = env.rng
    pairs = [(a, b) for a in FMTS for b in FMTS]
    if QUICK:
        pairs = rng.sample(pairs, 24)
    for i in range(0, len(pairs), 12):
        cb = CmdBuf()
        for sf, tf in pairs[i:i + 12]:
            s = env.new_target(sf, rng.randint(1, 40), rng.randint(1, 8))
            t = env.new_target(tf, rng.randint(1, 48), rng.randint(1, 10))
            blit(cb, t, s, mat_affine(rng, s, t, "translate"), bilinear=rng.random() < 0.3)
        cb.fence(i)
        await run(env, cb)


@cocotb.test()
async def blt02_transforms(dut):
    """Scale up/down, 90-degree rotations, free rotation, shear; point and bilinear; random blend, global
    alpha and formats."""
    env = await make_env(dut, "blt02")
    rng = env.rng
    for kind in KINDS:
        combos = [(f, b) for f in FMTS for b in (False, True)]       # every source format x filter
        if QUICK:
            combos = rng.sample(combos, 4)
        for i in range(0, len(combos), 12):
            cb = CmdBuf()
            for sf, bil in combos[i:i + 12]:
                s = env.new_target(sf, rng.randint(1, 40), rng.randint(1, 24))
                t = env.new_target(rng.choice(FMTS), rng.randint(8, 48), rng.randint(8, 32))
                blit(cb, t, s, mat_affine(rng, s, t, kind), bilinear=bil,
                     blend=rng.choice([0, 0, 1, rng.randint(0, 15)]), alpha=rand_alpha(rng), dalpha=rand_alpha(rng))
            cb.fence(1)
            await run(env, cb)


@cocotb.test()
async def blt03_perspective(dut):
    """Random perspective warps (including regions with w <= 0), point and bilinear."""
    env = await make_env(dut, "blt03")
    rng = env.rng
    combos = [(f, b) for f in FMTS for b in (False, True)]           # every source format x filter
    if QUICK:
        combos = rng.sample(combos, 6)
    for i in range(0, len(combos), 4):
        cb = CmdBuf()
        for sf, bil in combos[i:i + 4]:
            s = env.new_target(sf, rng.randint(2, 48), rng.randint(2, 40))
            t = env.new_target(rng.choice(FMTS), rng.randint(16, 64), rng.randint(16, 48))
            blit(cb, t, s, mat_persp(rng, s, t), bilinear=bil, blend=rng.choice([0, 1, 11]))
        cb.fence(3)
        await run(env, cb)


@cocotb.test()
async def blt04_subrect_clamp(dut):
    """Source sub-rectangles (blit_rect), 1x1 sources and rects, bilinear clamping at the rect edges,
    scissor."""
    env = await make_env(dut, "blt04")
    rng = env.rng
    for _ in range(3 if QUICK else 8):
        cb = CmdBuf()
        for _ in range(5):
            s = env.new_target(rng.choice(FMTS), rng.randint(1, 40), rng.randint(1, 30))
            if rng.random() < 0.3:
                x0, y0 = rng.randrange(s.w), rng.randrange(s.h)
                rect = (x0, y0, x0 + 1, y0 + 1)
            else:
                x0, y0 = rng.randrange(s.w), rng.randrange(s.h)
                rect = (x0, y0, rng.randint(x0 + 1, s.w), rng.randint(y0 + 1, s.h))
            t = env.new_target(rng.choice(FMTS), rng.randint(4, 48), rng.randint(4, 32))
            clip = (rng.randint(0, t.w // 2), rng.randint(0, t.h // 2), rng.randint(t.w // 2, t.w + 4),
                    rng.randint(t.h // 2, t.h + 4)) if rng.random() < 0.5 else None
            blit(cb, t, s, mat_affine(rng, s, t, rng.choice(KINDS)), rect=rect, clip=clip, bilinear=True)
        cb.fence(4)
        await run(env, cb)


@cocotb.test()
async def blt05_same_buffer(dut):
    """Source and target in the same buffer (disjoint rows), unaligned addresses and strides."""
    env = await make_env(dut, "blt05")
    rng = env.rng
    for _ in range(3 if QUICK else 8):
        fmt = rng.choice(FMTS)
        buf = env.new_target(fmt, rng.randint(4, 60), rng.randint(8, 24), pad_max=7)
        half = buf.h // 2
        s = Target(buf.addr, buf.w, half, fmt, buf.stride)
        t = Target(buf.addr + half * buf.stride, buf.w, buf.h - half, fmt, buf.stride)
        cb = CmdBuf()
        blit(cb, t, s, mat_affine(rng, s, t, rng.choice(KINDS)), bilinear=rng.random() < 0.5,
             blend=rng.choice([0, 1, 9]))
        cb.fence(5)
        await run(env, cb)


@cocotb.test()
async def blt06_cache_stress(dut):
    """Large rotations at 4x downscale (cache thrash), random tile walks over big sources."""
    env = await make_env(dut, "blt06")
    rng = env.rng
    for _ in range(1 if QUICK else 3):
        s = env.new_target(rng.choice(FMTS), rng.randint(200, 400), rng.randint(150, 300), randomize=True)
        t = env.new_target(rng.choice(FMTS), rng.randint(60, 100), rng.randint(40, 80))
        th = rng.uniform(0, 2 * math.pi)
        sc = 0.25
        cx, cy = s.w / 2, s.h / 2
        a, b, c, d = sc * math.cos(th), -sc * math.sin(th), sc * math.sin(th), sc * math.cos(th)
        m = [[a, b, t.w / 2 - a * cx - b * cy], [c, d, t.h / 2 - c * cx - d * cy], [0, 0, 1]]
        cb = CmdBuf()
        blit(cb, t, s, m, bilinear=rng.random() < 0.7)
        cb.fence(6)
        await run(env, cb)


@cocotb.test()
async def bld03_image_options(dut):
    """Colour keys (0-4 keys, overlapping ranges, exact bounds, lo > hi), FORCE_OPAQUE, SRC_PREMUL, image
    MULTIPLY with every blend mode, on palette images so the keys hit."""
    env = await make_env(dut, "bld03")
    rng = env.rng
    combos = [(m, p, o, k) for m in (False, True) for p in (False, True) for o in (False, True) for k in (0, 1, 2, 4)]
    if QUICK:
        combos = rng.sample(combos, 6)
    for it, (img_mul, premul, opaque, nk_fixed) in enumerate(combos):
        sf = rng.choice(FMTS)
        s = env.new_target(sf, rng.randint(4, 32), rng.randint(4, 16), randomize=False)
        bpp = FMT_BYTES[sf]
        palette = [rng.getrandbits(8 * bpp) for _ in range(4)]
        data = b"".join(rng.choice(palette).to_bytes(bpp, "little") for _ in range(s.stride * s.h // bpp + 1))
        env.write_mem(s.addr, data[:s.nbytes])
        keys = []
        nk = nk_fixed if nk_fixed != 2 else rng.choice([2, 3])
        for k in range(4):
            rgb = gm.expand(sf, rng.choice(palette)) & 0xFFFFFF
            lo = hi = 0
            for sh in (0, 8, 16):
                v = rgb >> sh & 255
                l, h = (v, v) if rng.random() < 0.3 else (max(0, v - rng.randrange(30)), min(255, v + rng.randrange(30)))
                if rng.random() < 0.1:
                    l, h = h + 1 if h < 255 else h, l
                lo |= l << sh
                hi |= h << sh
            keys += [(int(k < nk) << 31) | lo, (rng.randrange(256) << 24) | hi]
        t = env.new_target(rng.choice(FMTS), rng.randint(8, 48), rng.randint(8, 24))
        cb = CmdBuf()
        blit(cb, t, s, mat_affine(rng, s, t, rng.choice(KINDS)), bilinear=rng.random() < 0.5,
             blend=rng.randint(0, 15), color=rng.getrandbits(32), alpha=rand_alpha(rng), dalpha=rand_alpha(rng),
             img_mul=img_mul, premul=premul, opaque=opaque, ckeys=keys)
        cb.fence(it)
        await run(env, cb)


@cocotb.test()
async def cmd05_render_to_texture(dut):
    """Draw ordering (arch.md §5.3) in one command buffer, no fences in between: fill a texture, blend
    onto it, blit it into a target, blit that target into a third buffer, blend over a just-filled rect.
    Random write-response latency makes a missing drain read stale data."""
    env = await make_env(dut, "cmd05")
    rng = env.rng
    for it in range(3 if QUICK else 8):
        env.axi.profile = Profile.random(rng, max_lat=rng.choice([20, 200]))
        tex = env.new_target(rng.choice(FMTS), rng.randint(4, 32), rng.randint(4, 16))
        t1 = env.new_target(rng.choice(FMTS), rng.randint(8, 48), rng.randint(8, 24))
        t2 = env.new_target(rng.choice(FMTS), rng.randint(8, 48), rng.randint(8, 24))
        cb = CmdBuf()
        fill(cb, tex, (0, 0, tex.w, tex.h), rng.getrandbits(32))
        fill(cb, tex, rand_rect(rng, tex, 2), rng.getrandbits(32), blend=rng.randint(1, 15))
        blit(cb, t1, tex, mat_affine(rng, tex, t1, rng.choice(KINDS)), bilinear=rng.random() < 0.5)
        blit(cb, t2, t1, mat_affine(rng, t1, t2, rng.choice(KINDS)), blend=rng.choice([0, 1]))
        fill(cb, t2, rand_rect(rng, t2, 2), rng.getrandbits(32))
        fill(cb, t2, rand_rect(rng, t2, 2), rng.getrandbits(32), blend=rng.randint(1, 15))
        cb.fence(it)
        await run(env, cb)


@cocotb.test()
async def random_blits(dut):
    """Random streams of fills, blended fills, blits of every kind, NOPs, state noise, fences and rejected
    draws under random AXI profiles, several kicks per round."""
    rng = random.Random(SEED + 77)
    env = await make_env(dut, "random_blits", profile=Profile.random(rng, max_lat=60))
    for rnd in range(4 if QUICK else 12):
        env.axi.profile = Profile.random(rng, max_lat=rng.choice([0, 20, 200]))
        bufs = [env.new_target(rng.choice(FMTS), rng.randint(1, 64), rng.randint(1, 24)) for _ in range(3)]
        for _ in range(rng.randint(1, 4)):
            cb = CmdBuf()
            for _ in range(rng.randint(1, 8)):
                r = rng.random()
                t, s = rng.sample(bufs, 2)
                if r < 0.25:
                    fill(cb, t, rand_rect(rng, t), rng.getrandbits(32), rand_alpha(rng),
                         blend=rng.choice([0, rng.randint(1, 15)]), dalpha=rand_alpha(rng))
                elif r < 0.75:
                    m = mat_persp(rng, s, t) if rng.random() < 0.2 else mat_affine(rng, s, t, rng.choice(KINDS))
                    blit(cb, t, s, m, bilinear=rng.random() < 0.5, blend=rng.choice([0, rng.randint(0, 15)]),
                         color=rng.getrandbits(32), alpha=rand_alpha(rng), dalpha=rand_alpha(rng),
                         img_mul=rng.random() < 0.2, premul=rng.random() < 0.2, opaque=rng.random() < 0.2)
                elif r < 0.85:
                    cb.nop(rng.randint(0, 3), fill=rng.getrandbits(32))
                elif r < 0.97:
                    cb.fence(rng.getrandbits(32), irq=rng.random() < 0.3)
                else:   # misaligned ramp address: BAD_STATE
                    cb.load_state(STATE["GRAD_RAMP_ADDR"].addr, [0x0100_0002])
                    fill(cb, t, rand_rect(rng, t), 0, paint=2)
            if await env.kick(cb) is None:
                break
        await env.wait_done()
        await env.check()
        if CsrIf.field("STATUS", "HALTED", await env.csr.read("STATUS")):
            await env.soft_reset()
            await env.check()


@cocotb.test()
async def soft_reset_mid_blit(dut):
    """CSR-06 for the general pipeline: soft reset during a large rotated blit with slow memory; the GPU
    recovers and the next stream is exact."""
    env = await make_env(dut, "sr_blit", profile=Profile.random(random.Random(SEED + 5), max_lat=100))
    rng = env.rng
    s = env.new_target(FMTS[3], 120, 90)
    t = env.new_target(FMTS[1], 160, 120)
    cb = CmdBuf()
    blit(cb, t, s, mat_affine(rng, s, t, "rotate"), bilinear=True, blend=1)
    await env.kick(cb, to_model=False)
    await env.cycles(rng.randint(500, 3000))
    await env.soft_reset()
    env.mask.append((t.addr, t.addr + t.nbytes - 1))     # partially drawn: excluded from the compare
    await env.check(perf=False)
    t2 = env.new_target(FMTS[5], 50, 30)
    cb = CmdBuf()
    blit(cb, t2, s, mat_affine(rng, s, t2, "rotate"), bilinear=True, blend=1)
    await run(env, cb.fence(7), perf=False)


_last_stalls = {}


def log_cache_stalls(dut):
    """Simulation-only stall-cause counters of the source cache (Q-PERF-2), as deltas since the last call."""
    c = dut.u_dut.u_core.u_pix.u_cache
    names = ["cyc", "lookups", "pffull", "novictim", "mqfull", "headwait", "outstall"]
    try:
        now = {n: int(getattr(c, f"sim_{n}_q").value) for n in names}
    except AttributeError:
        return
    d = {n: now[n] - _last_stalls.get(n, 0) for n in names}
    _last_stalls.update(now)
    dut._log.info("  cache: " + ", ".join(f"{n} {d[n]}" for n in names))


@cocotb.test()
async def blit_throughput(dut):
    """Informal throughput (assumption A8) with an ideal memory and with a fixed 90-clock read/B latency,
    ARGB8888, 256x64 target: a blended solid rect, a translate copy, a rotated bilinear blit and a
    perspective blit. The rate is walked pixels (the draw rectangle, including pixels the source test
    skips) per busy cycle, i.e. the pipeline rate; command fetch and setup are included."""
    env = await make_env(dut, "blit_throughput")
    s = env.new_target(FMT["ARGB8888"], 256, 64, addr=0x1C00_0000, stride=1024)
    t = env.new_target(FMT["ARGB8888"], 256, 64, addr=0x1D00_0000, stride=1024)
    def rect_area(r):
        return max(0, min(r[2], t.w) - max(r[0], 0)) * max(0, min(r[3], t.h) - max(r[1], 0))

    cases = [
        ("blended rect", lambda cb: (fill(cb, t, (0, 0, 256, 64), 0x80402010, blend=1), (0, 0, 256, 64))[1]),
        ("translate copy", lambda cb: blit(cb, t, s, [[1, 0, 0], [0, 1, 0], [0, 0, 1]])[1]),
        ("rotated bilinear", lambda cb: blit(cb, t, s, [[0.9, -0.3, 40], [0.3, 0.9, -20], [0, 0, 1]], bilinear=True)[1]),
        ("perspective", lambda cb: blit(cb, t, s, [[1, 0.1, 0], [0, 1, 0], [0.0008, 0.0004, 1]], bilinear=True)[1]),
    ]
    results = {}
    for prof_name, prof in (("ideal", Profile.ideal()), ("lat90", Profile(r_lat=(90, 90), b_lat=(90, 90)))):
        env.axi.profile = prof
        for name, build in cases:
            await env.csr.write("PERF_CTRL", 0b011)
            cb = CmdBuf()
            walked = rect_area(build(cb))
            await run(env, cb.fence(1), perf=False)
            await env.csr.write("PERF_CTRL", 0b101)
            pix = await env.csr.read("PERF_PIXELS_OUT")
            busy = await env.csr.read("PERF_BUSY_CYCLES")
            miss = await env.csr.read("PERF_SRC_CACHE_MISS")
            drain = await env.csr.read("PERF_DRAIN_CYC")
            results[(prof_name, name)] = walked / busy
            dut._log.info(f"throughput {prof_name:6} {name:17}: {walked} px walked, {pix} written, {busy} busy "
                          f"cycles = {walked / busy:.3f} px/clk, {miss} line misses, {drain} drain cycles")
            log_cache_stalls(dut)
    env.perf_checked = False
    # the latency bound only holds for the default outstanding limits (the dw128 configuration starves
    # the port on purpose: 4 reads, 2 writes, 128-byte bursts)
    default_port = "RD_OUTSTANDING" not in P and "WR_OUTSTANDING" not in P
    for (prof_name, name), r in results.items():
        if prof_name == "ideal" or default_port:
            assert r > (0.85 if prof_name == "ideal" else 0.5), (prof_name, name, r)

# ------------------------------------------------------------------------------------------------
# v1.4
# ------------------------------------------------------------------------------------------------

@cocotb.test()
async def fmt01_v14_sources(dut):
    """Every source format, including sub-byte (both SUB_ORDER) and indexed (random CLUT, point-sampled even
    when BILINEAR is asked for), with random transforms and filters into random targets (new 16/8-bit
    targets included)."""
    env = await make_env(dut, "fmt01")
    rng = env.rng
    combos = [(f, b) for f in SRC_FMTS for b in (False, True)]
    if QUICK:
        combos = rng.sample(combos, 12)
    for i in range(0, len(combos), 6):
        cb = CmdBuf()
        for sf, bil in combos[i:i + 6]:
            s = env.new_target(sf, rng.randint(1, 40), rng.randint(1, 20))
            t = env.new_target(rng.choice(FMTS), rng.randint(4, 48), rng.randint(4, 24))
            clut = new_clut(env, rng) if sf in INDEXED_FMTS else None
            blit(cb, t, s, mat_affine(rng, s, t, rng.choice(KINDS)), bilinear=bil, sub_order=rng.randrange(2),
                 clut=clut, blend=rng.choice([0, 1, rng.randint(0, 15)]), alpha=rand_alpha(rng),
                 img_mul=rng.random() < 0.2, premul=rng.random() < 0.2, opaque=rng.random() < 0.2)
        cb.fence(i)
        await run(env, cb)


@cocotb.test()
async def wrp01_wrap_modes(dut):
    """WRAP COLOR / PAD / REPEAT / REFLECT x POINT / BILINEAR x affine (down-scaled, so many periods and
    seams are in view) / perspective, whole images and sub-rect tiles (blit_rect), 1-pixel periods; COLOR with
    BORDER_SOLID 0 / 1 (§7.5), with MULTIPLY and TRANSPARENT."""
    env = await make_env(dut, "wrp01")
    rng = env.rng
    combos = [(w, b, k) for w in (1, 2, 3, 4) for b in (False, True) for k in ("scale_down", "rotate", "persp")]
    if QUICK:
        combos = rng.sample(combos, 8)
    for i in range(0, len(combos), 4):
        cb = CmdBuf()
        for wrap, bil, kind in combos[i:i + 4]:
            sf = rng.choice(SRC_FMTS)
            s = env.new_target(sf, rng.choice([1, 2, 3, rng.randint(1, 24)]), rng.choice([1, 2, rng.randint(1, 16)]))
            t = env.new_target(rng.choice(FMTS), rng.randint(8, 48), rng.randint(8, 32))
            if rng.random() < 0.4:
                x0, y0 = rng.randrange(s.w), rng.randrange(s.h)
                rect = (x0, y0, rng.randint(x0 + 1, s.w), rng.randint(y0 + 1, s.h))
            else:
                rect = None
            if kind == "persp":
                m = mat_persp(rng, s, t)
            else:
                m = mat_affine(rng, s, t, kind)
                if kind == "scale_down":
                    sc = rng.uniform(0.15, 0.6)
                    m = [[m[0][0] * sc, m[0][1] * sc, m[0][2]], [m[1][0] * sc, m[1][1] * sc, m[1][2]], [0, 0, 1]]
            clut = new_clut(env, rng) if sf in INDEXED_FMTS else None
            blit(cb, t, s, m, rect=rect, bilinear=bil, wrap=wrap, border=rng.getrandbits(32), clut=clut,
                 sub_order=rng.randrange(2), blend=rng.choice([0, 1]), premul=rng.random() < 0.2,
                 border_solid=rng.random() < 0.5, img_mul=rng.random() < 0.3, color=rng.getrandbits(32),
                 transparent=rng.random() < 0.3)
        cb.fence(i)
        await run(env, cb)


@cocotb.test()
async def wrp02_color_border_modes(dut):
    """WRAP = COLOR, every BORDER_SOLID x filter x IMG_MUL combination (rendering.md §7.5), down-scaled so the
    border is in view, random border colour and transparency."""
    env = await make_env(dut, "wrp02")
    rng = env.rng
    cb = CmdBuf()
    for solid in (False, True):
        for bil in (False, True):
            for mul in (False, True):
                s = env.new_target(rng.choice(SRC_FMTS), rng.randint(2, 12), rng.randint(2, 10))
                t = env.new_target(rng.choice(FMTS), rng.randint(16, 40), rng.randint(12, 24))
                m = mat_affine(rng, s, t, "rotate")
                clut = new_clut(env, rng) if s.fmt in INDEXED_FMTS else None
                blit(cb, t, s, m, bilinear=bil, wrap=1, border=rng.getrandbits(32), border_solid=solid,
                     img_mul=mul, color=rng.getrandbits(32), clut=clut, transparent=rng.random() < 0.3,
                     blend=rng.choice([0, 1]), premul=rng.random() < 0.5)    # v1.5: premultiplied border (Q-API-19 b)
    cb.fence(1)
    await run(env, cb)


@cocotb.test()
async def dth01_trn01_dither_transparent(dut):
    """Dithered solid fills (they leave the fill fast path) and blits into every target format, blended and
    not; TRANSPARENT blits whose sources hold A = 0 texels (32-bit, 5551, A-only and indexed sources)."""
    env = await make_env(dut, "dth01")
    rng = env.rng
    fmts = FMTS if not QUICK else rng.sample(FMTS, 8)
    for i, tf in enumerate(fmts):
        cb = CmdBuf()
        t = env.new_target(tf, rng.randint(4, 40), rng.randint(4, 12))
        fill(cb, t, rand_rect(rng, t, 2), rng.getrandbits(32), dither=True, blend=rng.choice([0, 0, 1]))
        s = env.new_target(rng.choice(SRC_FMTS), rng.randint(2, 24), rng.randint(2, 12))
        clut = new_clut(env, rng) if s.fmt in INDEXED_FMTS else None
        blit(cb, t, s, mat_affine(rng, s, t, rng.choice(KINDS)), bilinear=rng.random() < 0.5, dither=True,
             blend=rng.choice([0, 1, 11]), clut=clut)
        cb.fence(i)
        await run(env, cb)
    for i in range(3 if QUICK else 8):
        cb = CmdBuf()
        sf = [FMT["BGRA8888"], FMT["I8"], FMT["A4"], FMT["BGRA5551"], FMT["A8"], FMT["ARGB8888"], FMT["A1"],
              FMT["I2"], FMT["BGRA4444"]][i]
        s = env.new_target(sf, rng.randint(2, 24), rng.randint(2, 12))
        t = env.new_target(rng.choice(FMTS), rng.randint(8, 40), rng.randint(8, 16))
        clut = new_clut(env, rng) if sf in INDEXED_FMTS else None
        blit(cb, t, s, mat_affine(rng, s, t, rng.choice(KINDS)), bilinear=rng.random() < 0.5, transparent=True,
             blend=rng.choice([0, 0, 1]), clut=clut, img_mul=rng.random() < 0.3, color=rng.getrandbits(32),
             wrap=rng.choice([0, 0, 1, 3]), sub_order=1 if sf in INDEXED_FMTS else i % 2)
        cb.fence(100 + i)
        await run(env, cb)


@cocotb.test()
async def clt01_clut_reuse(dut):
    """The CLUT shares the gradient ramp RAM and its reuse rule (commands.md §3): the same address in one
    buffer reuses the loaded table (also between a gradient and an indexed draw), a new kick or a GPU write
    reloads it. The CPU rewrites the table between kicks; a draw writes over another table in one buffer."""
    env = await make_env(dut, "clt01")
    rng = env.rng
    for it in range(2 if QUICK else 5):
        s = env.new_target(rng.choice(INDEXED_FMTS), rng.randint(4, 20), rng.randint(4, 10))
        t = env.new_target(rng.choice(FMTS), rng.randint(8, 32), rng.randint(8, 16))
        clut = new_clut(env, rng)
        m = mat_affine(rng, s, t, "translate")
        cb = CmdBuf()
        blit(cb, t, s, m, clut=clut)
        # gradient from the same 1 KB (reuse), then an indexed draw again
        cb.target(t.addr, t.w, t.h, t.fmt, t.stride)
        cb.load_state(STATE["GRAD_P"].addr, [rng.randint(-(4 << 16), 4 << 16) & 0xFFFF_FFFF, 0, 0, clut, 0])
        fill(cb, t, rand_rect(rng, t, 2), 0, paint=2, blend=1)
        blit(cb, t, s, mat_affine(rng, s, t, "translate"), clut=clut, blend=1)
        # a draw that writes over the table (as a BGRA8888 256x1 target), then indexed again: reload
        tbl = Target(clut, 256, 1, FMT["BGRA8888"], 1024)
        fill(cb, tbl, (rng.randrange(256), 0, 256, 1), rng.getrandbits(32))
        blit(cb, t, s, mat_affine(rng, s, t, "translate"), clut=clut)
        cb.fence(it)
        await run(env, cb)
        # CPU rewrites the table; the next kick must see it
        env.write_mem(clut, rng.randbytes(1024))
        cb = CmdBuf()
        blit(cb, t, s, mat_affine(rng, s, t, "translate"), clut=clut)
        cb.fence(50 + it)
        await run(env, cb)


# ------------------------------------------------------------------------------------------------

import pytest  # noqa: E402

CONFIGS = {
    "dw64": (dict(DATA_W=64), {}),
    "dw128": (dict(DATA_W=128, ADDR_W=48, KICK_DEPTH=8, MAX_BURST_BYTES=128, RD_OUTSTANDING=4, WR_OUTSTANDING=2),
              {"G2D_BASE_HI": "5"}),
}


@pytest.mark.parametrize("cfg", list(CONFIGS))
def test_blit(cfg):
    import runner
    params, env = CONFIGS[cfg]
    quick = cfg != "dw64" and os.environ.get("G2D_FULL") != "1"
    runner.run("g2d_top_tb", "test_blit", params=params, tag=cfg, extra_env={**env, "G2D_QUICK": "1" if quick else "0"})


# ------------------------------------------------------------------------------------------------
# v1.6
# ------------------------------------------------------------------------------------------------

def mask_state(cb, addr, stride, w, h, ox, oy, en=True):
    """MASK_ADDR .. MASK_CTRL (rendering.md §6.1)."""
    cb.load_state(STATE["MASK_ADDR"].addr, [addr, stride, state_word("MASK_SIZE", W=w, H=h),
                                            (ox & 0xFFFF) | ((oy & 0xFFFF) << 16), int(en)])


@cocotb.test()
async def msk01_mask_layers(dut):
    """v1.6 mask layer (rendering.md §6.1): random A8 masks (random bytes, all-0 / all-255 runs, any stride and byte
    address), placed at random origins (also partly or wholly outside the target, negative), on blended and NONE
    fills (no fast path with a mask), blits (affine / perspective, wrap modes), paths (AA, image paint, gradients);
    the mask switched on and off between draws."""
    from test_path import bbox_of, draw_path, grad_coeffs, grad_state, rand_poly, rand_ramp, ramp_bytes
    from g2d_cmd import edges_to_bytes
    env = await make_env(dut, "msk01")
    rng = env.rng
    for it in range(6 if QUICK else 16):
        cb = CmdBuf()
        t = env.new_target(rng.choice(FMTS), rng.randint(8, 64), rng.randint(6, 40))
        mw, mh = rng.randint(1, 70), rng.randint(1, 48)
        stride = mw + rng.choice([0, 1, 7, 64])
        maddr = env.place(bytes(rng.choice([0, 255, rng.getrandbits(8)]) if rng.random() < 0.3 else rng.getrandbits(8)
                                for _ in range(stride * mh)) + bytes(64))
        maddr += rng.randrange(3)              # any byte address (the bytes after the buffer belong to it)
        ox, oy = rng.randint(-mw, t.w), rng.randint(-mh, t.h)
        for k in range(3):
            mask_state(cb, maddr, stride, mw, mh, ox, oy, en=rng.random() < 0.85)
            kind = rng.randrange(4)
            if kind == 0:
                fill(cb, t, rand_rect(rng, t, 3), rng.getrandbits(32), rand_alpha(rng), blend=rng.choice([0, 1, 9, 11]))
            elif kind == 1:
                s = env.new_target(rng.choice(SRC_FMTS), rng.randint(1, 24), rng.randint(1, 16))
                clut = new_clut(env, rng) if s.fmt in INDEXED_FMTS else None
                m = mat_persp(rng, s, t) if rng.random() < 0.3 else mat_affine(rng, s, t, rng.choice(KINDS))
                blit(cb, t, s, m, bilinear=rng.random() < 0.5, blend=rng.choice([0, 1]), wrap=rng.choice([0, 0, 1, 3]),
                     border=rng.getrandbits(32), clut=clut, img_mul=rng.random() < 0.3, color=rng.getrandbits(32))
            elif kind == 2:
                paint = 0
                if rng.random() < 0.4:
                    ramp = env.place(ramp_bytes(rand_ramp(rng)))
                    paint = grad_state(cb, *grad_coeffs(rng, t, "other", 40), ramp, rng.randrange(4))
                draw_path(env, cb, t, rand_poly(rng, t.w / 2, t.h / 2, t.w * 0.6, rng.randint(3, 9)),
                          aa=rng.random() < 0.7, blend=rng.choice([0, 1]), paint=paint, color=rng.getrandbits(32))
            else:
                s = env.new_target(rng.choice(FMTS), rng.randint(2, 16), rng.randint(2, 12))
                edges = rand_poly(rng, t.w / 2, t.h / 2, t.w * 0.6, rng.randint(3, 9))
                blit(cb, t, s, mat_affine(rng, s, t, "rotate"), bilinear=True, wrap=2, blend=1,
                     path=(env.place(edges_to_bytes(edges)), len(edges), bbox_of(edges), True, False))
        mask_state(cb, 0, 0, 0, 0, 0, 0, en=False)
        cb.fence(it)
        await run(env, cb)


@cocotb.test()
async def msk02_mask_cov(dut):
    """Directed cg_mask bins: every draw kind (fill, image, gradient rect, path) with the mask inside, partly
    outside and wholly outside the target (the last draws nothing)."""
    from test_path import draw_path, grad_coeffs, grad_state, rand_poly, rand_ramp, ramp_bytes
    env = await make_env(dut, "msk02")
    rng = env.rng
    t = env.new_target(FMT["ARGB8888"], 32, 16)
    mw, mh = 12, 8
    maddr = env.place(bytes(rng.getrandbits(8) for _ in range(mw * mh)))
    ramp = env.place(ramp_bytes(rand_ramp(rng)))
    s = env.new_target(FMT["ARGB8888"], 8, 8)
    for ox, oy in ((4, 4), (26, -3), (40, 2)):        # inside, partial, outside
        for kind in ("fill", "image", "grad", "path"):
            cb = CmdBuf()
            mask_state(cb, maddr, mw, mw, mh, ox, oy)
            if kind == "fill":
                fill(cb, t, (0, 0, 32, 16), rng.getrandbits(32), blend=0)
            elif kind == "image":
                blit(cb, t, s, mat_affine(rng, s, t, "scale_up"), blend=1)
            elif kind == "grad":
                paint = grad_state(cb, *grad_coeffs(rng, t, "0", 20), ramp, 0)
                fill(cb, t, (0, 0, 32, 16), 0, blend=1, paint=paint)
            else:
                draw_path(env, cb, t, rand_poly(rng, 16, 8, 14, 6), color=rng.getrandbits(32), blend=1)
            mask_state(cb, 0, 0, 0, 0, 0, 0, en=False)
            await run(env, cb.fence(1))


def rand_bfunc(rng):
    return (rng.randint(0, 8) | (rng.randint(0, 16) << 4) | (rng.randint(0, 8) << 9) | (rng.randint(0, 16) << 13) |
            (rng.randint(0, 4) << 18) | (rng.choice(list(range(11)) + [15]) << 21) | (rng.choice(list(range(11)) + [15]) << 25))


@cocotb.test()
async def bld04_blend_families(dut):
    """v1.6 blend families (rendering.md §5.7): OpenVG 0-9, SVG2 0-6 and random Porter-Duff BLEND_FUNC words on solid
    fills (never the fast path), blits and AA paths, every target format, random global alpha and destinations."""
    from test_path import draw_path, rand_poly
    env = await make_env(dut, "bld04")
    rng = env.rng
    cases = [(1, m) for m in range(10)] + [(2, m) for m in range(7)] + [(3, 0)] * 10
    for i, (fam, mode) in enumerate(cases):
        cb = CmdBuf()
        func = (rand_bfunc(rng) & ~(7 << 18)) | ((i % 5) << 18)          # every equation (cg_family)
        cb.load_state(STATE["BLEND_FUNC"].addr, [func if fam == 3 else rng.getrandbits(29)])
        blend = mode | (fam << 4)
        t = env.new_target(rng.choice(FMTS), rng.randint(4, 40), rng.randint(2, 20))
        k = i % 3
        if k == 0:
            fill(cb, t, rand_rect(rng, t, 3), rng.getrandbits(32), rand_alpha(rng), blend=blend, dalpha=rand_alpha(rng))
        elif k == 1:
            s = env.new_target(rng.choice(FMTS), rng.randint(1, 16), rng.randint(1, 12))
            blit(cb, t, s, mat_affine(rng, s, t, rng.choice(KINDS)), bilinear=rng.random() < 0.5, blend=blend,
                 color=rng.getrandbits(32), img_mul=rng.random() < 0.3, alpha=rand_alpha(rng))
        else:
            draw_path(env, cb, t, rand_poly(rng, t.w / 2, t.h / 2, t.w * 0.6, rng.randint(3, 9)), aa=True,
                      color=rng.getrandbits(32), blend=blend, alpha=rand_alpha(rng))
        await run(env, cb.fence(i))


@cocotb.test()
async def flp01_mirror(dut):
    """v1.6 TGT_FMT.FLIP_Y (rendering.md §5.8): fast-path and blended fills, blits (also tiled: rotation), AA paths
    with image paint, dither, every target format, odd strides; the same buffers drawn normally in between."""
    from test_path import bbox_of, draw_path, rand_poly
    from g2d_cmd import edges_to_bytes
    env = await make_env(dut, "flp01")
    rng = env.rng
    for it in range(4 if QUICK else 10):
        cb = CmdBuf()
        cb.flip_y = it % 3 != 2
        t = env.new_target(rng.choice(FMTS), rng.randint(1, 48), rng.randint(1, 32))
        for k in range(4):
            kind = (it + k) % 4
            if kind == 0:
                fill(cb, t, rand_rect(rng, t, 3), rng.getrandbits(32), blend=rng.choice([0, 0, 1]),
                     dither=rng.random() < 0.3)
            elif kind == 1:
                s = env.new_target(rng.choice(SRC_FMTS), rng.randint(1, 16), rng.randint(1, 12))
                clut = new_clut(env, rng) if s.fmt in INDEXED_FMTS else None
                blit(cb, t, s, mat_affine(rng, s, t, rng.choice(KINDS)), bilinear=rng.random() < 0.5, clut=clut,
                     blend=rng.choice([0, 1]))
            elif kind == 2:
                draw_path(env, cb, t, rand_poly(rng, t.w / 2, t.h / 2, t.w * 0.6, rng.randint(3, 9)), aa=True,
                          color=rng.getrandbits(32), blend=1)
            else:
                s = env.new_target(rng.choice(FMTS), rng.randint(2, 12), rng.randint(2, 8))
                edges = rand_poly(rng, t.w / 2, t.h / 2, t.w * 0.6, rng.randint(3, 9))
                blit(cb, t, s, mat_affine(rng, s, t, "rotate"), bilinear=True, wrap=3, blend=1,
                     path=(env.place(edges_to_bytes(edges)), len(edges), bbox_of(edges), True, False))
        cb.fence(it)
        await run(env, cb)


@cocotb.test()
async def stn01_stencil(dut):
    """v1.6 stencil image mode (rendering.md §5.3.1): every source format as the stencil of a random paint colour,
    every §5.6 blend, POINT / BILINEAR, wrap modes, affine / perspective, also as the paint of AA paths."""
    from test_path import bbox_of, rand_poly
    from g2d_cmd import edges_to_bytes
    env = await make_env(dut, "stn01")
    rng = env.rng
    for mode in range(16):
        cb = CmdBuf()
        for k in range(2):
            sf = rng.choice(SRC_FMTS)
            s = env.new_target(sf, rng.randint(1, 16), rng.randint(1, 12))
            t = env.new_target(rng.choice(FMTS), rng.randint(4, 40), rng.randint(3, 24))
            clut = new_clut(env, rng) if sf in INDEXED_FMTS else None
            m = mat_persp(rng, s, t) if rng.random() < 0.2 else mat_affine(rng, s, t, rng.choice(KINDS))
            path = None
            if k:
                edges = rand_poly(rng, t.w / 2, t.h / 2, t.w * 0.6, rng.randint(3, 8))
                path = (env.place(edges_to_bytes(edges)), len(edges), bbox_of(edges), True, False)
            blit(cb, t, s, m, bilinear=rng.random() < 0.5, blend=mode, color=rng.getrandbits(32), clut=clut,
                 wrap=rng.choice([0, 0, 1, 2]), border=rng.getrandbits(32), premul=rng.random() < 0.3,
                 dalpha=rand_alpha(rng), stencil=True, path=path)
        await run(env, cb.fence(mode))


@cocotb.test()
async def f2401_24bit_sources(dut):
    """v1.6b 24-bit sources (rendering.md §3.2, §3.3): every 24-bit format, source rows starting at every byte offset
    of a 64-byte line (so texels cross lines at offsets 62 / 63 everywhere: the split's high requests), POINT /
    BILINEAR, every wrap mode (seams and line crossings together), affine / perspective, also as image paths."""
    from g2d_cmd import B24_FMTS
    from test_path import bbox_of, rand_poly
    from g2d_cmd import edges_to_bytes
    env = await make_env(dut, "f2401")
    rng = env.rng
    cases = [(f, off) for f in B24_FMTS for off in range(0, 64, 3 if QUICK else 1)]
    rng.shuffle(cases)
    for i in range(0, len(cases), 8):
        cb = CmdBuf()
        for sf, off in cases[i:i + 8]:
            w, h = rng.randint(1, 48), rng.randint(1, 12)
            stride = 3 * w + rng.choice([0, 1, 2, 5, 61, 64])
            base = 0x1000_0000 + (0x0020_0000 * len(env.targets)) % 0x5000_0000   # the env's own placement
            s = env.new_target(sf, w, h, addr=base + 64 * rng.randrange(64) + off, stride=stride)
            t = env.new_target(rng.choice(FMTS), rng.randint(4, 40), rng.randint(3, 20))
            m = mat_persp(rng, s, t) if rng.random() < 0.2 else mat_affine(rng, s, t, rng.choice(KINDS))
            path = None
            if rng.random() < 0.25:
                edges = rand_poly(rng, t.w / 2, t.h / 2, t.w * 0.6, rng.randint(3, 7))
                path = (env.place(edges_to_bytes(edges)), len(edges), bbox_of(edges), True, False)
            blit(cb, t, s, m, bilinear=rng.random() < 0.5, blend=rng.choice([0, 1, rng.randint(0, 15)]),
                 wrap=rng.choice([0, 0, 1, 2, 3, 4]), border=rng.getrandbits(32), premul=rng.random() < 0.2,
                 alpha=rand_alpha(rng), path=path)
        cb.fence(i)
        await run(env, cb)


def rand_gauss_w(rng, kind=None):
    """(W0, W1, W2) signed s3.12 (rendering.md §7.3, any values): kind "centre" (W1 = W2 = 0), "nocentre" (W0 = 0),
    "unity" (a real Gaussian, sum 4096), "sharpen" (negative edges), "gain" (sum != 4096, may saturate), None: any."""
    kind = kind or rng.choice(["centre", "nocentre", "unity", "sharpen", "gain"])
    if kind == "centre":
        return rng.choice([4096, rng.randint(-32768, 32767)]), 0, 0
    if kind == "nocentre":
        w1 = rng.randint(0, 1024)
        return 0, w1, 1024 - w1
    if kind == "unity":
        sig = rng.uniform(0.3, 2.0)
        g = [math.exp(-d * d / (2 * sig * sig)) for d in (0, 1)]
        tot = (g[0] + 2 * g[1]) ** 2
        w1, w2 = round(g[0] * g[1] / tot * 4096), round(g[1] * g[1] / tot * 4096)
        return 4096 - 4 * (w1 + w2), w1, w2
    if kind == "sharpen":
        w1, w2 = -4096 // rng.randint(1, 8), rng.choice([0, -128])
        return 4096 - 4 * (w1 + w2), w1, w2
    return rng.randint(-32768, 32767), rng.randint(-32768, 32767), rng.randint(-32768, 32767)



@cocotb.test()
async def gau01_gaussian(dut):
    """v1.6b Gaussian filter (rendering.md §7.3): random weights (incl. W0 = 0 and all-centre), every source format
    (indexed: point-sampled), every wrap mode (neighbours mapped, COLOR border texels), affine / perspective, image
    paths, MULTIPLY / premultiply / TRANSPARENT, 24-bit sources crossing lines."""
    from test_path import bbox_of, rand_poly
    from g2d_cmd import edges_to_bytes
    env = await make_env(dut, "gau01")
    rng = env.rng
    n = 24 if QUICK else 64
    for i in range(0, n, 8):
        cb = CmdBuf()
        for j in range(i, i + 8):         # the first 15 draws: every wrap x weight class (cg_gauss), as rects
            sf = rng.choice([f for f in SRC_FMTS if f not in INDEXED_FMTS] if j < 25 else SRC_FMTS)
            s = env.new_target(sf, rng.choice([1, 2, 3, rng.randint(1, 32)]), rng.choice([1, 2, rng.randint(1, 16)]))
            t = env.new_target(rng.choice(FMTS), rng.randint(4, 40), rng.randint(3, 20))
            clut = new_clut(env, rng) if sf in INDEXED_FMTS else None
            m = mat_persp(rng, s, t) if rng.random() < 0.2 else mat_affine(rng, s, t, rng.choice(KINDS))
            path = None
            if j >= 25 and rng.random() < 0.25:
                edges = rand_poly(rng, t.w / 2, t.h / 2, t.w * 0.6, rng.randint(3, 7))
                path = (env.place(edges_to_bytes(edges)), len(edges), bbox_of(edges), True, False)
            wrap = j % 5 if j < 25 else rng.choice([0, 0, 1, 2, 3, 4])
            kind = ("centre", "nocentre", "unity", "sharpen", "gain")[(j // 5) % 5] if j < 25 else None
            blit(cb, t, s, m, bilinear=rng.random() < 0.5, blend=rng.choice([0, 1, rng.randint(0, 15)]), clut=clut,
                 wrap=wrap, border=rng.getrandbits(32), premul=rng.random() < 0.3,
                 img_mul=rng.random() < 0.2, transparent=rng.random() < 0.1, alpha=rand_alpha(rng),
                 border_solid=rng.random() < 0.2, gauss=rand_gauss_w(rng, kind), path=path)
        cb.fence(i)
        await run(env, cb)


def new_yuv(env, rng, fmt, w, h):
    """A YUV source (rendering.md §3.7): the Y / packed plane as the source buffer, planes 1 and 2 as A8 buffers
    of the subsampled size (random content, random padding). Returns (source, planes tuple)."""
    from g2d_cmd import YUV_FMTS
    assert fmt in YUV_FMTS
    cls, sub, alpha = fmt & 0xF, (fmt >> 4) & 3, bool(fmt & 0x40)
    xs, ys = int(sub <= 1), int(sub == 0)
    cw, ch = (w + (1 << xs) - 1) >> xs, (h + (1 << ys) - 1) >> ys
    if cls == 3:        # YUY2: plane 0 address and stride multiples of 4 (rendering.md §3.7)
        base = 0x1000_0000 + (0x0020_0000 * len(env.targets)) % 0x5000_0000 + 4 * rng.randrange(4096)
        s = env.new_target(fmt, w, h, addr=base, stride=4 * ((w + 1) // 2) + 4 * rng.randint(0, 16))
    else:
        s = env.new_target(fmt, w, h)
    # the semi-planar U V plane as RGB565 (2-byte aligned address and stride), the others as A8 (any byte)
    p1 = None if cls == 3 else env.new_target(FMT["RGB565"], cw, ch) if cls == 4 else env.new_target(FMT["A8"], cw, ch)
    p2 = None
    if cls == 5:
        p2 = env.new_target(FMT["A8"], cw, ch)
    elif alpha:
        p2 = env.new_target(FMT["A8"], w, h)
    return s, (p1.addr if p1 else rng.getrandbits(32), p1.stride if p1 else rng.getrandbits(16),
               p2.addr if p2 else rng.getrandbits(32), p2.stride if p2 else rng.getrandbits(16))


@cocotb.test()
async def yuv01_yuv_sources(dut):
    """v1.6b YUV sources (rendering.md §3.7): every YUV format x BT.601 / BT.709 x UV_SWAP, odd and even sizes,
    POINT / BILINEAR, every wrap mode (chroma of the mapped luma texel), affine / perspective, image paths,
    MULTIPLY / premultiply / colour keys on the converted colour; unused plane state is random."""
    from g2d_cmd import YUV_FMTS
    from test_path import bbox_of, rand_poly
    from g2d_cmd import edges_to_bytes
    env = await make_env(dut, "yuv01")
    rng = env.rng
    combos = [(f, k) for f in YUV_FMTS for k in range(4)]
    if QUICK:
        combos = rng.sample(combos, 12)
    for i in range(0, len(combos), 6):
        cb = CmdBuf()
        for fmt, k in combos[i:i + 6]:
            s, planes = new_yuv(env, rng, fmt, rng.choice([1, 2, 3, rng.randint(1, 33)]), rng.choice([1, 2, rng.randint(1, 20)]))
            t = env.new_target(rng.choice(FMTS), rng.randint(4, 40), rng.randint(3, 24))
            m = mat_persp(rng, s, t) if rng.random() < 0.2 else mat_affine(rng, s, t, rng.choice(KINDS))
            path = None
            if k == 3 and rng.random() < 0.5:   # image paths only in the last combination (cg_src14 counts rects)
                edges = rand_poly(rng, t.w / 2, t.h / 2, t.w * 0.6, rng.randint(3, 7))
                path = (env.place(edges_to_bytes(edges)), len(edges), bbox_of(edges), True, False)
            blit(cb, t, s, m, bilinear=bool(k & 1), blend=rng.choice([0, 1, rng.randint(0, 15)]),
                 wrap=rng.choice([0, 0, 1, 2, 3, 4]), border=rng.getrandbits(32), premul=rng.random() < 0.2,
                 img_mul=rng.random() < 0.2, alpha=rand_alpha(rng), planes=planes, yuv709=bool(k & 2),
                 uv_swap=rng.random() < 0.5, path=path)
        cb.fence(i)
        await run(env, cb)



def rand_cunit(rng):
    """A colour unit setting: identity, a colour transform (diagonal), a random matrix (saturating), with gamma."""
    k = rng.randrange(4)
    if k == 0:
        m = [256 if i % 5 == 0 else 0 for i in range(16)]
        b = [0] * 4
    elif k == 1:
        m = [rng.randint(-512, 512) if i % 5 == 0 else 0 for i in range(16)]
        b = [rng.randint(-65280, 65280) for _ in range(4)]
    elif k == 2:
        m = [rng.randint(-400, 400) for _ in range(16)]
        b = [rng.randint(-(1 << 19), (1 << 19) - 1) for _ in range(4)]
    else:
        m = [rng.choice([-32768, 32767, rng.randint(-32768, 32767)]) for _ in range(16)]
        b = [rng.choice([-(1 << 19), (1 << 19) - 1, 0]) for _ in range(4)]
    return int(rng.random() < 0.85), rng.choice([0, 0, 1, 2]), m, b


@cocotb.test()
async def col01_color_unit(dut):
    """v1.6b colour unit (rendering.md §5.2.1): gamma both ways, identity / diagonal / random / saturating matrices,
    every source format (24-bit, YUV included), POINT / BILINEAR / GAUSSIAN, MULTIPLY after the unit, TRANSPARENT
    before it, image paths; then a draw with the unit off (state words left over must not matter)."""
    from g2d_cmd import YUV_FMTS
    from test_path import bbox_of, rand_poly
    from g2d_cmd import edges_to_bytes
    env = await make_env(dut, "col01")
    rng = env.rng
    n = 24 if QUICK else 64
    for i in range(0, n, 8):
        cb = CmdBuf()
        for j in range(8):
            yuv = rng.random() < 0.2
            sf = rng.choice(YUV_FMTS if yuv else SRC_FMTS)
            if yuv:
                s, planes = new_yuv(env, rng, sf, rng.randint(1, 24), rng.randint(1, 12))
            else:
                s, planes = env.new_target(sf, rng.randint(1, 24), rng.randint(1, 12)), None
            t = env.new_target(rng.choice(FMTS), rng.randint(4, 40), rng.randint(3, 20))
            clut = new_clut(env, rng) if sf in INDEXED_FMTS else None
            m = mat_persp(rng, s, t) if rng.random() < 0.2 else mat_affine(rng, s, t, rng.choice(KINDS))
            path = None
            if rng.random() < 0.2:
                edges = rand_poly(rng, t.w / 2, t.h / 2, t.w * 0.6, rng.randint(3, 7))
                path = (env.place(edges_to_bytes(edges)), len(edges), bbox_of(edges), True, False)
            blit(cb, t, s, m, bilinear=rng.random() < 0.5, blend=rng.choice([0, 1, rng.randint(0, 15)]), clut=clut,
                 wrap=rng.choice([0, 0, 1, 2, 3, 4]), border=rng.getrandbits(32), premul=rng.random() < 0.3,
                 img_mul=rng.random() < 0.3, transparent=rng.random() < 0.1, alpha=rand_alpha(rng),
                 gauss=rand_gauss_w(rng) if (not yuv and rng.random() < 0.2) else None, planes=planes,
                 cunit=rand_cunit(rng) if j < 7 else None, path=path)
        cb.fence(i)
        await run(env, cb)

