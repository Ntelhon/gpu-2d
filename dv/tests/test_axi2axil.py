# SPDX-License-Identifier: Apache-2.0
"""cocotb tests of the AXI4-to-AXI4-Lite configuration adapter rtl/adapt/g2d_axi2axil (v1.9, F-6, verification-plan.md
ADP-05..07).

An AXI4 burst master in this file drives the slave port with random VALID / READY timing; the AXI4-Lite side is
the slave model of dv/common/axil_slave.py. The reference is an independent Python model of the AXI4 beat address
rules (AMBA AXI4 A3.4: INCR / FIXED / WRAP, narrow transfers, unaligned starts) and of the adapter's word split
(spec in the RTL header): it predicts every AXI4-Lite word access (address, data, strobes) in order, and the read
data and responses on AXI4. Checks: the AXI4-Lite access log equals the prediction; B / R responses, IDs, RLAST
and read data bit-exact; AXI4 and AXI4-Lite handshake rules every cycle.
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

from axil_slave import AxilSlave

P = {k[6:]: int(v) for k, v in os.environ.items() if k.startswith("G2D_P_")}
DATA_W = P.get("DATA_W", 64)
ADDR_W = P.get("ADDR_W", 32)
ID_W = P.get("ID_W", 4)
DB = DATA_W // 8
NW = DATA_W // 32
SEED = int(os.environ.get("SEED", "1"))
QUICK = os.environ.get("G2D_QUICK") == "1"


class AxiProtocolError(Exception):
    pass


# ---------------------------------------------------------------- reference model

def beat_addrs(addr, size, length, burst):
    """AXI4 A3.4.1 address of every beat."""
    nbytes = 1 << size
    out = [addr]
    aligned = addr & ~(nbytes - 1)
    wrap_len = nbytes * (length + 1)
    wrap_lo = addr & ~(wrap_len - 1)
    a = addr
    for _ in range(length):
        if burst == 0:      # FIXED
            nxt = a
        else:
            nxt = (a & ~(nbytes - 1)) + nbytes
            if burst == 2 and nxt >= wrap_lo + wrap_len:
                nxt = wrap_lo
        out.append(nxt)
        a = nxt
    del aligned
    return out


def active_lanes(addr, size):
    """Byte lanes a beat at addr with transfer size uses (A3.4.3)."""
    nbytes = 1 << size
    lo = addr % DB
    hi = (addr & ~(nbytes - 1)) % DB + nbytes - 1
    return range(lo, hi + 1)


def write_words(beat_addr, strb):
    """The adapter's word accesses of one write beat: (word address, strobe nibble) for non-zero nibbles."""
    base = beat_addr & ~(DB - 1) & 0xFFFFFFFF
    return [((base + 4 * w) & 0xFFFFFFFF, (strb >> (4 * w)) & 0xF) for w in range(NW) if (strb >> (4 * w)) & 0xF]


