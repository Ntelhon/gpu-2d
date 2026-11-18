// SPDX-License-Identifier: Apache-2.0
// G2D golden model: memory and command executor.
#include "g2d/gpu.hpp"

#include <memory>

#include "g2d/raster.hpp"

#include <algorithm>
#include <cstring>

#include "g2d_regs.h"

namespace g2d {

// ---------------------------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------------------------

static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "Memory::read_le / write_le copy little-endian values");

namespace {
const std::array<uint8_t, Memory::kPage> kZeroPage{};
}  // namespace

// Bytes of the page at base for reading (sparse pages only; the window and identity mode are handled by the
// callers). v1.7: the last page is cached.
const uint8_t* Memory::rpage(uint32_t base) const {
  if (base != rc_base_) {
    auto it = pages_.find(base);
    rc_ptr_ = it == pages_.end() ? kZeroPage.data() : it->second->data();
    rc_base_ = base;
  }
  return rc_ptr_;
}

uint8_t* Memory::wpage(uint32_t base) {
  if (base != wc_base_) {
    auto& p = pages_[base];
    if (!p) {
      p = std::make_unique<Page>();
      p->fill(0);
      if (rc_base_ == base) rc_base_ = 1;   // the read entry pointed at the zero page
    }
    wc_ptr_ = p->data();
    wc_base_ = base;
  }
  return wc_ptr_;
}

uint8_t Memory::read8(uint32_t addr) const {
  if (hook_) touch(addr);
  if (identity_) return *reinterpret_cast<const volatile uint8_t*>(uintptr_t(addr));
  if (size_t(addr - win_base_) < win_size_) return win_ptr_[addr - win_base_];
  return rpage(addr & ~(kPage - 1))[addr & (kPage - 1)];
}

void Memory::write8(uint32_t addr, uint8_t v) {
  if (hook_) touch(addr);
  if (whook_) whook_(whook_ctx_, addr);
  if (identity_) {
    *reinterpret_cast<volatile uint8_t*>(uintptr_t(addr)) = v;
    return;
  }
  if (size_t(addr - win_base_) < win_size_) {
    win_ptr_[addr - win_base_] = v;
    return;
  }
  wpage(addr & ~(kPage - 1))[addr & (kPage - 1)] = v;
}

// v1.7 (Q-PERF-1): a value inside one sparse page, without hooks, in one access (little-endian host)
uint32_t Memory::read_le(uint32_t addr, unsigned n) const {
  const uint32_t off = addr & (kPage - 1);
  if (!hook_ && !identity_ && off + n <= kPage && size_t(addr - win_base_) >= win_size_ &&
      size_t(addr + n - 1 - win_base_) >= win_size_) {
    uint32_t v = 0;
    std::memcpy(&v, rpage(addr - off) + off, n);
    return v;
  }
  uint32_t v = 0;
  for (unsigned i = 0; i < n; ++i) v |= uint32_t(read8(addr + i)) << (8 * i);
  return v;
}

void Memory::write_le(uint32_t addr, unsigned n, uint32_t v) {
  const uint32_t off = addr & (kPage - 1);
  if (!hook_ && !whook_ && !identity_ && off + n <= kPage && size_t(addr - win_base_) >= win_size_ &&
      size_t(addr + n - 1 - win_base_) >= win_size_) {
    std::memcpy(wpage(addr - off) + off, &v, n);
    return;
  }
  for (unsigned i = 0; i < n; ++i) write8(addr + i, uint8_t(v >> (8 * i)));
}

void Memory::read(uint32_t addr, uint8_t* dst, size_t n) const {
  for (size_t i = 0; i < n; ++i) dst[i] = read8(addr + uint32_t(i));
}

void Memory::write(uint32_t addr, const uint8_t* src, size_t n) {
  for (size_t i = 0; i < n; ++i) write8(addr + uint32_t(i), src[i]);
}

std::vector<uint32_t> Memory::pages() const {
  std::vector<uint32_t> v;
  v.reserve(pages_.size());
  for (const auto& kv : pages_) v.push_back(kv.first);
  std::sort(v.begin(), v.end());
  return v;
}

// ---------------------------------------------------------------------------------------------
// Gpu
// ---------------------------------------------------------------------------------------------

namespace {

static_assert(G2D_ST_SIZE == 128, "state array size in gpu.hpp");
constexpr uint32_t kStateReset[G2D_ST_SIZE] = G2D_ST_RESET_INIT;
constexpr uint32_t kStateWmask[G2D_ST_SIZE] = G2D_ST_WMASK_INIT;

enum Opcode : uint32_t {
  kNop = G2D_OP_NOP, kLoadState = G2D_OP_LOAD_STATE, kDrawRect = G2D_OP_DRAW_RECT, kDrawPath = G2D_OP_DRAW_PATH,
  kFence = G2D_OP_FENCE,
};
enum PaintSrc : unsigned { kSolid = 0, kImage = 1, kGrad = 2, kRadial = 3 };

#define FGET(reg, field, v) G2D_FIELD_GET(G2D_ST_##reg##_##field, v)

inline int s16(uint32_t v) { return int(int16_t(uint16_t(v))); }

}  // namespace

Gpu::Gpu(Memory& mem) : mem_(mem) { reset(); }

void Gpu::reset() {
  soft_reset();
  win_lo_ = 0;
  win_hi_ = 0xFFFFFFFFu;
  stats_ = GpuStats{};
}

