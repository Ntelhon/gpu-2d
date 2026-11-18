# Roadmap v1 → v1.x → v2

Written 2026-10-18 on branch `v1.1`. Status: **approved 2026-10-18** (user; answers in §4). v1.1 started on branch `v1.1`.
Research behind it: `docs/research/GPU open questions industry research.md` (per-topic notes in
`docs/research/notes/`). The report and the notes that quote NXP's MCUXpresso VGLite pages stay **local and
uncommitted** until the source question in §4 item 4 is answered; only `notes/cdc_bus.md` is committed.

## 0. Starting point and versioning

- **v1 (= v1.0)**: branch `v1` on origin at `dfcdca2`. Phases 0–7a: spec, golden model, RTL (fill, blend, blit,
  perspective, path, linear gradient), verification (SYS, SVA, coverage, mutation), vg_lite driver + LVGL, Cheshire
  integration, Genesys2 bitstream (timing met), Verilator SoC simulation 6 / 6 bit-exact. **Not done: the board run
  (FPG-04)**: the board is not available for now.
- From here every update is a **v1.x** version; **v2** is reached when every open question is answered and every
  missing feature is either built or explicitly closed with a reason (§3).
- Each v1.x keeps the phase rules of CLAUDE.md: own branch, plan → spec → model → RTL → test → regression → docs,
  WAIT RULE at its end (report, wait for "approved, go to v1.x+1"), tag `v1.x-done`.
- **Board milestone B** (FPG-04 and what depends on the board) is not a version: it is slotted in as soon as the
  board is available, between whichever v1.x versions are running then (§2).

## 1. Versions

Ordered so that everything the first board session needs is ready first (v1.1, v1.2), then API / hardware
completeness, then integration and productisation. "Board" column: what can only be closed on the board.

