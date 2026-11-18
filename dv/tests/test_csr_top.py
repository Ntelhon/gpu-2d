# SPDX-License-Identifier: Apache-2.0
"""cocotb tests of the CSR subsystem g2d_csr_top (AXI4-Lite adapter + register file + control + perf).

verification-plan.md CSR-01..06 at block level: the core-side event ports are driven directly, so every
control rule (kick queue, first-error capture, IRQ sources, soft-reset sequence, perf counters) is checked
cycle by cycle. The same rules are re-checked end to end through the full GPU in test_top.py.
"""

import os
import random
import zlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

from csr_if import REGMAP, RESP_OKAY, RESP_SLVERR, CsrIf
from g2d_cmd import CSR

KICK_DEPTH = int(os.environ.get("G2D_P_KICK_DEPTH", "4"))
SEED = int(os.environ.get("SEED", "1"))

EVENT_INPUTS = ["kick_ready", "cmd_err_decode", "cmd_err_bad_state", "cmd_err_addr_range", "cmd_err_addr",
                "cmd_err_cmd_addr", "axi_err_rd", "axi_err_wr", "axi_err_win", "axi_err_addr", "cur_cmd_addr",
                "fence", "fence_value", "fence_irq", "core_busy", "drained", "perf_draw_done", "perf_pixels",
                "perf_rd_beat", "perf_wr_beat", "perf_rd_stall", "perf_wr_stall", "perf_drain", "perf_src_miss",
                "dbg_wr_data", "dbg_wr_strb", "dbg_ram_coll"]

ERR_BITS = {f.name: f.lsb for f in CSR["ERR_STATUS"].fields}
HALTING = ["CMD_DECODE", "BAD_STATE", "ADDR_RANGE", "AXI_RD", "AXI_WR"]


class Tb:
    def __init__(self, dut):
        self.dut = dut
        self.csr = CsrIf(dut, "s_axil", dut.clk, dut.rst_n)

    async def start(self):
        dut = self.dut
        cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
        for s in EVENT_INPUTS:
            getattr(dut, s).value = 0
        dut.drained.value = 1
        dut.rst_n.value = 0
        await Timer(35, unit="ns")
        dut.rst_n.value = 1
        await RisingEdge(dut.clk)
        await RisingEdge(dut.clk)

    async def pulse(self, **signals):
        """Drive the given inputs for exactly one clock cycle, then return them to 0."""
        for k, v in signals.items():
            getattr(self.dut, k).value = v
        await RisingEdge(self.dut.clk)
        for k in signals:
            getattr(self.dut, k).value = 0

    async def cycles(self, n):
        for _ in range(n):
            await RisingEdge(self.dut.clk)


def readable_reset(reg):
    v = 0
    for f in reg.fields:
        if f.access in ("rw", "w1c", "const"):
            v |= f.reset << f.lsb
    return v


@cocotb.test()
async def csr01_reset_values(dut):
    tb = Tb(dut)
    await tb.start()
    expect_ro = {
        ("HWCFG", "DATA_BYTES"): 8, ("HWCFG", "ADDR_W"): 32, ("HWCFG", "KICK_DEPTH"): KICK_DEPTH,
        **{("HWCFG", f"FEAT_{n}"): 1 for n in ("FAMILY", "GAUSS", "YUV", "COLOR", "RADIAL", "MASK")},   # v1.7 default
        ("STATUS", "IDLE"): 1, ("STATUS", "KICK_FREE"): KICK_DEPTH,
    }
    for reg in REGMAP.csr:
        v = await tb.csr.read(reg.name)
        exp = readable_reset(reg)
        for f in reg.fields:
            if f.access == "ro":
                exp |= expect_ro.get((reg.name, f.name), 0) << f.lsb
        assert v == exp, f"{reg.name}: 0x{v:08x} != 0x{exp:08x}"
    assert int(dut.irq.value) == 0 and int(dut.idle.value) == 1 and int(dut.stop.value) == 0


