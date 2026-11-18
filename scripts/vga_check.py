#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check a VGA frame captured from the pins in the SoC simulation (fpga/sim/tb_g2d_chs.sv +VGA_CAPTURE, little-endian
RGB565 words) against the frame the replay must leave in memory (scripts/fpga_lvgl.py lvgl_fb.raw), bit-exactly
(v1.2, Q-FPGA-5). Also checks the testbench's sync / blanking verdict in its output.

Cheshire's axi_vga starts its timing in the visible area as soon as it is enabled, before its fetcher has data; the
starved pixels go out black and are not consumed, and the fetcher streams FRAME_SIZE bytes per frame without
re-aligning to the frame start. The pixel stream therefore runs a few pixels behind the frame from then on (4 in the
SoC simulation): the capture is the reference rotated by k pixels (its first k pixels are the frame's last k). The
check requires the capture to equal the reference rotated by some k, 0 <= k < 64, every pixel bit-exact, and
reports k (docs/FPGA.md, Q-FPGA-5).

Usage: vga_check.py <capture.raw> <lvgl_fb.raw> [--sim-out <sim.out>] [--width 640] [--ppm <file.ppm>]
"""
import argparse
import re
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("reference")
    ap.add_argument("--sim-out", help="SoC simulation output with the testbench's '[tb] VGA: frame captured' line")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--ppm", help="write the capture as a PPM image (for looking at it)")
    a = ap.parse_args()
    cap = Path(a.capture).read_bytes() if Path(a.capture).exists() else b""
    ref = Path(a.reference).read_bytes()
    ok, why = True, []
    if len(cap) != len(ref):
        ok = False
        why.append("capture %d bytes, reference %d bytes (frame incomplete?)" % (len(cap), len(ref)))
    else:
        n = len(ref)
        shift = next((k for k in range(64) if cap == ref[n - 2 * k:] + ref[:n - 2 * k]), None)
        bad = [] if shift is not None else [i // 2 for i in range(0, n, 2) if cap[i:i + 2] != ref[i:i + 2]]
        if bad:
            ok = False
            x, y = bad[0] % a.width, bad[0] // a.width
            why.append("%d of %d pixels differ, first at (%d, %d): got %04x, expected %04x" % (
                len(bad), len(ref) // 2, x, y, int.from_bytes(cap[2 * bad[0]:2 * bad[0] + 2], "little"),
                int.from_bytes(ref[2 * bad[0]:2 * bad[0] + 2], "little")))
    if a.sim_out:
        m = re.search(r"\[tb\] VGA: frame captured to \S+, sync / blanking errors (\d+)", Path(a.sim_out).read_text())
        if not m:
            ok = False
            why.append("no capture line in %s" % a.sim_out)
        elif int(m.group(1)):
            ok = False
            why.append("%s sync / blanking errors" % m.group(1))
    if a.ppm and cap:
        h = len(cap) // 2 // a.width
        rgb = bytearray()
        for i in range(0, a.width * h * 2, 2):
            v = cap[i] | cap[i + 1] << 8
            r, g, b = v >> 11, v >> 5 & 63, v & 31
            rgb += bytes((r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2))
        Path(a.ppm).write_bytes(b"P6 %d %d 255\n" % (a.width, h) + bytes(rgb))
    if ok and len(cap) == len(ref):
        why.append("bit-exact, stream offset %d pixels" % shift)
    print("vga_check.py: %s: %s" % ("PASS" if ok else "FAIL", "; ".join(why)))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
