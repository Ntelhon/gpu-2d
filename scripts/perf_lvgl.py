#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""LVGL frame performance on the live RTL backend (spec/bandwidth.md §8; Phase 6 item P6-6).

Runs the LVGL demos at 1280x720 on the verilated g2d_top (build/live/live/lvgl_demo_live32_<cfg>, `make live-perf`)
with fixed AXI latency profiles and the per-frame GPU counters of G2D_FRAME_STATS (PERF_* CSRs read after
vg_lite_finish at the end of each frame), and prints the markdown tables of bandwidth.md §8.

Usage: perf_lvgl.py [--cfgs dw64 dw128 dw128o8] [--profiles ideal lat40 lat90] [--frames 150] [--jobs 8]
                    [--out DIR] [--keep]

Busy cycles are PERF_BUSY_CYCLES (cycles while STATUS.IDLE is low: GPU work only, not the CPU time LVGL spends
between kicks). Frame times are busy cycles at the clock targets of docs/PROJECT_RULES.md (600 MHz ASIC, 50 MHz FPGA).
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "build" / "live" / "live"
W, H = 1280, 720
CFG_DESC = {
    "dw64": "DATA_W 64, 256 B bursts, 8 / 8 outstanding",
    "dw128": "DATA_W 128, 128 B bursts, 4 rd / 2 wr outstanding (test configuration)",
    "dw128o8": "DATA_W 128, 256 B bursts, 8 / 8 outstanding",
}
COLS = ["busy", "draws", "pixels", "rd_beats", "wr_beats", "rd_stall", "wr_stall"]


def run(cfg, prof, demo, frames, out):
    exe = LIVE / f"lvgl_demo_live32_{cfg}"
    tag = f"{demo}_{cfg}_{prof}"
    stats = out / f"{tag}.txt"
    env = dict(os.environ, G2D_LIVE_PROFILE=prof, G2D_FRAME_STATS=str(stats), G2D_LIVE_SEED="1")
    p = subprocess.run([str(exe), demo, str(W), str(H), str(frames), str(out / tag), "0"], env=env,
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{tag}: exit {p.returncode}\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}")
    rows = []
    for ln in stats.read_text().splitlines()[1:]:
        v = [int(x) for x in ln.split()]
        rows.append(dict(zip(["frame"] + COLS, v)))
    return (demo, cfg, prof), rows


def ms(cycles, mhz):
    return cycles / (mhz * 1e3)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cfgs", nargs="+", default=["dw64", "dw128", "dw128o8"])
    ap.add_argument("--profiles", nargs="+", default=["ideal", "lat40", "lat90"])
    ap.add_argument("--frames", type=int, default=150, help="benchmark frames")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--keep", action="store_true", help="keep the stats files and frame dumps")
    a = ap.parse_args()
    missing = [c for c in a.cfgs if not (LIVE / f"lvgl_demo_live32_{c}").exists()]
    if missing:
        sys.exit(f"missing live LVGL builds {missing}: run `make live-perf`")
    out = a.out or Path(tempfile.mkdtemp(prefix="g2d_perf_", dir=os.environ.get("CLAUDE_JOB_DIR")))
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(c, p, "widgets", 1) for c in a.cfgs for p in a.profiles] + \
           [(c, p, "benchmark", a.frames) for c in a.cfgs for p in a.profiles]
    with ThreadPoolExecutor(max_workers=min(a.jobs, 8)) as ex:
        res = dict(ex.map(lambda j: run(*j, out), jobs))

    print(f"Configurations: " + "; ".join(f"{c}: {CFG_DESC.get(c, c)}" for c in a.cfgs) + "\n")
    print("#### Widgets demo, first frame (full redraw)\n")
    print("| Config | Profile | Draws | Busy cycles | Read beats | Write beats | ms @ 600 MHz | ms @ 50 MHz |")
    print("|---|---|---|---|---|---|---|---|")
    for c in a.cfgs:
        for p in a.profiles:
            r = res[("widgets", c, p)][0]
            print(f"| {c} | {p} | {r['draws']} | {r['busy']:,} | {r['rd_beats']:,} | {r['wr_beats']:,} | "
                  f"{ms(r['busy'], 600):.2f} | {ms(r['busy'], 50):.1f} |")
    print(f"\n#### Benchmark demo, {a.frames} frames (busy cycles per frame)\n")
    print("| Config | Profile | Mean | Max (frame) | Mean draws | Mean rd / wr beats | Max ms @ 600 MHz | Max ms @ 50 MHz |")
    print("|---|---|---|---|---|---|---|---|")
    for c in a.cfgs:
        for p in a.profiles:
            rows = res[("benchmark", c, p)]
            busy = [r["busy"] for r in rows]
            mx = max(rows, key=lambda r: r["busy"])
            n = len(rows)
            print(f"| {c} | {p} | {sum(busy) / n:,.0f} | {mx['busy']:,} ({mx['frame']}) | "
                  f"{sum(r['draws'] for r in rows) / n:.0f} | {sum(r['rd_beats'] for r in rows) / n:,.0f} / "
                  f"{sum(r['wr_beats'] for r in rows) / n:,.0f} | {ms(mx['busy'], 600):.2f} | {ms(mx['busy'], 50):.1f} |")
    if a.keep or a.out:
        print(f"\nraw files: {out}")
    else:
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    main()
