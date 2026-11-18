# SPDX-License-Identifier: Apache-2.0
"""Independent reference implementation of spec/rendering.md in Python/numpy.

Written from the spec text, deliberately NOT from the C++ model, so that agreement between the two is
a real cross-check:
  - integer stage functions: numpy-vectorised (blend, formats) or plain Python ints (xform, gradient)
  - path coverage: the literal per-sample crossing test of §8.3 (the model uses a prefix-sum form)
  - a slow per-pixel executor of the §4 pipeline for small scenes (ref_execute)
It is far too slow for large images; tests keep scenes small.
"""

from __future__ import annotations

import struct

import math

import numpy as np

# ------------------------------------------------------------------------------------------------
# §2 helpers (numpy, uint32 arithmetic)
# ------------------------------------------------------------------------------------------------


def div255r(v):
    v = np.asarray(v, dtype=np.int64)
    return (v + 128 + ((v + 128) >> 8)) >> 8


def mul8(a, b):
    return div255r(np.asarray(a, np.int64) * np.asarray(b, np.int64))


def sat(v):
    return np.clip(v, 0, 255)


def channels(argb):
    argb = np.asarray(argb, dtype=np.int64)
    return (argb >> 24) & 255, (argb >> 16) & 255, (argb >> 8) & 255, argb & 255


def pack(a, r, g, b):
    return ((np.asarray(a, np.int64) << 24) | (np.asarray(r, np.int64) << 16) |
            (np.asarray(g, np.int64) << 8) | np.asarray(b, np.int64)).astype(np.uint32)


# ------------------------------------------------------------------------------------------------
# §3 formats
# ------------------------------------------------------------------------------------------------

# For each FMT: bytes, and for 32-bit formats the byte index of (A, R, G, B); 'x' marks X formats.
_ORDER = {0: ("R", "G", "B", "A"), 1: ("B", "G", "R", "A"), 2: ("A", "R", "G", "B"), 3: ("A", "B", "G", "R")}


# Bits per pixel of every FMT code in the rendering.md §3.2 table (v1.4 rows included).
_BITS = {0x00: 8, 0x01: 8, 0x02: 16, 0x12: 16}
_BITS.update({c: 32 for c in (0x03, 0x13, 0x23, 0x33, 0x43, 0x53, 0x63, 0x73)})
_BITS.update({(o << 4) | cls: bits for cls, bits in ((4, 16), (5, 16), (6, 8)) for o in range(4)})
_BITS.update({0x07: 4, 0x08: 2, 0x09: 1, 0x0A: 4, 0x0B: 8, 0x0C: 4, 0x0D: 2, 0x0E: 1, 0x0F: 16})
_BITS.update({c: 24 for c in (0x80, 0x90, 0x81, 0x91, 0x82, 0x92)})                 # 24-bit (v1.6b)
YUV = (0x83, 0xC3, 0x84, 0xC4, 0x94, 0xA4, 0x85, 0x95, 0xA5)                           # §3.7 (v1.6b)
_BITS.update({c: 16 if c & 0xF == 3 else 8 for c in YUV})                             # Y / packed plane
SOURCE_ONLY = (0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x80, 0x90, 0x81, 0x91, 0x82, 0x92) + YUV
INDEXED = (0x0B, 0x0C, 0x0D, 0x0E)


def fmt_bits(fmt):
    return _BITS.get(fmt, 0)


def fmt_bytes(fmt):
    """Bytes per pixel; 0 for sub-byte and unknown codes."""
    return fmt_bits(fmt) // 8


def _packed_fields(fmt):
    """(name, lsb, width) of the 4444 / 5551 / 2222 classes, channels from bit 0 in the order's names."""
    cls = fmt & 0xF
    cw = {4: 4, 5: 5, 6: 2}[cls]
    aw = 1 if cls == 5 else cw
    out, pos = [], 0
    for n in _ORDER[(fmt >> 4) & 3]:
        w = aw if n == "A" else cw
        out.append((n, pos, w))
        pos += w
    return out


