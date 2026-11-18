# Tools

Setup: `source scripts/env.sh`. It sources `$G2D_TOOLS/env.sh` (default `~/tools`) and puts
`$G2D_TOOLS/verilator/bin` first in PATH. `make check-tools` verifies the critical versions.

Versions verified on the development host (2026-10-15, Ubuntu 24.04):

| Tool | Version | Used for | Source |
|---|---|---|---|
| Verilator | 5.052 (2026-09-05) | lint, simulation | built from source into `~/tools/verilator` (`git clone --branch v5.052 https://github.com/verilator/verilator && autoconf && ./configure --prefix=$HOME/tools/verilator && make -j && make install`). **Minimum 5.036** (cocotb 2.x). Distribution packages (e.g. Ubuntu 24.04's 5.020) are too old. Observed with 5.052 code coverage (Phase 5): a `case` item whose only statement is a `return` gets no line count (the function entry is counted, its return-only arms read 0, and they carry no branch points); `scripts/code_coverage.py` lists them as "not measurable". |
| LVGL | v9.6.0 (commit 80ca777e, 2026-09-16) | Phase 6 host demos, VG-Lite draw unit on the G2D driver | `scripts/fetch_lvgl.sh` clones the tag into `~/tools/src/lvgl-9.6.0` and checks the commit (MIT licence, not stored in this repo) |
| Python | 3.12.15 (original machine); 3.13.18 in the cloud container (v1.9 / v2.0 runs) | generators, cocotb, model bindings | `~/tools/python` (conda prefix env) / container system Python |
| cocotb | 2.1.0 | testbenches | pip |
| cocotbext-axi | 0.1.28 | AXI4-Lite master BFM (the AXI4 memory model is project-specific, dv/common/axi_mem.py) | pip |
| pytest | 9.1.1 | test runner | conda |
| PyYAML | 6.0.3 | regs.yaml parsing | pip |
| Jinja2 | 3.1.6 | (reserved for templates) | pip |
| pybind11 | 3.1.0 | golden model bindings (Phase 1) | pip |
| numpy 2.5.3, Pillow 12.3.0 | | image compare, PNG dumps | conda / pip |
| skia-python | 87.6 (Skia m87) | third-party renderer for the verification matrix (paths, strokes, gradients, image shaders, blends; docs/VERIFICATION_MATRIX.md); tests skip it if absent | `pip install --no-deps skia-python==87.6` into `~/tools/python` (BSD-3) |
| ThorVG | bundled with LVGL v9.6 (`src/libs/thorvg`, MIT) | LVGL software reference for vector graphics (strokes, gradients) | compiled by `make lvgl` from the LVGL tree, not stored here |
| matplotlib | 3.11.2 (Agg) | third-party AA path / stroke rasterizer in the verification matrix | conda |
| GCC / G++ | 13.3.0 | C header checks, golden model | system |
| CMake | 3.28.3 | golden model build (Phase 1) | system |
| RISC-V GCC (bare metal) | SiFive GCC-Metal 13.2.0 (toolsuite 3.0.6), multilib incl. `rv64imafdc_zicsr/lp64d` | bare-metal driver (Phase 6/7) | on PATH via `~/tools/env.sh` (any riscv64 bare-metal GCC 13 with this multilib works, e.g. xPack 13.2.0-2) |
| Vivado | 2024.2 | FPGA build (Phase 7): `make fpga`, `make fpga-ooc` | `source <Vivado install>/2024.2/settings64.sh` **before** `scripts/env.sh` (Vivado puts its own python3 first). xsim 2024.2 cannot elaborate Cheshire (OPEN_QUESTIONS Q-FPGA-7) |
| Cheshire | commit `6c6dd5e8` (main, 2026-10-14) | SoC host for the FPGA build and SoC simulation (Phase 7) | `make fpga-setup` (`fpga/scripts/setup_cheshire.sh`): clone into `CHS_ROOT` (default `~/tools/src/cheshire`), patch `fpga/cheshire/cheshire-g2d.patch`, `bender checkout`, `make chs-hw-all`; Solderpad / Apache, not stored here |
| bender | 0.32.1 | Cheshire dependencies and file lists | `~/.cargo/bin/bender` (already installed) |
| Cheshire Python venv | Python 3.12 + peakrdl 1.5.0, peakrdl-rawheader 0.2.9, mako 1.4.3, hjson 3.1.0, flatdict 4.1.0, setuptools < 81, pyyaml, tabulate, yapf | Cheshire's register / PLIC generators | `~/tools/python/bin/python -m venv ~/tools/cheshire-venv` + pip (2026-10-17) |
| Digilent vivado-boards | commit `d8d43ed9` (Genesys2 rev H, board part `digilentinc.com:genesys2:part0:1.1`) | board part for Cheshire's IP scripts (not shipped with Vivado 2024.2) | `git clone --depth 1 https://github.com/Digilent/vivado-boards ~/tools/src/vivado-boards` (MIT); used through `fpga/genesys2/vivado_boards.tcl` (`G2D_BOARD_REPO`), no Vivado settings changed |
| OpenOCD | 0.12.0 (+ libusb 1.0.27, static) | JTAG debug / ELF loading on the board (Phase 7; the board machine needs its own) | built from source into `~/tools/openocd` (`--enable-ftdi`, bundled jimtcl; log `~/tools/src/openocd-build.log`) |
| OSS CAD Suite | 2026-10-01 nightly (Yosys 0.69+173, SymbiYosys, yosys-smtbmc, z3, boolector, bitwuzla, yices) | formal proofs (v1.1: async FIFO, `make formal`); **not put on PATH** (it bundles its own Verilator / Python): the Makefile prefixes `$(OSS_CAD)/bin` only for `sby` | GitHub release `oss-cad-suite-linux-x64-20261001.tgz` extracted into `~/tools/oss-cad-suite-20261001` (2026-10-18, 2.5 GB, approved by the user); `OSS_CAD` in `scripts/env.sh` |

Integration and ASIC tools (v1.9, decision 2026-11-12; cloud container, no system packages: Nix and git only):

| Tool | Version | Used for | Install |
|---|---|---|---|
| FuseSoC | 2.4.7 | `g2d.core` checks (`lint` target, core-info in scripts/tests/test_packaging.py) | `python3 -m venv ~/tools/fusesoc-venv && ~/tools/fusesoc-venv/bin/pip install fusesoc==2.4.7` |
| nixpkgs | nixos-26.05 channel (26.05.13936.ac588ef1c914) | binary packages for the tools below, from cache.nixos.org | `nixexprs.tar.xz` from channels.nixos.org unpacked into `~/tools/nixpkgs/src` (GitHub tarballs are not reachable here) |
| OpenSTA | 3.2.0 (git 2947a0ca, 2026-11-10) | SDC check (`scripts/sta_sdc_check.py`), later STA | `git clone` The-OpenROAD-Project/OpenSTA into `~/tools/src/OpenSTA`, built by `~/tools/src/build_opensta.sh` in a `nix-shell` with cmake, tcl, swig, eigen, cudd 3.0.0, bison, flex, zlib, gtest; installed in `~/tools/opensta` |
| LibreLane | 3.0.3 (nixpkgs 26.05; OpenROAD 26Q2, Yosys 0.62, Magic, Netgen, KLayout 0.30.8) | open RTL-to-GDS flow (Phase 8 / v2.0 ASIC numbers) | `nix-env -p ~/tools/nix-profiles/librelane -f ~/tools/nixpkgs/src -iA librelane`; PATH `~/tools/nix-profiles/librelane/bin`. Yosys has no slang plugin: `USE_SLANG: false` (built-in SystemVerilog frontend) |
| IHP SG13G2 open PDK | IHP-Open-PDK commit 158c8ccb (2026-08-17), sparse: `libs.ref/sg13g2_stdcell`, `sg13g2_io`, `libs.tech/{librelane,klayout,magic,netgen}` (282 MB) | PDK for LibreLane / OpenSTA | `git clone --filter=blob:none` IHP-GmbH/IHP-Open-PDK into `~/tools/pdk/IHP-Open-PDK`, sparse checkout. 158c8cc, not HEAD: later commits use the `CELL_LIBS` variable of LibreLane 3.1 (dev only); ciel's PDK download hosts are not reachable here. Run: `librelane --manual-pdk --pdk-root ~/tools/pdk/IHP-Open-PDK --pdk ihp-sg13g2 --scl sg13g2_stdcell config.json` |

Linux driver tools (v2.0 step B, 2026-11-12; `make linux-kmod`, `make linux-dt-check`; not in the setup script):

| Tool | Version | Used for | Install |
|---|---|---|---|
| RISC-V GCC for kernel modules | xPack GNU RISC-V Embedded GCC 13.2.0 (`riscv64-unknown-elf-`) | `g2d.ko` builds (the Makefile uses it when present) | xPack release tarball extracted into `~/tools/riscv64-elf` |
| Linux v5.10.7 + cva6-sdk patches | tag v5.10.7 from the `gregkh/linux` GitHub mirror (git.kernel.org not reachable), the 8 patches of cva6-sdk `df31dbe` `linux_patch/`, `linux64_defconfig` as `cva6_defconfig` | the kernel Cheshire's cva6-sdk boots | `~/tools/src/fetch_linux.sh` (clone + `git am`), `~/tools/src/prep_linux.sh` (`make modules_prepare`); trees in `~/tools/src/linux-v5.10.7` |
| Linux v6.12.117 LTS | tag v6.12.117, same mirror, RISC-V `defconfig` | API check against a current kernel | same scripts, `~/tools/src/linux-v6.12.117` |
| cva6-sdk | commit `df31dbe` (2026-01-16) | the patches and defconfig above | `git clone https://github.com/pulp-platform/cva6-sdk ~/tools/src/cva6-sdk` |
| dtschema | 2026.9 | DT binding validation | `python3 -m venv ~/tools/dtschema-venv && ~/tools/dtschema-venv/bin/pip install dtschema` |
| dtc | from the v6.12.117 tree (`scripts/dtc/dtc`) | device tree compile for `linux-dt-check` (`DTC`) | built by `modules_prepare` |
| FreeRTOS Kernel | V11.3.3 (`8959280`) | FreeRTOS HAL tests (step A) | `scripts/fetch_freertos.sh` into `~/tools/src/FreeRTOS-Kernel-V11.3.3` |

Not available here: a Linux-target RISC-V toolchain (`riscv64-unknown-linux-gnu`), so no full kernel build (the vDSO
needs it) and no RISC-V build of the user-space HAL (OPEN_QUESTIONS Q-LNX-3).

ASIC run tools (v2.0 step C, 2026-11-13 .. 16; docs/ASIC.md; not in the setup script):

| Tool | Version | Used for | Install |
|---|---|---|---|
| sv2v | 0.0.13.1 | SystemVerilog to Verilog for Yosys (`scripts/asic_ihp.py`) | `nix-env -p ~/tools/nix-profiles/sv2v -f ~/tools/nixpkgs/src -iA haskellPackages.sv2v` |
| IHP SG13G2 SRAM macros | same PDK commit 158c8ccb, `libs.ref/sg13g2_sram` (LEF, Liberty, GDS, Verilog models; 44 MB) | `rtl/ram/ihp`, the ASIC run, RAM-variant and gate-level tests | `git -C ~/tools/pdk/IHP-Open-PDK sparse-checkout add ihp-sg13g2/libs.ref/sg13g2_sram` |
| Yosys / OpenROAD / KLayout / Magic | the LibreLane 3.0.3 Nix closure (Yosys 0.62, OpenROAD 26Q2) | also called directly: `scripts/gls_netlist.py` (Yosys), the congestion diagnosis (OpenROAD) | found under `/nix/store` |

Machine limits seen in the ASIC run (4 CPUs, 15 GB, about 13 GB of free disk): flat synthesis, LibreLane's
multi-corner STA steps, post-route antenna repair and KLayout DRC of the full g2d_top do not fit; the container
restarts about every 10-14 hours. The OSS CAD Suite (`make formal`) is not installed in the cloud container.

The cloud environment's setup script installs all of these (tested 2026-11-12 from an empty home directory and
re-run idempotently; it pins OpenSTA 2947a0ca, nixpkgs 26.05, IHP 158c8ccb).

Smoke run 2026-11-12: `g2d_axil_single` through the whole LibreLane flow at 100 MHz: 683 cells, 0 Magic / KLayout DRC
errors, LVS clean, setup and hold met in all three corners (slow 1.08 V 125 °C: setup slack 5.59 ns).

Not installed yet (needed later; I will ask before installing):

| Tool | Needed in | Note |
|---|---|---|
| RISC-V Linux toolchain (`riscv64-unknown-linux-gnu`) and Linux/buildroot for CVA6 | Phase 7b (Linux) | Cheshire's documented flow builds it from `sw/deps/cva6-sdk` (`make images`, ~30 min). Check in Phase 7b. |
| ThorVG / LVGL `vg_lite_tvg` build | Phase 1 (optional cross-check), Phase 6 | Built from source into `build/`, not installed. |
| Equivalence checking (LEC), yosys-slang plugin | ASIC (docs/ROADMAP_V2.md §1.1) | LibreLane / OpenSTA installed 2026-11-12 (above); LEC and the slang SystemVerilog plugin not yet. |
