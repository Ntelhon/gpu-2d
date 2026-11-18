# SPDX-License-Identifier: Apache-2.0
"""Generic cocotb driver for valid/ready pipeline blocks with one input and one output stream.

    outs = await run_stream(dut, vectors, drive, sample, rng, p_in=0.9, p_out=0.8)

`drive(dut, vec)` sets the input payload signals, `sample(dut)` returns the output payload. The driver
holds in_valid/payload stable until accepted, applies random input gaps and output back-pressure, and
returns the outputs in arrival order (one per input). Signals: clk_i, in_valid_i, in_ready_o,
out_valid_o, out_ready_i.
"""

from __future__ import annotations

from cocotb.triggers import ReadOnly, RisingEdge


async def run_stream(dut, vectors, drive, sample, rng, p_in=1.0, p_out=1.0, limit_factor=50, n_out=None):
    n = len(vectors)
    n_out = n if n_out is None else n_out
    outs = []
    i = 0
    valid = False
    cycles = 0
    limit = limit_factor * (n + 100)
    while len(outs) < n_out:
        if not valid and i < n and rng.random() < p_in:
            drive(dut, vectors[i])
            valid = True
        dut.in_valid_i.value = int(valid)
        out_ready = int(rng.random() < p_out)
        dut.out_ready_i.value = out_ready
        await ReadOnly()
        accepted = valid and int(dut.in_ready_o.value)
        got = None
        if int(dut.out_valid_o.value) and out_ready:
            got = sample(dut)
        await RisingEdge(dut.clk_i)
        if accepted:
            valid = False
            i += 1
        if got is not None:
            outs.append(got)
        cycles += 1
        assert cycles < limit, f"stream stalled: {len(outs)}/{n_out} outputs after {cycles} cycles"
    dut.in_valid_i.value = 0
    return outs
