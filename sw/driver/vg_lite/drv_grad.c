// SPDX-License-Identifier: Apache-2.0
/* G2D vg_lite driver: linear gradients (api-subset.md §6, rendering.md §10). The 256-entry ramp of a
 * gradient lives in its own image buffer (256 x 1 BGRA8888, i.e. the ARGB32 words the hardware reads),
 * rebuilt by the update functions. */
#include <stdlib.h>
#include <string.h>

#include "g2d_geom.h"
#include "vg_lite_drv.h"

#define FS(reg, field, v) G2D_FIELD_SET(G2D_ST_##reg##_##field, v)

static vg_lite_error_t ramp_image(vg_lite_buffer_t *img)
{
    if (img->handle)
        return VG_LITE_SUCCESS;
    memset(img, 0, sizeof *img);
    img->width = 256;
    img->height = 1;
    img->format = VG_LITE_BGRA8888;
    return vg_lite_allocate(img);
}

static vg_lite_error_t write_ramp(vg_lite_buffer_t *img, const uint32_t ramp[256])
{
    int safe;
    vg_lite_error_t e = drv_quiesce(&safe);    /* draws in flight may read the old ramp (D-4) */
    if (!safe)
        return e;
    memcpy(img->memory, ramp, 1024);
    g_drv.hal->cache_clean(g_drv.hal->ctx, img->memory, 1024);
    return e;
}

/* GRAD_* state from the coefficients of the gradient matrix (rendering.md §10.2). */
static vg_lite_error_t grad_state(const vg_lite_matrix_t *gm, int ext, const vg_lite_linear_gradient_parameter_t *p,
                                  uint32_t ramp_addr)
{
    double m[3][3];
    int32_t gp, gq;
    int64_t gr;
    int rc;
    drv_matrix_d(gm, m);
    rc = g2d_geom_grad_coeffs(DRV_CM(m), ext, p ? p->X0 : 0.0, p ? p->Y0 : 0.0, p ? p->X1 : 0.0, p ? p->Y1 : 0.0, &gp, &gq, &gr);
    if (rc != G2D_GEOM_OK)
        return drv_geom_err(rc) == VG_LITE_SUCCESS ? VG_LITE_INVALID_ARGUMENT : drv_geom_err(rc);
    drv_state(G2D_ST_GRAD_P, (uint32_t)gp);
    drv_state(G2D_ST_GRAD_Q, (uint32_t)gq);
    drv_state(G2D_ST_GRAD_R, (uint32_t)(uint64_t)gr);
    drv_state(G2D_ST_GRAD_RAMP_ADDR, ramp_addr);
    drv_state(G2D_ST_GRAD_R_HI, (uint32_t)((uint64_t)gr >> 32) & 0xFFFFu);
    return VG_LITE_SUCCESS;
}

/* ------------------------------------------------------------------------------------------------ */
/* Legacy 16-stop gradient                                                                          */
/* ------------------------------------------------------------------------------------------------ */

vg_lite_error_t vg_lite_init_grad(vg_lite_linear_gradient_t *grad)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!grad)
        return VG_LITE_INVALID_ARGUMENT;
    memset(grad, 0, sizeof *grad);
    vg_lite_identity(&grad->matrix);
    grad->colors = (vg_lite_uint32_t *)calloc(VLC_MAX_GRADIENT_STOPS, sizeof(uint32_t));
    grad->stops = (vg_lite_uint32_t *)calloc(VLC_MAX_GRADIENT_STOPS, sizeof(uint32_t));
    if (!grad->colors || !grad->stops) {
        free(grad->colors);
        free(grad->stops);
        grad->colors = grad->stops = NULL;
        return VG_LITE_OUT_OF_MEMORY;
    }
    grad->m_count = VLC_MAX_GRADIENT_STOPS;
    return ramp_image(&grad->image);
}

vg_lite_error_t vg_lite_set_grad(vg_lite_linear_gradient_t *grad, vg_lite_uint32_t count, vg_lite_uint32_t *colors,
                                 vg_lite_uint32_t *stops)
{
    uint32_t k;
    if (!grad || !grad->colors || !colors || !stops || count == 0 || count > VLC_MAX_GRADIENT_STOPS)
        return VG_LITE_INVALID_ARGUMENT;
    for (k = 0; k < count; k++) {
        if (stops[k] > 255u || (k && stops[k] < stops[k - 1]))
            return VG_LITE_INVALID_ARGUMENT;
    }
    memcpy(grad->colors, colors, count * sizeof(uint32_t));
    memcpy(grad->stops, stops, count * sizeof(uint32_t));
    grad->count = count;
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_update_grad(vg_lite_linear_gradient_t *grad)
{
    uint32_t ramp[256];
    vg_lite_error_t e = drv_check_open();
    int rc;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!grad || !grad->count || !grad->image.handle)
        return VG_LITE_INVALID_ARGUMENT;
    rc = g2d_geom_ramp_legacy(grad->count, grad->colors, grad->stops, ramp);
    if (rc != G2D_GEOM_OK)
        return drv_geom_err(rc) == VG_LITE_SUCCESS ? VG_LITE_INVALID_ARGUMENT : drv_geom_err(rc);
    return write_ramp(&grad->image, ramp);
}

