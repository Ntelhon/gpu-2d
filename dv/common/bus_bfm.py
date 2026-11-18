# SPDX-License-Identifier: Apache-2.0
"""Bus-functional models (masters) for the configuration-side adapter benches (v1.9, dv/tests/test_adapt.py).

Every BFM offers the same calls:
    err = await bfm.write(addr, data, strb)     # 32-bit word access, strb = byte lanes
    data, err = await bfm.read(addr)
    bfm.back_to_back = True                     # next access starts in the cycle after the previous one ends
    bfm.abort()                                 # drop the access in flight (bench resets the DUT)
Signals are sampled and driven at the rising clock edge (the convention of dv/common/axi_mem.py). Each BFM also
checks the slave-side rules of its bus that the adapter must keep (e.g. APB: PREADY only ends an access phase).
"""

from __future__ import annotations

import random

from cocotb.triggers import RisingEdge


class BusProtocolError(Exception):
    pass


class BfmBase:
    partial_writes = True

    def __init__(self, dut, clk, rng: random.Random):
        self.dut, self.clk, self.rng = dut, clk, rng
        self.back_to_back = False
        self._abort = False
        self.timeout = 5000

    def abort(self):
        self._abort = True
        self.idle()

    def random_strb(self, rng, addr):
        return rng.randrange(16)

    def idle(self):
        raise NotImplementedError


class ApbBfm(BfmBase):
    """APB4 requester: setup phase (PSEL, no PENABLE), access phase until PREADY. Reads drive PSTRB = 0."""

    def __init__(self, dut, clk, rng):
        super().__init__(dut, clk, rng)
        self.idle()

    def idle(self):
        d = self.dut
        d.psel.value = 0
        d.penable.value = 0
        d.pwrite.value = 0
        d.paddr.value = 0
        d.pwdata.value = 0
        d.pstrb.value = 0
        d.pprot.value = 0

    async def _xfer(self, write, addr, data, strb):
        d = self.dut
        self._abort = False
        d.psel.value = 1
        d.penable.value = 0
        d.pwrite.value = int(write)
        d.paddr.value = addr
        d.pwdata.value = data if write else self.rng.getrandbits(32)
        d.pstrb.value = strb if write else 0
        d.pprot.value = self.rng.randrange(8)
        await RisingEdge(self.clk)
        d.penable.value = 1
        n = 0
        while True:
            await RisingEdge(self.clk)
            if self._abort:
                return 0, False
            if int(d.pready.value):
                break
            n += 1
            if n > self.timeout:
                raise BusProtocolError("APB: PREADY never came")
        rdata, err = int(d.prdata.value), bool(int(d.pslverr.value))
        if not self.back_to_back:
            self.idle()
        else:
            d.penable.value = 0
        return rdata, err

    async def write(self, addr, data, strb):
        return (await self._xfer(True, addr, data, strb))[1]

    async def read(self, addr):
        return await self._xfer(False, addr, 0, 0)


class ObiBfm(BfmBase):
    """OBI 1.x manager: req held with stable signals until gnt; then the response (rvalid) is awaited."""

    def __init__(self, dut, clk, rng):
        super().__init__(dut, clk, rng)
        self.idle()

    def idle(self):
        d = self.dut
        d.req.value = 0
        d.we.value = 0
        d.addr.value = 0
        d.be.value = 0
        d.wdata.value = 0

    async def _xfer(self, write, addr, data, strb):
        d = self.dut
        self._abort = False
        d.req.value = 1
        d.we.value = int(write)
        d.addr.value = addr
        d.be.value = strb if write else 0xF
        d.wdata.value = data if write else self.rng.getrandbits(32)
        n = 0
        while True:
            await RisingEdge(self.clk)
            if self._abort:
                return 0, False
            if int(d.rvalid.value):
                raise BusProtocolError("OBI: rvalid before the grant")
            if int(d.gnt.value):
                break
            n += 1
            if n > self.timeout:
                raise BusProtocolError("OBI: no grant")
        self.idle()
        n = 0
        while True:
            await RisingEdge(self.clk)
            if self._abort:
                return 0, False
            if int(d.rvalid.value):
                return int(d.rdata.value), bool(int(d.err.value))
            n += 1
            if n > self.timeout:
                raise BusProtocolError("OBI: no rvalid")

    async def write(self, addr, data, strb):
        return (await self._xfer(True, addr, data, strb))[1]

    async def read(self, addr):
        return await self._xfer(False, addr, 0, 0)


