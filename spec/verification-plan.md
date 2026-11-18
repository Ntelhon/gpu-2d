# Verification plan (v1)

## 1. Strategy

| Level | What | Tool | Reference | Phase |
|---|---|---|---|---|
| L0 Spec arithmetic | Integer helpers, format round trips, sample pattern | pytest / C++ unit tests | Exhaustive math (rendering.md §2–3) | 0–1 |
| L1 Golden model | Each stage + command-stream executor | C++ tests + pytest via pybind11 | Independent Python reimplementation (bit-exact), float reference renderer (tolerance, rendering.md §12), committed reference vectors; ideal-formula checks (Phase 6, model/tests/test_ideal.py); ThorVG / Skia cross-checks: L4 | 1 (done) |
| L2 Block RTL | CSR, AXI master, cmd proc, blend, format, sampler, rasterizer, gradient | cocotb + Verilator | Golden model stage functions (bit-exact) | 2–4 (P2: common, CSR, AXI, draw check, package arithmetic; P3: SRAM wrappers, blend, transform/divider, texel addressing, texel unit, source cache; P4: signed divider, edge loader, rasterizer incl. streaming and forced pass splits, gradient index, ramp) |
| L3 GPU top | Command streams in memory → framebuffer | cocotb + AXI4 memory model | Golden model top (bit-exact memory image) | 2–5 |
| L4 API | vg_lite calls on the SW backend, RTL replay (SYS-06) and the live RTL backend; LVGL demos | C tests + pytest (sw/tests), cocotb (SYS-06), Verilator C++ harness (dv/live) | Bit-exact between backends and against the REF replay; IDEAL, LVGL software renderer, ThorVG, LVGL vg_lite_tvg and Skia with the tolerances of docs/VERIFICATION_MATRIX.md | 6 (done) |
| L5 System | Bare-metal and Linux on Cheshire/Genesys2 | FPGA | Golden images from L4 | 7 |

Every check is self-checking. Bit-exact is the default; tolerances apply only where rendering.md §12
says so.

## 2. Testbench architecture (L2/L3)

```
             ┌───────────────────────── cocotb test ─────────────────────────┐
             │ stimulus gen (seeded) ─► command stream + memory image         │
             │        │                         │                            │
             │        ▼                         ▼                            │
             │  golden model (pybind11)   AXI4-Lite master (cocotbext-axi)   │
             │        │ expected mem image      │ CSR kick / poll / IRQ      │
             │        ▼                         ▼                            │
             │   scoreboard ◄── memory diff ── DUT g2d_top ── AXI4 master ──► │
             │                                         ▲                     │
             │                    AXI4 memory model (sparse, byte-accurate,  │
             │                    random latency / back-pressure / error inj)│
             └───────────────────────────────────────────────────────────────┘
```

- **AXI4 memory model** (`dv/common/axi_mem.py`, project-specific, Phase 2 decision Q3): sparse byte memory;
  random ready on AW/W/AR, random R/B latency and RVALID/BVALID gaps; out-of-order completion and
  beat-level read interleaving across IDs (in order per ID); SLVERR/DECERR injection on address ranges
  (erroring writes do not modify memory); a write becomes visible when its B is sent and a read snapshots
  memory at AR acceptance, so an ordering bug reads stale data; optional transaction log. cocotbext-axi
  is used for the AXI4-Lite master.
- **Protocol monitor** (Python, inside the memory model, every L2 AXI and L3 test): no 4 KB crossing,
  INCR only, full-width aligned `AxSIZE`/address, `AxLEN` within `MAX_BURST_BYTES`, no transaction
  outside `ADDR_WIN`, no new AR/AW after soft reset starts, VALID held and payload stable while
  VALID && !READY on AW/W/AR, WLAST exactly on the last beat, bounded W-before-AW. WSTRB correctness is
  checked by the whole-memory compare (a wrong strobe changes a byte that must not change). In Phase 5 the
  same rules become SVA bound into the RTL where Verilator supports them.
- **Scoreboard**: compares the *whole* memory image after each test with the model's. This catches both
  wrong pixels and stray writes outside the target. On mismatch it dumps expected/actual/diff PNGs and
  the first 16 differing pixels with coordinates and channel values.
- **Interrupt / fence checker**: `FENCE_SEQ` monotonic, IRQ asserted iff the enabled status is set,
  `STATUS.IDLE` consistent with outstanding transactions.

## 3. Test list

IDs are stable. "P" = phase introduced.

### CSR / infrastructure (P2)
| ID | Test |
|---|---|
| CSR-01 | Reset values of all registers (from regs.yaml) *(done in Phase 0 on the generated block)* |
| CSR-02 | RW with random byte strobes; RO, const ignore writes; W1C set/clear/priority; WO strobe *(done in Phase 0)* |
| CSR-03 | Unmapped offsets → SLVERR on AXI4-Lite (block-level error *done in Phase 0*) |
| CSR-04 | AXI4-Lite back-pressure on every channel, simultaneous read/write *(P2: test_csr_top)* |
| CSR-05 | IRQ: each source × enable, W1C clear, level behaviour *(P2: test_csr_top, test_top)* |
| CSR-06 | Soft reset during idle and during a large draw; outstanding AXI drained, CSR config kept *(P2: test_csr_top, test_top)* |
| CSR-07 | Kick queue order/overflow, first-error capture and same-cycle priority, perf counters (snapshot, clear) *(P2: test_csr_top)* |

