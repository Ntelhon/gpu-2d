# Bandwidth and latency budget (1280×720 @ 60 fps)

Tables in §2 are produced by `scripts/bandwidth.py` (run it to regenerate; inputs are at the top of the
script). Clock targets (decision 2026-10-15): **FPGA 50 MHz** (Cheshire SoC clock on Genesys2,
single-clock v1) and **ASIC 600 MHz**.

## 1. Model

- Frame: 1280×720 = 921 600 px. Source images ARGB8888 (4 B/px).
- Per-pixel traffic: fill = W(dst). Opaque blit = R(src) + W(dst). Blended blit = R(src) + R(dst) + W(dst).
  AA path or blended solid/gradient = R(dst) + W(dst). Edge lists and gradient ramps are < 1 % and are ignored.
- Sustained AXI efficiency 70 % of peak (assumption; to be measured on FPGA in Phase 7).
- Compute: 1 px/clk pipeline. Fill fast path: one full data beat per clock (2 px/clk ARGB8888 at 64 bit).
- Workloads (fractions of screen area per frame):
  - W1 full clear.
  - W2 full-screen opaque blit.
  - W3 full-screen blended blit.
  - W4 typical UI frame: background copy 1.0 + blended widgets/images 0.5 + AA shapes/text 0.3.
  - W5 heavy UI or screen transition: clear + background copy + 2 full-screen blended layers + 0.3 AA.

## 2. Results

Frame 1280x720, 60 fps, AXI efficiency 70 %, source images 4 B/px. "max fps" is the lower of the
bandwidth limit (bw) and the compute limit (cmp).

#### Framebuffer ARGB8888

| Workload | MB/frame | GB/s @60 | Mpx/s @60 | max fps FPGA 50 MHz x 64b | max fps ASIC 600 MHz x 64b | max fps ASIC 600 MHz x 128b |
|---|---|---|---|---|---|---|
| W1 full clear | 3.69 | 0.22 | 55 | 76 (bw) | 911 (bw) | 1823 (bw) |
| W2 full opaque blit | 7.37 | 0.44 | 55 | 38 (bw) | 456 (bw) | 651 (cmp) |
| W3 full blended blit | 11.06 | 0.66 | 55 | 25 (bw) | 304 (bw) | 608 (bw) |
| W4 typical UI frame | 15.11 | 0.91 | 100 | 19 (bw) | 222 (bw) | 362 (cmp) |
| W5 heavy UI / transition | 35.39 | 2.12 | 238 | 8 (bw) | 95 (bw) | 183 (cmp) |

#### Framebuffer RGB565

| Workload | MB/frame | GB/s @60 | Mpx/s @60 | max fps FPGA 50 MHz x 64b | max fps ASIC 600 MHz x 64b | max fps ASIC 600 MHz x 128b |
|---|---|---|---|---|---|---|
| W1 full clear | 1.84 | 0.11 | 55 | 152 (bw) | 1823 (bw) | 3646 (bw) |
| W2 full opaque blit | 5.53 | 0.33 | 55 | 51 (bw) | 608 (bw) | 651 (cmp) |
| W3 full blended blit | 7.37 | 0.44 | 55 | 38 (bw) | 456 (bw) | 651 (cmp) |
| W4 typical UI frame | 10.32 | 0.62 | 100 | 27 (bw) | 326 (bw) | 362 (cmp) |
| W5 heavy UI / transition | 23.22 | 1.39 | 238 | 12 (bw) | 145 (bw) | 190 (cmp) |

## 3. Conclusions

1. **ASIC, 600 MHz, DATA_W = 64 meets 720p60** for every workload, with 1.6× margin on the worst case
   (W5 ARGB8888: 95 fps). The 128-bit port doubles the bandwidth headroom; the design is then
   compute-bound at 1 px/clk. Default stays 64; 128 is recommended if the SoC memory system offers it.
2. **1 px/clk is enough.** The worst case needs 238 Mpx/s against 600 Mpx/s available. A 2 px/clk
   blend pipeline (my earlier assumption) is not needed; only the fill fast path goes wider.
   Decision recorded in docs/STATUS.md.
3. **FPGA, 50 MHz single clock: functional, not real-time at 720p.** Expect about 19–27 fps for a
   typical UI frame and 8–12 fps for transitions, before the Cheshire crossbar and LLC are shared with the
   CPU. The 720p60 claim is supported by cycle-accurate counts (Phase 4/5) scaled to 600 MHz, plus FPGA
   measurements of efficiency. The later CDC phase (arch.md §2.1) separates the clocks.
4. Memory-boundness means the important efficiency features are: long bursts on scanline traversal,
   skipping the destination read whenever the blend allows (NONE with cov = 255), and the source cache
   hit rate for transformed blits.

