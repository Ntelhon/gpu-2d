# LVGL v9.6 vs the v1 vg_lite driver: unsupported calls (Phase 6 survey, 2026-10-17)

How this was found:
- **Static:** every `vg_lite_*` function named in LVGL's VG-Lite draw unit (`src/draw/vg_lite/*.c|h`) was checked
  against what our driver defines. So were the enum values LVGL passes (blend modes, image modes, path types,
  formats) and the feature bits it queries.
- **Dynamic:** the widgets, benchmark and vector-graphic demos ran with `LV_USE_VECTOR_GRAPHIC 1` and
  `LV_VG_LITE_USE_ASSERT 0`. LVGL logs every non-SUCCESS vg_lite return at ERROR level
  (`LV_VG_LITE_CHECK_ERROR`). Widgets and benchmark log no errors. The vector demo's errors are pinned by
  `sw/tests/test_lvgl.py::test_vector_known_gaps`.
- **Note on asserts:** with LVGL's default `LV_VG_LITE_USE_ASSERT 1`, a failing call ends in `LV_ASSERT`. The
  default handler loops forever, so the program hangs; it does not just skip the draw.

Ranking: how likely a 720p UI hits it × what happens when it does. Verdicts:
- **fixed:** done in this phase.
- **driver-only:** possible without RTL changes, to be scheduled.
- **hardware:** docs/FEATURES_LATER.md.
- **keep:** no action needed.

