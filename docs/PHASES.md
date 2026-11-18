# Phase roadmap and current phase plan

The roadmap is collected from the phase references already in the specs (arch.md §2.1, verification-plan.md
§1/§3, bandwidth.md, TOOLS.md, OPEN_QUESTIONS.md). Each phase follows CLAUDE.md rules 1–4.

| Phase | Name | Content | Status |
|---|---|---|---|
| 0 | Spec | Specs, regs.yaml + generator, tools | done (`phase-0-done`) |
| 1 | Model | C++ golden model, driver geometry, test infrastructure | done (`phase-1-done`) |
| 2 | RTL core | AXI4-Lite/CSR integration, AXI4 master, command processor, rect fill, first L3 top tests | done (`phase-2-done`) |
| 3 | Blend / blit | Blend pipeline (dst read, 16 modes, alpha, coverage), image sampler, source cache, perspective | done (`phase-3-done`) |
| 4 | Path / gradient | Edge rasterizer, winding accumulator, AA, linear gradient + ramp, PRF-01 throughput | done (`phase-4-done`) |
| 5 | Verification | SYS tests, SVA, code + functional coverage, `scripts/regress.py`, mutation campaign (CDC moved to 6b, user 2026-10-16) | done (`phase-5-done`) |
| 6 | Driver / API | `vg_lite` driver, SW backend (model), RTL-sim backend, LVGL | done (`phase-6-done`) |
| 6b | CDC | CDC wrappers (`CFG_ASYNC`/`MEM_ASYNC = 1`): AXI4-Lite 5-channel async bridge, AXI4 gray-pointer async FIFOs, own verification (randomized clock ratios, reset). **Postponed until after the first FPGA tests (user 2026-10-17)** | |
| 7 | FPGA | Cheshire + Genesys2 integration, bare-metal bring-up (user 2026-10-17: Linux moved to 7b) | **in progress** (plan below) |
| 7b | Linux | Linux kernel driver, device tree, CVA6 Linux on Genesys2 | |
| 8 | ASIC | Synthesis, STA, area/power at 600 MHz | |
| 9 | UVM | Only on explicit request | |

From 2026-10-18 (user): the state up to Phase 7a is branch `v1`; further work is versioned v1.1, v1.2, ... up to
v2 (every open question answered, every missing feature built or closed). Phases 6b, 7b and 8 are folded into those
versions: docs/ROADMAP_V2.md.

---

## Phase 2 plan: RTL core (branch `phase-2-rtl-core`)

**Status: done (`phase-2-done`, approved 2026-10-15).** Answers 2026-10-15: Q1 a (draws the Phase 2 hardware cannot execute report
`BAD_STATE`), Q2 a (blend pipeline stays in Phase 3), Q3 a (own AXI4 memory model), Q4 branch name kept.

Plan adjustments made during the phase:
- `g2d_ram_1r1w` / `g2d_ram_sp` move to Phase 3, where the first SRAM user (source cache) appears. The
  Phase 2 FIFOs are small and use flops.
- CMD-05 (draw N+1 reads draw N) moves to Phase 3 with the first reading draw. Command fetch is not a
  draw read, and command buffers written by the GPU are a hazard (arch.md §5.3), so there is no Phase 2
  version of this test.
- The generated CSR block gets a `soft_clr_i` input: a soft reset returns every w1c field
  (IRQ_STATUS, ERR_STATUS) to its reset value, as arch.md §2.2 requires.

### Goal

A complete `g2d_top` that the host programs through AXI4-Lite. It fetches command buffers over AXI4,
parses NOP/LOAD_STATE/DRAW_RECT/DRAW_PATH/FENCE, validates every draw (rendering.md §13, all checks), and
executes **solid rect fills with blend NONE** (clear and solid fill, all 12 target formats, any scissor,
source global alpha applied to S) on the fast path at one AXI beat per clock. The scoreboard compares the
whole memory image with the golden model after every test. Blend, blit, path and gradient execution
come in Phases 3–4.

### Tests in scope (verification-plan.md §3)

CSR-04..06, AXI-01..06, CMD-01..05, FIL-01..04. CSR-01..03 are re-run on the integrated top through
AXI4-Lite. CMD-05 (draw N+1 reads draw N) needs a reading draw, so in Phase 2 it is tested with a chain
of fills followed by a command buffer written by an earlier draw (the fetch reads what the draw wrote).
The full render-to-texture version moves to Phase 3.

### RTL as built (`g2d_` prefix, one module per file)

| File | Content |
|---|---|
| `rtl/pkg/g2d_pkg.sv` | Opcodes, format helpers, div255/mul8, global alpha, ARGB32 → format compression |
| `rtl/common/g2d_fifo.sv`, `g2d_spill_reg.sv`, `g2d_rr_arb.sv` | Generic FIFO, register slice, round-robin arbiter |
| `rtl/csr/g2d_axil2reg.sv` | AXI4-Lite slave → register interface, SLVERR on unmapped |
| `rtl/csr/g2d_ctrl.sv` | Kick queue, STATUS, IRQ, first-error capture, halt (command side), soft-reset sequencer, FENCE_SEQ |
| `rtl/csr/g2d_perf.sv` | Performance counters + snapshot shadows |
| `rtl/csr/g2d_csr_top.sv` | CSR subsystem (the four blocks above + generated `g2d_csr_regs`, now with `soft_clr_i`) |
| `rtl/axi/g2d_axi_rd.sv`, `g2d_axi_wr.sv`, `g2d_axi_mst.sv` | Read engine (N clients), write engine (W may lead AW, drop bursts), wrapper |
| `rtl/cmdproc/g2d_cmd_fetch.sv` | Kick checks, credit-based prefetch, word stream |
| `rtl/cmdproc/g2d_cmd_parse.sv` | Decode, LOAD_STATE, draw validation/issue, FENCE |
| `rtl/cmdproc/g2d_state.sv` | 64 state registers |
| `rtl/cmdproc/g2d_draw_check.sv` | rendering.md §13 complete, `RESTRICT_P2` |
| `rtl/fill/g2d_fill.sv` | Rect fill fast path (clip, colour, rows, beats); the planned `g2d_rect_setup`/`g2d_fmt_pack` are folded into it and `g2d_pkg` |
| `rtl/top/g2d_core.sv`, `g2d_top.sv` | Integration; top with the arch.md §4.1 ports, async CDC = elaboration error until Phase 5 |

### Verification as built (`dv/`)

| File | Content |
|---|---|
| `dv/common/axi_mem.py` | AXI4 slave memory model with protocol monitor (Q3 a) |
| `dv/common/g2d_env.py` | L3 env: model, memory compare, white-box state compare, perf, PNG dumps, Phase 2 predictor |
| `dv/common/csr_if.py`, `runner.py`, `tbwrap.py` | CSR access, cocotb runner (random X init), generated port-renaming wrappers |
| `dv/tests/test_common.py`, `test_csr_top.py`, `test_axi_mst.py`, `test_draw_check.py`, `test_pkg.py`, `test_top.py` | Block and top benches (pytest entry points) |
| `dv/tb/g2d_pkg_tb.sv` | Harness for the package functions |

### Spec / docs / build changes

- `spec/arch.md`: internal interfaces (read client, write span), fill fast-path details, soft-reset sequence timing.
- `spec/verification-plan.md`: Phase 2 notes per test ID, the temporary feature boundary.
- `spec/regs.yaml`: only if a question below needs it (e.g. a temporary "unsupported" indication).
- `Makefile`: lint units for all new RTL (the top is linted at DATA_W 64 and 128), the new pytest benches in
  `make test`, RTL random suites in `make regress`.
- `docs/STATUS.md`, `docs/OPEN_QUESTIONS.md`, `docs/TOOLS.md` at phase end.

### Commit order (each commit builds and passes)

1. common blocks + package → 2. axil2reg + ctrl + perf (CSR-01..06 on the integrated CSR) →
3. AXI master + memory model + monitor (AXI-01..06) → 4. state + cmd fetch/parse + draw check (CMD-01..04, FIL-04) →
5. fill engine + format pack (FIL-01..03) → 6. top + L3 env (CMD-05, end-to-end clears, DATA_W 128) →
7. regress hook-up, docs, phase report.

### Assumptions (non-blocking, will proceed with these unless told otherwise)

- A1. Both DATA_W 64 and 128 are tested in Phase 2 (AXI-01 requires it); `make test` runs a short set
  at 128, `make regress` the full set.
- A2. The port-level CDC blocks are pass-through wires when `*_ASYNC = 0` (no optional register slice yet).
- A3. The read engine returns data in order per client, so no reorder buffer is needed. Out-of-order
  responses only happen across IDs, and the engine routes them by ARID.
- A4. The fill fast path is the only write producer in Phase 2. The general 1 px/clk pipeline (dst read,
  blend, coverage lerp) is built in Phase 3 next to it.
- A5. The IDLE IRQ fires on the transition into idle, including after a soft reset (arch.md §7 says
  "entering idle").
- A6. Fill throughput is measured informally in Phase 2 (beats/clk with an ideal memory). The formal
  PRF-01 check stays in Phase 4.

---

## Phase 3 plan: Blend / blit (branch `phase-3-blend-blit`)

**Status: done (`phase-3-done`, approved 2026-10-16).** Answers 2026-10-15: Q1 a (latency-hiding source cache), assumptions
A1–A12 accepted ("a, assumptions ok").

Plan adjustments made during the phase:
- The transform and divider are one module (`g2d_xform`, divider stages inside); the planned
  `g2d_pdiv` file does not exist.
- SOLID pixels also run through the paint chain (no texel lookups), so the pipeline has a single join
  and needs no second paint path; costs latency only.
- POINT sampling uses the bilinear datapath with zero fractions (one datapath for both filters).
- Source cache set index is an XOR fold of the line address (measured: direct indexing thrashed under
  rotation with power-of-two strides).
- The SRAM FIFO (`g2d_fifo_ram`) is a separate common block, used by the command prefetch, the cache
  pixel FIFO and the destination buffer.
- `SRC_CACHE_MISS` counts line fills (up to four per pixel), so the cache reports a 3-bit count.
- A DV bug found on the way: the AXI memory model ignored profile changes after start (fixed; Phase 2
  random_streams had run every round with its first profile).

### As built

| File | Content |
|---|---|
| `rtl/common/g2d_ram_1r1w.sv`, `g2d_ram_sp.sv`, `g2d_fifo_ram.sv` | SRAM wrappers (behavioural), SRAM FIFO with 2-entry flop output stage |
| `rtl/blend/g2d_blend.sv` | Global alpha, 16 modes, coverage blend, compress; 5 stages |
| `rtl/blit/g2d_xform.sv` | U, V, W and the exact perspective divider (29 restoring stages) |
| `rtl/blit/g2d_tex_addr.sv` | Source test, texel coordinates with clamping, byte addresses |
| `rtl/blit/g2d_src_cache.sv` | Latency-hiding 8 KB cache: 4 banks, hashed sets, LRU, lock counts, 128-pixel FIFO |
| `rtl/blit/g2d_texel.sv` | Expand, force opaque, colour keys, premultiply, bilinear, image MULTIPLY |
| `rtl/pix/g2d_walk.sv`, `g2d_dst_rd.sv`, `g2d_wcomb.sv`, `g2d_pix.sv` | Walker, destination reader, write combiner, general pipeline |
| `rtl/pkg/g2d_pkg.sv` | `fmt_expand` added |
| `rtl/cmdproc/g2d_draw_check.sv` | `RESTRICT` level (3: paths and gradients rejected) |
| `rtl/cmdproc/g2d_cmd_fetch.sv` | prefetch FIFO on SRAM |
| `rtl/top/g2d_core.sv`, `g2d_top.sv` | dispatch, drain wait, 3 read clients, write-port mux; `SRC_CACHE_BYTES` parameter |
| `dv/tests/test_blend.py`, `test_xform.py`, `test_sampler.py`, `test_src_cache.py` | L2 benches |
| `dv/tests/test_blit.py` | L3: BLD-01..03, BLT-01..06, CMD-05, SYS-P3, soft reset mid-blit, throughput |
| `dv/common/stream.py`, `g2d_top_util.py`, `g2d_coverage.py`, `scripts/coverage_report.py` | stream driver, shared L3 helpers, functional coverage |

### Goal

`g2d_top` executes every `DRAW_RECT` the spec allows: solid paint with all 16 blend modes and destination
global alpha, and image blits (12 source × 12 target formats, POINT/BILINEAR, affine and perspective,
colour key, SRC_PREMUL, FORCE_OPAQUE, image MULTIPLY, source global alpha), bit-exact against the golden
model, at the arch.md §6.1 compute targets (1 px/clk for blended rects and blits, including perspective
and bilinear). `DRAW_PATH` and `LINEAR_GRAD` paint stay `BAD_STATE` until Phase 4 (A1).

### Proposed pipeline (arch.md §6 gets the as-built version)

```
 parser ─► dispatch ─┬─► g2d_fill (SOLID + blend NONE, unchanged fast path) ───────────────┐
                     │                                                                        │ write
                     └─► drain wait (§5.3) ─► g2d_walk ─► g2d_xform ─► g2d_pdiv ─► g2d_tex ──┤ port
                           spans / 16×16 tiles  │  U,V,W     exact ÷     coverage test,   │    mux ─► axi_wr
                                                │ incremental            texel addr, ckey,│
                                                │                        premul, bilinear │
                                                ├─► g2d_dst_rd (read client 1) ──┐       │
                                                │                     g2d_src_cache (client 2)
                                                └──────────────► g2d_blend ◄─────┘
                                                                 src/dst global alpha, 16 modes,
                                                                 coverage lerp, compress
                                                                     └─► g2d_wcomb (2 × 256 B,
                                                                         trimmed bursts + strobes)
```

- **Dispatch.** SOLID + blend NONE stays on `g2d_fill`. Every other draw goes to the general pipeline.
  A general-pipeline draw always reads memory (destination or source), so it first waits until all earlier
  writes are acknowledged (arch.md §5.3, `PERF_DRAIN_CYC`). The two engines therefore never overlap and
  share the write port through a simple mux.
- **Walk.** Rect clipped against target and scissor (logic shared with `g2d_fill`). Spans of at most
  256 B, burst-aligned. Scanline order for SOLID and for translate/scale-only blits; 16×16 destination tiles
  (one tile row = one span) when `XF_B ≠ 0`, `XF_D ≠ 0` or `PERSP` (A3).
- **Transform.** `U, V, W` per pixel incrementally (`+2·XF_A` per x step, restart per span from a
  multiply), exact integers, so identical to rendering.md §7.1.
- **Perspective divider.** Unsigned radix-2 restoring divider, 29 quotient bits, fully pipelined, one
  per coordinate. `U < 0` or a quotient ≥ 2^29 can only give a texel outside the source rect
  (`sx1, sy1 ≤ 8192`), so the pixel is skipped without dividing. Result: exact for **all** inputs, so the
  "unspecified outside the contract" clause in rendering.md §7.1 can be replaced by an exact statement (A5).
- **Texel unit.** Coverage test (§7.4), 1 or 2×2 texel coordinates with clamping (§7.3), per-texel steps
  §5.2 (expand, force opaque, 4 colour keys, premultiply) on 4 texels per clock, bilinear, IMG_MUL.
- **Source cache** (read client 2): 8 KB, 64 B lines. 4 banks selected by (texel row parity, line
  parity), so the up-to-4 lines of a bilinear quad are read in one clock; tags in flops, LRU per set,
  invalidated per draw. Miss handling is **Q1**.
- **Destination reader** (read client 1): reads each span when the blend reads D (Phase 3: blend ≠ NONE;
  Phase 4 also cov < 255) into a 1 KB credit-based buffer, ahead of the blend stage (A4).
- **Blend.** Source global alpha (§5.3), destination expand + global alpha (§5.5), 16 modes (§5.6),
  coverage lerp (§4 step 5), compress (§3.4). Pipelined, 1 px/clk, registered stages sized for 600 MHz
  (still unverified, Q-RTL-1).
- **Write combiner.** Collects one span (double-buffered, 2 × 256 B), then issues one write request
  trimmed to the first..last written pixel, with zero strobes on skipped pixels. A span with no written
  pixel issues nothing.
- **SRAM wrappers** `g2d_ram_1r1w`, `g2d_ram_sp` (behavioural in `rtl/common`), used by the cache data
  banks, destination buffer, write combiner and (moved from flops) the command prefetch FIFO (Q-RTL-5).

### Files

| New / changed | Content |
|---|---|
| `rtl/common/g2d_ram_1r1w.sv`, `g2d_ram_sp.sv` | SRAM wrappers (behavioural) |
| `rtl/blend/g2d_blend.sv` | Global alpha, 16 modes, coverage lerp, compress |
| `rtl/blit/g2d_xform.sv`, `g2d_pdiv.sv`, `g2d_tex.sv`, `g2d_texel.sv`, `g2d_src_cache.sv` | Transform, divider, texel addressing/filter, per-texel steps, cache |
| `rtl/pix/g2d_walk.sv`, `g2d_dst_rd.sv`, `g2d_wcomb.sv`, `g2d_pix.sv` | Walker, destination reader, write combiner, general pipeline top |
| `rtl/pkg/g2d_pkg.sv` | `fmt_expand`, blend helpers, clip helper shared with `g2d_fill` |
| `rtl/cmdproc/g2d_draw_check.sv` | `RESTRICT_P2` bit → `RESTRICT` level (A1) |
| `rtl/cmdproc/g2d_cmd_fetch.sv` | prefetch FIFO on `g2d_ram_1r1w` |
| `rtl/top/g2d_core.sv` | dispatch, drain wait, 3 read clients, write-port mux, DRAIN_CYC / SRC_CACHE_MISS counters |
| `dv/tests/test_blend.py`, `test_texel.py`, `test_xform.py`, `test_src_cache.py`, `test_ram.py` | L2 benches vs the model's stage functions |
| `dv/tests/test_blit.py`, `test_top.py` | L3: BLD-01..03 end to end, BLT-01..06, CMD-05, random mixed streams |
| `dv/common/coverage.py` | `cg_blend`, `cg_blend_alpha`, `cg_blit`, `cg_blit_dst`, `cg_blit_opts` → `build/coverage/report.md` |
| `dv/common/g2d_env.py`, `scenes.py` | Phase 3 restriction predictor, blit scene generator (driver geometry via `sw/lib/geom`) |
| `model/bindings/g2d_model.cpp` | extra stage-function bindings if a bench needs them (no semantic change planned) |
| specs / docs | arch.md §4.3, §6 (as built), rendering.md §7.1 (A5), §13.1 (A1), verification-plan.md P3 mapping, bandwidth.md (if Q1 = a), STATUS, OPEN_QUESTIONS, TOOLS |

