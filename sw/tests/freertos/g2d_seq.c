// SPDX-License-Identifier: Apache-2.0
#include <string.h>

#include "g2d_seq.h"
#include "vg_lite.h"

enum { W = 96, H = 64 };

/* CRC-32 (IEEE, as zlib.crc32), table driven: the bitwise loop costs ~1 M instructions per round on the simulated
 * CVA6 (SoC simulation time) */
static uint32_t crc32(const uint8_t *p, uint32_t n, uint32_t c)
{
    static uint32_t tab[256];
    if (!tab[1])
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t v = i;
            for (int k = 0; k < 8; k++)
                v = (v >> 1) ^ (0xEDB88320u & (0u - (v & 1u)));
            tab[i] = v;
        }
    c = ~c;
    while (n--)
        c = (c >> 8) ^ tab[(c ^ *p++) & 0xFFu];
    return ~c;
}

#define CALL(x) j->errors += (x) != VG_LITE_SUCCESS

void g2d_seq_run(g2d_seq_job_t *j)
{
    vg_lite_buffer_t t;
    vg_lite_path_t p;
    vg_lite_matrix_t m;
    uint32_t crc = 0;
    memset(&t, 0, sizeof t);
    t.width = W;
    t.height = H;
    t.format = VG_LITE_BGRA8888;
    CALL(vg_lite_allocate(&t));
    for (int r = 0; r < j->rounds; r++) {
        int k = j->id * 1000 + r;
        vg_lite_rectangle_t rc = { (k * 7) % 40, (k * 5) % 30, 20 + k % 30, 10 + k % 20 };
        int16_t x0 = (int16_t)(5 + k % 50), y0 = (int16_t)(3 + k % 40);
        int16_t pts[] = { 2, x0, y0, 4, (int16_t)(x0 + 30), (int16_t)(y0 + 5), 4, (int16_t)(x0 + 8), (int16_t)(y0 + 20), 0 };
        CALL(vg_lite_clear(&t, NULL, 0xFF000000u | (uint32_t)((uint32_t)k * 2654435761u >> 8)));
        CALL(vg_lite_clear(&t, &rc, 0xFF203040u + (uint32_t)j->id * 0x10u));
        CALL(vg_lite_init_path(&p, VG_LITE_S16, VG_LITE_HIGH, (vg_lite_uint32_t)sizeof pts, pts, 0, 0, W, H));
        CALL(vg_lite_identity(&m));
        CALL(vg_lite_translate((vg_lite_float_t)(j->id % 3), (vg_lite_float_t)(r % 4), &m));
        CALL(vg_lite_draw(&t, &p, VG_LITE_FILL_NON_ZERO, &m, VG_LITE_BLEND_SRC_OVER,
                          0x80000000u | (uint32_t)(j->id * 0x3F1F0F + r)));
        CALL(vg_lite_finish());
        CALL(vg_lite_clear_path(&p));
        for (int y = 0; y < H; y++)
            crc = crc32((const uint8_t *)t.memory + (uint32_t)y * (uint32_t)t.stride, (uint32_t)W * 4u, crc);
        if (g2d_seq_yield)
            g2d_seq_yield();
    }
    CALL(vg_lite_free(&t));
    j->crc = crc;
}
