// SPDX-License-Identifier: Apache-2.0
// pybind11 bindings for the G2D golden model and the shared driver geometry.
// Colours cross the boundary as packed ARGB32 integers (A<<24|R<<16|G<<8|B).
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <array>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

#include "g2d/gpu.hpp"
#include "g2d/raster.hpp"
#include "g2d/ops.hpp"
#include "g2d_geom.h"

namespace py = pybind11;
using namespace g2d;

namespace {

using U32Array = py::array_t<uint32_t, py::array::c_style | py::array::forcecast>;

U32Array blend_many(unsigned mode, const U32Array& s, const U32Array& d) {
  if (s.size() != d.size()) throw std::invalid_argument("size mismatch");
  U32Array out(s.size());
  auto ps = s.unchecked<1>(), pd = d.unchecked<1>();
  auto po = out.mutable_unchecked<1>();
  for (py::ssize_t i = 0; i < s.size(); ++i) po(i) = pack(blend(mode, unpack(ps(i)), unpack(pd(i))));
  return out;
}

U32Array expand_many(uint8_t fmt, const U32Array& raw) {
  U32Array out(raw.size());
  auto pr = raw.unchecked<1>();
  auto po = out.mutable_unchecked<1>();
  for (py::ssize_t i = 0; i < raw.size(); ++i) po(i) = pack(expand(fmt, pr(i)));
  return out;
}

U32Array compress_many(uint8_t fmt, const U32Array& argb) {
  U32Array out(argb.size());
  auto pa = argb.unchecked<1>();
  auto po = out.mutable_unchecked<1>();
  for (py::ssize_t i = 0; i < argb.size(); ++i) po(i) = compress(fmt, unpack(pa(i)));
  return out;
}

void to_c(const std::array<std::array<double, 3>, 3>& m, double out[3][3]) {
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j) out[i][j] = m[size_t(i)][size_t(j)];
}

int edge_sink(void* ctx, int32_t x0, int32_t y0, int32_t x1, int32_t y1) {
  static_cast<std::vector<std::tuple<int32_t, int32_t, int32_t, int32_t>>*>(ctx)->emplace_back(x0, y0, x1, y1);
  return 0;
}

}  // namespace

