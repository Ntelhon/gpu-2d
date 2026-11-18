# SPDX-License-Identifier: Apache-2.0
"""API scenes through the vg_lite driver on the software backend (Phase 6, P6-3).

A scene is a script for sw/tests/vgl_scene (the driver through its public API). The software backend
records the command stream it receives (G2D_RECORD). Each scene is checked four ways, none of them
against the golden model the backend itself runs:
  1. stream replay: the recorded kicks are decoded here (from commands.md) and rendered by the independent
     Python reference (dv/common/refimpl.py); the driver's buffers must match bit-exactly;
  2. API mapping: every draw's state words equal what this file derives from the API call (formats,
     clip, alpha, paint, blend, raster bits; geometry from the shared sw/lib/geom, rendering.md §9);
  3. paths against the float reference (dense supersampling), tolerances of rendering.md §12;
  4. bilinear blits against the float reference, |Δ| <= 2.5 per channel (rendering.md §12).
SEED selects the random scenes (make test: a few seeds; make regress: more).
"""

from __future__ import annotations

import math
import os
import random
import re
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "dv" / "common"), str(ROOT / "scripts"),
                os.environ.get("G2D_MODEL_PATH", str(ROOT / "build" / "model"))]

import floatref  # noqa: E402
import g2d_model as gm  # noqa: E402
import gen_regs  # noqa: E402
import refimpl  # noqa: E402
from g2d_cmd import BLEND, FMT, FMT_BITS, INDEXED_FMTS, STATE, TGT_FMTS  # noqa: E402

BIN = Path(os.environ.get("G2D_MODEL_PATH", ROOT / "build" / "model")) / "sw" / "vgl_scene"
SEED = int(os.environ.get("SEED", "1"))
REGMAP = gen_regs.load(ROOT / "spec" / "regs.yaml")
ST_NAME = {r.addr: r.name for r in REGMAP.state}
ST_RESET = {r.name: r.reset for r in REGMAP.state}
ST_MASK = {r.name: sum(f.mask for f in r.fields) for r in REGMAP.state}

pytestmark = pytest.mark.skipif(not BIN.exists(), reason="build the software stack first: make model")


def _enums():
    out = subprocess.run([str(BIN), "enums"], capture_output=True, text=True, check=True).stdout
    return {n: int(v) for n, v in (ln.split() for ln in out.splitlines())}


E = _enums() if BIN.exists() else {}
VG_NAME = {n: (f"INDEX_{n[1:]}" if n in ("I1", "I2", "I4", "I8") else n) for n in FMT}   # regs.yaml -> vg_lite
VG_FMT = {n: E.get(f"VG_LITE_{VG_NAME[n]}") for n in FMT}       # vg_lite enum value per format name
HW_FMT = {E.get(f"VG_LITE_{VG_NAME[n]}"): code for n, code in FMT.items()}
BITS = {n: FMT_BITS[c] for n, c in FMT.items()}
INDEXED = {n for n, c in FMT.items() if c in INDEXED_FMTS}
A_ONLY = ("A8", "A4", "A2", "A1")


def row_bytes(fmt, w):
    return (w * BITS[fmt] + 7) // 8


# ------------------------------------------------------------------------------------------------
# running a scene
# ------------------------------------------------------------------------------------------------

class Scene:
    def __init__(self):
        self.lines: list[str] = []
        self.bufs: dict[int, tuple] = {}         # id -> (w, h, fmt name)
        self.calls: list[tuple] = []             # API draw calls, in order, for the mapping checks
        self.expect: list[str] = []              # expected return code of each blit call

    def buf(self, i, w, h, fmt, stride=0):
        self.bufs[i] = (w, h, fmt)
        self.lines.append(f"buf {i} {w} {h} {VG_FMT[fmt]} {stride}")

    def add(self, line, call=None):
        self.lines.append(line)
        if call is not None:
            self.calls.append(call)