@cocotb.test()
async def csr02_03_rw_and_unmapped(dut):
    tb = Tb(dut)
    await tb.start()
    rng = random.Random(SEED)
    rw = [r for r in REGMAP.csr if all(f.access == "rw" for f in r.fields)]
    for reg in rw:
        mask = sum(f.mask for f in reg.fields)
        model = readable_reset(reg)
        for _ in range(8):
            v, strb = rng.getrandbits(32), rng.getrandbits(4)
            assert await tb.csr.write_resp(reg.name, v, strb) == RESP_OKAY
            bm = sum(0xFF << 8 * i for i in range(4) if strb >> i & 1)
            model = (model & ~bm) | (v & bm & mask)
            assert await tb.csr.read(reg.name) == model, reg.name
    # window and base outputs follow the registers (64-byte granularity, arch.md 4.2)
    await tb.csr.write("ADDR_WIN_LO", 0x8000_0047)
    await tb.csr.write("ADDR_WIN_HI", 0x9000_0000)
    await tb.csr.write("MEM_BASE_HI", 0x5A)
    await tb.cycles(1)
    assert int(dut.addr_win_lo.value) == 0x8000_0040
    assert int(dut.addr_win_hi.value) == 0x9000_003F
    assert int(dut.mem_base_hi.value) == 0x5A
    mapped = {r.addr for r in REGMAP.csr}
    for a in rng.sample([a for a in range(0, 4096, 4) if a not in mapped], 32):
        v, resp = await tb.csr.read_resp(a)
        assert resp == RESP_SLVERR and v == 0, hex(a)
        assert await tb.csr.write_resp(a, 0xFFFFFFFF) == RESP_SLVERR, hex(a)
    # ro/const ignore writes
    await tb.csr.write("ID", 0)
    assert await tb.csr.read("ID") == CSR["ID"].reset


@cocotb.test()
async def csr04_backpressure_concurrent(dut):
    """Random stalls on all five AXI4-Lite channels with reads and writes in flight together."""
    tb = Tb(dut)
    await tb.start()
    rng = random.Random(SEED + 4)
    m = tb.csr.master

    def gen(p):
        while True:
            yield rng.random() < p

    m.write_if.aw_channel.set_pause_generator(gen(0.3))
    m.write_if.w_channel.set_pause_generator(gen(0.4))
    m.write_if.b_channel.set_pause_generator(gen(0.5))
    m.read_if.ar_channel.set_pause_generator(gen(0.3))
    m.read_if.r_channel.set_pause_generator(gen(0.5))

    n = 200
    seen = []

    async def writer():
        for i in range(1, n + 1):
            await tb.csr.write("SCRATCH", i)
            await tb.csr.write("IRQ_ENABLE", i & 7)

    async def reader():
        for _ in range(n):
            seen.append(await tb.csr.read("SCRATCH"))
            v = await tb.csr.read("ID")
            assert v == CSR["ID"].reset

    w = cocotb.start_soon(writer())
    r = cocotb.start_soon(reader())
    await w
    await r
    assert seen == sorted(seen), "SCRATCH went backwards"
    assert await tb.csr.read("SCRATCH") == n
    assert await tb.csr.read("IRQ_ENABLE") == n & 7


@cocotb.test()
async def kick_queue_and_overflow(dut):
    tb = Tb(dut)
    await tb.start()
    rng = random.Random(SEED + 5)
    kicks = [(rng.getrandbits(29) << 3, rng.getrandbits(24)) for _ in range(KICK_DEPTH + 2)]
    for i, (a, s) in enumerate(kicks):
        await tb.csr.write("CMD_ADDR", a | 0x7)  # low bits are not stored
        await tb.csr.write("CMD_KICK", s)
        st = await tb.csr.read("STATUS")
        exp_free = max(KICK_DEPTH - (i + 1), 0)
        assert CsrIf.field("STATUS", "KICK_FREE", st) == exp_free
        assert CsrIf.field("STATUS", "IDLE", st) == 0
    err = await tb.csr.read("ERR_STATUS")
    assert err == 1 << ERR_BITS["KICK_OVF"], hex(err)
    assert int(dut.halted.value) == 0 and int(dut.stop.value) == 0
    assert CsrIf.field("IRQ_STATUS", "ERROR", await tb.csr.read("IRQ_STATUS")) == 1
    # KICK_OVF does not capture addresses
    assert await tb.csr.read("ERR_ADDR") == 0 and await tb.csr.read("ERR_CMD_ADDR") == 0
    # drain the queue with random ready; order and content preserved, overflowed kicks gone
    got = []
    while len(got) < KICK_DEPTH:
        rdy = int(rng.random() < 0.5)
        dut.kick_ready.value = rdy
        await RisingEdge(dut.clk)
        if rdy and int(dut.kick_valid.value):
            got.append((int(dut.kick_addr.value), int(dut.kick_size.value)))
    dut.kick_ready.value = 0
    await tb.cycles(2)
    assert int(dut.kick_valid.value) == 0
    assert got == kicks[:KICK_DEPTH]
    st = await tb.csr.read("STATUS")
    assert CsrIf.field("STATUS", "KICK_FREE", st) == KICK_DEPTH and CsrIf.field("STATUS", "IDLE", st) == 1