vg_lite_matrix_t *vg_lite_get_grad_matrix(vg_lite_linear_gradient_t *grad) { return grad ? &grad->matrix : NULL; }

vg_lite_error_t vg_lite_clear_grad(vg_lite_linear_gradient_t *grad)
{
    vg_lite_error_t e = VG_LITE_SUCCESS;
    if (!grad)
        return VG_LITE_INVALID_ARGUMENT;
    if (grad->image.handle)
        e = vg_lite_free(&grad->image);
    free(grad->colors);
    free(grad->stops);
    grad->colors = grad->stops = NULL;
    grad->count = grad->m_count = 0;
    return e;
}

vg_lite_error_t vg_lite_draw_grad(vg_lite_buffer_t *target, vg_lite_path_t *path, vg_lite_fill_t fill_rule,
                                  vg_lite_matrix_t *matrix, vg_lite_linear_gradient_t *grad, vg_lite_blend_t blend)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!grad || !grad->image.handle || !grad->count)
        return VG_LITE_INVALID_ARGUMENT;
    if ((e = grad_state(&grad->matrix, 0, NULL, grad->image.address)) != VG_LITE_SUCCESS)
        return e;
    if ((e = drv_state_paint(blend, FS(PAINT_CTRL, SRC, G2D_PAINT_GRAD) | FS(PAINT_CTRL, SPREAD, G2D_SPREAD_PAD), 0)) !=
        VG_LITE_SUCCESS)
        return e;
    return drv_draw_path(target, path, fill_rule, matrix);
}

/* ------------------------------------------------------------------------------------------------ */
/* Extended linear gradient (up to 256 float stops)                                                 */
/* ------------------------------------------------------------------------------------------------ */

static int spread_code(vg_lite_gradient_spreadmode_t s)
{
    switch (s) {
    case VG_LITE_GRADIENT_SPREAD_PAD:     return G2D_SPREAD_PAD;
    case VG_LITE_GRADIENT_SPREAD_REPEAT:  return G2D_SPREAD_REPEAT;
    case VG_LITE_GRADIENT_SPREAD_REFLECT: return G2D_SPREAD_REFLECT;
    case VG_LITE_GRADIENT_SPREAD_FILL:    return G2D_SPREAD_FILL;     /* v1.4, Q-API-6: paint_color outside */
    default:                              return -1;
    }
}

vg_lite_error_t vg_lite_set_linear_grad(vg_lite_ext_linear_gradient_t *grad, vg_lite_uint32_t count,
                                        vg_lite_color_ramp_t *color_ramp, vg_lite_linear_gradient_parameter_t grad_param,
                                        vg_lite_gradient_spreadmode_t spread_mode, vg_lite_uint8_t pre_mult)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!grad || !color_ramp || count == 0 || count > VLC_MAX_COLOR_RAMP_STOPS)
        return VG_LITE_INVALID_ARGUMENT;
    if (spread_code(spread_mode) < 0)
        return VG_LITE_NOT_SUPPORT;
    if (grad->m_count < count) {
        vg_lite_color_ramp_t *r = (vg_lite_color_ramp_t *)realloc(grad->color_ramp, count * sizeof *r);
        if (!r)
            return VG_LITE_OUT_OF_MEMORY;
        grad->color_ramp = r;
        grad->m_count = count;
    }
    memcpy(grad->color_ramp, color_ramp, count * sizeof *color_ramp);
    grad->ramp_length = count;
    grad->count = count;
    grad->linear_grad = grad_param;
    grad->spread_mode = spread_mode;
    grad->pre_multiplied = pre_mult;
    if (!grad->image.handle) {
        vg_lite_identity(&grad->matrix);       /* first use: the caller sets the matrix afterwards */
        e = ramp_image(&grad->image);
    }
    return e;
}

