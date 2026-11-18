# Closure of open items (v2.0)

Every item of docs/OPEN_QUESTIONS.md, docs/FEATURES_LATER.md and spec/api-subset.md §7 ("later") with its outcome at
v2.0 (docs/PHASES.md "v2.0 plan", item 11). Nothing is left without a recorded outcome; items that need hardware,
a network host or a third party that this repository cannot reach are closed here with the check that remains and
who does it.

| Outcome | Meaning |
|---|---|
| **Resolved** | answered by evidence, implemented and tested, or decided by the user (version / date given) |
| **Interpretation** | VGLite behaviour the public header does not define and that cannot be checked without vendor hardware: our reading is the accepted interpretation, defined in spec/rendering.md (user 2026-11-12, v2.0 question 5, assumption (a)). Compatibility is API-level (CLAUDE.md), so a different vendor result is a listed difference, not a bug |
| **Accepted** | a known limitation or design choice, documented where given; no further work planned |
| **External** | done as far as this repository can go; the remaining check needs the board, a network host or a third party (named) |
| **Future** | moved to a separate project |

## OPEN_QUESTIONS: VGLite API behaviour

| ID | Topic | Outcome |
|---|---|---|
| Q-API-1 | `vg_lite_clear` and the scissor | Resolved 2026-10-17 (LVGL evidence) |
| Q-API-2 | Colour-key alpha | Resolved 2026-10-17 (LVGL evidence) |
| Q-API-3 | Colour of an A8 source | Interpretation: rendering.md (Phase 6 B-6.2, consistent with every LVGL v9.6 use) |
| Q-API-4 | Global alpha and RGB of premultiplied sources | Interpretation: rendering.md §5.3 (only A changes; RGB scaling through MULTIPLY) |
| Q-API-5 | Scissor right / bottom inclusive? | Resolved 2026-10-17 (LVGL evidence: exclusive) |
| Q-API-6 | `SPREAD_FILL` | Interpretation, user decision 2026-10-20 (rendering.md §10.1) |
| Q-API-7 | Exact VGLite rounding | Accepted: API-level compatibility; our rounding is bit-exact to the golden model |
| Q-API-8 | Singular blit matrix | Interpretation: `INVALID_ARGUMENT` (rendering.md §9.3; OpenVG draws nothing, listed difference) |
| Q-API-9 | Gradient `pre_multiplied` | Resolved 2026-10-17 (LVGL evidence) |
| Q-API-10 | Gradient matrix independent of the path matrix | Resolved 2026-10-15 (evidence) |
| Q-API-11 | `transparency_mode` | Interpretation, changed in Phase 6 on LVGL evidence (rendering.md) |
| Q-API-12 | `draw_pattern` colours | Interpretation from LVGL v9.6 and its ThorVG reference; v1.4 (rendering.md §9.5) |
| Q-API-13 | SCREEN on premultiplied values | Interpretation: the header formula, computed as rendering.md §5.6 |
| Q-API-14 | Fractional AA pattern rectangles | Resolved v1.5: image paint on paths gives the exact 16-sample border (rendering.md §9.5); the row's earlier "decision needed" is obsolete |
| Q-API-15 | A4 / 4444 / 1555 / 2222 expansion | Interpretation: LVGL v9.6's own decoder conventions (rendering.md §3, §9.6) |
| Q-API-16 | `VLC_OP_BREAK` | Interpretation, assumption A3 approved 2026-10-19 (rendering.md §9.2) |
| Q-API-17 | Dash phase | Interpretation: OpenVG 1.1 §8.7.3, phase applied as given (rendering.md §9.7.1) |
| Q-API-18 | `index_endian` of 1 / 2 / 4-bit indices | Interpretation: the header comments as written, user decision (rendering.md §3.5) |
| Q-API-19 | OpenVG filtering cross-check | Resolved: user decisions 2026-10-24, built in v1.5 |
| Q-API-20 | Porter-Duff blend | Interpretation v1.6 (rendering.md §5.7 family 3) |
| Q-API-21 | SVG2 blends | Interpretation v1.6 (W3C Compositing Level 1, rendering.md §5.7 family 2) |
| Q-API-22 | RECOLOR mode | Interpretation v1.6 (= MULTIPLY) |
| Q-API-23 | Mask layers | Interpretation v1.6 (rendering.md §6.1, §9.9; OpenVG 1.1 §7.2) |
| Q-API-24 | `vg_lite_blit2` | Interpretation v1.6 (two sequential blits) |
| Q-API-25 | `vg_lite_set_mirror` | Interpretation v1.6 (rendering.md §5.8) |
| Q-API-26 | Stencil image mode | Interpretation v1.6 (rendering.md §5.3.1) |
| Q-API-27 | Gaussian filter | Interpretation, user decision 2026-10-26 (rendering.md §7.3) |
| Q-API-28 | YUV range / siting | Interpretation, user decision 2026-10-26 (video range, nearest chroma; rendering.md §3.7) |
| Q-API-29 | Colour unit order and domain | Interpretation, user decision 2026-10-26 (rendering.md §5.2.1, listed OpenVG deviation) |
| Q-API-30 | `OPENVG_*` buffer formats | Interpretation, user decision 2026-10-26 (header values kept) |