## 4. Latency and outstanding transactions

Bytes in flight needed = latency × bandwidth per clock.

| Config | Assumed read latency | Demand per clk | In flight needed | 256 B bursts | `RD_OUTSTANDING` |
|---|---|---|---|---|---|
| ASIC 600 MHz × 64 b | 150 ns = 90 clk | 8 B | 720 B | 3 | 8 |
| ASIC 600 MHz × 128 b | 150 ns = 90 clk | 16 B | 1440 B | 6 | 8 |
| FPGA 50 MHz × 64 b | ~40 clk (assumed, Cheshire crossbar + LLC + CDC + MIG, not measured) | 8 B | 320 B | 2 | 8 |

`RD_OUTSTANDING = 8` and `WR_OUTSTANDING = 8` cover all three cases. Phase 3 check: source-cache fills
are 64-byte bursts, so 8 outstanding reads hold only 512 bytes; at 90-clock latency 16 outstanding
reads did not change the measured blit rates (arch.md §6.1), so the default stays 8. The read-data buffers per stream
(destination, source cache refill) hold at least the outstanding bursts that stream may have, so R is
never back-pressured by a full buffer: 4 × 256 B for the destination read buffer.

## 5. On-chip buffer sizing

| Buffer | Size | Reasoning |
|---|---|---|
| Destination read buffer | 1 KB | 4 bursts of 256 B in flight (§4), so a scanline blend streams without bubbles. |
| Write combine | 512 B | 2 × 256 B: one burst filling while one drains. |
| Command prefetch | 512 B | Commands are ≤ 66 words. 2 bursts hide fetch latency between draws. |
| Source cache | 8 KB, 4-way, 64 B lines | Transformed blits walk 16×16 destination tiles. Under 90° rotation one tile touches 16 source rows of 64 B (1 KB). With bilinear (17×17 footprint, 2 lines per row when unaligned) at 2× downscale it touches ~34 rows × 2 lines ≈ 4.3 KB. 8 KB keeps this plus the next tile's prefetch. Untransformed copies go through the same cache in scanline order (64-byte fills, Phase 3 decision Q1 a); the cache is latency-hiding (arch.md §6.4). |
| Gradient ramp | 1 KB | 256 × 32 bit (rendering.md §10). |
| Edge buffer | 8 KB | 512 edges × 16 B. Typical UI paths (rounded rects, arcs, glyph outlines) have < 200 edges after flattening at 1/8 px. Longer lists are streamed per row segment. |
| Winding accumulator | 4 KB | 256 px segment × 16 samples × 8 bit. |

Total ≈ 23 KB of SRAM. On the XC7K325T (445 × 36 Kb BRAM) this is ~6 BRAM36-equivalents plus LUTRAM for
small FIFOs, which is negligible.

## 6. Path rasterizer throughput check

For one pixel row of a segment of width `w` with `e` edges crossing the row, crossing setup costs 16
sub-scanlines × `e`, at a target of 4 crossings/clk, i.e. `4e` clk, then `w` clk for the prefix sum and
coverage. For a 64 px wide glyph-sized path with 8 crossings: 32 + 64 clk per row, about 1.5 clk/px,
or ≈ 0.7 px/clk. For large shapes (w = 256, e = 4): ≈ 1.06 clk/px. AA paths in W4/W5 cover 0.3 of the
screen, so this stays inside the 600 MHz budget (W5's path share, 0.3 × 921 600 px at 1.5 instead of 1 clk/px,
adds ≈ 0.14 Mclk ≈ 0.23 ms per frame at 600 MHz). The exact crossing rate is fixed in Phase 4 and checked with the performance counters.

## 7. Perspective divider

1 px/clk perspective needs two exact quotients per pixel (rendering.md §7.1), or one reciprocal plus
two multiplies with a correction step. The pipelined divider costs ~35–50 stages of latency, which is
fine because it is fully pipelined. Its area is the main ASIC cost of 2.5D and is reported in Phase 8.

## 8. Measured LVGL frames (Phase 6, live RTL backend)

Measured on the verilated `g2d_top` running the unchanged driver under LVGL v9.6 at 1280×720 ARGB8888
(`scripts/perf_lvgl.py`, after `make live-perf`; 2026-10-17, two independent runs gave identical counts).
Per frame the PERF counters are read after `vg_lite_finish`: busy cycles = `PERF_BUSY_CYCLES` (GPU not idle;
CPU time spent by LVGL between kicks is not included). Memory profiles: `ideal` (no latency, no
back-pressure), `latN` (every read and write response exactly N cycles late; 40 is the FPGA assumption of
§4, 90 the ASIC one). Configurations: dw64 (defaults: 256 B bursts, 8 reads / 8 writes outstanding), dw128
(the L3 test configuration: 128 B bursts, 4 / 2 outstanding), dw128o8 (DATA_W 128 with the defaults).