vg_lite_error_t vg_lite_update_linear_grad(vg_lite_ext_linear_gradient_t *grad)
{
    uint32_t ramp[256], k;
    g2d_ramp_stop_t st[VLC_MAX_COLOR_RAMP_STOPS];
    vg_lite_error_t e = drv_check_open();
    int rc;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!grad || !grad->color_ramp || !grad->count || !grad->image.handle)
        return VG_LITE_INVALID_ARGUMENT;
    for (k = 0; k < grad->count; k++) {
        st[k].stop = grad->color_ramp[k].stop;
        st[k].r = grad->color_ramp[k].red;
        st[k].g = grad->color_ramp[k].green;
        st[k].b = grad->color_ramp[k].blue;
        st[k].a = grad->color_ramp[k].alpha;
    }
    rc = g2d_geom_ramp_ext(grad->count, st, grad->pre_multiplied, ramp);
    if (rc != G2D_GEOM_OK)
        return drv_geom_err(rc) == VG_LITE_SUCCESS ? VG_LITE_INVALID_ARGUMENT : drv_geom_err(rc);
    return write_ramp(&grad->image, ramp);
}

vg_lite_matrix_t *vg_lite_get_linear_grad_matrix(vg_lite_ext_linear_gradient_t *grad)
{
    return grad ? &grad->matrix : NULL;
}

vg_lite_error_t vg_lite_clear_linear_grad(vg_lite_ext_linear_gradient_t *grad)
{
    vg_lite_error_t e = VG_LITE_SUCCESS;
    if (!grad)
        return VG_LITE_INVALID_ARGUMENT;
    if (grad->image.handle)
        e = vg_lite_free(&grad->image);
    free(grad->color_ramp);
    grad->color_ramp = NULL;
    grad->count = grad->m_count = grad->ramp_length = 0;
    return e;
}

/* filter is ignored (api-subset.md §6: nearest ramp lookup); paint_color is the colour outside the ramp under
 * VG_LITE_GRADIENT_SPREAD_FILL (rendering.md §10.1, used as given). */
vg_lite_error_t vg_lite_draw_linear_grad(vg_lite_buffer_t *target, vg_lite_path_t *path, vg_lite_fill_t fill_rule,
                                         vg_lite_matrix_t *path_matrix, vg_lite_ext_linear_gradient_t *grad,
                                         vg_lite_color_t paint_color, vg_lite_blend_t blend, vg_lite_filter_t filter)
{
    vg_lite_error_t e = drv_check_open();
    int sp;
    (void)filter;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!drv_has(DRV_FEAT_RADIAL))
        return VG_LITE_NOT_SUPPORT;      /* v1.7: radial gradient not built */
    if (!grad || !grad->image.handle || !grad->count)
        return VG_LITE_INVALID_ARGUMENT;
    if ((sp = spread_code(grad->spread_mode)) < 0)
        return VG_LITE_NOT_SUPPORT;
    if ((e = grad_state(&grad->matrix, 1, &grad->linear_grad, grad->image.address)) != VG_LITE_SUCCESS)
        return e;
    if ((e = drv_state_paint(blend, FS(PAINT_CTRL, SRC, G2D_PAINT_GRAD) | FS(PAINT_CTRL, SPREAD, (uint32_t)sp),
                             drv_argb(paint_color))) != VG_LITE_SUCCESS)
        return e;
    return drv_draw_path(target, path, fill_rule, path_matrix);
}

/* ------------------------------------------------------------------------------------------------ */
/* Radial gradient (v1.5, F-4, rendering.md §10.4)                                                  */
/* ------------------------------------------------------------------------------------------------ */

vg_lite_error_t vg_lite_set_radial_grad(vg_lite_radial_gradient_t *grad, vg_lite_uint32_t count,
                                        vg_lite_color_ramp_t *color_ramp, vg_lite_radial_gradient_parameter_t grad_param,
                                        vg_lite_gradient_spreadmode_t spread_mode, vg_lite_uint8_t pre_mult)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!grad || !color_ramp || count == 0 || count > VLC_MAX_COLOR_RAMP_STOPS)
        return VG_LITE_INVALID_ARGUMENT;
    if (spread_code(spread_mode) < 0)
        return VG_LITE_NOT_SUPPORT;
    if (grad->m_count < count) {
        vg_lite_color_ramp_t *r = (vg_lite_color_ramp_t *)realloc(grad->color_ramp, count * sizeof *r);
        if (!r)
            return VG_LITE_OUT_OF_MEMORY;
        grad->color_ramp = r;
        grad->m_count = count;
    }
    memcpy(grad->color_ramp, color_ramp, count * sizeof *color_ramp);
    grad->ramp_length = count;
    grad->count = count;
    grad->radial_grad = grad_param;
    grad->spread_mode = spread_mode;
    grad->pre_multiplied = pre_mult;
    if (!grad->image.handle) {
        vg_lite_identity(&grad->matrix);       /* first use: the caller sets the matrix afterwards */
        e = ramp_image(&grad->image);
    }
    return e;
}