def _replicate(c, n):
    """§3.3: an n-bit value scaled to 8 bits = c * 255 / (2^n - 1) (exact for n in 1, 2, 4; replication for 5, 6)."""
    if n in (1, 2, 4, 8):
        return c * (255 // ((1 << n) - 1))
    return (c << (8 - n)) | (c >> (2 * n - 8))


_YUV_K = ((1192, 1634, 401, 832, 2066), (1192, 1836, 218, 546, 2163))   # §3.7 round(1024 k): BT.601, BT.709


def yuv_expand(ayuv, bt709=False):
    """§3.7: {A, Y, U, V} (A << 24 | Y << 16 | U << 8 | V) -> ARGB32, limited range, ints."""
    k = _YUV_K[int(bool(bt709))]
    y, u, v = ((ayuv >> 16) & 255) - 16, ((ayuv >> 8) & 255) - 128, (ayuv & 255) - 128
    c = lambda t: min(255, max(0, (t + 512) >> 10))  # noqa: E731
    return int(pack(ayuv >> 24, c(k[0] * y + k[1] * v), c(k[0] * y - k[2] * u - k[3] * v), c(k[0] * y + k[4] * u)))


def expand(fmt, raw, clut=None):
    raw = np.asarray(raw, np.int64)
    cls = fmt & 0xF
    if fmt in YUV:
        return np.vectorize(lambda r: yuv_expand(int(r)), otypes=[np.uint32])(raw)
    if fmt & 0x80 and cls <= 2:  # 24-bit (v1.6b): order 0 = R in the lowest field
        bgr = (fmt >> 4) & 1
        if cls == 0:
            lo, mid, hi, a = raw & 255, (raw >> 8) & 255, (raw >> 16) & 255, np.full_like(raw, 255)
        else:
            c = (raw >> 8) & 0xFFFF if cls == 1 else raw & 0xFFFF
            a = raw & 255 if cls == 1 else (raw >> 16) & 255
            lo, mid, hi = _replicate(c & 31, 5), _replicate((c >> 5) & 63, 6), _replicate((c >> 11) & 31, 5)
        return pack(a, hi, mid, lo) if bgr else pack(a, lo, mid, hi)
    if cls == 0:
        return pack(raw & 255, 255, 255, 255)
    if cls == 1:
        return pack(255, raw & 255, raw & 255, raw & 255)
    if cls in (7, 8, 9):  # A4 / A2 / A1
        n = fmt_bits(fmt)
        return pack(_replicate(raw & ((1 << n) - 1), n), 255, 255, 255)
    if cls == 10:  # L4
        l4 = _replicate(raw & 15, 4)
        return pack(255, l4, l4, l4)
    if cls == 15:  # A8L8 (v1.6): A in bits 7:0, L in bits 15:8
        return pack(raw & 255, (raw >> 8) & 255, (raw >> 8) & 255, (raw >> 8) & 255)
    if fmt in INDEXED:
        table = np.asarray(list(clut) + [0] * (256 - len(clut)), np.uint32)
        return table[raw & 255]
    if cls in (4, 5, 6):
        v = {n: _replicate((raw >> lsb) & ((1 << w) - 1), w) for n, lsb, w in _packed_fields(fmt)}
        return pack(v["A"], v["R"], v["G"], v["B"])
    if cls == 2:
        f5 = lambda c: (c << 3) | (c >> 2)  # noqa: E731
        f6 = lambda c: (c << 2) | (c >> 4)  # noqa: E731
        lo, mid, hi = f5(raw & 31), f6((raw >> 5) & 63), f5((raw >> 11) & 31)
        # VG_LITE_RGB565 (order 0) has R in the low bits; BGR565 (order 1) has B in the low bits
        return pack(255, lo, mid, hi) if (fmt >> 4) == 0 else pack(255, hi, mid, lo)
    names = _ORDER[(fmt >> 4) & 3]
    val = {n: (raw >> (8 * i)) & 255 for i, n in enumerate(names)}
    a = np.full_like(raw, 255) if fmt & 0x40 else val["A"]
    return pack(a, val["R"], val["G"], val["B"])


BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))  # rendering.md §3.6


def compress(fmt, argb, dither_xy=None):
    """§3.4; dither_xy = (x, y) applies the §3.6 dither to R, G, B channels of fewer than 8 bits."""
    a, r, g, b = channels(argb)
    cls = fmt & 0xF

    def q(c, n, colour=True):
        if dither_xy is None or not colour:
            return (c * ((1 << n) - 1) + 127) // 255
        d = BAYER[dither_xy[1] & 3][dither_xy[0] & 3]
        return (c * ((1 << n) - 1) * 32 + 255 * (2 * d + 1)) // 8160

    if cls in (4, 5, 6):
        val = {"A": a, "R": r, "G": g, "B": b}
        out = np.zeros_like(a)
        for n, lsb, w in _packed_fields(fmt):
            out |= q(val[n], w, n != "A") << lsb
        return out.astype(np.uint32)
    if cls == 0:
        return a.astype(np.uint32)
    if cls == 1:
        return ((77 * r + 150 * g + 29 * b + 128) >> 8).astype(np.uint32)
    if cls == 2:
        c5 = lambda c: q(c, 5)  # noqa: E731
        c6 = lambda c: q(c, 6)  # noqa: E731
        if (fmt >> 4) == 0:
            return (c5(r) | (c6(g) << 5) | (c5(b) << 11)).astype(np.uint32)
        return (c5(b) | (c6(g) << 5) | (c5(r) << 11)).astype(np.uint32)
    names = _ORDER[(fmt >> 4) & 3]
    val = {"A": np.full_like(a, 255) if fmt & 0x40 else a, "R": r, "G": g, "B": b}
    out = np.zeros_like(a)
    for i, n in enumerate(names):
        out |= val[n] << (8 * i)
    return out.astype(np.uint32)