| Rank | Call / feature | Where LVGL uses it | What happens today | Evidence | Verdict |
|---|---|---|---|---|---|
| 1 | Blend `VG_LITE_BLEND_SCREEN` (rendering.md §5.6) | lv_vector blend modes (SVG, Lottie, the vector demo) | Was wrong by up to 255 whenever `S + D > 255`; the spec saturated `S + D` before subtracting. Model and RTL matched the spec, so no test failed | vector demo `draw_blend`: (11,245,11) instead of (255,245,255) | **fixed** (B-6.1: spec, model, reference, RTL; `test_blend_vs_ideal` checks every mode against its ideal formula) |
| 2 | Source formats A4, ARGB1555 (`BGRA5551`), ARGB4444 (`BGRA4444`), ARGB2222 (`BGRA2222`); destinations ARGB1555 / 4444 / 2222 | `lv_vg_lite_is_src_cf_supported` / `_dest_cf_supported` accept them **without a feature check**, so LVGL sends such images and layers to VG-Lite | `drv_check_buffer` returns NOT_SUPPORT: the draw is lost (error logged; a hang with LVGL asserts on) | static (code path read); the demos use only ARGB8888 / RGB565 / A8 / XRGB8888 | **hardware, done (v1.4)**: sources and destinations read / written by the hardware in every channel order (rendering.md §3.2; F-3 closed; Phase 6 converted the sources on the CPU). Note: LVGL's VG-Lite image decoder already converts decoded image *assets* in these formats to BGRA8888 / A8 itself; the driver path matters for buffers LVGL passes directly (draw buffers, layers, canvases) |
| 3 | `vg_lite_draw_pattern` with non-rectangular paths | rounded image clip (`clip_radius`, e.g. avatars), arcs with an image, lv_vector image fills | NOT_SUPPORT: the draw is lost | vector demo `draw_pattern` (1 error) | **Done in v1.5** (F-1, image paint on paths; rendering.md §9.5) |
| 4 | `vg_lite_set_stroke`, `vg_lite_update_stroke`, `vg_lite_set_path_type(STROKE / FILL_STROKE)` | lv_vector strokes (SVG, Lottie, vector API), letter outlines with `outline_stroke_width`. Ordinary widgets (lines, arcs, borders) build fill paths and do not use it | was NOT_SUPPORT (3 strokes lost in the vector demo); all drawn since v1.3 | vector demo | **driver-only, done**: solid strokes (rendering.md §9.7: caps, joins, miter limit; verified against the exact stroke, Skia and ThorVG) and, since v1.3, dashes (§9.7.1; LVGL's `dash_phase = width / 2`, OPEN_QUESTIONS Q-API-17) |
| 5 | Radial gradient: `vg_lite_set/update/draw/clear_radial_grad`, `vg_lite_get_radial_grad_matrix`, `gcFEATURE_BIT_VG_RADIAL_GRADIENT` | radial lv_vector gradients, radial style gradients (`LV_GRAD_DIR_RADIAL`) | LVGL checks the bit (0) and skips the draw with a warning | vector demo `draw_radial_gradient` (1 warning) | **Done in v1.5** (F-4, rendering.md §10.4; feature bit 1) |
| 6 | `gcFEATURE_BIT_VG_IM_REPEAT_REFLECT` | LVGL refuses REPEAT / REFLECT gradient spread when the bit is 0 | Was reported 0 although the hardware and driver support both spreads (rendering.md §10), so such gradients were lost | static (`lv_vg_lite_grad.c:180`) | **fixed**: the bit now reads 1 (checked in `test_driver`) |
| 7 | Gradient fills of styles (`bg_grad`) | `lv_draw_vg_lite_fill` | Only drawn with `LV_USE_VECTOR_GRAPHIC 1`; otherwise a warning and **no fill** | `lv_draw_vg_lite_fill.c:79` | **keep** (LVGL configuration, not a driver call). The demo configuration now enables it; integrators must too (docs/PROGRAMMING.md) |
| 8 | Perspective path matrix in `vg_lite_draw` / gradients | only with layer transforms (`LV_DRAW_TRANSFORM_USE_MATRIX`); LVGL maps perspective image corners on the CPU itself | supported since v1.3 | static | **driver-only, done** (v1.3, rendering.md §9.8: adaptive curve subdivision against the projection, near-plane clip, projection) |
| 9 | 24-bit (RGB888, ARGB8565), indexed (I1–I8), YUV formats | guarded by feature bits (`24BIT`, `INDEX_ENDIAN`, `IM_INDEX_FORMAT`, `YUV*`) | v1.4: `IM_INDEX_FORMAT` and `INDEX_ENDIAN` read 1 (indexed sources in hardware, `vg_lite_set_CLUT`; LVGL converts I1 / I2 / I4 to I8 itself, Q-API-18); `24BIT` reads 1 since v1.6b (24-bit sources in hardware; until v1.6 it read 0, LVGL converted RGB888 / ARGB8565 to 32 bit and the driver converted 24-bit buffers passed directly, rendering.md §9.6); `YUV_INPUT` / `YUY2_INPUT` read 1 since v1.6b (LVGL then sends NV12 / YUY2 images to VG-Lite; the LVGL demos use neither) | static | **done**: indexed (v1.4), 24-bit and YUV (v1.6b) |
| 10 | `vg_lite_get_parameter`, `vg_lite_dump_command_buffer` | debug dumps only (`lv_vg_lite_dump_info`, error dumps) | generated stub, NOT_SUPPORT | static; never called in the demos | **keep** |
| 11 | `VG_LITE_GRADIENT_SPREAD_FILL` | only the default branch of LVGL's spread conversion (invalid input) | v1.4: drawn, `paint_color` outside the ramp (OPEN_QUESTIONS Q-API-6, UNVERIFIED) | static | **done** (v1.4) |

Everything else the three demos call returns SUCCESS, including pattern draws on rectangular paths (the
benchmark's tiled images). Other supported features (scissor, colour key, global alpha, all 16 blend codes,
image modes NORMAL / MULTIPLY, point and bilinear filters, linear and extended linear gradients) are covered
by sw/tests/test_scenes.py; which of them the demos reach was not measured per call.

Suggested order:
1. Driver: stroke-to-fill (rank 4) and source-format conversion (rank 2), both without RTL changes.
2. Next hardware revision: F-3 formats (small), F-1 image paint on paths (medium), F-4 radial gradient (medium).
