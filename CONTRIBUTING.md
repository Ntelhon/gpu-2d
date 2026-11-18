# Contributing to G2D

Thank you for your interest in G2D. This guide describes how changes are proposed, developed, verified and
reviewed. The complete engineering rules are in [docs/PROJECT_RULES.md](docs/PROJECT_RULES.md); this document is the
summary a contributor needs.

## Ways to contribute

- **Bug reports**: open an issue with the version or commit, the tool versions (`make check-tools`), the command
  that fails and its output. For rendering differences, include the draw call or command stream and the expected and
  actual images (`scripts/g2d_replay.py` replays a recorded stream).
- **Feature requests**: open an issue that describes the use case and the VGLite API calls involved. API coverage is
  tracked in `spec/api-subset.md`.
- **Pull requests**: fixes, tests, documentation and features. For larger changes, open an issue first so that the
  specification change can be agreed before implementation.
- **Security issues**: do not open a public issue; see [SECURITY.md](SECURITY.md).

## Development setup

Tool versions are listed in [docs/TOOLS.md](docs/TOOLS.md). The minimum for RTL and model work is Verilator 5.036 or
later, Python 3.12 or later with cocotb 2.x, cocotbext-axi, pytest, PyYAML, pybind11, numpy and Pillow, CMake and a
C/C++ compiler (GCC 13).

```sh
source scripts/env.sh   # tool environment
make check-tools        # verify versions
make regs-check lint model test
```

The FPGA and ASIC flows need additional tools; see [docs/FPGA.md](docs/FPGA.md) and [docs/ASIC.md](docs/ASIC.md).

## Development flow

### Order of work

Every functional change follows the same order, and is complete only when the test passes and the specification
matches the implementation:

1. **Specification**: `spec/` (rendering semantics in `spec/rendering.md`, commands in `spec/commands.md`,
   architecture in `spec/arch.md`).
2. **Golden model**: `model/` (bit-accurate C++).
3. **RTL**: `rtl/`.
4. **Testbench**: `dv/` (cocotb), self-checking against the golden model.
5. **Regression**: `make test`, and `make regress` for larger changes.
6. **Documentation**: user-facing documents and `docs/STATUS.md`.

### Register map and generated files

`spec/regs.yaml` is the single source of truth for the register map. `make regs` regenerates the CSR RTL and
packages, the C header, `docs/regs.md`, the bus wrappers and the packaging files (`g2d.core`, `Bender.yml`,
`ipxact/`). Generated files are committed and marked as generated; never edit them by hand. CI runs
`make regs-check` and fails if a committed generated file is out of date.

### Branches and commits

- Branch from `main`; one branch per change or release (`fix-...`, `feat-...`, `vX.Y-...`).
- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `test:`, `docs:`, `chore:`,
  `refactor:`. Keep commits small; every commit must build and pass the existing tests.
- History on `main` is never rewritten. Releases are tagged on `main` (`vX.Y`).

### Pull requests

A pull request should:

- describe the change and the reason for it, and reference the issue it resolves;
- list the specification sections, tests and documents that changed;
- pass CI (`regs-check`, lint, golden model, RTL benches) and, for RTL changes, `make test` locally;
- update `CHANGELOG.md` under "Unreleased" for user-visible changes.

## Coding rules

### SystemVerilog

- Synthesizable SystemVerilog, parameterized, Verilator `-Wall` lint clean (`make lint`), no latches.
- Reset: active-low, asynchronous assert / synchronous de-assert:
  `always_ff @(posedge clk_i or negedge rst_ni)`.
- Port suffixes `_i` / `_o`, active-low `_ni`; registered signals `_q`.
- Clock-domain crossings only through the CDC blocks in `rtl/cdc/`.
- Do not apply a bitwise NOT to a narrow signal inside an index or part-select (for example `mem[~sel]`): the
  sv2v conversion used by the ASIC flow widens the operand before the NOT. Assign the inverted value to a signal of
  the right width and index with that signal. `dv/tests/test_sv2v_equiv.py` checks the converted design.
- Memories through the wrappers `g2d_ram_1r1w` / `g2d_ram_sp`, so that FPGA and ASIC variants (`rtl/ram/`) can
  replace them.

### C / C++ / Python

- Driver: C99, no dependency on `libm`; hardware-visible memory only through the HAL.
- Golden model: C++17, deterministic integer arithmetic where bit-exactness is specified.
- Python: standard library plus the packages in `docs/TOOLS.md`.

### Tests

- Every test is self-checking: bit-exact against the golden model where the specification says so, otherwise with an
  explicit tolerance that is written in the specification. Visual inspection is not a test.
- New features need functional coverage points (`dv/common/g2d_coverage.py`) and, for RTL, assertions where an
  interface rule applies (`dv/sva/`).
- Limit parallel jobs to 8 (`JOBS`, `scripts/regress.py`).

### Licence headers

Every source file starts with its SPDX licence identifier:

| Files | First line |
|---|---|
| SystemVerilog, timing constraints (`.sv`, `.svh`, `.sdc`, `.xdc`) | `// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1` |
| Everything else (C, C++, Python, shell, Tcl, CMake, Makefile) | `SPDX-License-Identifier: Apache-2.0` in the language's comment style |

`python3 scripts/spdx.py --apply` adds missing identifiers and `--check` verifies them (part of the test suite).
Third-party material keeps its own licence and is listed in [THIRD_PARTY.md](THIRD_PARTY.md).

### Sources and provenance

G2D implements the public VGLite API only. Do not contribute code, register maps or text taken from vendor GPU RTL,
vendor driver sources (including the VGLite driver bundled with LVGL) or licence-restricted vendor documentation.
New third-party material needs a compatible licence and an entry in `THIRD_PARTY.md`.

Do not commit build outputs, waveforms, large binaries, tool licences or machine-specific paths.

## Licensing of contributions

By submitting a contribution you agree that it is licensed under the licence of the files it changes or adds
(Solderpad Hardware License v2.1 for hardware descriptions, Apache License 2.0 for everything else, as described in
[LICENSE](LICENSE)), in line with section 5 of the Apache License 2.0, and that you have the right to submit it.
