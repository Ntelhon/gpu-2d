# SPDX-License-Identifier: Apache-2.0
"""End-to-end (L3) tests of g2d_top against the golden model (verification-plan.md P2 tests, P3 updates).

Every test compares the whole memory image, the error/fence registers, the white-box state registers and
the performance counters with the golden model (dv/common/g2d_env.py).

  FIL-01..04  fills in all 12 formats, shapes, scissor, BAD_STATE / ADDR_RANGE causes
  CMD-01..04  command sequences, kick queue, malformed commands, fence ordering
  CSR-01/05/06 through the top: reset values, IRQ sources, soft reset idle and mid-draw
  AXI-04 at system level: write error on a target, read error on a command buffer
  random streams (all of the above mixed, plus blended solid fills on the general pipeline, random AXI
  profiles), fill throughput (informal, A6)
"""

import os
import random

import cocotb
from cocotb.triggers import RisingEdge, Timer

from axi_mem import ErrRange, Profile
from csr_if import CsrIf
from g2d_cmd import FMT, FMT_BYTES, STATE, CmdBuf, state_word
from g2d_env import ERR, top_of

from g2d_top_util import (ADDR_W, BASE_HI, DATA_W, FMTS, KICK_DEPTH, MAX_BURST, QUICK, SEED, fill, make_env,
                          rand_alpha, rand_rect, run)



# ------------------------------------------------------------------------------------------------
# FIL
# ------------------------------------------------------------------------------------------------

@cocotb.test()
async def fil01_all_formats(dut):
    env = await make_env(dut, "fil01")
    rng = env.rng
    for rep in range(1 if QUICK else 3):
        for fmt in FMTS:
            t = env.new_target(fmt, rng.randint(1, 200), rng.randint(1, 24))
            cb = CmdBuf()
            for _ in range(4):
                fill(cb, t, rand_rect(rng, t), rng.getrandbits(32), rand_alpha(rng))
            cb.fence(rep * 100 + fmt)
            await env.kick(cb)
        await env.wait_done()
        await env.check()


@cocotb.test()
async def fil02_shapes(dut):
    env = await make_env(dut, "fil02")
    rng = env.rng
    for fmt in FMTS:
        t = env.new_target(fmt, rng.randint(16, 96), rng.randint(8, 32))
        w, h = t.w, t.h
        cb = CmdBuf()
        shapes = [(5, 5, 5, 9), (5, 5, 9, 5), (9, 9, 5, 5),          # zero-size and negative-size
                  (w, 0, w + 10, h), (-20, -20, 0, 0), (0, h, w, h + 3),  # fully clipped by the target
                  (3, 4, 4, 5), (0, 2, w, 3), (2, 0, 3, h),           # 1x1, 1xN, Nx1
                  (0, 0, w, h), (-32768, -32768, 32767, 32767)]       # full target, extreme coordinates
        for r in shapes:
            fill(cb, t, r, rng.getrandbits(32))
        cb.fence(fmt)
        await run(env, cb)
    # largest sizes: 8192-wide slices and an 8192-tall column, 32 and 16 bpp
    for fmt, w, h in [(FMT["ARGB8888"], 8192, 2), (FMT["RGB565"], 8192, 3), (FMT["A8"], 1, 8192 if not QUICK else 600)]:
        t = env.new_target(fmt, w, h, pad_max=4)
        await run(env, fill(CmdBuf(), t, (0, 0, w, h), rng.getrandbits(32)).fence(1))
    t = env.new_target(FMT["XRGB8888"], 8192, 8192, stride=8192 * 4, randomize=False)  # 256 MB extent
    cb = fill(CmdBuf(), t, (8000, 8180, 8192, 8192), 0x12345678).fence(2)
    await run(env, cb)


@cocotb.test()
async def fil03_scissor(dut):
    env = await make_env(dut, "fil03")
    rng = env.rng
    for i in range(12 if QUICK else 40):
        fmt = rng.choice(FMTS)
        t = env.new_target(fmt, rng.randint(1, 150), rng.randint(1, 30))
        cb = CmdBuf()
        for _ in range(3):
            cx0, cy0 = rng.randint(0, t.w + 4), rng.randint(0, t.h + 4)
            clip = (cx0, cy0, rng.randint(0, t.w + 40), rng.randint(0, t.h + 40))  # may be empty or past the target
            r = rand_rect(rng, t, margin=60)
            if rng.random() < 0.2:
                r = (rng.randint(-32768, 0), rng.randint(-32768, 0), rng.randint(0, 32767), rng.randint(0, 32767))
            fill(cb, t, r, rng.getrandbits(32), rand_alpha(rng), clip=clip)
        cb.fence(i)
        await env.kick(cb)
        if i % 8 == 7:
            await env.wait_done()
            await env.check()
    await env.wait_done()
    await env.check()


