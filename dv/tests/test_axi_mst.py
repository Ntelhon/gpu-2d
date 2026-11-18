# SPDX-License-Identifier: Apache-2.0
"""cocotb tests of the AXI4 master g2d_axi_mst (verification-plan.md AXI-01..06, plus stop/drain).

The bench drives N_RD read clients and the write producer from Python and attaches the AXI4 memory
model with its protocol monitor (dv/common/axi_mem.py). Checks:
- read beats: data of every requested byte (strb) against the memory, strb exactly the requested
  bytes, `last` on the final beat of each request, err flag where an error range was hit;
- writes: after the engine is idle, the whole memory equals the expected image (catches wrong strobes
  and stray writes); errors leave memory unchanged;
- protocol: monitor rules on every cycle; burst counts against the boundary split rule; outstanding limits.
"""

import os
import random
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

from axi_mem import AxiMem, ErrRange, Profile, SparseMem

P = {k[6:]: int(v) for k, v in os.environ.items() if k.startswith("G2D_P_")}
N_RD = P.get("N_RD", 3)
DATA_W = P.get("DATA_W", 64)
DB = DATA_W // 8
MAX_BURST = P.get("MAX_BURST_BYTES", 256)
RD_OUT = P.get("RD_OUTSTANDING", 16)   # v1.7 default
WR_OUT = P.get("WR_OUTSTANDING", 8)
SEED = int(os.environ.get("SEED", "1"))
QUICK = os.environ.get("G2D_QUICK") == "1"   # reduced AXI-01 sweep for the secondary configurations


def split_bursts(addr, nbytes):
    """The engine's burst split: [start, end] byte ranges, never crossing a MAX_BURST boundary."""
    out = []
    end = addr + nbytes - 1
    while addr <= end:
        e = min(end, (addr | (MAX_BURST - 1)))
        out.append((addr, e))
        addr = e + 1
    return out


