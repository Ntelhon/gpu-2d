# SPDX-License-Identifier: Apache-2.0
"""L2 bench of g2d_src_cache (arch.md §6.4, verification-plan.md BLT-06 at block level).

Stimulus: pixels with bilinear/point texel quads (x1 - x0, y1 - y0 in {0, 1}, as g2d_tex_addr produces;
v1.4: also REPEAT-wrapped quads, x1 = (x0 + 1) mod w, whose bank-conflicting seam quads are issued as four
single-texel requests (in_smask_i), exactly as g2d_tex_split does) in a source image in a random memory, walked as scanlines, 16x16 tiles under random rotation/scale,
random jumps (thrash), plus pixels that fail the source test (pass = 0). A Python read-client responder
serves the 64-byte line requests in order with random latency and beat gaps.

Checks: every output in input order (tag), for pass pixels each requested texel's bytes equal memory; requests are
64-byte aligned lines of size 64; the number of fills equals the reported misses; invalidation between
"draws" with changed memory returns the new data; a soft-reset stop drains the pipeline with fills
outstanding; throughput with an ideal (zero-latency) responder on a hit-dominated walk.
"""

import math
import os
import random
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge, Timer

FULL = bool(int(os.environ.get("G2D_FULL", "0")))
DATA_W = int(os.environ.get("G2D_P_DATA_W", "64"))
DB = DATA_W // 8
TAG_W = 16


class Mem:
    def __init__(self, rng):
        self.rng = rng
        self.d = {}

    def byte(self, a):
        if a not in self.d:
            self.d[a] = self.rng.getrandbits(8)
        return self.d[a]

    def read(self, a, n):
        return sum(self.byte((a + i) & 0xFFFF_FFFF) << (8 * i) for i in range(n))


