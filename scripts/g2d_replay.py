#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Replay a G2D command stream on the golden model and dump framebuffers.

Manifest (JSON):
{
  "addr_win": [lo, hi],                                    optional
  "memory":   [{"addr": 268435456, "file": "fb.bin"}, ...], raw binary files loaded at GPU addresses
  "kicks":    [{"addr": 1048576, "size": 64}, ...],         command buffers, in order
  "dump":     [{"addr": ..., "w": ..., "h": ..., "stride": ..., "fmt": "BGRA8888" | 19,
                "png": "out.png", "raw": "out.bin"}, ...]   png and/or raw (stored bytes) per buffer
}
Relative file paths are resolved against the manifest's directory. Prints CSR-visible status
(fence, errors) and exits 1 if the GPU halted with an error.

Usage: g2d_replay.py manifest.json [--model-path build/model]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--model-path", type=Path, default=Path(os.environ.get("G2D_MODEL_PATH", ROOT / "build" / "model")))
    args = ap.parse_args(argv)
    sys.path[:0] = [str(args.model_path.resolve()), str(ROOT / "dv" / "common"), str(ROOT / "scripts")]

    import g2d_model as gm
    from g2d_cmd import FMT
    from g2d_image import buffer_argb, buffer_raw, save_png

    man = json.loads(args.manifest.read_text())
    base = args.manifest.resolve().parent
    mem = gm.Memory()
    for m in man.get("memory", []):
        mem.write(int(m["addr"]), (base / m["file"]).read_bytes())
    gpu = gm.Gpu(mem)
    if "addr_win" in man:
        gpu.set_addr_win(*[int(v) for v in man["addr_win"]])
    for k in man.get("kicks", []):
        if not gpu.kick(int(k["addr"]), int(k["size"])):
            break
    for d in man.get("dump", []):
        fmt = FMT[d["fmt"]] if isinstance(d["fmt"], str) else int(d["fmt"])
        args_buf = (int(d["addr"]), int(d["w"]), int(d["h"]), int(d["stride"]), fmt)
        if "png" in d:
            save_png(base / d["png"], buffer_argb(mem.read, *args_buf))
        if "raw" in d:
            (base / d["raw"]).write_bytes(buffer_raw(mem.read, *args_buf).tobytes())
    st = gpu.stats
    print(f"fence_seq=0x{gpu.fence_seq:08x} irq_fence={int(gpu.irq_fence)} halted={int(gpu.halted)} "
          f"err_status=0x{gpu.err_status:02x} err_addr=0x{gpu.err_addr:08x} err_cmd_addr=0x{gpu.err_cmd_addr:08x} "
          f"draws={st.draws} pixels_out={st.pixels_out} commands={st.commands}")
    return 1 if gpu.halted else 0


if __name__ == "__main__":
    sys.exit(main())
