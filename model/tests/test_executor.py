# SPDX-License-Identifier: Apache-2.0
"""Command-stream executor: random scenes vs the independent reference pipeline, plus directed tests of
command decode, validation and error semantics (commands.md, rendering.md §13)."""

import math
import os
import random
import struct

import pytest

import g2d_model as gm
import refimpl as ref
from g2d_cmd import (REGMAP, BLEND, FMT, FMT_BITS, FMT_BYTES, INDEXED_FMTS, STATE, TGT_FMTS, CmdBuf, edges_to_bytes, row_bytes,
                     state_word)

SEED = int(os.environ.get("SEED", "1"))
N_SCENES = int(os.environ.get("G2D_N_SCENES", "400"))
FMTS = [f for f in FMT.values() if not gm.fmt_yuv(f)]   # sources: every one-plane code (YUV: planes, own tests)
CLUT_ADDR = 0x4800_0000
CMD_ADDR = 0x0010_0000
TGT_BASE = 0x2000_0000
SRC_BASE = 0x3000_0000
RAMP_ADDR = 0x4000_0000
EDGE_ADDR = 0x5000_0000

WMASK = {r.name: sum(((1 << f.width) - 1) << f.lsb for f in r.fields) for r in STATE.values()}


class Scene:
    """Initial memory contents and a 64-word state image for one draw."""

    def __init__(self, rng: random.Random, kind: str):
        self.rng = rng
        self.kind = kind
        self.mem_init: list[tuple[int, bytes]] = []
        self.state = {name: 0 for name in STATE}
        self.state.update({n: r.reset for n, r in STATE.items()})
        self._target()
        self._clip()
        self._blend()
        if kind == "rect":
            self.paint = rng.choice([0, 1, 1, 2])
        else:
            self.paint = rng.choice([0, 0, 2])
        self._paint()
        if kind == "rect":
            w, h = self.tw, self.th
            if rng.random() < 0.9:
                ex0, ey0, ex1, ey1 = self.eclip
                x0, y0 = rng.randint(ex0 - 4, ex1 - 1), rng.randint(ey0 - 4, ey1 - 1)
                self.rect = (x0, y0, rng.randint(max(x0 + 1, ex0 + 1), ex1 + 4), rng.randint(max(y0 + 1, ey0 + 1), ey1 + 4))
            else:  # arbitrary, including empty and inverted rectangles
                self.rect = (rng.randint(-4, w), rng.randint(-4, h), rng.randint(-2, w + 4), rng.randint(-2, h + 4))
            if self.paint == 1 and rng.random() < 0.7:
                self.rect = self.blit_rect
        else:
            self._path()

    def _buf(self, base, max_wh, fmts):
        rng = self.rng
        fmt = rng.choice(fmts)
        al = max(1, FMT_BYTES[fmt])  # sub-byte sources: any byte address / stride
        w, h = rng.randint(1, max_wh), rng.randint(1, max_wh)
        stride = row_bytes(fmt, w) + rng.choice([0, 0, 1, 3, 8]) * al
        addr = base + rng.randrange(0, 64) * al
        data = bytes(rng.getrandbits(8) for _ in range(stride * (h - 1) + row_bytes(fmt, w)))
        self.mem_init.append((addr, data))
        return fmt, w, h, stride, addr

    def _target(self):
        fmt, w, h, stride, addr = self._buf(TGT_BASE, 32, TGT_FMTS)
        self.tfmt, self.tw, self.th, self.tstride, self.taddr = fmt, w, h, stride, addr
        self.state.update(TGT_ADDR=addr, TGT_STRIDE=stride, TGT_SIZE=state_word("TGT_SIZE", W=w, H=h),
                          TGT_FMT=state_word("TGT_FMT", FMT=fmt, DITHER=self.rng.random() < 0.3))

    def _clip(self):
        rng = self.rng
        if rng.random() < 0.6:
            x0, y0, x1, y1 = 0, 0, self.tw, self.th
        elif rng.random() < 0.9:  # non-empty scissor, may extend past the target
            x0, y0 = rng.randrange(self.tw), rng.randrange(self.th)
            x1, y1 = rng.randint(x0 + 1, self.tw + 4), rng.randint(y0 + 1, self.th + 4)
        else:  # deliberately arbitrary (often empty)
            x0, y0 = rng.randint(0, self.tw), rng.randint(0, self.th)
            x1, y1 = rng.randint(0, self.tw + 4), rng.randint(0, self.th + 4)
        self.state.update(CLIP_MIN=state_word("CLIP_MIN", X0=x0, Y0=y0), CLIP_MAX=state_word("CLIP_MAX", X1=x1, Y1=y1))
        # effective drawable region (scissor ∩ target), used to place primitives so most draws hit pixels
        ex0, ey0, ex1, ey1 = x0, y0, min(x1, self.tw), min(y1, self.th)
        self.eclip = (ex0, ey0, ex1, ey1) if ex1 > ex0 and ey1 > ey0 else (0, 0, self.tw, self.th)

    def _blend(self):
        rng = self.rng
        self.state["BLEND"] = rng.randrange(16)
        self.state["ALPHA_CTRL"] = state_word("ALPHA_CTRL", SRC_MODE=rng.randrange(3), DST_MODE=rng.randrange(3),
                                              SRC_VALUE=rng.randrange(256), DST_VALUE=rng.randrange(256))
        self.state["PAINT_COLOR"] = rng.getrandbits(32)

    def _paint(self):
        rng = self.rng
        pc = dict(SRC=self.paint)
        if self.paint == 1:
            fmt, w, h, stride, addr = self._buf(SRC_BASE, 24, FMTS)
            if rng.random() < 0.5:
                rx0, ry0, rx1, ry1 = 0, 0, w, h
            else:
                rx0, ry0 = rng.randrange(w), rng.randrange(h)
                rx1, ry1 = rng.randint(rx0 + 1, w), rng.randint(ry0 + 1, h)
            self.state.update(SRC_ADDR=addr, SRC_STRIDE=stride, SRC_SIZE=state_word("SRC_SIZE", W=w, H=h),
                              SRC_FMT=state_word("SRC_FMT", FMT=fmt, FORCE_OPAQUE=rng.random() < 0.2,
                                                 SUB_ORDER=rng.random() < 0.5, TRANSPARENT=rng.random() < 0.2),
                              SRC_RECT_MIN=state_word("SRC_RECT_MIN", X0=rx0, Y0=ry0),
                              SRC_RECT_MAX=state_word("SRC_RECT_MAX", X1=rx1, Y1=ry1))
            # colour keys: random ranges, some built around a texel colour of the source
            for k in range(4):
                lo = [rng.randrange(256) for _ in range(3)]
                hi = [min(255, v + rng.choice([0, 4, 64, 255])) for v in lo]
                self.state[f"CKEY{k}_LO"] = state_word("CKEY0_LO", R=lo[0], G=lo[1], B=lo[2], EN=rng.random() < 0.3)
                self.state[f"CKEY{k}_HI"] = state_word("CKEY0_HI", R=hi[0], G=hi[1], B=hi[2], ALPHA=rng.randrange(256))
            persp = rng.random() < 0.35
            ang = rng.uniform(-3.2, 3.2) if rng.random() < 0.7 else rng.choice([0.0, 1.5707963267948966, 3.141592653589793])
            sc = rng.choice([1.0, 1.0, 0.5, 2.0, rng.uniform(0.3, 3.0)])
            import math

            c, s = math.cos(ang) * sc, math.sin(ang) * sc
            # rotate/scale about the source-rect centre, which lands at a random point of the drawable region
            ex0, ey0, ex1, ey1 = self.eclip
            dcx, dcy = rng.uniform(ex0, ex1), rng.uniform(ey0, ey1)
            scx, scy = (rx0 + rx1) / 2, (ry0 + ry1) / 2
            tx, ty = dcx - (c * scx - s * scy), dcy - (s * scx + c * scy)
            g = rng.uniform(-0.02, 0.02) if persp else 0.0
            h = rng.uniform(-0.02, 0.02) if persp else 0.0
            m = [[c, -s, tx], [s, c, ty], [g, h, 1.0 - g * dcx - h * dcy]]  # w = 1 at the centre
            rc, xf, is_persp, draw = gm.blit_setup(m, [rx0, ry0, rx1, ry1], list(self.eclip))
            if rc != gm.GEOM_OK:  # degenerate: fall back to identity
                xf, is_persp, draw = [65536, 0, 0, 0, 65536, 0, 0, 0, 1 << 30], False, (0, 0, self.tw, self.th)
            for name, v in zip("ABCDEFGHI", xf):
                self.state[f"XF_{name}"] = v & 0xFFFFFFFF
            self.blit_rect = draw
            wrap = rng.choice([0, 0, 1, 2, 3, 4])
            if wrap and rng.random() < 0.7:
                self.blit_rect = self.eclip  # wrapped images cover the whole drawable region
            if fmt in INDEXED_FMTS:
                self.mem_init.append((CLUT_ADDR, bytes(rng.getrandbits(8) for _ in range(1024))))
                self.state["CLUT_ADDR"] = CLUT_ADDR
            self.state["BORDER_COLOR"] = rng.getrandbits(32)
            pc.update(IMG_MUL=rng.random() < 0.3, SRC_PREMUL=rng.random() < 0.3, FILTER=rng.random() < 0.5,
                      PERSP=is_persp, WRAP=wrap, BORDER_SOLID=rng.random() < 0.5)
        elif self.paint == 2:
            ramp = bytes(rng.getrandbits(8) for _ in range(1024))
            self.mem_init.append((RAMP_ADDR, ramp))
            p = rng.randint(-(64 << 16), 64 << 16)
            q = rng.randint(-(64 << 16), 64 << 16)
            r = rng.randint(-(2**40), 2**40)
            self.state.update(GRAD_P=p & 0xFFFFFFFF, GRAD_Q=q & 0xFFFFFFFF, GRAD_R=r & 0xFFFFFFFF,
                              GRAD_R_HI=(r >> 32) & 0xFFFF, GRAD_RAMP_ADDR=RAMP_ADDR)
            pc.update(SPREAD=rng.randrange(4))
        self.state["PAINT_CTRL"] = state_word("PAINT_CTRL", **pc)

    def _path(self):
        rng = self.rng
        edges = []
        for _ in range(rng.choice([1, 1, 2, 3])):
            n = rng.choice([3, 4, 5, 7, 12])
            lo, hi_x, hi_y = -32, self.tw * 16 + 32, self.th * 16 + 32
            pts = [(rng.randint(lo, hi_x), rng.randint(lo, hi_y)) for _ in range(n)]
            edges += [(pts[i][0], pts[i][1], pts[(i + 1) % n][0], pts[(i + 1) % n][1]) for i in range(n)]
        self.edges = edges
        xs = [v for e in edges for v in (e[0], e[2])]
        ys = [v for e in edges for v in (e[1], e[3])]
        self.rect = (min(xs) // 16, min(ys) // 16, -(-max(xs) // 16), -(-max(ys) // 16))
        self.mem_init.append((EDGE_ADDR, edges_to_bytes(edges)))
        self.state["RAST_CTRL"] = state_word("RAST_CTRL", FILL_RULE=rng.random() < 0.5, AA=rng.random() < 0.75)

    def command_buffer(self):
        cb = CmdBuf()
        words = [self.rng.getrandbits(32) for _ in range(REGMAP.state_size)]  # garbage in unused indices
        for name, reg in STATE.items():
            words[reg.addr] = self.state[name]
        cb.load_state(0, words)
        if self.kind == "rect":
            cb.draw_rect(*self.rect)
        else:
            cb.draw_path(self.rect, EDGE_ADDR, len(self.edges))
        cb.fence(0x1234_0000 + 7, irq=True)
        return cb.to_bytes()

    def masked_state(self):
        return {n: v & WMASK[n] for n, v in self.state.items()}


def _run_both(scene: Scene):
    mem = gm.Memory()
    rmem = ref.RefMemory()
    for addr, data in scene.mem_init:
        mem.write(addr, data)
        rmem.write(addr, data)
    cmd = scene.command_buffer()
    mem.write(CMD_ADDR, cmd)
    rmem.write(CMD_ADDR, cmd)
    gpu = gm.Gpu(mem)
    assert gpu.kick(CMD_ADDR, len(cmd)), hex(gpu.err_status)
    assert gpu.err_status == 0 and gpu.fence_seq == 0x1234_0007 and gpu.irq_fence
    ref.ref_draw(rmem, scene.masked_state(), scene.rect, scene.edges if scene.kind == "path" else None)
    for page in mem.pages():
        got = mem.read(page, 4096)
        want = bytes(rmem.b.get(page + i, 0) for i in range(4096))
        if got != want:
            i = next(k for k in range(4096) if got[k] != want[k])
            pytest.fail(f"mismatch at 0x{page + i:08x}: model {got[i]:#04x} ref {want[i]:#04x}")
    return gpu


def _run_chunk(kind, seed, n):
    rng = random.Random(seed)
    drawn = []
    for _ in range(n):
        drawn.append(_run_both(Scene(rng, kind)).stats.pixels_out)
    # Guard against a degenerate generator: most scenes must actually draw pixels. Over seeds 1-10 the
    # worst chunk drew in 73 % of scenes and >= 17.6 px/scene; a broken generator measured 18 % / 4.5.
    assert sum(1 for p in drawn if p > 0) >= 0.5 * n, drawn
    assert sum(drawn) >= 10 * n, sum(drawn)


@pytest.mark.parametrize("chunk", range(4))
def test_random_rect_scenes(chunk):
    _run_chunk("rect", SEED * 1000 + chunk, N_SCENES // 4)


@pytest.mark.parametrize("chunk", range(4))
def test_random_path_scenes(chunk):
    _run_chunk("path", SEED * 2000 + chunk, N_SCENES // 8)


# ------------------------------------------------------------------------------------------------
# Directed: decode, validation, errors, fence, state
# ------------------------------------------------------------------------------------------------

def _gpu_with(cmd: bytes, *extra):
    mem = gm.Memory()
    mem.write(CMD_ADDR, cmd)
    for addr, data in extra:
        mem.write(addr, data)
    return mem, gm.Gpu(mem)


def _valid_target(cb, w=8, h=8, fmt=FMT["BGRA8888"], addr=TGT_BASE):
    cb.target(addr, w, h, fmt)
    cb.clip(0, 0, w, h)
    return cb


def _words(*w):
    return struct.pack(f"<{len(w)}I", *w)


@pytest.mark.parametrize("words,why", [
    ((0x7F000000, 0), "unknown opcode"),
    ((0x00010000, 0), "NOP reserved bits"),
    ((0x01000100, 0), "LOAD_STATE reserved bits"),
    ((0x01000000, 0), "LOAD_STATE count 0"),
    ((0x0102007F, 1, 2, 0), "LOAD_STATE index+count > 128"),   # 64 until v1.6
    ((0x10000001, 0, 0, 0), "DRAW_RECT reserved bits"),
    ((0x20000002, 0), "FENCE reserved bits"),
    ((0x01030000, 1, 2, 3, 4, 0), "crosses buffer end"),  # needs 3 payload words, buffer has fewer after pad
])
def test_cmd_decode_errors(words, why):
    data = _words(*words)
    if words == (0x01030000, 1, 2, 3, 4, 0):
        data = _words(0x00000000, 0x01030000, 1, 2)  # NOP, then LOAD_STATE needing 3 payload words, only 2 left
    mem, gpu = _gpu_with(data)
    assert not gpu.kick(CMD_ADDR, len(data)), why
    assert gpu.halted and gpu.err_status == 1 and gpu.err_addr == 0
    hdr = CMD_ADDR + (4 if data[:4] == b"\0\0\0\0" and len(data) == 16 else 0)
    assert gpu.err_cmd_addr == hdr, why


def test_kick_size_and_window_errors():
    mem, gpu = _gpu_with(_words(0, 0))
    assert not gpu.kick(CMD_ADDR, 0) and gpu.err_status == 1 and gpu.err_cmd_addr == CMD_ADDR
    gpu.soft_reset()
    assert not gpu.kick(CMD_ADDR, 12) and gpu.err_status == 1
    gpu.soft_reset()
    gpu.set_addr_win(CMD_ADDR + 0x40, 0xFFFFFFFF)
    assert gpu.addr_win_lo == CMD_ADDR + 0x40
    assert not gpu.kick(CMD_ADDR, 8) and gpu.err_status == 4 and gpu.err_addr == CMD_ADDR
    # 64-byte granularity of the window registers
    gpu.set_addr_win(0x1234_5678, 0x8765_4300)
    assert gpu.addr_win_lo == 0x1234_5640 and gpu.addr_win_hi == 0x8765_433F
    # wrap past 2^32 is an error
    gpu.soft_reset()
    gpu.set_addr_win(0, 0xFFFFFFFF)
    assert not gpu.kick(0xFFFF_FFF8, 16) and gpu.err_status == 4


def _bad_state_cases():
    t = FMT["BGRA8888"]
    yield "bad target fmt", lambda cb: cb.state("TGT_FMT", FMT=0x44)
    yield "source-only target A4", lambda cb: cb.state("TGT_FMT", FMT=FMT["A4"])
    yield "source-only target I8", lambda cb: cb.state("TGT_FMT", FMT=FMT["I8"])
    yield "zero width", lambda cb: cb.state("TGT_SIZE", W=0, H=8)
    yield "width > 8192", lambda cb: cb.state("TGT_SIZE", W=8193, H=8)
    yield "misaligned target", lambda cb: cb.state_raw("TGT_ADDR", TGT_BASE + 2)
    yield "stride too small", lambda cb: cb.state("TGT_STRIDE", STRIDE=8 * 4 - 4)
    yield "stride not multiple", lambda cb: cb.state("TGT_STRIDE", STRIDE=8 * 4 + 2)
    yield "src alpha mode 3", lambda cb: cb.state("ALPHA_CTRL", SRC_MODE=3)
    yield "dst alpha mode 3", lambda cb: cb.state("ALPHA_CTRL", DST_MODE=3)
    # v1.5: PAINT_CTRL.SRC = 3 is RADIAL_GRAD (legal); its ramp follows the gradient rules
    yield "radial ramp misaligned", lambda cb: (cb.state("PAINT_CTRL", SRC=3), cb.state_raw("GRAD_RAMP_ADDR", 0x0010_0002))

    def img(extra):
        def f(cb):
            cb.source(SRC_BASE, 4, 4, t)
            cb.state("PAINT_CTRL", SRC=1)
            extra(cb)
        return f
    yield "src bad fmt", img(lambda cb: cb.state("SRC_FMT", FMT=0x45))
    yield "src bad fmt 0x17", img(lambda cb: cb.state("SRC_FMT", FMT=0x17))
    yield "wrap 5", img(lambda cb: cb.state("PAINT_CTRL", SRC=1, WRAP=5))
    yield "wrap 7", img(lambda cb: cb.state("PAINT_CTRL", SRC=1, WRAP=7))
    yield "clut misaligned", img(lambda cb: (cb.source(SRC_BASE, 4, 4, FMT["I4"]), cb.state_raw("CLUT_ADDR", CLUT_ADDR + 2)))
    yield "sub-byte stride too small", img(lambda cb: cb.source(SRC_BASE, 9, 4, FMT["A1"], stride=1))
    yield "565 src stride odd", img(lambda cb: cb.source(SRC_BASE, 4, 4, FMT["BGRA5551"], stride=9))
    yield "src rect empty", img(lambda cb: cb.state("SRC_RECT_MAX", X1=0, Y1=4))
    yield "src rect beyond size", img(lambda cb: cb.state("SRC_RECT_MAX", X1=5, Y1=4))
    yield "src misaligned", img(lambda cb: cb.state_raw("SRC_ADDR", SRC_BASE + 1))
    yield "ramp misaligned", lambda cb: (cb.state("PAINT_CTRL", SRC=2), cb.state_raw("GRAD_RAMP_ADDR", RAMP_ADDR + 2))


@pytest.mark.parametrize("name,setup", list(_bad_state_cases()))
def test_bad_state(name, setup):
    cb = _valid_target(CmdBuf())
    setup(cb)
    hdr_index = len(cb)
    cb.draw_rect(0, 0, 8, 8)
    cb.fence(99)
    data = cb.to_bytes()
    mem, gpu = _gpu_with(data, (TGT_BASE, b"\xAA" * 256))
    assert not gpu.kick(CMD_ADDR, len(data)), name
    assert gpu.err_status == 2 and gpu.err_addr == 0 and gpu.err_cmd_addr == CMD_ADDR + 4 * hdr_index
    assert mem.read(TGT_BASE, 256) == b"\xAA" * 256, "failing draw must not write"
    assert gpu.fence_seq == 0, "commands after a halting error are not executed"
    assert not gpu.kick(CMD_ADDR, len(data)), "halted GPU ignores kicks"


def test_path_rules():
    # image paint on a path is legal (v1.5, F-1); misaligned edge list is BAD_STATE; empty list is a no-op
    for setup, count, eaddr, ok in [
        (lambda cb: (cb.source(SRC_BASE, 4, 4, FMT["A8"]), cb.state("PAINT_CTRL", SRC=1)), 3, EDGE_ADDR, True),
        (lambda cb: None, 3, EDGE_ADDR + 8, False),
        (lambda cb: None, 0, EDGE_ADDR + 8, True),
    ]:
        cb = _valid_target(CmdBuf())
        setup(cb)
        cb.draw_path((0, 0, 8, 8), eaddr, count)
        data = cb.to_bytes()
        mem, gpu = _gpu_with(data)
        assert gpu.kick(CMD_ADDR, len(data)) == ok
        if not ok:
            assert gpu.err_status == 2


def test_addr_range_order_and_first_error():
    # Target outside the window -> ERR_ADDR = target; source also outside but target is checked first.
    cb = _valid_target(CmdBuf())
    cb.source(SRC_BASE, 4, 4, FMT["BGRA8888"])
    cb.state("PAINT_CTRL", SRC=1)
    cb.draw_rect(0, 0, 8, 8)
    data = cb.to_bytes()
    mem, gpu = _gpu_with(data)
    gpu.set_addr_win(0, TGT_BASE + 8 * 8 * 4 - 2)  # target extent ends at +255; window ends at +255 (64B granule)
    assert gpu.kick(CMD_ADDR, len(data)) is False or gpu.err_status == 4
    assert gpu.err_status == 4 and gpu.err_addr == SRC_BASE  # target fits (granule), source does not
    gpu.soft_reset()
    gpu.set_addr_win(0, TGT_BASE - 1)
    assert not gpu.kick(CMD_ADDR, len(data))
    assert gpu.err_status == 4 and gpu.err_addr == TGT_BASE


def test_v14_valid_edge_cases_and_clut_range():
    """Sub-byte sources at odd addresses / strides are legal; the CLUT extent is checked after the source (§13.2)."""
    for fmt, w, stride in ((FMT["A1"], 9, 2), (FMT["I2"], 5, 2), (FMT["L4"], 3, 2), (FMT["I1"], 8, 1)):
        cb = _valid_target(CmdBuf())
        cb.source(SRC_BASE + 3, w, 2, fmt, stride=stride)
        cb.state_raw("CLUT_ADDR", CLUT_ADDR)
        cb.state("PAINT_CTRL", SRC=1)
        cb.draw_rect(0, 0, 8, 8)
        data = cb.to_bytes()
        mem, gpu = _gpu_with(data)
        assert gpu.kick(CMD_ADDR, len(data)) and gpu.err_status == 0, hex(fmt)
    cb = _valid_target(CmdBuf())
    cb.source(SRC_BASE, 4, 4, FMT["I8"])
    cb.state_raw("CLUT_ADDR", 0xFFFF_FE00)  # 1 KB table would wrap past 2^32
    cb.state("PAINT_CTRL", SRC=1)
    cb.draw_rect(0, 0, 8, 8)
    data = cb.to_bytes()
    mem, gpu = _gpu_with(data)
    assert not gpu.kick(CMD_ADDR, len(data)) and gpu.err_status == 4 and gpu.err_addr == 0xFFFF_FE00


def test_fence_nop_and_state_masking():
    cb = CmdBuf()
    cb.nop(3, fill=0xDEADBEEF)
    cb.load_state(STATE["TGT_SIZE"].addr, [0xFFFFFFFF])
    cb.load_state(0x7F, [0xFFFFFFFF])  # unused index: ignored
    cb.fence(5)
    cb.fence(6, irq=True)
    data = cb.to_bytes()
    mem, gpu = _gpu_with(data)
    assert gpu.kick(CMD_ADDR, len(data))
    assert gpu.fence_seq == 6 and gpu.irq_fence
    assert gpu.state(STATE["TGT_SIZE"].addr) == WMASK["TGT_SIZE"]
    assert gpu.state(0x7F) == 0
    gpu.soft_reset()
    assert gpu.state(STATE["XF_A"].addr) == STATE["XF_A"].reset and gpu.fence_seq == 0 and not gpu.irq_fence


def test_state_persists_across_kicks_and_empty_draws():
    cb1 = _valid_target(CmdBuf())
    cb1.state_raw("PAINT_COLOR", 0xFF112233)
    d1 = cb1.to_bytes()
    cb2 = CmdBuf()
    cb2.draw_rect(0, 0, 2, 1)
    cb2.draw_rect(5, 5, 5, 9)  # empty: valid no-op
    d2 = cb2.to_bytes()
    mem, gpu = _gpu_with(d1)
    mem.write(CMD_ADDR + 0x100, d2)
    assert gpu.kick(CMD_ADDR, len(d1)) and gpu.kick(CMD_ADDR + 0x100, len(d2))
    assert mem.read(TGT_BASE, 8) == bytes([0x33, 0x22, 0x11, 0xFF] * 2)
    assert gpu.stats.draws == 2 and gpu.stats.pixels_out == 2


def test_mask_layer_by_hand():
    """rendering.md §6.1 (v1.6), derived by hand: an A8 mask 3 x 2 at origin (2, 1) on an 8 x 4 BGRA8888 target; a
    SOLID fill of the whole target with blend NONE gives lerp8(D, S, m) inside the mask (m = 255: S, m = 0: D) and
    leaves every pixel outside the mask untouched (outside = 0, skipped); mask disabled: the plain fill."""
    MASK = 0x6000_0000
    mvals = [255, 0, 128, 1, 64, 254]            # rows of 3, stride 4
    mdata = bytes(mvals[0:3]) + b"\x00" + bytes(mvals[3:6]) + b"\x00"
    d, s = 0x10203040, 0xF0E0D0C0                # ARGB
    div255r = lambda v: (v + 128 + ((v + 128) >> 8)) >> 8  # noqa: E731
    for en in (1, 0):
        cb = _valid_target(CmdBuf(), 8, 4)
        cb.load_state(STATE["MASK_ADDR"].addr, [MASK, 4, state_word("MASK_SIZE", W=3, H=2), 2 | (1 << 16), en])
        cb.state("BLEND", MODE=0)
        cb.state("PAINT_COLOR", ARGB=s)
        cb.draw_rect(0, 0, 8, 4)
        data = cb.to_bytes()
        mem, gpu = _gpu_with(data, (TGT_BASE, struct.pack("<I", d) * 32), (MASK, mdata))
        assert gpu.kick(CMD_ADDR, len(data))
        for y in range(4):
            for x in range(8):
                got = struct.unpack("<I", mem.read(TGT_BASE + 4 * (8 * y + x), 4))[0]
                if not en:
                    exp = s
                elif 2 <= x < 5 and 1 <= y < 3:
                    m = mvals[3 * (y - 1) + (x - 2)]
                    exp = 0
                    for sh in (0, 8, 16, 24):
                        dc, sc = (d >> sh) & 255, (s >> sh) & 255
                        exp |= div255r(sc * m + dc * (255 - m)) << sh
                    if m == 0:
                        exp = d
                else:
                    exp = d
                assert got == exp, (en, x, y, hex(got), hex(exp))


def test_flip_y_by_hand():
    """rendering.md §5.8 (v1.6): with TGT_FMT.FLIP_Y a fill of target row 0 lands in the last stored row."""
    cb = _valid_target(CmdBuf(), 4, 3)
    cb.state_raw("TGT_FMT", FMT["BGRA8888"] | (1 << 9))
    cb.state("PAINT_COLOR", ARGB=0x11223344)
    cb.draw_rect(1, 0, 3, 1)
    data = cb.to_bytes()
    mem, gpu = _gpu_with(data, (TGT_BASE, b"\x00" * 48))
    assert gpu.kick(CMD_ADDR, len(data))
    rows = [mem.read(TGT_BASE + 16 * r, 16) for r in range(3)]
    assert rows[0] == rows[1] == b"\x00" * 16
    assert rows[2] == b"\x00" * 4 + bytes([0x44, 0x33, 0x22, 0x11]) * 2 + b"\x00" * 4


def test_stencil_by_hand():
    """rendering.md §5.3.1 (v1.6), one pixel derived by hand: image I = (A 200, R 255, G 100, B 0) as a stencil for
    P = (A 128, R 10, G 20, B 30), SRC_OVER over D = (255, 40, 50, 60): aa = m(200, 128), ac = m(m(I.c, 200), 128),
    R = m(10, aR) + m(40, ~aR) ..., A = aa + m(255, ~aa)."""
    m = lambda a, b: (a * b + 128 + ((a * b + 128) >> 8)) >> 8  # noqa: E731
    ia, ic = 200, (255, 100, 0)
    pa, pc = 128, (10, 20, 30)
    d = (255, 40, 50, 60)
    aa = m(ia, pa)
    ac = [m(m(c, ia), pa) for c in ic]
    exp_c = [min(255, m(p, a) + m(dc, 255 - a)) for p, a, dc in zip(pc, ac, d[1:])]
    exp_a = min(255, aa + m(d[0], 255 - aa))
    got = gm.blend_stencil(1, (ia << 24) | (ic[0] << 16) | (ic[1] << 8) | ic[2], (pa << 24) | (pc[0] << 16) |
                           (pc[1] << 8) | pc[2], (d[0] << 24) | (d[1] << 16) | (d[2] << 8) | d[3])
    assert got == (exp_a << 24) | (exp_c[0] << 16) | (exp_c[1] << 8) | exp_c[2], hex(got)


def test_gaussian_by_hand():
    """rendering.md §7.3 (v1.6b), signed s3.12 weights, by hand: texels A = 255, R = 10 * k (k = 0..8 row-major), G = 0,
    B = 255. [1 2 1]^2 / 16 (W0 1024, W1 512, W2 256): R = (256 * 160 + 512 * 160 + 1024 * 40 + 2048) >> 12 = 40;
    corners only (W2 = 1024): 40; centre only: the centre texel. Sharpening (W0 = 5 * 4096, W1 = -4096): R =
    5 * 40 - (10 + 30 + 50 + 70) = 40, A 255, B 255; a gain of 2 (W0 = 8192, others 0) saturates B to 255 and
    doubles R; a negative kernel clamps to 0."""
    t = [0xFF0000FF | ((10 * k) << 16) for k in range(9)]
    assert gm.gaussian(t, 1024, 512, 256) == 0xFF2800FF
    assert gm.gaussian(t, 0, 0, 1024) == 0xFF2800FF
    assert gm.gaussian(t, 4096, 0, 0) == t[4]
    assert gm.gaussian(t, 5 * 4096, -4096, 0) == 0xFF2800FF
    assert gm.gaussian(t, 8192, 0, 0) == 0xFF5000FF
    assert gm.gaussian(t, -4096, 0, 0) == 0x00000000
    # rounding: one edge texel R = 1 at weight 2047 / 4096 -> (2047 + 2048) >> 12 = 0; 2048 -> 1
    u = [0xFF000000] * 9
    u[1] = 0xFF010000
    assert gm.gaussian(u, 4096, 2047, 0) & 0xFF0000 == 0 and gm.gaussian(u, 4096, 2048, 0) & 0xFF0000 == 0x10000



def test_color_unit_by_hand():
    """rendering.md §5.2.1 (v1.6b) by hand: identity (M = 256 I) keeps the colour; R' = 2 G - 64 (M[R][G] = 512,
    B[R] = -64 * 256): G 100 -> (512 * 100 - 16384 + 128) >> 8 = 136; negative results clamp to 0, large ones to
    255; gamma: LIN[255] = 255, LIN[0] = 0, SRGB[0] = 0, and LIN[128] = round(255 * ((128/255 + 0.055)/1.055)^2.4)."""
    ident = [256 if i % 5 == 0 else 0 for i in range(16)]
    assert gm.color_apply(0x80402010, True, 0, ident, [0, 0, 0, 0]) == 0x80402010
    m = list(ident)
    m[4 + 1], m[4 + 2] = 0, 512                     # row R: 0 R + 512 G
    assert gm.color_apply(0xFF006400, True, 0, m, [0, -64 * 256, 0, 0]) == 0xFF886400
    assert gm.color_apply(0xFF001000, True, 0, m, [0, -64 * 256, 0, 0]) == 0xFF001000   # 2 * 16 - 64 < 0
    assert gm.color_apply(0xFF00F000, True, 0, m, [0, 0, 0, 0]) == 0xFFFFF000            # 480 -> 255
    lin128 = int(math.floor(255 * ((128 / 255 + 0.055) / 1.055) ** 2.4 + 0.5))
    assert gm.gamma_lin(255) == 255 and gm.gamma_lin(0) == 0 and gm.gamma_srgb(0) == 0 and gm.gamma_lin(128) == lin128
    assert gm.color_apply(0x80FF8000, False, 1, ident, [0] * 4) == 0x80FF0000 | (lin128 << 8)
