# SPDX-License-Identifier: Apache-2.0
"""L2 benches of the gradient blocks (rendering.md §10.1, Phase 4 assumption A4).

g2d_grad against gm.grad_index: P, Q over the full signed 32-bit range (extremes included), R over the
signed 48-bit range, x, y over 0..8191, all four spreads (v1.4 FILL: out_fill_o = index outside [0, 255],
index as PAD otherwise); directed T values at the ramp-index boundaries
(i = -1/0, 255/256, 511/512 for REFLECT, large negative/positive).
v1.5 radial (§10.4) against gm.radial_index: driver coefficients of centred / focal / outside-focal / r <= 0
gradients under random gradient matrices, random coefficients, and U / V at and beyond the 32-bit saturation
(u = v = -2^31: square-root argument 2^63), every spread, random stalls; the radial path's 37-stage latency.
g2d_ramp: ramps loaded from every 4-byte alignment inside a beat (DATA_W 64 and 128) and looked up for
all 256 indices against memory; reload rules: the same address reuses the loaded copy (no request), a
kick, a GPU write or another address reloads (for the next draw only: pulses during the lookups of
the current draw must not stop them); lookups with random gaps and back-pressure.
"""

import math
import os
import random

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge

import clkrst
import g2d_model as gm
from read_client import ReadResponder
from stream import run_stream

DATA_W = int(os.environ.get("G2D_P_DATA_W", "64"))
FULL = bool(int(os.environ.get("G2D_FULL", "0")))


def s(v, w):
    v &= (1 << w) - 1
    return v - (1 << w) if v >> (w - 1) else v


# ------------------------------------------------------------------------------------------------
# g2d_grad
# ------------------------------------------------------------------------------------------------

@cocotb.test()
async def grad_index(dut):
    await clkrst.start(dut, zero=("clr_i", "in_valid_i", "out_ready_i", "cfg_radial_i"))
    rng = random.Random(int(os.environ.get("SEED", "1")) + 51)
    ext32 = [-(1 << 31), (1 << 31) - 1, 0, 1, -1, 1 << 16, -(1 << 16)]
    ext48 = [-(1 << 47), (1 << 47) - 1, 0, 1 << 17, -(1 << 17), (255 << 17), (256 << 17), (512 << 17) - 1]
    for it in range(30 if FULL else 12):
        for spread in range(4):
            if it < 4:   # directed: P = Q = 0, T = R at the index boundaries
                p = q = 0
                rs = [(k << 17) + d for k in (-2, -1, 0, 1, 254, 255, 256, 257, 510, 511, 512, 513, 1023, 1024)
                      for d in (-1, 0, 1)]
            else:
                p = rng.choice(ext32 + [rng.getrandbits(32), rng.randint(-(1 << 20), 1 << 20)])
                q = rng.choice(ext32 + [rng.getrandbits(32), rng.randint(-(1 << 20), 1 << 20)])
                rs = [rng.choice(ext48 + [rng.getrandbits(48)])]
            for r in rs:
                dut.cfg_p_i.value, dut.cfg_q_i.value = p & 0xFFFF_FFFF, q & 0xFFFF_FFFF
                dut.cfg_r_i.value, dut.cfg_spread_i.value = r & ((1 << 48) - 1), spread
                pts = [(0, 0), (8191, 8191), (8191, 0), (0, 8191)] + \
                      [(rng.randrange(8192), rng.randrange(8192)) for _ in range(30 if it >= 4 else 2)]

                def drive(d, v):
                    d.in_x_i.value, d.in_y_i.value = v

                def sample(d):
                    return (int(d.out_idx_o.value), int(d.out_fill_o.value), int(d.out_x_o.value),
                            int(d.out_y_o.value))

                outs = await run_stream(dut, pts, drive, sample, rng, 0.8, 0.8)
                for (x, y), (idx, fill, ox, oy) in zip(pts, outs):
                    exp = gm.grad_index(s(p, 32), s(q, 32), s(r, 48), x, y, spread)
                    assert (ox, oy) == (x, y)
                    ctx = f"P {s(p, 32)} Q {s(q, 32)} R {s(r, 48)} ({x},{y}) spread {spread}"
                    if spread == 3:
                        assert fill == int(exp < 0), f"{ctx}: fill {fill}, model {exp}"
                        if exp >= 0:
                            assert idx == exp, f"{ctx}: {idx} != {exp}"
                    else:
                        assert fill == 0 and idx == exp, f"{ctx}: {idx}/{fill} != {exp}"


