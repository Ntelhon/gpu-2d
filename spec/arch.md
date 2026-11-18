# G2D architecture specification (v1)

IP name: **G2D**. RTL module prefix `g2d_`, package `g2d_pkg`. 2D/2.5D raster GPU, API-compatible
with VGLite through the G2D driver (`spec/api-subset.md`).

## 1. Top-level block diagram

```
                     cfg domain             core domain (clk_i)                                mem domain
                   ┌───────────┐  ┌──────────────────────────────────────────────────────┐  ┌───────────┐
 AXI4-Lite slave ──┤ axil CDC  ├──┤ g2d_axil2reg ─► g2d_csr (generated) ─► ctrl/status   │  │           │
 (32-bit)          │ (bypass   │  │                     │ kick queue, fence, irq, perf   │  │ axi CDC   │
                   │  in v1)   │  │                     ▼                                │  │ (bypass   │
                   └───────────┘  │ ┌──────────┐  ┌──────────┐  ┌───────────────────┐    │  │  in v1)   │
 irq_o ◄──────────────────────────┤ │ cmd fetch├─►│ cmd parse├─►│ state regs (shadow)│    │  │           │
                                  │ └────▲─────┘  └────┬─────┘  └─────────┬─────────┘    │  │           │
                                  │      │             │ DRAW              │             │  │           │
                                  │      │        ┌────▼─────────────────────────────┐   │  │           │
                                  │      │        │ primitive setup                  │   │  │           │
                                  │      │        │  RECT iterator │ PATH rasterizer │   │  │           │
                                  │      │        └────┬───────────────────┬─────────┘   │  │           │
                                  │      │             │ (x,y,cov) quads    │ edge fetch  │  │           │
                                  │      │        ┌────▼──────────────┐     │             │  │           │
                                  │      │        │ paint: solid │ image sampler (xform, │  │           │
                                  │      │        │ src cache, filter, ckey) │ gradient  │  │           │
                                  │      │        └────┬──────────────┘     │             │  │           │
                                  │      │        ┌────▼──────────────┐     │             │  │           │
                                  │      │        │ dst read │ blend  │     │             │  │           │
                                  │      │        │ coverage │ format │     │             │  │           │
                                  │      │        └────┬──────────────┘     │             │  │           │
                                  │ ┌────┴─────────────▼────────────────────▼───────────┐ │  │           │
                                  │ │ g2d_axi_mst: read arbiter (cmd/dst/src/edge/ramp), ├─┼──┤           ├──► AXI4 master
                                  │ │ write engine, 4KB split, strobes, window check     │ │  │           │   (DATA_W)
                                  │ └────────────────────────────────────────────────────┘ │  └───────────┘
                                  └──────────────────────────────────────────────────────────┘
```

## 2. Clocks, resets, CDC

### 2.1 Clock domains (decision 2026-10-15)

| Domain | Clock / reset ports | Contents |
|---|---|---|
| cfg | `clk_cfg_i`, `rst_cfg_ni` | AXI4-Lite slave side of the config CDC only |
| core | `clk_i`, `rst_ni` | Everything else: CSR, command processor, pipeline, AXI master logic |
| mem | `clk_mem_i`, `rst_mem_ni` | AXI4 master side of the memory CDC only |

- **v1 (Phases 2–4, FPGA bring-up):** parameters `CFG_ASYNC = 0`, `MEM_ASYNC = 0`. The integrator
  connects all three clocks to the same clock net. In this mode the CDC blocks are optional register
  slices, the design has no clock crossings, and lint/STA treat it as one clock.
- **v1.1 (old Phase 6b):** `CFG_ASYNC = 1` / `MEM_ASYNC = 1` instantiate the async CDC wrappers (original RTL)
  (`rtl/cdc/`). The core stays one clock domain, so all crossings live in two bolt-on wrappers at the ports:
  - `g2d_cdc_axil` between `s_axil_*` (cfg domain) and the core: one dual-clock FIFO per AXI4-Lite channel
    (AW, W, B, AR, R), `2^CFG_CDC_LOG_DEPTH` entries (default 2).
  - `g2d_cdc_axi` between the core and `m_axi_*` (mem domain): one dual-clock FIFO per AXI4 channel,
    `2^MEM_CDC_LOG_DEPTH` entries (default 8).
  - `g2d_cdc_fifo`: Cummings gray-pointer FIFO (binary + gray pointer with one wrap bit per side; full / empty
    from the local pointer against the remote gray pointer after a `SYNC_STAGES`-flop `g2d_sync`; storage is a
    flop array written in the source domain and read in the destination domain only at entries the
    synchronized write pointer has made visible). The only signals that cross are the two gray pointers (through
    `g2d_sync`) and the storage read data (stable while visible). AXI handshakes bound the transactions in
    flight, so the depth only has to cover the pointer round trip (≈ `2·SYNC_STAGES + 2` cycles) for full
    throughput, not the outstanding transactions.
  - Latency added per direction: one source cycle (pointer register) + `SYNC_STAGES` destination cycles
    (+ up to one for the phase). Throughput: one transfer per cycle of the slower side.
  - FPGA (Genesys2, decision 2026-10-18): G2D core 100 MHz, config and memory ports at Cheshire's 50 MHz SoC clock.
    The memory path through Cheshire's 64-bit 50 MHz crossbar caps bandwidth at ~400 MB/s whatever the core
    clock (docs/OPEN_QUESTIONS.md Q-FPGA-1).
  - Constraints: synchronizer flops `ASYNC_REG`, gray-pointer and storage-read paths `set_max_delay
    -datapath_only` (≤ the destination period) with `set_bus_skew` on each gray bus (Vivado); ASIC SDC: max delay
    = the smaller period, hold false path on the same paths, synchronizer cells kept (dont_touch).
  - `SYNC_STAGES` (default 2) is a parameter; `g2d_sync` is the one cell an ASIC flow replaces by a library
    synchronizer. The ASIC value (2 or 3 at 600 MHz) follows the library's metastability data (ASIC step).
- `irq_o` is a level output synchronous to `clk_i`, driven straight from a flop (no glitches) and held until
  software clears the status bit. The integrator synchronises it into the interrupt controller domain (a 2–3
  flop synchronizer is enough). With `CFG_ASYNC = 1` the write that clears IRQ_STATUS reaches the core a few
  cycles after its B response; drivers read IRQ_STATUS back after clearing it (as for any posted write).

### 2.2 Reset (decision 2026-10-15)

