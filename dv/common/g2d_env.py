# SPDX-License-Identifier: Apache-2.0
"""Top-level (L3) environment: g2d_top + golden model + AXI4 memory model (verification-plan.md §2).

    env = G2dEnv(dut, data_w=64)
    await env.start()
    tgt = env.new_target(fmt, w, h)            # random address/stride, surrounding bytes randomised
    cb = CmdBuf(); cb.target(...); ...; cb.draw_rect(...); cb.fence(1)
    await env.kick(cb)
    await env.wait_done()
    await env.check()                          # whole memory + error/fence registers + perf vs model

Every byte the DUT or the model ever touched is compared (page granularity), so stray writes outside a
target are caught. On mismatch the first differing bytes are listed with their target coordinates and
expected/actual/diff PNGs are written to build/dv/fail/<test>/.

Phase 2 feature boundary (docs/PHASES.md, Q1): the hardware reports BAD_STATE for the first draw it
cannot execute (DRAW_PATH, PAINT_CTRL.SRC != SOLID, BLEND.MODE != NONE). The environment predicts this:
it runs the model on the commands before that draw and then overlays the expected error.
"""

from __future__ import annotations

import os
import random
import struct
from dataclasses import dataclass
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

import g2d_model as gm
from axi_mem import AxiMem, Profile, SparseMem
from csr_if import CsrIf
from g2d_coverage import ERR_BITS, Coverage, _s16
from g2d_cmd import CSR, FMT_BYTES, REGMAP, STATE, CmdBuf, row_bytes

ERR = {f.name: 1 << f.lsb for f in CSR["ERR_STATUS"].fields}
STATE_WMASK = {r.addr: sum(f.mask for f in r.fields) for r in REGMAP.state}
ROOT = Path(__file__).resolve().parents[2]

OP_NOP, OP_LOAD, OP_RECT, OP_PATH, OP_FENCE = 0x00, 0x01, 0x10, 0x11, 0x20


def top_of(dut):
    """The g2d_top instance under the bench (directly, or inside a g2d_top_<bus> wrapper)."""
    try:
        return dut.u_dut.u_top
    except AttributeError:
        return dut.u_dut