void Gpu::soft_reset() {
  std::copy(std::begin(kStateReset), std::end(kStateReset), state_.begin());
  halted_ = false;
  fence_seq_ = 0;
  irq_fence_ = false;
  err_status_ = err_addr_ = err_cmd_addr_ = 0;
  pending_.clear();
}

void Gpu::set_addr_win(uint32_t lo, uint32_t hi) {
  win_lo_ = lo & G2D_CSR_ADDR_WIN_LO_WMASK;
  win_hi_ = (hi & G2D_CSR_ADDR_WIN_HI_WMASK) | G2D_CSR_ADDR_WIN_HI_ONES_MASK;
}

void Gpu::error(uint32_t bit, uint32_t err_addr, uint32_t cmd_addr) {
  if (err_status_ == 0) {  // first error wins (arch.md §8)
    err_addr_ = err_addr;
    err_cmd_addr_ = cmd_addr;
  }
  err_status_ |= bit;
  halted_ = true;
}

void Gpu::load_state(unsigned idx, uint32_t v) { state_[idx] = v & kStateWmask[idx]; }

bool Gpu::extent_ok(uint32_t start, uint64_t len) const {
  const uint64_t end = uint64_t(start) + len - 1;
  return end <= 0xFFFFFFFFull && start >= win_lo_ && end <= win_hi_;
}

Gpu::Buffer Gpu::target() const {
  Buffer b;
  b.addr = state_[G2D_ST_TGT_ADDR];
  b.stride = FGET(TGT_STRIDE, STRIDE, state_[G2D_ST_TGT_STRIDE]);
  b.w = int(FGET(TGT_SIZE, W, state_[G2D_ST_TGT_SIZE]));
  b.h = int(FGET(TGT_SIZE, H, state_[G2D_ST_TGT_SIZE]));
  b.fmt = uint8_t(FGET(TGT_FMT, FMT, state_[G2D_ST_TGT_FMT]));
  b.bytes = fmt_bytes(b.fmt);
  b.bits = fmt_bits(b.fmt);
  return b;
}

Gpu::Buffer Gpu::source() const {
  Buffer b;
  b.addr = state_[G2D_ST_SRC_ADDR];
  b.stride = FGET(SRC_STRIDE, STRIDE, state_[G2D_ST_SRC_STRIDE]);
  b.w = int(FGET(SRC_SIZE, W, state_[G2D_ST_SRC_SIZE]));
  b.h = int(FGET(SRC_SIZE, H, state_[G2D_ST_SRC_SIZE]));
  b.fmt = uint8_t(FGET(SRC_FMT, FMT, state_[G2D_ST_SRC_FMT]));
  b.bytes = fmt_bytes(b.fmt);
  b.bits = fmt_bits(b.fmt);
  return b;
}

Gpu::Rect Gpu::clip_rect() const {
  const Buffer t = target();
  Rect r;
  r.x0 = std::max(0, int(FGET(CLIP_MIN, X0, state_[G2D_ST_CLIP_MIN])));
  r.y0 = std::max(0, int(FGET(CLIP_MIN, Y0, state_[G2D_ST_CLIP_MIN])));
  r.x1 = std::min(t.w, int(FGET(CLIP_MAX, X1, state_[G2D_ST_CLIP_MAX])));
  r.y1 = std::min(t.h, int(FGET(CLIP_MAX, Y1, state_[G2D_ST_CLIP_MAX])));
  return r;
}

namespace {

// Bytes per row of w pixels: ceil(w * bits / 8) (§13.1, §13.2).
uint64_t row_bytes(int w, unsigned bits) { return (uint64_t(w) * bits + 7) / 8; }

// §3.7 YUV planes (v1.6b): subsampling shifts of the chroma planes, bytes per chroma sample of plane 1.
struct YuvLayout {
  unsigned cls, xs, ys, n1;
  bool alpha;
};
YuvLayout yuv_layout(uint8_t fmt) {
  const unsigned cls = fmt & 0xF, sub = (fmt >> 4) & 3;
  return YuvLayout{cls, sub <= 1 ? 1u : 0u, sub == 0 ? 1u : 0u, cls == kXNv ? 2u : 1u, bool(fmt & 0x40)};
}
// Row bytes of the Y (or packed) plane: YUY2 4 * ceil(W / 2), else W.
uint64_t yuv_row0(uint8_t fmt, int w) { return (fmt & 0xF) == kXYuy2 ? 4 * ((uint64_t(w) + 1) / 2) : uint64_t(w); }

// §13.1 buffer rules shared by target and source. Sub-byte sources: any byte address and stride.
bool buffer_ok(uint8_t fmt, int w, int h, uint32_t addr, uint32_t stride) {
  const unsigned bits = fmt_bits(fmt);
  if (bits == 0) return false;
  if (w < 1 || w > 8192 || h < 1 || h > 8192) return false;
  if (fmt_yuv(fmt))                                                    // §3.7: YUY2 pairs 4-byte aligned
    return stride >= yuv_row0(fmt, w) && ((fmt & 0xF) != kXYuy2 || (addr % 4 == 0 && stride % 4 == 0));
  const unsigned al = (bits >= 8 && bits != 24) ? bits / 8 : 1;   // sub-byte and 24-bit (v1.6b): any byte
  if (addr % al != 0 || stride % al != 0 || stride < row_bytes(w, bits)) return false;
  return true;
}

uint64_t buffer_len(uint32_t stride, int w, int h, unsigned bits) {
  return uint64_t(stride) * uint64_t(h - 1) + row_bytes(w, bits);
}

// §13.2 extents of the YUV planes 1 and 2 (v1.6b); n = 0: the plane is not used.
struct PlaneExtent {
  uint32_t addr;
  uint64_t len, row;
  bool used;
};
void yuv_planes(uint8_t fmt, int w, int h, uint32_t a1, uint32_t s1, uint32_t a2, uint32_t s2, PlaneExtent p[2]) {
  const YuvLayout l = yuv_layout(fmt);
  const uint64_t cw = (uint64_t(w) + (1u << l.xs) - 1) >> l.xs, ch = (uint64_t(h) + (1u << l.ys) - 1) >> l.ys;
  p[0] = PlaneExtent{a1, 0, 0, l.cls != kXYuy2};
  if (p[0].used) p[0].row = l.n1 * cw, p[0].len = uint64_t(s1) * (ch - 1) + p[0].row;
  p[1] = PlaneExtent{a2, 0, 0, l.cls == kXYv || l.alpha};
  if (l.cls == kXYv) p[1].row = cw, p[1].len = uint64_t(s2) * (ch - 1) + cw;
  else if (l.alpha) p[1].row = uint64_t(w), p[1].len = uint64_t(s2) * uint64_t(h - 1) + uint64_t(w);
}

}  // namespace