class RegIntfBfm(BfmBase):
    """PULP reg_intf requester: valid held with stable signals until ready; the response comes with ready."""

    def __init__(self, dut, clk, rng):
        super().__init__(dut, clk, rng)
        self.idle()

    def idle(self):
        d = self.dut
        d.reg_valid.value = 0
        d.reg_write.value = 0
        d.reg_addr.value = 0
        d.reg_wdata.value = 0
        d.reg_wstrb.value = 0

    async def _xfer(self, write, addr, data, strb):
        d = self.dut
        self._abort = False
        d.reg_valid.value = 1
        d.reg_write.value = int(write)
        d.reg_addr.value = addr
        d.reg_wdata.value = data if write else self.rng.getrandbits(32)
        d.reg_wstrb.value = strb if write else 0
        n = 0
        while True:
            await RisingEdge(self.clk)
            if self._abort:
                return 0, False
            if int(d.reg_ready.value):
                break
            n += 1
            if n > self.timeout:
                raise BusProtocolError("reg_intf: no ready")
        r = int(d.reg_rdata.value), bool(int(d.reg_error.value))
        self.idle()
        return r

    async def write(self, addr, data, strb):
        return (await self._xfer(True, addr, data, strb))[1]

    async def read(self, addr):
        return await self._xfer(False, addr, 0, 0)


class WbBfm(BfmBase):
    """Wishbone B4 pipelined master: stb until not stalled, cyc until ack / err. Checks ack and err are never
    both high and never come without a request taken."""

    def __init__(self, dut, clk, rng):
        super().__init__(dut, clk, rng)
        self.idle()

    def idle(self):
        d = self.dut
        d.wb_cyc.value = 0
        d.wb_stb.value = 0
        d.wb_we.value = 0
        d.wb_adr.value = 0
        d.wb_dat_w.value = 0
        d.wb_sel.value = 0

    async def _xfer(self, write, addr, data, strb):
        d = self.dut
        self._abort = False
        d.wb_cyc.value = 1
        d.wb_stb.value = 1
        d.wb_we.value = int(write)
        d.wb_adr.value = addr
        d.wb_dat_w.value = data if write else self.rng.getrandbits(32)
        d.wb_sel.value = strb if write else 0xF
        n = 0
        while True:
            await RisingEdge(self.clk)
            if self._abort:
                return 0, False
            if int(d.wb_ack.value) or int(d.wb_err.value):
                raise BusProtocolError("WB: ack / err before the request was taken")
            if not int(d.wb_stall.value):
                break
            n += 1
            if n > self.timeout:
                raise BusProtocolError("WB: stalled forever")
        d.wb_stb.value = 0
        n = 0
        while True:
            await RisingEdge(self.clk)
            if self._abort:
                return 0, False
            ack, err = int(d.wb_ack.value), int(d.wb_err.value)
            if ack and err:
                raise BusProtocolError("WB: ack and err together")
            if ack or err:
                r = int(d.wb_dat_r.value), bool(err)
                if not self.back_to_back:
                    self.idle()
                return r
            n += 1
            if n > self.timeout:
                raise BusProtocolError("WB: no ack")

    async def write(self, addr, data, strb):
        return (await self._xfer(True, addr, data, strb))[1]

    async def read(self, addr):
        return await self._xfer(False, addr, 0, 0)


class TlulBfm(BfmBase):
    """TileLink-UL manager: one A message (PutFullData / PutPartialData / Get, size 2), then the D message; d_ready
    random. Checks the D opcode, size and source echo and d_param = 0."""

    def __init__(self, dut, clk, rng):
        super().__init__(dut, clk, rng)
        self.p_dready = 0.7
        self.idle()

    def idle(self):
        d = self.dut
        d.a_valid.value = 0
        d.a_opcode.value = 0
        d.a_param.value = 0
        d.a_size.value = 0
        d.a_source.value = 0
        d.a_address.value = 0
        d.a_mask.value = 0
        d.a_data.value = 0
        d.d_ready.value = 0

    async def message(self, opcode, addr, data, mask):
        d = self.dut
        self._abort = False
        src = self.rng.getrandbits(8)
        d.a_valid.value = 1
        d.a_opcode.value = opcode
        d.a_param.value = 0
        d.a_size.value = 2
        d.a_source.value = src
        d.a_address.value = addr
        d.a_mask.value = mask
        d.a_data.value = data
        d.d_ready.value = 0
        n = 0
        while True:
            await RisingEdge(self.clk)
            if self._abort:
                return 0, False
            if int(d.a_ready.value):
                break
            n += 1
            if n > self.timeout:
                raise BusProtocolError("TL-UL: a_ready never came")
        d.a_valid.value = 0
        dready = 0
        n = 0
        while True:
            dready = int(self.rng.random() < self.p_dready or self.back_to_back)
            d.d_ready.value = dready
            await RisingEdge(self.clk)
            if self._abort:
                return 0, False
            if int(d.d_valid.value) and dready:
                exp_op = 1 if opcode == 4 else 0
                if int(d.d_opcode.value) != exp_op or int(d.d_size.value) != 2 or int(d.d_source.value) != src \
                        or int(d.d_param.value) != 0:
                    raise BusProtocolError(f"TL-UL: bad D fields op={int(d.d_opcode.value)} size="
                                           f"{int(d.d_size.value)} src={int(d.d_source.value)} (exp {exp_op}, {src})")
                r = int(d.d_data.value), bool(int(d.d_error.value))
                d.d_ready.value = 0
                return r
            n += 1
            if n > self.timeout:
                raise BusProtocolError("TL-UL: no D message")

    async def write(self, addr, data, strb):
        return (await self.message(0 if strb == 0xF else 1, addr, data, strb))[1]

    async def read(self, addr):
        return await self.message(4, addr, 0, 0xF)