# ------------------------------------------------------------------------------------------------
# §5 blend: one function per row of the §5.6 table, applied to (colour, alpha) arrays
# ------------------------------------------------------------------------------------------------


def _row(mode, s, d, sa, da):
    inv = lambda x: 255 - x  # noqa: E731
    m = mul8
    if mode == 0:
        return s
    if mode == 1:
        return sat(s + m(d, inv(sa)))
    if mode == 2:
        return sat(m(s, inv(da)) + d)
    if mode == 3:
        return m(s, da)
    if mode == 4:
        return m(d, sa)
    if mode == 5:
        return sat(sat(m(s, inv(da)) + m(s, d)) + m(d, inv(sa)))
    if mode == 6:
        return sat(s + sat(d - m(s, d)))
    if mode == 7:
        return np.minimum(_row(1, s, d, sa, da), _row(2, s, d, sa, da))
    if mode == 8:
        return np.maximum(_row(1, s, d, sa, da), _row(2, s, d, sa, da))
    if mode == 9:
        return sat(s + d)
    if mode == 10:
        return m(d, inv(sa))
    if mode == 11:
        return sat(m(s, sa) + m(d, inv(sa)))
    if mode == 12:
        return sat(m(sat(s + d), sa) + m(d, inv(sa)))
    if mode == 13:
        return sat(m(sat(d - s), sa) + m(d, inv(sa)))
    if mode == 14:
        return sat(m(np.abs(d - s), sa) + m(d, inv(sa)))
    if mode == 15:
        return sat(m(m(s, d), sa) + m(d, inv(sa)))
    raise ValueError(mode)


def blend_stencil(mode, S, D, ac):
    """§5.3.1 (v1.6): each colour channel's row of §5.6 with its own source alpha ac[R, G, B]."""
    sa, sr, sg, sb = channels(S)
    da, dr, dg, db = channels(D)
    r, g, b = (_row(mode, s, d, a, da) for s, d, a in ((sr, dr, ac[0]), (sg, dg, ac[1]), (sb, db, ac[2])))
    a = 255 if 11 <= mode <= 15 else _row(mode, sa, da, sa, da)
    return int(pack(a, r, g, b))


def blend(mode, S, D):
    sa, sr, sg, sb = channels(S)
    da, dr, dg, db = channels(D)
    r, g, b = (_row(mode, s, d, sa, da) for s, d in ((sr, dr), (sg, dg), (sb, db)))
    a = np.full_like(sa, 255) if 11 <= mode <= 15 else _row(mode, sa, da, sa, da)
    return pack(a, r, g, b)


def coverage_blend(D, B, cov):
    cov = np.asarray(cov, np.int64)
    dc, bc = channels(D), channels(B)
    out = [div255r(b * cov + d * (255 - cov)) for d, b in zip(dc, bc)]
    res = pack(*out)
    return np.where(cov == 255, np.asarray(B, np.uint32), res)


def global_alpha(c, mode, value):
    a, r, g, b = channels(c)
    if mode == 1:
        a = np.full_like(a, value)
    elif mode == 2:
        a = mul8(a, value)
    return pack(a, r, g, b)


def premultiply(c):
    a, r, g, b = channels(c)
    return pack(a, mul8(r, a), mul8(g, a), mul8(b, a))


def multiply_color(s, p):
    return pack(*(mul8(x, y) for x, y in zip(channels(s), channels(p))))


def color_key(c, keys):
    """keys: list of (en, (lr,lg,lb), (hr,hg,hb), alpha). Scalar."""
    a, r, g, b = (int(x) for x in channels(c))
    for en, lo, hi, alpha in keys:
        if en and lo[0] <= r <= hi[0] and lo[1] <= g <= hi[1] and lo[2] <= b <= hi[2]:
            return int(pack(alpha, r, g, b))
    return int(c)


# ------------------------------------------------------------------------------------------------
# §7 sampling (Python ints, exact)
# ------------------------------------------------------------------------------------------------


def xform_map(xf, persp, x, y):
    A, B, C, D, E, F, G, H, I = xf
    xh, yh = 2 * x + 1, 2 * y + 1
    U = A * xh + B * yh + 2 * C
    V = D * xh + E * yh + 2 * F
    if not persp:
        return U >> 1, V >> 1
    W = G * xh + H * yh + 2 * I
    if W <= 0:
        return None
    return (U << 30) // W, (V << 30) // W  # Python // is floor division


