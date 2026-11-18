# FPGA prototype: G2D in Cheshire on the Genesys2

G2D runs as an external device of the [Cheshire](https://github.com/pulp-platform/cheshire) SoC (CVA6, PULP,
Solderpad / Apache licences) on the Digilent Genesys2 (XC7K325T-2, 1 GB DDR3). Cheshire is not part of this
repository: `fpga/scripts/setup_cheshire.sh` clones it at a pinned commit into `CHS_ROOT` (default
`~/tools/src/cheshire`) and applies `fpga/cheshire/cheshire-g2d.patch`.

## 1. System

```
              Cheshire SoC (50 MHz soc_clk, single clock for G2D)
 CVA6 ──┐
 DMA  ──┼── AXI crossbar ──┬── LLC (128 KB) ── CDC ── MIG DDR3 (200 MHz)          0x8000_0000..
 G2D m ─┘ (ext manager 0)  ├── reg demux: UART 0x0300_2000, CLINT 0x0204_0000, PLIC 0x0400_0000, regs 0x0300_0000
                           └── ext subordinate 0: [0x4800_0000, 0x4800_1000) ──► g2d_cheshire ──► G2D CSRs
 G2D irq_o ──► intr_ext_i[0] ──► PLIC source 58
```

| Item | Value | Where |
|---|---|---|
| Cheshire commit | `6c6dd5e8` (main, 2026-10-14) | `fpga/scripts/setup_cheshire.sh` |
| Cheshire config | `DefaultCfg` + Genesys2 changes of Cheshire's FPGA top + G2D ports | `fpga/cheshire/g2d_chs_pkg.sv` |
| G2D parameters | `DATA_W 64`, `ADDR_W 48`, `ID_W 3` (= Cheshire `AxiMstIdWidth`), defaults otherwise | `fpga/cheshire/g2d_cheshire.sv` |
| CSR window | `0x4800_0000`, 4 KB, in Cheshire's non-cached, non-idempotent region `[0x4000_0000, 0x8000_0000)` | `G2dCsrBase` |
| CSR path | AXI4 (64 bit) → PULP `axi_to_axi_lite` → `axi_lite_dw_converter` 64 → 32 → `g2d_top.s_axil` | bridge |
| Memory path | `g2d_top.m_axi` → Cheshire external manager port 0 → crossbar → LLC → DRAM (same LLC as the CPU) | bridge |
| Interrupt | `g2d_top.irq_o` → `intr_ext_i[0]` → PLIC source 58 (PLIC regenerated with 59 sources) | patch |
| GPU memory | bare-metal pool at `0x8100_0000`, 32 MB; program image in Cheshire's 8 MB `dram.ld` region | `fpga/scripts/build_sw.sh` |
| CPU caches | CVA6 `cv64a6_imafdchsclic_sv39_wb`: write-back D-cache, `fence` writes back and invalidates it | `sw/hal/baremetal` |
| Clock | single-clock build: 50 MHz SoC clock for Cheshire and G2D. Async build (v1.1, `FPGA_ASYNC=1`): G2D core at 100 MHz from the fourth output of the clock wizard (`g2d_clkwiz`, the same MMCM as the SoC clock; reset `fpga/cheshire/g2d_core_rst.sv`), both G2D ports at the SoC clock through G2D's own CDC (`CFG_ASYNC = MEM_ASYNC = 1`), CDC constraints `fpga/genesys2/g2d_cdc.tcl` | `gen_top.py --async` |

## 2. Build (development machine)

```sh
source <Vivado install>/2024.2/settings64.sh       # Vivado first: it puts its own python3 in PATH
source scripts/env.sh                                # then the project tools (python3, verilator)
make fpga-setup      # Cheshire checkout, patch, bender checkout, Cheshire hardware generation
make fpga            # bitstream: build/fpga/impl/g2d_chs.genesys2.bit (+ reports); ~1-2 h
make fpga-sw         # bare-metal test: build/fpga/sw/g2d_fpga_test.{board,sim}.elf + expected.txt
make fpga-package    # build/fpga/g2d_board_pkg.tar.gz for the board machine
```