@cocotb.test()
async def errors_first_wins_and_halt(dut):
    tb = Tb(dut)
    await tb.start()
    dut.cur_cmd_addr.value = 0x1111_1110
    await tb.pulse(axi_err_rd=1, axi_err_addr=0xAAAA_0000)
    await tb.cycles(1)
    assert int(dut.halted.value) == 1 and int(dut.stop.value) == 1
    await tb.pulse(cmd_err_bad_state=1, cmd_err_addr=0x5, cmd_err_cmd_addr=0x6)
    assert await tb.csr.read("ERR_STATUS") == (1 << ERR_BITS["AXI_RD"]) | (1 << ERR_BITS["BAD_STATE"])
    assert await tb.csr.read("ERR_ADDR") == 0xAAAA_0000
    assert await tb.csr.read("ERR_CMD_ADDR") == 0x1111_1110
    st = await tb.csr.read("STATUS")
    assert CsrIf.field("STATUS", "HALTED", st) == 1
    # W1C clears the bits but not HALTED; the next error is a "first" error again
    await tb.csr.write("ERR_STATUS", 0x3F)
    assert await tb.csr.read("ERR_STATUS") == 0
    assert CsrIf.field("STATUS", "HALTED", await tb.csr.read("STATUS")) == 1
    # same-cycle command and AXI errors: the command error's addresses win
    await tb.pulse(cmd_err_decode=1, cmd_err_addr=0, cmd_err_cmd_addr=0x2000, axi_err_wr=1, axi_err_addr=0x77)
    assert await tb.csr.read("ERR_STATUS") == (1 << ERR_BITS["CMD_DECODE"]) | (1 << ERR_BITS["AXI_WR"])
    assert await tb.csr.read("ERR_ADDR") == 0 and await tb.csr.read("ERR_CMD_ADDR") == 0x2000
    # AXI window violation reports ADDR_RANGE
    await tb.csr.write("ERR_STATUS", 0x3F)
    await tb.pulse(axi_err_win=1, axi_err_addr=0xC0)
    assert await tb.csr.read("ERR_STATUS") == 1 << ERR_BITS["ADDR_RANGE"]
    assert await tb.csr.read("ERR_ADDR") == 0xC0


@cocotb.test()
async def csr05_irq_sources(dut):
    tb = Tb(dut)
    await tb.start()
    names = ["FENCE", "ERROR", "IDLE"]
    for en in range(8):
        await tb.csr.write("IRQ_ENABLE", en)
        await tb.csr.write("IRQ_STATUS", 7)
        for src in names:
            await tb.csr.write("IRQ_STATUS", 7)
            if src == "FENCE":
                await tb.pulse(fence=1, fence_irq=1, fence_value=0x100 + en)
            elif src == "ERROR":
                await tb.pulse(perf_drain=0, axi_err_rd=1, axi_err_addr=0)  # any error bit
            else:
                dut.core_busy.value = 1
                await tb.cycles(2)
                dut.core_busy.value = 0
            await tb.cycles(3)
            st = await tb.csr.read("IRQ_STATUS")
            bit = names.index(src)
            assert st >> bit & 1, f"{src} not set"
            exp_irq = int(bool(st & en))
            assert int(dut.irq.value) == exp_irq, f"irq {src} en={en}"
            # level: stays while status & enable
            await tb.cycles(5)
            assert int(dut.irq.value) == exp_irq
            await tb.csr.write("IRQ_STATUS", st)
            await tb.cycles(2)
            assert int(dut.irq.value) == 0
    # FENCE_SEQ is written by every fence; IRQ_STATUS.FENCE only with the IRQ flag
    await tb.csr.write("IRQ_STATUS", 7)
    await tb.pulse(fence=1, fence_irq=0, fence_value=0xCAFE)
    assert await tb.csr.read("FENCE_SEQ") == 0xCAFE
    assert CsrIf.field("IRQ_STATUS", "FENCE", await tb.csr.read("IRQ_STATUS")) == 0


