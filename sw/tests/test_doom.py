# SPDX-License-Identifier: Apache-2.0
"""DOOM side project, step D1 / D2 (docs/PHASES.md): DOOM (doomgeneric, CMAP256) on the vg_lite driver with the
golden-model HAL. Every frame is presented by G2D (INDEX_8 screen + CLUT, 2 x 2.4 point-sampled blit into a BGR565
frame buffer, page flip) and doom_host checks it bit-exactly against a CPU-only present of the same screen and
palette. The run is deterministic (virtual clock): two runs give the same per-frame CRCs, and the demos must make
the frames change.

Needs scripts/fetch_doom.sh (doomgeneric + Freedoom, not committed); built here with `make doom-host` when fetched,
skipped otherwise. G2D_FULL=1: 3000 frames (all three demos start), else 400.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "build" / "doom" / "sw" / "doom_host"
WAD = Path(os.environ.get("DOOM_WAD", ROOT / "build" / "ext" / "freedoom" / "freedoom1.wad"))
FULL = os.environ.get("G2D_FULL", "0") == "1"
FRAMES = 3000 if FULL else 400


@pytest.fixture(scope="module")
def doom():
    if not WAD.exists():        # also with a built binary: the game data is a fetched file and can be missing
        pytest.skip("DOOM not fetched (scripts/fetch_doom.sh)")
    if not BIN.exists():
        if not (ROOT / "build" / "ext" / "doomgeneric" / "doomgeneric" / "doomgeneric.h").exists():
            pytest.skip("DOOM not fetched (scripts/fetch_doom.sh)")
        lock = ROOT / "build" / ".doom.lock"
        lock.parent.mkdir(exist_ok=True)
        with open(lock, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)     # parallel regression seeds share one build
            if not BIN.exists():
                subprocess.run(["make", "-C", str(ROOT), "doom-host"], check=True, capture_output=True)
    return BIN


def test_doom_keys(doom):
    """PS/2 set-2 decoder (sw/apps/doom/doom_keys.c, step D3) against hand-written sequences."""
    r = subprocess.run([str(doom.parent / "test_doom_keys")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout


def run(doom: Path, tmp: Path, name: str) -> list[str]:
    d = tmp / name
    d.mkdir()
    env = dict(os.environ, DOOM_FRAMES=str(FRAMES), DOOM_CRC=str(d / "crc.txt"))
    r = subprocess.run([str(doom), "-iwad", str(WAD)], cwd=d, env=env, capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "bit-exact against the CPU present" in r.stdout
    return (d / "crc.txt").read_text().split("\n")


def test_doom_present_bit_exact_and_deterministic(doom, tmp_path):
    a = run(doom, tmp_path, "a")
    b = run(doom, tmp_path, "b")
    assert a == b, "two runs of the same demo gave different frames"
    crcs = {line.split()[1] for line in a if line}
    assert len(crcs) > FRAMES // 4, f"only {len(crcs)} distinct frames in {FRAMES}: the demo does not play"
