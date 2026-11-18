# SPDX-License-Identifier: Apache-2.0
"""cocotb test of the generated CSR register file (rtl/csr/generated/g2d_csr_regs.sv).

The expected behaviour is derived from spec/regs.yaml through scripts/gen_regs.py's parser, so the
test follows the YAML: reset values, byte strobes, rw/ro/const/w1c/wo semantics, unmapped-offset error.
Run through pytest (runner at the bottom) or `make test`.
"""

import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import gen_regs  # noqa: E402

REGMAP = gen_regs.load(ROOT / "spec" / "regs.yaml")
REG2HW = gen_regs.struct_layout(REGMAP, "reg2hw")
HW2REG = gen_regs.struct_layout(REGMAP, "hw2reg")
HW2REG_W = sum(w for _, w in HW2REG.values())
REGMAP_BY_NAME = {r.name: r for r in REGMAP.csr}

# g2d_reg_req_t {valid, write, addr[11:2], wdata[31:0], wstrb[3:0]}, first member = MSB
ADDR_W = REGMAP.csr_addr_bits - 2


def pack_req(valid, write, offset, wdata=0, wstrb=0xF):
    v = wstrb & 0xF
    v |= (wdata & 0xFFFFFFFF) << 4
    v |= ((offset >> 2) & ((1 << ADDR_W) - 1)) << 36
    v |= (write & 1) << (36 + ADDR_W)
    v |= (valid & 1) << (37 + ADDR_W)
    return v


def unpack_rsp(v):
    # g2d_reg_rsp_t {ready, rdata[31:0], error}
    return (v >> 33) & 1, (v >> 1) & 0xFFFFFFFF, v & 1


def field_get(vec, layout, key):
    lsb, w = layout[key]
    return (vec >> lsb) & ((1 << w) - 1)


def field_put(vec, layout, key, value):
    lsb, w = layout[key]
    m = ((1 << w) - 1) << lsb
    return (vec & ~m) | ((value << lsb) & m)


class Csr:
    def __init__(self, dut):
        self.dut = dut
        self.hw2reg = 0

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk_i, 10, unit="ns").start())
        self.dut.req_i.value = 0
        self.dut.hw2reg_i.value = 0
        self.dut.soft_clr_i.value = 0
        self.dut.rst_ni.value = 0
        await Timer(25, unit="ns")
        self.dut.rst_ni.value = 1
        await FallingEdge(self.dut.clk_i)

    def set_hw(self, key, value):
        self.hw2reg = field_put(self.hw2reg, HW2REG, key, value)
        self.dut.hw2reg_i.value = self.hw2reg

    def reg2hw(self, key):
        return field_get(int(self.dut.reg2hw_o.value), REG2HW, key)

    async def access(self, write, offset, wdata=0, wstrb=0xF):
        """Drive one request at the falling edge, sample the combinational response, commit at the rising edge."""
        self.dut.req_i.value = pack_req(1, write, offset, wdata, wstrb)
        await Timer(1, unit="ns")
        ready, rdata, error = unpack_rsp(int(self.dut.rsp_o.value))
        assert ready == 1
        await RisingEdge(self.dut.clk_i)
        await FallingEdge(self.dut.clk_i)
        self.dut.req_i.value = 0
        return rdata, error

    async def read(self, offset):
        return await self.access(0, offset)

    async def write(self, offset, data, wstrb=0xF):
        return (await self.access(1, offset, data, wstrb))[1]


def readable_value(reg, hw_values):
    """Expected read value of a register given current rw/w1c state and ro inputs."""
    v = 0
    for f in reg.fields:
        if f.access in ("rw", "w1c", "const"):
            v |= hw_values.get((reg.name, f.name), f.reset) << f.lsb
        elif f.access == "ro":
            v |= hw_values.get((reg.name, f.name), 0) << f.lsb
    return v


@cocotb.test()
async def reset_values(dut):
    csr = Csr(dut)
    await csr.start()
    for reg in REGMAP.csr:
        rdata, err = await csr.read(reg.addr)
        assert err == 0, reg.name
        assert rdata == readable_value(reg, {}), f"{reg.name}: 0x{rdata:08x}"


@cocotb.test()
async def rw_byte_strobes(dut):
    csr = Csr(dut)
    await csr.start()
    rng = random.Random(1)
    rw_regs = [r for r in REGMAP.csr if all(f.access == "rw" for f in r.fields)]
    for reg in rw_regs:
        mask = sum(f.mask for f in reg.fields)
        model = reg.reset
        for _ in range(16):
            data, strb = rng.getrandbits(32), rng.getrandbits(4)
            bmask = sum(0xFF << (8 * i) for i in range(4) if strb >> i & 1)
            assert await csr.write(reg.addr, data, strb) == 0
            model = (model & ~bmask) | (data & bmask)
            rdata, _ = await csr.read(reg.addr)
            assert rdata == model & mask, f"{reg.name}"
            for f in reg.fields:
                exp = (model & f.mask) >> f.lsb
                assert csr.reg2hw(f"{reg.name.lower()}.{f.name.lower()}.q") == exp