def read_words(beat_addr, size):
    lanes = active_lanes(beat_addr, size)
    base = beat_addr & ~(DB - 1) & 0xFFFFFFFF
    return [((base + 4 * w) & 0xFFFFFFFF) for w in range(lanes[0] // 4, lanes[-1] // 4 + 1)]


# ---------------------------------------------------------------- AXI4 master BFM

class Axi4Master:
    def __init__(self, dut, rng):
        self.d, self.rng = dut, rng
        self.p_valid = 0.7
        self.p_ready = 0.7
        self.lock = None          # None: random AxLOCK
        for s in ("awvalid", "wvalid", "arvalid", "bready", "rready", "awid", "awaddr", "awlen", "awsize",
                  "awburst", "awlock", "awcache", "awprot", "awqos", "awregion", "wdata", "wstrb", "wlast", "arid",
                  "araddr", "arlen", "arsize", "arburst", "arlock", "arcache", "arprot", "arqos", "arregion"):
            getattr(dut, f"s_axi_{s}").value = 0

    def s(self, n):
        return getattr(self.d, f"s_axi_{n}")

    async def _addr(self, ch, aid, addr, length, size, burst):
        r = self.rng
        while r.random() > self.p_valid:
            await RisingEdge(self.d.clk)
        for f, v in (("id", aid), ("addr", addr), ("len", length), ("size", size), ("burst", burst),
                     ("lock", r.randrange(2) if self.lock is None else self.lock), ("cache", r.randrange(16)), ("prot", r.randrange(8)),
                     ("qos", r.randrange(16)), ("region", r.randrange(16))):
            self.s(f"{ch}{f}").value = v
        self.s(f"{ch}valid").value = 1
        while True:
            await RisingEdge(self.d.clk)
            if int(self.s(f"{ch}ready").value):
                break
        self.s(f"{ch}valid").value = 0

    async def write(self, aid, addr, size, length, burst, beats):
        """beats: [(data, strb)]; returns bresp."""
        aw = cocotb.start_soon(self._addr("aw", aid, addr, length, size, burst))
        for i, (data, strb) in enumerate(beats):
            while self.rng.random() > self.p_valid:
                self.s("wvalid").value = 0
                await RisingEdge(self.d.clk)
            self.s("wdata").value = data
            self.s("wstrb").value = strb
            self.s("wlast").value = int(i == length)
            self.s("wvalid").value = 1
            while True:
                await RisingEdge(self.d.clk)
                if int(self.s("wready").value):
                    break
            self.s("wvalid").value = 0
        await aw
        while True:
            br = int(self.rng.random() < self.p_ready)
            self.s("bready").value = br
            await RisingEdge(self.d.clk)
            if br and int(self.s("bvalid").value):
                if int(self.s("bid").value) != aid:
                    raise AxiProtocolError(f"BID {int(self.s('bid').value)} != {aid}")
                resp = int(self.s("bresp").value)
                self.s("bready").value = 0
                return resp

    async def read(self, aid, addr, size, length, burst):
        """returns [(data, resp)] per beat."""
        await self._addr("ar", aid, addr, length, size, burst)
        out = []
        held = None
        while len(out) <= length:
            rr = int(self.rng.random() < self.p_ready)
            self.s("rready").value = rr
            await RisingEdge(self.d.clk)
            if int(self.s("rvalid").value):
                cur = (int(self.s("rid").value), int(self.s("rdata").value), int(self.s("rresp").value),
                       int(self.s("rlast").value))
                if held is not None and cur != held:
                    raise AxiProtocolError(f"R changed while waiting: {held} -> {cur}")
                held = cur
                if rr:
                    rid, data, resp, last = cur
                    if rid != aid:
                        raise AxiProtocolError(f"RID {rid} != {aid}")
                    if last != int(len(out) == length):
                        raise AxiProtocolError(f"RLAST {last} on beat {len(out)} of {length + 1}")
                    out.append((data, resp))
                    held = None
            elif held is not None:
                raise AxiProtocolError("RVALID dropped before RREADY")
        self.s("rready").value = 0
        return out


# ---------------------------------------------------------------- bench

async def setup(dut, rng, **kw):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    m = Axi4Master(dut, rng)
    slv = AxilSlave(dut, "m_axil", dut.clk, random.Random(rng.getrandbits(32)), rst_n=dut.rst_n, **kw)
    dut.rst_n.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    cocotb.start_soon(slv.run())
    return m, slv


def random_burst(rng):
    size = rng.choice([s for s in range(DB.bit_length()) if (1 << s) <= DB] + [DB.bit_length() - 1] * 2)
    burst = rng.choice([0, 1, 1, 2])
    if burst == 2:
        length = rng.choice([1, 3, 7, 15])
        addr = rng.randrange(1 << 12) & ~((1 << size) - 1)      # WRAP: aligned start
    else:
        length = rng.choice([0, 0, 1, 2, 3, rng.randrange(16)])
        addr = rng.randrange(1 << 12)
    addr |= rng.choice([0, rng.randrange(1 << 20) << 12]) & ((1 << min(ADDR_W, 32)) - 1)
    if burst == 1:     # INCR bursts never cross a 4 KB boundary (A3.4.1)
        span = ((addr & ~((1 << size) - 1)) % 4096) + (length + 1) * (1 << size)
        if span > 4096:
            addr = (addr & ~0xFFF) | ((addr % (1 << size)))
    if ADDR_W > 32:
        addr |= rng.randrange(1 << (ADDR_W - 32)) << 32
    return addr, size, length, burst


def write_strb(rng, beat_addr, size):
    lanes = list(active_lanes(beat_addr, size))
    s = 0
    for ln in lanes:
        if rng.random() < 0.85:
            s |= 1 << ln
    return s


async def one_write(m, slv, rng, ref, err_words, exp_log):
    addr, size, length, burst = random_burst(rng)
    aid = rng.randrange(1 << ID_W)
    beats, err = [], False
    for ba in beat_addrs(addr, size, length, burst):
        data, strb = rng.getrandbits(DATA_W), write_strb(rng, ba, size)
        beats.append((data, strb))
        for wa, nib in write_words(ba, strb):
            word = (data >> (32 * ((wa % DB) // 4))) & 0xFFFFFFFF
            e = (wa >> 2) in err_words
            err |= e
            exp_log.append(("W", wa, word, nib, 2 if e else 0))
            if not e:
                old = ref.get(wa >> 2, slv.read_word(wa >> 2))
                msk = sum(0xFF << (8 * i) for i in range(4) if (nib >> i) & 1)
                ref[wa >> 2] = (old & ~msk) | (word & msk)
    resp = await m.write(aid, addr, size, length, burst, beats)
    assert resp == (2 if err else 0), (hex(addr), size, length, burst, resp, err)


async def one_read(m, slv, rng, ref, err_words, exp_log):
    addr, size, length, burst = random_burst(rng)
    aid = rng.randrange(1 << ID_W)
    exp = []
    for ba in beat_addrs(addr, size, length, burst):
        data, err = 0, False
        for wa in read_words(ba, size):
            e = (wa >> 2) in err_words
            err |= e
            v = 0 if e else ref.get(wa >> 2, slv.read_word(wa >> 2))
            exp_log.append(("R", wa, v, 0, 2 if e else 0))
            data |= v << (32 * ((wa % DB) // 4))
        exp.append((data, 2 if err else 0))
    got = await m.read(aid, addr, size, length, burst)
    for i, (g, e) in enumerate(zip(got, exp)):
        assert g[1] == e[1], (i, hex(addr), size, length, burst, g, e)
        if e[1] == 0:
            assert g[0] == e[0], (i, hex(addr), size, length, burst, hex(g[0]), hex(e[0]))


def compare_log(slv, exp_log, start=0):
    got = slv.log[start:]
    assert len(got) == len(exp_log), (len(got), len(exp_log))
    for i, (g, e) in enumerate(zip(got, exp_log)):
        assert g[0] == e[0] and g[1] == e[1] and g[4] == e[4], (i, g, e)
        if g[0] == "W":
            assert g[2] == e[2] and g[3] == e[3], (i, g, e)
        elif g[4] == 0:
            assert g[2] == e[2], (i, g, e)


@cocotb.test()
async def adp05_bursts(dut):
    """Random INCR / FIXED / WRAP bursts, narrow and unaligned transfers, random strobes, IDs, error words; the
    AXI4-Lite word accesses in order and every response / data beat bit-exact."""
    rng = random.Random(SEED + 5)
    err_words = {rng.randrange(1 << 10) for _ in range(30)}
    m, slv = await setup(dut, rng, err_words=err_words)
    ref, start = {}, 0
    for prof in [(1.0, 1.0, 1.0, (0, 0)), (0.6, 0.6, 0.5, (0, 5)), (0.3, 0.9, 0.2, (3, 20))]:
        m.p_valid, m.p_ready, slv.p_ready, slv.lat = prof
        exp_log = []
        for _ in range(40 if QUICK else 150):
            if rng.random() < 0.5:
                await one_write(m, slv, rng, ref, err_words, exp_log)
            else:
                await one_read(m, slv, rng, ref, err_words, exp_log)
        compare_log(slv, exp_log, start)
        start = len(slv.log)


@cocotb.test()
async def adp06_concurrent_rw(dut):
    """A writer and a reader at the same time on disjoint regions (the adapter alternates bursts): all data and
    responses exact, nothing lost."""
    rng = random.Random(SEED + 6)
    m, slv = await setup(dut, rng, p_ready=0.7, lat=(0, 4))
    # writes go to 0x0..0x7FF, reads from 0x800.. (preset): disjoint words
    for w in range(0x200, 0x400):
        slv.mem[w] = rng.getrandbits(32)
    wm = Axi4Master(dut, random.Random(rng.getrandbits(32)))
    exp_w = {}

    async def writer():
        for i in range(30):
            beats = [(rng.getrandbits(DATA_W), (1 << DB) - 1) for _ in range(4)]
            a = (i * 4 * DB) % 0x800
            assert await wm.write(1 % (1 << ID_W), a, DB.bit_length() - 1, 3, 1, beats) == 0
            for b, (data, _) in enumerate(beats):
                for w in range(NW):
                    exp_w[(a + b * DB + 4 * w) >> 2] = (data >> (32 * w)) & 0xFFFFFFFF

    async def reader():
        for i in range(30):
            a = 0x800 + (i * 2 * DB) % 0x800
            got = await m.read(2 % (1 << ID_W), a, DB.bit_length() - 1, 1, 1)
            for b, (data, resp) in enumerate(got):
                assert resp == 0
                for w in range(NW):
                    assert (data >> (32 * w)) & 0xFFFFFFFF == slv.mem[(a + b * DB + 4 * w) >> 2], (i, b, w)

    tw = cocotb.start_soon(writer())
    await reader()
    await tw
    for k, v in exp_w.items():
        assert slv.mem[k] == v, (hex(k), hex(slv.mem[k]), hex(v))


@cocotb.test()
async def adp07_exclusive_okay(dut):
    """Exclusive accesses (AxLOCK = 1) get OKAY (exclusive failure, AXI4 A7.2) and act as normal accesses."""
    rng = random.Random(SEED + 7)
    m, slv = await setup(dut, rng, p_ready=1.0, lat=(0, 0))
    m.lock = 1
    for i in range(10):
        a = 0x40 + 8 * i
        resp = await m.write(0, a, 2, 0, 1, [((0xA5A50000 + i) << (8 * (a % DB)) & ((1 << DATA_W) - 1), 0xF << (a % DB))])
        assert resp == 0
        got = await m.read(0, a, 2, 0, 1)
        assert got[0][1] == 0
        assert (got[0][0] >> (8 * (a % DB))) & 0xFFFFFFFF == 0xA5A50000 + i


# ------------------------------------------------------------------------------------------------

import pytest  # noqa: E402

CONFIGS = {
    "dw64": dict(DATA_W=64, ADDR_W=32, ID_W=4),
    "dw32": dict(DATA_W=32, ADDR_W=32, ID_W=2),
    "dw128": dict(DATA_W=128, ADDR_W=48, ID_W=6),
    "dw512": dict(DATA_W=512, ADDR_W=64, ID_W=1),
}


@pytest.mark.parametrize("cfg", list(CONFIGS))
def test_axi2axil(cfg):
    import runner
    runner.run("g2d_axi2axil_tb", "test_axi2axil", params=CONFIGS[cfg], tag=cfg,
               extra_env={"G2D_QUICK": "1" if cfg != "dw64" and os.environ.get("G2D_FULL") != "1" else "0"})
