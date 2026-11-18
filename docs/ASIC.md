# ASIC reference layout: G2D on IHP SG13G2

`g2d_top` (default parameters: DATA_W 64, AXI4-Lite configuration port, single clock, every feature) through the open
RTL-to-GDS flow LibreLane on the IHP SG13G2 130 nm open PDK. Goal (decision 2026-11-12): the highest clock
frequency this technology reaches, no scaled estimate for the 600 MHz target; then (2026-11-13) a 50 MHz clock
constraint and a finished GDS rather than the best one; then (2026-11-16) the layout below accepted as the reference.

## Flow

| Step | Tool | Notes |
|---|---|---|
| SystemVerilog → Verilog | sv2v 0.0.13.1 (`-DSYNTHESIS --top=g2d_top`) | Yosys 0.62 has no frontend for this RTL; the yosys-slang plugin is not packaged |
| Memories | `rtl/ram/ihp` | the drop-in RAM variant on the PDK's SRAM macros (below) |
| Synthesis | Yosys 0.62 via LibreLane 3.0.3 | `make asic-synth` (`scripts/asic_ihp.py synth`) |
| Floorplan, macro placement | `scripts/asic_ihp.py pnr` | macro blocks in a grid with standard-cell strips and bands around each block ("Floorplan" below) |
| PDN | LibreLane default + `syn/ihp/pdn_cfg.tcl` | the SRAMs bring power out as vertical Metal4 stripes; horizontal Metal5 straps over each macro tie them to the TopMetal1 grid |
| Place, CTS, route | OpenROAD | `make asic-pnr`; `asic_ihp.py resume` / `prune` for restarts and disk |
| Signoff timing | OpenSTA, one corner per run | `asic_ihp.py sta` (`syn/ihp/sta_signoff.tcl`) |
| DRC, LVS | KLayout, Magic, Netgen | not run to completion on the build machine ("Known defects") |
| Gate-level simulation | Verilator, `scripts/gls_netlist.py`, `dv/tests/test_gls_ihp.py` | `make asic-gls` |

Constraints: `syn/constraints/g2d_top.sdc` with `G2D_CORE_PERIOD` = the run's clock period and its defaults (60 % of
a period outside G2D on every input and output, 50 ps uncertainty). Corners: the PDK's LibreLane set, nominal RC:
slow 1.08 V 125 °C, typical 1.20 V 25 °C, fast 1.32 V −40 °C (the SRAM fast view is −55 °C, the closest provided).

## sv2v coding rule (v2.1)

The ASIC flow reads the RTL through sv2v. sv2v 0.0.13.1 rewrites an index into a packed array as an arithmetic
offset (`index * element_width`), and Verilog then sizes a bitwise NOT inside that expression to 32 bits: `a[~b]`
with a 1-bit `b` becomes an out-of-range offset. Rule: no bitwise NOT (or other width-sensitive operator) of a narrow
signal inside an index or part-select; give the value its own signal (`g2d_raster`: `nxt_par`). The whole design
converted by sv2v runs the RTL benches in `make test` (`dv/tests/test_sv2v_equiv.py`), so a new case shows up there.

## Memories on IHP SRAM macros

All 48 memories of the default `g2d_top` are one-read / one-write RAMs (`g2d_ram_1r1w`, 360 Kbit). `rtl/ram/ihp`
maps each byte-enable lane onto IHP two-port macros of the smallest depth that holds it (64, 256, 512, 1024 words):
32-bit tiles, the last tile the narrowest macro that holds the rest; port A reads, port B writes, the lane enable
drives the write enable (no bit masks), `DLY` tied to 1 as the datasheets require, BIST inputs tied off. `g2d_ram_sp`
uses the single-port macros the same way (not used by the default build). Memories under 1 Kbit or deeper than the
largest macro are flops.

Verification: `test_ram_variants.py::test_ram_variant[ihp]` (the top-level subset of the other RAM variants, on the
PDK's SRAM simulation models) and `test_ihp_ram_sizes` (8 sizes covering every tile path, random reads and lane
writes against a reference); a mutant that ignores the lane enable is caught.

## Results

