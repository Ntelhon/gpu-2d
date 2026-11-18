# SPDX-License-Identifier: Apache-2.0
"""cocotb tests of the dual-clock FIFO rtl/cdc/g2d_cdc_fifo (verification-plan.md CDC-01..03).

Two independent clocks per run: random periods (ps resolution, so the ratio is not a small fraction), a random
phase, and in the jitter configuration a per-half-period random variation. The jitter build also defines
G2D_CDC_JITTER, which makes every synchronizer take a random number of cycles (rtl/cdc/g2d_sync.sv).

  CDC-01 cdc_random      random valid / ready on both sides; order, no loss, no duplication, data held while
                         stalled; the source holds valid and data until ready (AXI rule)
  CDC-02 cdc_throughput  both sides always ready: words per cycle of the slower clock (>= 0.95 at the
                         default memory depth 8 with 2 stages, no jitter); first-word latency bound
  CDC-03 cdc_reset       both resets asserted together at random points mid-stream, released each in its own
                         domain at a random later edge: the FIFO is empty afterwards and streams again
"""

import os
import random
from collections import deque
from pathlib import Path

import cocotb
import pytest
from cocotb.triggers import FallingEdge, RisingEdge, Timer

ROOT = Path(__file__).resolve().parents[2]


def _rng():
    return random.Random(int(os.environ.get("SEED", "1")) * 7919 + hash(os.environ.get("G2D_COV_NAME", "")) % 1000)


def _full():
    return os.environ.get("G2D_FULL") == "1"


PERIODS = {"src": 10000, "dst": 10000}     # ps; the clocks read it every half period, so a Bench can change it


async def clock(sig, name, rng, jitter):
    """Free-running clock with period PERIODS[name]; with jitter each half period varies by up to +-10 %."""
    sig.value = 0
    while True:
        half = PERIODS[name] // 2
        h = half if not jitter else max(1, int(half * (1 + rng.uniform(-0.1, 0.1))))
        await Timer(h, unit="ps")
        sig.value = 1
        h = half if not jitter else max(1, int(half * (1 + rng.uniform(-0.1, 0.1))))
        await Timer(h, unit="ps")
        sig.value = 0