def bad_state_cases(env, rng):
    """(name, CmdBuf builder) for each §13.1/§13.2 cause."""
    a = 0x2000_0000
    cases = []

    def draw(fmt=FMT["ARGB8888"], w=16, h=4, addr=a, stride=None, alpha=0, paint=0, blend=0, path=False,
             eaddr=0x3000_0000, ecount=0, ramp=0x3000_0000):
        def build(cb):
            s = stride if stride is not None else w * FMT_BYTES.get(fmt, 4)
            cb.load_state(STATE["TGT_ADDR"].addr, [addr, s, state_word("TGT_SIZE", W=w, H=h), fmt])
            cb.clip(0, 0, 8192, 8192)
            cb.load_state(STATE["BLEND"].addr, [blend, alpha | 0xFFFF00, paint, 0xFF00FF00])
            cb.load_state(STATE["GRAD_RAMP_ADDR"].addr, [ramp])
            if path:
                cb.draw_path((0, 0, 4, 4), eaddr, ecount)
            else:
                cb.draw_rect(0, 0, 4, 4)
        return build

    cases += [("bad_fmt_44", draw(fmt=0x44)), ("bad_fmt_22", draw(fmt=0x22)), ("bad_fmt_ff", draw(fmt=0xFF)),
              ("tgt_src_only_a4", draw(fmt=FMT["A4"])), ("tgt_src_only_i8", draw(fmt=FMT["I8"]))]
    cases += [("addr_misaligned_32", draw(addr=a + 2)), ("addr_misaligned_16", draw(fmt=FMT["RGB565"], addr=a + 1))]
    cases += [("stride_not_multiple", draw(stride=16 * 4 + 2)), ("stride_too_small", draw(stride=15 * 4)),
              ("stride_16bpp_odd", draw(fmt=FMT["BGR565"], stride=33))]
    cases += [("w0", draw(w=0)), ("h0", draw(h=0)), ("w8193", draw(w=8193 & 0x3FFF)),
              ("h8193", draw(h=8193 & 0x3FFF))]
    cases += [("alpha_src_3", draw(alpha=3)), ("alpha_dst_3", draw(alpha=3 << 2)),
              ("radial_ramp_misaligned", draw(paint=3, ramp=0x3000_0002)),           # v1.5: SRC 3 = RADIAL_GRAD
              ("radial_ramp_range", draw(paint=3, ramp=0x7FFF_FF00))]
    cases += [("image_no_source", draw(paint=1)),     # source state at reset values: invalid source
              ("grad_ramp_misaligned", draw(paint=2, ramp=0x3000_0002)),
              ("grad_ramp_range", draw(paint=2, ramp=0x7FFF_FF00)),
              ("path_edge_misaligned", draw(path=True, eaddr=0x3000_0008, ecount=4)),
              ("path_image_no_source", draw(path=True, paint=1, eaddr=0x3000_0000, ecount=4)),   # v1.5: legal paint,
              # but the source state is at its reset values (invalid source), as image_no_source
              ("path_edges_range", draw(path=True, eaddr=0x7FFF_FFF0, ecount=4))]
    cases += [("range_window", draw(addr=0x7FFF_FFC0, w=64, h=2)), ("range_wrap", draw(addr=0xFFFF_FF00, w=128, h=2))]
    return cases


@cocotb.test()
async def fil04_bad_state(dut):
    env = await make_env(dut, "fil04")
    rng = env.rng
    await env.set_window(0x0000_0000, 0x7FFF_FFFF)
    for name, build in bad_state_cases(env, rng):
        t = env.new_target(FMT["ARGB8888"], 20, 6, addr=0x1000_0000 + 0x10000 * len(env.targets))
        cb = CmdBuf()
        fill(cb, t, (0, 0, 10, 3), rng.getrandbits(32))       # executed
        build(cb)                                            # fails, writes nothing
        fill(cb, t, (0, 3, 20, 6), rng.getrandbits(32))       # never executed
        cb.fence(0x55)
        await run(env, cb)
        st = await env.csr.read("STATUS")
        assert CsrIf.field("STATUS", "HALTED", st) == 1, name
        await env.soft_reset()
        await env.check()


@cocotb.test()
async def cov_fill_matrix(dut):
    """cg_fill and cg_align closure: every format x rect class x scissor class (verification-plan.md §4),
    and for 1/2/4 bytes per pixel every (address mod 64) x (stride mod 64) class."""
    env = await make_env(dut, "cov_fill")
    rng = env.rng
    w, h = 24, 8
    rects = {"empty": (5, 2, 5, 6), "clipped": (-3, 1, 10, h + 4), "full": (0, 0, w, h), "1px": (3, 3, 4, 4),
             "thin": (2, 1, 20, 2), "normal": (2, 1, 20, 6)}
    clips = {"off": (0, 0, 8192, 8192), "on-partial": (4, 1, 18, 7), "on-empty": (w, 0, w + 5, h)}
    for fmt in FMTS:
        t = env.new_target(fmt, w, h)
        cb = CmdBuf()
        for rect in rects.values():
            for clip in clips.values():
                fill(cb, t, rect, rng.getrandbits(32), clip=clip)
        await run(env, cb.fence(1))
    mod64 = [0, 1, 16, 56]                # one value per class 0 / 1-7 / 8-55 / 56-63
    for fmt in (FMT["A8"], FMT["RGB565"], FMT["ARGB8888"]):
        bpp = FMT_BYTES[fmt]
        cb = CmdBuf()
        for i, a in enumerate(mod64):
            for j, s in enumerate(mod64):
                a_off = max(bpp, a - a % bpp) if a else 0          # aligned to the pixel size, same class
                s_off = max(bpp, s - s % bpp) if s else 0
                t = env.new_target(fmt, 10, 2, addr=0x1C00_0000 + 0x10000 * (4 * i + j) + 0x100000 * bpp + a_off,
                                   stride=128 + s_off)
                fill(cb, t, (0, 0, 10, 2), rng.getrandbits(32))
        await run(env, cb.fence(2))


# ------------------------------------------------------------------------------------------------
# CMD
# ------------------------------------------------------------------------------------------------

@cocotb.test()
async def cmd01_sequences(dut):
    """NOP (with garbage payload), LOAD_STATE of every index with garbage in reserved bits/indices,
    fences and fills, in random order. The white-box state compare checks every LOAD_STATE."""
    env = await make_env(dut, "cmd01")
    rng = env.rng
    t = env.new_target(FMT["BGRA8888"], 64, 16)
    for i in range(10 if QUICK else 30):
        cb = CmdBuf()
        for _ in range(rng.randint(1, 12)):
            k = rng.random()
            if k < 0.25:
                cb.nop(rng.randint(0, 5), fill=rng.getrandbits(32))
            elif k < 0.6:
                idx = rng.randrange(64)
                cnt = rng.randint(1, 64 - idx)
                vals = [rng.getrandbits(32) for _ in range(cnt)]
                # keep the paint SOLID/blend NONE and the target valid so later fills execute
                for j in range(cnt):
                    if idx + j in (STATE["BLEND"].addr, STATE["PAINT_CTRL"].addr):
                        vals[j] = 0
                cb.load_state(idx, vals)
            elif k < 0.75:
                cb.fence(rng.getrandbits(32), irq=rng.random() < 0.5)
            else:
                fill(cb, t, rand_rect(rng, t), rng.getrandbits(32), rand_alpha(rng))
        await run(env, cb)
        if env.model.halted:
            await env.soft_reset()
            await env.check()


