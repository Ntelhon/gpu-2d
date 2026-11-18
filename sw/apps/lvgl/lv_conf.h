// SPDX-License-Identifier: Apache-2.0
/* LVGL configuration of the G2D host demos (sw/apps/lvgl). Everything not set here keeps the LVGL default
 * (lv_conf_internal.h). The build selects the variant:
 *   G2D_LV_VG     1: VG-Lite draw unit on the G2D vg_lite driver (software backend), 0: LVGL software renderer
 *   G2D_LV_DEPTH  32 or 16 (LV_COLOR_DEPTH)
 * LVGL's bundled VG-Lite driver (LV_USE_VG_LITE_DRIVER, src/libs/vg_lite_driver) is vendor code: it stays
 * off and its directory is excluded from the build (independent implementation, docs/PROJECT_RULES.md). */
#ifndef LV_CONF_H
#define LV_CONF_H

#ifndef G2D_LV_VG
#define G2D_LV_VG 1
#endif
#ifndef G2D_LV_DEPTH
#define G2D_LV_DEPTH 32
#endif

#define LV_COLOR_DEPTH G2D_LV_DEPTH

/* C library: malloc stays in the brk heap (below 4 GB in this non-PIE program), see main.c */
#define LV_USE_STDLIB_MALLOC LV_STDLIB_CLIB
#define LV_USE_STDLIB_STRING LV_STDLIB_CLIB
#define LV_USE_STDLIB_SPRINTF LV_STDLIB_CLIB

#define LV_USE_OS LV_OS_NONE
#define LV_DEF_REFR_PERIOD 33

#define LV_DRAW_BUF_ALIGN 64
#define LV_USE_DRAW_SW 1
#define LV_USE_FLOAT 1
#define LV_USE_MATRIX 1
/* vector graphics: gradient fills, lv_vector API (strokes, radial gradients); the software renderer draws
 * them with LVGL's bundled ThorVG (MIT, src/libs/thorvg), the VG-Lite build with our driver */
#define LV_USE_VECTOR_GRAPHIC 1
#define LV_USE_THORVG 1
#define LV_USE_THORVG_INTERNAL 1

#define LV_USE_DRAW_VG_LITE G2D_LV_VG
#if LV_USE_DRAW_VG_LITE
#define LV_VG_LITE_USE_GPU_INIT 0          /* main.c calls vg_lite_init() */
#define LV_VG_LITE_USE_ASSERT 0            /* a failing vg_lite call is logged (test_lvgl.py fails on it), not a hang */
#define LV_USE_VG_LITE_DRIVER 0            /* never the bundled vendor driver */
#ifdef G2D_LV_TVG                          /* tv32: LVGL's own VG-Lite simulator on ThorVG (src/debugging/vg_lite_tvg, */
#define LV_USE_VG_LITE_THORVG 1            /* MIT) instead of our driver: a third-party reading of the vg_lite API */
#else
#define LV_USE_VG_LITE_THORVG 0
#endif
#endif

#define LV_USE_LOG 1
#define LV_LOG_LEVEL LV_LOG_LEVEL_WARN
#define LV_LOG_PRINTF 1

/* time-dependent overlays off: frames must not depend on host speed */
#define LV_USE_PERF_MONITOR 0
#define LV_USE_MEM_MONITOR 0
#define LV_USE_SYSMON 0

#define LV_FONT_MONTSERRAT_12 1
#define LV_FONT_MONTSERRAT_14 1
#define LV_FONT_MONTSERRAT_16 1
#define LV_FONT_MONTSERRAT_18 1
#define LV_FONT_MONTSERRAT_20 1
#define LV_FONT_MONTSERRAT_24 1
#define LV_FONT_MONTSERRAT_26 1
#define LV_FONT_MONTSERRAT_28 1
#define LV_FONT_MONTSERRAT_32 1
#define LV_FONT_MONTSERRAT_40 1
#define LV_FONT_DEFAULT &lv_font_montserrat_14

#define LV_USE_DEMO_WIDGETS 1
#define LV_USE_DEMO_BENCHMARK 1
#define LV_USE_DEMO_VECTOR_GRAPHIC 1

#endif /* LV_CONF_H */
