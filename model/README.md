# G2D golden model

Bit-accurate C++17 model of `spec/rendering.md` and `spec/commands.md`, plus pybind11 bindings. The
shared C99 driver geometry it is tested with lives in `sw/lib/geom`. The model is the reference for
every RTL test (bit-exact), and later the software fallback renderer (Phase 6).

## Build and use

```
source scripts/env.sh
make model          # CMake build in build/model + C/C++ unit tests (ctest)
make test           # everything, including the Python cross-checks below
```

Python (with `build/model`, `dv/common` and `scripts` on `sys.path`; `conftest.py` does this for pytest):

```python
import g2d_model as gm
from g2d_cmd import CmdBuf, FMT
mem = gm.Memory(); gpu = gm.Gpu(mem)
cb = CmdBuf().target(0x1000_0000, 64, 64, FMT["BGRA8888"]).clip(0, 0, 64, 64)
cb.state_raw("PAINT_COLOR", 0xFF0000FF).draw_rect(0, 0, 64, 64).fence(1)
data = cb.to_bytes(); mem.write(0x10_0000, data); gpu.kick(0x10_0000, len(data))
```

Replay a stream from files: `scripts/g2d_replay.py manifest.json` (format in the script header).

## Structure

| File | Content |
|---|---|
| `include/g2d/ops.hpp`, `src/ops.cpp` | Pure stage functions, one per rendering.md section |
| `include/g2d/gpu.hpp`, `src/gpu.cpp` | Sparse `Memory`; `Gpu` command executor (decode, §13 validation, per-pixel §4 pipeline) |
| `bindings/g2d_model.cpp` | Python module `g2d_model` (stage functions, vectorised `*_many`, Memory, Gpu, geometry) |
| `../sw/lib/geom/g2d_geom.[ch]` | Driver geometry §9–10: path decode/flatten/clip/quantize, blit setup, gradient coefficients and ramps |
| `tests/test_ops.cpp`, `tests/test_geom.c` | C/C++ unit tests (ctest) |
| `tests/test_*.py` | Python cross-checks (below) |
| `tests/refs/` | Committed reference vectors and images (regenerate: `G2D_UPDATE_REFS=1 make test`) |

The state register layout comes from the generated `g2d_regs.h` (single source: `spec/regs.yaml`).

## Verification approach

1. **Independent reference** (`dv/common/refimpl.py`), written from the spec text, not from this code.
   It differs in structure: numpy vector arithmetic, a *literal* per-sample crossing test instead of the
   model's prefix-sum rasterizer, and a slow per-pixel pipeline. Model and reference must agree
   bit-exactly.
2. **Float reference** (`dv/common/floatref.py`): supersampled float geometry, float bilinear, float
   gradient coordinate. It checks driver + model against the tolerances of rendering.md §12.
3. **Committed references** (`tests/refs`) freeze behaviour and give the RTL block tests their vectors.
4. **Mutation check** (Phase 1): three non-equivalent model bugs (bilinear clamp off-by-one, a blend
   saturation order, crossing rounding) were each caught by the tests above.

## Traceability: spec feature → model → tests

| rendering.md | Model | Reference | Tests |
|---|---|---|---|
| §2 helpers | `div255r`, `mul8`, `lerp8`, `floor_div` | `refimpl.div255r` | `test_ops.cpp` (exhaustive), `test_stages.py::test_div255r_exhaustive` |
| §3 formats (12 codes) | `fmt_bytes`, `expand`, `compress` | `refimpl.expand/compress` | `test_stages.py::test_formats` (exhaustive 8/16-bit, 2^18 random 32-bit), round trips in `test_ops.cpp` |
| §4 pipeline order | `Gpu::draw` | `refimpl.ref_draw` | `test_executor.py` random scenes (400 per run) |
| §5.2 texel steps (opaque, colour key, premultiply) | `Gpu::source_texel`, `color_key`, `premultiply` | same names | `test_stages.py::test_color_key`, `test_coverage_blend_alpha_modes_premul_multiply`, scenes |
| §5.3/5.5 image multiply, global alpha | `multiply_color`, `apply_global_alpha` | same | as above + scenes |
| §5.6 blend 0–15 | `blend` | `refimpl.blend` (row-by-row) | `test_stages.py::test_blend` (2^19 random + all channel pairs × 24 alpha combos per mode) |
| §6 clipping | `Gpu::clip_rect`, rect ∩ clip | `ref_draw` | scenes (random scissor incl. empty/oversized), `test_state_persists...` |
| §7.1 transform (affine/perspective) | `xform_map` (128-bit divide) | `refimpl.xform_map` (big ints) | `test_stages.py::test_xform_map` (40 000 incl. full-range), `test_ops.cpp` |
| §7.3 point/bilinear | `Gpu::draw`, `bilinear` | `refimpl.bilinear` | `test_stages.py::test_bilinear`, scenes, `test_vs_float.py::test_blits` |
| §7.4 source coverage test | `Gpu::draw` | `ref_draw` | scenes, `test_vs_float.py::test_blits` (outside pixels untouched) |
| §8 path coverage, both rules, AA/no-AA, clamp, mod-256 | `coverage_row`, `clamp_coord` | `refimpl.pixel_coverage` (literal) | `test_stages.py::test_coverage_row_vs_literal`, `test_ops.cpp::test_coverage`, path scenes, `test_vs_float.py` |
| §9.2 path decode/flatten/clip/quantize/bbox | `g2d_geom_path_edges` | `floatref` | `test_geom.c`, `test_vs_float.py` (circles, rounded rect, star, ellipse via matrix, random polygons) |
| §9.3 blit setup | `g2d_geom_blit_setup` | numpy inverse | `test_geom.c`, `test_vs_float.py::test_blits` |
| §10 gradient function, spreads | `grad_index` | `refimpl.grad_index` | `test_stages.py::test_grad_index`, `test_vs_float.py::test_gradient_*` |
| §10.2–10.3 coefficients and ramps | `g2d_geom_grad_coeffs`, `ramp_legacy`, `ramp_ext` | float | `test_geom.c`, `test_vs_float.py` |
| §11 clear | RECT + SOLID + NONE | `ref_draw` | scenes, `test_replay.py` |
| §13 validation/errors | `Gpu::validate_draw`, `Gpu::kick` | — (directed expectations) | `test_executor.py` directed tests (decode ×8, BAD_STATE ×15, path rules, ADDR_RANGE order, window granularity, wrap, halt, soft reset, fence, state masking) |

## Known limitations

- Speed (v1.7, `scripts/bench_model.py`, 720p ARGB8888, this machine): fills 17–27 Mpx/s, blits 4.4 (perspective
  bilinear) to 8.7 Mpx/s (identity), AA paths 15–18 Mpx/s; v1.6b was 6–10, 2.0–3.6 and 6–7. The time is now spread
  over the per-pixel operations (expand, blend, compress, wrap, memory access); per-format row loops would be the
  next step (docs/OPEN_QUESTIONS.md Q-PERF-1).
- AXI response errors are not modelled (rendering.md §13.3); the testbench predicts them.
