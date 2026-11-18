# SPDX-License-Identifier: Apache-2.0
"""cocotb tests of the configuration-side bus adapters rtl/adapt/g2d_<bus>2axil (v1.9, F-6, verification-plan.md
ADP-01..03). Each adapter is driven by a bus-functional model of its bus (dv/common/bus_bfm.py) and answers
through the AXI4-Lite slave model with its protocol monitor (dv/common/axil_slave.py). Checks:
- every bus access reaches AXI4-Lite exactly once, in order, with the right address, data and byte strobes;
- read data and error responses on the bus equal the slave model's (bit-exact), writes to error words change nothing;
- protocol: the slave model's AXI4-Lite rules every cycle, the BFM's bus rules (bus_bfm.py), one access in flight.
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

from axil_slave import AxilSlave
from bus_bfm import BFMS

SEED = int(os.environ.get("SEED", "1"))
ADAPTER = os.environ.get("G2D_ADAPTER", "apb")
QUICK = os.environ.get("G2D_QUICK") == "1"


async def setup(dut, rng, **kw):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    bfm = BFMS[ADAPTER](dut, dut.clk, rng)
    slv = AxilSlave(dut, "m_axil", dut.clk, random.Random(rng.getrandbits(32)), rst_n=dut.rst_n, **kw)
    dut.rst_n.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    cocotb.start_soon(slv.run())
    return bfm, slv


async def random_accesses(bfm, slv, rng, n, err_words):
    ref = {}
    exp_log = []
    for _ in range(n):
        waddr = rng.choice([rng.randrange(1024), rng.choice(sorted(err_words)) if err_words else 0, rng.randrange(1 << 28)])
        addr = (waddr << 2) & 0xFFFFFFFC
        err = (addr >> 2) in err_words
        if rng.random() < 0.5:
            data = rng.getrandbits(32)
            strb = 0xF if rng.random() < 0.6 or not bfm.partial_writes else bfm.random_strb(rng, addr)
            got_err = await bfm.write(addr, data, strb)
            assert got_err == err, (hex(addr), got_err, err)
            exp_log.append(("W", addr, data, strb, 2 if err else 0))
            if not err:
                old = ref.get(addr >> 2, slv.read_word(addr >> 2))
                m = sum(0xFF << (8 * i) for i in range(4) if (strb >> i) & 1)
                ref[addr >> 2] = (old & ~m) | (data & m)
        else:
            got, got_err = await bfm.read(addr)
            exp = 0 if err else ref.get(addr >> 2, slv.read_word(addr >> 2))
            assert got_err == err, (hex(addr), got_err, err)
            if not err:
                assert got == exp, (hex(addr), hex(got), hex(exp))
            exp_log.append(("R", addr, exp, 0, 2 if err else 0))
        for _ in range(rng.choice([0, 0, 0, 1, 3])):
            await RisingEdge(bfm.clk)
    return exp_log


def compare_log(slv, exp_log, start=0):
    got = slv.log[start:]
    assert len(got) == len(exp_log), (len(got), len(exp_log))
    for i, (g, e) in enumerate(zip(got, exp_log)):
        # reads: data is the slave's; writes: compare all fields
        assert g[0] == e[0] and g[1] == e[1] and g[4] == e[4], (i, g, e)
        if g[0] == "W":
            assert g[2] == e[2] and g[3] == e[3], (i, g, e)


@cocotb.test()
async def adp01_random(dut):
    """Random reads / writes with random strobes, address bits and error words, ready / latency profiles."""
    rng = random.Random(SEED + 1)
    err_words = {rng.randrange(1024) for _ in range(20)}
    bfm, slv = await setup(dut, rng, err_words=err_words)
    start = 0
    for prof in [(1.0, (0, 0)), (0.5, (0, 4)), (0.2, (5, 30)), (0.8, (0, 1))]:
        slv.p_ready, slv.lat = prof
        exp = await random_accesses(bfm, slv, rng, 60 if QUICK else 250, err_words)
        compare_log(slv, exp, start)
        start = len(slv.log)
        assert slv.idle()


@cocotb.test()
async def adp02_back_to_back(dut):
    """No idle cycles between accesses (the bus's fastest legal sequence) with an always-ready slave."""
    rng = random.Random(SEED + 2)
    bfm, slv = await setup(dut, rng, p_ready=1.0, lat=(0, 0))
    bfm.back_to_back = True
    ref_log = []
    for i in range(100):
        a = (i * 4) & 0xFFC
        await bfm.write(a, i * 0x01010101 & 0xFFFFFFFF, 0xF)
        ref_log.append(("W", a, i * 0x01010101 & 0xFFFFFFFF, 0xF, 0))
        d, e = await bfm.read(a)
        assert (d, e) == (i * 0x01010101 & 0xFFFFFFFF, False), (i, hex(d), e)
        ref_log.append(("R", a, d, 0, 0))
    compare_log(slv, ref_log)


@cocotb.test()
async def adp03_reset_mid_access(dut):
    """Reset while an access waits for its response: afterwards the adapter is idle and works again."""
    rng = random.Random(SEED + 3)
    bfm, slv = await setup(dut, rng, p_ready=1.0, lat=(40, 40))
    cocotb.start_soon(bfm.write(0x40, 0x1234, 0xF))
    for _ in range(10):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 0
    bfm.abort()
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    slv.aw.clear(), slv.w.clear(), slv.b.clear(), slv.r.clear()
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    slv.lat = (0, 3)
    await bfm.write(0x80, 0xCAFEF00D, 0xF)
    d, e = await bfm.read(0x80)
    assert (d, e) == (0xCAFEF00D, False), (hex(d), e)


@cocotb.test()
async def adp04_bus_errors(dut):
    """Bus-specific error and abort cases: AHB transfer wider than 32 bits / unaligned (two-cycle ERROR, no AXI4-Lite
    access); TL-UL opcodes outside TL-UL (error response, no access); Wishbone cycle dropped before ack (no ack
    later, the next access is exact). Other buses: nothing bus-specific (passes)."""
    rng = random.Random(SEED + 4)
    bfm, slv = await setup(dut, rng, p_ready=0.7, lat=(0, 8))
    if ADAPTER == "ahb":
        for size, off in [(3, 0), (2, 1), (2, 2), (1, 1), (4, 0), (7, 0)]:
            n = len(slv.log)
            _, err = await bfm.transfer(rng.random() < 0.5, 0x100, 0x55AA55AA, size, off)
            assert err and len(slv.log) == n, (size, off, err, slv.log[n:])
    elif ADAPTER == "tlul":
        for op in (2, 3, 5, 6, 7):
            n = len(slv.log)
            _, err = await bfm.message(op, 0x100, 0x1234, 0xF)
            assert err and len(slv.log) == n, (op, err)
    elif ADAPTER == "wb":
        d = dut
        for _ in range(20):
            d.wb_cyc.value, d.wb_stb.value, d.wb_we.value, d.wb_adr.value, d.wb_sel.value = 1, 1, 0, 0x200, 0xF
            while True:
                await RisingEdge(dut.clk)
                if not int(d.wb_stall.value):
                    break
            d.wb_stb.value = 0
            for _ in range(rng.randint(0, 3)):
                await RisingEdge(dut.clk)
            d.wb_cyc.value = 0
            for _ in range(30):
                await RisingEdge(dut.clk)
                assert not int(d.wb_ack.value) and not int(d.wb_err.value), "ack after the cycle was dropped"
    # the adapter works normally afterwards
    v = rng.getrandbits(32)
    assert await bfm.write(0x300, v, 0xF) is False
    assert await bfm.read(0x300) == (v, False)


# ------------------------------------------------------------------------------------------------

import pytest  # noqa: E402

ADAPTERS = {
    "apb": "g2d_apb2axil",
    "ahb": "g2d_ahb2axil",
    "obi": "g2d_obi2axil",
    "regintf": "g2d_regintf2axil",
    "wb": "g2d_wb2axil",
    "tlul": "g2d_tlul2axil",
}


@pytest.mark.parametrize("adapter", list(ADAPTERS))
def test_adapt(adapter):
    import runner
    runner.run(f"{ADAPTERS[adapter]}_tb", "test_adapt", params={}, tag=adapter,
               extra_env={"G2D_ADAPTER": adapter},
               loops={"hready_i": "hreadyout_o"} if adapter == "ahb" else None)   # single-subordinate AHB-Lite