Async build (v1.1): `make fpga FPGA_ASYNC=1` → `build/fpga/impl_async/g2d_chs_async.genesys2.bit`;
`make fpga-sim-build FPGA_ASYNC=1` / `make fpga-sim FPGA_ASYNC=1 FPGA_ELF=...` simulate the same SoC with a 100 MHz G2D
core clock at an arbitrary phase. `make fpga-package` takes both bitstreams; on the board `./run_board.sh --async`
runs the async one into `results_async/`. The bare-metal software is the same for both.

Optional checks:
- `make fpga-ooc` (`FPGA_MHZ=50`): G2D alone, out of context, placed and routed on the Genesys2 part.
- `make fpga-sim-build` and `make fpga-sim FPGA_ELF=build/fpga/sw/g2d_fpga_test.sim.elf`: the whole Cheshire + G2D
  SoC on Verilator (`fpga/sim/tb_g2d_chs.sv`: DRAM model, passive boot through SCRATCH, UART decoder). The `sim`
  ELF uses a UART at 1.5625 Mbaud (divisor 2 at 50 MHz; divisor 1 does not work in the APB UART); check the log with
  `scripts/fpga_check.py build/fpga/sw/expected.txt build/fpga/socsim/run_g2d_fpga_test.sim/uart.log`.
  About 8 k cycles per second: use a small scene set (`FPGA_SCENES_ARGS="--per-kind 1"`).

Tools: docs/TOOLS.md (Vivado 2024.2, bender, Verilator, SiFive GCC 13.2, `~/tools/cheshire-venv` for Cheshire's
generators, Digilent board files in `~/tools/src/vivado-boards`, OpenOCD 0.12 in `~/tools/openocd`).

## 3. Run on the board (board machine)

Needs: the unpacked `g2d_board_pkg.tar.gz`, `vivado` (only to program; any other programmer works with
`--no-program`), `openocd` ≥ 0.12, `python3`. No GDB (2026-10-19): the package's own OpenOCD config
`openocd/openocd_g2d.tcl` (source `fpga/genesys2/openocd_g2d.tcl`: Cheshire's Genesys2 adapter / TAP values in the
current OpenOCD syntax, GDB / telnet / Tcl ports off unless set) has `load_verified`, which loads the flat image
`g2d_fpga_test.board.bin` in 256 KB chunks, checks each with a CRC computed by CVA6 (work area 0x80F0_0000, unused
by the test) and reloads bad chunks up to 3 times. `--gdb` uses `riscv64-unknown-elf-gdb` instead (`load` + `continue`
of the ELF through port 3333).

1. Switches: SW1..SW0 = `00` (boot mode 0, passive), SW5..SW2 not all `0` (fan), SW7 = `0` (test mode).
2. Connect the JTAG / programming USB port and the UART USB port; power on.
3. `tar xzf g2d_board_pkg.tar.gz && cd pkg && ./run_board.sh [--no-program] [--uart /dev/ttyUSBx] [--jtag-khz 4000]`
   - programs `g2d_chs.genesys2.bit` (unless `--no-program`), stops a `hw_server` it started (it holds the cable),
     refuses to start while another OpenOCD runs (`pkill -x openocd`), captures the UART at 115200 8N1;
   - OpenOCD: `load_verified`, `reg pc 0x80000000`, `resume`, `shutdown` (the hart keeps running);
   - stops at `G2D_TEST_END` (or `--timeout` seconds) and checks the log with `fpga_check.py` against
     `expected.txt` (the same scripts on the golden-model software backend).
4. Send back `results.zip` (the `results/` directory: `uart.log`, `check.md`, `openocd.log`, `run.txt`, `env.txt`;
   `gdb.log` with `--gdb`).
