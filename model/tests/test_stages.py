# SPDX-License-Identifier: Apache-2.0
"""Golden model stage functions vs the independent Python reference (dv/common/refimpl.py).

Bit-exact comparisons (rendering.md §12: model and RTL are bit-exact; the reference implements the
same spec independently). Exhaustive where the input space allows, otherwise seeded random.
"""

import os
import random

import numpy as np
import pytest

import g2d_model as gm
import refimpl as ref

SEED = int(os.environ.get("SEED", "1"))
FMTS = [0x00, 0x01, 0x02, 0x12, 0x03, 0x13, 0x23, 0x33, 0x43, 0x53, 0x63, 0x73,
        0x04, 0x14, 0x24, 0x34, 0x05, 0x15, 0x25, 0x35, 0x06, 0x16, 0x26, 0x36]   # targets (and sources)
SRC_ONLY = [0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,               # sub-byte, indexed, A8L8 (v1.6)
            0x80, 0x90, 0x81, 0x91, 0x82, 0x92,                                     # 24-bit (v1.6b)
            0x83, 0xC3, 0x84, 0xC4, 0x94, 0xA4, 0x85, 0x95, 0xA5]                   # YUV (v1.6b)


def test_div255r_exhaustive():
    v = np.arange(255 * 255 + 1, dtype=np.int64)
    expect = np.floor(v / 255.0 + 0.5).astype(np.int64)
    assert np.array_equal(ref.div255r(v), expect)
    assert all(gm.div255r(int(x)) == int(e) for x, e in zip(v[::97], expect[::97]))


@pytest.mark.parametrize("fmt", FMTS)
def test_formats(fmt):
    rng = np.random.default_rng(SEED + fmt)
    nb = gm.fmt_bytes(fmt)
    assert nb == ref.fmt_bytes(fmt)
    raw = np.arange(1 << (8 * nb), dtype=np.uint32) if nb <= 2 else rng.integers(0, 2**32, 1 << 18, dtype=np.uint32)
    assert np.array_equal(gm.expand_many(fmt, raw), ref.expand(fmt, raw))
    argb = np.concatenate([rng.integers(0, 2**32, 1 << 18, dtype=np.uint32),
                           (np.arange(256, dtype=np.uint32) * 0x01010101)])
    assert np.array_equal(gm.compress_many(fmt, argb), ref.compress(fmt, argb))
    if gm.fmt_bits(fmt) < 32:  # §3.4 then §3.3 is idempotent: compress(expand(raw)) == raw
        assert np.array_equal(gm.compress_many(fmt, gm.expand_many(fmt, raw)), raw)


@pytest.mark.parametrize("fmt", FMTS)
def test_dither(fmt):
    """§3.6 model vs reference at every Bayer position on random colours."""
    rng = np.random.default_rng(SEED + 7 * fmt)
    for argb in rng.integers(0, 2**32, 400, dtype=np.uint32):
        for y in range(4):
            for x in range(4):
                want = int(ref.compress(fmt, int(argb), (x + 4 * int(rng.integers(0, 3)), y)))
                assert gm.compress_dither(fmt, int(argb), x, y) == want, (hex(fmt), hex(int(argb)), x, y)


@pytest.mark.parametrize("fmt", SRC_ONLY)
def test_source_only_formats(fmt):
    rng = np.random.default_rng(SEED + fmt)
    clut = [int(v) for v in rng.integers(0, 2**32, 256, dtype=np.uint32)]
    bits = gm.fmt_bits(fmt)
    raws = range(1 << bits) if bits <= 16 else \
        [int(r) & ~(0xFF << sh) | (v << sh) for sh in range(0, bits, 8) for v in range(256)
         for r in rng.integers(0, 1 << bits, 4)]                     # 24-bit (v1.6b): every value of every byte
    for raw in raws:
        assert gm.expand(fmt, raw, clut) == int(ref.expand(fmt, raw, clut)), (hex(fmt), raw)
    assert not gm.fmt_target_ok(fmt) and gm.fmt_bits(fmt) == ref.fmt_bits(fmt)