### AXI master (P2)
| ID | Test |
|---|---|
| AXI-01 | Reads/writes of every length 1..4096 B at every start offset mod 64, DATA_W 64 and 128 |
| AXI-02 | 4 KB crossing splits; max burst; byte strobes at both ends |
| AXI-03 | Random latency 0..200 clk and random back-pressure on all 5 channels |
| AXI-04 | RRESP/BRESP errors → ERR_STATUS, ERR_ADDR, halt, no hang, soft-reset recovery |
| AXI-05 | ADDR_WIN violations: not issued, error reported |
| AXI-06 | Outstanding limit respected; interleaved IDs from multiple read clients |
| AXI-07 | Soft-reset stop: nothing new issued, outstanding bursts complete, producer can finish, then clear |
| AXI-08 | v1.9 throttle (arch.md §4.2.1): per-channel outstanding limit and request gap held at the port under long latencies; limits changed while a request waits (VALID never withdrawn: monitor + SVA); data exact (`axi07_throttle`) |
| AXI-09 | v1.9 MEM_ATTR: AxCACHE / AxPROT / AxQOS follow the setting, a waiting request keeps its payload (`axi08_attributes`); at the top, random MEM_QOS / MEM_ATTR between kicks, results bit-exact (test_top.py `mem_qos_system`) |

AXI-01..09 run in test_axi_mst.py at DATA_W 64/128, MAX_BURST_BYTES 64/256/4096, ADDR_W 32/48 (P2).

### Command processor (P2)
| ID | Test |
|---|---|
| CMD-01 | NOP/LOAD_STATE/FENCE sequences, state readback through model comparison |
| CMD-02 | Kick queue full/overflow, multiple buffers in flight, sizes 8 B .. 64 KB |
| CMD-03 | Malformed commands: bad opcode, reserved bits, count overflow, command crossing buffer end → CMD_DECODE |
| CMD-04 | Fence ordering: FENCE_SEQ only after the preceding writes are acknowledged (B delayed by the model) |
| CMD-05 | Draw ordering (arch.md §5.3): draw N+1 reads what draw N wrote (render-to-texture chains) *(P3: test_blit cmd05_render_to_texture)* |
| CMD-06 | Draw validation (rendering.md §13) at block level: random state vs the model's validation, every rule including image/gradient/path, with and without the P2 restriction *(P2: test_draw_check)* |

CMD-01..04 run in test_top.py (P2). CMD-01 compares all 64 state registers with the model (white-box).

### Fill / format (P2)
| ID | Test |
|---|---|
| FIL-01 | Clear all 12 target formats, random rect, random stride (≥ min, unaligned allowed), random address alignment |
| FIL-02 | Zero-size, fully clipped, 1×1, 1×N, N×1, full-target, max size 8192×N slices |
| FIL-03 | Scissor random vs target bounds, negative rect coords |
| FIL-04 | BAD_STATE: bad FMT, misaligned address, stride < width·bpp, size 0 or > 8192 |
| FIL-05 | Pixel arithmetic exhaustive: format compression (every format × channel value), global alpha, mul8 *(P2: test_pkg)* |
| SYS-P2 | Random command streams of fills, state noise, NOPs, fences and rejected draws under random AXI profiles; AXI errors on targets and command buffers *(P2: test_top)* |

FIL-01..04 run in test_top.py (P2), DATA_W 64 and 128 (the latter with ADDR_W 48 and MEM_BASE_HI ≠ 0).

### Blend / blit (P3)
| ID | Test |
|---|---|
| BLD-01 | Each blend mode × random S/D values (exhaustive per-channel table for 8-bit pairs at block level) *(P3: test_blend blend_exhaustive, all 65 536 pairs per mode with G2D_FULL=1; test_blit bld01_solid_modes)* |
| BLD-02 | Global alpha src/dst modes, image MULTIPLY, SRC_PREMUL, FORCE_OPAQUE *(P3: test_blend blend_alpha_formats, test_sampler texel, test_blit bld01/bld03)* |
| BLD-03 | Colour key: 0–4 keys, overlapping ranges (priority), boundary values lo/hi *(P3: test_sampler texel, test_blit bld03_image_options)* |
| BLT-01 | Translate-only copies, all src×dst format pairs *(P3: test_blit blt01_copy_formats)* |
| BLT-02 | Scale (up/down, non-integer), rotate (0/90/180/270 and random), shear: point and bilinear *(P3: test_blit blt02_transforms, every kind × source format × filter)* |
| BLT-03 | Perspective: random valid 4-point warps; W ≤ 0 regions; near-singular matrices *(P3: test_xform boundaries/random, test_blit blt03_perspective)* |
| BLT-04 | Source sub-rect (blit_rect), bilinear edge clamping, 1×1 source *(P3: test_sampler tex_addr, test_blit blt04_subrect_clamp)* |
| BLT-05 | Source/target overlap (same buffer, non-overlapping regions) and unaligned source strides *(P3: test_blit blt05_same_buffer)* |
| BLT-06 | Source cache stress: large rotations at 4× downscale (thrash), random tile walks *(P3: test_src_cache scan/tile/thrash walks incl. 2 KB cache and 8-deep FIFO, test_blit blt06_cache_stress)* |
| SYS-P3 | Random streams of fills, blended fills, blits of every kind, NOPs, state noise, fences and rejected (gradient) draws under random AXI profiles; soft reset during a blit *(P3: test_blit random_blits, soft_reset_mid_blit; test_top random_streams also mixes blended fills)* |

