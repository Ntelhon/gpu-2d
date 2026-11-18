// SPDX-License-Identifier: Apache-2.0
// G2D golden model: path rasterizer in the RTL's structure (see raster.hpp).
#include "g2d/raster.hpp"

#include <algorithm>

namespace g2d {

PathRaster::PathRaster(const std::vector<Edge>& edges, bool aa, bool even_odd, int rx0, int ry0, int rx1, int ry1)
    : aa_(aa), even_odd_(even_odd), rx0_(rx0), ry0_(ry0), rx1_(rx1), ry1_(ry1), row_(ry0), band_end_(ry0) {
  const int64_t s = aa ? 2 : 32;
  for (const Edge& e : edges) {
    const int64_t x0 = 2 * int64_t(e.x0), y0 = 2 * int64_t(e.y0), x1 = 2 * int64_t(e.x1), y1 = 2 * int64_t(e.y1);
    if (y0 == y1) continue;
    const bool down = y1 > y0;
    LoadedEdge le{};
    le.xa = down ? x0 : x1;
    le.ya = down ? y0 : y1;
    const int64_t xb = down ? x1 : x0;
    le.yb = down ? y1 : y0;
    // drop: no sample row inside the rectangle, or strictly right of every sample
    if (le.yb <= 32 * int64_t(ry0) || le.ya >= 32 * int64_t(ry1)) continue;
    if (std::min(le.xa, xb) > 32 * int64_t(rx1) - 1) continue;
    le.dx = xb - le.xa;
    le.dy = le.yb - le.ya;
    le.dir = down ? 1 : -1;
    floor_divmod(s * le.dx, le.dy, le.qs, le.rs);
    floor_divmod(32 * le.dx, le.dy, le.qr, le.rr);
    edges_.push_back(le);
  }
}

void PathRaster::start_band() {
  band_end_ = std::min(row_ + kBandRows, ry1_);
  const int64_t ylo = 32 * int64_t(row_), yhi = 32 * int64_t(band_end_);
  const int64_t y0 = 32 * int64_t(row_) + (aa_ ? 1 : 16);
  active_.clear();
  for (size_t i = 0; i < edges_.size(); ++i) {
    LoadedEdge& e = edges_[i];
    if (!(e.ya < yhi && e.yb > ylo)) continue;
    active_.push_back(i);
    // xt = ceil(N0/dy) = -floor(-N0/dy), r = xt*dy - N0
    const int64_t n0 = e.xa * e.dy + (y0 - e.ya) * e.dx;
    int64_t q, rem;
    floor_divmod(-n0, e.dy, q, rem);
    e.xt = -q;
    e.r = rem;
  }
}

namespace {

// One crossing step: (xt, r) at row Y -> at Y + step, with step*dx = q*dy + rr.
inline void step(int64_t& xt, int64_t& r, int64_t q, int64_t rr, int64_t dy) {
  if (rr > r) {
    xt += q + 1;
    r += dy - rr;
  } else {
    xt += q;
    r -= rr;
  }
}

}  // namespace

void PathRaster::next_row(uint8_t* cov) {
  if (row_ >= band_end_) start_band();
  const int py = row_;
  const int nsamp = aa_ ? 16 : 1;
  for (int sx0 = rx0_; sx0 < rx1_; sx0 += kSegW) {
    const int sx1 = std::min(sx0 + kSegW, rx1_);
    const int w = sx1 - sx0;
    // accumulator: delta[j][x], mod 256
    std::vector<uint8_t> acc(size_t(nsamp) * size_t(w), 0);
    for (size_t idx : active_) {
      const LoadedEdge& e = edges_[idx];
      int64_t xt = e.xt, r = e.r;
      for (int j = 0; j < nsamp; ++j) {
        const int64_t y = aa_ ? 32 * int64_t(py) + 2 * j + 1 : 32 * int64_t(py) + 16;
        const int64_t xoff = aa_ ? 2 * int64_t(kSampleQ[j]) + 1 : 16;
        if (e.ya <= y && y < e.yb) {
          const int64_t p = (xt - xoff + 31) >> 5;   // first pixel whose sample is at or right of the edge
          if (p < sx1) {
            const size_t col = p <= sx0 ? 0 : size_t(p - sx0);
            acc[size_t(j) * size_t(w) + col] = uint8_t(acc[size_t(j) * size_t(w) + col] + uint8_t(e.dir));
          }
        }
        if (j + 1 < nsamp) step(xt, r, e.qs, e.rs, e.dy);
      }
    }
    // sweep
    std::vector<uint8_t> wnd(size_t(nsamp), 0);
    for (int x = 0; x < w; ++x) {
      int n = 0;
      for (int j = 0; j < nsamp; ++j) {
        wnd[size_t(j)] = uint8_t(wnd[size_t(j)] + acc[size_t(j) * size_t(w) + size_t(x)]);
        const int8_t w8 = static_cast<int8_t>(wnd[size_t(j)]);
        n += (even_odd_ ? (w8 & 1) != 0 : w8 != 0) ? 1 : 0;
      }
      cov[sx0 - rx0_ + x] = aa_ ? uint8_t((n * 255 + 8) >> 4) : uint8_t(n ? 255 : 0);
    }
  }
  // the stored state moves to the next pixel row
  for (size_t idx : active_) {
    LoadedEdge& e = edges_[idx];
    step(e.xt, e.r, e.qr, e.rr, e.dy);
  }
  ++row_;
}

}  // namespace g2d
