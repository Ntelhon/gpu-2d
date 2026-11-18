// SPDX-License-Identifier: Apache-2.0
/* Driver build options (v1.6b, rendering.md §9.10), linked against vg_lite_opts:
 *   G2D_DRV_STRICT_GAUSSIAN = 1   vg_lite_gaussian_filter also rejects negative weights and sums other than 1
 *   G2D_DRV_OPENVG_FORMATS  = 0   OPENVG_* buffer formats are unknown (VG_LITE_NOT_SUPPORT) */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "g2d_hal.h"
#include "g2d_hal_model.h"
#include "vg_lite.h"

static int g_fail = 0, g_checks = 0;

#define CHECK(cond, msg)                                                  \
    do {                                                                  \
        g_checks++;                                                       \
        if (!(cond)) {                                                    \
            g_fail++;                                                     \
            printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, msg);          \
        }                                                                 \
    } while (0)

int main(void)
{
    vg_lite_buffer_t b;
    if (vg_lite_init(0, 0) != VG_LITE_SUCCESS) {
        printf("FAIL: vg_lite_init\n");
        return 1;
    }
    CHECK(vg_lite_gaussian_filter(0.5f, 0.0625f, 0.0625f) == VG_LITE_SUCCESS, "strict: unity kernel accepted");
    CHECK(vg_lite_gaussian_filter(5.0f, -1.0f, 0.0f) == VG_LITE_INVALID_ARGUMENT, "strict: negative weight rejected");
    CHECK(vg_lite_gaussian_filter(2.0f, 0.0f, 0.0f) == VG_LITE_INVALID_ARGUMENT, "strict: gain 2 rejected");
    CHECK(vg_lite_gaussian_filter(NAN, 0.0f, 0.0f) == VG_LITE_INVALID_ARGUMENT, "strict: NaN rejected");
    memset(&b, 0, sizeof b);
    b.width = 4;
    b.height = 4;
    b.format = OPENVG_sRGBA_8888;
    CHECK(vg_lite_allocate(&b) == VG_LITE_NOT_SUPPORT, "OPENVG_* compiled out: NOT_SUPPORT");
    b.format = OPENVG_lRGBA_8888;
    CHECK(vg_lite_allocate(&b) == VG_LITE_NOT_SUPPORT, "OPENVG_* compiled out: NOT_SUPPORT (linear)");
    vg_lite_close();
    {
        /* v1.7: a build without the optional units (HWCFG.FEAT_* = 0, the model HAL's control): feature bits read 0
         * and the APIs that need them return NOT_SUPPORT, never a silent wrong draw */
        const g2d_hal_t *hal = g2d_hal_open();          /* keeps the model alive across vg_lite_init / close */
        vg_lite_buffer_t t, y;
        vg_lite_matrix_t id;
        vg_lite_pixel_matrix_t pm = {0};
        vg_lite_pixel_channel_enable_t ce = {1, 1, 1, 1};
        g2d_hal_model_features(0u);
        CHECK(vg_lite_init(0, 0) == VG_LITE_SUCCESS, "init without optional units");
        CHECK(!vg_lite_query_feature(gcFEATURE_BIT_VG_GAUSSIAN_BLUR) && !vg_lite_query_feature(gcFEATURE_BIT_VG_YUV_INPUT) &&
              !vg_lite_query_feature(gcFEATURE_BIT_VG_GAMMA) && !vg_lite_query_feature(gcFEATURE_BIT_VG_RADIAL_GRADIENT),
              "feature bits follow HWCFG.FEAT_*");
        memset(&t, 0, sizeof t);
        t.width = 8; t.height = 8; t.format = VG_LITE_BGRA8888;
        CHECK(vg_lite_allocate(&t) == VG_LITE_SUCCESS, "allocate target");
        memset(&y, 0, sizeof y);
        y.width = 8; y.height = 8; y.format = VG_LITE_NV12;
        CHECK(vg_lite_allocate(&y) == VG_LITE_NOT_SUPPORT, "no YUV unit: NV12 NOT_SUPPORT");
        vg_lite_identity(&id);
        CHECK(vg_lite_blit(&t, &t, &id, OPENVG_BLEND_SRC_OVER, 0, VG_LITE_FILTER_POINT) == VG_LITE_NOT_SUPPORT,
              "no families: OpenVG blend NOT_SUPPORT");
        CHECK(vg_lite_blit(&t, &t, &id, VG_LITE_BLEND_NONE, 0, VG_LITE_FILTER_GAUSSIAN) == VG_LITE_NOT_SUPPORT,
              "no Gaussian: FILTER_GAUSSIAN NOT_SUPPORT");
        CHECK(vg_lite_set_pixel_matrix(pm, &ce) == VG_LITE_NOT_SUPPORT && vg_lite_set_gamma(VG_LITE_GAMMA_LINEAR) ==
              VG_LITE_NOT_SUPPORT, "no colour unit: NOT_SUPPORT");
        CHECK(vg_lite_enable_masklayer() == VG_LITE_NOT_SUPPORT, "no mask layer: NOT_SUPPORT");
        CHECK(vg_lite_blit(&t, &t, &id, VG_LITE_BLEND_SRC_OVER, 0, VG_LITE_FILTER_POINT) == VG_LITE_SUCCESS,
              "the base blit still works");
        CHECK(vg_lite_finish() == VG_LITE_SUCCESS, "finish");
        vg_lite_free(&t);
        vg_lite_close();
        g2d_hal_model_features(0x3Fu);
        g2d_hal_close(hal);
    }
    printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail != 0;
}