def test_invalid_formats():
    for f in range(256):
        assert (gm.fmt_bits(f) != 0) == (f in FMTS or f in SRC_ONLY)
        assert gm.fmt_bits(f) == ref.fmt_bits(f)
        assert gm.fmt_target_ok(f) == (f in FMTS)


def test_wrap():
    rng = random.Random(SEED)
    for _ in range(200000):
        mode = rng.randrange(5)
        s0 = rng.randrange(0, 8192)
        s1 = rng.randint(s0 + 1, min(8192, s0 + rng.choice([1, 2, 5, 64, 8192])))
        x = rng.randint(-(2**15), 2**15)
        assert gm.wrap_coord(mode, x, s0, s1) == ref.wrap(mode, x, s0, s1), (mode, x, s0, s1)


@pytest.mark.parametrize("mode", range(16))
def test_blend(mode):
    rng = np.random.default_rng(SEED * 100 + mode)
    s = rng.integers(0, 2**32, 1 << 19, dtype=np.uint32)
    d = rng.integers(0, 2**32, 1 << 19, dtype=np.uint32)
    assert np.array_equal(gm.blend_many(mode, s, d), ref.blend(mode, s, d))
    # Structured: every (S.c, D.c) pair for alphas at the extremes and mid-points
    c = np.arange(256, dtype=np.uint32)
    sc, dc = np.meshgrid(c, c)
    sc, dc = sc.ravel(), dc.ravel()
    for sa in (0, 1, 127, 128, 254, 255):
        for da in (0, 1, 128, 255):
            S = (np.uint32(sa) << 24) | (sc << 16) | (dc << 8) | sc
            D = (np.uint32(da) << 24) | (dc << 16) | (sc << 8) | dc
            assert np.array_equal(gm.blend_many(mode, S, D), ref.blend(mode, S, D)), (sa, da)


# Ideal (real-valued, premultiplied) Porter-Duff / separable formulas of the modes whose rendering.md §5.6 row is
# meant to be a rounded version of them (vg_lite.h comments). The integer rows must stay within 1.5 of these
# for every valid premultiplied input (c <= a); this caught B-6.1 (SCREEN saturating S + D first, off by 255).
IDEAL = {
    1: lambda S, D, SA, DA: S + D * (1 - SA),                      # SRC_OVER
    2: lambda S, D, SA, DA: S * (1 - DA) + D,                      # DST_OVER
    3: lambda S, D, SA, DA: S * DA,                                # SRC_IN
    4: lambda S, D, SA, DA: D * SA,                                # DST_IN
    5: lambda S, D, SA, DA: S * (1 - DA) + D * (1 - SA) + S * D,   # MULTIPLY
    6: lambda S, D, SA, DA: S + D - S * D,                         # SCREEN
    7: lambda S, D, SA, DA: np.minimum(S + D * (1 - SA), S * (1 - DA) + D),   # DARKEN = min(SrcOver, DstOver)
    8: lambda S, D, SA, DA: np.maximum(S + D * (1 - SA), S * (1 - DA) + D),   # LIGHTEN = max(SrcOver, DstOver)
    9: lambda S, D, SA, DA: np.minimum(S + D, 1),                  # ADDITIVE (saturating)
    10: lambda S, D, SA, DA: D * (1 - SA),                         # SUBTRACT
}

# The LVGL modes take a non-premultiplied source and write A = 255 (rendering.md §5.6 codes 11-15; vg_lite.h).
IDEAL_LVGL = {
    11: lambda S, D, SA: S * SA + D * (1 - SA),                               # NORMAL_LVGL
    12: lambda S, D, SA: np.minimum(S + D, 1) * SA + D * (1 - SA),            # ADDITIVE_LVGL
    13: lambda S, D, SA: np.maximum(D - S, 0) * SA + D * (1 - SA),            # SUBTRACT_LVGL
    14: lambda S, D, SA: np.abs(D - S) * SA + D * (1 - SA),                   # DIFFERENCE_LVGL
    15: lambda S, D, SA: S * D * SA + D * (1 - SA),                           # MULTIPLY_LVGL
}