- Active-low, **asynchronous assertion, synchronous de-assertion**, one reset per domain
  (`rst_cfg_ni`, `rst_ni`, `rst_mem_ni`). The integrator provides the de-assertion synchronisers (as in
  PULP/Cheshire).
- **With `CFG_ASYNC` / `MEM_ASYNC = 1` the three resets are asserted together** (one reset source, each released
  synchronously to its own clock). Resetting one domain while another runs is not supported: the CDC FIFOs
  would lose their pointer agreement (a one-sided clear needs an isolate-and-clear handshake, not built). Both
  halves of every CDC FIFO reset to empty; transactions in flight at the reset are lost on both sides, as for a
  single-clock hard reset.
- **Core reset distribution (v1.7):** inside `g2d_top` the core-domain reset passes `RST_STAGES` (default 2) flops
  (`g2d_rst_sync`: cleared asynchronously by `rst_ni`, shifting a 1 in), whose output is replicated by the tools to cut
  the fan-out of the reset recovery paths. Assertion stays asynchronous; de-assertion reaches the core `RST_STAGES`
  clock edges after `rst_ni` rises (the AXI ports hold their ready signals low meanwhile). The port domains of the
  async build use their resets directly (they hold only one side of the CDC FIFOs; OPEN_QUESTIONS Q-TEST-5).
- RTL style: `always_ff @(posedge clk_i or negedge rst_ni)`, applied consistently. Datapath registers
  that do not need reset (pipeline data qualified by a valid bit) are not reset (smaller ASIC area).
  Valid bits, FSMs, counters and CSRs are reset.
