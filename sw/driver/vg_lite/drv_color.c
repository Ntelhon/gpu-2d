// SPDX-License-Identifier: Apache-2.0
/* G2D vg_lite driver: colour unit (v1.6b, rendering.md §5.2.1 / §9.10): vg_lite_set_pixel_matrix,
 * vg_lite_set_color_transform / _enable_ / _disable_, vg_lite_set_gamma. The hardware applies the unit to the filtered
 * image colour; the driver applies the same integer function to solid draw colours, gradient ramps (a transformed
 * copy per command buffer) and a solid border colour, so every paint source sees it alike. Clears and mask-layer
 * operations do not use it. */
#include <string.h>

#include "generated/g2d_gamma.h"
#include "vg_lite_drv.h"

static const uint8_t k_lin[256] = G2D_GAMMA_LIN_INIT;
static const uint8_t k_srgb[256] = G2D_GAMMA_SRGB_INIT;

static int32_t sat(double v, int32_t lo, int32_t hi)
{
    double r = v < 0 ? -(double)(int64_t)(-v + 0.5) : (double)(int64_t)(v + 0.5);   /* round half away from 0 */
    return r < lo ? lo : r > hi ? hi : (int32_t)r;
}

/* The composed integer matrix (pixel matrix first, then the colour transform; rendering.md §9.10). */
static void compose(void)
{
    double m[4][5];
    int o, j;
    g_drv.color_en = 0;
    for (o = 0; o < 4; o++)
        for (j = 0; j < 5; j++)
            m[o][j] = (j == o) ? 1.0 : 0.0;
    if (g_drv.pm_set)
        for (o = 0; o < 4; o++)
            if (g_drv.pm_ch[o]) {
                for (j = 0; j < 5; j++)
                    m[o][j] = g_drv.pm[5 * o + j];
                g_drv.color_en = 1;
            }
    if (g_drv.ct_on) {
        for (o = 0; o < 4; o++) {
            for (j = 0; j < 5; j++)
                m[o][j] *= g_drv.ct[2 * o];          /* scale */
            m[o][4] += g_drv.ct[2 * o + 1];         /* bias */
        }
        g_drv.color_en = 1;
    }
    for (o = 0; o < 4; o++) {
        for (j = 0; j < 4; j++)
            g_drv.cmat[o][j] = sat(256.0 * m[o][j], -32768, 32767);
        g_drv.cbias[o] = sat(65280.0 * m[o][4], -(1 << 19), (1 << 19) - 1);
    }
    g_drv.color_gen++;
}

/* vg_lite_set_gamma, else the automatic conversion of an OPENVG_* image draw (rendering.md §9.10) */
static int eff_gamma(void)
{
    return g_drv.gamma ? g_drv.gamma : g_drv.auto_gamma;
}

int drv_color_active(void)
{
    return !g_drv.color_suspend && !g_drv.mask_suspend && (g_drv.color_en || eff_gamma());
}

uint32_t drv_color_apply(uint32_t argb)
{
    int32_t v[4], o, j;
    uint32_t out = 0;
    if (!drv_color_active())
        return argb;
    v[0] = (int32_t)(argb >> 24);
    v[1] = (int32_t)((argb >> 16) & 255u);
    v[2] = (int32_t)((argb >> 8) & 255u);
    v[3] = (int32_t)(argb & 255u);
    if (eff_gamma())
        for (j = 1; j < 4; j++)
            v[j] = eff_gamma() == 1 ? k_lin[v[j]] : k_srgb[v[j]];
    if (!g_drv.color_en)
        return ((uint32_t)v[0] << 24) | ((uint32_t)v[1] << 16) | ((uint32_t)v[2] << 8) | (uint32_t)v[3];
    for (o = 0; o < 4; o++) {
        int64_t s = (int64_t)g_drv.cbias[o] + 128;
        int64_t q;
        for (j = 0; j < 4; j++)
            s += (int64_t)g_drv.cmat[o][j] * v[j];
        q = s >= 0 ? s >> 8 : -((-s + 255) >> 8);    /* floor */
        out |= (uint32_t)(q < 0 ? 0 : q > 255 ? 255 : q) << (8 * (3 - o));
    }
    return out;
}

/* COLOR_* state for an image draw (the hardware unit), or the unit off. */
void drv_state_color(int image)
{
    int o, on = image && drv_color_active();
    drv_state(G2D_ST_COLOR_CTRL, on ? (G2D_FIELD_SET(G2D_ST_COLOR_CTRL_EN, (uint32_t)g_drv.color_en) |
                                       G2D_FIELD_SET(G2D_ST_COLOR_CTRL_GAMMA, (uint32_t)eff_gamma())) : 0u);
    if (!on || !g_drv.color_en)
        return;
    for (o = 0; o < 4; o++) {
        drv_state(G2D_ST_COLOR_M0 + 2u * (unsigned)o, ((uint32_t)g_drv.cmat[o][0] & 0xFFFFu) | ((uint32_t)g_drv.cmat[o][1] << 16));
        drv_state(G2D_ST_COLOR_M0 + 2u * (unsigned)o + 1u, ((uint32_t)g_drv.cmat[o][2] & 0xFFFFu) | ((uint32_t)g_drv.cmat[o][3] << 16));
        drv_state(G2D_ST_COLOR_B0 + (unsigned)o, (uint32_t)g_drv.cbias[o] & 0xFFFFFu);
    }
}

