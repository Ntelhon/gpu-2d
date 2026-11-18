// SPDX-License-Identifier: Apache-2.0
/* G2D vg_lite driver: buffers (api-subset.md §1, §2 "Buffer fields honoured in v1"), format helpers. */
#include <string.h>

#include "vg_lite_drv.h"

/* v1.6b (rendering.md §9.10): OPENVG_* buffer formats name channels from the MSB; each maps onto the FMT code with
 * the same memory layout. Low 6 bits: the OpenVG base format, bit 6 alpha first (ARGB), bit 7 BGR order.
 * Their enum values are those of the vendored vg_lite.h (0..255, below every VG_LITE_* value); build option
 * G2D_DRV_OPENVG_FORMATS = 0 compiles the mapping out (they are then NOT_SUPPORT), in case a later header reuses that
 * range (decision 2026-10-26). */
#ifndef G2D_DRV_OPENVG_FORMATS
#define G2D_DRV_OPENVG_FORMATS 1
#endif
static int ovg_fmt(unsigned f)
{
    static const int8_t rgba[26] = {
        /* sRGBX sRGBA sRGBA_PRE sRGB565 sRGBA5551 sRGBA4444 sL8 */
        0x73, 0x33, 0x33, 0x12, 0x35, 0x34, 0x01,
        /* lRGBX lRGBA lRGBA_PRE lL8 A8 BW1 A1 A4 */
        0x73, 0x33, 0x33, 0x01, 0x00, -1, 0x09, 0x07,
        /* sRGBX_PRE sRGB565_PRE sRGBA5551_PRE sRGBA4444_PRE lRGBX_PRE lRGB565 lRGB565_PRE lRGBA5551 lRGBA5551_PRE
         * lRGBA4444 lRGBA4444_PRE */
        0x73, 0x12, 0x35, 0x34, 0x73, 0x12, 0x12, 0x35, 0x35, 0x34, 0x34};
    /* the same layouts with A first (bit 6), B first (bit 7), both */
    static const struct { uint8_t from, argb, bgra, abgr; } swap[] = {
        {0x73, 0x53, 0x63, 0x43}, {0x33, 0x13, 0x23, 0x03}, {0x12, 0x12, 0x02, 0x02},
        {0x35, 0x15, 0x25, 0x05}, {0x34, 0x14, 0x24, 0x04}};
    unsigned base = f & 63u, v = (f >> 6) & 3u, k;
    int code;
    if (f > 255u || base > 25u || (code = rgba[base]) < 0)
        return -1;
    if (v == 0)
        return code;
    if (code == 0x01 || code == 0x00 || code == 0x09 || code == 0x07)
        return -1;                                           /* no ARGB / BGR variants of L / A formats */
    for (k = 0; k < sizeof swap / sizeof swap[0]; k++)
        if (swap[k].from == (unsigned)code)
            return v == 1 ? swap[k].argb : v == 2 ? swap[k].bgra : swap[k].abgr;
    return -1;
}

/* OPENVG_* formats in the linear (l) colour space (v1.6b); every other format counts as sRGB. */
int drv_fmt_linear(vg_lite_buffer_format_t f)
{
    unsigned b = (unsigned)f & 63u;
    return G2D_DRV_OPENVG_FORMATS && (unsigned)f < 256u && ((b >= 7u && b <= 10u) || b >= 19u);
}

static int drv_fmt_raw(vg_lite_buffer_format_t f);

/* The FMT code of a buffer format, -1 if unknown or not built (v1.7: YUV formats need the YUV unit). */
int drv_fmt(vg_lite_buffer_format_t f)
{
    int c = drv_fmt_raw(f);
    return (c >= 0 && drv_fmt_yuv(c) && !drv_has(DRV_FEAT_YUV)) ? -1 : c;
}

