# SPDX-License-Identifier: Apache-2.0
"""Small deterministic scenes rendered through the driver geometry (sw/lib/geom) and the golden model.

Used by the float-reference tolerance tests and by the committed reference images.
"""

from __future__ import annotations

import math
import struct

import numpy as np

import g2d_model as gm
from g2d_cmd import BLEND, FMT, PAINT_GRAD, PAINT_IMAGE, PAINT_SOLID, CmdBuf, edges_to_bytes
from g2d_image import buffer_argb

CMD = 0x0010_0000
TGT = 0x1000_0000
SRC = 0x2000_0000
RAMP = 0x3000_0000
EDGES = 0x4000_0000

# ------------------------------------------------------------------------------------------------
# VGLite path data (S32/FP32 encoding: one 4-byte element per opcode and coordinate)
# ------------------------------------------------------------------------------------------------


class PathData:
    def __init__(self):
        self.elems: list[tuple[str, object]] = []
        self.float_polys: list[list] = []  # float reference geometry (path space)
        self._cur = None
        self._start = None

    def _op(self, op, *args):
        self.elems.append(("u", op))
        for a in args:
            self.elems.append(("f", a))

    def move(self, x, y):
        self._op(0x02, x, y)
        self.float_polys.append([(x, y)])
        self._cur = self._start = (x, y)
        return self

    def line(self, x, y):
        self._op(0x04, x, y)
        self.float_polys[-1].append((x, y))
        self._cur = (x, y)
        return self

    def quad(self, cx, cy, x, y):
        from floatref import bezier_points

        self._op(0x06, cx, cy, x, y)
        self.float_polys[-1] += bezier_points(self._cur, (cx, cy), (x, y))
        self._cur = (x, y)
        return self

    def cubic(self, c1x, c1y, c2x, c2y, x, y):
        from floatref import bezier_points

        self._op(0x08, c1x, c1y, c2x, c2y, x, y)
        self.float_polys[-1] += bezier_points(self._cur, (c1x, c1y), (c2x, c2y), (x, y))
        self._cur = (x, y)
        return self

    def close(self):
        self._op(0x01)
        self._cur = self._start
        return self

    def end(self):
        self._op(0x00)
        return self

    def to_bytes(self) -> bytes:
        out = b""
        for kind, v in self.elems:
            out += struct.pack("<I", v) if kind == "u" else struct.pack("<f", v)
        return out


def circle(p: PathData, cx, cy, r):
    k = 0.5522847498307936 * r  # cubic approximation of a quarter circle
    p.move(cx + r, cy)
    p.cubic(cx + r, cy + k, cx + k, cy + r, cx, cy + r)
    p.cubic(cx - k, cy + r, cx - r, cy + k, cx - r, cy)
    p.cubic(cx - r, cy - k, cx - k, cy - r, cx, cy - r)
    p.cubic(cx + k, cy - r, cx + r, cy - k, cx + r, cy)
    return p.close()


def rounded_rect(p: PathData, x0, y0, x1, y1, r):
    p.move(x0 + r, y0).line(x1 - r, y0).quad(x1, y0, x1, y0 + r).line(x1, y1 - r).quad(x1, y1, x1 - r, y1)
    p.line(x0 + r, y1).quad(x0, y1, x0, y1 - r).line(x0, y0 + r).quad(x0, y0, x0 + r, y0)
    return p.close()


def star(p: PathData, cx, cy, r, n=5, step=2):
    pts = [(cx + r * math.cos(2 * math.pi * i * step / n - math.pi / 2),
            cy + r * math.sin(2 * math.pi * i * step / n - math.pi / 2)) for i in range(n)]
    p.move(*pts[0])
    for q in pts[1:]:
        p.line(*q)
    return p.close()


def transform_polys(polys, m):
    return [[(m[0][0] * x + m[0][1] * y + m[0][2], m[1][0] * x + m[1][1] * y + m[1][2]) for x, y in poly]
            for poly in polys]


# ------------------------------------------------------------------------------------------------
# Rendering helpers
# ------------------------------------------------------------------------------------------------


def run(mem, cb: CmdBuf):
    data = cb.to_bytes()
    mem.write(CMD, data)
    gpu = gm.Gpu(mem)
    ok = gpu.kick(CMD, len(data))
    assert ok and gpu.err_status == 0, hex(gpu.err_status)
    return gpu


def render_coverage(path: PathData, m, w, h, even_odd=False, aa=True):
    """Draw the path in white (blend NONE) onto an A8 target: the stored byte is the coverage."""
    rc, edges, bbox = gm.path_edges(path.to_bytes(), 3, m)
    assert rc in (gm.GEOM_OK, gm.GEOM_EMPTY), rc
    mem = gm.Memory()
    mem.write(TGT, bytes(w * h))
    mem.write(EDGES, edges_to_bytes(edges))
    cb = CmdBuf().target(TGT, w, h, FMT["A8"]).clip(0, 0, w, h)
    cb.state("BLEND", MODE=BLEND["NONE"]).state("PAINT_CTRL", SRC=PAINT_SOLID).state_raw("PAINT_COLOR", 0xFFFFFFFF)
    cb.state("RAST_CTRL", FILL_RULE=int(even_odd), AA=int(aa))
    cb.draw_path(bbox, EDGES, len(edges))
    run(mem, cb)
    return np.frombuffer(mem.read(TGT, w * h), np.uint8).reshape(h, w), edges