Reference run `p20e` (2026-11-16), accepted by the user as the v2.0 reference layout **with the defects listed
below** (defect 0 was found after that acceptance, while finalizing): it is a demonstration layout, not a tape-out candidate. The GDS (607 MB, KLayout stream-out) is a build
output (`build/asic/ihp/final/g2d_top.gds`), not stored in the repository.

Command: `scripts/asic_ihp.py synth --tag s10 --period 10`, then `pnr --tag p20e --from-synth s10 --period 20
--util 30 --density 35 --io-frac 0.3 --hold-margin 0.3 --macro-hold 0.5 --setup-margin 2.0` with the STA-mid,
STA-pre, STA-post, IR-drop and design-LEF antenna steps skipped, detailed routing on 3 threads, then
`sta --tag p20e --period 20`.

### Layout

| Item | Value |
|---|---|
| Technology | IHP SG13G2 130 nm, 5 thin metals + 2 thick, `sg13g2_stdcell`, 131 two-port SRAM macros |
| Clock constraint | 20 ns (50 MHz) (decision 2026-11-13; the first runs used 10 ns) |
| Die | 7270 x 6580 um = 47.8 mm2 |
| Standard cells | 638 371 cells, 9.28 mm2: 57 464 flip-flops (2.8 mm2), 119 177 timing-repair buffers (about 62 000 of them hold buffers), tie cells, antenna diodes |
| SRAM macros | 131 (7.78 mm2): 70 x 64x32, 32 x 256x8, 10 x 512x32, 9 x 64x22, 8 x 256x32, 1 x 512x16, 1 x 1024x16 |
| Standard-cell density | 36 % of the free area (placement target 35 %) |
| Logic size after synthesis | 506 521 cells, 7.47 mm2: about 1.03 M NAND2 equivalents (7.26 um2 each) |
| Wire length | 42.7 m (Metal2 .. TopMetal2) |
| Fill | 2.57 M fill / decap cells |

### Signoff timing (routed, extracted, `syn/constraints/g2d_top.sdc`, 20 ns clock)

One OpenSTA run per corner (`syn/ihp/sta_signoff.tcl`), nominal RC extraction (OpenRCX). "Core" = flip-flop /
SRAM to flip-flop / SRAM paths; "all" adds the I/O paths with the template's 60 % external budget (the layout was
built with a 30 % budget, so the "all" figures are pessimistic for it).

| Corner | Core setup slack | Core fmax | All-paths fmax | Hold | Reset recovery |
|---|---|---|---|---|---|
| Typical 1.20 V 25 C | +0.16 ns | **50.4 MHz** | 36.8 MHz | 23 endpoints violated, worst -0.32 ns | +10.3 ns |
| Slow 1.08 V 125 C | -11.96 ns | **31.3 MHz** | 31.3 MHz | 29 endpoints violated, worst -0.34 ns | +6.6 ns |
| Fast 1.32 V -40 C | +7.21 ns | **78.2 MHz** | 38.3 MHz | worst -0.31 ns | +9.7 ns |

Flip-flop (`sg13g2_dfrbpq_1`): clock-to-Q 0.18-0.21 / 0.29-0.34 / 0.12-0.15 ns and setup about 0.12 / 0.20 /
0.08 ns (typical / slow / fast); clock insertion delay 2.6-3.1 / 3.7-4.3 / 1.9-2.3 ns.

Critical path (all corners): `u_pix.u_mask.u_rd.u_sq` (mask reader skid queue) to `u_pix.u_ramp`, `u_mask.u_rd.u_uq`
and the cache request queue `u_cache.g_mq[0].u_mq`: a ready / valid back-pressure chain across units, 47 cells, 13
of them fan-out buffers (`buf_1` / `buf_2` driving long wires at 1.0-2.4 ns per stage in the slow corner),
32 ns from flip-flop to flip-flop in the slow corner.

### Known defects of the reference layout

