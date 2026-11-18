// SPDX-License-Identifier: Apache-2.0
// G2D golden model: path rasterizer in the same structure as the RTL (rendering.md §8; arch.md §6.3).
//
// The result equals the per-row formula of ops.hpp coverage_row (exact integer arithmetic either way);
// this class reproduces the hardware's order of operations so a mismatch can be traced to one step:
//   load:  clamp (§8.1), scale to 1/32 px, orient, drop edges that cannot cross a sample of the clipped
//          rectangle, per-edge steps S*dx = qs*dy + rs (sample rows, S = 2 with AA / 32 without) and
//          32*dx = qr*dy + rr (pixel rows), floor division;
//   bands: kBandRows pixel rows; the edges overlapping the band are active; for each, one floor division
//          gives the crossing state at the band's first sample row Y0:
//          N0 = Xa*dy + (Y0 - Ya)*dx, xt = ceil(N0/dy), r = xt*dy - N0 (0 <= r < dy)
//   rows:  per segment of kSegW pixels, every active edge steps through the sample rows of the pixel row
//          from (xt, r): event p = (xt - xoff_j + 31) >> 5 when Ya <= Y_j < Yb; step xt += qs + (rs > r),
//          r += (rs > r ? dy : 0) - rs. Events left of the segment go to its first column (base winding),
//          events at or right of its end are dropped. After the row, the stored state takes one row step
//          with (qr, rr).
//   sweep: 16 (or 1) running mod-256 sums along the segment, inside test (§8.3), cov (§8.4).
#pragma once

#include <cstdint>
#include <vector>

#include "g2d/ops.hpp"

namespace g2d {

class PathRaster {
 public:
  static constexpr int kBandRows = 16;
  static constexpr int kSegW = 256;

  struct LoadedEdge {
    int64_t xa, ya, yb, dx, dy;
    int dir;
    int64_t qs, rs, qr, rr;
    int64_t xt, r;   // crossing state at the current pixel row's first sample row (active edges)
  };

  // edges: clamped s.4 edges (rendering.md §8.1); rect: clipped draw rectangle (non-empty).
  PathRaster(const std::vector<Edge>& edges, bool aa, bool even_odd, int rx0, int ry0, int rx1, int ry1);

  // Coverage of the next pixel row (rows ry0 .. ry1-1 in order), rx1 - rx0 entries.
  void next_row(uint8_t* cov);

  const std::vector<LoadedEdge>& loaded() const { return edges_; }

 private:
  void start_band();

  bool aa_, even_odd_;
  int rx0_, ry0_, rx1_, ry1_;
  int row_;          // next pixel row
  int band_end_;     // first row after the current band
  std::vector<LoadedEdge> edges_;
  std::vector<size_t> active_;
};

// Floor division with 0 <= r < d (d > 0), as g2d_sdiv.
inline void floor_divmod(int64_t n, int64_t d, int64_t& q, int64_t& r) {
  q = floor_div(n, d);
  r = n - q * d;
}

}  // namespace g2d
