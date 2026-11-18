# SPDX-License-Identifier: Apache-2.0
"""CSR access over AXI4-Lite for cocotb benches. Register and field names come from spec/regs.yaml.

    csr = CsrIf(dut, prefix="s_axil", clk=dut.clk, rst=dut.rst_n)
    await csr.write("SCRATCH", 0x1234)
    v = await csr.read("STATUS")
    csr.field("STATUS", "KICK_FREE", v)
"""

from __future__ import annotations

import logging
import os
import random

from cocotb.triggers import Lock
from cocotbext.axi import AxiBus, AxiLiteBus, AxiLiteMaster, AxiMaster

from g2d_cmd import CSR, REGMAP  # noqa: F401  (REGMAP re-exported for tests)

RESP_OKAY, RESP_SLVERR = 0, 2


class CsrError(Exception):
    pass


# v1.9: configuration bus of the DUT (g2d_top_<bus> wrappers, rtl/wrap/generated): G2D_CSR_BUS = axil (g2d_top),
# axi (cocotbext-axi AXI4 master on s_axi_*), or a bus of dv/common/bus_bfm.py behind its wrapper port prefix.
BUS_PREFIX = {"apb": "s_apb_", "ahb": "s_ahb_", "obi": "s_obi_", "regintf": "s_", "wb": "s_", "tlul": "s_tl_"}


class _Prefixed:
    """dut signals seen without the wrapper's port prefix (s_apb_psel -> psel) for the bus BFMs."""

    def __init__(self, dut, prefix):
        object.__setattr__(self, "_d", dut)
        object.__setattr__(self, "_p", prefix)

    def __getattr__(self, n):
        return getattr(self._d, self._p + n)


class _BfmMaster:
    """CSR accesses through a bus BFM, one at a time (lock)."""

    def __init__(self, bus, dut, clk):
        from bus_bfm import BFMS
        self.bfm = BFMS[bus](_Prefixed(dut, BUS_PREFIX[bus]), clk, random.Random(int(os.environ.get("SEED", "1"))))
        self.lock = Lock()

    async def read(self, addr):
        async with self.lock:
            return await self.bfm.read(addr)

    async def write(self, addr, value, strb):
        async with self.lock:
            return await self.bfm.write(addr, value, strb)


class CsrIf:
    def __init__(self, dut, prefix: str, clk, rst):
        self.bus = os.environ.get("G2D_CSR_BUS", "axil")
        self.bfm = None
        if self.bus == "axil":
            self.master = AxiLiteMaster(AxiLiteBus.from_prefix(dut, prefix), clk, rst, reset_active_level=False)
            logging.getLogger(f"cocotb.{dut._name}.{prefix}").setLevel(logging.WARNING)
        elif self.bus == "axi":
            self.master = AxiMaster(AxiBus.from_prefix(dut, "s_axi"), clk, rst, reset_active_level=False)
            logging.getLogger(f"cocotb.{dut._name}.s_axi").setLevel(logging.WARNING)
        else:
            self.bfm = _BfmMaster(self.bus, dut, clk)

    @staticmethod
    def addr(name_or_offset) -> int:
        return CSR[name_or_offset].addr if isinstance(name_or_offset, str) else int(name_or_offset)

    async def read_resp(self, reg) -> tuple[int, int]:
        if self.bfm is not None:
            v, err = await self.bfm.read(self.addr(reg))
            return v, RESP_SLVERR if err else RESP_OKAY
        r = await self.master.read(self.addr(reg), 4)
        return int.from_bytes(r.data, "little"), int(r.resp)

    async def write_resp(self, reg, value: int, strb: int = 0xF) -> int:
        a = self.addr(reg)
        if self.bfm is not None:
            if self.bus == "ahb" and strb not in (0x1, 0x2, 0x4, 0x8, 0x3, 0xC, 0xF):   # AHB: one size per transfer
                resp = RESP_OKAY
                for i in range(4):
                    if strb >> i & 1:
                        resp = max(resp, RESP_SLVERR if await self.bfm.write(a, value, 1 << i) else RESP_OKAY)
                return resp
            return RESP_SLVERR if await self.bfm.write(a, value & 0xFFFFFFFF, strb) else RESP_OKAY
        data = (value & 0xFFFFFFFF).to_bytes(4, "little")
        if strb == 0xF:
            w = await self.master.write(a, data)
        else:
            # cocotbext-axi derives WSTRB from the byte range; write the strobed bytes one run at a time
            resp = RESP_OKAY
            i = 0
            while i < 4:
                if strb >> i & 1:
                    j = i
                    while j < 4 and strb >> j & 1:
                        j += 1
                    w = await self.master.write(a + i, data[i:j])
                    resp = max(resp, int(w.resp))
                    i = j
                else:
                    i += 1
            return resp
        return int(w.resp)

    async def read(self, reg) -> int:
        v, resp = await self.read_resp(reg)
        if resp != RESP_OKAY:
            raise CsrError(f"read {reg}: resp {resp}")
        return v

    async def write(self, reg, value: int) -> None:
        resp = await self.write_resp(reg, value)
        if resp != RESP_OKAY:
            raise CsrError(f"write {reg}: resp {resp}")

    @staticmethod
    def field(reg: str, fname: str, value: int) -> int:
        f = next(f for f in CSR[reg].fields if f.name == fname)
        return (value & f.mask) >> f.lsb

    @staticmethod
    def pack(reg: str, **fields) -> int:
        v = 0
        by = {f.name: f for f in CSR[reg].fields}
        for k, val in fields.items():
            v |= (int(val) << by[k].lsb) & by[k].mask
        return v
