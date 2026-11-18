# SPDX-License-Identifier: Apache-2.0
"""L2 bench of g2d_sdiv (pipelined signed floor division) against plain Python integer arithmetic:
q = n // d, r = n % d (floor semantics, 0 <= r < d), ovf = not (|n| < d * 2^QW) or d == 0.

Stimulus per configuration:
- full signed dividend range: the extremes -2^(NW-1), 2^(NW-1) - 1, 0, +-1, random over all NW bits
- divisor edge values: 1, 2, 3, every power of two and its neighbours, 2^DW - 1, 0 (ovf), random
- quotient boundaries: |n| = d * 2^QW - 1 (largest valid), d * 2^QW (first overflow), exact negative
  multiples (r = 0, the sign-fix special case), n = -1 / +1 with large d
- issue: back-to-back at full rate (one division per clock checked), and random input gaps with random
  output back-pressure
The small configuration (NW 8, DW 3, QW 4) is checked exhaustively: every n x every d.
"""

import os
import random

import cocotb

import clkrst
import pytest
from cocotb.triggers import RisingEdge

from stream import run_stream

NW = int(os.environ.get("G2D_P_NW", "44"))
DW = int(os.environ.get("G2D_P_DW", "21"))
QW = int(os.environ.get("G2D_P_QW", "31"))
FULL = bool(int(os.environ.get("G2D_FULL", "0")))
TAG_W = 16


def ref(n, d):
    if d == 0 or abs(n) >= d << QW:
        return None
    return n // d, n % d


async def start(dut):
    await clkrst.start(dut, zero=("clr_i", "in_valid_i", "out_ready_i"))


def drive(dut, v):
    n, d, tag = v
    dut.in_n_i.value = n & ((1 << NW) - 1)
    dut.in_d_i.value = d
    dut.in_tag_i.value = tag


def sample(dut):
    q = int(dut.out_q_o.value)
    if q >> QW:
        q -= 1 << (QW + 1)
    return q, int(dut.out_r_o.value), int(dut.out_ovf_o.value), int(dut.out_tag_o.value)


def check(vecs, outs):
    for i, ((n, d), (q, r, ovf, tag)) in enumerate(zip(vecs, outs)):
        assert tag == i & 0xFFFF, f"order: output {i} has tag {tag}"
        exp = ref(n, d)
        assert ovf == int(exp is None), f"n {n} d {d}: ovf {ovf}, expected {int(exp is None)}"
        if exp is not None:
            assert (q, r) == exp, f"n {n} d {d}: got q {q} r {r}, expected {exp}"


def divisors(rng):
    dmax = (1 << DW) - 1
    ds = {1, 2, 3, dmax, dmax - 1, 0}
    for k in range(DW):
        ds |= {1 << k, (1 << k) + 1, max((1 << k) - 1, 1)}
    ds |= {rng.randint(1, dmax) for _ in range(8)}
    return sorted(d for d in ds if 0 <= d <= dmax)


def vectors(rng):
    lo, hi = -(1 << (NW - 1)), (1 << (NW - 1)) - 1
    vecs = []
    for d in divisors(rng):
        ns = {lo, hi, 0, 1, -1, rng.randint(lo, hi), rng.randint(lo, hi)}
        if d:
            lim = d << QW
            for b in (lim - 1, lim, lim + 1, lim - d, d, d - 1):
                ns |= {b, -b}
            k = rng.randint(0, (1 << QW) - 1)
            ns |= {-k * d, k * d, -k * d - 1, -k * d + 1}     # exact negative multiples and neighbours
            ns |= {rng.randint(-lim + 1, lim - 1) for _ in range(6)}
        vecs += [(n, d) for n in ns if lo <= n <= hi]
    vecs += [(rng.randint(lo, hi), rng.randint(0, (1 << DW) - 1)) for _ in range(2000 if FULL else 500)]
    # mostly-valid random pairs (the common case of the users)
    for _ in range(4000 if FULL else 1000):
        d = rng.randint(1, (1 << DW) - 1)
        n = rng.randint(-(d << QW) + 1, (d << QW) - 1)
        vecs.append((max(lo, min(hi, n)), d))
    return vecs


@cocotb.test()
async def sdiv_random(dut):
    await start(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")) + 21)
    vecs = vectors(rng)
    if NW <= 10:   # small configuration: exhaustive
        vecs = [(n, d) for n in range(-(1 << (NW - 1)), 1 << (NW - 1)) for d in range(1 << DW)]
    tagged = [(n, d, i & 0xFFFF) for i, (n, d) in enumerate(vecs)]
    check(vecs, await run_stream(dut, tagged, drive, sample, rng, p_in=0.8, p_out=0.7))


@cocotb.test()
async def sdiv_back_to_back(dut):
    """Full-rate issue: one division accepted and one result delivered per clock."""
    await start(dut)
    rng = random.Random(5)
    vecs = vectors(rng)[:600]
    tagged = [(n, d, i & 0xFFFF) for i, (n, d) in enumerate(vecs)]
    t0 = cocotb.utils.get_sim_time("ns")
    outs = await run_stream(dut, tagged, drive, sample, rng)
    cycles = (cocotb.utils.get_sim_time("ns") - t0) / 10
    check(vecs, outs)
    assert cycles <= len(vecs) + QW + 4, cycles


@pytest.mark.parametrize("nw,dw,qw", [(44, 21, 31), (27, 21, 26), (8, 3, 4)])
def test_sdiv(nw, dw, qw):
    import runner
    runner.run("g2d_sdiv", "test_sdiv", params={"NW": nw, "DW": dw, "QW": qw, "TAG_W": TAG_W}, tag=f"{nw}_{dw}_{qw}")