| Version | Name | Content | Closes | Board |
|---|---|---|---|---|
| v1.1 | CDC (old Phase 6b) | Async wrappers for `CFG_ASYNC` / `MEM_ASYNC = 1`: Cummings gray-pointer FIFO (own RTL, same structure as PULP `cdc_fifo_gray`), AXI4-Lite 5-channel and AXI4 5-channel bridges, parameter `SYNC_STAGES` (default 2 FPGA / 3 ASIC, one swappable sync-cell module), FIFO depth 8 on the memory port and 2 on the config port (sized for the synchronizer round trip, not for outstanding transactions); spec rule "all three resets assert together, de-assert per domain" (one-sided reset not supported) and an SVA that the CDC FIFOs are empty at `soft_clr`; Vivado constraints (`ASYNC_REG`, `set_max_delay -datapath_only`, `set_bus_skew`) and ASIC SDC notes; verification: randomized and co-prime clock ratios / phases per seed, a sim-only synchronizer model with random N / N+1 delay, gray-step / overflow / underflow assertions, framebuffer CRC `ASYNC=1` == `ASYNC=0` on the L3 smoke set and SYS seeds; FPGA build variant with G2D core on its own clock (first target 100 MHz, memory and config ports at the 50 MHz SoC clock) | 6b, Q-RTL-4 (close: level IRQ from a flop, integrator synchronises; driver reads back IRQ_STATUS after clear) | first board test of the async build |
| v1.2 | Board readiness | SoC sim: fixed / programmable delay stage in front of the DRAM model and a prediction of busy cycles at 10 / 20 / 40 cycles (Q-FPGA-4 estimate, research: ~20–35 miss, ~8–12 LLC hit); board test: single-command latency probe + sweep of Cheshire's `USE_RAM_DELAY` registers (AR/R delay at `0x4300_0018 / _001C`, already in the bitstream), LVGL frame in the board ELF if missing (Phase 7 exit criterion 3); `run_board.sh` hardening (stop `hw_server` before OpenOCD, record `lsusb` / serial ports, reset-recovery procedure from Cheshire's docs); trimmed-Cheshire variant (USB, DMA, bus-error units, I2C off, **VGA kept** (user: used for FPGA demos); ~23 k LUT, 87.7 % → ~76 %) with the PLIC source number re-checked; **VGA output test** (approved, Q-FPGA-5): G2D renders into the framebuffer Cheshire's `axi_vga` scans out (640×480 RGB565); self-checking in the SoC simulation (the testbench captures the VGA pixel stream of a frame and compares it bit-exactly with the SW-backend image) and on the board (framebuffer CRC over UART + a visible demo: scene set and an LVGL frame on the monitor); new board package with both bitstreams (single clock, async) | Q-FPGA-4 (prediction), Q-FPGA-5 (VGA test), Q-FPGA-8 (procedure), K-2 (option) | all measurements, VGA picture |
| v1.3 | Driver-only API completion | Q-API-14 (b) centre-sampled AA border for fractional pattern rectangles (after approval); F-5 dashed strokes; perspective path matrix (projective transform + curve subdivision); arc path commands (`VLC_OP_*ARC*`, `vg_lite_init_arc_path`) and `VLC_OP_BREAK`; `vg_lite_set_command_buffer` / `_set_memory_pool` / `_frame_delimiter`; spec notes from the research (Q-API-4: premultiplied callers use MULTIPLY; Q-API-8: OpenVG draws nothing on a singular matrix, we return INVALID_ARGUMENT; Q-API-15: OpenVG A4 order and LVGL simulator `v<<4` listed as differences); `make smoke` (Q-TEST-3) | Q-API-4/8/14/15 (documented), F-5, Q-TEST-3 | — |
| v1.4 | Small hardware features | F-3 formats in hardware: targets BGRA5551 / 4444 / 2222, sources A4 / A2 / A1 / L8 / L4, indexed I1–I8 with a 256×32 CLUT (shares the gradient ramp RAM; point sampling only, as NEMA), optional RGB888 / ARGB8565; F-2 exact wrap modes (REPEAT / REFLECT / PAD / COLOR) for any size (per-draw reciprocal, ~15–20 kGE) + border colour register; `VG_LITE_GRADIENT_SPREAD_FILL` (t outside [0,1] → paint colour, Q-API-6); RGB565 dither (`vg_lite_enable_dither`); `transparency_mode = TRANSPARENT` as "skip texels with alpha 0" (Q-API-11, after approval); Q-RTL-9 `STATUS.CMD_ACTIVE` (regs.yaml) | F-2, F-3, Q-API-6, Q-API-11, Q-RTL-9 | — |
| v1.5 | Image paint on paths + radial gradients | F-1: path coverage × sampled texel (draw-check rule, join, model, driver); source-cache traversal fix for rotated images on scanlines (segment-major traversal inside a band or a tile-order coverage reorder buffer, decision by measurement); Q-API-14 solved exactly; F-4: radial gradient (~25 kGE) and focal variant (~35 kGE, Skia two-point-conical formulation, behaviour outside the cone defined in rendering.md first; 2026-10-24: OpenVG 1.1 §9.3.2 forces the focal point inside the circle, so no such case exists); Q-API-19 (a)–(d) per the user's decisions of 2026-10-24 (premultiplied border colour, [0, alpha] clamp analysis, perspective / projective-pattern tests and docs) | F-1, F-4, Q-API-14 (exact), Q-API-19 | — |
| v1.6 | VGLite 4 extras | Driver robustness D-1..D-4 (external review, 2026-10-24, docs/FEATURES_LATER.md); 24-bit sources in hardware (moved from v1.4); mask layers (`vg_lite_*mask*`), `vg_lite_scissor_rects` with n > 1 (via the mask), OPENVG_BLEND_* / `VG_LITE_PORTER_DUFF_BLEND` / `vg_lite_blend_func` (un-premultiply divide), `vg_lite_set_mirror`, `vg_lite_set_gamma` / colour transform, `vg_lite_gaussian_filter`, `vg_lite_blit2`, YUV sources with CSC (NV12 / YUY2, ~10–15 kGE). Each item: implement or close with a reason (e.g. compressed / tiled vendor formats are not publicly specified) | api-subset.md "later" rows | — |
| v1.7 | Performance and timing | Q-PERF-2: more outstanding source-cache fills (~11 at 90-clock latency) or tiled / Morton source layout; Q-PERF-4: per-16-px "fully inside" flag → skip the destination read for opaque spans; Q-PERF-3 / K-1: edge-list prefetch (cheaper step); timing: R register slice (Q-RTL-1), split stages (Q-RTL-6/8, Q-RTL-10 / T-1 from v1.6), area clean-up and trimmed FPGA configuration (T-2), radix-4 divider (Q-RTL-7), OOC sweeps at higher targets on the K7; Q-PERF-1 model speed | Q-PERF-1..4, K-1, Q-RTL-1/6/7/8 (FPGA view) | perf on DDR3 |
| v1.8 | Bus adapters and packaging | F-6: `g2d_axi2reg`, `g2d_apb2reg`, `g2d_ahb2reg`, `g2d_obi2reg`, `g2d_wb2reg`, `g2d_tlul2reg` (own RTL, each with its own cocotb bench + protocol checker); AXI master options (AxCACHE / PROT / QOS, ID serialisation, data-width converter); IRQ polarity / pulse option; FuseSoC core, Bender.yml, IP-XACT generated from regs.yaml; Cheshire integration switched from the PULP bridge to `g2d_axi2reg` after it passes the SoC sim | F-6 | SoC test with own adapter |
| v1.9 | Linux (old Phase 7b, software side) | Check cva6-sdk at Cheshire's pinned commit (kernel version, DMA config, Svpbmt); kernel driver as a misc char device + ioctls (user library = our vg_lite driver with a Linux HAL); device-tree binding (PLIC numbering 58 vs 59 checked); cache maintenance: CVA6 here has no Zicbom, so the driver uses the `fence` flush (as bare metal) or uncached mappings if Svpbmt is present; build-tested here, booted on the board | 7b (software), Linux coherence question | Linux boot + tests |
| v2.0 | Closure | Board-verified (bare metal, async build, Linux); ASIC numbers (Phase 8, §1.1); every item of OPEN_QUESTIONS / FEATURES_LATER / api-subset "later" resolved, implemented or closed with a recorded reason; docs, STATUS, TOOLS current; merge + tag `v2.0` | everything | — |