class AxiTb:
    def __init__(self, dut, profile=None, rng=None):
        self.dut = dut
        self.rng = rng or random.Random(SEED)
        self.mem = SparseMem()
        self.win = (0, 0xFFFF_FFFF)
        self.axi = AxiMem(dut, "m_axi", dut.clk, self.mem, DATA_W, profile or Profile.ideal(),
                          rng=random.Random(self.rng.getrandbits(32)), max_beats=MAX_BURST // DB,
                          window=lambda lo, hi: self.win[0] <= lo and hi <= (self.win[1] | 0x3F), rst_n=dut.rst_n)
        # clients
        self.rd_reqs = [deque() for _ in range(N_RD)]
        self.rd_exp = [deque() for _ in range(N_RD)]
        self.rd_done = [0] * N_RD
        self.rd_p_ready = 1.0
        self.wr_reqs = deque()       # (addr, data, pstrb) not yet accepted
        self.wr_beats = deque()      # (data, strb) for accepted and pending requests, in order
        self.wr_pending_beats = deque()
        self.p_wr_dat = 1.0
        self.exp_mem = SparseMem()
        self.err_events = []         # (cycle, kind, addr)
        self.cycle = 0
        self.id_switches = 0
        self._last_rsp_client = None
        self.accept_new = True
        self.cur_rd = [None] * N_RD
        self.cur_wr = self.cur_beat = None

    async def start(self):
        dut = self.dut
        cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
        dut.rst_n.value = 0
        dut.clr.value = 0
        dut.stop.value = 0
        dut.win_lo.value = 0
        dut.win_hi.value = 0xFFFF_FFFF
        dut.base_hi.value = 0
        self.set_attr(0x3, 0, 0, 0x3, 0, 0)    # MEM_ATTR reset values (v1.9)
        self.set_qos(0, 0, 0, 0)                # MEM_QOS reset = off
        dut.rd_req_valid.value = 0
        dut.rd_rsp_ready.value = 0
        dut.wr_req_valid.value = 0
        dut.wr_dat_valid.value = 0
        self.axi.start()
        await Timer(35, unit="ns")
        dut.rst_n.value = 1
        await RisingEdge(dut.clk)
        cocotb.start_soon(self._drive())

    def set_attr(self, arc, arp, arq, awc, awp, awq):
        d = self.dut
        d.ar_cache.value, d.ar_prot.value, d.ar_qos.value = arc, arp, arq
        d.aw_cache.value, d.aw_prot.value, d.aw_qos.value = awc, awp, awq

    def set_qos(self, rd_max, wr_max, rd_gap, wr_gap):
        d = self.dut
        d.rd_max_out.value, d.wr_max_out.value, d.rd_gap.value, d.wr_gap.value = rd_max, wr_max, rd_gap, wr_gap

    def set_window(self, lo, hi):
        self.win = (lo & ~0x3F, hi | 0x3F)
        self.dut.win_lo.value = self.win[0]
        self.dut.win_hi.value = self.win[1]

    # ---- request generation ----
    def read(self, client, addr, nbytes):
        self.rd_reqs[client].append((addr, nbytes))

    def write(self, addr, data: bytes, pstrb=None):
        self.wr_reqs.append((addr, data, pstrb))
        a0 = addr & ~(DB - 1)
        nb = (addr + len(data) - 1) // DB - addr // DB + 1
        for k in range(nb):
            word, strb = 0, 0
            for i in range(DB):
                off = a0 + k * DB + i - addr
                if 0 <= off < len(data):
                    word |= data[off] << (8 * i)
                    if pstrb is None or pstrb[off]:
                        strb |= 1 << i
            # bytes outside the request: random data and random producer strobe (the engine must mask them)
            for i in range(DB):
                off = a0 + k * DB + i - addr
                if not 0 <= off < len(data):
                    word |= self.rng.getrandbits(8) << (8 * i)
                    strb |= self.rng.getrandbits(1) << i
            self.wr_pending_beats.append((word, strb))

    def _expect_read(self, client, addr, nbytes):
        for (s, e) in split_bursts(addr, nbytes):
            a0 = s & ~(DB - 1)
            nb = e // DB - s // DB + 1
            err = self.axi._err_for(a0, nb * DB, True)
            for k in range(nb):
                ba = a0 + k * DB
                strb = 0
                for i in range(DB):
                    if s <= ba + i <= e:
                        strb |= 1 << i
                data = int.from_bytes(self.mem.read(ba, DB), "little")
                last = (e == addr + nbytes - 1) and k == nb - 1
                self.rd_exp[client].append((data, strb, int(last), int(err != 0)))

    def _expect_write(self, addr, data, pstrb):
        for (s, e) in split_bursts(addr, len(data)):
            a0 = s & ~(DB - 1)
            nb = e // DB - s // DB + 1
            if self.axi._err_for(a0, nb * DB, False):
                continue
            if not (self.win[0] <= a0 and a0 + nb * DB - 1 <= self.win[1]):
                return  # dropped from here on
            for a in range(s, e + 1):
                if pstrb is None or pstrb[a - addr]:
                    self.exp_mem.write(a, bytes([data[a - addr]]))

    # ---- per-cycle driver ----
    async def _drive(self):
        dut = self.dut
        rng = self.rng
        cur_rd = self.cur_rd = [None] * N_RD
        cur_wr = cur_beat = None
        while True:
            self.cur_wr, self.cur_beat = cur_wr, cur_beat
            # present
            valid, addrs, lens = 0, 0, 0
            for c in range(N_RD):
                if cur_rd[c] is None and self.rd_reqs[c] and self.accept_new:
                    cur_rd[c] = self.rd_reqs[c].popleft()
                if cur_rd[c] is not None:
                    valid |= 1 << c
                    addrs |= cur_rd[c][0] << (32 * c)
                    lens |= (cur_rd[c][1] - 1) << (16 * c)
            dut.rd_req_valid.value = valid
            dut.rd_req_addr.value = addrs
            dut.rd_req_len.value = lens
            rsp_ready = sum(int(rng.random() < self.rd_p_ready) << c for c in range(N_RD))
            dut.rd_rsp_ready.value = rsp_ready
            if cur_wr is None and self.wr_reqs and self.accept_new:
                cur_wr = self.wr_reqs.popleft()
            dut.wr_req_valid.value = int(cur_wr is not None)
            if cur_wr is not None:
                dut.wr_req_addr.value = cur_wr[0]
                dut.wr_req_len.value = len(cur_wr[1]) - 1
            if cur_beat is None and self.wr_beats and rng.random() < self.p_wr_dat:
                cur_beat = self.wr_beats.popleft()
            dut.wr_dat_valid.value = int(cur_beat is not None)
            if cur_beat is not None:
                dut.wr_dat_data.value = cur_beat[0]
                dut.wr_dat_strb.value = cur_beat[1]

            self.cur_wr, self.cur_beat = cur_wr, cur_beat
            await RisingEdge(dut.clk)
            self.cycle += 1
            # sample
            rr = int(dut.rd_req_ready.value)
            for c in range(N_RD):
                if cur_rd[c] is not None and rr >> c & 1:
                    self._expect_read(c, *cur_rd[c])
                    cur_rd[c] = None
            rv = int(dut.rd_rsp_valid.value)
            hs = rv & rsp_ready
            if hs:
                assert hs & (hs - 1) == 0, "more than one client valid"
                c = hs.bit_length() - 1
                if self._last_rsp_client is not None and c != self._last_rsp_client:
                    self.id_switches += 1
                self._last_rsp_client = c
                data, strb = int(dut.rd_rsp_data.value), int(dut.rd_rsp_strb.value)
                last, err = int(dut.rd_rsp_last.value), int(dut.rd_rsp_err.value)
                assert self.rd_exp[c], f"client {c}: unexpected beat"
                edata, estrb, elast, eerr = self.rd_exp[c].popleft()
                mask = sum(0xFF << (8 * i) for i in range(DB) if estrb >> i & 1)
                assert strb == estrb, f"client {c}: strb {strb:x} != {estrb:x}"
                assert last == elast, f"client {c}: last {last} != {elast}"
                assert err == eerr, f"client {c}: err {err} != {eerr}"
                if not eerr:
                    assert data & mask == edata & mask, f"client {c}: data {data:x} != {edata:x} (strb {strb:x})"
                if last:
                    self.rd_done[c] += 1
            if cur_wr is not None and int(dut.wr_req_ready.value):
                self._expect_write(*cur_wr)
                nb = (cur_wr[0] + len(cur_wr[1]) - 1) // DB - cur_wr[0] // DB + 1
                for _ in range(nb):
                    self.wr_beats.append(self.wr_pending_beats.popleft())
                cur_wr = None
            if cur_beat is not None and int(dut.wr_dat_ready.value):
                cur_beat = None
            for kind in ("rd", "wr", "win"):
                if int(getattr(dut, f"err_{kind}").value):
                    self.err_events.append((self.cycle, kind, int(dut.err_addr.value)))
            self.cur_wr, self.cur_beat = cur_wr, cur_beat

    # ---- waiting ----
    def busy(self):
        return (any(self.rd_reqs) or any(self.rd_exp) or self.wr_reqs or self.wr_beats
                or any(c is not None for c in self.cur_rd) or self.cur_wr is not None or self.cur_beat is not None
                or not int(self.dut.rd_idle.value) or not int(self.dut.wr_idle.value) or not self.axi.idle())

    async def wait_idle(self, limit=2_000_000):
        n = 0
        while self.busy():
            await RisingEdge(self.dut.clk)
            n += 1
            assert n < limit, "timeout waiting for idle"
        for _ in range(3):
            await RisingEdge(self.dut.clk)

    def check_memory(self):
        pages = self.mem.page_bases() | self.exp_mem.page_bases()
        for base in sorted(pages):
            got, exp = self.mem.read(base, 4096), self.exp_mem.read(base, 4096)
            if got != exp:
                i = next(i for i in range(4096) if got[i] != exp[i])
                raise AssertionError(f"memory mismatch at 0x{base + i:x}: 0x{got[i]:02x} != 0x{exp[i]:02x}")

    def fill(self, addr, n):
        data = bytes(self.rng.getrandbits(8) for _ in range(n))
        self.mem.write(addr, data)
        self.exp_mem.write(addr, data)