PYBIND11_MODULE(g2d_model, m) {
  m.doc() = "G2D bit-accurate golden model (spec/rendering.md) and driver geometry (sw/lib/geom)";

  // ---- stage functions ----
  m.def("div255r", &div255r);
  m.def("mul8", [](uint32_t a, uint32_t b) { return unsigned(mul8(a, b)); });
  m.def("fmt_bytes", &fmt_bytes);
  m.def("fmt_bits", &fmt_bits);
  m.def("fmt_target_ok", &fmt_target_ok);
  m.def("fmt_indexed", &fmt_indexed);
  m.def(
      "expand",
      [](uint8_t fmt, uint32_t raw, const std::vector<uint32_t>& clut) {
        std::vector<uint32_t> c(clut);
        c.resize(256, 0);
        return pack(expand(fmt, raw, c.data()));
      },
      py::arg("fmt"), py::arg("raw"), py::arg("clut") = std::vector<uint32_t>{});
  m.def("compress", [](uint8_t fmt, uint32_t argb) { return compress(fmt, unpack(argb)); });
  m.def("compress_dither", [](uint8_t fmt, uint32_t argb, int x, int y) { return compress_dither(fmt, unpack(argb), x, y); });
  m.def("dither_channel", &dither_channel);
  m.def("wrap_coord", [](unsigned mode, int64_t x, int s0, int s1) -> py::object {
    int o;
    if (!wrap_coord(mode, x, s0, s1, o)) return py::none();
    return py::int_(o);
  });
  m.def("expand_many", &expand_many);
  m.def("compress_many", &compress_many);
  m.def("blend", [](unsigned mode, uint32_t s, uint32_t d) { return pack(blend(mode, unpack(s), unpack(d))); });
  m.def("blend_many", &blend_many);
  m.def("blend_family", [](unsigned fam, unsigned mode, uint32_t func, uint32_t s, uint32_t d, uint32_t c) {
    return pack(blend_family(fam, mode, func, unpack(s), unpack(d), unpack(c)));
  });
  m.def("blend_family_ok", &blend_family_ok);
  m.def("color_apply", [](uint32_t c, bool en, unsigned gamma, std::vector<int32_t> m, std::vector<int32_t> b) {
    ColorUnit cu;                                                   // §5.2.1 (v1.6b); m row-major A, R, G, B
    cu.en = en;
    cu.gamma = gamma;
    for (int i = 0; i < 16; ++i) cu.m[i / 4][i % 4] = m.at(size_t(i));
    for (int i = 0; i < 4; ++i) cu.b[i] = b.at(size_t(i));
    return pack(color_apply(unpack(c), cu));
  });
  m.def("gamma_lin", &gamma_lin);
  m.def("gamma_srgb", &gamma_srgb);
  m.def("yuv_expand", [](uint32_t ayuv, bool bt709) { return pack(yuv_expand(ayuv, bt709)); });   // §3.7 (v1.6b)
  m.def("fmt_yuv", [](uint8_t f) { return fmt_yuv(f); });
  m.def("gaussian", [](std::vector<uint32_t> t, int32_t w0, int32_t w1, int32_t w2) {   // §7.3 (v1.6b), t[3 j + i]
    Argb a[9];
    for (int k = 0; k < 9; ++k) a[k] = unpack(t.at(size_t(k)));
    return pack(gaussian(a, w0, w1, w2));
  });
  m.def("blend_stencil", [](unsigned mode, uint32_t i, uint32_t p, uint32_t d) {   // §5.3.1: stencil source + blend
    uint8_t ac[3];
    const Argb s = stencil_source(unpack(i), unpack(p), ac);
    return pack(blend_stencil(mode, s, unpack(d), ac));
  });
  m.def("coverage_blend", [](uint32_t d, uint32_t b, uint8_t cov) { return pack(coverage_blend(unpack(d), unpack(b), cov)); });
  m.def("premultiply", [](uint32_t c) { return pack(premultiply(unpack(c))); });
  m.def("apply_global_alpha", [](uint32_t c, unsigned mode, uint8_t v) { return pack(apply_global_alpha(unpack(c), mode, v)); });
  m.def("multiply_color", [](uint32_t s, uint32_t p) { return pack(multiply_color(unpack(s), unpack(p))); });
  m.def(
      "color_key",
      [](uint32_t c, const std::vector<std::tuple<bool, uint32_t, uint32_t, uint8_t>>& keys) {
        // keys: up to 4 of (enable, low_rgb as 0xRRGGBB, high_rgb as 0xRRGGBB, alpha)
        ColorKey k[4];
        for (size_t i = 0; i < keys.size() && i < 4; ++i) {
          const auto& [en, lo, hi, a] = keys[i];
          k[i] = ColorKey{en, uint8_t(lo >> 16), uint8_t(lo >> 8), uint8_t(lo), uint8_t(hi >> 16), uint8_t(hi >> 8), uint8_t(hi), a};
        }
        return pack(color_key(unpack(c), k));
      },
      py::arg("c"), py::arg("keys"));
  m.def(
      "xform_map",
      [](const std::array<int32_t, 9>& xf, bool persp, int x, int y) -> py::object {
        Xform t{xf[0], xf[1], xf[2], xf[3], xf[4], xf[5], xf[6], xf[7], xf[8], persp};
        int64_t u, v;
        if (!xform_map(t, x, y, u, v)) return py::none();
        return py::make_tuple(u, v);
      });
  m.def("bilinear", [](const std::array<uint32_t, 4>& t, unsigned fx, unsigned fy) {
    const Argb tt[4] = {unpack(t[0]), unpack(t[1]), unpack(t[2]), unpack(t[3])};
    return pack(bilinear(tt, fx, fy));
  });
  m.def(
      "coverage_row",
      [](const std::vector<std::array<int32_t, 4>>& edges, int py, int x0, int x1, bool aa, bool even_odd) {
        std::vector<Edge> e;
        e.reserve(edges.size());
        for (const auto& t : edges) e.push_back(Edge{clamp_coord(t[0]), clamp_coord(t[1]), clamp_coord(t[2]), clamp_coord(t[3])});
        std::vector<uint8_t> cov(size_t(std::max(0, x1 - x0)));
        coverage_row(e, py, x0, x1, aa, even_odd, cov.data());
        return py::bytes(reinterpret_cast<const char*>(cov.data()), cov.size());
      });
  m.def(
      "path_raster",
      [](const std::vector<std::array<int32_t, 4>>& edges, const std::array<int, 4>& rect, bool aa, bool even_odd) {
        // Coverage of the clipped rectangle (x0, y0, x1, y1) by PathRaster (the RTL-structured
        // rasterizer), rows concatenated. Edges are raw s.4 and are clamped here (§8.1).
        std::vector<Edge> e;
        e.reserve(edges.size());
        for (const auto& t : edges) e.push_back(Edge{clamp_coord(t[0]), clamp_coord(t[1]), clamp_coord(t[2]), clamp_coord(t[3])});
        const int w = rect[2] - rect[0], h = rect[3] - rect[1];
        std::vector<uint8_t> out(size_t(std::max(0, w)) * size_t(std::max(0, h)));
        if (w > 0 && h > 0) {
          PathRaster pr(e, aa, even_odd, rect[0], rect[1], rect[2], rect[3]);
          for (int y = 0; y < h; ++y) pr.next_row(out.data() + size_t(y) * size_t(w));
        }
        return py::bytes(reinterpret_cast<const char*>(out.data()), out.size());
      });
  m.def(
      "path_loaded",
      [](const std::vector<std::array<int32_t, 4>>& edges, const std::array<int, 4>& rect, bool aa) {
        // Loaded edges (xa, ya, yb, dx, dy, dir, qs, rs, qr, rr) after clamp, orientation and drop.
        std::vector<Edge> e;
        for (const auto& t : edges) e.push_back(Edge{clamp_coord(t[0]), clamp_coord(t[1]), clamp_coord(t[2]), clamp_coord(t[3])});
        PathRaster pr(e, aa, false, rect[0], rect[1], rect[2], rect[3]);
        std::vector<std::array<int64_t, 10>> out;
        for (const auto& le : pr.loaded()) out.push_back({le.xa, le.ya, le.yb, le.dx, le.dy, le.dir, le.qs, le.rs, le.qr, le.rr});
        return out;
      });
  m.def("grad_index", &grad_index);
  m.def("isqrt64", &isqrt64);
  m.def("radial_index", [](const std::array<int32_t, 3>& p, const std::array<int32_t, 3>& q,
                           const std::array<int64_t, 3>& r, int x, int y, unsigned spread) {
    return radial_index(p.data(), q.data(), r.data(), x, y, spread);
  });
  m.attr("SAMPLE_Q") = std::vector<int>(kSampleQ, kSampleQ + 16);

  // ---- memory and executor ----
  py::class_<Memory>(m, "Memory")
      .def(py::init<>())
      .def("write", [](Memory& mem, uint32_t addr, py::bytes data) {
        std::string s = data;
        mem.write(addr, reinterpret_cast<const uint8_t*>(s.data()), s.size());
      })
      .def("read", [](const Memory& mem, uint32_t addr, size_t n) {
        std::string s(n, '\0');
        mem.read(addr, reinterpret_cast<uint8_t*>(&s[0]), n);
        return py::bytes(s);
      })
      .def("read_le", &Memory::read_le)
      .def("write_le", &Memory::write_le)
      .def("clear", &Memory::clear)
      .def("pages", &Memory::pages);

  py::class_<GpuStats>(m, "GpuStats")
      .def_readonly("draws", &GpuStats::draws)
      .def_readonly("pixels_out", &GpuStats::pixels_out)
      .def_readonly("commands", &GpuStats::commands);

  py::class_<Gpu>(m, "Gpu")
      .def(py::init<Memory&>(), py::keep_alive<1, 2>())
      .def("reset", &Gpu::reset)
      .def("soft_reset", &Gpu::soft_reset)
      .def("set_addr_win", &Gpu::set_addr_win)
      .def("set_features", &Gpu::set_features)
      .def("features", &Gpu::features)
      .def("kick", &Gpu::kick)
      .def("state", &Gpu::state)
      .def("clear_irq_fence", &Gpu::clear_irq_fence)
      .def_property_readonly("addr_win_lo", &Gpu::addr_win_lo)
      .def_property_readonly("addr_win_hi", &Gpu::addr_win_hi)
      .def_property_readonly("halted", &Gpu::halted)
      .def_property_readonly("fence_seq", &Gpu::fence_seq)
      .def_property_readonly("irq_fence", &Gpu::irq_fence)
      .def_property_readonly("err_status", &Gpu::err_status)
      .def_property_readonly("err_addr", &Gpu::err_addr)
      .def_property_readonly("err_cmd_addr", &Gpu::err_cmd_addr)
      .def_property_readonly("stats", &Gpu::stats);

  // ---- driver geometry (sw/lib/geom) ----
  m.attr("GEOM_OK") = G2D_GEOM_OK;
  m.attr("GEOM_EMPTY") = G2D_GEOM_EMPTY;
  m.attr("GEOM_INVALID") = G2D_GEOM_INVALID;
  m.attr("GEOM_NOT_SUPPORT") = G2D_GEOM_NOT_SUPPORT;
  m.def("rha", &g2d_rha);
  m.def(
      "path_edges",
      [](py::bytes data, int format, const std::array<std::array<double, 3>, 3>& mat) {
        std::string s = data;
        double mm[3][3];
        to_c(mat, mm);
        std::vector<std::tuple<int32_t, int32_t, int32_t, int32_t>> edges;
        g2d_rect_t bb;
        int rc = g2d_geom_path_edges(s.data(), uint32_t(s.size()), format, mm, edge_sink, &edges, &bb);
        return py::make_tuple(rc, edges, py::make_tuple(bb.x0, bb.y0, bb.x1, bb.y1));
      },
      "Returns (rc, [(x0,y0,x1,y1) s.4], (bx0,by0,bx1,by1)).");
  m.def(
      "stroke_edges",
      [](py::bytes data, int format, const std::array<std::array<double, 3>, 3>& mat, double width, double miter,
         int cap, int join, const std::vector<double>& dash, double dash_phase, bool dash_reset) {
        std::string s = data;
        double mm[3][3];
        to_c(mat, mm);
        std::vector<std::tuple<int32_t, int32_t, int32_t, int32_t>> edges;
        g2d_rect_t bb;
        g2d_stroke_t st{width, miter, cap, join, dash.empty() ? nullptr : dash.data(), uint32_t(dash.size()),
                        dash_phase, int(dash_reset)};
        int rc = g2d_geom_stroke_edges(s.data(), uint32_t(s.size()), format, mm, &st, edge_sink, &edges, &bb);
        return py::make_tuple(rc, edges, py::make_tuple(bb.x0, bb.y0, bb.x1, bb.y1));
      },
      py::arg("data"), py::arg("format"), py::arg("m"), py::arg("width"), py::arg("miter"), py::arg("cap"),
      py::arg("join"), py::arg("dash") = std::vector<double>(), py::arg("dash_phase") = 0.0, py::arg("dash_reset") = false,
      "Stroke outline edges (dashed if len(dash) >= 2; fill NON_ZERO). Returns (rc, edges, bbox).");
  m.def(
      "blit_setup",
      [](const std::array<std::array<double, 3>, 3>& mat, const std::array<int32_t, 4>& src,
         const std::array<int32_t, 4>& clip, bool full) {
        double mm[3][3];
        to_c(mat, mm);
        g2d_rect_t s{src[0], src[1], src[2], src[3]}, c{clip[0], clip[1], clip[2], clip[3]};
        g2d_blit_setup_t out{};
        int rc = full ? g2d_geom_blit_setup_full(mm, &s, &c, &out) : g2d_geom_blit_setup(mm, &s, &c, &out);
        std::vector<int32_t> xf(out.xf, out.xf + 9);
        return py::make_tuple(rc, xf, bool(out.persp), py::make_tuple(out.draw.x0, out.draw.y0, out.draw.x1, out.draw.y1));
      },
      py::arg("m"), py::arg("src"), py::arg("clip"), py::arg("full") = false,
      "Returns (rc, [XF_A..XF_I], persp, (x0,y0,x1,y1)); full: draw rectangle = clip (wrap modes).");
  m.def(
      "grad_coeffs",
      [](const std::array<std::array<double, 3>, 3>& gm, bool ext, double x0, double y0, double x1, double y1) {
        double mm[3][3];
        to_c(gm, mm);
        int32_t p = 0, q = 0;
        int64_t r = 0;
        int rc = g2d_geom_grad_coeffs(mm, ext, x0, y0, x1, y1, &p, &q, &r);
        return py::make_tuple(rc, p, q, r);
      },
      py::arg("gm"), py::arg("ext") = false, py::arg("x0") = 0.0, py::arg("y0") = 0.0, py::arg("x1") = 0.0,
      py::arg("y1") = 0.0);
  m.def(
      "radial_coeffs",
      [](const std::array<std::array<double, 3>, 3>& gm, double cx, double cy, double r, double fx, double fy) {
        double mm[3][3];
        to_c(gm, mm);
        g2d_geom_radial_t o{};
        int rc = g2d_geom_radial_coeffs(mm, cx, cy, r, fx, fy, &o);
        return py::make_tuple(rc, std::vector<int32_t>(o.p, o.p + 3), std::vector<int32_t>(o.q, o.q + 3),
                              std::vector<int64_t>(o.r, o.r + 3));
      },
      "Returns (rc, [P_T, P_U, P_V], [Q_T, Q_U, Q_V], [R_T, R_U, R_V]) (rendering.md §10.4).");
  m.def("ramp_legacy", [](const std::vector<uint32_t>& colors, const std::vector<uint32_t>& stops) {
    if (colors.size() != stops.size()) throw std::invalid_argument("colors/stops size mismatch");
    std::vector<uint32_t> ramp(256);
    int rc = g2d_geom_ramp_legacy(uint32_t(colors.size()), colors.data(), stops.data(), ramp.data());
    return py::make_tuple(rc, ramp);
  });
  m.def("ramp_ext", [](const std::vector<std::array<double, 5>>& stops, bool premul) {
    std::vector<g2d_ramp_stop_t> s;
    for (const auto& t : stops) s.push_back(g2d_ramp_stop_t{t[0], t[1], t[2], t[3], t[4]});
    std::vector<uint32_t> ramp(256);
    int rc = g2d_geom_ramp_ext(uint32_t(s.size()), s.data(), premul, ramp.data());
    return py::make_tuple(rc, ramp);
  });
}