static int drv_fmt_raw(vg_lite_buffer_format_t f)
{
    if ((unsigned)f < 256u)
        return G2D_DRV_OPENVG_FORMATS ? ovg_fmt((unsigned)f) : -1;
    switch (f) {
    case VG_LITE_A8:       return G2D_FMT_A8;
    case VG_LITE_L8:       return G2D_FMT_L8;
    case VG_LITE_RGB565:   return G2D_FMT_RGB565;
    case VG_LITE_BGR565:   return G2D_FMT_BGR565;
    case VG_LITE_RGBA8888: return G2D_FMT_RGBA8888;
    case VG_LITE_BGRA8888: return G2D_FMT_BGRA8888;
    case VG_LITE_ARGB8888: return G2D_FMT_ARGB8888;
    case VG_LITE_ABGR8888: return G2D_FMT_ABGR8888;
    case VG_LITE_RGBX8888: return G2D_FMT_RGBX8888;
    case VG_LITE_BGRX8888: return G2D_FMT_BGRX8888;
    case VG_LITE_XRGB8888: return G2D_FMT_XRGB8888;
    case VG_LITE_XBGR8888: return G2D_FMT_XBGR8888;
    /* v1.4 (rendering.md §3.2) */
    case VG_LITE_RGBA4444: return G2D_FMT_RGBA4444;
    case VG_LITE_BGRA4444: return G2D_FMT_BGRA4444;
    case VG_LITE_ARGB4444: return G2D_FMT_ARGB4444;
    case VG_LITE_ABGR4444: return G2D_FMT_ABGR4444;
    case VG_LITE_RGBA5551: return G2D_FMT_RGBA5551;
    case VG_LITE_BGRA5551: return G2D_FMT_BGRA5551;
    case VG_LITE_ARGB1555: return G2D_FMT_ARGB1555;
    case VG_LITE_ABGR1555: return G2D_FMT_ABGR1555;
    case VG_LITE_RGBA2222: return G2D_FMT_RGBA2222;
    case VG_LITE_BGRA2222: return G2D_FMT_BGRA2222;
    case VG_LITE_ARGB2222: return G2D_FMT_ARGB2222;
    case VG_LITE_ABGR2222: return G2D_FMT_ABGR2222;
    case VG_LITE_A4:       return G2D_FMT_A4;
    case VG_LITE_A2:       return G2D_FMT_A2;
    case VG_LITE_A1:       return G2D_FMT_A1;
    case VG_LITE_L4:       return G2D_FMT_L4;
    case VG_LITE_INDEX_8:  return G2D_FMT_I8;
    case VG_LITE_INDEX_4:  return G2D_FMT_I4;
    case VG_LITE_INDEX_2:  return G2D_FMT_I2;
    case VG_LITE_INDEX_1:  return G2D_FMT_I1;
    case VG_LITE_A8L8:     return G2D_FMT_A8L8;     /* v1.6, source only */
    /* v1.6b: 24-bit sources in hardware (rendering.md §3.2, §9.6) */
    case VG_LITE_RGB888:   return G2D_FMT_RGB888;
    case VG_LITE_BGR888:   return G2D_FMT_BGR888;
    case VG_LITE_ARGB8565: return G2D_FMT_ARGB8565;
    case VG_LITE_ABGR8565: return G2D_FMT_ABGR8565;
    case VG_LITE_RGBA5658: return G2D_FMT_RGBA5658;
    case VG_LITE_BGRA5658: return G2D_FMT_BGRA5658;
    /* v1.6b: YUV sources (rendering.md §3.7) */
    case VG_LITE_YUY2:     return G2D_FMT_YUY2;
    case VG_LITE_AYUY2:    return G2D_FMT_AYUY2;
    case VG_LITE_NV12:     return G2D_FMT_NV12;
    case VG_LITE_ANV12:    return G2D_FMT_ANV12;
    case VG_LITE_NV16:     return G2D_FMT_NV16;
    case VG_LITE_NV24:     return G2D_FMT_NV24;
    case VG_LITE_YV12:     return G2D_FMT_YV12;
    case VG_LITE_YV16:     return G2D_FMT_YV16;
    case VG_LITE_YV24:     return G2D_FMT_YV24;
    default:               return -1;
    }
}

/* Bits per pixel of a hardware format code (rendering.md §3.2, by class; extended codes: 24-bit, v1.6b). */
unsigned drv_fmt_bits(int hw_fmt)
{
    static const uint8_t bits[16] = {8, 8, 16, 32, 16, 16, 8, 4, 2, 1, 4, 8, 4, 2, 1, 16};   /* ..., A8L8 (v1.6) */
    if ((unsigned)hw_fmt & 0x80u)   /* 24-bit; YUV (v1.6b): the Y / packed plane, YUY2 16 bit per pixel */
        return ((unsigned)hw_fmt & 0xFu) <= 2u ? 24u : ((unsigned)hw_fmt & 0xFu) == 3u ? 16u : 8u;
    return bits[(unsigned)hw_fmt & 0xFu];
}