bool Gpu::validate_draw(bool is_path, uint32_t edge_addr, uint32_t edge_count, uint32_t cmd_addr) {
  const Buffer t = target();
  const uint32_t alpha = state_[G2D_ST_ALPHA_CTRL];
  const uint32_t paint = state_[G2D_ST_PAINT_CTRL];
  const unsigned src = FGET(PAINT_CTRL, SRC, paint);

  // §13.1 BAD_STATE
  bool ok = fmt_target_ok(t.fmt) && buffer_ok(t.fmt, t.w, t.h, t.addr, t.stride);
  ok = ok && FGET(ALPHA_CTRL, SRC_MODE, alpha) <= 2 && FGET(ALPHA_CTRL, DST_MODE, alpha) <= 2;
  // §5.3.1 stencil (v1.6): image paint, family 0, source global alpha NORMAL, no MULTIPLY
  if (FGET(PAINT_CTRL, STENCIL, paint))
    ok = ok && src == kImage && FGET(BLEND, FAMILY, state_[G2D_ST_BLEND]) == 0 && FGET(ALPHA_CTRL, SRC_MODE, alpha) == 0 &&
         !FGET(PAINT_CTRL, IMG_MUL, paint);
  ok = ok && blend_family_ok(FGET(BLEND, FAMILY, state_[G2D_ST_BLEND]), FGET(BLEND, MODE, state_[G2D_ST_BLEND]),
                             state_[G2D_ST_BLEND_FUNC]);   // §13.1, §5.7 (v1.6)
  // §13.1 (v1.7): units left out of the build
  {
    const uint32_t cc = state_[G2D_ST_COLOR_CTRL];
    const bool img = src == kImage;
    if (!(features_ & 1)) ok = ok && FGET(BLEND, FAMILY, state_[G2D_ST_BLEND]) == 0;
    if (!(features_ & 2)) ok = ok && !(img && FGET(PAINT_CTRL, GAUSS, paint));
    if (!(features_ & 4)) ok = ok && !(img && fmt_yuv(uint8_t(FGET(SRC_FMT, FMT, state_[G2D_ST_SRC_FMT]))));
    if (!(features_ & 8)) ok = ok && !(img && (FGET(COLOR_CTRL, EN, cc) || FGET(COLOR_CTRL, GAMMA, cc)));
    if (!(features_ & 16)) ok = ok && src != kRadial;
    if (!(features_ & 32)) ok = ok && !FGET(MASK_CTRL, EN, state_[G2D_ST_MASK_CTRL]);
  }
  if (ok && src == kImage) {
    const Buffer s = source();
    const uint32_t mn = state_[G2D_ST_SRC_RECT_MIN], mx = state_[G2D_ST_SRC_RECT_MAX];
    PlaneExtent pl[2];
    yuv_planes(s.fmt, s.w, s.h, state_[G2D_ST_SRC_PLANE1_ADDR], FGET(SRC_PLANE_STRIDE, S1, state_[G2D_ST_SRC_PLANE_STRIDE]),
               state_[G2D_ST_SRC_PLANE2_ADDR], FGET(SRC_PLANE_STRIDE, S2, state_[G2D_ST_SRC_PLANE_STRIDE]), pl);
    const uint32_t pstr[2] = {FGET(SRC_PLANE_STRIDE, S1, state_[G2D_ST_SRC_PLANE_STRIDE]),
                              FGET(SRC_PLANE_STRIDE, S2, state_[G2D_ST_SRC_PLANE_STRIDE])};
    if (fmt_yuv(s.fmt)) {   // §13.1 (v1.6b): plane strides >= their row bytes; U V plane 2-byte aligned
      for (int k = 0; k < 2; ++k) ok = ok && (!pl[k].used || pstr[k] >= pl[k].row);
      if ((s.fmt & 0xF) == kXNv) ok = ok && pl[0].addr % 2 == 0 && pstr[0] % 2 == 0;
    }
    ok = ok && !(fmt_yuv(s.fmt) && FGET(PAINT_CTRL, GAUSS, paint));
    ok = ok && FGET(COLOR_CTRL, GAMMA, state_[G2D_ST_COLOR_CTRL]) <= 2;   // §5.2.1 (v1.6b)
    ok = ok && !FGET(SRC_FMT, YUV_FULL, state_[G2D_ST_SRC_FMT]);         // §3.7: reserved full range
    ok = ok && buffer_ok(s.fmt, s.w, s.h, s.addr, s.stride) &&
         FGET(SRC_RECT_MIN, X0, mn) < FGET(SRC_RECT_MAX, X1, mx) && FGET(SRC_RECT_MAX, X1, mx) <= uint32_t(s.w) &&
         FGET(SRC_RECT_MIN, Y0, mn) < FGET(SRC_RECT_MAX, Y1, mx) && FGET(SRC_RECT_MAX, Y1, mx) <= uint32_t(s.h) &&
         FGET(PAINT_CTRL, WRAP, paint) <= 4 && (!fmt_indexed(s.fmt) || state_[G2D_ST_CLUT_ADDR] % 4 == 0);
  }
  if (ok && (src == kGrad || src == kRadial)) {
    ok = FGET(PAINT_CTRL, SPREAD, paint) <= 3 && state_[G2D_ST_GRAD_RAMP_ADDR] % 4 == 0;
  }
  if (ok && is_path && edge_count > 0) ok = edge_addr % 16 == 0;
  // §13.1 mask layer (v1.6): size 1..8192, stride >= W, < 2^16 (A8, any address)
  const bool mask_on = FGET(MASK_CTRL, EN, state_[G2D_ST_MASK_CTRL]);
  const uint32_t mw = FGET(MASK_SIZE, W, state_[G2D_ST_MASK_SIZE]), mh = FGET(MASK_SIZE, H, state_[G2D_ST_MASK_SIZE]);
  const uint32_t mstride = FGET(MASK_STRIDE, STRIDE, state_[G2D_ST_MASK_STRIDE]);
  if (ok && mask_on) ok = mw >= 1 && mw <= 8192 && mh >= 1 && mh <= 8192 && mstride >= mw;
  if (!ok) {
    error(kErrBadState, 0, cmd_addr);
    return false;
  }

  // §13.2 ADDR_RANGE, in table order
  if (!extent_ok(t.addr, buffer_len(t.stride, t.w, t.h, t.bits))) {
    error(kErrAddrRange, t.addr, cmd_addr);
    return false;
  }
  if (src == kImage) {
    const Buffer s = source();
    const uint64_t slen = fmt_yuv(s.fmt) ? uint64_t(s.stride) * uint64_t(s.h - 1) + yuv_row0(s.fmt, s.w)
                                         : buffer_len(s.stride, s.w, s.h, s.bits);
    if (!extent_ok(s.addr, slen)) {
      error(kErrAddrRange, s.addr, cmd_addr);
      return false;
    }
    if (fmt_yuv(s.fmt)) {   // §13.2 (v1.6b): plane 1, plane 2
      PlaneExtent pl[2];
      yuv_planes(s.fmt, s.w, s.h, state_[G2D_ST_SRC_PLANE1_ADDR], FGET(SRC_PLANE_STRIDE, S1, state_[G2D_ST_SRC_PLANE_STRIDE]),
                 state_[G2D_ST_SRC_PLANE2_ADDR], FGET(SRC_PLANE_STRIDE, S2, state_[G2D_ST_SRC_PLANE_STRIDE]), pl);
      for (const PlaneExtent& e : pl)
        if (e.used && !extent_ok(e.addr, e.len)) {
          error(kErrAddrRange, e.addr, cmd_addr);
          return false;
        }
    }
    if (fmt_indexed(s.fmt) && !extent_ok(state_[G2D_ST_CLUT_ADDR], 1024)) {
      error(kErrAddrRange, state_[G2D_ST_CLUT_ADDR], cmd_addr);
      return false;
    }
  }
  if ((src == kGrad || src == kRadial) && !extent_ok(state_[G2D_ST_GRAD_RAMP_ADDR], 1024)) {
    error(kErrAddrRange, state_[G2D_ST_GRAD_RAMP_ADDR], cmd_addr);
    return false;
  }
  if (is_path && edge_count > 0 && !extent_ok(edge_addr, uint64_t(edge_count) * 16)) {
    error(kErrAddrRange, edge_addr, cmd_addr);
    return false;
  }
  if (mask_on && !extent_ok(state_[G2D_ST_MASK_ADDR], uint64_t(mstride) * (mh - 1) + mw)) {
    error(kErrAddrRange, state_[G2D_ST_MASK_ADDR], cmd_addr);
    return false;
  }
  return true;
}