### 1.0 Next versions (proposal 2026-11-04, after the first DOOM board run; NOT approved yet)

The board run of the DOOM side project (docs/HANDOFF.md, OPEN_QUESTIONS Q-FPGA-9 / Q-FPGA-10) showed that v1.3–v1.7
were verified in simulation only and that the board differs: the trimmed Cheshire hangs DDR writes, path rendering
is wrong on the FPGA although it passes in simulation, `axi_vga` slides under load. Proposed order: make the board
trustworthy first (v1.8), then the planned integration work (v1.9), then Linux and closure (v2.0). The old v1.8
(bus adapters) moves to v1.9, the old v1.9 (Linux) into v2.0.

| Version | Name | Content | Board |
|---|---|---|---|
| v1.8 | Board-proven | **Q-FPGA-10** path rendering on the FPGA: find the synthesis / simulation difference (post-synthesis functional simulation of g2d_top from Vivado's netlist in the SoC sim or a standalone bench, dump a wrong frame with openocd_peek, bitstream with the pre-v1.7 rasterizer as a bisection point), fix, board test bit-exact again. **Q-FPGA-9** root cause (I2C trim alone vs combination; build I2C-only with FEATURES = 0 so it meets timing; report upstream to Cheshire if it is theirs). New standard board bitstreams: single clock and async (async never ran on the board), untrimmed-I2C Cheshire (mask 7), full G2D, chs_ps2, VGA buffer 2 KB, `expected.txt` per build features (G2D_MODEL_FEATURES); board package with run scripts for OpenOCD + telnet + picocom (no .sh needed), faster loading (JTAG ~1.5 KB/s on the VM host: SD-card boot of the WAD / program through Cheshire's boot ROM, or a direct-USB host). Simulation gaps closed: a DDR-like memory model in the SoC sim (variable latency, refresh stalls, the Xilinx DRAM wrapper's ID converter + CDC) so that Q-FPGA-9-type bugs show up before the board. DOOM: merged with known bugs on 2026-11-06 (`doom-done`); still to do: **Q-FPGA-11** residual VGA blinks (measure underruns / latency in the axi_vga fork, bigger buffer, VGA priority), fps measured (single-buffer wait removed: overlap the blit with the next frame's rendering), `doom-done` tag | full board regression, DOOM playable, async build |
| v1.9 | Bus adapters and packaging (old v1.8) | F-6: own `g2d_axi2reg`, `g2d_apb2reg`, `g2d_ahb2reg`, `g2d_obi2reg`, `g2d_wb2reg`, `g2d_tlul2reg` with benches and protocol checkers; AXI master options (AxCACHE / PROT / QOS, ID serialisation, data-width converter) and a QoS / bandwidth cap on the memory port (the DOOM run showed G2D bursts starving `axi_vga`); IRQ polarity / pulse option; FuseSoC core, Bender.yml, IP-XACT from regs.yaml; Cheshire integration switched to `g2d_axi2reg`; K-1 next-draw edge prefetch and Q-PERF-4 opaque SRC_OVER skip (deferred from v1.7) if the board numbers ask for them | SoC test with own adapter |
| v2.0 | Linux and closure (old v1.9 + v2.0) | cva6-sdk at Cheshire's pinned commit; kernel driver (misc char device + ioctls, our vg_lite as the user library with a Linux HAL), device-tree binding, cache maintenance (`fence` or Svpbmt uncached mappings); Linux boot on the board with LVGL / DOOM as user programs; ASIC numbers (Phase 8: synthesis with an open PDK flow if tools are approved); every OPEN_QUESTIONS / FEATURES_LATER / api-subset "later" item resolved or closed with a reason; docs current; tag `v2.0` | Linux boot + tests |