def wrap(mode, x, s0, s1):
    """§7.5 for one coordinate: the mapped coordinate, or None for a border texel (COLOR outside)."""
    n = s1 - s0
    if mode == 1:
        return x if s0 <= x < s1 else None
    if mode == 3:
        return s0 + (x - s0) % n  # Python % is the mathematical modulo for n > 0
    if mode == 4:
        k = (x - s0) % (2 * n)
        return s0 + k if k < n else s0 + 2 * n - 1 - k
    return min(max(x, s0), s1 - 1)  # PAD and the NONE clamp


def _srgb_to_lin(s):
    return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4


def _lin_to_srgb(v):
    return 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055


# §5.2.1 gamma tables (IEC 61966-2-1), round half up: written here from the formula
GAMMA_LIN = [int(math.floor(255 * _srgb_to_lin(c / 255) + 0.5)) for c in range(256)]
GAMMA_SRGB = [int(math.floor(255 * _lin_to_srgb(c / 255) + 0.5)) for c in range(256)]


def color_apply(c, en, gamma, m, b):
    """§5.2.1 (v1.6b): gamma on R, G, B, then o' = clamp((sum M[o][j] c[j] + B[o] + 128) >> 8); m row-major A R G B."""
    v = [(c >> 24) & 255, (c >> 16) & 255, (c >> 8) & 255, c & 255]
    if gamma in (1, 2):
        tab = GAMMA_LIN if gamma == 1 else GAMMA_SRGB
        v = [v[0]] + [tab[x] for x in v[1:]]
    if en:
        v = [min(255, max(0, (sum(m[4 * o + j] * v[j] for j in range(4)) + b[o] + 128) >> 8)) for o in range(4)]
    return (v[0] << 24) | (v[1] << 16) | (v[2] << 8) | v[3]


def color_state(st):
    """COLOR_CTRL / COLOR_M0..7 / COLOR_B0..3 -> (en, gamma, m[16], b[4]) with signed fields."""
    s16 = lambda x: x - 65536 if x & 0x8000 else x  # noqa: E731
    s20 = lambda x: x - (1 << 20) if x & (1 << 19) else x  # noqa: E731
    m = []
    for o in range(4):
        w0, w1 = st.get(f"COLOR_M{2 * o}", 0), st.get(f"COLOR_M{2 * o + 1}", 0)
        m += [s16(w0 & 0xFFFF), s16(w0 >> 16), s16(w1 & 0xFFFF), s16(w1 >> 16)]
    ctl = st.get("COLOR_CTRL", 0)
    return ctl & 1, (ctl >> 1) & 3, m, [s20(st.get(f"COLOR_B{o}", 0) & 0xFFFFF) for o in range(4)]


def gaussian(t, w0, w1, w2):
    """§7.3 Gaussian (v1.6b): t[3 j + i] = texel (x + i - 1, y + j - 1); signed weights in 1/4096; saturated."""
    w = [w2, w1, w2, w1, w0, w1, w2, w1, w2]
    chans = [channels(int(c)) for c in t]
    return int(pack(*[min(255, max(0, (sum(int(chans[k][ch]) * w[k] for k in range(9)) + 2048) >> 12))
                      for ch in range(4)]))


def bilinear(t, fx, fy):
    w = [(256 - fx) * (256 - fy), fx * (256 - fy), (256 - fx) * fy, fx * fy]
    chans = [channels(int(c)) for c in t]
    out = []
    for ch in range(4):
        s = sum(int(chans[k][ch]) * w[k] for k in range(4))
        out.append((s + 32768) >> 16)
    return int(pack(*out))


# ------------------------------------------------------------------------------------------------
# §8 coverage: literal per-sample test
# ------------------------------------------------------------------------------------------------

Q = [4, 12, 8, 1, 15, 6, 10, 2, 13, 5, 9, 0, 14, 7, 3, 11]  # rendering.md §8.2


def clamp_coord(v):
    lim = (1 << 18) - 1
    return max(-lim, min(lim, v))


def sample_inside(edges, X, Y, even_odd):
    w = 0
    for x0, y0, x1, y1 in edges:
        if y0 == y1:
            continue
        down = y1 > y0
        Xa, Ya, Xb, Yb = (2 * x0, 2 * y0, 2 * x1, 2 * y1) if down else (2 * x1, 2 * y1, 2 * x0, 2 * y0)
        if Ya <= Y < Yb and Xa * (Yb - Ya) + (Y - Ya) * (Xb - Xa) <= X * (Yb - Ya):
            w += 1 if down else -1
    w8 = ((w + 128) % 256) - 128
    return (w8 % 2 != 0) if even_odd else (w8 != 0)


def pixel_coverage(edges, px, py, aa, even_odd):
    if not aa:
        return 255 if sample_inside(edges, 32 * px + 16, 32 * py + 16, even_odd) else 0
    n = sum(sample_inside(edges, 32 * px + 2 * Q[j] + 1, 32 * py + 2 * j + 1, even_odd) for j in range(16))
    return (n * 255 + 8) >> 4


