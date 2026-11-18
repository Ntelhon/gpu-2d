# Third-party material

The project's own work is under `Apache-2.0 WITH SHL-2.1` (Solderpad Hardware License v2.1; hardware descriptions)
and `Apache-2.0` (everything else): `LICENSE`. Licence texts: `LICENSES/`.

Files in this repository that are not the project's own work, with their licences. Material fetched at build time
(not stored here) is listed separately.

## Stored in the repository

| Path | Origin | Licence |
|---|---|---|
| `sw/include/vg_lite.h` | VGLite API header, Vivante Corporation (version in `spec/vglite-reference.md`) | MIT (header comment) |
| `fpga/cheshire/cheshire-g2d.patch` | Changes to Cheshire (`hw/cheshire_soc.sv`, `hw/rv_plic.cfg.hjson`) | Cheshire's licence, Solderpad Hardware License 0.51 |
| `fpga/cheshire/axi_vga_g2d/` | `axi_vga` v0.1.3 of the PULP platform (ETH Zurich and University of Bologna), modified for this project (changes marked "G2D") | Solderpad Hardware License 0.51, SPDX `SHL-0.51` (`fpga/cheshire/axi_vga_g2d/LICENSE`, `LICENSES/SHL-0.51.txt`) |
| `sw/apps/doom/` | Platform layer for doomgeneric, written for this project | GPL-2.0-or-later (SPDX headers), because it links with doomgeneric |

## Fetched at build time (not stored here)

| What | Script | Licence |
|---|---|---|
| Cheshire SoC and its dependencies (CVA6, PULP common cells, AXI, register interface, ...) | `fpga/scripts/setup_cheshire.sh` | Solderpad 0.51 / Apache-2.0 (per component) |
| LVGL v9.6.0 (incl. ThorVG) | `scripts/fetch_lvgl.sh` | MIT |
| doomgeneric | `scripts/fetch_doom.sh` | GPL-2.0 |
| Freedoom 0.13.0 | `scripts/fetch_doom.sh` | BSD-3-Clause |
| FreeRTOS Kernel V11.3.3 | `scripts/fetch_freertos.sh` | MIT |
| Linux kernel v5.10.7 (+ cva6-sdk patches) and v6.12.117 (headers for building `sw/linux`) | not fetched by the repo; `docs/TOOLS.md` | GPL-2.0 (with syscall note for uapi) |
| IEEE 1685-2014 IP-XACT schema (Accellera files, EDA Alliance mirror) | `scripts/fetch_ipxact_schema.sh` | Accellera notice in the files; `LICENSE.md` of the mirror (fetched with the schema) |
| Python packages (cocotb, cocotbext-axi, pybind11, numpy, Pillow, skia-python, matplotlib, ...) | pip | their own licences |
