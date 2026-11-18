# Project status

**v2.0 (drivers and closure): done 2026-11-16**, finalized on the user's instruction ("accept this gds as reference ...
create the documents and finalize v2.0"), merged into `main`, tag `v2.0` (local; the user pushes tags). Steps: A
drivers / interrupts / FreeRTOS, B Linux, C ASIC reference layout + programming manual + closure. The ASIC layout is a
reference with known defects (docs/ASIC.md). Results: the three "Test results (v2.0 step ...)" sections below;
every open item has an outcome in docs/CLOSURE_V2.md.

**v1.9 (integration package and memory QoS): done, approved 2026-11-12**, merged into `main`, tag `v1.9-done`.
Results: "Test results (v1.9 integration package)" below.

**v1.8 (board-proven): done, approved 2026-11-12**, merged into `main`, tag `v1.8-done` (SoC simulation; board runs
pending). Results: "Test results (v1.8 board-proven)" below.

**Side project DOOM: done, approved 2026-11-06 with known bugs** (user: "tag doom as approved with current bugs"),
merged into `main`, tag `doom-done`. DOOM runs on the Genesys2 from the SD card, keyboard on the HID port, picture on
VGA presented by the G2D; residual VGA glitches (Q-FPGA-11), path rendering on the board (Q-FPGA-10) and the trimmed
Cheshire hang (Q-FPGA-9) stay open. Results: "Test results (DOOM side project)" below, docs/FPGA.md FPG-12..FPG-15.

Current version: **v1.5 (image paint on paths + radial gradients)**, done, approved 2026-10-24, merged into `main`, tag
`v1.5-done`. v1.6 (VGLite 4 extras) continues on `v1.6-vglite-extras` from the v1.5 tip (user
2026-10-24: "start v1.5 and then v1.6 next to it"). v1.4 (small hardware features) done, approved 2026-10-24 (with the changes in the decision log), merged
into `main`, tag `v1.4-done`. v1.3 (driver-only
API completion) done, approved 2026-10-20, merged into `main`, tag `v1.3-done`. v1.2 (board readiness)
done, approved 2026-10-19, merged into `main`, tag `v1.2-done`. v1.1 (CDC) done, approved
2026-10-18, merged into `main`, tag `v1.1-done`. From 2026-10-18 work is versioned v1.x → v2
(docs/ROADMAP_V2.md); `v1` (= tag `v1.0`, merged into `main`) is Phases 0–7a with the board run pending.
Phase 7: FPGA bring-up, all done except the board run. Phase 6 done (tag `phase-6-done`, approved 2026-10-17, merged
into `main`). Tag `phase-5-patch1` (`cf8baa7`): the Phase 5 RTL with the Phase 6 RTL fixes
B-6.1, B-6.4, B-6.5, all Phase 5 gates re-run green.
Phase 5 done (tag `phase-5-done`, approved 2026-10-16). Phase 4 done (tag `phase-4-done`, approved 2026-10-16). Phase 3 done (tag `phase-3-done`, approved 2026-10-16). Phase 2 done (tag `phase-2-done`, approved 2026-10-15). Phase 1 done (tag `phase-1-done`, approved 2026-10-15). Phase 0 done (tag `phase-0-done`, approved 2026-10-15).

## Done

| Phase | Item |
|---|---|
| 0 | Repository structure, CLAUDE.md, .gitignore, Makefile skeleton (`regs`, `regs-check`, `lint`, `test`, `regress`, `model`, `check-tools`, `clean`) |
| 0 | Tool check. Verilator 5.052 built into ~/tools; cocotb 2.1.0, cocotbext-axi 0.1.28, pybind11, PyYAML installed (docs/TOOLS.md) |
| 0 | VGLite version frozen: 4.0.135, MIT header vendored (spec/vglite-reference.md) |
| 0 | spec/api-subset.md, spec/rendering.md (bit-exact semantics), spec/arch.md, spec/commands.md, spec/bandwidth.md, spec/verification-plan.md |
| 0 | spec/regs.yaml + scripts/gen_regs.py. Generates the SV CSR/state packages, the CSR register file, the C header and docs/regs.md |
| 0 | Tests: generator validation (9 negative cases), stale-output check, C header compile + value check, cocotb test of the generated CSR block (6 tests: reset, RW + strobes, RO/const, W1C priority, WO strobe, unmapped error). Lint clean (Verilator -Wall). |
| 1 | Spec refinements: exact driver algorithms (rendering.md §9-10), deterministic validation/errors (§13), edge clamp, 48-bit gradient constant (`GRAD_R_HI`), 64-byte address window, path data encoding (from LVGL) |
| 1 | C++17 golden model (`model/`): all stage functions + command executor; pybind11 module `g2d_model` |
| 1 | Shared C99 driver geometry (`sw/lib/geom`): path decode/flatten/guard-clip/quantize/bbox, blit setup, gradient coefficients and ramps; deterministic double arithmetic |
| 1 | Test infrastructure: `dv/common` (command builder from regs.yaml, independent reference `refimpl.py`, float reference `floatref.py`, scenes, image helpers), `scripts/image_compare.py`, `scripts/g2d_replay.py` |
| 1 | Committed reference vectors and 6 reference images (`model/tests/refs`, 92 KB) |
| 1 | Traceability spec → model → tests in `model/README.md` |
| 2 | RTL: common blocks, CSR subsystem (AXI4-Lite adapter, control, perf), AXI4 master (N read clients, write engine), command fetch/parser/state/draw check (all of rendering.md §13), rect fill fast path, `g2d_core`, `g2d_top` |
| 2 | Generator: `soft_clr_i` on the CSR block (soft reset clears w1c fields) |
| 2 | DV: own AXI4 memory model + protocol monitor, L3 environment against the golden model (whole-memory, registers, white-box state, perf counters, PNG dumps), generated cocotb wrappers, shared runner with random X initialisation |
| 2 | Specs updated: arch.md (soft reset, AXI rules, internal interfaces §4.3, fetch, halt semantics §8, perf definitions, measured fill rate), rendering.md §13.1 (Phase 2 restriction), verification-plan.md (P2 test mapping, new IDs CSR-07, AXI-07, CMD-06, FIL-05, SYS-P2) |
| 3 | RTL: SRAM wrappers (`g2d_ram_1r1w`, `g2d_ram_sp`) and SRAM FIFO, command prefetch on SRAM; blend unit (global alpha, 16 modes, coverage blend, compress); inverse transform with an exact pipelined perspective divider; texel addressing; texel unit (colour keys, premultiply, bilinear, MULTIPLY); latency-hiding 8 KB source cache; general pixel pipeline (walker, destination reader, write combiner); dispatch with draw-ordering drain; `RESTRICT` level 3 |
| 3 | DV: L2 benches for every new block, L3 blit/blend bench (`test_blit`), functional coverage (`g2d_coverage.py`, `scripts/coverage_report.py`), shared L3 helpers, stream driver; AXI memory model fix (profile changes) |
| 4 | RTL: pipelined signed divider; path edge loader; rasterizer (bands, add-and-compare step pipeline, double-buffered winding accumulator, sweep), per-band streaming of long edge lists, multi-pass rows; gradient index and ramp (RAM, loader, coherence rule); path and gradient integration (read clients 3, 4); draw-check restriction removed; cache lookahead 256 and write-combiner span queue 32 (Q-PERF-2) |
| 4 | Golden model: `PathRaster` in the RTL's structure (same edge load, band division, stepping, segments); direct formula and literal sample test kept as cross-checks |
| 4 | DV: L2 benches for divider, edge loader, rasterizer (incl. forced pass splits), gradient; L3 `test_path` (PTH-01..06, GRD-01, AXI-08, PRF-01, coverage matrix); coverage groups `cg_path`, `cg_paint_path`, `cg_grad`; clock/reset start-up helper; read-client responder with strobes |
| 4 | Docs: `docs/PROGRAMMING.md` (driver/HAL rules); specs updated (arch.md §4.3 deadlock argument, §6.1 PRF-01, §6.2–6.4 raster/gradient/memories; rendering.md §13.1; bandwidth.md §6; verification-plan.md P4 mapping) |
| 5 | DV: SVA checkers in dv/sva (AXI4, AXI4-Lite, read clients, write producers, internal streams, credits, FIFO overflow, cache locks), bound into the RTL, armed at the first reset; system tests SYS-01..05 (`test_sys`); coverage groups `cg_fill`, `cg_align`, `cg_axi`, `cg_errors`; exact checks of every performance counter except CYCLES / SRC_CACHE_MISS; AXI slave option READY-after-VALID |
| 5 | Infrastructure: `scripts/regress.py` (parallel, 16-core budget, pipelined builds, process groups, lock, triage summary, JUnit), `make test` 3.4 min (was 16.5), `make code-coverage` (scripts/code_coverage.py), `scripts/mutate.py` mutation campaign |
| 5 | RTL fixes B-5.1..B-5.5 (table below); specs: arch.md §2.2 soft reset through the clear cycle, §4.3 handshake withdrawal rule, §6.3 stop rules, §8 ERR_ADDR / ERR_CMD_ADDR for AXI errors; verification-plan.md §4 (coverage as built, code coverage, mutation testing), §6 (regression tiers) |
| 5 | K-1 evaluation (analysis, nothing built): docs/OPEN_QUESTIONS.md Q-PERF-3 |
| 6 | Driver `sw/driver/vg_lite` (C99, no libm): HAL boundary `sw/hal/g2d_hal.h`, two command buffers with fences, state shadow re-emitted after soft reset, buffers / map / upload, clear / blit / blit_rect / copy_image, matrices, scissor, blends 0–15, global alpha, colour keys, paths (S8 / S16 / S32 / FP32, curves flattened by `sw/lib/geom`), legacy and extended linear gradients (PAD / REPEAT / REFLECT), queries, driver-side validation (rendering.md §13) |
| 6 | Driver-only features: `vg_lite_draw_pattern` on rectangular paths (rendering.md §9.5), sources in A4 / BGRA5551 / 4444 / 2222 converted with a content-hashed cache (§9.6), solid strokes with caps / joins / miter limit (§9.7) |
| 6 | Backends: software (golden model) with a kick recorder (`G2D_RECORD`, write masks, CHECK / POST records); RTL replay SYS-06 (`dv/tests/test_sys06.py`); live Verilator harness `dv/live` (AXI4-Lite master, AXI4 memory with random profiles, SVA bound); bare-metal Cheshire HAL compile-checked for RV64GC (`make baremetal-check`) |
| 6 | LVGL v9.6 host demos (`sw/apps/lvgl`: widgets, benchmark, vector, formats; VG-Lite on our driver, LVGL software renderer, `vg_lite_tvg` simulator variant), `make lvgl`; survey of LVGL's vg_lite use (docs/LVGL_SURVEY.md), hardware gap list with estimates (docs/FEATURES_LATER.md) |
| 6 | Verification: API scenes with an independent stream replay (test_scenes.py), ideal-formula checks (test_ideal.py, `*_vs_ideal`), strokes vs the exact region (test_stroke.py), Skia cross-checks (test_thirdparty.py), LVGL screenshots and three-way check (test_lvgl.py), live RTL (test_live.py), SYS-06; docs/VERIFICATION_MATRIX.md (feature × model, every cell filled); `make smoke` |
| 6 | RTL fixes B-6.1 (SCREEN), B-6.4 / B-6.5 (AXI_RD halt settling); performance on the live RTL (spec/bandwidth.md §8, `scripts/perf_lvgl.py`, `make live-perf`) |
| 6 | Docs: PROGRAMMING.md §5–7 (driver, HAL backends), api-subset.md status, verification-plan.md (SYS-06, API-01..07, Phase 6 tiers), rendering.md §5.6 (B-6.1), §8.2, §9.5–9.7, arch.md §8 (AXI_RD halt settling) |
| 7 | FPGA integration (fpga/): Cheshire `6c6dd5e8` set up by script with a PLIC patch (59 sources), config package, AXI bridge (PULP converters on the CSR side), Genesys2 top generated from Cheshire's, Vivado flows (`make fpga`, `make fpga-ooc`), board package (`make fpga-package`, `run_board.sh`) |
| 7 | Bare-metal test (`sw/apps/baremetal`): vgl_scene scripts through the unchanged driver on Cheshire, CRC-32 per buffer, PLIC interrupt test; `scripts/fpga_scenes.py` (expected output from the software backend), `scripts/fpga_check.py`; HAL `G2D_POOL_ADDR` |
| 7 | SoC simulation on Verilator (`fpga/sim`, `make fpga-sim`): Cheshire + G2D, DRAM model, passive boot, UART decoder |
| 7 | Results: FPG-01 G2D OOC 50 MHz WNS +5.24 ns; FPG-02 bitstream, all constraints met (WNS +0.015 ns, worst paths in Cheshire); FPG-03 SoC sim 6 / 6 scene kinds bit-exact + IRQ through the PLIC (docs/FPGA.md §5) |
| 3 | Specs updated: arch.md (dispatch §5.3, measured rates §6.1, traversal §6.2, pipeline as built §6.3, cache and memories §6.4, perf §9, layout §12), rendering.md §7.1 (exact divider), §13.1 (`RESTRICT`), bandwidth.md (cache for untransformed copies, outstanding reads), verification-plan.md (P3 mapping, SYS-P3, coverage as built) |
| v1.3 | Driver geometry: elliptical arcs (OpenVG 1.1 §8.4 / Appendix A, own deterministic atan2), `VLC_OP_BREAK` (A3, UNVERIFIED), projective path matrices (adaptive flattening against the projection, near-plane clip, projection; fills, strokes, gradient fills), dashed strokes (F-5, OpenVG 1.1 §8.7.3–8.7.4) (rendering.md §9.2.1, §9.7.1, §9.8) |
| v1.3 | Driver API: `vg_lite_init_arc_path`, `vg_lite_set_command_buffer`, `vg_lite_set_memory_pool`, `vg_lite_frame_delimiter`; Q-API-14 (b) centre-sampled fractional pattern rectangles (scaled LVGL images now drawn); spec notes Q-API-4 / 8 / 15 |
| v1.3 | Tests: `test_paths_ext.py` (API-08), dash tests in `test_stroke.py` (API-09, incl. Skia), `scene_vectors` in API scenes / SYS-06 / live RTL / FPGA scene kinds (vgl_scene `dstroke`), `test_pattern_fractional_rect`, `t_cmd_api`; LVGL tests updated (dashes and scaled A4 drawn); Skia area criterion with an absolute part (latent failure on never-run seeds) |
| v1.4 | Hardware formats (rendering.md §3.2, §3.5): 4444 / 5551 / 2222 in every channel order as source and target, A4 / A2 / A1 / L4 and INDEX_1/2/4/8 (CLUT in the ramp RAM, point-sampled) as sources, `SRC_FMT.SUB_ORDER`; dither (`TGT_FMT.DITHER`, own 4 × 4 Bayer, §3.6); wrap modes NONE / COLOR / PAD / REPEAT / REFLECT + `BORDER_COLOR` (§7.5, F-2 closed); `SPREAD = FILL` (Q-API-6); `SRC_FMT.TRANSPARENT` (Q-API-11); `STATUS.CMD_ACTIVE` (Q-RTL-9); F-3 closed except 24-bit (v1.6) |
| v1.4 | RTL: signed 32-bit `g2d_xform` (|U| / 4W divider), `g2d_tex_addr` (wrap per axis, reciprocal modulo, independent row addresses, sub-byte shifts), new `g2d_tex_split` / `g2d_tex_merge` around the cache (request mask), texel border colour and A = 0 flag, ramp-as-CLUT routing with a register slice, dither in the blend pack stage, dithered fills off the fast path, draw-check rules |
| v1.4 | Driver: every new format natively, `vg_lite_set_CLUT`, `index_endian`, `enable_dither`, TRANSPARENT, SPREAD_FILL with `paint_color`, `vg_lite_draw_pattern` as one blit with the hardware wrap mode (any pattern matrix), CPU conversion only for 24-bit; feature bits IM_INDEX_FORMAT / INDEX_ENDIAN / DITHER |
| v1.5 | F-1 image paint on paths: DRAW_PATH with an IMAGE paint (coverage gated by the source test, scanline walk), `vg_lite_draw_pattern` on any path / path matrix (exact rectangles stay one blit), Q-API-14 closed (exact AA borders) |
| v1.5 | F-4 radial gradients with a focal point (`PAINT_CTRL.SRC = 3`, `GRAD_UP .. GRAD_VR_HI`, rendering.md §10.4; OpenVG 1.1 §9.3.2 in a rotated frame, 32-stage `g2d_isqrt`), driver `vg_lite_*radial_grad`, feature bit |
| v1.6b | State space 128 words; 24-bit sources in hardware (driver conversion removed, `24BIT` = 1); texel sub-request sequencer (`g2d_tex_seq`) |
| v1.6b | 3 × 3 Gaussian filter (`PAINT_CTRL.GAUSS`, `GAUSS_W`, ¼ px/clk); YUV sources NV12 / ANV12 / NV16 / NV24 / YV12 / YV16 / YV24 / YUY2 / AYUY2 (BT.601 / BT.709 limited range) |
| v1.6b | Colour unit (gamma, pixel matrix, colour transform; image colours in hardware, solid colours / ramps by the driver); `OPENVG_*` buffer formats with automatic linear ↔ sRGB conversion |
| v1.7 | Timing (T-1): G2D OOC 100 MHz met on the K7 (+0.367 ns, fmax ≈ 114 MHz; was −6.1 ns): core reset distribution stage (`g2d_rst_sync`, `RST_STAGES`), blend front stages SI / SA / SB and family-product / pack stages, fill colour split, register slices (texel / mask inputs, transform out, cache in), registered arbitration and in-flight counting |
| v1.7 | Area / fit (T-2): parameter `FEATURES` (blend families, Gaussian, YUV, colour unit, radial, mask; `HWCFG.FEAT_*`, draw-check BAD_STATE, driver NOT_SUPPORT / feature bits); trimmed Cheshire + full G2D fits (82.8 % LUT, 97.1 % slices) |
| v1.7 | Performance: Q-PERF-2 `RD_OUTSTANDING` 16 + 16 destination spans (blended tiled blits 0.86 px/clk at 90-cycle latency, was 0.62); K-1 band-setup overlap in `g2d_raster` (64-px glyphs 0.867, was 0.834); Q-PERF-1 golden model 2–3× faster (`scripts/bench_model.py`) |
| v1.6 | Driver robustness D-1..D-4 (`drv_quiesce`, sticky fault, per-command-buffer target list, init cleanup; fault / allocator-failure / non-coherent-cache injection in the model HAL, `test_robust`); `vg_lite_scissor_rects` n > 1 (driver, disjoint pieces) |
| v1.6 | Mask layers: state MASK_* 0x31–0x35, `g2d_mask_rd` (read client 5), `vg_lite_create/fill/blit/render/set/enable/disable/destroy_masklayer` + `vg_lite_set_mask_layer`-style ops (rendering.md §6.1, §9.9) |
| v1.6 | Blend families OpenVG / SVG2 / Porter-Duff (`BLEND.FAMILY`, `BLEND_FUNC`, `vg_lite_blend_func`; blend unit 13 stages with an 8-stage restoring divider, §5.7) |
| v1.6 | Image modes STENCIL (§5.3.1), RECOLOR (= MULTIPLY), NONE; A8L8 sources; target mirror `vg_lite_set_mirror` (`TGT_FMT.FLIP_Y`, §5.8); `vg_lite_blit2`; `vg_lite_set_tess_buffer`; closures (tiled / compressed, FLEXA, fast clear, dump calls) |
| v1.5 | Q-API-19: (b) premultiplied border colour under SRC_PREMUL (TEXEL mode); (a) [0, alpha] clamp proven a no-op on the premultiplied path (no hardware); (c) perspective subset test vs OpenVG; (d) projective pattern matrices documented |
| v1.5 | Tests: L3 `img01`, `grd03`, `prf02`, `cov_path_matrix` image / radial; L2 `grad_radial`; SYS streams with image paths and radial; API `test_pattern_paths`, `test_radial` (OpenVG float, Skia), `test_pattern_border_premultiplied`, scene kinds `radial` / `imgpaths` (API, live RTL, SYS-06, SoC sim); model `test_premultiplied_filter_stays_in_alpha_range`, `test_perspective_openvg_subset`; coverage groups cg_radial, cg_paint_path image / radial |
| v1.4 | Tests: model unit tests (dither, wrap, exhaustive reciprocal modulo), model vs reference for every new format / mode, L2 benches (pkg, xform, tex_addr, cache seams, texel, blend, grad, draw check), L3 `fmt01` / `wrp01` / `dth01` / `clt01`, GRD-01 FILL, CMD_ACTIVE in `axi_rd_cmd_settle` and every halted wait; API: `test_source_formats_vs_decoded` (26 formats), pattern tests on hardware wrap, `test_dither_formula`, `test_transparent_skips_alpha0`, FILL; coverage groups cg_src14 / cg_tgt14 / cg_wrap / cg_img14 |

