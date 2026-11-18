# SPDX-License-Identifier: Apache-2.0
"""L2 bench of g2d_edge_load (rendering.md §8.1-8.3) against a direct Python transcription of the spec.

Reference per edge record (x0, y0, x1, y1 as signed 32-bit s.4):
  clamp to +-(2^18 - 1), x2 (1/32 px); drop if horizontal, Yb <= 32*ry0, Ya >= 32*ry1 or
  min(Xa, Xb) > 32*rx1 - 1; else (Xa, Ya, Yb, dx = Xb - Xa, dy, dir, qs, rs, qr, rr) with S*dx = qs*dy + rs
  and 32*dx = qr*dy + rr (Python floor division), S = 2 (AA) or 32.
Stimulus: random edges inside and far outside the guard band (int32 extremes), horizontal, vertical,
zero-length, edges above/below/left/right of the clipped rectangle and on its exact limits, both
orientations, steep and shallow, count 1 .. 300 (chunks crossing 256-byte blocks), both AA settings,
random read latency and output back-pressure. Checks the kept edges in list order, done_o, that the
reads cover exactly the list, and that the client never back-pressures R.
"""

import os
import random
import struct

import cocotb

import clkrst
import pytest
from cocotb.triggers import ReadOnly, RisingEdge

from read_client import ReadResponder

DATA_W = int(os.environ.get("G2D_P_DATA_W", "64"))
FULL = bool(int(os.environ.get("G2D_FULL", "0")))
LIM = (1 << 18) - 1


def s32(v):
    v &= 0xFFFF_FFFF
    return v - (1 << 32) if v >> 31 else v


def ref_edges(edges, aa, rx1, ry0, ry1):
    out = []
    for e in edges:
        x0, y0, x1, y1 = (2 * max(-LIM, min(LIM, s32(c))) for c in e)
        if y0 == y1:
            continue
        down = y1 > y0
        xa, ya, xb, yb = (x0, y0, x1, y1) if down else (x1, y1, x0, y0)
        if yb <= 32 * ry0 or ya >= 32 * ry1 or min(xa, xb) > 32 * rx1 - 1:
            continue
        dx, dy = xb - xa, yb - ya
        s = 2 if aa else 32
        qs, rs = divmod(s * dx, dy)
        qr, rr = divmod(32 * dx, dy)
        out.append((xa, ya, yb, dx, dy, int(down), qs, rs, qr, rr))
    return out


def rand_edge(rng, rect):
    rx1, ry0, ry1 = rect
    kind = rng.random()
    big = [-(1 << 31), (1 << 31) - 1, LIM, -LIM, LIM + 1, -LIM - 1, 1 << 20]
    def coord(lo, hi):
        return rng.randint(lo, hi)
    if kind < 0.1:
        return [rng.choice(big + [coord(-5000, 5000)]) for _ in range(4)]
    if kind < 0.2:                       # horizontal / zero-length
        y = coord(-100, 16 * ry1 + 100)
        x = coord(-100, 16 * rx1 + 100)
        return [x, y, x if rng.random() < 0.5 else coord(-100, 16 * rx1), y]
    if kind < 0.3:                       # on the drop limits (in s.4: 1/16 px, so Y = 2*y16)
        lim_y = rng.choice([16 * ry0, 16 * ry1, 16 * ry0 - 1, 16 * ry1 + 1])
        lim_x = rng.choice([16 * rx1, 16 * rx1 - 1, 16 * rx1 + 1])
        return [lim_x + rng.randint(0, 3), lim_y, lim_x + rng.randint(0, 40), lim_y + rng.choice([-30, -1, 1, 30])]
    if kind < 0.4:                       # vertical, steep, shallow
        x, y = coord(-50, 16 * rx1 + 50), coord(16 * ry0 - 50, 16 * ry1 + 50)
        return [x, y, x + rng.choice([0, 1, -1, 4000, -4000]), y + rng.choice([1, -1, 2, 300, -300])]
    return [coord(-200, 16 * rx1 + 200), coord(16 * ry0 - 200, 16 * ry1 + 200),
            coord(-200, 16 * rx1 + 200), coord(16 * ry0 - 200, 16 * ry1 + 200)]