5. Async build (v1.1): power-cycle the board, then `./run_board.sh --async [--uart ...]`; send back
   `results_async.zip` too. Same program and expected output; only the busy-cycle counts differ (core cycles at 100 MHz).

If the UART stays silent: reset the board (CPU reset button) and re-run with `--no-program` (Cheshire docs: resetting
during a debug session is not supported). If OpenOCD cannot find the adapter, check `ls /dev/bus/usb` permissions
(Digilent / FTDI udev rules) and that no `hw_server` or other OpenOCD holds it. If chunks keep failing the CRC
(`openocd.log`), lower the JTAG clock (`--jtag-khz 2000`).

Manual fallback (same steps by hand, in `pkg/`):

1. Program the bitstream (Vivado Hardware Manager, then close the hardware target), or skip it if already programmed.
2. Terminal 1, UART: `stty -F /dev/ttyUSBx 115200 cs8 -cstopb -parenb raw -echo -crtscts && cat /dev/ttyUSBx | tee uart.log`
3. Terminal 2: `openocd -f openocd/openocd_g2d.tcl -c "load_verified g2d_fpga_test.board.bin 0x80000000" -c "reg pc 0x80000000" -c resume -c shutdown`
   (interactive instead: add `-c "set TELNET_PORT 4444"` before `-f` and type the commands in `telnet localhost 4444`).
4. After `G2D_TEST_END` in terminal 1: `python3 fpga_check.py expected.txt uart.log --report check.md`.

## 4. Test program and its output

`sw/apps/baremetal/g2d_fpga_test.c` runs the unchanged vg_lite driver on the bare-metal HAL. It executes the
`vgl_scene` scripts generated by `scripts/fpga_scenes.py` (clears, blits, paths and gradients, patterns,
converted formats, strokes: the kinds `sw/tests/test_live.py` runs on the live RTL) and prints, per scene, every
API return code and a CRC-32 of every buffer, then an interrupt test through the PLIC:

```
G2D_TEST_BEGIN <n> id=47324420 hwcfg=... core_hz=...
SCENE 0 clears_0
rc init 0
...
crc 0 1a2b3c4d
rc close 0
SCENE_END 0 ret=0 busy=<PERF_BUSY_CYCLES> draws=<PERF_DRAWS>
...
LVGL widgets_vg16_640x480_f2 kicks=31 busy=... err=00000000 pages=151 bytes=614400 crc=73fa47bc
VGA_ON addr=... w=640 h=480 crc=73fa47bc hold_ms=5000
IRQ_TEST plic=1 claim=58 ok=1
LAT_PROBE d=0 busy=<min>/<max> n=8
SWEEP 8 ... (LAT_PROBE, the scenes and the LVGL frame again with 8 extra DRAM cycles) ... SWEEP_END 8
... (16, 32, 64)
G2D_TEST_END fails=0
VGA_FINAL on
```

Pass criterion (bit-exact, docs/PROJECT_RULES.md rule 7): every `rc` line in order and every `crc` line equal to the software
backend's, `IRQ_TEST ... ok=1`, `G2D_TEST_END fails=0`. `busy` gives the GPU cycles per scene on real DDR3.

## 5. Results