- **Soft reset** (`CTRL.SOFT_RESET`, self-clearing): command fetch stops issuing, the AXI master
  stops issuing new transactions and drains every outstanding one (responses are accepted and
  discarded), then every core FSM, queue and pipeline valid bit is cleared synchronously. CSR
  configuration (IRQ_ENABLE, ADDR_WIN, MEM_BASE_HI, SCRATCH) and the performance counters are kept;
  status, error, fence and state registers, the kick queue and every w1c field (IRQ_STATUS, ERR_STATUS)
  are cleared. Kicks written while the soft reset is in progress are dropped (no KICK_OVF).
  `STATUS.SOFT_RESET_BUSY` reads 1 until done. AXI protocol is never violated by a soft reset: an AR/AW
  already presented completes its handshake, every write burst whose AW was issued gets all its W beats
  (the pixel engine finishes the row it started), and every R/B response is accepted.
  Implementation (Phase 2): `g2d_ctrl` raises `soft_busy`; the pixel engine finishes its current row,
  the AXI master issues no new burst and turns the rest of any request into drop bursts (§4.3); when
  nothing is outstanding a one-cycle `soft_clr` clears the core. `soft_busy` (the engines' stop) stays
  high through the `soft_clr` cycle, so no engine presents a request in the cycle it is cleared (Phase 5
  finding B-5.1). After the reset the GPU enters idle,
  so `IRQ_STATUS.IDLE` is set (Phase 2 assumption A5).
  With `MEM_ASYNC = 1` the soft reset needs nothing in the CDC: "nothing outstanding" is counted at the core side
  of `g2d_cdc_axi` (every R last and B seen there), and by then every AW / W / AR entry has left its FIFO (the slave
  answers only after it) and every R / B entry has been taken, so all five FIFOs are empty at `soft_clr`
  (checked by an SVA, verification-plan CDC-04). The config-port CDC is never cleared by a soft reset.

## 3. Parameters

| Parameter | Default | Range | Meaning |
|---|---|---|---|
| `DATA_W` | 64 | 64, 128 | AXI4 master data width |
| `ADDR_W` | 32 | 32..64 | AXI4 master address width. Bits above 31 come from `MEM_BASE_HI` |
| `ID_W` | 4 | ≥ 3 | AXI4 ID width (ID = read client index) |
| `MAX_BURST_BYTES` | 256 | 64..4096 (power of 2) | Largest burst issued |
| `RD_OUTSTANDING` | 16 | 1..32 | Outstanding read bursts (total); v1.7: 16 (was 8), needed for ≥ 0.85 px/clk blended tiled blits at 90-cycle latency (Q-PERF-2); Cheshire accepts 24 per master |
| `WR_OUTSTANDING` | 8 | 1..32 | Outstanding write bursts |
| `KICK_DEPTH` | 4 | 2..16 | Command-buffer kick queue depth |
| `SRC_CACHE_BYTES` | 8192 | 2048..32768 (power of 2) | Source texel cache (§6.4) |
| `CFG_ASYNC`, `MEM_ASYNC` | 0 | 0, 1 | CDC enables (§2.1) |
| `SYNC_STAGES` | 2 | 2..4 | Synchronizer flops per crossing (§2.1) |
| `CFG_CDC_LOG_DEPTH` | 1 | 1..4 | Config-port CDC FIFO depth = 2^n (only with `CFG_ASYNC`) |
| `MEM_CDC_LOG_DEPTH` | 3 | 1..5 | Memory-port CDC FIFO depth = 2^n (only with `MEM_ASYNC`) |
| `RST_STAGES` | 2 | 0..4 | Core reset distribution stages (§2.2, v1.7); 0 = `rst_ni` used directly |
| `IRQ_PULSE` | 0 | 0, 1 | v1.9: `irq_o` is a one-cycle pulse per cycle in which an enabled `IRQ_STATUS` bit becomes pending (pulses at least one cycle apart), for edge-triggered interrupt controllers (§7) |
| `IRQ_ACTIVE_LOW` | 0 | 0, 1 | v1.9: `irq_o` active low |
| `FEATURES` | `6'b111111` | any | Optional units built (v1.7, `g2d_pkg` `FEAT_*`): bit 0 blend families (§5.7), 1 Gaussian filter, 2 YUV sources, 3 colour unit, 4 radial gradient, 5 mask layer. A missing unit is removed by synthesis (its configuration is tied off), reported in `HWCFG.FEAT_*`, and a draw that needs it is `BAD_STATE` (rendering.md §13.1); the driver reads `HWCFG` and returns `VG_LITE_NOT_SUPPORT` for the APIs that need it (feature bits 0) |

The AXI4-Lite port is fixed at 32-bit data, 32-bit address (only the low 12 bits are decoded; 4 KB CSR window).

## 4. Interfaces

### 4.1 Port list (`g2d_top`)

```
clk_cfg_i, rst_cfg_ni, clk_i, rst_ni, clk_mem_i, rst_mem_ni
test_mode_i   v1.9 DFT: 1 bypasses the core reset distribution stage (scan); 0 in functional use
s_axil_*      AXI4-Lite slave: aw/w/b/ar/r, 32-bit data, 32-bit addr, prot ignored
m_axi_*       AXI4 master: full aw/w/b/ar/r incl. id, len, size, burst(INCR only), cache / prot / qos from
              MEM_ATTR (v1.9), lock=0, region=0, user absent
irq_o         interrupt: level (default) or pulse (IRQ_PULSE), active high (default) or low (IRQ_ACTIVE_LOW)
```

The CSR core sees a bus-agnostic register interface (`g2d_reg_req_t {valid, write, addr[11:2], wdata,
wstrb}` / `g2d_reg_rsp_t {ready, rdata, error}`). `g2d_axil2reg` adapts AXI4-Lite to it.

Other configuration buses (v1.9, F-6): `rtl/adapt/g2d_<bus>2axil` bridges AXI4 (any width 32..512, bursts),
APB3 / APB4, AHB-Lite, OBI, PULP reg_intf, Wishbone B4 pipelined and TileLink-UL to the AXI4-Lite port, one
access at a time (`g2d_axil_single`). They work in front of the `CFG_ASYNC` crossing unchanged because the crossing
is on AXI4-Lite. `scripts/gen_wrappers.py` generates `g2d_top_<bus>` (g2d_top with the adapter in the
configuration-port clock domain). Integration guide: docs/INTEGRATION.md. Cheshire (fpga/cheshire/g2d_cheshire.sv)
uses `g2d_top_axi` behind PULP's ATOP filter since v1.9.

### 4.2 AXI4 master rules

- INCR bursts only. Bursts never cross a 4 KB boundary. `AxSIZE = log2(DATA_W/8)` always (full-width
  beats). Unaligned starts and ends are handled with `WSTRB` on writes and byte selection on reads.
- `AxCACHE` / `AxPROT` / `AxQOS` per channel from CSR `MEM_ATTR` (v1.9). Reset: `AxCACHE = 4'b0011` (normal
  non-cacheable bufferable), `AxPROT = 3'b000`, `AxQOS = 0`, the fixed values of v1.0..v1.8. A new value is
  used by requests issued after the write; a request already waiting (VALID without READY) keeps its payload.
  `AxLOCK = 0`, `AxREGION = 0` always.
- Each read client has a fixed ARID. Responses for one ID come back in order (AXI rule). Writes use a
  single AWID (in-order B).
- Address window: every draw's buffer extents are checked against `[ADDR_WIN_LO, ADDR_WIN_HI]` before
  the draw starts (rendering.md §13.2), so a draw either runs completely inside the window or not at all.
  As defence in depth, the AXI master also checks every transaction against the window (64-byte granular)
  and blocks any violation. This second check can only fire on an RTL bug.
- `RRESP`/`BRESP` of SLVERR or DECERR raise `ERR_STATUS.AXI_RD/AXI_WR` and capture the address of the
  failing burst (its first requested byte, not the beat-aligned AxADDR).
- Burst split (Phase 2): a request is cut at every `MAX_BURST_BYTES`-aligned boundary, which also
  guarantees no 4 KB crossing. `MAX_BURST_BYTES / (DATA_W/8)` must be ≤ 256 beats (elaboration check).
- AW and W are decoupled: the W data of a burst may be presented before its AW handshake (a slave may
  wait for WVALID before AWREADY). W leaves through a register slice.
- The window check works per burst on the requested bytes, with a 33-bit cursor, so a request that wraps
  past 2^32 is a violation. A violating burst is not issued; the rest of that request is dropped
  (`ADDR_RANGE`, `ERR_ADDR` = first byte of the violating burst).

### 4.2.1 Memory-port throttle (v1.9, CSR `MEM_QOS`)

A shared SoC interconnect needs a way to keep the GPU from taking all memory bandwidth (e.g. from a display
controller). One gate per address channel (`g2d_axi_qos`) sits between the engine and the port:

- `RD_MAX` / `WR_MAX`: at most this many bursts outstanding on AR / AW (from the request handshake to the last R
  beat / the B response). 0 = only the build-time limit `RD_OUTSTANDING` / `WR_OUTSTANDING`; a value above it
  has no further effect.
- `RD_GAP` / `WR_GAP`: after each AR / AW handshake, at least this many cycles pass before the next one on that
  channel (0..255). Together with the burst length (`MAX_BURST_BYTES`) this bounds the request rate.
- A request that is already VALID is never withdrawn: once VALID is shown, it stays until READY, also if
  `MEM_QOS` changes meanwhile (AXI rule).
- Reset = all fields 0 = off. Off is cycle-identical to v1.8 (the gate passes VALID / READY unchanged).
- Throttled cycles are not counted by `PERF_RD_STALL` / `PERF_WR_STALL` (those count bus back-pressure only).

### 4.3 Internal interfaces (Phase 2)

- **Read client** (`g2d_axi_rd`, one fixed ARID per client): request = byte address + byte length − 1
  (1..65536 bytes). Responses come back in request order per client as DATA_W-aligned beats with a byte
  mask of the requested bytes (`strb`), `last` on the final beat of the request and `err` on beats of a
  burst answered with SLVERR/DECERR. RREADY is the ready of the client addressed by RID, so a client must
  accept every beat it requested without depending on others: clients are credit-based (they request
  only what they can buffer). Client 0 is the command fetch, client 1 the destination reader, client 2 the
  source cache (Phase 3), client 3 the path edge loader and client 4 the gradient ramp loader (Phase 4).
  All five are internal clients of the single AXI4 master. v1.6: client 5 is the mask-layer reader
  (`g2d_mask_rd`: a destination reader for A8 spans plus zero fill outside the layer, rendering.md §6.1).
- **No starvation, no deadlock between the read clients and the write-back** (Phase 4 condition A1).
  The read engine grants requests round-robin, one burst per grant, so every requesting client is served
  within N_RD grants. Each client requests only what it can buffer and accepts every beat it requested at
  once (the destination reader, edge loader and command fetch into SRAM FIFOs sized by their credits, the
  source cache and ramp loader straight into SRAM), so R never waits on a consumer and every read
  completes. The write port has its own engine. Within g2d_pix the consumers wait for producers (the
  join waits for paint, destination and coverage; the write combiner for pixels), but no producer waits
  for a read that a consumer must free first: all reads complete unconditionally, so the pipeline always
  drains. Checked by `test_path.axi08_path_deadlock` (150–200-clock latency, 30 % ready on all five
  channels, paths with edge fetch, destination read and write-back, blits and fills in one stream).
- **Write producer** (`g2d_axi_wr`, single AWID 0, in-order B): request = byte address + length − 1; the
  producer then supplies exactly the DATA_W-aligned beats covering the range, each with a byte mask;
  the engine masks the bytes outside the range. Bursts produced while the soft reset is in progress (and
  after a window violation in the same request) are drop bursts: the producer's beats are consumed and
  discarded, no AW is issued, so a producer can always finish a request it started.
- **Handshake rule on these interfaces** (checked by `dv/sva`): a request or beat is held and stable
  until accepted. The only exception is a request not yet accepted that a client or producer withdraws
  while it is stopped by a soft reset (read clients, `g2d_fill`); nothing has been handed over then.

## 5. Command processor

### 5.1 Submission model (decision: command lists + kick queue)

The driver builds command buffers in memory (format in `spec/commands.md`) and submits each one by
writing `CMD_ADDR` then `CMD_KICK = size_in_bytes`. The write to `CMD_KICK` pushes `{addr, size}` into a
`KICK_DEPTH` queue. `STATUS.KICK_FREE` gives the free slots. Kicking while the queue is full is dropped and
flags `ERR_STATUS.KICK_OVF`.

The command processor pops entries in order, fetches each buffer (8-byte aligned, size a multiple of 8)
with bursts into a 2×`MAX_BURST_BYTES` prefetch FIFO, and parses 32-bit words. Chunks are
`MAX_BURST_BYTES`-aligned and requested only when the FIFO has room for all their beats. The next kick is
popped once the parser has consumed the last word of the current buffer (a FENCE at the end of a buffer
may still be waiting), so up to two buffers are out of the queue at a time; there is no prefetch across
buffers in v1.

### 5.2 Fence / sequence counter

The `FENCE` command carries a 32-bit value. It completes when every earlier draw has finished,
including all write responses (B). The value is then written to `FENCE_SEQ`, and `IRQ_STATUS.FENCE` is
set if the command requests it. The driver waits with `vg_lite_finish` by polling `FENCE_SEQ` or on the interrupt.

### 5.3 Ordering guarantee

Draws execute in command order. **A draw's memory reads observe all writes of all earlier draws.** v1
implements this conservatively: a draw that reads memory (destination read, image source, gradient ramp,
edge list) waits until the previous draw's writes have all been acknowledged. The counter `PERF_DRAIN_CYC`
measures the cost. A later optimisation may track address ranges instead.
Command fetch is not a draw read: command buffers must not be written by GPU draws of buffers that are
still queued or executing (hazard, like §13.3 of rendering.md); the CPU writes command buffers before the
kick.
State loads are applied in order with respect to draws. The pipeline keeps a shadow copy of the state,
latched per draw, so a state change after a draw never affects that draw.

Implementation (Phase 3, `g2d_core` dispatch): SOLID + blend NONE rects without dither go to the fill fast
path (`g2d_fill`; a dithered fill varies per pixel, v1.4), every other draw to the general pipeline (`g2d_pix`). A draw is issued only when both
engines are idle, so there is one write producer at a time. A `g2d_pix` draw always reads memory
(destination or source), so it also waits until every earlier write is acknowledged (`wr_idle`); those
cycles count as `PERF_DRAIN_CYC`.

## 6. Pixel pipeline

### 6.1 Throughput targets (derivation in `spec/bandwidth.md`)

| Operation | Target, DATA_W=64 | Target, DATA_W=128 |
|---|---|---|
| Fill/clear (no dst read), 32 bpp | 2 px/clk | 4 px/clk |
| Fill/clear, 16 bpp | 4 px/clk | 8 px/clk |
| Blended rect / blit (point or bilinear, affine) | 1 px/clk | 1 px/clk |
| Perspective blit | 1 px/clk (pipelined divider) | 1 px/clk |
| Path fill (≤ 8 crossing edges per pixel row) | 1 px/clk | 1 px/clk |

Steady state may be memory-bound below these figures. The targets are the compute limits.
Measured (Phase 2, ideal memory, 1024×32 ARGB8888 clear): 0.998 beats/clk at DATA_W 64 (2.0 px/clk) and
0.992 at DATA_W 128 (3.97 px/clk), counted over all busy cycles including command fetch.
Measured (Phase 3, DATA_W 64, ARGB8888, 256×64 target, pixels of the draw rectangle per busy cycle
including command fetch and setup; `test_blit.blit_throughput`):

| Draw | ideal memory | 90-clk read and B latency |
|---|---|---|
| Blended solid rect (SRC_OVER) | 0.993 | 0.979 |
| Translate copy | 0.991 | 0.975 |
| Rotated bilinear blit | 0.987 | 0.772 |
| Perspective bilinear blit | 0.973 | 0.743 |

The transformed blits under latency were below the compute limit (docs/OPEN_QUESTIONS.md Q-PERF-2).
Phase 4 tuning (source-cache lookahead FIFO 128 → 256 pixels, write-combiner span queue 8 → 32): rotated
bilinear 0.91 and perspective 0.87 px/clk at 90-clock latency, ideal memory unchanged.

Measured (Phase 4, PRF-01, `test_path.prf01_path_throughput`; DATA_W 64, ARGB8888, SRC_OVER, AA, pixels
of the clipped bbox per busy cycle including command fetch, edge load and band setup, after a warm-up
draw):

| Draw | ideal memory | 90-clk read and B latency |
|---|---|---|
| Circle 60×60 (glyph-sized, ≤ 8 crossings per row, 76 edges) | 0.833 | 0.773 |
| Circle 252×126 (≤ 8 crossings per row) | 0.948 | 0.926 |
| Quad 16 px wide, 128 rows | 0.649 | 0.614 |
| Star, 41 points (~40 crossings per row) | 0.960 | 0.945 |
| Linear gradient rect 256×128 | 0.994 | 0.986 |

Measured (v1.5, `test_path.prf02_image_path_throughput`; DATA_W 64, ARGB8888, SRC_OVER, bilinear 256 × 64 image;
pixels of the draw rectangle per busy cycle): a transformed image as the paint of a rectangle path (scanline order)
against the same blit (16 × 16 tiles):

| Draw | ideal: blit / path | 90-clk latency: blit / path | line misses blit / path |
|---|---|---|---|
| Rotated (≈ 18°, scale 0.95) | 0.885 / 0.937 | 0.619 / 0.851 | 910 / 835 |
| Rotated 45° | 0.910 / 0.933 | 0.690 / 0.896 | 537 / 430 |
| Perspective | 0.845 / 0.857 | 0.547 / 0.699 | 1086 / 1024 |

The scanline order of image paths costs nothing (the XOR-folded set index keeps the misses below the tiled
blit's), so no traversal change was built (v1.5 plan item 2, criterion: path ≥ 85 % of the blit, asserted by the
test). The blended tiled blit is the slower one under latency: a 16-pixel tile row is one 64-byte destination read,
so the destination reader is latency-bound (the Phase 3 / 4 figures above are blend NONE, no destination read);
recorded in OPEN_QUESTIONS Q-PERF-2 for v1.7.

v1.7 (Q-PERF-2): the cause was the shared read port, not the tile shape: with 8 outstanding bursts the destination
reads and the source line fills waited for each other. `RD_OUTSTANDING` 16 and 16 destination spans in flight give
(same test, ideal: blit / path, 90-clk latency: blit / path): rotated 0.889 / 0.939, 0.864 / 0.912; rotated 45°
0.923 / 0.933, 0.894 / 0.915; perspective 0.850 / 0.860, 0.833 / 0.850. The perspective blit is bounded by the
texel path (its ideal-memory rate), not by memory.

Per pixel row and 256-px segment the rasterizer needs max(pass, sweep) cycles: a pass is the active
edges plus ~20 cycles (it starts when the previous pass has left the 16-stage step pipeline), the sweep
is the segment width. Narrow shapes are pass-bound. Band setup (a scan of the edge buffer and the band
divisions, ~100 cycles per 16-row band for 76 edges) is overlapped since v1.7: the active list and the state
RAM are kept per parity, the first band is set up while the list loads, and the next band's scan runs during
the current band's rows on the edge-buffer read cycles the passes leave free (not with streaming or one-row
bands, which reload the buffer). The per-draw edge load (2 beats per edge at DATA_W 64) is not overlapped with
the previous draw: glyph-sized paths reach 0.867 px/clk (64 px, ideal memory; docs/STATUS.md K-1,
docs/OPEN_QUESTIONS.md Q-PERF-3).