## In progress

**v2.1** (sv2v fix of `g2d_raster`, ASIC defect 0 fixed in the RTL) merged into `main` 2026-11-16; a new layout to
carry it into the GDS is a separate ~10 h run (user's choice). v2.0 is finalized
(2026-11-16). Open on the user's side: board runs (v1.8 / v1.9 bitstreams,
Q-FPGA-10, FreeRTOS, Linux `insmod`), pushing the tags, deleting the old
remote display branch. Open on a return to the ASIC flow: docs/ASIC.md "How to improve it" (hold fix first).

## Known bugs

- **ASIC reference layout only (v2.0, docs/ASIC.md defect 0):** the p20e netlist / GDS loses the path draws:
  sv2v 0.0.13.1 mis-converted the index `na_q[~cur_q]` of `g2d_raster`. **Fixed in the RTL in v2.1** (2026-11-16,
  `nxt_par`; the sv2v output of the whole design passes the RTL benches, dv/tests/test_sv2v_equiv.py); a new layout
  is needed to remove it from the GDS.
- **Trimmed Cheshire bitstreams hang on the board** (2026-11-02, OPEN_QUESTIONS Q-FPGA-9): every `FPGA_TRIM=1`
  bitstream, the v1.7 release ones included, jams the CPU's DDR write path; do not use the v1.7 board package.
  Board builds go back to the untrimmed Cheshire; per-unit trims USB / iDMA / bus-error pass.
- **Path rendering wrong on the board** with G2D v1.7 (2026-11-04, Q-FPGA-10): blits / clears bit-exact, path-based
  scenes not; simulation of the same configuration passes. Under investigation.
- **VGA scan-out glitches under load** (2026-11-06, Q-FPGA-11): with the axi_vga fork (per-frame re-sync +
  prefetch) the picture no longer slides, but some frames still blink / break for one frame while DOOM plays
  ("not super but playable"); fewer with a smaller DOOM screen size (less CPU + G2D memory traffic). Accepted for
  the DOOM approval.

## Bugs found and fixed in Phase 5

| ID | Found by | Block | Bug | Fix | Finding test |
|---|---|---|---|---|---|
| B-5.1 | SYS-03 (seed 3, dw64): `p_req_hold` on the destination reader | `g2d_ctrl` | `soft_busy` / `stop` fell in the `soft_clr` cycle: an engine that was drained but still held queued work (stopped engines report idle regardless of contents) presented a read request for one cycle and the clear withdrew it; a pass-through client could start an AXI handshake it then abandons | `soft_busy_o`, `stop_o`, `STATUS.SOFT_RESET_BUSY` (and the kick drop) cover the clear cycle | `test_sys` SYS-03 seed 3 (dw64) with the dv/sva checkers |
| B-5.2 | SYS-04 (seed 6, dw128): `p_max` fetch credits 17 > 16 | `g2d_rr_arb` (AXI read master) | the arbiter's lock held `valid_o` after the locked requester withdrew (command fetch stopped by a halt); the read master then captured the stale request and issued an AR nobody owned, whose beats entered the fetch FIFO without credit | a lock holds only while the locked requester still requests; otherwise the arbiter re-arbitrates in the same cycle (as its header already specified) | `test_common` `rr_arb` (requesters now withdraw; fails on the old arbiter) |
| B-5.3 | SYS-04 (seed 15): memory mismatch after recovery | `g2d_cmd_fetch` | a rejected kick (size 0 or not a multiple of 8, range) raised its error when popped, i.e. as soon as the previous buffer's last word was fetched, before that buffer's last command (a draw waiting for the engine) executed | a kick failing its checks is popped only when the parser is idle | `test_top` `cmd05_bad_kick_order` (fails on the old fetch) |
| B-5.4 | SYS-03 (seeds 12, 19, 25, 26): `g2d_ram_1r1w` read/write collision in the rasterizer state buffer | `g2d_raster` | on a soft-reset stop the FSM left `S_ROWS` but the issuing pass stayed active with a frozen position: it re-read one edge every cycle until the clear, colliding with its own row write-back (results discarded by the clear, but a real SRAM collision) | stop also ends the issuing pass | `test_sys` SYS-03 seed 25 (dw64) |
| B-5.5 | SYS-03 (seed 34, dw128): soft reset never completed | `g2d_pix` / `g2d_ramp` | gradient pixels waited for their ramp also under stop; a soft reset before the ramp read was accepted left the ramp unloaded forever, the pixels never passed and the write combiner waited for the rest of its span: `drained` never came (deadlock) | under stop gradient pixels do not wait for the ramp and the ramp RAM is not read (a load may be landing) | `test_path` `grd02_soft_reset_ramp` (hangs on the old RTL) |

### Mutation survivors (Phase 5 triage)

| Mutants | Class | Reason |
|---|---|---|
| 1, 2, 3, 74, 129 | test gap, closed | stall / busy / drain counters were not compared; now exact at every check |
| 8 | test gap, closed | write master waiting for AWREADY before AWVALID (AXI dependency rule); slave option `ready_after_valid` |
| 61 | test gap, closed | arbiter priority wrap at N a power of two; bench widths 2 and 4 |
| 27, 44, 56, 57, 81, 123 | equivalent | loop bound `<` → `<=`: the extra iteration only indexes past the array end (write ignored) |
| 33, 114, 89 | equivalent | comparison changed only at equality, where both branches give the same value |
| 64 | equivalent | divider cast `(RW+1)'` → `(RW+0)'`: the divisor fits in RW bits, same zero extension |
| 9, 93 | equivalent | value set by the soft-reset clear is rewritten before first use (`bad_q` at the next request, `loaded_q` at the next `need_i`) |
| 105 | equivalent | walker `done_o` raised early in SETUP2: the draw also waits for `drained`, which needs the walker idle |
| 124 | equivalent | accumulator FREE → FILL dropped: FILL differs from FREE only in `free_o`, and a pass into the buffer being filled is only started in a multi-pass row, where `free` is bypassed |
| 18 | performance-only | texel-cache victim choice: any victim is functionally correct (hit rate only) |

Checker and test-bench findings (not RTL bugs): the dv/sva checkers and the texel cache's simulation check
evaluated random pre-reset values at time 0 (now armed by the first reset / skip time 0); ERR_ADDR /
ERR_CMD_ADDR for AXI errors were under-specified (arch.md §8 now states them); `g2d_fill` withdraws an
unaccepted write request under soft reset (designed; rule in arch.md §4.3).

## Bugs found and fixed in Phase 6

