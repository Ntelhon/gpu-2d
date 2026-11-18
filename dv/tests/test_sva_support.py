# SPDX-License-Identifier: Apache-2.0
"""Verilator SVA support check (Phase 5, before any protocol checker relies on it).

g2d_sva_selftest_tb carries properties built from the constructs the checkers in dv/sva use (|=>, |->,
##N, $stable, $past, $rose, disable iff, cover). A legal stimulus must pass; each violation must stop
the simulation with the message of exactly that property.
"""

import os

import cocotb
import pytest
from cocotb.triggers import RisingEdge, Timer

import clkrst

CASE = os.environ.get("G2D_SVA_CASE", "legal")


@cocotb.test()
async def drive(dut):
    await clkrst.start(dut, zero=("v", "r", "d"), clk="clk", rst="rst_n")
    seq = {
        # legal: valid held while not ready, payload stable, no 0xFF two cycles after a handshake
        "legal": [(1, 0, 5), (1, 0, 5), (1, 1, 5), (0, 0, 0), (1, 1, 7), (0, 0, 0), (0, 0, 0), (1, 1, 0xFF)],
        "hold_drop": [(1, 0, 5), (0, 0, 5), (0, 0, 0)],          # valid dropped while not ready
        "hold_data": [(1, 0, 5), (1, 0, 6), (0, 0, 0)],          # payload changed while stalled
        "seq": [(1, 1, 1), (0, 0, 0), (1, 0, 0xFF), (1, 1, 0xFF), (0, 0, 0)],
    }[CASE]
    for v, r, d in seq + [(0, 0, 0)] * 4:
        dut.v.value, dut.r.value, dut.d.value = v, r, d
        await RisingEdge(dut.clk)
    await Timer(1, unit="ns")


def _run(case):
    import runner
    runner.run("g2d_sva_selftest_tb", "test_sva_support", tag="sva", extra_env={"G2D_SVA_CASE": case})


def test_sva_legal():
    _run("legal")


@pytest.mark.parametrize("case,prop", [("hold_drop", "p_hold"), ("hold_data", "p_hold"), ("seq", "p_seq")])
def test_sva_violation_fires(case, prop, capfd):
    with pytest.raises(BaseException):
        _run(case)
    out = capfd.readouterr()
    assert f"{prop} violated" in out.out + out.err, f"{case}: the simulation did not report {prop}"
