// SPDX-License-Identifier: Apache-2.0
/* G2D vg_lite driver: matrices (api-subset.md §3). The operations multiply on the right (m = m · op), so
 * the last call applies first to a point. Arithmetic follows rendering.md §9.1: double, + − × ÷ and floor
 * only; sin / cos come from the polynomial in sw/lib/geom (g2d_geom_sincos_deg), never from libm. */
#include <math.h>
#include <string.h>

#include "g2d_geom.h"
#include "vg_lite_drv.h"

static void mul(vg_lite_matrix_t *m, double b[3][3])
{
    double a[3][3], r[3][3];
    int i, j;
    drv_matrix_d(m, a);
    for (i = 0; i < 3; i++)
        for (j = 0; j < 3; j++)
            r[i][j] = a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j];
    for (i = 0; i < 3; i++)
        for (j = 0; j < 3; j++)
            m->m[i][j] = (vg_lite_float_t)r[i][j];
}

vg_lite_error_t vg_lite_identity(vg_lite_matrix_t *matrix)
{
    if (!matrix)
        return VG_LITE_INVALID_ARGUMENT;
    memset(matrix, 0, sizeof *matrix);
    matrix->m[0][0] = matrix->m[1][1] = matrix->m[2][2] = 1.0f;
    matrix->scaleX = matrix->scaleY = 1.0f;
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_translate(vg_lite_float_t x, vg_lite_float_t y, vg_lite_matrix_t *matrix)
{
    double t[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
    if (!matrix)
        return VG_LITE_INVALID_ARGUMENT;
    t[0][2] = (double)x;
    t[1][2] = (double)y;
    mul(matrix, t);
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_scale(vg_lite_float_t scale_x, vg_lite_float_t scale_y, vg_lite_matrix_t *matrix)
{
    double s[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
    if (!matrix)
        return VG_LITE_INVALID_ARGUMENT;
    s[0][0] = (double)scale_x;
    s[1][1] = (double)scale_y;
    mul(matrix, s);
    matrix->scaleX *= scale_x;
    matrix->scaleY *= scale_y;
    return VG_LITE_SUCCESS;
}

/* sin / cos of an angle in degrees: the shared polynomial of sw/lib/geom (exact for multiples of 90). */
void drv_sincos_deg(double deg, double *s, double *c) { g2d_geom_sincos_deg(deg, s, c); }

vg_lite_error_t vg_lite_rotate(vg_lite_float_t degrees, vg_lite_matrix_t *matrix)
{
    double r[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
    double s, c;
    if (!matrix)
        return VG_LITE_INVALID_ARGUMENT;
    drv_sincos_deg((double)degrees, &s, &c);
    r[0][0] = c;
    r[0][1] = -s;
    r[1][0] = s;
    r[1][1] = c;
    mul(matrix, r);
    matrix->angle += degrees;
    return VG_LITE_SUCCESS;
}

/* Projective map of the unit square (0,0),(1,0),(1,1),(0,1) onto the quad p (Heckbert, 1989). */
static int square_to_quad(const vg_lite_float_point4_t p, double h[3][3])
{
    double x0 = p[0].x, y0 = p[0].y, x1 = p[1].x, y1 = p[1].y, x2 = p[2].x, y2 = p[2].y, x3 = p[3].x, y3 = p[3].y;
    double sx = x0 - x1 + x2 - x3, sy = y0 - y1 + y2 - y3;
    if (sx == 0.0 && sy == 0.0) {
        h[0][0] = x1 - x0; h[0][1] = x2 - x1; h[0][2] = x0;
        h[1][0] = y1 - y0; h[1][1] = y2 - y1; h[1][2] = y0;
        h[2][0] = 0.0;     h[2][1] = 0.0;     h[2][2] = 1.0;
    } else {
        double dx1 = x1 - x2, dx2 = x3 - x2, dy1 = y1 - y2, dy2 = y3 - y2;
        double den = dx1 * dy2 - dx2 * dy1, g, hh;
        if (den == 0.0)
            return -1;
        g = (sx * dy2 - dx2 * sy) / den;
        hh = (dx1 * sy - sx * dy1) / den;
        h[0][0] = x1 - x0 + g * x1; h[0][1] = x3 - x0 + hh * x3; h[0][2] = x0;
        h[1][0] = y1 - y0 + g * y1; h[1][1] = y3 - y0 + hh * y3; h[1][2] = y0;
        h[2][0] = g;                h[2][1] = hh;                h[2][2] = 1.0;
    }
    return 0;
}

/* Matrix mapping the quad src onto the quad dst (corner order as given). */
vg_lite_error_t vg_lite_get_transform_matrix(vg_lite_float_point4_t src, vg_lite_float_point4_t dst, vg_lite_matrix_t *mat)
{
    double hs[3][3], hd[3][3], inv[3][3], r[3][3];
    int i, j;
    if (!src || !dst || !mat)
        return VG_LITE_INVALID_ARGUMENT;
    if (square_to_quad(src, hs) || square_to_quad(dst, hd) || g2d_geom_inverse(DRV_CM(hs), inv) != G2D_GEOM_OK)
        return VG_LITE_INVALID_ARGUMENT;
    for (i = 0; i < 3; i++)
        for (j = 0; j < 3; j++)
            r[i][j] = hd[i][0] * inv[0][j] + hd[i][1] * inv[1][j] + hd[i][2] * inv[2][j];
    if (r[2][2] == 0.0)
        return VG_LITE_INVALID_ARGUMENT;
    vg_lite_identity(mat);
    for (i = 0; i < 3; i++)
        for (j = 0; j < 3; j++)
            mat->m[i][j] = (vg_lite_float_t)(r[i][j] / r[2][2]);
    return VG_LITE_SUCCESS;
}