### Commit order (each commit builds and passes)

1. SRAM wrappers + prefetch FIFO on them → 2. blend unit + test_blend (BLD-01/02 at block level) →
3. transform, divider, texel unit + tests → 4. source cache + test_src_cache →
5. walker, destination reader, write combiner, `g2d_pix`, dispatch, `RESTRICT` level; L3 blended solid rects →
6. blits end to end (BLT-01..06, CMD-05, BLD-03, DATA_W 128) → 7. coverage collector, regress, mutation
checks, throughput numbers, docs, phase report.

### Question (blocking)

**Q1. Source cache miss handling.**
- **(a) Latency-hiding cache (recommended).** The tag stage runs ahead of the data stage: misses are
  requested immediately, pixels wait in a FIFO sized for the memory latency (~90 clk at 600 MHz), and
  lines stay locked until the pixels that need them have read them. Keeps 1 px/clk under latency.
  Untransformed copies use the same cache in scanline order (64 B source bursts, 4 B/px at ARGB8888, half
  of the 64-bit port), so no separate streaming path is needed; arch.md §6.4 and bandwidth.md §5
  ("untransformed copies bypass the cache") are updated.
- **(b) Blocking cache + separate streaming path for untransformed copies.** Simpler cache, but a
  transformed blit stalls a full read latency on every miss (estimate: well under 0.5 px/clk at 90 clk
  latency), and the streaming path is a second source datapath to build and verify.

### Assumptions (non-blocking, will proceed with these unless told otherwise)

- A1. `g2d_draw_check.RESTRICT_P2` becomes an integer `RESTRICT` (2 = Phase 2 rules, 3 = Phase 3 rules:
  `DRAW_PATH` and `SRC = LINEAR_GRAD` are `BAD_STATE`, 0 = none; top default 3). The testbench predictor
  follows it. Phase 4 sets it to 0 and removes it.
- A2. `g2d_fill` stays as is and only takes SOLID + blend NONE.
- A3. Tile traversal only for rotation/shear/perspective; translate and scale-only blits walk scanlines.
- A4. The destination reader reads whole spans, also over pixels the source test skips. This is only
  extra read traffic (the memory image is identical); the write side is trimmed and strobed.
- A5. rendering.md §7.1: "the hardware must give the exact quotient for |U|,|V| < 2^47 ... larger values
  are unspecified" becomes "exact for all inputs" (the divider above). Model unchanged.
- A6. Colour key semantics stay as specified (OPEN_QUESTIONS Q-API-2 is re-dated to Phase 6; LVGL does not
  use colour keys and there is no public evidence either way).
- A7. The coverage lerp (cov < 255) is built and block-tested now; end-to-end use comes with paths in Phase 4.
- A8. Throughput is measured informally (ideal memory and 90-clk latency) for blended rects, translate
  blits, rotated bilinear and perspective blits; the formal PRF-01 check stays in Phase 4.
- A9. Read clients have fixed ARIDs: 0 command fetch, 1 destination, 2 source cache.
- A10. Functional coverage is collected in Python for the Phase 3 groups (`cg_blend`, `cg_blend_alpha`,
  `cg_blit`, `cg_blit_dst`, `cg_blit_opts`); exit criterion: 100 % or gaps documented
  (verification-plan.md §7).
- A11. `make test` stays under 5 minutes (short fixed-seed blit runs); full-length runs go to `make regress`.
- A12. Branch `phase-3-blend-blit`.

---

## Phase 4 plan: Path / gradient (branch `phase-4-path-gradient`)

**Status: done (`phase-4-done`, approved 2026-10-16; K-1 accepted).** Answers 2026-10-16: Q1 a (full support), assumptions A1–A10
accepted, with conditions (user):
- Staging: band re-reads from memory first (many edges in total, ≤ 512 per band); the multi-pass handling
  of a single row with > 512 active edges last, as its own commit with directed PTH-05 variants (exactly
  512, 513, 1024, all edges on one row).
- Golden model structure identical to the RTL: same add-and-compare edge stepping, same once-per-band
  division, same rounding. (Claude: the stepping is exact integer arithmetic, so the direct
  ceil-division formula moves to the independent reference `refimpl.py` and a test checks that both
  agree.)
- Deterministic passes: identical results for any split of the edges across passes; a test renders the
  same path with different forced split points.
- A1: the edge and ramp clients are internal and share the single AXI4 master (round-robin per burst, no
  starvation); deadlock argument in arch.md; directed test with long latency and back-pressure on edge
  fetch, destination read and write-back together.
- A4: the driver/HAL flushes CPU caches before every kick (the GPU does not snoop): add to a programming
  guide (`docs/PROGRAMMING.md`).
- A8: if paths miss the 90 % target at 90-clock latency, edge-channel prefetch depth is tuned first.
- A3: skipping the destination read for fully covered interior pixels (SRC mode) is logged as a
  proposal only.
- Commit 1: the divider is tested over the full signed range, divisor edge values and back-to-back issue
  against a plain integer reference.

Plan adjustments made during the phase:
- The divider is a generic `g2d_sdiv` in `rtl/common`; the planned `g2d_edge_div` file does not exist.
  The edge loader runs two (sample step and row step), the rasterizer one (band start).
- The stored crossing state advances by a row step (`32·dx = qr·dy + rr`), not by replaying 16 sample
  steps, so a row's state write-back is a single add/compare (no hazard between consecutive rows).
- Band halving was not built: a band with more active edges than one pass is processed one row at a time
  with several passes per segment (the general mechanism; halving would only be a speed-up).
- `CHUNK` (edges per pass, default the buffer size) is a parameter so tests can force small passes.
- The gradient index is computed right after the walker and its 8-bit result rides through the paint
  chain as the tag (instead of carrying x, y through the 256-deep cache FIFO).
- Transitional `RESTRICT = 4` (gradients only) between the path and the gradient integration; removed in
  commit 5 together with its testbench predictor.
- DV infrastructure: shared clock/reset start-up (`dv/common/clkrst.py`, also in the L3 environment) so
  the asynchronous reset acts before the first clock edge with random initial values; generic read-client
  responder with strobes (`dv/common/read_client.py`).
- Q-PERF-2 (A9): two cheap knobs adopted (write-combiner span queue 8 → 32, cache lookahead 128 → 256);
  PRF-01: the glyph-sized path misses A8 (0.83 px/clk), known issue K-1 with a proposal (Q-PERF-3).

### As built

| File | Content |
|---|---|
| `rtl/common/g2d_sdiv.sv` | Pipelined signed floor divider (QW + 2 stages, ovf flag) |
| `rtl/raster/g2d_edge_load.sv` | Edge fetch (read client 3), clamp, scale, orient, drop, sample/row steps |
| `rtl/raster/g2d_raster.sv` | Edge buffer, bands, active list, band division, step pipeline, passes, streaming and multi-pass rows |
| `rtl/raster/g2d_wind_acc.sv` | Double-buffered winding accumulator (2 × 16 banks), sweep, fill rules, coverage |
| `rtl/gradient/g2d_grad.sv`, `g2d_ramp.sv` | Gradient index; ramp RAM + loader (read client 4) + lookup |
| `rtl/pix/g2d_pix.sv`, `g2d_walk.sv`, `g2d_wcomb.sv` | Path and gradient integration, clipped-rect export, span queue 32 |
| `rtl/blit/g2d_src_cache.sv` | Lookahead 256, simulation-only stall counters |
| `rtl/top/g2d_core.sv`, `rtl/cmdproc/g2d_draw_check.sv` | Read clients 3/4, path dispatch, ramp coherence pulses; `RESTRICT` removed |
| `model/include/g2d/raster.hpp`, `model/src/raster.cpp` | Golden-model `PathRaster` in the RTL's structure |
| `dv/tests/test_sdiv.py`, `test_edge_load.py`, `test_raster.py`, `test_grad.py` | L2 benches |
| `dv/tests/test_path.py` | L3: PTH-01..06, GRD-01, paths × paint × blend, random streams, AXI-08, coverage matrix, PRF-01 |
| `model/tests/test_raster_model.py` | Model rasterizer vs direct formula, literal sample test and the edge-load spec |
| `dv/common/clkrst.py`, `read_client.py`, `g2d_coverage.py` | Reset start-up, read responder, path/gradient coverage groups |
| `docs/PROGRAMMING.md` | Driver/HAL rules (cache maintenance, ramp/edge/command buffer coherence) |

### Goal

`g2d_top` executes every draw of the v1 spec: `DRAW_PATH` (rendering.md §8: edge list, both fill rules,
16-sample AA and 1-sample, guard-band clamp, mod-256 winding) and `LINEAR_GRAD` paint (§10.1: T function,
PAD/REPEAT/REFLECT, 256-entry ramp) on paths and rects, with every blend mode and global alpha, bit-exact
against the golden model. The `RESTRICT` rule is removed. PRF-01 throughput numbers are measured and
recorded; Q-PERF-2 (blits under latency) is investigated.

### Proposed design

**Exact crossings without a per-sample divide.** For an edge (1/32-px units, `Ya < Yb`, `dy = Yb − Ya`)
the crossing threshold at sample row `Y` is `xt(Y) = ceil(N(Y)/dy)` with `N(Y) = Xa·dy + (Y − Ya)·dx`
(rendering.md §8.3). Sample rows are `S = 2` apart with AA (also across pixel rows) and `S = 32` without.
Keeping `N = xt·dy − r` (`0 ≤ r < dy`) and `S·dx = qs·dy + rs` (constant per edge), one step is
`xt += qs + (rs > r)`, `r += (rs > r ? dy : 0) − rs`: one add/compare per sample row, exact. The pixel of
sample row `j` is `p = (xt − xoff_j + 31) >> 5` (`xoff_j = 2·q[j] + 1`, or 16 without AA). Divisions are
only needed per edge at load (`qs, rs`) and per edge per band (initial `xt, r`), both on pipelined
restoring dividers (1 edge/clk).

**Rasterizer `g2d_raster`** (new `rtl/raster/`):
1. **Edge load** (read client 3): stream the list, clamp (§8.1), scale to 1/32, orient, drop horizontal
   edges, edges entirely below/above the clipped bbox rows and edges entirely right of it (they can never
   pass at or left of a sample). Per edge: `dy, dir, qs, rs` into the **edge buffer** (SRAM, 512 edges).
2. **Bands** of 16 pixel rows: scan the edge buffer, put the edges overlapping the band into an active
   index list, compute each one's `(xt, r)` at the band's first sample row (extrapolated if the edge
   starts lower; events are masked by `Ya ≤ Y < Yb`).
3. **Row × segment** (segments of `RAST_SEG_W` = 256 px): every active edge goes through a 16-stage
   step pipeline (one stage per sample row), one edge per clock, producing up to 16 events
   `delta[j][p] += dir` (p clamped to the segment start: "left of the segment" = base winding).
   The **winding accumulator** has 16 banks (one per sample row) × 256 × 8 bit, so every edge does at most
   one read-modify-write per bank.
4. **Sweep**: 16 running 8-bit sums along x, inside test per sample (§8.3), `cov = (n·255 + 8) >> 4`
   (§8.4), one pixel per clock, clearing the accumulator behind it. The accumulator is **double-buffered**
   (A6) so edge processing of the next row-segment overlaps the sweep: rate ≈ 1 px/clk while the active
   edges per row ≤ 256.
5. After the last segment of a row, the stepped `(xt, r)` is written back for the next row.

**Pipeline integration** (`g2d_pix`): a path draw walks its clipped bbox in scanline order (`g2d_walk`, no
tiles); the raster emits `cov` in exactly that pixel order, joined with the paint result. Paths always
read the destination (cov < 255 at edges; A3). `cov = 0` pixels are not written (write combiner strobes,
as for skipped blit pixels).

**Gradient `g2d_grad`** (new `rtl/gradient/`), in the paint chain next to the texel unit: `T` (64-bit,
two 32×15 multiplies), `i = T >> 17`, spread, ramp RAM lookup. Ramp: 256 × 32 SRAM loaded through read
client 4 (1 KB) at the start of a gradient draw, unless it holds the same `GRAD_RAMP_ADDR` and no write
and no new kick happened since it was loaded (A4).

### Question (blocking)

**Q1. Edge lists longer than the 512-edge buffer, and rows with many active edges** (PTH-05 requires
lists larger than the buffer).
- **(a) Full support, recommended.** If the list has more than 512 surviving edges, the edge loader
  re-streams it from memory per band and keeps only the band's active edges. If a band has more than 512
  active edges, the band height halves (16 → 8 → … → 1 row). If a single pixel row still has more than
  512 active edges, that row's segments are processed in several passes over the list (the accumulator
  is additive). Correct for every input; slow only for pathological paths.
- **(b) Contract limit.** At most 512 edges active in any pixel row. The driver checks this when it builds
  the edge list and returns `VG_LITE_NOT_SUPPORT` above it; the hardware result above the limit is
  unspecified (but never hangs). Simpler hardware; any number of total edges still works through
  per-band streaming.

### Assumptions (non-blocking, will proceed with these unless told otherwise)

- A1. Read clients: 3 edge loader, 4 gradient ramp (`ID_W = 4` default suffices).
- A2. `RESTRICT` is removed from `g2d_draw_check`, the testbench predictor and rendering.md §13.1.
- A3. Path draws always read the destination (no "fully covered row" shortcut in v1).
- A4. Ramp coherence: the ramp RAM is valid only within one kicked buffer (the CPU writes ramps before
  the kick, commands.md §3) and is reloaded after any GPU write since its load or a `GRAD_RAMP_ADDR`
  change. arch.md §6.4 gets this rule.
- A5. Bands of 16 pixel rows; `RAST_SEG_W` = 256 stays fixed.
- A6. The winding accumulator is double-buffered: 8 KB instead of the 4 KB in arch.md §6.4 (total on-chip
  memory ≈ 31 KB with a ~10 KB edge buffer of 512 × 160-bit entries, since each entry also keeps its
  stepping state).
- A7. Gradient paint is supported on `DRAW_RECT` too (rendering.md allows it), with the same unit.
- A8. PRF-01 (verification-plan.md): cycles per pixel for fill, blit and path (glyph-sized and large
  shapes, ≤ 8 and more crossings per row) and gradient rects, with ideal memory and 90-clock latency,
  recorded in arch.md §6.1 and STATUS. Pass criterion: ideal-memory rates ≥ 0.9 of the arch.md §6.1
  compute targets for paths with ≤ 8 crossing edges per row; others recorded.
- A9. Q-PERF-2: add stall-cause counters inside the source cache (testbench-visible, not CSRs), measure,
  try at most the cheap knobs (pixel FIFO depth, cache ways); larger changes go to the report as a proposal.
- A10. Functional coverage adds `cg_path`, `cg_paint_path`, `cg_grad`; exit criterion 100 % or gaps
  documented.

### Files

| New / changed | Content |
|---|---|
| `rtl/raster/g2d_edge_load.sv`, `g2d_raster.sv`, `g2d_wind_acc.sv`, `g2d_edge_div.sv` | Edge loader + setup, band/row/segment control and step pipeline, accumulator + sweep, pipelined divider |
| `rtl/gradient/g2d_grad.sv`, `g2d_ramp.sv` | T function, spread, ramp RAM + loader |
| `rtl/pix/g2d_pix.sv`, `g2d_walk.sv` | path draws (scanline bbox walk), coverage join, gradient paint, destination read for paths |
| `rtl/top/g2d_core.sv` | DRAW_PATH dispatch, read clients 3/4 |
| `rtl/cmdproc/g2d_draw_check.sv`, `g2d_cmd_parse.sv` | `RESTRICT` removed; edge address/count to the engine |
| `dv/tests/test_raster.py`, `test_grad.py` | L2: crossings/coverage vs `gm.coverage_row`, gradient index vs `gm.grad_index` |
| `dv/tests/test_path.py` | L3: PTH-01..06, GRD-01, paths × paint × blend, random streams, PRF-01 |
| `dv/common/g2d_coverage.py`, `g2d_env.py` | `cg_path`, `cg_paint_path`, `cg_grad`; predictor without `RESTRICT` |
| specs / docs | arch.md §6 (raster, gradient as built, memory sizes), rendering.md §13.1, verification-plan.md P4 mapping, bandwidth.md §6, STATUS, OPEN_QUESTIONS |

### Commit order (each commit builds and passes)

1. pipelined divider + edge loader/setup + L2 test → 2. accumulator + sweep + step pipeline (`g2d_raster`)
+ test_raster vs `gm.coverage_row` → 3. path integration in `g2d_pix`/core, `RESTRICT` removed for paths,
L3 PTH tests → 4. gradient unit + ramp + test_grad → 5. gradient integration, GRD-01, `RESTRICT` gone →
6. streaming (Q1) + PTH-05 → 7. PRF-01, Q-PERF-2, coverage, regress, docs, phase report.

---

## Phase 5 plan: Verification and hardening (branch `phase-5-verification`)

**Status: done (`phase-5-done`, approved 2026-10-16).** Answers 2026-10-16 (user): Q1 a (CDC wrappers move to their own phase 6b, before
FPGA timing work); Q2: SYS tests 300 seeds × 2 configurations as proposed; full-length block and L3
benches with 10 seeds, seeds not used before (101–110); at most 16 cores (parallel jobs ≤ 16).
Assumptions A1–A10 accepted.

### Goal

No new features. Bring the whole GPU to the verification-plan.md §7 P5 exit criteria: SYS-01..05 green
over at least 300 seeds, SVA protocol and invariant checks bound into every bench, code coverage
measured (line ≥ 95 % on `rtl/`, exceptions justified), all functional coverage groups at 100 % or gaps
justified, a systematic mutation campaign, a parallel regression runner, and `make test` back under the
5-minute smoke target. RTL bugs found here are fixed on this branch (`fix:` commits, each with the test
that found it).

### Work items