### 6.2 Traversal

- RECT without image transform: scanline order, spans split at 4 KB / burst boundaries (fill fast path)
  or at 256-byte-aligned blocks (general pipeline, below).
- Transformed blits: the draw rectangle is walked in **16×16 destination tiles** (row-major tiles,
  scanline inside a tile) so the source cache working set stays small under rotation.
- As built (Phase 3, `g2d_walk`): the general pipeline cuts the clipped rectangle into spans of one row
  whose bytes lie in one 256-byte-aligned block (at most 256 bytes). Blits with `XF_B ≠ 0`, `XF_D ≠ 0` or
  `PERSP` walk tiles on the 16-aligned grid; solid draws and translate/scale-only blits walk scanlines.
  Spans go to the destination reader and the write combiner, pixels (one per clock) to the paint chain.
- PATH: walked one pixel row at a time over the bbox, in segments of up to `RAST_SEG_W` = 256 px (fixed
  in Phase 4). v1.5 (F-1): also with an IMAGE paint; the walker then walks scanlines whatever the transform
  (the raster's order), so rotated images on paths sample in scanline order (cost measured in §6.1). As built: `g2d_walk` walks the clipped bbox in scanlines (no tiles); `g2d_raster` produces
  the coverage in the same pixel order (bands of 16 rows, rows, segments).

### 6.3 Stages

1. **Primitive setup**: clips the rect/bbox against target and scissor; produces span or tile walks.
2. **Coverage**: RECT gives 255. PATH (as built: `g2d_raster`, below): the edge-setup and crossing unit computes per-sample winding
   (rendering.md §8) with a winding accumulator of `RAST_SEG_W × 16` samples × 8 bits, then a
   per-pixel prefix sum and count.
3. **Paint**: solid colour; image sampler (inverse transform, optional divider, 2×2 texel fetch from the
   source cache, colour key, premultiply, bilinear); or gradient (T function, spread, 256-entry ramp RAM).
4. **Destination read**: burst reads ahead of the blend stage, matched to the span, skipped when not needed.
5. **Blend + coverage + format**: rendering.md §4 steps 3–6, fully pipelined, 1 px/clk (fill fast path
   bypasses it).
6. **Write combiner**: packs pixels into full-width beats with `WSTRB` and issues bursts.

As built (Phase 3, `rtl/pix/g2d_pix.sv`), one pixel per clock, every block a valid/ready stream:

```
g2d_walk ─► g2d_xform ─► g2d_tex_addr ─► g2d_src_cache ─► g2d_texel ─┐ S, pass
   │ spans   (U,V,W, exact  (source test,   (read client 2) (§5.2, bilinear,
   │          divide)        texel addrs)                   MULTIPLY)  │
   ├─► g2d_dst_rd (read client 1, when BLEND.MODE ≠ NONE) ──► join ◄───┘
   └─► g2d_wcomb ◄──────────────────────────────────────── g2d_blend
       (2 × 256 B span buffers, trimmed bursts, strobes)   (global alpha, 16 modes, coverage, compress)
```

- SOLID pixels also run through the paint chain (no texel lookups); at the join `S = PAINT_COLOR`,
  `cov = 255`. IMAGE: `S` from the texel unit, `cov = 255` if the pixel passes the source test (§7.4 of
  rendering.md) and 0 otherwise. All pixels stay in walk order, so one join suffices.
- POINT sampling is the bilinear datapath with zero fractions on one texel (weights 65536, 0, 0, 0).
- v1.4 as built: `g2d_xform` outputs signed 32-bit `u, v` (33 + 2 stages: |U| divided by 4W, sign applied at the
  end) with the range test of the draw's wrap mode; `g2d_tex_addr` (7 stages) maps both texel coordinates per
  axis (clamp, COLOR border flags, REPEAT / REFLECT modulo with a per-draw reciprocal from two serial dividers
  started with the draw, exact by an exhaustive model check), addresses both texel rows independently and
  computes the sub-byte shift; `g2d_tex_split` issues a quad whose texels would need two lines of one cache
  bank (only at wrap seams) as four single-texel requests (`in_smask_i`) and `g2d_tex_merge` reassembles it and
  extracts sub-byte texels; the texel unit replaces border texels by `BORDER_COLOR` after its premultiply stage
  and reports "A = 0 before MULTIPLY" for TRANSPARENT. The ramp RAM is also the CLUT: for an indexed source the
  ramp lookup sits between the merge and the texel unit (index = texel t00), otherwise after the texel unit
  (gradient index from the tag); a register slice on its output breaks the routing loop. The paint chain tag
  carries the ramp index, the FILL flag and `(x & 3, y & 3)` for the dither in the blend unit's pack stage.
- v1.6b, 24-bit sources: `g2d_tex_addr` forms the column byte as `24·x / 8` and passes, per texel, the number of
  its bytes in the first line when it starts at line offset 62 or 63 (in the sub-byte shift field). `g2d_tex_split`
  then follows the quad's request(s) with a "high" request for the next line of each such texel (one request, or
  single-texel requests when those lines conflict at a wrap seam); the cache is unchanged (it already returns zero
  past a line end) and `g2d_tex_merge` joins the two parts. Cost: about 2 / 64 of the texels need the extra
  request, so random 24-bit blits run at roughly 0.9 of the 32-bit rate; the cache tag grew by one bit (`hi`).