| Test | Result |
|---|---|
| FPG-01 G2D out of context, xc7k325t-2, 50 MHz (`make fpga-ooc`) | met, WNS +5.24 ns (about 68 MHz possible); 36 381 LUT (17.9 %), 23 142 FF (5.7 %), 59 BRAM tiles (13.3 %: 41 RAMB36 + 36 RAMB18), 41 DSP (4.9 %), 1064 LUTRAM. Worst path: source-cache tag stage → `g2d_pix` in-flight counter, 20 logic levels, 84 % routing (Q-RTL-6) |
| FPG-02 Cheshire + G2D bitstream (`make fpga`, 2026-10-18) | all constraints met, WNS +0.015 ns / WHS +0.051 ns at 50 MHz; the worst paths are in Cheshire (LLC atomics adapter), none of the 30 worst in G2D. Device: 178 666 LUT (87.7 %), 134 883 FF (33.1 %), 204.5 BRAM tiles (46 %), 69 DSP; G2D + bridge 40 152 LUT, 25 900 FF, 41 RAMB36 + 36 RAMB18, 42 DSP; Cheshire 124 238 LUT; DRAM wrapper 13 223 LUT. ~1 h 20 min on 8 threads |
| FPG-03 SoC simulation (Verilator, 1 scene per kind, zero-latency DRAM model) | all 6 kinds bit-exact (every return code, every buffer CRC-32 = software backend), `IRQ_TEST plic=1 claim=58 ok=1`, `G2D_TEST_END fails=0`. GPU busy cycles: clears 10 671 (9 draws), blits 16 689 (4), paths 2 527 (3), patterns 87 958 (14), converted 45 735 (16), strokes 37 359 (5) |
| FPG-04 board run (single clock, 2026-10-19, user's Genesys2) | **PASS**: 12 / 12 scenes bit-exact (every `rc`, every CRC-32 = software backend), LVGL widgets frame CRC 73fa47bc (31 kicks, 767 840 busy cycles = 15.4 ms at 50 MHz), VGA_ON frame-buffer CRC 73fa47bc, `IRQ_TEST plic=1 claim=58 ok=1`, DRAM-delay sweep 0 / 8 / 16 / 32 / 64 bit-exact at every step, `G2D_TEST_END fails=0`, `core_hz=49980000`. Loaded with OpenOCD only (`openocd_g2d.tcl`, `load_verified`). Latency probe 33 + N busy cycles (N = 64: 97 / 111, one outlier). Busy cycles at N = 0: clears 319, blits 1 366, paths 1 742, patterns 4 234, converted 4 977, strokes 7 478 (scenes `_0`); +8 .. +47 per cycle of extra latency (LVGL +666). The SoC sim (FPG-03 / 06 / 07) is 1.4× (paths) to 33× (clears) slower for the same scenes and 3.3× for LVGL although its round trip is shorter (22): the sim's memory path, not G2D, limits it (Q-FPGA-4). Logs: user's `g2d_chs_results.zip` (uart.log, check.md) |
| FPG-06 DRAM-latency prediction (v1.2, SoC sim) | Cheshire's `USE_RAM_DELAY` stage (`axi_fifo_delay_dyn` behind the LLC, cfg registers at `0x4300_0000`) in the testbench as on the board; `scripts/socsim_sweep.py`: 6 kinds × N = 0/10/20/40 extra AR/AW cycles, 24 / 24 bit-exact; +2 .. +17 busy cycles per cycle of latency (docs/STATUS.md v1.2). Latency probe (NOP-only kick) 22 + N: the LLC is all scratchpad after Cheshire's boot ROM, so every GPU access goes to DRAM |
| FPG-07 LVGL frame + VGA (v1.2, SoC sim) | widgets RGB565 640×480 recorded by the host demo linked at `0x9000_0000` (`make lvgl-chs`, `scripts/fpga_lvgl.py`), replayed in place: CRC 73fa47bc = software backend, 2 549 646 busy cycles. VGA (`axi_vga`, 640×480 at SoC / 2, VESA porches, negative syncs): pin capture sync / blanking clean, pixels bit-exact with a 4-pixel stream offset (`axi_vga` starts visible before its fetcher has data and never re-aligns; `scripts/vga_check.py` reports the offset). Trimmed Cheshire (`FPGA_TRIM=1`): same results, PLIC source 58 |
| FPG-05 async build (v1.1, 2026-10-18) | G2D OOC at 100 MHz: met, WNS +0.334 ns (worst: source-cache tag stage, 16 levels). Cheshire + G2D with the G2D core at 100 MHz (`make fpga FPGA_ASYNC=1`): all constraints met, WNS +0.012 ns (Cheshire, SD-card output) / WHS +0.051 ns; worst G2D path +0.041 ns (source cache → `g2d_xform` divider enable, 100 MHz); 180 035 LUT (88.3 %), G2D + bridge 40 440 LUT. CDC constraints (`fpga/genesys2/g2d_cdc.tcl`): 20 gray buses with max delay + bus skew (all met), 10 storage arrays, reset synchronizer, interrupt. `report_cdc`: the only critical items are reads of the CDC FIFO storage (Vivado cannot recognise a FIFO it did not generate; safe by the gray-pointer protocol, arch.md §2.1) and the core reset synchronizer's clear (SoC reset AND clock-wizard lock, assertion only). SoC simulation `--async` (100 MHz core clock at an arbitrary phase): all 6 kinds bit-exact, IRQ ok; busy cycles ≈ 2× the 50 MHz ones (same wall time: the scenes are limited by the 50 MHz memory path) |
| FPG-08 v1.5 (2026-10-24, OOC + SoC sim) | G2D OOC 50 MHz: met, WNS +5.441 ns; 43 607 LUT, 28 544 FF, 59 BRAM tiles, 64 DSP (v1.4: 41 301 / 26 272 / 59 / 48). SoC sim kinds paths / patterns / radial / imgpaths bit-exact, IRQ ok, `fails=0` (busy 1 415 / 103 148 / 27 601 / 29 413; `build/fpga/socsim_kinds15`). No bitstream (v1.7) |
| FPG-09 v1.6 (2026-10-24, OOC + SoC sim) | G2D OOC 50 MHz: met, WNS +1.972 ns (worst: source format → blend S0 stencil terms, 43 levels, OPEN_QUESTIONS Q-RTL-10); 50 640 LUT, 29 623 FF, 60 BRAM tiles, 74 DSP (v1.5: 43 607 / 28 544 / 59 / 64). SoC sim kinds v16 / patterns / blits / converted bit-exact, IRQ ok, `fails=0` (busy 6 378 / 103 148 / 17 018 / 34 276; `build/fpga/socsim_kinds16`). No bitstream (v1.7) |
| FPG-10 v1.6b (2026-10-26, OOC + SoC sim) | G2D OOC 50 MHz: met, WNS +0.862 ns (worst path unchanged: blend S0 stencil terms, 45 levels, T-1); 53 313 LUT, 30 913 FF, 62.5 BRAM tiles, 138 DSP (v1.6: 50 640 / 29 623 / 60 / 74; texel unit 3 379 LUT / 64 DSP with YUV conversion and colour matrix, merge 1 208, sequencer 160). SoC sim kinds v16b / v16 / blits / converted bit-exact, IRQ ok, `fails=0` (busy 11 192 / 6 420 / 18 008 / 34 283; `build/fpga/socsim_kinds16b`). No bitstream (v1.7) |
| FPG-11 v1.7 (2026-10-27, trimmed bitstreams + SoC sim) | G2D OOC 100 MHz met (+0.367 ns, fmax ≈ 114 MHz), 54 237 LUT, 138 DSP. Bitstreams with the trimmed Cheshire (no USB, iDMA, bus-error units, I2C; VGA kept), every G2D unit: single clock `g2d_chs_trim.genesys2.bit` 168 170 LUT (82.5 %), 50 418 slices (99.0 %), WNS +0.177 ns; async (core 100 MHz) `g2d_chs_async_trim.genesys2.bit` 169 619 LUT (83.2 %), 49 833 slices (97.8 %), WNS +0.062 ns. Package: `make fpga-package FPGA_TRIM=1` (run_board.sh picks the `_trim` names). SoC sim (trimmed) 11 kinds × DRAM delay 0 / 40 bit-exact (`build/fpga/socsim_kinds17`). Board run: by the user (milestone B) |
| FPG-12 DOOM (2026-10-28, SoC sim + bitstream) | Bitstream `g2d_chs_trim_doom.genesys2.bit` (trimmed Cheshire + full G2D + chs_ps2 on the USB HID pins AD23 / AE20): WNS +0.177 ns, 170 364 LUT (83.6 %), 49 790 slices (97.7 %), chs_ps2 96 LUT. SoC simulation (`build/fpga/socsim/run_doom`, Freedoom 0.13.0 WAD preloaded at 0xA000_0000, 8.3 s SoC time, ~10 h wall): boot ROM leaves the LLC all scratchpad (0xff), DOOM switches it to cache; DOOM boots, the PS/2 model's Esc and Up keys reach the game (`DOOM_KEY +1b / -1b / +ad / -ad`; Esc opens the menu); frame 3 presented by the G2D RTL = CPU present (`DOOM_CHECK ... cpu_present=MATCH`, crc 279c8f89); VGA pins: no sync / blanking errors, pixels = the frame buffer with axi_vga's known 4-pixel stream offset (FPG-07). Image: docs/doom/socsim_vga_menu.png. Board run: by the user |
| FPG-13 DOOM SD-card boot (2026-11-05; board: works, 2026-11-05) | Cheshire boot mode 1 (SW1..SW0 = 01): the boot ROM loads the G2D loader (sw/apps/doom/doom_loader.c, 7.6 KB, Cheshire zero-stage-loader GPT type) into the LLC scratchpad; it reads the program partition (Cheshire firmware type) to 0x8000_0000 and the WAD partition (type D00D0AD1-6B2D-4A5E-9F3A-5C0FFEE1D00D) to 0xA000_0000 with the boot ROM's CRC-checked SPI SD routine, flushes the D-cache and starts DOOM. Image: `make doom-sdimage` -> build/fpga/doom/doom_sd.img (30 MB); `sudo dd if=doom_sd.img of=/dev/sdX bs=1M conv=fsync && sudo sgdisk -e /dev/sdX`. No new bitstream (SPI host and SD pins are in every build). GCC 13.2 rejects Cheshire's gprw() gp clobber (Cheshire's zsl.c too): the loader swaps gp itself, -mno-relax |
| FPG-14 DOOM board run (2026-11-04 .. 11-06, user) | DOOM playable on the Genesys2: SD-card boot, USB keyboard through chs_ps2, 10–13 fps, VGA picture presented by the G2D. Cheshire trim mask 7 (USB + iDMA + bus-error units off; the full trim hangs, Q-FPGA-9), G2D FEATURES = 0 (path rendering wrong on the board, Q-FPGA-10; DOOM uses only clears and blits). VGA: picture slid with axi_vga's 16-beat buffer and with 256 beats (underruns, never re-aligned) |
| FPG-15 axi_vga fork (2026-11-05 / 06) | `fpga/cheshire/axi_vga_g2d` (Cheshire's axi_vga v0.1.3, SHL-0.51, changes marked "G2D") replaces Cheshire's copy in `build_fpga.sh` and `socsim.py`: (1) per-frame re-sync at vsync (HOLD new bursts → DRAIN the fetcher → RUN from START_ADDR), (2) prefetch: a burst is requested whenever the buffer has room for a whole burst, burst capped at BufferDepth / 4 (64 beats with the 256-beat buffer; the SD image's BURST_LEN 255 is capped in hardware). Bench `dv/tests/test_axi_vga.py` 8 / 8. Board: slide gone after (1); after (2) occasional one-frame blinks remain, playable, fewer with a smaller DOOM screen (Q-FPGA-11). Best bitstream (tm7, FEATURES = 0, chs_ps2, fork): WNS +0.062 / WHS +0.050 ns, 166 794 LUT (81.8 %), 49 334 slices (96.8 %), 133 DSP, archived `build/archive/doom_tm7_prefetch_f0_2026-11-06.tar.gz` (bitstream, reports, SD image). Build: `G2D_FPGA_TRIM_MASK=7 G2D_FPGA_FEATURES=000000 make fpga FPGA_DOOM=1` (~2 h on a loaded server) |
| FPG-16 v1.8 board-proven (2026-11-11 / 12, SoC sim) | Simulators rebuilt with the v1.8 RTL (debug registers `DBG_STATUS.RAM_COLL` / `WR_SIG`, axi_vga fork with real bursts and a 1023-beat block-RAM buffer, Cheshire patch parameter `LlcCachedRegionEnd`, testbench DRAM refresh / jitter stage `tb_dram_stall.sv`). (1) All 11 scene kinds (`scripts/socsim_sweep.py --delays 0 --ddr`, `build/fpga/socsim_v18`): bit-exact, `coll=00` in every scene, `fails=0`. (2) The same with the LLC switched to cache mode on the DOOM build (`G2D_LLC_CACHE=1 ... --ps2`, cached region [0x8000_0000, 0x8100_0000), G2D pool at 0x8100_0000 outside it; `build/fpga/socsim_v18_llc`): 11 / 11 bit-exact, `coll=00`, every `WR_SIG` equal to (1). (3) LVGL widgets frame + VGA (`build/fpga/socsim/run_v18vga2`): CRC 73fa47bc, IRQ ok, VGA pin capture bit-exact with **stream offset 0** (FPG-07: 4 pixels with Cheshire's axi_vga). Scene `vectors_0` needs 1.79 s SoC time (~2.4 h wall): the CPU builds round-cap discs under a perspective matrix (FEATURES_LATER D-5), not a hang. Board run: by the user |
| FPG-17 Cheshire on the G2D AXI4 adapter (v1.9, 2026-11-12, SoC sim) | `g2d_cheshire.sv`: the PULP `axi_to_axi_lite` + `axi_lite_dw_converter` pair replaced by `g2d_top_axi` (the G2D `g2d_axi2axil`, 64-bit AXI4 slave, bursts split into 32-bit accesses) behind PULP `axi_atop_filter`. SoC sim with the DRAM refresh / jitter stage (`--ddr`): clears, blits, paths, patterns `TB_RESULT PASS`, fpga_check PASS, `coll=00`, write signatures equal to `fpga/ref/wsig_k1_seed1.txt` (908b6a60, 30baed2f, 8cd500d3, 466315f3); busy cycles within 14 cycles of v1.8 (CSR access timing). `ID` / `HWCFG` read through the adapter. Out: `build/fpga/socsim_v19`. Bitstream and board: by the user (area / timing of the adapter not measured here) |

Findings in the async build (v1.1): (1) XDC files do not accept `foreach`, so the CDC constraints are an unmanaged
Tcl constraint file that stops with an error if a FIFO is not found (instance names carry the generate block,
`g_mem_cdc.u_mem_cdc`); (2) an extra MMCM for the G2D clock made the clock placer fail (Place 30-575, an IOB driving
two MMCMs directly): in the passing single-clock build `opt_design` had inserted a BUFG on the 200 MHz board clock, in
the async runs it did not. The async top therefore uses no extra MMCM (`g2d_clkwiz`: Cheshire's clock wizard settings
with the unused fourth output at 100 MHz, `fpga/genesys2/g2d_clkwiz_ip.tcl`) and instantiates that board-clock BUFG
explicitly (`gen_top.py --async`).

Findings while bringing the SoC simulation up (all fixed in the scripts / test program, none in the IP):
`clint_get_core_freq` measures 49.995 MHz, and Cheshire's `uart_init` truncates the divisor (the test program
rounds the frequency to 100 kHz); the APB UART does not run with divisor 1; CVA6's Verilator mock tracer writes
every instruction to `trace_hart_0.dasm` (sent to /dev/null by `socsim.py`).
