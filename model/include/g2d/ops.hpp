// SPDX-License-Identifier: Apache-2.0
// G2D golden model: pure stage functions. Every function implements a numbered section of
// spec/rendering.md exactly; section numbers are given in the comments.
#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace g2d {

// ---------------------------------------------------------------------------------------------
// §2 integer helpers
// ---------------------------------------------------------------------------------------------

// round(v / 255) for 0 <= v <= 255*255 (exact, no ties possible).
inline uint32_t div255r(uint32_t v) {
  v += 128;
  return (v + (v >> 8)) >> 8;
}
inline uint8_t mul8(uint32_t a, uint32_t b) { return static_cast<uint8_t>(div255r(a * b)); }
// Coverage blend: d*(1-c) + s*c, rounded.
inline uint8_t lerp8(uint32_t d, uint32_t s, uint32_t c) {
  return static_cast<uint8_t>(div255r(s * c + d * (255u - c)));
}
inline uint8_t sat8(int v) { return static_cast<uint8_t>(v < 0 ? 0 : (v > 255 ? 255 : v)); }

// Floor division and ceiling division for signed 64-bit operands (b != 0).
inline int64_t floor_div(int64_t a, int64_t b) {
  int64_t q = a / b;
  if ((a % b != 0) && ((a < 0) != (b < 0))) --q;
  return q;
}
inline int64_t ceil_div(int64_t a, int64_t b) { return -floor_div(-a, b); }

// ---------------------------------------------------------------------------------------------
// §3 pixel formats
// ---------------------------------------------------------------------------------------------

struct Argb {
  uint8_t a = 0, r = 0, g = 0, b = 0;
  bool operator==(const Argb& o) const { return a == o.a && r == o.r && g == o.g && b == o.b; }
  bool operator!=(const Argb& o) const { return !(*this == o); }
};

inline uint32_t pack(Argb c) {
  return (uint32_t(c.a) << 24) | (uint32_t(c.r) << 16) | (uint32_t(c.g) << 8) | uint32_t(c.b);
}
inline Argb unpack(uint32_t v) {
  return Argb{uint8_t(v >> 24), uint8_t(v >> 16), uint8_t(v >> 8), uint8_t(v)};
}

// FMT byte: [3:0] class, [5:4] order, [6] x_alpha (§3.2).
enum FmtClass : uint8_t {
  kA8 = 0, kL8 = 1, kP16 = 2, kP32 = 3, kP4444 = 4, kP5551 = 5, kP2222 = 6,
  kA4 = 7, kA2 = 8, kA1 = 9, kL4 = 10, kI8 = 11, kI4 = 12, kI2 = 13, kI1 = 14, kA8L8 = 15,
  // extended classes (FMT bit 7, v1.6b): 24-bit sources
  kX888 = 0, kX8565 = 1, kX5658 = 2, kXYuy2 = 3, kXNv = 4, kXYv = 5,   // YUV (v1.6b, §3.7)
};

// Bits per pixel (1, 2, 4, 8, 16, 32) of a format code of §3.2, or 0 if the code is not one.
unsigned fmt_bits(uint8_t fmt);
inline bool fmt_valid(uint8_t fmt) { return fmt_bits(fmt) != 0; }
// Bytes per pixel for formats of 8 bits or more; 0 for sub-byte and invalid codes.
inline unsigned fmt_bytes(uint8_t fmt) { return fmt_bits(fmt) / 8; }
// Source-only codes (sub-byte, indexed) are not valid targets (§13.1).
// §5.2.1 colour unit (v1.6b): gamma (0 none, 1 sRGB -> linear, 2 linear -> sRGB) then the 4 x 5 matrix when en;
// m[o][j] signed s7.8, rows / columns in the order A, R, G, B; b[o] signed, 1/256 channel steps.
struct ColorUnit {
  bool en = false;
  unsigned gamma = 0;
  int32_t m[4][4] = {};
  int32_t b[4] = {};
};
Argb color_apply(Argb c, const ColorUnit& cu);
uint8_t gamma_lin(uint8_t c);
uint8_t gamma_srgb(uint8_t c);

// §3.7 YUV (v1.6b): extended classes 3..5
inline bool fmt_yuv(uint8_t fmt) { return (fmt & 0x80) && (fmt & 0xF) >= kXYuy2 && (fmt & 0xF) <= kXYv; }
// §3.7 conversion of an assembled texel {A, Y, U, V} (A << 24 | Y << 16 | U << 8 | V), limited range
Argb yuv_expand(uint32_t ayuv, bool bt709);
inline bool fmt_target_ok(uint8_t fmt) { return fmt_valid(fmt) && !(fmt & 0x80) && (fmt & 0xF) < kA4; }
inline bool fmt_indexed(uint8_t fmt) { return fmt_valid(fmt) && (fmt & 0xF) >= kI8 && (fmt & 0xF) <= kI1; }

// §3.3: raw = little-endian value of the pixel's bytes (sub-byte: the pixel's bits, right-aligned).
// Indexed formats look raw up in clut (256 ARGB32 words), which must then be given.
Argb expand(uint8_t fmt, uint32_t raw, const uint32_t* clut = nullptr);
// §3.4: returns the little-endian raw value to store (fmt_bytes(fmt) bytes). Target formats only.
uint32_t compress(uint8_t fmt, Argb c);
// §3.4 with the §3.6 dither for the pixel at target (x, y).
uint32_t compress_dither(uint8_t fmt, Argb c, int x, int y);
// §3.6 one channel: c packed to n < 8 bits with Bayer value d (0..15).
inline uint32_t dither_channel(uint32_t c, unsigned n, unsigned d) {
  return (c * ((1u << n) - 1u) * 32u + 255u * (2u * d + 1u)) / 8160u;
}
extern const uint8_t kBayer[4][4];