| # | Defect | Effect | Cause | Fix |
|---|---|---|---|---|
| 0 | **The netlist is not equivalent to the RTL in path draws** (found 2026-11-16 by the gate-level simulation): the random system stream `sys01` writes wrong pixels on the routed netlist | The path rasterizer of this layout produces no coverage; fills, blits, soft reset and the random fill / blit streams are bit-exact | sv2v 0.0.13.1 converts the index `na_q[~cur_q]` (1-bit `cur_q`, 2-entry packed array) of `g2d_raster` into `~cur_q * (EI + 1)`; Verilog widens `cur_q` to 32 bits before the NOT, so the index is 0xFFFFFFFE / 0xFFFFFFFF and the active-edge count reads out of range (found by comparing waveforms of the RTL and the sv2v module on the raster unit bench: first divergence `na_q`). The FPGA flow reads SystemVerilog directly and is not affected | **Fixed in the RTL in v2.1** (`nxt_par`, a 1-bit signal for the other parity; `dv/tests/test_sv2v_equiv.py`: the sv2v output of `g2d_raster` and of the whole `g2d_top` pass the RTL benches, in `make test`). The p20e layout keeps the defect until it is routed again |
| 1 | **Hold violations**: 29 endpoints (slow), 23 (typical), worst -0.34 ns. All are SRAM data / address pins except two flip-flops at -0.04 ns (typical) | Silicon could fail at any clock frequency: not manufacturable as is | The SRAM macros need 1.05 ns hold; the post-CTS repair works on estimated wires and was about 0.65 ns optimistic at these pins (margins used: 0.3 ns everywhere + 0.5 ns at SRAM clock pins) | One more run with `--macro-hold 1.3` (only SRAM inputs get more buffers), or a post-route hold ECO on a machine with more memory |
| 2 | **Slow corner misses 50 MHz** (31.3 MHz) | The 50 MHz constraint is met only in the typical and fast corners | Long ready / valid chains with weak fan-out buffers; timing repair ran before routing, on estimated wires | RTL: register the back-pressure path between the mask reader, ramp and cache queues (spill registers); flow: post-global-route timing repair, stronger buffer cells for high fan-out nets |
| 3 | **1637 antenna-rule nets** | Yield / reliability risk in manufacturing | Antenna repair ran at the global-routing stage only; the post-route repair needs the global and the detailed router in memory together and was killed at 15 GB | Post-route antenna repair on a larger machine |
| 4 | **No DRC signoff**: detailed-routing DRC is 0, but the KLayout DRC deck was killed (9 GB+ at the derivation stage) and Magic DRC was not run | Layout rules beyond routing (density, fill, macro abutment) unchecked | Memory (15 GB) and disk | Run `KLayout.DRC` / `Magic.DRC` on a larger machine |
| 5 | **No LVS**, no GDS XOR, no IR-drop report | Layout-versus-netlist equivalence and the power grid are unchecked (the SRAM power hook-up over Metal5 straps is new in this flow) | Skipped: memory, disk, time | `Netgen.LVS`, `KLayout.XOR`, `OpenROAD.IRDropReport` on a larger machine |
| 6 | **No clock gating, no scan insertion, SRAM BIST pins tied off** | Power and production test not addressed | Out of scope of the open flow / v2.0 | Integrator's DFT / power flow |

### Floorplan

The SRAM macros decide whether this design routes. Each macro has all its signal pins on the bottom edge
(Metal2), so its roughly 80 nets leave through the channel under it. What was tried (`scripts/asic_ihp.py
place_macros`, logs under `build/asic/ihp`):

| Run | Floorplan | Result |
|---|---|---|
| p10, p10b | macro columns side by side at the die's left and right, 15 / 45 um between stacked macros, density 60 % | global route did not converge / 51 900 overflow; detailed routing stopped early (by mistake: it converges from several 100 k violations) |
| p20 | same, density 35 %, 20 ns | 27 700 overflow; detailed routing stuck at 17 000 shorts: the outer column's nets cannot cross the inner columns |
| p20b | die-high macro columns with logic strips between | 14 800 overflow; stuck at 12 000 shorts on two lines at the edges of the texture-cache column (2048 read-data wires running along it) |
| p20c | **blocks of at most 1900 um (one cache bank of 16 macros per block) in three tiers, logic strips and bands around every block** | 162 overflow; detailed routing clean (0 violations) in 3 h 20; hold violated (-0.5 ns, margin 0.1) |
| p20d | p20c + 0.8 ns hold margin on every path | 134 000 hold buffers: detailed routing out of memory |
| p20e | p20c + hold margin 0.3 ns, + 0.5 ns at the SRAM pins, 3 routing threads | **reference**: routed clean, hold -0.34 ns at 29 SRAM endpoints |