| ID | Found by | Block | Bug | Fix | Finding test |
|---|---|---|---|---|---|
| B-6.1 | LVGL vector demo vs LVGL's software renderer (survey) | spec rendering.md §5.6, model `ops.cpp`, reference `refimpl.py`, RTL `g2d_blend.sv` | SCREEN was specified as `(S + D) - m(S, D)` with saturation after each step: the sum saturated first, so the result was wrong by up to 255 whenever `S + D > 255` (e.g. magenta over a light grey gave (10,245,10) instead of (255,245,255)). Spec, model, reference and RTL agreed bit-exactly, so no test could see it | `S + (D - m(S, D))`: no step saturates, result `round(S + D - S·D/255)` exactly, same as before whenever nothing saturated; no extra multiplier in the RTL; 47 committed blend vectors regenerated (SCREEN only) | `model/tests/test_stages.py::test_blend_vs_ideal` (every mode vs its ideal formula over all premultiplied inputs; fails on the old SCREEN) |
| B-6.2 | LVGL formats demo vs LVGL's software renderer (recoloured A4 image: mean difference 30) | driver `drv_image_paint` | A8 / A4 sources were premultiplied white under every blend; LVGL draws recoloured alpha images with NORMAL_LVGL, which multiplies by S.A itself, so the alpha was applied twice (up to ~64 levels darker at half alpha) | premultiplied white only under the Porter-Duff blends, plain white under the LVGL blends 11–15 (Q-API-3 refined) | `sw/tests/test_scenes.py::test_a8_recolor_vs_ideal[NORMAL_LVGL]` (vs the ideal recolour formula; fails on the old driver) |
| B-6.3 | test_stroke.py (cap/join sweep vs the exact stroke region) | driver geometry `g2d_geom_stroke_edges` (new code, before its first commit) | join and square-cap pieces shared an edge with the segment rectangle; the two collinear edges were quantized separately and could leave a sliver of up to 1/32 px (2 of 16 samples missing inside the stroke: a crack at joints) | join / cap pieces reach `e = min(hw, L0, L1)/2` into the segment rectangles; straight vertices get a patch across the seam | `sw/tests/test_stroke.py::test_no_cracks` (fails with the overlap removed) |
| B-6.4 | Phase 5 gate regression on the SCREEN-fixed RTL, `SEED=111 G2D_FULL=1 test_top[dw128]` `axi_errors_system` (FENCE_SEQ 0, expected 1) | `g2d_core` (drained / busy), `g2d_cmd_fetch`, `g2d_cmd_parse` | after an AXI_RD error on command words the parser legally goes on with the commands before the faulting word (arch.md §8), but `drained` (engines + AXI idle) and `core_busy` (parser / fetch masked once HALTED) did not count that: between two of those commands the core looked drained, so a waiter (the bench, software polling IDLE) took the halt as settled while earlier commands were still to run | the command side is live while halted by AXI_RD only, not soft-resetting, the parser not in its halt state and either mid-command or with command beats buffered / in flight (`cmd_live`, new outputs `g2d_cmd_fetch.pend_o`, `g2d_cmd_parse.halt_o`); `drained` includes it. arch.md §8 states when such a halt has settled | `dv/tests/test_top.py::axi_rd_cmd_settle` (white-box: no FENCE / AW after HALTED + drained; fails on the old RTL at DATA_W 64 and 128) |
| B-6.5 | SYS-04 `axi_rd_src` in the 300-seed SYS gate (dw64 seeds 6, 11, 21, …: "timeout waiting for the GPU"), on the first B-6.4 fix (`79121fd`, never tagged) | `g2d_core` (`cmd_live`) | the first B-6.4 fix counted every parser state other than idle / halted as live; after an AXI_RD error on a source read the fetch stops requesting, and a parser waiting in a command's payload for words that will never be fetched stayed "live" forever: the core never drained, the halt never settled | live = parser checking / issuing / fencing (new output `g2d_cmd_parse.exec_o`) or command beats buffered / in flight; a parser waiting for words with nothing in flight is settled | `dv/tests/test_top.py::axi_rd_src_starve` (source-read error, then 300 NOPs with 30-word payloads; hangs on `79121fd` at DATA_W 64 and 128) |

## Bugs found and fixed in Phase 7

| ID | Found by | Block | Bug | Fix | Finding test |
|---|---|---|---|---|---|
| B-7.1 | linking the driver for CVA6 (undefined `acos`, `cos`, `sin`) | driver geometry `sw/lib/geom` (stroke discs, Phase 6) | round caps / joins used libm `sin` / `cos` / `acos`, against rendering.md §9.1 (only correctly rounded operations): host and CVA6 libm may differ in the last bit, so the same stroke could produce different edges on the two | the driver's polynomial sin / cos moved to `g2d_geom_sincos_deg` (vg_lite_rotate unchanged), vertex count by bisection on `cos(180°/n) ≥ 1 − tol/r`; spec §9.1 / §9.7 state it | FPG-03 strokes scene bit-exact between the host software backend and CVA6; test_stroke / test_live green on the new discs |
| B-7.2 | Vivado xsim 2024.2 (elaborating g2d_top) | `rtl/pix/g2d_pix.sv` | state-latch loops indexed `state_i[int'(G2D_ST_XF_A) + k]`: legal SV, rejected by xsim as non-constant (a tool-portability problem; Verilator and Vivado synthesis accepted it) | int localparams `XfBase` / `CkeyBase` | xsim elaborates g2d_top; `make lint`, `make test` green |

## Phase 6 working notes (folded from the session handoff)

- Builds: `make model`, `make lvgl` (build/lvgl/sw/lvgl_demo_{vg32,vg16,sw32,sw16,tv32}), `make live`
  (build/live/live/{vgl_scene,test_driver,lvgl_demo_live32}_live_{dw64,dw128}), `make live-perf` (adds dw128o8),
  `make baremetal-check`. CMake globs are evaluated at configure time: after adding a source file run `make live` /
  `make lvgl` (they re-configure), not only `cmake --build`.
- Independent implementation: LVGL's `src/libs/vg_lite_driver` and any vendor GPU material are never read or compiled. LVGL's
  `src/debugging/vg_lite_tvg` (LVGL code + the public MIT Vivante header) is used by the tv32 variant only.
- Third-party tools added in Phase 6: skia-python 87.6 (BSD-3) in ~/tools/python; ThorVG and vg_lite_tvg are built
  from the LVGL v9.6 tree (MIT). docs/TOOLS.md.
- LVGL's VG-Lite decoder converts image assets itself; the driver conversion covers buffers passed directly. LVGL logs
  every failing vg_lite call at ERROR level; the demos run with `LV_VG_LITE_USE_ASSERT 0` and the tests fail on any
  logged error (except the pinned known gaps).
- When mutating C under -Werror, make sure the mutant builds (an unused-parameter error once left the old binary in
  place). Phase 6 mutation checks: pattern 8/9 (1 near-equivalent), conversion 3/3, B-6.2 1/1, stroker 4/4.

## Decisions waiting for the user (Phase 6; Phase 6 approved 2026-10-17, items 1–5 carried into the Phase 7 start questions)

1. **Q-API-14**: transformed images LVGL clips with a fractional AA rectangle path are NOT_SUPPORT (approved §9.5), so
   they are missing. Recommendation: driver-only centre-sampled border (AA off on the border pixels), listed as a known
   difference with an edge-mask test; exact support comes with F-1.
2. **Q-API-3 refinement (B-6.2)**: A8 / A4 sources premultiplied white only under the Porter-Duff blends, plain white
   under the LVGL blends (implemented; needs approval, UNVERIFIED on Vivante hardware).
3. **Q-TEST-3**: `make smoke` = `make test` (assumption).
4. **Test bounds corrected from measurements** (decision log 2026-10-17): AA one-edge mean ≤ 6.8 over 48 polygons, Skia
   stroke check with per-pixel attribution and ≤ 8 excursion pixels per shape.
5. **Proposals, not built**: Q-RTL-9 (STATUS bit for "halt settled" with kicks queued), FEATURES_LATER F-1 / F-4
   estimates, `RD_OUTSTANDING` / `WR_OUTSTANDING` ≥ 8 for any 128-bit integration (bandwidth.md §8).
6. ~~Phase 6 approval, merge of `phase-6-driver` into `main`.~~ Approved and merged 2026-10-17.

## Known issues

