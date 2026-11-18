# SPDX-License-Identifier: Apache-2.0
"""AXI4-Lite slave model (32-bit) with a protocol monitor, for the configuration-side adapter benches (v1.9).

The adapter under test is the AXI4-Lite master (ports <prefix>_aw*, _w*, _b*, _ar*, _r*). The model
- drives AWREADY / WREADY / ARREADY at random (probability p_ready), accepts AW and W independently,
- executes a write when both are held: byte lanes with WSTRB = 1 go to a word memory; B after a random latency,
- executes a read on AR: R after a random latency with the word,
- answers SLVERR for word addresses in `err_words` (nothing is written),
- logs every executed access as (op, addr, data, strb, resp) in order,
- checks: VALID and payload stable until READY on AW / W / AR, B / R only consumed with READY,
  at most `max_out` accesses outstanding (adapters here allow one).
"""

from __future__ import annotations

import random
from collections import deque

from cocotb.triggers import RisingEdge


class AxilProtocolError(Exception):
    pass


class AxilSlave:
    def __init__(self, dut, prefix, clk, rng: random.Random, p_ready=0.6, lat=(0, 6), err_words=(), max_out=1,
                 rst_n=None):
        self.dut, self.p, self.clk, self.rng = dut, prefix, clk, rng
        self.p_ready, self.lat = p_ready, lat
        self.err_words = set(err_words)
        self.max_out = max_out
        self.rst_n = rst_n
        self.mem: dict[int, int] = {}
        self.log: list[tuple] = []
        self.aw = deque()
        self.w = deque()
        self.b = deque()      # (ready_cycle, resp)
        self.r = deque()      # (ready_cycle, data, resp)
        self.cycle = 0
        self.prev = {}
        for s, v in (("awready", 0), ("wready", 0), ("arready", 0), ("bvalid", 0), ("bresp", 0), ("rvalid", 0),
                     ("rdata", 0), ("rresp", 0)):
            self.sig(s).value = v

    def sig(self, name):
        return getattr(self.dut, f"{self.p}_{name}")

    def read_word(self, waddr: int) -> int:
        return self.mem.get(waddr, (waddr * 0x9E3779B1) & 0xFFFFFFFF)

    def outstanding(self):
        return len(self.b) + len(self.r) + min(len(self.aw), len(self.w))

    def idle(self):
        return not (self.aw or self.w or self.b or self.r)

    def _stable(self, ch, valid, ready, payload):
        pv = self.prev.get(ch)
        if pv is not None:
            pvalid, pready, ppay = pv
            if pvalid and not pready:
                if not valid:
                    raise AxilProtocolError(f"{ch}: VALID dropped before READY")
                if payload != ppay:
                    raise AxilProtocolError(f"{ch}: payload changed while waiting: {ppay} -> {payload}")
        self.prev[ch] = (valid, ready, payload)

    async def run(self):
        awready = wready = arready = 0
        bvalid = rvalid = 0
        while True:
            await RisingEdge(self.clk)   # sample and drive at the edge, as dv/common/axi_mem.py
            self.cycle += 1
            if self.rst_n is not None and not int(self.rst_n.value):
                for s in ("awready", "wready", "arready", "bvalid", "rvalid"):
                    self.sig(s).value = 0
                awready = wready = arready = bvalid = rvalid = 0
                self.prev.clear()
                continue
            awv, wv, arv = (int(self.sig(s).value) for s in ("awvalid", "wvalid", "arvalid"))
            bready, rready = int(self.sig("bready").value), int(self.sig("rready").value)
            aw = (int(self.sig("awaddr").value), int(self.sig("awprot").value)) if awv else None
            w = (int(self.sig("wdata").value), int(self.sig("wstrb").value)) if wv else None
            ar = (int(self.sig("araddr").value), int(self.sig("arprot").value)) if arv else None
            self._stable("AW", awv, awready, aw)
            self._stable("W", wv, wready, w)
            self._stable("AR", arv, arready, ar)
            if awv and awready:
                self.aw.append(aw[0])
            if wv and wready:
                self.w.append(w)
            if arv and arready:
                a = ar[0]
                resp = 2 if (a >> 2) in self.err_words else 0
                d = self.read_word(a >> 2) if resp == 0 else 0
                self.log.append(("R", a, d, 0, resp))
                self.r.append((self.cycle + self.rng.randint(*self.lat), d, resp))
            while self.aw and self.w:
                a = self.aw.popleft()
                d, s = self.w.popleft()
                resp = 2 if (a >> 2) in self.err_words else 0
                if resp == 0:
                    old = self.read_word(a >> 2)
                    m = sum(0xFF << (8 * i) for i in range(4) if (s >> i) & 1)
                    self.mem[a >> 2] = (old & ~m) | (d & m)
                self.log.append(("W", a, d, s, resp))
                self.b.append((self.cycle + self.rng.randint(*self.lat), resp))
            if bvalid and bready:
                self.b.popleft()
            if rvalid and rready:
                self.r.popleft()
            if self.outstanding() > self.max_out:
                raise AxilProtocolError(f"more than {self.max_out} accesses outstanding")
            awready, wready, arready, bvalid, rvalid = self._drive_next()

    def _drive_next(self):
        """Drive the outputs for the next edge; returns the driven (awready, wready, arready, bvalid, rvalid)
        (cocotb applies writes later, so they cannot be read back in this step)."""
        rnd = self.rng.random
        awready, wready, arready = (int(rnd() < self.p_ready) for _ in range(3))
        self.sig("awready").value = awready
        self.sig("wready").value = wready
        self.sig("arready").value = arready
        bvalid = int(bool(self.b) and self.b[0][0] <= self.cycle)
        self.sig("bvalid").value = bvalid
        if bvalid:
            self.sig("bresp").value = self.b[0][1]
        rvalid = int(bool(self.r) and self.r[0][0] <= self.cycle)
        self.sig("rvalid").value = rvalid
        if rvalid:
            self.sig("rdata").value = self.r[0][1]
            self.sig("rresp").value = self.r[0][2]
        return awready, wready, arready, bvalid, rvalid
