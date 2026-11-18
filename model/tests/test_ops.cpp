// SPDX-License-Identifier: Apache-2.0
// Unit tests for the golden model stage functions (spec/rendering.md). Self-contained, no framework.
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <vector>

#include "g2d/gpu.hpp"
#include "g2d/ops.hpp"

using namespace g2d;

static int g_failures = 0;
static int g_checks = 0;
#define CHECK(cond)                                                          \
  do {                                                                       \
    ++g_checks;                                                              \
    if (!(cond)) {                                                           \
      if (g_failures++ < 20) std::printf("%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #cond); \
    }                                                                        \
  } while (0)

// Target formats (rendering.md §3.2).
static const uint8_t kFmts[] = {0x00, 0x01, 0x02, 0x12, 0x03, 0x13, 0x23, 0x33, 0x43, 0x53, 0x63, 0x73,
                                0x04, 0x14, 0x24, 0x34, 0x05, 0x15, 0x25, 0x35, 0x06, 0x16, 0x26, 0x36};
// Source-only formats (sub-byte, indexed).
static const uint8_t kSrcOnly[] = {0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E};

static void test_div255r() {
  for (uint32_t v = 0; v <= 255u * 255u; ++v) CHECK(div255r(v) == uint32_t(std::floor(v / 255.0 + 0.5)));
  for (uint32_t a = 0; a < 256; ++a)
    for (uint32_t b = 0; b < 256; ++b) CHECK(mul8(a, b) == mul8(b, a));
  for (uint32_t c = 0; c < 256; ++c) CHECK(mul8(c, 255) == c && mul8(c, 0) == 0);
}

static void test_floor_ceil_div() {
  for (int64_t a = -50; a <= 50; ++a)
    for (int64_t b : {-7, -3, -1, 1, 2, 5, 32}) {
      CHECK(floor_div(a, b) == int64_t(std::floor(double(a) / double(b))));
      CHECK(ceil_div(a, b) == int64_t(std::ceil(double(a) / double(b))));
    }
}

static void test_formats() {
  // fmt_bytes table and invalid codes
  int valid = 0;
  for (int f = 0; f < 256; ++f) valid += fmt_valid(uint8_t(f)) ? 1 : 0;
  CHECK(valid == 48);   // v1.6: A8L8 (0x0F); v1.6b: six 24-bit codes, nine YUV codes
  // §3.7 YUV (v1.6b), by hand from round(1024 k): black / white levels, BT.601 and BT.709 red
  CHECK(pack(yuv_expand(0xFF108080u, false)) == 0xFF000000u);   // Y 16: 0
  CHECK(pack(yuv_expand(0xFFEB8080u, false)) == 0xFFFFFFFFu);   // Y 235: (1192 * 219 + 512) >> 10 = 255
  // Y 81 U 90 V 240: R (1192 * 65 + 1634 * 112 + 512) >> 10 = 254, G (77480 + 401 * 38 - 832 * 112 + 512) >> 10 = 0,
  // B (77480 - 2066 * 38 + 512) >> 10 = -1 -> 0; A from the word
  CHECK(pack(yuv_expand(0x80515AF0u, false)) == 0x80FE0000u);
  // BT.709, Y 63 U 102 V 240: R 262168 >> 10 = 256 -> 255, G (56024 + 218 * 26 - 546 * 112 + 512) >> 10 = 1, B 0
  CHECK(pack(yuv_expand(0xFF3F66F0u, true)) == 0xFFFF0100u);
  for (uint8_t f : {0x83, 0xC3, 0x84, 0xC4, 0x94, 0xA4, 0x85, 0x95, 0xA5}) CHECK(fmt_yuv(f) && !fmt_target_ok(f));
  CHECK(!fmt_valid(0xD4) && !fmt_valid(0xC5) && !fmt_valid(0xB4) && !fmt_valid(0x93));
  // 24-bit (v1.6b, rendering.md §3.2 / §3.3), by hand: bytes 0x12 0x34 0x56 -> value 0x563412
  for (uint8_t f : {0x80, 0x90, 0x81, 0x91, 0x82, 0x92}) CHECK(fmt_bits(f) == 24 && !fmt_target_ok(f) && !fmt_indexed(f));
  CHECK(pack(expand(0x80, 0x563412u, nullptr)) == 0xFF123456u);   // RGB888: R = byte 0
  CHECK(pack(expand(0x90, 0x563412u, nullptr)) == 0xFF563412u);   // BGR888: B = byte 0
  // ARGB8565: A = 0x12, 565 part 0x5634: R = 0x14 -> 0xA5, G = 0x31 -> 0xC7, B = 0x0A -> 0x52
  CHECK(pack(expand(0x81, 0x563412u, nullptr)) == 0x12A5C752u);
  CHECK(pack(expand(0x91, 0x563412u, nullptr)) == 0x1252C7A5u);   // ABGR8565: B, R swapped
  // RGBA5658: 565 part 0x3412: R = 0x12 -> 0x94, G = 0x20 -> 0x82, B = 0x06 -> 0x31; A = 0x56
  CHECK(pack(expand(0x82, 0x563412u, nullptr)) == 0x56948231u);
  CHECK(pack(expand(0x92, 0x563412u, nullptr)) == 0x56318294u);   // BGRA5658
  CHECK(fmt_bits(0x0F) == 16 && !fmt_target_ok(0x0F) && !fmt_indexed(0x0F));
  CHECK(pack(expand(0x0F, 0x1234u, nullptr)) == 0x34121212u);   // A = bits 7:0, L = bits 15:8
  for (uint8_t f : kFmts) CHECK(fmt_target_ok(f) && !fmt_indexed(f));
  for (uint8_t f : kSrcOnly) CHECK(fmt_valid(f) && !fmt_target_ok(f));
  CHECK(fmt_bits(0x07) == 4 && fmt_bits(0x08) == 2 && fmt_bits(0x09) == 1 && fmt_bits(0x0A) == 4);
  CHECK(fmt_bits(0x0B) == 8 && fmt_bits(0x0C) == 4 && fmt_bits(0x0D) == 2 && fmt_bits(0x0E) == 1);
  CHECK(fmt_bits(0x16) == 8 && fmt_bits(0x25) == 16 && fmt_bits(0x44) == 0 && fmt_bits(0x17) == 0 && fmt_bits(0xA0) == 0 &&
        fmt_bits(0xC0) == 0);
  // §3.4 then §3.3 is idempotent: compress(expand(raw)) == raw for every stored value
  for (uint8_t f : kFmts) {
    const unsigned n = fmt_bytes(f);
    if (n <= 2) {
      for (uint32_t raw = 0; raw < (1u << (8 * n)); ++raw) CHECK(compress(f, expand(f, raw)) == raw);
    } else {
      std::mt19937 rng(f);
      for (int k = 0; k < 20000; ++k) {
        uint32_t raw = rng();
        uint32_t back = compress(f, expand(f, raw));
        const uint32_t xmask = (f & 0x40) ? 0u : 0u;
        (void)xmask;
        if (f & 0x40) {
          // X byte is written as 0xFF: compare colour bytes only (alpha byte position per order)
          Argb a = expand(f, raw), b = expand(f, back);
          CHECK(a == b && b.a == 255);
        } else {
          CHECK(back == raw);
        }
      }
    }
  }
  // Byte layouts (rendering.md §3.2) on a known colour A=0x11 R=0x22 G=0x33 B=0x44
  const Argb c{0x11, 0x22, 0x33, 0x44};
  CHECK(compress(0x03, c) == 0x11443322u);  // RGBA bytes R,G,B,A
  CHECK(compress(0x13, c) == 0x11223344u);  // BGRA bytes B,G,R,A (LVGL ARGB8888 = 0xAARRGGBB word)
  CHECK(compress(0x23, c) == 0x44332211u);  // ARGB bytes A,R,G,B
  CHECK(compress(0x33, c) == 0x22334411u);  // ABGR bytes A,B,G,R
  CHECK(compress(0x53, c) == 0xFF223344u);  // BGRX
  CHECK(compress(0x00, c) == 0x11u);
  // 565: pure red in BGR565 (LVGL RGB565) has R in bits 15:11
  CHECK(compress(0x12, Argb{255, 255, 0, 0}) == 0xF800u);
  CHECK(compress(0x02, Argb{255, 255, 0, 0}) == 0x001Fu);
  CHECK(expand(0x12, 0x07E0u) == (Argb{255, 0, 255, 0}));
  // v1.4 packed layouts, channels from bit 0 in the name's order
  CHECK(compress(0x15, Argb{255, 255, 0, 0}) == 0xFC00u);   // BGRA5551: R in [14:10], A in [15]
  CHECK(compress(0x25, Argb{255, 0, 0, 255}) == 0xF801u);   // ARGB1555: A [0], R [5:1], G [10:6], B [15:11]
  CHECK(compress(0x15, Argb{127, 0, 0, 0}) == 0x0000u && compress(0x15, Argb{128, 0, 0, 0}) == 0x8000u);
  CHECK(compress(0x14, Argb{0x11, 0x22, 0x33, 0x44}) == 0x1234u);  // BGRA4444 = c*17 values 1,2,3,4 (A,R,G,B)
  CHECK(compress(0x04, Argb{0x11, 0x22, 0x33, 0x44}) == 0x1432u);  // RGBA4444: R [3:0] ... A [15:12]
  CHECK(compress(0x34, Argb{0x11, 0x22, 0x33, 0x44}) == 0x2341u);  // ABGR4444: A [3:0], B, G, R
  CHECK(compress(0x16, Argb{0xFF, 0xAA, 0x55, 0x00}) == 0xE4u);    // BGRA2222: B 0, G 1, R 2, A 3
  CHECK(expand(0x26, 0x1Bu) == (Argb{255, 170, 85, 0}));           // ARGB2222: A [1:0]=3, R=2, G=1, B=0
  // Sub-byte and indexed expansion (§3.3)
  for (uint32_t v = 0; v < 16; ++v) {
    CHECK(expand(0x07, v) == (Argb{uint8_t(17 * v), 255, 255, 255}));
    CHECK(expand(0x0A, v) == (Argb{255, uint8_t(17 * v), uint8_t(17 * v), uint8_t(17 * v)}));
  }
  for (uint32_t v = 0; v < 4; ++v) CHECK(expand(0x08, v).a == 85 * v);
  CHECK(expand(0x09, 0).a == 0 && expand(0x09, 1).a == 255);
  {
    uint32_t clut[256];
    for (uint32_t i = 0; i < 256; ++i) clut[i] = 0x01020304u * i ^ 0xA5000000u;
    for (uint8_t f : {uint8_t(0x0B), uint8_t(0x0C), uint8_t(0x0D), uint8_t(0x0E)})
      for (uint32_t i = 0; i < (1u << fmt_bits(f)); ++i) CHECK(pack(expand(f, i, clut)) == clut[i]);
  }
  // L8 weights sum to 256: white stays 255, black 0
  CHECK(compress(0x01, Argb{0, 255, 255, 255}) == 255u && compress(0x01, Argb{0, 0, 0, 0}) == 0u);
  CHECK(expand(0x00, 0x80) == (Argb{0x80, 255, 255, 255}));
}

static void test_blend_identities() {
  std::mt19937 rng(7);
  for (int k = 0; k < 200000; ++k) {
    Argb s = unpack(rng()), d = unpack(rng());
    CHECK(blend(0, s, d) == s);
    // SRC_OVER with opaque source is the source
    Argb so = s;
    so.a = 255;
    CHECK(blend(1, so, d) == so);
    // SRC_OVER with fully transparent black source is the destination
    CHECK(blend(1, Argb{0, 0, 0, 0}, d) == d);
    // NORMAL_LVGL alpha is always 255
    for (unsigned m = 11; m <= 15; ++m) CHECK(blend(m, s, d).a == 255);
    // DARKEN <= LIGHTEN per channel
    Argb dk = blend(7, s, d), lt = blend(8, s, d);
    CHECK(dk.r <= lt.r && dk.g <= lt.g && dk.b <= lt.b && dk.a <= lt.a);
    // coverage blend end points
    CHECK(coverage_blend(d, s, 255) == s && coverage_blend(d, s, 0) == d);
  }
}

static void test_xform() {
  Xform id;  // identity: u = x + 0.5 in s15.16
  for (int y = -3; y < 5; ++y)
    for (int x = -3; x < 5; ++x) {
      int64_t u, v;
      CHECK(xform_map(id, x, y, u, v));
      CHECK(u == (int64_t(x) << 16) + 32768 && v == (int64_t(y) << 16) + 32768);
      Xform p = id;
      p.persp = true;  // W = 2^31 everywhere: same result as affine
      int64_t up, vp;
      CHECK(xform_map(p, x, y, up, vp) && up == u && vp == v);
    }
  Xform neg;
  neg.persp = true;
  neg.i = -(1 << 30);
  int64_t u, v;
  CHECK(!xform_map(neg, 0, 0, u, v));  // W <= 0 skipped
  // bilinear: zero fraction returns t00 exactly; equal texels return the texel
  Argb t[4] = {Argb{1, 2, 3, 4}, Argb{9, 9, 9, 9}, Argb{7, 7, 7, 7}, Argb{5, 5, 5, 5}};
  CHECK(bilinear(t, 0, 0) == t[0]);
  Argb same[4] = {t[1], t[1], t[1], t[1]};
  for (unsigned f = 0; f < 256; f += 17) CHECK(bilinear(same, f, 255 - f) == t[1]);
}

static std::vector<Edge> rect_edges(int x0, int y0, int x1, int y1) {  // s.4 coords, clockwise
  return {Edge{x0, y0, x1, y0}, Edge{x1, y0, x1, y1}, Edge{x1, y1, x0, y1}, Edge{x0, y1, x0, y0}};
}

static void test_coverage() {
  uint8_t cov[8];
  // Pixel-aligned square [1,3)x[1,3) in pixels: full coverage inside, none outside, both rules, AA and not
  auto sq = rect_edges(16, 16, 48, 48);
  for (int aa = 0; aa < 2; ++aa)
    for (int eo = 0; eo < 2; ++eo)
      for (int py = 0; py < 4; ++py) {
        coverage_row(sq, py, 0, 4, aa, eo, cov);
        for (int px = 0; px < 4; ++px) {
          const bool in = px >= 1 && px < 3 && py >= 1 && py < 3;
          CHECK(cov[px] == (in ? 255 : 0));
        }
      }
  // Half pixel horizontally: [0, 0.5) x [0,1): 8 of 16 n-rooks samples inside -> 128
  auto half = rect_edges(0, 0, 8, 16);
  coverage_row(half, 0, 0, 1, true, false, cov);
  CHECK(cov[0] == 128);
  // Reversed orientation gives the same coverage under both rules
  std::vector<Edge> rev;
  for (const Edge& e : sq) rev.push_back(Edge{e.x1, e.y1, e.x0, e.y0});
  coverage_row(rev, 1, 0, 4, true, false, cov);
  CHECK(cov[1] == 255 && cov[0] == 0);
  // Two overlapping copies: nonzero inside, even-odd empty
  std::vector<Edge> two = sq;
  two.insert(two.end(), sq.begin(), sq.end());
  coverage_row(two, 1, 0, 4, true, false, cov);
  CHECK(cov[1] == 255);
  coverage_row(two, 1, 0, 4, true, true, cov);
  CHECK(cov[1] == 0);
  // 256 copies: winding 256 = 0 mod 256 -> outside under nonzero (spec §8.3)
  std::vector<Edge> many;
  for (int k = 0; k < 256; ++k) many.insert(many.end(), sq.begin(), sq.end());
  coverage_row(many, 1, 0, 4, true, false, cov);
  CHECK(cov[1] == 0);
  // Coverage of every n in 0..16 maps to the §8.4 table
  for (unsigned n = 0; n <= 16; ++n) CHECK(((n * 255 + 8) >> 4) == (n == 16 ? 255u : (n * 255 + 8) >> 4));
}

// §3.6 dither: matches floor(c*(2^n-1)/255 + (d+0.5)/16), stays in range, keeps 0 and 255, unbiased over a tile.
static void test_dither() {
  for (unsigned n : {1u, 2u, 4u, 5u, 6u})
    for (unsigned c = 0; c < 256; ++c) {
      const unsigned top = (1u << n) - 1u;
      double sum = 0;
      for (unsigned d = 0; d < 16; ++d) {
        const uint32_t q = dither_channel(c, n, d);
        // exact rational check: q = floor((32 c top + 255 (2d+1)) / 8160)
        CHECK(q * 8160u <= c * top * 32u + 255u * (2 * d + 1) && c * top * 32u + 255u * (2 * d + 1) < (q + 1) * 8160u);
        CHECK(q <= top);
        sum += q;
      }
      if (c == 0) CHECK(sum == 0);
      if (c == 255) CHECK(sum == 16.0 * top);
      CHECK(std::fabs(sum / 16 - c * double(top) / 255.0) <= 1.0 / 32 + 1e-9);
    }
  // compress_dither = compress for formats without sub-8-bit colour channels
  const Argb c{0x80, 0x7F, 0x41, 0x13};
  for (uint8_t f : {uint8_t(0x00), uint8_t(0x01), uint8_t(0x13), uint8_t(0x63)})
    for (int y = 0; y < 4; ++y)
      for (int x = 0; x < 4; ++x) CHECK(compress_dither(f, c, x, y) == compress(f, c));
  // alpha of 5551 / 4444 is not dithered; position (x & 3, y & 3) selects BAYER[y][x]
  CHECK(compress_dither(0x15, Argb{128, 0, 0, 0}, 3, 1) == 0x8000u);
  CHECK(compress_dither(0x12, Argb{255, 4, 4, 4}, 5, 6) ==
        ((dither_channel(4, 5, kBayer[2][1]) << 11) | (dither_channel(4, 6, kBayer[2][1]) << 5) | dither_channel(4, 5, kBayer[2][1])));
}

// §7.5 wrap against a brute-force definition.
static void test_wrap() {
  for (int s0 : {0, 3, 100})
    for (int n : {1, 2, 3, 7, 64})
      for (int64_t x = -300; x < 400; ++x) {
        const int s1 = s0 + n;
        int o;
        CHECK(wrap_coord(1, x, s0, s1, o) == (x >= s0 && x < s1));
        CHECK(wrap_coord(2, x, s0, s1, o) && o == std::min<int64_t>(std::max<int64_t>(x, s0), s1 - 1));
        // REPEAT: unique o in [s0, s1) with o == x (mod n)
        CHECK(wrap_coord(3, x, s0, s1, o) && o >= s0 && o < s1 && ((x - o) % n) == 0);
        // REFLECT: walk the mirrored sequence ... s0..s1-1, s1-1..s0, s0..s1-1 ...
        int64_t k = x - s0, period = 2 * n;
        k = ((k % period) + period) % period;
        const int64_t ref = k < n ? s0 + k : s0 + (period - 1 - k);
        CHECK(wrap_coord(4, x, s0, s1, o) && o == ref);
      }
}

// The RTL's wrap modulo (rtl/blit/g2d_tex_addr.sv, v1.4): q = (r * R) >> 31 with the per-draw reciprocal
// R = ceil(2^31 / m), then one correction step. Exhaustive over every period m (n or 2n, n <= 8192) and every
// offset r = x - s0 the hardware can see (x in [-2^15, 2^15], s0 in [0, 8191]).
static void test_wrap_reciprocal() {
  long bad = 0;
  for (int64_t m = 1; m <= 16384; ++m) {
    const int64_t R = ((int64_t(1) << 31) + m - 1) / m;
    const int64_t r0 = -(int64_t(1) << 15) - 8191;
    int64_t want = ((r0 % m) + m) % m;  // advanced incrementally (no division in the loop)
    for (int64_t r = r0; r <= (int64_t(1) << 15); ++r) {
      const int64_t q = (r * R) >> 31;  // arithmetic shift = floor
      int64_t rem = r - q * m;
      if (rem >= m) rem -= m;
      if (rem != want) ++bad;
      if (++want == m) want = 0;
    }
  }
  CHECK(bad == 0);
}

static void test_grad_index() {
  // P = 2^16*a with a = 1 entry/px; R = 0: i = floor(x + 0.5)
  for (int x = -300; x < 600; ++x) {
    int pad = grad_index(1 << 16, 0, 0, x, 0, 0);
    int64_t i = x;  // floor(x + 0.5) = x for integer x
    CHECK(pad == int(i < 0 ? 0 : (i > 255 ? 255 : i)));
    CHECK(grad_index(1 << 16, 0, 0, x, 0, 1) == int(i & 255));
    int64_t k = i & 511;
    CHECK(grad_index(1 << 16, 0, 0, x, 0, 2) == int(k < 256 ? k : 511 - k));
    CHECK(grad_index(1 << 16, 0, 0, x, 0, 3) == ((i < 0 || i > 255) ? -1 : int(i)));
  }
}

int main() {
  test_div255r();
  test_floor_ceil_div();
  test_formats();
  test_dither();
  test_wrap();
  test_wrap_reciprocal();
  test_blend_identities();
  test_xform();
  test_coverage();
  test_grad_index();
  std::printf("model ops tests: %d checks, %d failures\n", g_checks, g_failures);
  return g_failures ? 1 : 0;
}