| ID | Issue |
|---|---|
| K-2 | FPGA capacity / timing: Cheshire + G2D use 87.7 % of the XC7K325T LUTs (G2D + bridge 40.2 k, Cheshire 124.2 k) and close timing at 50 MHz with WNS +0.015 ns, the worst paths in Cheshire's LLC atomics adapter. Fine for v1 bring-up; a later CDC / higher-clock phase or more G2D logic needs Cheshire features trimmed (e.g. USB, VGA, serial link) or a bigger device. |
| K-1 | PRF-01: glyph-sized AA paths (60×60, ≤ 8 crossings per row) reach 0.83 px/clk with ideal memory; Phase 4 assumption A8 asks ≥ 0.9. Large shapes reach 0.95. Cause: per-draw edge load and per-band setup are not overlapped with the rows. **Accepted by the user 2026-10-16** (latency per draw, not bandwidth: 720p60 needs ~55 Mpx/s; LVGL text normally uses A8 bitmap fonts through the blit path). Q-PERF-3 is not built in Phase 5; the cheaper alternative (prefetch the next draw's edge list on the existing edge channel while the current draw renders, no new memory) is to be evaluated first. **Phase 5 evaluation** (docs/OPEN_QUESTIONS.md Q-PERF-3): edge prefetch lifts 64-px glyphs from 0.837 to at most 0.893 px/clk (ideal memory), both measures to 0.960; at 24 px the row passes dominate (0.305, at most 0.371 with both). Recommendation: build neither now. **Approved with Phase 5 (2026-10-16): not built.** |

## Test results (v2.0 step C: ASIC reference layout, manual, closure; 2026-11-12 .. 16, container)

| Item | Result |
|---|---|
| `make regress` (on dc59b87; later commits change scripts, docs, the Makefile and one opt-in test only) | make test 135 / 135; model suites 2125 / 2125 (seeds 1-5); benches `--full` 310 / 310; LVGL + live RTL + SYS-06 dw64 / dw128 / async / async_s3d2 6 / 6; functional coverage 100 %. Logs build/logs/v2.0/regress_v20.log, regress_v20_phase6.log |
| IHP memory variant `rtl/ram/ihp` | `test_ram_variant[ihp]` (top-level subset on the PDK SRAM models) and 8 extra sizes pass; lane-enable mutant caught; `make lint` covers it (10 standalone sizes) |
| ASIC layout (run p20e, docs/ASIC.md) | IHP SG13G2, 47.8 mm2, 638 371 standard cells + 131 SRAM macros, detailed-routing DRC 0, GDS written (KLayout). Core fmax 50.4 MHz typical / 31.3 MHz slow / 78.2 MHz fast at the 20 ns constraint |
| ASIC defect found while finalizing | the synthesized netlist is not equivalent to the RTL in path draws (sv2v, docs/ASIC.md defect 0); RTL / FPGA / model unaffected |
| ASIC defects (accepted, user 2026-11-16) | hold violated at 29 SRAM endpoints (worst -0.34 ns); slow corner below 50 MHz; 1637 antenna nets; KLayout DRC, Magic DRC, LVS, XOR and IR drop not completed (memory / disk) |
| Gate-level simulation (`make asic-gls`, routed netlist, black-box bench) | fills in all formats, soft reset, random fill / blit streams: bit-exact. Random system streams with path draws: **mismatch**, traced to sv2v 0.0.13.1's conversion of `g2d_raster` (the sv2v output alone fails identically; RTL with SYNTHESIS defined passes; bisection over 48 modules). Strict expected failures `test_gls_sys`, `test_sv2v_equiv` |
| Programming manual | docs/PROGRAMMING.md: hardware interface, rules, driver, HAL backends (model, live, bare metal, FreeRTOS, Linux), HAL reference and porting guide |
| Closure | docs/CLOSURE_V2.md: every OPEN_QUESTIONS / FEATURES_LATER / api-subset "later" item with its outcome |
| Final checks before the merge | `make test` 137 / 137 (12.3 min, on the final tree; the first run had one DOOM test fail because the fetched game data was missing: the test now skips without it); `make lint` clean (63 units, 20 variants, RAM variants incl. ihp); spdx, regs-check clean; `g2d_env.py` got a black-box mode for the gate-level bench (no change for RTL runs) |

## Test results (v2.0 step B: Linux, 2026-11-12, container: build + host test; board boot by the user)

| Item | Result |
|---|---|
| Kernel module `g2d.ko` (`make linux-kmod`) | W=1, 0 warnings, against Linux 5.10.7 + cva6-sdk patches (Cheshire `linux64_defconfig`) and 6.12.117 LTS; 40 / 42 imported symbols, all exported by that kernel (scripts/build_kmod.py; a missing export fails the check, mutant) |
| Device tree (`make linux-dt-check`) | binding `sw/linux/bindings/gpu/g2d.yaml` valid (dtschema 2026.9); Cheshire genesys2 DTS + G2D node valid; a node without `interrupts` rejected |
| Linux HAL (`test_linux_hal`, ctest) | vg_lite over the Linux HAL and a fake `/dev/g2d` (kernel semantics on the golden model): 15 checks PASS: EBUSY on a second open, EPERM on ADDR_WIN / MEM_BASE_HI writes, out-of-pool addresses refused, 3 sequences alone and in 3 threads = model HAL CRCs |
| `make test` | 125 / 125; `make model` ctest 8 / 8; spdx and regs-check clean |
| Not done here | full kernel build and RISC-V user-space build (no Linux-target toolchain, Q-LNX-3); insmod / board run (user) |

## Test results (v2.0 step A: drivers, interrupts, FreeRTOS, 2026-11-12)

| Item | Result |
|---|---|
| Thread-safe API (C-1) | generated lock wrappers (scripts/gen_drv_api.py, 84 functions); sw/tests/test_threads.c: 4 threads x 40 rounds = single-thread CRCs, no nested lock, one thread inside; without the lock the driver crashes (mutant) |
| HAL clock, memory window (C-5, C-6) | test_robust t_clock_and_base: timeout measured in HAL time (a quarter of the waits at 400 us per wait), MEM_BASE_HI from the HAL |
| Interrupt wait on bare metal (C-3) | Cheshire PLIC + WFI + CLINT timeout (G2D_HAL_IRQ=1): SoC sim clears and paths bit-exact, write signatures = v1.8 reference |
| Memory QoS API | vg_lite_g2d_set_mem_attr / set_mem_qos: test_robust t_mem_api (values, range checks, before init) |
| Strokes (D-5) | unit circle cached per vertex count: strokes / vectors expected CRCs identical to v1.8; sin / cos calls of vectors_0 100 896 -> 10 474 |
| FreeRTOS (V11.3.3) | host (POSIX port): 4 tasks x 30 rounds = single-task CRCs, fails without the mutex; Cheshire SoC sim (RISC-V port, RTL G2D): 3 tasks x 3 rounds, CRCs = host model, 18 G2D interrupts, TB_RESULT PASS |
| `make test` | 125 / 125 |
| Bugs found | FreeRTOS: the level G2D interrupt re-fired without an acknowledge in the ISR; FP registers were not saved per task (configENABLE_FPU); the Cheshire-linked memset made vg_lite_init take minutes of SoC time (HAL now clears with 64-bit stores) |

## Test results (v1.9 integration package, 2026-11-12, container)

| Item | Result |
|---|---|
| `make lint` | clean: 63 units, 20 parameter variants (AXI4 adapter at 32 / 128 / 512 bit), `g2d_top` with the Xilinx and the ASIC memory variants |
| `make regress` (2026-11-12, 4 jobs) | test suite 125 / 125; model 2125 / 2125 (seeds 1–5); benches `--full` 308 / 310 on the first run: the full-length runs found two bugs in the new v1.9 tests (not in the RTL), both fixed: `mem_qos_system` read the memory-port pins in the MEM_ASYNC build (they hold the last crossed payload while idle) and `dft01` reset only part of an async build mid-run; after the fix test_top 5 builds × 5 seeds 25 / 25; LVGL + live RTL + SYS-06 dw64 / dw128 / async / async_s3d2 6 / 6; functional coverage 100 % in every group. Logs `build/logs/v1.9/` |
| Memory QoS (B-7) | `MEM_ATTR` / `MEM_QOS`, `g2d_axi_qos`: test_axi_mst `axi07_throttle` / `axi08_attributes` in 4 configs, test_top `mem_qos_system` in 4 configs; mutants (gate removed, waiting request withdrawn) caught by the monitor and the SVA |
| Configuration adapters (F-6) | AXI4, APB, AHB-Lite, OBI, reg_intf, Wishbone, TL-UL: test_adapt ADP-01..04 (6 buses), test_axi2axil ADP-05..07 (32 / 64 / 128 / 512 bit, full length); one mutant per adapter caught |
| Wrappers | `g2d_top_<bus>` (7) generated; test_wrap ADP-08: CSR reset values, IRQ, fills, random streams, QoS through every bus (reg_intf with both crossings) |
| IRQ / DFT options | `IRQ_PULSE` + `IRQ_ACTIVE_LOW` build: csr01, csr09_irq_pulse, dft01_test_mode_reset |
| Memory variants (B-2) | `rtl/ram/xilinx`, `rtl/ram/asic` (+ macro stand-ins): test_top subset + sys01 bit-exact with each |
| SDC (D-2) | `syn/constraints/g2d_top.sdc`; `scripts/check_sdc.py` vocabulary check only (no STA tool here, Q-INT-2) |
| Packaging | `g2d.core` (FuseSoC 2.4.7: `lint` target passes), `Bender.yml` (flist per target checked), `ipxact/g2d_top.xml` (structure and register map checked against regs.yaml; schema validation Q-INT-3) |
| Cheshire on `g2d_top_axi` | SoC sim with the DRAM stage: clears, blits, paths, patterns bit-exact, write signatures = v1.8 reference (FPGA.md FPG-17) |
| Not done here | bitstream with the adapter, board run, STA, IP-XACT schema validation |

## Test results (v1.8 board-proven, 2026-11-12, container: SoC simulation only, board runs pending)

| Item | Result |
|---|---|
| `make lint` | clean (46 units, 17 parameter variants) |
| `make regress` (2026-11-12, 4 jobs) | test suite 105 / 105; model suite 2125 / 2125 (seeds 1–5); benches `--full` 305 / 305; LVGL + live RTL + SYS-06 dw64 / dw128 / async / async_s3d2 6 / 6; functional coverage 100 % in all 44 groups. Log `build/logs/audit/regress_v18.log` |
| axi_vga fork (item 1) | `dv/tests/test_axi_vga.py` 12 / 12 (real bursts, block-RAM buffer, counters); SoC sim VGA capture bit-exact, stream offset 0 pixels (v1.2: 4) |
| Debug registers (item 4b) | DBG_STATUS.RAM_COLL / WR_SIG checked after every top-level and SYS test; coll=00 in every SoC scene |
| LLC cached-region end (item 2) | DOOM builds cache only [0x8000_0000, 0x8100_0000); board test with G2D_LLC_CACHE=1: 11 / 11 kinds bit-exact, write signatures equal to the plain build |
| DRAM timing stage (item 5) | `--ddr 390,13,32` (refresh + jitter): 11 / 11 kinds bit-exact, coll=00 (`build/fpga/socsim_v18`, `socsim_v18_llc`) |
| Board reference | `fpga/ref/wsig_k1_seed1.txt` (per-scene write signatures for `fpga_check.py --wsig-ref`) |
| Finding | vectors_0 needs 1.79 s SoC time: driver stroke geometry on CVA6, not a hang (FEATURES_LATER D-5) |
| Not done here | bitstreams, board runs (doom_nollc SD image, new DOOM bitstream, `--wsig-ref` board test), netlist simulation (Q-FPGA-10), Q-FPGA-9 bisection |

## Test results (DOOM side project, 2026-10-27 .. 2026-11-06)

| Item | Result |
|---|---|
| Host (exit criterion 1) | `make doom-host`; `sw/tests/test_doom.py`: G2D present (INDEX_8 + CLUT, 2 × 2.4 point-sampled blit, flip) bit-exact vs a CPU present |
| chs_ps2 (criterion 2) | `dv/tests/test_ps2.py` self-checking (scancodes, parity errors, glitches, FIFO overflow); set-2 decoder `doom_keys.c` + test |
| SoC sim (criterion 3, 2026-10-28) | DOOM boots, PS/2 keys reach the game, frame presented by the G2D RTL = CPU present (crc 279c8f89), VGA pins clean (FPG-12) |
| Bitstreams (criterion 4) | Timing met at 50 MHz. Board build = Cheshire with USB + iDMA + bus-error units removed (trim mask 7; the full trim hangs, Q-FPGA-9) + G2D FEATURES = 0 + chs_ps2 + the axi_vga fork: WNS +0.062 / WHS +0.050 ns, 166 794 LUT (81.8 %), 49 334 slices (96.8 %), 133 DSP (FPG-15). Archived: `build/archive/doom_tm7_prefetch_f0_2026-11-06.tar.gz` |
| Board (user, 2026-11-04 .. 11-06) | DOOM playable on the Genesys2: boots from the SD card (FPG-13), USB keyboard through chs_ps2, 10–13 fps, picture on a VGA monitor. VGA: the picture slid (axi_vga underruns, never re-aligned) → buffer 256 beats, then per-frame re-sync (slide gone), then prefetch (fewer glitches); residual glitches Q-FPGA-11 |
| VGA bench | `dv/tests/test_axi_vga.py` 8 / 8 (fork vs original, bursts 16 / 256, long stall + periodic latency): fork bit-exact after every disturbance from the next frame; original 0 / 12 frames under periodic latency |
| `make test` at the merge (2026-11-06) | 101 / 101 pass, 10.1 min on 8 jobs (build/regress/test/summary.md) |

## Test results (v1.7 performance and timing, 2026-10-27)

- `make lint` clean (46 units, 17 parameter variants: new `FEATURES=0`, `RST_STAGES=0`); `make test` 88 / 88 after every
  feature commit; full regression: model suite 2125 / 2125 (seeds 1–5), benches `--full` 305 / 305 (seeds 1–5), LVGL +
  live RTL + SYS-06 dw64 / dw128 / async / async_s3d2 6 / 6; functional coverage 100 % in every group. Logs
  `build/logs/v1.7/`.
- New tests: L3 `test_feat` (every optional unit drawn when built, BAD_STATE when not, HWCFG; builds `full` / `none`),
  driver checks of a build without the optional units (`test_driver_opts`), prf02 lower bounds at 90-cycle latency
  (rotated ≥ 0.85, perspective ≥ 0.80), `scripts/bench_model.py` (model speed with CRCs against a reference run).
- Bugs found during the work: the async benches hung with a reset stage in the cfg domain (Verilator spin at 1 ns, port
  domains left unstaged, Q-TEST-5); band-setup overlap first read both state RAM parities (read / write collision
  assertion in the path regression; per-parity read enable).
- Mutation spot check (33 mutants in g2d_raster / g2d_dst_rd / g2d_spill_reg / g2d_draw_check / g2d_rst_sync /
  g2d_rr_arb): 28 killed, 5 survived, all equivalent or benign (an edge starting exactly at a band end, a clear-time
  value reset by the next draw, an equal bound, two cast widths). Report `build/mutate_v17/report.md`.
- G2D OOC (final RTL): 100 MHz WNS +0.367 ns, 54 237 LUT, 32 880 FF, 61.5 BRAM tiles, 138 DSP; 125 MHz −0.778 ns
  (worst: band-setup multiply from the edge-buffer RAM). `FEATURES = 0`: 44 801 LUT, 104 DSP.
- Trimmed Cheshire + G2D implementation (b8c7494 RTL, fit check): 168 794 LUT (82.8 %), 49 460 slices (97.1 %),
  113 754 FF, 205.5 BRAM tiles, 167 DSP, WNS +0.092 ns at 50 MHz; G2D 59 425 LUT in context.
- Final bitstreams (trimmed Cheshire, VGA kept, every G2D unit; `make fpga FPGA_TRIM=1 [FPGA_ASYNC=1]`): single clock
  `g2d_chs_trim.genesys2.bit` 168 170 LUT (82.5 %), 50 418 slices (99.0 %), 206.5 BRAM tiles, 167 DSP, WNS +0.177 ns;
  async (G2D core 100 MHz) `g2d_chs_async_trim.genesys2.bit` 169 619 LUT (83.2 %), 49 833 slices (97.8 %), 199 DSP,
  WNS +0.062 ns; G2D 58 968 / 60 296 LUT in context. Slice packing is the margin to watch.
- SoC simulation, trimmed Cheshire (`build/fpga/socsim_kinds17`): all 11 scene kinds (clears, blits, paths, patterns,
  converted, strokes, vectors, radial, imgpaths, v16, v16b) bit-exact at DRAM delay 0 and 40, IRQ ok.

## Test results (v1.6b texel path extensions, 2026-10-26)

- After the user's decisions on Q-API-27..30 (`2a6099c`: signed Gaussian weights with saturation, YUV_FULL reserved,
  closed-format tests, OPENVG build parameter and per-format tests): `make test` green, coverage 100 % (cg_gauss
  25 / 25); benches 183 / 183 (seeds 1–3), SYS-06 dw64 + live + LVGL 3 / 3; OOC 50 MHz 53 793 LUT, 31 055 FF, 60.5
  BRAM tiles, 170 DSP (+32: the signed 21-bit weight products), WNS +2.016 ns; SoC sim v16b / blits bit-exact.
  The numbers below are from before that change.

- `make lint` clean (45 units, 15 parameter variants); `make test` green after every feature commit; full regression
  (18 jobs): model suite 1880 / 1880 (seeds 1–5), benches 305 / 305, LVGL + live RTL + SYS-06 dw64 / dw128 / async / async_s3d2 6 / 6;
  functional coverage 100 % in every group (new cg_gauss 15 / 15, cg_color 6 / 6, cg_src14 96 / 96 with the 24-bit
  and YUV formats). Logs `build/logs/v1.6b/`.
- New tests: L3 `f2401` (24-bit, every line offset), `gau01`, `yuv01`, `col01`; L2 `texel_gauss`; model hand tests
  (24-bit expansion, Gaussian pixel, YUV conversions, colour unit); API `test_gaussian_vs_float`, `test_yuv_vs_float`,
  `test_color_unit_api`, `test_openvg_formats`, `test_openvg_linear_to_srgb`, scene kind `v16b`; deliberate
  breakages of the 24-bit join, a Gaussian weight and the U / V order are each caught.
- Bugs found by the new tests: YUV U V pairs / YUY2 words crossing a 64-byte line at odd plane addresses (fixed by an
  alignment rule, rendering.md §3.7); sign of packed-array element selects in the colour sums; L2 benches left new
  inputs undriven (random under `--x-initial unique`); exhaustive 2^24 loops of the format tests; test state arrays
  still 64 words.
- Mutation spot check (30 mutants in g2d_tex_seq / split / merge / texel / tex_addr): 23 killed, 7 equivalent (four
  out-of-range loop bounds; 8-bit texels through the sub-byte path with shift 0; `off == 62` → `!=` in the straddle
  code, whose 0 / 2 cases share the join; reciprocal `R + 1` for power-of-two periods, exhaustively equal remainders).
  Report `build/mutate_v16b/report.md`.
- G2D OOC 50 MHz: 53 313 LUT (+2 673), 30 913 FF, 62.5 BRAM tiles, 138 DSP (+64), WNS +0.862 ns (worst path still
  the blend S0 stencil terms, T-1). SoC simulation (`build/fpga/socsim_kinds16b`): v16b, v16, blits, converted all
  bit-exact, IRQ ok, `fails=0`.

