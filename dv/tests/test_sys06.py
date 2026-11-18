# SPDX-License-Identifier: Apache-2.0
"""SYS-06: replay of recorded driver command streams on g2d_top (verification-plan.md, Phase 6).

The software backend (sw/hal/model) records what the driver sends to the GPU (G2D_RECORD, format in
sw/hal/model/g2d_hal_model.h: page contents the GPU needs, kicks, soft resets, and at the end the content of
every touched page as the software backend computed it). Here the recordings are replayed on the RTL:
  * MEM records are written to the AXI memory (and the model); a kick that brings new page contents first
    waits for the GPU to finish (the CPU would not overwrite memory the GPU still uses); kicks without new
    contents are queued back to back, so the kick queue fills and back-pressures;
  * every recording runs under its own random AXI profile (latency, READY / VALID back-pressure, reordering,
    interleaving; axi_mem.Profile.random);
  * at the end of each recording: every CHECK page equals the software backend's result (bit-exact), and
    the whole memory, the error / fence registers and the white-box state equal the golden model.
Streams: API scenes (sw/tests/test_scenes.py generators through vgl_scene: clears, blits, paths / gradients,
patterns, converted source formats, strokes) and LVGL demo frames
(sw/apps/lvgl, identity addresses). make test: API scenes + 2 widgets frames at 320x240; G2D_FULL=1 also the
16-bit widgets build, the vector demo and 150 benchmark frames.
"""

from __future__ import annotations

import os
import random
import struct
import subprocess
import sys
from pathlib import Path

import cocotb

from axi_mem import Profile
from g2d_top_util import SEED, make_env

ROOT = Path(__file__).resolve().parents[2]
REC_MEM, REC_KICK, REC_SOFT_RESET, REC_CHECK, REC_POST, REC_END = 1, 2, 3, 4, 5, 0xFFFFFFFF