# ------------------------------------------------------------------------------------------------
# §10 gradient
# ------------------------------------------------------------------------------------------------


def grad_index(p, q, r, x, y, spread):
    """Ramp index; None under FILL (spread 3) when outside [0, 255]."""
    return spread_index((p * (2 * x + 1) + q * (2 * y + 1) + r) >> 17, spread)


def radial_index(p, q, r, x, y, spread):
    """§10.4: p, q, r = (T, U, V) coefficients; Python integers, math.isqrt."""
    import math
    f = [p[k] * (2 * x + 1) + q[k] * (2 * y + 1) + r[k] for k in range(3)]
    sat = lambda v: max(-(1 << 31), min((1 << 31) - 1, v))  # noqa: E731
    u, v = sat(f[1] >> 9), sat(f[2] >> 9)
    return spread_index(((f[0] >> 9) + math.isqrt(u * u + v * v)) >> 8, spread)


def spread_index(i, spread):
    if spread == 3:
        return i if 0 <= i <= 255 else None
    if spread == 1:
        return i & 255
    if spread == 2:
        k = i & 511
        return k if k < 256 else 511 - k
    return max(0, min(255, i))


# ------------------------------------------------------------------------------------------------
# Slow reference executor for one draw (§4 pipeline), on a dict-based memory
# ------------------------------------------------------------------------------------------------


class RefMemory:
    def __init__(self):
        self.b = {}

    def write(self, addr, data: bytes):
        for i, v in enumerate(data):
            self.b[addr + i] = v

    def read_le(self, addr, n):
        return sum(self.b.get(addr + i, 0) << (8 * i) for i in range(n))

    def write_le(self, addr, n, v):
        for i in range(n):
            self.b[addr + i] = (v >> (8 * i)) & 255


def _f(word, lsb, width):
    return (word >> lsb) & ((1 << width) - 1)