1. **SVA** (`dv/sva/`, `bind` modules, compiled into the simulations only): AXI4 master and AXI4-Lite
   slave protocol (VALID held, payload stable while stalled, INCR only, no 4 KB crossing, AxLEN within
   MAX_BURST_BYTES, WLAST on the last beat, outstanding limits, no AR/AW after a soft reset starts);
   internal valid/ready streams on the main interfaces (payload stable while stalled); invariants (FIFO
   never over/under-flows, cache lock counts never wrap, accumulator buffer state machine, divider
   contract, write-combiner beat counts, credits never exceed their buffers). First step: show that a
   deliberately violated property fires in Verilator (support for each construct verified, not assumed).
2. **SYS tests** (`dv/tests/test_sys.py`): SYS-01 random streams of 1..200 mixed draws (fills, blends,
   blits of every kind, paths with and without gradients, fences, NOPs, state noise, several targets);
   SYS-02 a grid of latency / back-pressure / reorder profiles on SYS-01 seeds; SYS-03 hard and soft
   reset at random points, the next stream exact; SYS-04 AXI errors, window violations and malformed
   commands mid-stream; SYS-05 DATA_W 64 vs 128: identical memory images for identical seeds (output
   digests compared across configurations, as in the Phase 4 split test). SYS-06 (LVGL replay) needs
   Phase 6 and stays there.
3. **Functional coverage**: the remaining groups of verification-plan.md §4: `cg_fill`, `cg_axi`,
   `cg_errors`, `cg_align`.
4. **Code coverage**: Verilator `--coverage-line --coverage-toggle` on all benches, merged with
   `verilator_coverage`, report in `build/coverage/code/`; goal line ≥ 95 % on `rtl/` excluding generated
   read-mux defaults and simulation-only code; every exception listed.
5. **`scripts/regress.py`**: parallel runs (default and maximum 16 jobs, user), one seed per run, pass/fail
   table, failure triage grouped by first error with the exact rerun command, merged functional and
   code coverage, JUnit XML; `make regress` calls it.
6. **`make test` < 5 min**: smaller default sizes for the long benches (paths, rasterizer splits), full
   sizes in `make regress`.
