# SPDX-License-Identifier: Apache-2.0
"""The unchanged driver on the live RTL backend (dv/live, `make live`) vs the software backend (Phase 6).

Every program runs twice, linked against the golden-model HAL (sw/hal/model) and against the verilated
g2d_top with a random AXI profile (G2D_LIVE_SEED: latency, READY / VALID back-pressure, reordering,
interleaving). The outputs must be identical byte for byte:
  * test_driver (the driver unit test, its own checks);
  * vgl_scene scripts of test_scenes.py (clears, blits, paths and gradients, patterns): every return code and
    every buffer;
  * LVGL frames (widgets, 320x240) from lvgl_demo_live32 vs lvgl_demo_vg32.
Both RTL configurations (dw64, dw128). Skipped when the live backend is not built.
"""

from __future__ import annotations

import os
import random
import subprocess
from pathlib import Path

import pytest

import test_scenes as ts

ROOT = Path(__file__).resolve().parents[2]
LIVE = Path(os.environ.get("G2D_LIVE_PATH", ROOT / "build" / "live" / "live"))
LVGL = ROOT / "build" / "lvgl" / "sw"
SEED = int(os.environ.get("SEED", "1"))
FULL = os.environ.get("G2D_FULL") == "1"
CFGS = ["dw64", "dw128"]

pytestmark = pytest.mark.skipif(not (LIVE / "vgl_scene_live_dw64").exists(), reason="build the live backend: make live")


def live_env(seed):
    return {**os.environ, "G2D_LIVE_SEED": str(seed)}


@pytest.mark.parametrize("cfg", CFGS)
def test_driver_unit_on_rtl(cfg):
    p = subprocess.run([str(LIVE / f"test_driver_live_{cfg}")], capture_output=True, text=True,
                       env=live_env(SEED * 10 + 1), timeout=1800)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    assert " 0 failed" in p.stdout


def run_script(exe: Path, lines: list[str], bufs, tmp: Path, env):
    tmp.mkdir(exist_ok=True)
    script = lines + ["finish"] + [f"dump {i} {tmp / f'buf{i}.bin'}" for i in bufs]
    p = subprocess.run([str(exe)], input="\n".join(script) + "\n", capture_output=True, text=True,
                       env={**env, "G2D_MODEL_ARENA_MB": "16"}, timeout=1800)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    rcs = [ln for ln in p.stdout.splitlines() if ln.startswith(("rc ", "buf "))]
    return rcs, {i: (tmp / f"buf{i}.bin").read_bytes() for i in bufs}


GENS = {"clears": ts.scene_clears, "blits": ts.scene_blits, "paths": ts.scene_paths,
        "patterns": lambda r: ts.scene_patterns(r)[0], "converted": ts.scene_converted, "strokes": ts.scene_strokes,
        "vectors": ts.scene_vectors, "radial": ts.scene_radial, "imgpaths": ts.scene_image_paths,
        "v16": ts.scene_v16, "v16b": ts.scene_v16b}


@pytest.mark.parametrize("cfg", CFGS)
@pytest.mark.parametrize("kind", list(GENS))
def test_scenes_on_rtl(tmp_path, cfg, kind):
    rng = random.Random(SEED * 1000 + list(GENS).index(kind))
    for k in range(3 if FULL else 1):
        sc = GENS[kind](rng)
        sw = run_script(ts.BIN, sc.lines, sc.bufs, tmp_path / f"sw{k}", dict(os.environ))
        rtl = run_script(LIVE / f"vgl_scene_live_{cfg}", sc.lines, sc.bufs, tmp_path / f"rtl{k}",
                         live_env(SEED * 100 + k))
        assert sw[0] == rtl[0], "return codes / buffer addresses differ"
        for i in sc.bufs:
            assert sw[1][i] == rtl[1][i], f"{kind} scene {k}: buffer {i} differs between the software and RTL backends"


@pytest.mark.parametrize("cfg", CFGS)
def test_lvgl_frames_on_rtl(tmp_path, cfg):
    exe = LIVE / f"lvgl_demo_live32_{cfg}"
    ref = LVGL / "lvgl_demo_vg32"
    if not (exe.exists() and ref.exists()):
        pytest.skip("LVGL demos not built (make lvgl, make live)")
    demos = [("widgets", 2)] + ([("vector", 2), ("benchmark", 40)] if FULL else [])
    for demo, frames in demos:
        out = {}
        for name, prog, env in (("sw", ref, dict(os.environ)), ("rtl", exe, live_env(SEED * 7 + frames))):
            prefix = tmp_path / f"{demo}_{name}"
            p = subprocess.run([str(prog), demo, "320", "240", str(frames), str(prefix), str(frames)],
                               capture_output=True, text=True, env=env, timeout=7200)
            assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
            out[name] = (Path(f"{prefix}_{frames:04d}.raw")).read_bytes()
        assert out["sw"] == out["rtl"], f"{demo} frame {frames}: RTL frame differs from the software backend"