def ref_draw(mem: RefMemory, st: dict, rect, edges=None):
    """Render one validated draw. st: dict of state word name -> value (as in regs.yaml).
    rect: payload rectangle (x0,y0,x1,y1) already as ints. edges: list of s.4 edges for DRAW_PATH."""
    tw, th = _f(st["TGT_SIZE"], 0, 14), _f(st["TGT_SIZE"], 16, 14)
    tfmt, tstride, taddr = _f(st["TGT_FMT"], 0, 8), _f(st["TGT_STRIDE"], 0, 16), st["TGT_ADDR"]
    tb = fmt_bytes(tfmt)
    cx0, cy0 = _f(st["CLIP_MIN"], 0, 14), _f(st["CLIP_MIN"], 16, 14)
    cx1, cy1 = _f(st["CLIP_MAX"], 0, 14), _f(st["CLIP_MAX"], 16, 14)
    x0, y0 = max(rect[0], cx0, 0), max(rect[1], cy0, 0)
    x1, y1 = min(rect[2], cx1, tw), min(rect[3], cy1, th)
    pc = st["PAINT_CTRL"]
    src, img_mul, premul, bil, persp, spread = (_f(pc, 0, 2), _f(pc, 2, 1), _f(pc, 3, 1), _f(pc, 4, 1),
                                                _f(pc, 5, 1), _f(pc, 6, 2))
    mode, family = _f(st["BLEND"], 0, 4), _f(st["BLEND"], 4, 2)
    ac = st["ALPHA_CTRL"]
    paint = st["PAINT_COLOR"]
    rc = st.get("RAST_CTRL", 2)
    aa, eo = _f(rc, 1, 1), _f(rc, 0, 1)
    if edges is not None:
        edges = [tuple(clamp_coord(v) for v in e) for e in edges]
    keys = []
    for k in range(4):
        lo, hi = st[f"CKEY{k}_LO"], st[f"CKEY{k}_HI"]
        keys.append((_f(lo, 31, 1), (_f(lo, 16, 8), _f(lo, 8, 8), _f(lo, 0, 8)),
                     (_f(hi, 16, 8), _f(hi, 8, 8), _f(hi, 0, 8)), _f(hi, 24, 8)))
    sfmt, sstride, saddr = _f(st["SRC_FMT"], 0, 8), _f(st["SRC_STRIDE"], 0, 16), st["SRC_ADDR"]
    sbits = fmt_bits(sfmt)
    force_opaque, sub_hi, transparent = _f(st["SRC_FMT"], 8, 1), _f(st["SRC_FMT"], 9, 1), _f(st["SRC_FMT"], 10, 1)
    wrap_mode = _f(pc, 8, 3)
    solid = wrap_mode == 1 and _f(pc, 11, 1)        # §7.5 COLOR, BORDER_SOLID
    border = st.get("BORDER_COLOR", 0)
    dither = _f(st["TGT_FMT"], 8, 1)
    clut = [mem.read_le(st.get("CLUT_ADDR", 0) + 4 * i, 4) for i in range(256)] if (src == 1 and sfmt in INDEXED) else None
    if sfmt in INDEXED:
        bil = 0  # §3.5: indexed sources are point-sampled
    gauss = _f(pc, 13, 1) and sfmt not in INDEXED              # §7.3 Gaussian (v1.6b), overrides FILTER
    s16 = lambda v: v - 65536 if v & 0x8000 else v  # noqa: E731
    gw0 = s16(_f(st.get("GAUSS_W0", 0), 0, 16))
    gw1, gw2 = s16(_f(st.get("GAUSS_W", 0), 0, 16)), s16(_f(st.get("GAUSS_W", 0), 16, 16))
    cu = color_state(st)                                       # §5.2.1 colour unit (v1.6b)
    sx0, sy0 = _f(st["SRC_RECT_MIN"], 0, 14), _f(st["SRC_RECT_MIN"], 16, 14)
    sx1, sy1 = _f(st["SRC_RECT_MAX"], 0, 14), _f(st["SRC_RECT_MAX"], 16, 14)
    to_s32 = lambda v: v - (1 << 32) if v & (1 << 31) else v  # noqa: E731
    xf = [to_s32(st[f"XF_{c}"]) for c in "ABCDEFGHI"]
    gp, gq = to_s32(st["GRAD_P"]), to_s32(st["GRAD_Q"])
    r48 = ((st["GRAD_R_HI"] & 0xFFFF) << 32) | st["GRAD_R"]
    gr = r48 - (1 << 48) if r48 & (1 << 47) else r48

    def s48(lo, hi):
        v = ((st.get(hi, 0) & 0xFFFF) << 32) | st.get(lo, 0)
        return v - (1 << 48) if v & (1 << 47) else v
    rad = ([gp, to_s32(st.get("GRAD_UP", 0)), to_s32(st.get("GRAD_VP", 0))],
           [gq, to_s32(st.get("GRAD_UQ", 0)), to_s32(st.get("GRAD_VQ", 0))],
           [gr, s48("GRAD_UR", "GRAD_UR_HI"), s48("GRAD_VR", "GRAD_VR_HI")])

    sfw = st["SRC_FMT"]
    p1, p2 = st.get("SRC_PLANE1_ADDR", 0), st.get("SRC_PLANE2_ADDR", 0)
    s1, s2 = _f(st.get("SRC_PLANE_STRIDE", 0), 0, 16), _f(st.get("SRC_PLANE_STRIDE", 0), 16, 16)

    def yuv_texel(x, y):
        """§3.7: {A, Y, U, V} of texel (x, y) from the planes."""
        cls, sub, alpha = sfmt & 0xF, (sfmt >> 4) & 3, bool(sfmt & 0x40)
        xs, ys = int(sub <= 1), int(sub == 0)
        cx, cy = x >> xs, y >> ys
        if cls == 3:
            w = mem.read_le(saddr + y * sstride + 4 * (x >> 1), 4)
            Y, U, V = (w >> 16) & 255 if x & 1 else w & 255, (w >> 8) & 255, w >> 24
        else:
            Y = mem.read_le(saddr + y * sstride + x, 1)
            if cls == 4:
                U, V = mem.read_le(p1 + cy * s1 + 2 * cx, 1), mem.read_le(p1 + cy * s1 + 2 * cx + 1, 1)
            else:
                U, V = mem.read_le(p1 + cy * s1 + cx, 1), mem.read_le(p2 + cy * s2 + cx, 1)
        A = mem.read_le(p2 + y * s2 + x, 1) if alpha else 255
        if _f(sfw, 12, 1):
            U, V = V, U
        return (A << 24) | (Y << 16) | (U << 8) | V

    def raw_texel(x, y):
        row = saddr + y * sstride
        if sfmt in YUV:
            return yuv_texel(x, y)
        if sbits >= 8:
            return mem.read_le(row + x * (sbits // 8), sbits // 8)
        per = 8 // sbits
        byte = mem.read_le(row + x // per, 1)
        j = x % per
        shift = 8 - sbits * (j + 1) if sub_hi else sbits * j
        return (byte >> shift) & ((1 << sbits) - 1)

    def texel(x, y):
        mode = 0 if solid else wrap_mode
        x, y = wrap(mode, x, sx0, sx1), wrap(mode, y, sy0, sy1)
        if x is None or y is None:
            return int(premultiply(border)) if premul else border    # §5.2 step 4 (v1.5, Q-API-19 b)
        c = yuv_expand(raw_texel(x, y), _f(sfw, 11, 1)) if sfmt in YUV else int(expand(sfmt, raw_texel(x, y), clut))
        if force_opaque:
            c = (c & 0x00FFFFFF) | 0xFF000000
        c = color_key(c, keys)
        if premul:
            c = int(premultiply(c))
        return c

    # §6.1 mask layer (v1.6)
    mask_on = _f(st.get("MASK_CTRL", 0), 0, 1)
    msz, mori = st.get("MASK_SIZE", 0), st.get("MASK_ORIGIN", 0)
    mw, mh = _f(msz, 0, 14), _f(msz, 16, 14)
    mox, moy = _f(mori, 0, 16), _f(mori, 16, 16)
    mox, moy = mox - (1 << 16) if mox & 0x8000 else mox, moy - (1 << 16) if moy & 0x8000 else moy
    maddr, mstride = st.get("MASK_ADDR", 0), _f(st.get("MASK_STRIDE", 0), 0, 16)

    writes = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            cov = pixel_coverage(edges, x, y, aa, eo) if edges is not None else 255
            if cov == 0:
                continue
            if mask_on:
                mx, my = x - mox, y - moy
                m = mem.read_le(maddr + my * mstride + mx, 1) if (0 <= mx < mw and 0 <= my < mh) else 0
                cov = mul8(cov, m)
                if cov == 0:
                    continue
            if src == 1:
                uv = xform_map(xf, persp, x, y)
                if uv is None:
                    continue
                u, v = uv
                if wrap_mode == 0:
                    if not (sx0 <= u >> 16 < sx1 and sy0 <= v >> 16 < sy1):
                        continue
                elif not (-(1 << 31) <= u < (1 << 31) and -(1 << 31) <= v < (1 << 31)):
                    continue
                if solid and not (sx0 <= u >> 16 < sx1 and sy0 <= v >> 16 < sy1):
                    S = border                          # plain colour (§7.5 BORDER_SOLID)
                else:
                    if gauss:
                        tx_, ty_ = u >> 16, v >> 16
                        S = gaussian([texel(tx_ + i - 1, ty_ + j - 1) for j in range(3) for i in range(3)], gw0, gw1, gw2)
                    elif not bil:
                        S = texel(u >> 16, v >> 16)
                    else:
                        up, vp = u - 32768, v - 32768
                        bx, by, fx, fy = up >> 16, vp >> 16, (up >> 8) & 255, (vp >> 8) & 255
                        S = bilinear([texel(bx, by), texel(bx + 1, by), texel(bx, by + 1), texel(bx + 1, by + 1)],
                                     fx, fy)
                    if transparent and (S >> 24) == 0:
                        continue
                    S = color_apply(S, *cu)
                    if img_mul:
                        S = int(multiply_color(S, paint))
            elif src in (2, 3):
                gi = grad_index(gp, gq, gr, x, y, spread) if src == 2 else radial_index(*rad, x, y, spread)
                S = paint if gi is None else mem.read_le(st["GRAD_RAMP_ADDR"] + 4 * gi, 4)
            else:
                S = paint
            stc = None
            if src == 1 and _f(pc, 12, 1):        # §5.3.1 stencil (v1.6): I x P, per-channel alphas
                ia, ir, ig, ib = channels(S)
                pa, pr, pg, pb = channels(paint)
                stc = [mul8(mul8(c, ia), pa) for c in (ir, ig, ib)]
                S = int(pack(mul8(ia, pa), mul8(pr, stc[0]), mul8(pg, stc[1]), mul8(pb, stc[2])))
            S = int(global_alpha(S, _f(ac, 0, 2), _f(ac, 8, 8)))
            ta = taddr + ((th - 1 - y) if _f(st["TGT_FMT"], 9, 1) else y) * tstride + x * tb   # §5.8 FLIP_Y
            D = 0
            if mode != 0 or family != 0 or cov < 255:
                D = int(global_alpha(int(expand(tfmt, mem.read_le(ta, tb))), _f(ac, 2, 2), _f(ac, 16, 8)))
            Bv = (blend_family(family, mode, st.get("BLEND_FUNC", 0), S, D, paint) if family else
                  blend_stencil(mode, S, D, stc) if stc is not None else int(blend(mode, S, D)))
            O = int(coverage_blend(D, Bv, cov))
            writes.append((ta, int(compress(tfmt, O, (x, y) if dither else None))))
    for ta, v in writes:
        mem.write_le(ta, tb, v)
    return len(writes)


def unpack_edges(data: bytes):
    return [struct.unpack_from("<4i", data, 16 * i) for i in range(len(data) // 16)]


# ------------------------------------------------------------------------------------------------
# §5.7 blend families (v1.6), written from rendering.md independently of the C++ model
# ------------------------------------------------------------------------------------------------

def _sadd(a, b):
    return min(255, a + b)


def _ssub(a, b):
    return max(0, a - b)


def _div_ra(n, ra):
    if ra == 0:
        return 0
    if n >= ra:
        return 255
    return (n * 255 + (ra >> 1)) // ra


def _svg_bf(mode, cs, cb):
    return [cs, mul8(cs, cb), _sadd(cs, _ssub(cb, mul8(cs, cb))), max(cs, cb), min(cs, cb), abs(cb - cs),
            _ssub(_ssub(_sadd(cs, cb), mul8(cs, cb)), mul8(cs, cb))][mode]


def _pd_cm(cm, src, cs, cd, as_, ad):
    if cm == 0:
        return mul8(cs, as_) if src else mul8(cd, ad)
    if cm == 15:
        return cs if src else cd
    return [None, cs, cd, _sadd(cs, cd), _ssub(cd, cs), mul8(cs, cd), abs(cd - cs), _sadd(cs, _ssub(cd, mul8(cs, cd))),
            max(cs, cd), min(cs, cd), _ssub(_ssub(_sadd(cs, cd), mul8(cs, cd)), mul8(cs, cd))][cm]


def _pd_fc(f, cs, cd, as_, ad, ac, cc):
    return [0, 255, cs, 255 - cs, as_, 255 - as_, ad, 255 - ad, cd, 255 - cd, min(as_, 255 - ad), ac, 255 - ac, cc,
            255 - cc, _sadd(255 - ad, mul8(ad, cd)), 255 - mul8(ad, cd)][f]


def _pd_fa(f, as_, ad, ac):
    return [0, 255, as_, 255 - as_, ad, 255 - ad, min(as_, 255 - ad), ac, 255 - ac][f]


def _pd_eq(eq, fs, xs, fd, xd):
    if eq == 0:
        return _sadd(mul8(fs, xs), mul8(fd, xd))
    if eq == 1:
        return _ssub(mul8(fs, xs), mul8(fd, xd))
    if eq == 2:
        return _ssub(mul8(fd, xd), mul8(fs, xs))
    return min(xs, xd) if eq == 3 else max(xs, xd)


def blend_family(family, mode, func, s, d, c):
    """ARGB32 ints in and out."""
    ch = lambda v, k: (v >> (8 * k)) & 255  # noqa: E731   k: 0 B, 1 G, 2 R, 3 A
    sa, da = ch(s, 3), ch(d, 3)
    out = [0, 0, 0, 0]
    if family == 3:
        fsa, fsc, fda, fdc = func & 15, (func >> 4) & 31, (func >> 9) & 15, (func >> 13) & 31
        eq, scm, dcm = (func >> 18) & 7, (func >> 21) & 15, (func >> 25) & 15
        for k in range(3):
            cs, cd, cc = ch(s, k), ch(d, k), ch(c, k)
            out[k] = _pd_eq(eq, _pd_fc(fsc, cs, cd, sa, da, ch(c, 3), cc), _pd_cm(scm, True, cs, cd, sa, da),
                            _pd_fc(fdc, cs, cd, sa, da, ch(c, 3), cc), _pd_cm(dcm, False, cs, cd, sa, da))
        out[3] = _pd_eq(eq, _pd_fa(fsa, sa, da, ch(c, 3)), _pd_cm(scm, True, sa, da, sa, da),
                        _pd_fa(fda, sa, da, ch(c, 3)), _pd_cm(dcm, False, sa, da, sa, da))
    else:
        over = _sadd(sa, mul8(da, 255 - sa))
        if family == 2:
            ra = over
        else:
            ra = {0: sa, 2: _sadd(mul8(sa, 255 - da), da), 3: mul8(sa, da), 4: mul8(sa, da), 9: _sadd(sa, da)}.get(mode, over)
        for k in range(3):
            cs, cd = ch(s, k), ch(d, k)
            sp, dp = mul8(cs, sa), mul8(cd, da)
            if family == 2:
                n = _sadd(_sadd(mul8(sp, 255 - da), mul8(dp, 255 - sa)), mul8(mul8(sa, da), _svg_bf(mode, cs, cd)))
            elif mode == 0:
                n = sp
            elif mode == 1:
                n = _sadd(sp, mul8(dp, 255 - sa))
            elif mode == 2:
                n = _sadd(mul8(sp, 255 - da), dp)
            elif mode == 3:
                n = mul8(sp, da)
            elif mode == 4:
                n = mul8(dp, sa)
            elif mode == 5:
                n = _sadd(_sadd(mul8(sp, dp), mul8(sp, 255 - da)), mul8(dp, 255 - sa))
            elif mode == 6:
                n = _sadd(sp, _ssub(dp, mul8(sp, dp)))
            elif mode in (7, 8):
                x, y = mul8(sp, da), mul8(dp, sa)
                n = _sadd(_sadd(min(x, y) if mode == 7 else max(x, y), mul8(sp, 255 - da)), mul8(dp, 255 - sa))
            else:
                n = _sadd(sp, dp)
            out[k] = _div_ra(n, ra)
        out[3] = ra
    return out[0] | (out[1] << 8) | (out[2] << 16) | (out[3] << 24)