/* Alignment unit of addresses and strides in bytes: the pixel size, 1 for sub-byte, 24-bit and planar YUV formats,
 * 4 for YUY2. */
unsigned drv_fmt_bytes(int hw_fmt)
{
    unsigned b = drv_fmt_bits(hw_fmt) / 8u;
    if (drv_fmt_yuv(hw_fmt))
        return ((unsigned)hw_fmt & 0xFu) == 3u ? 4u : 1u;    /* YUY2: 4-byte Y0 U Y1 V pairs (rendering.md §3.7) */
    return (b && b != 3u) ? b : 1u;
}

/* Sub-byte, indexed, A8L8 and the extended (24-bit) formats are sources only (rendering.md §13.1). */
int drv_fmt_target(int hw_fmt) { return hw_fmt >= 0 && !((unsigned)hw_fmt & 0x80u) && ((unsigned)hw_fmt & 0xFu) < 7u; }

int drv_fmt_indexed(int hw_fmt)
{
    return hw_fmt >= 0 && !((unsigned)hw_fmt & 0x80u) && ((unsigned)hw_fmt & 0xFu) >= 11u && ((unsigned)hw_fmt & 0xFu) <= 14u;
}

int drv_fmt_yuv(int hw_fmt)
{
    return hw_fmt >= 0 && ((unsigned)hw_fmt & 0x80u) && ((unsigned)hw_fmt & 0xFu) >= 3u && ((unsigned)hw_fmt & 0xFu) <= 5u;
}

uint32_t drv_row_bytes(int hw_fmt, int32_t width)
{
    if (drv_fmt_yuv(hw_fmt) && ((unsigned)hw_fmt & 0xFu) == 3u)
        return 4u * (((uint32_t)width + 1u) / 2u);                  /* YUY2: whole Y0 U Y1 V pairs */
    return ((uint32_t)width * drv_fmt_bits(hw_fmt) + 7u) / 8u;
}

/* v1.6b YUV planes (rendering.md §3.7): plane 1 (U V or U) and plane 2 (V or alpha) row bytes and rows; 0 rows:
 * the plane is not used. */
void drv_yuv_planes(int hw_fmt, int32_t w, int32_t h, uint32_t row[2], uint32_t rows[2])
{
    unsigned cls = (unsigned)hw_fmt & 0xFu, sub = ((unsigned)hw_fmt >> 4) & 3u, alpha = ((unsigned)hw_fmt >> 6) & 1u;
    uint32_t xs = sub <= 1u, ys = sub == 0u;
    uint32_t cw = ((uint32_t)w + (1u << xs) - 1u) >> xs, ch = ((uint32_t)h + (1u << ys) - 1u) >> ys;
    row[0] = cls == 4u ? 2u * cw : cw;
    rows[0] = cls == 3u ? 0u : ch;
    row[1] = cls == 5u ? cw : (uint32_t)w;
    rows[1] = cls == 5u ? ch : alpha ? (uint32_t)h : 0u;
}

uint32_t drv_argb(vg_lite_color_t c)
{
    return (c & 0xFF00FF00u) | ((c & 0xFFu) << 16) | ((c >> 16) & 0xFFu);
}

vg_lite_error_t drv_check_buffer(const vg_lite_buffer_t *b)
{
    int f;
    unsigned bpp;
    if (!b)
        return VG_LITE_INVALID_ARGUMENT;
    f = drv_fmt(b->format);
    if (f < 0 || b->tiled != VG_LITE_LINEAR || b->compress_mode != VG_LITE_DEC_DISABLE || b->fc_enable)
        return VG_LITE_NOT_SUPPORT;
    bpp = drv_fmt_bytes(f);
    if (b->width < 1 || b->height < 1 || b->width > 8192 || b->height > 8192)
        return VG_LITE_INVALID_ARGUMENT;
    if (b->stride < 0 || (uint32_t)b->stride < drv_row_bytes(f, b->width) || b->stride >= 65536)
        return VG_LITE_INVALID_ARGUMENT;
    if (b->address % bpp || (uint32_t)b->stride % bpp)
        return VG_LITE_NOT_ALIGNED;
    return VG_LITE_SUCCESS;
}