Phase 3 block benches also cover the SRAM wrappers and SRAM FIFO (test_common), and the source cache
invalidation with rewritten memory and soft-reset drain (test_src_cache). Blit coefficients in the L3
tests come from the driver geometry (`gm.blit_setup`).

### Path / gradient (P4)
| ID | Test |
|---|---|
| PTH-01 | Random polygons (3..512 edges), both fill rules, AA on/off *(P4: test_path pth01; test_raster vs gm.path_raster)* |
| PTH-02 | Degenerate: horizontal edges, zero-length, collinear, duplicate vertices, edges on sample positions (tie rule) *(P4: test_path pth02; small-dy/tie stimulus in test_raster and model/tests/test_raster_model.py)* |
| PTH-03 | Self-intersecting stars, nested contours of opposite winding, winding ≥ 128 (mod-256 rule) *(P4: test_path pth03, incl. winding ≥ 256)* |
| PTH-04 | Tiny (sub-pixel) and huge (guard-band) shapes, full-frame shapes, bbox clipped by scissor *(P4: test_path pth04, also EDGE_COUNT = 0 and coordinates beyond the guard band)* |
| PTH-05 | Edge list > edge buffer (streaming path) *(P4: test_path pth05_long_lists (per-band streaming), pth05_row_overflow (exactly 512, 513, 1024 active edges per row, all edges in one row); test_raster me64 and forced split points CHUNK 64/13/5 with identical outputs)* |
| PTH-06 | Flattened curves from the driver library (quad/cubic) *(P4: test_path pth06)* |
| IMG-01 | (v1.5, F-1) Image paint on paths: shapes × every source format × filter × wrap mode (BORDER_SOLID 0 / 1) × affine / perspective × blend, global alpha, MULTIPLY, premultiply, TRANSPARENT, AA, fill rules *(test_path img01_image_paths; API: sw/tests/test_pattern_paths.py vs float region × float image sample, test_scenes pattern tests incl. exact fractional AA rectangles)* |
| BLD-04 | (v1.6) Blend families: OpenVG 0–9, SVG2 0–6, Porter-Duff BLEND_FUNC (every factor / equation / channel mode) *(model vs independent reference 6·10⁴ cases, OpenVG vs the header's float formulas; L2 test_blend blend_families; L3 test_blit bld04 (cg_family 22 bins); draw-check random family codes; API test_blend_families_api: enum → BLEND / BLEND_FUNC mapping, replay)* |
| OVG-01 | (v1.6b) `OPENVG_*` formats: each of the 51 header enumerators draws exactly like the VGLite format with its layout (derived from the name); linear ↔ sRGB conversion between spaces; BW_1 rejected; build parameter off: `NOT_SUPPORT` *(API test_openvg_formats (one per format), test_openvg_linear_to_srgb, test_driver_opts, replay)* |
| CLS-01 | (v1.6b) Closed formats (YUYV, *_PLANAR, tiled YUV, ETC2) return `NOT_SUPPORT` on allocate / upload / blit / as target and never draw; `SRC_FMT.YUV_FULL` (reserved) is BAD_STATE *(test_driver t_closed_formats; draw-check random YUV_FULL)* |
| COL-01 | (v1.6b) Colour unit: gamma both ways, identity / diagonal / random / saturating matrices, every source format incl. 24-bit and YUV, POINT / BILINEAR / GAUSSIAN, MULTIPLY after and TRANSPARENT before the unit *(model hand test; L3 test_blit col01; draw-check random COLOR_CTRL (GAMMA 3); API test_color_unit_api: composition from the float API values, solid colour, transformed gradient ramp, clears untouched, image vs the float matrix |Δ| ≤ 3, replay)* |
| FEA-01 | (v1.7) Optional units (`FEATURES`): every unit's draw drawn when built, BAD_STATE when not (model agrees), HWCFG.FEAT_*, base fill / blit in both builds; driver feature bits and NOT_SUPPORT on a build without them *(L3 test_feat full / none; test_driver_opts; lint variant FEATURES = 0)* |
| YUV-01 | (v1.6b) YUV sources: all nine formats, BT.601 / BT.709, UV / VU, odd sizes, POINT / BILINEAR, wrap modes, image paths, plane alignment rules *(model hand conversions; L3 test_blit yuv01 (a U / V swap is caught); draw-check random plane state, extents and alignment; API test_yuv_vs_float: allocate + upload_buffer, every format × standard × swizzle against the float Kr / Kb matrix, |Δ| ≤ 1, A exact, Gaussian rejected, replay)* |
| GAU-01 | (v1.6b) Gaussian filter: signed weights (real Gaussians, all-centre, W0 = 0, sharpening, gain / saturation, random), every source format, every wrap mode, COLOR border texels, image paths, MULTIPLY / premultiply / TRANSPARENT *(model hand test; L2 test_sampler texel_gauss (four groups, stalls); L3 test_blit gau01 (cg_gauss 15 bins); draw-check random GAUSS / GAUSS_W; driver unit test (3 × 3 by hand, argument checks); API test_gaussian_vs_float (PAD / REPEAT / REFLECT, |Δ| ≤ 1))* |
| F24-01 | (v1.6b) 24-bit sources: every format, rows at every line offset (texels crossing lines), filters, wrap modes, paths *(model hand expansions; L3 test_blit f2401; test_pkg / test_stages byte sweeps; API test_source_formats_vs_decoded for the six formats, now read natively)* |
| STN-01 | (v1.6) Stencil image mode, RECOLOR / NONE, A8L8 sources *(model hand test; L2 test_blend blend_stencil (every mode); L3 test_blit stn01 (every blend, formats, paths); draw-check random STENCIL; API test_image_modes_api (state mapping incl. the folded global alpha, replay), test_source_formats_vs_decoded[A8L8])* |
| FLP-01 | (v1.6) Target mirror (`TGT_FMT.FLIP_Y`): fast-path and blended fills, blits (tiled too), paths, image paths, dither *(model hand test; L3 test_blit flp01; API test_mirror: the mirrored image equals the normal one flipped, replay)* |
| BL2-01 | (v1.6) `vg_lite_blit2` = two blits; `vg_lite_set_tess_buffer` accepted *(test_driver)* |
| MSK-01 | (v1.6) Mask layers: random A8 layers (any stride, byte address, origin inside / partly / wholly outside the target) on fills, blits, paths, gradients and image paths, mask switched on and off *(test_blit msk01, msk02 (cg_mask 12 bins); draw-check random mask state; SYS streams; model hand test; API test_mask_layers: every operation exact, render exact, masked clear lerp8(D, C, m) exact, replay)* |
| GRD-03 | (v1.5, F-4) Radial gradient: centred / focal / focal outside / r ≤ 0 × every spread, rects and paths, random gradient matrices, mixed with linear and solid draws *(test_path grd03_radial; L2 test_grad grad_radial incl. u = v = −2^31; model isqrt64 exhaustive / random; API sw/tests/test_radial.py: index ±1 vs the float OpenVG function, Skia two-point conical, replay; cg_radial, cg_paint_path radial)* |
| GRD-01 | Linear gradient: all spreads, gradient length 1 px .. 8192 px, all angles, ramp reload between draws *(P4: test_path grd01_gradients, grd01_ramp_reload; test_grad block level)* |
| PRF-01 | Throughput vs bandwidth.md: cycles/pixel for fill, blit, path with ideal memory (zero latency, no back-pressure) and with 90-clk latency *(P4: test_top fill_throughput, test_blit blit_throughput, test_path prf01; numbers in arch.md §6.1)* |
| AXI-08 | Edge fetch, destination read and write-back under 150–200-clock latency and 30 % ready on all channels: no deadlock, bit-exact *(P4: test_path axi08, Phase 4 condition A1)* |
| SYS-P4 | Random streams of paths (solid and gradient), fills, blended fills and blits under random AXI profiles *(P4: test_path random_paths)* |

### Full GPU (P5)
| ID | Test |
|---|---|
| SYS-01 | Random command streams: 1..200 mixed draws, random state, formats, strides, several targets |
| SYS-02 | Latency/back-pressure sweeps (grid of profiles) on SYS-01 seeds |
| SYS-03 | Reset (hard and soft) at random points of SYS-01 streams; GPU recovers and the next stream is exact |
| SYS-04 | Error injection (AXI errors, address window, malformed commands) mid-stream |
| SYS-05 | DATA_W 64 vs 128: identical memory images for identical seeds |
| SYS-06 | LVGL-like frames: replay of command streams recorded from the SW backend running LVGL demos (Phase 6 onward) |

As built (Phase 5): `dv/tests/test_sys.py`, one cocotb test per SYS-01..04 in both configurations (dw64,
dw128 with 48-bit addresses), and `test_sys05_same` comparing the SYS-01 memory digests of a seed.
Short mode (`make test`): 1..40 commands, 2 SYS-02 profiles, 2 resets, 3 error kinds; full mode
(`G2D_FULL=1`): 3 streams of 1..200 commands, all 8 SYS-02 profiles, 6 resets, all 7 error kinds
(decode, invalid state, window, AXI write, AXI read of source / edge list / command buffer). Predicted
errors are compared exactly; for AXI errors the error bit, halt, ERR_ADDR (inside a burst the memory
answered with an error) and ERR_CMD_ADDR (inside or just past the buffer) are checked and the stream's
targets are excluded from the compare. Phase 6 adds `test_top.axi_rd_cmd_settle` (B-6.4): after an AXI_RD
error on command words no FENCE and no AW may follow once HALTED and `drained` are both seen.

As built (Phase 6): **SYS-06** (`dv/tests/test_sys06.py`) replays recordings of the software backend
(`G2D_RECORD`, format in sw/hal/model/g2d_hal_model.h) on `g2d_top` in both configurations, each recording under
its own random AXI profile; kicks without new memory contents are queued back to back (kick-queue
back-pressure). Checks: every CHECK page bit-exact against the software backend, the whole memory, error / fence
registers and white-box state against the golden model. Short mode: the API scene generators of
sw/tests/test_scenes.py and two widgets frames at 320×240; full mode adds the 16-bit widgets build, the vector demo
and 150 benchmark frames.

### Clock-domain crossing (v1.1; arch.md §2.1)

| ID | Test | File |
|---|---|---|
| CDC-01 | `g2d_cdc_fifo` random valid / ready on both sides, random clock periods (ps resolution) and phase: order, no loss / duplication, data held while stalled; SVA: gray pointers change one bit per clock, the true (cross-domain) word count stays in 0..DEPTH | dv/tests/test_cdc.py `cdc_random`, dv/sva/g2d_cdc_sva.sv |
| CDC-02 | Throughput with both sides always ready at five clock ratios: ≥ 0.90 words per cycle of the slower clock at depth ≥ 2·(SYNC_STAGES + 2) without jitter (measured 0.95–0.985 at depth 8); first-word latency ≤ one source + (SYNC_STAGES + 2) destination cycles | `cdc_throughput` |
| CDC-03 | Both resets asserted together mid-stream, released per domain at random edges: empty afterwards, streams again | `cdc_reset` |
| CDC-04 | Top level with `CFG_ASYNC = MEM_ASYNC = 1` at random clock ratios per seed: the L3 top bench and SYS-01..04 bit-exact against the golden model as in the single-clock build (hard and soft resets included); at every `soft_clr` all memory-port CDC FIFOs are truly empty | dv/tests/test_top.py, test_sys.py (configuration `async`) |
| CDC-05 | Formal (SymbiYosys, multiclock): `g2d_cdc_fifo` never overflows / underflows, gray pointers step by one bit, and words leave in the order they entered (tagged-word proof) | dv/formal/, `make formal` |

Configurations of CDC-01..03: config-port default (depth 2), memory-port default (depth 8), 3 synchronizer
stages, and two jitter builds (`G2D_CDC_JITTER`: each synchronizer randomly takes extra cycles, ±10 % clock
jitter). Verilator is two-state and cannot model metastability itself; the jitter build stands in for it.

### Phase 6 API and backend tests

| ID | Test | File |
|---|---|---|
| API-01 | Driver unit test (every API entry, error codes, queries) on the software backend and on the live RTL | sw/tests/test_driver.c, test_live.py |
| API-02 | API scenes (clears, blits, paths / gradients, patterns, converted formats, strokes): stream replay on REF bit-exact, API → state-word mapping, IDEAL for paths and bilinear blits | sw/tests/test_scenes.py |
| API-03 | Strokes vs the exact stroke region (distance, caps, joins, miter limit, degenerate input, transforms), no cracks (B-6.3) | sw/tests/test_stroke.py |
| API-04 | Live RTL backend: the same programs and scenes, outputs byte-identical to the software backend (random AXI profiles, kick queue full, fences, IRQ) | sw/tests/test_live.py |
| API-05 | SYS-06 replay (above) | dv/tests/test_sys06.py |
| API-06 | Third party: Skia (blends 0–10, image draws, gradients, fills, strokes), each vs IDEAL so a disagreement is attributed | sw/tests/test_thirdparty.py, test_stroke.py |
| API-07 | LVGL screenshots vs LVGL's software renderer (below), vector demo vs ThorVG, three-way with LVGL's vg_lite_tvg simulator | sw/tests/test_lvgl.py |
| API-08 | v1.3 path features: arcs vs OpenVG Appendix A's construction (all opcodes, scaled radii, affine matrices, strokes), BREAK exact vs MOVE, projective path matrices vs inverse-mapped samples (fills, strokes, near-plane clip), perspective path vs blit | sw/tests/test_paths_ext.py |
| API-09 | v1.3 dashes vs an independent interval reference (odd counts, zero / negative entries, phase, reset, dots, exact fit, matrices) and vs Skia `SkDashPathEffect`; fractional AA pattern rectangles (Q-API-14 (b)); `scene_vectors` in API-02 / 04 / 05; command buffer / memory pool / frame delimiter in API-01 | sw/tests/test_stroke.py, test_scenes.py, test_driver.c |

Every cell of the resulting feature × model matrix (exact / tolerance / documented difference / not applicable)
is in docs/VERIFICATION_MATRIX.md.

### v1.4 hardware features (rendering.md §3.2, §3.5, §3.6, §5.2, §7.5, §10.1; arch.md §8)

| ID | What | Where |
|---|---|---|
| FMT-01 | Every format code: expand (all raw values up to 16 bit), compress, dither at every Bayer position, code classification, vs the model (exhaustive) and the model vs the independent reference; every source format incl. sub-byte (both SUB_ORDER) and indexed (CLUT) through the L3 pipeline into every target format | dv/tests/test_pkg.py, model/tests/test_stages.py, test_ops.cpp, dv/tests/test_sampler.py, test_blit.py `fmt01_v14_sources` |
| WRP-01 | Wrap modes NONE / COLOR / PAD / REPEAT / REFLECT: texel addressing vs a spec transcription (every pixel size, seams, far negative coordinates, periods 1..8192, back-to-back draws); the RTL's reciprocal modulo exhaustively in the model test; xform signed range boundaries; seam quads through the cache as single-texel requests; L3 wrap draws (affine, down-scaled, perspective, sub-rect tiles; COLOR with BORDER_SOLID 0 / 1, cg_border) | test_sampler.py `tex_addr`, test_ops.cpp `test_wrap_reciprocal`, test_xform.py `xform_wrap_boundaries`, test_src_cache.py `cache_wrap_seams`, test_blit.py `wrp01_wrap_modes` |
| DTH-01 | Dither: package exhaustive, blend unit at random positions, dithered fills (leave the fast path) and blits on every target, API clear vs the formula | test_pkg.py, test_blend.py `blend_dither`, test_blit.py `dth01_trn01_dither_transparent`, sw/tests/test_scenes.py `test_dither_formula` |
| TRN-01 | TRANSPARENT: A = 0 results not written (texel unit A = 0 flag before MULTIPLY), API per-pixel check | test_sampler.py `texel`, test_blit.py `dth01_trn01_dither_transparent`, test_scenes.py `test_transparent_skips_alpha0` |
| CLT-01 | CLUT in the ramp RAM: reuse with the same address (also after a gradient), reload after a kick / GPU write / CPU rewrite; draw-check alignment and range | test_blit.py `clt01_clut_reuse`, test_draw_check.py, model/tests/test_executor.py |
| GRD-02 | SPREAD = FILL: gradient index unit, L3 rects and paths, API vs the float position (paint colour outside, exact) | test_grad.py, test_path.py `grd01_gradients`, test_scenes.py `test_gradient_spread_through_driver[FILL]` |
| CSR-08 | `STATUS.CMD_ACTIVE` (Q-RTL-9): halt settled with a kick queued (IDLE low), cross-checked against `drained` in every halted wait | test_top.py `axi_rd_cmd_settle`, dv/common/g2d_env.py `wait_done` |
| API-10 | Driver: every non-8888 source format vs an independently decoded 8 / 32-bit image (blits, both filters, patterns; indexed both endians, BILINEAR = POINT; 24-bit conversion cache), patterns on the hardware wrap modes (texel identity incl. PAD, COLOR, perspective; bilinear seams wrap), pattern colour multiplied (TEXEL, default) or plain (SOLID, `test_pattern_color_solid`, Q-API-12); LVGL D9 band fixed at 1 texel, auto-checked, bit-exact RTL vs golden model in both modes (`test_lvgl.py::test_formats_band_on_rtl`) | sw/tests/test_scenes.py `test_source_formats_vs_decoded`, `test_pattern_*` |

### v1.9 integration (docs/PHASES.md "v1.9 plan")

| ID | Description | Test |
|---|---|---|
| ADP-01 | Every configuration-side adapter (`rtl/adapt/g2d_<bus>2axil`): random reads / writes, strobes, address bits, error words, READY / latency profiles; each bus access reaches AXI4-Lite exactly once, in order, with address, data and strobes; read data and errors bit-exact; AXI4-Lite rules every cycle (dv/common/axil_slave.py), bus rules in the BFM (dv/common/bus_bfm.py) | test_adapt.py `adp01_random` |
| ADP-02 | Back-to-back accesses at the bus's fastest legal rate | test_adapt.py `adp02_back_to_back` |
| ADP-03 | Reset while an access waits for its response; the adapter works afterwards | test_adapt.py `adp03_reset_mid_access` |
| ADP-04 | Bus-specific errors: AHB transfer wider than 32 bits / unaligned (two-cycle ERROR, no access), TL-UL opcodes outside TL-UL (error, no access), Wishbone cycle dropped before ack (no late ack) | test_adapt.py `adp04_bus_errors` |
| ADP-05 | AXI4 adapter (`g2d_axi2axil`, DATA_W 32 / 64 / 128 / 512, ADDR_W up to 64, ID_W 1..6): random INCR / FIXED / WRAP bursts, narrow and unaligned transfers, random strobes and IDs, error words; every AXI4-Lite word access predicted by an independent model of the AXI4 beat-address rules, B / R responses, IDs, RLAST and data bit-exact | test_axi2axil.py `adp05_bursts` |
| ADP-06 | AXI4 adapter: writer and reader at the same time (bursts alternate), nothing lost | test_axi2axil.py `adp06_concurrent_rw` |
| ADP-07 | AXI4 adapter: exclusive accesses answered OKAY (exclusive failure) and performed normally | test_axi2axil.py `adp07_exclusive_okay` |
| ADP-08 | L3 smoke through every generated wrapper `g2d_top_<bus>` (AXI4, APB, AHB-Lite, OBI, reg_intf (with CFG_ASYNC / MEM_ASYNC), Wishbone, TL-UL): CSR reset values, IRQ, fills in all formats, random command streams, memory QoS, bit-exact against the golden model | test_wrap.py |

### Phase 7 FPGA tests (L5; docs/FPGA.md)

| ID | Test | File |
|---|---|---|
| FPG-01 | G2D out of context on the Genesys2 part (xc7k325t-2): placed and routed at 50 MHz with positive slack; utilization and worst paths recorded | `make fpga-ooc`, fpga/genesys2/g2d_ooc.tcl |
| FPG-02 | Cheshire + G2D bitstream: synthesis, implementation, all timing constraints met (impl_g2d.tcl fails otherwise) | `make fpga`, fpga/genesys2/impl_g2d.tcl |
| FPG-03 | SoC simulation (Verilator): Cheshire boots the bare-metal test, the CSRs read through the bridge, the API scene subset runs bit-exact against the software backend (every return code, every buffer CRC-32), the G2D interrupt reaches the PLIC as source 58 | `make fpga-sim`, fpga/sim/tb_g2d_chs.sv, scripts/fpga_check.py |
| FPG-04 | Board run: the same test on the Genesys2 (2 scenes per kind), bit-exact; IRQ test; per-scene busy cycles on DDR3 | fpga/scripts/run_board.sh (user), scripts/fpga_check.py |
| FPG-05 | Async build (v1.1): G2D OOC at 100 MHz; Cheshire + G2D bitstream with the G2D core at 100 MHz and both ports through the CDC, timing met with the CDC constraints; SoC simulation (`--async`, core clock at an arbitrary phase) of the 6 scene kinds bit-exact + PLIC interrupt; board run `run_board.sh --async` | `make fpga-ooc FPGA_MHZ=100`, `make fpga FPGA_ASYNC=1`, `make fpga-sim FPGA_ASYNC=1` |

FPG-03 and FPG-04 compare against the golden model through the same driver code, so a difference is in the
integration (bridge, address map, cache maintenance, interrupt) or in the hardware, not in the expected values.

## 4. Functional coverage model

Collected in Python (cocotb), from the state the scoreboard sees at each draw. Report: `dv/coverage/` →
`build/coverage/report.md` (and JSON for merging across seeds).
As built (Phase 3): `dv/common/g2d_coverage.py` samples every draw the golden model executes, per
process, into `build/coverage/<bench>_<config>_s<seed>.json`; `scripts/coverage_report.py` merges them
(`make test` prints the report, `make regress` fails below 100 %). Implemented groups: `cg_blend`,
`cg_blend_alpha`, `cg_blit` (transform class from the XF coefficients: translate, scale up/down,
rotate 90k, free rotation, shear, perspective), `cg_blit_dst`, `cg_blit_opts` (Phase 3); `cg_path`
(size class from the bbox, guard-band when a coordinate reaches ±16383 px), `cg_paint_path`, `cg_grad`
(angle and length from GRAD_P/GRAD_Q) (Phase 4); `cg_fill`, `cg_align`, `cg_axi`, `cg_errors` (Phase 5,
definitions in the module header: `cg_axi` samples every AR/AW handshake of the AXI model, length classes
relative to MAX_BURST_BYTES, no max-length/unaligned bin since the master never issues one; `cg_errors`
samples each ERR_STATUS bit as it is set, "busy" when a draw engine is busy). Directed closure tests:
`test_top` `cov_fill_matrix`, `cov_errors_idle`.

| Covergroup | Bins | Goal |
|---|---|---|
| `cg_fill` | dst FMT (12) × rect class {empty, 1px, thin, normal, full, clipped} × scissor {off, on-partial, on-empty} | 100 % |
| `cg_blend` | blend mode (16) × dst FMT (12) | 100 % |
| `cg_blend_alpha` | blend (16) × src global-alpha mode (3) × dst global-alpha mode (3) | 100 % |
| `cg_blit` | src FMT (12) × filter (2) × transform {translate, scale-up, scale-down, rotate-90k, rotate-free, shear, perspective} | 100 % |
| `cg_blit_dst` | src FMT (12) × dst FMT (12) | 100 % |
| `cg_blit_opts` | image mode (2) × SRC_PREMUL (2) × FORCE_OPAQUE (2) × ckey count {0,1,2-3,4} | 100 % |
| `cg_path` | fill rule (2) × AA (2) × size class {sub-pixel, small, medium, large, guard-band} × edges {≤8, ≤64, ≤512, >512} | 100 % |
| `cg_paint_path` | paint {solid, gradient} × blend (16) | 100 % |
| `cg_grad` | spread (3) × angle class {0, 90, 45, other} × length class {<16, <256, ≥256 px} | 100 % |
| `cg_axi` | len {1, short, long, max} × 4 KB offset {aligned, ends at 4 KB, burst-aligned, other} × DATA_W × {read, write} (60 bins) | 100 % |
| `cg_errors` | each ERR_STATUS bit × {idle, busy} | 100 % |
| `cg_align` | (address mod 64) class × (stride mod 64) class × bpp (1/2/4) | 100 % |

Code coverage (Verilator `--coverage`, line + toggle) is collected from Phase 5. Goal: line ≥ 95 % on
`rtl/` excluding generated CSR read-mux defaults. Exceptions are documented.
As built (Phase 5): `make code-coverage` (scripts/code_coverage.py) runs the make-test suite with
coverage builds and writes `build/coverage/code/report.md`. Lines kept out of the figure are listed one by
one in three classes: excluded (simulation-only `ifndef SYNTHESIS` code; generated read-mux default and
elaboration-only package functions), not measurable (return-only `case` items, no Verilator count; covered
by the functional groups), exceptions (FSM `default:` recovery arms). The raw figure is reported as well.
Phase 5 result: 100 % of the counted lines (1198), raw 91.5 %; toggle 93.2 % (no goal).

## 5. Corner cases checklist (cross-referenced by tests above)

Zero/negative/oversized rects · fully clipped draws · width 1, height 1 · 8192 limits · stride = width·bpp
exactly and stride with padding · address alignment 1/2/4 B (not 64) · 4 KB crossings within a row and
between rows · source equals target buffer · RGB565 rounding round trips · L8 conversion · A8 source in
MULTIPLY mode · global alpha 0 and 255 · colour key range lo > hi (never matches) · bilinear at u/v exactly
on texel centres and at the rect border · perspective with W → 0⁺ and W ≤ 0 · matrix with det = 0 (driver
rejects) · path edges exactly through sample points · horizontal edges · winding ≥ 256 · AA coverage 1/16
and 15/16 · gradient length 1 px, REFLECT boundaries at 255/256 · FENCE without draws · back-to-back kicks
· kick overflow · soft reset with zero and maximum outstanding transactions.

### LVGL screenshots (Phase 6)

`sw/tests/test_lvgl.py`: an LVGL demo rendered with simulated time by two builds of the same LVGL
(sw/apps/lvgl): the VG-Lite draw unit on the G2D driver (software backend) and LVGL's own software
renderer (the reference, Phase 6 answer Q3). The renderers differ by design in anti-aliasing and rounding,
so the criterion is statistical, fixed before the first comparison (2026-10-17): per frame and channel,
`|Δ| ≤ 24` on at least 99.5 % of pixels, mean `|Δ|` ≤ 1.0, PSNR ≥ 32 dB. Frames are also checked to be
non-trivial (the reference differs from a cleared screen). A frame that fails is investigated (driver bug
or a real rendering difference) before any tolerance changes.

As built (2026-10-17): the criterion is evaluated on B, G, R (alpha is not compared); a pixel is "off" when
any channel has `|Δ| > 24` (stricter than per channel); every non-SUCCESS vg_lite return that LVGL logs fails
the test. Demos: widgets (60 frames, dumps 30 / 60) and benchmark (dumps every 60 frames, up to 180 in short
mode, 600 with `G2D_FULL=1`), 32- and 16-bit builds.

**Rotated-image frames** (decision 2026-10-17, OPEN_QUESTIONS Q-TEST-2): LVGL's software transform samples
rotated images up to ~0.7 px off the pixel centre, so their edges differ from ours (exact pixel centre,
rendering.md §7.1). For the frames listed in `ROTATED` in test_lvgl.py (now: benchmark frame 600, ten
rotated logo images) the criterion is PSNR ≥ 28 dB, at most 2 % off pixels, mean `|Δ|` ≤ 1.0, **and** every
off pixel within 1 px of an image edge of the reference frame (local gradient > 48), which pins the cause.
Measured: 32-bit 30.0 dB / 1.08 % / 0.75, 16-bit 29.9 dB / 1.12 % / 0.81; off pixels 100 % within 1 px of an
image edge. Exactness of the rotated blits is covered by test_scenes.py against the float reference.

### Mutation testing (Phase 5)

`scripts/mutate.py` measures whether the tests notice RTL changes: deterministic operator mutants
(relational / equality swaps, `&`/`|`, `&&`/`||`, dropped `~` / `!`, constant 0/1 in assignments, ±1 → 0) on
run-time code (comments, declarations, elaboration-only lines, simulation-only blocks and generated files
skipped), four lines per file. A mutant is killed when one of its kill tests fails (block bench of the
file, then the top-level benches of both configurations; hangs end through `G2D_WAIT_LIMIT` /
`G2D_FAIL_FAST`). Every survivor is triaged: equivalent (reasoned in docs/STATUS.md), performance-only,
or a test gap that gets a test, after which the mutant is rerun (`--ids`). Phase 5: 130 mutants, 108
killed at first; 7 gaps closed (exact checks of the BUSY / DRAIN / RD_STALL / WR_STALL counters, an AXI
slave that raises READY only after VALID, arbiter widths 2 and 4), 115 killed after the rerun; the other 15
are 14 equivalent and one performance-only (texel-cache victim choice).

## 6. Regression structure

| Tier | Content | When | Target time |
|---|---|---|---|
| smoke | `make test` (= `make smoke`): unit tests, every block and top-level bench once (seed 1, short mode), API / stroke / third-party / LVGL (short) / live suites (when built), SYS-06 short | every commit | < 5 min (32 jobs) |
| regress | `make regress`: model suites and every bench in full-length mode × `REGRESS_SEEDS` (default 1–5) | phase end, before merge | < 2 h (32 jobs) |
| phase 5 | SYS 300 seeds × both DATA_W; benches full-length × 10 new seeds (101–110) | phase 5 end | hours (16 jobs, limit then) |
| phase 6 | `make regress` also runs, full-length once: LVGL screenshots (all demos, benchmark 600 frames), the live RTL backend (all scene kinds × 3, LVGL frames), SYS-06 full; Phase 6 end additionally re-runs the phase 5 tier on new seeds (111–120) and code coverage | phase 6 end | hours (16 jobs, limit then) |
| nightly | `scripts/regress.py --seeds …` + coverage merge + image-compare regression | on request | hours |

- Every random test takes `SEED` (environment variable). `G2D_FULL=1` selects full-length mode.
- `scripts/regress.py` runs one pytest node (bench × configuration) per seed: simulators are built once,
  runs go in parallel (at most 32 jobs, project limit since 2026-10-17; 16 before), longest first. Output in `build/regress/<run>/`:
  `summary.md` (pass/fail per node, failures grouped by first error message with the exact rerun
  command `SEED=… G2D_FULL=… python3 -m pytest -q '<node>'`), `junit.xml`, one log per run, and the merged
  functional coverage report.
- Golden reference images for L4/L5 live in `sw/tests/refs/` (small PNGs only, whitelisted in .gitignore).
- No test is ever marked "expected fail" silently. Known failures are listed in docs/STATUS.md with an ID.

## 7. Exit criteria (summary; phase details in docs/PROJECT_RULES.md and docs/PHASES.md)

- P2: CSR/AXI/CMD/FIL tests green for DATA_W 64 and 128, lint clean.
- P3: BLD/BLT green, coverage groups cg_blend/cg_blit/cg_blit_dst/cg_blit_opts at 100 % or gaps documented.
- P4: PTH/GRD green, PRF-01 numbers recorded in docs/STATUS.md.
- P5: SYS green with ≥ 300 seeds, all coverage groups at goal or gaps justified.
- P6: API-01..07 green; docs/VERIFICATION_MATRIX.md complete (no empty cell); every disagreement resolved or
  documented; phase 5 tier re-run green on the fixed RTL.
- P7: FPG-01..03 green here; FPG-04 green on the board (run by the user); Q-FPGA items resolved or re-dated.