### 1.0b Proposal 2026-11-10 (after the repository audit; NOT approved yet)

Source: docs/AUDIT_2026-11.md (finding IDs A- / B- / C- / D- refer to its §3). Replaces the §1.0 table if approved.
Changes against §1.0: v1.8 gets a concrete VGA fix and a netlist-simulation stage; a display-controller version is
inserted as v1.9; bus adapters move to v1.10; v2.0 also takes the driver work; the time after v2.0 is listed.

| Version | Name | Content | Closes | Board |
|---|---|---|---|---|
| v1.8 | Board-proven | **Q-FPGA-11** (audit §2.2): step 1 SD image with `DOOM_LLC_CACHE=0` as the confirmation test; step 0 underrun / fill-level / latency counters in the `axi_vga` fork; step 2 real bursts (burst splitter and credit counter removed, block-RAM FIFO, 1 024 beats); step 3 frame buffers outside the LLC's cached region (Cheshire patch: own cached-region end; GPU pool above it); step 4 line-based fetch with repeat-last-line on underrun. **Q-FPGA-10**: post-synthesis functional netlist of `g2d_top` in the cocotb benches (new regression stage), Verilator with randomised initial values, synthesizable RAM-collision sticky bits and a write-channel signature register (A-1, A-2), Vivado RAM-inference review, then the fix and a full-feature (FEATURES = all) board regression. **Q-FPGA-9** as in §1.0. Async bitstream on the board (A-5). DDR-like memory model in the SoC sim (as §1.0). Repository: CI workflow for `make regs-check lint test`, top-level README, licence decision, third-party notice (A-6, D-1), Makefile help text (D-3) | Q-FPGA-9 / 10 / 11, A-1..A-6, D-1, D-3 | full board regression, DOOM without glitches, async build |
| v1.9 | Display controller and memory QoS | Own `g2d_dc` (spec first: `spec/display.md`, registers in regs.yaml or an own map): programmable timing generator, line-buffered fetch (underrun repeats the previous line, counter + interrupt), vsync interrupt and frame counter, shadowed flip register, formats RGB565 / ARGB8888 / INDEX_8 with CLUT, integer up-scaler, two layers with per-pixel and global alpha, VGA pins on the Genesys2 (HDMI as an option, to be checked against the board); golden model of the scan-out and a self-checking pin-capture test (as the v1.2 VGA test). G2D memory-port throttle (CSR: maximum outstanding bursts, minimum gap; default off) (B-7). DOOM scanned out directly from its 320 × 200 indexed screen; LVGL demo on the monitor. `chs_ps2`'s frame counter replaced by the display controller's | B-7, display gap of audit §4 | DOOM and LVGL on the monitor, no `axi_vga` |
| v1.10 | Bus adapters and packaging (old v1.9) | As §1.0 v1.9 (F-6 adapters, AXI master options, IRQ options, FuseSoC / Bender / IP-XACT, Cheshire on `g2d_axi2reg`), plus: SDC template for the IP (D-2), memory wrapper variants (behavioural / Xilinx with explicit style and write mode / ASIC stub with BIST ports) (B-2), `test_mode_i` for the reset path (B-4) | F-6, B-2, B-4, D-2 | SoC test with own adapter |
| v2.0 | Drivers and closure | Driver: HAL lock hooks and thread-safe entry points (C-1), interrupt-driven wait on bare metal (C-3), range-based cache maintenance with `fence` fallback and the non-coherent HAL model as a permanent test (C-4), 64-bit GPU addresses in the HAL (C-5), timer-calibrated delays (C-6); one RTOS HAL (Zephyr or FreeRTOS, user's choice) with LVGL (C-2); Linux as §1.0 v2.0; ASIC numbers from the open flow if approved (B-1): area, logic depth, fmax, and from them a statement on the 600 MHz target; every OPEN_QUESTIONS / FEATURES_LATER / api-subset item closed; tag `v2.0` | C-1..C-6, B-1 (numbers), everything | RTOS and Linux on the board |

After v2.0 (each needs its own approval; order is a suggestion):

| Version | Name | Content |
|---|---|---|
| v2.1 | OpenVG 1.1 API layer | Software only (audit §5.2): `vg*` + VGU over the internal driver, minimal EGL for bare metal / RTOS, software fallbacks for convolution / lookup filters, image comparison against a reference with a stated tolerance; no conformance claim |
| v2.2 | ASIC hardening | Pipelining to the frequency the v2.0 numbers justify, unit clock gating and an idle / power-down handshake (B-3), scan and BIST integration, a second synthesis library for comparison |
| v2.3 | Vector and image quality / speed | Hardware path front end (transform + flatten in front of `g2d_edge_load`) if the measured CPU share justifies it (B-5); driver mip levels or a box prefilter (B-6); an own open compressed frame-buffer / texture format |
| G3D | 3D GPU (separate IP) | Audit §5.3: shared platform blocks factored out of `rtl/` and `dv/`, then a fixed-function triangle pipeline (setup, edge-function rasterizer, depth / stencil, perspective-correct interpolation, mip-mapped textures) with its own golden model and a small GL library; programmable shading only after that |

### 1.0c Order from 2026-11-12 (approved: user, "Continue with new recommendations")

The goal is an industry-ready 2.5D GPU IP. GPU IPs of this class ship without a display controller (the SoC
provides one), so the display controller of §1.0b v1.9 leaves the main line.

| Version | Name | Content | Closes |
|---|---|---|---|
| v1.8 | Board-proven | done (tag `v1.8-done`); board runs by the user | Q-FPGA-10 / 11 tooling |
| v1.9 | Integration package and memory QoS | §1.0b v1.10 content: F-6 configuration adapters, memory-port attributes, throttle (B-7), ID / width / burst options, IRQ options, `test_mode_i` (B-4), RAM wrapper variants (B-2), SDC template (D-2), IP-XACT / FuseSoC / Bender, Cheshire on `g2d_axi2reg`, `docs/INTEGRATION.md` (docs/PHASES.md "v1.9 plan") | F-6, B-2, B-4, B-7, D-2 |
| v2.0 | Drivers and closure | as §1.0b v2.0 (C-1..C-6, RTOS HAL, Linux, ASIC numbers, programming manual, every open item closed) | C-1..C-6, B-1 |

Display controller: a separate, optional companion IP or project after v2.0 (the §1.0b v1.9 feature list is its
starting point). The demos keep the `axi_vga` fork.

Decisions needed (one batch, 2026-11-10):

1. Approve §1.0b in place of §1.0 (order v1.8 → v1.9 display controller → v1.10 adapters → v2.0)?
2. Own display controller (v1.9) or keep improving the `axi_vga` fork only?
3. Frame buffers outside the LLC's cached region (a second small Cheshire patch): acceptable?
4. Licence for the own RTL and software, and may a top-level README describe the project publicly?
5. CI on GitHub (needs a tools container; no licensed tools in it): yes / no?
6. RTOS for the first non-bare-metal HAL: Zephyr or FreeRTOS?
7. After v2.0: which of v2.1 (OpenVG), v2.2 (ASIC hardening), v2.3, G3D first?

Assumptions if no answer: nothing of §1.0b starts (WAIT RULE); the next session begins with v1.8 step 1 (the
software-only LLC confirmation test), which needs no approval beyond the already proposed v1.8.

### 1.1 ASIC (old Phase 8) inside v1.7 / v2.0

Q-TOOL-1 first (which ASIC tool flow). Default: Yosys + OpenROAD (ORFS) with an open PDK (IHP SG13G2 proposed). **Postponed (user 2026-10-18): the ASIC
flow is installed only later and after a new explicit approval.** 600 MHz is a 28-nm-class target; an open 130-nm flow will not reach it (Basilisk, a Linux SoC in IHP
130 nm, reached 77 MHz), so the open flow reports area (kGE), logic depth normalised to FO4, and its own fmax,
and 600 MHz stays a target to check with commercial tools.

## 2. Board milestone B (whenever the board is available)

1. FPG-04 with the existing v1 package (single clock): `run_board.sh`, `scripts/fpga_check.py`, bit-exact scenes,
   PLIC IRQ, soft reset / error recovery.
2. Q-FPGA-4: latency probe + RAM-delay sweep; bandwidth.md §8 FPGA column; compare with the v1.2 prediction.
3. Q-FPGA-8: UART chip, udev, hw_server / OpenOCD sequence recorded in FPGA.md.
4. Async build (v1.1) on the board; trimmed-Cheshire build if v1.2 made one.
5. VGA output test and demo (approved, v1.2).
6. Later sessions: v1.5–v1.8 hardware on the board, Linux (v1.9).

## 3. v2 exit criteria

- `docs/OPEN_QUESTIONS.md`: no item in the open tables; every item resolved by evidence, by measurement, or
  accepted by the user with the reason recorded. Items that only Vivante hardware could settle (exact rounding,
  internal premultiply) stay "accepted difference, API-level compatibility".
- `spec/api-subset.md`: no "later" row without a decision (implemented, or out of scope with a reason).
- `docs/FEATURES_LATER.md`: every F-item built or closed.
- Board: bare metal (single clock + async) and Linux pass their tests bit-exact.
- ASIC: synthesis / STA report with area and fmax (commercial or open flow, as approved).
- `make lint / test / regress / model / regs` green from a clean checkout.

## 4. Decisions needed from the user (one batch)

1. **Approve this roadmap** (order of v1.1–v1.9; board milestone B as a floating milestone).
2. **Branches / merges**: proposal: tag `v1.0` on `v1`; each version on its own branch from the previous one
   (`v1.1` now, `v1.2-board-ready`, ...), merged into `main` at its end with tag `v1.x-done`. Should `v1` also be
   merged into `main` now (main is at Phase 6)?
3. **CDC clocks for the FPGA build**: G2D core at 100 MHz with ports at the 50 MHz SoC clock (assumption). Note: the
   memory path through Cheshire's 64-bit 50 MHz crossbar caps bandwidth at ~400 MB/s whatever the core clock, so
   the faster core helps compute-bound draws only.
4. **Source provenance**: NXP's public MCUXpresso "VGLite Graphics Driver" API reference web pages answered several API
   questions (SPREAD_FILL, transparency_mode, pattern_color). They may be an HTML edition of the VGLite manual we
   treat as confidential. May the spec cite them? Until you decide, the research marks that evidence QUARANTINED
   and the roadmap does not depend on it.
5. **Tool installs under `~/tools`** (when their version comes): OSS CAD Suite (yosys, SymbiYosys, solvers) for the
   async-FIFO formal proof in v1.1; ORFS + IHP SG13G2 for the ASIC numbers.
6. **VGA display test** on the board (Q-FPGA-5): yes / no.
7. Carried over, still open: Q-API-14 (b), Q-API-3 refinement, Q-TEST-3 (`make smoke`), Q-API-11 TRANSPARENT.

Assumptions if no answer: v1.1 starts with the CDC exactly as above, no new tools installed (formal proof deferred
until approved), MCUXpresso evidence not cited.

### Decision (user, 2026-10-18, during v1.2)

No bitstreams are generated until the end of v1.7; work focuses on new features (v1.3 onwards). v1.2 is closed with
what the SoC simulation verifies; the trimmed-Cheshire bitstream, any new bitstream and the next board package are
built at the end of v1.7 (together with v1.7's timing work).

### Answers (user, 2026-10-18)

1. Roadmap approved. Cheshire trimming must **keep VGA** (FPGA demos); a test using the VGA output is added (v1.2).
2. Branch handling approved: tag `v1.0` on `v1` and `v1` merged into `main` (done 2026-10-18); one branch per version,
   merged into `main` with tag `v1.x-done` at its end.
3. CDC FPGA clocks approved: G2D core 100 MHz, config and memory ports at the 50 MHz SoC clock.
4. NXP material is used **locally only**, never committed (the report and three notes stay untracked, gitignored).
5. OSS CAD Suite installed under `~/tools`; ASIC flow postponed until a later approval. Free disk space is checked before
   every install / large build.
6. VGA display test: yes.
7. Q-API-14 (b), Q-API-3 refinement, Q-API-11 TRANSPARENT: undecided, stay open. (`make smoke` already exists as an
   alias of `make test`.)