### Run time and memory (4 CPUs, 15 GB, run p20e)

| Step | Time | Peak memory |
|---|---|---|
| Synthesis (per module, flattened afterwards; a flat run needed 7 GB and was killed) | 55 min | 6.7 GB |
| Floorplan, power grid, placement | 25 min | 4.7 GB |
| Design repair, clock tree | 17 min | 4.6 GB |
| Timing repair after the clock tree | 36 min | 5.7 GB |
| Global routing | 14 min | 6.3 GB |
| Antenna check and repair (diode insertion) | 1 h 55 | 8.4 GB |
| Detailed routing, 3 threads (4 threads: 3 h 20 on p20c) | 5 h 06 | 12.9 GB |
| Post-route checks, fill, RCX, KLayout stream-out | 25 min | 4 GB (Magic stream-out: 28 min, 7 GB) |
| Signoff STA, three corners in turn | 9 min | about 4 GB |
| Not possible here | LibreLane's multi-corner STA steps (12.6 GB each), post-route antenna repair, KLayout DRC | over 15 GB |

A full place-and-route attempt is about 9.5 hours. The container restarted three times during the work (about
every 10-14 hours); `asic_ihp.py resume` continues from the last finished step and `prune` keeps the disk usable.

### How to improve it (in order of value)

0. Route a new layout from the v2.1 RTL (defect 0 is fixed in the RTL; the p20e netlist still has it).
1. Close hold: rerun with `--macro-hold 1.3` (about 10 h here), then re-check with `asic_ihp.py sta`.
2. Run the missing signoff (DRC, LVS, antenna repair, IR drop) on a machine with 32 GB or more.
3. Slow-corner speed: break the ready / valid chain of the critical path in RTL, then let timing repair work
   after global routing (`RUN_POST_GRT_RESIZER_TIMING`) with real wire estimates.
4. Area: 1.03 M gate equivalents is several times the size of typical MCU-class 2D GPUs. Candidates: the trimmed
   `FEATURES` build as a second reference, the texture cache's 64 small SRAMs (2 Kbit used per 64x32 macro), the
   29-stage perspective divider, the 119 k repair buffers (fewer with a better floorplan and stronger cells).
5. Harden the large blocks (rasterizer, texture cache, dividers) separately and assemble them: each stage then
   fits a small machine and the top level routes in minutes.
6. Power and test: clock gating at the unit level, scan insertion, memory BIST on the tied-off macro pins.

## Gate-level simulation

The post-route netlist runs the same test subset as the RAM variants (four top-level tests and the random system
streams, bit-exact against the golden model): the PDK's standard-cell Verilog uses UDPs, which Verilator does not
support, so `scripts/gls_netlist.py` rebuilds the cells from their Liberty functions in Yosys and writes plain
Verilog; the SRAM macros use the PDK's models. Zero-delay simulation: it checks the logic of synthesis, buffering,
resizing and CTS; timing is signed off by STA. It stands in for formal equivalence checking, which the open flow
does not provide (CLOSURE_V2 Q-TOOL-1).

Result on the reference netlist (run p20e, 638 k cells, 2026-11-16; `make asic-gls`):

| Test | Result |
|---|---|
| `test_gls_top`: fills in all formats, soft reset, random streams (3 of the 4 top-level tests of the RAM-variant subset; `mem_qos_system` reads internal signals and cannot run on a flat netlist) | pass, bit-exact against the golden model |
| `test_gls_sys`: random system streams (`sys01`: paths, gradients, blits) | **fails** on p20e: memory mismatch in a path draw (defect 0 above); strict expected failure |
| `test_sv2v_equiv`: the sv2v output alone on `sys01` (PDK SRAM models) | failed with the same bytes before the v2.1 fix; passes since |

The bench runs in black-box mode on a netlist (`dv/common/g2d_env.py`): all checks through the ports stay (memory
against the model, PERF draw / pixel / beat / stall counters, write signature, RAM-collision flags); the white-box
ones (state view, BUSY / DRAIN references, error coverage) are dropped. Building the Verilator model of the netlist
takes about 1.5 hours here (544 C++ files); the tests then run in about 6 minutes.
