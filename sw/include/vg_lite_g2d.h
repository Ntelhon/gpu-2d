// SPDX-License-Identifier: Apache-2.0
/* G2D extensions of the vg_lite API (not part of vg_lite.h). */
#ifndef VG_LITE_G2D_H
#define VG_LITE_G2D_H

#include "vg_lite.h"

#ifdef __cplusplus
extern "C" {
#endif

/* What vg_lite_draw_pattern does with pattern_color in VG_LITE_PATTERN_COLOR mode (rendering.md §7.5, §9.5;
 * OPEN_QUESTIONS Q-API-12, UNVERIFIED which one Vivante implements):
 *   TEXEL (default): pattern_color is a texel outside the image: filtered with its neighbours (bilinear blends it
 *                    in at the image edge) and multiplied by `color` in MULTIPLY image mode, as OpenVG 1.1 §9.4.1
 *                    defines VG_TILE_FILL for filtering;
 *   SOLID:           a pixel whose sample centre is outside the image gets pattern_color as a plain colour (no
 *                    filtering, no MULTIPLY); the v1.0-v1.3 behaviour. */
typedef enum {
    VG_LITE_G2D_PATTERN_COLOR_TEXEL = 0,
    VG_LITE_G2D_PATTERN_COLOR_SOLID = 1,
} vg_lite_g2d_pattern_color_mode_t;

/* Applies to every later vg_lite_draw_pattern until changed; vg_lite_init resets it to TEXEL. */
vg_lite_error_t vg_lite_g2d_set_pattern_color_mode(vg_lite_g2d_pattern_color_mode_t mode);

/* v2.0: AXI attributes of the GPU's memory port (CSR MEM_ATTR, arch.md §4.2): AxCACHE 0..15, AxPROT 0..7,
 * AxQOS 0..15 for reads and for writes. Takes effect for the GPU's next memory requests; kept across the driver's
 * error recovery (soft reset), reset to cache 0x3 / prot 0 / qos 0 only by a hardware reset.
 * VG_LITE_INVALID_ARGUMENT if a value is out of range or a pointer is NULL. */
typedef struct {
    vg_lite_uint32_t cache;
    vg_lite_uint32_t prot;
    vg_lite_uint32_t qos;
} vg_lite_g2d_axi_attr_t;

vg_lite_error_t vg_lite_g2d_set_mem_attr(const vg_lite_g2d_axi_attr_t *read, const vg_lite_g2d_axi_attr_t *write);

/* v2.0: memory-port throttle (CSR MEM_QOS, arch.md §4.2.1): at most rd_max / wr_max bursts outstanding (1..31;
 * 0 = only the build-time limit) and at least rd_gap / wr_gap cycles (0..255) between burst requests. All 0 = off
 * (the reset state). VG_LITE_INVALID_ARGUMENT if a value is out of range. */
vg_lite_error_t vg_lite_g2d_set_mem_qos(vg_lite_uint32_t rd_max, vg_lite_uint32_t wr_max, vg_lite_uint32_t rd_gap,
                                        vg_lite_uint32_t wr_gap);

#ifdef __cplusplus
}
#endif

#endif /* VG_LITE_G2D_H */