@cocotb.test()
async def ro_and_const(dut):
    csr = Csr(dut)
    await csr.start()
    rng = random.Random(2)
    values = {}
    for reg in REGMAP.csr:
        for f in reg.fields:
            if f.access == "ro":
                v = rng.getrandbits(f.width)
                values[(reg.name, f.name)] = v
                csr.set_hw(f"{reg.name.lower()}.{f.name.lower()}.d", v)
    await Timer(1, unit="ns")
    for reg in REGMAP.csr:
        if reg.has("ro", "const"):
            await csr.write(reg.addr, 0xFFFFFFFF ^ readable_value(reg, values))  # must be ignored for ro/const
            rdata, _ = await csr.read(reg.addr)
            exp = readable_value(reg, values) if all(f.access in ("ro", "const") for f in reg.fields) else None
            if exp is not None:
                assert rdata == exp, reg.name


@cocotb.test()
async def w1c_set_clear_priority(dut):
    csr = Csr(dut)
    await csr.start()
    for reg in [r for r in REGMAP.csr if r.has("w1c")]:
        for f in [f for f in reg.fields if f.access == "w1c"]:
            key = f"{reg.name.lower()}.{f.name.lower()}"
            ones = (1 << f.width) - 1
            # hardware set pulse
            csr.set_hw(f"{key}.set", ones)
            await RisingEdge(dut.clk_i)
            await FallingEdge(dut.clk_i)
            csr.set_hw(f"{key}.set", 0)
            rdata, _ = await csr.read(reg.addr)
            assert (rdata & f.mask) >> f.lsb == ones, key
            assert csr.reg2hw(f"{key}.q") == ones
            # writing 0 does not clear
            await csr.write(reg.addr, 0)
            assert ((await csr.read(reg.addr))[0] & f.mask) >> f.lsb == ones
            # set and clear in the same cycle: set wins
            csr.set_hw(f"{key}.set", ones)
            await csr.write(reg.addr, f.mask)
            csr.set_hw(f"{key}.set", 0)
            assert ((await csr.read(reg.addr))[0] & f.mask) >> f.lsb == ones, f"{key} set priority"
            # write 1 clears
            await csr.write(reg.addr, f.mask)
            assert ((await csr.read(reg.addr))[0] & f.mask) == 0, f"{key} clear"


@cocotb.test()
async def w1c_soft_clear(dut):
    """soft_clr_i returns every w1c field to its reset value and leaves rw fields alone."""
    csr = Csr(dut)
    await csr.start()
    w1c = [(r, f) for r in REGMAP.csr for f in r.fields if f.access == "w1c"]
    for r, f in w1c:
        csr.set_hw(f"{r.name.lower()}.{f.name.lower()}.set", (1 << f.width) - 1)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    for r, f in w1c:
        csr.set_hw(f"{r.name.lower()}.{f.name.lower()}.set", 0)
    await csr.write(REGMAP_BY_NAME["SCRATCH"].addr, 0x12345678)
    for r in {r.name: r for r, _ in w1c}.values():
        assert (await csr.read(r.addr))[0] != 0, r.name
    dut.soft_clr_i.value = 1
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.soft_clr_i.value = 0
    for r in {r.name: r for r, _ in w1c}.values():
        assert (await csr.read(r.addr))[0] == r.reset, r.name
    assert (await csr.read(REGMAP_BY_NAME["SCRATCH"].addr))[0] == 0x12345678


@cocotb.test()
async def wo_strobe(dut):
    csr = Csr(dut)
    await csr.start()
    rng = random.Random(3)
    for reg in [r for r in REGMAP.csr if r.has("wo")]:
        for f in [f for f in reg.fields if f.access == "wo"]:
            key = f"{reg.name.lower()}.{f.name.lower()}"
            data = rng.getrandbits(32)
            dut.req_i.value = pack_req(1, 1, reg.addr, data, 0xF)
            await RisingEdge(dut.clk_i)
            dut.req_i.value = 0
            await Timer(1, unit="ns")
            assert csr.reg2hw(f"{key}.qe") == 1, key
            assert csr.reg2hw(f"{key}.q") == (data & f.mask) >> f.lsb, key
            await RisingEdge(dut.clk_i)
            await Timer(1, unit="ns")
            assert csr.reg2hw(f"{key}.qe") == 0, f"{key} strobe must last one cycle"
            await FallingEdge(dut.clk_i)
            rdata, _ = await csr.read(reg.addr)
            assert rdata & f.mask == 0, f"{key} reads as 0"


@cocotb.test()
async def unmapped_offsets_error(dut):
    csr = Csr(dut)
    await csr.start()
    mapped = {r.addr for r in REGMAP.csr}
    unmapped = [a for a in range(0, 1 << REGMAP.csr_addr_bits, 4) if a not in mapped]
    for a in random.Random(4).sample(unmapped, 64):
        rdata, err = await csr.read(a)
        assert err == 1 and rdata == 0, hex(a)
        assert await csr.write(a, 0xFFFFFFFF) == 1, hex(a)
    for a in sorted(mapped):
        assert (await csr.read(a))[1] == 0


# ------------------------------------------------------------------------------------------------
# pytest entry point
# ------------------------------------------------------------------------------------------------

def test_csr_regs():
    import runner
    runner.run("g2d_csr_regs", "test_csr_regs", tag="regs",
               sources=[ROOT / "rtl/pkg/generated/g2d_csr_pkg.sv", ROOT / "rtl/csr/generated/g2d_csr_regs.sv"])
