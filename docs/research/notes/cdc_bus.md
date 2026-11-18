# CDC of the AXI4 / AXI4-Lite ports and platform-neutral bus adapters (G2D Phase 6b / F-6)

Scope: concrete design guidance for `CFG_ASYNC` / `MEM_ASYNC` (spec/arch.md §2.1, §2.2, §4), the F-6 adapter family
(docs/FEATURES_LATER.md), PHASES.md row 6b, and OPEN_QUESTIONS Q-RTL-4 / Q-RTL-9. No vendor GPU
material was read. Sources are public docs plus open-source RTL (Solderpad-licensed PULP sources, read
locally from the Cheshire Bender checkout at `~/tools/src/cheshire/.bender/git/checkouts/`; versions noted).
Labels: **[E]** = evidence from the cited source, **[I]** = my inference. Research date 2026-10-18.

Source versions read locally (also on GitHub):
- pulp-platform/common_cells **v1.38.0** (`cdc_fifo_gray.sv`, `cdc_fifo_gray_clearable.sv`, `cdc_reset_ctrlr.sv`, `sync.sv`)
- pulp-platform/axi **v0.39.8** (`axi_cdc.sv`, `axi_cdc_src/dst.sv`, `axi_isolate.sv`, `axi_intf.sv`)
- pulp-platform/register_interface **v0.4.5** (`axi_lite_to_reg`, `apb_to_reg`, `reg_cdc`, `reg_to_tlul`, ...)
- pulp-platform/cheshire **v0.3.1-49-g6c6dd5e** (`target/xilinx/constraints/*.xdc`)
- Local tools: Verilator 5.052 (has `--timing`, `--x-assign`, `--x-initial`, `--x-initial-edge`), cocotbext-axi 0.1.28
  (has `apb.py`, AXI, AXI-Lite, AXI-Stream models). **Not installed**: yosys, sby, z3/boolector/bitwuzla, verible, slang.

---

## Q1. Async FIFO best practice (gray pointers, full/empty, depth vs throughput, synchronizer stages, MTBF, Vivado constraints)

### Takeaway
Use the Cummings "Style #1" FIFO (binary+gray pointers with one extra wrap bit, synchronize-then-compare, pessimistic
full/empty from the local pointer vs the synchronized remote pointer, power-of-2 depth, no logic between the gray
register and the first sync flop); this is exactly what PULP `cdc_fifo_gray` and OpenTitan `prim_fifo_async` do.
Constrain every gray-pointer and data path with `set_max_delay -datapath_only` ≤ min(T_src, T_dst) (plus optional
`set_bus_skew`) and false-path hold; depth must cover the pointer round-trip latency, not the AXI outstanding count.