Argb Gpu::fetch_texel(const Buffer& s, int x, int y) const {
  const uint32_t row = s.addr + uint32_t(y) * s.stride;
  if (fmt_yuv(s.fmt)) {   // §3.7 (v1.6b): assemble {A, Y, U, V} from the planes, then convert
    const YuvLayout l = yuv_layout(s.fmt);
    const uint32_t a1 = state_[G2D_ST_SRC_PLANE1_ADDR], a2 = state_[G2D_ST_SRC_PLANE2_ADDR];
    const uint32_t s1 = FGET(SRC_PLANE_STRIDE, S1, state_[G2D_ST_SRC_PLANE_STRIDE]);
    const uint32_t s2 = FGET(SRC_PLANE_STRIDE, S2, state_[G2D_ST_SRC_PLANE_STRIDE]);
    const uint32_t cx = uint32_t(x) >> l.xs, cy = uint32_t(y) >> l.ys;
    uint32_t Y, U, V, A = 255;
    if (l.cls == kXYuy2) {
      const uint32_t w = mem_.read_le(row + 4 * (uint32_t(x) >> 1), 4);
      Y = (x & 1) ? (w >> 16) & 0xFF : w & 0xFF;
      U = (w >> 8) & 0xFF;
      V = w >> 24;
    } else {
      Y = mem_.read8(row + uint32_t(x));
      if (l.cls == kXNv) {
        U = mem_.read8(a1 + cy * s1 + 2 * cx);
        V = mem_.read8(a1 + cy * s1 + 2 * cx + 1);
      } else {
        U = mem_.read8(a1 + cy * s1 + cx);
        V = mem_.read8(a2 + cy * s2 + cx);
      }
    }
    if (l.alpha) A = mem_.read8(a2 + uint32_t(y) * s2 + uint32_t(x));
    if (FGET(SRC_FMT, UV_SWAP, state_[G2D_ST_SRC_FMT])) std::swap(U, V);
    return yuv_expand((A << 24) | (Y << 16) | (U << 8) | V, FGET(SRC_FMT, YUV_709, state_[G2D_ST_SRC_FMT]));
  }
  if (s.bits >= 8) return expand(s.fmt, mem_.read_le(row + uint32_t(x) * s.bytes, s.bytes), clut_.data());
  // §3.5 sub-byte: slot j of byte x*bits/8, low bits first (SUB_ORDER 0) or high bits first (1)
  const unsigned per = 8 / s.bits, j = unsigned(x) % per;
  const uint8_t byte = mem_.read8(row + uint32_t(x) / per);
  const bool hi_first = FGET(SRC_FMT, SUB_ORDER, state_[G2D_ST_SRC_FMT]);
  const unsigned sh = hi_first ? 8 - s.bits * (j + 1) : s.bits * j;
  return expand(s.fmt, (byte >> sh) & ((1u << s.bits) - 1u), clut_.data());
}