@cocotb.test()
async def grad_radial(dut):
    await clkrst.start(dut, zero=("clr_i", "in_valid_i", "out_ready_i"))
    dut.cfg_radial_i.value = 1
    rng = random.Random(int(os.environ.get("SEED", "1")) + 57)
    M = (1 << 48) - 1
    cases = []
    for k in range(40 if FULL else 16):
        a, sc = rng.uniform(-3.2, 3.2), rng.choice([0.05, 0.5, 1.0, 2.0, 40.0])
        gmx = [[sc * math.cos(a), -sc * math.sin(a), rng.uniform(-100, 300)],
               [sc * math.sin(a) * rng.uniform(0.5, 2), sc * math.cos(a), rng.uniform(-100, 300)], [0, 0, 1]]
        cx, cy, r = rng.uniform(-50, 50), rng.uniform(-50, 50), rng.choice([0.0, -3.0, 0.3, 4.0, 60.0, 900.0])
        fo = rng.choice([0.0, 0.5, 0.98, 0.995, 1.0, 3.0])            # focal offset in units of r
        th = rng.uniform(0, 6.3)
        rc, p, q, rr = gm.radial_coeffs(gmx, cx, cy, r, cx + fo * abs(r) * math.cos(th), cy + fo * abs(r) * math.sin(th))
        if rc == gm.GEOM_OK:
            cases.append((p, q, rr))
    e32 = [-(1 << 31), (1 << 31) - 1, 0, 1 << 20, -(1 << 20)]
    e48 = [-(1 << 47), (1 << 47) - 1, 0, 1 << 40, -(1 << 40)]
    for _ in range(12 if FULL else 6):
        cases.append(([rng.choice(e32 + [s(rng.getrandbits(32), 32)]) for _ in range(3)],
                      [rng.choice(e32 + [s(rng.getrandbits(32), 32)]) for _ in range(3)],
                      [rng.choice(e48 + [s(rng.getrandbits(48), 48)]) for _ in range(3)]))
    cases.append(([0, 0, 0], [0, 0, 0], [0, -(1 << 47), -(1 << 47)]))     # u = v = -2^31 (saturated)
    for p, q, rr in cases:
        for spread in range(4):
            names = (("p", "q", "r"), ("up", "uq", "ur"), ("vp", "vq", "vr"))
            for k, (pn, qn, rn) in enumerate(names):
                getattr(dut, f"cfg_{pn}_i").value = p[k] & 0xFFFF_FFFF
                getattr(dut, f"cfg_{qn}_i").value = q[k] & 0xFFFF_FFFF
                getattr(dut, f"cfg_{rn}_i").value = rr[k] & M
            dut.cfg_spread_i.value = spread
            pts = [(0, 0), (8191, 8191)] + [(rng.randrange(8192), rng.randrange(8192)) for _ in range(6)] + \
                  [(rng.randrange(64), rng.randrange(64)) for _ in range(6)]

            def drive(d, v):
                d.in_x_i.value, d.in_y_i.value = v

            def sample(d):
                return (int(d.out_idx_o.value), int(d.out_fill_o.value), int(d.out_x_o.value), int(d.out_y_o.value))

            outs = await run_stream(dut, pts, drive, sample, rng, 0.8, 0.8)
            for (x, y), (idx, fill, ox, oy) in zip(pts, outs):
                exp = gm.radial_index(p, q, rr, x, y, spread)
                ctx = f"T/U/V {p} {q} {rr} ({x},{y}) spread {spread}"
                assert (ox, oy) == (x, y), ctx
                if spread == 3:
                    assert fill == int(exp < 0), f"{ctx}: fill {fill}, model {exp}"
                    if exp >= 0:
                        assert idx == exp, f"{ctx}: {idx} != {exp}"
                else:
                    assert fill == 0 and idx == exp, f"{ctx}: {idx}/{fill} != {exp}"


