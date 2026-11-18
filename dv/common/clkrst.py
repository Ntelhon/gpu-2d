# SPDX-License-Identifier: Apache-2.0
"""Clock and reset start-up for the block benches.

With random initial values (runner: +verilator+rand+reset+2), a reset input that already starts low and
a clock that already starts high give no edge at time 0, so the asynchronous reset would only act at the
first clock edge, where simulation-only checks still see random register values. start() therefore
drives the reset high at time 0 and low 1 ns later (a definite negedge: asynchronous reset before any
clock edge), with the clock starting low (first rising edge at half a period).
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer


async def start(dut, zero=(), clk="clk_i", rst="rst_ni", period_ns=10):
    """Start the clock, apply the reset, set the signals in `zero` to 0, release the reset after 2.5
    periods and wait for one rising edge."""
    getattr(dut, rst).value = 1
    for s in zero:
        getattr(dut, s).value = 0
    cocotb.start_soon(Clock(getattr(dut, clk), period_ns, unit="ns").start(start_high=False))
    await Timer(1, unit="ns")
    getattr(dut, rst).value = 0
    await Timer(int(2.5 * period_ns), unit="ns")
    getattr(dut, rst).value = 1
    await RisingEdge(getattr(dut, clk))
