# VGLite API subset for v1

Reference header: `sw/include/vg_lite.h` (4.0.135, see `spec/vglite-reference.md`).
Exact pixel behaviour: `spec/rendering.md`. Mapping to hardware commands: `spec/commands.md`.

Legend: **v1** = implemented in hardware and the software backend in v1. **SW** = implemented purely in
the driver (no hardware involvement). **later** = returns `VG_LITE_NOT_SUPPORT` in v1.
Any function in the header but not listed here also returns `VG_LITE_NOT_SUPPORT` (or is a no-op where
the header type is `void`).

Priority (P1 is highest) gives the implementation order across Phases 2-6.

**Status (Phase 6 end, 2026-10-17):** every entry marked v1 or SW below is implemented in
`sw/driver/vg_lite` and runs on all three backends (software / golden model, RTL replay SYS-06, live RTL);
tests API-01..07 of spec/verification-plan.md, feature × model results in docs/VERIFICATION_MATRIX.md.
Phase 6 additions beyond the original subset (driver-only, no RTL change): `vg_lite_draw_pattern` on
rectangular paths, solid strokes, sources in A4 / BGRA5551 / BGRA4444 / BGRA2222 (converted by the driver),
REPEAT / REFLECT spread reported by `gcFEATURE_BIT_VG_IM_REPEAT_REFLECT`. Everything marked later returns
`VG_LITE_NOT_SUPPORT`; the calls LVGL v9.6 makes that still fail are ranked in docs/LVGL_SURVEY.md.

## 1. Context, memory, submission

