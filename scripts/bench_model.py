#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Golden-model speed (docs/OPEN_QUESTIONS.md Q-PERF-1, v1.7 item 6): 720p workloads through the Python bindings,
megapixels per second of model time (command building excluded). Also prints a CRC of every target so a speed
change can be checked for bit-exactness against a reference run (--expect file written with --save).

    PYTHONPATH=build/model:dv/common:dv/tests python3 scripts/bench_model.py [--reps N] [--save F | --expect F]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import types
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("build/model", "dv/common", "dv/tests"):
    sys.path.insert(0, str(ROOT / p))

import g2d_model as gm  # noqa: E402
from g2d_cmd import FMT, FMT_BYTES, CmdBuf, edges_to_bytes, state_word, STATE  # noqa: E402
from scenes import PathData, circle, rounded_rect  # noqa: E402

W, H = 1280, 720
TGT, SRC, EDGES, CMD = 0x1000_0000, 0x2000_0000, 0x3000_0000, 0x0010_0000


def buf(addr, w, h, fmt):
    return types.SimpleNamespace(addr=addr, w=w, h=h, fmt=fmt, stride=w * FMT_BYTES[fmt])


def path_cmd(cb, mem, t, pd, blend=1, color=0xC03070C0):
    rc, edges, bbox = gm.path_edges(pd.to_bytes(), 3, [[1, 0, 0], [0, 1, 0], [0, 0, 1]])
    assert rc == gm.GEOM_OK
    mem.write(EDGES, edges_to_bytes([tuple(e) for e in edges]))
    cb.target(t.addr, t.w, t.h, t.fmt, t.stride)
    cb.clip(0, 0, t.w, t.h)
    cb.load_state(STATE["BLEND"].addr, [blend, state_word("ALPHA_CTRL", SRC_VALUE=255, DST_VALUE=255), 0, color])
    cb.load_state(STATE["RAST_CTRL"].addr, [state_word("RAST_CTRL", AA=1)])
    cb.draw_path(tuple(bbox), EDGES, len(edges))
    return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])


def fill_cmd(cb, t, blend, color):
    cb.target(t.addr, t.w, t.h, t.fmt, t.stride)
    cb.clip(0, 0, t.w, t.h)
    cb.load_state(STATE["BLEND"].addr, [blend, state_word("ALPHA_CTRL", SRC_VALUE=255, DST_VALUE=255), 0, color])
    cb.draw_rect(0, 0, t.w, t.h)
    return t.w * t.h


def blit_cmd(cb, t, s, m, bilinear):
    from test_blit import blit
    _, draw = blit(cb, t, s, m, bilinear=bilinear, blend=1)
    x0, y0, x1, y1 = draw
    return (min(x1, t.w) - max(x0, 0)) * (min(y1, t.h) - max(y0, 0))


def workloads(mem):
    t32 = buf(TGT, W, H, FMT["ARGB8888"])
    t16 = buf(TGT, W, H, FMT["RGB565"])
    s = buf(SRC, 512, 512, FMT["ARGB8888"])
    rot = [[0.9, -0.3, 300], [0.3, 0.9, 50], [0, 0, 1]]
    persp = [[1.2, 0.1, 100], [0, 1.2, 0], [0.0004, 0.0002, 1]]
    return {
        "fill NONE argb": lambda cb: fill_cmd(cb, t32, 0, 0xFF336699),
        "fill SRC_OVER argb": lambda cb: fill_cmd(cb, t32, 1, 0x80336699),
        "fill SRC_OVER rgb565": lambda cb: fill_cmd(cb, t16, 1, 0x80336699),
        "blit identity point": lambda cb: blit_cmd(cb, t32, s, [[1, 0, 100], [0, 1, 100], [0, 0, 1]], False),
        "blit rotated bilinear": lambda cb: blit_cmd(cb, t32, s, rot, True),
        "blit perspective bilinear": lambda cb: blit_cmd(cb, t32, s, persp, True),
        "path rrect AA": lambda cb: path_cmd(cb, mem, t32, rounded_rect(PathData(), 40, 40, 1240, 680, 48)),
        "path circle AA": lambda cb: path_cmd(cb, mem, t32, circle(PathData(), 640, 360, 340)),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--save")
    ap.add_argument("--expect")
    a = ap.parse_args(argv)
    mem = gm.Memory()
    gpu = gm.Gpu(mem)
    gpu.set_addr_win(0, 0xFFFF_FFC0)
    import random
    rng = random.Random(7)
    mem.write(SRC, bytes(rng.getrandbits(8) for _ in range(512 * 512 * 4)))
    crcs, ok = {}, True
    for name, build in workloads(mem).items():
        best = None
        for _ in range(a.reps):
            mem.write(TGT, bytes(W * H * 4))
            cb = CmdBuf()
            px = build(cb)
            cb.fence(1)
            data = cb.to_bytes()
            mem.write(CMD, data)
            t0 = time.perf_counter()
            assert gpu.kick(CMD, len(data)), hex(gpu.err_status)
            dt = time.perf_counter() - t0
            best = dt if best is None else min(best, dt)
        crcs[name] = zlib.crc32(mem.read(TGT, W * H * 4))
        print(f"{name:28} {px / 1e6:6.3f} Mpx  {best * 1e3:8.1f} ms  {px / best / 1e6:7.2f} Mpx/s  crc {crcs[name]:08x}")
    if a.save:
        Path(a.save).write_text(json.dumps(crcs, indent=1))
    if a.expect:
        ref = json.loads(Path(a.expect).read_text())
        for k, v in crcs.items():
            if ref.get(k) != v:
                print(f"MISMATCH {k}: {v:08x} != {ref.get(k, 0):08x}")
                ok = False
        print("bit-exact vs reference" if ok else "NOT bit-exact")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