@pytest.mark.parametrize("mode", sorted(IDEAL_LVGL))
def test_lvgl_blend_vs_ideal(mode):
    v = np.arange(0, 256, 5, dtype=np.uint32)
    sa, sc, dc = (x.ravel() for x in np.meshgrid(v, v, v, indexing="ij"))
    S = (sa << 24) | (sc << 16) | (sc << 8) | sc
    D = (np.uint32(255) << 24) | (dc << 16) | (dc << 8) | dc
    out = gm.blend_many(mode, S, D).astype(np.int64)
    ideal = np.clip(IDEAL_LVGL[mode](sc / 255, dc / 255, sa / 255), 0, 1) * 255
    err = np.abs(((out >> 16) & 0xFF) - ideal)
    assert err.max() <= 1.5, err.max()
    assert ((out >> 24) == 255).all()


@pytest.mark.parametrize("mode", sorted(IDEAL))
def test_blend_vs_ideal(mode):
    v = np.arange(0, 256, 3, dtype=np.uint32)
    sa, sc, da, dc = (x.ravel() for x in np.meshgrid(v, v, v, v, indexing="ij"))
    ok = (sc <= sa) & (dc <= da)
    sa, sc, da, dc = sa[ok], sc[ok], da[ok], dc[ok]
    S = (sa << 24) | (sc << 16) | (sc << 8) | sc
    D = (da << 24) | (dc << 16) | (dc << 8) | dc
    out = gm.blend_many(mode, S, D).astype(np.int64)
    f = lambda x, y, xa, ya: np.clip(IDEAL[mode](x / 255, y / 255, xa / 255, ya / 255), 0, 1) * 255  # noqa: E731
    err_c = np.abs(((out >> 16) & 0xFF) - f(sc, dc, sa, da))
    err_a = np.abs((out >> 24) - f(sa, da, sa, da))
    assert err_c.max() <= 1.5 and err_a.max() <= 1.5, (err_c.max(), err_a.max())


def test_coverage_blend_alpha_modes_premul_multiply():
    rng = random.Random(SEED)
    for _ in range(50000):
        d, b, p = rng.getrandbits(32), rng.getrandbits(32), rng.getrandbits(32)
        cov = rng.choice([0, 1, 16, 128, 254, 255, rng.randrange(256)])
        assert gm.coverage_blend(d, b, cov) == int(ref.coverage_blend(d, b, cov))
        mode, val = rng.randrange(3), rng.randrange(256)
        assert gm.apply_global_alpha(d, mode, val) == int(ref.global_alpha(d, mode, val))
        assert gm.premultiply(d) == int(ref.premultiply(d))
        assert gm.multiply_color(d, p) == int(ref.multiply_color(d, p))


def test_color_key():
    rng = random.Random(SEED)
    for _ in range(20000):
        keys = []
        for _k in range(4):
            lo = [rng.randrange(256) for _ in range(3)]
            hi = [min(255, x + rng.choice([0, 1, 10, 255])) if rng.random() < 0.8 else rng.randrange(256) for x in lo]
            keys.append((rng.random() < 0.7, tuple(lo), tuple(hi), rng.randrange(256)))
        c = rng.getrandbits(32)
        if rng.random() < 0.5:  # force a hit on a random key
            k = rng.randrange(4)
            r, g, b = (rng.randint(min(keys[k][1][i], keys[k][2][i]), max(keys[k][1][i], keys[k][2][i])) for i in range(3))
            c = (c & 0xFF000000) | (r << 16) | (g << 8) | b
        gk = [(en, (lo[0] << 16) | (lo[1] << 8) | lo[2], (hi[0] << 16) | (hi[1] << 8) | hi[2], a)
              for en, lo, hi, a in keys]
        assert gm.color_key(c, gk) == ref.color_key(c, keys)