- v1.6b, sub-requests: `g2d_tex_seq` (one register stage between `g2d_xform` and `g2d_tex_addr`) turns a pixel
  into the 2 × 2 requests it needs, each with a texel mask (`gmask`), a group number and "end of group" / "end of
  pixel" flags in the tag: one request for every existing filter and format (no throughput change), four for the
  Gaussian (columns / rows `−1, 0` and `+1` around the point texel; masks 1111, 0101, 0011, 0001, so ¼ px/clk), and
  later one per YUV plane. `g2d_tex_split` requests only the masked texels; `g2d_tex_merge` hands a quad on at the
  end of its group; `g2d_texel` weights each group (Gaussian: `W0 / W1 / W2` by slot) and sums the groups of a pixel
  before its rounding stage, so only the pixel's last group leaves the unit. The cache tag grew by 8 bits.
- v1.6b, YUV: one sub-request per plane (`cfg_planes`); `g2d_tex_addr` takes the plane's base, stride, texel size
  and subsampling shifts (the two row multipliers are shared by the planes, which run in different cycles) and for
  YUY2 passes the luma column parity in the shift field; `g2d_tex_merge` collects the planes' bytes into one
  `{A, Y, U, V}` word per texel and hands it on with the last plane; `g2d_texel` converts it in S0 (five constant
  products per texel, BT.601 / BT.709 by a mux). NV12 / AYUY2 run at ½ px/clk, ANV12 and the three-plane formats at
  ⅓, YUY2 at 1. The YUY2 / NV chroma alignment rule keeps every sample inside one 64-byte line.