void Gpu::load_src_cfg() {
  src_cfg_.force_opaque = FGET(SRC_FMT, FORCE_OPAQUE, state_[G2D_ST_SRC_FMT]);
  src_cfg_.premul = FGET(PAINT_CTRL, SRC_PREMUL, state_[G2D_ST_PAINT_CTRL]);
  for (int k = 0; k < 4; ++k) {
    ColorKey& key = src_cfg_.keys[k];
    const uint32_t lo = state_[G2D_ST_CKEY0_LO + 2 * k], hi = state_[G2D_ST_CKEY0_HI + 2 * k];
    key.en = FGET(CKEY0_LO, EN, lo);
    key.lr = uint8_t(FGET(CKEY0_LO, R, lo));
    key.lg = uint8_t(FGET(CKEY0_LO, G, lo));
    key.lb = uint8_t(FGET(CKEY0_LO, B, lo));
    key.hr = uint8_t(FGET(CKEY0_HI, R, hi));
    key.hg = uint8_t(FGET(CKEY0_HI, G, hi));
    key.hb = uint8_t(FGET(CKEY0_HI, B, hi));
    key.alpha = uint8_t(FGET(CKEY0_HI, ALPHA, hi));
  }
}

Argb Gpu::source_texel(const Buffer& s, int x, int y) const {
  Argb c = fetch_texel(s, x, y);
  if (src_cfg_.force_opaque) c.a = 255;
  c = color_key(c, src_cfg_.keys);
  if (src_cfg_.premul) c = premultiply(c);
  return c;
}