vg_lite_error_t drv_check_target(const vg_lite_buffer_t *b)
{
    vg_lite_error_t e = drv_check_buffer(b);
    if (e == VG_LITE_SUCCESS && !drv_fmt_target(drv_fmt(b->format)))
        return VG_LITE_NOT_SUPPORT;      /* sub-byte / indexed targets (rendering.md §13.1) */
    return e;
}

vg_lite_error_t vg_lite_allocate(vg_lite_buffer_t *buffer)
{
    vg_lite_error_t e = drv_check_open();
    int f;
    uint32_t stride;
    void *cpu;
    uint32_t gpu;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!buffer || buffer->width < 1 || buffer->height < 1 || buffer->width > 8192 || buffer->height > 8192)
        return VG_LITE_INVALID_ARGUMENT;
    f = drv_fmt(buffer->format);
    if (f < 0 || buffer->tiled != VG_LITE_LINEAR)
        return VG_LITE_NOT_SUPPORT;
    /* stride = align(width * bpp, 64) (api-subset.md §1); a larger stride set by the caller is kept. Source-only
     * formats can be allocated and uploaded; they work as sources only. */
    stride = ((drv_row_bytes(f, buffer->width) + 63u) & ~63u);
    if (buffer->stride > 0 && (uint32_t)buffer->stride > stride)
        stride = (uint32_t)buffer->stride;
    if (stride >= 65536u)
        return VG_LITE_INVALID_ARGUMENT;
    {
        /* v1.6b YUV (rendering.md §9.10): planes 1 and 2 follow the Y plane in the same block, rows 64-byte aligned */
        uint32_t row[2] = {0, 0}, rows[2] = {0, 0}, ps[2], size = stride * (uint32_t)buffer->height, off[2];
        int k;
        if (drv_fmt_yuv(f))
            drv_yuv_planes(f, buffer->width, buffer->height, row, rows);
        for (k = 0; k < 2; k++) {
            ps[k] = (row[k] + 63u) & ~63u;
            off[k] = size;
            size += ps[k] * rows[k];
        }
        if (g_drv.hal->mem_alloc(g_drv.hal->ctx, size, 64, &cpu, &gpu))
            return VG_LITE_OUT_OF_MEMORY;
        if (drv_fmt_yuv(f)) {
            uint8_t *c = (uint8_t *)cpu;
            unsigned cls = (unsigned)f & 0xFu;
            buffer->yuv.uv_planar = rows[0] ? gpu + off[0] : 0;
            buffer->yuv.uv_stride = rows[0] ? ps[0] : 0;
            buffer->yuv.uv_height = rows[0];
            buffer->yuv.uv_memory = rows[0] ? c + off[0] : NULL;
            if (cls == 5u) {
                buffer->yuv.v_planar = gpu + off[1];
                buffer->yuv.v_stride = ps[1];
                buffer->yuv.v_height = rows[1];
                buffer->yuv.v_memory = c + off[1];
            } else if (rows[1]) {
                buffer->yuv.alpha_planar = gpu + off[1];
                buffer->yuv.alpha_stride = ps[1];
                buffer->yuv.alpha_height = rows[1];
                buffer->yuv.alpha_memory = c + off[1];
            }
        }
    }
    buffer->stride = (vg_lite_int32_t)stride;
    buffer->memory = cpu;
    buffer->handle = cpu;
    buffer->address = gpu;
    buffer->base_address = gpu;
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_free(vg_lite_buffer_t *buffer)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!buffer || !buffer->handle)
        return VG_LITE_INVALID_ARGUMENT;
    /* draws that may still read or write it must have finished, or the GPU must be stopped (D-2); if neither can
     * be confirmed the memory is left allocated (intentional leak) and the error returned */
    {
        int safe;
        e = drv_quiesce(&safe);
        if (!safe)
            return e;
    }
    g_drv.hal->mem_free(g_drv.hal->ctx, buffer->memory, buffer->address);
    buffer->handle = NULL;
    buffer->memory = NULL;
    buffer->address = 0;
    return e;
}