# ------------------------------------------------------------------------------------------------
# g2d_ramp
# ------------------------------------------------------------------------------------------------

@cocotb.test()
async def ramp(dut):
    await clkrst.start(dut, zero=("clr_i", "stop_i", "kick_i", "wr_i", "need_i", "in_valid_i", "out_ready_i",
                                  "rd_rsp_valid_i", "rd_req_ready_i"))
    rng = random.Random(int(os.environ.get("SEED", "1")) + 53)
    mem = {}

    def mem_read(a, n):
        return bytes(mem.get(a + i, 0) for i in range(n))

    resp = ReadResponder(dut, mem_read, DATA_W, rng, lat=(0, 40))
    db = DATA_W // 8

    def ramp_words(addr):
        return [int.from_bytes(mem_read(addr + 4 * i, 4), "little") for i in range(256)]

    async def need(addr, pre=None):
        if pre:   # the kick / GPU write happens before the draw that needs the ramp
            getattr(dut, pre).value = 1
            await RisingEdge(dut.clk_i)
            getattr(dut, pre).value = 0
            for _ in range(rng.randint(0, 3)):
                await RisingEdge(dut.clk_i)
        dut.need_i.value, dut.need_addr_i.value = 1, addr
        await RisingEdge(dut.clk_i)
        dut.need_i.value = 0
        for _ in range(4000):
            await ReadOnly()
            if int(dut.ready_o.value):
                break
            await RisingEdge(dut.clk_i)
        else:
            raise AssertionError("ramp never ready")
        await RisingEdge(dut.clk_i)

    async def pulse_noise():
        """Write / kick pulses during the lookups of the current draw; ready_o (which g2d_pix uses to let
        gradient pixels through) must stay high: the pulses only concern later draws."""
        while True:
            dut.wr_i.value = int(rng.random() < 0.2)
            dut.kick_i.value = int(rng.random() < 0.05)
            await ReadOnly()
            assert int(dut.ready_o.value) == 1, "ramp not ready during its draw after a write/kick pulse"
            await RisingEdge(dut.clk_i)

    async def lookup_all(addr, noise=False):
        exp = ramp_words(addr)
        idx = list(range(256)) + [rng.randrange(256) for _ in range(200)]

        def drive(d, v):
            d.in_idx_i.value, d.in_pass_i.value = v, v

        def sample(d):
            return int(d.out_word_o.value), int(d.out_pass_o.value)

        # the draw's own writes (and kicks) arrive while it looks up: they must not stop it; they make the
        # next draw reload, so only lookups followed by an expected reload get them
        task = cocotb.start_soon(pulse_noise()) if noise else None
        outs = await run_stream(dut, idx, drive, sample, rng, 0.8, 0.7)
        if task:
            task.cancel()
            dut.wr_i.value, dut.kick_i.value = 0, 0
        for i, (w, pa) in zip(idx, outs):
            assert pa == i and w == exp[i], f"ramp @0x{addr:x} index {i}: 0x{w:08x} != 0x{exp[i]:08x}"

    for it in range(12 if FULL else 6):
        addr = (rng.randrange(0x1000, 0x7000_0000) & ~(db - 1)) + 4 * (it % (db // 4))
        for i in range(1024):
            mem[addr + i] = rng.getrandbits(8)
        n0 = len(resp.requests)
        await need(addr)
        assert len(resp.requests) == n0 + 1, "no load for a new address"
        await lookup_all(addr)
        await need(addr)
        assert len(resp.requests) == n0 + 1, "reloaded a valid ramp"
        for pre in ("kick_i", "wr_i"):
            for i in range(1024):
                mem[addr + i] = rng.getrandbits(8)
            n1 = len(resp.requests)
            await need(addr, pre)
            assert len(resp.requests) == n1 + 1, f"no reload after {pre}"
            await lookup_all(addr, noise=True)


import pytest  # noqa: E402


def test_grad_index():
    import runner
    runner.run("g2d_grad", "test_grad", testcase=["grad_index", "grad_radial"])


@pytest.mark.parametrize("dw", [64, 128])
def test_ramp(dw):
    import runner
    runner.run("g2d_ramp", "test_grad", params={"DATA_W": dw, "PASS_W": 8}, tag=f"dw{dw}", testcase="ramp")
