# SPDX-License-Identifier: Apache-2.0
"""AXI4 slave memory model with protocol monitor for cocotb (verification-plan.md §2).

    mem = SparseMem()
    axi = AxiMem(dut, "m_axi", dut.clk, mem, data_w=64, profile=Profile.random(rng))
    axi.start()

Features
- Sparse byte memory (`SparseMem`, 4 KB pages, unwritten bytes read 0).
- Random ready on AW/W/AR, random R/B latency, random RVALID gaps, out-of-order completion across IDs
  and (optionally) beat-level read interleaving across IDs; per ID always in order (AXI rule).
- Error injection: address ranges answered with SLVERR/DECERR on reads and/or writes. Erroring writes
  do not modify memory.
- Ordering-adversarial: a write becomes visible when its B is sent, a read snapshots memory when its AR
  is accepted. A design that reads before the B of an earlier write sees stale data.
- Protocol monitor (raises `AxiProtocolError` in the clock coroutine, which fails the test):
  INCR only, full-width AxSIZE, aligned address, no 4 KB crossing, len <= max_beats, address inside
  `window` (callable), VALID/payload stable until READY on AW/W/AR, WLAST exactly on the last beat,
  W beats never exceed the announced bursts by more than `w_ahead_max` bursts, no AR/AW raised while
  `forbid_new` is set (soft reset), outstanding counts.
- Statistics: beats, bursts, max outstanding, transaction log (optional).

Sampling convention (checked in Phase 2 with Verilator): values read right after RisingEdge are the
values at that edge, so a handshake at an edge is `valid and ready` as read there; values driven right
after the edge take effect for the next edge.
"""

from __future__ import annotations

import random
import zlib
from collections import deque
from dataclasses import dataclass, field

import cocotb
from cocotb.triggers import RisingEdge


class AxiProtocolError(AssertionError):
    pass


# ------------------------------------------------------------------------------------------------
# memory
# ------------------------------------------------------------------------------------------------

class SparseMem:
    PAGE = 4096

    def __init__(self):
        self.pages: dict[int, bytearray] = {}

    def _page(self, base: int, create: bool):
        p = self.pages.get(base)
        if p is None and create:
            p = self.pages[base] = bytearray(self.PAGE)
        return p

    def read(self, addr: int, n: int) -> bytes:
        out = bytearray()
        while n > 0:
            base, off = addr & ~(self.PAGE - 1), addr & (self.PAGE - 1)
            k = min(n, self.PAGE - off)
            p = self._page(base, False)
            out += p[off:off + k] if p is not None else bytes(k)
            addr += k
            n -= k
        return bytes(out)

    def write(self, addr: int, data: bytes) -> None:
        i = 0
        while i < len(data):
            base, off = (addr + i) & ~(self.PAGE - 1), (addr + i) & (self.PAGE - 1)
            k = min(len(data) - i, self.PAGE - off)
            self._page(base, True)[off:off + k] = data[i:i + k]
            i += k

    def write_strb(self, addr: int, data: bytes, strb: int) -> None:
        for i, b in enumerate(data):
            if strb >> i & 1:
                a = addr + i
                self._page(a & ~(self.PAGE - 1), True)[a & (self.PAGE - 1)] = b

    def page_bases(self) -> set[int]:
        return set(self.pages)


# ------------------------------------------------------------------------------------------------
# configuration
# ------------------------------------------------------------------------------------------------

@dataclass
class Profile:
    p_awready: float = 1.0
    p_wready: float = 1.0
    p_arready: float = 1.0
    p_rvalid: float = 1.0        # probability to present a ready R beat in a cycle
    p_bvalid: float = 1.0
    r_lat: tuple[int, int] = (0, 0)   # cycles from AR accept to first beat eligible
    b_lat: tuple[int, int] = (0, 0)   # cycles from last W (and AW) to B eligible
    reorder: bool = False        # complete bursts of different IDs out of order
    interleave: bool = False     # switch IDs between read beats
    ready_after_valid: bool = False   # AWREADY / WREADY / ARREADY only in a cycle after VALID was seen
                                      # (legal slave; a master that waits for READY before VALID hangs)

    @staticmethod
    def ideal():
        return Profile()

    @staticmethod
    def random(rng: random.Random, max_lat: int = 200):
        lat = rng.choice([0, 2, 10, 40, max_lat])
        return Profile(
            p_awready=rng.choice([1.0, 0.7, 0.3]),
            p_wready=rng.choice([1.0, 0.7, 0.3]),
            p_arready=rng.choice([1.0, 0.7, 0.3]),
            p_rvalid=rng.choice([1.0, 0.8, 0.4]),
            p_bvalid=rng.choice([1.0, 0.8, 0.4]),
            r_lat=(rng.randint(0, lat), lat),
            b_lat=(rng.randint(0, lat), lat),
            reorder=rng.random() < 0.7,
            interleave=rng.random() < 0.5,
            ready_after_valid=rng.random() < 0.3,
        )


