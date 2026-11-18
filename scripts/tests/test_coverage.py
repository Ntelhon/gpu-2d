# SPDX-License-Identifier: Apache-2.0
"""Unit tests of the functional coverage helpers (dv/common/g2d_coverage.py): the transform class of
driver-generated coefficients (gm.blit_setup) and the merge/report of coverage files."""

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dv" / "common"))

import g2d_coverage as cov  # noqa: E402
import g2d_model as gm  # noqa: E402
from g2d_cmd import STATE  # noqa: E402


def state_for(m):
    rc, xf, persp, _ = gm.blit_setup(m, [0, 0, 32, 32], [0, 0, 256, 256])
    assert rc == gm.GEOM_OK
    st = [0] * 64
    for i, c in enumerate(xf):
        st[STATE["XF_A"].addr + i] = c & 0xFFFF_FFFF
    st[STATE["PAINT_CTRL"].addr] = (1 << 5) if persp else 0
    return st


def rot(th, s=1.0, tx=100.0, ty=100.0):
    return [[s * math.cos(th), -s * math.sin(th), tx], [s * math.sin(th), s * math.cos(th), ty], [0, 0, 1]]


@pytest.mark.parametrize("m,cls", [
    ([[1, 0, 10.5], [0, 1, 3], [0, 0, 1]], "translate"),
    ([[2, 0, 0], [0, 3, 0], [0, 0, 1]], "scale_up"),
    ([[0.5, 0, 0], [0, 0.25, 0], [0, 0, 1]], "scale_down"),
    ([[0, -1, 100], [1, 0, 0], [0, 0, 1]], "rotate_90k"),
    ([[-1, 0, 100], [0, -1, 100], [0, 0, 1]], "rotate_90k"),
    (rot(0.3), "rotate_free"),
    (rot(2.0, 1.7), "rotate_free"),
    ([[1, 0.4, 0], [0.1, 1, 0], [0, 0, 1]], "shear"),
    ([[1, 0, 0], [0, 1, 0], [0.001, 0.002, 1]], "perspective"),
])
def test_xform_class(m, cls):
    assert cov.xform_class(state_for(m)) == cls


def test_merge_report(tmp_path):
    a, b = cov.bins()["cg_blit_opts"][:2]
    (tmp_path / "x.json").write_text(json.dumps({"cg_blit_opts": {a: 2}}))
    (tmp_path / "y.json").write_text(json.dumps({"cg_blit_opts": {a: 1, b: 1}, "unknown": {"z": 1}}))
    total = cov.merge(sorted(tmp_path.glob("*.json")))
    assert total["cg_blit_opts"][a] == 3 and total["cg_blit_opts"][b] == 1
    text, full = cov.report(total)
    assert not full and "| cg_blit_opts | 2 | 32 |" in text
