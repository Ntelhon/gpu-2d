# SPDX-License-Identifier: Apache-2.0
"""scripts/g2d_replay.py: manifest-driven replay of a command stream with PNG/raw dumps."""

import json

import numpy as np
from PIL import Image

import g2d_replay
from g2d_cmd import FMT, CmdBuf


def test_replay_clear_and_dump(tmp_path):
    w, h, tgt, cmd = 8, 4, 0x1000_0000, 0x0010_0000
    cb = CmdBuf().target(tgt, w, h, FMT["BGRA8888"], stride=w * 4 + 8).clip(0, 0, w, h)
    cb.state("BLEND", MODE=0).state_raw("PAINT_COLOR", 0xFF336699).draw_rect(1, 1, 5, 3).fence(42)
    data = cb.to_bytes()
    (tmp_path / "cmd.bin").write_bytes(data)
    (tmp_path / "fb.bin").write_bytes(bytes(h * (w * 4 + 8)))
    man = {
        "memory": [{"addr": tgt, "file": "fb.bin"}, {"addr": cmd, "file": "cmd.bin"}],
        "kicks": [{"addr": cmd, "size": len(data)}],
        "dump": [{"addr": tgt, "w": w, "h": h, "stride": w * 4 + 8, "fmt": "BGRA8888", "png": "out.png", "raw": "out.bin"}],
    }
    (tmp_path / "m.json").write_text(json.dumps(man))
    assert g2d_replay.main([str(tmp_path / "m.json")]) == 0
    img = np.asarray(Image.open(tmp_path / "out.png").convert("RGBA"))
    assert img.shape == (h, w, 4)
    assert tuple(img[1, 1]) == (0x33, 0x66, 0x99, 0xFF) and tuple(img[0, 0]) == (0, 0, 0, 0)
    raw = (tmp_path / "out.bin").read_bytes()
    assert len(raw) == w * h * 4 and raw[(1 * w + 1) * 4:(1 * w + 1) * 4 + 4] == bytes([0x99, 0x66, 0x33, 0xFF])


def test_replay_reports_error(tmp_path, capsys):
    cmd = 0x0010_0000
    (tmp_path / "cmd.bin").write_bytes(bytes([0, 0, 0, 0x7F, 0, 0, 0, 0]))  # unknown opcode
    man = {"memory": [{"addr": cmd, "file": "cmd.bin"}], "kicks": [{"addr": cmd, "size": 8}]}
    (tmp_path / "m.json").write_text(json.dumps(man))
    assert g2d_replay.main([str(tmp_path / "m.json")]) == 1
    assert "err_status=0x01" in capsys.readouterr().out