@dataclass
class ErrRange:
    lo: int
    hi: int          # inclusive
    resp: int = 2    # 2 SLVERR, 3 DECERR
    on_read: bool = True
    on_write: bool = True


@dataclass
class _RBurst:
    id: int
    addr: int
    beats: int
    data: list = field(default_factory=list)
    resp: int = 0
    ready_at: int = 0
    sent: int = 0


@dataclass
class _WBurst:
    addr: int
    beats: int
    id: int
    data: list = field(default_factory=list)   # (data, strb)


# ------------------------------------------------------------------------------------------------
# model
# ------------------------------------------------------------------------------------------------

class AxiMem:
    def __init__(self, dut, prefix, clk, mem: SparseMem, data_w: int, profile: Profile | None = None,
                 rng: random.Random | None = None, max_beats: int = 256, window=None, log=False, rst_n=None):
        self.dut = dut
        self.rst_n = rst_n            # active-low reset: while low the model idles and checks nothing
        self.p = prefix
        self.clk = clk
        self.mem = mem
        self.data_w = data_w
        self.db = data_w // 8
        self.size = self.db.bit_length() - 1
        self.profile = profile or Profile.ideal()
        self.rng = rng or random.Random(0)
        self.max_beats = max_beats
        self.window = window          # callable(addr_lo, addr_hi) -> bool, or None
        self.err_ranges: list[ErrRange] = []
        self.err_bursts: list = []   # (address, bytes, is_read) of every burst answered with SLVERR / DECERR
        self.on_burst = None         # callback(is_read, address, beats) at every AR / AW handshake (coverage)
        self.forbid_new = False
        self.w_ahead_max = 64
        self.log_enabled = log
        self.log: list[tuple] = []
        # statistics
        # rd_stall / wr_stall as PERF_RD_STALL / PERF_WR_STALL count them (arch.md §9)
        self.stats = dict(ar=0, aw=0, r_beats=0, w_beats=0, b=0, max_rd_out=0, max_wr_out=0, rd_stall=0,
                          wr_stall=0)
        self.on_cycle = None     # callback at every sampled edge out of reset (environment references)
        self.cycle = 0
        self._task = None
        self.reset_state()

    def sig(self, name):
        return getattr(self.dut, f"{self.p}_{name}")

    def _attr(self, ch):
        """(cache, prot, qos) of the AR / AW channel where the DUT has them (v1.9: part of the stable payload)."""
        out = []
        for f in ("cache", "prot", "qos"):
            h = getattr(self.dut, f"{self.p}_{ch}{f}", None)
            out.append(int(h.value) if h is not None else 0)
        return tuple(out)

    def reset_state(self):
        self.aw_q: deque[_WBurst] = deque()     # accepted AW waiting for W data
        self.w_beats: deque = deque()            # W beats not yet matched to an AW
        self.b_pending: deque = deque()          # (ready_at, wburst, resp) in order
        self.b_cur = None
        self.r_bursts: list[_RBurst] = []        # accepted AR, not finished
        self.r_cur: _RBurst | None = None        # burst of the R beat currently driven
        self.r_driving = False
        self.prev = {}                           # channel -> (valid, payload) at last edge, for stability
        # v1.8: reference of the core's WR_SIG (arch.md §9.1): zlib.crc32 over every accepted W beat, lanes with
        # WSTRB = 0 as 0x00; restarts with the reset, as the hardware's
        self.wsig = 0

    def start(self):
        self._drive_idle()
        self._task = cocotb.start_soon(self._run())
        return self

    def stop(self):
        if self._task:
            self._task.cancel()

    # ---- helpers ----
    def _err_for(self, addr, nbytes, is_read):
        for e in self.err_ranges:
            if (e.on_read if is_read else e.on_write) and addr <= e.hi and addr + nbytes - 1 >= e.lo:
                return e.resp
        return 0

    def _drive_idle(self):
        for n in ("awready", "wready", "arready", "rvalid", "bvalid"):
            self.sig(n).value = 0
        self.sig("rdata").value = 0
        self.sig("rid").value = 0
        self.sig("rresp").value = 0
        self.sig("rlast").value = 0
        self.sig("bid").value = 0
        self.sig("bresp").value = 0

    def _check_ax(self, kind, addr, length, size, burst):
        beats = length + 1
        if burst != 1:
            raise AxiProtocolError(f"{kind}: burst type {burst} (INCR only)")
        if size != self.size:
            raise AxiProtocolError(f"{kind}: size {size} != {self.size}")
        if addr % self.db:
            raise AxiProtocolError(f"{kind}: unaligned address 0x{addr:x}")
        if beats > self.max_beats:
            raise AxiProtocolError(f"{kind}: {beats} beats > {self.max_beats}")
        if (addr & 0xFFF) + beats * self.db > 4096:
            raise AxiProtocolError(f"{kind}: 4 KB crossing at 0x{addr:x} len {length}")
        if self.window is not None and not self.window(addr, addr + beats * self.db - 1):
            raise AxiProtocolError(f"{kind}: 0x{addr:x}+{beats * self.db} outside the address window")
        if self.forbid_new:
            raise AxiProtocolError(f"{kind}: new transaction while forbid_new (soft reset)")

    def _stable(self, ch, valid, ready, payload):
        """VALID must stay high with an unchanged payload until READY."""
        pv = self.prev.get(ch)
        if pv is not None:
            pvalid, pready, ppay = pv
            if pvalid and not pready:
                if not valid:
                    raise AxiProtocolError(f"{ch}: VALID dropped before READY")
                if payload != ppay:
                    raise AxiProtocolError(f"{ch}: payload changed while VALID and not READY")
        self.prev[ch] = (valid, ready, payload)

    def rd_outstanding(self):
        return len(self.r_bursts)

    def wr_outstanding(self):
        return len(self.aw_q) + len(self.b_pending) + (1 if self.b_cur else 0)

    def idle(self):
        return not (self.r_bursts or self.aw_q or self.w_beats or self.b_pending or self.b_cur)

    # ---- per-cycle process ----
    async def _run(self):
        rng = self.rng
        awready = wready = arready = 0
        while True:
            await RisingEdge(self.clk)
            pr = self.profile          # read every cycle: tests switch profiles between phases
            self.cycle += 1
            if self.rst_n is not None and not int(self.rst_n.value):
                self.reset_state()
                self._drive_idle()
                awready = wready = arready = 0
                continue
            # ---------------- sample at this edge
            awvalid = int(self.sig("awvalid").value)
            wvalid = int(self.sig("wvalid").value)
            arvalid = int(self.sig("arvalid").value)
            rready = int(self.sig("rready").value)
            bready = int(self.sig("bready").value)
            rvalid, bvalid = self.r_driving, self.b_cur is not None
            self.stats["rd_stall"] += int((arvalid and not arready) or (rvalid and not rready))
            self.stats["wr_stall"] += int((awvalid and not awready) or (wvalid and not wready) or (bvalid and not bready))
            if self.on_cycle:
                self.on_cycle()

            if awvalid:
                aw = (int(self.sig("awid").value), int(self.sig("awaddr").value), int(self.sig("awlen").value),
                      int(self.sig("awsize").value), int(self.sig("awburst").value)) + self._attr("aw")
            else:
                aw = None
            new_aw = awvalid and not (self.prev.get("AW", (0, 0, None))[0] and not self.prev["AW"][1])
            self._stable("AW", awvalid, awready, aw)
            if awvalid and new_aw:
                self._check_ax("AW", aw[1], aw[2], aw[3], aw[4])
            if awvalid and awready:
                self.aw_q.append(_WBurst(addr=aw[1], beats=aw[2] + 1, id=aw[0]))
                if self.on_burst:
                    self.on_burst(False, aw[1], aw[2] + 1)
                self.stats["aw"] += 1
                if self.log_enabled:
                    self.log.append(("AW", self.cycle, aw))

            if wvalid:
                w = (int(self.sig("wdata").value), int(self.sig("wstrb").value), int(self.sig("wlast").value))
            else:
                w = None
            self._stable("W", wvalid, wready, w)
            if wvalid and wready:
                self.w_beats.append(w)
                self.stats["w_beats"] += 1
                self.wsig = zlib.crc32(bytes(((w[0] >> (8 * i)) & 0xFF) if (w[1] >> i) & 1 else 0
                                             for i in range(self.db)), self.wsig)

            if arvalid:
                ar = (int(self.sig("arid").value), int(self.sig("araddr").value), int(self.sig("arlen").value),
                      int(self.sig("arsize").value), int(self.sig("arburst").value)) + self._attr("ar")
            else:
                ar = None
            new_ar = arvalid and not (self.prev.get("AR", (0, 0, None))[0] and not self.prev["AR"][1])
            self._stable("AR", arvalid, arready, ar)
            if arvalid and new_ar:
                self._check_ax("AR", ar[1], ar[2], ar[3], ar[4])
            if arvalid and arready:
                beats = ar[2] + 1
                if self.on_burst:
                    self.on_burst(True, ar[1], beats)
                nbytes = beats * self.db
                resp = self._err_for(ar[1], nbytes, True)
                if resp:
                    self.err_bursts.append((ar[1], nbytes, True))
                raw = self.mem.read(ar[1], nbytes)
                data = [int.from_bytes(raw[i * self.db:(i + 1) * self.db], "little") for i in range(beats)]
                self.r_bursts.append(_RBurst(id=ar[0], addr=ar[1], beats=beats, data=data, resp=resp,
                                             ready_at=self.cycle + rng.randint(*pr.r_lat)))
                self.stats["ar"] += 1
                if self.log_enabled:
                    self.log.append(("AR", self.cycle, ar))

            # R beat handshake
            if self.r_driving and rready:
                b = self.r_cur
                b.sent += 1
                self.stats["r_beats"] += 1
                if b.sent == b.beats:
                    self.r_bursts.remove(b)
                    self.r_cur = None
                self.r_driving = False
            # B handshake: the write becomes visible now
            if self.b_cur is not None and bready:
                wb, resp = self.b_cur
                if resp == 0:
                    for i, (d, s, _) in enumerate(wb.data):
                        self.mem.write_strb(wb.addr + i * self.db, d.to_bytes(self.db, "little"), s)
                self.b_cur = None
                self.stats["b"] += 1

            # match W beats to AWs (W may arrive before its AW)
            while self.aw_q and len(self.w_beats) >= self.aw_q[0].beats:
                wb = self.aw_q.popleft()
                for i in range(wb.beats):
                    d, s, last = self.w_beats.popleft()
                    if last != int(i == wb.beats - 1):
                        raise AxiProtocolError(f"W: WLAST={last} on beat {i} of {wb.beats} (AW 0x{wb.addr:x})")
                    wb.data.append((d, s, last))
                resp = self._err_for(wb.addr, wb.beats * self.db, False)
                if resp:
                    self.err_bursts.append((wb.addr, wb.beats * self.db, False))
                self.b_pending.append((self.cycle + rng.randint(*pr.b_lat), wb, resp))
            if not self.aw_q and self.w_beats:
                # W ahead of AW: WLAST positions are checked once the AW arrives
                if sum(1 for b in self.w_beats if b[2]) > self.w_ahead_max:
                    raise AxiProtocolError("W: too many bursts of data ahead of AW")

            self.stats["max_rd_out"] = max(self.stats["max_rd_out"], self.rd_outstanding())
            self.stats["max_wr_out"] = max(self.stats["max_wr_out"], self.wr_outstanding())

            # ---------------- drive for the next edge
            rav = pr.ready_after_valid
            awready = int((awvalid or not rav) and rng.random() < pr.p_awready)
            wready = int((wvalid or not rav) and rng.random() < pr.p_wready)
            arready = int((arvalid or not rav) and rng.random() < pr.p_arready)
            self.sig("awready").value = awready
            self.sig("wready").value = wready
            self.sig("arready").value = arready

            # R: keep a beat that was not accepted; otherwise choose the next one
            if not self.r_driving:
                cand = None
                if self.r_cur is not None and not pr.interleave:
                    cand = self.r_cur
                else:
                    heads = {}
                    for b in self.r_bursts:          # first unfinished burst of each ID
                        if b.id not in heads:
                            heads[b.id] = b
                    elig = [b for b in heads.values() if b.ready_at <= self.cycle]
                    if self.r_cur is not None and not pr.interleave:
                        elig = [self.r_cur]
                    if elig:
                        cand = rng.choice(elig) if pr.reorder else min(elig, key=lambda b: self.r_bursts.index(b))
                if cand is not None and rng.random() < pr.p_rvalid:
                    self.r_cur = cand
                    self.r_driving = True
                    i = cand.sent
                    self.sig("rid").value = cand.id
                    self.sig("rdata").value = cand.data[i]
                    self.sig("rresp").value = cand.resp
                    self.sig("rlast").value = int(i == cand.beats - 1)
            self.sig("rvalid").value = int(self.r_driving)

            # B: in order, after its latency
            if self.b_cur is None and self.b_pending and self.b_pending[0][0] <= self.cycle \
                    and rng.random() < pr.p_bvalid:
                _, wb, resp = self.b_pending.popleft()
                self.b_cur = (wb, resp)
                self.sig("bid").value = wb.id
                self.sig("bresp").value = resp
            self.sig("bvalid").value = int(self.b_cur is not None)
