# VGLite API reference: version, source, license

Status: frozen for v1 (2026-10-15).

## Chosen version

| Item | Value |
|---|---|
| Upstream | VeriSilicon VGLite driver, GitHub `VeriSilicon/VGLite_4.x` |
| URL | https://github.com/VeriSilicon/VGLite_4.x |
| Tag | `REL_4.0.135_20260609` (newest tag on 2026-10-15) |
| Header | `inc/vg_lite.h`, `VGLITE_RELEASE_VERSION = 4.0.135`, `VGLITE_HEADER_VERSION = 7`, `VGLITE_API_VERSION_3_0` |
| Header license | MIT (notice "Copyright (c) 2014 - 2026 Vivante Corporation", at the top of the file) |
| Repository `LICENSE.txt` | MIT-style permission notice (Vivante Corporation, 2012-2023) |
| Vendored copy | `sw/include/vg_lite.h`, byte-identical to upstream, SHA-256 `b2916c169c940a112101c0e3ede561763fbbe213da9d96e21b157b1507e7cd1b` |

The header is vendored unchanged with its MIT notice intact. Any extension goes in a
separate header (`sw/include/g2d_vg_lite_ext.h`), never as an edit to `vg_lite.h`.

Other public VGLite sources looked at:
- NXP MCUXpresso SDK ships VGLite as middleware (`VGLite/inc/vg_lite.h`). The SDK web docs are public,
  but no newer header is published separately. Not used.
- `nxp-mcuxpresso/gpu-vglite-examples` (BSD-3-Clause), example applications only. May be used later as
  test inspiration; no code is copied.

## Sources not used (independent implementation)

| Material | Why excluded |
|---|---|
| `doc/Vivante.VGLite.Vector.Graphics.API.User.Manual-v4.x.x-B-20260527.pdf` and the other PDFs in that repo | Every page is marked "VeriSilicon LEVEL B: CONFIDENTIAL – DISTRIBUTION RESTRICTED", and the legal notice restricts use to NDA licensees, even though the file sits in a public repository. Only the legal page and table of contents were read, to find this out; the file was then deleted. |
| VGLite driver sources (`VGLite/`, `VGLiteKernel/` in the upstream repo, LVGL `src/libs/vg_lite_driver`) | Rule: no vendor driver code. The G2D HAL, kernel layer and command format are written from scratch. Not read. |
| Any other vendor GPU material (driver sources, SDK internals) | Vendor material of unknown license. Not read. |
| Any Vivante/VeriSilicon register map or hardware documentation | API-level compatibility only. |

## Sources of API semantics (all permissively licensed)

1. **`vg_lite.h` comments (MIT).** Blend equations (RGB and alpha per mode), enum meanings, struct
   field descriptions, function signatures.
2. **LVGL (MIT), commit `58ae125` (2026-10-14), latest release v9.6.0.** This is the main consumer of the API. Used to settle:
   - Format naming. `LV_COLOR_FORMAT_ARGB8888` (bytes B,G,R,A in memory) maps to `VG_LITE_BGRA8888`, and
     `LV_COLOR_FORMAT_RGB565` (R in bits 15:11) maps to `VG_LITE_BGR565`
     (`src/draw/vg_lite/lv_vg_lite_utils.c`, `lv_vg_lite_vg_fmt`). So VGLite names list channels
     starting at the least significant bit / lowest byte address.
   - Colour packing. `vg_lite_color_t = A<<24 | B<<16 | G<<8 | R` (`lv_vg_lite_color`).
   - Premultiplication. LVGL picks `VG_LITE_BLEND_SRC_OVER` for premultiplied sources and
     `VG_LITE_BLEND_NORMAL_LVGL` for straight ones (`lv_vg_lite_blend_mode`). So "S" in the header's
     equations is the source value as stored, with no automatic premultiplication.
   - Matrix convention. `x' = m[0][0]*x + m[0][1]*y + m[0][2]`: column vectors, row-major `m[row][col]`,
     mapping source/path coordinates to target coordinates (`src/debugging/vg_lite_tvg/vg_lite_tvg.cpp`).
3. **LVGL's ThorVG-based VGLite simulator (`vg_lite_tvg`, MIT).** A behavioural reference for coverage.
   It is treated as a tolerance-level cross-check only. It is not bit-exact with any hardware and not with G2D.

Where none of these settles a behaviour, `spec/rendering.md` defines it and the item is logged in
`docs/OPEN_QUESTIONS.md`.
