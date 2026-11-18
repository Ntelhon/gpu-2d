# SPDX-License-Identifier: Apache-2.0
"""Exhaustive checks of the g2d_pkg arithmetic (rendering.md §2, §3.4, §5.3) against the golden model:
- fmt_expand: every format x every raw value (8/16-bit) or every byte value (32-bit)
- fmt_compress: every format x every value of every channel (other channels random), plus random ARGB
- apply_global_alpha: every mode x every (A, g) pair
- mul8: every (a, b) pair
- fmt_compress_d (dither, rendering.md §3.6): every target format x every value of every colour channel at every
  Bayer position
- fmt_valid / fmt_tgt_valid / fmt_indexed / fmt_lg_bits / fmt_lg_bytes: all 256 codes
"""

import random

import cocotb
from cocotb.triggers import Timer

import g2d_model as gm
from g2d_cmd import FMT, FMT_BITS, INDEXED_FMTS, TGT_FMTS, YUV_FMTS


async def settle():
    await Timer(1, unit="ns")


@cocotb.test()
async def compress_exhaustive(dut):
    rng = random.Random(5)
    for fmt in TGT_FMTS:
        dut.fmt.value = fmt
        cases = []
        for sh in (0, 8, 16, 24):
            for v in range(256):
                c = rng.getrandbits(32) & ~(0xFF << sh) | (v << sh)
                cases.append(c)
        cases += [rng.getrandbits(32) for _ in range(2000)]
        for c in cases:
            dut.argb.value = c
            await settle()
            exp = gm.compress(fmt, c)
            assert int(dut.packed_o.value) == exp, f"fmt 0x{fmt:02x} argb 0x{c:08x}: {int(dut.packed_o.value):x} != {exp:x}"


@cocotb.test()
async def dither_exhaustive(dut):
    """fmt_compress_d with dither: every target format x every value of every channel x every Bayer position."""
    rng = random.Random(9)
    for fmt in TGT_FMTS:
        dut.fmt.value = fmt
        for dy in range(4):
            dut.dy.value = dy
            for dx in range(4):
                dut.dx.value = dx
                cases = [rng.getrandbits(32) & ~(0xFF << sh) | (v << sh) for sh in (0, 8, 16, 24) for v in range(256)]
                for c in cases:
                    dut.argb.value = c
                    await settle()
                    exp = gm.compress_dither(fmt, c, dx, dy)
                    assert int(dut.dither_o.value) == exp, f"fmt 0x{fmt:02x} argb 0x{c:08x} ({dx},{dy})"


@cocotb.test()
async def expand_exhaustive(dut):
    """fmt_expand: every format x every raw value up to 16 bits; 24- (v1.6b) and 32-bit formats every value of every
    byte.
    Sub-byte formats get their bits right-aligned, indexed formats the CLUT word (rendering.md §3.5)."""
    rng = random.Random(8)
    for fmt in FMT.values():
        dut.fmt.value = fmt
        bits = 32 if fmt in YUV_FMTS else FMT_BITS[fmt]   # YUV (v1.6b): the assembled {A, Y, U, V} word
        if fmt in INDEXED_FMTS:  # the texel unit receives the CLUT word: identity
            cases = [rng.getrandbits(32) for _ in range(2000)]
            for raw in cases:
                dut.argb.value = raw
                await settle()
                assert int(dut.expand_o.value) == raw, hex(fmt)
            continue
        if bits < 24:
            cases = list(range(1 << bits))
        else:
            cases = [rng.getrandbits(bits) & ~(0xFF << sh) | (v << sh) for sh in range(0, bits, 8) for v in range(256)]
        for raw in cases:
            # bits above the pixel size must not matter
            dut.argb.value = raw | (rng.getrandbits(32) << bits) & 0xFFFF_FFFF
            await settle()
            exp = gm.expand(fmt, raw)
            assert int(dut.expand_o.value) == exp, f"fmt 0x{fmt:02x} raw 0x{raw:x}: {int(dut.expand_o.value):08x} != {exp:08x}"


@cocotb.test()
async def global_alpha_exhaustive(dut):
    rng = random.Random(6)
    for mode in (0, 1, 2):
        dut.mode.value = mode
        for a in range(256):
            for g in range(256):
                c = (a << 24) | rng.getrandbits(24)
                dut.argb.value = c
                dut.g.value = g
                await settle()
                exp = gm.apply_global_alpha(c, mode, g)
                assert int(dut.alpha_o.value) == exp, (mode, hex(c), g)


@cocotb.test()
async def mul8_exhaustive(dut):
    for a in range(256):
        dut.a.value = a
        for b in range(256):
            dut.b.value = b
            await settle()
            assert int(dut.mul8_o.value) == gm.mul8(a, b), (a, b)


@cocotb.test()
async def fmt_codes(dut):
    for f in range(256):
        dut.fmt.value = f
        await settle()
        assert int(dut.valid_o.value) == int(f in FMT.values()), hex(f)
        assert int(dut.tgt_o.value) == int(f in TGT_FMTS), hex(f)
        if f in FMT.values():
            # 24-bit (v1.6b): read as 32 bits (the address comes from fmt_b24)
            assert 1 << int(dut.lg_bits_o.value) == (32 if f & 0x80 else FMT_BITS[f]), hex(f)
            assert int(dut.indexed_o.value) == int(f in INDEXED_FMTS), hex(f)
            if f in TGT_FMTS:
                assert 1 << int(dut.lg_o.value) == FMT_BITS[f] // 8, hex(f)


def test_pkg():
    import runner
    runner.run("g2d_pkg_tb", "test_pkg")