### Cited Findings
- [E] Cummings' "Simulation and Synthesis Techniques for Asynchronous FIFO Design" (SNUG San Jose 2002, rev 1.3 2005) is
  the "recommended" gray-code synchronize-then-compare design; the companion "with Asynchronous Pointer Comparisons" paper
  (with Peter Alfke) is Style #2. The sunburst-design.com URL now 301-redirects to paradigm-works.com, which requires a
  login, so the full text was not re-read this session — [Paradigm Works library index](https://www.paradigm-works.com/papers/CummingsSNUG2002SJ_FIFO1.pdf); [search result summary](https://www.scribd.com/doc/85464597/FIFO)
- [E] PULP `cdc_fifo_gray` architecture: two separately clocked and reset halves; source increments write pointer, the
  "entire FIFO content is exposed over the `async_data` port", destination owns the read pointer and read mux;
  pointers are gray-coded and passed through "a classic multi-stage FF synchronizer"; not-full uses the local wptr vs the
  "pessimistic read pointer ... delayed at least two cycles"; not-empty uses the pessimistic wptr, so "the FIFO content
  does not need to be synchronized"; "the read select logic into the FIFO is completely clocked by the destination clock
  domain"; depth is 2**LOG_DEPTH, LOG_DEPTH ≥ 1 — [cdc_fifo_gray.sv v1.38.0](https://github.com/pulp-platform/common_cells/blob/v1.38.0/src/cdc_fifo_gray.sv)
- [E] PULP's required synthesis constraints (header of `cdc_fifo_gray`): `set_ungroup ... false`,
  `set_boundary_optimization ... false`, `set_max_delay min(T_src, T_dst) -through [async pins] -through [async pins]`,
  `set_false_path -hold` through the same pins; the module carries `(* no_ungroup *) (* no_boundary_optimization *)`
  and the async ports are tagged `(* async *)` so the constraint can find them — [cdc_fifo_gray.sv](https://github.com/pulp-platform/common_cells/blob/v1.38.0/src/cdc_fifo_gray.sv); [axi_cdc.sv](https://github.com/pulp-platform/axi/blob/v0.39.8/src/axi_cdc.sv)
- [E] PULP `sync.sv` (the synchronizer cell) marks its flops `(* dont_touch = "true" *)` and `(* async_reg = "true" *)` —
  [sync.sv](https://github.com/pulp-platform/common_cells/blob/v1.38.0/src/sync.sv)
- [E] OpenTitan `prim_fifo_async`: Depth must be a power of 2 ("Depth must be a power of 2 for the gray code pointers to
  work", `ParamCheckDepth_A`); depth 1 and 2 are supported (depth 2 uses a simplified gray conversion); pointers are one
  bit wider than the index; full = synchronized pointer with inverted MSB match, empty = exact match; two
  `prim_flop_2sync` instances; an extra registered copy of the synchronized rptr on the write clock; pointers reset
  (async, per side), storage not reset; assertions `GrayWptr_A` / `GrayRptr_A` check the gray pointer changes by at most
  one bit per clock; a TODO notes full/empty assertions are not yet written — [prim_fifo_async.sv](https://github.com/lowRISC/opentitan/blob/master/hw/ip/prim/rtl/prim_fifo_async.sv)
- [E] AMD UG949: a CDC bus "uses gray-coding (for example, FIFO)" ⇒ use a max-delay constraint; "It is usually sufficient
  to use the source clock period", and with a high period ratio "choosing the minimum of the source and destination clock
  periods"; "A clean asynchronous CDC path should not have any logic between the source and destination sequential
  cells"; `set_bus_skew` "prevents the receiving clock domain from latching multiple states of the bus" — [UG949 Constraints on Individual CDC Paths](https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Constraints-on-Individual-CDC-Paths)
- [E] UG903 worked example (5 ns src → 2.5 ns dst gray bus): `set_bus_skew -from [get_cells src_gray_ff_reg*] -to
  [get_cells {dest_graysync_ff_reg[0]*}] 2.500` (destination period) plus `set_max_delay -datapath_only` 5.000 (source
  period) "to ensure the source and destination registers are not placed too far apart" — [UG903 set_bus_skew Example Two](https://docs.amd.com/r/en-US/ug903-vivado-using-constraints/set_bus_skew-Example-Two)
- [E] UG835: `set_bus_skew` is not overridden by clock groups / max delay / false path and should be roughly the smaller
  of the two periods — [UG835 set_bus_skew](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/set_bus_skew) (via search summary)
- [E] Cheshire (Genesys2) constrains its own `axi_cdc` instances: hold false-path through `*async*` pins of every
  `axi_cdc_src` / `axi_cdc_dst`, `KEEP_HIERARCHY SOFT` on `sync` cells, and for the DRAM CDC
  `set_max_delay -datapath_only` (MIG period) from FIFO regs to `*i_sync/reg*/D` and to the dst spill register —
  [cheshire.xdc](https://github.com/pulp-platform/cheshire/blob/main/target/xilinx/constraints/cheshire.xdc); [genesys2.xdc](https://github.com/pulp-platform/cheshire/blob/main/target/xilinx/constraints/genesys2.xdc)
- [E] Xilinx `XPM_CDC_GRAY`: `DEST_SYNC_FF` range 2..10, **default 4**; "the binary value provided to the macro must only
  increment or decrement by one"; "the destination clock must sample the input data two or more times";
  `SIM_LOSSLESS_GRAY_CHK` enables a simulation check of the ±1 rule; run `report_cdc` to confirm structure recognition —
  [UG974 XPM_CDC_GRAY](https://docs.amd.com/r/en-US/ug974-vivado-ultrascale-libraries/XPM_CDC_GRAY)
- [E] Xilinx AXI Clock Converter (PG059): `ACLK_ASYNC` (auto, can be forced to 1), `ACLK_RATIO` for synchronous ratios,
  `SYNCHRONIZATION_STAGES` 2..8 (the clock-converter table says default 3, the interconnect MTBF page says default 2 —
  conflicting in the docs) — [PG059 AXI Clock Converter Parameters](https://docs.amd.com/r/en-US/pg059-axi-interconnect/AXI-Clock-Converter-Parameters); [PG059 CDC MTBF options](https://docs.amd.com/r/en-US/pg059-axi-interconnect/Clock-Domain-Crossing-MTBF-Options)
- [E] MTBF model: MTBF = e^(S/τ) / (T_W · F_C · F_D), S = resolution time allotted; an SoC with K synchronizers has 1/K
  the MTBF of one — [Beer/Ginosar, MTBF of multi-synchronizer SoCs](https://www.ee.technion.ac.il/~ran/papers/MTBFmultiSyncSoc.pdf); multistage synchronizers have an effective τ and are sensitive to voltage/temperature variability — [Variability in multistage synchronizers 2014](https://www.ee.technion.ac.il/~ran/papers/VariabilityInMultiStageSynchronizers-2014.pdf); [VT effects, IEEE TVLSI 2015](https://webee.technion.ac.il/~ran/papers/VT-var-effects-on-Sync-BeerGinosar2015.pdf)
- [E] ZipCPU formally verified a Cummings-style FIFO with SymbiYosys and reports no bug in Cummings' design; his own
  earlier FIFO failed the ordering "contract" (overwrote the tail on overrun) — [ZipCPU async FIFO](https://zipcpu.com/blog/2018/07/06/afifo.html)

### Inferences
- [I] **Depth vs throughput.** With N sync stages, a write becomes visible as not-empty after ≈ 1 (gray reg) + N dst
  cycles, and the freed slot becomes visible as not-full after ≈ 1 + N src cycles. For back-to-back streaming at equal
  clocks the FIFO must hold the round trip ≈ 2·(N+1)..2·(N+2) entries: depth 8 (LOG_DEPTH = 3) for N = 2, depth 8–16 for
  N = 3. PULP's `axi_cdc` default `LogDepth = 1` (depth 2) therefore throttles to well below one beat/cycle; this is fine
  for a CSR port, not for a pixel memory port.
- [I] **Depth is not the AXI outstanding count.** AXI flow control (valid/ready on each channel) means the CDC FIFO only
  buffers in-flight beats; outstanding-transaction limits stay in the core (`RD_OUTSTANDING`, `WR_OUTSTANDING`). Because
  G2D read clients are credit-based and RREADY is never stalled for long (arch.md §4.3 A1), the R FIFO drains at the
  core clock rate. Recommended G2D defaults: `MEM_CDC_LOG_DEPTH = 3` (8 entries) for AR/AW/B and R/W; raise R/W to 16 only
  if the clock ratio near 1:1 with 3-stage sync is shown to limit bandwidth in the bandwidth.md model. CFG side: depth 2
  (LOG_DEPTH 1) on all five channels is enough (single-outstanding CSR traffic).
- [I] **Slow side bounds bandwidth.** On the Genesys2, mem port = 50 MHz × 8 B = 400 MB/s regardless of FIFO depth; the
  async split lets the core run faster than 50 MHz but the memory throughput of spec/bandwidth.md must be recomputed for
  f_mem, not f_core.
- [I] **Data path through the FIFO storage** is not synchronized (it is stable before the synchronized pointer says so),
  but it is still a CDC path in STA: it needs the same `set_max_delay -datapath_only` (PULP's "three paths": wptr→dst
  sync, rptr→src sync, storage→dst read mux / output register). Constrain all three per channel.
- [I] **Sync stages.** Parameterize `SYNC_STAGES` (default 2, allow 3–4). At a 600 MHz ASIC target the per-stage
  resolution time is < 1.67 ns minus clk→q/setup; the ASIC library's τ/T_W are needed to decide, and the number of
  synchronizers is large (10 pointers × LOG_DEPTH+1 bits per wrapper), which divides MTBF (Beer/Ginosar 1/K). Industry
  practice at ≥ 500 MHz in advanced nodes is often 3 stages or a library "synchronizer cell"; make the stage count a
  parameter and use a dedicated `g2d_sync` cell module that a foundry sync cell can replace (OpenTitan `prim_flop_2sync`
  and PULP `sync` both follow this "one cell to swap" pattern). For 7-series FPGA at 50–200 MHz, 2 stages with
  `ASYNC_REG` is the common default; XPM defaults to 4 and the AXI clock converter to 2–3, so 3 is a safe FPGA default if
  timing allows.
- [I] **Vivado recipe for our wrappers** (scoped, not `set_clock_groups -asynchronous`, so max-delay still applies):
  `ASYNC_REG TRUE` on sync flops (attribute in RTL: `(* ASYNC_REG = "TRUE" *)`); per channel
  `set_max_delay -datapath_only min(Tsrc,Tdst)` from gray-pointer regs to first sync flop and from storage regs to the
  dst read register; optionally `set_bus_skew min(Tsrc,Tdst)` on each gray bus; `set_false_path -hold` (or rely on
  datapath_only). Ship these as a generated XDC/SDC fragment in `syn/` and `fpga/` so integrators do not have to discover
  hierarchy names (Cheshire's hand-written xdc shows the fragility of path globs).
- [I] Keep `(* async *)`-style port naming / a consistent prefix (e.g. `async_*`) on the inter-half nets so SDC can use
  `-through` filters like PULP does; keep the two halves as separate modules with `no_ungroup` / `KEEP_HIERARCHY`.

### Gaps
- Full text of both Cummings papers (2002 FIFO, 2008 CDC) could not be fetched (paywalled/login redirect; third-party
  copies only). Their exact rules (e.g., registered full/empty, "always register before crossing", 2-deep FIFO for
  multi-bit control) are well known but not re-verified here.
- No public τ / T_W data for any 600 MHz ASIC library was found; ASIC stage count must be decided with the target PDK
  (Phase 8). No 7-series-specific MTBF numbers were fetched (Vivado `report_synchronizer_mtbf` is UltraScale-only to my
  knowledge — unverified).
- Arm ADB-400 internal structure (slave/master halves, depth) is not described in public material found; only a cycle
  model guide and an errata notice exist publicly — [ADB-400 cycle model UG](https://documentation-service.arm.com/static/5ed10729ca06a95ce53f89b2). A third-party page says it can be a DVFS bridge (different voltage domains) or a clock bridge — [SoC Labs NIC-450 page](https://soclabs.eprints-hosting.org/taxonomy/term/25).

---

## Q2. How PULP `axi_cdc`, OpenTitan, Xilinx and Arm structure AXI CDC

### Takeaway
All public designs split the bridge into a source half and a destination half connected only by gray pointers + FIFO
storage (no combinational path across), one independent async FIFO per AXI channel (AW, W, AR forward; B, R backward),
power-of-2 depth parameter and sync-stage parameter, separate per-domain resets, and leave isolation/clear to a separate
block. G2D's planned "5 async FIFOs" matches this exactly.

### Cited Findings
- [E] `axi_cdc`: "For each of the five AXI channels, this module instantiates a CDC FIFO, whose push and pop ports are in
  separate clock domains"; parameters `LogDepth` (default 1, depth 2**LogDepth) and `SyncStages` (default 2); split into
  `axi_cdc_src` (src_clk/src_rst) and `axi_cdc_dst` (dst_clk/dst_rst) connected by `(* async *)` `async_data_*` /
  `wptr` / `rptr` nets; "IMPORTANT: For each AXI channel, you MUST properly constrain three paths through the FIFO";
  `axi_lite_cdc_intf` wraps the same module for AXI-Lite — [axi_cdc.sv v0.39.8](https://github.com/pulp-platform/axi/blob/v0.39.8/src/axi_cdc.sv)
- [E] The src/dst split lets the two halves be placed in different hierarchies (e.g. different power/clock domains), the
  only crossing nets being the async bundle — same file; Cheshire constrains them by module name
  (`axi_cdc_src`/`axi_cdc_dst`) — [cheshire.xdc](https://github.com/pulp-platform/cheshire/blob/main/target/xilinx/constraints/cheshire.xdc)
- [E] `axi_isolate`: counts open read/write transactions; on `isolate_i` "all open transactions are gracefully
  terminated" and when none are in flight `isolated_o` is asserted and the master port outputs are silenced to '0 —
  [axi_isolate.sv](https://github.com/pulp-platform/axi/blob/v0.39.8/src/axi_isolate.sv)
- [E] PULP `register_interface` has its own `reg_cdc` (register-bus CDC) besides the AXI-Lite path —
  [register_interface v0.4.5 src/](https://github.com/pulp-platform/register_interface/tree/v0.4.5/src)
- [E] OpenTitan uses per-signal-class prims (`prim_fifo_async`, `prim_flop_2sync`) and builds TL-UL async FIFOs from them
  (see Q1 citation) — [prim_fifo_async.sv](https://github.com/lowRISC/opentitan/blob/master/hw/ip/prim/rtl/prim_fifo_async.sv)
- [E] Xilinx AXI Clock Converter: async or synchronous-ratio mode, configurable sync stages 2..8 (Q1 citations).
- [E] ADB-400: public material only identifies it as an asynchronous domain bridge usable across clock or voltage domains
  (Q1 gap).

### Inferences
- [I] For G2D: `g2d_cdc_axil` (5 channels, LOG_DEPTH 1) and `g2d_cdc_axi` (5 channels, LOG_DEPTH param, default 3) built
  from one `g2d_cdc_fifo` with `_src` / `_dst` halves and a `g2d_sync` cell. With `CFG_ASYNC = 0` / `MEM_ASYNC = 0`
  instantiate a register slice (or wires), as arch.md §2.1 already says.
- [I] Do not cross the core's `soft_busy` / drain state through the CDC: soft reset is core-only and arch.md §2.2 already
  drains every outstanding AXI transaction before `soft_clr`, so the CDC FIFOs are empty at `soft_clr` — no clear port
  is needed for soft reset (see Q3).
- [I] The AXI rule that W may precede AW (arch.md §4.2) is preserved: AW and W have independent FIFOs, each in-order; the
  slave sees the same relative order per channel. No cross-channel deadlock is introduced because no FIFO waits on
  another.
- [I] Optional register slice after each `_dst` output (PULP adds a spill register on the dst side, visible in the
  Cheshire DRAM constraint path `i_spill_register`) improves dst timing at 600 MHz and makes the dst outputs flop-driven
  (AXI "no combinational path input→output" friendliness).

### Gaps
- ADB-400 and Xilinx clock converter internal FIFO depths are not publicly documented in what was found.
- Whether newer pulp-platform/axi releases (> v0.39.8) added a clearable `axi_cdc` was not checked.

---

## Q3. Reset across domains: synchronizers, sequencing of the two halves, soft reset, flush, AXI during reset

### Takeaway
A plain gray FIFO is only safe for power-on reset where both halves' resets assert together (asynchronously) and each
de-asserts synchronously to its own clock; any one-sided ("warm") reset of one half needs an isolate→clear→release
handshake mirrored in both domains (PULP `cdc_reset_ctrlr`). For G2D the cleanest rule is: CDC halves are reset only by
the hard resets, all three asserted together; soft reset never touches the CDC (it already drains AXI).

### Cited Findings
- [E] `cdc_fifo_gray` "must not be used if warm reset capability is a requirement" unless a controller sequences the
  resets while gating both clocks; for POR "src_rst_ni and dst_rst_ni signal must be asserted SIMULTANEOUSLY ...
  Otherwise, spurious transactions could occur in the domain where the reset arrives later"; de-assertion must be
  synchronized to each domain (e.g. `rstgen`); warm resets of part of a circuit "might introduce metastability in this
  separate reset-domain when you assert the reset" — [cdc_fifo_gray.sv](https://github.com/pulp-platform/common_cells/blob/v1.38.0/src/cdc_fifo_gray.sv)
- [E] `cdc_fifo_gray_clearable` supports one-sided warm resets: a clear on one side resets the other "without introducing
  any spurious transactions" via `cdc_reset_ctrlr`, which "first isolates the CDC from the outside world and then resets
  it"; src becomes unready the cycle after a clear request; some pending transactions may complete, others are dropped
  (FIFO order kept); "During the reset sequence the dst might withdraw the valid signal. This might violate higher level
  protocols" (i.e. AXI); `CLEAR_ON_ASYNC_RESET` raises the minimum sync stages from 2 to 3 — [cdc_fifo_gray_clearable.sv](https://github.com/pulp-platform/common_cells/blob/v1.38.0/src/cdc_fifo_gray_clearable.sv)
- [E] `cdc_reset_ctrlr` problem statement: clearing one pointer without the other gives an invalid fill state; resetting
  both asynchronously with one reset causes RDC metastability; synchronizing assertion naively has fast-to-slow pulse loss;
  solution = 4-phase handshake per phase (assert isolate → isolate_ack → assert clear → deassert clear → deassert isolate)
  mirrored in lock-step in both domains; bound t_clear ≤ 20·T + 16·SYNC_STAGES·T with T = max(T_a, T_b) —
  [cdc_reset_ctrlr.sv](https://github.com/pulp-platform/common_cells/blob/v1.38.0/src/cdc_reset_ctrlr.sv)
- [E] `axi_isolate` provides the "drain open transactions then silence" behaviour needed before a clear — [axi_isolate.sv](https://github.com/pulp-platform/axi/blob/v0.39.8/src/axi_isolate.sv)
- [E] OpenTitan `prim_fifo_async` resets pointers per side asynchronously, storage not reset — [prim_fifo_async.sv](https://github.com/lowRISC/opentitan/blob/master/hw/ip/prim/rtl/prim_fifo_async.sv)
- [E] Reset signals not synchronized for the destination clock domain are a CDC error class checked by tools —
  [SemiEngineering, Effective CDC verification](https://semiengineering.com/effective-clock-domain-crossing-verification)

### Inferences
- [I] **G2D hard reset contract (spec text to add to arch.md §2.2):** "With `CFG_ASYNC = 1` or `MEM_ASYNC = 1`,
  `rst_cfg_ni`, `rst_ni` and `rst_mem_ni` must be asserted together (same source, asynchronous assertion), each
  de-asserted synchronously to its own clock; resetting one domain alone while the others run is not supported." This
  matches the existing decision that the integrator provides de-assertion synchronizers. Cheap, standard, and avoids
  `cdc_reset_ctrlr` complexity in v1.
- [I] **AXI during reset:** AXI4 requires masters to drive ARVALID/AWVALID/WVALID low and slaves RVALID/BVALID low during
  reset, and VALID may only rise after reset is released (AMBA AXI spec "Reset" section — [IHI0022](https://developer.arm.com/documentation/ihi0022/latest), not re-fetched). With both CDC halves reset together, `_src` and `_dst` valid outputs are flops reset to 0 → compliant.
  A one-sided reset of the mem domain while the core has outstanding bursts would break AXI on the core side (lost R/B) —
  another reason to forbid it or add an isolate (axi_isolate-like) + clear sequence later.
- [I] **Soft reset:** keep as-is (core-domain only). Because the AXI master drains every outstanding transaction and
  only then pulses `soft_clr`, the CDC FIFOs are provably empty at `soft_clr` (no AR/AW issued after drain start, all R/B
  accepted). Add an SVA: `soft_clr |-> cdc_mem_empty` (the src-side "all pointers equal" view, conservative) and a
  cocotb test with soft reset under random clock ratios.
- [I] **Optional later feature (only if a power-domain / DVFS use case appears):** a `MEM_CDC_CLEARABLE` option built
  like `cdc_fifo_gray_clearable` + `axi_isolate`; record as F-6 follow-up, not v1.1.
- [I] **Reset synchronizers inside the IP?** Arch already assigns them to the integrator. For a reusable IP, offering an
  optional `g2d_rst_sync` (2-flop async-assert/sync-deassert, `ASYNC_REG`, test-mode bypass for DFT) as a separate,
  integrator-instantiated cell is common practice (PULP `rstgen`/`rstgen_bypass` exist for that) — [common_cells src](https://github.com/pulp-platform/common_cells/tree/v1.38.0/src).

### Gaps
- AXI reset-rule wording was not re-fetched from IHI0022 this session.

---

## Q4. Interrupt synchronization (level IRQ into another domain) — relates to Q-RTL-4

### Takeaway
A level, sticky-until-acknowledged interrupt that is driven directly by a flop is the textbook-safe single-bit CDC:
the receiver needs only a 2–3-flop synchronizer. G2D's registered `irq_o` (Q-RTL-4) is the correct choice; keep it
flop-driven with no logic after the flop.

### Cited Findings
- [E] "A clean asynchronous CDC path should not have any logic between the source and destination sequential cells" —
  [UG949](https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Constraints-on-Individual-CDC-Paths)
- [E] The standard fix for a single-bit crossing is a second flop stage on the destination clock — [SemiEngineering](https://semiengineering.com/effective-clock-domain-crossing-verification)
- [E] Missing-synchronizer and reset-sync errors are the first-order structural CDC checks — same source.

### Inferences
- [I] Q-RTL-4 can be closed as "correct by design": `irq_o` is a flop output (glitch-free), level, held until software
  clears IRQ_STATUS via w1c, so it is held for far longer than any receiving clock period; no pulse stretching or
  handshake needed. Spec note: "`irq_o` is driven by a flop in the `clk_i` domain; the integrator must synchronize it
  (2+ flops) if the interrupt controller runs on another clock."
- [I] Driver-visible effect of the latency: after a w1c clear, the de-assertion reaches the PLIC/CLIC 2–3 receiver
  cycles plus bus latency later; a level-triggered controller may see the old level if the ISR returns immediately.
  Standard driver practice is to read back IRQ_STATUS (posted-write flush across the CFG CDC) before returning; worth one
  sentence in the HAL doc once `CFG_ASYNC = 1` adds latency.
- [I] Offer an optional parameter `IRQ_SYNC_STAGES` / `IRQ_CLK` port? Not recommended — it adds a fourth clock; keep the
  integrator-side synchronizer (arch.md §2.1 decision).
- [I] Q-RTL-9 (`STATUS.CMD_ACTIVE`) is unaffected by CDC: all status registers live in the core domain and are read
  through the CFG CDC as one 32-bit word, coherent because the read data crosses inside the R FIFO entry (multi-bit data
  through FIFO storage, not through individual synchronizers).

### Gaps
- None material.

---

## Q5. CDC verification without commercial tools (cocotb + Verilator, assertions, formal, lint; Spyglass-friendliness)

### Takeaway
Verilator is cycle-based and 2-state, so it cannot model metastability or sub-cycle skew; the open-source strategy is:
(1) randomized, mutually prime clock periods and phases per seed in cocotb, (2) a simulation-only randomized-latency
synchronizer model (metastability injection), (3) SVA on gray-pointer one-bit change / no overflow / no underflow /
ordering, (4) a SymbiYosys proof of the FIFO with a `$global_clock` multi-clock model (tool must be installed under
~/tools first), and (5) structural CDC rules enforced by coding convention so a later Spyglass CDC run is clean.

### Cited Findings
- [E] Verilator 5.052 (installed) supports `--timing` ("Enables/disables support for timing constructs such as delays,
  event controls"; needs a coroutine-capable C++ compiler) and `--x-assign`, `--x-initial`, `--x-initial-edge`;
  four-state support is "Experimental, for developer use only" (default is 2-state) — [Verilator exe_verilator docs](https://verilator.org/guide/latest/exe_verilator.html); local `verilator --help`
- [E] Verilator handles multiple clocks as long as the testbench drives each clock; evaluation advances edge to edge
  non-uniformly — [ZipCPU, tbclock](https://zipcpu.com/blog/2018/09/06/tbclock.html)
- [E] cocotb lets each clock be created with its own period and started as an independent task (`Clock(...)`,
  `cocotb.start_soon`) — [cocotb clock docs summary](https://tessl.io/registry/tessl/pypi-cocotb/files/docs/clock-generation.md)
- [E] Randomizing per-path flip-flop delays with seeds, and varying clock periods, is a documented CDC simulation
  methodology — [US patent 7289946, random flip-flop delays](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/7289946)
- [E] SymbiYosys async-FIFO proof method: each clock derived from a counter advancing by an `anyconst` nonzero step per
  `$global_clock` tick; resets assert together and release synchronously; inputs change only on their clock edge;
  properties: fill in [0, 2^N], flags match fill, gray pointers change one bit, gray full/empty equals binary math,
  ordering "contract" on two arbitrary consecutive writes, covers for full/empty; open-source flow lacked `$onehot` and
  some concurrent-assertion features; induction needed helper properties — [ZipCPU async FIFO](https://zipcpu.com/blog/2018/07/06/afifo.html)
- [E] OpenTitan's async FIFO has `GrayWptr_A` / `GrayRptr_A` (≤ 1 bit change per clock) and a param check; full/empty
  assertions are a TODO — [prim_fifo_async.sv](https://github.com/lowRISC/opentitan/blob/master/hw/ip/prim/rtl/prim_fifo_async.sv)
- [E] XPM has a simulation-only check of the gray ±1 rule (`SIM_LOSSLESS_GRAY_CHK`) — [UG974 XPM_CDC_GRAY](https://docs.amd.com/r/en-US/ug974-vivado-ultrascale-libraries/XPM_CDC_GRAY)
- [E] PULP `AXI_BUS` interface carries stability assertions (e.g. `aw_valid && !aw_ready |=> $stable(aw_addr)`) usable as
  protocol checks on any AXI port — [axi_intf.sv](https://github.com/pulp-platform/axi/blob/v0.39.8/src/axi_intf.sv)
- [E] Commercial CDC tools check: missing synchronizers, multi-bit coherency (gray coding), convergence/reconvergence of
  separately synchronized signals ("can be offset by one cycle", called the most common chip killer), reset
  synchronization — [SemiEngineering](https://semiengineering.com/effective-clock-domain-crossing-verification); SpyGlass rule names (Ac_unsync01, Ac_conv*) appear in Synopsys support material but the rule definitions are not public — [Synopsys JP support Q&A](https://www.synopsys.com/ja-jp/japan/today-tomorrow/today-tomorrow-support-4.html)
- [E] A DVCon paper covers SVA for CDC data paths, and another a "pragmatic formal" CDC methodology — [DVCon SVA for CDC data paths](https://dvcon-proceedings.org/wp-content/uploads/systemverilog-assertions-for-clock-domain-crossing-data-paths.pdf); [arXiv 2406.06533](https://arxiv.org/pdf/2406.06533)
- [E] Local check: yosys, sby, SMT solvers, verible and slang are **not installed**; cocotbext-axi 0.1.28 is.

### Inferences
- [I] **Clock-ratio regression plan (cocotb):** per seed choose `T_cfg`, `T_core`, `T_mem` from a set including
  1:1 with random phase, near-1:1 (e.g. 10.0/10.3 ns, beat-frequency stress), 1:2, 2:1, 1:7, 7:1, extreme 1:20 / 20:1, and
  co-prime ps values; start each clock at a random offset; optionally a jitter clock coroutine (`Timer` per half period
  with ±x % random) instead of `cocotb.clock.Clock`. Use ps time precision. Scoreboard: every AXI transaction on the core
  side matches the mem side bit-exactly and in per-ID order (reuse the existing golden-model compare: a full draw stream
  with MEM_ASYNC = 1 must produce the same framebuffer CRC as MEM_ASYNC = 0).
- [I] **Metastability injection without 4-state:** write `g2d_sync` with an `ifdef G2D_CDC_RAND_DELAY` simulation model
  in which each bit independently takes N or N+1 cycles (random per transition). This exposes reconvergence bugs (e.g.
  binary instead of gray pointer, or two separately synchronized control bits) that a pure 2-flop model hides. OpenTitan
  has a similar CDC random-delay facility in its prims (not verified this session — check `prim_cdc_rand_delay`).
  Must be compile-time so lint/synthesis never see it.
- [I] **Assertions to add in `dv/sva`** (bind, both clocks): gray pointer one-bit change per own clock; no push when
  full, no pop when empty; fill ≤ depth computed from binary pointers sampled in a common `$global_clock`-like sim
  domain; valid stable until ready on all 10 boundary channels (AXI src and dst sides); after reset both halves empty.
  Verilator supports a useful SVA subset for these immediate/simple concurrent forms (the project already uses `dv/sva`).
- [I] **Formal:** install OSS CAD Suite (yosys + sby + solvers) under `~/tools` (needs user approval, record in
  docs/TOOLS.md) and prove `g2d_cdc_fifo` for depths 2, 4, 8 with ZipCPU's clock model: BMC + k-induction for overflow,
  underflow, ordering. Small block, proof is cheap; it is the strongest evidence achievable without commercial CDC tools.
  Document in OPEN_QUESTIONS that the formal model abstracts metastability (each sync stage modeled as 1 cycle) unless a
  nondeterministic N/N+1 stage is added (recommended: use `anyseq` to choose the extra cycle).
- [I] **Spyglass-CDC-friendly RTL rules for 6b:** (1) every crossing goes through either `g2d_sync` (single bit /
  gray bus) or async-FIFO storage read only under synchronized pointer qualification; (2) source of every synchronized
  signal is a flop in the source domain, no combinational logic before the first sync flop; (3) no fan-out of the first
  sync stage; (4) never synchronize two related control bits separately (convergence) — bundle them through the FIFO;
  (5) gray pointers registered after the binary→gray conversion; (6) reset de-assertion synchronizers are recognizable
  cells; (7) one module per synchronizer type so the tool's synchronizer recognition can be configured once (`-sync_cell`
  style), (8) CFG_ASYNC=0/MEM_ASYNC=0 must still produce the same hierarchy names for constraints, (9) document the
  clocks in an SDC with `create_clock` for all three ports so CDC tools infer domains. These mirror the error classes
  in the SemiEngineering summary.
- [I] **Open-source structural CDC lint:** no mature open-source CDC checker was confirmed. A cheap substitute is a
  Python script on Verilator's `--xml-only`/`--json-only` netlist (or slang AST if installed) listing every flop whose D
  cone contains a flop clocked by another clock and asserting it is inside `g2d_sync` or `g2d_cdc_fifo_*` storage reads.
  This is project-specific tooling, label as such.

### Gaps
- Verilator's `--timing` interaction with cocotb's VPI scheduling for multi-clock designs was not documented in sources
  found (the project already runs cocotb 2.x on Verilator 5.052 with clocks driven from Python, which is the usual mode).
- No open-source CDC lint tool (Yosys/slang/Verible-based) with verified CDC rules was found in this session.
- Exact SpyGlass CDC rule definitions are not public.

---

## Q6. Bus adapters (F-6): protocol essentials and pitfalls, AXI master options, packaging, open-source protocol checkers

### Takeaway
Every F-6 config adapter maps onto the existing `g2d_reg_req_t/rsp_t` single-access interface; the pitfalls are
per-protocol response timing and error signalling, not datapath. PULP `register_interface` already has open-source
equivalents (`axi_lite_to_reg`, `axi_to_reg_v2`, `apb_to_reg`, `periph_to_reg`, `reg_to_tlul`, `reg_cdc`) that are good
behavioral references (Solderpad, public). For the memory side, keep the AXI4 master native and expose
parameters (ID width, AxCACHE/AxPROT/AxQOS); width/ID conversion can be left to SoC interconnect or offered as optional
wrappers. Package with FuseSoC `.core` + Bender `Bender.yml` (+ IP-XACT generated from regs.yaml later).

### Cited Findings
- [E] Open-source reg-bus adapters exist in PULP `register_interface` v0.4.5: `apb_to_reg.sv`, `axi_lite_to_reg.sv`,
  `axi_to_reg_v2.sv` (and deprecated `axi_to_reg.sv`), `periph_to_reg.sv`, `reg_to_tlul.sv`, `reg_to_apb.sv`,
  `reg_to_axi.sv`, `reg_cdc.sv`, `reg_cut.sv`, `reg_err_slv.sv` — [register_interface src](https://github.com/pulp-platform/register_interface/tree/v0.4.5/src)
- [E] PULP `axi` v0.39.8 provides `axi_lite_to_apb`, `axi_to_axi_lite`, `axi_lite_dw_converter`, `axi_dw_converter`,
  `axi_iw_converter`, `axi_id_serialize`, `axi_id_remap`, `axi_id_prepend`, `axi_serializer`, `axi_isolate` —
  [pulp-platform/axi src](https://github.com/pulp-platform/axi/tree/v0.39.8/src)
- [E] PULP `obi` repository (used by Cheshire) has `obi_pkg`, `obi_intf`, `obi_err_sbr`, `obi_cut`, `apb_to_obi`,
  `obi_rready_converter`, `obi_atop_resolver` — [pulp-platform/obi](https://github.com/pulp-platform/obi)
- [E] cocotbext-axi 0.1.28 (installed) provides AXI4, AXI4-Lite, AXI-Stream and APB models (`apb.py`) — [cocotbext-axi](https://github.com/alexforencich/cocotbext-axi)
- [E] PULP `AXI_BUS` interface includes handshake-stability assertions (Q5).
- [E] AMD AXI Interconnect provides data-width converter with optional packet-FIFO-with-clock-conversion mode —
  [PG059 DWC parameters](https://docs.amd.com/r/en-US/pg059-axi-interconnect/AXI-Data-Width-Converter-Parameters)
- Specifications to cite in F-6 (public; not re-fetched this session, contents below are from prior knowledge, see
  Inferences): [AMBA AXI and ACE / AXI5 spec IHI0022](https://developer.arm.com/documentation/ihi0022/latest),
  [AMBA APB IHI0024](https://developer.arm.com/documentation/ihi0024/latest),
  [AMBA AHB IHI0033](https://developer.arm.com/documentation/ihi0033/latest),
  [OpenHW OBI spec](https://github.com/openhwgroup/obi),
  [Wishbone B4 (OpenCores)](https://cdn.opencores.org/downloads/wbspec_b4.pdf),
  [SiFive TileLink spec](https://www.sifive.com/document-file/tilelink-spec-1.9.3),
  [OpenTitan TL-UL docs](https://opentitan.org/book/hw/ip/tlul/index.html),
  [IEEE 1685-2022 IP-XACT](https://standards.ieee.org/ieee/1685/10583/),
  [FuseSoC docs](https://fusesoc.readthedocs.io/), [Bender](https://github.com/pulp-platform/bender).

### Inferences (protocol checklists per adapter; spec-derived, not re-verified this session)
- [I] **`g2d_axi2reg` (full AXI4 slave):** independent AW/W arrival (W may come first); bursts INCR/FIXED/WRAP must each
  be handled or answered SLVERR (registers: split into single accesses, honour `AxSIZE` narrower than bus width, lane
  from address); reflect `AxID` on B/R; RLAST on last beat; B only after the last W beat; ATOPs (AXI5 `AWATOP`) must get
  SLVERR and, for atomics with read response, also R beats — easy to get wrong (PULP `axi_atop_filter` exists as a
  reference); `AxLOCK` exclusive → OKAY (not EXOKAY) signals failure, which is legal; no combinational VALID-on-READY
  dependence (VALID must not wait for READY). Per-ID ordering only; a single-outstanding implementation is legal.
- [I] **`g2d_apb2reg`:** two-phase SETUP/ACCESS; PREADY may stretch; PSLVERR only valid in last ACCESS cycle with
  PREADY; APB4 adds PSTRB (writes only; must be 0 on reads) and PPROT; APB3 lacks PSTRB (treat as all ones). Pitfall:
  sampling PWDATA/PADDR in SETUP vs ACCESS (both stable over the transfer, so either works); never start the reg access
  twice if PREADY is low for several cycles.
- [I] **`g2d_ahb2reg` (AHB-Lite):** address/data pipeline — address phase of transfer N overlaps data phase of N-1, so
  write data arrives one cycle after the address: register HADDR/HWRITE/HSIZE when HSEL & HREADY & HTRANS[1]; error
  response is **two cycles** (HRESP=1 with HREADYOUT=0, then HRESP=1 with HREADYOUT=1); IDLE/BUSY transfers get zero-wait
  OKAY; byte/half writes derive strobes from HSIZE + HADDR[1:0]; use HREADY (bus) not HREADYOUT to qualify a new address.
- [I] **`g2d_obi2reg`:** OBI A-channel `req/gnt`, R-channel `rvalid` (optional `rready` in OBI 1.5+); response must
  come strictly after grant and in order; `be` byte enables; `err` on R; PULP `periph_to_reg` is the closest reference.
- [I] **`g2d_wb2reg` (Wishbone B4 pipelined):** `CYC`, `STB`, `STALL`, `ACK`/`ERR`(/`RTY`); in pipelined mode a request
  is accepted when `STB & !STALL`, and exactly one ACK/ERR per accepted request, in order; `SEL` byte selects; drop
  everything when `CYC` deasserts (master abort). Pitfall: classic vs pipelined ACK timing differ; assert STALL while a
  reg access is outstanding to keep the adapter single-outstanding.
- [I] **`g2d_tlul2reg`:** A channel opcodes Get / PutFullData / PutPartialData; D channel AccessAck / AccessAckData with
  `d_source` and `d_size` reflected; `a_mask` must be consistent with `a_size` and address (PutFullData = all lanes for
  that size); unsupported opcode or misaligned access → `d_error`; TL-UL allows multiple outstanding via `a_source`, but
  responses for the same source in order. OpenTitan's `tlul_adapter_reg` is the canonical reference (public, Apache-2.0).
- [I] **Memory side:** keep AXI4 native. Parameters to expose: `ID_W` already; `AXI_CACHE`, `AXI_PROT`, `AXI_QOS`
  (or CSR fields, which then need a regs.yaml change); `USER_W` (0 = absent). ID-width reduction is only needed if the
  SoC port is narrower than `log2(5 clients)` — G2D uses ≥ 3 bits; a `serialize to 1 ID` wrapper (like `axi_id_serialize`)
  is the generic fallback but costs read reordering. Data width: `DATA_W` 64/128 natively; a 32-bit or 256/512-bit
  fabric is the SoC interconnect's job (DWC) — document, do not build, unless a target forces it. AXI5 / ACE-Lite / CHI
  not needed (non-coherent GPU writes to framebuffer; cache maintenance by software).
- [I] **Verification of adapters:** one shared cocotb test per adapter running the same CSR test list (reset values,
  w1c, RO, error address → error response, byte strobes, back-to-back, random wait states) against the golden register
  model generated from regs.yaml. cocotbext-axi covers AXI/AXI-Lite/APB; AHB-Lite, OBI, Wishbone and TL-UL need small
  in-house cocotb drivers (or third-party cocotbext packages — e.g. cocotbext-wishbone / cocotbext-ahb exist on PyPI to
  my knowledge but were not verified this session; check maintenance before relying on them).
- [I] **Packaging:** provide `g2d.core` (FuseSoC CAPI2, filesets per adapter, targets lint/sim) and `Bender.yml`
  (needed for PULP/Cheshire integration, which the project already uses); generate IP-XACT 1685-2022 component + register
  description from `spec/regs.yaml` in `scripts/gen_regs.py` later (rule 5: single source of truth) rather than
  hand-writing XML. Ship the CDC constraint fragment (Q1) with the package.

### Gaps
- None of the protocol specs (AXI/APB/AHB/OBI/Wishbone/TileLink) nor the OpenTitan TL-UL doc page could be re-fetched in
  this session (OpenTitan protocol URL returned 404); the per-adapter checklists above are from prior knowledge and must
  be checked against the spec PDFs when the F-6 spec section is written.
- Open-source protocol checkers for AHB/OBI/Wishbone/TL-UL usable under Verilator were not surveyed.
- IP-XACT 2022 tooling (open-source generators/validators) was not surveyed.