#### Widgets demo, first frame (full redraw, 284 draws)

| Config | Profile | Busy cycles | Read / write beats | ms @ 600 MHz | ms @ 50 MHz |
|---|---|---|---|---|---|
| dw64 | ideal | 1 942 753 | 712 739 / 877 928 | 3.24 | 38.9 |
| dw64 | lat40 | 1 966 188 | 712 739 / 877 928 | 3.28 | 39.3 |
| dw64 | lat90 | 2 020 182 | 712 739 / 877 928 | 3.37 | 40.4 |
| dw128 | ideal | 1 668 767 | 358 370 / 442 006 | 2.78 | 33.4 |
| dw128 | lat40 | 2 316 873 | 358 370 / 442 006 | 3.86 | 46.3 |
| dw128 | lat90 | 3 681 915 | 358 370 / 442 006 | 6.14 | 73.6 |
| dw128o8 | ideal | 1 669 341 | 358 986 / 442 006 | 2.78 | 33.4 |
| dw128o8 | lat40 | 1 692 812 | 358 986 / 442 006 | 2.82 | 33.9 |
| dw128o8 | lat90 | 1 745 894 | 358 986 / 442 006 | 2.91 | 34.9 |

#### Benchmark demo, 150 frames (76 draws per frame on average; LVGL redraws only invalidated areas)

| Config | Profile | Mean busy / frame | Max busy (frame) | Max ms @ 600 MHz | Max ms @ 50 MHz |
|---|---|---|---|---|---|
| dw64 | ideal | 926 901 | 1 596 833 (107) | 2.66 | 31.9 |
| dw64 | lat40 | 931 563 | 1 604 982 (142) | 2.67 | 32.1 |
| dw64 | lat90 | 1 015 664 | 1 807 850 (121) | 3.01 | 36.2 |
| dw128 | ideal | 647 097 | 1 254 885 (143) | 2.09 | 25.1 |
| dw128 | lat40 | 1 266 526 | 2 011 411 (145) | 3.35 | 40.2 |
| dw128 | lat90 | 2 540 594 | 4 063 882 (121) | 6.77 | 81.3 |
| dw128o8 | ideal | 643 193 | 1 244 948 (106) | 2.07 | 24.9 |
| dw128o8 | lat40 | 647 750 | 1 252 835 (106) | 2.09 | 25.1 |
| dw128o8 | lat90 | 714 436 | 1 412 387 (106) | 2.35 | 28.2 |

Conclusions:

1. **720p60 at 600 MHz holds with margin.** The heaviest measured frame (full widgets redraw) needs at most
   3.4 ms of GPU time in the recommended configurations (dw64, dw128o8) at 90-cycle latency, against a
   16.7 ms frame: ≈ 5× headroom, consistent with §3 (W4 typical frame 222 fps bandwidth limit at 64 bit).
2. **At 50 MHz (FPGA)** a full 720p redraw takes 33–40 ms (≈ 25–30 fps); the benchmark's partial updates
   average 0.65–1.0 Mclk = 13–20 ms (≈ 50–75 fps) with worst frames of 25–36 ms. Real time at 60 fps on the
   FPGA only with partial updates, as predicted in §3.3.
3. **The dw128 slowdown under latency is the outstanding limit, not the width.** With 4 reads / 2 writes
   outstanding and 128 B bursts, dw128 holds 512 B of reads in flight: at 40 cycles × 16 B/clk = 640 B needed
   (§4) it is latency-bound (mean busy +96 % at lat40, +293 % at lat90). The same width with the default 8 / 8
   and 256 B bursts (dw128o8) loses < 1 % at lat40 and 11 % at lat90 (benchmark mean) and is the fastest configuration
   everywhere. The dw128 test configuration exists to stress small bursts and few outstanding transactions
   in verification; it is not a recommended integration. Recommendation unchanged (§3.1, §4): DATA_W 64 by
   default, 128 when the SoC offers it, `RD_OUTSTANDING` / `WR_OUTSTANDING` ≥ 8.
4. **Latency tolerance at 64 bit:** +1.2 % / +4.0 % busy at lat40 / lat90 for the widgets frame
   (+0.5 % / +9.6 % for the benchmark). This bounds every latency effect, including the one read latency
   per kick before its first command (OPEN_QUESTIONS Q-RTL-2), so kick prefetch stays unbuilt.
5. Bus occupancy: the widgets frame at dw64 moves 1.59 M beats (0.71 M read + 0.88 M write) in 1.94 M busy
   cycles, i.e. the 64-bit port carries a beat in 82 % of the cycles summed over both channels: the frame is
   mostly bandwidth-bound, as the §2 model assumes.