vg_lite_error_t vg_lite_upload_buffer(vg_lite_buffer_t *buffer, vg_lite_uint8_t *data[3], vg_lite_uint32_t stride[3])
{
    vg_lite_error_t e = drv_check_open();
    int f, y;
    uint32_t row;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!buffer || !buffer->memory || !data || !data[0] || !stride)
        return VG_LITE_INVALID_ARGUMENT;
    f = drv_fmt(buffer->format);
    if (f < 0)
        return VG_LITE_NOT_SUPPORT;
    row = drv_row_bytes(f, buffer->width);
    if (stride[0] < row)
        return VG_LITE_INVALID_ARGUMENT;
    for (y = 0; y < buffer->height; y++)
        memcpy((uint8_t *)buffer->memory + (size_t)y * (uint32_t)buffer->stride, data[0] + (size_t)y * stride[0], row);
    g_drv.hal->cache_clean(g_drv.hal->ctx, buffer->memory, (size_t)buffer->stride * (uint32_t)buffer->height);
    if (drv_fmt_yuv(f)) {   /* v1.6b: data[1], data[2] = plane 1 (U V / U), plane 2 (V / alpha) */
        uint32_t prow[2], prows[2];
        uint8_t *mem[2] = {(uint8_t *)buffer->yuv.uv_memory,
                           (uint8_t *)((f & 0xF) == 5 ? buffer->yuv.v_memory : buffer->yuv.alpha_memory)};
        uint32_t pst[2] = {buffer->yuv.uv_stride, (f & 0xF) == 5 ? buffer->yuv.v_stride : buffer->yuv.alpha_stride};
        int k;
        drv_yuv_planes(f, buffer->width, buffer->height, prow, prows);
        for (k = 0; k < 2; k++) {
            if (!prows[k])
                continue;
            if (!data[k + 1] || !mem[k] || stride[k + 1] < prow[k] || pst[k] < prow[k])
                return VG_LITE_INVALID_ARGUMENT;
            for (y = 0; y < (int)prows[k]; y++)
                memcpy(mem[k] + (size_t)y * pst[k], data[k + 1] + (size_t)y * stride[k + 1], prow[k]);
            g_drv.hal->cache_clean(g_drv.hal->ctx, mem[k], (size_t)pst[k] * prows[k]);
        }
    }
    return VG_LITE_SUCCESS;
}

/* Wraps an existing GPU-visible buffer given by buffer->address (VG_LITE_MAP_USER_MEMORY). */
vg_lite_error_t vg_lite_map(vg_lite_buffer_t *buffer, vg_lite_map_flag_t flag, vg_lite_int32_t fd)
{
    vg_lite_error_t e = drv_check_open();
    (void)fd;
    if (e != VG_LITE_SUCCESS)
        return e;
    if (flag != VG_LITE_MAP_USER_MEMORY)
        return VG_LITE_NOT_SUPPORT;
    e = drv_check_buffer(buffer);
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!buffer->memory) {
        buffer->memory = g_drv.hal->mem_cpu(g_drv.hal->ctx, buffer->address,
                                            (size_t)buffer->stride * (uint32_t)buffer->height);
        if (!buffer->memory)
            return VG_LITE_INVALID_ARGUMENT;
    }
    buffer->handle = NULL;      /* not owned: vg_lite_free does not apply */
    return VG_LITE_SUCCESS;
}

vg_lite_error_t vg_lite_unmap(vg_lite_buffer_t *buffer)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!buffer)
        return VG_LITE_INVALID_ARGUMENT;
    {
        int safe;
        e = drv_quiesce(&safe);       /* the memory goes back to the caller: the GPU must be done with it */
        if (!safe)
            return e;
    }
    buffer->memory = NULL;
    buffer->address = 0;
    return e;
}

vg_lite_error_t vg_lite_flush_mapped_buffer(vg_lite_buffer_t *buffer)
{
    vg_lite_error_t e = drv_check_open();
    if (e != VG_LITE_SUCCESS)
        return e;
    if (!buffer || !buffer->memory)
        return VG_LITE_INVALID_ARGUMENT;
    /* both directions (write back and invalidate, a cache "flush"): CPU writes become visible to the GPU, and
     * the CPU sees what the GPU wrote once the draws are finished */
    g_drv.hal->cache_clean(g_drv.hal->ctx, buffer->memory, (size_t)buffer->stride * (uint32_t)buffer->height);
    g_drv.hal->cache_invalidate(g_drv.hal->ctx, buffer->memory, (size_t)buffer->stride * (uint32_t)buffer->height);
    return VG_LITE_SUCCESS;
}