void Gpu::draw(const Rect& r, const std::vector<Edge>* edges, bool aa, bool even_odd) {
  const Buffer t = target();
  const uint32_t paint = state_[G2D_ST_PAINT_CTRL], alpha = state_[G2D_ST_ALPHA_CTRL];
  const unsigned src = FGET(PAINT_CTRL, SRC, paint);
  const unsigned mode = FGET(BLEND, MODE, state_[G2D_ST_BLEND]);
  const unsigned family = FGET(BLEND, FAMILY, state_[G2D_ST_BLEND]);
  const bool stencil = src == kImage && FGET(PAINT_CTRL, STENCIL, paint);
  uint8_t ac[3] = {0, 0, 0};
  const uint32_t bfunc = state_[G2D_ST_BLEND_FUNC];
  const Argb paint_color = unpack(state_[G2D_ST_PAINT_COLOR]);
  const unsigned src_amode = FGET(ALPHA_CTRL, SRC_MODE, alpha), dst_amode = FGET(ALPHA_CTRL, DST_MODE, alpha);
  const uint8_t src_aval = uint8_t(FGET(ALPHA_CTRL, SRC_VALUE, alpha));
  const uint8_t dst_aval = uint8_t(FGET(ALPHA_CTRL, DST_VALUE, alpha));

  // Image source parameters.
  const Buffer s = source();
  load_src_cfg();
  Xform xf;
  xf.a = int32_t(state_[G2D_ST_XF_A]);
  xf.b = int32_t(state_[G2D_ST_XF_B]);
  xf.c = int32_t(state_[G2D_ST_XF_C]);
  xf.d = int32_t(state_[G2D_ST_XF_D]);
  xf.e = int32_t(state_[G2D_ST_XF_E]);
  xf.f = int32_t(state_[G2D_ST_XF_F]);
  xf.g = int32_t(state_[G2D_ST_XF_G]);
  xf.h = int32_t(state_[G2D_ST_XF_H]);
  xf.i = int32_t(state_[G2D_ST_XF_I]);
  xf.persp = FGET(PAINT_CTRL, PERSP, paint);
  const bool bilin = FGET(PAINT_CTRL, FILTER, paint);
  const bool img_mul = FGET(PAINT_CTRL, IMG_MUL, paint);
  const int sx0 = int(FGET(SRC_RECT_MIN, X0, state_[G2D_ST_SRC_RECT_MIN]));
  const int sy0 = int(FGET(SRC_RECT_MIN, Y0, state_[G2D_ST_SRC_RECT_MIN]));
  const int sx1 = int(FGET(SRC_RECT_MAX, X1, state_[G2D_ST_SRC_RECT_MAX]));
  const int sy1 = int(FGET(SRC_RECT_MAX, Y1, state_[G2D_ST_SRC_RECT_MAX]));
  const unsigned wrap = FGET(PAINT_CTRL, WRAP, paint);
  // §7.5 COLOR with BORDER_SOLID: outside pixels get BORDER_COLOR as a plain colour, inside ones are sampled as NONE
  const bool border_solid = wrap == 1 && FGET(PAINT_CTRL, BORDER_SOLID, paint);
  const Argb border = unpack(state_[G2D_ST_BORDER_COLOR]);
  // §5.2 step 4 (v1.5, Q-API-19 b): the border texel is premultiplied like the image texels; BORDER_SOLID: as given
  const Argb border_tex = FGET(PAINT_CTRL, SRC_PREMUL, paint) ? premultiply(border) : border;
  const bool transparent = FGET(SRC_FMT, TRANSPARENT, state_[G2D_ST_SRC_FMT]);
  const bool dither = FGET(TGT_FMT, DITHER, state_[G2D_ST_TGT_FMT]);
  // Indexed sources: the CLUT is read before the first pixel and point-sampled only (§3.5).
  if (src == kImage && fmt_indexed(s.fmt))
    for (unsigned i = 0; i < 256; ++i) clut_[i] = mem_.read_le(state_[G2D_ST_CLUT_ADDR] + 4 * i, 4);
  const bool bilin_eff = bilin && !fmt_indexed(s.fmt);
  // §7.3 Gaussian (v1.6b), overrides FILTER; indexed sources stay point-sampled
  const bool gauss_eff = FGET(PAINT_CTRL, GAUSS, paint) && !fmt_indexed(s.fmt);
  // signed s3.12 weights (any values, the result saturates)
  const int32_t gw0 = int16_t(FGET(GAUSS_W0, W0, state_[G2D_ST_GAUSS_W0]));
  const int32_t gw1 = int16_t(FGET(GAUSS_W, W1, state_[G2D_ST_GAUSS_W])), gw2 = int16_t(FGET(GAUSS_W, W2, state_[G2D_ST_GAUSS_W]));
  // §5.2.1 colour unit (v1.6b), on the filtered image colour
  ColorUnit cu;
  cu.en = FGET(COLOR_CTRL, EN, state_[G2D_ST_COLOR_CTRL]);
  cu.gamma = FGET(COLOR_CTRL, GAMMA, state_[G2D_ST_COLOR_CTRL]);
  for (int o = 0; o < 4; ++o) {
    const uint32_t w0 = state_[G2D_ST_COLOR_M0 + 2 * o], w1 = state_[G2D_ST_COLOR_M0 + 2 * o + 1];
    cu.m[o][0] = int16_t(w0 & 0xFFFF);
    cu.m[o][1] = int16_t(w0 >> 16);
    cu.m[o][2] = int16_t(w1 & 0xFFFF);
    cu.m[o][3] = int16_t(w1 >> 16);
    cu.b[o] = int32_t(state_[G2D_ST_COLOR_B0 + o] << 12) >> 12;   // signed 20 bit
  }

  // Gradient parameters.
  const int32_t gp = int32_t(state_[G2D_ST_GRAD_P]), gq = int32_t(state_[G2D_ST_GRAD_Q]);
  const uint64_t r48 = (uint64_t(state_[G2D_ST_GRAD_R_HI] & 0xFFFFu) << 32) | state_[G2D_ST_GRAD_R];
  const int64_t gr = int64_t(r48 << 16) >> 16;
  const unsigned spread = FGET(PAINT_CTRL, SPREAD, paint);
  // §10.4 radial: T (the linear coefficients), U, V
  auto s48 = [&](unsigned lo, unsigned hi) {
    return int64_t(((uint64_t(state_[hi] & 0xFFFFu) << 32) | state_[lo]) << 16) >> 16;
  };
  const int32_t rp[3] = {gp, int32_t(state_[G2D_ST_GRAD_UP]), int32_t(state_[G2D_ST_GRAD_VP])};
  const int32_t rq[3] = {gq, int32_t(state_[G2D_ST_GRAD_UQ]), int32_t(state_[G2D_ST_GRAD_VQ])};
  const int64_t rr[3] = {gr, s48(G2D_ST_GRAD_UR, G2D_ST_GRAD_UR_HI), s48(G2D_ST_GRAD_VR, G2D_ST_GRAD_VR_HI)};
  const uint32_t ramp = state_[G2D_ST_GRAD_RAMP_ADDR];
  // The whole ramp is read before the first pixel, as the hardware loads it (arch.md §6.4); the result is
  // the same either way (§13.3: a draw never writes over its own ramp).
  std::array<uint32_t, 256> ramp_words{};
  if (src == kGrad || src == kRadial)
    for (unsigned i = 0; i < 256; ++i) ramp_words[i] = mem_.read_le(ramp + 4 * i, 4);

  std::vector<uint8_t> cov_row(size_t(std::max(0, r.x1 - r.x0)), 255);
  const bool flip_y = FGET(TGT_FMT, FLIP_Y, state_[G2D_ST_TGT_FMT]);
  const bool reads_dst = blend_reads_dst(mode) || family != 0;
  pending_.reserve(size_t(std::max(0, r.x1 - r.x0)) * size_t(std::max(0, r.y1 - r.y0)));
  // §6.1 mask layer (v1.6): coverage x mask byte, 0 outside the layer
  const bool mask_on = FGET(MASK_CTRL, EN, state_[G2D_ST_MASK_CTRL]);
  const uint32_t maddr = state_[G2D_ST_MASK_ADDR];
  const int mw = int(FGET(MASK_SIZE, W, state_[G2D_ST_MASK_SIZE])), mh = int(FGET(MASK_SIZE, H, state_[G2D_ST_MASK_SIZE]));
  const uint32_t mstride = FGET(MASK_STRIDE, STRIDE, state_[G2D_ST_MASK_STRIDE]);
  const int mox = int(int16_t(FGET(MASK_ORIGIN, X, state_[G2D_ST_MASK_ORIGIN])));
  const int moy = int(int16_t(FGET(MASK_ORIGIN, Y, state_[G2D_ST_MASK_ORIGIN])));
  // Paths: the rasterizer in the RTL's structure (raster.hpp); equal to coverage_row() per row.
  std::unique_ptr<PathRaster> raster;
  if (edges) raster = std::make_unique<PathRaster>(*edges, aa, even_odd, r.x0, r.y0, r.x1, r.y1);
  for (int y = r.y0; y < r.y1; ++y) {
    if (raster) raster->next_row(cov_row.data());
    for (int x = r.x0; x < r.x1; ++x) {
      uint8_t cov = cov_row[size_t(x - r.x0)];
      if (cov == 0) continue;
      if (mask_on) {
        const int mx = x - mox, my = y - moy;
        const uint8_t m = (mx >= 0 && mx < mw && my >= 0 && my < mh) ? mem_.read8(maddr + uint32_t(my) * mstride + uint32_t(mx)) : 0;
        cov = mul8(cov, m);
        if (cov == 0) continue;
      }

      // §5.1 paint
      Argb S;
      if (src == kImage) {
        int64_t u, v;
        if (!xform_map(xf, x, y, u, v)) continue;
        const int64_t tx = u >> 16, ty = v >> 16;
        if (wrap == 0) {
          if (tx < sx0 || tx >= sx1 || ty < sy0 || ty >= sy1) continue;  // §7.4
        } else if (u < -(int64_t(1) << 31) || u >= (int64_t(1) << 31) || v < -(int64_t(1) << 31) ||
                   v >= (int64_t(1) << 31)) {
          continue;  // §7.4 with WRAP != NONE
        }
        // §7.5: texel (cx, cy) after the wrap of both coordinates; BORDER_COLOR under COLOR outside.
        const unsigned tw = border_solid ? 0u : wrap;
        auto texel = [&](int64_t cx, int64_t cy) {
          int wx, wy;
          const bool in_x = wrap_coord(tw, cx, sx0, sx1, wx), in_y = wrap_coord(tw, cy, sy0, sy1, wy);
          return (in_x && in_y) ? source_texel(s, wx, wy) : border_tex;
        };
        if (border_solid && (tx < sx0 || tx >= sx1 || ty < sy0 || ty >= sy1)) {
          S = border;  // plain colour: no filtering, TRANSPARENT or MULTIPLY
        } else {
          if (gauss_eff) {
            Argb tex[9];
            for (int j = 0; j < 3; ++j)
              for (int i = 0; i < 3; ++i) tex[3 * j + i] = texel(tx + i - 1, ty + j - 1);
            S = gaussian(tex, gw0, gw1, gw2);
          } else if (!bilin_eff) {
            S = texel(tx, ty);
          } else {
            const int64_t up = u - 32768, vp = v - 32768;
            const int64_t bx = up >> 16, by = vp >> 16;
            const unsigned fx = unsigned((up >> 8) & 0xFF), fy = unsigned((vp >> 8) & 0xFF);
            const Argb tex[4] = {texel(bx, by), texel(bx + 1, by), texel(bx, by + 1), texel(bx + 1, by + 1)};
            S = bilinear(tex, fx, fy);
          }
          if (transparent && S.a == 0) continue;  // §5.2 step 6
          S = color_apply(S, cu);                 // §5.2 step 7 (v1.6b)
          if (img_mul) S = multiply_color(S, paint_color);
        }
      } else if (src == kGrad || src == kRadial) {
        const int i = src == kGrad ? grad_index(gp, gq, gr, x, y, spread) : radial_index(rp, rq, rr, x, y, spread);
        S = i < 0 ? paint_color : unpack(ramp_words[size_t(i)]);  // FILL outside [0, 255]: paint colour
      } else {
        S = paint_color;
      }
      if (stencil) S = stencil_source(S, paint_color, ac);   // §5.3.1 (SRC_MODE NORMAL: no global alpha)
      S = apply_global_alpha(S, src_amode, src_aval);

      // §4 steps 3-6
      const int ty = flip_y ? t.h - 1 - y : y;   // §5.8 (v1.6)
      const uint32_t taddr = t.addr + uint32_t(ty) * t.stride + uint32_t(x) * t.bytes;
      Argb D;
      if (reads_dst || cov < 255) {
        D = apply_global_alpha(expand(t.fmt, mem_.read_le(taddr, t.bytes)), dst_amode, dst_aval);
      }
      const Argb B = family ? blend_family(family, mode, bfunc, S, D, paint_color)
                            : stencil ? blend_stencil(mode, S, D, ac) : blend(mode, S, D);
      const Argb O = coverage_blend(D, B, cov);
      pending_.push_back({taddr, {dither ? compress_dither(t.fmt, O, x, y) : compress(t.fmt, O), t.bytes}});
    }
  }
  for (const auto& w : pending_) mem_.write_le(w.first, w.second.second, w.second.first);
  stats_.pixels_out += pending_.size();
  pending_.clear();
  ++stats_.draws;
}