The "How to verify" notes in OPEN_QUESTIONS stay as the procedure for anyone with a Vivante VGLite board.

## OPEN_QUESTIONS: tests

| ID | Topic | Outcome |
|---|---|---|
| Q-TEST-1 | Coverage bound for arbitrary curves | Accepted: the bounds of rendering.md §12 hold for the listed shape classes; random self-intersecting Béziers on tiny targets are covered by the pixel-max and inside / outside bounds (they come from the 1/32 px flattening and 16-sample coverage, not from the driver) |
| Q-TEST-2 | Rotated-image screenshot criterion | Resolved 2026-10-17 (user) |
| Q-TEST-3 | `make smoke` | Resolved 2026-10-19 |
| Q-TEST-4 | Image fill on curved paths vs ThorVG | Resolved 2026-10-24 (own criterion) |
| Q-TEST-5 | Reset stage in the async build simulation | Resolved v1.7 (direct port resets, documented) |

## OPEN_QUESTIONS: FPGA / Cheshire

| ID | Topic | Outcome |
|---|---|---|
| Q-FPGA-1 | SoC / DRAM clocks | Resolved 2026-10-17 (source) |
| Q-FPGA-2 | DRAM region, widths | Resolved 2026-10-17 (source) |
| Q-FPGA-3 | External ports, interrupts, CSR slot | Resolved 2026-10-17 (source) |
| Q-FPGA-4 | Read latency on the board | External: board run (user), perf counters `PERF_RD_STALL` / `PERF_CYCLES`; bandwidth.md keeps the 40-cycle assumption |
| Q-FPGA-5 | Display output | Resolved v1.2 (VGA in the SoC sim); the display controller is Future (DC-1..8) |
| Q-FPGA-6 | Bare-metal HAL on Cheshire | Resolved 2026-10-17 |
| Q-FPGA-7 | SoC-level simulator | Resolved: Verilator SoC simulation (`fpga/sim/socsim.py`), used through v2.0 |
| Q-FPGA-8 | Board-side facts (UART, udev, hw_server) | External: board machine (user); `run_board.sh` asks for what it cannot detect |
| Q-FPGA-9 | Trimmed Cheshire hangs on the board | External: in Cheshire's DDR path, not G2D (a CPU-only test hangs with no G2D traffic); board builds use the untrimmed Cheshire or trim mask 7 (DOOM); the per-unit bisection is on the user's machine |
| Q-FPGA-10 | Path rendering wrong on the v1.7 board | External: board run (user). v1.8 added the tools to localise it (RAM collision flags `DBG_STATUS.RAM_COLL`, write signatures `WR_SIG`, reference `fpga/ref/wsig_k1_seed1.txt` for `fpga_check.py --wsig-ref`); the SoC simulation with a DRAM timing model is bit-exact in all 11 scene kinds. Remaining: the `--wsig-ref` board test and a Vivado netlist simulation (user's machine) |
| Q-FPGA-11 | VGA glitches under load | Future: the display controller project (DC-3 underrun handling, DC-8 throttle); `axi_vga_g2d` stays a demo |

## OPEN_QUESTIONS: performance, RTL, tools, integration, Linux

| ID | Topic | Outcome |
|---|---|---|
| Q-PERF-1 | Golden model speed | Resolved v1.7 (2.2–2.8× faster, bit-exact) |
| Q-PERF-2 | Transformed blits under latency | Accepted (measured; 0.77 / 0.74 px/clk at 90 cycles) |
| Q-PERF-3 | Glyph-sized paths 0.83 px/clk | Accepted, user decision 2026-10-16 |
| Q-PERF-4 | Skip destination reads of covered spans | Accepted: measured in v1.7, no throughput gain; only read bandwidth |
| Q-RTL-1 | Timing of the R data path at 600 MHz | Resolved v2.0 by measurement (docs/ASIC.md): routed on IHP SG13G2 130 nm, core paths 50.4 MHz typical / 31.3 MHz slow / 78.2 MHz fast. The 600 MHz target is not reachable in this technology and no scaled estimate is given (user 2026-11-12). The R data path is not the critical path |
| Q-RTL-2 | No prefetch of the next kicked buffer | Accepted (one read latency per kick; measured, spec/bandwidth.md §8) |
| Q-RTL-3 | Stop point after an `AXI_WR` error | Accepted (asynchronous by nature, arch.md §8) |
| Q-RTL-4 | Registered `irq_o` | Resolved 2026-10-18 (correct by design) |
| Q-RTL-5 | Phase 2 buffers in flops | Resolved 2026-10-15 |
| Q-RTL-6 | Phase 3 timing at 600 MHz | Resolved v2.0 by measurement (same run). The source-cache tag stage is not the critical path; the critical path is the ready / valid chain mask reader -> ramp / cache request queue (docs/ASIC.md, "How to improve it" item 3) |
| Q-RTL-7 | Perspective divider area | Resolved v2.0 by measurement: the whole logic is about 1.03 M NAND2 equivalents (7.47 mm2 after synthesis); the divider is listed among the area candidates in docs/ASIC.md. No per-module area split was taken (Accepted) |
| Q-RTL-8 | Phase 4 timing at 600 MHz | Resolved v2.0 by measurement (same run): the rasterizer's band-setup stage is not on the worst reported paths |
| Q-RTL-9 | Halt settled with kicks queued | Resolved v1.4 (`STATUS.CMD_ACTIVE`) |
| Q-RTL-10 | v1.6 FPGA timing | Resolved v1.7 (100 MHz OOC met) |
| Q-TOOL-1 | ASIC tool flow | Resolved v2.0: LibreLane 3.0.3, OpenSTA 3.2.0, sv2v 0.0.13.1, IHP SG13G2 PDK with SRAM macros; g2d_top routed to a GDS (docs/ASIC.md). External: DRC / LVS / antenna signoff and the hold fix need a machine with more than 15 GB (docs/ASIC.md "Known defects"). Formal equivalence (LEC): Accepted as not available in the open flow; gate-level simulation of the routed netlist: fills / blits / soft reset bit-exact, path draws differ (sv2v conversion of `g2d_raster`, docs/ASIC.md defect 0) |
| Q-TOOL-2 | Vendor GPU material | Accepted (independent implementation, never read) |
| Q-INT-1 | OBI revision | External: an integrator's OBI system; the adapter's choice (OBI 1.x without `rready`) is documented in INTEGRATION.md |
| Q-INT-2 | SDC in a real STA flow | Resolved v2.0: the SDC constrains the synthesized and routed g2d_top in OpenROAD and in the OpenSTA signoff runs (docs/ASIC.md). Finding: its 60 % I/O budget is too large for a 20 ns clock in this technology (I/O paths limit at 37-38 MHz); the reference run used `G2D_IN_FRAC` / `G2D_OUT_FRAC` 0.3 |
| Q-INT-3 | IP-XACT schema validity | Resolved 2026-11-16: validates against the IEEE 1685-2014 schema (EDA Alliance mirror of the Accellera files; OPEN_QUESTIONS Q-INT-3). Tool import (Vivado / Kactus2): External, user |
| Q-INT-4 | Bus protocol details from memory | External: a third-party VIP or the specifications; our BFMs and SVA checkers are the current evidence |
| Q-LNX-1 | Coherence of the Linux DMA pool | External: board run (user); correct on Cheshire by construction (fence flushes the D-cache) |
| Q-LNX-2 | Kernel module licence | Resolved 2026-11-12 (user) |
| Q-LNX-3 | Kernel build coverage | External: board run (user) with a Linux-target toolchain |
| Q-LNX-4 | DT vendor prefix, maintainer | Accepted until an upstream submission |

## New at v2.0: reference layout defects (docs/ASIC.md)

| Item | Outcome |
|---|---|
| Netlist not equivalent to the RTL in path draws (sv2v conversion of `g2d_raster`, found by the gate-level simulation after the acceptance) | Fixed in the RTL in v2.1 (sv2v mis-converted `na_q[~cur_q]`; docs/ASIC.md defect 0, sv2v coding rule; `dv/tests/test_sv2v_equiv.py` in `make test`). The p20e reference layout keeps the defect until it is routed again |
| Hold violations at 29 SRAM endpoints, slow corner 31.3 MHz, 1637 antenna nets, no DRC / LVS / IR-drop signoff | Accepted by the user 2026-11-16 as the state of the v2.0 reference layout (demonstration, not a tape-out candidate); fixes listed in docs/ASIC.md. External: a larger machine, or a later return to the flow |

## FEATURES_LATER

| ID | Item | Outcome |
|---|---|---|
| F-1 | Image paint on paths | Resolved v1.5 |
| F-2 | Texel wrap modes | Resolved v1.4 |
| F-3 | More pixel formats | Resolved v1.4 (24-bit sources in hardware v1.6b) |
| F-4 | Radial gradients | Resolved v1.5 |
| F-5 | Dashed strokes | Resolved v1.3 |
| F-6 | Platform-neutral bus adapters | Resolved v1.9 (APB, AHB-Lite, OBI, reg_intf, Wishbone, TL-UL, AXI4 wrappers) |
| T-1 | FPGA timing after v1.6 | Resolved v1.7 |
| T-2 | FPGA area, trimmed configuration | Resolved v1.7 (`FEATURES`) |
| D-1 .. D-4 | Driver robustness findings | Resolved v1.6 (test_robust) |
| D-5 | Stroke disc sin / cos cost | Resolved v2.0 step A (unit-circle cache, bit-identical) |
| DC-1 .. DC-8 | Display controller | Future: separate optional project (user 2026-11-12) |

## spec/api-subset.md §7 (later)

| Item | Outcome |
|---|---|
| 24-bit sources in hardware, YUV with CSC, gamma, colour transform / pixel matrix, Gaussian filter | Resolved v1.6b |
| Tiled and compressed (DEC) layouts, tiled YUV | Accepted: `NOT_SUPPORT` (vendor layouts not public), v1.6 |
| FLEXA, fast-clear buffers (`fc_enable`), command caching / dump calls | Accepted: `NOT_SUPPORT`, reasons in api-subset.md, v1.6 |
| Dashed strokes, arcs, `VLC_OP_BREAK`, projective path matrices, command buffer / memory pool / frame delimiter calls | Resolved v1.3 |
| 16 / 8-bit, sub-byte and indexed formats, wrap modes, dither, SPREAD_FILL, TRANSPARENT | Resolved v1.4 |
