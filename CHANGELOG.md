# Changelog

All notable changes to G2D are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
releases are tagged on `main`. Detailed results, test reports and decisions per release are in `docs/STATUS.md` and
`docs/PHASES.md`.

## [Unreleased]

### Changed
- SPDX identifier of hardware descriptions corrected from `SHL-2.1` to `Apache-2.0 WITH SHL-2.1` (the SPDX licence
  list carries the Solderpad Hardware License v2.1 as an exception to Apache-2.0); `scripts/spdx.py --check` now
  rejects a hardware file with another identifier. The licence itself is unchanged.
- CI actions moved to their Node.js 24 releases (`actions/checkout@v5`, `actions/cache@v5`,
  `actions/setup-python@v6`).
- Repository documentation revised: README, LICENSE, THIRD_PARTY; new CONTRIBUTING, SECURITY, CHANGELOG, document
  index (`docs/README.md`) and GitHub issue / pull-request templates.

### Added
- `LICENSES/SHL-0.51.txt` (licence of the `axi_vga` fork and the Cheshire patch).

## [2.1] - 2026-11-16

### Fixed
- `g2d_raster`: indexing with `~cur_q` was converted wrongly by sv2v 0.0.13.1 (the operand is widened before the
  NOT), so the synthesized netlist lost path draws. The inverted parity is now a 1-bit signal (`nxt_par`).

### Added
- `dv/tests/test_sv2v_equiv.py`: the rasterizer unit bench and a top-level subset on the sv2v output, in `make test`.
- Gate-level simulation of the IHP netlist runs the bench in black-box mode.

## [2.0] - 2026-11-16

### Added
- Driver: HAL interface v2 (lock hooks, interrupt-driven wait, 64-bit GPU addresses, timer), memory attribute and
  QoS API, faster stroke geometry (unit-circle tables, bit-exact with the previous output).
- FreeRTOS HAL with host and SoC-simulation tests.
- Linux kernel driver, uapi header, devicetree binding and user-space HAL.
- ASIC reference layout on IHP SG13G2 130 nm (LibreLane), signoff STA per corner, gate-level simulation
  (`docs/ASIC.md`, with its known defects).
- Programming manual (`docs/PROGRAMMING.md`) and closure of all open items (`docs/CLOSURE_V2.md`).

## [1.9] - 2026-11-12

### Added
- Configuration-bus adapters and generated top-level wrappers: AXI4, APB, AHB-Lite, OBI, PULP `reg_intf`,
  Wishbone, TileLink-UL.
- Memory attributes (AxCACHE / AxPROT / AxQOS) and a bandwidth throttle; interrupt level / pulse option.
- Memory wrappers for FPGA and ASIC, SDC template, FuseSoC core, Bender package, IP-XACT description, integration
  guide (`docs/INTEGRATION.md`).

## [1.8] - 2026-11-12

### Changed
- Board-readiness work on the Cheshire / Genesys2 platform: VGA scan-out fork with real bursts and debug counters,
  audit findings (`docs/AUDIT_2026-11.md`), SoC-simulation coverage of the board scenarios.

## [1.7] - 2026-10-27

### Changed
- Timing: G2D out-of-context on the Kintex-7 at 100 MHz met (fmax about 114 MHz) through added pipeline stages and
  register slices.
- Area: `FEATURES` parameter to remove optional units; a trimmed Cheshire with the full G2D fits the XC7K325T.
- Performance: more outstanding source-cache fills, edge-list setup overlap in the rasterizer, faster golden model.

## [1.6] - 2026-10-26 (including 1.6b)

### Added
- Mask layers, `vg_lite_scissor_rects` with several rectangles, OpenVG / SVG 2 / Porter-Duff blend families,
  stencil and recolour image modes, target mirroring, `vg_lite_blit2`.
- 3 x 3 Gaussian filter, YUV sources (NV12 / NV16 / NV24 / YV12 / YV16 / YV24 / YUY2 and alpha variants), colour
  unit (gamma, pixel matrix, colour transform), OpenVG sRGB / linear formats, 24-bit sources in hardware.
- Driver robustness: quiesce on error, sticky fault, per-buffer target list, fault-injection tests.

## [1.5] - 2026-10-24

### Added
- Image paint on vector paths; radial gradients with a focal point.

## [1.4] - 2026-10-24

### Added
- 4444 / 5551 / 2222 formats in every channel order, sub-byte alpha and luminance, indexed colour with a CLUT,
  dithering, exact wrap modes with border colour, gradient spread FILL, transparency mode.

## [1.3] - 2026-10-20

### Added
- Driver: elliptical arcs, path break, projective path matrices, dashed strokes, command-buffer and memory-pool
  configuration, frame delimiter.

## [1.2] - 2026-10-19

### Added
- Board readiness: DRAM latency model in the SoC simulation, latency probe, trimmed Cheshire variant, VGA output test
  with bit-exact capture.

## [1.1] - 2026-10-18

### Added
- Optional clock-domain crossings on the configuration and memory ports, with formal proof of the asynchronous FIFO.

## [1.0] - 2026-10-18

### Added
- First release: register map and generator, golden model, rectangle fill, transformed blits, blending, anti-aliased
  paths and gradients, VGLite driver with software and hardware backends, LVGL demonstrations, verification
  environment (cocotb, SVA, coverage, regression) and the Cheshire / Genesys2 FPGA integration.