@cocotb.test()
async def cmd02_kick_queue(dut):
    env = await make_env(dut, "cmd02")
    rng = env.rng
    # (a) overflow: a long first buffer keeps the queue busy
    big = env.new_target(FMT["ARGB8888"], 256, 64 if not QUICK else 32)
    await env.kick(fill(CmdBuf(), big, (0, 0, big.w, big.h), 0x11223344).fence(1))
    small = env.new_target(FMT["RGB565"], 32, 8)
    # fill the queue (the fetch may already hold the next buffer while the first one runs)
    n = 0
    while CsrIf.field("STATUS", "KICK_FREE", await env.csr.read("STATUS")):
        await env.kick(fill(CmdBuf(), small, (n % 28, n % 6, n % 28 + 4, n % 6 + 2), rng.getrandbits(32)).fence(10 + n),
                       wait_free=False)
        n += 1
    assert KICK_DEPTH <= n <= KICK_DEPTH + 1, n
    await env.kick(fill(CmdBuf(), small, (0, 0, 32, 8), 0xDEADBEEF).fence(99), to_model=False,
                   wait_free=False)  # dropped
    assert await env.csr.read("ERR_STATUS") == ERR["KICK_OVF"]
    assert CsrIf.field("STATUS", "HALTED", await env.csr.read("STATUS")) == 0
    await env.wait_done()
    await env.check()
    assert await env.csr.read("FENCE_SEQ") == 10 + n - 1
    await env.csr.write("ERR_STATUS", ERR["KICK_OVF"])
    # (b) buffer sizes 8 B .. 64 KB (NOP payload of random words, then a fence)
    for size in [8, 16, 24, 64, 256, 264, 1000 * 8, 65536]:
        cb = CmdBuf()
        words = size // 4
        if words >= 4:
            cb.nop(words - 3, fill=rng.getrandbits(32))
            cb.fence(size)
        elif words == 2:
            cb.nop(1, fill=0xFFFFFFFF)
        else:
            cb.fence(size)
        data = cb.to_bytes()
        assert len(data) == size, (size, len(data))
        await env.kick(data)
    await env.wait_done()
    await env.check()
    assert await env.csr.read("FENCE_SEQ") == 65536


def malformed_cases():
    """(name, words) commands that must raise CMD_DECODE (spec/commands.md)."""
    c = []
    c.append(("bad_opcode_02", [0x0200_0000]))
    c.append(("bad_opcode_ff", [0xFF00_0000]))
    c.append(("nop_reserved", [0x0001_0000]))
    c.append(("load_reserved", [0x0101_0100 | 0, 0]))
    c.append(("load_count0", [0x0100_0005]))
    c.append(("load_overflow", [0x0102_003F, 1, 2]))
    c.append(("rect_reserved", [0x1000_0001, 0, 0]))
    c.append(("path_reserved", [0x1180_0000, 0, 0, 0, 0]))
    c.append(("path_count_reserved", [0x1100_0000, 0, 0x0004_0004, 0x3000_0000, 0x0100_0000]))
    c.append(("fence_reserved", [0x2000_0002, 0]))
    return c


@cocotb.test()
async def cmd03_malformed(dut):
    env = await make_env(dut, "cmd03")
    rng = env.rng
    t = env.new_target(FMT["ARGB8888"], 32, 8)

    async def case(words_mid, tail_cross=None):
        cb = CmdBuf()
        fill(cb, t, (0, 0, 8, 8), rng.getrandbits(32))
        cb.words += words_mid
        if tail_cross is None:
            fill(cb, t, (8, 0, 32, 8), rng.getrandbits(32))
            cb.fence(7)
        data = cb.to_bytes() if tail_cross is None else None
        if tail_cross is not None:
            cb.words += tail_cross          # a command whose payload runs past the buffer end
            if len(cb.words) % 2:
                cb.words.insert(len(cb.words) - len(tail_cross), 0)   # NOP before it, keep 8-byte size
            data = b"".join(w.to_bytes(4, "little") for w in cb.words)
        await env.kick(data)
        await env.wait_done()
        await env.check()
        await env.soft_reset()
        await env.check()

    for name, words in malformed_cases():
        await case(words)
    # payload crossing the buffer end, for every command type
    for hdr in [0x0000_0003, 0x0103_0010, 0x1000_0000, 0x1100_0000, 0x2000_0001]:
        await case([], tail_cross=[hdr])
    # kick checks: size 0, size not a multiple of 8, extent outside the window, wrap past 2^32
    for size in (0, 12, 4):
        await env.kick(CmdBuf().fence(1).to_bytes(), size=size)
        await env.wait_done()
        await env.check()
        await env.soft_reset()
    await env.set_window(0x0000_0000, 0x00FF_FFFF)
    await env.kick(CmdBuf().fence(1).to_bytes(), addr=0x0100_0000 - 8, size=16)   # crosses the window end
    await env.wait_done()
    await env.check()
    await env.soft_reset()
    await env.set_window(0, 0xFFFF_FFFF)
    await env.kick(CmdBuf().fence(1).nop().to_bytes(), addr=0xFFFF_FFF8, size=16)    # wraps
    await env.wait_done()
    await env.check()
    await env.soft_reset()
    await env.check()


@cocotb.test()
async def cmd05_bad_kick_order(dut):
    """A rejected kick (CMD_DECODE / ADDR_RANGE from the kick checks) is reported after every command of the
    buffers kicked before it, also when the last command of the previous buffer is a draw still waiting
    for the engine when the fetch moves on (Phase 5 finding B-5.3, SYS-04 seed 15)."""
    env = await make_env(dut, "cmd05", profile=Profile(r_lat=(20, 40), b_lat=(20, 40)))
    rng = env.rng
    bad = [dict(size=0), dict(size=12), dict(addr=0xFFFF_FFF8, size=16)]
    for k in bad * (1 if QUICK else 3):
        t = env.new_target(FMT["ARGB8888"], 64, 16)
        cb = CmdBuf()
        # two general-pipeline draws: the second waits for the engine, so the parser is still busy with
        # it when its last word has been fetched
        fill(cb, t, (0, 0, 64, 16), rng.getrandbits(32), blend=1)
        fill(cb, t, (rng.randint(0, 8), 0, 64, 16), rng.getrandbits(32), blend=rng.randint(1, 15))
        await env.kick(cb)
        await env.kick(CmdBuf().fence(1).nop().to_bytes(), **k)
        await env.wait_done()
        await env.check()
        await env.soft_reset()
        await env.check()
    env.perf_checked = False