@cocotb.test()
async def axi01_lengths_offsets(dut):
    """Writes then reads of lengths 1..LMAX at every start offset mod 64, plus long lengths."""
    tb = AxiTb(dut)
    await tb.start()
    rng = tb.rng
    lengths = list(range(1, 3 * DB + 9)) + [63, 64, 65, 127, 128, 129, 255, 256, 257, 511, 1000, 4095, 4096]
    base = 0x1000_0000
    jobs = []
    for off in (range(0, 64, 5) if QUICK else range(64)):
        for n in lengths:
            a = base + off
            jobs.append((a, n))
            base += ((off + n + 2 * MAX_BURST) // 4096 + 1) * 4096  # separate pages
    for a, n in jobs:
        pstrb = [rng.random() < 0.8 for _ in range(n)] if rng.random() < 0.3 else None
        tb.write(a, bytes(rng.getrandbits(8) for _ in range(n)), pstrb)
    await tb.wait_idle()
    tb.check_memory()
    for i, (a, n) in enumerate(jobs):
        tb.read(i % N_RD, a, n)
    await tb.wait_idle()
    exp_bursts = sum(len(split_bursts(a, n)) for a, n in jobs)
    assert tb.axi.stats["aw"] == exp_bursts and tb.axi.stats["ar"] == exp_bursts
    assert sum(tb.rd_done) == len(jobs)


@cocotb.test()
async def axi02_4k_crossing_long(dut):
    tb = AxiTb(dut)
    await tb.start()
    rng = tb.rng
    jobs = []
    for i in range(40):
        a = 0x2000_0000 + i * 0x2_0000 + rng.choice([4096 - 1, 4096 - DB, 4096 - 3, 4096 - 64, rng.randrange(4096)])
        n = rng.choice([2, DB + 1, 300, 4097, 9000, 65536, rng.randint(1, 65536)])
        jobs.append((a, n))
        tb.fill(a, n)
    for i, (a, n) in enumerate(jobs):
        tb.read(i % N_RD, a, n)
    await tb.wait_idle()
    for a, n in jobs[:20]:
        tb.write(a + 0x8000_0000, bytes(rng.getrandbits(8) for _ in range(n)))
    await tb.wait_idle()
    tb.check_memory()
    exp = sum(len(split_bursts(a, n)) for a, n in jobs)
    assert tb.axi.stats["ar"] == exp
    assert tb.axi.stats["aw"] == sum(len(split_bursts(a + 0x8000_0000, n)) for a, n in jobs[:20])


@cocotb.test()
async def axi03_random_latency_backpressure(dut):
    """Random latency up to 200 clk and back-pressure on all channels; concurrent reads and writes."""
    rng = random.Random(SEED + 3)
    tb = AxiTb(dut, profile=Profile.random(rng), rng=rng)
    await tb.start()
    for rnd in range(6):
        tb.axi.profile = Profile.random(rng)
        tb.rd_p_ready = rng.choice([1.0, 0.7, 0.3])
        tb.p_wr_dat = rng.choice([1.0, 0.6, 0.2])
        rd_base = 0x3000_0000 + rnd * 0x10_0000
        wr_base = 0x4000_0000 + rnd * 0x10_0000
        jobs = []
        for i in range(60):
            a = rd_base + rng.randrange(0x8_0000)
            n = rng.choice([rng.randint(1, 64), rng.randint(1, 600), rng.randint(1, 5000)])
            tb.fill(a, n)
            jobs.append((a, n))
        for i, (a, n) in enumerate(jobs):
            tb.read(rng.randrange(N_RD), a, n)
            if i % 2 == 0:
                tb.write(wr_base + i * 0x2000 + rng.randrange(64), bytes(rng.getrandbits(8) for _ in range(n % 4000 + 1)))
        await tb.wait_idle()
        tb.check_memory()


@cocotb.test()
async def axi04_error_responses(dut):
    rng = random.Random(SEED + 4)
    tb = AxiTb(dut, profile=Profile.random(rng), rng=rng)
    await tb.start()
    tb.axi.err_ranges = [ErrRange(0x5000_1000, 0x5000_10FF, 2), ErrRange(0x5000_3000, 0x5000_3FFF, 3, on_write=False),
                         ErrRange(0x6000_0040, 0x6000_007F, 2, on_read=False)]
    tb.fill(0x5000_0F00, 0x400)
    tb.fill(0x5000_2F00, 0x1200)
    tb.read(0, 0x5000_0F00, 0x400)      # bursts at 0x1000..0x10FF error (SLVERR)
    tb.read(1 % N_RD, 0x5000_2F00, 0x1200)     # DECERR on 0x3000..0x3FFF
    await tb.wait_idle()  # reads and writes in separate phases: one error address is reported per cycle
    tb.write(0x6000_0000, bytes(range(256)))   # one burst errors, the others land
    tb.write(0x5000_1080, bytes(16))    # write error on the read+write range
    await tb.wait_idle()
    tb.check_memory()
    kinds = {k for _, k, _ in tb.err_events}
    assert kinds == {"rd", "wr"}, kinds

    def err_bursts(addr, n, is_read):
        return {s for s, e in split_bursts(addr, n)
                if tb.axi._err_for(s & ~(DB - 1), (e // DB - s // DB + 1) * DB, is_read)}

    exp_rd = err_bursts(0x5000_0F00, 0x400, True) | err_bursts(0x5000_2F00, 0x1200, True)
    exp_wr = err_bursts(0x6000_0000, 256, False) | err_bursts(0x5000_1080, 16, False)
    assert {a for _, k, a in tb.err_events if k == "rd"} == exp_rd
    assert {a for _, k, a in tb.err_events if k == "wr"} == exp_wr
    # recovery: clear, then normal traffic is exact
    dut.clr.value = 1
    await RisingEdge(dut.clk)
    dut.clr.value = 0
    tb.axi.err_ranges = []
    tb.fill(0x5000_0000, 0x2000)
    tb.read(0, 0x5000_0000, 0x2000)
    tb.write(0x7000_0003, bytes(range(200)))
    await tb.wait_idle()
    tb.check_memory()


@cocotb.test()
async def axi05_address_window(dut):
    tb = AxiTb(dut)
    await tb.start()
    tb.set_window(0x8000_0000, 0x8000_FFFF)
    tb.fill(0x8000_0000, 0x1000)
    tb.read(0, 0x8000_0000, 0x1000)                     # inside: normal
    await tb.wait_idle()
    n_err = len(tb.err_events)
    ar0, aw0 = tb.axi.stats["ar"], tb.axi.stats["aw"]
    # fully outside: not issued, error with the request address (one at a time: the engine reports one
    # error address per cycle)
    tb.rd_reqs[1 % N_RD].append((0x7FFF_FF00, 16))
    for _ in range(50):
        await RisingEdge(dut.clk)
    tb.write(0x9000_0000, bytes(100))
    for _ in range(200):
        await RisingEdge(dut.clk)
    ev = [(k, a) for _, k, a in tb.err_events[n_err:]]
    assert ev == [("win", 0x7FFF_FF00), ("win", 0x9000_0000)], ev
    assert tb.axi.stats["ar"] == ar0 and tb.axi.stats["aw"] == aw0, "transaction outside the window issued"
    assert not tb.wr_beats, "producer beats of a dropped write were not consumed"
    # partly outside write: in-window bursts land, the rest is dropped and reported once
    tb.rd_exp[1 % N_RD].clear()
    n_err = len(tb.err_events)
    tb.write(0x8000_FF00, bytes(range(256)) * 2)
    await tb.wait_idle()
    tb.check_memory()
    assert [(k, a) for _, k, a in tb.err_events[n_err:]] == [("win", 0x8001_0000)]
    # wrap past 2^32 is a violation as well
    tb.set_window(0, 0xFFFF_FFFF)
    n_err = len(tb.err_events)
    tb.write(0xFFFF_FFF0, bytes(32))
    await tb.wait_idle()
    assert [(k, a) for _, k, a in tb.err_events[n_err:]] == [("win", 0x0000_0000)]


@cocotb.test()
async def axi06_outstanding_and_ids(dut):
    rng = random.Random(SEED + 6)
    prof = Profile(r_lat=(150, 200), b_lat=(150, 200), reorder=True, interleave=True)
    tb = AxiTb(dut, profile=prof, rng=rng)
    await tb.start()
    for i in range(120):
        a = 0x9000_0000 + i * 0x400 + rng.randrange(64)
        n = rng.randint(1, 3 * MAX_BURST)
        tb.fill(a, n)
        tb.read(i % N_RD, a, n)
        tb.write(0xA000_0000 + i * 0x400, bytes(rng.getrandbits(8) for _ in range(rng.randint(1, 3 * MAX_BURST))))
    await tb.wait_idle()
    tb.check_memory()
    assert tb.axi.stats["max_rd_out"] == RD_OUT, tb.axi.stats
    assert tb.axi.stats["max_wr_out"] == WR_OUT, tb.axi.stats
    if N_RD > 1:
        assert tb.id_switches > 50, tb.id_switches


class ChanMon:
    """Request handshakes and outstanding bursts per address channel, seen at the port (v1.9 throttle checks)."""

    def __init__(self, dut):
        self.dut = dut
        self.cyc = 0
        self.last_hs = {"ar": None, "aw": None}
        self.min_space = {"ar": 1 << 30, "aw": 1 << 30}
        self.out = {"ar": 0, "aw": 0}
        self.max_out = {"ar": 0, "aw": 0}
        self.limit = {"ar": (0, 0), "aw": (0, 0)}   # (max_out, gap) in force when the request first became valid
        self.violations = []
        self.attrs = []                              # (ch, (cache, prot, qos)) per handshake

    def hook(self):
        d = self.dut
        for ch in ("ar", "aw"):
            v, r = int(getattr(d, f"m_axi_{ch}valid").value), int(getattr(d, f"m_axi_{ch}ready").value)
            if v and r:
                mx, gap = self.limit[ch]
                if self.last_hs[ch] is not None:
                    sp = self.cyc - self.last_hs[ch]
                    self.min_space[ch] = min(self.min_space[ch], sp)
                    if sp < gap + 1:
                        self.violations.append((ch, "gap", self.cyc, sp, gap))
                if mx and self.out[ch] >= mx:
                    self.violations.append((ch, "max_out", self.cyc, self.out[ch], mx))
                self.last_hs[ch] = self.cyc
                self.out[ch] += 1
                self.max_out[ch] = max(self.max_out[ch], self.out[ch])
                self.attrs.append((ch, tuple(int(getattr(d, f"m_axi_{ch}{f}").value) for f in ("cache", "prot", "qos"))))
        if int(d.m_axi_rvalid.value) and int(d.m_axi_rready.value) and int(d.m_axi_rlast.value):
            self.out["ar"] -= 1
        if int(d.m_axi_bvalid.value) and int(d.m_axi_bready.value):
            self.out["aw"] -= 1
        self.cyc += 1


def traffic(tb, rng, n, base=0x9000_0000):
    for i in range(n):
        a = base + i * 0x400 + rng.randrange(64)
        k = rng.randint(1, 3 * MAX_BURST)
        tb.fill(a, k)
        tb.read(i % N_RD, a, k)
        tb.write(base + 0x0100_0000 + i * 0x400, bytes(rng.getrandbits(8) for _ in range(rng.randint(1, 3 * MAX_BURST))))


@cocotb.test()
async def axi07_throttle(dut):
    """MEM_QOS (v1.9): outstanding limits and request gaps hold at the port under long latencies; settings
    change while requests wait (a waiting request is never withdrawn: the monitor checks VALID stability);
    data and memory stay exact; off = no limit beyond the build-time one."""
    rng = random.Random(SEED + 8)
    prof = Profile(r_lat=(40, 120), b_lat=(40, 120), reorder=True, interleave=True)
    tb = AxiTb(dut, profile=prof, rng=rng)
    mon = ChanMon(dut)
    tb.axi.on_cycle = mon.hook
    await tb.start()
    settings = [(1, 1, 0, 0), (3, 2, 5, 9), (0, 0, 20, 3), (2, 0, 0, 40), (min(RD_OUT, 31), min(WR_OUT, 31), 1, 1)]
    for rd_max, wr_max, rd_gap, wr_gap in settings:
        tb.set_qos(rd_max, wr_max, rd_gap, wr_gap)
        await RisingEdge(dut.clk)
        mon.limit = {"ar": (rd_max, rd_gap), "aw": (wr_max, wr_gap)}
        mon.last_hs = {"ar": None, "aw": None}
        mon.max_out = {"ar": 0, "aw": 0}
        traffic(tb, rng, 30)
        await tb.wait_idle()
        tb.check_memory()
        assert not mon.violations, mon.violations[:5]
        if rd_max:
            assert mon.max_out["ar"] == min(rd_max, RD_OUT), (rd_max, mon.max_out)
        if wr_max:
            assert mon.max_out["aw"] == min(wr_max, WR_OUT), (wr_max, mon.max_out)
    # random changes in the middle of traffic: only protocol and data are checked (limits change on the fly)
    # The slow READY makes requests wait while the limits drop (a gate that withdrew VALID would fail the monitor).
    mon.limit = {"ar": (0, 0), "aw": (0, 0)}
    tb.axi.profile = Profile(p_arready=0.15, p_awready=0.15, p_wready=0.7, r_lat=(5, 60), b_lat=(5, 60),
                             reorder=True, interleave=True, ready_after_valid=True)
    traffic(tb, rng, 60, base=0xA000_0000)
    for _ in range(200):
        for _ in range(rng.randint(1, 15)):
            await RisingEdge(dut.clk)
        tb.set_qos(rng.randint(0, 3), rng.randint(0, 3), rng.choice([0, 0, 3, 50]), rng.choice([0, 0, 7, 50]))
    tb.set_qos(0, 0, 0, 0)
    await tb.wait_idle()
    tb.check_memory()


@cocotb.test()
async def axi08_attributes(dut):
    """MEM_ATTR (v1.9): AxCACHE / AxPROT / AxQOS follow the settings; a change while a request waits does not
    alter it (payload stability is checked by the monitor); after a change every new request carries it."""
    rng = random.Random(SEED + 9)
    prof = Profile.random(rng, max_lat=40)
    tb = AxiTb(dut, profile=prof, rng=rng)
    mon = ChanMon(dut)
    tb.axi.on_cycle = mon.hook
    await tb.start()
    traffic(tb, rng, 4)
    await tb.wait_idle()
    assert mon.attrs and all(a == ((0x3, 0, 0) if ch == "ar" else (0x3, 0, 0)) for ch, a in mon.attrs), mon.attrs[:4]
    for _ in range(6):
        v = [rng.randrange(16), rng.randrange(8), rng.randrange(16), rng.randrange(16), rng.randrange(8), rng.randrange(16)]
        tb.set_attr(*v)
        await RisingEdge(dut.clk)
        await RisingEdge(dut.clk)
        mon.attrs.clear()
        traffic(tb, rng, 6)
        await tb.wait_idle()
        tb.check_memory()
        for ch, a in mon.attrs:
            assert a == (tuple(v[:3]) if ch == "ar" else tuple(v[3:])), (ch, a, v)
    # toggling while traffic runs: protocol (stable payload) + data only
    traffic(tb, rng, 30, base=0xB800_0000)
    for _ in range(50):
        for _ in range(rng.randint(1, 20)):
            await RisingEdge(dut.clk)
        tb.set_attr(*[rng.randrange(16), rng.randrange(8), rng.randrange(16), rng.randrange(16), rng.randrange(8),
                      rng.randrange(16)])
    await tb.wait_idle()
    tb.check_memory()


@cocotb.test()
async def stop_drain_and_clear(dut):
    """stop mid-traffic: nothing new is issued, outstanding bursts complete, the producer can finish its
    accepted requests, the bus goes idle; after clr the engine works again."""
    rng = random.Random(SEED + 7)
    tb = AxiTb(dut, profile=Profile.random(rng, max_lat=60), rng=rng)
    await tb.start()
    for i in range(40):
        a = 0xB000_0000 + i * 0x4000
        tb.fill(a, 3000)
        tb.read(i % N_RD, a, 3000)
        tb.write(0xC000_0000 + i * 0x4000, bytes(rng.getrandbits(8) for _ in range(3000)))
    for _ in range(rng.randint(200, 600)):
        await RisingEdge(dut.clk)
    tb.accept_new = False
    dut.stop.value = 1
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    tb.axi.forbid_new = True
    n = 0
    while not (int(dut.rd_idle.value) and int(dut.wr_idle.value) and tb.axi.idle() and not tb.wr_beats):
        await RisingEdge(dut.clk)
        n += 1
        assert n < 100_000, "did not drain"
    tb.axi.forbid_new = False
    dut.clr.value = 1
    await RisingEdge(dut.clk)
    dut.clr.value = 0
    dut.stop.value = 0
    # memory: only bytes inside requested write ranges may differ from the initial image
    for base in sorted(tb.mem.page_bases()):
        if not 0xC000_0000 <= base < 0xC000_0000 + 40 * 0x4000:
            assert tb.mem.read(base, 4096) == tb.exp_mem.read(base, 4096), hex(base)
    # fresh state: new traffic is exact
    for c in range(N_RD):
        tb.rd_reqs[c].clear()
        tb.rd_exp[c].clear()
    tb.wr_reqs.clear()
    tb.wr_pending_beats.clear()
    tb.exp_mem = SparseMem()
    tb.mem.pages.clear()
    tb.accept_new = True
    tb.fill(0xD000_0000, 5000)
    tb.read(0, 0xD000_0000, 5000)
    tb.write(0xD100_0001, bytes(range(255)) * 4)
    await tb.wait_idle()
    tb.check_memory()


# ------------------------------------------------------------------------------------------------

import pytest  # noqa: E402

CONFIGS = {
    "dw64": dict(N_RD=3, DATA_W=64),
    "dw128": dict(N_RD=3, DATA_W=128),
    "dw64_small": dict(N_RD=2, DATA_W=64, MAX_BURST_BYTES=64, RD_OUTSTANDING=2, WR_OUTSTANDING=1),
    "dw128_big": dict(N_RD=1, DATA_W=128, MAX_BURST_BYTES=4096, RD_OUTSTANDING=16, WR_OUTSTANDING=16, ADDR_W=48),
}


@pytest.mark.parametrize("cfg", list(CONFIGS))
def test_axi_mst(cfg):
    import runner
    quick = cfg != "dw64" and os.environ.get("G2D_FULL") != "1"
    runner.run("g2d_axi_mst_tb", "test_axi_mst", params=CONFIGS[cfg], tag=cfg,
               extra_env={"G2D_QUICK": "1" if quick else "0"})