// ---------------------------------------------------------------------------------------------
// §5 paint and blend
// ---------------------------------------------------------------------------------------------

struct ColorKey {
  bool en = false;
  uint8_t lr = 0, lg = 0, lb = 0;
  uint8_t hr = 0, hg = 0, hb = 0;
  uint8_t alpha = 0;
};

Argb color_key(Argb c, const ColorKey keys[4]);       // §5.4
Argb premultiply(Argb c);                             // §5.2 step 4
Argb apply_global_alpha(Argb c, unsigned mode, uint8_t value);  // §5.3 / §5.5: 0 NORMAL 1 GLOBAL 2 SCALED
Argb multiply_color(Argb s, Argb paint);              // §5.3 image mode MULTIPLY
Argb blend(unsigned mode, Argb s, Argb d);            // §5.6, mode 0..15
// §5.3.1 stencil (v1.6): image value i, paint p -> (source, channel alphas); blend with per-channel source alpha
Argb stencil_source(Argb i, Argb p, uint8_t ac[3]);
Argb blend_stencil(unsigned mode, Argb s, Argb d, const uint8_t ac[3]);
Argb coverage_blend(Argb d, Argb b, uint8_t cov);     // §4 step 5
bool blend_reads_dst(unsigned mode);                  // every mode except NONE
// §5.7 blend families (v1.6): family 1 OpenVG, 2 SVG2, 3 Porter-Duff (func = BLEND_FUNC, c = the constant).
Argb blend_family(unsigned family, unsigned mode, uint32_t func, Argb s, Argb d, Argb c);
bool blend_family_ok(unsigned family, unsigned mode, uint32_t func);   // §13.1

// ---------------------------------------------------------------------------------------------
// §7 image sampling
// ---------------------------------------------------------------------------------------------

struct Xform {
  int32_t a = 1 << 16, b = 0, c = 0;  // s15.16
  int32_t d = 0, e = 1 << 16, f = 0;  // s15.16
  int32_t g = 0, h = 0, i = 1 << 30;  // s1.30
  bool persp = false;
};

// §7.1: maps pixel (x,y) to source (u,v) in s15.16. Returns false if the pixel is skipped (W <= 0).
bool xform_map(const Xform& m, int x, int y, int64_t& u, int64_t& v);

// §7.3 bilinear weights and combination. t = {t00, t10, t01, t11}.
Argb bilinear(const Argb t[4], unsigned fx, unsigned fy);
// §7.3 Gaussian (v1.6b): t[3 j + i] = texel (x + i - 1, y + j - 1); signed weights in 1/4096 (centre, edge,
// corner); clamp((sum + 2048) >> 12, 0, 255).
Argb gaussian(const Argb t[9], int32_t w0, int32_t w1, int32_t w2);

// §7.5 wrap of one integer texel coordinate x against [s0, s1). mode: 1 COLOR, 2 PAD, 3 REPEAT, 4 REFLECT
// (0 NONE clamps, as the bilinear neighbours of §7.3). Returns false when the texel is the border colour
// (COLOR, x outside).
bool wrap_coord(unsigned mode, int64_t x, int s0, int s1, int& out);

// ---------------------------------------------------------------------------------------------
// §8 path coverage
// ---------------------------------------------------------------------------------------------

struct Edge {
  int32_t x0, y0, x1, y1;  // s.4 (1/16 px)
};

extern const uint8_t kSampleQ[16];  // §8.2 n-rooks x offsets

// §8.1 hardware clamp of a coordinate to the guard band.
inline int32_t clamp_coord(int32_t v) {
  const int32_t lim = (1 << 18) - 1;
  return v < -lim ? -lim : (v > lim ? lim : v);
}

// Coverage of pixels [x0, x1) of row py (§8.2-8.4). Edges must already be clamped.
// cov must hold x1 - x0 entries.
void coverage_row(const std::vector<Edge>& edges, int py, int x0, int x1, bool aa, bool even_odd,
                  uint8_t* cov);

// ---------------------------------------------------------------------------------------------
// §10 gradient
// ---------------------------------------------------------------------------------------------

// Ramp index for pixel (x,y). r is the sign-extended 48-bit GRAD_R. spread: 0 PAD 1 REPEAT 2 REFLECT
// 3 FILL. Returns -1 under FILL when the index is outside [0, 255] (the paint colour is used).
int grad_index(int32_t p, int32_t q, int64_t r, int x, int y, unsigned spread);

// §10.1 spread of a signed ramp index (as grad_index; -1 = outside under FILL).
int spread_index(int64_t i, unsigned spread);

// Exact floor(sqrt(v)) for any 64-bit v (§10.4).
uint64_t isqrt64(uint64_t v);

// §10.4 radial ramp index: T, U, V coefficients (p, q, sign-extended 48-bit r) in that order.
int radial_index(const int32_t p[3], const int32_t q[3], const int64_t r[3], int x, int y, unsigned spread);

}  // namespace g2d
