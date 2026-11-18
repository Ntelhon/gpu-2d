# G2D — 2D / 2.5D Vector Graphics GPU IP

[![ci](https://github.com/Ntelhon/GPU/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Ntelhon/GPU/actions/workflows/ci.yml)
![Hardware licence: SHL-2.1](https://img.shields.io/badge/hardware-SHL--2.1-blue)
![Software licence: Apache-2.0](https://img.shields.io/badge/software-Apache--2.0-blue)

G2D is a synthesizable 2D and 2.5D graphics processor IP in SystemVerilog, built for embedded user interfaces. It
implements the public **VGLite API** (`vg_lite.h`) and is delivered with a bit-accurate C++ golden model, a
self-checking verification environment, drivers for bare-metal, FreeRTOS and Linux, an FPGA prototype and an ASIC
reference layout.

## Contents

- [Features](#features)
- [Status](#status)
- [Repository layout](#repository-layout)
- [Getting started](#getting-started)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [Licensing](#licensing)

## Features

**Rendering**
- Rectangle fills and clears; image blits with a full 3 x 3 transform (affine and perspective) and point,
  bilinear or 3 x 3 Gaussian sampling.
- Anti-aliased vector paths (non-zero and even-odd fill rules, 16 samples per pixel) with solid, linear-gradient,
  radial-gradient (with focal point) and image paint; strokes and dashes in the driver.
- Blending: Porter-Duff, LVGL, OpenVG and SVG 2 blend families; A8 mask layers, colour key, colour matrix, gamma,
  dithering.
- Formats: ARGB8888, RGB565 and the other 32 / 24 / 16 / 8-bit layouts of VGLite, sub-byte alpha and luminance,
  indexed colour with a CLUT, YUV sources (NV12, NV16, NV24, YV12, YUY2 and variants). No 3D.
- Bit-exact results: every operation is defined in `spec/rendering.md` and checked against the golden model.

**Integration**
- Configuration slave: AXI4-Lite, or through generated wrappers AXI4, APB, AHB-Lite, OBI, PULP `reg_intf`,
  Wishbone or TileLink-UL.
- Memory: one AXI4 master, data width 64 or 128 bits, programmable cache / protection / QoS attributes and a
  bandwidth throttle.
- Level or pulse interrupt; optional clock-domain crossings on the configuration and memory ports.
- Delivered as a FuseSoC core, a Bender package and an IP-XACT (IEEE 1685-2014) description, with an SDC template and
  memory wrappers for FPGA (Xilinx) and ASIC (generic and IHP SG13G2 SRAM macros).

**Software**
- VGLite driver in C99, thread-safe, with HALs for the golden model (software rendering), bare metal, FreeRTOS and
  Linux (kernel driver with devicetree binding and a user-space HAL).
- Demonstrated with LVGL 9.6 and a DOOM port.

## Status

| Area | State |
|---|---|
| Release | v2.1 (`main`). Release history: [CHANGELOG.md](CHANGELOG.md) |
| Verification | Simulation with Verilator and cocotb: unit, block, top-level and system tests, bit-exact against the golden model, SystemVerilog assertions, functional and code coverage. CI on every push |
| FPGA | Runs on a Digilent Genesys2 (Kintex-7 XC7K325T) inside the PULP Cheshire SoC (CVA6 host) at 50 MHz; open board issues in `docs/STATUS.md` ("Known bugs") |
| ASIC | Reference layout on the IHP SG13G2 130 nm open PDK: 47.8 mm², about 1 M gate equivalents plus 131 SRAM macros, 50 MHz typical / 31 MHz slow corner. A demonstration layout with documented defects (hold at SRAM inputs, no DRC / LVS signoff), not a tape-out candidate: `docs/ASIC.md` |
| Silicon | None |

The 600 MHz clock figure in the specifications is a design target for an advanced process node, not a measured
result. Every open item and its outcome is listed in `docs/CLOSURE_V2.md`.

## Repository layout

| Directory | Contents |
|---|---|
| `spec/` | Specifications: architecture, bit-exact rendering rules, command format, register map (`regs.yaml`, the single source for the generated RTL, C header and register documentation), API subset, verification plan |
| `model/` | Golden model (C++17) and Python bindings |
| `rtl/` | The IP (`rtl/top/g2d_top.sv`), configuration-bus adapters (`rtl/adapt`), generated bus wrappers (`rtl/wrap`), memory variants (`rtl/ram`) |
| `dv/` | cocotb testbenches, SVA checkers, functional coverage, AXI memory models |
| `sw/` | VGLite driver, HALs, geometry library, Linux driver, LVGL and DOOM demonstrations |
| `fpga/` | Cheshire SoC integration, Genesys2 build and board scripts, SoC simulation |
| `syn/` | Timing-constraints template, ASIC flow configuration (IHP SG13G2) |
| `ipxact/` | IP-XACT component description (generated) |
| `scripts/` | Generators, regression runner, coverage, ASIC and image tools |
| `docs/` | User and integration guides, status, plans and reports ([index](docs/README.md)) |

## Getting started

Required tools and versions are listed in `docs/TOOLS.md` (Verilator 5.036 or later, cocotb 2.x, cocotbext-axi,
pybind11, CMake, GCC).

```sh
source scripts/env.sh   # tool environment (see docs/TOOLS.md)
make check-tools        # verify the tools on PATH
make lint               # Verilator -Wall lint of every unit and parameter variant
make model              # golden model, Python module, C / C++ unit tests
make test               # per-commit suite: unit, block, top-level and system tests against the golden model
make regress            # seeded full-length regression with merged functional coverage
```

The FPGA flow (`make fpga-setup`, `make fpga`, `make fpga-sim`) needs Cheshire, Bender, a RISC-V GCC toolchain and
Vivado: `docs/FPGA.md`. The ASIC flow (`scripts/asic_ihp.py`) needs LibreLane and the IHP Open PDK: `docs/ASIC.md`.

## Documentation

| Document | Purpose |
|---|---|
| [docs/INTEGRATION.md](docs/INTEGRATION.md) | Integrating the IP into an SoC: ports, parameters, packaging, constraints, memories |
| [docs/PROGRAMMING.md](docs/PROGRAMMING.md) | Programming manual: driver, HAL, command buffers, interrupts |
| [docs/regs.md](docs/regs.md) | Register reference (generated) |
| [spec/](spec/) | Architecture, rendering semantics and command format |
| [docs/FPGA.md](docs/FPGA.md), [docs/ASIC.md](docs/ASIC.md) | Implementation results |
| [docs/STATUS.md](docs/STATUS.md) | Project status, known issues, decision log |
| [docs/README.md](docs/README.md) | Index of all documents |

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) for the development flow, coding rules
and test requirements, and [SECURITY.md](SECURITY.md) for reporting security issues.

## Licensing

- Hardware descriptions (SystemVerilog, timing constraints): **Solderpad Hardware License v2.1**
  (SPDX `Apache-2.0 WITH SHL-2.1`).
- All other original work (software, golden model, scripts, tests, documentation): **Apache License 2.0**
  (SPDX `Apache-2.0`).
- The Linux kernel driver and devicetree binding are dual-licensed for kernel use; third-party material keeps its own
  licence (`vg_lite.h` MIT, DOOM port GPL-2.0-or-later, `axi_vga` fork SHL-0.51).

Every source file carries its SPDX identifier. Details: [LICENSE](LICENSE), [LICENSES/](LICENSES/) and
[THIRD_PARTY.md](THIRD_PARTY.md).

G2D is an independent implementation of the public VGLite API. It is not affiliated with or endorsed by the owner of
the VGLite API; all product names belong to their respective owners.