@cocotb.test()
async def csr06_soft_reset(dut):
    tb = Tb(dut)
    await tb.start()
    # configuration that must survive
    cfg = {"IRQ_ENABLE": 0x5, "ADDR_WIN_LO": 0x1000_0000, "ADDR_WIN_HI": 0x2000_0000 | 0x3F,
           "MEM_BASE_HI": 0x12, "SCRATCH": 0xDEAD_BEEF}
    for k, v in cfg.items():
        await tb.csr.write(k, v)
    # state that must be cleared
    await tb.pulse(fence=1, fence_value=0x42, fence_irq=1)
    await tb.pulse(cmd_err_addr_range=1, cmd_err_addr=0x10, cmd_err_cmd_addr=0x20)
    for i in range(2):
        await tb.csr.write("CMD_ADDR", 0x100 * i)
        await tb.csr.write("CMD_KICK", 8)
    await tb.csr.write("PERF_CTRL", 1)
    await tb.cycles(10)
    await tb.csr.write("PERF_CTRL", 1 | 4)
    cyc_before = await tb.csr.read("PERF_CYCLES")
    assert cyc_before > 0
    # soft reset waits for drained
    dut.drained.value = 0
    await tb.csr.write("CTRL", 1)
    st = await tb.csr.read("STATUS")
    assert CsrIf.field("STATUS", "SOFT_RESET_BUSY", st) == 1 and int(dut.stop.value) == 1
    await tb.csr.write("CMD_KICK", 8)  # dropped while busy, no KICK_OVF
    await tb.cycles(20)
    assert int(dut.soft_clr.value) == 0
    st = await tb.csr.read("STATUS")
    assert CsrIf.field("STATUS", "SOFT_RESET_BUSY", st) == 1
    dut.drained.value = 1
    clr_cycles = 0
    for _ in range(4):
        await RisingEdge(dut.clk)
        clr_cycles += int(dut.soft_clr.value)
    assert clr_cycles == 1, "soft_clr must pulse exactly once"
    await tb.cycles(2)
    st = await tb.csr.read("STATUS")
    assert CsrIf.field("STATUS", "SOFT_RESET_BUSY", st) == 0
    assert CsrIf.field("STATUS", "HALTED", st) == 0 and int(dut.stop.value) == 0
    assert CsrIf.field("STATUS", "KICK_FREE", st) == KICK_DEPTH and int(dut.kick_valid.value) == 0
    assert CsrIf.field("STATUS", "IDLE", st) == 1
    assert await tb.csr.read("ERR_STATUS") == 0
    assert await tb.csr.read("ERR_ADDR") == 0 and await tb.csr.read("ERR_CMD_ADDR") == 0
    assert await tb.csr.read("FENCE_SEQ") == 0
    # IRQ_STATUS: only IDLE (entered idle after the soft reset, assumption A5)
    assert await tb.csr.read("IRQ_STATUS") == 1 << 2
    for k, v in cfg.items():
        exp = v & ~0x3F if k == "ADDR_WIN_LO" else v
        assert await tb.csr.read(k) == exp, k
    # perf counters survive a soft reset
    await tb.csr.write("PERF_CTRL", 1 | 4)
    assert await tb.csr.read("PERF_CYCLES") > cyc_before


@cocotb.test()
async def csr07_debug(dut):
    """v1.8 debug registers (arch.md §9.1): RAM_COLL sticky / write-1-to-clear / cleared by a soft reset; WR_SIG =
    zlib.crc32 of the W beats (strobed-off lanes as 0x00), restarted by DBG_CTRL.SIG_CLEAR, kept by a soft reset."""
    tb = Tb(dut)
    await tb.start()
    rng = random.Random(SEED + 7)
    db = 8   # DATA_W 64 in this bench
    assert await tb.csr.read("WR_SIG") == 0 and await tb.csr.read("DBG_STATUS") == 0
    # collisions: every bit sets on its own and stays set
    for b in range(7):
        await tb.pulse(dbg_ram_coll=1 << b)
        assert await tb.csr.read("DBG_STATUS") == (1 << (b + 1)) - 1
    await tb.csr.write("DBG_STATUS", 0x05)
    assert await tb.csr.read("DBG_STATUS") == 0x7A
    # signature: beats with random data and strobes; cycles without a beat change nothing
    ref = 0
    for _ in range(40):
        d, st = rng.getrandbits(64), rng.getrandbits(db)
        beat = rng.random() < 0.7
        await tb.pulse(perf_wr_beat=int(beat), dbg_wr_data=d, dbg_wr_strb=st)
        if beat:
            ref = zlib.crc32(bytes(((d >> (8 * i)) & 0xFF) if (st >> i) & 1 else 0 for i in range(db)), ref)
    assert await tb.csr.read("WR_SIG") == ref, "WR_SIG != zlib.crc32 of the beats"
    # soft reset: RAM_COLL back to 0 (w1c), WR_SIG kept
    await tb.csr.write("CTRL", 1)
    await tb.cycles(10)
    assert await tb.csr.read("DBG_STATUS") == 0
    assert await tb.csr.read("WR_SIG") == ref, "a soft reset must not touch WR_SIG"
    # SIG_CLEAR restarts it; a write of 0 does nothing
    await tb.csr.write("DBG_CTRL", 0)
    assert await tb.csr.read("WR_SIG") == ref
    await tb.csr.write("DBG_CTRL", 1)
    assert await tb.csr.read("WR_SIG") == 0 and await tb.csr.read("DBG_CTRL") == 0
    await tb.pulse(perf_wr_beat=1, dbg_wr_data=0x0807060504030201, dbg_wr_strb=0xFF)
    assert await tb.csr.read("WR_SIG") == zlib.crc32(bytes(range(1, 9)))


