# SPDX-License-Identifier: Apache-2.0
"""L2 bench of g2d_raster (rendering.md §8) against the golden model's RTL-structured rasterizer
(gm.path_raster, model/src/raster.cpp), which the model tests tie to the direct formula and the literal
per-sample test. Every coverage value of the clipped rectangle is compared, in scanline order.

Stimulus: random polygons (3..60 edges) over rectangles of 1..600 px width (1..3 segments) and 1..48 rows
(1..3 bands); dense short edges with small dy and sample-column ties (remainder exactness); edges far
outside and beyond the guard band; edges left of the rectangle (base winding); a single edge; lists where
every edge is dropped; both fill rules, AA on and off; random edge-read latency and coverage
back-pressure. Band-limited scenes with more kept edges than the buffer (streaming, <= MAX_EDGES per
band); the me64 configuration (64-entry buffer) streams on most scenes.
"""

import hashlib
import os
import random
import struct
from pathlib import Path

import cocotb

import clkrst
import pytest
from cocotb.triggers import ReadOnly, RisingEdge

import g2d_model as gm
from read_client import ReadResponder

DATA_W = int(os.environ.get("G2D_P_DATA_W", "64"))
MAX_EDGES = int(os.environ.get("G2D_P_MAX_EDGES", "512"))
CHUNK = int(os.environ.get("G2D_P_CHUNK", str(MAX_EDGES)))
FULL = bool(int(os.environ.get("G2D_FULL", "0")))
LIM = (1 << 18) - 1


async def start(dut):
    await clkrst.start(dut, zero=("clr_i", "stop_i", "start_i", "cov_ready_i", "rd_rsp_valid_i", "rd_req_ready_i"))


def rand_poly(rng, cx, cy, r, n):
    pts = [(int(16 * (cx + rng.uniform(-r, r))), int(16 * (cy + rng.uniform(-r, r)))) for _ in range(n)]
    return [(*pts[i], *pts[(i + 1) % n]) for i in range(n)]