vg_lite_error_t vg_lite_update_radial_grad(vg_lite_radial_gradient_t *grad)
{
    uint32_t ramp[256], k;
    g2d_ramp_stop_t st[VLC_MAX_COLOR_RAMP_STOPS];
    vg_lite_error_t e = drv_check_open();
    int rc;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!grad || !grad->color_ramp || !grad->count || !grad->image.handle)
        return VG_LITE_INVALID_ARGUMENT;
    for (k = 0; k < grad->count; k++) {
        st[k].stop = grad->color_ramp[k].stop;
        st[k].r = grad->color_ramp[k].red;
        st[k].g = grad->color_ramp[k].green;
        st[k].b = grad->color_ramp[k].blue;
        st[k].a = grad->color_ramp[k].alpha;
    }
    rc = g2d_geom_ramp_ext(grad->count, st, grad->pre_multiplied, ramp);
    if (rc != G2D_GEOM_OK)
        return drv_geom_err(rc) == VG_LITE_SUCCESS ? VG_LITE_INVALID_ARGUMENT : drv_geom_err(rc);
    return write_ramp(&grad->image, ramp);
}

vg_lite_matrix_t *vg_lite_get_radial_grad_matrix(vg_lite_radial_gradient_t *grad)
{
    return grad ? &grad->matrix : NULL;
}

vg_lite_error_t vg_lite_clear_radial_grad(vg_lite_radial_gradient_t *grad)
{
    vg_lite_error_t e = VG_LITE_SUCCESS;
    if (!grad)
        return VG_LITE_INVALID_ARGUMENT;
    if (grad->image.handle)
        e = vg_lite_free(&grad->image);
    free(grad->color_ramp);
    grad->color_ramp = NULL;
    grad->count = grad->m_count = grad->ramp_length = 0;
    return e;
}

/* As vg_lite_draw_linear_grad: filter is ignored, paint_color is the colour outside [0, 1] under SPREAD_FILL. */
vg_lite_error_t vg_lite_draw_radial_grad(vg_lite_buffer_t *target, vg_lite_path_t *path, vg_lite_fill_t fill_rule,
                                         vg_lite_matrix_t *path_matrix, vg_lite_radial_gradient_t *grad,
                                         vg_lite_color_t paint_color, vg_lite_blend_t blend, vg_lite_filter_t filter)
{
    vg_lite_error_t e = drv_check_open();
    double m[3][3];
    g2d_geom_radial_t rc_;
    static const uint32_t base[3][4] = {
        {G2D_ST_GRAD_P, G2D_ST_GRAD_Q, G2D_ST_GRAD_R, G2D_ST_GRAD_R_HI},
        {G2D_ST_GRAD_UP, G2D_ST_GRAD_UQ, G2D_ST_GRAD_UR, G2D_ST_GRAD_UR_HI},
        {G2D_ST_GRAD_VP, G2D_ST_GRAD_VQ, G2D_ST_GRAD_VR, G2D_ST_GRAD_VR_HI}};
    int sp, rc, k;
    (void)filter;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!grad || !grad->image.handle || !grad->count)
        return VG_LITE_INVALID_ARGUMENT;
    if ((sp = spread_code(grad->spread_mode)) < 0)
        return VG_LITE_NOT_SUPPORT;
    drv_matrix_d(&grad->matrix, m);
    rc = g2d_geom_radial_coeffs(DRV_CM(m), grad->radial_grad.cx, grad->radial_grad.cy, grad->radial_grad.r,
                                grad->radial_grad.fx, grad->radial_grad.fy, &rc_);
    if (rc != G2D_GEOM_OK)
        return drv_geom_err(rc) == VG_LITE_SUCCESS ? VG_LITE_INVALID_ARGUMENT : drv_geom_err(rc);
    for (k = 0; k < 3; k++) {          /* T (GRAD_P..), U (GRAD_UP..), V (GRAD_VP..) */
        drv_state(base[k][0], (uint32_t)rc_.p[k]);
        drv_state(base[k][1], (uint32_t)rc_.q[k]);
        drv_state(base[k][2], (uint32_t)(uint64_t)rc_.r[k]);
        drv_state(base[k][3], (uint32_t)((uint64_t)rc_.r[k] >> 32) & 0xFFFFu);
    }
    drv_state(G2D_ST_GRAD_RAMP_ADDR, grad->image.address);
    if ((e = drv_state_paint(blend, FS(PAINT_CTRL, SRC, G2D_PAINT_RADIAL) | FS(PAINT_CTRL, SPREAD, (uint32_t)sp),
                             drv_argb(paint_color))) != VG_LITE_SUCCESS)
        return e;
    return drv_draw_path(target, path, fill_rule, path_matrix);
}