async def start(dut):
    await clkrst.start(dut, zero=("clr_i", "stop_i", "start_i", "out_ready_i", "rd_rsp_valid_i", "rd_req_ready_i"))


@cocotb.test()
async def edge_load_random(dut):
    await start(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")) + 31)
    mem = {}

    def mem_read(a, n):
        return bytes(mem.get(a + i, 0) for i in range(n))

    resp = ReadResponder(dut, mem_read, DATA_W, rng, lat=(0, 60))
    for it in range(40 if FULL else 14):
        rx1, ry0 = rng.randint(1, 600), rng.randint(0, 400)
        ry1 = ry0 + rng.randint(1, 300)
        aa = rng.random() < 0.6
        n = rng.choice([1, 2, 15, 16, 17, rng.randint(1, 300)])
        edges = [rand_edge(rng, (rx1, ry0, ry1)) for _ in range(n)]
        addr = rng.randrange(0x1000, 0x7000_0000) & ~0xF
        data = b"".join(struct.pack("<4I", *(c & 0xFFFF_FFFF for c in e)) for e in edges)
        for i, b in enumerate(data):
            mem[addr + i] = b
        exp = ref_edges(edges, aa, rx1, ry0, ry1)
        nreq = len(resp.requests)
        dut.cfg_addr_i.value, dut.cfg_count_i.value, dut.cfg_aa_i.value = addr, n, int(aa)
        dut.cfg_rx1_i.value, dut.cfg_ry0_i.value, dut.cfg_ry1_i.value = rx1, ry0, ry1
        dut.start_i.value = 1
        await RisingEdge(dut.clk_i)
        dut.start_i.value = 0
        got, done, cyc = [], False, 0
        while not done:
            ordy = int(rng.random() < 0.7)
            dut.out_ready_i.value = ordy
            await ReadOnly()
            if int(dut.out_valid_o.value) and ordy:
                v = [int(getattr(dut, f"out_{f}_o").value)
                     for f in ("xa", "ya", "yb", "dx", "dy", "dir", "qs", "rs", "qr", "rr")]
                sx = lambda x, w: x - (1 << w) if x >> (w - 1) else x
                got.append((sx(v[0], 21), sx(v[1], 21), sx(v[2], 21), sx(v[3], 21), v[4], v[5], sx(v[6], 27), v[7],
                            sx(v[8], 27), v[9]))
            done = bool(int(dut.done_o.value))
            await RisingEdge(dut.clk_i)
            cyc += 1
            assert cyc < 100 * (n + 50), "edge loader stalled"
        assert got == exp, (f"it {it}: {len(got)} edges, expected {len(exp)}; first diff "
                            f"{next(((i, g, e) for i, (g, e) in enumerate(zip(got, exp)) if g != e), None)}")
        reqs = resp.requests[nreq:]
        cover = sorted((a, a + ln) for a, ln in reqs)
        assert cover[0][0] == addr and cover[-1][1] == addr + 16 * n - 1, "reads do not cover the list"
        for (a0, e0), (a1, _) in zip(cover, cover[1:]):
            assert a1 == e0 + 1, "reads overlap or leave a gap"
        assert all(a // 256 == (a + ln) // 256 for a, ln in reqs), "request crosses a 256-byte block"
        await RisingEdge(dut.clk_i)
        assert int(dut.idle_o.value) == 1


def test_edge_load():
    import runner
    runner.run("g2d_edge_load", "test_edge_load", params={}, tag="dw64")


def test_edge_load_dw128():
    import runner
    runner.run("g2d_edge_load", "test_edge_load", params={"DATA_W": 128}, tag="dw128")