@cocotb.test()
async def perf_counters(dut):
    tb = Tb(dut)
    await tb.start()
    rng = random.Random(SEED + 9)
    await tb.csr.write("PERF_CTRL", 2)  # clear
    names = ["perf_draw_done", "perf_rd_beat", "perf_wr_beat", "perf_rd_stall", "perf_wr_stall", "perf_drain",
             "perf_src_miss"]
    regs = ["PERF_DRAWS", "PERF_RD_BEATS", "PERF_WR_BEATS", "PERF_RD_STALL", "PERF_WR_STALL", "PERF_DRAIN_CYC",
            "PERF_SRC_CACHE_MISS"]
    exp = dict.fromkeys(regs, 0)
    exp_pix = 0
    # EN = 0: nothing counts
    await tb.pulse(perf_draw_done=1, perf_pixels=5)
    await tb.csr.write("PERF_CTRL", 1)  # EN
    n_cycles = 300
    busy = 0
    for _ in range(n_cycles):
        vals = {n: int(rng.random() < 0.4) for n in names}
        pix = rng.getrandbits(14) if rng.random() < 0.3 else 0
        b = int(rng.random() < 0.5)
        for n, v in vals.items():
            getattr(dut, n).value = v
        dut.perf_pixels.value = pix
        dut.core_busy.value = b
        await RisingEdge(dut.clk)
        for n, r in zip(names, regs):
            exp[r] += vals[n]
        exp_pix += pix
    for n in names:
        getattr(dut, n).value = 0
    dut.perf_pixels.value = 0
    dut.core_busy.value = 0
    await tb.csr.write("PERF_CTRL", 0)  # stop counting, then snapshot
    await tb.csr.write("PERF_CTRL", 4)
    for r in regs:
        assert await tb.csr.read(r) == exp[r], r
    assert await tb.csr.read("PERF_PIXELS_OUT") == exp_pix
    cyc = await tb.csr.read("PERF_CYCLES")
    busy = await tb.csr.read("PERF_BUSY_CYCLES")
    assert n_cycles <= cyc <= n_cycles + 40
    assert 0 < busy < cyc
    # reads show the shadow: new events after the snapshot are invisible until the next snapshot
    await tb.csr.write("PERF_CTRL", 1)
    await tb.pulse(perf_draw_done=1)
    assert await tb.csr.read("PERF_DRAWS") == exp["PERF_DRAWS"]
    await tb.csr.write("PERF_CTRL", 1 | 4)
    assert await tb.csr.read("PERF_DRAWS") == exp["PERF_DRAWS"] + 1
    # CLEAR + SNAPSHOT in one write: the snapshot holds the pre-clear values; the counters restart
    await tb.csr.write("PERF_CTRL", 2 | 4)
    assert await tb.csr.read("PERF_DRAWS") == exp["PERF_DRAWS"] + 1
    await tb.csr.write("PERF_CTRL", 4)
    assert await tb.csr.read("PERF_DRAWS") == 0


# ------------------------------------------------------------------------------------------------

def test_csr_top():
    import runner
    runner.run("g2d_csr_top_tb", "test_csr_top", params={"KICK_DEPTH": 4}, tag="k4")


def test_csr_top_depth16():
    import runner
    runner.run("g2d_csr_top_tb", "test_csr_top", params={"KICK_DEPTH": 16}, tag="k16",
               testcase=["csr01_reset_values", "kick_queue_and_overflow", "csr06_soft_reset"])