@dataclass
class Target:
    addr: int
    w: int
    h: int
    fmt: int
    stride: int

    @property
    def bpp(self):
        """Bytes per pixel, 1 for sub-byte formats (their alignment unit)."""
        return max(1, FMT_BYTES[self.fmt])

    @property
    def row(self):
        return row_bytes(self.fmt, self.w)

    @property
    def nbytes(self):
        return self.stride * (self.h - 1) + self.row

    def locate(self, a):
        off = a - self.addr
        if off < 0 or off >= self.nbytes:
            return None
        y, xb = divmod(off, self.stride)
        if xb >= self.row:
            return ("pad", y, xb)
        return (xb // self.bpp, y)


# ------------------------------------------------------------------------------------------------
# command decode helpers
# ------------------------------------------------------------------------------------------------

def iter_draws(words: list[int], state: list[int]):
    """Yield (word index, opcode, state) for every draw of a command buffer, following the command decode
    of spec/commands.md and tracking LOAD_STATE. Stops at the first malformed command (where the model
    stops with CMD_DECODE). The state list is the live tracking copy: copy it to keep it."""
    st = list(state)
    n = len(words)
    pos = 0
    while pos < n:
        h = words[pos]
        op = h >> 24
        if op == OP_NOP:
            if (h >> 16) & 0xFF:
                return
            pay = h & 0xFFFF
        elif op == OP_LOAD:
            cnt, idx = (h >> 16) & 0xFF, h & 0xFF
            if (h >> 8) & 0xFF or cnt == 0 or idx + cnt > REGMAP.state_size:
                return
            pay = cnt
        elif op in (OP_RECT, OP_PATH):
            if h & 0xFFFFFF:
                return
            pay = 2 if op == OP_RECT else 4
        elif op == OP_FENCE:
            if h & 0xFFFFFE:
                return
            pay = 1
        else:
            return
        if pos + 1 + pay > n:
            return
        if op == OP_LOAD:
            for k in range(pay):
                st[idx + k] = words[pos + 1 + k] & STATE_WMASK.get(idx + k, 0)
        if op == OP_PATH and words[pos + 4] >> 24:
            return
        if op in (OP_RECT, OP_PATH):
            yield pos, op, st
        pos += 1 + pay


# ------------------------------------------------------------------------------------------------
# environment
# ------------------------------------------------------------------------------------------------

class G2dEnv:
    def __init__(self, dut, data_w: int = 64, addr_w: int = 32, kick_depth: int = 4,
                 profile: Profile | None = None, rng: random.Random | None = None, base_hi: int = 0):
        self.dut = dut
        # g2d_top instance: the bench's u_dut, or u_dut.u_top inside a per-bus wrapper (v1.9, rtl/wrap/generated)
        self.top = top_of(dut)
        self.data_w = data_w
        self.kick_depth = kick_depth
        self.rng = rng or random.Random(int(os.environ.get("SEED", "1")))
        self.base_hi = base_hi if addr_w > 32 else 0
        self.off = self.base_hi << 32
        self.dmem = SparseMem()                      # what the DUT sees (AXI addresses)
        self.mmem = gm.Memory()                      # golden model memory (32-bit GPU addresses)
        self.model = gm.Gpu(self.mmem)
        # v1.7: the optional units of the build under test (parameter FEATURES, HWCFG.FEAT_*)
        self.model.set_features(int(os.environ.get("G2D_P_FEATURES", "63")))
        self.win = (0, 0xFFFF_FFFF)
        # CFG_ASYNC = MEM_ASYNC = 1 build (verification-plan.md CDC-04): the CSR master runs on clk_cfg and the
        # memory model on clk_mem, at periods (ps) and phases drawn from the seed; the core keeps 10 ns
        self.async_mode = os.environ.get("G2D_ASYNC") == "1"
        if self.async_mode:
            # own generator: self.rng must draw the same sequence in every build (SYS-05 compares their memory)
            crng = random.Random(int(os.environ.get("SEED", "1")) * 1_000_003 + 17)
            self.crng = crng    # also draws the reset-release edges
            self.periods = dict(cfg=2 * crng.randint(1500, 11500), mem=2 * crng.randint(1500, 11500))   # even (cocotb Clock)
            self.phases = dict(cfg=crng.randint(0, 9999), mem=crng.randint(0, 9999))
            dut._log.info(f"async ports: cfg {self.periods['cfg']} ps, mem {self.periods['mem']} ps, core 10000 ps")
        mclk, mrst = (dut.clk_mem, dut.rst_mem_n) if self.async_mode else (dut.clk, dut.rst_n)
        cclk, crst = (dut.clk_cfg, dut.rst_cfg_n) if self.async_mode else (dut.clk, dut.rst_n)
        self.axi = AxiMem(dut, "m_axi", mclk, self.dmem, data_w, profile or Profile.ideal(),
                          rng=random.Random(self.rng.getrandbits(32)), max_beats=256,
                          window=self._in_window, rst_n=mrst)
        self.csr = CsrIf(dut, "s_axil", cclk, crst)
        self.core_stall = dict(rd_stall=0, wr_stall=0)   # async: PERF_*_STALL references at the core-side port
        self.cdc_clr_checks = 0
        self.targets: list[Target] = []
        self.override = None            # expected error state where the model cannot predict it (AXI errors)
        self.mask: list[tuple[int, int]] = []   # GPU address ranges excluded from the memory compare
        self.next_cmd = 0x0100_0000
        self.perf_checked = True
        self.test_name = "test"
        self.cov = Coverage()

    # ---- basics ----
    def _in_window(self, lo, hi):
        lo, hi = lo - self.off, hi - self.off
        return self.win[0] <= lo and hi <= self.win[1]

    async def start(self):
        dut = self.dut
        # reset high at time 0 and low 1 ns later: a definite asynchronous reset before the first clock
        # edge, also with random initial values (see dv/common/clkrst.py)
        for n in ("clk_cfg", "clk_mem"):
            getattr(dut, n).value = 0
        dut.test_mode.value = 0        # v1.9 DFT input: functional mode
        for n in ("rst_n", "rst_cfg_n", "rst_mem_n"):
            getattr(dut, n).value = 1
        cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start(start_high=False))
        if self.async_mode:
            for n in ("cfg", "mem"):
                cocotb.start_soon(self._port_clock(getattr(dut, f"clk_{n}"), self.periods[n], self.phases[n]))
        await Timer(1, unit="ns")
        for n in ("rst_n", "rst_cfg_n", "rst_mem_n"):
            getattr(dut, n).value = 0
        self.axi.start()
        await Timer(34, unit="ns")
        await self._release_resets()
        if self.base_hi:
            await self.csr.write("MEM_BASE_HI", self.base_hi)
        await self.csr.write("PERF_CTRL", 0b011)   # clear + enable
        mb = int(os.environ.get("G2D_P_MAX_BURST_BYTES", "256"))
        self.axi.on_burst = lambda rd, a, beats: self.cov.sample_burst(rd, a, beats, self.data_w, mb)
        # A flattened gate-level netlist (dv/tests/test_gls_ihp.py) has no u_core hierarchy: black-box mode keeps
        # every check through the ports (memory against the model, PERF draws / pixels / beats / stalls, WR_SIG,
        # DBG_STATUS) and drops the white-box ones (BUSY / DRAIN references, error coverage, drained, state view).
        self.blackbox = not hasattr(self.top, "u_core")
        self.ref = dict(busy=0, drain=0)
        if self.blackbox:
            return
        # references of PERF_BUSY_CYCLES and PERF_DRAIN_CYC, from the inputs of the counted conditions
        core = self.top.u_core
        sig = dict(idle=core.u_csr.u_ctrl.idle, dv=core.draw_valid, fill=core.is_fill, fi=core.fill_idle,
                   pi=core.pix_idle, wi=core.wr_idle)
        self.ref = dict(busy=0, drain=0)

        def on_cycle():
            v = {k: int(x.value) for k, x in sig.items()}
            self.ref["busy"] += 1 - v["idle"]
            self.ref["drain"] += v["dv"] & (1 - v["fill"]) & v["pi"] & (1 - (v["fi"] & v["wi"]))
        if not self.async_mode:
            self.axi.on_cycle = on_cycle
        else:
            cocotb.start_soon(self._core_cycles(on_cycle))
            cocotb.start_soon(self._watch_cdc_clear())
        regs = self.top.u_core.u_csr.u_regs
        for bit in ERR_BITS:
            cocotb.start_soon(self._watch_error(getattr(regs, f"err_status_{bit}_q"), bit))

    async def _port_clock(self, sig, period_ps, phase_ps):
        await Timer(phase_ps + 1, unit="ps")
        await Clock(sig, period_ps, unit="ps").start(start_high=False)

    async def _release_resets(self):
        """Release the resets. Single clock: all three together, as before. Async: each synchronously to its own
        clock (at a falling edge after 1-3 rising edges), as the integrator's reset synchronizers do
        (arch.md §2.2)."""
        dut = self.dut
        if not self.async_mode:
            for n in ("rst_n", "rst_cfg_n", "rst_mem_n"):
                getattr(dut, n).value = 1
            await RisingEdge(dut.clk)
            return

        async def rel(clk, rst, n):
            for _ in range(n):
                await RisingEdge(clk)
            await FallingEdge(clk)
            rst.value = 1
        jobs = [cocotb.start_soon(rel(getattr(dut, c), getattr(dut, r), self.crng.randint(1, 3)))
                for c, r in (("clk", "rst_n"), ("clk_cfg", "rst_cfg_n"), ("clk_mem", "rst_mem_n"))]
        for j in jobs:
            await j
        await RisingEdge(dut.clk)

    async def _core_cycles(self, on_cycle):
        """Async: the core-clock references (BUSY / DRAIN) and the stall counts at the core side of the memory
        CDC, where g2d_axi_mst's PERF_RD_STALL / PERF_WR_STALL count them (arch.md §9)."""
        dut = self.dut
        top = self.top
        rd = [top.c_axi_arvalid, top.c_axi_arready, top.c_axi_rvalid, top.c_axi_rready]
        wr = [top.c_axi_awvalid, top.c_axi_awready, top.c_axi_wvalid, top.c_axi_wready, top.c_axi_bvalid,
              top.c_axi_bready]
        while True:
            await RisingEdge(dut.clk)
            if not int(dut.rst_n.value):
                continue
            arv, arr, rv, rr = (int(x.value) for x in rd)
            awv, awr, wv, wr_, bv, br = (int(x.value) for x in wr)
            self.core_stall["rd_stall"] += int((arv and not arr) or (rv and not rr))
            self.core_stall["wr_stall"] += int((awv and not awr) or (wv and not wr_) or (bv and not br))
            on_cycle()

    async def _watch_cdc_clear(self):
        """CDC-04: at every soft_clr the five memory-port CDC FIFOs are truly empty (write pointer == read
        pointer across the domains; arch.md §2.2)."""
        core = self.top.u_core
        cdc = self.top.g_mem_cdc.u_mem_cdc
        while True:
            await RisingEdge(self.dut.clk)
            if int(self.dut.rst_n.value) and int(core.soft_clr.value):
                for ch in ("u_aw", "u_w", "u_b", "u_ar", "u_r"):
                    f = getattr(cdc, ch)
                    assert int(f.wbin_q.value) == int(f.rbin_q.value), f"CDC FIFO {ch} not empty at soft_clr"
                self.cdc_clr_checks += 1

    async def _watch_error(self, sig, bit):
        """cg_errors: each time ERR_STATUS.<bit> is set, whether a draw engine was busy."""
        core = self.top.u_core
        while True:
            await RisingEdge(sig)
            self.cov.sample_error(bit, not (int(core.fill_idle.value) and int(core.pix_idle.value)))

    async def cycles(self, n):
        for _ in range(n):
            await RisingEdge(self.dut.clk)

    def write_mem(self, addr: int, data: bytes):
        """Write to both memories. GPU addresses are 32-bit: a range past 2^32 wraps (as in the model)."""
        first = min(len(data), (1 << 32) - addr)
        self.dmem.write(self.off + addr, data[:first])
        if first < len(data):
            self.dmem.write(self.off, data[first:])
        self.mmem.write(addr, data[:first])
        if first < len(data):
            self.mmem.write(0, data[first:])

    def read_dut(self, addr: int, n: int) -> bytes:
        return self.dmem.read(self.off + addr, n)

    def randomize(self, addr: int, n: int):
        self.write_mem(addr, self.rng.randbytes(n))

    async def set_window(self, lo: int, hi: int):
        await self.csr.write("ADDR_WIN_LO", lo)
        await self.csr.write("ADDR_WIN_HI", hi)
        self.model.set_addr_win(lo, hi)
        self.win = (self.model.addr_win_lo, self.model.addr_win_hi)

    def new_target(self, fmt: int, w: int, h: int, addr: int | None = None, stride: int | None = None,
                   pad_max: int = 64, randomize: bool = True) -> Target:
        bpp = max(1, FMT_BYTES[fmt])
        if stride is None:
            stride = row_bytes(fmt, w) + bpp * self.rng.randint(0, pad_max)
        if addr is None:
            # 2 MB apart (every test target is smaller), 640 targets before the pattern repeats
            addr = 0x1000_0000 + (0x0020_0000 * len(self.targets)) % 0x5000_0000 + bpp * self.rng.randrange(0, 4096)
        t = Target(addr, w, h, fmt, stride)
        self.targets.append(t)
        # randomise the target and 64 bytes around it so wrong strobes and stray writes show up
        if randomize:
            self.randomize(addr - 64, t.nbytes + 128)
        return t

    # ---- command submission ----
    def place(self, data: bytes, addr: int | None = None) -> int:
        if addr is None:
            addr = self.next_cmd
            self.next_cmd += (len(data) + 63) & ~63
        self.write_mem(addr, data)
        return addr

    async def kick(self, cb, addr: int | None = None, size: int | None = None, to_model: bool = True,
                   wait_free: bool = True):
        """Place a command buffer and kick it. wait_free: first wait for a free kick-queue slot (returns
        None without kicking if the GPU is halted and the queue stays full)."""
        data = cb.to_bytes() if isinstance(cb, CmdBuf) else bytes(cb)
        if wait_free:
            n = 0
            while True:
                st = await self.csr.read("STATUS")
                if CsrIf.field("STATUS", "KICK_FREE", st):
                    break
                if CsrIf.field("STATUS", "HALTED", st):
                    return None
                await self.cycles(32)
                n += 32
                if n >= self.wait_limit():
                    self.timed_out(f"timeout waiting for a free kick slot (STATUS 0x{st:x})")
        addr = self.place(data, addr)
        size = len(data) if size is None else size
        await self.csr.write("CMD_ADDR", addr)
        await self.csr.write("CMD_KICK", size)
        if to_model:
            self.model_kick(addr, size)
        return addr

    def model_kick(self, addr: int, size: int):
        if self.override is not None or self.model.halted:
            return  # halted: the hardware keeps the kick queued, the model ignores it
        words = []
        state0 = [self.model.state(i) for i in range(REGMAP.state_size)]
        if size and size % 8 == 0:
            words = list(struct.unpack(f"<{size // 4}I", self.mmem.read(addr, size)))
        self.model.kick(addr, size)
        self.sample_coverage(addr, words, state0)

    def sample_coverage(self, addr: int, words: list[int], state0: list[int]):
        """Functional coverage of the draws the model executed (all, or those before the command the model
        halted on)."""
        end = len(words)
        if self.model.halted and self.model.err_cmd_addr >= addr:
            end = min(end, (self.model.err_cmd_addr - addr) // 4)
        for pos, op, st in iter_draws(words, state0):
            if pos >= end:
                break
            if op == OP_RECT:
                p0, p1 = words[pos + 1], words[pos + 2]
                self.cov.sample_rect(st, (_s16(p0), _s16(p0 >> 16), _s16(p1), _s16(p1 >> 16)))
            elif op == OP_PATH:
                payload = words[pos + 1:pos + 5]
                n = payload[3] & 0xFF_FFFF
                raw = self.mmem.read(payload[2], 16 * n) if n else b""
                edges = list(struct.iter_unpack("<4i", raw))
                self.cov.sample_path(st, payload, edges)
        self.cov.save()

    def model_prefix(self, addr: int, nwords: int):
        """Run the model on the first nwords command words of the buffer at addr. An odd count is padded
        with a NOP placed in the model's memory only for the duration of the call."""
        if nwords == 0:
            return
        if nwords % 2:
            saved = self.mmem.read(addr + 4 * nwords, 4)
            self.mmem.write(addr + 4 * nwords, bytes(4))   # NOP, SKIP = 0
            self.model.kick(addr, 4 * (nwords + 1))
            self.mmem.write(addr + 4 * nwords, saved)
        else:
            self.model.kick(addr, 4 * nwords)

    # ---- waiting ----
    async def wait_done(self, limit: int = 5_000_000, poll: int = 16):
        """Until STATUS.IDLE, or HALTED with STATUS.CMD_ACTIVE = 0 (v1.4, Q-RTL-9: issued draws finish after a
        halt; kicks still queued keep IDLE low). CMD_ACTIVE is cross-checked against the core's `drained`
        (white-box). G2D_WAIT_LIMIT overrides the cycle limit (mutation runs use a short one so a hanging
        mutant fails fast)."""
        limit = self.wait_limit(limit)
        n = 0
        drained = None if self.blackbox else self.top.u_core.drained
        while True:
            st = await self.csr.read("STATUS")
            if CsrIf.field("STATUS", "IDLE", st):
                return
            if CsrIf.field("STATUS", "HALTED", st) and not CsrIf.field("STATUS", "CMD_ACTIVE", st) and self.axi.idle():
                await self.cycles(4)
                st = await self.csr.read("STATUS")
                if not CsrIf.field("STATUS", "CMD_ACTIVE", st) and self.axi.idle():
                    assert drained is None or int(drained.value), "STATUS.CMD_ACTIVE = 0 while the core is not drained"
                    return
            await self.cycles(poll)
            n += poll
            if n >= limit:
                self.timed_out("timeout waiting for the GPU")

    @staticmethod
    def wait_limit(default: int = 5_000_000) -> int:
        """Cycle limit of every wait on the GPU (G2D_WAIT_LIMIT overrides it)."""
        return int(os.environ.get("G2D_WAIT_LIMIT", default))

    @staticmethod
    def timed_out(msg: str):
        """A wait hit its limit. G2D_FAIL_FAST=1 (mutation runs) ends the simulation at once instead of
        letting every later test of the module run into the same hang."""
        if os.environ.get("G2D_FAIL_FAST") == "1":
            print(f"FAIL_FAST: {msg}", flush=True)
            os._exit(3)
        raise AssertionError(msg)

    async def soft_reset(self):
        await self.csr.write("CTRL", 1)
        n = 0
        while CsrIf.field("STATUS", "SOFT_RESET_BUSY", await self.csr.read("STATUS")):
            await self.cycles(8)
            n += 8
            if n >= self.wait_limit():
                self.timed_out("timeout waiting for the soft reset to finish (SOFT_RESET_BUSY)")
        self.model.soft_reset()
        self.override = None

    async def hard_reset(self, cycles: int = 3):
        """Assert rst_n for a few cycles at an arbitrary point (also mid-transaction: both AXI sides reset),
        then restore the configuration start() sets. Memory is kept; the model is reset to power-on."""
        for n in ("rst_n", "rst_cfg_n", "rst_mem_n"):
            getattr(self.dut, n).value = 0
        await self.cycles(cycles)
        await self._release_resets()
        self.model.reset()
        self.win = (self.model.addr_win_lo, self.model.addr_win_hi)
        self.override = None
        if self.base_hi:
            await self.csr.write("MEM_BASE_HI", self.base_hi)
        await self.csr.write("PERF_CTRL", 0b011)
        self.perf_checked = False     # the AXI model's beat counts span the reset

    # ---- checking ----
    def expected_regs(self):
        if self.override is not None:
            o = self.override
            return dict(ERR_STATUS=o["err_status"], ERR_ADDR=o["err_addr"], ERR_CMD_ADDR=o["err_cmd_addr"],
                        HALTED=1)
        m = self.model
        return dict(ERR_STATUS=m.err_status, ERR_ADDR=m.err_addr, ERR_CMD_ADDR=m.err_cmd_addr, HALTED=int(m.halted))

    async def check(self, mem: bool = True, regs: bool = True, perf: bool | None = None, state: bool = True):
        self.cov.save()
        if regs:
            exp = self.expected_regs()
            st = await self.csr.read("STATUS")
            got = dict(ERR_STATUS=await self.csr.read("ERR_STATUS") & ~ERR["KICK_OVF"],
                       ERR_ADDR=await self.csr.read("ERR_ADDR"), ERR_CMD_ADDR=await self.csr.read("ERR_CMD_ADDR"),
                       HALTED=CsrIf.field("STATUS", "HALTED", st))
            assert got == exp, f"registers: got {_hex(got)}, expected {_hex(exp)}"
            assert await self.csr.read("FENCE_SEQ") == self.model.fence_seq
            assert CsrIf.field("IRQ_STATUS", "FENCE", await self.csr.read("IRQ_STATUS")) == int(self.model.irq_fence)
            # v1.8 debug (arch.md §9.1): no RAM read/write collision, and the write signature matches the W beats
            # the memory model accepted
            assert await self.csr.read("DBG_STATUS") == 0, "DBG_STATUS.RAM_COLL set (RAM read/write collision)"
            if self.axi.idle():
                sig = await self.csr.read("WR_SIG")
                assert sig == self.axi.wsig, f"WR_SIG {sig:#010x} != {self.axi.wsig:#010x} (W beats seen)"
        if state:
            self.check_state()
        if mem:
            self.check_memory()
        if perf if perf is not None else self.perf_checked:
            await self.csr.write("PERF_CTRL", 0b101)  # snapshot, keep enabled
            draws = await self.csr.read("PERF_DRAWS")
            pix = await self.csr.read("PERF_PIXELS_OUT")
            wb = await self.csr.read("PERF_WR_BEATS")
            rb = await self.csr.read("PERF_RD_BEATS")
            assert draws == self.model.stats.draws & 0xFFFF_FFFF, f"PERF_DRAWS {draws} != {self.model.stats.draws}"
            assert pix == self.model.stats.pixels_out & 0xFFFF_FFFF, f"PERF_PIXELS_OUT {pix} != {self.model.stats.pixels_out}"
            assert wb == self.axi.stats["w_beats"], f"PERF_WR_BEATS {wb} != {self.axi.stats['w_beats']}"
            # R beats delivered by the memory model up to the snapshot (CSR reads do not use AXI4)
            assert rb == self.axi.stats["r_beats"], f"PERF_RD_BEATS {rb} != {self.axi.stats['r_beats']}"
            # AXI idle and no draw in flight: these counters are frozen, so the references compare exactly.
            # BUSY_CYCLES only when STATUS.IDLE (halted with queued kicks the GPU stays busy after the snapshot)
            names = ["RD_STALL", "WR_STALL"] if self.blackbox else ["DRAIN_CYC", "RD_STALL", "WR_STALL"]
            if not self.blackbox and CsrIf.field("STATUS", "IDLE", await self.csr.read("STATUS")):
                names.append("BUSY_CYCLES")
            stall = self.core_stall if self.async_mode else self.axi.stats
            ref = dict(BUSY_CYCLES=self.ref["busy"], DRAIN_CYC=self.ref["drain"], RD_STALL=stall["rd_stall"],
                       WR_STALL=stall["wr_stall"])
            got = {n: await self.csr.read(f"PERF_{n}") for n in names}
            exp = {n: ref[n] & 0xFFFF_FFFF for n in names}
            assert got == exp, f"perf counters {got} != {exp}"

    def check_state(self):
        """White-box: the state registers (128 since v1.6b) equal the model's LOAD_STATE view (skipped while the
        hardware is halted on a Phase 2 restriction the model did not execute up to)."""
        if self.override is not None or self.blackbox:
            return
        v = int(self.top.u_core.u_state.state_o.value)
        for i in range(REGMAP.state_size):
            got = (v >> (32 * i)) & 0xFFFF_FFFF
            assert got == self.model.state(i), f"state[{i}] = 0x{got:08x}, model 0x{self.model.state(i):08x}"

    def _masked(self, a):
        return any(lo <= a <= hi for lo, hi in self.mask)

    def check_memory(self):
        pages = {b - self.off for b in self.dmem.page_bases()} | set(self.mmem.pages())
        diffs = []
        for base in sorted(pages):
            got = self.read_dut(base, 4096)
            exp = self.mmem.read(base, 4096)
            if got != exp:
                for i in range(4096):
                    if got[i] != exp[i] and not self._masked(base + i):
                        diffs.append((base + i, got[i], exp[i]))
                        if len(diffs) >= 16:
                            break
            if len(diffs) >= 16:
                break
        if diffs:
            lines = []
            for a, g, e in diffs:
                where = next(((t, t.locate(a)) for t in self.targets if t.locate(a) is not None), None)
                loc = f" target@0x{where[0].addr:x} fmt 0x{where[0].fmt:02x} {where[1]}" if where else ""
                lines.append(f"  0x{a:08x}: got 0x{g:02x} expected 0x{e:02x}{loc}")
            hit = [t for t in self.targets if any(t.locate(a) is not None for a, _, _ in diffs)]
            self.dump_pngs(hit)
            raise AssertionError("memory mismatch (first 16):\n" + "\n".join(lines))

    def dump_pngs(self, targets):
        try:
            import numpy as np

            from g2d_image import buffer_argb, save_png
        except Exception:  # noqa: BLE001  (PNG dumps are best effort)
            return
        out = ROOT / "build" / "dv" / "fail" / self.test_name
        out.mkdir(parents=True, exist_ok=True)
        for i, t in enumerate(targets):
            if t.w * t.h > 4096 * 4096:
                continue
            exp = buffer_argb(self.mmem.read, t.addr, t.w, t.h, t.stride, t.fmt)
            got = buffer_argb(self.read_dut, t.addr, t.w, t.h, t.stride, t.fmt)
            diff = np.where(exp != got, np.uint32(0xFFFF0000), np.uint32(0xFF000000))
            for name, img in (("expected", exp), ("actual", got), ("diff", diff)):
                save_png(out / f"t{i}_{t.addr:08x}_{name}.png", img)


def _hex(d):
    return {k: hex(v) for k, v in d.items()}