/* Gradient draws under an active unit read a transformed copy of their ramp (one per command buffer, ramp and
 * colour state); called after drv_reserve, so the draw and its copy are in the same buffer. */
vg_lite_error_t drv_color_ramp(void)
{
    uint32_t src = G2D_FIELD_GET(G2D_ST_PAINT_CTRL_SRC, g_drv.st_want[G2D_ST_PAINT_CTRL]);
    uint32_t ramp = g_drv.st_want[G2D_ST_GRAD_RAMP_ADDR];
    drv_cmdbuf_t *b = &g_drv.cb[g_drv.cur];
    const uint32_t *in;
    uint32_t *out;
    void *cpu;
    int i;
    vg_lite_error_t e;
    if ((src != G2D_PAINT_GRAD && src != G2D_PAINT_RADIAL) || !drv_color_active())
        return VG_LITE_SUCCESS;
    if (b->cramp_gpu && b->cramp_src == ramp && b->cramp_gen == g_drv.color_gen) {
        drv_state(G2D_ST_GRAD_RAMP_ADDR, b->cramp_gpu);
        return VG_LITE_SUCCESS;
    }
    in = (const uint32_t *)g_drv.hal->mem_cpu(g_drv.hal->ctx, ramp, 1024u);
    if (!in)
        return VG_LITE_INVALID_ARGUMENT;
    if ((e = drv_scratch(1024u, &cpu, &b->cramp_gpu)) != VG_LITE_SUCCESS)
        return e;
    out = (uint32_t *)cpu;
    for (i = 0; i < 256; i++)
        out[i] = drv_color_apply(in[i]);
    g_drv.hal->cache_clean(g_drv.hal->ctx, cpu, 1024u);
    b->cramp_src = ramp;
    b->cramp_gen = g_drv.color_gen;
    drv_state(G2D_ST_GRAD_RAMP_ADDR, b->cramp_gpu);
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_set_pixel_matrix(vg_lite_pixel_matrix_t matrix, vg_lite_pixel_channel_enable_t *channel)
{
    vg_lite_error_t e = drv_check_open();
    int k;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!drv_has(DRV_FEAT_COLOR))
        return VG_LITE_NOT_SUPPORT;      /* v1.7: colour unit not built */
    if (!matrix || !channel)
        return VG_LITE_INVALID_ARGUMENT;
    for (k = 0; k < 20; k++)
        g_drv.pm[k] = matrix[k];
    g_drv.pm_ch[0] = channel->enable_a != 0;
    g_drv.pm_ch[1] = channel->enable_r != 0;
    g_drv.pm_ch[2] = channel->enable_g != 0;
    g_drv.pm_ch[3] = channel->enable_b != 0;
    g_drv.pm_set = 1;
    compose();
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_set_color_transform(vg_lite_color_transform_t *values)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!drv_has(DRV_FEAT_COLOR))
        return VG_LITE_NOT_SUPPORT;      /* v1.7: colour unit not built */
    if (!values)
        return VG_LITE_INVALID_ARGUMENT;
    g_drv.ct[0] = values->a_scale; g_drv.ct[1] = values->a_bias;
    g_drv.ct[2] = values->r_scale; g_drv.ct[3] = values->r_bias;
    g_drv.ct[4] = values->g_scale; g_drv.ct[5] = values->g_bias;
    g_drv.ct[6] = values->b_scale; g_drv.ct[7] = values->b_bias;
    compose();
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_enable_color_transform(vg_lite_void)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!drv_has(DRV_FEAT_COLOR))
        return VG_LITE_NOT_SUPPORT;      /* v1.7: colour unit not built */
    g_drv.ct_on = 1;
    compose();
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_disable_color_transform(vg_lite_void)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!drv_has(DRV_FEAT_COLOR))
        return VG_LITE_NOT_SUPPORT;      /* v1.7: colour unit not built */
    g_drv.ct_on = 0;
    compose();
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_set_gamma(vg_lite_gamma_conversion_t gamma_value)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!drv_has(DRV_FEAT_COLOR))
        return VG_LITE_NOT_SUPPORT;      /* v1.7: colour unit not built */
    if (gamma_value != VG_LITE_GAMMA_NO_CONVERSION && gamma_value != VG_LITE_GAMMA_LINEAR &&
        gamma_value != VG_LITE_GAMMA_NON_LINEAR)
        return VG_LITE_INVALID_ARGUMENT;
    g_drv.gamma = gamma_value == VG_LITE_GAMMA_LINEAR ? 1 : gamma_value == VG_LITE_GAMMA_NON_LINEAR ? 2 : 0;
    g_drv.color_gen++;
    return VG_LITE_SUCCESS;
}