@cocotb.test()
async def cmd04_fence_ordering(dut):
    """FENCE_SEQ only after every earlier write is acknowledged: B responses delayed by 100-200 clk."""
    prof = Profile(b_lat=(100, 200), p_wready=0.7)
    env = await make_env(dut, "cmd04", profile=prof)
    rng = env.rng
    fence_sig = top_of(dut).u_core.fence
    events = []

    async def watch():
        while True:
            await RisingEdge(dut.clk)
            if int(fence_sig.value):
                events.append((env.axi.wr_outstanding(), len(env.axi.b_pending)))

    cocotb.start_soon(watch())
    ts = [env.new_target(rng.choice(FMTS), rng.randint(20, 120), rng.randint(4, 20)) for _ in range(4)]
    cb = CmdBuf()
    for i, t in enumerate(ts * 2):
        fill(cb, t, rand_rect(rng, t), rng.getrandbits(32))
        cb.fence(100 + i, irq=True)
    await run(env, cb)
    assert len(events) == 8
    assert all(e == (0, 0) for e in events), f"fence completed with writes outstanding: {events}"


# ------------------------------------------------------------------------------------------------
# CSR through the top
# ------------------------------------------------------------------------------------------------

IRQ_PULSE = os.environ.get("G2D_P_IRQ_PULSE", "0") == "1"
IRQ_LOW = int(os.environ.get("G2D_P_IRQ_ACTIVE_LOW", "0"))


def irq_level(dut):
    """irq_o as active-high (IRQ_ACTIVE_LOW undone); in IRQ_PULSE builds the pulse."""
    return int(dut.irq.value) ^ IRQ_LOW


@cocotb.test()
async def csr01_top_reset_values(dut):
    env = await make_env(dut, "csr01")
    hw = await env.csr.read("HWCFG")
    assert CsrIf.field("HWCFG", "DATA_BYTES", hw) == DATA_W // 8
    assert CsrIf.field("HWCFG", "ADDR_W", hw) == ADDR_W
    assert CsrIf.field("HWCFG", "KICK_DEPTH", hw) == KICK_DEPTH
    for f in ("CFG_ASYNC", "MEM_ASYNC"):
        assert CsrIf.field("HWCFG", f, hw) == int(os.environ.get(f"G2D_P_{f}", "0")), f
    assert await env.csr.read("ID") == 0x47324420
    st = await env.csr.read("STATUS")
    assert CsrIf.field("STATUS", "IDLE", st) == 1 and CsrIf.field("STATUS", "KICK_FREE", st) == KICK_DEPTH
    assert irq_level(dut) == 0
    await env.check()


@cocotb.test(skip=IRQ_PULSE)
async def csr05_top_irq(dut):
    env = await make_env(dut, "csr05")
    t = env.new_target(FMT["ARGB8888"], 16, 4)
    await env.csr.write("IRQ_ENABLE", 0b111)
    await env.csr.write("IRQ_STATUS", 0b111)
    # FENCE with IRQ
    await run(env, fill(CmdBuf(), t, (0, 0, 4, 4), 1).fence(5, irq=True))
    assert irq_level(dut) == 1
    st = await env.csr.read("IRQ_STATUS")
    assert st == 0b101, hex(st)            # FENCE and IDLE
    await env.csr.write("IRQ_STATUS", 0b101)
    env.model.clear_irq_fence()
    await env.cycles(3)
    assert irq_level(dut) == 0
    # FENCE without IRQ: only IDLE
    await run(env, CmdBuf().fence(6))
    assert await env.csr.read("IRQ_STATUS") == 0b100
    await env.csr.write("IRQ_STATUS", 0b100)
    # ERROR with only the ERROR enable
    await env.csr.write("IRQ_ENABLE", 0b010)
    await run(env, CmdBuf().load_state(STATE["TGT_FMT"].addr, [0x99]).draw_rect(0, 0, 1, 1))
    await env.cycles(3)
    assert irq_level(dut) == 1
    assert CsrIf.field("IRQ_STATUS", "ERROR", await env.csr.read("IRQ_STATUS")) == 1
    await env.csr.write("IRQ_STATUS", 0b111)
    await env.cycles(3)
    assert irq_level(dut) == 0
    await env.soft_reset()
    await env.check()


@cocotb.test(skip=not IRQ_PULSE)
async def csr09_irq_pulse(dut):
    """v1.9 IRQ_PULSE (and IRQ_ACTIVE_LOW): one-cycle pulses only when an enabled IRQ_STATUS bit becomes pending,
    none while the pending bits stay set, a new one after a bit is cleared and set again."""
    env = await make_env(dut, "csr09")
    t = env.new_target(FMT["ARGB8888"], 16, 4)
    pulses = []

    async def mon():
        prev = 0
        while True:
            await RisingEdge(dut.clk)
            v = irq_level(dut)
            if v and not prev:
                pulses.append(env.cycle if hasattr(env, "cycle") else 0)
            if v and prev:
                raise AssertionError("irq pulse longer than one cycle")
            prev = v
    cocotb.start_soon(mon())
    await env.csr.write("IRQ_STATUS", 0b111)
    await env.csr.write("IRQ_ENABLE", 0b111)
    await env.cycles(5)
    pulses.clear()
    await run(env, fill(CmdBuf(), t, (0, 0, 4, 4), 1).fence(5, irq=True))
    await env.cycles(5)
    assert len(pulses) >= 1, pulses                  # FENCE (and IDLE) became pending
    pulses.clear()
    await run(env, CmdBuf().fence(6, irq=True))      # FENCE and IDLE still pending: nothing new
    await env.cycles(5)
    assert len(pulses) == 0, pulses
    await env.csr.write("IRQ_STATUS", 0b001)         # clear FENCE only (IDLE stays pending)
    env.model.clear_irq_fence()
    await env.cycles(5)
    assert len(pulses) == 0, pulses                  # clearing gives no pulse
    await run(env, CmdBuf().fence(7, irq=True))
    await env.cycles(5)
    assert len(pulses) == 1, pulses                  # FENCE pending again: exactly one pulse
    await env.csr.write("IRQ_STATUS", 0b111)
    env.model.clear_irq_fence()
    await env.check()