- v1.6b, colour unit: three stages in `g2d_texel` between the filter sum and MULTIPLY: gamma (two 256-entry tables,
  `g2d_gamma_pkg`, generated by `scripts/gen_gamma.py`), 16 products (signed 16 × 8 bit), sums with bias, `>>> 8`
  and clamp; the texel unit grew from 6 to 9 stages. Solid colours and ramps are transformed by the driver.
- The write combiner issues one write request per span, trimmed to its first..last written byte, with
  zero strobes on skipped pixels; a span with no written pixel issues nothing. The destination reader
  reads whole spans (Phase 3 assumption A4: extra read traffic only, the memory image is the same).
- Soft reset: the walker starts no new span, the join stops waiting for destination data, the cache
  for fills and gradient pixels for their ramp (B-5.5), the write combiner completes every span it started (the write engine drops the bursts),
  then `soft_clr` clears the engine.

### 6.4 On-chip memories (initial sizes; justification in `spec/bandwidth.md`)

| Memory | Size | Organisation |
|---|---|---|
| Command prefetch FIFO | 512 B | 2 × 256 B, `g2d_fifo_ram` (SRAM + 2-entry flop output stage) |
| Source texel cache | 8 KB | 4 banks × 8 sets × 4 ways, 64 B lines, LRU, read-only, invalidated per draw (§5.3 keeps it coherent); details below |
| Destination read buffer | 1 KB | 4 × 256 B bursts in flight |
| Mask read buffer (v1.6) | 512 B | the mask reader's embedded destination reader (A8 spans) |
| Write combine buffer | 512 B | 2 × 256 B span buffers (SRAM data, flop byte strobes) |
| Source cache pixel FIFO | 3.5 KB | 256 × 107 bit, latency-hiding queue between tag and data stage (Phase 4: 128 → 256; v1.4: the tag grew from 8 to 34 bits: request mask, sub-byte shifts, border flags, FILL, dither position) |
| Gradient ramp / CLUT | 1 KB | 256 × 32 bit in DATA_W/32 banks; reuse rule below; also the colour table of indexed sources (v1.4) |
| Edge buffer | 12.6 KB | 512 edges × 202 bit (constants: Xa, Ya, Yb, dx, dy, dir, sample and row steps) |
| Edge state | 3.3 KB | 512 × 52 bit (crossing state xt, r of the active edges) |
| Active list | 0.6 KB | 512 × 9 bit |
| Winding accumulator | 8 KB | 2 buffers × 16 banks × 256 × 8 bit (double-buffered, Phase 4 A6) |
| Edge / beat FIFOs | 1 KB | edge loader 512 B, destination 1 KB (above), command prefetch 512 B (above) |
| **Total** | **≈ 45 KB** | Single-port / 1r1w SRAM macros on ASIC, BRAM on FPGA (Q-RTL-7: edge buffer is the largest) |

**Path rasterizer (Phase 4, `rtl/raster`).** Same structure as the golden model's `PathRaster`
(model/src/raster.cpp): `g2d_edge_load` reads the list (read client 3), clamps, scales to 1/32 px, orients,
drops edges that cannot cross a sample of the clipped bbox and computes the per-edge steps
`S·dx = qs·dy + rs` (sample rows) and `32·dx = qr·dy + rr` (pixel rows) on two `g2d_sdiv`. Per 16-row band
the active edges get their crossing state at the band's first sample row from one floor division
(`g2d_sdiv`, 44-bit dividend, 31-bit quotient); per row and 256-px segment every active edge passes a
16-stage add-and-compare step pipeline (one stage per sample row) whose events go to the winding
accumulator (`g2d_wind_acc`, read-modify-write with forwarding), and the sweep turns the 16 running
mod-256 sums into coverage. Longer lists (Phase 4 Q1 a): with more kept edges than one pass holds
(`CHUNK` = 512), the list is re-read from memory per band; with more active edges in a band, that band
runs one row at a time and each segment is accumulated over several passes (chunks of the row's active
edges in list order). The split cannot change the result (mod-256 sums), checked with forced split points.