## Test results (v1.6 VGLite extras, 2026-10-25)

- `make lint` clean (44 units, 15 parameter variants). `make test` green after every feature commit. Full regression
  (18 jobs): model suite 1480 / 1480 (seeds 1–5); benches 305 / 305; LVGL (full) + live RTL + SYS-06 dw64 / dw128 /
  async / async_s3d2 6 / 6. Functional coverage: every group 100 % (new cg_mask 12 / 12, cg_family 22 / 22). Logs
  `build/logs/v1.6/`.
- New tests: `test_robust` (old driver: 12 / 60 checks fail, D-1 alone 732 pixels); L3 `msk01` / `msk02` / `bld04` /
  `flp01` / `stn01`, mask state and random BLEND_FUNC in the blit bench; L2 `blend_families`, `blend_stencil`; model
  hand tests (stencil, mirror, families); API `test_scissor_rects_union`, `test_mask_layers`,
  `test_blend_families_api`, `test_mirror`, `test_image_modes_api`, A8L8 in `test_source_formats_vs_decoded`,
  `test_v16_scene_stream`; scene kind `v16` (API, live RTL, SYS-06, SoC sim); blend families checked against an
  independent Python reference written from rendering.md §5.7.
- Bugs found before commit: Verilator BLKANDNBLK on packed divider stage arrays (unpacked now); stale expectations
  (paint source 3, OpenVG blends, scissor_rects n > 1 used to be NOT_SUPPORT); hard-coded format lists missing A8L8.
- Mutation spot check (30 mutants in g2d_blend, g2d_mask_rd, g2d_walk, g2d_fill, g2d_draw_check): 27 killed,
  3 equivalent (`max` `>` → `>=`; an out-of-range extra divider generate stage; an out-of-range 4th channel index).
  Report `build/mutate_v16/report.md`.
- G2D OOC 50 MHz (xc7k325t): 50 640 LUT (v1.5: 43 607, +7 033, mostly g2d_blend 9 613: families + divider), 29 623 FF
  (+1 079), 74 DSP (+10), 60 BRAM tiles (+1, mask reader FIFO), WNS +1.972 ns (v1.5 +5.441; worst path source format →
  blend S0 stencil terms, Q-RTL-10). Full SoC estimate now above the v1.5 ~91.5 % LUT: the trimmed build (v1.7) is
  needed for the board.
- SoC simulation (`build/fpga/socsim_kinds16`): v16, patterns, blits, converted all bit-exact, IRQ through the PLIC,
  `fails=0`. Busy cycles 6 378 / 103 148 / 17 018 / 34 276.

## Test results (v1.5 image paint on paths + radial gradients, 2026-10-24)

- `make test` all green (after `make live` / `make lvgl` rebuilds). Full regression (make regress content, 18 jobs):
  model suite 1355 / 1355 (seeds 1–5); benches 300 / 305, the 5 failures in the draw-check bench's random fault
  generator (a stride fault on a buffer an earlier fault had given an invalid format: `KeyError` in the test, exposed
  by the changed random sequence), fixed in `29537ab`, seeds 1–8 green; LVGL (full) + live RTL + SYS-06 dw64 / dw128 /
  async / async_s3d2 6 / 6. Functional coverage: every group 100 % (new cg_radial 8 / 8, cg_paint_path 64 / 64).
  Logs `build/logs/v1.5/`.
- Bugs found by the new tests before commit: rotated images on paths walked 16 × 16 tiles while the raster emits
  scanlines (first `img01` run; walker tile flag now gated by `path`); the linear gradient index lost its sign when the
  spread logic moved into a function (a part-select is unsigned; L2 `grad_index`).
- Mutation spot check (20 mutants in g2d_grad, g2d_isqrt, g2d_pix, g2d_draw_check): 18 killed, 2 equivalent (`sat32`
  `<` → `<=` at exactly −2^31; an out-of-range extra isqrt generate stage). Report `build/mutate_v15/report.md`.
- Traversal (plan item 2, prf02): image paths are not slower than tiled blits (ideal 1.01–1.06×, 90-clock latency
  1.28–1.38× of the blit's px/clk), so no traversal change was built; blended tiled blits under latency are
  destination-read-bound (0.55–0.69 px/clk), recorded in Q-PERF-2 for v1.7 (arch.md §6.1).
- LVGL: the vector demo's two gaps are drawn (VECTOR_GAPS 0); the radial gradient meets the default ThorVG criterion;
  the image fill on a curved path differs from ThorVG by ThorVG's sample offset (Q-TEST-4, D10, own criterion with an
  offset-compensated check). D9 (formats demo, scaled A4): max 69 (v1.4: 75), outside the band 26, mean 5.98: the
  change comes from the exact AA border of the fractional rectangle (now a path draw); the premultiplied border
  (Q-API-19 (b)) does not change D9 because LVGL passes `pattern_color = 0`.
- G2D OOC 50 MHz (xc7k325t): 43 607 LUT (v1.4: 41 301, +2 306), 28 544 FF (+2 272), 64 DSP (+16: radial products and
  squarers), 59 BRAM tiles (unchanged), WNS +5.441 ns (v1.4 +5.910). Full SoC estimate ~91.5 % LUT untrimmed (no
  bitstream before v1.7, user).
- SoC simulation (Verilator Cheshire + G2D, 1 scene per kind, seed 1, `build/fpga/socsim_kinds15`): paths, patterns,
  radial, imgpaths all bit-exact (every rc and buffer CRC = software backend), IRQ through the PLIC, `fails=0`. Busy
  cycles: paths 1 415, patterns 103 148, radial 27 601, imgpaths 29 413.

## Test results (v1.4 small hardware features, 2026-10-24)

- `make test` 84 / 84 (after `make live` / `make lvgl` rebuilds of the stale v1.3 binaries). `make regress`
  (`4a30990`): 1289 / 1290 runs, the one failure a test expectation (`test_pattern_semantics[2]` seed 2: a perspective
  pattern whose inverse coefficient exceeds 32 bits is INVALID_ARGUMENT by rendering.md §9.3; expectation fixed in
  `46266e2`, seeds 1–8 green); then LVGL tests 5 / 5 and SYS-06 4 / 4 on the rebuilt demos. Log
  `build/logs/v1.4/regress.log`.
- Functional coverage (regress): every group 100 % (new: cg_src14 64, cg_tgt14 48, cg_wrap 20, cg_img14 8 bins after
  the TRANSPARENT x indexed stimulus fix).
- Model: 10⁵ random scenes model vs independent reference over seeds 1–9; the RTL's wrap modulo (reciprocal + one
  correction) exhaustive over every period 1..16384 and every reachable offset; dither, every format, every Bayer
  position exhaustive in the RTL package bench.
- G2D OOC 50 MHz (xc7k325t): 41 301 LUT (v1.3: 36 381, +4 920), 48 DSP (+7), 59 BRAM tiles (unchanged), WNS +5.910 ns
  (v1.3 +5.243). Full SoC estimate: 87.7 % → ~90.1 % LUT untrimmed, ~78 % trimmed (no bitstream before v1.7, user).
- SoC simulation (Verilator Cheshire + G2D, 1 scene per kind, seed 1): blits / patterns / converted all bit-exact
  (every rc, every buffer CRC-32 = software backend; `fpga_check.py` exit 0), LVGL widgets frame CRC 73fa47bc, IRQ through
  the PLIC, `G2D_TEST_END fails=0`. Busy cycles: blits 16 996 (4 draws, v1.4 source formats), patterns 99 289 (17,
  hardware wrap), converted 43 716 (6: sub-byte / indexed / packed / 24-bit sources, dither). Reports
  `build/fpga/socsim_kinds14/<kind>/check.md`.

## Test results (v1.3 driver-only API completion, 2026-10-19)

- `make regress` (after `ad1a82e`): all green, 1295 / 1295 runs (log `build/logs/v1.3/regress.log`): make test 84 / 84
  (10.0 min), model suite seeds 1–5 900 / 900, RTL benches seeds 1–5 full 305 / 305, then live RTL (incl. `vectors`
  scenes), LVGL screenshots (all demos), SYS-06 replay on dw64 / dw128 / async / async_s3d2 (incl. `scene_vectors`:
  arcs, BREAK, perspective fills and strokes, dashes) 6 / 6 (128.9 min).
- Extra full-mode runs: `test_paths_ext.py` seeds 1–4 (4 / 4); `test_stroke.py` seeds 5–8 Skia tests 4 / 4 after the
  area-criterion fix (`1577d52`); `make baremetal-check` OK (RV64, -Werror -pedantic); ctest 3 / 3.
- Measurements: dashed strokes vs the independent reference, per-shape edge mean over 585 shapes median 7.0, max 9.7
  (§12 bound 12 holds); Skia area: 1080 strokes + 360 dashed, ours vs exact ≤ 2.7 % (solid), Skia vs exact up to 7.6 %
  (6.2 px) on small shapes; LVGL formats demo scaled A4 vs LVGL's software renderer max 28 / mean 5.7.
- Equivalence: solid strokes and fills (affine) bit-identical to `main` on 3000 random paths (refactor check).

## Test results (v1.2 board readiness, 2026-10-19, SoC simulation only; no bitstreams until v1.7, user)

- `make lint` clean, `make test` 83 / 83 (4.2 min).
- FPG-06 DRAM-latency sweep (SoC sim with Cheshire's own `USE_RAM_DELAY` stage behind the LLC, `scripts/socsim_sweep.py`,
  extra AR / AW delay N): 24 / 24 runs bit-exact. Busy cycles at N = 0 / 10 / 20 / 40: clears 10 681 / 10 711 / 10 741 /
  10 801, blits 16 715 / 16 785 / 16 853 / 17 006, paths 2 549 / 2 621 / 2 692 / 2 881, patterns 87 964 / 87 984 /
  88 004 / 88 044, converted 45 797 / 45 956 / 46 124 / 46 487, strokes 37 405 / 37 548 / 37 705 / 38 004 (+2 .. +17
  busy cycles per cycle of latency; +0.1 .. +1.7 % at N = 40, paths +13 %: short scene, latency-bound command fetches).
- Latency probe (NOP-only kick): 22 + N busy cycles. Finding: Cheshire's boot ROM puts every LLC way into scratchpad
  mode, so DRAM accesses bypass the LLC; there is no "LLC hit" case in our bare-metal setup.