@cocotb.test(skip=os.environ.get("G2D_ASYNC") == "1")
async def dft01_test_mode_reset(dut):
    """v1.9 test_mode_i: 1 = the core reset follows rst_ni directly (scan), 0 = released RST_STAGES edges later.
    Single-clock builds only: a hard reset in the middle of a run of an async build sets the crossing pointers to 0
    in one step, which the CDC checker's one-bit-per-step rule (dv/sva/g2d_cdc_sva.sv) reports by design."""
    env = await make_env(dut, "dft01")
    core_rst = top_of(dut).rst_core_n
    stages = int(os.environ.get("G2D_P_RST_STAGES", "2"))
    resets = ("rst_n", "rst_cfg_n", "rst_mem_n")   # asserted together (arch.md §2.2: the crossings need it)
    for tm in (1, 0):
        dut.test_mode.value = tm
        await RisingEdge(dut.clk)
        for r in resets:
            getattr(dut, r).value = 0
        await Timer(1, unit="ns")
        assert int(core_rst.value) == 0
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        for r in resets:
            getattr(dut, r).value = 1
        await Timer(1, unit="ns")
        if tm or stages == 0:
            assert int(core_rst.value) == 1, "test_mode: reset not passed through"
        else:
            assert int(core_rst.value) == 0
            for _ in range(stages):
                await RisingEdge(dut.clk)
            await Timer(1, unit="ns")
            assert int(core_rst.value) == 1
    dut.test_mode.value = 0
    del env


@cocotb.test()
async def csr06_soft_reset(dut):
    rng = random.Random(SEED + 66)
    env = await make_env(dut, "csr06", profile=Profile.random(rng, max_lat=40))
    # idle soft reset keeps the configuration
    await env.csr.write("IRQ_ENABLE", 0b101)
    await env.set_window(0x0000_0000, 0x7FFF_FFFF)
    await env.csr.write("SCRATCH", 0xABCD)
    await env.soft_reset()
    assert await env.csr.read("IRQ_ENABLE") == 0b101
    assert await env.csr.read("ADDR_WIN_HI") == 0x7FFF_FFFF
    assert await env.csr.read("SCRATCH") == 0xABCD
    await env.check()
    # soft reset in the middle of a large draw, with more kicks queued
    for trial in range(2 if QUICK else 4):
        big = env.new_target(FMT["ARGB8888"], 512, 64)
        env.mask.append((big.addr, big.addr + big.nbytes - 1))
        await env.kick(fill(CmdBuf(), big, (0, 0, 512, 64), rng.getrandbits(32)).fence(1))
        other = env.new_target(FMT["RGB565"], 64, 8)
        env.mask.append((other.addr, other.addr + other.nbytes - 1))
        await env.kick(fill(CmdBuf(), other, (0, 0, 64, 8), 5).fence(2), to_model=False)
        await env.cycles(rng.randint(50, 2000))
        await env.csr.write("CTRL", 1)
        await env.cycles(4)
        # No new transaction once the soft reset runs (arch.md §2.2). Async build: checked at the core side of the
        # memory CDC, where g2d_axi_mst issues them; an AW / AR the core issued just before can still be in the CDC
        # FIFO and reach m_axi_* afterwards, which is legal (it is not new).
        late = []
        if env.async_mode:
            top = top_of(dut)

            async def no_new():
                while True:
                    await RisingEdge(dut.clk)
                    for ch in ("aw", "ar"):
                        if int(getattr(top, f"c_axi_{ch}valid").value) and int(getattr(top, f"c_axi_{ch}ready").value):
                            late.append(ch)
            mon = cocotb.start_soon(no_new())
        else:
            env.axi.forbid_new = True
        while CsrIf.field("STATUS", "SOFT_RESET_BUSY", await env.csr.read("STATUS")):
            await env.cycles(8)
        if env.async_mode:
            mon.cancel()
            assert not late, f"new transactions at the core side during the soft reset: {late}"
        assert env.axi.idle(), "AXI transactions still outstanding after the soft reset"
        env.axi.forbid_new = False
        env.model.soft_reset()
        env.override = None
        st = await env.csr.read("STATUS")
        assert CsrIf.field("STATUS", "IDLE", st) == 1 and CsrIf.field("STATUS", "KICK_FREE", st) == KICK_DEPTH
        assert await env.csr.read("IRQ_STATUS") == 0b100   # only IDLE, entered after the reset (A5)
        await env.csr.write("IRQ_STATUS", 0b100)
        await env.check(perf=False)
        # the next stream is exact
        t = env.new_target(rng.choice(FMTS), 40, 10)
        await run(env, fill(CmdBuf(), t, (1, 1, 39, 9), rng.getrandbits(32)).fence(3), perf=False)
    env.perf_checked = False
    if env.async_mode:   # CDC-04: the FIFO-empty check ran at the soft resets
        dut._log.info(f"CDC FIFOs checked empty at {env.cdc_clr_checks} soft_clr pulses")
        assert env.cdc_clr_checks > 0


# ------------------------------------------------------------------------------------------------
# AXI errors at system level
# ------------------------------------------------------------------------------------------------

def bursts_of_fill(t, rect):
    """AXI bursts (start byte address, end byte address) of a clipped full-target fill, in order."""
    x0, y0, x1, y1 = rect
    out = []
    for y in range(y0, y1):
        a = t.addr + y * t.stride + x0 * t.bpp
        e = a + (x1 - x0) * t.bpp - 1
        while a <= e:
            be = min(e, a | (MAX_BURST - 1))
            out.append((a, be))
            a = be + 1
    return out