| Function | v1 | Notes |
|---|---|---|
| `vg_lite_init(w, h)` | v1 | Opens the device and allocates command buffers. `w,h` are ignored (no tessellation buffer needed). |
| `vg_lite_close` | v1 | |
| `vg_lite_flush` | v1 | Kicks the current command buffer, non-blocking. |
| `vg_lite_finish` | v1 | Kick plus FENCE, then waits (IRQ or polling, depending on backend). |
| `vg_lite_allocate`, `vg_lite_free` | v1 | Contiguous buffers. Stride = `align(width*bpp, 64)` by default. |
| `vg_lite_upload_buffer` | v1 | Copies planar data into the buffer (CPU). Only plane 0 for v1 formats. |
| `vg_lite_map`, `vg_lite_unmap` | v1 | Wraps an existing physical buffer. Requires the alignment rules in rendering.md §3. |
| `vg_lite_flush_mapped_buffer` | v1 | CPU cache maintenance hook (no-op on coherent backends). |
| `vg_lite_get_info`, `vg_lite_get_product_info`, `vg_lite_query_feature` | v1 | Reports the G2D product name and ID. Feature bits are true only for v1 features. |
| `vg_lite_get_register` | v1 | Reads the G2D CSR space (debug). |
| `vg_lite_set_command_buffer_size` | v1 | |
| `vg_lite_get_mem_size` | v1 | |
| `vg_lite_set_command_buffer(physical, size)` | v1.3 | After `vg_lite_init`: the caller's 64-byte aligned GPU memory (`NOT_ALIGNED` otherwise) becomes the driver's two alternating command buffers (`size / 2` each, rounded down to 64 bytes, at least 4 KB, else `INVALID_ARGUMENT`; memory the HAL cannot map: `INVALID_ARGUMENT`). The driver waits for idle first; the memory stays the caller's. |
| `vg_lite_set_memory_pool(type, pool)` | v1.3 | One pool (the HAL's): `VG_LITE_MEMORY_POOL_1` succeeds for every buffer type, `POOL_2` returns `NOT_SUPPORT`, unknown values `INVALID_ARGUMENT`. |
| `vg_lite_frame_delimiter(flag, stall)` | v1.3 | `VG_LITE_END_FLAG` / `VG_LITE_FRAME_END_FLAG`: flush (the buffer's FENCE raises the interrupt); `stall` also waits for idle (finish). Other flags `INVALID_ARGUMENT`. |
| `vg_lite_set_tess_buffer` | v1.6 | Accepted and unused (no tessellation buffer: paths are flattened on the CPU). |
| `vg_lite_cache_command`, `vg_lite_dump_*`, `vg_lite_set_dump_api` | closed (v1.6) | Vendor debugging / command caching; `NOT_SUPPORT` (no equivalent needed: command buffers are ours). |
| FLEXA (`vg_lite_flexa_*`) | closed (v1.6) | Vendor streaming interface to a display controller, not publicly specified, so it cannot be implemented from public documentation; `NOT_SUPPORT`. |

## 2. Fill, clear, blit (P1-P3)

| Function | Prio | v1 | Notes |
|---|---|---|---|
| `vg_lite_clear(target, rect, color)` | P1 | v1 | Solid fill, no blend, ignores global alpha/colour key. Honours the scissor (OPEN_QUESTIONS Q-API-1). `rect==NULL` means the whole target. |
| `vg_lite_blit(target, src, matrix, blend, color, filter)` | P2 | v1 | Full 3x3 matrix. Affine and perspective. |
| `vg_lite_blit_rect(target, src, rect, matrix, blend, color, filter)` | P2 | v1 | `rect` is the source sub-rectangle. |
| `vg_lite_copy_image` | P2 | v1 | Driver maps it to a translate-only blit with `VG_LITE_BLEND_NONE`, point filter. |
| `vg_lite_blit2` | — | v1.6 (driver) | The two blits in order (source0 with matrix0, then source1 with matrix1), the same blend and filter; no colour parameter: MULTIPLY-mode sources use white (assumption, v1.6 question 10) |
| `vg_lite_set_mirror` | — | v1.6 | `BOTTOM_TOP` stores target row y at row H − 1 − y for later draws (`TGT_FMT.FLIP_Y`, rendering.md §5.8) |
| `vg_lite_gaussian_filter(w0, w1, w2)` | — | v1.6b | Any finite weights (negative, any sum; the result saturates); NaN / Inf: `INVALID_ARGUMENT`; s3.12, unity-gain kernels exact; build option `G2D_DRV_STRICT_GAUSSIAN` for the strict check (rendering.md §9.10). Default before the first call: `[1 2 1] × [1 2 1] / 16` (assumption) |
| `vg_lite_set_pixel_matrix(m, channel)`, `vg_lite_set_color_transform`, `vg_lite_enable_color_transform` / `_disable_`, `vg_lite_set_gamma` | — | v1.6b | Colour unit (rendering.md §5.2.1 / §9.10): gamma, then one 4 × 5 integer matrix (pixel matrix first, then the transform, composed on the CPU; biases normalised, 1.0 = 255). Image colours in hardware, solid colours / ramps / solid border by the driver; not on clears and mask-layer operations. `GAMMA`, `COLOR_TRANSFORMATION`, `PIXEL_MATRIX` feature bits = 1 |

Filters: `VG_LITE_FILTER_POINT` (P2), `VG_LITE_FILTER_BI_LINEAR` (P2), `VG_LITE_FILTER_LINEAR` (treated
as bilinear, P2), `VG_LITE_FILTER_GAUSSIAN` (v1.6b, 3 × 3 in hardware at ¼ px/clk, rendering.md §7.3; indexed
sources stay point-sampled, YUV sources `NOT_SUPPORT`).

Image modes (`vg_lite_buffer_t.image_mode`): `VG_LITE_NORMAL_IMAGE_MODE` and `VG_LITE_ZERO` (normal),
`VG_LITE_MULTIPLY_IMAGE_MODE` (P2). v1.6: `VG_LITE_STENCIL_MODE` (OpenVG 1.1 §10.8 in the premultiplied form,
rendering.md §5.3.1; the §5.6 blends only, global alpha folded into the paint alpha), `VG_LITE_RECOLOR_MODE` (=
MULTIPLY with the draw colour, UNVERIFIED), `VG_LITE_NONE_IMAGE_MODE` (nothing is drawn, success).

A8 / A4 / A2 / A1 sources are premultiplied white `(a, a, a, a)` under the Porter-Duff blends 0–10 and plain
white `(a, 255, 255, 255)` under the LVGL blends 11–15, which multiply by S.A themselves (OPEN_QUESTIONS Q-API-3,
B-6.2): the driver sets `SRC_PREMUL` accordingly.

Buffer fields honoured in v1: `width, height, stride, format, address, memory, image_mode,
premultiplied` (source only, meaning "data is already premultiplied"), `apply_premult` (source:
premultiply on read), `transparency_mode` (OPAQUE: no effect on alpha; LVGL sets `VG_LITE_IMAGE_OPAQUE` on every buffer, A8 glyphs
included, so OPAQUE cannot mean "force A = 255"; OPEN_QUESTIONS Q-API-11. The hardware FORCE_OPAQUE bit,
rendering.md §5.2, is not used by the driver. v1.4: `VG_LITE_IMAGE_TRANSPARENT` skips pixels whose sampled source
has A = 0, rendering.md §5.2 step 6, UNVERIFIED), `index_endian` (indexed sources, v1.4).
`tiled` must be `VG_LITE_LINEAR`, `compress_mode` must be none, and `fc_enable` must be 0. Otherwise the
call returns `VG_LITE_NOT_SUPPORT`.

## 3. State: matrices, scissor, blend, alpha, colour key (P1-P3)

| Function | Prio | v1 | Notes |
|---|---|---|---|
| `vg_lite_identity`, `vg_lite_translate`, `vg_lite_scale`, `vg_lite_rotate` | P2 | SW | Float math in the driver. |
| `vg_lite_get_transform_matrix` | P2 | SW | Computes the perspective (4-point) matrix. |
| `vg_lite_set_scissor(x, y, right, bottom)` | P1 | v1 | One rectangle `[x,right) x [y,bottom)`; `(-1,-1,-1,-1)` disables it (rendering.md §6). |
| `vg_lite_enable_scissor`, `vg_lite_disable_scissor` | P1 | v1 | |
| `vg_lite_scissor_rects(target, n, rects)` | P3 | v1; n > 1 v1.6 | n > 1 (v1.6, driver-only): the union of the rectangles as disjoint pieces (up to 256, else `OUT_OF_RESOURCES`); every later draw is emitted once per piece with its clip (rendering.md §6); n = 0 disables the scissor. |
| Blend `VG_LITE_BLEND_NONE … VG_LITE_BLEND_SUBTRACT` (0-10) | P2 | v1 | |
| Blend `VG_LITE_BLEND_NORMAL_LVGL … MULTIPLY_LVGL` (11-15) | P2 | v1 | LVGL needs these. |
| Blend `OPENVG_BLEND_*` (0x2000–0x2009), `SVG2_BLEND_*` | — | v1.6 | Hardware blend families (rendering.md §5.7): the header's OpenVG formulas with the divide by the result alpha; SVG2 = W3C Compositing Level 1 separable modes with source-over (G2D choice, no formulas in the header); A8 sources are not premultiplied for them |
| `VG_LITE_PORTER_DUFF_BLEND`, `vg_lite_blend_func` | — | v1.6 | Factors, equation and channel modes in `BLEND_FUNC` (§5.7 family 3, UNVERIFIED interpretation; the constant is the draw's colour); invalid enum values `INVALID_ARGUMENT` |
| `vg_lite_source_global_alpha(mode, value)` | P2 | v1 | `NORMAL`, `GLOBAL`, `SCALED`. |
| `vg_lite_dest_global_alpha(mode, value)` | P3 | v1 | |
| `vg_lite_set_color_key(keys[4])` | P3 | v1 | Applies to image sources only. |
| `vg_lite_enable_dither`, `vg_lite_disable_dither` | — | v1.4 | Own ordered 4 × 4 dither on channels below 8 bits (rendering.md §3.6; listed difference). |
| `vg_lite_set_CLUT` | — | v1.4 | Up to 256 ARGB entries (rendering.md §3.5, §9.6). |
| `vg_lite_g2d_set_pattern_color_mode` (G2D extension, `sw/include/vg_lite_g2d.h`) | — | v1.4 | COLOR-pattern colour as a texel (default) or a plain colour (`PAINT_CTRL.BORDER_SOLID`, rendering.md §7.5; OPEN_QUESTIONS Q-API-12). |
| Mask layers: `vg_lite_create_masklayer`, `_fill_`, `_blend_`, `_render_`, `_set_`, `_enable_`, `_disable_`, `_destroy_masklayer` | — | v1.6 | A8 layers; operations (OpenVG 1.1 §7.2) are draws into the layer; masked draws multiply the coverage by the layer in hardware (rendering.md §6.1, §9.9); outside the layer = masked out |

## 4. Formats

v1 source and target formats (exact layouts in rendering.md §3):

| VGLite format | Source | Target | Prio |
|---|---|---|---|
| `VG_LITE_BGRA8888` (LVGL ARGB8888), `VG_LITE_RGBA8888`, `VG_LITE_ARGB8888`, `VG_LITE_ABGR8888` | v1 | v1 | P1 |
| `VG_LITE_BGRX8888` (LVGL XRGB8888), `VG_LITE_RGBX8888`, `VG_LITE_XRGB8888`, `VG_LITE_XBGR8888` | v1 | v1 | P1 |
| `VG_LITE_BGR565` (LVGL RGB565), `VG_LITE_RGB565` | v1 | v1 | P1 |
| `VG_LITE_A8` | v1 | v1 | P2 |
| `VG_LITE_L8` | v1 | v1 | P2 |
| `*4444`, `*5551` / `*1555`, `*2222` (four channel orders each) | v1.4 | v1.4 | P6 (Phase 6: BGRA orders converted by the driver) |
| `VG_LITE_A4`, `VG_LITE_A2`, `VG_LITE_A1`, `VG_LITE_L4` | v1.4 (first pixel in the high bits, rendering.md §3.5) | — | |
| `VG_LITE_INDEX_1/2/4/8` with `vg_lite_set_CLUT`, `index_endian` | v1.4 (point-sampled) | — | |
| `RGB888` / `BGR888`, `*8565` / `*5658` | v1.6b (hardware, rendering.md §3.2; driver conversion v1.3–v1.6) | — | `gcFEATURE_BIT_VG_24BIT` = 1 |
| `A8L8` | v1.6 (A bits 7:0, L bits 15:8) | — | |
| YUV `NV12`, `ANV12`, `NV16`, `NV24`, `YV12`, `YV16`, `YV24`, `YUY2`, `AYUY2` | v1.6b (hardware, rendering.md §3.7; `yuv2rgb` BT.601 / BT.709 limited range, `swizzle` UV / VU; planes in `buffer->yuv`, allocated after the Y plane by `vg_lite_allocate`, filled by `vg_lite_upload_buffer(data[0..2])`) | — | `YUV_INPUT`, `YUY2_INPUT`, `AYUV_INPUT` = 1. YUY2 planes 4-byte, NV chroma 2-byte aligned. Not with `FILTER_GAUSSIAN` (`NOT_SUPPORT`). `VG_LITE_YUYV` and the `*_PLANAR` 24-bit formats closed (layout not public) |
| Tiled layouts, tiled YUV, ETC2, compressed (DEC) | closed (v1.6) | closed | Vendor layouts / codecs are not publicly specified; `NOT_SUPPORT` |
| `OPENVG_*` formats (sRGB / linear, premultiplied variants) | v1.6b (driver mapping onto the FMT code with the same layout; an `l` / `s` source drawn into the other space is converted by the gamma unit; `OPENVG_A_1` / `A_4` first pixel in the low bits) | v1.6b (same mapping) | `OPENVG_BW_1`: `NOT_SUPPORT`. `_PRE`: the caller's `premultiplied` flag as for any buffer. Build parameter `G2D_DRV_OPENVG_FORMATS` (default 1) |

## 5. Paths (P4)

| Function | Prio | v1 | Notes |
|---|---|---|---|
| `vg_lite_init_path(path, format, quality, length, data, bbox)` | P4 | v1 | Formats S8, S16, S32, FP32. |
| `vg_lite_clear_path`, `vg_lite_upload_path`, `vg_lite_append_path` | P4 | v1 | `upload_path` caches the flattened edge list in GPU memory. |
| `vg_lite_draw(target, path, fill_rule, matrix, blend, color)` | P4 | v1 | Solid paint. |
| `vg_lite_set_path_type` | P4 | v1 | `VG_LITE_DRAW_FILL_PATH` (and `ZERO` meaning fill), `VG_LITE_DRAW_STROKE_PATH`, `VG_LITE_DRAW_FILL_STROKE_PATH` (Phase 6, strokes driver-only). |
| `vg_lite_init_arc_path` | — | v1.3 (driver-only) | Same as `vg_lite_init_path` (assumption A2); both accept arcs (rendering.md §9.2.1). |
| `vg_lite_set_stroke`, `vg_lite_update_stroke` | — | v1 (driver-only) | Butt / round / square caps, miter / round / bevel joins, miter limit (rendering.md §9.7); dash pattern, phase and `path->stroke->dash_reset` (v1.3, §9.7.1, OpenVG rules; F-5 closed) |
| `vg_lite_draw_pattern` | — | v1.5 | Any path and path matrix (affine or projective): DRAW_PATH with the image paint (F-1, rendering.md §9.5); exact pixel rectangles stay one blit; every pattern mode and pattern matrix through the hardware wrap modes (v1.4, §7.5) |

Path opcodes: `VLC_OP_END, CLOSE, MOVE(_REL), LINE(_REL), QUAD(_REL), CUBIC(_REL), HLINE(_REL), VLINE(_REL),
SQUAD(_REL), SCUBIC(_REL)` (v1); the eight arcs `SCCWARC … LCWARC_REL` (v1.3, rendering.md §9.2.1, OpenVG 1.1
§8.4) and `VLC_OP_BREAK` (v1.3, ends the subpath without closing it; UNVERIFIED, OPEN_QUESTIONS Q-API-16). An
unknown opcode gives `INVALID_ARGUMENT`.

The driver transforms points by the path matrix and flattens curves in software (rendering.md §9.2).
The hardware receives line edges only. Path matrices may be affine or (v1.3) projective: curves are flattened
against the projection, what lies behind the eye is clipped away and the points are projected by the driver
(rendering.md §9.8), for fills, strokes and gradient fills.

Fill rules: `VG_LITE_FILL_NON_ZERO`, `VG_LITE_FILL_EVEN_ODD` (P4).
Quality: `VG_LITE_HIGH` = 16 samples per pixel; `VG_LITE_UPPER` and `VG_LITE_MEDIUM` also render with
16 samples in v1 (more quality than asked, never less). `VG_LITE_LOW` = 1 sample (no AA).

## 6. Gradients (P5)

| Function | Prio | v1 | Notes |
|---|---|---|---|
| `vg_lite_init_grad`, `vg_lite_set_grad`, `vg_lite_update_grad`, `vg_lite_get_grad_matrix`, `vg_lite_clear_grad` | P5 | v1 | Legacy 16-stop linear gradient. |
| `vg_lite_draw_grad` | P5 | v1 | |
| `vg_lite_set_linear_grad`, `vg_lite_update_linear_grad`, `vg_lite_get_linear_grad_matrix`, `vg_lite_clear_linear_grad` | P5 | v1 | Extended linear gradient, up to 256 stops. Spread modes PAD, REPEAT, REFLECT. |
| `vg_lite_draw_linear_grad` | P5 | v1 | `filter` is ignored (nearest ramp lookup); `paint_color` is ignored. |
| Spread `VG_LITE_GRADIENT_SPREAD_FILL` | — | v1.4 | Outside the ramp the draw's `paint_color` (rendering.md §10.1; OPEN_QUESTIONS Q-API-6, decision 2026-10-20, UNVERIFIED). |
| Radial gradient: `vg_lite_set_radial_grad`, `_update_`, `_clear_`, `vg_lite_get_radial_grad_matrix`, `vg_lite_draw_radial_grad` | — | v1.5 | Centre, radius and focal point (OpenVG 1.1 §9.3.2; focal points outside or within 0.99 r of the circle moved to 0.99 r), up to 256 stops, `pre_mult`, PAD / REPEAT / REFLECT / FILL; `filter` ignored; affine gradient matrix (rendering.md §10.4); `gcFEATURE_BIT_VG_RADIAL_GRADIENT` = 1 |

Gradient matrices map gradient space to target space, independent of the path matrix, and must be
affine (rendering.md §10.2). Ramp construction is in rendering.md §10.3.

## 7. Explicitly later (not v1)

v1.6b (planned): 24-bit sources in hardware, YUV sources (NV12 / ANV12 / YUY2 / AYUY2) with CSC, gamma, colour transform /
pixel matrix, Gaussian filter. Closed in v1.6 (`NOT_SUPPORT`, reason recorded): tiled and compressed (DEC) layouts and
tiled YUV (vendor layouts not public), FLEXA, fast clear buffers (`fc_enable`), command caching / dump calls. (Dashed strokes, arcs, `VLC_OP_BREAK`, projective path matrices and the command buffer /
memory pool / frame delimiter calls were added in v1.3; the 16 / 8-bit, sub-byte and indexed formats, wrap modes,
dither, SPREAD_FILL and TRANSPARENT in v1.4.)
