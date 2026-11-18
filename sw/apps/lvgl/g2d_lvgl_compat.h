// SPDX-License-Identifier: Apache-2.0
/* Force-included into the LVGL build only. LVGL v9.6 names one error code that the frozen vg_lite.h API
 * version of this project (4.0.135, spec/vglite-reference.md) does not have; LVGL only uses it in its
 * error-to-string switch, and the G2D driver never returns it. The value is outside the header's error
 * range, so it cannot collide with a real code. */
#ifndef G2D_LVGL_COMPAT_H
#define G2D_LVGL_COMPAT_H
#ifndef VG_LITE_FLEXA_HANDSHAKE_FAIL
#define VG_LITE_FLEXA_HANDSHAKE_FAIL ((vg_lite_error_t)0x7FFF)
#endif
#endif