@cocotb.test()
async def axi_errors_system(dut):
    rng = random.Random(SEED + 77)
    env = await make_env(dut, "axierr", profile=Profile.random(rng, max_lat=30))
    env.perf_checked = False
    db = DATA_W // 8
    # write error inside a target
    t = env.new_target(FMT["ARGB8888"], 100, 20)
    lo = t.addr + 7 * t.stride + 40
    env.axi.err_ranges = [ErrRange(env.off + lo, env.off + lo + 15, 2, on_read=False)]
    first = next(b for b in bursts_of_fill(t, (0, 0, t.w, t.h))
                 if env.axi._err_for(env.off + (b[0] & ~(db - 1)), ((b[1] | (db - 1)) - (b[0] & ~(db - 1)) + 1), False))
    env.mask.append((t.addr, t.addr + t.nbytes - 1))
    cb = fill(CmdBuf(), t, (0, 0, t.w, t.h), 0xCAFEF00D)
    fence_at = len(cb.words) * 4
    cb.fence(1)
    other = env.new_target(FMT["RGB565"], 16, 4)
    fill(cb, other, (0, 0, 16, 4), 0x12345678)          # must not run: the GPU halts at the fence
    addr = await env.kick(cb, to_model=False)
    # model: everything up to the fence (the erroring fill writes into the masked target)
    env.model_prefix(addr, fence_at // 4)
    env.override = dict(err_status=ERR["AXI_WR"], err_addr=first[0], err_cmd_addr=addr + fence_at)
    await env.wait_done()
    await env.check()
    await env.soft_reset()
    env.axi.err_ranges = []
    await env.check()
    # read error on a command buffer: commands in the chunks before the erroring one run
    chunk = MAX_BURST
    ts = [env.new_target(FMT["BGRA8888"], 8, 2) for _ in range(6)]
    cb = CmdBuf()
    for i, tt in enumerate(ts):
        fill(cb, tt, (0, 0, 8, 2), 0x01010101 * (i + 1))
        cb.fence(i)
        while len(cb.words) * 4 % chunk:
            cb.nop()
    per = len(cb.words) * 4 // len(ts)
    assert per % chunk == 0
    addr = env.place(cb.to_bytes(), addr=0x0300_0000)
    bad = addr + 2 * per                    # the third command group sits in an erroring chunk
    env.axi.err_ranges = [ErrRange(env.off + bad, env.off + bad + chunk - 1, 3, on_write=False)]
    await env.csr.write("CMD_ADDR", addr)
    await env.csr.write("CMD_KICK", len(cb.words) * 4)
    env.model.kick(addr, 2 * per)
    await env.wait_done()
    assert await env.csr.read("ERR_STATUS") == ERR["AXI_RD"]
    assert await env.csr.read("ERR_ADDR") == bad
    eca = await env.csr.read("ERR_CMD_ADDR")
    assert addr <= eca < addr + len(cb.words) * 4, hex(eca)
    assert await env.csr.read("FENCE_SEQ") == 1   # both command groups before the faulting chunk ran
    env.override = dict(err_status=ERR["AXI_RD"], err_addr=bad, err_cmd_addr=eca)
    await env.check()
    await env.soft_reset()
    env.axi.err_ranges = []
    t = env.new_target(FMT["L8"], 30, 3)
    await run(env, fill(CmdBuf(), t, (0, 0, 30, 3), 0xFF808080).fence(9))


@cocotb.test()
async def axi_rd_cmd_settle(dut):
    """B-6.4: after an AXI_RD error on command words the parser still executes the commands before the
    faulting chunk (arch.md §8), so the core must not look drained before it reaches that word. Invariant
    checked every cycle: once STATUS.HALTED and `drained` are both seen, no FENCE and no AW follow.
    Every fetch chunk holds a large fill + FENCE, NOPs, a small redraw + FENCE: the faulting burst is
    requested when the parser enters the chunk before it and completes during that chunk's large fill, so
    the NOPs after its FENCE leave the engines and AXI idle while the redraw is still to run. FENCE_SEQ
    must reach the last FENCE before the faulting chunk. Fails on the old RTL (drained = engines and AXI
    idle only) at DATA_W 64 and 128."""
    core = top_of(dut).u_core
    chunk = MAX_BURST
    env = await make_env(dut, "b64")
    env.perf_checked = False
    for name, prof in [("ideal", Profile()), ("rlat6", Profile(r_lat=(6, 6))), ("wslow", Profile(b_lat=(30, 30)))]:
        env.axi.profile = prof
        groups, bad_group = 5, 3
        ts = [env.new_target(FMT["ARGB8888"], 64, 16) for _ in range(groups)]
        cb = CmdBuf()
        for i, tt in enumerate(ts):
            start = len(cb.words)
            fill(cb, tt, (0, 0, tt.w, tt.h), 0x01010101 * (i + 1))
            cb.fence(2 * i)
            for _ in range(4):
                cb.nop()
            cb.draw_rect(0, 0, 4, 2)
            cb.fence(2 * i + 1)
            assert (len(cb.words) - start) * 4 <= chunk
            while len(cb.words) * 4 % chunk:
                cb.nop()
        per = len(cb.words) * 4 // groups
        assert per == chunk
        env.next_cmd = (env.next_cmd + chunk - 1) & ~(chunk - 1)     # groups aligned to fetch chunks
        addr = env.place(cb.to_bytes())
        bad = addr + bad_group * per
        env.axi.err_ranges = [ErrRange(env.off + bad, env.off + bad + chunk - 1, 2, on_write=False)]
        violations = []

        async def watch():
            settled = False
            while True:
                await RisingEdge(dut.clk)
                if settled and (int(core.fence.value) or int(top_of(dut).c_axi_awvalid.value)):
                    violations.append(int(core.cur_cmd_addr.value))
                if int(core.halted.value) and int(core.drained.value):
                    settled = True

        mon = cocotb.start_soon(watch())
        await env.csr.write("CMD_ADDR", addr)
        await env.csr.write("CMD_KICK", len(cb.words) * 4)
        env.model.kick(addr, bad_group * per)
        # Q-RTL-9 (v1.4): a second kick stays queued behind the halt, so IDLE stays low; STATUS.CMD_ACTIVE alone
        # tells that the halt has settled (wait_done uses it and cross-checks `drained`)
        await env.csr.write("CMD_ADDR", addr)
        await env.csr.write("CMD_KICK", per)
        await env.wait_done()
        st = await env.csr.read("STATUS")
        assert CsrIf.field("STATUS", "HALTED", st) and not CsrIf.field("STATUS", "IDLE", st) and \
            not CsrIf.field("STATUS", "CMD_ACTIVE", st), f"{name}: STATUS 0x{st:x}"
        await env.cycles(200)
        mon.cancel()
        assert not violations, f"{name}: activity after halted+drained at {[hex(v) for v in violations[:4]]}"
        assert await env.csr.read("ERR_STATUS") == ERR["AXI_RD"]
        assert await env.csr.read("FENCE_SEQ") == 2 * bad_group - 1, name
        eca = await env.csr.read("ERR_CMD_ADDR")
        env.override = dict(err_status=ERR["AXI_RD"], err_addr=bad, err_cmd_addr=eca)
        await env.check()
        await env.soft_reset()
        env.axi.err_ranges = []


@cocotb.test()
async def axi_rd_src_starve(dut):
    """B-6.5: an AXI_RD error on a blit's source halts the GPU but not the parser, which goes on with the words
    already fetched; the fetch issues no new read. When the parser runs dry in the middle of a command (here: a
    long run of NOPs with 30-word payloads after the blit, far longer than the prefetch FIFO) it can never
    progress, so the halt has settled: the core must report drained (the first B-6.4 fix counted such a parser
    as live and the wait never ended: SYS-04 axi_rd_src, 300-seed regression). Checks: the wait ends, no FENCE
    or AW after HALTED + drained, the final FENCE is never reached, and a soft reset recovers exactly."""
    from test_blit import blit   # noqa: PLC0415  (test_blit imports this module)
    core = top_of(dut).u_core
    env = await make_env(dut, "b65")
    env.perf_checked = False
    for name, prof in [("ideal", Profile()), ("rlat20", Profile(r_lat=(20, 20)))]:
        env.axi.profile = prof
        src = env.new_target(FMT["ARGB8888"], 64, 32)
        dst = env.new_target(FMT["ARGB8888"], 64, 32)
        cb = CmdBuf()
        rc, _ = blit(cb, dst, src, [[1, 0, 0], [0, 1, 0], [0, 0, 1]])
        assert rc == 0
        for _ in range(300):
            cb.nop(skip=30)
        cb.fence(7)
        cb.pad()
        env.axi.err_ranges = [ErrRange(env.off + src.addr, env.off + src.addr + src.nbytes - 1, 2, on_write=False)]
        env.axi.err_bursts = []
        env.mask.append((dst.addr, dst.addr + dst.nbytes - 1))
        violations = []

        async def watch():
            settled = False
            while True:
                await RisingEdge(dut.clk)
                if settled and (int(core.fence.value) or int(top_of(dut).c_axi_awvalid.value)):
                    violations.append(int(core.cur_cmd_addr.value))
                if int(core.halted.value) and int(core.drained.value):
                    settled = True

        mon = cocotb.start_soon(watch())
        addr = env.place(cb.to_bytes())
        await env.csr.write("CMD_ADDR", addr)
        await env.csr.write("CMD_KICK", len(cb.words) * 4)
        await env.wait_done(limit=200_000)
        await env.cycles(200)
        mon.cancel()
        assert not violations, f"{name}: activity after halted+drained at {[hex(v) for v in violations[:4]]}"
        assert await env.csr.read("ERR_STATUS") & ERR["AXI_RD"], name
        assert CsrIf.field("STATUS", "HALTED", await env.csr.read("STATUS")), name
        assert await env.csr.read("FENCE_SEQ") != 7, f"{name}: the parser got past the stopped fetch"
        env.axi.err_ranges = []
        await env.soft_reset()
        await env.check()


@cocotb.test()
async def cov_errors_idle(dut):
    """cg_errors closure for errors raised while no draw engine is busy: an AXI write error whose B arrives
    after the fill engine finished (slow B), and KICK_OVF on a halted GPU whose kick queue is full."""
    env = await make_env(dut, "cov_err", profile=Profile(b_lat=(400, 500)))
    env.perf_checked = False
    core = top_of(dut).u_core
    t = env.new_target(FMT["ARGB8888"], 16, 2)
    env.mask.append((t.addr, t.addr + t.nbytes - 1))
    env.axi.err_ranges = [ErrRange(env.off + t.addr, env.off + t.addr + t.nbytes - 1, 2, on_read=False)]
    env.axi.err_bursts = []
    cb = fill(CmdBuf(), t, (0, 0, 16, 2), 0x0BADF00D)
    addr = await env.kick(cb, to_model=False)
    while not int(core.fill_idle.value) or not CsrIf.field("STATUS", "HALTED", await env.csr.read("STATUS")):
        await env.cycles(8)
    st, ea = await env.csr.read("ERR_STATUS"), await env.csr.read("ERR_ADDR")
    assert st == ERR["AXI_WR"], hex(st)
    assert any(a - env.off <= ea < a - env.off + n for a, n, _ in env.axi.err_bursts), hex(ea)
    env.override = dict(err_status=st, err_addr=ea, err_cmd_addr=await env.csr.read("ERR_CMD_ADDR"))
    assert addr <= env.override["err_cmd_addr"] <= addr + len(cb.to_bytes())
    await env.wait_done()
    await env.check()
    env.axi.err_ranges = []
    await env.soft_reset()
    # KICK_OVF while halted and idle: a malformed command halts the GPU, the queue fills, one more kick
    await env.kick(CmdBuf().nop().to_bytes(), size=0)
    await env.wait_done()
    for _ in range(KICK_DEPTH + 1):
        await env.kick(CmdBuf().fence(3).to_bytes(), to_model=False, wait_free=False)
    assert await env.csr.read("ERR_STATUS") & ERR["KICK_OVF"]
    await env.check()
    await env.soft_reset()
    await env.check()


# ------------------------------------------------------------------------------------------------
# random streams and throughput
# ------------------------------------------------------------------------------------------------

@cocotb.test()
async def random_streams(dut):
    rng = random.Random(SEED + 99)
    env = await make_env(dut, "random", profile=Profile.random(rng, max_lat=60))
    rounds = 6 if QUICK else 16
    for rnd in range(rounds):
        env.axi.profile = Profile.random(rng, max_lat=rng.choice([0, 20, 200]))
        targets = [env.new_target(rng.choice(FMTS), rng.randint(1, 120), rng.randint(1, 20))
                   for _ in range(rng.randint(1, 4))]
        for k in range(rng.randint(1, KICK_DEPTH + 3)):
            cb = CmdBuf()
            for _ in range(rng.randint(1, 10)):
                r = rng.random()
                t = rng.choice(targets)
                if r < 0.6:
                    clip = None if rng.random() < 0.5 else (rng.randint(0, t.w), rng.randint(0, t.h),
                                                            rng.randint(0, t.w + 9), rng.randint(0, t.h + 9))
                    blend = 0 if rng.random() < 0.6 else rng.randint(1, 15)   # 0: fast path, else g2d_pix
                    fill(cb, t, rand_rect(rng, t), rng.getrandbits(32), rand_alpha(rng), clip=clip, blend=blend,
                         dalpha=rand_alpha(rng))
                elif r < 0.7:
                    cb.nop(rng.randint(0, 3), fill=rng.getrandbits(32))
                elif r < 0.8:
                    idx = rng.choice([0x10, 0x20, 0x28, 0x38, 0x0C])
                    cb.load_state(idx, [rng.getrandbits(32) for _ in range(rng.randint(1, 8))])
                elif r < 0.95:
                    cb.fence(rng.getrandbits(32), irq=rng.random() < 0.3)
                else:  # a draw with a misaligned ramp address: BAD_STATE
                    cb.load_state(STATE["GRAD_RAMP_ADDR"].addr, [0x0100_0002])
                    fill(cb, t, rand_rect(rng, t), 0, blend=rng.randint(0, 15), paint=2)
            if await env.kick(cb) is None:
                break   # halted with a full queue
        await env.wait_done()
        await env.check()
        if CsrIf.field("STATUS", "HALTED", await env.csr.read("STATUS")):
            await env.soft_reset()
            await env.check()
        await env.csr.write("IRQ_STATUS", 0b111)
        env.model.clear_irq_fence()


@cocotb.test()
async def mem_qos_system(dut):
    """v1.9 MEM_QOS / MEM_ATTR at the top: random throttle and attribute settings, changed between kicks while
    the engine may still run; every result bit-exact against the model, the port attributes follow the CSR."""
    rng = random.Random(SEED + 77)
    env = await make_env(dut, "qos", profile=Profile.random(rng, max_lat=60))
    for rnd in range(3 if QUICK else 8):
        env.axi.profile = Profile.random(rng, max_lat=rng.choice([0, 20, 120]))
        t = env.new_target(rng.choice(FMTS), rng.randint(8, 120), rng.randint(4, 20))
        for k in range(rng.randint(1, KICK_DEPTH)):
            qos = rng.randint(0, 3) | rng.randint(0, 3) << 8 | rng.choice([0, 2, 30]) << 16 | rng.choice([0, 5, 60]) << 24
            attr = rng.getrandbits(4) | rng.getrandbits(3) << 4 | rng.getrandbits(4) << 8 | \
                rng.getrandbits(4) << 16 | rng.getrandbits(3) << 20 | rng.getrandbits(4) << 24
            await env.csr.write("MEM_QOS", qos)
            await env.csr.write("MEM_ATTR", attr)
            assert await env.csr.read("MEM_QOS") == qos
            assert await env.csr.read("MEM_ATTR") == attr
            cb = CmdBuf()
            for _ in range(rng.randint(1, 6)):
                blend = 0 if rng.random() < 0.5 else rng.randint(1, 15)
                fill(cb, t, rand_rect(rng, t), rng.getrandbits(32), rand_alpha(rng), blend=blend, dalpha=rand_alpha(rng))
            cb.fence(rnd * 16 + k)
            if await env.kick(cb) is None:
                break
        await env.wait_done()
        await env.check()
        # core side of the memory port (= m_axi_* without MEM_ASYNC; with it, the port pins hold the last payload
        # that crossed while the channel is idle)
        top = top_of(dut)
        ar = (int(top.c_axi_arcache.value), int(top.c_axi_arprot.value), int(top.c_axi_arqos.value))
        aw = (int(top.c_axi_awcache.value), int(top.c_axi_awprot.value), int(top.c_axi_awqos.value))
        assert ar == (attr & 0xF, attr >> 4 & 7, attr >> 8 & 0xF), (hex(attr), ar)
        assert aw == (attr >> 16 & 0xF, attr >> 20 & 7, attr >> 24 & 0xF), (hex(attr), aw)
    await env.csr.write("MEM_QOS", 0)
    await env.csr.write("MEM_ATTR", 0x0003_0003)


@cocotb.test()
async def fill_throughput(dut):
    """Informal fill throughput with an ideal memory (assumption A6): beats per busy cycle."""
    env = await make_env(dut, "throughput")
    t = env.new_target(FMT["ARGB8888"], 1024, 32, addr=0x1F00_0000, stride=4096)
    await env.csr.write("PERF_CTRL", 0b011)
    await run(env, fill(CmdBuf(), t, (0, 0, 1024, 32), 0x89ABCDEF).fence(1), perf=False)
    await env.csr.write("PERF_CTRL", 0b101)
    beats = await env.csr.read("PERF_WR_BEATS")
    busy = await env.csr.read("PERF_BUSY_CYCLES")
    eff = beats / busy
    dut._log.info(f"fill throughput: {beats} beats in {busy} busy cycles = {eff:.3f} beats/clk "
                  f"({eff * DATA_W / 32:.2f} px/clk at 32 bpp)")
    assert beats == 1024 * 4 * 32 // (DATA_W // 8)
    if not env.async_mode:      # in core cycles; the async build adds the crossing and a slower memory clock
        assert eff > 0.9, eff
    env.perf_checked = False


# ------------------------------------------------------------------------------------------------

import pytest  # noqa: E402

CONFIGS = {
    "dw64": (dict(DATA_W=64), {}),
    "dw128": (dict(DATA_W=128, ADDR_W=48, KICK_DEPTH=8, MAX_BURST_BYTES=128, RD_OUTSTANDING=4, WR_OUTSTANDING=2),
              {"G2D_BASE_HI": "5"}),
    # v1.1 CDC (verification-plan.md CDC-04): both ports on their own clocks, periods and phases from the seed
    "async": (dict(DATA_W=64, CFG_ASYNC=1, MEM_ASYNC=1), {"G2D_ASYNC": "1"}),
    "async_s3d2": (dict(DATA_W=128, ADDR_W=48, CFG_ASYNC=1, MEM_ASYNC=1, SYNC_STAGES=3, MEM_CDC_LOG_DEPTH=1),
                   {"G2D_ASYNC": "1", "G2D_BASE_HI": "3"}),
}


def test_top_irq_modes():
    """v1.9: IRQ_PULSE + IRQ_ACTIVE_LOW build (verification-plan.md CSR-09)."""
    import runner
    runner.run("g2d_top_tb", "test_top", params=dict(DATA_W=64, IRQ_PULSE=1, IRQ_ACTIVE_LOW=1), tag="irq_pulse_low",
               testcase=["csr01_top_reset_values", "csr09_irq_pulse", "dft01_test_mode_reset"])


@pytest.mark.parametrize("cfg", list(CONFIGS))
def test_top(cfg):
    import runner
    params, env = CONFIGS[cfg]
    quick = cfg != "dw64" and os.environ.get("G2D_FULL") != "1"
    runner.run("g2d_top_tb", "test_top", params=params, tag=cfg, extra_env={**env, "G2D_QUICK": "1" if quick else "0"})