- In-software DRAM-delay sweep (the board procedure: the test program writes Cheshire's cfg registers): steps 0 and 20
  bit-exact, probe 22 / 42.
- LVGL frame on the target (Phase 7 exit criterion 3): LVGL widgets, RGB565 640x480, 2 frames recorded on the host
  (`make lvgl-chs`), replayed by the bare-metal test: 31 kicks, 2 549 646 busy cycles, ERR_STATUS 0, CRC 73fa47bc over
  the 614 400 GPU-written bytes = the software backend's; on the full and on the trimmed Cheshire build.
- VGA output test (Q-FPGA-5): Cheshire's `axi_vga` scans the LVGL frame out (640x480, 60 Hz from SoC / 2); the
  testbench captures a frame from the pins: sync / blanking 0 errors, every pixel bit-exact with a stream offset of
  4 pixels (axi_vga starts visible before its fetcher has data and never re-aligns; docs/FPGA.md §5).
- Trimmed Cheshire (`FPGA_TRIM=1`: USB, iDMA, bus-error units, I2C off): SoC sim 6 / 6 kinds bit-exact with the same
  busy cycles, IRQ through PLIC source 58, VGA test as above. Bitstream not built (after v1.7).

## Test results (v1.1 CDC, 2026-10-18)

- `make lint`: clean (40 units, 15 parameter variants, async tops and FIFO variants included). `make test`: 83 / 83 (3.9 min).
- CDC-01..03 (`dv/tests/test_cdc.py`, 5 configurations incl. two synchronizer-jitter builds): pass; throughput at depth 8
  0.95–0.985 words per cycle of the slower clock (0.90+ with jitter), depth 2 (config port) 0.39–0.66.
- CDC-04: test_top / test_sys in the `async` and `async_s3d2` builds bit-exact; FIFOs empty at every soft_clr; SYS-05
  memory digests identical over all four builds.
- CDC-05 formal (`make formal`, SymbiYosys): depth 2 (41 s), depth 8 (8 min), 3 stages (3.3 min) proven unbounded
  (PDR); covers reached.
- Regression: benches full-length seeds 1–5: 305 runs, 1 failure (test assumption in the async soft-reset check,
  fixed `c195d6b`, rerun green), functional coverage 100 %; model / driver suites 800 / 800; LVGL screenshots and the
  live RTL backend full: pass; SYS-06 full per build 4 / 4.
- FPG-05: G2D OOC 100 MHz WNS +0.334 ns; Cheshire + G2D async bitstream timing met (WNS +0.012 ns), CDC constraints and
  bus skew met; SoC simulation with a 100 MHz core clock: 6 / 6 kinds bit-exact + IRQ (docs/FPGA.md §5).

## Test results (Phase 7, 2026-10-18, before the board run)

- `make lint`: clean (36 units + 11 variants). `make test`: 73 / 73 (4.8 min).
- `make regress` (seeds 1-5, 32 jobs) on the Phase 7 code (B-7.1 / B-7.2 fixed): model and driver suites 800 / 800,
  every bench full length 275 / 275, Phase 6 tier (LVGL screenshots, live RTL, SYS-06 full) 3 / 3 (75 min);
  test_live + test_stroke full mode 44 / 44 on the rebuilt live backend.
- FPG-01 / 02 / 03: docs/FPGA.md §5 (G2D OOC WNS +5.24 ns; SoC bitstream timing met, LUT 87.7 %; SoC simulation
  6 / 6 scene kinds bit-exact + PLIC interrupt). FPG-04 (board): pending, run by the user.

## Test results (Phase 6 end, 2026-10-17)

- `make lint`: clean, 36 units + 11 parameter variants.
- `make test` (= `make smoke`): 73 runs, all pass, 4.2–5.1 min on 16 jobs (5.1 with a full RTL rebuild).
- Phase 5 gates on the fixed RTL (`cf8baa7`, tag `phase-5-patch1`): SYS-01..05 full length, seeds 1–300 × dw64 /
  dw128 + SYS-05 compare: 900 / 900 pass (33.5 min); every bench full length, new seeds 111–120: 550 / 550 (18.8 min);
  model and driver suites, seeds 111–120: 1600 / 1600; merged functional coverage 100 % in all 12 groups; code
  coverage line 100 % (1198 / 1198; raw 91.5 %), toggle 93.5 %.
- Phase 6 full length (`make regress`): LVGL screenshots (widgets, benchmark 600 frames incl. the Q-TEST-2 rotated
  frame, vector demo vs ThorVG, formats, three-way with vg_lite_tvg), live RTL (driver unit test, all scene kinds × 3,
  LVGL frames, dw64 and dw128), SYS-06 full (API scenes, LVGL widgets 32 / 16 bit, vector, 150 benchmark frames):
  3 / 3 pass (85 min, SYS-06 84 min).
- First gate run found B-6.5 (hang, from the first B-6.4 fix) and three test problems on new seeds (converted-format
  scenes, AA mean bound, Skia stroke bound); all fixed before the run above.
- Verification matrix: docs/VERIFICATION_MATRIX.md (11 models × 15 features, no empty cell, 8 documented
  differences D1–D8, every disagreement with its resolution).
- Performance (spec/bandwidth.md §8): full 720p widgets redraw 1.94–2.02 Mclk at DATA_W 64 (3.2–3.4 ms at 600 MHz,
  39–40 ms at 50 MHz); benchmark worst frame ≤ 1.81 Mclk at 64 bit, ≤ 1.41 Mclk at 128 bit with 8 / 8 outstanding.
- Bugs: B-6.1..B-6.5 (tables above), each with a test that fails on the old code.

## Test results (Phase 5 end)

- `make lint`: clean, 36 units + 11 parameter variants.
- `make test`: 66 runs (every unit, model, block, top-level and SYS node once, seed 1, short mode), all
  pass, 3.4 min on 16 jobs (incremental; 3.6 min from a clean build). Functional coverage 100 % in all 12
  groups.
- Full regression (16 jobs, 2026-10-16): SYS-01..05 300 seeds x DATA_W 64 / 128, full-length: 900 runs
  pass (32.9 min); every bench full-length on new seeds 101-110: 550 runs, 549 pass, 1 test-bench
  failure (SRAM bench drove enables before the first edge from random initial values; fixed, the RAM
  benches pass on seeds 101-130); model suites seeds 101-110: 750 pass. Merged functional coverage of the
  regression: 100 % in all 12 groups.
- Code coverage (`make code-coverage`): line 100 % of 1198 counted lines (raw 91.5 %; 24 uncovered lines
  kept out and listed: simulation-only and generated code, 18 return-only case items Verilator cannot
  count, 6 FSM default arms), toggle 93.2 %.
- Mutation campaign: 130 mutants, 108 killed at first, 7 test gaps closed, 115 killed after the rerun,
  14 equivalent, 1 performance-only (triage table above).
- Bugs found: 5 RTL bugs (B-5.1..B-5.5), each with a test that fails on the old RTL.

## Test results (Phase 4 end)

- `make lint`: clean, 36 units + 11 parameter variants (new: raster DATA_W 128 / MAX_EDGES 64, MAX_EDGES 64 /
  CHUNK 5, edge loader DATA_W 128, divider 27/21/26, ramp DATA_W 128).
- `make test`: 189 passed in 16.5 min (161 from Phases 0–3, 28 new). Over the 5-minute smoke target of
  verification-plan.md §6 (the new path and rasterizer benches): to be trimmed in Phase 5 (verification).
- `make regress`, seeds 1 and 2 (user decision 2026-10-16: two seeds, deeper regression in Phase 5): all
  green. Seed 1: 75 model + 52 RTL tests (20 min); seed 2, run in parallel from a git worktree of the same
  commit: 75 + 52 (28.5 min). Merged functional coverage (make test + both seeds, 12 files): 100 % in all
  eight groups (cg_blend 192, cg_blend_alpha 144, cg_blit 168, cg_blit_dst 144, cg_blit_opts 32,
  cg_path 80, cg_paint_path 32, cg_grad 36).
- New benches: test_sdiv (3 configurations, one exhaustive), test_edge_load, test_raster (directed,
  random, streaming with a 64-entry buffer, forced pass splits CHUNK 64/13/5 with identical output
  digests), test_grad, model/tests/test_raster_model.py, test_path (PTH-01..06, GRD-01, AXI-08, coverage
  matrix, PRF-01) at DATA_W 64 and 128.
- PRF-01 (ideal / 90-clock latency, px/clk): glyph-sized circle 0.833 / 0.773 (K-1), large circle
  0.948 / 0.926, 16-px-wide shape 0.649 / 0.614, 41-point star 0.960 / 0.945, gradient rect 0.994 / 0.986.
  Blits after the Q-PERF-2 tuning at 90-clock latency: rotated bilinear 0.91, perspective 0.87.
- Mutation checks: divider 3/3; edge loader 3 of 4 (the fourth equivalent: X in 1/32 px is even, the
  right-drop limit odd); model rasterizer: remainder off by one equivalent (N and dy even), off by two /
  four caught after adding small-dy/tie stimulus; rasterizer RTL 6 of 8 (two equivalent: column clamp at
  p = segment start, active test without the band end); streaming 1/1; multi-pass rows 1/1; path
  integration 2/2; gradient and ramp 5/5 (two of them only after the bench was strengthened: REFLECT
  boundary, ramp kept ready during its draw).
- Bugs found and fixed during the phase (before commit): the last edges of a pass were stepped in the
  next row (row/segment taken live instead of latched per pass); an empty kept-edge list scanned one
  stale buffer entry; a reload in the middle of a row restarted the row's segments (extra coverage);
  the ramp was invalidated by its own draw's pixel writes (hang); accumulator sweeps could run out of
  fill order (found in review). DV: assertions sampled random register values before the asynchronous
  reset (fixed with a reset start-up that acts before the first clock edge); scene and timing randomness
  shared one generator (split points produced different scenes).

## Test results (Phase 3 end)

- `make lint`: clean, 30 units + 8 parameter variants (new: source cache at DATA_W 128 / 2 KB / 8-deep FIFO
  and at 32 KB, draw check at `RESTRICT` 0 and 2).
- `make test`: 161 passed in ~6 min (134 from Phases 0–2 incl. updated benches, 27 new), functional
  coverage 100 % in all Phase 3 groups:
  - L2: test_common (SRAM wrappers, SRAM FIFO depths 1/2/5/16), test_blend (every (s, d) pair per mode;
    all 65 536 with G2D_FULL=1: 2.1 M vectors), test_pkg (`fmt_expand` exhaustive), test_xform
    (boundaries of both range checks, W ≤ 0, W ≥ 2^32, random affine/perspective), test_sampler (texel
    addressing, texel unit vs the model), test_src_cache (scan/tile/thrash walks with random latency,
    invalidation with rewritten memory, soft-reset drain, 1 px/clk; default and 2 KB/8-deep/DATA_W 128)
  - L3 (DATA_W 64, and 128 with ADDR_W 48, MEM_BASE_HI = 5, 4 outstanding reads / 2 writes):
    test_blit (BLD-01..03, BLT-01..06, CMD-05, random streams, soft reset mid-blit, throughput),
    test_top (Phase 2 tests; random streams now include blended fills; fil04 uses the Phase 3
    restrictions)
- `make regress REGRESS_SEEDS="1 2 3 4 5"`: all green (seeds 1–3 in one run, seeds 4–5 after the
  simulation-only assertion fix below; ~29 + 21 min). Merged coverage: cg_blend 192/192,
  cg_blend_alpha 144/144, cg_blit 168/168, cg_blit_dst 144/144, cg_blit_opts 32/32.
- Throughput (informal, A8; arch.md §6.1): ideal memory 0.97–0.99 px/clk for blended rects, copies,
  rotated and perspective blits; at 90-clock latency 0.98 / 0.98 / 0.77 / 0.74 (Q-PERF-2).
- Mutation checks: 10 L3 integration mutants (dispatch without the write drain, twice; skipped pixels
  written; skipped-pixel strobes set; bilinear clamp off by one; premultiply with the pre-key alpha;
  destination unpack offset; span not cut at the 256-byte block; SOLID using the texel colour; W = 0 as
  positive): 9 caught, 1 equivalent (W = 0 already fails `2U < W`). Block mutants caught: transform range
  checks (2), colour-key priority, sign extension of `u - 2^15` (missed first, stimulus extended with
  origin-based source rects), cache eviction of locked lines, no fill wait, no invalidation (missed first,
  stimulus extended to redraw rewritten images).
- Bugs found and fixed during the phase (before commit): coverage weight `16'(~cov)` widened before
  inverting; unsigned part-select lost the sign of `u - 2^15`; cache miss counter counted pixels instead
  of lines; cache could not drain under soft reset (full miss queues); set index thrashing with
  power-of-two strides (XOR-folded index); cache SRAM read during late fills under soft reset.
  DV: the AXI memory model ignored profile changes (Phase 2 random rounds ran with their first
  profile); targets 16 MB apart wrapped past 4 GB in large tests; two simulation-only assertions fired at
  time 0 on random initial values (SEED=3, SEED=4).

## Test results (Phase 2 end)

- `make lint`: clean, 18 units + 5 parameter variants (DATA_W 128, ADDR_W 48/64, MAX_BURST_BYTES 64/4096,
  KICK_DEPTH 16, RESTRICT_P2 = 0).
- `make test`: 134 passed in ~3.5 min (113 from Phases 0–1, 21 new pytest entries running 40+ cocotb tests):
  - test_common (FIFO depths 1–7, register slice, arbiter N = 1/3/5) vs Python models
  - test_csr_top (CSR-01..07 at block level, KICK_DEPTH 4 and 16)
  - test_axi_mst (AXI-01..07; 4 configurations: DATA_W 64/128, bursts 64/256/4096 B, 1–3 clients, ADDR_W 48)
  - test_draw_check (CMD-06: 3000 random states vs the model's validation, RESTRICT_P2 = 0 and 1)
  - test_pkg (FIL-05: compression of every format × channel value, global alpha and mul8 exhaustive)
  - test_top (FIL-01..04, CMD-01..04, CSR-01/05/06, AXI errors at system level, random streams,
    throughput) at DATA_W 64, and at DATA_W 128 with ADDR_W 48 and MEM_BASE_HI = 5; async-CDC
    elaboration error check
- `make regress REGRESS_SEEDS="1 2 3 4 5"`: all green, 24 min (per seed: 75 model tests and every RTL bench
  in full mode).
- Fill throughput (ideal memory): 0.998 beats/clk at DATA_W 64 (2.0 px/clk at 32 bpp), 0.992 at 128
  (3.97 px/clk); the arch.md §6.1 targets are 2 and 4 px/clk.
- Mutation checks: 9 RTL mutants (AXI write tail strobe, draw-check stride rule, scissor X1 ignored,
  LOAD_STATE index not advancing, fence not waiting for B, 4-byte kick size check, SCALED alpha as GLOBAL,
  565 rounding) all caught. The 565 rounding mutant was first missed by the random fills (it changes a
  single colour value); test_pkg was added for it.
- Bugs found and fixed during the phase (before commit): a halt from a later command aborted an earlier
  issued draw; an early AXI read error on prefetched command words stopped the parser before valid
  commands; a write request wrapping past 2^32 passed the per-burst window check.

## Test results (Phase 1 end)

- `make model`: ctest 2/2 passed. `test_ops`: 2 426 824 checks; `test_geom`: 613 checks; 0 failures.
- `make test`: 113 pytest tests pass in ~32 s (14 from Phase 0, 99 new). Highlights:
  - stage functions vs the independent reference, bit-exact (exhaustive 8/16-bit formats, 2^19 random +
    structured pairs per blend mode, 40 000 transforms incl. perspective, literal-vs-prefix coverage)
  - 400 random scenes per run (all paint sources, formats, strides, scissors, blends, alpha, keys,
    affine/perspective, point/bilinear, paths with both rules and AA on/off), compared over every
    touched memory page; the generator is guarded so ≥ 60 % of scenes really draw
  - 30 directed decode/validation/error tests
  - float-reference tolerance tests (rendering.md §12) for paths, blits and gradients
- Mutation check: 3 non-equivalent model bugs each caught; one equivalent mutant identified as such.
- `make regress REGRESS_SEEDS="1 … 10"`: all green (10 × 75 seeded tests, i.e. 4 000 random rect/path scenes
  plus the randomised stage suites, ~5 min). Seed 2 initially tripped the stimulus-quality guard
  (not a model mismatch); the scissor generator and the guard floors were adjusted from a 10-seed
  measurement.
- `make lint`: clean.

## Test results (Phase 0 end)

`make lint`: clean. `make test`: 14 passed (13 pytest + 1 cocotb runner with 6 cocotb tests).
A mutation check (W1C set/clear priority flipped in the generated RTL) was caught by
`w1c_set_clear_priority`.

## Decision log

| Date | Decision | By |
|---|---|---|
| 2026-11-17 | Repository documentation made release-grade (README, LICENSE, THIRD_PARTY, CONTRIBUTING, SECURITY, CHANGELOG, docs/README.md, GitHub templates); SPDX expression for hardware corrected to `Apache-2.0 WITH SHL-2.1` (SHL-2.1 is an SPDX exception, not a licence; licence unchanged), `scripts/spdx.py --check` enforces it; `LICENSES/SHL-0.51.txt` added; no new version | user |
| 2026-11-16 | CI actions on Node.js 24 (checkout v5, cache v5, setup-python v6); CI history: only run 1 failed (runner DNS, transient) | user |
| 2026-11-16 | v2.1 started (user: "work on the sv2v rasterizer problem"): sv2v mis-conversion of `na_q[~cur_q]` fixed in `g2d_raster` (`nxt_par`), sv2v equivalence tests added to `make test`; sv2v coding rule in docs/ASIC.md | user |
| 2026-11-16 | Accellera network access granted; IP-XACT file validated against the 1685-2014 schema (EDA Alliance mirror, `scripts/fetch_ipxact_schema.sh`), committed after the `v2.0` tag | user |
| 2026-11-16 | v2.0 finalized: the p20e layout accepted as the reference GDS with its defects documented (docs/ASIC.md); a return to the flow later is possible; merge into `main`, tag `v2.0` | user |
| 2026-11-13 | ASIC clock constraint 50 MHz; goal is a finished GDS, not the best one | user |
| 2026-11-12 | Licences of the Linux parts: kernel driver GPL-2.0-only OR MIT, binding GPL-2.0-only OR BSD-2-Clause, generated register header Apache-2.0 OR MIT (the option to sell the GPU stays with the copyright holder) | user |
| 2026-11-12 | v2.0 in three steps A / B / C; FreeRTOS; Linux build + host test here, board by the user; ASIC on IHP SG13G2 with the highest frequency reached, no 600 MHz estimate; Q-API interpretation questions closed as accepted interpretations | user |
| 2026-11-12 | v1.9 approved; merged into `main`, tag `v1.9-done` (tag pushed by the user); environment setup script gets the v1.9 tools; start v2.0 | user |
| 2026-11-12 | Licences: SHL-2.1 for hardware descriptions (SystemVerilog, constraints), Apache-2.0 for the rest of the own work, DOOM port stays GPL-2.0-or-later; SPDX line in every own source file (scripts/spdx.py, checked in the tests); texts in LICENSES/ (SHL-2.1 and MIT from the SPDX licence list, Apache-2.0 and GPL-2 from the system copies) | user |
| 2026-11-12 | Generated sources (*/generated/, wrappers, packaging) committed; `make regs-check` runs first in CI. Branch `v1.9-display` deleted; display-controller work listed in FEATURES_LATER (DC-1..8). Adapter design approved | user |
| 2026-11-12 | v1.9 adapters bridge every bus to AXI4-Lite (not to the internal register port): one configuration port on g2d_top, the CFG_ASYNC crossing stays on AXI4-Lite; wrappers generated (gitignored), packaging files generated and committed | Claude |
| 2026-11-12 | v1.9 memory variants selected by file list (same module names), not by a parameter; ASIC variant maps to compiler-style macro stand-ins with a tied-off MBIST path | Claude |
| 2026-11-12 | v1.9 register map 0.4.0 (MEM_ATTR, MEM_QOS); IRQ_PULSE / IRQ_ACTIVE_LOW parameters, test_mode_i port; Cheshire uses g2d_top_axi behind PULP axi_atop_filter | Claude |
| 2026-11-12 | v1.9 = integration package and memory QoS (ROADMAP_V2.md §1.0c); the display controller leaves the main line (separate optional project after v2.0), `axi_vga` fork kept for demos | user |
| 2026-11-12 | v1.8 approved; merged into `main`, tag `v1.8-done`. Public-release hygiene: no machine paths, tool licence state or disk usage in tracked files; CLAUDE.md and docs/HANDOFF.md stay tracked in this private repository but are left out of the public one (user, changed the same day); no new repository and no history rewrite until after v2 (author e-mail stays) | user |
| 2026-11-10 | Repository audit written (docs/AUDIT_2026-11.md); roadmap proposal ROADMAP_V2.md §1.0b (v1.8 board-proven, v1.9 display controller, v1.10 adapters, v2.0 drivers and closure) waits for approval | audit session, user to decide |
| 2026-11-06 | DOOM side project approved with the current bugs (Q-FPGA-9, Q-FPGA-10, Q-FPGA-11): docs, push, archive the best bitstream, merge `doom` into `main`, tag `doom-done` | user |
| 2026-11-06 | axi_vga forked into `fpga/cheshire/axi_vga_g2d` (SHL-0.51, changes marked "G2D"): per-frame re-sync at vsync and room-driven prefetch with the burst capped at BufferDepth / 4; Cheshire's copy is left out of the Vivado and SoC-sim file lists | Claude, board-driven |
| 2026-11-05 | At most 8 parallel jobs / cores (CLAUDE.md, Makefile `JOBS`, regress.py `MAX_JOBS`, socsim.py, socsim_sweep.py, perf_lvgl.py); was 32 | user |
| 2026-11-04 | Push the `doom` branch, do not merge before the board tests are approved | user |
| 2026-10-27 | v1.7 approved ("push and merge every update"), merged into `main`, tag `v1.7-done`; `main`, every phase / version branch and tag pushed to origin (v1.6, v1.6b, v1.7 for the first time) | user |
| 2026-10-27 | v1.7 assumptions taken (docs/PHASES.md v1.7 questions; for approval): Q-PERF-4 not built (no px/clk gain, bandwidth only for NONE / SRC paths; v2 proposal: skip D also for opaque SRC_OVER); next-draw edge prefetch not built (needs draw-level lookahead in the command processor; the ≥ 0.9 glyph target is not met, 0.867); radix-4 divider not built (LUT-bound FPGA); no unit left out of the board build (it fits); Q-PERF-2 solved by `RD_OUTSTANDING` 16 instead of a destination prefetch | Claude, for approval |
| 2026-10-27 | Disk: when short of space, delete older implementations and archive what is needed (tar.gz). Archived `build/archive/impl_v1x_2026-10-18.tar.gz`, `mutate_phase5.tar.gz` | user |
| 2026-10-26 | v1.6b approved, merged into `main`, tag `v1.6b-done`; go to v1.7 (plan and question assumptions in docs/PHASES.md) | user |
| 2026-10-19 | v1.2 approved, merged into `main`, tag `v1.2-done`; v1.3 on `v1.3-api`. v1.3 answers: Q-API-14 (b) built; odd dash count = OpenVG (last entry ignored); `VLC_OP_BREAK` = end subpath without closing (A3, UNVERIFIED) | user |
| 2026-10-19 | v1.3 semantics from the public OpenVG 1.1 spec (arcs §8.4 / Appendix A, dashing §8.7.3–8.7.4); one-zero-radius arc drawn as its chord (simplification); all-zero dash pattern = solid; dash phase applied as given although LVGL passes width / 2 (Q-API-17); near plane ε = max(|m20|, |m21|, |m22|) / 2²⁰; Skia area criterion 2 % + 4 px from measurement | Claude, evidence-driven |
| 2026-10-15 | API-level compatibility; AXI4-Lite config, AXI4 master DATA_W 64/128; ASIC + Genesys2/Cheshire; cocotb + Verilator; C++ golden model (project brief) | user |
| 2026-10-15 | FPGA bring-up: GPU in the Cheshire SoC clock domain (50 MHz), single clock. **Later phase: separate core, AXI-memory and config clocks with CDC inside the IP**; ports and CDC parameters are already defined (arch.md §2.1) | user |
| 2026-10-15 | ASIC clock target 600 MHz | user |
| 2026-10-15 | Reset: active-low, asynchronous assert, synchronous de-assert, one reset per clock domain | user |
| 2026-10-15 | Vendor the MIT `vg_lite.h` 4.0.135 unchanged; semantics from header comments + LVGL (MIT); the confidential API manual is excluded | user |
| 2026-10-15 | Verilator 5.052 + cocotb 2.1 (cocotb 2.x requires Verilator ≥ 5.036) | Claude, tool-driven |
| 2026-10-15 | Submission model: command buffers in DRAM + kick queue (addr, size) + FENCE sequence counter + IRQ | Claude, assumption stated at Phase 0 start |
| 2026-10-15 | Pipeline 1 px/clk (fill fast path one beat/clk); the 2 px/clk idea was dropped because bandwidth.md shows 1 px/clk is enough at 600 MHz | Claude, approved by user 2026-10-15 (Phase 0 sign-off) |
| 2026-10-15 | Blend equations take S and D as stored (no implicit premultiply); `SRC_PREMUL` state bit for `apply_premult` | Claude, from LVGL evidence |
| 2026-10-15 | 16x AA = 16-sample n-rooks pattern; UPPER/MEDIUM quality render with 16 samples in v1; LOW = 1 sample | Claude, approved by user 2026-10-15 (Phase 0 sign-off) |
| 2026-10-15 | Curves flattened in software; hardware sees line edges in s.4 (1/16 px), guard band ±16384 px | Project brief + Claude |
| 2026-10-15 | Draw-to-draw memory ordering guaranteed by hardware (drain before dependent reads) | Claude, approved by user 2026-10-15 (Phase 0 sign-off) |
| 2026-10-15 | Generated docs/regs.md is committed (marked generated, checked by `make regs-check`); generated RTL/C go to gitignored `generated/` dirs | Claude |
| 2026-10-15 | Phase 0 approved by user ("go to phase 1") | user |
| 2026-10-15 | AA sample pattern changed to the optimised n-rooks `q = [4,12,8,1,15,6,10,2,13,5,9,0,14,7,3,11]` (RMS error 9.3 → 7.5, max 43 → 28 vs the (5j+3) lattice). Still n-rooks, as approved | Claude, approved by user 2026-10-15 (Phase 1 sign-off) |
| 2026-10-15 | Curve flattening tolerance 1/32 px (1/8 px left a coverage bias of up to 32/255 on curves) | Claude, approved by user 2026-10-15 (Phase 1 sign-off) |
| 2026-10-15 | Path tolerance vs float reference: per pixel ≤ 56 (two edges × 28), mean ≤ 10, PSNR ≥ 38 dB (measured, rendering.md §12) | Claude, approved by user 2026-10-15 (Phase 1 sign-off) |
| 2026-10-15 | Draw validation happens before any memory access; a failing draw writes nothing (deterministic errors); edges outside the guard band are clamped, not an error | Claude |
| 2026-10-15 | Address window is 64-byte granular (`ADDR_WIN_LO[5:0] = 0`, `ADDR_WIN_HI[5:0] = 0x3F`) | Claude |
| 2026-10-15 | Gradient constant widened to 48 bits (`GRAD_R_HI`): 32 bits overflowed for short gradients far from the origin | Claude |
| 2026-10-15 | Path matrices must be affine in v1 (`NOT_SUPPORT` otherwise); perspective stays available for blits | Claude, stated at Phase 1 start |
| 2026-10-15 | Gradient matrix maps gradient space → target, independent of the path matrix (evidence: LVGL `vg_lite_tvg`) | Claude, from evidence |
| 2026-10-15 | Shared driver geometry is C99 in `sw/lib/geom` (usable by the bare-metal driver), linked by the model harness | Claude |
| 2026-10-15 | Phase 1 approved by user | user |
| 2026-10-15 | Phase 2 answers: Q1 a (unsupported draws → BAD_STATE in Phase 2), Q2 a (blend in Phase 3), Q3 a (own AXI4 memory model), Q4 branch `phase-2-rtl-core` | user |
| 2026-10-15 | Phase 2 assumptions A1–A6 (docs/PHASES.md) taken as stated, not objected to | Claude, stated at Phase 2 start |
| 2026-10-15 | Bursts split at `MAX_BURST_BYTES`-aligned boundaries; W may lead AW; drop bursts for soft reset and window violations | Claude, approved by user 2026-10-15 (Phase 2 sign-off) |
| 2026-10-15 | Halt semantics (arch.md §8): command side stops, issued draws complete; AXI_RD stops at the faulting command word; AXI_WR stops wherever the parser is | Claude, approved by user 2026-10-15 (Phase 2 sign-off) |
| 2026-10-15 | Soft reset keeps the perf counters and SCRATCH, drops kicks written during it, clears all w1c fields (`soft_clr_i` added to the generated CSR block) | Claude, approved by user 2026-10-15 (Phase 2 sign-off) |
| 2026-10-15 | No prefetch across kicked buffers in v1 (OPEN_QUESTIONS Q-RTL-2); command buffers written by GPU draws are a hazard | Claude, approved by user 2026-10-15 (Phase 2 sign-off) |
| 2026-10-15 | SRAM wrappers and CMD-05 move to Phase 3 | Claude, approved by user 2026-10-15 (Phase 2 sign-off) |
| 2026-10-15 | cocotb wrappers that only rename ports are generated (`dv/common/tbwrap.py` → build/), not committed | Claude |
| 2026-10-15 | Phase 2 approved by user ("continue to phase 3") | user |
| 2026-10-15 | Phase 3 answers: Q1 a (latency-hiding source cache, untransformed copies through the cache); assumptions A1–A12 accepted | user |
| 2026-10-15 | Exact perspective divide for all inputs: only quotients < 2^29 are computed (others fail the source test); rendering.md §7.1 tightened | Claude, A5 approved at Phase 3 start |
| 2026-10-15 | Source cache: 4 banks by (row parity, line parity), XOR-folded set index, LRU among unlocked ways, 128-pixel lookahead FIFO | Claude, approved by user 2026-10-16 (Phase 3 sign-off) |
| 2026-10-15 | General pipeline: SOLID pixels also traverse the paint chain (single join); POINT = bilinear with zero fractions; spans of ≤ 256 B inside a 256-byte block; write requests trimmed to the written bytes | Claude, approved by user 2026-10-16 (Phase 3 sign-off) |
| 2026-10-15 | `RD_OUTSTANDING` / `WR_OUTSTANDING` defaults stay 8 (16 measured, no gain for blits) | Claude, approved by user 2026-10-16 (Phase 3 sign-off) |
| 2026-10-16 | Phase 3 approved by user ("Approved, continue to phase 4") | user |
| 2026-10-16 | Phase 4 answers: Q1 a (full support for long edge lists and > 512 active edges per row), A1–A10 accepted with conditions (docs/PHASES.md) | user |
| 2026-10-16 | Golden model path rasterizer restructured to the RTL's structure (user condition); the direct formula moves to the cross-checks | user, Claude |
| 2026-10-16 | Crossing state stored per row and advanced by a row step (32·dx/dy), sample steps only inside a row | Claude, approved by user 2026-10-16 (Phase 4 sign-off) |
| 2026-10-16 | Rows with more active edges than one pass: one-row bands and several passes per segment (no band halving) | Claude, approved by user 2026-10-16 (Phase 4 sign-off) |
| 2026-10-16 | Gradient index computed after the walker, carried through the paint chain as the tag; ramp reuse until a kick or a GPU write, never invalidated for the draw using it | Claude, approved by user 2026-10-16 (Phase 4 sign-off) |
| 2026-10-16 | Q-PERF-2 knobs: cache lookahead 256 (+1.3 KB SRAM), write-combiner span queue 32 | Claude, A9 |
| 2026-10-16 | Testbench reset start-up: reset high at time 0, low 1 ns later (asynchronous reset before the first clock edge with random initial values) | Claude |
| 2026-10-16 | Phase 4 regression with two seeds (1, 2); the deeper regression moves to Phase 5 | user |
| 2026-10-16 | K-1 accepted (glyph-sized paths 0.83 px/clk); Q-PERF-3 (band-setup overlap, second state RAM) not built in Phase 5 (hazard risk before the quality gates for ~+0.08 px/clk on one draw class); evaluate next-draw edge-list prefetch on the existing edge channel first | user |
| 2026-10-16 | Phase 4 approved by user ("other decisions are approved. Go to phase 5") | user |
| 2026-10-16 | Phase 5 answers: Q1 a (CDC wrappers to a new phase 6b before FPGA timing work), SYS 300 seeds × 2 configurations, full benches 10 new seeds (101–110), at most 16 cores | user |
| 2026-10-16 | ERR_ADDR for AXI errors = first requested byte of the faulting burst; ERR_CMD_ADDR = parser position (just past the buffer when it was consumed); specified from the implementation (arch.md §8) | Claude, for approval |
| 2026-10-16 | Code-coverage figure keeps out only listed lines (simulation-only / generated, Verilator-unmeasurable return-only case items, FSM default arms); raw figure reported too | Claude, for approval |
| 2026-10-16 | Phase 5 approved by user ("Approved, go to phase 6"); K-1 recommendation, ERR_ADDR / ERR_CMD_ADDR definitions and the code-coverage counting rule approved with it | user |
| 2026-10-16 | Path tolerance vs float reference, mean over edge pixels: per shape ≤ 10 → ≤ 12 plus the bound 10 kept for the mean pooled over a test's shapes (rendering.md §12), from the Phase 6 distribution over about 2100 shapes (per-shape max 11.8 on few-pixel shapes, median 7.4); arbitrary random Béziers tracked as Q-TEST-1 | Claude, for approval |
| 2026-10-17 | `transparency_mode` no longer forces source alpha: LVGL sets VG_LITE_IMAGE_OPAQUE on every buffer, so the Phase 0 reading (OPAQUE = force A 255) broke every glyph; the driver leaves FORCE_OPAQUE at 0 (api-subset.md §2, Q-API-11) | Claude, for approval |
| 2026-10-17 | A8 source texels are premultiplied white (a,a,a,a) (Q-API-3 resolved from LVGL usage); driver sets SRC_PREMUL for A8 sources, no hardware change | Claude, for approval |
| 2026-10-17 | `vg_lite_draw_pattern` implemented driver-only for rectangular paths (rendering.md §9.5): REPEAT / REFLECT as one blit per tile with exact fixed-point tile transforms, COLOR / PAD when the image covers the rect or the outside stays unchanged, COLOR with axis-aligned images as blit + SOLID rects; other paths NOT_SUPPORT and listed in docs/FEATURES_LATER.md (F-1 image paint on paths, F-2 texel wrap modes). Bilinear at tile seams clamps per tile (F-2). Q-API-12 records the pattern_color assumption | Claude, for user approval |
| 2026-10-17 | User approved: pattern scope, Q-API-12 as assumed (marked UNVERIFIED, locking test), Q-TEST-2 rotated-image criterion for listed frames (after the edge-locality check), seam-bilinear pinning test as F-2 | User |
| 2026-10-17 | B-6.1 SCREEN blend fixed in spec, model, reference and RTL (bug fix, no feature change) | Claude, reported to user |
| 2026-10-17 | `gcFEATURE_BIT_VG_IM_REPEAT_REFLECT` reads 1 (REPEAT / REFLECT spread already supported; LVGL refused such gradients) | Claude, reported to user |
| 2026-10-17 | LVGL demo config: `LV_USE_VECTOR_GRAPHIC 1` with LVGL's bundled ThorVG for the software reference, vector demo added, `LV_VG_LITE_USE_ASSERT 0` (errors fail test_lvgl.py instead of hanging); survey in docs/LVGL_SURVEY.md | Claude, reported to user |
| 2026-10-17 | Test bounds corrected from measurements (no renderer change): `test_coverage_vs_exact_area` one-edge mean ≤ 6.8 over 48 polygons, max ≤ 32 (400-seed population mean 6.27, max 28.9; the old 6.5 / 28.5 came from rendering.md §8.2's random-edge population); Skia stroke check with per-pixel attribution and ≤ 8 excursion pixels per shape (1620 strokes measured, max 5); rendering.md §8.2 states the second population | Claude, for approval |
| 2026-10-17 | Performance-only live build `dw128o8` and `make live-perf` (scripts/perf_lvgl.py, bandwidth.md §8) | Claude |
| 2026-10-17 | B-6.4 / B-6.5 fixed as clear bug fixes (RTL to the approved arch.md §8): after an AXI_RD halt the command side stays live while the parser can still progress; arch.md §8 now says when such a halt has settled (HALTED + IDLE with no kick queued). A STATUS bit for the queued-kick case is only proposed (Q-RTL-9) | Claude, reported to user |
| 2026-10-17 | FEATURES_LATER: F-1 (image paint on paths) re-estimated as a small RTL change (the path pipeline already runs the texel chain), F-4 (radial) ≈ 25 kGE (≈ 35 kGE with focal point); nothing planned | Claude, for approval |
| 2026-10-17 | `api-subset.md` status kept as a status section (not a per-row column); the phase's driver-only additions listed there | Claude |
| 2026-10-17 | Phase 6 approved by user ("phase 6 finished, push it"); merged into `main` | user |
| 2026-10-17 | CDC wrappers (Phase 6b) postponed until after the first FPGA tests; the first bring-up runs single-clock in the Cheshire SoC domain | user |
| 2026-10-17 | Phase 7 answers: Phase 7 = bare metal, Linux = new Phase 7b; CSR port via Cheshire's AXI4 external slave + PULP converters (v2: own platform-neutral bus adapters, planned in Phase 7, built later); Cheshire + OpenOCD installed into ~/tools; the board is on another machine (deliver bitstream, binaries, scripts; user runs them and returns logs) | user |
| 2026-10-17 | SoC simulation on Verilator 5.052 (Cheshire's commercial-simulator flow not used, Vivado xsim 2024.2 crashes on cheshire_soc); own testbench instead of Cheshire's (it needs vendor flash / EEPROM models); passive boot through SCRATCH | Claude |
| 2026-10-17 | Cheshire config: `AxiMstIdWidth 3` (G2D ID_W 3, no ID converter), G2D CSR at `0x4800_0000`, PLIC regenerated with 59 sources (G2D = 58), GPU pool at `0x8100_0000` (32 MB); the Genesys2 top is generated from Cheshire's (`gen_top.py`), Cheshire stays outside the repository | Claude |
| 2026-10-17 | B-7.1 (stroke discs without libm trig) fixed as a spec-conformance bug fix; rendering.md §9.7 now defines the disc vertex count exactly | Claude, reported to user |
| 2026-10-17 | Parallel job limit raised from 16 to 32 (scripts/regress.py, Makefile `JOBS`); session rules: check and stop stale shells at start, progress in docs/HANDOFF.md, long operations in the background | user |
| 2026-10-18 | Versioning v1.x → v2; branch `v1` + tag `v1.0` (Phases 0–7a), merged into `main`; roadmap docs/ROADMAP_V2.md approved; VGA kept in any Cheshire trimming and a VGA output test added (v1.2); NXP VGLite material local only; OSS CAD Suite installed, ASIC flow postponed; free disk space checked before big steps | user |
| 2026-10-18 | v1.1 CDC: own Cummings gray-pointer FIFO (`rtl/cdc`), FIFO depth 8 memory / 2 config port, `SYNC_STAGES` 2; all three resets asserted together (one-sided reset not supported); soft reset needs no CDC clear | Claude (plan approved with the roadmap) |
| 2026-10-18 | v1.1 approved; merged into `main`, tag `v1.1-done`; v1.2 started on `v1.2-board-ready` | user |
| 2026-10-18 | No bitstream builds until the end of v1.7; focus on new features. v1.2 closes with its simulation-verified parts (latency sweep + probe, LVGL frame, VGA test, trimmed-Cheshire option, run_board.sh); the trimmed / new bitstreams and the new board package move to the end of v1.7 | user |
| 2026-10-20 | v1.3 approved; merged into `main`, tag `v1.3-done`. DOOM side project planned (PS/2 via the Genesys2 HID port, GPU scale + flip, Freedoom + own WAD, GPL engine fetched into build/) and parked; v1.4 started | user |
| 2026-10-20 | v1.4 answers: 24-bit formats later (v1.6, driver conversion meanwhile); TRANSPARENT = A = 0 pixels not written; SPREAD_FILL = paint_color outside; own 4 × 4 Bayer dither | user |
| 2026-10-21 | v1.4: wrap seams handled by splitting the quad in front of the source cache (no cache change beyond a request mask); CLUT shares the ramp RAM with per-draw routing; wrap modulo by a per-draw reciprocal (serial dividers) | Claude |
| 2026-10-22 | v1.4: COLOR-mode pattern colour is a texel (multiplied by MULTIPLY image mode, bilinear-blended at the image edge; Q-API-12 assumption changed, D9 vs LVGL-SW) | Claude, for approval |
| 2026-10-24 | v1.5 scope and decisions on Q-API-12 / 18 / 19 (pasted with the start command), driver review findings D-1..D-4 recorded for v1.6; "start v1.5 and then v1.6 next to it" | user |
| 2026-10-26 | Q-API-27..30 decided (all stay open, how-to-verify notes): 128-word state approved (no register map / ABI change); Gaussian ¼ px/clk, any finite weights accepted and saturated, NaN / Inf rejected, strict check as a build option, default kernel an assumption; YUV video range + nearest chroma, YUV_FULL reserved, YUYV / 24-bit planar closed with errors (YUYV first to open); colour unit order and scope approved, stored-value operation an intentional OpenVG deviation (future un-premultiply bit); OPENVG_* kept behind a build parameter with a test per format | user |
| 2026-10-26 | v1.6b assumptions taken (docs/PHASES.md v1.6b questions 1–8, Q-API-27..30, all UNVERIFIED): state 128 words; Gaussian ¼ px/clk, weights checked; YUV limited range, YUYV / 24-bit planar closed, YUY2 / NV chroma alignment; colour unit order gamma → matrix → transform on stored values, also solid / gradient paints, not clears / masks; OPENVG_* formats in this version | Claude, for approval |
| 2026-10-26 | v1.6 approved, merged into `main`, tag `v1.6-done`; the v1.6 timing (WNS +1.972 ns) and LUT growth (+7 033) accepted for now and recorded as later work T-1 / T-2 (docs/FEATURES_LATER.md, v1.7); go to v1.6b | user |
| 2026-10-25 | v1.6 interpretations taken (Q-API-20..26, all UNVERIFIED): Porter-Duff operands / constant = PAINT_COLOR; SVG2 = W3C separable modes; RECOLOR = MULTIPLY; mask (x, y) = layer origin, 0 outside; blit2 = two blits with white; mirror = bottom-up storage; stencil = OpenVG §10.8 premultiplied, VG_LITE blends only | Claude, for approval |
| 2026-10-24 | v1.5 approved, merged into `main`, tag `v1.5-done`; continue v1.6 (items 1, 3, 4, 5, 7, 8, 10, 12) and stop before v1.6b (the v1.6 / v1.6b split accepted) | user |
| 2026-10-24 | v1.5 non-blocking assumptions taken (docs/PHASES.md v1.5 questions 1–7): D-items in v1.6; radial fixed point and focal clamp at 0.99 r (OpenVG-allowed); no [0, alpha] clamp hardware (proven no-op); premultiplied border only in TEXEL mode; traversal fix only below 85 % (not needed: measured ≥ 128 %); exact rectangles keep the blit; v1.6 continues from the v1.5 tip, merges only after approval | Claude, pending user confirmation |
| 2026-10-24 | v1.4 approved with changes: (1) COLOR pattern colour selectable, texel default (`PAINT_CTRL.BORDER_SOLID`, driver `vg_lite_g2d_set_pattern_color_mode`), Q-API-12 stays open; (2) D9 exemption only in the default mode, band fixed at 1 texel, auto-checked, band bit-exact RTL vs golden model, mean over the whole region; (3) Q-API-18 literal reading, open, no parameter. OpenVG cross-check recorded as Q-API-19 | user |
| 2026-10-18 | FPGA async build: G2D core 100 MHz from the clock wizard's unused fourth output (`g2d_clkwiz`), not an extra MMCM, and an explicit BUFG on the board clock (the extra MMCM made the clock placer fail); CDC constraints as unmanaged Tcl | Claude |