7. **Mutation campaign** (`scripts/mutate.py`): a mutant list per RTL block (at least 3 each, the earlier
   phases' mutants included), run against the benches in parallel, results table; equivalent mutants
   justified.
8. **K-1 alternative** (user, Phase 4 sign-off): evaluate prefetching the next draw's edge list on the
   existing edge channel while the current draw renders — measurement and analysis only (how much of
   the per-draw edge load could be hidden, what it costs), result in the report; no RTL change unless
   approved.

### Questions (blocking)

**Q1. CDC wrappers (`CFG_ASYNC` / `MEM_ASYNC = 1`)** are in the Phase 5 row of the roadmap, but they are
new design work, and the first FPGA bring-up runs the GPU in the Cheshire SoC clock domain (decision
2026-10-15, single clock).
- **(a) Move them to their own phase before FPGA timing work, recommended.** Phase 5 stays pure
  verification of the existing design; the CDC phase then gets its own verification (randomized clock
  ratios, gray-code and reset checks).
- **(b) Keep them in Phase 5.** Built first, then verified together with everything else.

**Q2. Seed budget for the exit criterion "SYS green with ≥ 300 seeds".** A full-length run of every bench
takes 20–30 min per seed, too long for 300 seeds. Proposal:
- SYS-01..05 are written as short runs (≈ 1–2 min per seed and configuration); 300 seeds × 2
  configurations (DATA_W 64 / 128) on 32 parallel jobs ≈ 30–40 min;
- the full block and L3 benches in full-length mode with 20 seeds (≈ 30 min on 32 jobs);
- 500 seeds (nightly scale) only on request.
Approve these numbers, or give others.

### Assumptions (non-blocking, will proceed with these unless told otherwise)

- A1. SVA lives in `dv/sva/` (bound from the benches), not in `rtl/`; Verilator `--assert` is enabled
  for all benches.
- A2. Code coverage excludes the generated CSR block's default read cases and `ifndef SYNTHESIS` code.
- A3. RTL bugs found in Phase 5 are fixed here; each fix lists the finding test, and STATUS gets a bug
  table (ID, found by, fixed in).
- A4. SYS tests compare against the golden model as every L3 test does (whole memory, registers, state,
  performance counters); SYS-05 additionally compares the two configurations' output digests.
- A5. `make test` keeps one short run of every bench; `make regress` runs `scripts/regress.py` with the
  Q2 numbers.
- A6. The functional coverage report merges L3 runs across seeds and configurations (as in Phase 4).
- A7. No new features and no performance work (K-1 evaluation is analysis only).
- A8. Tool versions do not change; nothing new is installed (`verilator_coverage` ships with Verilator).
- A9. Mutation runs use the short benches with `G2D_WAIT_LIMIT` (fast failure of hanging mutants).
- A10. Branch `phase-5-verification`.

### Commit order (each commit builds and passes)

1. SVA infrastructure + construct check + AXI/AXI-Lite protocol properties → 2. internal stream and
invariant properties → 3. `scripts/regress.py` + `make test` trimming → 4. SYS-01/02/05 → 5. SYS-03/04 →
6. remaining functional coverage groups → 7. code coverage flow + closing holes → 8. mutation campaign →
9. K-1 evaluation, full regression, docs, phase report.

---

## Phase 6 plan: Driver / API (branch `phase-6-driver`)

**Status: done (`phase-6-done`, approved 2026-10-17; results in docs/STATUS.md).** Answers 2026-10-16 (user): Q1 c (record / replay for regressions and a live Verilator
C++ harness); Q2 a (`git clone --branch v9.6.0` into `~/tools/src/lvgl-9.6.0`); Q3 a (LVGL's software renderer
is the screenshot reference); Q4 a plus a list: driver-only software support where no RTL change is needed,
LVGL's software renderer for the rest, and every feature LVGL would need from future hardware recorded in a
list (docs/FEATURES_LATER.md) with where LVGL uses it. Assumptions A1-A7 stand.

Goal: the `vg_lite.h` subset of spec/api-subset.md on our own command format and HAL, running LVGL's
VG-Lite draw unit, with a software backend (golden model) and an RTL-simulation backend.

Work items, in order (spec → model → RTL → testbench → regression → docs, CLAUDE.md rule 6; no RTL change
is planned in this phase):
1. **HAL boundary** `sw/hal/g2d_hal.h`: CSR read/write, contiguous memory (CPU pointer + 32-bit GPU
   address), cache clean / invalidate, wait for IRQ or poll, time. The driver never touches anything else.
2. **Driver** `sw/driver/vg_lite/` (C99, no libm): context and command buffers (encoding from the
   generated `g2d_regs.h` and commands.md), state shadow re-emitted after soft reset (PROGRAMMING.md §5),
   kick / fence / `vg_lite_finish`, buffers (allocate, map, upload, flush), clear / blit / blit_rect /
   copy_image, matrices (own sin/cos polynomial, rendering.md §9) incl. `vg_lite_get_transform_matrix`,
   scissor, blend, global alpha, colour key, paths (init / upload / append / draw through sw/lib/geom),
   gradients (legacy 16-stop and extended linear), info / feature queries, driver-side validation and
   error codes (rendering.md §13).
3. **Backends**
   - SW: a C++ HAL over the golden model (`gm::Gpu`): a kick executes at once; optional recorder writes
     every kick (command words + the memory it reads) to a replay file.
   - RTL replay: cocotb SYS-06 replays recorded files on `g2d_top` (both configurations), bit-exact against
     the model.
   - RTL live (Q1): a Verilator C++ harness (no cocotb) with a C++ AXI4-Lite master and AXI4 memory, the
     driver linked in, so kick-queue back-pressure, fences and IRQ/poll run against the real RTL.
   - Bare-metal (Cheshire) HAL: MMIO + polling, compile-checked with a riscv64 bare-metal GCC; run in
     Phase 7.
4. **LVGL** v9.6.0 (latest release tag, read from github.com/lvgl/lvgl 2026-10-16) host build, no display
   hardware (memory framebuffer, PNG dumps), `LV_USE_DRAW_VG_LITE` on our driver + SW backend; demos at
   1280×720 in ARGB8888 and RGB565.
5. **API questions** Q-API-1, 2, 3, 4, 5, 8, 9 resolved from LVGL's VG-Lite draw unit; specs updated.
6. **Tests** (self-checking, CLAUDE.md rule 7)
   - driver unit tests (ctest): every v1 function incl. error codes and NOT_SUPPORT paths;
   - API scenes through driver + SW backend against the independent Python reference (dv/common/refimpl.py)
     and the float reference (tolerances of rendering.md §14), not against the model the backend runs;
   - LVGL screenshots against LVGL's own software renderer, tolerance written into verification-plan.md
     before the first comparison (Q3);
   - SYS-06: recorded LVGL frames replayed on the RTL, bit-exact, both configurations;
   - RTL live smoke: a few API scenes and one LVGL frame through the live harness.
7. **Performance**: cycles per LVGL frame from the RTL replay → frame rate at 600 MHz (ASIC) and 50 MHz
   (FPGA) against the 720p60 target (bandwidth.md); Q-RTL-2 (kick prefetch) revisited with these streams.
8. **Docs**: PROGRAMMING.md (driver and HAL), api-subset.md status column, TOOLS.md (LVGL), STATUS.

Files to create: `sw/hal/g2d_hal.h`, `sw/hal/model/` (C++ SW backend + recorder), `sw/hal/baremetal/`,
`sw/driver/vg_lite/*.{c,h}`, `sw/CMakeLists.txt`, `sw/tests/` (ctest, pytest), `sw/apps/lvgl/` (lv_conf.h,
host main, CMake), `scripts/fetch_lvgl.sh`, `dv/tests/test_sys06.py`, `dv/live/` (Verilator C++ harness, if
Q1 b/c). Files to change: Makefile (`sw`, `sw-test`, `lvgl` targets), spec/api-subset.md, spec/rendering.md
(Q-API outcomes), spec/verification-plan.md (SYS-06, LVGL tolerance), docs/PROGRAMMING.md, docs/TOOLS.md,
docs/OPEN_QUESTIONS.md, docs/STATUS.md.

Assumptions (non-blocking; continue unless you object):
- A1. Driver in C99 without libm; trigonometry from our own polynomial, deterministic double arithmetic as in
  sw/lib/geom.
- A2. Linux kernel driver: Phase 7 with the FPGA bring-up; Phase 6 delivers the HAL boundary and the
  bare-metal HAL (compile-only).
- A3. LVGL on the host without SDL or a display: framebuffer to PNG.
- A4. The model is optimised for speed (Q-PERF-1) only if an LVGL frame takes more than ~5 s on the SW
  backend.
- A5. Demos: `lv_demo_widgets` and `lv_demo_benchmark` scenes.
- A6. `make test` stays under 5 minutes: API tests and one short replay in `make test`; LVGL screenshots,
  full SYS-06 and the live harness in `make regress`.
- A7. Branch `phase-6-driver`.

Questions (blocking):
- Q1. RTL-simulation backend: (a) record / replay only (SYS-06 in cocotb); (b) live Verilator C++ harness
  only; (c) both (replay for regressions, live harness for driver ↔ hardware timing: kick queue full, fences,
  IRQ).
- Q2. LVGL source: (a) `git clone --branch v9.6.0` into `~/tools/src/lvgl-9.6.0` (shared tools, documented,
  not in the repo); (b) git submodule in `sw/third_party/lvgl`; (c) vendored copy in the repo.
- Q3. Reference for LVGL screenshots: (a) LVGL's software renderer only; (b) also LVGL's VG-Lite simulator on
  ThorVG (needs a ThorVG build in ~/tools).
- Q4. LVGL features outside the v1 subset (to be listed from the draw unit's source): (a) add driver-only
  (software) support where no RTL change is needed (e.g. arc flattening, stroke to fill outline) and let
  LVGL's software renderer take the rest; (b) strict v1 subset, everything else goes to LVGL's software
  renderer; (c) also plan RTL additions (a later phase).

---

## Phase 7 plan: FPGA bring-up, bare metal (branch `phase-7-fpga`)

**Status: in progress, board run pending (user).** Plan approved by the user 2026-10-17.

Plan adjustments made during the phase:
- SoC simulation: Verilator (as planned) after the first try failed through my own error (linted `cheshire_soc`
  with its all-zero default `Cfg`); Vivado xsim crashes on `cheshire_soc` and is not used. Own testbench instead of
  Cheshire's, which needs vendor flash / EEPROM models.
- The Genesys2 top is generated from Cheshire's by `fpga/scripts/gen_top.py` (no modified Cheshire file in the
  repository); Cheshire's IP scripts run unchanged with the Digilent board files through `vivado_boards.tcl`.
- No AXI ID converter: Cheshire's `AxiMstIdWidth` = 3 = G2D `ID_W`. The PLIC needs a patch (59 sources).
- Bare-metal test reuses `sw/tests/vgl_scene.c` (newlib on the UART, scripts linked in) instead of a new
  scene program; checks are CRC-32 per buffer (no framebuffer dumps over JTAG needed).
- Fixes found on the way: B-7.1 (stroke discs used libm trig), B-7.2 (xsim-incompatible loop index).

### Context

Phases 0–6 are done and merged (`main` = `fe47051`, tag `phase-6-done`). The G2D IP is verified in simulation
(bit-exact vs the golden model, 300-seed SYS gates, LVGL frames on the live RTL), but it has never been
synthesized or run on silicon/FPGA. Timing (Q-RTL-1/6/8), real memory latency (Q-FPGA-4), the Cheshire
integration details (Q-FPGA-2/3/6) and the bare-metal HAL hooks are all unverified. Phase 7 puts G2D into
the Cheshire SoC on the Genesys2 and shows the same pictures come out of real hardware.

User decisions (2026-10-17): Phase 7 = bare metal only, Linux → new Phase 7b; CDC (6b) after the first FPGA
tests (single clock, 50 MHz SoC domain); CSR port through Cheshire's AXI4 external slave + PULP converters
for now, and **plan v2 bus adapters** so the IP drops into any platform unchanged; Cheshire + OpenOCD may be
installed into `~/tools`; **the board is on another machine**: I deliver bitstream, binaries and scripts,
the user runs them there and pastes logs.

### Working rules for this phase (user, 2026-10-17)

- **Start of every session/step:** check active shells, background tasks and monitors (TaskList, `ps` for
  verilator/pytest/make/vivado). Kill only the ones this project started and no longer needs. Checked at
  plan time: no background tasks; one Vivado GUI (pid 1414041, up 1 d 10 h, started from a gnome-terminal)
  belongs to the user and is left alone.
- **Progress always in files** so another session can pick up: `docs/HANDOFF.md` (live: current step, what is
  running and where its logs go, done, next) updated at every step and commit; `docs/STATUS.md` "In progress"
  kept current; folded into STATUS at phase end (as in Phase 6).
- **CPU limit 16 → 32 parallel jobs** (machine has 40): `scripts/regress.py` `MAX_JOBS = 32` and its docstring,
  Makefile defaults, verification-plan.md §6 ("16 jobs" → 32), decision log entry; Vivado `-jobs` / 
  `maxThreads` capped at 32 too. First commit of the phase (`chore:`).
- **Long operations in the background:** tests, regressions, Vivado runs and Cheshire builds run as
  background shells or subagents with a Monitor on their log; the main session keeps working and reads the
  result when notified. Results and log paths are written into `docs/HANDOFF.md`.
- These rules are also saved in the assistant's memory. CLAUDE.md is not changed by the assistant (edit blocked by the permission check); the user adds them there if wanted.

### Goal / exit criteria

1. `make fpga` builds a Genesys2 bitstream of Cheshire + G2D (`DATA_W 64`) at 50 MHz with **positive slack**,
   no latches, no critical CDC warnings; utilization and timing reports summarized in STATUS.
2. A Verilator (or Vivado xsim if needed) simulation of the **Cheshire + G2D SoC** runs the bare-metal smoke
   binary over the real Cheshire interconnect before anything goes to the board (catches address map,
   ID width, interrupt and cache issues on this machine).
3. On the board (user's machine): bare-metal test binary passes the API scene set and one LVGL frame
   **bit-exact** against golden images produced on the host by the SW backend (CLAUDE.md rule 7);
   fence IRQ through the PLIC and polling both work; soft-reset/error recovery works.
4. Perf counters on the board: busy cycles per scene/frame, RD/WR stall, measured read latency →
   Q-FPGA-4 closed, bandwidth.md §8 gets an FPGA column.
5. Docs: OPEN_QUESTIONS Q-FPGA-1..6 resolved or re-dated, TOOLS.md, PROGRAMMING.md §7, new
   `docs/FPGA.md` (bring-up guide for the board machine), STATUS.

### Work items (spec → model → RTL → test → regression → docs)

1. **Tools** (install into `~/tools`, log in `~/tools/README.md` + `docs/TOOLS.md`): Cheshire `git clone` at a
   pinned commit into `~/tools/src/cheshire` (re-verify the 2026-10-14 facts at that commit), OpenOCD from
   source into `~/tools/openocd`. `make check-tools` gets an optional `fpga` group. No system packages.
2. **Re-verify Cheshire facts** (read its source, record in OPEN_QUESTIONS): Genesys2 config
   (`gen_cheshire_xilinx_cfg`): AddrWidth, AxiDataWidth, AXI ID/user widths, `AxiExtNumMst/Slv`,
   `NumExtIntrs`, free address-map slot for the CSR window, DRAM range, LLC/SPM behaviour, CVA6 cache
   maintenance options (uncached region vs explicit flush).
3. **Integration RTL** (`fpga/cheshire/`, not in `rtl/`): `g2d_cheshire.sv` wrapper:
   - CSR: Cheshire `axi_ext_slv` → PULP `axi_dw_converter` (64→32) → `axi_to_axi_lite` → `g2d_top.s_axil_*`.
   - Memory: `g2d_top.m_axi_*` → Cheshire `axi_ext_mst` (ID width adapt via PULP `axi_iw_converter` if
     Cheshire's ext-master ID width < our `ID_W`; user signals tied 0; struct ↔ flat-port mapping).
   - `irq_o` → one Cheshire external interrupt (PLIC).
   - A Cheshire config package/patch applied by script (`fpga/scripts/`), Cheshire itself stays external.
4. **SRAM mapping**: check Vivado infers BRAM/LUTRAM from `g2d_ram_1r1w` / `g2d_ram_sp` (behavioural); if
   not, `fpga/` mapping variants (xpm_memory), never vendor primitives in `rtl/` (arch.md §11).
5. **Synthesis + timing at 50 MHz** (`fpga/genesys2/`, Vivado 2024.2 batch Tcl, `make fpga`): G2D
   out-of-context first (utilization + timing of the IP alone, also run at a higher target to give a
   first view of Q-RTL-1/6/8), then the full SoC. Timing fixes only if 50 MHz fails; any RTL change is a
   `fix:` with the full `make test` + affected gates re-run.
6. **SoC simulation** (`fpga/sim/`): Cheshire's own testbench flow with G2D attached, preloading the
   bare-metal ELF; check: CSR read of `ID`/`VERSION`, a clear + fence, one blit, one path, IRQ.
7. **Bare-metal software** (`sw/hal/baremetal/`, `sw/apps/baremetal/`):
   - real `G2D_CSR_BASE` from the address map, pool placed in GPU-reachable DRAM (linker script section),
     CVA6 D-cache clean/invalidate hooks (or uncached pool, whichever Cheshire supports, item 2), delay /
     timeout from CLINT `mtime`;
   - `g2d_fpga_test` binary: runs the API scene set + one recorded LVGL frame (inputs generated on the host,
     linked in), prints per-scene CRC32 + perf counters over UART;
   - host script `scripts/fpga_check.py`: produces the expected CRCs/PNGs from the SW backend (same driver
     code, same inputs), compares the UART log and optional GDB framebuffer dumps (`dump binary memory`
     over JTAG/OpenOCD) bit-exactly, writes a report.
   - LVGL bare-metal demo build (widgets frame) as a stretch item inside the phase.
8. **Board package for the other machine** (`make fpga-package` → `build/fpga/pkg/`, gitignored):
   bitstream, ELFs, OpenOCD/GDB scripts, `run_board.sh` (program FPGA, load ELF, capture UART + dumps),
   expected results; `docs/FPGA.md` lists exactly what the user runs and which logs to paste back. No
   machine-specific paths committed (tool paths via env variables).
9. **v2 bus adapter plan (documentation only in Phase 7, per user)**: `docs/FEATURES_LATER.md` /
   new section in arch.md §4: platform-neutral adapter set so `g2d_top` integrates without touching the IP:
   CSR side APB, AXI4 (any data width), AXI4-Lite, regbus/TileLink-UL, Wishbone over the existing
   bus-agnostic `g2d_reg_req_t`; memory side AXI4 data-width/ID-width/user adapters, optional AHB/TileLink;
   IRQ level/pulse option; all our own RTL, each with its own cocotb bench. Built in a later phase.
10. **Regression**: `make test` unchanged (< 5 min) plus a lint of `fpga/cheshire/*.sv`; the SoC simulation
    joins `make regress`; board results recorded by hand in STATUS (cannot run here).

### Files

New: `fpga/cheshire/g2d_cheshire.sv`, `fpga/cheshire/*.patch|cfg`, `fpga/genesys2/*.tcl|*.xdc`,
`fpga/scripts/{setup_cheshire.sh,build.sh,package.sh,run_board.sh,*.cfg,*.gdb}`, `fpga/sim/`,
`sw/apps/baremetal/` (test binary, linker script), `scripts/fpga_check.py`, `docs/FPGA.md`.
Changed: `sw/hal/baremetal/g2d_hal_baremetal.c` (real hooks), `Makefile` (`fpga`, `fpga-sim`,
`fpga-package`, `check-tools` fpga group), `docs/{TOOLS,OPEN_QUESTIONS,PROGRAMMING,STATUS,PHASES,FEATURES_LATER}.md`,
`spec/{arch.md §2/§4/§10, bandwidth.md §8, verification-plan.md L5/P7}`, `.gitignore`. `rtl/` only for `fix:` commits.

### Commit order

1. tools + Cheshire facts re-verified (docs) → 2. wrapper + lint → 3. G2D OOC synthesis/timing →
4. SoC sim with G2D smoke → 5. bare-metal HAL hooks + test binary + `fpga_check.py` (validated against the
SoC sim) → 6. full bitstream + board package + FPGA.md → 7. board results (user logs), perf, v2 adapter
plan, docs, phase report → STOP (wait rule).

### Assumptions (non-blocking)

- A1. `DATA_W 64`, defaults otherwise (`RD/WR_OUTSTANDING 8`); 128 only if Cheshire's Genesys2 config is 128.
- A2. No display output (Q-FPGA-5): results via UART CRCs + JTAG framebuffer dumps.
- A3. Phase 6 open items accepted as recommended unless you object: Q-API-14 (b) centre-sampled border,
  Q-API-3 refinement, Q-TEST-3 (`make smoke` = `make test`), corrected test bounds; Q-RTL-9 and F-1/F-4 stay
  proposals. Q-API-14 (b) is implemented early in Phase 7 only if you confirm; otherwise left as is.
- A4. Cheshire's bus/IP libraries (Solderpad/Apache) are used only in `fpga/`, fetched by bender, not copied.
- A5. Branch `phase-7-fpga`; Linux = Phase 7b; CDC = after 7b's first tests unless timing needs it earlier.

### Verification

- Here: `make lint`, `make test`, `make fpga` (timing summary must show WNS ≥ 0), `make fpga-sim`
  (SoC sim passes the smoke and matches `fpga_check.py` expectations bit-exactly).
- Board machine: `run_board.sh` → UART log + dumps → `scripts/fpga_check.py --log ...` reports every scene
  PASS bit-exact; perf numbers into bandwidth.md §8.

---

## v1.1 plan: CDC (old Phase 6b; branch `v1.1`)

**Status: done, approved 2026-10-18, merged into `main`, tag `v1.1-done`** (results: docs/STATUS.md "Test results (v1.1 CDC)"). Roadmap and answers: docs/ROADMAP_V2.md. Research:
docs/research/notes/cdc_bus.md.

### Goal / exit criteria

1. `CFG_ASYNC = 1` / `MEM_ASYNC = 1` build and work: the AXI4-Lite config port and the AXI4 memory port run on
   their own clocks (any ratio), the core on `clk_i`. `= 0` stays exactly as v1 (wires, no added latency).
2. Our own RTL only (Cummings gray-pointer FIFO, same structure as PULP `cdc_fifo_gray`); lint clean, no latches,
   Spyglass-CDC-friendly (each crossing = gray pointer through a synchronizer cell, or FIFO storage read only
   behind a synchronized pointer).
3. Verification: unit bench of the FIFO under random co-prime clock ratios with a simulation-only random
   synchronizer delay; SVA (gray step, no overflow / underflow, FIFOs empty at `soft_clr`); formal proof of the
   FIFO (SymbiYosys, multiclock); the L3 top bench and SYS-01..04 run with both ports asynchronous at random
   clock ratios, bit-exact against the golden model as before.
4. FPGA: Cheshire + G2D bitstream with G2D core at 100 MHz and both ports at the 50 MHz SoC clock, timing met,
   CDC constraints generated; SoC simulation of the scene set bit-exact on the async build; board package has both
   bitstreams.
5. Docs: arch.md §2/§3, verification-plan (CDC-xx, FPG-05), FPGA.md, OPEN_QUESTIONS (Q-RTL-4 closed), STATUS.

### Design

- `rtl/cdc/g2d_sync.sv`: `STAGES`-flop synchronizer with reset (default 2; 3 for ASIC). The one cell an ASIC
  flow swaps for a library synchronizer. Simulation option `G2D_CDC_JITTER` (define, never in synthesis): the
  first stage randomly holds its old value for a cycle when the input changed (whole-bus, so a gray code only ever
  shows a real past value): models a synchronizer that takes N or N+1 (or more) cycles.
- `rtl/cdc/g2d_cdc_fifo.sv`: dual-clock FIFO, `WIDTH`, `LOG_DEPTH` (≥ 1), `SYNC_STAGES`; binary + gray pointers
  with an extra wrap bit, full / empty from the local pointer against the synchronized remote gray pointer;
  storage = flop array written in the source domain, read in the destination domain at the read pointer
  (stable while the entry is visible). Valid/ready on both sides.
- `rtl/cdc/g2d_cdc_axil.sv` (5 FIFOs: AW, W, B, AR, R; depth 2) and `rtl/cdc/g2d_cdc_axi.sv` (5 FIFOs, depth 8,
  `MEM_CDC_LOG_DEPTH` parameter): depth covers the synchronizer round trip, not outstanding transactions (AXI
  handshakes bound those).
- `g2d_top`: `CFG_ASYNC` puts `g2d_cdc_axil` between `s_axil_*` (cfg domain) and the core; `MEM_ASYNC` puts
  `g2d_cdc_axi` between the core and `m_axi_*` (mem domain). New parameters `SYNC_STAGES` (2), `CFG_CDC_LOG_DEPTH`
  (1), `MEM_CDC_LOG_DEPTH` (3). `irq_o` stays a core-domain level output (integrator synchronises it; Q-RTL-4).
- Reset rule (spec): the three resets are asserted together (asynchronously) and de-asserted each synchronously
  to its own clock; a one-sided reset is not supported. Soft reset needs nothing new: the AXI master drains every
  outstanding transaction before `soft_clr`, so the memory-port FIFOs are empty then (SVA).

### Work items (commit order; each builds and passes `make lint` + `make test`)

1. docs: this plan, roadmap answers, tools (OSS CAD Suite), gitignore of local-only research.
2. spec: arch.md §2.1 / §2.2 / §3 (parameters, reset rule, latency of the crossing), verification-plan CDC tests.
3. feat: `g2d_sync`, `g2d_cdc_fifo` + unit bench `dv/tests/test_cdc.py` (random clock ratios, jitter define,
   back-pressure on both sides, reset) + SVA bind; lint variants.
4. test: formal proof `dv/formal/g2d_cdc_fifo.sby` (`make formal`, uses `$OSS_CAD`).
5. feat: `g2d_cdc_axil`, `g2d_cdc_axi`, `g2d_top` integration; env: CSR master on `clk_cfg`, AXI memory on `clk_mem`,
   random periods per seed; test_top / test_sys configurations `async` (short in `make test`, full in regress).
6. feat(fpga): Genesys2 top with a G2D core clock (MMCM in `fpga/` only), XDC for the crossings
   (`ASYNC_REG`, `set_max_delay -datapath_only`, `set_bus_skew`), `make fpga FPGA_ASYNC=1`; SoC sim on the async
   build; board package with both bitstreams.
7. regression (`make regress`), docs, STATUS, report → STOP (wait rule).

### Assumptions (non-blocking)

- A1. Default `SYNC_STAGES = 2` (FPGA at ≤ 100 MHz has ample MTBF; ASIC value decided with the library data).
- A2. FIFO storage is flops (≤ 8 entries); no SRAM.
- A3. Crossing latency (≈ 2–3 destination cycles per direction) is not hidden; the core's outstanding limits
  (8 / 8) already cover it at 100 / 50 MHz.
- A4. Formal proves: no overflow / underflow, gray pointers change by one bit per write / read, data order
  (a tagged-word proof); full functional equivalence is left to simulation.

## v1.2 plan: board readiness (branch `v1.2-board-ready`)

**Status: done 2026-10-19 in the SoC simulation, waiting for approval** (results: docs/STATUS.md "Test results
(v1.2)"). User 2026-10-18: no bitstreams until the end of v1.7, so the trimmed / new bitstreams and the new board
package (exit criteria 5–6, bitstream parts) move there. Roadmap row: docs/ROADMAP_V2.md §1 v1.2.
Branch from `main` at `fa46467` (v1.1 merged, tag `v1.1-done`).

### Goal / exit criteria

Everything the first board session needs is ready, checked in simulation, and in one board package:

1. **Latency model (Q-FPGA-4 prediction)**: the SoC sim has a programmable delay stage in front of the DRAM model;
   busy cycles of every scene kind at 0 / 10 / 20 / 40 cycles of added read latency are in docs/FPGA.md and
   bandwidth.md §8 (prediction column), all scenes still bit-exact at every delay.
2. **Board-side measurements prepared**: the board ELF has a single-command latency probe (one small read-bound
   draw, busy cycles via PERF_*) and a sweep of Cheshire's `USE_RAM_DELAY` registers (`chs_xilinx` cfg regs at
   `0x4300_0000`: AR delay `+0x18`, R delay `+0x1C`, read from Cheshire's `chs_xilinx_regs.hjson`), each step
   re-checks the scene CRCs. Both run in the SoC sim first (the sim build adds the same `axi_fifo_delay_dyn`).
3. **LVGL frame on the target** (Phase 7 exit criterion 3, missing from the board ELF): one recorded SYS-06 frame
   replayed by the bare-metal test, CRC checked by `fpga_check.py`.
4. **VGA output test (Q-FPGA-5)**: G2D renders into the framebuffer Cheshire's `axi_vga` scans out (640×480
   RGB565). SoC sim: the testbench captures the VGA pixel stream of one frame (hsync / vsync / 5-6-5 pins) and
   compares it bit-exactly with the SW-backend image. Board: framebuffer CRC over UART + a visible demo (scene
   set and the LVGL frame, held on screen for N s each).
5. **Trimmed Cheshire variant (K-2 option)**: USB, iDMA, bus-error units and I2C off, VGA / UART / JTAG / SPI host /
   GPIO / LLC kept; PLIC source number of G2D re-checked (sources move when units are removed); LUT use measured
   (expected ~87.7 % → ~76 %), timing met, SoC sim bit-exact.
6. **`run_board.sh` hardening (Q-FPGA-8 procedure)**: stop `hw_server` after programming before OpenOCD starts,
   record `lsusb`, `/dev/serial/by-id`, OpenOCD / gdb versions into `results/`; reset-recovery steps (from
   Cheshire's docs) printed on timeout; `--vga` and `--sweep` modes; new board package with all bitstreams.
7. `make lint / test / regress` green; docs (FPGA.md, bandwidth.md §8, OPEN_QUESTIONS, STATUS, TOOLS) current.

### Work items (commit order; each builds and passes `make lint` + `make test`)

1. docs: this plan, HANDOFF.
2. feat(fpga-sim): `tb_g2d_chs.sv` delay stage (PULP `axi_fifo_delay_dyn`, already a Cheshire dependency, AR / R
   delay from plusargs) + `socsim.py --rd-delay N`; sweep job over the six kinds × {0, 10, 20, 40}; results →
   FPGA.md §5, bandwidth.md §8 prediction column.
3. feat(sw): bare-metal latency probe + RAM-delay sweep (`G2D_SWEEP` build, cfg-reg writes), `fpga_scenes.py` /
   `fpga_check.py` extended (sweep sections, per-step CRCs); checked in the SoC sim with the delay stage driven by
   the same register offsets.
4. feat(sw): LVGL frame in the board ELF (recorded SYS-06 command stream + expected CRC from the SW backend).
5. feat(fpga): VGA test: bare-metal `axi_vga` setup (640×480, `clk_div` from the 50 MHz SoC clock), G2D target at
   the VGA framebuffer, cache clean before scan-out; `tb_g2d_chs.sv` VGA capture → PPM, `scripts/vga_check.py`
   (capture vs SW-backend image, bit-exact on the 5-6-5 pins); board demo mode.
6. feat(fpga): trimmed-Cheshire variant (`FPGA_TRIM=1`: config in `g2d_chs_pkg.sv`, top defines in `gen_top.py`,
   PLIC source re-derived), OOC + full implementation, SoC sim of the scene kinds + VGA on the trimmed build.
7. feat(fpga): `run_board.sh` hardening + `--vga` / `--sweep`, `package.sh` with all bitstreams; FPGA.md §3
   board procedure.
8. regression (`make regress`), docs, STATUS, report → STOP (wait rule).

### Assumptions (non-blocking; I continue with these unless you object)

- A1. Sim delay values 0 / 10 / 20 / 40 SoC cycles on AR and R (the research range: ~8–12 LLC hit, ~20–35 miss);
  the delay stage sits between the LLC and the DRAM model, as `USE_RAM_DELAY` does on the board.
- A2. Board sweep: added AR + R delay {0, 8, 16, 32, 64} cycles, each step runs one scene per kind + the probe
  (not the full 12-scene set) to keep the board run under ~10 min.
- A3. VGA: 640×480 at `clk_div` 2 (25.0 MHz pixel clock from 50 MHz, ~59.5 Hz frame rate, inside VESA tolerance
  of common monitors; standard 640×480 porches / negative syncs); framebuffer in the GPU pool, RGB565.
- A4. LVGL frame: the smallest recorded SYS-06 frame that fits 640×480 (so the same frame is the VGA demo);
  otherwise a 640×480 crop of a widgets frame rendered on the host with the SW backend.
- A5. The VGA sim capture runs on the single-clock build only (one frame ≈ 840 k SoC cycles ≈ 2 min of sim);
  the async and trimmed builds run the scene kinds.
- A6. Nothing in `rtl/` changes in v1.2 (FPGA integration, sim, software and scripts only); regs.yaml unchanged.

### Questions (one batch)

1. **Trimmed Cheshire**: (a) turn off USB, iDMA, bus-error units, I2C; keep VGA, SPI host (SD card), GPIO, UART,
   JTAG, LLC. OK, or should SPI host / GPIO go as well? (b) Which builds get it: proposal = a third bitstream
   "async + trimmed", the two v1 / v1.1 bitstreams stay unchanged as references.
2. **VGA board demo**: is a VGA monitor available at the board machine? The board check itself is the UART CRC;
   the picture is confirmed by you (a photo is enough).
3. **Disk**: one more Vivado implementation + three SoC sim builds need ~4–5 GB.
   May I delete regenerable outputs: `build/dv` (4.9 GB, rebuilt by `make test`) and the Vivado project
   directories `build/fpga/impl*/proj` (bitstreams, reports and the board package are kept)?

## v1.3 plan: driver-only API completion (branch `v1.3-api`, draft 2026-10-18)

**Status: done 2026-10-19, waiting for approval** (results: docs/STATUS.md "Test results (v1.3)"). Approved and started 2026-10-19 (v1.2 approved, merged into `main`, tag `v1.2-done`; answers below). Roadmap row: docs/ROADMAP_V2.md §1 v1.3. User
direction 2026-10-18: new features first, no bitstreams until the end of v1.7. Nothing in `rtl/` changes in v1.3: every
item is driver geometry / API (`sw/driver/vg_lite`, `sw/lib/geom`), so the RTL regressions only re-confirm.

### Items (work order per item: rendering.md / api-subset.md → driver + geom → tests vs an independent reference →
docs → commit)

1. **F-5 dashed strokes** (`vg_lite_set_stroke` dash_pattern / pattern_count / dash_phase, `dash_reset` field):
   OpenVG 1.1 rule (public Khronos spec; VGLite's API follows OpenVG): on/off lengths from the start of each subpath
   (or continued across subpaths unless reset), phase offset, odd count → last entry ignored, negative entries → 0,
   all-zero pattern → solid. Every dash is an open sub-polyline capped with the path's cap style; joins only inside a
   dash. Split happens on the path-space flattened polyline (§9.7 step 1), so dashes stay exact under the matrix.
   Tests: exact-region check of `test_stroke.py` extended to dashes (distance to the dashed polyline), LVGL vector
   demo `draw_lines` against LVGL's software renderer (tolerance as today), Skia cross-check (`SkDashPathEffect`).
2. **Arcs** (`VLC_OP_SCCWARC/SCWARC/LCCWARC/LCWARC` + `_REL`, 5 arguments `rh, rv, rot, x, y`, as OpenVG 1.1 §8.4 and
   LVGL's `vlc_op_arg_len`): endpoint parameterisation → centre form (OpenVG / SVG F.6.5), out-of-range radii scaled up
   (F.6.6), zero radius → line; flattened like the curves (segment count from the tolerance), both for fills and
   strokes. `vg_lite_init_arc_path` = `vg_lite_init_path` that accepts arc opcodes (assumption A2). Tests: area /
   boundary vs the analytic ellipse, SVG-style reference cases, Skia `arcTo` cross-check.
3. **Perspective path matrix** (`m[2][0..1] ≠ 0` or `m[2][2] ≠ 1`): flatten in path space with a tolerance from the
   local projective scale (adaptive subdivision until the projected chord error ≤ 1/32 px), project every point
   (`x/w, y/w`); lines stay lines, so the polygon is exact. Points with `w ≤ ε` → clip the polygon against the
   `w = ε` plane before projecting (as for perspective blits, rendering.md §7). Strokes under perspective: pieces are
   built in path space (§9.7) and projected the same way. Tests: projected high-resolution reference polygon,
   coverage criteria of §12; blit-vs-path consistency (a perspective image and a perspective rectangle path agree).
4. **`VLC_OP_BREAK`**: no public semantics (header: opcode value only). Assumption A3: ends the current subpath
   without closing it for strokes (an implicit MOVE); for fills subpaths are closed anyway. Listed as UNVERIFIED.
5. **Command buffer / memory API**: `vg_lite_set_command_buffer(physical, size)` (after init: the driver uses the
   caller's 64-byte aligned buffer instead of its own; `NOT_ALIGNED` / `INVALID_ARGUMENT` checks),
   `vg_lite_set_memory_pool` (one pool here: `VG_LITE_MEMORY_POOL_1` accepted, others `NOT_SUPPORT`),
   `vg_lite_frame_delimiter(flag, stall)` (flush + finish; `stall_flag` waits for idle; IRQ already raised by the
   FENCE). Tests in `sw/tests/test_driver.c`.
6. **Spec notes** (no behaviour change): Q-API-4 (premultiplied callers use MULTIPLY), Q-API-8 (OpenVG draws
   nothing on a singular matrix; we return INVALID_ARGUMENT, listed as a difference), Q-API-15 (OpenVG A4 order and
   the LVGL simulator's `v<<4` listed as differences); Q-TEST-3 closed (`make smoke` exists = `make test`).
7. Regression (`make test`, `make regress` incl. SYS-06 with new recordings: dashed / arc / perspective scenes on the
   RTL), docs (api-subset.md status, rendering.md §9.2 / §9.7 / new §9.8, VERIFICATION_MATRIX, STATUS) → STOP.

### Questions

1. Q-API-14 (b) (centre-sampled AA border for fractional pattern rectangles): build it in v1.3? (Still undecided.)
2. Dash semantics for an odd `pattern_count`: OpenVG (ignore the last entry) or SVG (repeat the list)? Proposal:
   OpenVG.
3. `VLC_OP_BREAK` = "end subpath without closing" (A3) acceptable, or leave it `NOT_SUPPORT` until evidence exists?

**Answers (user, 2026-10-19)**: 1. yes, build Q-API-14 (b) (listed known difference, edge-mask test); 2. OpenVG (odd
count: last entry ignored); 3. A3 accepted (BREAK ends the subpath without closing, marked UNVERIFIED).

### Assumptions (non-blocking)

- A1. Arc direction (CW / CCW) in path coordinates as OpenVG (CCW = positive angle in the path's own coordinate
  system; with LVGL's y-down screens that appears clockwise on screen).
- A2. `vg_lite_init_arc_path` behaves as `vg_lite_init_path` (same fields; arcs accepted by both).
- A4. Driver-side flattening limits (n ≤ 256 per curve) also apply to arcs, per quarter turn.

## Side project "DOOM on Cheshire + G2D" — restart (branch `doom`, 2026-10-27)

**Restarted (user, 2026-10-27)**: "play doom using our GPU, USB input, VGA output; I will test it in FPGA". The 2026-10-20
plan below stays the base; changes since then:

- **GPU level L2 by default** (v1.4 built indexed sources): DOOM's 320×200 8-bit screen is the G2D source as `INDEX_8`
  with the 256-entry CLUT (DOOM's current palette, reloaded when it changes: damage / pickup flashes), point-sampled
  2 × 2.4 into the 640×480 RGB565 back buffer, then a page flip. The CPU does no per-pixel present work.
- **Bitstream**: the v1.7 trimmed single-clock build is at 99.0 % slices. DOOM build = trimmed Cheshire + full G2D +
  `chs_ps2`; if it does not place, leave G2D units out with `FEATURES` in the v1.7 order (colour unit, Gaussian, YUV,
  blend families; DOOM uses none of them).
- **Vblank**: `axi_vga` has no status; `chs_ps2` also counts `vsync_o` edges (frame counter + vblank flag) so the flip
  can wait for vblank (no tearing).
- **LLC**: Cheshire's boot ROM leaves the 128 KiB LLC as scratchpad; the DOOM startup switches it to cache (measured
  both ways in the SoC sim / on the board).

### Steps (restart)

| Step | Work | Exit |
|---|---|---|
| D1 | `scripts/fetch_doom.sh` (doomgeneric e257a09, Freedoom v0.13.0 release, pinned + SHA-256), host port `sw/apps/doom/` (doomgeneric platform layer on our vg_lite driver, SW / model backend), demo playback for N tics, per-frame CRCs vs a CPU-only present | `make doom-host`, `sw/tests/test_doom.py` bit-exact |
| D2 | Present path: CLUT + I8 blit 2 × 2.4 into the back buffer, page flip (double buffer), vblank wait | in D1 test + live RTL backend run (`test_live`-style) bit-exact |
| D3 | `fpga/periph/chs_ps2.sv` + reg-bus glue in `gen_top.py`, PLIC source, PS/2 pins in the XDC; cocotb test with a PS/2 device model; set-2 scancode → DOOM key table; UART keys as fallback | `test_ps2.py` self-checking |
| D4 | Bare-metal DOOM on CVA6: WAD in DRAM (memory-backed file calls), CLINT tics, heap, LLC as cache, fps over UART | SoC sim boots to the title screen |
| D5 | SoC sim: present path bit-exact (VGA pin capture vs model), PS/2 model keys reach the game, N tics of a demo | `socsim` DOOM kind |
| D6 | DOOM bitstream, `run_doom.sh` (program, load ELF + WAD over JTAG, UART console), board package | timing met; board run by the user |

Status 2026-10-28: D1–D6 done except the board run. Results: docs/FPGA.md FPG-12; the SoC simulation boots DOOM,
keys reach the game, the G2D frame equals the CPU present and the VGA pins carry it (docs/doom/socsim_vga_menu.png).

### Questions (2026-10-27, one batch; non-blocking, assumption in brackets)

1. GPU level: L2 (palette lookup + scaling + flip in G2D)? [yes]
2. Input: USB keyboard in the Genesys2 "USB HID" port (PIC24 → PS/2 pins); mouse too? [keyboard only now; mouse later
   on the same receiver if wanted; UART keys as fallback]
3. WAD: Freedoom phase 1 (`freedoom1.wad`, fetched) by default; your own `doom1.wad` / `doom.wad` through `DOOM_WAD=`? [both]
4. WAD loading on the board: over JTAG into DRAM by `run_doom.sh` (simple; tens of seconds for a ~28 MB WAD,
   unmeasured) or from the microSD card (needs an SD / FAT path)? [JTAG]
5. Bitstream: trimmed Cheshire + full G2D + PS/2, units left out only if it does not place? [yes]
6. No sound (no audio path in this SoC)? [yes]
7. Performance: no fps target, measured and printed over UART? [yes]

## Side project "DOOM on Cheshire + G2D" (branch `doom`, draft 2026-10-20, **parked**)

**Parked (user, 2026-10-20)**: v1.4 first, DOOM later. **Answers (user, 2026-10-20)**: input = PS/2 through the
Genesys2 USB HID port (UART keys as fallback); GPU level L1 (scale + flip); WAD = Freedoom fetched by script +
`DOOM_WAD=` for the user's own; licence = engine fetched into `build/`, only our GPL-2.0-or-later glue in
`sw/apps/doom/`. Open assumption: this side project builds a bitstream (exception to "no bitstreams before v1.7").

Goal: play DOOM on the Genesys2: CVA6 (bare metal) runs the game, G2D presents every frame (palette → RGB565 →
scaled 640×480 into the frame buffer `axi_vga` scans out), a keyboard drives it. Runs before v1.4 (user,
2026-10-20); the roadmap order after it is unchanged. Same rules as a version: plan → test → docs → STOP, tag
`doom-done`.

### Facts checked (2026-10-20)

- Genesys2 has a "USB HID" host connector: a PIC24 turns a USB keyboard / mouse into PS/2 on FPGA pins AD23
  (ps2_clk) / AE20 (ps2_data), LVCMOS33 with pull-ups (Digilent Genesys-2 master XDC). Not in Cheshire's top today.
- Cheshire's USB is a 4-port OHCI 1.1 host (SpinalHDL) whose ports go to PMOD JA through an external USB 1.1
  adapter; Cheshire ships no OHCI / HID software. The trimmed build (v1.2) already removes it to save LUTs (K-2).
- `axi_vga` registers: start address, frame size, timing; no interrupt and no status (no vsync to poll by
  software). The `vsync_o` pin exists in the top.
- LLC 128 KiB (8 ways × 256 lines × 8 blocks × 64 bit); Cheshire's boot ROM sets it to all scratchpad, so every CPU
  L1 miss goes to DRAM. G2D reaches DRAM through the same crossbar and LLC.
- doomgeneric (github.com/ozkl/doomgeneric, GPL-2.0, HEAD e257a09) and Freedoom (BSD-3, HEAD 3ac0196) are reachable.

### Effort estimate (my estimate, unmeasured)

| Step | Work | Size |
|---|---|---|
| D1 | Host port: doomgeneric + our vg_lite driver on the SW backend, deterministic demo playback, per-frame CRCs | small |
| D2 | Present path: CPU palette lookup into a 320×200 RGB565 texture, G2D scales it (2 × 2.4, point sampled, aspect-correct) into a 640×480 back buffer, page flip on `axi_vga` | small |
| D3 | Input: own PS/2 receiver (`fpga/periph/chs_ps2.sv`, ~150 lines: synchroniser, filter, 11-bit frame + parity check, 16-entry FIFO, PLIC IRQ, plus a vsync counter / vblank flag sampled from `vsync_o`), Cheshire reg-bus slave at `0x4300_1000`, cocotb test against a Python PS/2 model; set-2 scancode → DOOM key table in software; UART keys as fallback (sim, no keyboard) | medium |
| D4 | Bare-metal DOOM on CVA6: WAD in DRAM at a fixed address (memory-backed `_open/_read/_lseek`), CLINT ticks, heap, LLC switched from scratchpad to cache (measure both) | medium |
| D5 | SoC sim: Verilator Cheshire + G2D + PS/2 model; present path checked bit-exactly on a recorded DOOM frame (VGA pin capture vs SW backend); DOOM boot to the first frame as a long run | medium (mostly sim time) |
| D6 | Bitstream (trimmed Cheshire + PS/2 pins), board package `run_doom.sh` (loads ELF + WAD over JTAG), board run by the user | small here, board time |

Frame rate on the board: unknown until measured. DOOM's game logic is capped at 35 tics/s; the software
renderer on a 50 MHz in-order RV64 with a 128 KiB LLC is the limit, not G2D (presenting a frame is ~1 blit). My
guess is single-digit to ~20 fps; the LLC mode will matter most. Sound: none (no audio path in this SoC).

### GPU involvement levels

- L1 (this plan): G2D does scaling, page composition (menus / status bar are part of DOOM's 320×200 buffer), flip.
- L2 (optional, pulls a v1.4 item forward): indexed I8 source + 256-entry CLUT in hardware (F-3), so G2D does the
  palette lookup too and the CPU only writes 64 000 bytes per frame.
- L3 (research, not planned): DOOM walls and flats as G2D perspective blits (each wall seg is a projective quad);
  needs DOOM's light-level colormaps per pixel, i.e. a renderer rewrite.

### Licences

doomgeneric and DOOM's engine source are GPL-2.0: fetched by `scripts/fetch_doom.sh` at a pinned commit into
`build/` (not committed); our platform layer `sw/apps/doom/` is marked GPL-2.0-or-later. WADs are never committed:
Freedoom (BSD-3) is fetched by the script for tests; the user may point `DOOM_WAD=` at their own doom1.wad / doom.wad.

### Exit criteria

1. Host: `make doom-host` builds; `test_doom.py` replays a demo for N tics through the SW backend and compares
   per-frame CRCs with a CPU-only reference present (bit-exact; scaling rule in rendering.md terms: point sampling
   of a 2 × 2.4 scale).
2. `chs_ps2` cocotb test: random scancodes, parity errors, glitches, FIFO overflow, IRQ — self-checking.
3. SoC sim: present path bit-exact on the VGA pins; PS/2 bytes from the sim model reach DOOM's key handler.
4. Bitstream: timing met at 50 MHz, utilisation recorded. Board (user): DOOM playable on a VGA monitor with a USB
   keyboard in the Genesys2 HID port; fps printed over UART.

**Result (2026-11-06): approved by the user with known bugs**, merged into `main`, tag `doom-done`. Criteria 1–3 met
(docs/STATUS.md "Test results (DOOM side project)"); 4 met on the board with limits: DOOM playable (10–13 fps,
SD-card boot, USB keyboard), but with G2D FEATURES = 0 and Cheshire trim mask 7 (Q-FPGA-9, Q-FPGA-10) and residual VGA
blinks under load (Q-FPGA-11). Follow-up work: docs/ROADMAP_V2.md v1.8.

## v1.4 plan: small hardware features (branch `v1.4-hw-small`, draft 2026-10-20)

Scope from docs/ROADMAP_V2.md §1: F-3 formats in hardware, F-2 exact wrap modes + border colour,
`VG_LITE_GRADIENT_SPREAD_FILL` (Q-API-6), RGB565 dither, `transparency_mode = TRANSPARENT` (Q-API-11),
Q-RTL-9 `STATUS.CMD_ACTIVE`. Order per item: spec → model → RTL → test → regression → docs. No bitstream (user
2026-10-18: bitstreams at the end of v1.7); FPGA evidence = OOC synthesis + SoC-sim scene kinds.

### Header facts used (public MIT `vg_lite.h`)

- Formats: `VG_LITE_A1 / A2 / A4 / L4`, `BGRA5551 / 4444 / 2222`, `RGB888`, `ARGB8565`, `VG_LITE_INDEX_1/2/4/8`.
- `vg_lite_set_CLUT(count, colors)` (ARGB); `vg_lite_buffer_t.index_endian`: LITTLE = first pixel in the low bits
  (I1 bit 0 first), BIG = first pixel in the high bits (I1 bit 7 first).
- `vg_lite_pattern_mode_t`: COLOR (outside = the pattern colour), PAD (closest edge pixel), REPEAT, REFLECT.
- `VG_LITE_GRADIENT_SPREAD_FILL = 0` (no semantics given; the enum comment says "match OpenVG", which has no FILL).
- `vg_lite_enable_dither` / `_disable_dither`: no semantics given.

### Work items

1. **Formats (F-3)** — rendering.md §3, regs.yaml `format` enum, `fmt_*` functions in `g2d_pkg`.
   - Targets (read for blending and written): BGRA5551, BGRA4444 (16 bit), BGRA2222 (8 bit). Compression as §3.4
     (round(c·(2^n−1)/255)); 1-bit alpha `a ≥ 128`.
   - Sources: the three above, A1 / A2 / A4 / L4 (sub-byte), I1 / I2 / I4 / I8 (indexed).
   - Sub-byte sources: `tex_addr` gives byte address + bit offset (`x·bpp mod 8`), the source cache carries the
     offset, the texel unit extracts; new field `SRC_FMT.SUB_ORDER` (0 = first pixel in the low bits, 1 = high
     bits; A4 from LVGL = 1, indexed per `index_endian`). Stride and source extent checks (§13.2) in bits.
   - Indexed: 256 × ARGB32 CLUT in the gradient ramp RAM (a draw uses either a gradient or a CLUT), loaded by the
     ramp loader from a new state register `CLUT_ADDR` with the same coherence rule as the ramp; index → CLUT
     entry → then §5.2 steps (colour key, premultiply) as for any texel. **Point sampling only** for indexed
     sources (one CLUT read port; a BILINEAR request samples POINT, listed difference).
   - The driver's CPU conversion (§9.6) is removed for the formats now in hardware; LVGL feature bits updated.
2. **Wrap modes (F-2)** — rendering.md §7 new §7.5, `PAINT_CTRL.WRAP` (0 NONE = today's source test,
   1 COLOR, 2 PAD, 3 REPEAT, 4 REFLECT) + `BORDER_COLOR` (ARGB32). Wrap acts on the integer texel coordinates
   relative to the source rectangle `[sx0, sx1)`, per axis, before the address; bilinear neighbours wrap the same
   way (exact seams, replaces the clamp). Coordinate range with WRAP ≠ NONE: `|u|, |v| < 2^15` pixels (s15.16),
   otherwise the pixel is skipped; the perspective divider is extended to signed quotients in that range.
   Exact `mod` for any period 1..8192 by a per-draw reciprocal + one correction step (checked exhaustively in the
   model tests). `vg_lite_draw_pattern` and tiled images use it (the one-blit-per-tile fallback and its bilinear
   seam difference go away).
3. **`SPREAD_FILL`** (Q-API-6): `PAINT_CTRL.SPREAD = 3` = FILL: `t` outside [0, 1] → colour per question 3; inside
   as PAD. rendering.md §10.1.
4. **Dither**: `TGT_FMT.DITHER`, 4 × 4 ordered (Bayer) dither added before compression to 5/6-bit (and 4/2/1-bit)
   channels, position from the pixel's target `(x & 3, y & 3)`, defined bit-exactly in rendering.md §3.4 (our own
   definition, listed as a difference: Vivante's dither is not public).
5. **`transparency_mode = TRANSPARENT`** (Q-API-11), per question 2.
6. **Q-RTL-9**: `STATUS.CMD_ACTIVE` (= `cmd_live` or not drained), regs.yaml; arch.md §8.
7. **Draw check** (rendering.md §13): BAD_STATE for unknown codes, indexed source with `CLUT_ADDR` unaligned,
   sub-byte target formats; ADDR_RANGE for bit-accurate source extents and the CLUT's 1 KB.
8. **Verification**: golden model + `refimpl.py` + committed vectors for every new format / mode; ideal-formula
   tests (wrap vs an independent float reference, indexed vs a direct lookup, dither vs its formula, round trip
   §3.3 ∘ §3.4 for the new formats); L2 benches (`tex_addr`, `src_cache` offsets, `texel`, `ramp` as CLUT,
   write path with dither); L3 `test_blit` format × wrap × filter matrix, `test_path` SPREAD_FILL; coverage groups
   (`cg_fmt`, `cg_wrap`); SYS random state includes the new fields; driver scenes + SYS-06 recordings + live RTL
   (DW 64 / 128); LVGL three-way with the hardware formats; mutation spot check on the new RTL; FPGA scene kinds
   (new formats / wrap) in the SoC sim; G2D OOC at 50 MHz (LUT / timing delta vs v1.3).
9. Docs: rendering.md, commands.md, arch.md, api-subset.md, FEATURES_LATER (F-2 / F-3 closed), OPEN_QUESTIONS
   (Q-API-6 / 11, Q-RTL-9), VERIFICATION_MATRIX, STATUS, regs.md (generated) → STOP.

### Files

- Change: spec/{rendering,commands,arch,api-subset,verification-plan}.md, spec/regs.yaml (+ generated
  rtl/csr/generated, sw/include header, docs/regs.md); model/ (formats, sampling, gradient, executor, bindings,
  tests, refs); dv/common/{refimpl,g2d_cmd,scenes,g2d_coverage}.py; rtl/pkg/g2d_pkg.sv, rtl/blit/{g2d_xform,
  g2d_tex_addr,g2d_src_cache,g2d_texel}.sv, rtl/gradient/g2d_ramp.sv, rtl/pix/{g2d_pix,g2d_wcomb or write path}.sv,
  rtl/cmdproc/g2d_draw_check.sv, rtl/top/g2d_core.sv, rtl/csr/g2d_ctrl.sv; dv/tests (L2 / L3 / SYS);
  sw/driver/vg_lite (formats, CLUT, patterns, spread, dither, transparency, features), sw/tests, sw/apps/lvgl
  config; scripts/fpga_scenes.py; docs.
- New: dv/tests/test_formats.py (L2/L3 format matrix), model/tests/test_wrap.* (exhaustive mod check),
  sw/tests/test_formats_api.py.

### Questions

1. 24-bit formats `RGB888` / `ARGB8565` (3-byte texels: a texel can straddle a cache line / beat): in v1.4, or
   later (v1.6)? Proposal: later; driver CPU conversion meanwhile.
2. `transparency_mode = TRANSPARENT`: "texels with A = 0 are not drawn" (pixel skipped, destination untouched),
   or close Q-API-11 as "no effect" (documented)?
3. `SPREAD_FILL` outside [0, 1]: the draw's `paint_color` (the gradient draw call passes one), or transparent?
4. Dither: our own 4 × 4 Bayer definition (listed difference), or keep `enable_dither` = NOT_SUPPORT?

**Answers (user, 2026-10-20)**: 1. later (v1.6); RGB888 / ARGB8565 sources converted by the driver meanwhile.
2. TRANSPARENT = texels with A = 0 are not drawn (UNVERIFIED). 3. FILL outside [0, 1] = the draw's `paint_color`.
4. own 4 × 4 Bayer dither (listed difference).

### Assumptions (non-blocking)

- A1. Indexed sources are point-sampled only (as above). A2. CLUT shares the ramp RAM; `count < 256` leaves the
  rest 0. A3. Wrap region = the source rectangle (so `blit_rect` tiles wrap inside their rectangle). A4. Throughput
  stays 1 pixel / clock; extra pipeline stages are allowed.

## v1.5 plan: image paint on paths + radial gradients (branch `v1.5-paths-radial`, draft 2026-10-24)

Scope from docs/ROADMAP_V2.md §1 (F-1, F-4, Q-API-14 exact, source-cache traversal by measurement) plus the
Q-API-19 work the user decided on 2026-10-24 (docs/OPEN_QUESTIONS.md). Order per item: spec → model → RTL → test →
regression → docs. No bitstream (user 2026-10-18); FPGA evidence = OOC synthesis + SoC-sim scene kinds. The user
asked to continue with v1.6 next to v1.5 (question 7).

### Facts used

- Public MIT `vg_lite.h`: `vg_lite_radial_gradient_parameter_t {cx, cy, r, fx, fy}`, `vg_lite_set_radial_grad(grad,
  count, color_ramp, param, spread_mode, pre_mult)`, `_update_`, `_clear_`, `_get_radial_grad_matrix`,
  `vg_lite_draw_radial_grad(target, path, fill_rule, path_matrix, grad, paint_color, blend, filter)`, up to
  `VLC_MAX_COLOR_RAMP_STOPS` = 256 stops; the spread enum says "Match OpenVG enum VGColorRampSpreadMode".
- OpenVG 1.1 §9.3.2 (public Khronos spec, read 2026-10-24): `g = ((dx·fx' + dy·fy') + sqrt(r²(dx² + dy²) −
  (dx·fy' − dy·fx')²)) / (r² − (fx'² + fy'²))`, `fx' = fx − cx`, `dx = x − fx` (likewise y); g = 0 at the focal point,
  1 on the circle. A focal point outside the circle is replaced by the intersection of the centre-focus line with the
  circle; to avoid the division by 0 an implementation may move it towards the centre as long as it stays at least
  0.99 r from the centre. r ≤ 0: g = 1 everywhere. With the focal point inside the circle the square root's argument
  is never negative, so there is no "outside the cone" case to define.
- LVGL v9.6 (`lv_vg_lite_grad.c`): radial gradients with `fx = cx`, `fy = cy` (no focal point), `pre_mult = 1`,
  `paint_color = 0`, filter LINEAR; `vg_lite_draw_pattern` with non-rectangular paths for images with `clip_radius`,
  arcs with an image source, vector image fills and the label fallback (docs/FEATURES_LATER.md F-1).

### Work items

1. **F-1 image paint on paths.** rendering.md §4 / §7.4 / §9.5 / §13.1: DRAW_PATH with `SRC = IMAGE` is legal;
   `cov = path coverage` when the pixel passes the source test (and the TRANSPARENT test), else 0. RTL: draw-check
   rule dropped (source checks and extents apply to paths), `g2d_pix` join (`cov = tx_pass ? cv : 0` for path +
   IMAGE, BORDER_SOLID tag as for blits), source cache invalidated per draw as today. Model + refimpl: path coverage ×
   sampled texel. Driver: `vg_lite_draw_pattern` on any path and any path matrix (affine or projective, §9.8); a
   rectangle with whole-pixel borders keeps the blit (same pixels, faster); fractional rectangles go through the path
   (Q-API-14 solved exactly, the known difference of §9.5 step 1 removed).
2. **Source-cache traversal (decision by measurement).** Paths walk scanlines, so a rotated image on a path samples
   in scanline order instead of 16 × 16 tiles. Measure px/clk and `SRC_CACHE_MISS` of rotated / perspective image
   fills on rectangle paths against the same blit (ideal memory and 90-clock latency). Build a fix (segment-major
   order inside a band, or a coverage reorder buffer) only if the path is below the threshold of question 5;
   otherwise record the numbers in arch.md §6.1 and Q-PERF-2.
3. **F-4 radial gradient (centred and focal).** `PAINT_CTRL.SRC = 3` (RADIAL_GRAD). The hardware evaluates, besides the
   linear `T` (§10.1), two more affine functions `U`, `V` (state `GRAD_UP / UQ / UR / UR_HI`, `GRAD_VP / VQ / VR /
   VR_HI`, indices 0x18–0x1F; same format as `GRAD_P / Q / R`):
   ```
   u = sat32(U >> 9),  v = sat32(V >> 9)              (LSB 2^-8 ramp step; |u|, |v| < 2^31 = 2^15 ramps)
   S = isqrt(u² + v²)                                 (exact floor square root of a 63-bit value)
   i = ((T >> 9) + S) >> 8                            then spread exactly as §10.1 (PAD / REPEAT / REFLECT / FILL)
   ```
   Driver (sw/lib/geom, double): rotate gradient space so that the focal offset `f' = f − c` lies on +x,
   `c = min(|f'| / r, 0.99)` (focal outside the circle or closer than 0.99 r to it: moved to 0.99 r on the centre-focus
   line, which OpenVG allows), `k = 256 / (r·(1 − c²))`; with `(dx', dy')` the rotated offset of the pixel centre from
   the (moved) focal point (an affine function of the target pixel through the inverse gradient matrix):
   `T = 2^17·k·c·dx'`, `U = 2^17·k·dx'`, `V = 2^17·k·sqrt(1 − c²)·dy'`, so that `T + |(U, V)| = 2^17·256·g`
   (OpenVG's formula rewritten in the rotated frame). Centred: `T = 0`. `r ≤ 0`: `U = V = 0`, `T = 2^17·256` (g = 1).
   Ramp construction as the extended linear gradient (§10.3), spread from `spread_mode` (FILL = `paint_color`).
   Perspective gradient matrix: `NOT_SUPPORT` (as linear). RTL: `g2d_grad` grows two affine units, two 32 × 32
   squarers, a 64-bit add and a pipelined 32-stage integer square root (global stall, latency only, 1 px/clk).
   Tolerance vs the float OpenVG formula: ramp index ±1 (§12, as linear). Feature bit
   `gcFEATURE_BIT_VG_RADIAL_GRADIENT` = 1.
4. **Q-API-19 (user decisions 2026-10-24).**
   (a) OpenVG's [0, alpha] colour clamp: on our premultiplied path (`SRC_PREMUL`) every texel has `c = mul8(c, a) ≤ a`
   and bilinear filtering with one weight set and monotone rounding keeps `c ≤ a`, so the clamp would never change a
   value there: proved in the spec, checked exhaustively / randomly in the model tests, no hardware (area 0). For
   sources declared premultiplied (`premultiplied = 1`, no `SRC_PREMUL`) the data may violate `c ≤ a`; clamping them is
   cheap (three 8-bit compare-and-select per pixel, estimate ≈ 150 GE) but changes stored-value semantics: question 3.
   (b) `BORDER_COLOR` premultiplied like the texels when `SRC_PREMUL` (TEXEL border mode; SOLID stays the v1.3 plain
   colour, question 4): texel unit, model, refimpl; test: constant-colour image + matching border colour, edge pixels
   = interior pixels (POINT and BILINEAR, all wrap seams); re-run D9 and report the edge difference.
   (c) test: perspective blits with every corner `w > 0` equal the OpenVG-defined result (NORMAL image mode, float
   reference, §12 bilinear tolerance; MULTIPLY with white = NORMAL bit-exactly); the superset (MULTIPLY under
   projection, partial `w > 0`) documented in rendering.md §7.1 / §5.3. (d) rendering.md §9.5: projective pattern
   matrices documented as an extension of OpenVG.
5. **Draw check** (rendering.md §13): `PAINT_CTRL.SRC ≤ 3`; radial: `GRAD_RAMP_ADDR` rules as linear; DRAW_PATH +
   IMAGE takes the image checks.
6. **Verification**: model unit tests (isqrt exhaustive for small and random for large arguments, radial index vs a
   float OpenVG reference, the premultiplied-filter invariant); refimpl.py + committed vectors; L2 `test_grad` (radial,
   every spread, saturation corners), L3 `test_path` image × path × filter × wrap × blend matrix and radial GRD-02,
   PRF throughput (radial rect 1 px/clk; rotated image on a path vs blit); coverage groups (`cg_paint_path` with IMAGE
   and RADIAL, `cg_radial`); SYS random state includes both; API: `test_pattern_paths` (rounded rectangles, circles,
   arcs, projective path matrix; against an independent float reference = path region × float image sample),
   `test_radial` (centred, focal, focal outside, r ≤ 0, every spread, pre_mult; against the float formula and Skia's
   two-point conical as a third-party check); driver scenes + SYS-06 + live RTL (DW 64 / 128); LVGL three-way with
   the clip-radius / arc / vector-image / radial cases now on the GPU; mutation spot check on the new RTL; FPGA scene
   kinds (patterns on paths, radial) in the SoC sim; G2D OOC 50 MHz (LUT / DSP / timing delta vs v1.4).
7. Docs: rendering.md (§4, §5.1, §7, §9.5, new §10.4 radial, §12, §13), commands.md, arch.md (§6.3 radial pipeline,
   §6.1 numbers), api-subset.md, FEATURES_LATER (F-1, F-4 closed), OPEN_QUESTIONS (Q-API-14 closed, Q-API-19 (a)–(d)
   results), VERIFICATION_MATRIX, STATUS, regs.md (generated) → v1.5 report.

### Files

- Change: spec/{rendering,commands,arch,api-subset,verification-plan}.md, spec/regs.yaml (+ generated
  rtl/csr/generated, sw/include header, docs/regs.md); model/ (ops, gpu, bindings, tests, refs);
  dv/common/{refimpl,floatref,g2d_cmd,scenes,g2d_coverage}.py; rtl/gradient/g2d_grad.sv, rtl/blit/g2d_texel.sv,
  rtl/pix/g2d_pix.sv, rtl/cmdproc/g2d_draw_check.sv, rtl/pkg/g2d_pkg.sv; dv/tests (test_grad, test_path,
  test_draw_check, test_texel, test_sys); sw/lib/geom (radial coefficients), sw/driver/vg_lite (drv_draw pattern on
  paths, drv_grad radial, features), sw/tests, sw/apps/lvgl config; scripts/fpga_scenes.py; docs.
- New: rtl/common/g2d_isqrt.sv (pipelined integer square root) + dv/tests/test_isqrt.py; sw/tests/test_radial.py,
  sw/tests/test_pattern_paths.py.

### Questions (one batch with the v1.6 questions below; non-blocking: I continue with the stated assumption)

1. Driver robustness D-1..D-4 (docs/FEATURES_LATER.md): in v1.6 as its first item (assumption), or now in v1.5?
2. Radial fixed point as in item 3 (8 fraction bits after the affine stage, saturation at 2^15 ramps, focal point
   moved to 0.99 r when it is outside or closer than that to the circle, r ≤ 0 → g = 1): assumption yes.
3. Q-API-19 (a): no clamp hardware (provably a no-op on the `SRC_PREMUL` path, area 0), and declared-premultiplied
   sources stay unclamped (documented deviation): assumption yes. Alternative: clamp declared-premultiplied sources
   too (≈ 150 GE).
4. Q-API-19 (b): premultiply `BORDER_COLOR` only in the default TEXEL border mode; SOLID stays exactly the v1.3 plain
   colour: assumption yes.
5. Traversal fix threshold: build it only if a rotated / perspective image on a rectangle path at 90-clock latency
   reaches less than 85 % of the px/clk of the same blit; otherwise document the numbers (assumption).
6. Rectangle pattern draws with whole-pixel borders keep the blit; fractional ones use the exact path (assumption).
7. Sequencing: v1.5 ends with its report; I then continue with v1.6 on `v1.6-vglite-extras` branched from the v1.5
   tip, and merge v1.5 / v1.6 into `main` only after your approval of each (assumption, reading "start v1.5 and then
   v1.6 next to it"). Merge v1.5 first, before v1.6 starts, if you prefer.

### Assumptions (non-blocking)

- A1. Image paint on paths samples in the raster's scanline order (no tile walk for paths), as FEATURES_LATER F-1.
- A2. Radial gradients reuse the 256-entry ramp, its loader and its reuse rule; `filter` is ignored (nearest ramp
  entry, as the linear gradient).
- A3. Radial gradients on DRAW_RECT work as linear ones do (paint source of any draw).
- A4. Throughput stays 1 px/clk; the radial pipeline adds about 36 stages of latency.

## v1.6 plan: VGLite 4 extras (branch `v1.6-vglite-extras`, draft 2026-10-24)

Scope from docs/ROADMAP_V2.md §1 plus the items moved here: 24-bit formats (v1.4 question 1) and driver robustness
D-1..D-4 (2026-10-24). Rule per item: implement, or close with a recorded reason. Branched from the v1.5 tip
(v1.5 question 7). Order per item: spec → model → RTL → test → regression → docs. No bitstream (v1.7).

### Header facts used (public MIT `vg_lite.h` 4.0.135; OpenVG 1.1 where the header says "Match OpenVG")

- Mask layers: `vg_lite_create_masklayer(buf, w, h)` ("default format A8 and default pixel value 255"),
  `_fill_masklayer(buf, rect, value)`, `_blend_masklayer(dst, src, operation, rect)`, `_render_masklayer(buf,
  operation, path, fill_rule, color, matrix)`, `_set_masklayer(buf, x, y)`, `_enable_ / _disable_masklayer`,
  `_destroy_masklayer`; `vg_lite_mask_operation_t` "Match OpenVG enum VGMaskOperation": CLEAR (0), FILL (1), SET
  (copy), UNION, INTERSECT, SUBTRACT. OpenVG 1.1 §7.2: union = 1 − (1 − m)(1 − s), intersect = m·s, subtract =
  m·(1 − s); the mask multiplies the coverage of every draw.
- `vg_lite_scissor_rects(target, n, rects)` "Set scissor rectangles on mask layer", enabled by
  `vg_lite_enable_scissor`.
- Blends: `OPENVG_BLEND_SRC .. ADDITIVE` with the header's formulas (premultiplied SP = S·Sa, DP = D·Da, result
  divided by the result alpha); `SVG2_BLEND_*` (no formulas in the header); `VG_LITE_PORTER_DUFF_BLEND` with
  `vg_lite_blend_func(factor_src_alpha, factor_src_color, factor_dst_alpha, factor_dst_color, equation,
  srcchannelmode, dstchannelmode)`: factor enums (ZERO, ONE, SRC / DST alpha / colour and inverses, SRC_ALPHA_SAT,
  CONST alpha / colour, two special colour factors), equations ADD / SUB / REVERSE_SUB / MIN / MAX, channel modes
  (premultiply, src, dst, sum, difference, product, |difference|, screen, max, min, `Cs + Cd − 2·Cs·Cd`, bypass).
- `vg_lite_set_pixel_matrix(m[20], channel_enable)`: the 4 × 5 matrix on (a, r, g, b, 1), per-channel enable;
  `vg_lite_set_color_transform` (scale + bias per channel) with enable / disable; `vg_lite_set_gamma` (declared;
  `vg_lite_gamma_conversion_t`: none, sRGB → linear, linear → sRGB).
- `vg_lite_gaussian_filter(w0, w1, w2)` + `VG_LITE_FILTER_GAUSSIAN`: the 3 × 3 kernel [[w2 w1 w2] [w1 w0 w1] [w2 w1
  w2]] on the 3 × 3 texel block around the sample, weights summing to 1.
- `vg_lite_set_mirror(orientation)`: TOP_BOTTOM / BOTTOM_TOP. `vg_lite_blit2(target, src0, src1, m0, m1, blend,
  filter)`: "Copy two source images to the target buffer with transformation, blending, and filtering".
- Image modes "Match OpenVG enum VGImageMode": NORMAL, MULTIPLY, STENCIL (OpenVG 1.1 §10.8: the image's channels act
  as per-channel alpha for the paint colour), NONE, RECOLOR (no OpenVG counterpart).
- Formats: `RGB888`, `BGR888`, `*8565`, `*5658`, `A8L8`, YUV `NV12`, `ANV12`, `YUY2`, `AYUY2` (+ `_TILED`),
  `vg_lite_yuv2rgb_t` YUV601 / YUV709, `vg_lite_swizzle_t` UV / VU.

### Work items

1. **D-1..D-4 driver robustness** (docs/FEATURES_LATER.md "Driver robustness"): `drv_quiesce()` (flush; on timeout
   soft reset, idle check) and a sticky fault state; every free / overwrite of GPU-visible memory gated on it
   (`vg_lite_free`, `drop_cache`, `write_ramp`, `vg_lite_close`); extra scratch allocations tracked and cleaned
   before the kick; `vg_lite_init` records each allocation immediately and cleans up on failure. Tests: model HAL
   hooks for a forced flush timeout, allocator failure at every point of `vg_lite_init` with a leak check, a
   non-coherent-cache model HAL (GPU sees only explicitly cleaned ranges, flags dirty reads).
2. **24-bit sources in hardware**: RGB888 / BGR888 / ARGB8565 / ABGR8565 / BGRA5658 / RGBA5658 (FMT class codes in
   the spare class / bit 7 space); 3-byte texels can straddle a 64-byte line: `g2d_tex_split` issues such a texel
   as two line requests and `g2d_tex_merge` joins the bytes. Driver CPU conversion removed. Targets: question 2.
3. **`vg_lite_scissor_rects` n > 1** (driver-only): the union of the rectangles as a disjoint rectangle set; every
   later draw is emitted once per rectangle with that scissor (each pixel at most once, exact). Alternative through
   the mask (item 4): question 3.
4. **Mask layers**: mask operations are driver-only draws into the A8 mask buffer with existing blends (CLEAR / FILL:
   clear; SET: blit NONE; UNION: SCREEN; INTERSECT: DST_IN; SUBTRACT: SUBTRACT; render_masklayer: path coverage ×
   colour alpha with the same blends). Masked draws need hardware: a mask read client (A8 spans, like the destination
   reader) and `cov = mul8(cov, mask)` at the join, mask origin `(x, y)` from `set_masklayer`, outside the mask
   buffer = 0 (question 4). New state `MASK_ADDR / MASK_STRIDE / MASK_ORIGIN / MASK_SIZE`, `RAST_CTRL.MASK`.
5. **Blend extensions** in `g2d_blend`: (a) `OPENVG_BLEND_*` exactly as the header formulas: premultiply S and D,
   blend, divide the colour by the result alpha (8-bit reciprocal table + multiply, rounded, defined bit-exactly in
   rendering.md; result alpha 0 → colour 0); (b) `VG_LITE_PORTER_DUFF_BLEND` + `vg_lite_blend_func` as factor muxes,
   equations and channel modes on 8-bit values (`CONST` colour / alpha from a new state word); (c) `SVG2_BLEND_*`:
   the W3C Compositing and Blending Level 1 formulas (public) with source-over compositing, question 5.
6. **Colour unit** after §5.3: `vg_lite_set_pixel_matrix` (4 × 5, s7.8 fixed point, per-channel enable, clamp),
   `vg_lite_set_color_transform` (the same unit with a diagonal matrix), `vg_lite_set_gamma` (two fixed 256-entry
   tables, sRGB → linear and linear → sRGB, IEC 61966-2-1 formula rounded, on S before the matrix: question 6).
7. **Image modes**: STENCIL per OpenVG 1.1 §10.8 (paint colour with per-channel alpha from the image, only with the
   blends where it is defined), NONE (draw nothing, success), RECOLOR (question 7; proposal: = MULTIPLY with the
   draw colour, as LVGL uses MULTIPLY for recolouring), `A8L8` source format (16-bit, A and L bytes, `gcFEATURE_BIT_
   VG_A124_A8L8`).
8. **`vg_lite_set_mirror`**: `TGT_FMT.FLIP_Y` (target row y stored at row H − 1 − y), hardware addressing in the walker
   and the fill path; BOTTOM_TOP sets it for later draws (question 8).
9. **`vg_lite_gaussian_filter` / FILTER_GAUSSIAN**: 3 × 3 taps; hardware option: three texel rows per pixel from the
   source cache at 1/3 px/clk; driver option: two separable passes when `w0·w2 = w1²` else NOT_SUPPORT (question 9).
10. **`vg_lite_blit2`**: driver-only as two blits (source0 then source1, same blend and filter) unless the user
   prefers a defined single-pass combine (question 10).
11. **YUV sources**: NV12 / ANV12 (Y plane + interleaved UV plane at half resolution, optional A plane) and YUY2 /
   AYUY2 (packed 4:2:2), BT.601 / BT.709 limited range, chroma nearest-sited (question 11); a second source plane
   address and the CSC in the texel unit; tiled YUV closed (vendor tiling not public).
12. **Closed with a reason**: FLEXA (vendor streaming interface), compressed / DEC formats, tiled layouts
   (undocumented), `vg_lite_set_tess_buffer` (no tessellation buffer: no-op success), dump / cache-command calls.

### Questions (v1.6, one batch; non-blocking with the assumption in brackets)

1. (= v1.5 question 1) D-1..D-4 as the first v1.6 item. [yes]
2. 24-bit formats as targets too? [no: sources only, as LVGL needs; targets NOT_SUPPORT]
3. scissor_rects n > 1: driver decomposition (exact, more draws) or through the mask layer (one draw, needs item
   4)? [decomposition]
4. Mask pixels outside the mask buffer (after the `set_masklayer` offset): 0 (masked out) or 255? [0]
5. SVG2 blends: implement per W3C Compositing Level 1, or close (no formula in the header)? [implement]
6. Gamma: where in the pipeline (source colour before blending, or the blend result before the write)? [source]
7. RECOLOR image mode: = MULTIPLY with the draw colour? [yes, listed UNVERIFIED]
8. Mirror: vertical flip of the target (BOTTOM_TOP = row 0 at the bottom)? [yes]
9. Gaussian: hardware 3 × 3 (≈ 1/3 px/clk, larger texel unit) or driver separable passes only? [hardware]
10. blit2: two sequential blits? [yes]
11. YUV: BT.601 / 709 limited range ("video" range), chroma nearest? [yes]
12. Scope: one v1.6 or split (v1.6: items 1–5, v1.6b: 6–11)? [split if the report would grow too large; decide
    at the v1.5 report]

**Split taken (2026-10-24, assumption of question 12, after item 1):** the 24-bit sources need the texel path to
handle non-power-of-two texels that straddle cache lines (address generation, the cache's per-row line pairing, the
merge extraction, extent checks), the same part of the RTL as the Gaussian filter, YUV and the colour unit. **v1.6** =
items 1, 3, 4, 5, 7, 8, 10, 12 (driver robustness, scissor rects, mask layers, blend extensions, image modes and A8L8,
mirror, blit2, closures); **v1.6b** = items 2, 6, 9, 11 (24-bit sources, colour unit with gamma / colour transform /
pixel matrix, Gaussian filter, YUV), planned after the v1.6 report.

## v1.6b plan: texel path extensions (branch `v1.6b-color-yuv`, draft 2026-10-26)

Scope: the v1.6 items 2, 6, 9, 11 (split note above), branched from `main` after the v1.6 merge (`v1.6-done`). Order
per item: spec → model → RTL → test → regression → docs. No bitstream (v1.7). Timing / area clean-up is not in scope
(user 2026-10-26, docs/FEATURES_LATER.md T-1 / T-2); v1.6b only adds pipeline stages where 50 MHz needs them.

### Common mechanism: sub-requests in the texel path

Today one pixel = one cache request of four texels (a 2 × 2 cross product of two columns and two rows), split into
single-texel requests only at wrap seams (`g2d_tex_split`). v1.6b generalises this:

- A **sequencer** in front of `g2d_tex_addr` turns one pixel into N sub-requests `(group g, plane p)`; each is a 2 × 2
  cross product with its own column / row offsets, plane base / stride / subsampling / bytes per texel. N = 1 for
  every existing format and filter (no throughput change), YUY2 1, NV12 / AYUY2 2, ANV12 / 3-plane YUV 3, Gaussian 4.
- `g2d_tex_split` keeps the seam rule and adds the **24-bit straddle part**: a 3-byte texel at line offset 62 / 63
  gets a second request for the next line (offset 0); the cache stays unchanged (it already zero-fills past the line
  end). The per-texel `sh` field tells the merge how many low bytes came from the first line.
- `g2d_tex_merge` assembles per pixel: for YUV the per-plane bytes into one `{A, Y, U, V}` word per texel (swizzle,
  YUY2 Y select); for 24-bit the two parts; for the Gaussian it forwards each group with its slot weights.
- `g2d_texel` accumulates the Gaussian groups before the rounding stage (one output per pixel), converts YUV in S0
  (expansion, §3.3), and gets the colour unit after filtering.
- The per-texel row parity becomes 4 bits (one per slot) in the split / cache interface.

### Work items

1. **State space 64 → 128 words** (`regs.yaml` `size`, `gen_regs.py`, `g2d_state` index width, consumers, model,
   refimpl, driver shadow). Needed: about 15 new words, 9 free. Only defined bits are stored, so the cost is the new
   registers only.
2. **24-bit sources** (v1.6 item 2): extended FMT codes with bit 7 set (`0x80 | class`): RGB888 / BGR888, ARGB8565 /
   ABGR8565, BGRA5658 / RGBA5658 (layouts of rendering.md §9.6, expansion §3.3). Tex address = x·3 (not a shift),
   straddle part in split / merge, draw-check extents. Driver: native, the CPU conversion and its cache removed,
   `gcFEATURE_BIT_VG_24BIT` = 1 (LVGL then passes RGB888 / ARGB8565 directly). Sources only (question 2 of v1.6).
3. **Gaussian filter** (item 9): `PAINT_CTRL.GAUSS` (bit 13), state `GAUSS_W` = `W1 [15:0]`, `W2 [31:16]`, `W0 = 65536
   − 4·(W1 + W2)` (BAD_STATE if negative). Taps at the point-sampled texel ± 1 per axis, neighbours mapped by the wrap
   mode (NONE: clamped, like bilinear); `c = (Σ c_k·W_k + 32768) >> 16` over the 9 texels after §5.2 steps 1–4. Four
   sub-requests per pixel (¼ px/clk). Driver: `vg_lite_gaussian_filter(w0, w1, w2)` quantises W1, W2 (round) and
   checks w ≥ 0, |w0 + 4 w1 + 4 w2 − 1| ≤ 1e-3; `VG_LITE_FILTER_GAUSSIAN` on blits and patterns. Not with YUV or
   indexed sources (indexed: point, as for bilinear).
4. **YUV sources** (item 11): NV12, ANV12, YUY2, AYUY2 (LVGL 9.6 draws NV12 / YUY2 through VG-Lite when
   `YUV_INPUT` / `YUY2_INPUT` are set) and, with the same machinery at no extra hardware, NV16, NV24, YV12, YV16,
   YV24 (public FourCC layouts). Planes: `SRC_ADDR` = Y (or the packed plane), state `SRC_PLANE1_ADDR`,
   `SRC_PLANE2_ADDR`, `SRC_PLANE_STRIDE` (two 16-bit strides). `SRC_FMT.YUV_709` (bit 11), `SRC_FMT.UV_SWAP` (bit
   12). Conversion per texel in the texel unit, limited ("video") range, integer coefficients × 1024 from the BT.601 /
   BT.709 definitions (rendering.md §3.3, exact formula and table), chroma nearest (each luma texel takes the chroma
   sample that covers it); then filtering as usual. Tiled YUV closed (v1.6).
5. **Colour unit** (item 6): in `g2d_texel` after filtering, before §5.3: gamma (two 256-entry tables, IEC
   61966-2-1 sRGB, generated by a script into an RTL package and a model header), then a 4 × 5 affine matrix on
   (A, R, G, B, 1): `c' = clamp((Σ M_ij·c_j + B_i + 128) >> 8, 0, 255)`, `M` s7.8 (16 bit), `B` in 1/256 channel
   steps (20 bit signed). State `COLOR_CTRL` (EN, GAMMA [1:0]), `COLOR_M0..M7` (two coefficients each),
   `COLOR_B0..B3`. Driver: `vg_lite_set_pixel_matrix` (per-channel enable: disabled rows = identity) and
   `vg_lite_set_color_transform` / `_enable_ / _disable_` composed on the CPU into the one matrix (pixel matrix first,
   then the transform; no clamp in between, documented), `vg_lite_set_gamma`. Solid colours and gradient ramp entries
   get the same integer function on the CPU (exact, so the result does not depend on the paint source). Not applied to
   `vg_lite_clear` and mask-layer operations.
6. **`OPENVG_*` buffer formats** (driver-only, api-subset §4): mapped onto the existing FMT codes (MSB-first names →
   our LSB-first codes), `_PRE` → premultiplied, `s` / `l` colour spaces → the gamma direction when source and
   target spaces differ (OpenVG 1.1 §3.4 conversion before blending). `OPENVG_BW_1` closed (no 1-bit luminance class).
7. Tests: model hand tests (straddling texel, one Gaussian pixel, YUV pixel per standard, gamma and matrix pixel);
   refimpl for all; L2 benches (tex split / merge straddle and planes, texel Gaussian accumulation, YUV, colour unit);
   L3 `f24-01` (24-bit, every wrap / filter, straddles at every line offset), `gau-01`, `yuv-01`, `col-01`; draw-check
   rules; API tests vs independent float references (YUV per the standards, Gaussian in float, sRGB formulas, matrix
   in float), LVGL with `24BIT` / `YUV_INPUT` / `YUY2_INPUT` reported; scene kind `v16b` (API, live RTL, SYS-06, SoC
   sim); coverage groups; full regression, OOC, SoC sim, mutation spot check.

Files: `spec/{rendering,regs.yaml,commands,api-subset,arch,verification-plan}.md`, `scripts/gen_regs.py`, new
`scripts/gen_gamma.py`; `rtl/cmdproc/{g2d_state,g2d_draw_check,g2d_cmd_parse}.sv`, `rtl/blit/{g2d_tex_addr,
g2d_tex_split,g2d_tex_merge,g2d_src_cache,g2d_texel}.sv` (+ a sequencer, new `g2d_tex_seq.sv`, and `g2d_color.sv`),
`rtl/pix/g2d_pix.sv`, `rtl/pkg/g2d_pkg.sv`; `model/src/{ops,gpu}.cpp`; `dv/common/{refimpl,g2d_cmd,g2d_coverage}.py`,
`dv/tests/*`; `sw/driver/vg_lite/{drv_draw,drv_conv,drv_buffer,drv_core,drv_grad}.c`, `sw/tests/*`; docs.

### Questions (v1.6b, one batch; non-blocking, assumption in brackets)

1. State space 64 → 128 words (our own command format, LOAD_STATE already has an 8-bit index)? [yes]
2. Gaussian throughput ¼ px/clk (four 2 × 2 sub-requests, no extra cache ports) acceptable? [yes]
3. Gaussian weights: reject negative weights and sums off by more than 1e-3 with `VG_LITE_INVALID_ARGUMENT`? [yes]
4. YUV range: limited / video range only (the header has no range selector)? [yes]
5. `VG_LITE_YUYV` next to `VG_LITE_YUY2`: the header gives no layout difference. [closed, `NOT_SUPPORT`, until a
   public layout is known]; the `*_PLANAR` 24-bit formats [closed: layout not public].
6. Colour unit order: gamma → pixel matrix → colour transform, on stored (possibly premultiplied) values, after
   filtering, before §5.3; matrix bias in normalised units (1.0 = 255)? [yes; listed differences vs OpenVG, which
   un-premultiplies]
7. Colour transform / matrix / gamma also on solid and gradient paints (driver, exact) but not on clears and mask
   operations? [yes]
8. OPENVG_* formats in this version (driver mapping + gamma), or close? [this version]

## v1.7 plan: performance and timing (branch `v1.7-perf-timing`, draft 2026-10-26)

Scope from docs/ROADMAP_V2.md (v1.7 row) plus the v1.6 / v1.6b later-work items T-1 / T-2 (user 2026-10-26: "we have
to solve timing problems later"). Branched from `main` after the v1.6b merge (`v1.6b-done`). Every change keeps the
bit-exact behaviour (no spec change except new parameters); latency may grow, throughput may not drop. The bitstreams
and the next board package are built at the end of v1.7 (user 2026-10-18).

### Work items

1. **Timing (T-1, Q-RTL-10, Q-RTL-1 / 6 / 8):** register the stencil terms and split the blend-family S2 adds, then
   OOC sweeps on the K7 (50, 100, 125, 150 MHz) and fix the worst paths in order (R register slice, draw-check
   multiply, band-setup multiply-add, source-cache tag stage, texel extraction) until the target of question 1 is met
   with margin; report fmax and the path list. Each fix: lint, `make test`, OOC.
2. **Area / FPGA fit (T-2):** a full Cheshire + G2D implementation run to get the real SoC utilisation; compile-time
   parameters to leave out optional units for an FPGA build (blend families, Gaussian, YUV, colour unit, radial
   gradient, mask layer), with the driver's feature bits and the draw check following the parameters (a left-out
   feature is `BAD_STATE` / `NOT_SUPPORT`, never a silent wrong draw); radix-4 divider for the perspective transform
   (Q-RTL-7) if it saves area at equal timing.
3. **Q-PERF-2:** blended tiled blits under latency (0.55–0.69 px/clk at 90 cycles): destination prefetch across the
   rows of a tile (the reader requests the next tile row while the current one is consumed) or wider tiles, chosen by
   measurement; target ≥ 0.85 px/clk.
4. **Q-PERF-4:** skip the destination read for spans whose coverage is 255 throughout when the blend does not read
   D (NONE / SRC-like), path and rect draws.
5. **Q-PERF-3 / K-1:** edge-list prefetch (load the next band's edges while the current band rasterizes); glyph
   target ≥ 0.9 px/clk.
6. **Q-PERF-1:** golden-model speed (flat memory, per-draw decode, row loops), bit-exact tests as the guard.
7. **End of v1.7:** trimmed Cheshire bitstreams (single clock and async), board package, `make regress`, OOC numbers,
   SoC sim; board tests by the user (milestone B).

### Questions (v1.7, one batch; non-blocking, assumption in brackets)

1. Timing target: G2D OOC ≥ 100 MHz on the K7 (the async build's core clock) as the requirement, fmax reported? [yes]
2. FPGA fit: if Cheshire + G2D does not fit with every unit, which units may the board build leave out, in which
   order? [none if it fits; else colour unit, then Gaussian, then YUV, then blend families, reported per step]
3. Performance targets as written (Q-PERF-2 ≥ 0.85 px/clk at 90-cycle latency, glyph paths ≥ 0.9 px/clk, Q-PERF-4
   implemented)? [yes]
4. Extra pipeline latency (cycles per draw / per pixel in flight) acceptable where it buys timing, throughput
   unchanged? [yes]
5. Radix-4 divider only if it saves area at equal timing? [yes]
6. ASIC flow stays postponed (no new tools)? [yes]
7. Bitstreams at the end of v1.7: single-clock and async, both trimmed Cheshire, VGA kept? [yes]

## v1.8 plan: board-proven (branch `v1.8-board-proven`, started 2026-11-11)

Scope: docs/ROADMAP_V2.md §1.0b, v1.8 row (user 2026-11-11: "start the v1.8 in a new branch"). Branched from `main`
(`doom-done`) with the audit branch merged in (docs/AUDIT_2026-11.md, finding IDs A- / B- / C- / D-). Work is done in
two places: the cloud container (Verilator, cocotb, Cheshire, no Vivado, no board, 4 cores) and the user's machine
(Vivado, board). Each item says where.

### Work items

1. **VGA scan-out, `axi_vga` fork (Q-FPGA-11, audit §2.2 steps 0 and 2; container):**
   a. real bursts: the burst splitter, its credit counter and the `stream_join` are removed; the fetcher's AR goes out
      as one burst (capped at BufferDepth / 4 beats, 4 KB boundaries as before); the fork's `committed_q` accounting
      keeps every accepted burst within the buffer;
   b. the R buffer becomes a block-RAM FIFO (`g2d_fifo_ram`), VgaBufferDepth 256 → 1024 beats in `g2d_chs_pkg`;
   c. debug counters read through the fork's register window above the vendor registers (offset 0x40..): visible
      pixels shown without data (underruns), frames with an underrun, minimum buffer fill since the last read, worst
      AR-to-first-R latency; clear on write; no Cheshire patch needed;
   d. bench: new mode `arb` (AR accepted once every N cycles, a crossbar shared with a bursting manager): the fork
      must stay bit-exact, the original fails; every mode checks the counters (zero underruns where bit-exact).
2. **Frame buffers outside the LLC (audit §2.2 step 3; container: SoC sim):** Cheshire patch option for the LLC's
   cached region end; the DOOM build places the GPU pool and the frame buffers in the uncached part; DOOM SoC sim
   check (`DOOM_CHECK` + VGA capture) still passes. *Board*: the user.
3. **LLC confirmation image (audit §2.2 step 1):** `DOOM_LLC_CACHE=0` SD image (needs a RISC-V GCC; container: a
   public toolchain under `~/tools`, see question 5; else on the user's machine). *Board*: the user, before item 2's
   bitstream.
4. **Q-FPGA-10 tooling (A-1, A-2):** (a) Verilator runs with randomised initial values (`--x-initial unique`,
   `+verilator+rand+reset+2`) as a `make regress` stage (container); (b) synthesizable RAM read/write collision
   sticky bits per `g2d_ram_1r1w` instance, collected into a new status register (regs.yaml → generated), and a
   write-channel signature (CRC-32 over W beats since the last clear) so the board test localises the first wrong
   draw (container: spec, RTL, tests); (c) post-synthesis functional netlist simulation of `g2d_top` in the cocotb
   benches (script here, run on the user's machine with Vivado); (d) fix, then a FEATURES = all board regression.
5. **Q-FPGA-9** (trimmed Cheshire hang): per-unit bisection as in §1.0 (user's machine) and a DDR-like memory model
   in the SoC sim (variable latency, refresh stalls) (container).
6. **Repository (A-6, D-1):** top-level README, third-party notice, CI workflow (`make regs-check lint model` and a
   quick test subset) (container).
7. **End of v1.8:** `make regress`, new bitstreams (single clock, async; user's machine), board package, board
   regression and DOOM (user), docs, STATUS, merge, tag `v1.8-done`.

Files: `fpga/cheshire/axi_vga_g2d/*`, `fpga/cheshire/g2d_chs_pkg.sv`, `fpga/cheshire/cheshire-g2d.patch`,
`fpga/sim/socsim.py`, `fpga/scripts/build_fpga.sh`, `dv/tb/tb_axi_vga.sv`, `dv/tests/test_axi_vga.py`, `sw/apps/doom/*`,
`rtl/common/g2d_ram_1r1w.sv`, `spec/regs.yaml`, `spec/arch.md`, `rtl/csr/*`, new `dv/tests/` cases, `scripts/regress.py`,
`README.md`, `THIRD_PARTY.md`, `.github/workflows/ci.yml`, docs.

### Questions (v1.8, one batch; non-blocking, assumption in brackets)

1. Audit step 4 (line-based fetch with repeat-last-line): build it in the fork now, or leave it to the own display
   controller of v1.9? [v1.9; v1.8 makes underruns rare and measurable]
2. Frame buffers outside the LLC: a build option of our Cheshire patch, on for the DOOM build only? [yes; the LVGL /
   board-test builds keep today's map]
3. Licence for the own RTL / software (D-1). [README and THIRD_PARTY.md now; no LICENSE file until you choose]
4. CI: GitHub Actions building Verilator 5.052 (cached) and running lint, model and a short test subset on every
   push? [yes, workflow file committed; it only runs if Actions are enabled on the repository]
5. RISC-V GCC in the cloud container to build DOOM / bare-metal images: a public prebuilt riscv64 bare-metal GCC 13
   under `~/tools`? [yes, container only; images built here are checked on your machine before the board]
6. Board and Vivado steps (bitstreams, netlist simulation, board runs) stay on your machine, prepared as scripts
   here. [yes]

## v1.9 plan: integration package and memory QoS (branch `v1.9-integration`, started 2026-11-12)

Scope: docs/ROADMAP_V2.md §1.0c (user 2026-11-12: "Continue with new recommendations. Display will be separate option
or separate project from this."). A 2.5D GPU IP is sold without a display controller (the SoC has its own); the
integration package is what an integrator needs first. The display-controller plan of the same day (first commit on
`v1.9-display`) is withdrawn; the `axi_vga` fork stays for the demos. Container: everything except the board.

### Work items (rule-6 order: spec → model where needed → RTL → bench → regression → docs)

1. **Configuration-side adapters (F-6)** in `rtl/adapt/`, all to the bus-agnostic `g2d_reg_req_t / g2d_reg_rsp_t`
   port (arch.md §4.1): `g2d_axi2reg` (full AXI4 slave, 32..512-bit data, bursts split into single accesses, ID
   reflection, ATOPs answered SLVERR, configurable outstanding depth), `g2d_apb2reg` (APB3 / APB4), `g2d_ahb2reg`
   (AHB-Lite), `g2d_obi2reg` (OBI 1.x and PULP `reg_intf`), `g2d_wb2reg` (Wishbone B4 pipelined), `g2d_tlul2reg`
   (TileLink-UL). Each: own bench with an own BFM and protocol checker (dv/sva), the CSR model as reference; the L3
   smoke set once through every adapter.
2. **Memory-port options (F-6, B-7)**: `MEM_ATTR` CSR (AxCACHE, AxPROT, AxQOS for read and write; reset values from
   top parameters = today's constants); **throttle** `MEM_QOS` CSR (maximum outstanding read / write bursts, minimum
   gap in cycles between burst requests; default off = cycle-identical to v1.8); maximum burst length parameter
   (down to 1); ID width below 3 with internal ID serialization; own AXI4 data-width converter at the port
   (internal 64 / 128 to external 32..512). AXI4 stays the only memory protocol (bridges to AHB / TileLink later
   if asked).
3. **Interrupt and reset options**: IRQ polarity and an optional one-cycle pulse per newly set IRQ_STATUS bit
   (parameters); `test_mode_i` (reset-synchronizer bypass, B-4) and a reserved `scan_en_i` for future clock gates.
4. **Memory macro layer (B-2)**: `g2d_ram_*` variants selected by a `RAM_IMPL` parameter: behavioural (today),
   Xilinx (explicit `ram_style` and write mode, collision behaviour documented), ASIC stub (macro port shape with BIST
   ports, behavioural inside, to be replaced by a generated macro).
5. **Timing constraints (D-2)**: `syn/constraints/g2d_top.sdc` template (clocks for the single and async builds, CDC
   exceptions equivalent to `fpga/genesys2/g2d_cdc.tcl`, I/O budgets as parameters), checked with an open STA tool
   if one is available in OSS CAD Suite (else listed in OPEN_QUESTIONS).
6. **Packaging**: generated per-bus wrappers `g2d_top_<bus>`, an IP-XACT (IEEE 1685-2014) component generated from
   regs.yaml and the port list, a FuseSoC core file and a Bender.yml; each checked by its tool where installable
   (FuseSoC from pip, schema check with xmllint).
7. **Cheshire on our adapter**: the PULP `axi_to_axi_lite` + `axi_lite_dw_converter` pair in `g2d_cheshire.sv`
   replaced by `g2d_axi2reg`; SoC sim 11 / 11 kinds bit-exact with the same write signatures as v1.8.
8. **Documentation**: `docs/INTEGRATION.md` (ports, parameters, clocks / resets, adapters, memory attributes and
   QoS, constraints, RAM wrappers, interrupts, bring-up checklist), README links; regs.yaml version 0.4.0.
9. **End**: `make regress`, STATUS / FPGA.md / OPEN_QUESTIONS, phase report. Board: optional (Cheshire adapter
   bitstream by the user).

### Questions (one batch; [assumption] used until answered)

1. Adapters: all six (AXI4, APB, AHB-Lite, OBI / reg_intf, Wishbone, TileLink-UL)? [all six]
2. Memory side: AxCACHE / AxPROT / AxQOS as CSR fields with parameter reset values? [yes] Data-width converter
   external 32..512? [yes] Memory bridges to AHB / TileLink: not in v1.9? [not in v1.9]
3. Throttle default off (cycle-identical), set by software? [yes]
4. RAM wrappers: ASIC variant is a stub with BIST ports (no PDK / memory compiler here)? [yes]
5. Licence: IP-XACT and FuseSoC carry a licence field; D-1 is still open. [field "TBD" until you choose]
6. Integration guide in v1.9, a full programming manual in v2.0? [yes]
7. K-1 edge prefetch / Q-PERF-4 opaque SRC_OVER skip: not in v1.9 (only if board numbers ask)? [not in v1.9]
8. Display controller: separate optional project after v2.0; `axi_vga` fork kept for demos? [yes]

## v2.0 plan: drivers and closure (branch `v2.0-drivers`, started 2026-11-12)

Scope: docs/ROADMAP_V2.md §1.0c, v2.0 row (user 2026-11-12: "Then start next version"). v2.0 is large, so it runs
in three steps, each ending with a report and an approval; the tag `v2.0` comes after the last one.

### Step A: driver robustness, interrupts, caches, RTOS (container: host + SoC simulation)

1. (Already done in v1.6: the review findings D-1..D-4, docs/PROGRAMMING.md §1.1, sw/tests/test_robust.c; checked
   again at the step A end.)
2. HAL interface v2 (audit C-1, C-3, C-5, C-6; C-4's range calls exist already): lock / unlock hooks around every
   public entry (no-ops on bare metal), interrupt-driven wait on bare metal (PLIC claim + WFI with a timeout,
   polling kept as fallback), 64-bit GPU addresses (MEM_BASE_HI from the HAL), delays calibrated from the platform
   timer. The non-coherent cache model of the robustness test stays a permanent test (extended to the new paths).
3. G2D extensions in `vg_lite_g2d.h`: memory attributes / throttle (`MEM_ATTR`, `MEM_QOS`), interrupt mode query.
4. Stroke geometry speed (FEATURES_LATER D-5): unit-circle table per vertex count, bit-exact with today's output.
5. RTOS HAL: FreeRTOS [assumption] (mutex for the lock hooks, a semaphore given from the G2D interrupt). Tests:
   the FreeRTOS POSIX port on the host with the golden-model HAL (several tasks drawing at once, LVGL in one task);
   on the SoC simulation with the RISC-V port (CLINT tick, PLIC interrupt), one scene kind per task.

### Step B: Linux (container: build; board: user)

6. Kernel driver `sw/linux/g2d.ko`: platform driver from a device-tree node, misc device with ioctls (submit a
   command buffer, wait for a fence with timeout, allocate / free DMA-coherent buffers and mmap them), interrupt
   handler, runtime reset on timeout. Device-tree binding `sw/linux/g2d.yaml` (dt-schema style).
7. Linux HAL for the vg_lite driver (user library), so LVGL / the scene tests run as Linux programs.
8. Build-tested here against the kernel version Cheshire's cva6-sdk pins (cloned shallow); a host test of the HAL
   against a fake device (ioctl layer replaced by the golden model). Boot and run on the Genesys2: user.

   As built (2026-11-12, docs/PROGRAMMING.md §7.2): no submit / fence ioctl and no reset in the kernel. The vg_lite
   driver keeps doing both (command buffers, FENCE_SEQ, soft-reset recovery) over REG_READ / REG_WRITE, so it stays
   identical on every HAL; the kernel's job is confinement (the address window is fixed to the DMA pool and
   locked), memory, interrupt wait and exclusive open. Binding at `sw/linux/bindings/gpu/g2d.yaml` (dtschema path rule).

### Step C: ASIC numbers and closure

9. `g2d_top` through LibreLane on IHP SG13G2 (130 nm): SystemVerilog through sv2v or the yosys-slang plugin (built
   from source if needed), memories on IHP SRAM macros where a size fits (else flops), area, cell count, fmax per
   corner, critical paths; a written statement on the 600 MHz target (estimate by FO4 scaling, marked as estimate).
   Equivalence check of the synthesized netlist against the RTL by simulation (gate-level run of a test subset).
   (User 2026-11-12: report the highest frequency reached in IHP SG13G2; no scaled 600 MHz estimate.)
10. `docs/PROGRAMMING.md`: programming manual (register use, command buffers, fences, interrupts, caches, errors,
    the driver's HAL and porting guide).
11. Every item of OPEN_QUESTIONS, FEATURES_LATER and api-subset "later" resolved, implemented or closed with a
    recorded reason (docs/CLOSURE_V2.md lists each with its outcome).
12. `make regress`, docs, STATUS; merge, tag `v2.0`.

   As built (2026-11-16): item 9 produced a reference layout with known defects (docs/ASIC.md): routed GDS on IHP
   SG13G2 at a 50 MHz constraint (user 2026-11-13), SRAM macros from the PDK, signoff timing per corner with OpenSTA;
   DRC / LVS / antenna signoff and the last hold fix did not fit the 15 GB build machine. The user accepted it as the
   v2.0 reference (2026-11-16) and asked to finalize; a return to the flow is possible later.

### Answers (user 2026-11-12)

1. FreeRTOS. 2. Linux build-only here plus a host test with a fake device, board boot by the user (explained, taken
as agreed). 3. ASIC on IHP SG13G2: report the highest frequency reached in this technology; no 600 MHz estimate.
4. Steps A / B / C, each approved, tag `v2.0` after C. 5. Explained; assumption (a): close Q-API-* interpretation
items as "accepted interpretation, defined in spec/rendering.md" unless the user prefers to keep them open.

### Questions (one batch; [assumption] used until answered)

1. RTOS: FreeRTOS or Zephyr? [FreeRTOS: generic RISC-V port with CLINT, no Cheshire board port needed, host-testable
   with its POSIX port; Zephyr would need an SoC / board port for Cheshire first]
2. Linux in the container is build-only plus a host test with a fake device; booting on the board is yours? [yes]
3. ASIC numbers on IHP SG13G2 (130 nm, the PDK installed now); the 600 MHz statement as a scaled estimate? [yes]
4. Three steps A / B / C with a report and approval each, tag `v2.0` after C? [yes]
5. Closing API interpretation questions that the public header cannot settle (Q-API-*) as "accepted
   interpretation, documented in spec/rendering.md" rather than leaving them open? [yes]