def scene(rng, kind):
    x0, y0 = rng.randint(0, 300), rng.randint(0, 200)
    w = rng.choice([1, 9, 64, 255, 256, 257, 600]) if kind != "dense" else rng.randint(8, 80)
    h = rng.choice([1, 15, 16, 17, 33, 48]) if kind != "dense" else rng.randint(4, 24)
    rect = (x0, y0, x0 + w, y0 + h)
    if kind == "poly":
        edges = rand_poly(rng, x0 + w / 2, y0 + h / 2, max(w, h) * 0.6, rng.randint(3, min(60, MAX_EDGES - 8)))
        edges += rand_poly(rng, x0 - 20, y0 + h / 2, 10, 3)       # base winding
    elif kind == "dense":
        edges = []
        for _ in range(rng.randint(100, 480) if MAX_EDGES >= 512 else rng.randint(10, (MAX_EDGES - 4) // 2)):
            x, y = rng.randint(16 * x0 - 100, 16 * (x0 + w) + 100), rng.randint(16 * y0 - 20, 16 * (y0 + h) + 20)
            dy = rng.choice([1, 2, 3, 4, 5, 7, 8, 12])
            dx = rng.randint(-2000, 2000) if rng.random() < 0.5 else dy * rng.choice([1, -1, 3, -3, 16])
            edges.append((x, y, x + dx, y + dy) if rng.random() < 0.5 else (x + dx, y + dy, x, y))
    elif kind == "far":
        edges = [tuple(rng.choice([-(1 << 31), (1 << 31) - 1, LIM, -LIM, rng.randint(-6000, 6000)]) for _ in range(4))
                 for _ in range(rng.randint(1, min(30, MAX_EDGES - 8)))]
        edges += rand_poly(rng, x0 + w / 2, y0 + h / 2, max(w, h), 5)
    elif kind == "bands":
        # streaming (more kept edges than the buffer, at most MAX_EDGES - 4 per band): small closed
        # triangles kept inside the rows of one band each, plus a long edge pair spanning every band
        h = rng.choice([17, 33, 48, 64])
        rect = (x0, y0, x0 + w, y0 + h)
        edges = []
        nb = (h + 15) // 16
        for b in range(nb):
            top, bot = 16 * (y0 + 16 * b), 16 * (y0 + min(16 * b + 16, h)) - 1
            for _ in range(rng.randint((MAX_EDGES - 4) // 6, (MAX_EDGES - 4) // 3)):
                pts = [(rng.randint(16 * x0 - 40, 16 * (x0 + w) + 40), rng.randint(top, bot)) for _ in range(3)]
                edges += [(*pts[i], *pts[(i + 1) % 3]) for i in range(3)]
        xm = 16 * x0 + 8 * w
        edges += [(xm, 16 * y0 - 8, xm + 40, 16 * (y0 + h) + 8), (xm + 300, 16 * (y0 + h) + 8, xm + 260, 16 * y0 - 8)]
    elif kind == "overflow":
        # more than CHUNK edges active in the same rows: long, nearly vertical edges over the whole height
        # (pairs, so the shape closes), plus a cluster of short edges inside one pixel row
        h = rng.choice([3, 17, 20])
        rect = (x0, y0, x0 + w, y0 + h)
        edges = []
        for _ in range(rng.randint(MAX_EDGES // 2 + 1, 2 * MAX_EDGES)):   # scenes do not depend on CHUNK
            xa, xb = rng.randint(16 * x0 - 30, 16 * (x0 + w) + 30), rng.randint(16 * x0 - 30, 16 * (x0 + w) + 30)
            edges += [(xa, 16 * y0 - 5, xb, 16 * (y0 + h) + 5), (xb + rng.randint(1, 60), 16 * (y0 + h) + 5,
                                                                xa + rng.randint(1, 60), 16 * y0 - 5)]
        row = 16 * (y0 + rng.randrange(h))
        for _ in range(rng.randint(0, MAX_EDGES)):
            xa = rng.randint(16 * x0, 16 * (x0 + w))
            edges += [(xa, row + 2, xa + 30, row + 13), (xa + 31, row + 13, xa + 1, row + 2)]
    elif kind == "single":
        edges = [(16 * x0 + rng.randint(0, 16 * w), 16 * y0 - 50, 16 * x0 + rng.randint(0, 16 * w), 16 * (y0 + h) + 50)]
    else:  # dropped: everything above, below or right of the rectangle
        edges = [(16 * (x0 + w) + rng.randint(16, 900), rng.randint(0, 4000), 16 * (x0 + w) + rng.randint(16, 900),
                  rng.randint(0, 4000)) for _ in range(rng.randint(1, 20))]
        edges += [(rng.randint(0, 9000), 16 * y0 - 300, rng.randint(0, 9000), 16 * y0 - 2)]
    return rect, edges


async def run_one(dut, rng, mem, rect, edges, aa, eo, p_rdy=1.0):
    """Rasterize one scene; returns (got, expected) coverage bytes."""
    addr = rng.randrange(0x1000, 0x7000_0000) & ~0xF
    for i, b in enumerate(b"".join(struct.pack("<4I", *(c & 0xFFFF_FFFF for c in e)) for e in edges)):
        mem[addr + i] = b
    exp = gm.path_raster([list(e) for e in edges], list(rect), aa, eo)
    dut.cfg_addr_i.value, dut.cfg_count_i.value = addr, len(edges)
    dut.cfg_aa_i.value, dut.cfg_eo_i.value = int(aa), int(eo)
    dut.cfg_rx0_i.value, dut.cfg_ry0_i.value, dut.cfg_rx1_i.value, dut.cfg_ry1_i.value = rect
    dut.start_i.value = 1
    await RisingEdge(dut.clk_i)
    dut.start_i.value = 0
    got = bytearray()
    cyc = 0
    while len(got) < len(exp):
        rdy = int(rng.random() < p_rdy)
        dut.cov_ready_i.value = rdy
        await ReadOnly()
        if int(dut.cov_valid_o.value) and rdy:
            got.append(int(dut.cov_o.value))
        await RisingEdge(dut.clk_i)
        cyc += 1
        assert cyc < stall_limit(rect, edges), f"stalled at {len(got)}/{len(exp)} after {cyc} cycles"
    dut.cov_ready_i.value = 1
    for _ in range(50):
        await RisingEdge(dut.clk_i)
        assert not int(dut.cov_valid_o.value), "extra coverage"
        if not int(dut.busy_o.value):
            break
    assert not int(dut.busy_o.value), "rasterizer still busy"
    return bytes(got), exp


def stall_limit(rect, edges):
    """Generous cycle bound: per row and segment, every pass of the multi-pass mode re-reads the whole
    list (two beats per edge at DATA_W 64) and runs the band setup."""
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    segs = (w + 255) // 256
    passes = len(edges) // CHUNK + 2
    return 5000 + 60 * w * h + 8 * len(edges) * (1 + h * segs * passes)


def cov_map(cov, w):
    return "\n".join(" ".join(f"{c:3d}" for c in cov[y * w:(y + 1) * w]) for y in range(len(cov) // w))


@cocotb.test()
async def raster_directed(dut):
    """Axis-aligned square and a triangle inside one segment and one band, AA and not."""
    await start(dut)
    rng = random.Random(3)
    mem = {}
    ReadResponder(dut, lambda a, n: bytes(mem.get(a + i, 0) for i in range(n)), DATA_W, rng, lat=(0, 5))
    sq = [(32, 32, 96, 32), (96, 32, 96, 96), (96, 96, 32, 96), (32, 96, 32, 32)]       # px 2..6
    tri = [(20, 10, 110, 40), (110, 40, 40, 120), (40, 120, 20, 10)]
    for name, edges in (("square", sq), ("triangle", tri)):
        for aa in (False, True):
            rect = (0, 0, 9, 9)
            got, exp = await run_one(dut, rng, mem, rect, edges, aa, False)
            assert got == exp, f"{name} aa {aa}\nRTL:\n{cov_map(got, 9)}\nmodel:\n{cov_map(exp, 9)}"


@cocotb.test()
async def raster_vs_model(dut):
    await start(dut)
    # scenes and timing use separate generators: the per-cycle randomness (read latency, back-pressure)
    # depends on how long a configuration takes, so sharing one generator would change the scenes
    rng = random.Random(int(os.environ.get("SEED", "1")) + 41)
    io_rng = random.Random(int(os.environ.get("SEED", "1")) + 43)
    mem = {}

    def mem_read(a, n):
        return bytes(mem.get(a + i, 0) for i in range(n))

    ReadResponder(dut, mem_read, DATA_W, io_rng, lat=(0, 60))
    kinds = ["poly", "dense", "far", "single", "dropped", "bands", "overflow"]
    if os.environ.get("G2D_RASTER_KINDS"):
        kinds = os.environ["G2D_RASTER_KINDS"].split(",")
    digest = hashlib.sha256()
    iters = int(os.environ.get("G2D_RASTER_ITERS", "40" if FULL else "15"))
    for it in range(iters):
        kind = kinds[it % len(kinds)]
        rect, edges = scene(rng, kind)
        aa, eo = rng.random() < 0.7, rng.random() < 0.4
        addr = rng.randrange(0x1000, 0x7000_0000) & ~0xF
        for i, b in enumerate(b"".join(struct.pack("<4I", *(c & 0xFFFF_FFFF for c in e)) for e in edges)):
            mem[addr + i] = b
        exp = gm.path_raster([list(e) for e in edges], list(rect), aa, eo)
        dut.cfg_addr_i.value, dut.cfg_count_i.value = addr, len(edges)
        dut.cfg_aa_i.value, dut.cfg_eo_i.value = int(aa), int(eo)
        dut.cfg_rx0_i.value, dut.cfg_ry0_i.value, dut.cfg_rx1_i.value, dut.cfg_ry1_i.value = rect
        dut.start_i.value = 1
        await RisingEdge(dut.clk_i)
        dut.start_i.value = 0
        got = bytearray()
        cyc = 0
        p_rdy = rng.choice([1.0, 0.7, 0.3])
        while len(got) < len(exp):
            rdy = int(io_rng.random() < p_rdy)
            dut.cov_ready_i.value = rdy
            await ReadOnly()
            if int(dut.cov_valid_o.value) and rdy:
                got.append(int(dut.cov_o.value))
            await RisingEdge(dut.clk_i)
            cyc += 1
            assert cyc < stall_limit(rect, edges), f"it {it} ({kind}): stalled at {len(got)}/{len(exp)}"
        dut.cov_ready_i.value = 1
        for _ in range(50):
            await RisingEdge(dut.clk_i)
            assert not int(dut.cov_valid_o.value), "extra coverage"
            if not int(dut.busy_o.value):
                break
        assert not int(dut.busy_o.value), "rasterizer still busy"
        w = rect[2] - rect[0]
        digest.update(bytes(got))
        if bytes(got) != exp:
            bad = [i for i in range(len(exp)) if got[i] != exp[i]]
            i = bad[0]
            rows = sorted({rect[1] + j // w for j in bad})
            cols = sorted({rect[0] + j % w for j in bad})
            nz = sum(1 for j in bad if got[j] != 0)
            raise AssertionError(f"it {it} ({kind}, aa {aa}, eo {eo}, {len(edges)} edges, rect {rect}): {len(bad)} diffs "
                                 f"({nz} with non-zero RTL value), rows {rows[:6]}..{rows[-1]}, cols {cols[0]}..{cols[-1]}; "
                                 f"first at ({rect[0] + i % w}, {rect[1] + i // w}): got {got[i]} expected {exp[i]}; "
                                 f"RTL non-zero total {sum(1 for g in got if g)}, model {sum(1 for e in exp if e)}")
    out = os.environ.get("G2D_RASTER_DIGEST")
    if out:
        Path(out).write_text(digest.hexdigest())


def test_raster():
    import runner
    runner.run("g2d_raster", "test_raster", params={}, tag="dw64")


def test_raster_dw128():
    import runner
    runner.run("g2d_raster", "test_raster", params={"DATA_W": 128}, tag="dw128")


SPLITS = [64, 13, 5]


def split_digest(chunk):
    """Output digest of a split run, per seed (the three runs of one seed render the same scenes)."""
    root = Path(__file__).resolve().parents[2]
    return root / "build" / "dv" / f"raster_split_{chunk}_s{os.environ.get('SEED', '1')}.txt"


@pytest.mark.parametrize("chunk", SPLITS)
def test_raster_split(chunk):
    """Multi-pass rows with forced split points: a 64-entry buffer with CHUNK edges per pass. Every run
    is compared with the model; the digest of all coverage outputs is kept for test_raster_split_same."""
    import runner
    runner.run("g2d_raster", "test_raster", params={"MAX_EDGES": 64, "CHUNK": chunk}, tag=f"ch{chunk}",
               testcase="raster_vs_model",
               extra_env={"G2D_RASTER_KINDS": "overflow,bands,poly", "G2D_RASTER_DIGEST": str(split_digest(chunk)),
                          "G2D_RASTER_ITERS": "12" if os.environ.get("G2D_FULL") == "1" else "6"})


def test_raster_split_same():
    """Phase 4 condition: identical results for every split of the edges across passes (same scenes,
    CHUNK 64 / 13 / 5)."""
    digests = {c: split_digest(c).read_text() for c in SPLITS}
    assert len(set(digests.values())) == 1, digests


def test_raster_stream():
    """64-entry edge buffer: every scene with more than 64 kept edges streams per band."""
    import runner
    runner.run("g2d_raster", "test_raster", params={"MAX_EDGES": 64}, tag="me64", testcase="raster_vs_model")
