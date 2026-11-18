# SPDX-License-Identifier: Apache-2.0
"""cocotb tests of the generic blocks in rtl/common: g2d_fifo, g2d_spill_reg, g2d_rr_arb.

Each stream block is driven with random valid/ready and checked against a Python queue (order, no loss,
no duplication, no data change while stalled). The arbiter is checked for grant stability, round-robin
order and fairness. One pytest case per block and parameter set.
"""

import os
import random
from collections import deque
from pathlib import Path

import cocotb

import clkrst
import pytest
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

ROOT = Path(__file__).resolve().parents[2]


async def reset(dut):
    """Clock and reset (dv/common/clkrst.py); every input of the block is driven to 0 before the reset is
    released (with random initial values an undriven input would act during the first cycle)."""
    inputs = [n for n in ("clr_i", "in_valid_i", "in_data_i", "out_ready_i", "req_i", "ack_i") if hasattr(dut, n)]
    await clkrst.start(dut, zero=inputs)


async def stream_check(dut, rng, cycles, p_in, p_out, has_count, depth, clear_at=None):
    """Random push/pop for `cycles`; returns the number of transferred items."""
    width = len(dut.in_data_i)
    model = deque()
    moved = 0
    in_valid, in_data = 0, 0
    last_out = None
    total = cycles + depth + 4  # the tail drains the block (no new input, always ready)
    for cyc in range(total):
        if cyc >= cycles:
            p_in, p_out = 0.0, 1.0
        # values driven now are sampled at the next rising edge
        dut.in_valid_i.value = in_valid
        dut.in_data_i.value = in_data
        out_ready = int(rng.random() < p_out)
        dut.out_ready_i.value = out_ready
        do_clear = clear_at is not None and cyc == clear_at
        dut.clr_i.value = int(do_clear)
        await RisingEdge(dut.clk_i)
        in_ready = int(dut.in_ready_o.value)
        out_valid = int(dut.out_valid_o.value)
        if has_count:
            assert int(dut.count_o.value) == len(model)
            assert in_ready == int(len(model) < depth)
        if out_valid:
            data = int(dut.out_data_o.value)
            assert model, "output valid with empty model"
            assert data == model[0], f"cycle {cyc}: got {data:x}, expected {model[0]:x}"
            if last_out is not None and not last_out[1]:
                assert data == last_out[0], "data changed while stalled"
            last_out = (data, out_ready)
        else:
            assert not model or not has_count
            last_out = None
        if do_clear:
            model.clear()
            in_valid = 0
            last_out = None
            continue
        if out_valid and out_ready:
            model.popleft()
            moved += 1
            last_out = None
        if in_valid and in_ready:
            model.append(in_data)
            in_valid = 0
        if not in_valid and rng.random() < p_in:
            in_valid, in_data = 1, rng.getrandbits(width)
    assert not model and not in_valid, "block did not drain"
    dut.in_valid_i.value = 0
    return moved