def run(scene: Scene, tmp: Path):
    rec = tmp / "rec.bin"
    lines = scene.lines + ["finish"] + [f"dump {i} {tmp / f'buf{i}.bin'}" for i in scene.bufs]
    env = {**os.environ, "G2D_RECORD": str(rec), "G2D_MODEL_ARENA_MB": "16"}
    p = subprocess.run([str(BIN)], input="\n".join(lines) + "\n", capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    rcs, addrs = [], {}
    for ln in p.stdout.splitlines():
        f = ln.split()
        if f[0] == "rc":
            rcs.append((f[1], int(f[2])))
        elif f[0] == "buf":
            addrs[int(f[1])] = (int(f[2]), int(f[3]))
    dumps = {i: (tmp / f"buf{i}.bin").read_bytes() for i in scene.bufs}
    return rcs, addrs, dumps, rec.read_bytes()


# ------------------------------------------------------------------------------------------------
# 1. stream replay through the independent reference
# ------------------------------------------------------------------------------------------------

def s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


class Replay:
    """Decodes the recorder format (sw/hal/model/g2d_hal_model.h) and the command words (commands.md)."""

    def __init__(self, data: bytes):
        self.mem = refimpl.RefMemory()
        self.pages: set[int] = set()            # 4 KB pages the stream supplied (MEM records)
        self.st = dict(ST_RESET)
        self.draws: list[tuple] = []
        self.checks: list[tuple[int, bytes]] = []
        w = struct.unpack_from("<2I", data, 0)
        assert w[0] == 0x43455232 and w[1] in (1, 2), "recorder magic / version"
        pos = 8
        while True:
            (kind,) = struct.unpack_from("<I", data, pos)
            pos += 4
            if kind == 0xFFFFFFFF:
                break
            if kind == 1:                       # MEM addr len bytes
                addr, n = struct.unpack_from("<2I", data, pos)
                self.mem.write(addr, data[pos + 8:pos + 8 + n])
                self.pages.update(range(addr >> 12, (addr + n + 4095) >> 12))
                pos += 8 + n
            elif kind == 2:                     # KICK addr size
                addr, size = struct.unpack_from("<2I", data, pos)
                pos += 8
                self.kick(addr, size)
            elif kind == 3:
                self.st = dict(ST_RESET)
            elif kind in (4, 5):                # CHECK / POST addr len bytes mask: GPU-written bytes
                addr, n = struct.unpack_from("<2I", data, pos)
                rec = (addr, data[pos + 8:pos + 8 + n], data[pos + 8 + n:pos + 8 + n + n // 8])
                if kind == 4:
                    self.checks.append(rec)
                pos += 8 + n + n // 8
            else:
                raise AssertionError(f"record kind {kind}")

    def kick(self, addr, size):
        assert size % 8 == 0 and size > 0
        words = [self.mem.read_le(addr + 4 * i, 4) for i in range(size // 4)]
        i = 0
        while i < len(words):
            w = words[i]
            op = w >> 24
            if op == 0x00:                      # NOP SKIP
                i += 1 + (w & 0xFFFF)
            elif op == 0x01:                    # LOAD_STATE COUNT INDEX
                cnt, idx = (w >> 16) & 0xFF, w & 0xFF
                for k in range(cnt):
                    name = ST_NAME.get(idx + k)
                    if name:
                        self.st[name] = words[i + 1 + k] & ST_MASK[name]
                i += 1 + cnt
            elif op == 0x10:                    # DRAW_RECT
                p0, p1 = words[i + 1], words[i + 2]
                rect = (s16(p0), s16(p0 >> 16), s16(p1), s16(p1 >> 16))
                refimpl.ref_draw(self.mem, self.st, rect)
                self.draws.append(("rect", dict(self.st), rect, None, None))
                i += 3
            elif op == 0x11:                    # DRAW_PATH
                p0, p1, ea, n = words[i + 1:i + 5]
                bb = (s16(p0), s16(p0 >> 16), s16(p1), s16(p1 >> 16))
                raw = bytes(self.mem.read_le(ea + k, 1) for k in range(16 * n))
                edges = refimpl.unpack_edges(raw)
                ramp = None
                if self.st["PAINT_CTRL"] & 3 in (2, 3):
                    # entries on pages the stream supplied (the model reads only the entries it uses, so a
                    # ramp page with none of them is not recorded); None for the others
                    ra = self.st["GRAD_RAMP_ADDR"]
                    ramp = [self.mem.read_le(ra + 4 * k, 4) if ((ra + 4 * k) >> 12) in self.pages else None
                            for k in range(256)]
                refimpl.ref_draw(self.mem, self.st, bb, edges)
                self.draws.append(("path", dict(self.st), bb, edges, ramp))
                i += 5
            elif op == 0x20:                    # FENCE
                i += 2
            else:
                raise AssertionError(f"opcode 0x{op:02x} at word {i}")

    def read(self, addr, n):
        return bytes(self.mem.read_le(addr + k, 1) for k in range(n))


def check_replay(scene, addrs, dumps, rep: Replay):
    """Bytes on pages the stream supplied: every GPU write lands on such a page, and the stream holds its
    whole pre-kick content; other pages only hold what the CPU wrote and are not part of the stream."""
    compared = 0
    for i, (w, h, fmt) in scene.bufs.items():
        addr, stride = addrs[i]
        row = row_bytes(fmt, w)
        bad = 0
        for y in range(h):
            for x in range(row):
                a = addr + y * stride + x
                if (a >> 12) in rep.pages:
                    compared += 1
                    bad += dumps[i][y * stride + x] != rep.mem.read_le(a, 1)
        if bad:
            raise AssertionError(f"buffer {i} ({fmt} {w}x{h}): {bad} bytes differ from the reference replay")
    # recorder v2: the software backend's final content of every touched page equals the replay's
    for addr, data, mask in rep.checks:
        got = rep.read(addr, len(data))
        bits = np.unpackbits(np.frombuffer(mask, np.uint8), bitorder="little").astype(bool)
        if (bits & (np.frombuffer(got, np.uint8) != np.frombuffer(data, np.uint8))).any():
            raise AssertionError(f"page 0x{addr:08x}: reference replay differs from the software backend")
        compared += int(bits.sum())
    return compared      # 0 when every draw was clipped away (the mapping check still covers them)


# ------------------------------------------------------------------------------------------------
# 2. API -> state mapping, written from api-subset.md / rendering.md (not from the C driver)
# ------------------------------------------------------------------------------------------------

def argb(c):          # vg_lite_color_t A<<24|B<<16|G<<8|R -> ARGB32 (rendering.md §9.4)
    return (c & 0xFF00FF00) | ((c & 0xFF) << 16) | ((c >> 16) & 0xFF)


def exp_target(addrs, scene, t, scissor):
    w, h, fmt = scene.bufs[t]
    addr, stride = addrs[t]
    x0, y0, x1, y1 = 0, 0, w, h
    if scissor is not None:
        clampv = lambda v, lo, hi: max(lo, min(hi, v))  # noqa: E731
        x0, y0 = clampv(scissor[0], 0, w), clampv(scissor[1], 0, h)
        x1, y1 = clampv(scissor[2], x0, w), clampv(scissor[3], y0, h)
    return {"TGT_ADDR": addr, "TGT_STRIDE": stride, "TGT_SIZE": w | (h << 16), "TGT_FMT": FMT[fmt],
            "CLIP_MIN": x0 | (y0 << 16), "CLIP_MAX": x1 | (y1 << 16)}, (x0, y0, x1, y1)


def exp_alpha(sa, sv, da, dv):
    return sa | (da << 2) | (sv << 8) | (dv << 16)


def check_mapping(scene, addrs, rep: Replay):
    draws = list(rep.draws)
    for call in scene.calls:
        kind = call[0]
        if kind == "empty":          # the driver must not have emitted anything for this call
            continue
        assert draws, f"no draw recorded for {call[:2]}"
        _, st, rect, edges, ramp_at_draw = draws.pop(0)
        exp, clip = exp_target(addrs, scene, call[1], call[2])
        if kind == "clear":
            _, t, sc, r, color = call
            exp.update(BLEND=0, ALPHA_CTRL=exp_alpha(0, 255, 0, 255), PAINT_CTRL=0, PAINT_COLOR=argb(color))
            w, h, _ = scene.bufs[t]
            assert rect == (r if r else (0, 0, w, h)), (rect, r)
        elif kind == "blit":
            _, t, sc, s, m, srect, blend, color, flt, alpha, img, keys = call[:12]
            endian = call[12] if len(call) > 12 else 0
            sw, sh, sfmt = scene.bufs[s]
            saddr, sstride = addrs[s]
            mode, transp, premul = img
            rc, xf, persp, draw = gm.blit_setup([list(r) for r in m], list(srect), list(clip))
            assert rc == 0
            # A8 / A4 / A2 / A1 sources are premultiplied white under the Porter-Duff blends, plain white under the
            # LVGL blends 11-15 (Q-API-3): SRC_PREMUL is set accordingly
            a8_premul = sfmt in A_ONLY and not (11 <= blend <= 15)
            paint = 1 | (int(mode == E["VG_LITE_MULTIPLY_IMAGE_MODE"]) << 2) | (int(premul or a8_premul) << 3) | \
                (int(flt != E["VG_LITE_FILTER_POINT"]) << 4) | (persp << 5)
            # SUB_ORDER: indexed sources from index_endian, A4 / A2 / A1 / L4 first pixel in the high bits (LVGL);
            # TRANSPARENT from transparency_mode (v1.4, rendering.md §3.5, §5.2)
            sub = (endian == E["VG_LITE_INDEX_BIG_ENDIAN"]) if sfmt in INDEXED else BITS[sfmt] < 8
            tr = transp == E["VG_LITE_IMAGE_TRANSPARENT"]
            exp.update(SRC_ADDR=saddr, SRC_STRIDE=sstride, SRC_SIZE=sw | (sh << 16),
                       SRC_FMT=FMT[sfmt] | (int(sub) << 9) | (int(tr) << 10), BORDER_COLOR=0,
                       SRC_RECT_MIN=srect[0] | (srect[1] << 16), SRC_RECT_MAX=srect[2] | (srect[3] << 16),
                       BLEND=blend, ALPHA_CTRL=exp_alpha(*alpha), PAINT_CTRL=paint, PAINT_COLOR=argb(color))
            for k, n in enumerate("ABCDEFGHI"):
                exp[f"XF_{n}"] = xf[k] & 0xFFFFFFFF
            for k in range(8):
                exp[ST_NAME[0x10 + k]] = keys[k]
            assert rect == tuple(draw), (rect, draw)
        elif kind in ("path", "lgrad", "xgrad", "rgrad"):
            _, t, sc, fmt, data, m, fill, quality, blend, color, alpha, grad = call
            rc, gedges, bbox = gm.path_edges(data, fmt, [list(r) for r in m])
            assert rc == 0 and [tuple(e) for e in gedges] == [tuple(e) for e in edges], "path edges"
            assert rect == tuple(bbox)
            exp.update(BLEND=blend, ALPHA_CTRL=exp_alpha(*alpha),
                       RAST_CTRL=int(fill == E["VG_LITE_FILL_EVEN_ODD"]) | (int(quality != E["VG_LITE_LOW"]) << 1))
            if kind == "path":
                exp.update(PAINT_CTRL=0, PAINT_COLOR=argb(color))
            elif kind == "rgrad":
                gmat, prm, spread = grad
                rc, p, q, r = gm.radial_coeffs([list(row) for row in gmat], *prm)
                assert rc == 0
                exp.update(PAINT_CTRL=3 | (spread << 6))
                for k, (pn, qn, rn, hn) in enumerate((("GRAD_P", "GRAD_Q", "GRAD_R", "GRAD_R_HI"),
                                                      ("GRAD_UP", "GRAD_UQ", "GRAD_UR", "GRAD_UR_HI"),
                                                      ("GRAD_VP", "GRAD_VQ", "GRAD_VR", "GRAD_VR_HI"))):
                    exp.update({pn: p[k] & 0xFFFFFFFF, qn: q[k] & 0xFFFFFFFF, rn: r[k] & 0xFFFFFFFF,
                                hn: (r[k] >> 32) & 0xFFFF})
            else:
                gmat, ext, x0, y0, x1, y1, spread, ramp_words = grad
                rc, p, q, r = gm.grad_coeffs([list(row) for row in gmat], ext, x0, y0, x1, y1)
                assert rc == 0
                exp.update(PAINT_CTRL=2 | (spread << 6), GRAD_P=p & 0xFFFFFFFF, GRAD_Q=q & 0xFFFFFFFF,
                           GRAD_R=r & 0xFFFFFFFF, GRAD_R_HI=(r >> 32) & 0xFFFF)
                # the ramp as the GPU read it (its buffer may be reused by a later gradient); a draw whose
                # area is empty reads nothing (rendering.md §6), so its ramp is not in the stream
                tw, th = st["TGT_SIZE"] & 0x3FFF, (st["TGT_SIZE"] >> 16) & 0x3FFF
                cx0, cy0 = st["CLIP_MIN"] & 0x3FFF, (st["CLIP_MIN"] >> 16) & 0x3FFF
                cx1, cy1 = st["CLIP_MAX"] & 0x3FFF, (st["CLIP_MAX"] >> 16) & 0x3FFF
                empty = min(rect[2], cx1, tw) <= max(rect[0], cx0, 0) or min(rect[3], cy1, th) <= max(rect[1], cy0, 0)
                known = [(k, v) for k, v in enumerate(ramp_at_draw) if v is not None]
                assert empty or (known and all(v == ramp_words[k] & 0xFFFFFFFF for k, v in known)), "ramp"
        for k, v in exp.items():
            assert st[k] == v & ST_MASK[k], f"{kind}: {k} = 0x{st[k]:x}, expected 0x{v & ST_MASK[k]:x}"
    assert not draws, f"{len(draws)} unexpected draws"


# ------------------------------------------------------------------------------------------------
# scene generators
# ------------------------------------------------------------------------------------------------

def rand_scissor(rng, w, h):
    if rng.random() < 0.5:
        return None
    x0, y0 = rng.randint(-4, w), rng.randint(-4, h)
    return (x0, y0, x0 + rng.randint(0, w + 4), y0 + rng.randint(0, h + 4))


def set_scissor(sc: Scene, s):
    sc.add("noscissor" if s is None else f"scissor {s[0]} {s[1]} {s[2]} {s[3]}")


def mat_str(m):
    return " ".join(repr(float(v)) for r in m for v in r)


def f32(v):
    return struct.unpack("<f", struct.pack("<f", v))[0]


def f32m(m):
    return [[f32(v) for v in r] for r in m]


def rand_matrix(rng, sw, sh, tw, th, persp=False):
    th_ = rng.uniform(0, 2 * math.pi) if rng.random() < 0.5 else 0.0
    s = rng.uniform(0.5, 2.0)
    a, b, c, d = s * math.cos(th_), -s * math.sin(th_), s * math.sin(th_), s * math.cos(th_)
    tx = tw / 2 - (a * sw / 2 + b * sh / 2) + rng.uniform(-4, 4)
    ty = th / 2 - (c * sw / 2 + d * sh / 2) + rng.uniform(-4, 4)
    m = [[a, b, tx], [c, d, ty], [0.0, 0.0, 1.0]]
    if persp:
        m[2] = [rng.uniform(-0.6, 0.6) / sw, rng.uniform(-0.6, 0.6) / sh, 1.0]
    return f32m(m)


def blit_expect(m, sw, sh, tw, th):
    """Expected return code of a whole-image blit with matrix m (rendering.md §9.3, from the shared geometry as
    in scene_blits): a perspective matrix can need an inverse coefficient beyond 32 bits, INVALID_ARGUMENT."""
    rc, _, _, _ = gm.blit_setup([list(r) for r in m], [0, 0, sw, sh], [0, 0, tw, th])
    return "INVALID_ARGUMENT" if rc == -1 else "SUCCESS"


def poly_path(rng, fmt, cx, cy, r, n, curves=True):
    """Random closed path with lines and (optionally) quads / cubics. Returns (elements, data bytes,
    float points for the float reference given the matrix applied later)."""
    pts = [(cx + rng.uniform(-r, r), cy + rng.uniform(-r, r)) for _ in range(n)]
    if fmt in ("S16", "S32", "S8"):
        pts = [(round(x), round(y)) for x, y in pts]
    el = [E["VLC_OP_MOVE"], *pts[0]]
    segs = []
    cur = pts[0]
    for p in pts[1:] + [pts[0]]:
        kind = rng.choice(["line", "line", "quad", "cubic"]) if curves else "line"
        if kind == "line":
            el += [E["VLC_OP_LINE"], *p]
            segs.append(("line", cur, p))
        elif kind == "quad":
            c1 = ((cur[0] + p[0]) / 2 + rng.uniform(-r / 2, r / 2), (cur[1] + p[1]) / 2 + rng.uniform(-r / 2, r / 2))
            if fmt != "FP32":
                c1 = (round(c1[0]), round(c1[1]))
            el += [E["VLC_OP_QUAD"], *c1, *p]
            segs.append(("quad", cur, c1, p))
        else:
            c1 = (cur[0] + rng.uniform(-r / 2, r / 2), cur[1] + rng.uniform(-r / 2, r / 2))
            c2 = (p[0] + rng.uniform(-r / 2, r / 2), p[1] + rng.uniform(-r / 2, r / 2))
            if fmt != "FP32":
                c1, c2 = (round(c1[0]), round(c1[1])), (round(c2[0]), round(c2[1]))
            el += [E["VLC_OP_CUBIC"], *c1, *c2, *p]
            segs.append(("cubic", cur, c1, c2, p))
        cur = p
    el += [E["VLC_OP_END"]]
    # opcodes are integer elements in every format (rendering.md §9.2); mark them for the script
    ops = set()
    i = 0
    nargs = {E["VLC_OP_MOVE"]: 2, E["VLC_OP_LINE"]: 2, E["VLC_OP_QUAD"]: 4, E["VLC_OP_CUBIC"]: 6, E["VLC_OP_END"]: 0}
    while i < len(el):
        ops.add(i)
        i += 1 + nargs[el[i]]
    if fmt == "FP32":
        el = [v if k in ops else f32(v) for k, v in enumerate(el)]
        segs = [tuple(seg[0:1]) + tuple((f32(q[0]), f32(q[1])) for q in seg[1:]) for seg in segs]
    pack = {"S8": "b", "S16": "h", "S32": "i", "FP32": "f"}[fmt]
    data = b"".join(struct.pack("<I" if fmt == "FP32" and k in ops else f"<{pack}", int(v) if k in ops else v)
                    for k, v in enumerate(el))
    toks = [f"@{int(v)}" if k in ops else (repr(float(v)) if fmt == "FP32" else str(int(v))) for k, v in enumerate(el)]
    return toks, data, segs


def float_polygon(segs, m):
    def tr(p):
        return (m[0][0] * p[0] + m[0][1] * p[1] + m[0][2], m[1][0] * p[0] + m[1][1] * p[1] + m[1][2])
    pts = []
    for seg in segs:
        if seg[0] == "line":
            pts.append(tr(seg[2]))
        elif seg[0] == "quad":
            pts += floatref.bezier_points(tr(seg[1]), tr(seg[2]), tr(seg[3]))
        else:
            pts += floatref.bezier_points(tr(seg[1]), tr(seg[2]), tr(seg[3]), tr(seg[4]))
    return pts


FMTS = [n for n in FMT if FMT[n] in TGT_FMTS]     # target formats
SRC_FMTS = list(FMT)                                # every source format (v1.4: sub-byte, indexed)
BLENDS = list(BLEND.values())


def clut_line(rng, n=256):
    return "clut %d %s" % (n, " ".join(str(rng.getrandbits(32)) for _ in range(n)))


def scene_clears(rng):
    sc = Scene()
    for i in range(3):
        sc.buf(i, rng.randint(1, 40), rng.randint(1, 24), rng.choice(FMTS))
    for _ in range(rng.randint(3, 12)):
        t = rng.randrange(3)
        w, h, _ = sc.bufs[t]
        s = rand_scissor(rng, w, h)
        set_scissor(sc, s)
        color = rng.getrandbits(32)
        if rng.random() < 0.3:
            sc.add(f"clear {t} 0 0 -1 -1 {color}", ("clear", t, s, None, color))
        else:
            x, y = rng.randint(-5, w), rng.randint(-5, h)
            ww, hh = rng.randint(0, w + 5), rng.randint(0, h + 5)
            sc.add(f"clear {t} {x} {y} {ww} {hh} {color}", ("clear", t, s, (x, y, x + ww, y + hh), color))
    return sc


def scene_blits(rng, n=6):
    sc = Scene()
    sc.buf(0, rng.randint(8, 40), rng.randint(8, 32), rng.choice(FMTS))
    sc.buf(1, rng.randint(2, 24), rng.randint(2, 24), rng.choice(SRC_FMTS))
    sc.buf(2, rng.randint(2, 24), rng.randint(2, 24), rng.choice(SRC_FMTS))
    for i in range(3):
        sc.add(f"fill {i} {rng.getrandbits(31)}")
    endian = {1: 0, 2: 0}
    for i in (1, 2):
        if sc.bufs[i][2] in INDEXED:
            endian[i] = rng.choice([E["VG_LITE_INDEX_LITTLE_ENDIAN"], E["VG_LITE_INDEX_BIG_ENDIAN"]])
            sc.add(f"endian {i} {endian[i]}")
    if any(sc.bufs[i][2] in INDEXED for i in (1, 2)):
        sc.add(clut_line(rng, rng.choice([2, 4, 16, 256])))
    keys = [0] * 8
    alpha = (0, 255, 0, 255)
    for _ in range(n):
        t, s = 0, rng.choice([1, 2])
        tw, th, _ = sc.bufs[t]
        sw, sh, _ = sc.bufs[s]
        scs = rand_scissor(rng, tw, th)
        set_scissor(sc, scs)
        if rng.random() < 0.3:
            alpha = (rng.randrange(3), rng.randrange(256), rng.randrange(3), rng.randrange(256))
            sc.add(f"salpha {alpha[0]} {alpha[1]}")
            sc.add(f"dalpha {alpha[2]} {alpha[3]}")
        if rng.random() < 0.2:
            kk, words = [], []
            for k in range(4):
                en, lo, hi, a = rng.random() < 0.5, [rng.randrange(256) for _ in range(3)], \
                    [rng.randrange(256) for _ in range(3)], rng.randrange(256)
                kk += [int(en), *lo, a, *hi]
                words += [lo[2] | (lo[1] << 8) | (lo[0] << 16) | (int(en) << 31), hi[2] | (hi[1] << 8) | (hi[0] << 16) | (a << 24)]
            keys = words
            sc.add("ckey " + " ".join(map(str, kk)))
        mode = rng.choice([E["VG_LITE_NORMAL_IMAGE_MODE"], E["VG_LITE_ZERO"], E["VG_LITE_MULTIPLY_IMAGE_MODE"]])
        transp = rng.choice([E["VG_LITE_IMAGE_TRANSPARENT"], E["VG_LITE_IMAGE_OPAQUE"]])
        premul = int(rng.random() < 0.3)
        sc.add(f"img {s} {mode} {transp} {premul}")
        m = rand_matrix(rng, sw, sh, tw, th, persp=rng.random() < 0.25)
        flt = rng.choice([E["VG_LITE_FILTER_POINT"], E["VG_LITE_FILTER_BI_LINEAR"], E["VG_LITE_FILTER_LINEAR"]])
        blend, color = rng.choice(BLENDS), rng.getrandbits(32)
        srect = (0, 0, sw, sh)
        extra = ""
        if rng.random() < 0.4:
            x0, y0 = rng.randrange(sw), rng.randrange(sh)
            srect = (x0, y0, rng.randint(x0 + 1, sw), rng.randint(y0 + 1, sh))
            extra = f" {srect[0]} {srect[1]} {srect[2] - srect[0]} {srect[3] - srect[1]}"
        _, clip = exp_target({t: (0, 0)}, sc, t, scs)
        rc, _, _, _ = gm.blit_setup([list(r) for r in m], list(srect), list(clip))
        call = ("blit", t, scs, s, m, srect, blend, color, flt, alpha, (mode, transp, premul), keys, endian[s]) \
            if rc == 0 else ("empty",)
        # rendering.md §9.3: det == 0 or a coefficient beyond 32 bits is INVALID_ARGUMENT; empty is success
        sc.expect.append("INVALID_ARGUMENT" if rc == -1 else "SUCCESS")
        sc.add(f"blit {t} {s} {blend} {color} {flt} {mat_str(m)}{extra}", call)
    return sc


def scene_paths(rng, n=3):
    sc = Scene()
    sc.buf(0, rng.randint(16, 40), rng.randint(16, 32), rng.choice(FMTS))
    sc.add(f"fill 0 {rng.getrandbits(31)}")
    w, h, _ = sc.bufs[0]
    for _ in range(n):
        fmt = rng.choice(["S16", "S32", "FP32", "S8"])
        el, data, _ = poly_path(rng, fmt, w / 2, h / 2, min(w, h) / 2.5, rng.randint(3, 7))
        m = rand_matrix(rng, w, h, w, h)
        scs = rand_scissor(rng, w, h)
        set_scissor(sc, scs)
        fill = rng.choice([E["VG_LITE_FILL_NON_ZERO"], E["VG_LITE_FILL_EVEN_ODD"]])
        q = rng.choice([E["VG_LITE_HIGH"], E["VG_LITE_UPPER"], E["VG_LITE_MEDIUM"], E["VG_LITE_LOW"]])
        blend, color = rng.choice(BLENDS), rng.getrandbits(32)
        kind = rng.choice(["path", "path", "lgrad", "xgrad", "rgrad"])
        fe = E[f"VG_LITE_{fmt}"]
        rc, edges, _ = gm.path_edges(data, fe, [list(r) for r in m])
        elems = " ".join(el)
        alpha = (0, 255, 0, 255)
        if kind == "path":
            call = ("path", 0, scs, fe, data, m, fill, q, blend, color, alpha, None)
            line = f"path 0 {fe} {q} {fill} {blend} {color} {mat_str(m)} {len(el)} {elems}"
        elif kind == "lgrad":
            k = rng.randint(1, 16)
            colors = [rng.getrandbits(32) for _ in range(k)]
            stops = sorted(rng.randrange(256) for _ in range(k))
            gmat = rand_matrix(rng, 256, 1, w, h)
            _, ramp = gm.ramp_legacy(colors, stops)
            call = ("lgrad", 0, scs, fe, data, m, fill, q, blend, color, alpha, (gmat, 0, 0, 0, 0, 0, 0, ramp))
            line = (f"lgrad 0 {fe} {q} {fill} {blend} {mat_str(m)} {mat_str(gmat)} {k} " + " ".join(map(str, colors)) +
                    " " + " ".join(map(str, stops)) + f" {len(el)} {elems}")
        elif kind == "rgrad":          # v1.5, F-4
            k = rng.randint(1, 8)
            st = sorted(f32(rng.random()) for _ in range(k))
            stops = [[st[i]] + [f32(rng.random()) for _ in range(4)] for i in range(k)]
            spread = rng.randrange(4)
            r_ = f32(rng.choice([rng.uniform(2, w), 0.0]))
            prm = (f32(rng.uniform(0, w)), f32(rng.uniform(0, h)), r_, f32(rng.uniform(0, w)), f32(rng.uniform(0, h)))
            gmat = f32m([[1, 0, 0], [0, 1, 0], [0, 0, 1]])
            sp_enum = E[f"VG_LITE_GRADIENT_SPREAD_{['PAD', 'REPEAT', 'REFLECT', 'FILL'][spread]}"]
            call = ("rgrad", 0, scs, fe, data, m, fill, q, blend, color, alpha, (gmat, prm, spread))
            line = (f"rgrad 0 {fe} {q} {fill} {blend} {sp_enum} 1 {mat_str(m)} {mat_str(gmat)} "
                    + " ".join(repr(v) for v in prm) + f" {k} " +
                    " ".join(" ".join(repr(v) for v in s_) for s_ in stops) + f" {len(el)} {elems}")
        else:
            k = rng.randint(1, 8)
            st = sorted(f32(rng.random()) for _ in range(k))
            stops = [[st[i]] + [f32(rng.random()) for _ in range(4)] for i in range(k)]
            spread = rng.randrange(3)
            premul = int(rng.random() < 0.5)
            x0, y0, x1, y1 = (f32(rng.uniform(0, w)), f32(rng.uniform(0, h)), f32(rng.uniform(0, w)), f32(rng.uniform(0, h)))
            if (x0, y0) == (x1, y1):
                x1 += 1
            gmat = f32m([[1, 0, 0], [0, 1, 0], [0, 0, 1]])
            _, ramp = gm.ramp_ext(stops, bool(premul))
            sp_enum = [E["VG_LITE_GRADIENT_SPREAD_PAD"], E["VG_LITE_GRADIENT_SPREAD_REPEAT"],
                       E["VG_LITE_GRADIENT_SPREAD_REFLECT"]][spread]
            call = ("xgrad", 0, scs, fe, data, m, fill, q, blend, color, alpha, (gmat, 1, x0, y0, x1, y1, spread, ramp))
            line = (f"xgrad 0 {fe} {q} {fill} {blend} {sp_enum} {premul} {mat_str(m)} {mat_str(gmat)} "
                    f"{x0!r} {y0!r} {x1!r} {y1!r} {k} " + " ".join(" ".join(repr(v) for v in s_) for s_ in stops) +
                    f" {len(el)} {elems}")
        if rc != 0 or len(edges) == 0:
            call = ("empty",)
        sc.add(line, call)
    return sc


def check_rcs(rcs, allowed=("SUCCESS",)):
    ok = {E[f"VG_LITE_{a}"] for a in allowed}
    bad = [(n, c) for n, c in rcs if c not in ok]
    assert not bad, f"unexpected return codes {bad[:5]}"


def check_blit_rcs(sc, rcs):
    """Blits return exactly sc.expect (in order), every other call SUCCESS."""
    check_rcs([r for r in rcs if r[0] not in ("blit", "blit_rect")])
    assert [c for n, c in rcs if n in ("blit", "blit_rect")] == [E[f"VG_LITE_{x}"] for x in sc.expect]


SEEDS = [SEED * 100 + k for k in range(3)]
SEED_IDS = [str(k) for k in range(len(SEEDS))]   # node ids independent of SEED (scripts/regress.py
                                                    # collects them once and runs them with every seed)


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_clears(tmp_path, seed):
    sc = scene_clears(random.Random(seed))
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    rep = Replay(rec)
    check_replay(sc, addrs, dumps, rep)
    check_mapping(sc, addrs, rep)


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_blits(tmp_path, seed):
    sc = scene_blits(random.Random(seed))
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs([r for r in rcs if r[0] not in ("blit", "blit_rect")])
    assert [c for n, c in rcs if n in ("blit", "blit_rect")] == [E[f"VG_LITE_{x}"] for x in sc.expect]
    rep = Replay(rec)
    check_replay(sc, addrs, dumps, rep)
    check_mapping(sc, addrs, rep)


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_paths_and_gradients(tmp_path, seed):
    sc = scene_paths(random.Random(seed))
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    rep = Replay(rec)
    check_replay(sc, addrs, dumps, rep)
    check_mapping(sc, addrs, rep)


# ------------------------------------------------------------------------------------------------
# 3. / 4. float reference
# ------------------------------------------------------------------------------------------------

def circle_path(cx, cy, r):
    """Circle from four cubic arcs (FP32 elements), as LVGL builds round shapes."""
    k = 0.5522847498 * r
    pts = [(cx + r, cy), (cx, cy + r), (cx - r, cy), (cx, cy - r)]
    ctl = [((cx + r, cy + k), (cx + k, cy + r)), ((cx - k, cy + r), (cx - r, cy + k)),
           ((cx - r, cy - k), (cx - k, cy - r)), ((cx + k, cy - r), (cx + r, cy - k))]
    el = [E["VLC_OP_MOVE"], *pts[0]]
    segs, cur = [], pts[0]
    for i in range(4):
        p = pts[(i + 1) % 4]
        el += [E["VLC_OP_CUBIC"], *ctl[i][0], *ctl[i][1], *p]
        segs.append(("cubic", cur, ctl[i][0], ctl[i][1], p))
        cur = p
    el.append(E["VLC_OP_END"])
    ops, i = set(), 0
    while i < len(el):
        ops.add(i)
        i += {E["VLC_OP_MOVE"]: 3, E["VLC_OP_CUBIC"]: 7, E["VLC_OP_END"]: 1}[el[i]]
    vals = [v if k_ in ops else f32(v) for k_, v in enumerate(el)]
    segs = [tuple(sg[0:1]) + tuple((f32(q[0]), f32(q[1])) for q in sg[1:]) for sg in segs]
    toks = [f"@{int(v)}" if k_ in ops else repr(float(v)) for k_, v in enumerate(vals)]
    return toks, segs


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_path_coverage_vs_float(tmp_path, seed):
    """White path on black (blend NONE): the R channel is the coverage. rendering.md §12 path criteria, for
    the shape classes they were measured on (Phase 1): random polygons and smooth curved shapes (circles,
    ellipses through an affine matrix), 64 x 64 as there. Random self-intersecting Béziers on small targets
    can exceed the mean bound (up to 15.4 seen, OPEN_QUESTIONS Q-TEST-1) while max and inside/outside
    bounds hold."""
    rng = random.Random(seed)
    w, h = 64, 64              # Phase 1 measured on 64 x 64 with shapes spanning the image
    pooled = []
    for even_odd, shape in ((False, "poly"), (True, "poly"), (False, "circle")):
        sc = Scene()
        sc.buf(0, w, h, "BGRA8888")
        sc.add(f"clear 0 0 0 -1 -1 {0xFF000000}")
        if shape == "poly":
            fmt = rng.choice(["FP32", "S16"])
            el, data, segs = poly_path(rng, fmt, w / 2, h / 2, 36, rng.randint(3, 8), curves=False)
            m = f32m([[1, 0, rng.uniform(-3, 3)], [0, 1, rng.uniform(-3, 3)], [0, 0, 1]])
        else:
            fmt = "FP32"
            el, segs = circle_path(0.0, 0.0, rng.uniform(6, 28))
            a, sx, sy = rng.uniform(0, math.pi), rng.uniform(0.6, 1.4), rng.uniform(0.6, 1.4)
            m = f32m([[sx * math.cos(a), -sy * math.sin(a), w / 2 + rng.uniform(-4, 4)],
                      [sx * math.sin(a), sy * math.cos(a), h / 2 + rng.uniform(-4, 4)], [0, 0, 1]])
        fill = E["VG_LITE_FILL_EVEN_ODD"] if even_odd else E["VG_LITE_FILL_NON_ZERO"]
        elems = " ".join(el)
        sc.add(f"path 0 {E[f'VG_LITE_{fmt}']} {E['VG_LITE_HIGH']} {fill} {E['VG_LITE_BLEND_NONE']} {0xFFFFFFFF} "
               f"{mat_str(m)} {len(el)} {elems}")
        rcs, addrs, dumps, _ = run(sc, tmp_path)
        check_rcs(rcs)
        _, stride = addrs[0]
        img = np.frombuffer(dumps[0], np.uint8).reshape(h, stride)[:, :4 * w].reshape(h, w, 4)
        got = img[:, :, 2].astype(float)                         # R of B,G,R,A
        ref = floatref.path_coverage(floatref.polygons_to_edges([float_polygon(segs, m)]), w, h, even_odd, ss=32) * 255
        d = np.abs(got - ref)
        edge = (ref > 0) & (ref < 255)          # as Phase 1 (model/tests/test_vs_float.py)
        assert d.max() <= 56, f"max |Δ| {d.max()}"
        assert d[~edge].max(initial=0) <= 16, f"inside/outside |Δ| {d[~edge].max()}"
        if edge.any():
            assert d[edge].mean() <= 12, f"mean |Δ| on edge pixels {d[edge].mean()}"
            pooled += list(d[edge])
    assert np.mean(pooled) <= 10, f"pooled mean |Δ| on edge pixels {np.mean(pooled)}"


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_bilinear_blit_vs_float(tmp_path, seed):
    rng = random.Random(seed)
    sw, sh, w, h = 20, 16, 40, 32
    sc = Scene()
    sc.buf(0, w, h, "BGRA8888")
    sc.buf(1, sw, sh, "BGRA8888")
    sc.add(f"fill 1 {rng.getrandbits(31)}")
    # OPAQUE keeps the source alpha (Q-API-11); TRANSPARENT would skip A = 0 results (v1.4)
    sc.add(f"img 1 {E['VG_LITE_NORMAL_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
    sc.add("clear 0 0 0 -1 -1 0")
    for _ in range(100):           # a valid blit with enough pixels away from the source border
        m = rand_matrix(rng, sw, sh, w, h, persp=rng.random() < 0.5)
        rc, _, _, _ = gm.blit_setup([list(r) for r in m], [0, 0, sw, sh], [0, 0, w, h])
        if rc != 0:
            continue
        _, inside0, u0, v0 = floatref.blit_bilinear(np.zeros((sh, sw), np.uint32), (0, 0, sw, sh),
                                                   np.linalg.inv(np.asarray(m, float)), w, h)
        if (inside0 & (u0 >= 1) & (u0 < sw - 1) & (v0 >= 1) & (v0 < sh - 1)).sum() > 50:
            break
    sc.add(f"blit 0 1 {E['VG_LITE_BLEND_NONE']} 0 {E['VG_LITE_FILTER_BI_LINEAR']} {mat_str(m)}")
    rcs, addrs, dumps, _ = run(sc, tmp_path)
    check_rcs(rcs)
    sb = np.frombuffer(dumps[1], np.uint8).reshape(sh, addrs[1][1])[:, :4 * sw].reshape(sh, sw, 4).astype(np.uint32)
    src = (sb[:, :, 3] << 24) | (sb[:, :, 2] << 16) | (sb[:, :, 1] << 8) | sb[:, :, 0]   # ARGB32
    tb = np.frombuffer(dumps[0], np.uint8).reshape(h, addrs[0][1])[:, :4 * w].reshape(h, w, 4).astype(float)
    got = np.stack([tb[:, :, 3], tb[:, :, 2], tb[:, :, 1], tb[:, :, 0]], axis=-1)        # A R G B
    rc, inv = 0, np.linalg.inv(np.asarray(m, float))
    ref, inside, u, v = floatref.blit_bilinear(src, (0, 0, sw, sh), inv, w, h)
    deep = inside & (u >= 1) & (u < sw - 1) & (v >= 1) & (v < sh - 1)
    assert deep.sum() > 50
    d = np.abs(got - ref)[deep]
    assert d.max() <= 2.5, f"bilinear max |Δ| {d.max()}"


def test_error_codes(tmp_path):
    sc = Scene()
    sc.buf(0, 16, 16, "BGRA8888")
    sc.add(f"buf 1 8 8 {E['VG_LITE_YUYV']} 0")                               # YUYV: closed (v1.6b question 5)
    sc.add(f"blit 0 0 {E['VG_LITE_BLEND_NONE']} 0 {E['VG_LITE_FILTER_GAUSSIAN']} 1 0 0 0 1 0 0 0 1")   # v1.6b: drawn
    sc.add(f"blit 0 0 {E['VG_LITE_BLEND_NONE']} 0 {E['VG_LITE_FILTER_POINT']} 0 0 0 0 0 0 0 0 0")   # det = 0
    sc.add(f"blit 0 0 {0x3002} 0 {E['VG_LITE_FILTER_POINT']} 1 0 0 0 1 0 0 0 1")   # SVG2 code with no mode (v1.6)
    rcs, _, _, _ = run(sc, tmp_path)
    got = [c for n, c in rcs if n in ("allocate", "blit")]
    assert got == [E["VG_LITE_SUCCESS"], E["VG_LITE_NOT_SUPPORT"], E["VG_LITE_SUCCESS"],
                   E["VG_LITE_INVALID_ARGUMENT"], E["VG_LITE_NOT_SUPPORT"]], got


# ------------------------------------------------------------------------------------------------
# 5. vg_lite_draw_pattern (driver-only, rendering.md §9.5)
# ------------------------------------------------------------------------------------------------

def path_tokens(fmt, pts):
    """Closed polygon path (MOVE, LINE..., CLOSE, END) as script tokens, coordinates exact in fmt."""
    toks = [f"@{E['VLC_OP_MOVE']}", *map(repr if fmt == "FP32" else str, pts[0])]
    for p in pts[1:]:
        toks += [f"@{E['VLC_OP_LINE']}", *map(repr if fmt == "FP32" else str, p)]
    toks += [f"@{E['VLC_OP_CLOSE']}", f"@{E['VLC_OP_END']}"]
    return toks


def covered_rect(x0, y0, x1, y1, aa):
    """Pixels a rectangular pattern draw drawn as a blit covers (rendering.md §9.5 step 1): the pixel centre decides
    (exact without AA; with AA the driver uses the blit only for whole-pixel borders, where it is the same)."""
    del aa
    c = lambda a: math.ceil(a - 0.5)  # noqa: E731  first p with p + 0.5 >= a
    return c(x0), c(y0), c(x1), c(y1)


def path_cov_masks(tpts, aa, eo, clip, w, h):
    """(fully covered, partially covered) pixel masks of the closed polygon tpts (target pixels, exact in s.4)
    inside the clip rectangle, from the literal per-sample test of rendering.md §8 (refimpl.pixel_coverage)."""
    q = [(int(round(16 * x)), int(round(16 * y))) for x, y in tpts]
    edges = [(*q[i], *q[(i + 1) % len(q)]) for i in range(len(q))]
    full, part = np.zeros((h, w), bool), np.zeros((h, w), bool)
    for y in range(max(0, clip[1]), min(h, clip[3])):
        for x in range(max(0, clip[0]), min(w, clip[2])):
            c = refimpl.pixel_coverage(edges, x, y, aa, eo)
            full[y, x], part[y, x] = c == 255, 0 < c < 255
    return full, part


def pattern_expect(mode, inv, iw, ih, R, w, h, sentinel, pcolor_bytes):
    """Expected (B, G) texel coordinates or a fixed pixel per target pixel, from rendering.md §9.5 / §7.5 written
    with float geometry: (u, v) = P^-1 (x + 0.5, y + 0.5); REPEAT wraps the texel index, REFLECT mirrors
    every odd tile, PAD clamps it, COLOR is the image inside its bounds and the pattern colour outside.
    Pixels behind the eye (w <= 0) or beyond the wrap range (|u|, |v| >= 2^15, §7.4) are not drawn. Returns
    (expected bytes h x w x 4, mask of pixels to compare): pixels whose sample lies within 1e-3 texel of
    a texel boundary, or near w = 0, are excluded (as the point-sampled blit tolerance, rendering.md §12)."""
    exp = np.empty((h, w, 4), np.int64)
    exp[:, :] = sentinel
    mask = np.ones((h, w), bool)
    ys, xs = np.mgrid[0:h, 0:w]
    px, py = xs + 0.5, ys + 0.5
    W = inv[2][0] * px + inv[2][1] * py + inv[2][2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = (inv[0][0] * px + inv[0][1] * py + inv[0][2]) / W
        v = (inv[1][0] * px + inv[1][1] * py + inv[1][2]) / W
    ok = (W > 1e-6) & (np.abs(u) < 32767) & (np.abs(v) < 32767)
    u, v = np.where(ok, u, 0.5), np.where(ok, v, 0.5)
    if isinstance(R[0], np.ndarray):    # path coverage (F-1): fully covered pixels; partial ones are not compared
        inr = R[0].copy()
        mask &= ~R[1]
    else:
        inr = (xs >= R[0]) & (xs < R[2]) & (ys >= R[1]) & (ys < R[3])
    near = (np.abs(u - np.round(u)) < 1e-3) | (np.abs(v - np.round(v)) < 1e-3) | (np.abs(W) < 1e-3)
    mask &= ~(inr & near)
    inr &= ok
    tu, tv = np.floor(u).astype(np.int64), np.floor(v).astype(np.int64)
    if mode == "REPEAT":
        iu, iv, img = tu % iw, tv % ih, inr
    elif mode == "PAD":
        iu, iv, img = np.clip(tu, 0, iw - 1), np.clip(tv, 0, ih - 1), inr
    elif mode == "REFLECT":
        iu = np.where((tu // iw) % 2 == 0, tu % iw, iw - 1 - tu % iw)
        iv = np.where((tv // ih) % 2 == 0, tv % ih, ih - 1 - tv % ih)
        img = inr
    else:
        iu, iv = tu, tv
        img = inr & (tu >= 0) & (tu < iw) & (tv >= 0) & (tv < ih)
        if pcolor_bytes is not None:
            exp[inr & ~img] = pcolor_bytes
    exp[img, 0] = iu[img]
    exp[img, 1] = iv[img]
    exp[img, 2] = 0x5A
    exp[img, 3] = 0xFF
    return exp, mask


def bgra(c):          # vg_lite_color_t (A<<24|B<<16|G<<8|R) as B, G, R, A bytes of a BGRA8888 pixel
    return [(c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF, (c >> 24) & 0xFF]


def scene_patterns(rng, n=10):
    """Point-sampled pattern draws, blend NONE (or SRC_OVER with a transparent pattern colour, which leaves the
    outside alone) on a coordinate image, so each target pixel names the texel it got. Each draw has its own
    target. Returns the scene and per draw (id, expected rc, expectation or None)."""
    sc = Scene()
    draws = []
    sentinel = [0x11, 0x22, 0x33, 0x44]
    sent_color = 0x44112233      # A=0x44 B=0x11 G=0x22 R=0x33 -> bytes B,G,R,A = 11 22 33 44
    iw, ih = rng.randint(2, 20), rng.randint(2, 20)
    sc.buf(0, iw, ih, "BGRA8888")
    sc.add("coords 0")
    sc.add(f"img 0 {E['VG_LITE_NORMAL_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
    for d in range(1, n + 1):
        w, h = rng.randint(8, 48), rng.randint(8, 40)
        sc.buf(d, w, h, "BGRA8888")
        sc.add("noscissor")
        sc.add(f"clear {d} 0 0 -1 -1 {sent_color}")
        scs = rand_scissor(rng, w, h)
        set_scissor(sc, scs)
        mode = rng.choice(["REPEAT", "REPEAT", "REFLECT", "REFLECT", "COLOR", "COLOR", "COLOR", "PAD"])
        aa = rng.random() < 0.6
        q = E["VG_LITE_HIGH"] if aa else E["VG_LITE_LOW"]
        rule = rng.choice([E['VG_LITE_FILL_NON_ZERO'], E['VG_LITE_FILL_EVEN_ODD']])
        fmt = rng.choice(["FP32", "S16"])
        # rectangle chosen in target space (whole or quarter pixels, exact in s.4); the path holds it mapped back
        # through a right-angle rotation with an integer translation, so the corners stay exact
        step = 1 if fmt == "S16" or rng.random() < 0.6 else 0.25
        rx0, ry0 = rng.randint(-4, w - 2) + (0 if step == 1 else 0.25 * rng.randrange(4)), \
            rng.randint(-4, h - 2) + (0 if step == 1 else 0.25 * rng.randrange(4))
        rx1, ry1 = rx0 + rng.randint(1, w) * step * (1 if step == 1 else 4) + (0 if step == 1 else 0.25 * rng.randrange(4)), \
            ry0 + rng.randint(1, h) * step * (1 if step == 1 else 4) + (0 if step == 1 else 0.25 * rng.randrange(4))
        rot = rng.randrange(4)
        cs, sn = [(1, 0), (0, 1), (-1, 0), (0, -1)][rot]
        tx, ty = rng.randint(-20, 20), rng.randint(-20, 20)
        pmat = [[cs, -sn, tx], [sn, cs, ty], [0, 0, 1]]
        back = lambda x, y: (cs * (x - tx) + sn * (y - ty), -sn * (x - tx) + cs * (y - ty))  # noqa: E731
        pts = [back(rx0, ry0), back(rx1, ry0), back(rx1, ry1), back(rx0, ry1)]
        tpts = [(rx0, ry0), (rx1, ry0), (rx1, ry1), (rx0, ry1)]
        if rng.random() < 0.15:                 # not a rectangle: path coverage x image (F-1, v1.5)
            pts, tpts = pts[:3], tpts[:3]
        if rng.random() < 0.5:                  # mirrored winding
            pts = pts[::-1]
        # pattern matrix: axis-aligned scale, a rotation, an image stretched over the rectangle, or (v1.4) a
        # perspective one; every mode draws the whole rectangle (hardware wrap modes, rendering.md §7.5)
        axis = mode == "COLOR" and rng.random() < 0.5 or rng.random() < 0.3
        s_ = rng.uniform(0.5, 2.5)
        a = 0.0 if axis else rng.uniform(0, 2 * math.pi)
        sx, sy = s_ * rng.choice([1, -1]) if axis else s_, s_ * rng.uniform(0.7, 1.4) * (rng.choice([1, -1]) if axis else 1)
        P = f32m([[sx * math.cos(a), -sy * math.sin(a), rng.uniform(-6, w)],
                  [sx * math.sin(a), sy * math.cos(a), rng.uniform(-6, h)], [0, 0, 1]])
        if mode in ("COLOR", "PAD") and rng.random() < 0.5:
            mx, my = rng.uniform(0.6, 3), rng.uniform(0.6, 3)
            a = rng.uniform(-0.05, 0.05)
            kx, ky = (rx1 - rx0 + 2 * mx) / iw, (ry1 - ry0 + 2 * my) / ih
            P = f32m([[kx * math.cos(a), -ky * math.sin(a), rx0 - mx], [kx * math.sin(a), ky * math.cos(a), ry0 - my], [0, 0, 1]])
            axis = False
        if rng.random() < 0.25:                 # perspective, w stays well above 0 over the target
            P = f32m([list(P[0]), list(P[1]), [rng.uniform(-0.4, 0.4) / w, rng.uniform(-0.4, 0.4) / h, 1.0]])
        inv = np.linalg.inv(np.asarray(P, float))
        blend, pcolor = E["VG_LITE_BLEND_NONE"], rng.getrandbits(32)
        if mode == "COLOR" and not axis and rng.random() < 0.7:
            blend, pcolor = E["VG_LITE_BLEND_SRC_OVER"], 0     # outside stays as it is
        elif mode == "COLOR" and not axis and rng.random() < 0.5:
            pcolor = 0                  # transparent, but NONE writes it: not a no-op
        _, clip = exp_target({d: (0, 0)}, sc, d, scs)
        expect = "SUCCESS"
        eo = rule == E["VG_LITE_FILL_EVEN_ODD"]
        whole = all(float(c).is_integer() for p_ in tpts for c in p_)
        if len(tpts) == 4 and (not aa or whole):   # drawn as a blit over the covered rectangle (§9.5)
            cov = covered_rect(rx0, ry0, rx1, ry1, aa)
            box = (max(cov[0], clip[0]), max(cov[1], clip[1]), min(cov[2], clip[2]), min(cov[3], clip[3]))
            R = box
        else:                                   # DRAW_PATH with the image paint over the path's bbox (∩ clip)
            xs_, ys_ = [p_[0] for p_ in tpts], [p_[1] for p_ in tpts]
            box = (max(math.floor(min(xs_)), clip[0]), max(math.floor(min(ys_)), clip[1]),
                   min(math.ceil(max(xs_)), clip[2]), min(math.ceil(max(ys_)), clip[3]))
            R = path_cov_masks(tpts, aa, eo, clip, w, h)
        if box[2] <= box[0] or box[3] <= box[1]:
            R = (0, 0, 0, 0)
        else:
            if rng.random() < 0.1 and mode == "COLOR":
                blend, pcolor = E["VG_LITE_BLEND_SRC_OVER"], 0
            if gm.blit_setup(P, [0, 0, iw, ih], list(box), True)[0] == -1:
                expect = "INVALID_ARGUMENT"      # rendering.md §9.3: an inverse coefficient beyond 32 bits
        pc = None if (mode != "COLOR" or pcolor == 0 and blend == E["VG_LITE_BLEND_SRC_OVER"]) else bgra(pcolor)
        draws.append((d, expect, (mode, inv, iw, ih, R, w, h, sentinel, pc) if expect == "SUCCESS" else None))
        el = path_tokens(fmt, [(int(x), int(y)) if fmt == "S16" else (float(x), float(y)) for x, y in pts])
        sc.add(f"pattern {d} 0 {E[f'VG_LITE_{fmt}']} {q} {rule} "
               f"{blend} {E[f'VG_LITE_PATTERN_{mode}']} {pcolor} 0 {E['VG_LITE_FILTER_POINT']} "
               f"{mat_str(f32m(pmat))} {mat_str(P)} {len(el)} {' '.join(el)}")
    return sc, draws


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_pattern_semantics(tmp_path, seed):
    """Every pixel against the pattern semantics (texel identity), every byte against the reference replay
    of the recorded stream, return codes against the supported cases of rendering.md §9.5."""
    rng = random.Random(seed)
    checked = 0
    for _ in range(4):
        sc, draws = scene_patterns(rng)
        rcs, addrs, dumps, rec = run(sc, tmp_path)
        check_rcs([r for r in rcs if r[0] != "draw_pattern"])
        got_rc = [c for n, c in rcs if n == "draw_pattern"]
        assert got_rc == [E[f"VG_LITE_{x}"] for _, x, _ in draws], (got_rc, [x for _, x, _ in draws])
        for d, _, ex in draws:
            w, h, _ = sc.bufs[d]
            img = np.frombuffer(dumps[d], np.uint8).reshape(h, addrs[d][1])[:, :4 * w].reshape(h, w, 4).astype(np.int64)
            if ex is None:          # NOT_SUPPORT draws change nothing
                assert (img == [0x11, 0x22, 0x33, 0x44]).all()
                continue
            exp, mask = pattern_expect(*ex)
            bad = (img != exp).any(axis=2) & mask
            assert not bad.any(), f"draw {d} ({ex[0]}): {bad.sum()} pixels differ, first at {np.argwhere(bad)[0]}"
            checked += int(mask.sum())
        check_replay(sc, addrs, dumps, Replay(rec))
    assert checked > 0


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_pattern_streams(tmp_path, seed):
    """Random formats, blends, filters, image modes and global alpha on REPEAT / REFLECT / covering COLOR
    draws: all succeed and the recorded stream replays bit-exactly on the independent reference."""
    rng = random.Random(seed)
    sc = Scene()
    sc.buf(0, rng.randint(8, 48), rng.randint(8, 40), rng.choice(FMTS))
    sc.buf(1, rng.randint(1, 16), rng.randint(1, 16), rng.choice(FMTS))
    sc.add(f"fill 0 {rng.getrandbits(31)}")
    sc.add(f"fill 1 {rng.getrandbits(31)}")
    w, h, _ = sc.bufs[0]
    iw, ih, _ = sc.bufs[1]
    for _ in range(6):
        set_scissor(sc, rand_scissor(rng, w, h))
        if rng.random() < 0.3:
            sc.add(f"salpha {rng.randrange(3)} {rng.randrange(256)}")
            sc.add(f"dalpha {rng.randrange(3)} {rng.randrange(256)}")
        mode = rng.choice([E["VG_LITE_MULTIPLY_IMAGE_MODE"], E["VG_LITE_NORMAL_IMAGE_MODE"], E["VG_LITE_ZERO"]])
        sc.add(f"img 1 {mode} {E['VG_LITE_IMAGE_OPAQUE']} {int(rng.random() < 0.3)}")
        x0, y0 = rng.randint(-4, w), rng.randint(-4, h)
        x1, y1 = x0 + rng.randint(1, w), y0 + rng.randint(1, h)
        el = path_tokens("S16", [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        P = rand_matrix(rng, iw, ih, w, h)
        pmode = rng.choice(["REPEAT", "REFLECT", "COLOR"])
        sc.add(f"pcmode {rng.randrange(2)}")
        flt = rng.choice([E["VG_LITE_FILTER_POINT"], E["VG_LITE_FILTER_BI_LINEAR"]])
        sc.add(f"pattern 0 1 {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_EVEN_ODD']} {rng.choice(BLENDS)} "
               f"{E[f'VG_LITE_PATTERN_{pmode}']} {rng.getrandbits(32)} {rng.getrandbits(32)} {flt} "
               f"1 0 0 0 1 0 0 0 1 {mat_str(P)} {len(el)} {' '.join(el)}")
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    check_replay(sc, addrs, dumps, Replay(rec))


def hw_texel(xf, x, y, row):
    """Texel index floor(u / 2^16) the hardware addresses at pixel (x, y) (rendering.md §7.1, affine)."""
    a, b, c = xf[3 * row:3 * row + 3]
    return ((a * (2 * x + 1) + b * (2 * y + 1) + 2 * c) >> 1) >> 16


def seam_matrix(a_fix, x0, target_u, ax_y, rng):
    """1-D pattern-matrix row (scale, translation) whose base transform has XF_A (or XF_E) = a_fix and puts
    u (or v) of pixel x0 exactly at target_u (s15.16): searched over float32 translations, checked with
    the shared blit setup."""
    # u = floor(U / 2), U = a (2 x0 + 1) + 2 c: pick U = 2 target_u + (U odd)
    U = 2 * target_u + (a_fix & 1)
    c_fix = (U - a_fix * (2 * x0 + 1)) // 2
    s = np.float32(65536.0 / a_fix)
    t = np.float32(-c_fix / 65536.0 * float(s))
    for _ in range(4000):
        m = [[float(s), 0.0, float(t)], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        if ax_y:
            m = [[1.0, 0.0, 0.0], [0.0, float(s), float(t)], [0.0, 0.0, 1.0]]
        rc, xf, _, _ = gm.blit_setup(m, [0, 0, 1, 1], [-32768, -32768, 32767, 32767])
        if rc == 0 and xf[4 if ax_y else 0] == a_fix and xf[5 if ax_y else 2] == c_fix:
            return float(s), float(t)
        cur = xf[5 if ax_y else 2]
        t = np.nextafter(t, np.float32(np.inf) if (cur > c_fix) == (s > 0) else np.float32(-np.inf))
    raise AssertionError("no float32 translation gives the wanted fixed-point offset")


@pytest.mark.parametrize("mode", ["REPEAT", "REFLECT"])
def test_pattern_tile_seams(tmp_path, mode):
    """Pixels exactly on and one LSB before tile boundaries (rendering.md §9.5): every pixel of the rectangle
    gets exactly the texel of the base transform's u, v (wrapped or mirrored), with even and odd parity of
    the transform (the mirror rounding depends on it), in x and y, at the start of odd and even tiles."""
    rng = random.Random(7)
    iw, ih, w, h = 5, 3, 40, 24
    cases = [(a, off, k) for a in (131072, 65537) for off in (-1, 0) for k in (7, 8)]
    for part in (cases[:4], cases[4:]):
        sc = Scene()
        sc.buf(0, iw, ih, "BGRA8888")
        sc.add("coords 0")
        sc.add(f"img 0 {E['VG_LITE_NORMAL_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
        expects = []
        for d, (a, off, k) in enumerate(part, start=1):
            sc.buf(d, w, h, "BGRA8888")
            sx, tx = seam_matrix(a, 20, k * iw * 65536 + off, False, rng)
            # y: the same kind of boundary at row 12 (tile height ih)
            sy, ty = seam_matrix(a, 12, (k + 1) * ih * 65536 + off, True, rng)
            P = [[sx, 0.0, tx], [0.0, sy, ty], [0.0, 0.0, 1.0]]
            rc, xf, _, _ = gm.blit_setup(P, [0, 0, iw, ih], [-32768, -32768, 32767, 32767])
            assert rc == 0
            el = path_tokens("S16", [(0, 0), (w, 0), (w, h), (0, h)])
            sc.add(f"pattern {d} 0 {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} "
                   f"{E['VG_LITE_BLEND_NONE']} {E[f'VG_LITE_PATTERN_{mode}']} 0 0 {E['VG_LITE_FILTER_POINT']} "
                   f"1 0 0 0 1 0 0 0 1 {mat_str(P)} {len(el)} {' '.join(el)}")
            expects.append((d, xf))
        rcs, addrs, dumps, rec = run(sc, tmp_path)
        check_rcs(rcs)
        for d, xf in expects:
            img = np.frombuffer(dumps[d], np.uint8).reshape(h, addrs[d][1])[:, :4 * w].reshape(h, w, 4)
            for y in range(h):
                for x in range(w):
                    tu, tv = hw_texel(xf, x, y, 0), hw_texel(xf, x, y, 1)
                    if mode == "REPEAT":
                        iu, iv = tu % iw, tv % ih
                    else:
                        iu = tu % iw if (tu // iw) % 2 == 0 else iw - 1 - tu % iw
                        iv = tv % ih if (tv // ih) % 2 == 0 else ih - 1 - tv % ih
                    assert tuple(img[y, x]) == (iu, iv, 0x5A, 0xFF), (d, x, y, tuple(img[y, x]), iu, iv)
        check_replay(sc, addrs, dumps, Replay(rec))


def test_pattern_color_multiplied(tmp_path):
    """Locks the UNVERIFIED assumption of OPEN_QUESTIONS Q-API-12 as changed in v1.4 (rendering.md §7.5, §5.2): in
    COLOR mode a texel outside the image is `pattern_color` (BORDER_COLOR, used as given) and then goes through
    the image steps after filtering like every texel, so MULTIPLY image mode multiplies it by `color` (§5.3), as
    the image pixels. (v1.0-v1.3 drew the outside as a SOLID paint, unmultiplied.) If Q-API-12 is answered the
    other way, this test is the one to change."""
    iw, ih, w, h = 6, 5, 32, 24
    color = 0x80C04020            # vg_lite_color_t: A 0x80, B 0xC0, G 0x40, R 0x20
    pcolor = 0x9033AA55           # A 0x90, B 0x33, G 0xAA, R 0x55
    sc = Scene()
    sc.buf(0, iw, ih, "BGRA8888")
    sc.add("coords 0")
    sc.add(f"img 0 {E['VG_LITE_MULTIPLY_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
    sc.buf(1, w, h, "BGRA8888")
    sc.add(f"clear 1 0 0 -1 -1 {0x44112233}")
    P = [[2.0, 0.0, 9.0], [0.0, 2.0, 7.0], [0.0, 0.0, 1.0]]       # image at [9, 21) x [7, 17), axis-aligned
    el = path_tokens("S16", [(4, 3), (28, 3), (28, 21), (4, 21)])
    sc.add(f"pattern 1 0 {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} {E['VG_LITE_BLEND_NONE']} "
           f"{E['VG_LITE_PATTERN_COLOR']} {pcolor} {color} {E['VG_LITE_FILTER_POINT']} "
           f"1 0 0 0 1 0 0 0 1 {mat_str(P)} {len(el)} {' '.join(el)}")
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    img = np.frombuffer(dumps[1], np.uint8).reshape(h, addrs[1][1])[:, :4 * w].reshape(h, w, 4)
    cb = bgra(color)
    for y in range(h):
        for x in range(w):
            if not (4 <= x < 28 and 3 <= y < 21):
                exp = [0x11, 0x22, 0x33, 0x44]                                   # outside the path
            elif 9 <= x < 21 and 7 <= y < 17:
                tex = [(x - 9) // 2, (y - 7) // 2, 0x5A, 0xFF]
                exp = [refimpl.mul8(t, c) for t, c in zip(tex, cb)]               # image x color
            else:
                exp = [refimpl.mul8(p_, c) for p_, c in zip(bgra(pcolor), cb)]  # pattern colour x color
            assert list(img[y, x]) == exp, (x, y, list(img[y, x]), exp)
    check_replay(sc, addrs, dumps, Replay(rec))


@pytest.mark.parametrize("flt", ["POINT", "BI_LINEAR"])
@pytest.mark.parametrize("rot", [0.0, 0.37])
def test_pattern_border_premultiplied(tmp_path, flt, rot):
    """OPEN_QUESTIONS Q-API-19 (b), v1.5 (rendering.md §5.2 step 4): under apply_premult the COLOR-mode pattern colour
    (BORDER_COLOR) is premultiplied like the image texels. A constant, non-opaque image with the matching pattern
    colour therefore draws one uniform colour: the image edges (bilinear blend of image and border texels) equal the
    interior, every drawn pixel is the premultiplied colour. Until v1.4 the edge rows mixed in the unpremultiplied
    colour."""
    iw, ih, w, h = 5, 4, 40, 32
    argb = 0x80C06030                       # A 0x80, R 0xC0, G 0x60, B 0x30 (non-premultiplied)
    sc = Scene()
    sc.buf(0, iw, ih, "BGRA8888", 64)
    raw = bytes([0x30, 0x60, 0xC0, 0x80]) * iw + bytes(64 - 4 * iw)
    (tmp_path / "const.bin").write_bytes(raw * ih)
    sc.add(f"load 0 {tmp_path / 'const.bin'}")
    sc.add(f"img 0 {E['VG_LITE_NORMAL_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 1")
    sc.buf(1, w, h, "BGRA8888")
    sc.add(f"clear 1 0 0 -1 -1 {0x44112233}")
    c, s_ = 4.0 * math.cos(rot), 4.0 * math.sin(rot)
    P = [[c, -s_, 12.0], [s_, c, 6.0], [0.0, 0.0, 1.0]]           # image scaled 4x (rotated), inside the target
    el = path_tokens("S16", [(0, 0), (w, 0), (w, h), (0, h)])
    pcolor = 0x803060C0                 # vg_lite_color_t A<<24 | B<<16 | G<<8 | R
    sc.add(f"pattern 1 0 {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} {E['VG_LITE_BLEND_NONE']} "
           f"{E['VG_LITE_PATTERN_COLOR']} {pcolor} 0 {E[f'VG_LITE_FILTER_{flt}']} "
           f"1 0 0 0 1 0 0 0 1 {mat_str(P)} {len(el)} {' '.join(el)}")
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    img = np.frombuffer(dumps[1], np.uint8).reshape(h, addrs[1][1])[:, :4 * w].reshape(h, w, 4)
    pm = int(refimpl.premultiply(argb))
    exp = [pm & 255, (pm >> 8) & 255, (pm >> 16) & 255, pm >> 24]   # B, G, R, A bytes
    assert (img == exp).all(axis=2).all(), np.argwhere((img != exp).any(axis=2))[:5].tolist()
    check_replay(sc, addrs, dumps, Replay(rec))


@pytest.mark.parametrize("flt", ["POINT", "BI_LINEAR"])
def test_pattern_color_solid(tmp_path, flt):
    """vg_lite_g2d_set_pattern_color_mode(SOLID) (PAINT_CTRL.BORDER_SOLID, rendering.md §7.5, user 2026-10-24): the
    v1.0-v1.3 COLOR behaviour. A pixel whose sample centre is outside the image gets pattern_color as a plain colour
    (not multiplied by `color` in MULTIPLY image mode, no filtering); inside pixels are the image x color, with
    bilinear neighbours clamped to the image. Then TEXEL mode again (the mode is a driver state)."""
    iw, ih, w, h = 6, 5, 32, 24
    color = 0x80C04020
    pcolor = 0x9033AA55
    sc = Scene()
    sc.buf(0, iw, ih, "BGRA8888")
    sc.add("coords 0")
    sc.add(f"img 0 {E['VG_LITE_MULTIPLY_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
    sc.buf(1, w, h, "BGRA8888")
    sc.buf(2, w, h, "BGRA8888")
    P = [[2.0, 0.0, 9.0], [0.0, 2.0, 7.0], [0.0, 0.0, 1.0]]       # image at [9, 21) x [7, 17), axis-aligned
    el = path_tokens("S16", [(4, 3), (28, 3), (28, 21), (4, 21)])
    for t, mode in ((1, 1), (2, 0)):
        sc.add(f"pcmode {mode}")
        sc.add(f"clear {t} 0 0 -1 -1 {0x44112233}")
        sc.add(f"pattern {t} 0 {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} "
               f"{E['VG_LITE_BLEND_NONE']} {E['VG_LITE_PATTERN_COLOR']} {pcolor} {color} {E[f'VG_LITE_FILTER_{flt}']} "
               f"1 0 0 0 1 0 0 0 1 {mat_str(P)} {len(el)} {' '.join(el)}")
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    cb = bgra(color)
    img = np.frombuffer(dumps[1], np.uint8).reshape(h, addrs[1][1])[:, :4 * w].reshape(h, w, 4)
    for y in range(h):
        for x in range(w):
            if not (4 <= x < 28 and 3 <= y < 21):
                exp = [0x11, 0x22, 0x33, 0x44]                                   # outside the path
            elif 9 <= x < 21 and 7 <= y < 17:
                u, v = (x + 0.5 - 9) / 2 * 65536, (y + 0.5 - 7) / 2 * 65536
                tex = bilinear_texel(int(u), int(v), iw, ih, "PAD") if flt == "BI_LINEAR" else \
                    [(x - 9) // 2, (y - 7) // 2, 0x5A, 0xFF]
                exp = [refimpl.mul8(t_, c) for t_, c in zip(tex, cb)]            # image x color, clamped
            else:
                exp = bgra(pcolor)                                               # plain pattern colour
            assert list(img[y, x]) == exp, (flt, x, y, list(img[y, x]), exp)
    # TEXEL mode (default, restored): outside pixels are pattern colour x color
    img2 = np.frombuffer(dumps[2], np.uint8).reshape(h, addrs[2][1])[:, :4 * w].reshape(h, w, 4)
    assert list(img2[3, 5]) == [refimpl.mul8(p_, c) for p_, c in zip(bgra(pcolor), cb)]
    check_replay(sc, addrs, dumps, Replay(rec))


@pytest.mark.parametrize("quality", ["VG_LITE_HIGH", "VG_LITE_LOW"])
def test_pattern_fractional_rect(tmp_path, quality):
    """OPEN_QUESTIONS Q-API-14, rendering.md §9.5 (v1.5, F-1, exact): a rectangular pattern path at fractional
    positions with AA is drawn as a DRAW_PATH with the image paint, so its border pixels get the exact path coverage
    (16 samples, §8): O = lerp8(D, texel, cov) under blend NONE, with cov from the literal per-sample reference;
    without AA the pixel centre decides (the blit, as before). Every pixel is checked."""
    iw, ih, w, h = 16, 16, 48, 40
    sc = Scene()
    sc.buf(0, iw, ih, "BGRA8888")
    sc.add("coords 0")
    sc.add(f"img 0 {E['VG_LITE_NORMAL_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
    sc.buf(1, w, h, "BGRA8888")
    sc.add(f"clear 1 0 0 -1 -1 {0x44112233}")
    P = [[2.0, 0.0, 8.0], [0.0, 2.0, 6.0], [0.0, 0.0, 1.0]]       # image at [8, 40) x [6, 38)
    xl, xr, ya, yb = 10.3125, 30.5, 7.5, 20.25                      # exact in s.4 and FP32
    el = path_tokens("FP32", [(xl, ya), (xr, ya), (xr, yb), (xl, yb)])
    sc.add(f"pattern 1 0 {E['VG_LITE_FP32']} {E[quality]} {E['VG_LITE_FILL_NON_ZERO']} {E['VG_LITE_BLEND_NONE']} "
           f"{E['VG_LITE_PATTERN_COLOR']} 0 {0xFFFFFFFF} {E['VG_LITE_FILTER_POINT']} "
           f"1 0 0 0 1 0 0 0 1 {mat_str(P)} {len(el)} {' '.join(el)}")
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    img = np.frombuffer(dumps[1], np.uint8).reshape(h, addrs[1][1])[:, :4 * w].reshape(h, w, 4)
    aa = quality != "VG_LITE_LOW"
    q = [(int(16 * xl), int(16 * ya)), (int(16 * xr), int(16 * ya)), (int(16 * xr), int(16 * yb)), (int(16 * xl), int(16 * yb))]
    edges = [(*q[i], *q[(i + 1) % 4]) for i in range(4)]
    div255r = lambda v: (v + 128 + ((v + 128) >> 8)) >> 8  # noqa: E731  rendering.md §2
    partial = 0
    for y in range(h):
        for x in range(w):
            if aa:
                cov = refimpl.pixel_coverage(edges, x, y, True, False)
            else:
                cov = 255 if xl <= x + 0.5 < xr and ya <= y + 0.5 < yb else 0
            d = [0x11, 0x22, 0x33, 0x44]
            t = [(x - 8) // 2, (y - 6) // 2, 0x5A, 0xFF] if cov else d
            exp = t if cov in (0, 255) else [div255r(t[k] * cov + d[k] * (255 - cov)) for k in range(4)]
            partial += 0 < cov < 255
            assert list(img[y, x]) == exp, (x, y, cov, list(img[y, x]), exp)
    assert (partial > 0) == aa
    check_replay(sc, addrs, dumps, Replay(rec))


def bilinear_texel(u, v, iw, ih, mode):
    """rendering.md §7.3 / §7.5 on the coordinate image (texel (x, y) = B x, G y, R 0x5A, A 0xFF): neighbours
    clamped to the image (PAD), wrapped modulo the image size (REPEAT) or mirrored (REFLECT)."""
    up, vp = u - 32768, v - 32768
    x0, y0, fx, fy = up >> 16, vp >> 16, (up >> 8) & 0xFF, (vp >> 8) & 0xFF

    def fix(c, n):
        if mode == "REPEAT":
            return c % n
        if mode == "REFLECT":
            k = c % (2 * n)
            return k if k < n else 2 * n - 1 - k
        return min(max(c, 0), n - 1)
    out = []
    for ch in range(4):
        acc = 0
        for dy, wy in ((0, 256 - fy), (1, fy)):
            for dx, wx in ((0, 256 - fx), (1, fx)):
                tx, ty = fix(x0 + dx, iw), fix(y0 + dy, ih)
                acc += (tx, ty, 0x5A, 0xFF)[ch] * wx * wy
        out.append((acc + 32768) >> 16)
    return out


@pytest.mark.parametrize("mode", ["REPEAT", "REFLECT"])
def test_pattern_bilinear_seams(tmp_path, mode):
    """Bilinear at the wrap seams (v1.4, rendering.md §7.5, F-2 closed): the neighbours of a texel wrap (REPEAT) or
    mirror (REFLECT) into the next period, bit-exact against §7.3 / §7.5 on the base transform. Up to v1.3 every
    tile was its own blit and the neighbours clamped at the seam; the test checks that the seams really differ
    from that clamp."""
    iw, ih, w, h = 5, 4, 40, 28
    sc = Scene()
    sc.buf(0, iw, ih, "BGRA8888")
    sc.add("coords 0")
    sc.add(f"img 0 {E['VG_LITE_NORMAL_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
    sc.buf(1, w, h, "BGRA8888")
    a = 0.3
    P = f32m([[1.7 * math.cos(a), -1.7 * math.sin(a), 3.3], [1.7 * math.sin(a), 1.7 * math.cos(a), -2.6], [0, 0, 1]])
    el = path_tokens("S16", [(0, 0), (w, 0), (w, h), (0, h)])
    sc.add(f"pattern 1 0 {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_EVEN_ODD']} {E['VG_LITE_BLEND_NONE']} "
           f"{E[f'VG_LITE_PATTERN_{mode}']} 0 0 {E['VG_LITE_FILTER_BI_LINEAR']} "
           f"1 0 0 0 1 0 0 0 1 {mat_str(P)} {len(el)} {' '.join(el)}")
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    rc, xf, _, _ = gm.blit_setup(P, [0, 0, iw, ih], [-32768, -32768, 32767, 32767])
    assert rc == 0
    img = np.frombuffer(dumps[1], np.uint8).reshape(h, addrs[1][1])[:, :4 * w].reshape(h, w, 4)
    seam_diff = 0
    for y in range(h):
        for x in range(w):
            u = (xf[0] * (2 * x + 1) + xf[1] * (2 * y + 1) + 2 * xf[2]) >> 1        # base transform, §7.1
            v = (xf[3] * (2 * x + 1) + xf[4] * (2 * y + 1) + 2 * xf[5]) >> 1
            exp = bilinear_texel(u, v, iw, ih, mode)
            assert list(img[y, x]) == exp, (x, y, list(img[y, x]), exp)
            # the v1.3 tile blit: the tile's own coordinates, neighbours clamped at the tile border
            k, l = (u >> 16) // iw, (v >> 16) // ih
            mx, my = mode == "REFLECT" and k % 2, mode == "REFLECT" and l % 2
            ut = (k + 1) * iw * 65536 - 1 - u if mx else u - k * iw * 65536
            vt = (l + 1) * ih * 65536 - 1 - v if my else v - l * ih * 65536
            seam_diff += exp != bilinear_texel(ut, vt, iw, ih, "PAD")
    assert seam_diff > 0, "no seam pixel where wrapping and the old clamp differ: the test lost its point"
    check_replay(sc, addrs, dumps, Replay(rec))


@pytest.mark.parametrize("spread", ["PAD", "REPEAT", "REFLECT", "FILL"])
def test_gradient_spread_through_driver(tmp_path, spread):
    """Backs gcFEATURE_BIT_VG_IM_REPEAT_REFLECT = 1: extended linear gradients with each spread mode through
    the API. Bit-exact against the independent replay of the recorded stream, and the ramp position of every
    pixel against the float definition (rendering.md §10.2: index floor(256 (g - x0) / L), clamped, wrapped
    or mirrored), within one ramp entry (circular at the REPEAT wrap). FILL (v1.4, Q-API-6): outside the ramp the
    draw's paint_color, exactly; inside as PAD."""
    w, h = 64, 48
    rng = random.Random(41 + len(spread))
    sc = Scene()
    sc.buf(0, w, h, "BGRA8888")
    gpaint = 0xFF7F5500                       # A FF, B 7F, G 55, R 00: G marks "outside" (the ramp has G = 0)
    sc.add(f"gpaint {gpaint}")
    el = path_tokens("S16", [(0, 0), (w, 0), (w, h), (0, h)])
    for trial in range(3):
        a, L = rng.uniform(-math.pi, math.pi), rng.uniform(6, 30)
        x0, y0 = f32(rng.uniform(0, w)), f32(rng.uniform(0, h))
        x1, y1 = f32(x0 + L * math.cos(a)), f32(y0 + L * math.sin(a))
        stops = [[0.0, 0.0, 0.0, 0.0, 1.0], [1.0, 0.0, 0.0, 1.0, 1.0]]       # blue = ramp position
        sc.add(f"xgrad 0 {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} {E['VG_LITE_BLEND_NONE']} "
               f"{E[f'VG_LITE_GRADIENT_SPREAD_{spread}']} 0 1 0 0 0 1 0 0 0 1 1 0 0 0 1 0 0 0 1 "
               f"{x0!r} {y0!r} {x1!r} {y1!r} 2 " + " ".join(" ".join(repr(v) for v in s_) for s_ in stops) +
               f" {len(el)} {' '.join(el)}")
        sc.add(f"dump 0 {tmp_path / f'g{trial}.bin'}")
        rcs, addrs, dumps, rec = run(sc, tmp_path)
        check_rcs(rcs)
        check_replay(sc, addrs, dumps, Replay(rec))
        img = np.frombuffer((tmp_path / f"g{trial}.bin").read_bytes(), np.uint8).reshape(h, addrs[0][1])[:, :4 * w]
        blue = img.reshape(h, w, 4)[:, :, 0].astype(np.int64)
        py, px = np.mgrid[0:h, 0:w] + 0.5
        dx, dy = x1 - x0, y1 - y0
        g = ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy)          # 0 at (x0, y0), 1 at (x1, y1)
        i = np.floor(256 * g).astype(np.int64)
        idx = {"PAD": np.clip(i, 0, 255), "REPEAT": i & 255, "FILL": np.clip(i, 0, 255),
               "REFLECT": np.where((i & 511) < 256, i & 511, 511 - (i & 511))}[spread]
        exp = np.floor((idx + 0.5) / 256 * 255 + 0.5)                          # ramp entry value (rha)
        d = np.abs(blue - exp)
        if spread == "REPEAT":
            d = np.minimum(d, 255 - d)
        if spread == "FILL":
            green = img.reshape(h, w, 4)[:, :, 1].astype(np.int64)
            far = (256 * g < -0.01) | (256 * g >= 256.01)                     # clearly outside the ramp
            near_ = (np.abs(256 * g) < 0.01) | (np.abs(256 * g - 256) < 0.01)
            assert (green[far] == 0x55).all() and (blue[far] == 0x7F).all(), (trial, "FILL outside")
            assert (green[~far & ~near_] == 0).all(), (trial, "FILL inside")
            d = np.where(far | near_, 0, d)
        assert d.max() <= 1.5, (spread, trial, d.max())
        sc.lines = sc.lines[:2]                                                # next trial: fresh script


# ------------------------------------------------------------------------------------------------
# 6. source formats other than 8888: read by the hardware (v1.4, rendering.md §3.2, §3.5) or converted by the
#    driver (24-bit, §9.6), against an independently decoded 8-bit / 32-bit image
# ------------------------------------------------------------------------------------------------

_SEQ = {"RGBA": "RGBA", "BGRA": "BGRA", "ARGB": "ARGB", "ABGR": "ABGR"}
_W24 = {"RGB888": [("R", 8), ("G", 8), ("B", 8)], "BGR888": [("B", 8), ("G", 8), ("R", 8)],
        "ARGB8565": [("A", 8), ("R", 5), ("G", 6), ("B", 5)], "ABGR8565": [("A", 8), ("B", 5), ("G", 6), ("R", 5)],
        "BGRA5658": [("B", 5), ("G", 6), ("R", 5), ("A", 8)], "RGBA5658": [("R", 5), ("G", 6), ("B", 5), ("A", 8)]}


def _fields(fmt):
    """[(channel, width)] from bit 0, from the format name (vg_lite.h: channels from the LSB in name order)."""
    if fmt in _W24:
        return _W24[fmt]
    if fmt == "A8L8":                      # v1.6: A in bits 7:0, L in bits 15:8
        return [("A", 8), ("L", 8)]
    name, digits = fmt[:4], fmt[4:]
    return [(ch, int(d)) for ch, d in zip(name, digits)]


def _scale(c, n):
    return c * 255 // ((1 << n) - 1) if n in (1, 2, 4, 8) else (c << (8 - n)) | (c >> (2 * n - 8))


def expand_ref(fmt, raw, w, h, stride, clut=None, endian_big=False):
    """Independent decode into (bytes, reference format, reference stride): A4 / A2 / A1 -> A8, L4 -> L8 (first
    pixel in the high bits, LVGL's layout), indexed -> BGRA8888 through the CLUT (index_endian), the packed and
    24-bit formats -> BGRA8888."""
    rows = np.frombuffer(raw, np.uint8).reshape(h, stride)
    out = None
    if fmt in ("A4", "A2", "A1", "L4") or fmt in INDEXED:
        bits = BITS[fmt]
        per = 8 // bits
        idx = np.zeros((h, w), np.uint32)
        for x in range(w):
            j = x % per
            hi_first = endian_big if fmt in INDEXED else True
            sh = 8 - bits * (j + 1) if hi_first else bits * j
            idx[:, x] = (rows[:, x // per] >> sh) & ((1 << bits) - 1)
        if fmt in INDEXED:
            words = np.asarray(clut, np.uint32)[idx]
            b, g, r, a = words & 255, (words >> 8) & 255, (words >> 16) & 255, words >> 24
        else:
            v = idx * (255 // ((1 << bits) - 1))
            ref_stride = (w + 63) & ~63
            out = np.zeros((h, ref_stride), np.uint8)
            out[:, :w] = v
            return out.tobytes(), ("A8" if fmt[0] == "A" else "L8"), ref_stride
    else:
        nbytes = sum(n for _, n in _fields(fmt)) // 8
        v = rows[:, : nbytes * w].reshape(h, w, nbytes).astype(np.uint32)
        val = sum(v[:, :, k] << (8 * k) for k in range(nbytes))
        ch, pos = {"A": np.full((h, w), 255, np.uint32)}, 0
        for c, n in _fields(fmt):
            ch[c] = _scale((val >> pos) & ((1 << n) - 1), n)
            pos += n
        if "L" in ch:
            ch["R"] = ch["G"] = ch["B"] = ch["L"]
        b, g, r, a = ch["B"], ch["G"], ch["R"], ch["A"]
    ref_stride = (4 * w + 63) & ~63
    out = np.zeros((h, ref_stride), np.uint8)
    out[:, : 4 * w] = np.stack([b, g, r, a], -1).reshape(h, 4 * w)
    return out.tobytes(), "BGRA8888", ref_stride


NATIVE_V14 = ["A4", "A2", "A1", "L4", "I8", "I4", "I2", "I1", "BGRA5551", "RGBA5551", "ARGB1555", "ABGR1555",
              "BGRA4444", "RGBA4444", "ARGB4444", "ABGR4444", "BGRA2222", "RGBA2222", "ARGB2222", "ABGR2222"]
CONV_FMTS = list(_W24)                                     # converted by the driver until v1.6, hardware since v1.6b
SRC_V14 = NATIVE_V14 + CONV_FMTS + ["A8L8"]              # A8L8: native since v1.6


def vg_enum(fmt):
    return E[f"VG_LITE_{VG_NAME.get(fmt, fmt)}"]


@pytest.mark.parametrize("fmt", SRC_V14)
def test_source_formats_vs_decoded(tmp_path, fmt):
    """A source in fmt draws exactly like the independently decoded 8- / 32-bit image: blits (random matrices,
    both filters, random blends, image modes, premultiply) and a REPEAT pattern, all bit-exact; indexed sources
    are point-sampled even when BILINEAR is asked for (rendering.md §3.5), with both index_endian values; the
    stream replays bit-exactly; the conversion cache (24-bit) follows content changes; source-only and converted
    formats are rejected as targets."""
    rng = random.Random(SEED * 50 + SRC_V14.index(fmt))
    sw, sh, tw, th = rng.randint(3, 33), rng.randint(3, 25), 40, 32
    bits = BITS[fmt] if fmt in BITS else 24
    indexed = fmt in INDEXED
    ntrial = 3 if fmt in CONV_FMTS else 2
    # 1st run: allocate, fill and dump the source (its stride comes from the driver)
    sc0 = Scene()
    sc0.lines.append(f"buf 1 {sw} {sh} {vg_enum(fmt)} 0")
    sc0.bufs[1] = (sw, sh, "A8")             # dump only; size from the stride below
    seed_fill, seed_b = rng.getrandbits(31), rng.getrandbits(31)
    sc0.lines.append(f"fill 1 {seed_fill}")
    sc0.lines.append(f"buf 5 {sw} {sh} {vg_enum(fmt)} 0")
    sc0.bufs[5] = (sw, sh, "A8")
    sc0.lines.append(f"fill 5 {seed_b}")                  # the content after the change of trial 2
    _, addrs0, _, _ = run(sc0, tmp_path)
    stride = addrs0[1][1]
    assert stride == (((sw * bits + 7) // 8) + 63) & ~63
    raw = (tmp_path / "buf1.bin").read_bytes()[: stride * sh]
    raw_b = (tmp_path / "buf5.bin").read_bytes()[: stride * sh]
    for trial in range(ntrial):
        n = 1 << bits if indexed else 0
        clut = [rng.getrandbits(32) & (0x00FF_FFFF if rng.random() < 0.1 else 0xFFFF_FFFF) for _ in range(n)]
        big = rng.random() < 0.5
        ref, ref_fmt, _ = expand_ref(fmt, raw, sw, sh, stride, clut + [0] * (256 - n), big)
        (tmp_path / "ref.bin").write_bytes(ref)
        sc = Scene()
        sc.buf(0, tw, th, "BGRA8888")                     # target fed by the source in fmt
        sc.buf(2, tw, th, "BGRA8888")                     # target fed by the reference image
        sc.lines.append(f"buf 1 {sw} {sh} {vg_enum(fmt)} 0")
        sc.lines.append(f"fill 1 {seed_fill}")
        sc.lines.append(f"buf 3 {sw} {sh} {VG_FMT[ref_fmt]} 0")
        sc.lines.append(f"load 3 {tmp_path / 'ref.bin'}")
        if indexed:
            sc.add(f"endian 1 {E['VG_LITE_INDEX_BIG_ENDIAN'] if big else E['VG_LITE_INDEX_LITTLE_ENDIAN']}")
            sc.add(f"clut {n} " + " ".join(map(str, clut)))
        fill_t = rng.getrandbits(31)
        sc.add(f"fill 0 {fill_t}")
        sc.add(f"fill 2 {fill_t}")
        mode = rng.choice([E["VG_LITE_NORMAL_IMAGE_MODE"], E["VG_LITE_MULTIPLY_IMAGE_MODE"]])
        premul = int(rng.random() < 0.5)
        transp = rng.choice([E["VG_LITE_IMAGE_OPAQUE"], E["VG_LITE_IMAGE_TRANSPARENT"]])
        for b in (1, 3):
            sc.add(f"img {b} {mode} {transp} {premul}")
        pt, bl = E["VG_LITE_FILTER_POINT"], E["VG_LITE_FILTER_BI_LINEAR"]
        for _ in range(3):
            m = rand_matrix(rng, sw, sh, tw, th, persp=rng.random() < 0.3)
            flt = rng.choice([pt, bl])
            blend, color = rng.choice(BLENDS), rng.getrandbits(32)
            for t, s, f_ in ((0, 1, flt), (2, 3, pt if indexed else flt)):
                sc.add(f"blit {t} {s} {blend} {color} {f_} {mat_str(m)}")
                sc.expect.append(blit_expect(m, sw, sh, tw, th))
        el = path_tokens("S16", [(2, 2), (tw - 3, 2), (tw - 3, th - 1), (2, th - 1)])
        P = rand_matrix(rng, sw, sh, tw, th)
        for t, s, f_ in ((0, 1, bl), (2, 3, pt if indexed else bl)):
            sc.add(f"pattern {t} {s} {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} "
                   f"{E['VG_LITE_BLEND_SRC_OVER']} {E['VG_LITE_PATTERN_REPEAT']} 0 0 {f_} "
                   f"1 0 0 0 1 0 0 0 1 {mat_str(P)} {len(el)} {' '.join(el)}")
        if trial == 2:      # content change between draws of the same buffer (v1.3-v1.6: the conversion cache)
            ref_b, _, _ = expand_ref(fmt, raw_b, sw, sh, stride)
            (tmp_path / "ref_b.bin").write_bytes(ref_b)
            sc.add("finish")
            sc.add(f"fill 1 {seed_b}")
            sc.add(f"load 3 {tmp_path / 'ref_b.bin'}")
            for b in (1, 3):                    # NORMAL image mode: the draw must show the new content
                sc.add(f"img {b} {E['VG_LITE_NORMAL_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
            m = rand_matrix(rng, sw, sh, tw, th)
            for t, s_ in ((0, 1), (2, 3)):
                sc.add(f"blit {t} {s_} {E['VG_LITE_BLEND_SRC_OVER']} 0 {E['VG_LITE_FILTER_BI_LINEAR']} {mat_str(m)}")
                sc.expect.append(blit_expect(m, sw, sh, tw, th))
        rcs, addrs, dumps, rec = run(sc, tmp_path)
        check_blit_rcs(sc, rcs)
        assert dumps[0] == dumps[2], f"{fmt} trial {trial}: source differs from the decoded reference"
        check_replay(sc, addrs, dumps, Replay(rec))
    if fmt in BITS and FMT[fmt] in TGT_FMTS:
        return                              # also a target format (tested as such by the other scenes)
    sc = Scene()
    sc.lines.append(f"buf 4 16 8 {vg_enum(fmt)} 0")
    sc.add(f"clear 4 0 0 -1 -1 {0xFF0000FF}")
    rcs, _, _, _ = run(sc, tmp_path)
    assert [c for n, c in rcs if n == "clear"] == [E["VG_LITE_NOT_SUPPORT"]]


def test_transparent_skips_alpha0(tmp_path):
    """transparency_mode = VG_LITE_IMAGE_TRANSPARENT (v1.4, OPEN_QUESTIONS Q-API-11, UNVERIFIED): pixels whose
    sampled source has A = 0 are not written, even under blend NONE; OPAQUE writes them. Checked per pixel on an
    A8 source over a known background."""
    w, h = 24, 12
    sc = Scene()
    sc.buf(0, w, h, "BGRA8888")
    sc.buf(1, w, h, "BGRA8888")
    sc.buf(2, w, h, "A8")
    stride = (w + 63) & ~63                                    # vg_lite_allocate: align64(width)
    data = bytes(0 if (x + 2 * y) % 5 == 0 else (x * 11 + y * 7) % 255 + 1 for y in range(h) for x in range(stride))
    (tmp_path / "a8.bin").write_bytes(data)
    sc.add(f"load 2 {tmp_path / 'a8.bin'}")
    bg = 0xFF203040
    for t, tr in ((0, "TRANSPARENT"), (1, "OPAQUE")):
        sc.add(f"clear {t} 0 0 -1 -1 {bg}")
        sc.add(f"img 2 {E['VG_LITE_NORMAL_IMAGE_MODE']} {E[f'VG_LITE_IMAGE_{tr}']} 0")
        sc.add(f"blit {t} 2 {E['VG_LITE_BLEND_NONE']} 0 {E['VG_LITE_FILTER_POINT']} 1 0 0 0 1 0 0 0 1")
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    a = np.frombuffer(dumps[2], np.uint8).reshape(h, addrs[2][1])[:, :w]
    for t in (0, 1):
        out = np.frombuffer(dumps[t], np.uint8).reshape(h, addrs[t][1])[:, :4 * w].reshape(h, w, 4)
        bgb = np.array(bgra(bg), np.uint8)                     # B, G, R, A bytes of the background
        kept = (out == bgb).all(axis=2)
        if t == 0:
            assert (kept == (a == 0)).all(), "TRANSPARENT: exactly the A = 0 pixels keep the background"
        else:
            assert not kept[a == 0].any(), "OPAQUE: A = 0 pixels are written (premultiplied white x 0 = 0)"
    assert (a == 0).any(), "no A = 0 texel: the test lost its point"
    check_replay(sc, addrs, dumps, Replay(rec))


def test_dither_formula(tmp_path):
    """vg_lite_enable_dither (v1.4, rendering.md §3.6): a dithered clear of a constant colour on every target with
    channels below 8 bits matches the ordered-dither formula per pixel position, computed here independently;
    8-bit targets are unchanged by dither; disable_dither restores plain rounding."""
    rng = random.Random(SEED + 9)
    color = rng.getrandbits(32)
    bayer = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
    for fmt in ("RGB565", "BGR565", "BGRA4444", "BGRA5551", "ARGB1555", "BGRA2222", "BGRA8888", "L8"):
        w, h = 13, 9
        sc = Scene()
        sc.buf(0, w, h, fmt)
        sc.buf(1, w, h, fmt)
        sc.add("dither 1")
        sc.add(f"clear 0 0 0 -1 -1 {color}")
        sc.add("dither 0")
        sc.add(f"clear 1 0 0 -1 -1 {color}")
        rcs, addrs, dumps, rec = run(sc, tmp_path)
        check_rcs(rcs)
        nb = BITS[fmt] // 8
        a_, r_, g_, b_ = color >> 24, color & 255, (color >> 8) & 255, (color >> 16) & 255
        for t, dith in ((0, True), (1, False)):
            rows = np.frombuffer(dumps[t], np.uint8).reshape(h, addrs[t][1])
            for y in range(h):
                for x in range(w):
                    got = int.from_bytes(rows[y, nb * x: nb * x + nb].tobytes(), "little")
                    d = bayer[y & 3][x & 3]
                    q = (lambda c, n: (c * ((1 << n) - 1) * 32 + 255 * (2 * d + 1)) // 8160) if dith else \
                        (lambda c, n: (c * ((1 << n) - 1) + 127) // 255)
                    plain = lambda c, n: (c * ((1 << n) - 1) + 127) // 255  # noqa: E731
                    if fmt in ("RGB565", "BGR565"):
                        lo, hi = (r_, b_) if fmt == "RGB565" else (b_, r_)
                        exp = q(lo, 5) | (q(g_, 6) << 5) | (q(hi, 5) << 11)
                    elif fmt == "BGRA4444":
                        exp = q(b_, 4) | (q(g_, 4) << 4) | (q(r_, 4) << 8) | (plain(a_, 4) << 12)
                    elif fmt == "BGRA5551":
                        exp = q(b_, 5) | (q(g_, 5) << 5) | (q(r_, 5) << 10) | (plain(a_, 1) << 15)
                    elif fmt == "ARGB1555":
                        exp = plain(a_, 1) | (q(r_, 5) << 1) | (q(g_, 5) << 6) | (q(b_, 5) << 11)
                    elif fmt == "BGRA2222":
                        exp = q(b_, 2) | (q(g_, 2) << 2) | (q(r_, 2) << 4) | (plain(a_, 2) << 6)
                    elif fmt == "BGRA8888":
                        exp = b_ | (g_ << 8) | (r_ << 16) | (a_ << 24)
                    else:
                        exp = (77 * r_ + 150 * g_ + 29 * b_ + 128) >> 8
                    assert got == exp, (fmt, dith, x, y, hex(got), hex(exp))
        check_replay(sc, addrs, dumps, Replay(rec))


@pytest.mark.parametrize("blend", ["NORMAL_LVGL", "SRC_OVER"])
def test_a8_recolor_vs_ideal(tmp_path, blend):
    """B-6.2 regression: an A8 image in MULTIPLY mode with a premultiplied colour (LVGL's recolour / glyph
    path) gives the ideal c*a + D*(1 - a) under both NORMAL_LVGL (LVGL images) and SRC_OVER (LVGL glyphs).
    The A8 texel is plain white under the LVGL blends and premultiplied white under SRC_OVER (Q-API-3); the
    old driver premultiplied under every blend, so NORMAL_LVGL applied the alpha twice (up to 64 levels)."""
    w, h = 32, 16
    sc = Scene()
    sc.buf(0, w, h, "BGRA8888")
    sc.buf(1, w, h, "A8")
    sc.add(f"fill 1 {77}")
    sc.add(f"img 1 {E['VG_LITE_MULTIPLY_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
    bg = 0xFF8090A0                                     # A FF, B 80, G 90, R A0
    sc.add(f"clear 0 0 0 -1 -1 {bg}")
    color = 0xFF40D0FF                                  # opaque: premultiplied = itself (B 40, G D0, R FF)
    sc.add(f"blit 0 1 {E[f'VG_LITE_BLEND_{blend}']} {color} {E['VG_LITE_FILTER_POINT']} 1 0 0 0 1 0 0 0 1")
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    a = np.frombuffer(dumps[1], np.uint8).reshape(h, addrs[1][1])[:, :w].astype(float) / 255
    out = np.frombuffer(dumps[0], np.uint8).reshape(h, addrs[0][1])[:, :4 * w].reshape(h, w, 4).astype(float)
    for ch, (c, d) in enumerate(((0x40, 0x80), (0xD0, 0x90), (0xFF, 0xA0))):      # B, G, R
        ideal = c * a + d * (1 - a)
        assert np.abs(out[:, :, ch] - ideal).max() <= 1.5, (blend, ch, np.abs(out[:, :, ch] - ideal).max())
    check_replay(sc, addrs, dumps, Replay(rec))


# ---- scene generators for the RTL backends (test_live.py, dv/tests/test_sys06.py) --------------------------------

def scene_converted(rng):
    """Blits and a REPEAT / REFLECT / PAD / COLOR pattern from sources in the v1.4 formats (sub-byte, indexed, packed
    16 / 8 bit read by the hardware, 24-bit converted by the driver until v1.6, read by the hardware since v1.6b),
    with dither on or off."""
    sc = Scene()
    tw, th = rng.randint(16, 40), rng.randint(16, 32)
    sc.buf(0, tw, th, rng.choice(["BGRA8888", "RGB565", "ARGB8888"]))
    sc.add(f"fill 0 {rng.getrandbits(31)}")
    for b, fmt in enumerate(rng.sample(SRC_V14, 2), start=1):
        sw_, sh_ = rng.randint(2, 20), rng.randint(2, 16)
        sc.lines.append(f"buf {b} {sw_} {sh_} {vg_enum(fmt)} 0")
        sc.lines.append(f"fill {b} {rng.getrandbits(31)}")
        if fmt in INDEXED:
            sc.add(f"endian {b} {rng.choice([E['VG_LITE_INDEX_LITTLE_ENDIAN'], E['VG_LITE_INDEX_BIG_ENDIAN']])}")
            sc.add(clut_line(rng, 1 << BITS[fmt]))
        sc.add(f"img {b} {rng.choice([E['VG_LITE_NORMAL_IMAGE_MODE'], E['VG_LITE_MULTIPLY_IMAGE_MODE']])} "
               f"{E['VG_LITE_IMAGE_OPAQUE']} {int(rng.random() < 0.3)}")
        for _ in range(2):
            m = rand_matrix(rng, sw_, sh_, tw, th, persp=rng.random() < 0.3)
            sc.add(f"blit 0 {b} {rng.choice(BLENDS)} {rng.getrandbits(32)} "
                   f"{rng.choice([E['VG_LITE_FILTER_POINT'], E['VG_LITE_FILTER_BI_LINEAR']])} {mat_str(m)}")
            sc.expect.append(blit_expect(m, sw_, sh_, tw, th))
        el = path_tokens("S16", [(1, 1), (tw - 1, 1), (tw - 1, th - 1), (1, th - 1)])
        pm = rng.choice(["REPEAT", "REFLECT", "PAD", "COLOR"])
        sc.add(f"dither {int(rng.random() < 0.4)}")
        sc.add(f"pcmode {rng.randrange(2)}")
        sc.add(f"pattern 0 {b} {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} {rng.choice(BLENDS)} "
               f"{E[f'VG_LITE_PATTERN_{pm}']} {rng.getrandbits(32)} {rng.getrandbits(32)} "
               f"{rng.choice([E['VG_LITE_FILTER_POINT'], E['VG_LITE_FILTER_BI_LINEAR']])} "
               f"1 0 0 0 1 0 0 0 1 {mat_str(rand_matrix(rng, sw_, sh_, tw, th, persp=rng.random() < 0.3))} "
               f"{len(el)} {' '.join(el)}")
    sc.add("dither 0")
    sc.add("pcmode 0")
    return sc


def scene_strokes(rng):
    """STROKE and FILL_STROKE draws with every cap and join (rendering.md §9.7)."""
    sc = Scene()
    w, h = rng.randint(24, 48), rng.randint(24, 40)
    sc.buf(0, w, h, rng.choice(FMTS))
    sc.add(f"fill 0 {rng.getrandbits(31)}")
    for _ in range(4):
        pts = [(f32(rng.uniform(2, w - 2)), f32(rng.uniform(2, h - 2))) for _ in range(rng.randint(2, 5))]
        toks = [f"@{E['VLC_OP_MOVE']}", repr(pts[0][0]), repr(pts[0][1])]
        for x, y in pts[1:]:
            toks += [f"@{E['VLC_OP_LINE']}", repr(x), repr(y)]
        if rng.random() < 0.4:
            toks.append(f"@{E['VLC_OP_CLOSE']}")
        toks.append(f"@{E['VLC_OP_END']}")
        typ = rng.choice(["STROKE", "FILL_STROKE"])
        cap = rng.choice(["BUTT", "ROUND", "SQUARE"])
        join = rng.choice(["MITER", "ROUND", "BEVEL"])
        m = rand_matrix(rng, w, h, w, h)
        sc.add(f"stroke 0 {E['VG_LITE_FP32']} {rng.choice([E['VG_LITE_HIGH'], E['VG_LITE_LOW']])} {rng.choice(BLENDS)} "
               f"{E[f'VG_LITE_DRAW_{typ}_PATH']} {rng.getrandbits(32)} {rng.getrandbits(32)} {E[f'VG_LITE_CAP_{cap}']} "
               f"{E[f'VG_LITE_JOIN_{join}']} {rng.uniform(0.5, 6)!r} {rng.choice([1.0, 4.0, 10.0])!r} {mat_str(m)} "
               f"{len(toks)} {' '.join(toks)}")
    return sc


V13_OPS = dict(BREAK=0x0A, SCCWARC=0x13, SCCWARC_REL=0x14, SCWARC=0x15, SCWARC_REL=0x16, LCCWARC=0x17,
               LCCWARC_REL=0x18, LCWARC=0x19, LCWARC_REL=0x1A)


def scene_vectors(rng):
    """v1.3 driver geometry: arcs (all eight opcodes) and BREAK in fills and strokes, perspective path matrices
    (fills and strokes, also with part of the shape behind the eye), dashed strokes with every cap / join, odd
    counts, zero entries, phases and dash_reset (rendering.md §9.2.1, §9.7.1, §9.8)."""
    sc = Scene()
    w, h = rng.randint(28, 48), rng.randint(24, 40)
    sc.buf(0, w, h, rng.choice(FMTS))
    sc.add(f"fill 0 {rng.getrandbits(31)}")

    def pt():
        return repr(f32(rng.uniform(2, w - 2))), repr(f32(rng.uniform(2, h - 2)))

    def toks_vec(closed):
        t = [f"@{E['VLC_OP_MOVE']}", *pt()]
        for _ in range(rng.randint(2, 4)):
            k = rng.random()
            if k < 0.45:
                op = rng.choice(list(V13_OPS)[1:])
                rel = op.endswith("_REL")
                x, y = (repr(f32(rng.uniform(-8, 8))), repr(f32(rng.uniform(-8, 8)))) if rel else pt()
                t += [f"@{V13_OPS[op]}", repr(f32(rng.uniform(1, w / 2))), repr(f32(rng.uniform(1, h / 2))),
                      repr(f32(rng.uniform(-180, 180))), x, y]
            elif k < 0.55:
                t += [f"@{V13_OPS['BREAK']}"]
            elif k < 0.7:
                t += [f"@{E['VLC_OP_QUAD']}", *pt(), *pt()]
            else:
                t += [f"@{E['VLC_OP_LINE']}", *pt()]
        if closed:
            t.append(f"@{E['VLC_OP_CLOSE']}")
        return t + [f"@{E['VLC_OP_END']}"]

    def matrix():
        m = rand_matrix(rng, w, h, w, h, persp=rng.random() < 0.6)
        if rng.random() < 0.15:                       # w = 0 line through the target: part behind the eye
            m = f32m([list(m[0]), list(m[1]), [rng.choice([-1, 1]) * 2.5 / w, rng.uniform(-0.5, 0.5) / h, 0.2]])
        return m

    for _ in range(3):
        el = toks_vec(rng.random() < 0.5)
        sc.add(f"path 0 {E['VG_LITE_FP32']} {rng.choice([E['VG_LITE_HIGH'], E['VG_LITE_LOW']])} "
               f"{rng.choice([E['VG_LITE_FILL_NON_ZERO'], E['VG_LITE_FILL_EVEN_ODD']])} {rng.choice(BLENDS)} "
               f"{rng.getrandbits(32)} {mat_str(matrix())} {len(el)} {' '.join(el)}")
    for _ in range(4):
        el = toks_vec(rng.random() < 0.3)
        typ = rng.choice(["STROKE", "FILL_STROKE"])
        cap = rng.choice(["BUTT", "ROUND", "SQUARE"])
        join = rng.choice(["MITER", "ROUND", "BEVEL"])
        pat = [f32(rng.choice([0.0, rng.uniform(0.5, 6), rng.uniform(2, 10)])) for _ in range(rng.randint(2, 5))]
        if sum(pat[:len(pat) // 2 * 2]) == 0:
            pat[0] = 3.0
        sc.add(f"dstroke 0 {E['VG_LITE_FP32']} {rng.choice([E['VG_LITE_HIGH'], E['VG_LITE_LOW']])} {rng.choice(BLENDS)} "
               f"{E[f'VG_LITE_DRAW_{typ}_PATH']} {rng.getrandbits(32)} {rng.getrandbits(32)} {E[f'VG_LITE_CAP_{cap}']} "
               f"{E[f'VG_LITE_JOIN_{join}']} {rng.uniform(0.5, 5)!r} {rng.choice([1.0, 4.0, 10.0])!r} "
               f"{len(pat)} {' '.join(repr(v) for v in pat)} {rng.uniform(-10, 10)!r} {rng.randint(0, 1)} "
               f"{mat_str(matrix())} {len(el)} {' '.join(el)}")
    return sc


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_converted_and_stroke_streams(tmp_path, seed):
    """The generators above (converted sources, strokes, v1.3 vectors) on the software backend: return codes and
    the reference replay."""
    rng = random.Random(seed + 77)
    for gen in (scene_converted, scene_strokes, scene_vectors):
        sc = gen(rng)
        rcs, addrs, dumps, rec = run(sc, tmp_path)
        check_blit_rcs(sc, rcs)
        check_replay(sc, addrs, dumps, Replay(rec))


def scene_radial(rng):
    """v1.5 F-4: radial gradients (centred, focal inside, focal outside the circle, r = 0) with every spread, random
    gradient matrices and stops, on curved paths, with blends (scene kind "radial" of the FPGA tests)."""
    sc = Scene()
    w, h = rng.randint(28, 48), rng.randint(24, 40)
    sc.buf(0, w, h, rng.choice(FMTS))
    sc.add(f"fill 0 {rng.getrandbits(31)}")
    for k in range(6):
        fmt = rng.choice(["S16", "FP32"])
        el, _, _ = poly_path(rng, fmt, w / 2, h / 2, min(w, h) / 2.2, rng.randint(3, 6))
        cx, cy, r = f32(rng.uniform(0, w)), f32(rng.uniform(0, h)), f32(rng.choice([rng.uniform(2, w), 0.0]))
        rho = [0.0, rng.uniform(0.2, 0.9), rng.uniform(1.0, 2.0)][k % 3]
        a = rng.uniform(0, 2 * math.pi)
        prm = (cx, cy, r, f32(cx + rho * r * math.cos(a)), f32(cy + rho * r * math.sin(a)))
        n = rng.randint(2, 6)
        st = sorted(f32(rng.random()) for _ in range(n))
        stops = " ".join(" ".join(repr(v) for v in [st[i]] + [f32(rng.random()) for _ in range(4)]) for i in range(n))
        g = f32m(rand_matrix(rng, 64, 64, w, h))
        spread = ["PAD", "REPEAT", "REFLECT", "FILL"][k % 4]
        sc.add(f"gpaint {rng.getrandbits(32)}")
        sc.add(f"rgrad 0 {E[f'VG_LITE_{fmt}']} {rng.choice([E['VG_LITE_HIGH'], E['VG_LITE_LOW']])} "
               f"{rng.choice([E['VG_LITE_FILL_NON_ZERO'], E['VG_LITE_FILL_EVEN_ODD']])} {rng.choice(BLENDS)} "
               f"{E[f'VG_LITE_GRADIENT_SPREAD_{spread}']} {int(rng.random() < 0.5)} 1 0 0 0 1 0 0 0 1 {mat_str(g)} "
               + " ".join(repr(v) for v in prm) + f" {n} {stops} {len(el)} {' '.join(el)}")
    return sc


def scene_image_paths(rng):
    """v1.5 F-1: vg_lite_draw_pattern on curved paths (random polygons with quads / cubics) and projective path
    matrices, every pattern mode, both filters, blends (scene kind "imgpaths" of the FPGA tests)."""
    sc = Scene()
    w, h = rng.randint(28, 48), rng.randint(24, 40)
    sc.buf(0, w, h, rng.choice(FMTS))
    sc.buf(1, rng.randint(2, 16), rng.randint(2, 16), rng.choice(FMTS))
    sc.add(f"fill 0 {rng.getrandbits(31)}")
    sc.add(f"fill 1 {rng.getrandbits(31)}")
    iw, ih, _ = sc.bufs[1]
    for k in range(6):
        fmt = rng.choice(["S16", "FP32"])
        el, _, _ = poly_path(rng, fmt, w / 2, h / 2, min(w, h) / 2.2, rng.randint(3, 6))
        m = f32m([[1, 0, 0], [0, 1, 0], [0, 0, 1]] if k % 3 else
                 [[1, 0, 0], [0, 1, 0], [rng.uniform(-0.3, 0.3) / w, rng.uniform(-0.3, 0.3) / h, 1]])
        sc.add(f"img 1 {rng.choice([E['VG_LITE_NORMAL_IMAGE_MODE'], E['VG_LITE_MULTIPLY_IMAGE_MODE']])} "
               f"{E['VG_LITE_IMAGE_OPAQUE']} {int(rng.random() < 0.3)}")
        P = f32m(rand_matrix(rng, iw, ih, w, h))
        mode = ["COLOR", "PAD", "REPEAT", "REFLECT"][k % 4]
        flt = rng.choice([E["VG_LITE_FILTER_POINT"], E["VG_LITE_FILTER_BI_LINEAR"]])
        sc.add(f"pattern 0 1 {E[f'VG_LITE_{fmt}']} {rng.choice([E['VG_LITE_HIGH'], E['VG_LITE_LOW']])} "
               f"{rng.choice([E['VG_LITE_FILL_NON_ZERO'], E['VG_LITE_FILL_EVEN_ODD']])} {rng.choice(BLENDS)} "
               f"{E[f'VG_LITE_PATTERN_{mode}']} {rng.getrandbits(32)} {rng.getrandbits(32)} {flt} "
               f"{mat_str(m)} {mat_str(P)} {len(el)} {' '.join(el)}")
    return sc


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
@pytest.mark.parametrize("gen", ["radial", "imgpaths"])
def test_v15_scene_streams(tmp_path, gen, seed):
    """The v1.5 FPGA scene kinds: every call succeeds and the recorded stream replays bit-exactly."""
    sc = (scene_radial if gen == "radial" else scene_image_paths)(random.Random(seed))
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    check_replay(sc, addrs, dumps, Replay(rec))


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_scissor_rects_union(tmp_path, seed):
    """v1.6 (rendering.md §6, api-subset.md §3): vg_lite_scissor_rects with several overlapping rectangles clips every
    draw to their union, each pixel drawn once. Reference: the same draws without a scissor inside the union, the
    untouched target outside (clipping only masks pixels, so this is exact); blended draws expose a double draw. The
    recorded stream (one draw per disjoint piece) replays bit-exactly."""
    rng = random.Random(seed)
    w, h = rng.randint(16, 48), rng.randint(12, 40)
    rects = [(rng.randint(-4, w - 2), rng.randint(-4, h - 2), rng.randint(0, w // 2), rng.randint(0, h // 2))
             for _ in range(rng.randint(2, 7))]
    draws = []
    for _ in range(5):
        k = rng.random()
        if k < 0.4:
            draws.append(f"clear 0 {rng.randint(-4, w)} {rng.randint(-4, h)} {rng.randint(1, w)} {rng.randint(1, h)} "
                         f"{rng.getrandbits(32)}")
        else:
            el, _, _ = poly_path(rng, "S16", w / 2, h / 2, min(w, h) / 2, rng.randint(3, 6))
            draws.append(f"path 0 {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} "
                         f"{rng.choice([E['VG_LITE_BLEND_SRC_OVER'], E['VG_LITE_BLEND_ADDITIVE'], E['VG_LITE_BLEND_NONE']])} "
                         f"{rng.getrandbits(32)} 1 0 0 0 1 0 0 0 1 {len(el)} {' '.join(el)}")
    out = {}
    for name, sc_lines in (("clipped", [f"srects {len(rects)} " + " ".join(f"{x} {y} {a} {b}" for x, y, a, b in rects)]),
                           ("free", []), ("none", None)):
        sc = Scene()
        sc.buf(0, w, h, "BGRA8888")
        sc.add(f"fill 0 {seed}")
        if sc_lines is not None:
            for ln in sc_lines:
                sc.add(ln)
            for d in draws:
                sc.add(d)
        (tmp_path / name).mkdir()
        rcs, addrs, dumps, rec = run(sc, tmp_path / name)
        check_rcs(rcs)
        out[name] = np.frombuffer(dumps[0], np.uint8).reshape(h, addrs[0][1])[:, :4 * w].reshape(h, w, 4)
        if name == "clipped":
            check_replay(sc, addrs, dumps, Replay(rec))
    ys, xs = np.mgrid[0:h, 0:w]
    inside = np.zeros((h, w), bool)
    for x, y, a, b in rects:
        inside |= (xs >= x) & (xs < x + a) & (ys >= y) & (ys < y + b)
    exp = np.where(inside[..., None], out["free"], out["none"])
    bad = (out["clipped"] != exp).any(axis=2)
    assert not bad.any(), f"{bad.sum()} pixels differ, first at {np.argwhere(bad)[0]}"


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_mask_layers(tmp_path, seed):
    """v1.6 mask layers (rendering.md §6.1, §9.9; OpenVG 1.1 §7.2 operations), every result checked:
      * create = 255 everywhere; fill = the value in the rectangle;
      * blend_masklayer SET / UNION / INTERSECT / SUBTRACT / CLEAR / FILL with another layer over a rectangle: exactly
        s, m + s − mul8(m, s), mul8(m, s), mul8(m, 255 − s), 0, 255 (§5.6 blends on A8 values);
      * render_masklayer with a polygon, source s = mul8(cov, colour alpha) (cov: the literal per-sample test):
        SET / INTERSECT exact (through the temporary layer), UNION / SUBTRACT within 1 level (blended at the
        coverage step), outside the path unchanged for UNION / SUBTRACT;
      * a masked clear (blend NONE) = lerp8(D, C, m) inside the layer (placed at an origin, partly outside the
        target), untouched outside it and where m = 0; after disable the plain clear;
      * the whole stream replays bit-exactly."""
    rng = random.Random(seed)
    W, H = rng.randint(6, 24), rng.randint(4, 16)
    OPS = ["CLEAR", "FILL", "SET", "UNION", "INTERSECT", "SUBTRACT"]
    div255r = lambda v: (v + 128 + ((v + 128) >> 8)) >> 8  # noqa: E731
    mul8 = lambda a, b: div255r(a * b)  # noqa: E731
    sc = Scene()
    sc.buf(0, 32, 24, "BGRA8888")
    sc.add(f"fill 0 {seed}")
    exp = {}

    def create(i):
        sc.add(f"mcreate {i} {W} {H}")
        sc.bufs[i] = (W, H, "A8")
        exp[i] = np.full((H, W), 255, np.int64)

    def rect():
        x0, y0 = rng.randint(-3, W - 1), rng.randint(-3, H - 1)
        return x0, y0, rng.randint(0, W), rng.randint(0, H)

    def clipped(r):
        x, y, w, h = r
        return max(x, 0), max(y, 0), min(x + w, W), min(y + h, H)

    for i in (1, 2):
        create(i)
        for _ in range(4):
            r, v = rect(), rng.randrange(256)
            sc.add(f"mfill {i} {r[0]} {r[1]} {r[2]} {r[3]} {v}")
            x0, y0, x1, y1 = clipped(r)
            if x1 > x0 and y1 > y0:
                exp[i][y0:y1, x0:x1] = v
    for op in rng.sample(OPS, len(OPS)):
        r = rect()
        sc.add(f"mblend 1 2 {E['VG_LITE_' + op + '_MASK']} {r[0]} {r[1]} {r[2]} {r[3]}")
        x0, y0, x1, y1 = clipped(r)
        if x1 <= x0 or y1 <= y0:
            continue
        m, s = exp[1][y0:y1, x0:x1], exp[2][y0:y1, x0:x1]
        exp[1][y0:y1, x0:x1] = {"CLEAR": 0 * m, "FILL": 0 * m + 255, "SET": s, "UNION": m + s - mul8(m, s),
                                "INTERSECT": mul8(m, s), "SUBTRACT": mul8(m, 255 - s)}[op]
    # render_masklayer on layer 3 with a polygon (integer coordinates: exact edges)
    create(3)
    sc.add(f"mfill 3 0 0 {W} {H} {rng.randrange(256)}")
    rops = []
    for _ in range(3):
        pts = [(rng.randint(-2, W + 2), rng.randint(-2, H + 2)) for _ in range(rng.randint(3, 6))]
        op, ca, eo = rng.choice(["SET", "UNION", "INTERSECT", "SUBTRACT"]), rng.randrange(256), rng.random() < 0.3
        el = path_tokens("S16", pts)
        sc.add(f"mrender 3 {E['VG_LITE_' + op + '_MASK']} {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} "
               f"{E['VG_LITE_FILL_EVEN_ODD'] if eo else E['VG_LITE_FILL_NON_ZERO']} {ca << 24} 1 0 0 0 1 0 0 0 1 "
               f"{len(el)} {' '.join(el)}")
        rops.append((op, ca, eo, pts))
    # masked clears of the target with layer 1
    ox, oy = rng.randint(-W // 2, 32 - W // 2), rng.randint(-H // 2, 24 - H // 2)
    color = rng.getrandbits(32) | 0x01000000
    sc.add(f"mset 1 {ox} {oy}")
    sc.add("mon")
    sc.add(f"clear 0 0 0 -1 -1 {color}")
    sc.add("moff")
    sc.add(f"clear 0 30 0 2 24 {color}")
    # D for the masked clear: the same scene up to "mon" (the target is not changed by mask operations)
    pre = Scene()
    pre.bufs = dict(sc.bufs)
    pre.lines = sc.lines[:sc.lines.index("mon")]
    (tmp_path / "pre").mkdir()
    _, paddrs, pdumps, _ = run(pre, tmp_path / "pre")
    D = np.frombuffer(pdumps[0], np.uint8).reshape(24, paddrs[0][1])[:, :128].reshape(24, 32, 4).astype(np.int64)
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)

    def layer(i):
        return np.frombuffer(dumps[i], np.uint8).reshape(H, addrs[i][1])[:, :W].astype(np.int64)
    for i in (1, 2):
        assert (layer(i) == exp[i]).all(), (i, np.argwhere(layer(i) != exp[i])[:4].tolist())
    # layer 3: the render ops replayed exactly as the draws compute them (rendering.md §4, §5.6, §9.9)
    fill3 = int([ln for ln in sc.lines if ln.startswith("mfill 3")][0].split()[-1])
    m3 = np.full((H, W), fill3, np.int64)
    lerp = lambda d, b, c: div255r(b * c + d * (255 - c))  # noqa: E731
    for op, ca, eo, pts in rops:
        edges = [(16 * pts[k][0], 16 * pts[k][1], 16 * pts[(k + 1) % len(pts)][0], 16 * pts[(k + 1) % len(pts)][1])
                 for k in range(len(pts))]
        cov = np.array([[refimpl.pixel_coverage(edges, x, y, True, eo) for x in range(W)] for y in range(H)])
        s_ = lerp(0, ca, cov)                                  # the temporary layer: lerp8(0, ca, cov) = mul8(cov, ca)
        if op == "SET":
            m3 = s_
        elif op == "INTERSECT":
            m3 = mul8(m3, s_)
        elif op == "UNION":                                    # SCREEN draw at coverage cov (0: pixel skipped)
            m3 = np.where(cov == 0, m3, lerp(m3, ca + (m3 - mul8(ca, m3)), cov))
        else:                                                  # SUBTRACT draw
            m3 = np.where(cov == 0, m3, lerp(m3, mul8(m3, 255 - ca), cov))
    assert (layer(3) == m3).all(), ("render", np.argwhere(layer(3) != m3)[:4].tolist())
    # the masked clear: lerp8(D, C, m) inside the layer, D outside and where m = 0; the plain clear after disable
    tgt = np.frombuffer(dumps[0], np.uint8).reshape(24, addrs[0][1])[:, :128].reshape(24, 32, 4).astype(np.int64)
    cb = np.array(bgra(color))
    for y in range(24):
        for x in range(30):
            mx, my = x - ox, y - oy
            m = int(exp[1][my, mx]) if (0 <= mx < W and 0 <= my < H) else 0
            e_ = D[y, x] if m == 0 else lerp(D[y, x], cb, m)
            assert (tgt[y, x] == e_).all(), (x, y, m, list(tgt[y, x]), list(e_))
        assert (tgt[y, 30:32] == cb).all(), ("unmasked clear", y)
    check_replay(sc, addrs, dumps, Replay(rec))


FAMILY_BLENDS = ([("OPENVG_BLEND_" + n, 1, i) for i, n in enumerate(
    ["SRC", "SRC_OVER", "DST_OVER", "SRC_IN", "DST_IN", "MULTIPLY", "SCREEN", "DARKEN", "LIGHTEN", "ADDITIVE"])] +
    [("SVG2_BLEND_" + n, 2, i) for i, n in enumerate(
        ["NORMAL", "MULTIPLY", "SCREEN", "LIGHTEN", "DARKEN", "DIFFERENCE", "EXCLUSION"])] +
    [("VG_LITE_PORTER_DUFF_BLEND", 3, 0)])


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_blend_families_api(tmp_path, seed):
    """v1.6 (rendering.md §5.7, api-subset.md §3): every OPENVG_BLEND_*, SVG2_BLEND_* and VG_LITE_PORTER_DUFF_BLEND
    (with random vg_lite_blend_func arguments, BYPASS = 0xFF included) on clears-then-fills, blits and paths: the
    recorded BLEND (MODE, FAMILY) and BLEND_FUNC words are exactly the mapping, and the stream replays bit-exactly."""
    rng = random.Random(seed)
    sc = Scene()
    sc.buf(0, rng.randint(8, 32), rng.randint(6, 24), rng.choice(FMTS))
    sc.buf(1, rng.randint(2, 12), rng.randint(2, 10), rng.choice(FMTS))
    sc.add(f"fill 0 {seed}")
    sc.add(f"fill 1 {seed + 1}")
    w, h, _ = sc.bufs[0]
    expect = []
    for name, fam, mode in FAMILY_BLENDS * 2:
        func = None
        if fam == 3:
            a = [rng.randint(0, 8), rng.randint(0, 16), rng.randint(0, 8), rng.randint(0, 16), rng.randint(0, 4),
                 rng.choice(list(range(11)) + [0xFF]), rng.choice(list(range(11)) + [0xFF])]
            sc.add("bfunc " + " ".join(map(str, a)))
            cm = lambda v: 15 if v == 0xFF else v  # noqa: E731
            func = a[0] | (a[1] << 4) | (a[2] << 9) | (a[3] << 13) | (a[4] << 18) | (cm(a[5]) << 21) | (cm(a[6]) << 25)
        if rng.random() < 0.5:
            P = rand_matrix(rng, sc.bufs[1][0], sc.bufs[1][1], w, h)
            sc.add(f"blit 0 1 {E[name]} {rng.getrandbits(32)} {E['VG_LITE_FILTER_BI_LINEAR']} {mat_str(P)}")
        else:
            el, _, _ = poly_path(rng, "S16", w / 2, h / 2, min(w, h) / 2, rng.randint(3, 6))
            sc.add(f"path 0 {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} {E[name]} "
                   f"{rng.getrandbits(32)} 1 0 0 0 1 0 0 0 1 {len(el)} {' '.join(el)}")
        expect.append((mode | (fam << 4), func))
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    rep = Replay(rec)
    draws = [d for d in rep.draws]
    got = [(d[1]["BLEND"], d[1]["BLEND_FUNC"] if (d[1]["BLEND"] >> 4) == 3 else None) for d in draws]
    assert len(got) == len(expect) and got == expect, [(g, e) for g, e in zip(got, expect) if g != e][:4]
    check_replay(sc, addrs, dumps, rep)


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_mirror(tmp_path, seed):
    """v1.6 vg_lite_set_mirror(BOTTOM_TOP) (rendering.md §5.8): the same random scene (clears, blits, paths, gradients,
    patterns: every draw of scene_paths / scene_blits) drawn with the mirror gives exactly the normal image flipped
    vertically; the stream replays bit-exactly."""
    rng = random.Random(seed)
    base = scene_paths(rng, n=4)
    out = {}
    for mirror in (0, 1):
        sc = Scene()
        sc.bufs = dict(base.bufs)
        sc.lines = list(base.lines)
        k = next(i for i, ln in enumerate(sc.lines) if ln.startswith("fill "))
        sc.lines[k] = f"clear 0 0 0 -1 -1 {0x80402010 + seed}"   # uniform start: storage order does not matter
        sc.lines.insert(k, f"mirror {mirror}")
        (tmp_path / str(mirror)).mkdir()
        rcs, addrs, dumps, rec = run(sc, tmp_path / str(mirror))
        check_rcs(rcs)
        w, h, fmt = sc.bufs[0]
        rb = row_bytes(fmt, w)
        out[mirror] = [dumps[0][y * addrs[0][1]:y * addrs[0][1] + rb] for y in range(h)]
        if mirror:
            check_replay(sc, addrs, dumps, Replay(rec))
    assert out[1] == out[0][::-1], "mirrored image is not the normal one flipped vertically"


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_image_modes_api(tmp_path, seed):
    """v1.6 image modes (rendering.md §5.3.1, api-subset.md §2): STENCIL blits under every source global-alpha mode
    record PAINT_CTRL.STENCIL, SRC_MODE NORMAL and the paint alpha folded as §5.3.1 (GLOBAL: g, SCALED: m(a, g));
    RECOLOR records IMG_MUL; NONE draws nothing (no draw recorded, target unchanged); everything replays bit-exactly."""
    rng = random.Random(seed)
    sc = Scene()
    sc.buf(0, 24, 16, rng.choice(FMTS))
    sc.buf(1, rng.randint(2, 10), rng.randint(2, 8), rng.choice(["BGRA8888", "A8", "A4", "BGRA4444", "A8L8"]))
    sc.add(f"fill 0 {seed}")
    sc.add(f"fill 1 {seed + 3}")
    exp = []
    div255r = lambda v: (v + 128 + ((v + 128) >> 8)) >> 8  # noqa: E731
    for k in range(9):
        mode = ["STENCIL", "RECOLOR", "NONE"][k % 3]
        am, av = rng.randrange(3), rng.randrange(256)
        sc.add(f"salpha {am} {av}")
        sc.add(f"img 1 {E['VG_LITE_' + mode + ('_IMAGE_MODE' if mode == 'NONE' else '_MODE')]} {E['VG_LITE_IMAGE_OPAQUE']} 0")
        color = rng.getrandbits(32)
        blend = rng.randint(0, 15)
        P = rand_matrix(rng, sc.bufs[1][0], sc.bufs[1][1], 24, 16)
        sc.add(f"blit 0 1 {blend} {color} {E['VG_LITE_FILTER_BI_LINEAR']} {mat_str(P)}")
        if mode != "NONE":
            pa = color >> 24
            pa = av if (mode == "STENCIL" and am == 1) else div255r(pa * av) if (mode == "STENCIL" and am == 2) else pa
            exp.append((mode, 0 if mode == "STENCIL" else am, pa))
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    rep = Replay(rec)
    got = rep.draws
    assert len(got) == len(exp), (len(got), len(exp))
    for (mode, am, pa), d in zip(exp, got):
        st = d[1]
        assert ((st["PAINT_CTRL"] >> 12) & 1) == (mode == "STENCIL"), mode
        assert ((st["PAINT_CTRL"] >> 2) & 1) == (mode == "RECOLOR"), mode
        assert (st["ALPHA_CTRL"] & 3) == am and (st["PAINT_COLOR"] >> 24) == pa, (mode, hex(st["ALPHA_CTRL"]),
                                                                                 hex(st["PAINT_COLOR"]), pa)
    check_replay(sc, addrs, dumps, rep)


def scene_v16(rng):
    """v1.6 features in one scene (scene kind "v16" of the FPGA tests): scissor rectangles, a mask layer (created,
    filled, rendered from a path, set at an origin), blend families (OpenVG, SVG2, Porter-Duff), stencil and recolor
    images, the target mirror."""
    sc = Scene()
    w, h = rng.randint(24, 40), rng.randint(16, 32)
    sc.buf(0, w, h, rng.choice(FMTS))
    sc.buf(1, rng.randint(3, 12), rng.randint(3, 10), rng.choice(["BGRA8888", "A8", "A8L8", "BGRA4444"]))
    sc.add(f"fill 0 {rng.getrandbits(31)}")
    sc.add(f"fill 1 {rng.getrandbits(31)}")
    mw, mh = rng.randint(8, w), rng.randint(6, h)
    sc.add(f"mcreate 2 {mw} {mh}")
    sc.bufs[2] = (mw, mh, "A8")
    sc.add(f"mfill 2 {rng.randint(0, mw // 2)} {rng.randint(0, mh // 2)} {rng.randint(1, mw)} {rng.randint(1, mh)} "
           f"{rng.randrange(256)}")
    el = path_tokens("S16", [(rng.randint(0, mw), rng.randint(0, mh)) for _ in range(4)])
    sc.add(f"mrender 2 {E['VG_LITE_' + rng.choice(['UNION', 'INTERSECT', 'SUBTRACT', 'SET']) + '_MASK']} {E['VG_LITE_S16']} "
           f"{E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} {rng.getrandbits(32)} 1 0 0 0 1 0 0 0 1 {len(el)} {' '.join(el)}")
    rects = " ".join(f"{rng.randint(-2, w - 4)} {rng.randint(-2, h - 4)} {rng.randint(2, w // 2)} {rng.randint(2, h // 2)}"
                     for _ in range(3))
    sc.add(f"srects 3 {rects}")
    sc.add(f"mset 2 {rng.randint(-4, w // 2)} {rng.randint(-4, h // 2)}")
    sc.add("mon")
    sc.add(f"mirror {rng.randrange(2)}")
    sc.add("bfunc 1 4 3 5 0 255 255")
    blends = [E[b] for b in ("OPENVG_BLEND_SRC_OVER", "OPENVG_BLEND_MULTIPLY", "SVG2_BLEND_SCREEN",
                              "SVG2_BLEND_EXCLUSION", "VG_LITE_PORTER_DUFF_BLEND", "VG_LITE_BLEND_SRC_OVER")]
    for k in range(4):
        mode = rng.choice(["NORMAL", "STENCIL", "RECOLOR"])
        sc.add(f"img 1 {E['VG_LITE_' + mode + ('_IMAGE_MODE' if mode == 'NORMAL' else '_MODE')]} {E['VG_LITE_IMAGE_OPAQUE']} 0")
        b = E["VG_LITE_BLEND_SRC_OVER"] if mode == "STENCIL" else rng.choice(blends)
        P = rand_matrix(rng, sc.bufs[1][0], sc.bufs[1][1], w, h)
        sc.add(f"blit 0 1 {b} {rng.getrandbits(32)} {E['VG_LITE_FILTER_BI_LINEAR']} {mat_str(P)}")
        pel, _, _ = poly_path(rng, "S16", w / 2, h / 2, min(w, h) / 2, rng.randint(3, 6))
        sc.add(f"path 0 {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} {rng.choice(blends)} "
               f"{rng.getrandbits(32)} 1 0 0 0 1 0 0 0 1 {len(pel)} {' '.join(pel)}")
    sc.add("moff")
    sc.add("srects 0")
    sc.add("mirror 0")
    return sc


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_v16_scene_stream(tmp_path, seed):
    """The v1.6 FPGA scene kind: every call succeeds and the stream replays bit-exactly."""
    sc = scene_v16(random.Random(seed))
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    check_replay(sc, addrs, dumps, Replay(rec))


@pytest.mark.parametrize("kernel", ["gauss", "sharpen", "gain"])
@pytest.mark.parametrize("spread", ["PAD", "REPEAT", "REFLECT"])
@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_gaussian_vs_float(tmp_path, seed, spread, kernel):
    """v1.6b vg_lite_gaussian_filter + VG_LITE_FILTER_GAUSSIAN (rendering.md §7.3 / §9.10) against an independent
    float 3 x 3 convolution of the image (numpy, the given float weights, neighbours by the pattern mode: clamped /
    modulo / mirrored): every channel within 1 (rendering.md §12). The image is scaled 2x so each target pixel
    samples a texel well inside it. Kernels (decision 2026-10-26): a real Gaussian, a sharpening kernel (negative
    edges) and one with a gain of 1.5, both compared with the float convolution clamped to [0, 255]; NaN / Inf
    weights are rejected (INVALID_ARGUMENT), any other kernel is accepted; the stream replays bit-exactly."""
    rng = random.Random(seed * 13 + len(spread))
    iw, ih = rng.randint(3, 12), rng.randint(3, 10)
    w, h = 2 * iw + 8, 2 * ih + 6
    img = np.zeros((ih, ((4 * iw + 63) // 64) * 16, 4), np.uint8)     # 64-byte stride
    pix = np.array([[[rng.randrange(256) for _ in range(4)] for _ in range(iw)] for _ in range(ih)], np.uint8)
    img[:, :iw] = pix
    (tmp_path / "g.bin").write_bytes(img.tobytes())
    sig = rng.uniform(0.4, 1.5)
    g1 = [math.exp(-d * d / (2 * sig * sig)) for d in (0, 1)]
    tot = (g1[0] + 2 * g1[1]) ** 2
    w0, w1, w2 = g1[0] * g1[0] / tot, g1[0] * g1[1] / tot, g1[1] * g1[1] / tot
    if kernel == "sharpen":
        a = rng.uniform(0.2, 1.0)
        w0, w1, w2 = 1 + 4 * a, -a, 0.0
    elif kernel == "gain":
        w0, w1, w2 = 1.5 * w0, 1.5 * w1, 1.5 * w2
    sc = Scene()
    sc.buf(0, iw, ih, "BGRA8888", img.shape[1] * 4)
    sc.add(f"load 0 {tmp_path / 'g.bin'}")
    sc.add(f"img 0 {E['VG_LITE_NORMAL_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
    sc.buf(1, w, h, "BGRA8888")
    sc.add(f"clear 1 0 0 -1 -1 {0x11223344}")
    sc.add("gauss nan 0.3 0.0")                        # rejected: NaN
    sc.add("gauss 0.5 inf 0.2")                        # rejected: Inf
    sc.add(f"gauss {w0!r} {w1!r} {w2!r}")
    tx, ty = rng.randint(0, 6), rng.randint(0, 4)
    P = [[2.0, 0.0, float(tx)], [0.0, 2.0, float(ty)], [0.0, 0.0, 1.0]]
    el = path_tokens("S16", [(0, 0), (w, 0), (w, h), (0, h)])
    sc.add(f"pattern 1 0 {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} {E['VG_LITE_BLEND_NONE']} "
           f"{E[f'VG_LITE_PATTERN_{spread}']} 0 0 {E['VG_LITE_FILTER_GAUSSIAN']} "
           f"1 0 0 0 1 0 0 0 1 {mat_str(P)} {len(el)} {' '.join(el)}")
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    assert [c for n, c in rcs if n == "gaussian_filter"] == [E["VG_LITE_INVALID_ARGUMENT"]] * 2 + [E["VG_LITE_SUCCESS"]]
    assert all(c == 0 for n, c in rcs if n != "gaussian_filter"), rcs
    out = np.frombuffer(dumps[1], np.uint8).reshape(h, addrs[1][1])[:, :4 * w].reshape(h, w, 4).astype(float)

    def coord(c, n):
        if spread == "PAD":
            return min(max(c, 0), n - 1)
        if spread == "REPEAT":
            return c % n
        k = c % (2 * n)
        return k if k < n else 2 * n - 1 - k

    wt = {0: w0, 1: w1, 2: w2}
    ref = np.zeros((h, w, 4))
    for y in range(h):
        for x in range(w):
            u, v = math.floor((x + 0.5 - tx) / 2), math.floor((y + 0.5 - ty) / 2)
            ref[y, x] = np.clip(sum(wt[abs(i) + abs(j)] * pix[coord(v + j, ih), coord(u + i, iw)].astype(float)
                                    for i in (-1, 0, 1) for j in (-1, 0, 1)), 0, 255)
    d = np.abs(out - ref)
    assert d.max() <= 1.0, (d.max(), np.argwhere(d > 1.0)[:4].tolist())
    check_replay(sc, addrs, dumps, Replay(rec))



YUV_NAMES = ["YUY2", "AYUY2", "NV12", "ANV12", "NV16", "NV24", "YV12", "YV16", "YV24"]
# standard limited-range matrices (Kr, Kb): BT.601, BT.709
_KRB = ((0.299, 0.114), (0.2126, 0.0722))


def yuv_float(Y, U, V, bt709):
    """The BT.601 / BT.709 limited-range conversion in double, from Kr / Kb (independent of the coefficient table)."""
    kr, kb = _KRB[int(bt709)]
    kg = 1 - kr - kb
    y, cb, cr = (Y - 16) * 255 / 219, (U - 128) * 255 / 224, (V - 128) * 255 / 224
    r = y + 2 * (1 - kr) * cr
    b = y + 2 * (1 - kb) * cb
    g = (y - kr * r - kb * b) / kg
    return [min(255.0, max(0.0, c)) for c in (r, g, b)]


@pytest.mark.parametrize("name", YUV_NAMES)
@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_yuv_vs_float(tmp_path, seed, name):
    """v1.6b YUV sources (rendering.md §3.7) through vg_lite_allocate / vg_lite_upload_buffer and a point-sampled
    identity blit, against an independent float conversion from Kr / Kb (both standards, both swizzles): R, G, B
    within 1 (coefficient rounding to 1/1024 and the final rounding), A exact (alpha plane or 255); Y0 / Y1 of YUY2
    pairs, chroma nearest per subsampling; the Gaussian filter on a YUV source is NOT_SUPPORT; replay bit-exact."""
    rng = random.Random(seed * 7 + YUV_NAMES.index(name))
    w, h = rng.randint(1, 23), rng.randint(1, 15)
    code = FMT[name]
    cls, sub, alpha = code & 0xF, (code >> 4) & 3, bool(code & 0x40)
    xs, ys = int(sub <= 1), int(sub == 0)
    cw, ch = (w + (1 << xs) - 1) >> xs, (h + (1 << ys) - 1) >> ys
    r = np.random.default_rng(seed * 31 + cls)
    if cls == 3:
        p0 = r.integers(0, 256, (h, 4 * ((w + 1) // 2)), dtype=np.uint8)
    else:
        p0 = r.integers(0, 256, (h, w), dtype=np.uint8)
    p1 = r.integers(0, 256, (ch, 2 * cw if cls == 4 else cw), dtype=np.uint8) if cls != 3 else None
    p2 = (r.integers(0, 256, (ch, cw), dtype=np.uint8) if cls == 5 else
          r.integers(0, 256, (h, w), dtype=np.uint8) if alpha else None)
    files = []
    for k, p in enumerate((p0, p1, p2)):
        if p is None:
            files.append(("0", "-"))
            continue
        (tmp_path / f"p{k}.bin").write_bytes(p.tobytes())
        files.append((str(p.shape[1]), str(tmp_path / f"p{k}.bin")))
    out = {}
    for std in (0, 1):
        for swz in (0, 1):
            sc = Scene()
            sc.lines.append(f"buf 0 {w} {h} {vg_enum(name)} 0")
            sc.bufs[0] = (w, h, name)
            sc.add(f"yuvload 0 {' '.join(f[0] for f in files)} {' '.join(f[1] for f in files)}")
            sc.add(f"yuvfmt 0 {std} {swz}")
            sc.add(f"img 0 {E['VG_LITE_NORMAL_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
            sc.buf(1, w, h, "BGRA8888")
            sc.add(f"clear 1 0 0 -1 -1 0")
            sc.add(f"blit 1 0 {E['VG_LITE_BLEND_NONE']} 0 {E['VG_LITE_FILTER_POINT']} 1 0 0 0 1 0 0 0 1")
            sc.add(f"blit 1 0 {E['VG_LITE_BLEND_NONE']} 0 {E['VG_LITE_FILTER_GAUSSIAN']} 1 0 0 0 1 0 0 0 1")
            d = tmp_path / f"{std}{swz}"
            d.mkdir()
            rcs, addrs, dumps, rec = run(sc, d)
            blits = [c for n, c in rcs if n == "blit"]
            assert blits == [0, E["VG_LITE_NOT_SUPPORT"]], rcs
            assert all(c == 0 for n, c in rcs if n != "blit"), rcs
            img = np.frombuffer(dumps[1], np.uint8).reshape(h, addrs[1][1])[:, :4 * w].reshape(h, w, 4).astype(int)
            for y in range(h):
                for x in range(w):
                    if cls == 3:
                        Y, U, V = int(p0[y, 4 * (x >> 1) + 2 * (x & 1)]), int(p0[y, 4 * (x >> 1) + 1]), int(p0[y, 4 * (x >> 1) + 3])
                    else:
                        Y = int(p0[y, x])
                        if cls == 4:
                            U, V = int(p1[y >> ys, 2 * (x >> xs)]), int(p1[y >> ys, 2 * (x >> xs) + 1])
                        else:
                            U, V = int(p1[y >> ys, x >> xs]), int(p2[y >> ys, x >> xs])
                    A = int(p2[y, x]) if alpha else 255
                    if swz:
                        U, V = V, U
                    ref = yuv_float(Y, U, V, std)
                    got = img[y, x]                      # B, G, R, A
                    assert got[3] == A and all(abs(got[2 - c] - ref[c]) <= 1.0 for c in range(3)), \
                        (name, std, swz, x, y, (Y, U, V), list(got), ref)
            check_replay(sc, addrs, dumps, Replay(rec))
            out[(std, swz)] = img
    assert not (out[(0, 0)] == out[(1, 0)]).all() or w * h < 3, "BT.601 and BT.709 gave the same image"



def _f32(v):
    return struct.unpack("<f", struct.pack("<f", v))[0]


def compose_color(pm, ch, ct, ct_on):
    """rendering.md §9.10 in double from the float32 API values: pixel matrix rows (enabled channels), then the
    colour transform; M = round(256 m) (half away from zero, 16-bit), B = round(65280 b) (20-bit). -> (en, m16, b4)."""
    rnd = lambda v: int(math.copysign(math.floor(abs(v) + 0.5), v))  # noqa: E731
    m = [[1.0 if j == o else 0.0 for j in range(5)] for o in range(4)]
    en = 0
    if pm is not None:
        for o in range(4):
            if ch[o]:
                m[o] = [_f32(x) for x in pm[5 * o:5 * o + 5]]
                en = 1
    if ct_on:
        for o in range(4):
            m[o] = [x * _f32(ct[2 * o]) for x in m[o]]
            m[o][4] += _f32(ct[2 * o + 1])
        en = 1
    mi = [max(-32768, min(32767, rnd(256 * m[o][j]))) for o in range(4) for j in range(4)]
    bi = [max(-(1 << 19), min((1 << 19) - 1, rnd(65280 * m[o][4]))) for o in range(4)]
    return en, mi, bi


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_color_unit_api(tmp_path, seed):
    """v1.6b colour unit through the API (rendering.md §5.2.1 / §9.10): vg_lite_set_pixel_matrix (channel enables),
    vg_lite_set_color_transform / enable / disable, vg_lite_set_gamma. Image draws record COLOR_CTRL / M / B equal to
    the composition computed here from the float API values; solid draws record PAINT_COLOR = the integer function of
    the colour (refimpl.color_apply); gradients read a transformed ramp; clears are not transformed; the image result
    is within 3 of the float matrix applied in double (rendering.md §12); the stream replays bit-exactly."""
    rng = random.Random(seed * 17 + 3)
    w, h = 16, 12
    pm = [rng.uniform(-1.2, 1.2) for _ in range(20)]
    for o in range(4):
        pm[5 * o + o] = rng.uniform(0.5, 1.5)
        pm[5 * o + 4] = rng.uniform(-0.3, 0.3)
    ch = [rng.randrange(2) for _ in range(4)]
    ct = [v for _ in range(4) for v in (rng.uniform(-1.5, 2.0), rng.uniform(-0.5, 0.5))]
    gam = rng.randrange(3)
    img = np.array([[[rng.randrange(256) for _ in range(4)] for _ in range(w)] for _ in range(h)], np.uint8)
    (tmp_path / "img.bin").write_bytes(np.pad(img, ((0, 0), (0, 16 - w), (0, 0))).tobytes())
    sc = Scene()
    sc.buf(0, w, h, "BGRA8888", 64)
    sc.add(f"load 0 {tmp_path / 'img.bin'}")
    sc.add(f"img 0 {E['VG_LITE_NORMAL_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
    for k in (1, 2, 3):
        sc.buf(k, w, h, "BGRA8888")
    sc.add(f"pmatrix {' '.join(repr(v) for v in pm)} {' '.join(str(c) for c in ch)}")
    sc.add(f"ctrans {' '.join(repr(v) for v in ct)}")
    sc.add("cton 1")
    sc.add(f"gamma {gam}")
    sc.add(f"clear 1 0 0 -1 -1 {0x80402010}")                       # not transformed
    sc.add(f"blit 2 0 {E['VG_LITE_BLEND_NONE']} 0 {E['VG_LITE_FILTER_POINT']} 1 0 0 0 1 0 0 0 1")
    col = rng.getrandbits(32)
    el = path_tokens("S16", [(0, 0), (w, 0), (w, h), (0, h)])
    sc.add(f"path 3 {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} {E['VG_LITE_BLEND_NONE']} "
           f"{col} 1 0 0 0 1 0 0 0 1 {len(el)} {' '.join(el)}")
    # the same linear gradient with the unit on (buffer 4) and off (buffer 5): 4 = colour function of 5
    gm_ = [[0.7, 0.2, 1.0], [-0.1, 0.9, 2.0], [0.0, 0.0, 1.0]]
    gcol = [rng.getrandbits(32) for _ in range(3)]
    gline = (f"lgrad {{t}} {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} {E['VG_LITE_BLEND_NONE']} "
             f"1 0 0 0 1 0 0 0 1 {mat_str(gm_)} 3 {' '.join(map(str, gcol))} 0 128 255 {len(el)} {' '.join(el)}")
    sc.buf(4, w, h, "BGRA8888")
    sc.buf(5, w, h, "BGRA8888")
    sc.add(gline.format(t=4))
    sc.add("cton 0")
    sc.add("pmatrix " + " ".join(["0"] * 20) + " 0 0 0 0")
    sc.add("gamma 0")
    sc.add(gline.format(t=5))
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    rep = Replay(rec)
    en, mi, bi = compose_color(pm, ch, ct, True)
    draws = rep.draws
    img_draw = next(d for d in draws if d[1]["PAINT_CTRL"] & 3 == 1)
    st = img_draw[1]
    assert refimpl.color_state(st) == (en, gam, mi, bi), (refimpl.color_state(st), (en, gam, mi, bi))
    solid = [d for d in draws if d[1]["PAINT_CTRL"] & 3 == 0]
    argb = ((col >> 24) << 24) | ((col & 255) << 16) | (col & 0xFF00) | ((col >> 16) & 255)   # vg_lite_color_t ABGR
    assert solid[-1][1]["PAINT_COLOR"] == refimpl.color_apply(argb, en, gam, mi, bi), hex(solid[-1][1]["PAINT_COLOR"])
    clear = np.frombuffer(dumps[1], np.uint8).reshape(h, addrs[1][1])[:, :4 * w].reshape(h, w, 4)
    assert (clear == [0x40, 0x20, 0x10, 0x80]).all(), "clear went through the colour unit"   # ABGR 0x80402010
    # image vs the float matrix in double
    out = np.frombuffer(dumps[2], np.uint8).reshape(h, addrs[2][1])[:, :4 * w].reshape(h, w, 4).astype(float)
    tab = refimpl.GAMMA_LIN if gam == 1 else refimpl.GAMMA_SRGB if gam == 2 else list(range(256))
    m = [[1.0 if j == o else 0.0 for j in range(5)] for o in range(4)]
    for o in range(4):
        if ch[o]:
            m[o] = [_f32(x) for x in pm[5 * o:5 * o + 5]]
        m[o] = [x * _f32(ct[2 * o]) for x in m[o]]
        m[o][4] += _f32(ct[2 * o + 1])
    for y in range(h):
        for x in range(w):
            b_, g_, r_, a_ = (int(v) for v in img[y, x])
            v = [a_, tab[r_], tab[g_], tab[b_]]
            ref = [min(255.0, max(0.0, sum(m[o][j] * v[j] for j in range(4)) + 255 * m[o][4])) for o in range(4)]
            got = [out[y, x, 3], out[y, x, 2], out[y, x, 1], out[y, x, 0]]
            assert max(abs(g - r) for g, r in zip(got, ref)) <= 3.0, (x, y, got, ref)
    g_on = np.frombuffer(dumps[4], np.uint8).reshape(h, addrs[4][1])[:, :4 * w].reshape(h, w, 4)
    g_off = np.frombuffer(dumps[5], np.uint8).reshape(h, addrs[5][1])[:, :4 * w].reshape(h, w, 4)
    for y in range(h):
        for x in range(w):
            b_, g_, r_, a_ = (int(v) for v in g_off[y, x])
            want = refimpl.color_apply((a_ << 24) | (r_ << 16) | (g_ << 8) | b_, en, gam, mi, bi)
            got = g_on[y, x]
            assert (int(got[3]) << 24 | int(got[2]) << 16 | int(got[1]) << 8 | int(got[0])) == want, (x, y)
    check_replay(sc, addrs, dumps, rep)


def _openvg_enums():
    """Every OPENVG_* enumerator of the vendored header (name -> value)."""
    text = (ROOT / "sw" / "include" / "vg_lite.h").read_text()
    out = {}
    for name, expr in re.findall(r"\b(OPENVG_(?:[sl][A-Z]+_\d+(?:_PRE)?|A_\d|BW_1))\s*=\s*([^,\n]+),", text):
        out[name] = eval(expr.split("/*")[0], {})       # e.g. "1 | (1 << 6)"; buffer formats only
    return out


OVG_ENUMS = _openvg_enums()


def openvg_same(name):
    """(VGLite format with the same memory layout, linear?, sub-byte order reversed?) from the OpenVG name alone:
    channels named MSB first, so the LSB-first VGLite name reverses letters and widths; None for BW_1."""
    base = name[len("OPENVG_"):].replace("_PRE", "")
    if base == "BW_1":
        return None
    if base in ("A_8", "A_1", "A_4"):
        return base.replace("_", ""), False, base != "A_8"
    linear = base[0] == "l"
    chans, bits = base[1:].split("_")
    if chans == "L":
        return "L8", linear, False
    if bits == "565":
        return chans[::-1] + "565", linear, False
    return chans[::-1] + bits[::-1], linear, False


@pytest.mark.parametrize("ovg", sorted(OVG_ENUMS))
def test_openvg_formats(tmp_path, ovg):
    """v1.6b OPENVG_* buffer formats (rendering.md §9.10, decision 2026-10-26: one test per format): a source in the
    OpenVG format draws exactly like the same bytes in the VGLite format with that layout (derived here from the
    name), into a target of its own colour space (sRGB: BGRA8888, linear: OPENVG_lRGBA_8888 vs the VGLite ABGR8888,
    so nothing is converted); OPENVG_A_1 / A_4 put the first pixel in the low bits (the VGLite twin gets the slots
    reversed); OPENVG_BW_1 is NOT_SUPPORT."""
    rng = random.Random(OVG_ENUMS[ovg] * 131 + 7)
    w, h = 9, 5
    same = openvg_same(ovg)
    sc = Scene()
    if same is None:
        sc.lines.append(f"buf 0 {w} {h} {OVG_ENUMS[ovg]} 0")
        rcs, _, _, _ = run(sc, tmp_path)
        assert [c for n, c in rcs if n == "allocate"] == [E["VG_LITE_NOT_SUPPORT"]], rcs
        return
    vg, linear, rev = same
    raw = bytearray(rng.getrandbits(8) for _ in range(64 * h))
    twin = bytearray(raw)
    if rev:                                    # first pixel low (OpenVG) -> first pixel high (VGLite A1 / A4 here)
        bits = 1 if vg == "A1" else 4
        def rev_slots(b):
            per = 8 // bits
            sl = [(b >> (bits * k)) & ((1 << bits) - 1) for k in range(per)]
            return sum(v << (bits * (per - 1 - k)) for k, v in enumerate(sl))
        twin = bytearray(rev_slots(b) for b in raw)
    (tmp_path / "a.bin").write_bytes(bytes(raw))
    (tmp_path / "b.bin").write_bytes(bytes(twin))
    sc.lines.append(f"buf 0 {w} {h} {OVG_ENUMS[ovg]} 64")
    sc.bufs[0] = (w, h, vg)
    sc.buf(1, w, h, vg, 64)
    sc.add(f"load 0 {tmp_path / 'a.bin'}")
    sc.add(f"load 1 {tmp_path / 'b.bin'}")
    for b in (0, 1):
        sc.add(f"img {b} {E['VG_LITE_NORMAL_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
    if linear:
        sc.lines.append(f"buf 2 {w} {h} {OVG_ENUMS['OPENVG_lRGBA_8888']} 0")
        sc.bufs[2] = (w, h, "ABGR8888")
        sc.buf(3, w, h, "ABGR8888")
    else:
        sc.buf(2, w, h, "BGRA8888")
        sc.buf(3, w, h, "BGRA8888")
    for t, s_ in ((2, 0), (3, 1)):
        sc.add(f"clear {t} 0 0 -1 -1 0")
        sc.add(f"blit {t} {s_} {E['VG_LITE_BLEND_SRC_OVER']} 0 {E['VG_LITE_FILTER_POINT']} 1 0 0 0 1 0 0 0 1")
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    assert dumps[2] == dumps[3], f"{ovg} != {vg}"
    check_replay(sc, addrs, dumps, Replay(rec))


def test_openvg_linear_to_srgb(tmp_path):
    """v1.6b: an OPENVG_l* source drawn into an OPENVG_s* target is converted linear -> sRGB (OpenVG 1.1 §3.4) with
    the §5.2.1 table, an s* source into an l* target sRGB -> linear; the same spaces are not converted; the
    replay is bit-exact."""
    w, h = 16, 16
    vals = bytes(range(256))
    raw = b"".join(bytes([vals[(4 * i + k) % 256] for k in range(4)]) for i in range(w * h))
    (tmp_path / "raw.bin").write_bytes(raw)
    cases = [("OPENVG_lRGBA_8888", "OPENVG_sRGBA_8888", refimpl.GAMMA_SRGB),
             ("OPENVG_sRGBA_8888", "OPENVG_lRGBA_8888", refimpl.GAMMA_LIN),
             ("OPENVG_lRGBA_8888", "OPENVG_lRGBA_8888", list(range(256)))]
    for n, (src, dst, tab) in enumerate(cases):
        sc = Scene()
        sc.lines.append(f"buf 0 {w} {h} {E[src]} 64")
        sc.bufs[0] = (w, h, "ABGR8888")
        sc.add(f"load 0 {tmp_path / 'raw.bin'}")
        sc.add(f"img 0 {E['VG_LITE_NORMAL_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
        sc.lines.append(f"buf 1 {w} {h} {E[dst]} 64")
        sc.bufs[1] = (w, h, "ABGR8888")
        sc.add(f"blit 1 0 {E['VG_LITE_BLEND_NONE']} 0 {E['VG_LITE_FILTER_POINT']} 1 0 0 0 1 0 0 0 1")
        d = tmp_path / str(n)
        d.mkdir()
        rcs, addrs, dumps, rec = run(sc, d)
        check_rcs(rcs)
        out = dumps[1]
        for i in range(w * h):
            a, b, g, r = raw[4 * i:4 * i + 4]            # ABGR8888 = OpenVG RGBA_8888: byte 0 A
            assert tuple(out[4 * i:4 * i + 4]) == (a, tab[b], tab[g], tab[r]), (src, dst, i)
        check_replay(sc, addrs, dumps, Replay(rec))


def scene_v16b(rng):
    """v1.6b features in one scene (scene kind "v16b"): 24-bit and YUV sources (planes filled by fill), the Gaussian
    filter, the colour unit (pixel matrix, colour transform, gamma) on blits, a solid path and a gradient."""
    sc = Scene()
    w, h = rng.randint(24, 40), rng.randint(16, 32)
    sc.buf(0, w, h, rng.choice(FMTS))
    sc.add(f"fill 0 {rng.getrandbits(31)}")
    sc.buf(1, rng.randint(3, 14), rng.randint(3, 10), rng.choice(["RGB888", "BGR888", "ARGB8565", "BGRA5658"]))
    sc.buf(2, rng.randint(2, 14), rng.randint(2, 10), rng.choice(["NV12", "YUY2", "YV12", "ANV12"]))
    for b in (1, 2):
        sc.add(f"fill {b} {rng.getrandbits(31)}")
        sc.add(f"img {b} {E['VG_LITE_NORMAL_IMAGE_MODE']} {E['VG_LITE_IMAGE_OPAQUE']} 0")
    sc.add("gauss 0.25 0.125 0.0625")
    pm = [rng.uniform(-0.3, 0.3) for _ in range(20)]
    for o in range(4):
        pm[5 * o + o] = rng.uniform(0.7, 1.2)
    sc.add(f"pmatrix {' '.join(repr(v) for v in pm)} 1 1 {rng.randrange(2)} 1")
    sc.add(f"ctrans {' '.join(repr(rng.uniform(0.6, 1.2) if k % 2 == 0 else rng.uniform(-0.1, 0.1)) for k in range(8))}")
    for k in range(4):
        sc.add(f"cton {k & 1}")
        sc.add(f"gamma {rng.randrange(3)}")
        src = 1 if k % 2 == 0 else 2
        flt = E["VG_LITE_FILTER_GAUSSIAN"] if src == 1 and k == 2 else E[rng.choice(["VG_LITE_FILTER_POINT", "VG_LITE_FILTER_BI_LINEAR"])]
        P = rand_matrix(rng, sc.bufs[src][0], sc.bufs[src][1], w, h)
        sc.add(f"blit 0 {src} {rng.choice([E['VG_LITE_BLEND_SRC_OVER'], E['VG_LITE_BLEND_NONE']])} {rng.getrandbits(32)} "
               f"{flt} {mat_str(P)}")
    pel, _, _ = poly_path(rng, "S16", w / 2, h / 2, min(w, h) / 2, rng.randint(3, 6))
    sc.add(f"path 0 {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} {E['VG_LITE_BLEND_SRC_OVER']} "
           f"{rng.getrandbits(32)} 1 0 0 0 1 0 0 0 1 {len(pel)} {' '.join(pel)}")
    el = path_tokens("S16", [(0, 0), (w, 0), (w, h // 2), (0, h // 2)])
    sc.add(f"lgrad 0 {E['VG_LITE_S16']} {E['VG_LITE_HIGH']} {E['VG_LITE_FILL_NON_ZERO']} {E['VG_LITE_BLEND_SRC_OVER']} "
           f"1 0 0 0 1 0 0 0 1 1 0 0 0 1 0 0 0 1 2 {rng.getrandbits(32)} {rng.getrandbits(32)} 0 255 {len(el)} {' '.join(el)}")
    sc.add("cton 0")
    sc.add("gamma 0")
    sc.add("pmatrix " + " ".join(["0"] * 20) + " 0 0 0 0")
    return sc


@pytest.mark.parametrize("seed", SEEDS, ids=SEED_IDS)
def test_v16b_scene_stream(tmp_path, seed):
    """The v1.6b FPGA scene kind: every call succeeds and the stream replays bit-exactly."""
    sc = scene_v16b(random.Random(seed))
    rcs, addrs, dumps, rec = run(sc, tmp_path)
    check_rcs(rcs)
    check_replay(sc, addrs, dumps, Replay(rec))