**Blend unit (v1.6).** `g2d_blend` has 13 stages (was 5): the §5.7 family operands in S1, numerators / result alpha /
Porter-Duff result in S2, eight restoring-division stages (B.c = (n·255 + ra/2) / ra, quotient < 256 when n < ra),
then coverage blend and pack; the VG_LITE result rides along the division stages. The stencil mode (§5.3.1) forms its
source and per-channel alphas in S0 and uses the per-channel alpha in the S1 / S2 products. Throughput stays 1 px/clk.
Target mirror (§5.8): the walker and the fill engine get a flipped base address and a negative 32-bit row step.

**Radial gradient (v1.5, F-4).** `g2d_grad` has two paths chosen per draw: linear (2 stages, as before) and radial
(37 stages: six 32 × 15 products for T, U, V, sums with `>> 9` and saturation, two 32 × 32 squarers, a 64-bit add, the
32-stage `g2d_isqrt` (restoring, one result bit per stage), then `(T >> 9) + S >> 8` and the shared spread logic,
rendering.md §10.4). Both run under the same global stall, so throughput stays 1 px/clk; the radial path adds
latency only. The index then uses the existing ramp, loader and reuse rule.

**Gradient (Phase 4, `rtl/gradient`).** `g2d_grad` computes the ramp index (rendering.md §10.1) right after
the walker; the 8-bit index travels through the paint chain as its tag, and `g2d_ramp` looks it up at the
end. The ramp (read client 4, one 1024-byte request) is reused by a later draw with the same
`GRAD_RAMP_ADDR` until a kick is popped or the GPU accepts a write; neither affects the draw that is using
it. The CLUT of an indexed source (v1.4) is loaded the same way from `CLUT_ADDR` under the same rule (one copy,
keyed by address: a gradient ramp and a CLUT at the same address share it). Driver rules: docs/PROGRAMMING.md.

**Source cache (Phase 3 decision Q1 a: latency-hiding).** A texel's bank is {texel row parity, line
address parity}; the up-to-four texels of a bilinear quad are then in distinct banks or share a line, so
every bank serves at most one line per pixel (one 512-bit SRAM read per bank per clock). The set index
is an XOR fold of the line address: with power-of-two strides a direct index maps a column of a rotated
tile walk onto one set (measured: 1519 instead of 910 fills, 0.48 instead of 0.77 px/clk at 90-clock
latency). The tag stage runs ahead of the data stage through a 128-pixel FIFO: a miss allocates the LRU
way among ways no queued pixel refers to (per-way lock counts), queues a 64-byte fill (read client 2)
and marks the way pending; the data stage pops a pixel once all its lines are filled. Fills are always
accepted, so the tag stage may stall but cannot deadlock. Untransformed copies use the same cache in
scanline order (64-byte fills); there is no separate streaming path.

## 7. Interrupt

`irq_o = |(IRQ_STATUS & IRQ_ENABLE)` (level, default). With `IRQ_PULSE` (v1.9): one-cycle pulse in each cycle in
which a bit of `IRQ_STATUS & IRQ_ENABLE` becomes set; a further new bit while a pulse is out gives its own pulse
after one low cycle (edge-triggered controllers count edges); clearing bits gives no pulse. `IRQ_ACTIVE_LOW`
inverts the output. Sources: `FENCE` (a fence with the IRQ flag completed), `ERROR`
(any `ERR_STATUS` bit set), `IDLE` (kick queue empty and pipeline idle, edge-triggered on entering idle).
`IRQ_STATUS` is write-1-to-clear.

## 8. Error handling

| Error (`ERR_STATUS` bit) | Cause | Response |
|---|---|---|
| `CMD_DECODE` | Unknown opcode, bad payload length, command crosses the buffer end | Command processor halts |
| `BAD_STATE` | Draw state invalid (rendering.md §13.1) | Draw skipped before any memory access; command processor halts |
| `ADDR_RANGE` | Draw or command-buffer extent outside `ADDR_WIN` (rendering.md §13.2) | Draw skipped before any memory access; command processor halts |
| `AXI_RD`, `AXI_WR` | SLVERR/DECERR | Data discarded, command processor halts after draining |
| `KICK_OVF` | Kick while the queue is full | Kick dropped; processing continues |

On a halting error the GPU sets `STATUS.HALTED` and stops the command side: no new kick is popped and
no further command is executed. Draws issued before the error complete, and every outstanding AXI
transaction drains. Where the command stream stops:
- `CMD_DECODE`, `BAD_STATE`, `ADDR_RANGE`: at the failing command (it has no effect).
- `AXI_RD` on command words: at the first command containing a word that came back with the error; the
  commands before it execute normally (the error is reported when the response arrives, possibly while
  the parser is still working on earlier commands). Until the parser reaches the faulting word, or has
  consumed every command word that can still arrive (the fetch issues no new read after the halt), the
  command side counts as active: the core is not drained and `STATUS.IDLE` stays low (B-6.4). So with
  no further kick queued, `HALTED = 1, IDLE = 1` means the halt has settled. With kicks queued, IDLE
  stays low, and `STATUS.CMD_ACTIVE` (v1.4, Q-RTL-9) tells it: it is 1 while commands are still executing
  (the command side is live or an engine / AXI transaction is not drained) and does not look at the kick
  queue, so `HALTED = 1, CMD_ACTIVE = 0` means the halt has settled. Software that soft-resets as soon as it
  sees HALTED (the driver does) abandons the remaining earlier commands, which is allowed.
- `AXI_WR`: write responses are asynchronous to the command stream; the parser stops at the command it is
  working on when the response arrives (a draw waiting to be issued or a FENCE waiting is abandoned).
`ERR_ADDR` holds the faulting memory address (AXI errors: the first byte the client requested in the
faulting burst, i.e. the request address for its first burst, else the burst address) and
`ERR_CMD_ADDR` the address of the command header being executed (for AXI errors: the one the parser is at when the response arrives; if the parser has
already consumed the whole kicked buffer, the address just past its end). The first halting error
wins; later errors only OR their bits into `ERR_STATUS` (same cycle: command errors before AXI errors,
window before read before write). `KICK_OVF` never captures addresses. Clearing `ERR_STATUS` does not
clear `HALTED`. Recovery is a soft reset (§2.2). Errors never hang the AXI interfaces.