def test_xform_map():
    rng = random.Random(SEED)
    s32 = lambda: rng.randint(-(2**31), 2**31 - 1)  # noqa: E731
    for k in range(40000):
        persp = k % 2 == 1
        if k % 4 < 2:
            xf = [s32() for _ in range(9)]  # full-range garbage (must still match exactly)
        else:  # realistic: scale/rotation within ±4, small perspective, W around 1
            xf = [rng.randint(-(4 << 16), 4 << 16), rng.randint(-(4 << 16), 4 << 16), rng.randint(-(2**30), 2**30),
                  rng.randint(-(4 << 16), 4 << 16), rng.randint(-(4 << 16), 4 << 16), rng.randint(-(2**30), 2**30),
                  rng.randint(-(2**19), 2**19), rng.randint(-(2**19), 2**19), rng.randint(2**28, 2**30)]
        x, y = rng.randint(-100, 8292), rng.randint(-100, 8292)
        assert gm.xform_map(xf, persp, x, y) == ref.xform_map(xf, persp, x, y), (xf, persp, x, y)


def test_bilinear():
    rng = random.Random(SEED)
    for _ in range(50000):
        t = [rng.getrandbits(32) for _ in range(4)]
        fx, fy = rng.randrange(256), rng.randrange(256)
        assert gm.bilinear(t, fx, fy) == ref.bilinear(t, fx, fy)


def _random_polygon(rng, n, lo, hi):
    pts = [(rng.randint(lo, hi), rng.randint(lo, hi)) for _ in range(n)]
    return [(pts[i][0], pts[i][1], pts[(i + 1) % n][0], pts[(i + 1) % n][1]) for i in range(n)]