class Bench:
    def __init__(self, dut, rng, periods=None):
        self.dut = dut
        self.rng = rng
        self.jitter = os.environ.get("G2D_JITTER") == "1"
        self.depth = 1 << int(os.environ["G2D_P_LOG_DEPTH"])
        self.stages = int(os.environ["G2D_P_SYNC_STAGES"])
        self.width = len(dut.src_data_i)
        if periods is None:
            periods = (rng.randint(2000, 20000), rng.randint(2000, 20000))
        self.ps, self.pd = periods
        PERIODS["src"], PERIODS["dst"] = periods
        self.model = deque()
        self.moved = 0
        self.p_in = 0.5
        self.p_out = 0.5
        self.in_reset = False
        dut._log.info(f"src {self.ps} ps, dst {self.pd} ps, depth {self.depth}, stages {self.stages}, "
                      f"jitter {self.jitter}")

    async def start(self):
        dut = self.dut
        for n in ("src_valid_i", "src_data_i", "dst_ready_i"):
            getattr(dut, n).value = 0
        dut.src_rst_ni.value = 1
        dut.dst_rst_ni.value = 1
        await Timer(1, unit="ns")
        dut.src_rst_ni.value = 0
        dut.dst_rst_ni.value = 0
        cocotb.start_soon(clock(dut.src_clk_i, "src", random.Random(self.rng.getrandbits(32)), self.jitter))
        await Timer(self.rng.randint(0, self.pd), unit="ps")     # random phase
        cocotb.start_soon(clock(dut.dst_clk_i, "dst", random.Random(self.rng.getrandbits(32)), self.jitter))
        await self.release(3)

    async def release(self, edges):
        """Release each reset synchronously to its own clock (at a falling edge, after `edges` rising edges)."""
        async def rel(clk, rst):
            for _ in range(edges + self.rng.randint(0, 3)):
                await RisingEdge(clk)
            await FallingEdge(clk)
            rst.value = 1
        a = cocotb.start_soon(rel(self.dut.src_clk_i, self.dut.src_rst_ni))
        b = cocotb.start_soon(rel(self.dut.dst_clk_i, self.dut.dst_rst_ni))
        await a
        await b

    async def source(self, count, stop):
        """Push `count` random words (AXI source: valid and data held until ready)."""
        dut = self.dut
        sent = 0
        valid, data = 0, 0
        while sent < count and not stop[0]:
            dut.src_valid_i.value = valid
            dut.src_data_i.value = data
            await RisingEdge(dut.src_clk_i)
            if self.in_reset:
                valid = 0
                continue
            if valid and int(dut.src_ready_o.value):
                self.model.append(data)
                sent += 1
                valid = 0
            if not valid and self.rng.random() < self.p_in:
                valid, data = 1, self.rng.getrandbits(self.width)
        # finish the word in flight (AXI: a valid may not be withdrawn)
        while valid and not self.in_reset:
            dut.src_valid_i.value = valid
            dut.src_data_i.value = data
            await RisingEdge(dut.src_clk_i)
            if int(dut.src_ready_o.value):
                self.model.append(data)
                valid = 0
        dut.src_valid_i.value = 0
        return sent

    async def sink(self, stop):
        dut = self.dut
        held = None
        while not stop[0]:
            ready = int(self.rng.random() < self.p_out)
            dut.dst_ready_i.value = ready
            await RisingEdge(dut.dst_clk_i)
            if self.in_reset:
                held = None
                continue
            if int(dut.dst_valid_o.value):
                d = int(dut.dst_data_o.value)
                assert self.model, "dst_valid with nothing sent"
                assert d == self.model[0], f"got {d:x}, expected {self.model[0]:x}"
                if held is not None:
                    assert d == held, "data changed while stalled"
                if ready:
                    self.model.popleft()
                    self.moved += 1
                    held = None
                else:
                    held = d
            else:
                assert held is None, "valid dropped while stalled"
        dut.dst_ready_i.value = 0

    async def drain(self, limit_cycles=2000):
        self.p_out = 1.0
        for _ in range(limit_cycles):
            if not self.model:
                break
            await RisingEdge(self.dut.dst_clk_i)
        assert not self.model, f"FIFO did not drain: {len(self.model)} words left"
        # a few more cycles: nothing may appear
        for _ in range(4 * self.stages + 8):
            await RisingEdge(self.dut.dst_clk_i)
            assert not int(self.dut.dst_valid_o.value), "extra word after drain"


@cocotb.test()
async def cdc_random(dut):
    rng = _rng()
    runs = 6 if _full() else 2
    for r in range(runs):
        b = Bench(dut, rng)
        if r == 0:
            await b.start()
        b.p_in, b.p_out = rng.choice([(0.5, 0.5), (0.9, 0.2), (0.2, 0.9), (1.0, 1.0), (0.7, 0.7)])
        stop = [False]
        sink = cocotb.start_soon(b.sink(stop))
        await b.source(4000 if _full() else 600, [False])
        await b.drain()
        stop[0] = True
        await sink
        dut._log.info(f"run {r}: moved {b.moved}")
        assert b.moved > 0


