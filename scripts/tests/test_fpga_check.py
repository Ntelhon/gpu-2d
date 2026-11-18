# SPDX-License-Identifier: Apache-2.0
"""Unit tests for scripts/fpga_check.py, v1.8 additions: SCENE_END wsig / coll (RAM collisions fail the run) and
--wsig-ref (first scene whose write signature differs from a reference log)."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = """SCENE 0 clears_0
rc init 0
crc a 11111111
SCENE 1 paths_0
rc draw 0
crc b 22222222
"""


def log(wsig0="aaaaaaaa", wsig1="bbbbbbbb", coll1="00", sig=True):
    extra = lambda w, c: f" wsig={w} coll={c}" if sig else ""
    return ("G2D_TEST_BEGIN 2\nSCENE 0 clears_0\nrc init 0\ncrc a 11111111\n"
            f"SCENE_END 0 ret=0 busy=10 draws=1{extra(wsig0, '00')}\n"
            "SCENE 1 paths_0\nrc draw 0\ncrc b 22222222\n"
            f"SCENE_END 1 ret=0 busy=20 draws=2{extra(wsig1, coll1)}\n"
            "IRQ_TEST ok=1\nG2D_TEST_END fails=0\n")


def run(tmp_path, got, ref=None):
    (tmp_path / "exp.txt").write_text(EXPECTED)
    (tmp_path / "got.log").write_text(got)
    args = [sys.executable, str(ROOT / "scripts" / "fpga_check.py"), str(tmp_path / "exp.txt"), str(tmp_path / "got.log")]
    if ref is not None:
        (tmp_path / "ref.log").write_text(ref)
        args += ["--wsig-ref", str(tmp_path / "ref.log")]
    r = subprocess.run(args, capture_output=True, text=True)
    return r.returncode, r.stdout


def test_plain_pass(tmp_path):
    assert run(tmp_path, log(sig=False))[0] == 0     # older bitstream: no wsig / coll fields
    assert run(tmp_path, log())[0] == 0


def test_collision_fails(tmp_path):
    rc, out = run(tmp_path, log(coll1="01"))
    assert rc == 1 and "scene 1: 01" in out


def test_wsig_reference(tmp_path):
    assert run(tmp_path, log(), ref=log())[0] == 0
    rc, out = run(tmp_path, log(wsig1="bbbbbbbc"), ref=log())
    assert rc == 1 and "first scene with different writes: 1 paths_0" in out
    rc, out = run(tmp_path, log(), ref=log(sig=False))
    assert rc == 1 and "no wsig in the reference" in out