class Responder:
    """Read client side of g2d_axi_rd: accepts requests with random ready, returns beats in order."""

    def __init__(self, dut, mem, rng, lat=(0, 40), p_gap=0.2, p_req=0.8):
        self.dut, self.mem, self.rng = dut, mem, rng
        self.lat, self.p_gap, self.p_req = lat, p_gap, p_req
        self.q = deque()
        self.fills = 0
        self.stopped = False
        cocotb.start_soon(self.run())

    async def run(self):
        dut = self.dut
        cyc = 0
        beats = deque()
        while True:
            dut.rd_req_ready_i.value = int(self.rng.random() < self.p_req)
            if beats and cyc >= beats[0][0] and self.rng.random() >= self.p_gap:
                _, data = beats[0]
                dut.rd_rsp_valid_i.value = 1
                dut.rd_rsp_data_i.value = data
            else:
                dut.rd_rsp_valid_i.value = 0
            await ReadOnly()
            if int(dut.rd_req_valid_o.value) and int(dut.rd_req_ready_i.value):
                a, ln = int(dut.rd_req_addr_o.value), int(dut.rd_req_len_o.value)
                assert a % 64 == 0 and ln == 63, f"request 0x{a:x} len {ln}"
                self.fills += 1
                if not self.stopped:
                    t = cyc + self.rng.randint(*self.lat)
                    for i in range(64 // DB):
                        beats.append((t, self.mem.read(a + i * DB, DB)))
            if int(dut.rd_rsp_valid_i.value):
                assert int(dut.rd_rsp_ready_o.value) == 1
                beats.popleft()
            await RisingEdge(dut.clk_i)
            cyc += 1


async def start(dut):
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_ni.value = 0
    for s in ("clr_i", "stop_i", "inv_i", "in_valid_i", "out_ready_i", "rd_rsp_valid_i", "rd_req_ready_i"):
        getattr(dut, s).value = 0
    dut.in_smask_i.value = 0xF
    await Timer(25, unit="ns")
    dut.rst_ni.value = 1
    await RisingEdge(dut.clk_i)


def pack(words):
    v = 0
    for i, x in enumerate(words):
        v |= (x & 0xFFFF_FFFF) << (32 * i)
    return v


class Image:
    def __init__(self, rng, w, h, lg):
        self.w, self.h, self.lg = w, h, lg
        self.bpp = 1 << lg
        self.stride = rng.choice([w * self.bpp, w * self.bpp + rng.randrange(1, 64) * self.bpp,
                                  (w * self.bpp + 63) // 64 * 64])
        self.addr = rng.randrange(0x1000, 0x7000_0000) & ~(self.bpp - 1)
        if rng.random() < 0.5:
            self.addr &= ~63

    def quad(self, x, y, bil, wrap=False):
        cx = lambda c: min(max(c, 0), self.w - 1)
        cy = lambda c: min(max(c, 0), self.h - 1)
        x0, y0 = cx(x), cy(y)
        if wrap:   # REPEAT over the whole image: neighbours wrap to column / row 0
            x1, y1 = ((x0 + 1) % self.w, (y0 + 1) % self.h) if bil else (x0, y0)
        else:
            x1, y1 = (cx(x + 1), cy(y + 1)) if bil else (x0, y0)
        a = [self.addr + yy * self.stride + (xx << self.lg) for yy in (y0, y1) for xx in (x0, x1)]
        return a, (y0 & 1) | ((y1 & 1) << 1)


def walk(rng, img, kind, n):
    pts = []
    if kind == "scan":
        x0, y0 = rng.randrange(img.w), rng.randrange(img.h)
        for i in range(n):
            pts.append((x0 + i % 64 - 1, y0 + i // 64))
    elif kind == "tiles":
        th = rng.uniform(0, 2 * math.pi)
        sc = rng.choice([0.5, 1.0, 2.0, 4.0])
        cx, cy = rng.randrange(img.w), rng.randrange(img.h)
        while len(pts) < n:
            tx, ty = rng.randrange(0, 128, 16), rng.randrange(0, 128, 16)
            for j in range(16):
                for i in range(16):
                    dx, dy = tx + i - 64, ty + j - 64
                    pts.append((int(cx + sc * (dx * math.cos(th) - dy * math.sin(th))),
                                int(cy + sc * (dx * math.sin(th) + dy * math.cos(th)))))
        pts = pts[:n]
    else:  # random jumps: thrash
        pts = [(rng.randrange(-1, img.w + 1), rng.randrange(-1, img.h + 1)) for _ in range(n)]
    return pts


def conflict(a, yp):
    """Two texels in one bank ({row parity, address bit 6}) on different lines (g2d_tex_split)."""
    bank = [((yp >> (k // 2)) & 1, (a[k] >> 6) & 1) for k in range(4)]
    return any(bank[s] == bank[t] and a[s] >> 6 != a[t] >> 6 for s in range(4) for t in range(s + 1, 4))


async def run_pixels(dut, rng, img, mem, pts, bil, p_in=0.9, p_out=0.8, p_fail=0.05, wrap=False):
    vecs = []
    nsplit = 0
    for i, (x, y) in enumerate(pts):
        a, yp = img.quad(x, y, bil, wrap)
        ps = int(rng.random() >= p_fail)
        fx, fy = rng.randrange(256), rng.randrange(256)
        if ps and conflict(a, yp):
            nsplit += 1
            vecs += [(ps, a, yp, fx, fy, i & 0xFFFF, 1 << k) for k in range(4)]
        else:
            vecs.append((ps, a, yp, fx, fy, i & 0xFFFF, 0xF))
    dut._nsplit = getattr(dut, "_nsplit", 0) + nsplit
    n = len(vecs)
    outs = []
    i = 0
    valid = False
    cyc = 0
    while len(outs) < n:
        if not valid and i < n and rng.random() < p_in:
            ps, a, yp, fx, fy, tag, sm = vecs[i]
            dut.in_pass_i.value, dut.in_addr_i.value, dut.in_ypar_i.value = ps, pack(a), yp
            dut.in_smask_i.value = sm
            dut.in_fx_i.value, dut.in_fy_i.value, dut.in_tag_i.value = fx, fy, tag
            valid = True
        dut.in_valid_i.value = int(valid)
        ordy = int(rng.random() < p_out)
        dut.out_ready_i.value = ordy
        await ReadOnly()
        acc = valid and int(dut.in_ready_o.value)
        if int(dut.out_valid_o.value) and ordy:
            t = int(dut.out_tex_o.value)
            outs.append((int(dut.out_pass_o.value), [(t >> (32 * k)) & 0xFFFF_FFFF for k in range(4)],
                         int(dut.out_fx_o.value), int(dut.out_fy_o.value), int(dut.out_tag_o.value)))
        await RisingEdge(dut.clk_i)
        if acc:
            valid = False
            i += 1
        cyc += 1
        assert cyc < 200 * (n + 50), f"stalled at {len(outs)}/{n}"
    dut.in_valid_i.value = 0
    mask = (1 << (8 * img.bpp)) - 1
    for (ps, a, yp, fx, fy, tag, sm), (gp, tex, gfx, gfy, gtag) in zip(vecs, outs):
        assert (gtag, gp, gfx, gfy) == (tag, ps, fx, fy), f"order/sideband: tag {gtag} expected {tag}"
        if ps:
            for k in (k for k in range(4) if sm >> k & 1):
                exp = mem.read(a[k], img.bpp)
                assert tex[k] & mask == exp, (f"tag {tag} texel {k} @0x{a[k]:x}: got 0x{tex[k] & mask:x} "
                                              f"expected 0x{exp:x}")
    return cyc


async def count_misses(dut):
    n = 0
    while True:
        await ReadOnly()
        n += int(dut.miss_o.value)
        await RisingEdge(dut.clk_i)
        dut._misses = n


async def wait_idle(dut, limit=5000):
    for _ in range(limit):
        await RisingEdge(dut.clk_i)
        if int(dut.idle_o.value):
            return
    raise AssertionError("cache not idle")


async def invalidate(dut):
    await wait_idle(dut)
    dut.inv_i.value = 1
    await RisingEdge(dut.clk_i)
    dut.inv_i.value = 0


@cocotb.test()
async def cache_random(dut):
    await start(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")) + 11)
    mem = Mem(rng)
    resp = Responder(dut, mem, rng, lat=(0, rng.choice([5, 40, 120])))
    dut._misses = 0
    cocotb.start_soon(count_misses(dut))
    ndraw, npix = (24, 1500) if FULL else (8, 500)
    for d in range(ndraw):
        img = Image(rng, rng.randrange(1, 600), rng.randrange(1, 600), rng.randrange(3))
        kind = ["scan", "tiles", "jump"][d % 3]
        pts = walk(rng, img, kind, npix)
        bil = rng.random() < 0.7
        await run_pixels(dut, rng, img, mem, pts, bil)
        await invalidate(dut)
        # the CPU rewrites the image, then the same pixels are drawn again: no stale line may be used
        for a in mem.d:
            mem.d[a] = rng.getrandbits(8)
        await run_pixels(dut, rng, img, mem, pts[: npix // 4], bil)
        await invalidate(dut)
    await wait_idle(dut)
    assert resp.fills == dut._misses, (resp.fills, dut._misses)


@cocotb.test()
async def cache_wrap_seams(dut):
    """v1.4: REPEAT-wrapped bilinear quads; seam quads that conflict in a bank go in as single-texel requests."""
    await start(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")) + 13)
    mem = Mem(rng)
    resp = Responder(dut, mem, rng, lat=(0, rng.choice([5, 40])))
    dut._misses = 0
    dut._nsplit = 0
    cocotb.start_soon(count_misses(dut))
    for d in range(8 if FULL else 4):
        img = Image(rng, rng.choice([1, 2, 3, 17, 40, 300]), rng.choice([1, 2, 5, 33, 200]), rng.randrange(3))
        # walk across the right / bottom edges and around the whole image
        pts = [(x, y) for y in range(img.h - 3, img.h) for x in range(img.w - 20, img.w)]
        pts += [(rng.randrange(img.w), rng.randrange(img.h)) for _ in range(200)]
        await run_pixels(dut, rng, img, mem, pts, bil=True, wrap=True)
        await invalidate(dut)
    await wait_idle(dut)
    assert resp.fills == dut._misses, (resp.fills, dut._misses)
    assert dut._nsplit > 0, "no split quad generated"


@cocotb.test()
async def cache_stop(dut):
    """Soft-reset stop with fills outstanding: the pipeline drains (don't-care texels), then clr."""
    await start(dut)
    rng = random.Random(4)
    mem = Mem(rng)
    resp = Responder(dut, mem, rng, lat=(80, 120))
    img = Image(rng, 300, 300, 2)
    pts = walk(rng, img, "jump", 200)
    n_in = 0
    dut.out_ready_i.value = 1
    for x, y in pts:
        a, yp = img.quad(x, y, True)
        dut.in_pass_i.value, dut.in_addr_i.value, dut.in_ypar_i.value = 1, pack(a), yp
        dut.in_fx_i.value, dut.in_fy_i.value, dut.in_tag_i.value = 0, 0, n_in
        dut.in_valid_i.value = 1
        await ReadOnly()
        acc = int(dut.in_ready_o.value)
        await RisingEdge(dut.clk_i)
        n_in += acc
        if n_in >= 60:
            break
    dut.in_valid_i.value = 0
    resp.stopped = True          # as if the AXI master dropped the remaining requests
    dut.stop_i.value = 1
    await wait_idle(dut, 2000)
    dut.clr_i.value = 1
    await RisingEdge(dut.clk_i)
    dut.clr_i.value = 0
    dut.stop_i.value = 0
    await RisingEdge(dut.clk_i)
    assert int(dut.idle_o.value) == 1 and int(dut.out_valid_o.value) == 0


@cocotb.test()
async def cache_throughput(dut):
    """Zero-latency responder, scanline walk over an image that fits the cache: ~1 pixel per clock."""
    await start(dut)
    rng = random.Random(6)
    mem = Mem(rng)
    Responder(dut, mem, rng, lat=(0, 0), p_gap=0.0, p_req=1.0)
    img = Image(rng, 64, 16, 2)
    img.stride, img.addr = 256, 0x10000
    pts = [(i % 64, (i // 64) % 16) for i in range(2048)]
    await run_pixels(dut, rng, img, mem, pts[:1024], bil=True, p_in=1.0, p_out=1.0, p_fail=0)  # warm
    cyc = await run_pixels(dut, rng, img, mem, pts[1024:], bil=True, p_in=1.0, p_out=1.0, p_fail=0)
    assert cyc <= 1024 + 20, cyc


def _run(params, tag):
    import runner
    runner.run("g2d_src_cache", "test_src_cache", params={"TAG_W": TAG_W, **params}, tag=tag)


def test_src_cache():
    _run({}, "default")


def test_src_cache_small():
    """2 KB cache, 8-pixel FIFO, 2 fills in flight, DATA_W 128: maximum stall and eviction pressure."""
    _run({"DATA_W": 128, "CACHE_BYTES": 2048, "PIX_DEPTH": 8, "TRK_DEPTH": 2}, "small")
