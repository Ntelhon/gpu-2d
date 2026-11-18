# SPDX-License-Identifier: Apache-2.0
"""G2D command-buffer builder (spec/commands.md). Indices and fields come from spec/regs.yaml.

Shared by golden-model tests now and by the cocotb testbenches from Phase 2 on.

    cb = CmdBuf()
    cb.state("TGT_ADDR", ADDR=0x1000)
    cb.state("TGT_SIZE", W=64, H=32)
    cb.draw_rect(0, 0, 64, 32)
    cb.fence(1, irq=True)
    data = cb.to_bytes()
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_ROOT / "scripts"))

import gen_regs  # noqa: E402

REGMAP = gen_regs.load(_ROOT / "spec" / "regs.yaml")
STATE = {r.name: r for r in REGMAP.state}
CSR = {r.name: r for r in REGMAP.csr}

_C = {g: vals for g, (_, _, vals) in REGMAP.constants.items()}   # spec/regs.yaml "constants"
OP_NOP, OP_LOAD_STATE, OP_DRAW_RECT, OP_DRAW_PATH, OP_FENCE = (
    _C["opcode"][k] for k in ("NOP", "LOAD_STATE", "DRAW_RECT", "DRAW_PATH", "FENCE"))

# Hardware FMT codes (rendering.md §3.2), keyed by VGLite enum name without the VG_LITE_ prefix.
FMT = dict(_C["format"])
# Bits per pixel by format class (rendering.md §3.2): A8 L8 565 8888 4444 5551 2222 A4 A2 A1 L4 I8 I4 I2 I1.
_CLASS_BITS = (8, 8, 16, 32, 16, 16, 8, 4, 2, 1, 4, 8, 4, 2, 1, 16)   # ..., A8L8 (v1.6)
FMT_BITS = {code: (24 if code & 0xF <= 2 else 16 if code & 0xF == 3 else 8) if code & 0x80 else _CLASS_BITS[code & 0xF]
            for code in FMT.values()}   # 24-bit; YUV (v1.6b): the Y / packed plane
FMT_BYTES = {code: bits // 8 for code, bits in FMT_BITS.items()}  # 0 for sub-byte formats
SRC_ONLY_FMTS = [c for c in FMT.values() if c & 0x80 or (c & 0xF) >= 7]   # sub-byte, indexed, A8L8, extended (§3.2)
INDEXED_FMTS = [c for c in FMT.values() if not c & 0x80 and 11 <= (c & 0xF) <= 14]
B24_FMTS = [c for c in FMT.values() if c & 0x80 and (c & 0xF) <= 2]
YUV_FMTS = [c for c in FMT.values() if c & 0x80 and 3 <= (c & 0xF) <= 5]     # rendering.md §3.7 (v1.6b)
TGT_FMTS = [c for c in FMT.values() if c not in SRC_ONLY_FMTS]


def row_bytes(fmt, w):
    """Bytes per row of w pixels: ceil(w * bpp / 8) (rendering.md §13); YUY2: 4 ceil(w / 2)."""
    if fmt in YUV_FMTS and fmt & 0xF == 3:
        return 4 * ((w + 1) // 2)
    return (w * FMT_BITS[fmt] + 7) // 8

BLEND = dict(_C["blend"])
PAINT_SOLID, PAINT_IMAGE, PAINT_GRAD, PAINT_RADIAL = (_C["paint"][k] for k in ("SOLID", "IMAGE", "GRAD", "RADIAL"))
ALPHA_NORMAL, ALPHA_GLOBAL, ALPHA_SCALED = (_C["alpha"][k] for k in ("NORMAL", "GLOBAL", "SCALED"))
SPREAD_PAD, SPREAD_REPEAT, SPREAD_REFLECT, SPREAD_FILL = (_C["spread"][k] for k in ("PAD", "REPEAT", "REFLECT", "FILL"))
WRAP = dict(_C["wrap"])


def state_word(name: str, **fields) -> int:
    """Pack field values into a state word; unspecified fields are 0 (not the reset value)."""
    reg = STATE[name]
    by_name = {f.name: f for f in reg.fields}
    v = 0
    for k, val in fields.items():
        f = by_name[k]
        val = int(val) & ((1 << f.width) - 1)  # signed values are written as two's complement
        v |= val << f.lsb
    return v


def s16pair(lo: int, hi: int) -> int:
    return (lo & 0xFFFF) | ((hi & 0xFFFF) << 16)


class CmdBuf:
    def __init__(self):
        self.words: list[int] = []

    def __len__(self):
        return len(self.words)

    # ---- raw commands ----
    def nop(self, skip: int = 0, fill: int = 0):
        self.words.append((OP_NOP << 24) | (skip & 0xFFFF))
        self.words.extend([fill] * skip)
        return self

    def load_state(self, index: int, values):
        values = [v & 0xFFFFFFFF for v in values]
        assert 1 <= len(values) and index + len(values) <= REGMAP.state_size
        self.words.append((OP_LOAD_STATE << 24) | (len(values) << 16) | index)
        self.words.extend(values)
        return self

    def state(self, name: str, **fields):
        return self.load_state(STATE[name].addr, [state_word(name, **fields)])

    def state_raw(self, name: str, value: int):
        return self.load_state(STATE[name].addr, [value])

    def draw_rect(self, x0, y0, x1, y1):
        self.words += [OP_DRAW_RECT << 24, s16pair(x0, y0), s16pair(x1, y1)]
        return self

    def draw_path(self, bbox, edge_addr: int, edge_count: int):
        bx0, by0, bx1, by1 = bbox
        self.words += [OP_DRAW_PATH << 24, s16pair(bx0, by0), s16pair(bx1, by1), edge_addr, edge_count]
        return self

    def fence(self, value: int, irq: bool = False):
        self.words += [(OP_FENCE << 24) | int(irq), value & 0xFFFFFFFF]
        return self

    # ---- convenience ----
    flip_y = False      # v1.6: every target of this buffer with TGT_FMT.FLIP_Y (rendering.md §5.8)

    def target(self, addr, w, h, fmt, stride=None, dither=False):
        stride = stride if stride is not None else w * FMT_BYTES[fmt]
        self.load_state(STATE["TGT_ADDR"].addr, [addr, stride, state_word("TGT_SIZE", W=w, H=h),
                                                 state_word("TGT_FMT", FMT=fmt, DITHER=int(dither),
                                                            FLIP_Y=int(self.flip_y))])
        return self

    def clip(self, x0, y0, x1, y1):
        return self.load_state(STATE["CLIP_MIN"].addr, [state_word("CLIP_MIN", X0=x0, Y0=y0),
                                                        state_word("CLIP_MAX", X1=x1, Y1=y1)])

    def source(self, addr, w, h, fmt, stride=None, rect=None, force_opaque=False, sub_order=0, transparent=False,
               planes=None, yuv709=False, uv_swap=False):
        """planes = (plane-1 addr, stride, plane-2 addr, stride) for YUV sources (rendering.md §3.7, v1.6b)."""
        stride = stride if stride is not None else row_bytes(fmt, w)
        x0, y0, x1, y1 = rect if rect is not None else (0, 0, w, h)
        self.load_state(STATE["SRC_ADDR"].addr, [
            addr, stride, state_word("SRC_SIZE", W=w, H=h),
            state_word("SRC_FMT", FMT=fmt, FORCE_OPAQUE=int(force_opaque), SUB_ORDER=sub_order,
                       TRANSPARENT=int(transparent), YUV_709=int(yuv709), UV_SWAP=int(uv_swap)),
            state_word("SRC_RECT_MIN", X0=x0, Y0=y0), state_word("SRC_RECT_MAX", X1=x1, Y1=y1)])
        if planes is not None:
            a1, s1, a2, s2 = planes
            self.load_state(STATE["SRC_PLANE1_ADDR"].addr, [a1, a2, state_word("SRC_PLANE_STRIDE", S1=s1, S2=s2)])
        return self

    def xform(self, xf):
        """XF_A..XF_I (nine words); PAINT_CTRL.PERSP selects the perspective divide."""
        assert len(xf) == 9
        return self.load_state(STATE["XF_A"].addr, list(xf))

    def pad(self):
        """Pad with NOP words to a multiple of 8 bytes."""
        if len(self.words) % 2:
            self.nop()
        return self

    def to_bytes(self) -> bytes:
        self.pad()
        return struct.pack(f"<{len(self.words)}I", *self.words)


def edges_to_bytes(edges) -> bytes:
    """Edge records (commands.md §2.4): int32 x0, y0, x1, y1 in s.4."""
    return b"".join(struct.pack("<4i", *e) for e in edges)