def parse(path: str):
    d = Path(path).read_bytes()
    magic, ver = struct.unpack_from("<2I", d, 0)
    assert magic == 0x43455232 and ver == 2, f"{path}: not a version-2 recording"
    pos, out = 8, []
    while True:
        (k,) = struct.unpack_from("<I", d, pos)
        pos += 4
        if k == REC_END:
            return out
        if k == REC_MEM:
            a, n = struct.unpack_from("<2I", d, pos)
            out.append((k, a, d[pos + 8:pos + 8 + n]))
            pos += 8 + n
        elif k in (REC_CHECK, REC_POST):              # data, then one mask bit per byte
            a, n = struct.unpack_from("<2I", d, pos)
            out.append((k, a, (d[pos + 8:pos + 8 + n], d[pos + 8 + n:pos + 8 + n + n // 8])))
            pos += 8 + n + n // 8
        elif k == REC_KICK:
            a, n = struct.unpack_from("<2I", d, pos)
            out.append((k, a, n))
            pos += 8
        elif k == REC_SOFT_RESET:
            out.append((k, 0, 0))
        else:
            raise AssertionError(f"{path}: record kind {k} at {pos - 4}")


def masked_diff(got: bytes, data: bytes, mask: bytes):
    """Indices of the bytes marked in mask (bit i%8 of mask[i//8]) where got differs from data."""
    import numpy as np
    m = np.unpackbits(np.frombuffer(mask, np.uint8), bitorder="little").astype(bool)
    g, e = np.frombuffer(got, np.uint8), np.frombuffer(data, np.uint8)
    return np.nonzero(m & (g != e))[0], int(m.sum())


def verify(env, name: str, what: str, a: int, data: bytes, mask: bytes) -> int:
    got = env.read_dut(a, len(data))
    bad, n = masked_diff(got, data, mask)
    if len(bad):
        model = env.mmem.read(a, len(data))
        b = int(bad[0])
        raise AssertionError(f"{name}: {what} page 0x{a:08x}: {len(bad)} of {n} GPU-written bytes differ from the "
                             f"software backend, first 0x{a + b:08x} (RTL 0x{got[b]:02x}, replay model "
                             f"0x{model[b]:02x}, backend 0x{data[b]:02x})")
    return n


async def replay(env, path: str):
    """Apply a recording. POST records (after each kick) are checked lazily: a page's GPU-written bytes are
    compared before a MEM record overwrites that page and at the end, so every RTL result is compared once
    before the replay could replace it."""
    name = Path(path).name
    recs = parse(path)
    pending: list[tuple[int, bytes]] = []
    post: dict[int, tuple[bytes, bytes]] = {}
    kicks = checked = 0
    for kind, a, x in recs:
        if kind == REC_MEM:
            pending.append((a, x))
        elif kind == REC_POST:
            post[a] = x
        elif kind == REC_KICK:
            if pending:
                await env.wait_done()
                for pa, data in pending:
                    if pa in post:
                        checked += verify(env, name, "POST", pa, *post.pop(pa))
                    env.write_mem(pa, data)
                pending = []
            await env.kick(b"", addr=a, size=x)
            kicks += 1
        elif kind == REC_SOFT_RESET:
            await env.wait_done()
            await env.soft_reset()
    await env.wait_done()
    for pa, (data, mask) in post.items():
        checked += verify(env, name, "POST", pa, data, mask)
    for kind, a, x in recs:
        if kind == REC_CHECK:
            checked += verify(env, name, "CHECK", a, *x)
    await env.check(perf=False)
    env.dut._log.info(f"SYS-06 {name}: {kicks} kicks, {checked} GPU-written bytes equal to the software backend")
    return kicks


@cocotb.test()
async def sys06_replay(dut):
    rng = random.Random(SEED * 7919 + 6)
    files = [f for f in os.environ["G2D_SYS06_REC"].split(os.pathsep) if f]
    env = await make_env(dut, "sys06", profile=Profile.random(rng, max_lat=60))
    env.perf_checked = False
    total = 0
    for f in files:
        env.axi.profile = Profile.random(rng, max_lat=60)
        dut._log.info(f"SYS-06 {Path(f).name}: profile {env.axi.profile}")
        total += await replay(env, f)
    assert total > 0, "no kicks replayed"


# ------------------------------------------------------------------------------------------------
# pytest entry points: make the recordings, then one simulation per configuration
# ------------------------------------------------------------------------------------------------

import pytest  # noqa: E402

from test_top import CONFIGS  # noqa: E402

LVGL = ROOT / "build" / "lvgl" / "sw"
FULL = os.environ.get("G2D_FULL") == "1"


def api_recordings(tmp: Path) -> list[str]:
    """API scenes of sw/tests/test_scenes.py (clears, blits, paths / gradients, patterns), recorded."""
    sys.path.insert(0, str(ROOT / "sw" / "tests"))
    import test_scenes as ts
    if not ts.BIN.exists():
        pytest.skip("build the software stack first: make model")
    rng = random.Random(SEED * 31 + 6)
    out = []
    gens = [ts.scene_clears, ts.scene_blits, ts.scene_paths, lambda r: ts.scene_patterns(r)[0], ts.scene_converted,
            ts.scene_strokes, ts.scene_vectors, ts.scene_radial, ts.scene_image_paths, ts.scene_v16, ts.scene_v16b]
    for k, gen in enumerate(gens * (2 if FULL else 1)):
        d = tmp / f"api{k}"
        d.mkdir()
        ts.run(gen(rng), d)
        out.append(str(d / "rec.bin"))
    return out


def lvgl_recordings(tmp: Path) -> list[str]:
    runs = [("vg32", "widgets", 2)]
    if FULL:
        runs += [("vg16", "widgets", 2), ("vg32", "vector", 2), ("vg32", "benchmark", 150)]
    out = []
    for v, demo, frames in runs:
        exe = LVGL / f"lvgl_demo_{v}"
        if not exe.exists():
            continue                              # LVGL not built: API scenes only (make lvgl adds these)
        rec = tmp / f"{demo}_{v}.rec"
        subprocess.run([str(exe), demo, "320", "240", str(frames), str(tmp / f"{demo}_{v}"), "0"],
                       env={**os.environ, "G2D_RECORD": str(rec), "G2D_RECORD_POST": "1"}, check=True,
                       capture_output=True)
        out.append(str(rec))
    return out


@pytest.mark.parametrize("cfg", list(CONFIGS))
def test_sys06(cfg, tmp_path):
    import runner
    params, env = CONFIGS[cfg]
    recs = api_recordings(tmp_path) + lvgl_recordings(tmp_path)
    runner.run("g2d_top_tb", "test_sys06", params=params, tag=cfg,
               extra_env={**env, "G2D_SYS06_REC": os.pathsep.join(recs)})