@pytest.mark.parametrize("aa", [True, False])
@pytest.mark.parametrize("even_odd", [False, True])
def test_coverage_row_vs_literal(aa, even_odd):
    """Model's prefix-sum rasterizer vs the literal per-sample test of §8.3."""
    rng = random.Random(SEED + 10 * aa + even_odd)
    for k in range(120):
        n = rng.choice([3, 4, 5, 8, 16])
        # mostly s.4 coords inside a 6x6 px window, sometimes snapped to coarse grids (more ties)
        grid = rng.choice([1, 1, 2, 8, 16])
        edges = [tuple(grid * (v // grid) for v in e) for e in _random_polygon(rng, n, -16, 6 * 16 + 16)]
        if k % 10 == 0:  # out-of-range coordinates exercise the hardware clamp
            edges.append((-(2**20), 40, 50, 2**21))
            edges.append((50, 2**21, -(2**20), 40))
        cl = [tuple(ref.clamp_coord(v) for v in e) for e in edges]
        for py in range(-1, 8):
            x0, x1 = -1, 8
            got = gm.coverage_row([list(e) for e in edges], py, x0, x1, aa, even_odd)
            want = bytes(ref.pixel_coverage(cl, px, py, aa, even_odd) for px in range(x0, x1))
            assert got == want, (edges, py)


def test_grad_index():
    rng = random.Random(SEED)
    for _ in range(100000):
        p, q = rng.randint(-(2**31), 2**31 - 1), rng.randint(-(2**31), 2**31 - 1)
        r = rng.randint(-(2**47), 2**47 - 1)
        x, y, spread = rng.randint(-10, 8200), rng.randint(-10, 8200), rng.randrange(4)
        want = ref.grad_index(p, q, r, x, y, spread)
        assert gm.grad_index(p, q, r, x, y, spread) == (-1 if want is None else want)


def test_premultiplied_filter_stays_in_alpha_range():
    """OPEN_QUESTIONS Q-API-19 (a), v1.5: OpenVG 1.1 clamps premultiplied colour channels to [0, alpha] before
    interpolation. On our premultiplied path (SRC_PREMUL, rendering.md §5.2 step 4, also for the border texel) every
    texel has c = mul8(c, a) <= a, and the bilinear filter (§7.3) applies one weight set to all four channels with
    monotone rounding, so its result keeps c <= a: the clamp could never change a value there (no hardware needed).
    Exhaustive for the premultiply, random for the filter (incl. the extreme weight / value corners)."""
    for a in range(256):
        for c in range(256):
            assert gm.mul8(c, a) <= a
    rng = random.Random(1)
    corners = [0x00FFFFFF, 0xFFFFFFFF, 0x01FFFFFF, 0xFF000000, 0x80FF00FF, 0x00000000]
    for n in range(200_000):
        tex = [gm.premultiply(rng.choice(corners) if n % 7 == 0 else rng.getrandbits(32)) for _ in range(4)]
        fx, fy = (rng.choice([0, 1, 128, 254, 255]) if n % 5 == 0 else rng.randrange(256) for _ in range(2))
        o = gm.bilinear(tex, fx, fy)
        a = o >> 24
        assert all(((o >> s) & 255) <= a for s in (16, 8, 0)), (hex(o), [hex(t) for t in tex], fx, fy)


def _rand_func(rng):
    return (rng.randint(0, 8) | (rng.randint(0, 16) << 4) | (rng.randint(0, 8) << 9) | (rng.randint(0, 16) << 13) |
            (rng.randint(0, 4) << 18) | (rng.choice(list(range(11)) + [15]) << 21) |
            (rng.choice(list(range(11)) + [15]) << 25))


def test_blend_families_vs_reference():
    """rendering.md §5.7 (v1.6): the model's blend families against the independent reference, random values with
    the 0 / 255 corners, every mode of families 1 and 2 and random BLEND_FUNC words of family 3."""
    rng = random.Random(15)
    corner = [0, 1, 254, 255]
    for n in range(60_000):
        fam = 1 + n % 3
        mode = rng.randint(0, 9 if fam == 1 else 6) if fam != 3 else 0
        func = _rand_func(rng) if fam == 3 else 0
        s, d, c = ((sum((rng.choice(corner) if rng.random() < 0.3 else rng.getrandbits(8)) << (8 * k) for k in range(4)))
                   for _ in range(3))
        assert gm.blend_family_ok(fam, mode, func)
        assert gm.blend_family(fam, mode, func, s, d, c) == ref.blend_family(fam, mode, func, s, d, c), \
            (fam, mode, hex(func), hex(s), hex(d), hex(c))
    assert not gm.blend_family_ok(1, 10, 0) and not gm.blend_family_ok(2, 7, 0)
    assert not gm.blend_family_ok(3, 0, 9) and not gm.blend_family_ok(3, 0, 17 << 4) and not gm.blend_family_ok(3, 0, 5 << 18)
    assert not gm.blend_family_ok(3, 0, 11 << 21) and gm.blend_family_ok(3, 0, (15 << 21) | (15 << 25))


def test_openvg_blends_vs_header_formulas():
    """Family 1 against the vg_lite.h formulas in real numbers (S, D non-premultiplied, SP = S·Sa): the colour within
    1.5 levels where the result alpha is at least 32 (the 8-bit products and the final rounding), alpha within 1."""
    rng = random.Random(16)
    for n in range(40_000):
        mode = n % 10
        s, d = rng.getrandbits(32), rng.getrandbits(32)
        got = gm.blend_family(1, mode, 0, s, d, 0)
        ch = lambda v, k: ((v >> (8 * k)) & 255) / 255.0  # noqa: E731
        sa, da = ch(s, 3), ch(d, 3)
        ra = [sa, sa + da * (1 - sa), sa * (1 - da) + da, sa * da, sa * da] + [sa + da * (1 - sa)] * 4 + [min(1, sa + da)]
        ra = min(1.0, ra[mode])
        assert abs(ch(got, 3) * 255 - ra * 255) <= 1.0, (mode, hex(s), hex(d), hex(got))
        if ra * 255 < 32:
            continue
        for k in range(3):
            sp, dp = ch(s, k) * sa, ch(d, k) * da
            num = [sp, sp + dp * (1 - sa), sp * (1 - da) + dp, sp * da, dp * sa, sp * dp + sp * (1 - da) + dp * (1 - sa),
                   sp + dp - sp * dp, min(sp * da, dp * sa) + sp * (1 - da) + dp * (1 - sa),
                   max(sp * da, dp * sa) + sp * (1 - da) + dp * (1 - sa), sp + dp][mode]
            exp = min(1.0, num / ra) * 255
            assert abs(ch(got, k) * 255 - exp) <= 1.5 + 255 * 2.0 / (ra * 255), (mode, k, hex(s), hex(d), hex(got), exp)