@cocotb.test()
async def cdc_throughput(dut):
    rng = _rng()
    jitter = os.environ.get("G2D_JITTER") == "1"
    ratios = [(5000, 5000), (5000, 7130), (7130, 5000), (3000, 10000), (10000, 3000)]
    first = True
    for ps, pd in ratios:
        b = Bench(dut, rng, (ps, pd))
        if first:
            await b.start()
            first = False
        b.p_in = b.p_out = 1.0
        stop = [False]
        n = 2000 if _full() else 400
        sink = cocotb.start_soon(b.sink(stop))
        t0 = cocotb.utils.get_sim_time("ps")
        await b.source(n, [False])
        await b.drain()
        t1 = cocotb.utils.get_sim_time("ps")
        stop[0] = True
        await sink
        slow = max(ps, pd)
        rate = n / ((t1 - t0) / slow)
        dut._log.info(f"src {ps} dst {pd}: {rate:.3f} words per slower-clock cycle")
        # >= 2 * (SYNC_STAGES + 2) entries cover the pointer round trip (arch.md §2.1)
        if b.depth >= 2 * (b.stages + 2) and not jitter:
            assert rate >= 0.90, f"throughput {rate:.3f} < 0.90 at depth {b.depth}"
    # first-word latency: one source cycle + SYNC_STAGES (+2) destination cycles, no jitter
    if not jitter:
        b = Bench(dut, rng, (5000, 7130))
        await RisingEdge(dut.src_clk_i)
        dut.dst_ready_i.value = 1
        dut.src_valid_i.value = 1
        dut.src_data_i.value = 1
        await RisingEdge(dut.src_clk_i)
        dut.src_valid_i.value = 0
        t0 = cocotb.utils.get_sim_time("ps")
        n = 0
        while not int(dut.dst_valid_o.value):
            await RisingEdge(dut.dst_clk_i)
            n += 1
        lat = cocotb.utils.get_sim_time("ps") - t0
        bound = b.ps + (b.stages + 2) * b.pd
        dut._log.info(f"first-word latency {lat} ps ({n} dst edges), bound {bound} ps")
        assert lat <= bound
        await RisingEdge(dut.dst_clk_i)


@cocotb.test()
async def cdc_reset(dut):
    rng = _rng()
    b = Bench(dut, rng)
    await b.start()
    for k in range(5 if _full() else 2):
        b.p_in, b.p_out = 0.7, 0.4
        stop = [False]
        sink = cocotb.start_soon(b.sink(stop))
        src_stop = [False]
        src = cocotb.start_soon(b.source(10 ** 6, src_stop))
        await Timer(rng.randint(20, 400) * max(b.ps, b.pd) // 10, unit="ps")
        # both resets together, mid-stream
        b.in_reset = True
        dut.src_rst_ni.value = 0
        dut.dst_rst_ni.value = 0
        dut.src_valid_i.value = 0
        src_stop[0] = True
        stop[0] = True
        await Timer(1, unit="ps")
        assert not int(dut.dst_valid_o.value), "dst_valid during reset"
        await src
        await sink
        b.model.clear()
        await b.release(2)
        b.in_reset = False
        # empty after the reset, then a normal stream
        for _ in range(4 * b.stages + 4):
            await RisingEdge(dut.dst_clk_i)
            assert not int(dut.dst_valid_o.value), "word after reset"
        assert int(dut.src_ready_o.value), "not ready after reset"
        stop = [False]
        sink = cocotb.start_soon(b.sink(stop))
        await b.source(200, [False])
        await b.drain()
        stop[0] = True
        await sink
        dut._log.info(f"reset {k}: ok")


# ------------------------------------------------------------------------------------------------
# pytest entry points
# ------------------------------------------------------------------------------------------------

CONFIGS = {
    # tag: (WIDTH, LOG_DEPTH, SYNC_STAGES, jitter)
    "cfg":     (35, 1, 2, False),     # config-port default (depth 2)
    "mem":     (70, 3, 2, False),     # memory-port default (depth 8)
    "mem3":    (70, 3, 3, False),     # ASIC-style 3 stages
    "jit":     (16, 3, 2, True),      # random synchronizer delay + clock jitter
    "jit_d2":  (16, 1, 3, True),
}


@pytest.mark.parametrize("cfg", list(CONFIGS))
def test_cdc_fifo(cfg):
    import runner
    width, log_depth, stages, jitter = CONFIGS[cfg]
    params = {"WIDTH": width, "LOG_DEPTH": log_depth, "SYNC_STAGES": stages}
    runner.run("g2d_cdc_fifo", "test_cdc", params=params, tag=cfg,
               testcase=["cdc_random", "cdc_throughput", "cdc_reset"],
               extra_env={"G2D_JITTER": "1" if jitter else "0"},
               sources=sorted((ROOT / "rtl/cdc").glob("*.sv")),
               defines={"G2D_CDC_JITTER": 1} if jitter else None)