bool Gpu::kick(uint32_t addr, uint32_t size) {
  if (halted_) return false;
  if (size == 0 || size % 8 != 0) {
    error(kErrCmdDecode, 0, addr);
    return false;
  }
  if (!extent_ok(addr, size)) {
    error(kErrAddrRange, addr, addr);
    return false;
  }
  const uint32_t nwords = size / 4;
  auto word = [&](uint32_t i) { return mem_.read_le(addr + 4 * i, 4); };

  uint32_t pos = 0;
  while (pos < nwords) {
    const uint32_t hdr_addr = addr + 4 * pos;
    const uint32_t h = word(pos);
    const uint32_t op = h >> 24;
    uint32_t payload = 0;
    bool bad = false;
    switch (op) {
      case kNop:
        bad = ((h >> 16) & 0xFF) != 0;
        payload = h & 0xFFFF;
        break;
      case kLoadState: {
        const uint32_t count = (h >> 16) & 0xFF, idx = h & 0xFF;
        bad = ((h >> 8) & 0xFF) != 0 || count == 0 || idx + count > G2D_ST_SIZE;
        payload = count;
        break;
      }
      case kDrawRect:
        bad = (h & 0xFFFFFF) != 0;
        payload = 2;
        break;
      case kDrawPath:
        bad = (h & 0xFFFFFF) != 0;
        payload = 4;
        break;
      case kFence:
        bad = (h & 0xFFFFFE) != 0;
        payload = 1;
        break;
      default:
        bad = true;
    }
    if (!bad && pos + 1 + payload > nwords) bad = true;  // command crosses the buffer end
    if (!bad && op == kDrawPath && (word(pos + 4) >> 24) != 0) bad = true;
    if (bad) {
      error(kErrCmdDecode, 0, hdr_addr);
      return false;
    }
    ++stats_.commands;

    switch (op) {
      case kLoadState: {
        const uint32_t count = (h >> 16) & 0xFF, idx = h & 0xFF;
        for (uint32_t k = 0; k < count; ++k) load_state(idx + k, word(pos + 1 + k));
        break;
      }
      case kDrawRect: {
        const uint32_t p0 = word(pos + 1), p1 = word(pos + 2);
        if (!validate_draw(false, 0, 0, hdr_addr)) return false;
        const Rect c = clip_rect();
        Rect r{std::max(s16(p0), c.x0), std::max(s16(p0 >> 16), c.y0), std::min(s16(p1), c.x1),
               std::min(s16(p1 >> 16), c.y1)};
        if (!r.empty()) draw(r, nullptr, false, false);
        else ++stats_.draws;
        break;
      }
      case kDrawPath: {
        const uint32_t p0 = word(pos + 1), p1 = word(pos + 2), eaddr = word(pos + 3);
        const uint32_t ecount = word(pos + 4) & 0xFFFFFF;
        if (!validate_draw(true, eaddr, ecount, hdr_addr)) return false;
        const Rect c = clip_rect();
        Rect r{std::max(s16(p0), c.x0), std::max(s16(p0 >> 16), c.y0), std::min(s16(p1), c.x1),
               std::min(s16(p1 >> 16), c.y1)};
        std::vector<Edge> edges;
        edges.reserve(ecount);
        for (uint32_t k = 0; k < ecount; ++k) {
          const uint32_t ea = eaddr + 16 * k;
          edges.push_back(Edge{clamp_coord(int32_t(mem_.read_le(ea, 4))), clamp_coord(int32_t(mem_.read_le(ea + 4, 4))),
                               clamp_coord(int32_t(mem_.read_le(ea + 8, 4))), clamp_coord(int32_t(mem_.read_le(ea + 12, 4)))});
        }
        const uint32_t rast = state_[G2D_ST_RAST_CTRL];
        if (!r.empty() && ecount > 0) draw(r, &edges, FGET(RAST_CTRL, AA, rast), FGET(RAST_CTRL, FILL_RULE, rast));
        else ++stats_.draws;
        break;
      }
      case kFence:
        fence_seq_ = word(pos + 1);
        if (h & 1) irq_fence_ = true;
        break;
      default:
        break;  // NOP
    }
    pos += 1 + payload;
  }
  return true;
}

}  // namespace g2d