class AhbBfm(BfmBase):
    """AHB-Lite manager (single subordinate, HREADY = HREADYOUT): address phase, then data phase until HREADYOUT.
    Checks the two-cycle ERROR response (HRESP high with HREADYOUT low, then high). Byte / half-word writes use
    HSIZE and HADDR[1:0] (random_strb returns only strobes an AHB transfer can produce)."""

    LEGAL = [0x1, 0x2, 0x4, 0x8, 0x3, 0xC, 0xF]

    def __init__(self, dut, clk, rng):
        super().__init__(dut, clk, rng)
        self.idle()

    def random_strb(self, rng, addr):
        return rng.choice(self.LEGAL)

    def idle(self):
        d = self.dut
        d.hsel.value = 0
        d.htrans.value = 0
        d.haddr.value = 0
        d.hwrite.value = 0
        d.hsize.value = 0
        d.hburst.value = 0
        d.hprot.value = 0
        d.hmastlock.value = 0
        d.hwdata.value = 0

    @staticmethod
    def size_off(strb):
        return {0x1: (0, 0), 0x2: (0, 1), 0x4: (0, 2), 0x8: (0, 3), 0x3: (1, 0), 0xC: (1, 2), 0xF: (2, 0)}[strb]

    async def transfer(self, write, addr, data, size, off):
        d = self.dut
        self._abort = False
        d.hsel.value = 1
        d.htrans.value = 2          # NONSEQ
        d.haddr.value = addr + off
        d.hwrite.value = int(write)
        d.hsize.value = size
        d.hburst.value = self.rng.choice([0, 1])
        d.hprot.value = self.rng.randrange(16)
        n = 0
        while True:                 # address phase ends at an edge with HREADY high
            await RisingEdge(self.clk)
            if self._abort:
                return 0, False
            if int(d.hreadyout.value):
                break
            n += 1
            if n > self.timeout:
                raise BusProtocolError("AHB: bus never ready")
        d.htrans.value = 0          # IDLE in the next address phase
        d.hsel.value = int(self.rng.random() < 0.5)
        d.hwdata.value = data if write else self.rng.getrandbits(32)
        seen_err1 = False
        n = 0
        while True:
            await RisingEdge(self.clk)
            if self._abort:
                return 0, False
            ready, resp = int(d.hreadyout.value), int(d.hresp.value)
            if resp and not ready:
                seen_err1 = True
            elif ready:
                if resp and not seen_err1:
                    raise BusProtocolError("AHB: ERROR without its first cycle")
                if seen_err1 and not resp:
                    raise BusProtocolError("AHB: ERROR first cycle not followed by the second")
                r = int(d.hrdata.value), bool(resp)
                if not self.back_to_back:
                    self.idle()
                return r
            elif seen_err1:
                raise BusProtocolError("AHB: HRESP dropped during ERROR")
            n += 1
            if n > self.timeout:
                raise BusProtocolError("AHB: data phase never ended")

    async def write(self, addr, data, strb):
        size, off = self.size_off(strb)
        return (await self.transfer(True, addr, data, size, off))[1]

    async def read(self, addr):
        return await self.transfer(False, addr, 0, 2, 0)


BFMS = {
    "apb": ApbBfm,
    "ahb": AhbBfm,
    "obi": ObiBfm,
    "regintf": RegIntfBfm,
    "wb": WbBfm,
    "tlul": TlulBfm,
}
