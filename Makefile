# SPDX-License-Identifier: Apache-2.0
# G2D top-level Makefile. Run `source scripts/env.sh` first (see docs/TOOLS.md).
SHELL         := /bin/bash
PYTHON        ?= python3
VERILATOR     ?= verilator
VERILATOR_MIN := 5.036

# Lint: every unit is linted as its own top with all packages and RTL sources (Verilator -Wall).
RTL_PKGS   := rtl/pkg/generated/g2d_csr_pkg.sv rtl/pkg/generated/g2d_state_pkg.sv rtl/pkg/generated/g2d_gamma_pkg.sv \
              rtl/pkg/g2d_pkg.sv
RTL_SRCS   := rtl/csr/generated/g2d_csr_regs.sv $(sort $(wildcard rtl/common/*.sv rtl/csr/*.sv rtl/axi/*.sv \
              rtl/cmdproc/*.sv rtl/fill/*.sv rtl/blend/*.sv rtl/blit/*.sv rtl/raster/*.sv rtl/gradient/*.sv rtl/pix/*.sv rtl/cdc/*.sv rtl/adapt/*.sv rtl/top/*.sv)) \
              $(addprefix rtl/wrap/generated/g2d_top_,$(addsuffix .sv,axi apb ahb obi regintf wb tlul))
LINT_TOPS  := $(basename $(notdir $(RTL_SRCS)))
# v1.9 memory variants (rtl/ram/<impl>, drop-in replacements of rtl/common/g2d_ram_*.sv), linted with g2d_top
RAM_IMPLS  := xilinx asic ihp
# v2.0 IHP SG13G2 macro mapping (rtl/ram/ihp): lint against port-only stubs; standalone sizes cover every tile path
IHP_RAM_SRCS := rtl/ram/ihp/g2d_ihp_2p.sv rtl/ram/ihp/g2d_ihp_1p.sv rtl/ram/ihp/stubs/g2d_ihp_sram_stubs.sv
IHP_RAM_VARIANTS := g2d_ram_1r1w:-GWIDTH=202,-GDEPTH=512 g2d_ram_1r1w:-GWIDTH=512,-GDEPTH=32,-GBE_W=8 \
                    g2d_ram_1r1w:-GWIDTH=67,-GDEPTH=64 g2d_ram_1r1w:-GWIDTH=9,-GDEPTH=1024 g2d_ram_1r1w:-GWIDTH=8,-GDEPTH=16 \
                    g2d_ram_1r1w:-GWIDTH=8,-GDEPTH=4096 g2d_ram_sp:-GWIDTH=100,-GDEPTH=256 \
                    g2d_ram_sp:-GWIDTH=64,-GDEPTH=4096,-GBE_W=4 g2d_ram_sp:-GWIDTH=32,-GDEPTH=2000,-GBE_W=2 \
                    g2d_ram_sp:-GWIDTH=24,-GDEPTH=8
RTL_SRCS_NO_RAM := $(filter-out rtl/common/g2d_ram_1r1w.sv rtl/common/g2d_ram_sp.sv,$(RTL_SRCS))
# Extra parameter sets: <top>:<-G args separated by commas>
LINT_VARIANTS := g2d_axi2axil:-GDATA_W=32,-GID_W=1 g2d_axi2axil:-GDATA_W=512,-GADDR_W=64 g2d_axi2axil:-GDATA_W=128,-GADDR_W=48 \
                 g2d_axi_mst:-GDATA_W=128,-GADDR_W=48,-GN_RD=3 g2d_axi_mst:-GADDR_W=64,-GMAX_BURST_BYTES=64 \
                 g2d_top:-GDATA_W=128,-GADDR_W=48,-GKICK_DEPTH=16 g2d_top:-GADDR_W=64,-GMAX_BURST_BYTES=4096,-GDATA_W=128 \
                 g2d_src_cache:-GDATA_W=128,-GCACHE_BYTES=2048,-GPIX_DEPTH=8 \
                 g2d_src_cache:-GCACHE_BYTES=32768 g2d_edge_load:-GDATA_W=128 g2d_sdiv:-GNW=27,-GDW=21,-GQW=26 \
                 g2d_raster:-GDATA_W=128,-GMAX_EDGES=64 g2d_ramp:-GDATA_W=128 g2d_raster:-GMAX_EDGES=64,-GCHUNK=5 \
                 g2d_top:-GCFG_ASYNC=1,-GMEM_ASYNC=1 g2d_top:-GMEM_ASYNC=1,-GDATA_W=128,-GADDR_W=48,-GSYNC_STAGES=3,-GMEM_CDC_LOG_DEPTH=5 \
                 g2d_top:-GCFG_ASYNC=1,-GCFG_CDC_LOG_DEPTH=4 g2d_cdc_fifo:-GLOG_DEPTH=3,-GSYNC_STAGES=3,-GWIDTH=70 \
                 g2d_top:-GFEATURES=0 g2d_top:-GRST_STAGES=0

.PHONY: help all check-tools regs regs-check lint test smoke regress code-coverage lvgl live live-perf baremetal-check model clean \
        lvgl-chs doom-host doom-sw doom-sdimage doom-package fpga-setup fpga fpga-sw fpga-package fpga-ooc fpga-sim-build fpga-sim formal freertos-sw linux-kmod linux-dt-check \
        asic-synth asic-pnr asic-report asic-gls

help:
	@echo "Targets:"
	@echo "  regs        generate RTL/C/docs from spec/regs.yaml"
	@echo "  regs-check  fail if generated outputs are stale"
	@echo "  lint        Verilator -Wall lint of all RTL"
	@echo "  test        everything once (seed 1, short mode) on at most 8 jobs: unit, model, block, top-level, SYS"
	@echo "  smoke       per-commit run, same as test (< 5 min)"
	@echo "  regress     make test + seeded model suites and full-length RTL benches for REGRESS_SEEDS,"
	@echo "              then the merged functional coverage (build/coverage/report.md, must be 100 %)"
	@echo "  code-coverage RTL line/toggle coverage of the make-test suite (build/coverage/code/report.md)"
	@echo "  baremetal-check  compile driver + geometry + bare-metal HAL for RISC-V (Cheshire, Phase 7)"
	@echo "  live        live RTL backend: driver programs on the verilated g2d_top (build/live)"
	@echo "  live-perf   live + the DATA_W 128 / 8-outstanding LVGL build for scripts/perf_lvgl.py"
	@echo "  model       build the C++ golden model, geometry lib and Python module; run C/C++ unit tests"
	@echo "  check-tools verify tool versions"
	@echo "  fpga-setup  Cheshire checkout for the FPGA build (CHS_ROOT, fpga/scripts/setup_cheshire.sh)"
	@echo "  fpga        Cheshire + G2D bitstream for the Genesys2 (build/fpga/impl), Vivado"
	@echo "  lvgl-chs    LVGL VG-Lite demos linked at 0x9000_0000 (build/lvgl_chs) for the FPGA test's LVGL frame"
	@echo "  fpga-sw     bare-metal test ELFs + expected output (build/fpga/sw), RISC-V GCC"
	@echo "  fpga-package board package for the Genesys2 machine (build/fpga/g2d_board_pkg.tar.gz)"
	@echo "  fpga-ooc    G2D out-of-context synthesis + P&R for the Genesys2 part (build/fpga/ooc<MHz>)"
	@echo "  fpga-sim    Cheshire + G2D SoC simulation on Verilator (build/fpga/socsim), FPGA_ELF=<elf>"
	@echo "  freertos-sw FreeRTOS test ELFs for Cheshire (build/fpga/freertos), needs scripts/fetch_freertos.sh"
	@echo "  linux-kmod  build sw/linux/g2d.ko against each KDIRS tree + export check (build/linux/v<ver>)"
	@echo "  linux-dt-check  validate the DT binding and the Cheshire node with dtschema (DTSCHEMA_BIN, CHS_ROOT)"
	@echo "  asic-synth  g2d_top synthesis on IHP SG13G2, LibreLane (build/asic/ihp/<ASIC_SYNTH>, docs/ASIC.md)"
	@echo "  asic-pnr    floorplan to signoff from asic-synth at ASIC_PERIOD ns (build/asic/ihp/<ASIC_TAG>)"
	@echo "  asic-report area, cells, per-corner slack and fmax of ASIC_TAG"
	@echo "  asic-gls    gate-level simulation of the newest routed netlist (dv/tests/test_gls_ihp.py)"
	@echo "  formal      SymbiYosys proofs of the CDC FIFO (build/formal, OSS_CAD)"
	@echo "  clean       remove build/ and generated files"

all: lint test

check-tools:
	@command -v $(VERILATOR) >/dev/null || { echo "error: verilator not found (source scripts/env.sh)"; exit 1; }
	@v=$$($(VERILATOR) --version | awk '{print $$2}'); \
	  if [ "$$(printf '%s\n%s\n' $(VERILATOR_MIN) $$v | sort -V | head -1)" != "$(VERILATOR_MIN)" ]; then \
	    echo "error: Verilator $$v < $(VERILATOR_MIN) (needed by cocotb 2.x); source scripts/env.sh"; exit 1; fi
	@$(PYTHON) -c "import yaml, cocotb, cocotb_tools, cocotbext.axi, pytest" || \
	  { echo "error: python packages missing (see docs/TOOLS.md)"; exit 1; }
	@echo "tools OK: $$($(VERILATOR) --version | cut -d' ' -f1-2), $$($(PYTHON) --version), cocotb $$($(PYTHON) -c 'import cocotb; print(cocotb.__version__)')"

regs:
	$(PYTHON) scripts/gen_regs.py
	$(PYTHON) scripts/gen_wrappers.py
	$(PYTHON) scripts/gen_packaging.py
	$(PYTHON) scripts/gen_drv_api.py

regs-check:
	$(PYTHON) scripts/gen_regs.py --check
	$(PYTHON) scripts/gen_wrappers.py --check
	$(PYTHON) scripts/gen_packaging.py --check
	$(PYTHON) scripts/gen_drv_api.py --check
	$(PYTHON) scripts/check_sdc.py syn/constraints/g2d_top.sdc

lint: check-tools regs
	@for t in $(LINT_TOPS); do \
	  $(VERILATOR) --lint-only -Wall rtl/lint.vlt $(RTL_PKGS) $(RTL_SRCS) --top-module $$t || exit 1; \
	done
	@for v in $(LINT_VARIANTS); do \
	  t=$${v%%:*}; g=$${v#*:}; \
	  $(VERILATOR) --lint-only -Wall rtl/lint.vlt $(RTL_PKGS) $(RTL_SRCS) --top-module $$t $${g//,/ } || exit 1; \
	done
	@for r in $(RAM_IMPLS); do \
	  $(VERILATOR) --lint-only -Wall rtl/lint.vlt $(RTL_PKGS) $(RTL_SRCS_NO_RAM) rtl/ram/$$r/g2d_ram_1r1w.sv \
	    rtl/ram/$$r/g2d_ram_sp.sv $(wildcard rtl/ram/asic/models/*.sv) $$([ $$r = ihp ] && echo $(IHP_RAM_SRCS)) \
	    --top-module g2d_top || exit 1; \
	done
	@for v in $(IHP_RAM_VARIANTS); do \
	  t=$${v%%:*}; g=$${v#*:}; \
	  $(VERILATOR) --lint-only -Wall rtl/lint.vlt rtl/ram/ihp/$$t.sv $(IHP_RAM_SRCS) --top-module $$t $${g//,/ } || exit 1; \
	done
	@echo "lint OK ($(words $(LINT_TOPS)) units, $(words $(LINT_VARIANTS)) parameter variants, RAM variants: $(RAM_IMPLS)," \
	  "$(words $(IHP_RAM_VARIANTS)) IHP RAM sizes)"

# Everything once (seed 1, short mode) on up to 8 parallel jobs (scripts/regress.py; target < 5 min).
test: check-tools model
	rm -rf build/coverage
	G2D_MODEL_PATH=$(MODEL_BUILD) $(PYTHON) scripts/regress.py --jobs $(JOBS) --suite test --seeds 1 --out build/regress/test

# LVGL host demos on the G2D driver (needs scripts/fetch_lvgl.sh); build/lvgl/sw/lvgl_demo_{vg,sw}{32,16}
LVGL_DIR ?= $(HOME)/tools/src/lvgl-9.6.0
lvgl: check-tools regs
	scripts/fetch_lvgl.sh $(LVGL_DIR)
	cmake -S model -B build/lvgl -DG2D_BUILD_LVGL=ON -DG2D_LVGL_DIR=$(LVGL_DIR) -DPython3_EXECUTABLE=$$(command -v $(PYTHON)) >/dev/null
	cmake --build build/lvgl -j $(JOBS) --target lvgl_demo_vg32 lvgl_demo_vg16 lvgl_demo_sw32 lvgl_demo_sw16 lvgl_demo_tv32
# DOOM side project (docs/PHASES.md): host harness on the golden-model HAL, build/doom/sw/doom_host
doom-host: check-tools regs
	scripts/fetch_doom.sh
	cmake -S model -B build/doom -DG2D_BUILD_DOOM=ON -DPython3_EXECUTABLE=$$(command -v $(PYTHON)) >/dev/null
	cmake --build build/doom -j $(JOBS) --target doom_host test_doom_keys
# v1.2: the VG-Lite demos linked at 0x9000_0000 (identity-mode GPU addresses inside Cheshire's DRAM) for the LVGL frame
# of the bare-metal FPGA test (scripts/fpga_lvgl.py). Above 2 GB x86-64 needs the large code model and the
# position-independent crtbegin (sw/apps/lvgl/chs_link.specs).
lvgl-chs: check-tools regs
	scripts/fetch_lvgl.sh $(LVGL_DIR)
	cmake -S model -B build/lvgl_chs -DG2D_BUILD_LVGL=ON -DG2D_LVGL_DIR=$(LVGL_DIR) -DPython3_EXECUTABLE=$$(command -v $(PYTHON)) \
	  -DCMAKE_C_FLAGS=-mcmodel=large -DCMAKE_CXX_FLAGS=-mcmodel=large \
	  "-DCMAKE_EXE_LINKER_FLAGS=-specs=$(CURDIR)/sw/apps/lvgl/chs_link.specs -Wl,-Ttext-segment=0x90000000 -Wl,--no-relax" >/dev/null
	cmake --build build/lvgl_chs -j $(JOBS) --target lvgl_demo_vg32 lvgl_demo_vg16

# RTL line / toggle coverage of the make-test suite (scripts/code_coverage.py; build/coverage/code/report.md)
code-coverage: check-tools model
	G2D_MODEL_PATH=$(MODEL_BUILD) $(PYTHON) scripts/code_coverage.py

REGRESS_SEEDS ?= 1 2 3 4 5
REGRESS_SEEDS_CSV = $(subst $(eval) ,$(comma),$(strip $(REGRESS_SEEDS)))
comma := ,

# Until the Phase 5 runner: make test, then per seed the seeded model suites and every RTL bench in
# full mode (G2D_FULL=1: all AXI/top configurations at full length).
REGRESS_DV := dv/tests/test_common.py dv/tests/test_csr_top.py dv/tests/test_axi_mst.py dv/tests/test_draw_check.py \
              dv/tests/test_blend.py dv/tests/test_xform.py dv/tests/test_sampler.py dv/tests/test_src_cache.py \
              dv/tests/test_sdiv.py dv/tests/test_edge_load.py dv/tests/test_raster.py dv/tests/test_grad.py \
              dv/tests/test_top.py dv/tests/test_blit.py dv/tests/test_path.py dv/tests/test_cdc.py
# per-commit smoke run (user 2026-10-17, OPEN_QUESTIONS Q-TEST-3): same as test, < 5 min
smoke: test

regress: test lvgl live
	G2D_MODEL_PATH=$(MODEL_BUILD) $(PYTHON) scripts/regress.py --jobs $(JOBS) --suite model --seeds $(REGRESS_SEEDS_CSV) \
	  --out build/regress/model --coverage-report ""
	G2D_MODEL_PATH=$(MODEL_BUILD) $(PYTHON) scripts/regress.py --jobs $(JOBS) --suite benches --full --seeds $(REGRESS_SEEDS_CSV) \
	  --out build/regress/benches --coverage-report full
	# Phase 6 verification matrix, full length once: LVGL screenshots (all demos), live RTL backend, SYS-06 replay
	# SYS-06 one node per build (dw64, dw128, async, async_s3d2) so they run in parallel (in series they pass the 2 h
	# per-run limit since v1.1 added the async builds; the async replays take ~6000 s, so the limit is 3 h here)
	G2D_MODEL_PATH=$(MODEL_BUILD) $(PYTHON) scripts/regress.py --jobs $(JOBS) --nodes sw/tests/test_lvgl.py sw/tests/test_live.py \
	  $(foreach c,dw64 dw128 async async_s3d2,"dv/tests/test_sys06.py::test_sys06[$(c)]") --full --seeds 1 --timeout 10800 \
	  --out build/regress/phase6 --coverage-report ""

# Formal proofs (verification-plan.md CDC-05) with SymbiYosys from the OSS CAD Suite (OSS_CAD, scripts/env.sh). Its
# bin/ is put first only for sby: it bundles its own Verilator / Python, which must not replace ours elsewhere.
OSS_CAD ?= $(HOME)/tools/oss-cad-suite-20261001
FORMAL_TASKS ?= prove_d2 prove_d8 prove_s3 cover_d2
formal:
	@test -x $(OSS_CAD)/bin/sby || { echo "sby not found in OSS_CAD=$(OSS_CAD) (docs/TOOLS.md)"; exit 1; }
	@mkdir -p build/formal
	@for t in $(FORMAL_TASKS); do \
	  rm -rf build/formal/$$t; \
	  ( cd dv/formal && PATH=$(OSS_CAD)/bin:$$PATH sby -f -d ../../build/formal/$$t g2d_cdc_fifo.sby $$t ) \
	    > build/formal/$$t.log 2>&1 || { echo "formal $$t FAILED (build/formal/$$t.log)"; exit 1; }; \
	  echo "formal $$t PASS"; \
	done

MODEL_BUILD := build/model

# Bare-metal HAL (sw/hal/baremetal, Cheshire / CVA6): compile check of driver + geometry + HAL with the SiFive
# riscv64 GCC (run in Phase 7). RV_CC from PATH (~/tools/env.sh).
RV_CC ?= riscv64-unknown-elf-gcc
RV_FLAGS := -march=rv64imafdc_zicsr -mabi=lp64d -mcmodel=medany -O2 -std=c99 -Wall -Wextra -Werror -pedantic \
            -ffp-contract=off -ffreestanding -Isw/include -Isw/hal -Isw/lib/geom -Isw/driver/vg_lite
baremetal-check: regs
	@mkdir -p build/baremetal
	python3 scripts/gen_vglite_stubs.py sw/include/vg_lite.h build/baremetal/vg_lite_stubs.c sw/driver/vg_lite/drv_*.c
	for f in sw/driver/vg_lite/drv_*.c sw/lib/geom/g2d_geom.c sw/hal/baremetal/g2d_hal_baremetal.c build/baremetal/vg_lite_stubs.c; do \
	  $(RV_CC) $(RV_FLAGS) -c $$f -o build/baremetal/$$(basename $$f .c).o || exit 1; done
	riscv64-unknown-elf-ar rcs build/baremetal/libg2d_vglite.a build/baremetal/*.o
	@echo "baremetal-check OK: build/baremetal/libg2d_vglite.a"

# Live RTL backend (dv/live): vgl_scene / test_driver (and the LVGL demo when LVGL is fetched) on the verilated
# g2d_top, build/live/live/{vgl_scene,test_driver,lvgl_demo_live32}_live_{dw64,dw128}
live: check-tools regs
	cmake -S model -B build/live -DG2D_BUILD_LIVE=ON $$(test -f $(LVGL_DIR)/lvgl.h && echo -DG2D_BUILD_LVGL=ON -DG2D_LVGL_DIR=$(LVGL_DIR)) \
	  -DPython3_EXECUTABLE=$$(command -v $(PYTHON)) >/dev/null
	cmake --build build/live -j $(JOBS) --target vgl_scene_live_dw64 vgl_scene_live_dw128 test_driver_live_dw64 \
	  test_driver_live_dw128 $$(test -f $(LVGL_DIR)/lvgl.h && echo lvgl_demo_live32_dw64 lvgl_demo_live32_dw128)
# Performance-only live build (DATA_W 128, default outstanding limits) for scripts/perf_lvgl.py
live-perf: live
	cmake --build build/live -j $(JOBS) --target lvgl_demo_live32_dw128o8

JOBS        ?= 8    # project limit: at most 8 cores (user, 2026-11-05; was 32)

model: regs
	cmake -S model -B $(MODEL_BUILD) -DPython3_EXECUTABLE=$$(command -v $(PYTHON)) >/dev/null
	cmake --build $(MODEL_BUILD) -j $(JOBS)
	cd $(MODEL_BUILD) && ctest --output-on-failure

# ---- FPGA (Phase 7): Cheshire + G2D on Genesys2. Needs Vivado in PATH (source its settings64.sh) and bender.
CHS_ROOT    ?= $(HOME)/tools/src/cheshire
FPGA_MHZ    ?= 50
FPGA_ELF    ?=
FPGA_ASYNC  ?= 0     # 1: v1.1 async build (G2D core 100 MHz, ports at the SoC clock through the CDC)
FPGA_TRIM   ?= 0     # 1: v1.2 trimmed Cheshire (no USB, iDMA, bus-error units, I2C; VGA kept)
FPGA_DOOM   ?= 0     # 1: DOOM side project: chs_ps2 keyboard receiver on the USB HID pins (bitstream *_doom)
SOCSIM_ASYNC = $(if $(filter 1,$(FPGA_ASYNC)),--async,) $(if $(filter 1,$(FPGA_TRIM)),--trim,) $(if $(filter 1,$(FPGA_DOOM)),--ps2,)
fpga-setup:
	CHS_ROOT=$(CHS_ROOT) fpga/scripts/setup_cheshire.sh
fpga-ooc: regs
	@command -v vivado >/dev/null || { echo "error: vivado not in PATH (source Vivado's settings64.sh)"; exit 1; }
	@mkdir -p build/fpga/ooc$(FPGA_MHZ)$(OOC_TAG)
	cd build/fpga/ooc$(FPGA_MHZ)$(OOC_TAG) && vivado -mode batch -nojournal -log vivado.log -source $(CURDIR)/fpga/genesys2/g2d_ooc.tcl \
	  -tclargs . $$(python3 -c "print(1000.0/$(FPGA_MHZ))") $(addprefix $(CURDIR)/,$(RTL_PKGS) $(RTL_SRCS)) >/dev/null
	@grep -E "G2D_OOC_DONE" build/fpga/ooc$(FPGA_MHZ)$(OOC_TAG)/vivado.log
fpga: regs
	CHS_ROOT=$(CHS_ROOT) G2D_FPGA_ASYNC=$(FPGA_ASYNC) G2D_FPGA_TRIM=$(FPGA_TRIM) G2D_FPGA_DOOM=$(FPGA_DOOM) fpga/scripts/build_fpga.sh $(RTL_PKGS) $(RTL_SRCS)
fpga-sw: model
	CHS_ROOT=$(CHS_ROOT) PYTHON=$(PYTHON) fpga/scripts/build_sw.sh
# v2.0 FreeRTOS test for Cheshire (sw/apps/freertos): ELFs + expected CRCs from the host test (needs make model with
# the FreeRTOS kernel fetched by scripts/fetch_freertos.sh)
freertos-sw: model
	CHS_ROOT=$(CHS_ROOT) fpga/scripts/build_freertos_sw.sh
# v2.0 Linux kernel module (sw/linux): KDIRS = configured kernel trees (make modules_prepare), RISC-V GCC in PATH
KDIRS ?= $(HOME)/tools/src/linux-v5.10.7 $(HOME)/tools/src/linux-v6.12.117
KMOD_CROSS ?= $(if $(wildcard $(HOME)/tools/riscv64-elf/bin/riscv64-unknown-elf-gcc),$(HOME)/tools/riscv64-elf/bin/,)riscv64-unknown-elf-
linux-kmod: regs-check
	$(PYTHON) scripts/build_kmod.py --cross $(KMOD_CROSS) $(addprefix --kdir ,$(KDIRS))
DTSCHEMA_BIN ?= $(HOME)/tools/dtschema-venv/bin
DTC ?= $(HOME)/tools/src/linux-v6.12.117/scripts/dtc/dtc
linux-dt-check:
	@mkdir -p build/linux/dt
	$(DTSCHEMA_BIN)/dt-doc-validate -u sw/linux/bindings sw/linux/bindings/gpu/g2d.yaml
	$(DTSCHEMA_BIN)/dt-mk-schema -j sw/linux/bindings > build/linux/dt/g2d.json
	cat $(CHS_ROOT)/sw/boot/cheshire.genesys2.dts sw/linux/dts/g2d-cheshire.dtsi > build/linux/dt/chs-g2d.dts
	$(DTC) -q -I dts -O dtb -i $(CHS_ROOT)/sw/boot -o build/linux/dt/chs-g2d.dtb build/linux/dt/chs-g2d.dts
	@$(DTSCHEMA_BIN)/dt-validate -s build/linux/dt/g2d.json build/linux/dt/chs-g2d.dtb 2>&1 | grep 'gpu@' ; \
	  if [ $$? -eq 0 ]; then echo "FAIL: gpu node"; exit 1; else echo "PASS: binding + Cheshire gpu node"; fi
# v2.0 ASIC numbers on IHP SG13G2 (docs/ASIC.md): LibreLane 3.0.3, sv2v, IHP-Open-PDK with the SRAM macros
ASIC_PERIOD ?= 20.0
ASIC_SYNTH  ?= s10
ASIC_TAG    ?= p$(ASIC_PERIOD)
asic-synth: regs-check
	$(PYTHON) scripts/asic_ihp.py synth --tag $(ASIC_SYNTH) --period $(ASIC_PERIOD)
asic-pnr:
	$(PYTHON) scripts/asic_ihp.py pnr --tag $(ASIC_TAG) --from-synth $(ASIC_SYNTH) --period $(ASIC_PERIOD)
asic-report:
	$(PYTHON) scripts/asic_ihp.py report --tag $(ASIC_TAG)
asic-gls:
	G2D_GLS=1 $(PYTHON) -m pytest -q -p no:cacheprovider dv/tests/test_gls_ihp.py
doom-sw: regs
	CHS_ROOT=$(CHS_ROOT) fpga/scripts/build_doom_sw.sh
doom-sdimage: doom-sw
	CHS_ROOT=$(CHS_ROOT) fpga/scripts/mk_doom_sd.sh
doom-package:
	fpga/scripts/package_doom.sh
fpga-package:
	CHS_ROOT=$(CHS_ROOT) G2D_FPGA_TRIM=$(FPGA_TRIM) fpga/scripts/package.sh
fpga-sim-build: regs
	CHS_ROOT=$(CHS_ROOT) $(PYTHON) fpga/sim/socsim.py build $(SOCSIM_ASYNC) --rtl $(RTL_PKGS) $(RTL_SRCS)
fpga-sim: fpga-sim-build
	@test -n "$(FPGA_ELF)" || { echo "error: FPGA_ELF=<program.elf> missing"; exit 1; }
	CHS_ROOT=$(CHS_ROOT) $(PYTHON) fpga/sim/socsim.py run $(SOCSIM_ASYNC) $(FPGA_ELF)

clean:
	rm -rf build sim_build rtl/pkg/generated rtl/csr/generated sw/include/generated
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
