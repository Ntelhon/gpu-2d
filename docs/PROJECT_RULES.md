# Project rules

Engineering rules for G2D, a 2D/2.5D GPU IP that implements the public VGLite API (`vg_lite.h`).

Independent implementation: no vendor RTL, vendor driver source or licence-restricted vendor documentation is
copied or read. Only the public API surface is used; the HAL, command format and hardware are original work of this
project (spec/vglite-reference.md lists the sources used and not used).

## Fixed project decisions
- Compatibility: API level only. Own backend, no register-level compatibility with any vendor GPU.
- Config port: AXI4-Lite slave, 32-bit. CSR core is bus-agnostic (APB wrapper possible later).
- Memory port: one AXI4 master, `DATA_W` parameter (64 or 128, default 64), single shared datapath.
- Targets: ASIC (primary); FPGA prototype on Digilent Genesys2 (XC7K325T, 1 GB DDR3) with the Cheshire SoC
  (PULP, CVA6) as host.
- Performance v1: 1280x720 @ 60 fps UI workloads, ARGB8888 and RGB565 framebuffers.
- Scope v1: 2D and 2.5D (3x3 matrix with perspective for blits). No 3D.
- Verification: cocotb + Verilator, cocotbext-axi.
- RTL: synthesizable SystemVerilog, Verilator-lint clean, CDC-tool-friendly style, no latches, parameterized.
  Reset: active-low, async assert / sync de-assert (`always_ff @(posedge clk_i or negedge rst_ni)`).
- Clocks: single clock for v1 (FPGA at Cheshire's 50 MHz SoC clock); separate core / AXI-memory / config clocks
  with CDC inside the IP (spec/arch.md §2).
- ASIC clock target 600 MHz.
- Golden model: bit-accurate C++, Python bindings, also used as the software fallback renderer.

## Working rules
1. Git: branch per phase / version from `main`, small conventional commits (feat:, fix:, test:, docs:, chore:,
   refactor:). Every commit builds and passes existing tests. Phase end: all green, merge into `main`
   (no force push, no history rewrite), tag `<phase>-done`.
2. Never commit build outputs, waveforms, large binaries, tool licences or machine-specific tool paths. Generated
   source files (register RTL / C header, bus wrappers, packaging files) are committed, marked generated, rewritten
   by `make regs` and checked by `make regs-check` (CI); never edit them by hand.
3. Every phase ends with a phase report (done, test results, open issues, decisions, next plan) and an approval.
4. Phase start: a short plan, the files to create / change, all questions in one batch.
5. Single source of truth: the register map is `spec/regs.yaml`. `scripts/gen_regs.py` generates the RTL CSR block
   and package, the C header and the register docs. Never hand-edit generated files.
6. Feature work order: spec section -> golden model -> RTL -> testbench -> regression -> docs -> commit. Not done
   until the test passes and the spec matches the implementation.
7. Every test is self-checking against the golden model (bit-exact where specified, otherwise an explicit tolerance
   written in the spec). No "looks right" tests.
8. `docs/STATUS.md` is updated at each phase end (done, in progress, known bugs, decision log with dates).
9. No invented facts about tools, boards or APIs. Unverified items go into `docs/OPEN_QUESTIONS.md`.
10. At most 8 parallel jobs / cores (`scripts/regress.py` MAX_JOBS, Makefile `JOBS`, Vivado `-jobs`).
11. Reproducible: `make lint`, `make test`, `make regress`, `make model`, `make regs` work from a clean checkout.
    Tool versions are recorded in `docs/TOOLS.md`.
12. Licences (2026-11-12, expression corrected 2026-11-17): every own source file starts with its SPDX identifier:
    `Apache-2.0 WITH SHL-2.1` (Solderpad Hardware License v2.1) for SystemVerilog and timing constraints,
    `Apache-2.0` for everything else; third-party paths keep theirs (`LICENSE`). `scripts/spdx.py --apply` adds
    missing lines, `--check` (in the tests) also rejects a hardware file with another identifier.

## Environment
- Tools: `source scripts/env.sh` (wraps `~/tools/env.sh` and puts `~/tools/verilator` first; cocotb 2.x needs
  Verilator >= 5.036). `make check-tools` verifies them; the Makefile uses tools from PATH only.

## Layout
spec/ model/ rtl/ dv/ sw/ fpga/ syn/ docs/ scripts/ (spec/arch.md §12). Key specs: rendering.md (bit-exact
semantics), commands.md, regs.yaml, arch.md, api-subset.md, bandwidth.md, verification-plan.md.

## Public release
The repository moves to a new public repository after v2 (no history rewrite here). Left out of the public copy:
`CLAUDE.md` and `docs/HANDOFF.md` (machine-specific paths and session notes), and the gitignored local research
notes.
