# SPDX-License-Identifier: Apache-2.0
"""chs_ps2 (DOOM side project, step D3; fpga/periph/chs_ps2.sv): PS/2 receiver and VGA frame counter.

A PS/2 device model sends frames (start, 8 data bits LSB first, odd parity, stop; data changes while the clock is
high, the host samples on the falling edge). Self-checking against a reference queue:
  ps01  random bytes with random bit timing, read back in order through DATA (pop), STATUS count
  ps02  bad parity / bad stop bit dropped and counted, a frame abandoned in the middle (timeout) dropped, the
        following good bytes received; error count cleared by a write
  ps03  glitches shorter than the filter on both lines, idle and inside frames: no effect
  ps04  FIFO overflow: DEPTH bytes kept, later ones dropped, OVF sticky until cleared
  ps05  frame counter: vsync pulses of random length counted (active level VSYNC_ACTIVE), ID register
The bench uses a short filter and timeout (parameters) so a bit takes tens of clocks instead of thousands; the
board values (FILT 8, TIMEOUT 100 000 at 50 MHz, PS/2 10–16.7 kHz) are one configuration with real-ish timing.
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[2]
P = {k[len("G2D_P_"):]: int(v) for k, v in os.environ.items() if k.startswith("G2D_P_")}
DEPTH, FILT, TIMEOUT = P.get("DEPTH", 64), P.get("FILT", 8), P.get("TIMEOUT", 100_000)
SEED = int(os.environ.get("G2D_SEED", "1"))


class Dut:
    def __init__(self, dut):
        self.dut = dut
        self.rng = random.Random(SEED)
        self.half = max(4 * FILT, 12)      # clocks per half PS/2 clock period (scaled down)

    async def start(self):
        d = self.dut
        cocotb.start_soon(Clock(d.clk_i, 10, unit="ns").start())
        d.ps2_clk_i.value = 1
        d.ps2_data_i.value = 1
        d.vsync_i.value = 1
        d.req_i.value = 0
        d.we_i.value = 0
        d.addr_i.value = 0
        d.wdata_i.value = 0
        d.rst_ni.value = 0
        await ClockCycles(d.clk_i, 5)
        d.rst_ni.value = 1
        await ClockCycles(d.clk_i, 5)

    async def reg(self, addr, wdata=None):
        d = self.dut
        await FallingEdge(d.clk_i)
        d.req_i.value = 1
        d.we_i.value = int(wdata is not None)
        d.addr_i.value = addr >> 2
        d.wdata_i.value = wdata or 0
        await Timer(1, unit="ns")
        r = int(d.rdata_o.value)
        await RisingEdge(d.clk_i)
        await FallingEdge(d.clk_i)
        d.req_i.value = 0
        d.we_i.value = 0
        return r

    async def wait(self, n):
        await ClockCycles(self.dut.clk_i, n)

    async def send(self, byte, parity_ok=True, stop=1, abort_after=None, glitch=False):
        """One frame; abort_after = number of bits sent before the device goes silent."""
        d = self.dut
        par = 1 - (bin(byte).count("1") & 1)
        if not parity_ok:
            par ^= 1
        bits = [0] + [(byte >> i) & 1 for i in range(8)] + [par, stop]
        for i, b in enumerate(bits):
            if abort_after is not None and i == abort_after:
                d.ps2_clk_i.value = 1
                d.ps2_data_i.value = 1
                await self.wait(TIMEOUT + 4 * FILT + 20)
                return
            d.ps2_data_i.value = b
            await self.wait(self.half + self.rng.randrange(self.half // 2))
            if glitch and self.rng.random() < 0.5:
                await self.glitch(d.ps2_clk_i, 1)
            d.ps2_clk_i.value = 0
            await self.wait(self.half + self.rng.randrange(self.half // 2))
            if glitch and self.rng.random() < 0.5:
                await self.glitch(d.ps2_data_i, b)
            d.ps2_clk_i.value = 1
        d.ps2_data_i.value = 1
        await self.wait(self.half)

    async def glitch(self, sig, level):
        sig.value = 1 - level
        await self.wait(self.rng.randint(1, max(1, FILT - 2)))
        sig.value = level
        await self.wait(FILT + 2)

    async def drain(self):
        out = []
        while True:
            r = await self.reg(0x0)
            if not (r >> 8) & 1:
                return out
            out.append(r & 0xFF)


@cocotb.test()
async def ps01_random_bytes(dut):
    t = Dut(dut)
    await t.start()
    assert await t.reg(0xC) == 0x5032_0001
    want = [t.rng.randrange(256) for _ in range(min(DEPTH, 40))]
    for b in want:
        await t.send(b)
    st = await t.reg(0x4)
    assert st & 0xFF == len(want), hex(st)
    got = await t.drain()
    assert got == want, (got, want)
    assert (await t.reg(0x4)) & 0x1FF == 0


@cocotb.test()
async def ps02_errors(dut):
    t = Dut(dut)
    await t.start()
    want, bad = [], 0
    for _ in range(24):
        b = t.rng.randrange(256)
        kind = t.rng.choice(["ok", "ok", "parity", "stop", "abort"])
        if kind == "ok":
            await t.send(b)
            want.append(b)
        elif kind == "parity":
            await t.send(b, parity_ok=False)
            bad += 1
        elif kind == "stop":
            await t.send(b, stop=0)
            bad += 1
            # a stop bit of 0 leaves data low: release it before the next start bit
            dut.ps2_data_i.value = 1
            await t.wait(4 * FILT)
        else:
            await t.send(b, abort_after=t.rng.randint(2, 9))
    st = await t.reg(0x4)
    assert (st >> 16) & 0xFF == bad, (hex(st), bad)
    assert await t.drain() == want
    await t.reg(0x4, 1 << 16)
    assert (await t.reg(0x4)) >> 16 & 0xFF == 0


@cocotb.test()
async def ps03_glitches(dut):
    t = Dut(dut)
    await t.start()
    want = []
    for _ in range(min(20, DEPTH)):    # read back once: stay within the FIFO
        if t.rng.random() < 0.5:     # idle glitches on either line
            await t.glitch(dut.ps2_clk_i, 1)
            await t.glitch(dut.ps2_data_i, 1)
        b = t.rng.randrange(256)
        await t.send(b, glitch=True)
        want.append(b)
    got = await t.drain()
    assert got == want, [(i, hex(g), hex(w)) for i, (g, w) in enumerate(zip(got, want)) if g != w] + [len(got), len(want), hex(await t.reg(0x4))]
    assert (await t.reg(0x4)) >> 16 & 0xFF == 0


@cocotb.test()
async def ps04_overflow(dut):
    t = Dut(dut)
    await t.start()
    n = min(DEPTH + 3, 80)
    sent = [t.rng.randrange(256) for _ in range(n)]
    for b in sent:
        await t.send(b)
    st = await t.reg(0x4)
    assert st & 0xFF == min(n, DEPTH) and (st >> 8) & 1 == (1 if n > DEPTH else 0), hex(st)
    assert await t.drain() == sent[:DEPTH]
    await t.reg(0x4, 1 << 8)
    assert (await t.reg(0x4)) >> 8 & 1 == 0
    await t.send(0x5A)
    assert await t.drain() == [0x5A]


@cocotb.test()
async def ps05_frames(dut):
    t = Dut(dut)
    await t.start()
    active = P.get("VSYNC_ACTIVE", 0)
    dut.vsync_i.value = 1 - active
    await t.wait(5)
    f0 = await t.reg(0x8)
    n = t.rng.randint(5, 12)
    for _ in range(n):
        await t.wait(t.rng.randint(4, 30))
        dut.vsync_i.value = active
        await t.wait(t.rng.randint(3, 20))
        dut.vsync_i.value = 1 - active
    await t.wait(5)
    assert (await t.reg(0x8)) - f0 == n


CONFIGS = {
    "fast": dict(DEPTH=16, FILT=2, TIMEOUT=200),
    "fast_d64": dict(DEPTH=64, FILT=3, TIMEOUT=300, SYNC=3),
    "vs_high": dict(DEPTH=16, FILT=2, TIMEOUT=200, VSYNC_ACTIVE=1),
    "board": dict(FILT=8, TIMEOUT=2000),       # board filter, shorter timeout (sim time)
}


@pytest.mark.parametrize("cfg", list(CONFIGS))
def test_ps2(cfg):
    import runner
    runner.run("chs_ps2", "test_ps2", params=CONFIGS[cfg], tag=f"ps2_{cfg}",
               sources=[ROOT / "fpga" / "periph" / "chs_ps2.sv"])
