# Features needed from future hardware

Phase 6 decision (user, Q4 a): where LVGL needs something the v1 hardware cannot do, the driver supports
what it can without RTL changes, LVGL's software renderer covers the rest, and the hardware gap is listed
here with where LVGL uses it and a rough cost. Nothing here is planned for a phase yet.

| ID | Feature | Where LVGL v9.6 uses it | Driver today | Hardware cost (estimate) |
|---|---|---|---|---|
| F-1 | Image paint on paths (DRAW_PATH with `PAINT_CTRL.SRC = IMAGE`: path coverage × sampled texel) | `vg_lite_draw_pattern` with non-rectangular paths: images with `clip_radius` (rounded-rect path, `lv_draw_vg_lite_img.c`), arcs with an image source (`lv_draw_vg_lite_arc.c`), vector graphics with image fills (`lv_draw_vg_lite_vector.c`), label fallback (`lv_draw_vg_lite_label.c`) | **Closed 2026-10-24 (v1.5)**: DRAW_PATH with `PAINT_CTRL.SRC = IMAGE` (draw-check rule dropped, join gates the path coverage with the source test, walker in scanline order for paths); `vg_lite_draw_pattern` on any path and path matrix (rendering.md §9.5) | Built: as estimated (a mux, the draw-check rule; the walker's tile flag gated by `path`) |
| F-2 | Texel wrap modes in the texel unit (REPEAT / REFLECT / PAD addressing and a border colour for COLOR) | Tiled images (`dsc->tile`, `VG_LITE_PATTERN_REPEAT`); exact wrap also for bilinear seams | **Closed 2026-10-20 (v1.4)**: `PAINT_CTRL.WRAP` + `BORDER_COLOR` (rendering.md §7.5), exact modulo for every period (per-draw reciprocal), wrapped bilinear neighbours, seam quads split in front of the source cache; `vg_lite_draw_pattern` is one blit for every mode and pattern matrix (§9.5) | Built: two 17 x 32 and two 20 x 15 bit multipliers, two serial dividers, split / merge stages (v1.4 OOC numbers in docs/STATUS.md) |
| F-3 | More pixel formats: unpack A4, BGRA5551, BGRA4444, BGRA2222 (sources); pack BGRA5551 / 4444 / 2222 (targets). Optional: 24-bit RGB888 / ARGB8565, indexed I1–I8 with CLUT | LVGL accepts A4 / 1555 / 4444 / 2222 for VG-Lite without a feature check (`lv_vg_lite_is_src_cf_supported`, `_dest_cf_supported`); the others are guarded by feature bits and fall back to software | **Closed 2026-10-20 (v1.4)** except 24-bit: every order of 4444 / 5551 / 2222 as source and target, A4 / A2 / A1 / L4 and I1 / I2 / I4 / I8 (CLUT in the ramp RAM, point-sampled) as sources (rendering.md §3.2, §3.5). 24-bit RGB888 / BGR888 / *8565 / *5658: converted by the driver until v1.6, **in hardware since v1.6b** (§3.2; a texel crossing a cache line is read with a second request, `g2d_tex_split` / `g2d_tex_merge`) | Built (v1.6b): a ×24 column address, the split's straddle request, a 24-bit join in the merge |
| F-4 | Radial gradient paint (`vg_lite_draw_radial_grad`, `gcFEATURE_BIT_VG_RADIAL_GRADIENT`) | radial lv_vector gradients, radial style gradients | **Closed 2026-10-24 (v1.5)**: `PAINT_CTRL.SRC = 3`, state `GRAD_UP .. GRAD_VR_HI`, centred and focal (rendering.md §10.4); driver `vg_lite_*radial_grad`, feature bit 1 | Built: four more 32 × 15 multipliers, two 32 × 32 squarers, a 64-bit add, a 32-stage integer square root (v1.5 OOC numbers in docs/STATUS.md) |
| F-5 | Dashed strokes (`vg_lite_set_stroke` with a dash pattern and phase) | lv_vector strokes with `lv_draw_vector_dsc_set_stroke_dash` (vector demo `draw_lines`), SVG `stroke-dasharray` | **Closed 2026-10-19 (v1.3)**: driver-only dashes (rendering.md §9.7.1, OpenVG 1.1 rules), tests in sw/tests/test_stroke.py (independent interval reference, Skia `SkDashPathEffect`) | None: driver-only work (split the flattened polyline at the dash boundaries, cap each dash); listed here because it is out of the Phase 6 scope (user, 2026-10-17), not because hardware is needed |
| F-6 | Platform-neutral bus adapters (v2, user 2026-10-17): G2D integrates into any SoC without editing the IP | not an LVGL feature: integration (Phase 7 uses PULP converters in fpga/cheshire only) | n/a | Small RTL per adapter, medium verification (plan below) |

## Hardware estimates (Phase 6, P6 item "FEATURES_LATER estimates")

Rough, from the RTL as built (arch.md §6.3) and textbook cell counts; GE = NAND2 equivalents, before
synthesis (to be replaced by Phase 8 numbers). Not planned for a phase; listed so a later decision has a
starting point.

### F-1 Image paint on paths

What exists: every DRAW_PATH already runs through the general pipeline `g2d_walk → g2d_xform →
g2d_tex_addr → g2d_src_cache → g2d_texel → join → g2d_blend`; the path coverage of `g2d_raster` arrives at
the join in walk order, and the destination is always read for paths. Today the texel results of a path
pixel are ignored because `g2d_draw_check` rejects `PAINT_CTRL.SRC = IMAGE` on DRAW_PATH (rendering.md
§13.1).

| Block | Change |
|---|---|
| `g2d_draw_check` | drop the `SRC ≠ IMAGE` rule for DRAW_PATH; apply the source checks (§13.1) and the source extent (§13.2) to paths |
| `g2d_pix` join | `cov = tx_pass ? cv : 0` for path + IMAGE (path coverage gated by the source test, rendering.md §7.4); the source cache is invalidated per draw as for blits |
| `g2d_pix` walker | none functionally: paths walk scanlines (the raster's order). Rotated / perspective images then sample in scanline order instead of 16×16 tiles: correct, but a lower source-cache hit rate (measure; a tile-order raster is a larger change) |
| read clients | edge (3), source (2) and destination (1) active in one draw: already arbitrated per burst; the credit argument of arch.md §4.3 holds (each client accepts its own beats), to be restated for this case |
| golden model / refimpl | path coverage × sampled texel in the executor (both pieces exist) |
| driver | `vg_lite_draw_pattern` and image-filled `vg_lite_draw` on any path (rendering.md §9.5 loses its rectangle rule); Q-API-14 (fractional AA rectangles) is solved exactly by the same change |

Pipeline stages: none added. Area: a few hundred GE (a mux and the draw-check rule). Cost is verification:
L3 path × image × blend × filter matrix, a new coverage group, SYS streams, the LVGL clip-radius / arc /
vector image cases against LVGL's software renderer and ThorVG, and a throughput check of rotated image
fills under memory latency (PRF).

### F-4 Radial gradients

Driver work: fold the gradient matrix, the centre and `1/r` into per-draw coefficients so the hardware never
divides per pixel: `gx = Pa·x + Qa·y + Ra`, `gy = Pb·x + Qb·y + Rb` in units where the ramp radius is 256.

| Block | Change | Rough size |
|---|---|---|
| `g2d_grad` | second coefficient set: two more 32×15 multiplies (the linear T already has two) | ≈ 6 kGE |
| new: squares + sum | `gx² + gy²` from ~24-bit operands (two squarers, one 48-bit add) | ≈ 4 kGE |
| new: square root | pipelined non-restoring integer sqrt, 48 → 24 bits (1 bit per stage, 24 stages of a ≤ 48-bit add/sub plus pipeline registers) | ≈ 15 kGE (≈ 1.7 k flops) |
| spread + ramp | unchanged: `i = floor(sqrt(...))` (pixel centre) feeds the existing PAD / REPEAT / REFLECT logic and the 256-entry ramp RAM | 0 |
| registers (spec/regs.yaml) | `GRAD_KIND`, second coefficient set, focal constants | small |
| focal-point variant (`fx, fy ≠ cx, cy`) | `t = (B + sqrt(B² − A·C))·(1/A)` with `A` and `1/A` constant per draw (driver): one extra multiply-add before the sqrt, one multiply after; a pixel outside the cone needs a defined result (spec decision) | + ≈ 10 kGE |

Pipeline stages: about 26–30 (multiplies, squares, sqrt) between the walker and the tag join; the gradient
index already rides through the paint chain as an 8-bit tag, so this adds latency, not throughput loss
(1 px/clk). Total rough area: simple radial ≈ 25 kGE, with focal point ≈ 35 kGE; no new SRAM. Golden model,
refimpl and an IDEAL check (float distance, index ±1) come first (docs/PROJECT_RULES.md rule 6).

### F-6 Platform-neutral bus adapters (v2 plan, user request 2026-10-17)

Goal: `g2d_top` drops into any SoC (PULP / Cheshire, Xilinx / Intel FPGA systems, Arm AMBA, RISC-V TileLink, Wishbone
SoCs) with a thin wrapper of ours, no change inside the IP, no third-party converter. Phase 7 attaches it to Cheshire
with the PULP `axi_to_axi_lite` + `axi_lite_dw_converter` (fpga/cheshire/g2d_cheshire.sv); v2 replaces that with
the adapters below. Not scheduled yet.

**Configuration side.** The CSR core already has a bus-agnostic port (`g2d_reg_req_t {valid, write, addr[11:2],
wdata, wstrb}` / `g2d_reg_rsp_t {ready, rdata, error}`, arch.md §4.1); `g2d_axil2reg` is its first adapter. New
adapters in `rtl/adapt/`, each a few hundred GE:

| Adapter | Protocol points |
|---|---|
| `g2d_axi2reg` | full AXI4 slave at any data width 32..512: bursts split into single register accesses, lane select from the address, ID reflection, ATOPs answered with SLVERR, configurable outstanding depth (replaces the PULP pair on Cheshire) |
| `g2d_apb2reg` | APB3 / APB4 (PSTRB, PPROT ignored, PSLVERR) |
| `g2d_ahb2reg` | AHB-Lite slave (HREADY wait states, HRESP error, byte / half-word writes through HSIZE) |
| `g2d_obi2reg` | OBI / PULP `reg_intf` (req / gnt / rvalid), used by CVA6 / Cheshire register buses |
| `g2d_wb2reg` | Wishbone B4 pipelined |
| `g2d_tlul2reg` | TileLink-UL (OpenTitan / Chipyard style) |

**Memory side.** The engines talk to the internal read-client / write-producer interfaces (arch.md §4.3); only
`g2d_axi_mst` speaks AXI4. v2 options:
- parameters on the AXI4 master that today are fixed: `AxCACHE` / `AxPROT` / `AxQOS` values (or CSR fields), user
  width (driven 0), ID width below 3 with internal ID serialization (read clients share IDs, in order), maximum
  burst length 1 (AXI4-Lite-like systems);
- an own AXI4 data-width converter at the port (64 / 128 internal to 32 / 256 / 512 external);
- protocol bridges as wrappers: AXI4 → AHB (master), AXI4 → TileLink-UH, AXI4 → OBI.

**Interrupt and reset.** Level interrupt today; parameters for polarity and an optional one-cycle pulse per new
IRQ_STATUS bit; reset polarity / synchronous option only in the wrapper (the core keeps arch.md §2.2).

**Clocks.** The CDC wrappers of Phase 6b are the asynchronous form of the same adapter ports, so every adapter
works in the core clock or behind the CDC.

**Packaging.** One generated wrapper per bus (`g2d_top_<bus>`) from a small description, plus an IP-XACT component
(register map from spec/regs.yaml, port list) for Vivado / Platform Designer, a FuseSoC core file and a Bender.yml.

**Verification.** Each adapter gets its own cocotb bench (protocol checker in dv/sva, randomized bus BFM, the CSR
model as reference); the L3 environment runs its smoke set once through every adapter. The PULP-based Cheshire
bridge stays as the reference until `g2d_axi2reg` passes the same SoC-level test (fpga/sim).

## Timing and area (deferred to v1.7, user 2026-10-26)

At the v1.6 approval the user accepted the v1.6 timing and LUT growth for now and asked for it to be solved later.
Not fixed in v1.6 / v1.6b; v1.6b may add pipeline stages where it must to meet 50 MHz, but it does not take on the
area / timing clean-up.

| ID | Item | Numbers (G2D OOC, xc7k325t, 50 MHz) | Planned |
|---|---|---|---|
| T-1 | Timing: the slack fell from +5.441 ns (v1.5) to +1.972 ns (v1.6); worst path `g2d_pix` source format → texel / ramp mux → `g2d_blend` S0 stencil terms (43 logic levels, 62 % routing), next the family S2 adds (Q-RTL-10) | WNS +1.972 ns; ASIC target 600 MHz is far from these depths | v1.7 timing work (with Q-RTL-1 / 6 / 7 / 8): register the stencil terms, split the family S2 adds, then OOC sweeps at higher targets |
| T-2 | Area: G2D 50 640 LUT (v1.5 43 607, +7 033; `g2d_blend` 9 613 with the family divider), 74 DSP (+10), 60 BRAM tiles. v1.6b: 53 313 LUT (+2 673), 138 DSP (+64: colour matrix, YUV conversion), 62.5 BRAM tiles, WNS +0.862 ns. The full SoC no longer fits the v1.5 ~91.5 % estimate untrimmed | +16 % LUT in v1.6, +5 % in v1.6b | v1.7 board package: trimmed FPGA configuration (parameters to leave out families / Gaussian / YUV / colour unit), share the divider with other units where the pipeline allows. **v1.7 (2026-10-27):** parameter `FEATURES` built (OOC 100 MHz: every unit 53 979 LUT, none 44 801); the trimmed Cheshire + full G2D implementation fits: 168 794 LUT (82.8 %), 49 460 slices (97.1 %), 113 754 FF (27.9 %), 205.5 BRAM tiles (46.2 %), 167 DSP, WNS +0.092 ns at the 50 MHz SoC clock; G2D 59 425 LUT in context (Cheshire 94 776, DDR 13 222). No unit left out (plan question 2); the slice packing (97 %) is the margin to watch. |

## Driver robustness (external code review, recorded 2026-10-24)

Not hardware features: driver work found by an external review of `sw/driver/vg_lite`. The hardware arithmetic was
not at issue; the findings concern driver memory management, cache coherency and error recovery. Each finding was
checked against the code on `main` at `6cedd54` (line numbers as found there; the review's numbers were
approximate). **Fixed in v1.6** (docs/PROGRAMMING.md §1, §1.1; `sw/tests/test_robust.c`, which fails 12 of 60 checks
on the v1.5 driver and D-1 alone with 732 differing pixels). Found on the way and fixed with them: the driver never
invalidated GPU-written targets before the CPU read them (now after `vg_lite_finish`, and in
`vg_lite_flush_mapped_buffer`), and `recover()` released the scratch of in-flight buffers before the soft reset.

Note: the Cheshire configuration flushes and invalidates the whole D-cache on `fence` (OPEN_QUESTIONS Q-FPGA-6),
which hides D-1 on the FPGA; it surfaces on an ASIC / SoC integration with range-based cache maintenance.

| ID | Severity | Where | Problem | Fix |
|---|---|---|---|---|
| D-1 | High | `drv_core.c` `drv_scratch()` (l. 186–214), `kick()` (l. 108–115) | A request that does not fit the 256 KB scratch arena gets a dedicated allocation (`b->extra`). `kick()` cleans only the command buffer and the arena; the extra allocation's header is cleaned when it is made, its payload (written afterwards: edges, ramps, CLUTs, converted images) never. On a non-coherent system with range cleans the GPU can read stale or partial edge data for large path / stroke draws. | Track every extra allocation of a submission and clean each range before the kick (or allocate extras non-cacheable). Review the invalidate side for buffers the GPU writes and the CPU reads later. |
| D-2 | High | `drv_buffer.c` `vg_lite_free()` (l. 131–145); same pattern in `drv_path.c` `drop_cache()` (l. 77–87) | `drv_flush(1)` is called, but `mem_free()` runs even if the flush failed (timeout): the GPU may still access the memory, which can be reallocated meanwhile (corruption). | On a failed flush do not free: reset the GPU and confirm it is idle, then free; if that cannot be confirmed, quarantine the buffer (deferred-free list or intentional leak) and return the error. |
| D-3 | Medium | `drv_core.c` `vg_lite_init()` (l. 350–359) | The command buffer and the arena of each slot are allocated back to back; if the second allocation fails, the first address is not yet stored in `g_drv.cb[i].cpu`, so `vg_lite_close()` cannot free it (leak). | Record each successful allocation in its structure at once; one cleanup path on failure; `vg_lite_init()` cleans up after itself instead of relying on `vg_lite_close()`. |
| D-4 | Medium | `drv_grad.c` `write_ramp()` (l. 23–28); `drv_core.c` `vg_lite_close()` (l. 375–392) | `write_ramp()` overwrites the ramp even if `drv_flush(1)` failed; `vg_lite_close()` ignores the flush error and frees everything. Invisible in normal operation, serious on a hardware stall, bus error or timeout. | Do not modify or free GPU-visible data until the GPU is verified stopped. |

**Common policy for D-2 and D-4.** One helper (e.g. `drv_quiesce()`) flushes, soft-resets on a timeout and returns a
status; every path that frees or overwrites GPU-visible memory gates on it. A sticky fault state makes later calls
fail cleanly after an unrecoverable timeout.

**Tests to add.**
- Fault injection: force `drv_flush()` to time out (HAL hook in the model backend) and check that no free or
  overwrite happens and the error is reported.
- Allocator failure injection at each allocation point of `vg_lite_init()`, with a leak check afterwards.
- Non-coherent cache model (D-1): a model-backend HAL that keeps a CPU-side copy of every allocation, makes the
  GPU see only ranges explicitly cleaned, and flags any GPU read of a dirty line.

## Driver performance on small CPUs (v1.8, 2026-11-12)

**Done in v2.0 step A (2026-11-12):** the unit circle is cached per vertex count in the stroke state (bit-identical
output; vectors_0: 10 474 instead of 100 896 sin / cos calls).

| ID | Where | Observation | Proposal |
|---|---|---|---|
| D-5 | `sw/lib/geom/g2d_geom.c` `disc()` / `circle_n()` | Round caps and joins become discs of `circle_n` vertices, each vertex one `g2d_geom_sincos_deg` call (§9.7); under a perspective path matrix `disc_tol` shrinks the tolerance, and with a disc corner behind the eye it returns 0, which means 1024 vertices. The FPGA test scene `vectors_0` (seed 1) needs 194 discs and 100 896 sin/cos calls (host count with gdb); on the SoC simulation's CVA6 at 50 MHz (~0.25 instructions per cycle with DRAM-resident data) that is ~2 s of CPU time in one `vg_lite_draw`, the GPU idle. Bit-exact, only slow. | The vertices of a disc depend only on `n` and `r`: cache the unit circle per `n` (at most 1017 tables, or the few `n` actually used, filled on first use with the same polynomial, so bit-exact), then scale and offset per disc. Removes the sin/cos cost from every round join / cap. Measure on the board before building. |

## Display controller (separate optional project, user 2026-11-12)

Not part of the G2D IP: GPU IPs of this class ship without a display controller (the SoC has its own). Moved out of
the main line on 2026-11-12 (docs/ROADMAP_V2.md §1.0c); the withdrawn v1.9 plan (branch `v1.9-display`, deleted) is
the starting point. Candidate work, as its own IP or project after v2.0:

| ID | Item |
|---|---|
| DC-1 | Spec `spec/display.md` and an own register map (`G2DC` ID, 4 KB window) through `scripts/gen_regs.py` |
| DC-2 | Programmable timing generator (active, porches, sync widths and polarities); pixel clock as its own clock domain with the line buffer as the crossing (720p needs 74.25 MHz) |
| DC-3 | Line-buffered fetch over an AXI4 read master: underrun repeats the previous line, counter, sticky status and interrupt |
| DC-4 | Layers: background colour, L0 opaque, L1 with per-pixel and global alpha (rendering.md `lerp8`); formats RGB565 / ARGB8888 / INDEX_8 with a 256-entry CLUT; integer up-scaler ×1..×4; per-layer window and stride |
| DC-5 | Shadowed registers with a FLIP strobe taking effect at the next frame (no tearing); frame counter; vsync and line-match interrupts |
| DC-6 | Golden model of the scan-out and a self-checking pin-capture bench (as the v1.2 VGA test), memory stalls forcing underruns |
| DC-7 | Genesys2: VGA pins (HDMI only after the board's HDMI path is checked); replaces the `axi_vga` fork; DOOM scanned out from its 320 × 200 INDEX_8 screen, LVGL on the monitor, `chs_ps2` frame counter replaced |
| DC-8 | Works with the G2D memory throttle (`MEM_QOS`) so the scan-out never starves |