@cocotb.test()
async def fifo_random(dut):
    await reset(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")))
    depth = int(os.environ["G2D_DEPTH"])
    for p_in, p_out in [(0.9, 0.9), (0.9, 0.3), (0.3, 0.9), (1.0, 1.0), (0.5, 0.5)]:
        moved = await stream_check(dut, rng, 600, p_in, p_out, True, depth)
        assert moved > 50
    await stream_check(dut, rng, 200, 0.9, 0.1, True, depth, clear_at=100)


@cocotb.test()
async def fifo_full_throughput(dut):
    """With both sides always ready the FIFO moves one item per cycle."""
    await reset(dut)
    rng = random.Random(7)
    depth = int(os.environ["G2D_DEPTH"])
    moved = await stream_check(dut, rng, 400, 1.0, 1.0, True, depth)
    assert moved >= (395 if depth > 1 else 195)  # depth 1 has no same-cycle push into full


@cocotb.test()
async def fifo_ram_random(dut):
    """SRAM FIFO: order and no loss under random traffic, a clear, and one item per cycle when both
    sides are always ready (capacity >= DEPTH, so the queue model checks no count)."""
    await reset(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")))
    depth = int(os.environ["G2D_DEPTH"])
    for p_in, p_out in [(0.9, 0.9), (0.9, 0.3), (0.3, 0.9), (0.5, 0.5)]:
        assert await stream_check(dut, rng, 600, p_in, p_out, False, depth + 2) > 50
    await stream_check(dut, rng, 200, 0.9, 0.1, False, depth + 2, clear_at=100)
    moved = await stream_check(dut, random.Random(9), 400, 1.0, 1.0, False, depth + 2)
    assert moved >= (390 if depth > 1 else 190), moved
    # capacity: with the output blocked, at least DEPTH items are accepted
    dut.out_ready_i.value = 0
    n = 0
    for _ in range(depth + 8):
        dut.in_valid_i.value = 1
        dut.in_data_i.value = n
        await RisingEdge(dut.clk_i)
        n += int(dut.in_ready_o.value)
    dut.in_valid_i.value = 0
    assert n >= depth, n
    got = []
    dut.out_ready_i.value = 1
    for _ in range(n + 6):
        await RisingEdge(dut.clk_i)
        if int(dut.out_valid_o.value):
            got.append(int(dut.out_data_o.value))
    assert got == list(range(n)), got


async def clock_only(dut):
    """Clock for blocks without a reset (SRAM wrappers). The control inputs are driven to 0 before the first
    edge: with random initial values an undriven read and write enable could collide at that edge."""
    for n in ("we_i", "re_i", "en_i"):
        if hasattr(dut, n):
            getattr(dut, n).value = 0
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    await RisingEdge(dut.clk_i)


@cocotb.test()
async def sram_random(dut):
    """g2d_ram_1r1w / g2d_ram_sp against a Python array: random lane-masked writes and reads; read data
    appears the cycle after the read and holds until the next read. Addresses are first written in full
    (uninitialised SRAM contents are not compared)."""
    await clock_only(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")))
    sp = hasattr(dut, "en_i")
    width, depth, be_w = len(dut.wdata_i), int(os.environ["G2D_DEPTH"]), len(dut.wbe_i)
    lw = width // be_w
    mem = [rng.getrandbits(width) for _ in range(depth)]

    def drive_write(a, d, be):
        if sp:
            dut.en_i.value, dut.we_i.value, dut.addr_i.value = 1, 1, a
        else:
            dut.we_i.value, dut.waddr_i.value = 1, a
        dut.wdata_i.value, dut.wbe_i.value = d, be

    def idle():
        if sp:
            dut.en_i.value, dut.we_i.value = 0, 0
        else:
            dut.we_i.value, dut.re_i.value = 0, 0

    for a in range(depth):
        drive_write(a, mem[a], (1 << be_w) - 1)
        if not sp:
            dut.re_i.value = 0
        await RisingEdge(dut.clk_i)
    idle()
    expect = None
    reads = 0
    for _ in range(3000):
        wr = rng.random() < 0.5
        rd = (not sp or not wr) and rng.random() < 0.6
        wa, ra = rng.randrange(depth), rng.randrange(depth)
        if wr and rd and wa == ra:
            rd = False
        d, be = rng.getrandbits(width), rng.getrandbits(be_w)
        idle()
        if wr:
            drive_write(wa, d, be)
        if rd:
            if sp:
                dut.en_i.value, dut.we_i.value, dut.addr_i.value = 1, 0, ra
            else:
                dut.re_i.value, dut.raddr_i.value = 1, ra
        await RisingEdge(dut.clk_i)
        await Timer(1, unit="ns")
        if rd:
            expect = mem[ra]  # memory before this cycle's write (different address)
            reads += 1
        if expect is not None:
            assert int(dut.rdata_o.value) == expect, f"read data {int(dut.rdata_o.value):x} != {expect:x}"
        if wr:
            for l in range(be_w):
                if be >> l & 1:
                    m = ((1 << lw) - 1) << (l * lw)
                    mem[wa] = (mem[wa] & ~m) | (d & m)
    assert reads > 500


@cocotb.test()
async def spill_random(dut):
    await reset(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")))
    for p_in, p_out in [(0.9, 0.9), (0.9, 0.3), (0.3, 0.9), (1.0, 1.0)]:
        assert await stream_check(dut, rng, 600, p_in, p_out, False, 2) > 50
    assert await stream_check(dut, random.Random(3), 400, 1.0, 1.0, False, 2) >= 395
    await stream_check(dut, rng, 200, 0.9, 0.1, False, 2, clear_at=100)


@cocotb.test()
async def rr_arb(dut):
    await reset(dut)
    n = len(dut.req_i)
    rng = random.Random(int(os.environ.get("SEED", "1")))
    grants = [0] * n
    pending = None  # (idx) granted but not acked
    req = 0
    for cyc in range(3000):
        # keep requests of the pending winner up (valid stability by the requester), except that a
        # requester may withdraw (a client stopped by a halt or soft reset): it then loses the grant
        if pending is not None:
            if rng.random() < 0.1:
                req &= ~(1 << pending)
                pending = None
            else:
                req |= 1 << pending
        ack = int(rng.random() < 0.6)
        dut.req_i.value = req
        dut.ack_i.value = ack
        await RisingEdge(dut.clk_i)
        valid, idx = int(dut.valid_o.value), int(dut.idx_o.value)
        assert valid == int(req != 0 or pending is not None)
        if valid:
            assert req >> idx & 1, "grant to a non-requester"
            if pending is not None:
                assert idx == pending, "grant changed before acceptance"
            if ack:
                grants[idx] += 1
                req &= ~(1 << idx)
                pending = None
            else:
                pending = idx
        # new random requests
        req |= rng.getrandbits(n) if rng.random() < 0.5 else 0
    # all-requesting fairness: round-robin order
    await RisingEdge(dut.clk_i)
    order = []
    dut.req_i.value = (1 << n) - 1
    dut.ack_i.value = 1
    for _ in range(3 * n):
        await RisingEdge(dut.clk_i)
        order.append(int(dut.idx_o.value))
    for a, b in zip(order, order[1:]):
        assert b == (a + 1) % n, f"not round-robin: {order}"
    assert min(grants) > 0


# ------------------------------------------------------------------------------------------------
# pytest entry points
# ------------------------------------------------------------------------------------------------

def _run(top, params, testcase, extra_env=None):
    import runner
    tag = "_".join(f"{k}{v}" for k, v in params.items())
    runner.run(top, "test_common", params=params, tag=tag, testcase=testcase, extra_env=extra_env,
               sources=sorted((ROOT / "rtl/common").glob("*.sv")))


@pytest.mark.parametrize("depth", [1, 2, 3, 4, 7])
def test_fifo(depth):
    _run("g2d_fifo", {"WIDTH": 12, "DEPTH": depth}, ["fifo_random", "fifo_full_throughput"],
         {"G2D_DEPTH": str(depth)})


@pytest.mark.parametrize("depth", [1, 2, 5, 16])
def test_fifo_ram(depth):
    _run("g2d_fifo_ram", {"WIDTH": 12, "DEPTH": depth}, ["fifo_ram_random"], {"G2D_DEPTH": str(depth)})


@pytest.mark.parametrize("top,depth,be_w", [("g2d_ram_1r1w", 16, 1), ("g2d_ram_1r1w", 7, 4),
                                             ("g2d_ram_sp", 32, 8), ("g2d_ram_sp", 1, 2)])
def test_ram(top, depth, be_w):
    _run(top, {"WIDTH": 32, "DEPTH": depth, "BE_W": be_w}, ["sram_random"], {"G2D_DEPTH": str(depth)})


def test_spill_reg():
    _run("g2d_spill_reg", {"WIDTH": 16}, ["spill_random"])


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5])      # powers of two too: the priority wrap differs
def test_rr_arb(n):
    _run("g2d_rr_arb", {"N": n}, ["rr_arb"])
