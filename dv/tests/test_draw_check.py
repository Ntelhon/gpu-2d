# SPDX-License-Identifier: Apache-2.0
"""cocotb test of g2d_draw_check (rendering.md §13) against the golden model's draw validation.

Random state words (biased towards the rule boundaries: invalid formats, source-only formats as targets,
size 0/1/8192/8193, misaligned addresses and strides, stride = row bytes +-1 pixel (sub-byte sources: rows
of ceil(w * bpp / 8) bytes), extents ending exactly at the window end or wrapping past 2^32, WRAP 5..7,
CLUT misaligned or reaching past the window (indexed sources)), random windows, RECT and PATH draws. For each case the model executes LOAD_STATE of all 64
words plus an empty draw; its ERR_STATUS/ERR_ADDR is the expected result.

Every §13 rule is checked (the temporary hardware restriction of Phases 2-4 is gone).
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

import g2d_model as gm
from g2d_cmd import FMT, FMT_BITS, INDEXED_FMTS, REGMAP, STATE, TGT_FMTS, CmdBuf, row_bytes, state_word

SEED = int(os.environ.get("SEED", "1"))
N = int(os.environ.get("G2D_N", "3000"))
BAD_STATE, ADDR_RANGE = 1 << 1, 1 << 2
VALID = list(FMT.values())


def bpp_of(fmt):
    """Alignment unit in bytes (1 for formats of 8 bits or fewer; YUY2 4, v1.6b)."""
    if fmt in (FMT.get("YUY2"), FMT.get("AYUY2")):
        return 4
    return max(1, FMT_BITS.get(fmt, 32) // 8)


def valid_buf(rng, win, fmts):
    """A buffer that passes §13.1, placed inside the window most of the time."""
    fmt = rng.choice(fmts)
    bpp = bpp_of(fmt)
    w = rng.choice([1, 2, rng.randint(1, 300), rng.randint(1, 2048), 8192])
    h = rng.choice([1, rng.randint(1, 100), rng.randint(1, 2048), 8192])
    stride = min(row_bytes(fmt, w) + bpp * rng.choice([0, 0, 1, rng.randint(0, 64)]), 0xFFFF // bpp * bpp)
    ext = stride * (h - 1) + row_bytes(fmt, w)
    r = rng.random()
    span = win[1] - win[0] + 1
    if r < 0.6 and ext < span:
        addr = win[0] + rng.randrange(0, span - ext + 1)          # inside
    elif r < 0.75:
        addr = win[1] + 1 - ext + rng.choice([0, 0, bpp])          # ends at the window end, or one pixel past
    elif r < 0.85:
        addr = win[0] - rng.choice([0, bpp, 64])                   # starts at / before the window start
    else:
        addr = (1 << 32) - ext + rng.choice([0, bpp])              # around the 2^32 wrap
    addr = (addr - addr % bpp) & 0xFFFF_FFFF
    return [fmt, w, h, addr, stride]


def break_buf(rng, b):
    fmt, w, h, addr, stride = b
    k = rng.randrange(6)
    if k == 0:
        b[0] = rng.choice([0x44, 0x22, 0x32, 0x86, 0xFF, 0x0F, 0x17, 0x1B, 0x47, 0xD4, 0xB0])   # 0x83: YUY2 since v1.6b
    elif k == 1:
        b[1] = rng.choice([0, 8193])
    elif k == 2:
        b[2] = rng.choice([0, 8193])
    elif k == 3 and bpp_of(fmt) > 1:
        b[3] = (addr + rng.randint(1, bpp_of(fmt) - 1)) & 0xFFFF_FFFF
    elif k == 4 and bpp_of(fmt) > 1:
        b[4] = stride + 1
    elif fmt in FMT_BITS:
        b[4] = max(0, row_bytes(fmt, w) - bpp_of(fmt))              # stride one unit too small
    else:
        b[4] = 0                     # a second fault on a buffer whose format an earlier fault made invalid


def put_buf(st, prefix, b):
    fmt, w, h, addr, stride = b
    st[STATE[prefix + "_ADDR"].addr] = addr
    st[STATE[prefix + "_STRIDE"].addr] = stride & 0xFFFF
    st[STATE[prefix + "_SIZE"].addr] = state_word(prefix + "_SIZE", W=w & 0x3FFF, H=h & 0x3FFF)
    st[STATE[prefix + "_FMT"].addr] = fmt


def rand_case(rng):
    lo = rng.getrandbits(26) << 6
    hi = min(0xFFFF_FFFF, lo + rng.choice([0x10_0000, 0x1000_0000, 1 << 32]) - 1) | 0x3F
    if rng.random() < 0.2:
        lo, hi = 0, 0xFFFF_FFFF
    win = (lo, hi)
    st = [rng.getrandbits(32) for _ in range(REGMAP.state_size)]
    tgt, srcb = valid_buf(rng, win, TGT_FMTS), valid_buf(rng, win, VALID)
    if rng.random() < 0.05:
        tgt[0] = rng.choice([FMT["A4"], FMT["I8"], FMT["L4"], FMT["I1"]])   # source-only code as the target
    sw, sh = srcb[1], srcb[2]
    x0, y0 = rng.randrange(sw), rng.randrange(sh)
    x1, y1 = rng.randint(x0 + 1, sw), rng.randint(y0 + 1, sh)
    src = rng.choice([0, 0, 1, 1, 2, 3])            # v1.5: 3 = RADIAL_GRAD
    spread = rng.choice([0, 1, 2, 3])
    wrap = rng.choice([0, 0, 1, 2, 3, 4])
    clut = rng.choice([win[0], win[1] - 1023, win[1] - 1020, rng.randrange(win[0], max(win[0] + 1, win[1] - 1023))])
    clut = (clut & ~3) & 0xFFFF_FFFF
    amode = [rng.choice([0, 1, 2]), rng.choice([0, 1, 2])]
    blend = 0 if rng.random() < 0.5 else rng.randint(1, 15)
    ramp = rng.choice([win[0], win[1] - 1023, rng.randrange(win[0], max(win[0] + 1, win[1] - 1023))])
    ramp = (ramp & ~3) & 0xFFFF_FFFF
    is_path = rng.random() < 0.35
    count = rng.choice([0, 1, rng.randint(1, 512), rng.randint(1, 1 << 16)])
    eaddr = rng.choice([win[0], max(win[0], win[1] + 1 - 16 * count), rng.randrange(win[0], win[1])]) & ~0xF
    # faults: none (about half the cases), or one or two specific rule violations
    for _ in range(rng.choice([0, 0, 0, 1, 1, 2])):
        f = rng.randrange(12)
        if f == 0:
            break_buf(rng, tgt)
        elif f == 1:
            break_buf(rng, srcb)
        elif f == 2:
            amode[rng.randrange(2)] = 3
        elif f == 3:                 # v1.5: SRC 3 is RADIAL_GRAD (legal); its ramp misaligned
            src = 3
            ramp |= rng.randint(1, 3)
        elif f == 4:
            wrap = rng.choice([5, 6, 7])
        elif f == 5:
            ramp |= rng.randint(1, 3)
        elif f == 6:
            eaddr |= rng.randint(1, 15)
        elif f == 7:
            x1, y1 = rng.choice([(x0, y1), (x1, y0), (sw + 1, y1), (x1, sh + 1)])
        elif f == 9:
            clut |= rng.randint(1, 3)
        elif f == 10:
            srcb[0] = rng.choice(INDEXED_FMTS)
            clut = rng.choice([win[1] - 1022, (1 << 32) - 1020, win[0] - 4]) & 0xFFFF_FFFC
        else:
            eaddr = rng.getrandbits(32) & ~0xF
    put_buf(st, "TGT", tgt)
    put_buf(st, "SRC", srcb)
    st[STATE["SRC_FMT"].addr] |= rng.getrandbits(3) << 8           # FORCE_OPAQUE, SUB_ORDER, TRANSPARENT
    st[STATE["TGT_FMT"].addr] |= rng.getrandbits(1) << 8           # DITHER
    st[STATE["CLUT_ADDR"].addr] = clut
    st[STATE["SRC_RECT_MIN"].addr] = state_word("SRC_RECT_MIN", X0=x0, Y0=y0)
    st[STATE["SRC_RECT_MAX"].addr] = state_word("SRC_RECT_MAX", X1=x1 & 0x3FFF, Y1=y1 & 0x3FFF)
    st[STATE["PAINT_CTRL"].addr] = src | (spread << 6) | (rng.getrandbits(4) << 2) | (wrap << 8) | \
        (int(rng.random() < 0.15) << 12) | \
        (int(rng.random() < 0.2) << 13)                             # v1.6b GAUSS (W1 + W2 <= 16384 below)
    if rng.random() < 0.6:      # v1.6b YUV planes: addresses at / beyond the window edges, odd / short strides
        st[STATE["SRC_PLANE1_ADDR"].addr] = rng.choice([win[0], rng.randrange(win[0], win[1]), win[1] - rng.randint(0, 64),
                                                         rng.getrandbits(32)]) & 0xFFFF_FFFF
        st[STATE["SRC_PLANE2_ADDR"].addr] = rng.choice([win[0], rng.randrange(win[0], win[1]), win[1] - rng.randint(0, 64),
                                                         rng.getrandbits(32)]) & 0xFFFF_FFFF
        st[STATE["SRC_PLANE_STRIDE"].addr] = rng.choice([0, rng.randint(1, 64), rng.randint(0, 9000)]) | \
            (rng.choice([0, rng.randint(1, 64), rng.randint(0, 9000)]) << 16)
        st[STATE["SRC_FMT"].addr] |= rng.getrandbits(2) << 11            # YUV_709, UV_SWAP
        st[STATE["SRC_FMT"].addr] |= int(rng.random() < 0.1) << 13       # YUV_FULL (reserved: BAD_STATE)
    st[STATE["COLOR_CTRL"].addr] = rng.getrandbits(3) if rng.random() < 0.3 else 0    # v1.6b: GAMMA 3 is BAD_STATE
    if rng.random() < 0.5:
        w1 = rng.choice([0, 16384, rng.randrange(16385), rng.getrandbits(16)])
        st[STATE["GAUSS_W"].addr] = w1 | (rng.choice([0, 16384 - w1, 16385 - w1, rng.getrandbits(16)]) & 0xFFFF) << 16
        st[STATE["GAUSS_W0"].addr] = rng.getrandbits(16)          # any weights are legal since 2026-10-26
    st[STATE["ALPHA_CTRL"].addr] = amode[0] | (amode[1] << 2) | 0xFFFF00
    st[STATE["BLEND"].addr] = blend
    if rng.random() < 0.3:      # v1.6 blend families (rendering.md §5.7): valid and out-of-table codes
        fam = rng.randint(1, 3)
        st[STATE["BLEND"].addr] = rng.randint(0, 15) | (fam << 4)
        st[STATE["BLEND_FUNC"].addr] = rng.getrandbits(29) if rng.random() < 0.5 else \
            (rng.randint(0, 8) | (rng.randint(0, 16) << 4) | (rng.randint(0, 8) << 9) | (rng.randint(0, 16) << 13) |
             (rng.randint(0, 4) << 18) | (rng.choice([0, 5, 10, 15]) << 21) | (rng.choice([0, 9, 15]) << 25))
    st[STATE["GRAD_RAMP_ADDR"].addr] = ramp
    # v1.6 mask layer (rendering.md §6.1, §13): valid sizes and strides mostly, invalid ones and extents at / beyond
    # the window edges sometimes
    if rng.random() < 0.35:
        mw = rng.choice([1, rng.randint(1, 8192), 8192, 0, 8193 & 0x3FFF])
        mh = rng.choice([1, rng.randint(1, 8192), 8192, 0])
        mstride = rng.choice([mw, mw + rng.randint(0, 64), max(0, mw - 1)]) & 0xFFFF
        maddr = rng.choice([win[0], max(win[0], win[1] - mstride * max(mh - 1, 0) - mw + 1), rng.randrange(win[0], win[1]),
                            win[1] - rng.randint(0, 64), rng.getrandbits(32)]) & 0xFFFF_FFFF
        st[STATE["MASK_ADDR"].addr] = maddr
        st[STATE["MASK_STRIDE"].addr] = mstride
        st[STATE["MASK_SIZE"].addr] = state_word("MASK_SIZE", W=mw & 0x3FFF, H=mh & 0x3FFF)
        st[STATE["MASK_CTRL"].addr] = 1
    return win, st, is_path, eaddr & 0xFFFF_FFFF, count


def model_expect(win, st, is_path, eaddr, count):
    mem = gm.Memory()
    gpu = gm.Gpu(mem)
    gpu.set_addr_win(*win)
    cb = CmdBuf()
    for i in range(0, REGMAP.state_size, 32):
        cb.load_state(i, st[i:i + 32])
    if is_path:
        cb.draw_path((0, 0, 0, 0), eaddr, count)
    else:
        cb.draw_rect(0, 0, 0, 0)
    data = cb.to_bytes()
    cmd = gpu.addr_win_lo
    mem.write(cmd, data)
    gpu.kick(cmd, len(data))
    state = [gpu.state(i) for i in range(REGMAP.state_size)]
    err = gpu.err_status
    assert err & ~(BAD_STATE | ADDR_RANGE) == 0, f"unexpected model error {err:#x}"
    exp_bad = bool(err & BAD_STATE)
    exp_range = bool(err & ADDR_RANGE)
    exp_addr = gpu.err_addr if err else 0
    return state, gpu.addr_win_lo, gpu.addr_win_hi, exp_bad, exp_range, exp_addr


@cocotb.test()
async def draw_check_vs_model(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value = 0
    dut.start.value = 0
    await Timer(25, unit="ns")
    dut.rst_n.value = 1
    rng = random.Random(SEED)
    hits = {"ok": 0, "bad": 0, "range": 0}
    for n in range(N):
        win, st, is_path, eaddr, count = rand_case(rng)
        state, lo, hi, eb, er, ea = model_expect(win, st, is_path, eaddr, count)
        dut.state.value = sum(v << (32 * i) for i, v in enumerate(state))
        dut.is_path.value = int(is_path)
        dut.edge_addr.value = eaddr
        dut.edge_count.value = count
        dut.win_lo.value = lo
        dut.win_hi.value = hi
        dut.start.value = 1
        await RisingEdge(dut.clk)
        dut.start.value = 0
        await RisingEdge(dut.clk)
        await RisingEdge(dut.clk)
        assert int(dut.done.value) == 1
        got = (bool(int(dut.bad_state.value)), bool(int(dut.addr_range.value)), int(dut.err_addr.value))
        if eb or er:
            assert got == (eb, er, ea), f"case {n}: got {got}, expected {(eb, er, hex(ea))} path={is_path}"
        else:
            assert got[:2] == (False, False), f"case {n}: got {got}, expected no error (path={is_path})"
        hits["bad" if eb else "range" if er else "ok"] += 1
    dut._log.info(f"draw_check: {hits}")
    for k, v in hits.items():
        assert v > N // 20, f"poor stimulus: {hits}"


# ------------------------------------------------------------------------------------------------

import pytest  # noqa: E402


def test_draw_check():
    import runner
    runner.run("g2d_draw_check_tb", "test_draw_check",
               extra_env={"G2D_N": "3000" if os.environ.get("G2D_FULL") != "1" else "20000"})
