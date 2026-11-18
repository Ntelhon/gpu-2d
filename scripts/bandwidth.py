#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bandwidth and throughput budget for 1280x720 @ 60 fps (spec/bandwidth.md tables come from this script).

Usage: bandwidth.py            print the markdown tables
"""

W, H, FPS = 1280, 720, 60
PX = W * H
EFF = 0.70  # sustained/peak AXI efficiency assumed for DRAM (bursts, refresh, turnarounds)

# bytes per pixel per operation, as (src_read, dst_read, dst_write) multipliers of (src_bpp, dst_bpp)
OPS = {
    "fill":    (0, 0, 1),  # clear / opaque solid fill, no dst read
    "copy":    (1, 0, 1),  # opaque blit, BLEND_NONE
    "blit":    (1, 1, 1),  # blended blit (SRC_OVER)
    "path":    (0, 1, 1),  # AA path / blended solid or gradient paint (edge + ramp traffic negligible)
}

# (name, [(op, fraction of screen area)])
WORKLOADS = [
    ("W1 full clear", [("fill", 1.0)]),
    ("W2 full opaque blit", [("copy", 1.0)]),
    ("W3 full blended blit", [("blit", 1.0)]),
    ("W4 typical UI frame", [("copy", 1.0), ("blit", 0.5), ("path", 0.3)]),
    ("W5 heavy UI / transition", [("fill", 1.0), ("copy", 1.0), ("blit", 2.0), ("path", 0.3)]),
]

FB = [("ARGB8888", 4), ("RGB565", 2)]
SRC_BPP = 4  # UI images are ARGB8888 in both cases

BUSES = [("FPGA 50 MHz x 64b", 50e6, 64), ("ASIC 600 MHz x 64b", 600e6, 64), ("ASIC 600 MHz x 128b", 600e6, 128)]


def frame_cost(work, dst_bpp, data_w):
    """Return (bytes per frame, compute cycles per frame) for one workload."""
    nbytes = 0.0
    cycles = 0.0
    for op, frac in work:
        s, r, w = OPS[op]
        px = PX * frac
        nbytes += px * (s * SRC_BPP + r * dst_bpp + w * dst_bpp)
        if op == "fill":
            cycles += px / max(1, (data_w // 8) // dst_bpp)  # fill fast path: one beat per cycle
        else:
            cycles += px  # 1 px/clk pipeline
    return nbytes, cycles


def main():
    print(f"Frame {W}x{H} = {PX} px; {FPS} fps; AXI efficiency assumed {EFF:.0%}; source images {SRC_BPP} B/px.\n")
    for fbname, dbpp in FB:
        print(f"#### Framebuffer {fbname}\n")
        hdr = "| Workload | MB/frame | GB/s @60 | Mpx/s @60 |" + "".join(f" max fps {b[0]} |" for b in BUSES)
        print(hdr)
        print("|" + "---|" * (4 + len(BUSES)))
        for name, work in WORKLOADS:
            nb, _ = frame_cost(work, dbpp, 64)
            mpx = sum(f for _, f in work) * PX * FPS / 1e6
            cells = []
            for _, clk, dw in BUSES:
                nb_dw, cyc = frame_cost(work, dbpp, dw)
                fps_bw = clk * dw / 8 * EFF / nb_dw
                fps_cmp = clk / cyc
                lim = "bw" if fps_bw < fps_cmp else "cmp"
                cells.append(f"{min(fps_bw, fps_cmp):.0f} ({lim})")
            print(f"| {name} | {nb / 1e6:.2f} | {nb * FPS / 1e9:.2f} | {mpx:.0f} | " + " | ".join(cells) + " |")
        print()


if __name__ == "__main__":
    main()
