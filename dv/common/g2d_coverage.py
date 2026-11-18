# SPDX-License-Identifier: Apache-2.0
"""Functional coverage of the L3 benches (verification-plan.md §4), Phase 3 groups.

The environment samples every draw the golden model executes (G2dEnv.model_kick) with the state it
was issued with. Counts are kept per process and written to build/coverage/<name>.json after every
sample batch (name from G2D_COV_NAME, set by the runner, plus the seed); scripts/coverage_report.py
merges all files into build/coverage/report.md.

Groups (bins):
  cg_blend       blend mode (16) x target FMT (24)                                   RECT draws
  cg_blend_alpha blend mode (16) x source global-alpha mode (3) x destination mode (3) RECT draws
  cg_blit        source FMT (12 v1 formats) x filter (2) x transform class (7)       blits
  cg_blit_dst    source FMT (12 v1) x target FMT (12 v1)                             blits
  cg_blit_opts   IMG_MUL (2) x SRC_PREMUL (2) x FORCE_OPAQUE (2) x colour keys {0,1,2-3,4} blits
  cg_path        fill rule (2) x AA (2) x size {sub-pixel, small, medium, large, guard-band}
                 x edges {<=8, <=64, <=512, >512}                                    DRAW_PATH
  cg_paint_path  paint {solid, gradient, image, radial (v1.5)} x blend (16)           DRAW_PATH
  cg_radial      (v1.5) spread (4) x {centred (T = 0), focal}                         radial gradient draws
  cg_family      (v1.6) OpenVG modes (10) + SVG2 modes (7) + Porter-Duff equations (5)       blend families
  cg_mask        (v1.6) draw {fill, image, gradient, path} x mask {inside, partial, outside} the target   masked draws
  cg_color       (v1.6b) gamma (3) x matrix {off, on}                                            image RECT draws
  cg_gauss       (v1.6b) wrap (5) x weights {centre only, no centre, unity, sharpening, gain}            Gaussian image RECT draws
  cg_grad        spread (4) x angle {0, 90, 45, other} x length {<16, <256, >=256 px} gradient draws
  cg_fill        target FMT (24) x rect class (6) x scissor class (3)                 SOLID-paint RECT draws
                 rect: empty (x1 <= x0 or y1 <= y0), clipped (reaches outside the target), full (exactly
                 the target), 1px, thin (1 px wide or high), normal; scissor: off (contains the target),
                 on-empty (no overlap with the target), on-partial
  cg_align       (address mod 64) x (stride mod 64) x bpp (1/2/4), classes 0 / 1-7 / 8-55 / 56-63
                 target of every draw, source of every image draw
  cg_axi         direction x DATA_W (64/128) x length {1, short, long, max} x 4 KB offset
                 {4k-aligned, ends at 4k, MAX_BURST_BYTES-aligned, other}; short = 2..max/2 beats; a
                 max-length burst always starts MAX_BURST_BYTES-aligned (no "max/other" bin)
                 every AR / AW handshake
  cg_errors      ERR_STATUS bit (6) x {idle, busy}: a draw engine busy when the bit is set
  v1.4:
  cg_src14       source FMT (32) x filter (2)                                        blits
  cg_tgt14       target FMT (24) x dither (2)                                        RECT draws
  cg_wrap        WRAP (5) x filter (2) x perspective (2)                             blits
  cg_img14       TRANSPARENT (2) x SUB_ORDER (2) x indexed (2)                       blits
  cg_border      BORDER_SOLID (2) x filter (2) x IMG_MUL (2)                          WRAP = COLOR blits
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from pathlib import Path

from g2d_cmd import FMT, FMT_BITS, INDEXED_FMTS, STATE, TGT_FMTS

ROOT = Path(__file__).resolve().parents[2]
FMT_NAMES = {v: k for k, v in FMT.items()}
V1_FMTS = {0x00, 0x01, 0x02, 0x12, 0x03, 0x13, 0x23, 0x33, 0x43, 0x53, 0x63, 0x73}
XCLASSES = ["translate", "scale_up", "scale_down", "rotate_90k", "rotate_free", "shear", "perspective"]
CKEY_CLASSES = ["0", "1", "2-3", "4"]
SIZE_CLASSES = ["subpixel", "small", "medium", "large", "guard"]
EDGE_CLASSES = ["le8", "le64", "le512", "gt512"]
ANGLE_CLASSES = ["0", "90", "45", "other"]
LEN_CLASSES = ["lt16", "lt256", "ge256"]
RECT_CLASSES = ["empty", "clipped", "full", "1px", "thin", "normal"]
SCISSOR_CLASSES = ["off", "on-partial", "on-empty"]
MOD64_CLASSES = ["0", "1-7", "8-55", "56-63"]
BLEN_CLASSES = ["1", "short", "long", "max"]
OFF_CLASSES = ["4k", "end4k", "blk", "other"]
ERR_BITS = ["cmd_decode", "bad_state", "addr_range", "axi_rd", "axi_wr", "kick_ovf"]
FMT_BPP = {c: max(1, b // 8) for c, b in FMT_BITS.items()}   # alignment unit in bytes (sub-byte: 1)
GUARD = 16 * 16383            # s.4: a coordinate at or beyond the guard band (clamped by the hardware)


def _s32(v):
    return v - (1 << 32) if v >> 31 else v


def xform_class(st) -> str:
    """Class of the inverse transform XF_A..XF_I (target -> source) as the driver produces it."""
    if (st[STATE["PAINT_CTRL"].addr] >> 5) & 1:
        return "perspective"
    a, b, d, e = (_s32(st[STATE[n].addr]) for n in ("XF_A", "XF_B", "XF_D", "XF_E"))
    one = 1 << 16
    if b == 0 and d == 0:
        if a == one and e == one:
            return "translate"
        if abs(a) == one and abs(e) == one:
            return "rotate_90k"          # 180 degrees (or a mirror)
        return "scale_up" if abs(a) * abs(e) < one * one else "scale_down"
    if a == 0 and e == 0 and abs(b) == one and abs(d) == one:
        return "rotate_90k"
    if a == e and b == -d:
        return "rotate_free"
    return "shear"


def bins():
    fmts = [FMT_NAMES[c] for c in FMT_NAMES if c in V1_FMTS]
    tgts = [FMT_NAMES[c] for c in TGT_FMTS]
    srcs = list(FMT_NAMES.values())
    return {
        "cg_blend": [f"{m}/{f}" for m in range(16) for f in tgts],
        "cg_blend_alpha": [f"{m}/{s}/{d}" for m in range(16) for s in range(3) for d in range(3)],
        "cg_blit": [f"{f}/{flt}/{x}" for f in fmts for flt in ("point", "bilinear") for x in XCLASSES],
        "cg_blit_dst": [f"{s}/{t}" for s in fmts for t in fmts],
        "cg_blit_opts": [f"mul{m}/pre{p}/opq{o}/keys{k}" for m in range(2) for p in range(2) for o in range(2)
                         for k in CKEY_CLASSES],
        "cg_path": [f"eo{r}/aa{a}/{sz}/{e}" for r in range(2) for a in range(2) for sz in SIZE_CLASSES
                    for e in EDGE_CLASSES],
        "cg_paint_path": [f"{p}/{m}" for p in ("solid", "grad", "image", "radial") for m in range(16)],
        "cg_radial": [f"sp{s}/{k}" for s in range(4) for k in ("centred", "focal")],
        "cg_family": [f"ovg{m}" for m in range(10)] + [f"svg{m}" for m in range(7)] + [f"pd{e}" for e in range(5)],
        "cg_mask": [f"{d}/{p}" for d in ("fill", "image", "grad", "path") for p in ("inside", "partial", "outside")],
        "cg_color": [f"g{g}/m{m}" for g in range(3) for m in range(2)],
        "cg_gauss": [f"w{w}/{k}" for w in range(5) for k in ("centre", "nocentre", "unity", "sharpen", "gain")],
        "cg_grad": [f"sp{s}/{a}/{ln}" for s in range(4) for a in ANGLE_CLASSES for ln in LEN_CLASSES],
        "cg_fill": [f"{f}/{r}/{c}" for f in tgts for r in RECT_CLASSES for c in SCISSOR_CLASSES],
        "cg_align": [f"a{a}/s{s}/{b}B" for a in MOD64_CLASSES for s in MOD64_CLASSES for b in (1, 2, 4)],
        "cg_axi": [f"{d}/dw{w}/{ln}/{o}" for d in ("rd", "wr") for w in (64, 128) for ln in BLEN_CLASSES
                   for o in OFF_CLASSES if not (ln == "max" and o == "other")],
        "cg_errors": [f"{e}/{c}" for e in ERR_BITS for c in ("idle", "busy")],
        "cg_src14": [f"{f}/{flt}" for f in srcs for flt in ("point", "bilinear")],
        "cg_tgt14": [f"{f}/d{d}" for f in tgts for d in range(2)],
        "cg_wrap": [f"w{w}/{flt}/p{p}" for w in range(5) for flt in ("point", "bilinear") for p in range(2)],
        "cg_img14": [f"tr{t}/sub{s}/idx{i}" for t in range(2) for s in range(2) for i in range(2)],
        "cg_border": [f"solid{b}/{flt}/mul{m}" for b in range(2) for flt in ("point", "bilinear") for m in range(2)],
    }


def mod64_class(v):
    m = v % 64
    return "0" if m == 0 else "1-7" if m < 8 else "8-55" if m < 56 else "56-63"


def rect_class(x0, y0, x1, y1, w, h):
    if x1 <= x0 or y1 <= y0:
        return "empty"
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return "clipped"
    if (x0, y0, x1, y1) == (0, 0, w, h):
        return "full"
    if x1 - x0 == 1 and y1 - y0 == 1:
        return "1px"
    if x1 - x0 == 1 or y1 - y0 == 1:
        return "thin"
    return "normal"


def scissor_class(cx0, cy0, cx1, cy1, w, h):
    if cx0 <= 0 and cy0 <= 0 and cx1 >= w and cy1 >= h:
        return "off"
    if min(cx1, w) <= max(cx0, 0) or min(cy1, h) <= max(cy0, 0):
        return "on-empty"
    return "on-partial"


def _s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v >> 15 else v


def grad_class(st):
    """(angle class, length class) of the gradient coefficients GRAD_P, GRAD_Q (rendering.md §10.2:
    P = 2^16 * d x_g / dx, Q = 2^16 * d x_g / dy in ramp units; the ramp spans 256 units)."""
    p, q = _s32(st[STATE["GRAD_P"].addr]), _s32(st[STATE["GRAD_Q"].addr])
    if q == 0 and p != 0:
        ang = "0"
    elif p == 0 and q != 0:
        ang = "90"
    elif p != 0 and abs(abs(p) - abs(q)) <= max(1, abs(p) >> 20):
        ang = "45"
    else:
        ang = "other"
    g = math.hypot(p, q)
    length = 256 * 65536 / g if g else math.inf
    return ang, "lt16" if length < 16 else "lt256" if length < 256 else "ge256"


_COUNTS: dict | None = None   # per process: every environment (one per cocotb test) adds to the same counts


class Coverage:
    def __init__(self):
        global _COUNTS
        if _COUNTS is None:
            _COUNTS = {g: Counter() for g in bins()}
        self.counts = _COUNTS
        name = os.environ.get("G2D_COV_NAME", "local")
        self.path = ROOT / "build" / "coverage" / f"{name}_s{os.environ.get('SEED', '1')}.json"

    def sample_align(self, addr, stride, fmt):
        self.counts["cg_align"][f"a{mod64_class(addr)}/s{mod64_class(stride)}/{FMT_BPP.get(fmt & 0xFF, 4)}B"] += 1

    def sample_burst(self, is_read, addr, beats, data_w, max_burst_bytes):
        """One AR / AW handshake (cg_axi)."""
        db = data_w // 8
        maxb = max_burst_bytes // db
        ln = "1" if beats == 1 else "max" if beats == maxb else "short" if beats <= maxb // 2 else "long"
        off = addr % 4096
        oc = ("4k" if off == 0 else "end4k" if off + beats * db == 4096 else
              "blk" if off % max_burst_bytes == 0 else "other")
        self.counts["cg_axi"][f"{'rd' if is_read else 'wr'}/dw{data_w}/{ln}/{oc}"] += 1

    def sample_error(self, bit, busy):
        self.counts["cg_errors"][f"{bit}/{'busy' if busy else 'idle'}"] += 1

    def sample_mask(self, st, kind):
        """A draw with MASK_CTRL.EN (rendering.md §6.1): where the mask buffer lies relative to the target."""
        if not st[STATE["MASK_CTRL"].addr] & 1:
            return
        sz, org, tsz = (st[STATE[n].addr] for n in ("MASK_SIZE", "MASK_ORIGIN", "TGT_SIZE"))
        mw, mh, tw, th = sz & 0x3FFF, (sz >> 16) & 0x3FFF, tsz & 0x3FFF, (tsz >> 16) & 0x3FFF
        ox, oy = _s16(org), _s16(org >> 16)
        if ox >= 0 and oy >= 0 and ox + mw <= tw and oy + mh <= th:
            pl = "inside"
        elif ox >= tw or oy >= th or ox + mw <= 0 or oy + mh <= 0:
            pl = "outside"
        else:
            pl = "partial"
        self.counts["cg_mask"][f"{kind}/{pl}"] += 1

    def sample_family(self, st):
        b = st[STATE["BLEND"].addr]
        fam, mode = (b >> 4) & 3, b & 15
        if fam == 1 and mode <= 9:
            self.counts["cg_family"][f"ovg{mode}"] += 1
        elif fam == 2 and mode <= 6:
            self.counts["cg_family"][f"svg{mode}"] += 1
        elif fam == 3 and ((st[STATE["BLEND_FUNC"].addr] >> 18) & 7) <= 4:
            self.counts["cg_family"][f"pd{(st[STATE['BLEND_FUNC'].addr] >> 18) & 7}"] += 1

    def sample_target(self, st):
        self.sample_family(st)
        self.sample_align(st[STATE["TGT_ADDR"].addr], st[STATE["TGT_STRIDE"].addr] & 0xFFFF,
                          st[STATE["TGT_FMT"].addr])

    def sample_rect(self, st, rect=None):
        """One executed DRAW_RECT with state st (64 words); rect = (x0, y0, x1, y1) of its payload."""
        paint = st[STATE["PAINT_CTRL"].addr]
        src = paint & 3
        mode = st[STATE["BLEND"].addr] & 0xF
        alpha = st[STATE["ALPHA_CTRL"].addr]
        tfmt = FMT_NAMES.get(st[STATE["TGT_FMT"].addr] & 0xFF)
        if tfmt is None:
            return
        self.sample_target(st)
        self.sample_mask(st, ("fill", "image", "grad", "grad")[src])
        self.counts["cg_tgt14"][f"{tfmt}/d{(st[STATE['TGT_FMT'].addr] >> 8) & 1}"] += 1
        if src == 0 and rect is not None:
            size, cmin, cmax = (st[STATE[n].addr] for n in ("TGT_SIZE", "CLIP_MIN", "CLIP_MAX"))
            w, h = size & 0x3FFF, (size >> 16) & 0x3FFF
            sc = scissor_class(cmin & 0x3FFF, (cmin >> 16) & 0x3FFF, cmax & 0x3FFF, (cmax >> 16) & 0x3FFF, w, h)
            self.counts["cg_fill"][f"{tfmt}/{rect_class(*rect, w, h)}/{sc}"] += 1
        if src == 1:
            self.sample_align(st[STATE["SRC_ADDR"].addr], st[STATE["SRC_STRIDE"].addr] & 0xFFFF,
                              st[STATE["SRC_FMT"].addr])
        if src in (2, 3):
            self.sample_grad(st)
            return
        self.counts["cg_blend"][f"{mode}/{tfmt}"] += 1
        self.counts["cg_blend_alpha"][f"{mode}/{alpha & 3}/{(alpha >> 2) & 3}"] += 1
        if src != 1:
            return
        sfmt_word = st[STATE["SRC_FMT"].addr]
        sfmt = FMT_NAMES.get(sfmt_word & 0xFF)
        if sfmt is None:
            return
        flt = "bilinear" if (paint >> 4) & 1 else "point"
        self.counts["cg_src14"][f"{sfmt}/{flt}"] += 1
        cctl = st[STATE["COLOR_CTRL"].addr]                                      # v1.6b colour unit
        if (cctl >> 1) & 3 < 3:
            self.counts["cg_color"][f"g{(cctl >> 1) & 3}/m{cctl & 1}"] += 1
        if (paint >> 13) & 1 and (sfmt_word & 0xFF) not in INDEXED_FMTS:      # v1.6b Gaussian
            s16 = lambda v: v - 65536 if v & 0x8000 else v  # noqa: E731
            gw = st[STATE["GAUSS_W"].addr]
            w0, w1, w2 = s16(st[STATE["GAUSS_W0"].addr] & 0xFFFF), s16(gw & 0xFFFF), s16(gw >> 16)
            k = ("centre" if w1 == w2 == 0 else "nocentre" if w0 == 0 else "sharpen" if min(w1, w2) < 0 and
                 w0 + 4 * (w1 + w2) == 4096 else "unity" if w0 + 4 * (w1 + w2) == 4096 else "gain")
            self.counts["cg_gauss"][f"w{min((paint >> 8) & 7, 4)}/{k}"] += 1
        self.counts["cg_wrap"][f"w{min((paint >> 8) & 7, 4)}/{flt}/p{(paint >> 5) & 1}"] += 1
        if (paint >> 8) & 7 == 1:
            self.counts["cg_border"][f"solid{(paint >> 11) & 1}/{flt}/mul{(paint >> 2) & 1}"] += 1
        self.counts["cg_img14"][f"tr{(sfmt_word >> 10) & 1}/sub{(sfmt_word >> 9) & 1}/"
                                f"idx{int((sfmt_word & 0xFF) in INDEXED_FMTS)}"] += 1
        nkeys = sum((st[STATE["CKEY0_LO"].addr + 2 * k] >> 31) & 1 for k in range(4))
        kcls = CKEY_CLASSES[min(nkeys, 1) if nkeys < 2 else (2 if nkeys < 4 else 3)]
        self.counts["cg_blit_opts"][f"mul{(paint >> 2) & 1}/pre{(paint >> 3) & 1}/opq{(sfmt_word >> 8) & 1}/"
                                    f"keys{kcls}"] += 1
        if (sfmt_word & 0xFF) not in V1_FMTS:
            return
        self.counts["cg_blit"][f"{sfmt}/{flt}/{xform_class(st)}"] += 1
        if (st[STATE["TGT_FMT"].addr] & 0xFF) in V1_FMTS:
            self.counts["cg_blit_dst"][f"{sfmt}/{tfmt}"] += 1

    def sample_grad(self, st):
        paint = st[STATE["PAINT_CTRL"].addr]
        if paint & 3 == 3:
            centred = all(st[STATE[n].addr] == 0 for n in ("GRAD_P", "GRAD_Q", "GRAD_R", "GRAD_R_HI"))
            self.counts["cg_radial"][f"sp{(paint >> 6) & 3}/{'centred' if centred else 'focal'}"] += 1
            return
        if paint & 3 != 2:
            return
        ang, ln = grad_class(st)
        self.counts["cg_grad"][f"sp{(paint >> 6) & 3}/{ang}/{ln}"] += 1

    def sample_path(self, st, payload, edges):
        """One executed DRAW_PATH: payload words (bbox, edge address, count), edges as s.4 tuples."""
        paint = st[STATE["PAINT_CTRL"].addr] & 3
        rast = st[STATE["RAST_CTRL"].addr]
        mode = st[STATE["BLEND"].addr] & 0xF
        self.sample_target(st)
        self.sample_mask(st, "path")
        if paint not in (0, 1, 2, 3):
            return
        bx0, by0 = _s16(payload[0]), _s16(payload[0] >> 16)
        bx1, by1 = _s16(payload[1]), _s16(payload[1] >> 16)
        n = len(edges)
        if any(abs(c) >= GUARD for e in edges for c in e):
            size = "guard"
        else:
            ext = max(bx1 - bx0, by1 - by0)
            size = "subpixel" if ext <= 1 else "small" if ext <= 16 else "medium" if ext <= 128 else "large"
        ecls = "le8" if n <= 8 else "le64" if n <= 64 else "le512" if n <= 512 else "gt512"
        self.counts["cg_path"][f"eo{rast & 1}/aa{(rast >> 1) & 1}/{size}/{ecls}"] += 1
        self.counts["cg_paint_path"][f"{('solid', 'image', 'grad', 'radial')[paint & 3]}/{mode}"] += 1
        self.sample_grad(st)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({g: dict(c) for g, c in self.counts.items()}, sort_keys=True))


def merge(files) -> dict:
    total = {g: Counter() for g in bins()}
    for f in files:
        for g, c in json.loads(Path(f).read_text()).items():
            if g in total:
                total[g].update(c)
    return total


def report(total) -> tuple[str, bool]:
    lines = ["# Functional coverage", "",
             "Generated by scripts/coverage_report.py from build/coverage/*.json.", "",
             "| Group | Bins hit | Total | Coverage |", "|---|---|---|---|"]
    full = True
    holes = []
    for g, all_bins in bins().items():
        hit = sum(1 for b in all_bins if total[g][b] > 0)
        lines.append(f"| {g} | {hit} | {len(all_bins)} | {100.0 * hit / len(all_bins):.1f} % |")
        if hit < len(all_bins):
            full = False
            holes += [f"- {g}: {b}" for b in all_bins if total[g][b] == 0]
    if holes:
        lines += ["", "## Holes", ""] + holes
    return "\n".join(lines) + "\n", full