Stride/alignment contract checked by `BAD_STATE`: buffer address aligned to the pixel size
(1/2/4 bytes; sub-byte sources any byte), `stride` a multiple of the pixel size, `stride ≥ ceil(width × bpp/8)`,
`stride < 2^16`, `1 ≤ width, height ≤ 8192`. 64-byte alignment is recommended for performance but not required.

## 9. Performance counters

32-bit, wrapping, enabled by `PERF_CTRL.EN`, cleared by `PERF_CTRL.CLEAR`, frozen into shadow registers
by `PERF_CTRL.SNAPSHOT` so they can be read coherently: `CYCLES`, `BUSY_CYCLES`, `DRAWS`, `PIXELS_OUT`,
`RD_BEATS`, `WR_BEATS`, `RD_STALL` (AR or R back-pressure), `WR_STALL` (AW, W or B back-pressure),
`DRAIN_CYC` (§5.3), `SRC_CACHE_MISS`. At 600 MHz the counters wrap after 7.1 s, so the driver samples
them once per frame. `RD_STALL` = cycles with ARVALID && !ARREADY or RVALID && !RREADY; `WR_STALL` = cycles
with AWVALID && !AWREADY, WVALID && !WREADY or BVALID && !BREADY. `PIXELS_OUT` counts pixels written
(requested rows of the fill path). `DRAWS` counts completed draw commands including empty ones, not
failing ones. CLEAR and SNAPSHOT in one write: the snapshot holds the values before the clear. Counters
survive a soft reset. `DRAIN_CYC` counts cycles in which a general-pipeline draw waits for earlier
writes to be acknowledged (§5.3); `SRC_CACHE_MISS` counts 64-byte source line fills (up to four per
pixel). `PIXELS_OUT` counts pixels written by either engine (pixels skipped by the source test are not
written and not counted).

### 9.1 Debug: RAM collisions and write signature (v1.8, Q-FPGA-10)

Two always-built aids for finding faults that only appear on hardware (simulation passes, the board does not):

- **RAM collisions.** Every `g2d_ram_1r1w` reports a read and a write of the same address in the same cycle
  (`coll_o`; the data read is undefined on a real RAM, so the design never does it). The flags are ORed per RAM group
  into `DBG_STATUS.RAM_COLL` (sticky, write 1 to clear; bits listed in `spec/regs.yaml`). FIFOs built on the RAM
  (`g2d_fifo_ram`) cannot collide (a read needs an entry, a write needs room) and are not reported. Simulation also
  stops on a collision (`$error` in the wrapper); on hardware the bit is the only trace.
- **Write signature.** `WR_SIG` is a CRC-32 (zlib.crc32) over the bytes of every W beat the core issues, lanes with
  WSTRB = 0 counted as 0x00, from reset or `DBG_CTRL.SIG_CLEAR`; a soft reset does not touch it. For the same command
  stream the core issues the same W beats in the same order whatever the memory latency (one write producer at a time,
  §5.3, and bursts split by address only, §4.2), so the value after each draw can be compared between the SoC
  simulation and the board to find the first draw that differs.

## 10. Address map assumption

GPU addresses in commands and state are 32-bit (the VGLite API uses `vg_lite_uint32_t`). The AXI
address is `{MEM_BASE_HI[ADDR_W-33:0], addr32}` when `ADDR_W > 32`. On Cheshire/Genesys2 the DRAM
region is `0x8000_0000–0xFFFF_FFFF` (AddrWidth 48, `MEM_BASE_HI = 0`); see docs/OPEN_QUESTIONS.md Q-FPGA-2.
Phase 7 integration (docs/FPGA.md): `ADDR_W 48`, `ID_W 3`, CSR window `0x4800_0000` (4 KB), interrupt = PLIC source 58.

## 11. RTL coding rules

- SystemVerilog-2017 synthesizable subset. `always_ff` / `always_comb` only, no latches, every
  `always_comb` assigns defaults first. `unique case` with a default.
- One module per file, file name = module name. Packages in `rtl/pkg`.
- Ports `*_i`, `*_o`; active-low `*_n*`; registers `*_q` with next-state `*_d`.
- Valid/ready handshakes on every internal stream. No combinational path from `ready_i` to `valid_o`
  inside a module unless documented.
- Verilator `--lint-only -Wall` clean, with no blanket waivers; each waiver is in `rtl/lint.vlt` with a reason.
- No vendor primitives in `rtl/`. SRAMs go through a `g2d_ram_1r1w` / `g2d_ram_sp` wrapper (behavioural
  model in `rtl/`, macro or BRAM mapping in `syn/` and `fpga/`).

## 12. Repository layout

`spec/` specifications · `model/` C++ golden model and pybind11 bindings · `rtl/` SystemVerilog ·
`dv/` cocotb testbenches, AXI models, scoreboards, coverage · `sw/` vg_lite.h, HAL, drivers, library,
tests, LVGL port · `fpga/` Cheshire + Genesys2 integration · `syn/` ASIC synthesis/STA/LEC ·
`docs/` status, tools, open questions, guides · `scripts/` generators and runners.

RTL (Phase 4): `rtl/pkg` packages (`g2d_pkg` hand-written, the others generated) · `rtl/common` FIFO,
register slice, arbiter · `rtl/csr` AXI4-Lite adapter, control, perf, generated register file ·
`rtl/axi` AXI4 master (read/write engines) · `rtl/cmdproc` fetch, parser, state, draw check ·
`rtl/fill` rect fill · `rtl/blend` blend unit · `rtl/blit` transform/divider, texel addressing, source
cache, texel unit · `rtl/pix` walker, destination reader, write combiner, general pipeline · `rtl/raster` edge loader,
rasterizer, winding accumulator · `rtl/gradient` gradient index, ramp ·
`rtl/top` `g2d_core`, `g2d_top`. `rtl/common` also holds the SRAM wrappers `g2d_ram_1r1w`, `g2d_ram_sp`
`g2d_fifo_ram` and the pipelined divider `g2d_sdiv`. DV: `dv/common` environment, AXI memory model,
CSR helper, runner, wrapper generator · `dv/tests` cocotb benches (pytest entry points) · `dv/tb`
hand-written harnesses only (port-renaming wrappers are generated into `build/dv/tb`).