def render_edges(edges, bbox, w, h, even_odd=False, aa=True):
    """Rasterize given s.4 edges (white, blend NONE) onto an A8 target: the stored byte is the coverage."""
    mem = gm.Memory()
    mem.write(TGT, bytes(w * h))
    if not edges:
        return np.zeros((h, w), np.uint8)
    mem.write(EDGES, edges_to_bytes(edges))
    cb = CmdBuf().target(TGT, w, h, FMT["A8"]).clip(0, 0, w, h)
    cb.state("BLEND", MODE=BLEND["NONE"]).state("PAINT_CTRL", SRC=PAINT_SOLID).state_raw("PAINT_COLOR", 0xFFFFFFFF)
    cb.state("RAST_CTRL", FILL_RULE=int(even_odd), AA=int(aa))
    cb.draw_path(bbox, EDGES, len(edges))
    run(mem, cb)
    return np.frombuffer(mem.read(TGT, w * h), np.uint8).reshape(h, w)


def test_image(w, h, seed=0, smooth=True):
    """Deterministic ARGB source image: smooth gradients + a few hard edges."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    if smooth:
        a = np.full((h, w), 255)
        r = (127 + 127 * np.sin(x / 3.1 + seed)).astype(int)
        g = (127 + 127 * np.cos(y / 2.3)).astype(int)
        b = ((x * 7 + y * 13) % 256)
    else:
        a, r, g, b = (rng.integers(0, 256, (h, w)) for _ in range(4))
    return ((a << 24) | (r << 16) | (g << 8) | b).astype(np.uint32)


def pack_pixels(fmt, argb) -> bytes:
    """Stored bytes of an ARGB32 array in format fmt (rendering.md §3.4), rows packed without padding."""
    nb = gm.fmt_bytes(fmt)
    raw = gm.compress_many(fmt, np.asarray(argb, np.uint32).ravel()).astype("<u4")
    return raw.view(np.uint8).reshape(-1, 4)[:, :nb].tobytes()


def render_blit(src_argb, m, w, h, rect=None, bilinear=True, fmt_src=FMT["BGRA8888"], fmt_dst=FMT["BGRA8888"],
                img_mul=False, color=0):
    sh, sw = src_argb.shape
    rect = rect or (0, 0, sw, sh)
    rc, xf, persp, draw = gm.blit_setup(m, list(rect), [0, 0, w, h])
    mem = gm.Memory()
    mem.write(SRC, pack_pixels(fmt_src, src_argb))
    mem.write(TGT, bytes(w * h * 4))
    if rc == gm.GEOM_EMPTY:
        return buffer_argb(mem.read, TGT, w, h, w * 4, fmt_dst), rc
    assert rc == gm.GEOM_OK, rc
    cb = CmdBuf().target(TGT, w, h, fmt_dst).clip(0, 0, w, h)
    cb.source(SRC, sw, sh, fmt_src, rect=rect)
    cb.xform(xf)
    cb.state("BLEND", MODE=BLEND["NONE"])
    cb.state("PAINT_CTRL", SRC=PAINT_IMAGE, FILTER=int(bilinear), PERSP=int(persp), IMG_MUL=int(img_mul))
    cb.state("PAINT_COLOR", ARGB=color)
    cb.draw_rect(*draw)
    run(mem, cb)
    return buffer_argb(mem.read, TGT, w, h, w * 4, fmt_dst), rc


def render_gradient(gmatrix, w, h, stops, spread=0, ext=None):
    """Legacy (ext=None, stops = (colors_vg, stops)) or extended (ext=(x0,y0,x1,y1), stops = float stops)."""
    if ext is None:
        rc, p, q, r = gm.grad_coeffs(gmatrix)
        rrc, ramp = gm.ramp_legacy(*stops)
    else:
        rc, p, q, r = gm.grad_coeffs(gmatrix, True, *ext)
        rrc, ramp = gm.ramp_ext(stops, False)
    assert rc == gm.GEOM_OK and rrc == gm.GEOM_OK
    mem = gm.Memory()
    mem.write(RAMP, struct.pack("<256I", *ramp))
    mem.write(TGT, bytes(w * h * 4))
    cb = CmdBuf().target(TGT, w, h, FMT["BGRA8888"]).clip(0, 0, w, h)
    cb.state("BLEND", MODE=BLEND["NONE"]).state("PAINT_CTRL", SRC=PAINT_GRAD, SPREAD=spread)
    cb.state_raw("GRAD_P", p & 0xFFFFFFFF).state_raw("GRAD_Q", q & 0xFFFFFFFF)
    cb.state_raw("GRAD_R", r & 0xFFFFFFFF).state_raw("GRAD_R_HI", (r >> 32) & 0xFFFF)
    cb.state_raw("GRAD_RAMP_ADDR", RAMP)
    cb.draw_rect(0, 0, w, h)
    run(mem, cb)
    return buffer_argb(mem.read, TGT, w, h, w * 4, FMT["BGRA8888"]), np.asarray(ramp, np.uint32), (p, q, r)

