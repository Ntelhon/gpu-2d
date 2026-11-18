# G2D programming manual

How to program the G2D 2D / 2.5D GPU: from the registers and the command stream up to the `vg_lite` driver, its
hardware abstraction layer (HAL) and its ports (bare metal, FreeRTOS, Linux). The authoritative definitions are
spec/regs.yaml (registers, generated docs/regs.md and `g2d_regs.h`), spec/commands.md (command format), spec/arch.md
(submission, ordering, errors, interrupts) and spec/rendering.md (pixel results, validation); this manual says how to
use them. Integration of the hardware (clocks, resets, buses, memories) is docs/INTEGRATION.md.

| Section | Contents |
|---|---|
| §0 | Hardware interface: bring-up, registers, command submission, fences, interrupts |
| §1 – §5 | Rules every command source must follow: coherence, internal caches, command buffers, hazards, errors |
| §6 | The `vg_lite` driver: structure, threads, timeouts, memory window |
| §7 | HAL backends: model, live RTL, bare metal, FreeRTOS, Linux |
| §8 | HAL reference and porting guide |

## 0. Hardware interface

### 0.1 Bring-up sequence

1. Reset released (arch.md §2.2). Read `ID` (`0x4732_4420`, ASCII "G2D "), `VERSION` and `HWCFG` (memory data width,
   address width, kick-queue depth, CDC options, optional features). A wrong `ID` means a wrong base address or a
   bus problem; `SCRATCH` is a free read / write register for bus tests.
2. Memory window: `MEM_BASE_HI` (bits [63:32] of every GPU address; all GPU memory lies in one 4 GB window) and
   `ADDR_WIN_LO` / `ADDR_WIN_HI` (64-byte granular; a GPU access outside raises `ERR_STATUS.ADDR_RANGE` and halts,
   rendering.md §13.2). The reset window is all 4 GB. An OS confines the GPU to its buffers here (the Linux driver
   locks the window to its DMA pool, §7.2).
3. Optional: `MEM_ATTR` (AXI cache / protection / QoS attributes of the memory port, arch.md §4.2) and `MEM_QOS`
   (outstanding-burst throttle, arch.md §4.2.1). The `vg_lite` driver sets them with `vg_lite_g2d_set_mem_attr` /
   `vg_lite_g2d_set_mem_qos` (`vg_lite_g2d.h`).
4. Interrupts: clear `IRQ_STATUS` (write ones), set `IRQ_ENABLE.FENCE` and `IRQ_ENABLE.ERROR`. The interrupt output is
   a level (default) or a pulse, active high or low, chosen at build time (arch.md §3 / §7, `IRQ_PULSE`, `IRQ_ACTIVE_LOW`).
5. The GPU is idle (`STATUS.IDLE = 1`) and waits for kicks.

### 0.2 Registers in use

| Purpose | Registers |
|---|---|
| Identification | `ID`, `VERSION`, `HWCFG`, `SCRATCH` |
| Control / status | `CTRL.SOFT_RESET`; `STATUS` (`IDLE`, `HALTED`, `SOFT_RESET_BUSY`, `CMD_ACTIVE`, `KICK_FREE`) |
| Submission | `CMD_ADDR`, `CMD_KICK` (size), `FENCE_SEQ` |
| Interrupts | `IRQ_STATUS` (write 1 to clear), `IRQ_ENABLE`: `FENCE`, `ERROR`, `IDLE` |
| Errors | `ERR_STATUS` (write 1 to clear), `ERR_ADDR`, `ERR_CMD_ADDR` |
| Memory | `MEM_BASE_HI`, `ADDR_WIN_LO`, `ADDR_WIN_HI`, `MEM_ATTR`, `MEM_QOS` |
| Debug / performance | `CUR_CMD_ADDR`, `DBG_CTRL`, `DBG_STATUS`, `WR_SIG` (write signature, arch.md §9.1); `PERF_*` counters |

Drawing state (target, scissor, blend, paint, source image, transform, gradients, mask, colour unit) is not written
through the configuration port: it lives in state registers that only the command stream loads (`LOAD_STATE`, §0.3).
The full list with field layouts is docs/regs.md; C code uses the generated `sw/include/generated/g2d_regs.h`.

### 0.3 Command submission and fences

- A command buffer is a list of 32-bit words in GPU memory: a header (opcode in bits [31:24]) and its payload
  (spec/commands.md): `NOP`, `LOAD_STATE` (state registers), `DRAW_RECT` (rectangle fill / blit), `DRAW_PATH` (path
  fill from an edge list), `FENCE`. Buffers are 8-byte aligned, a multiple of 8 bytes, padded with `NOP` (§3).
- Kick: write the buffer address to `CMD_ADDR`, then its size in bytes to `CMD_KICK`. The kick enters a queue of
  `HWCFG.KICK_DEPTH` entries; `STATUS.KICK_FREE` tells how many are free (a kick into a full queue is dropped and
  flags `ERR_STATUS.KICK_OVF`, arch.md §5.1). Buffers execute in kick order, commands strictly in order (arch.md §5.3).
- Completion: end each buffer with `FENCE` carrying an increasing sequence number (IRQ bit set if the CPU waits for
  it with an interrupt). When the fence executes, every earlier draw has finished writing memory and `FENCE_SEQ`
  holds the number; with IRQ = 1 `IRQ_STATUS.FENCE` is set. Compare sequence numbers with wrap-around arithmetic.
- Example (spec/commands.md §4): `LOAD_STATE TGT_*`, `LOAD_STATE CLIP_*`, `LOAD_STATE BLEND..PAINT_COLOR`,
  `DRAW_RECT`, `FENCE IRQ=1`.

### 0.4 Interrupts

`irq_o` is asserted while an enabled `IRQ_STATUS` bit is set (level build). The handler acknowledges by writing the
pending bits back to `IRQ_STATUS` (otherwise a level interrupt fires again at once: the FreeRTOS and Linux backends do
this, §7.1, §7.2) and wakes the waiting thread; the waiter then re-reads `FENCE_SEQ` / `STATUS`. Before sleeping, a
waiter clears `IRQ_STATUS.FENCE` and re-checks `FENCE_SEQ`, so a fence that completed in between is not missed
(§6.2). `IRQ_STATUS.ERROR` means an `ERR_STATUS` bit was set: see §5.

## 1. Memory coherence: the GPU does not snoop CPU caches

The GPU reads and writes memory through its AXI4 master only. It never looks into CPU caches.

- **Before every kick** (`CMD_KICK` write), clean (write back) every CPU cache line the CPU has written
  that the GPU will read: the command buffer itself, edge lists, gradient ramps, source images, and
  any target the CPU drew into (destination reads see memory, not the CPU cache).
- **Before the CPU reads GPU results** (after `FENCE_SEQ` reached the expected value, or on the fence
  interrupt), invalidate the CPU cache lines of the GPU-written buffers.
- On a cache-coherent integration both steps are no-ops; the HAL hook stays (`vg_lite_flush_mapped_buffer`
  maps to it).
- How the `vg_lite` driver does it (v1.6, D-1, docs/FEATURES_LATER.md "Driver robustness"): at every kick it cleans
  the command buffer, the used part of its scratch arena and every scratch allocation too large for the arena
  (header and payload); ramps, CLUT copies, uploaded edge lists and converted images are cleaned when written.
  `vg_lite_finish` invalidates every target drawn since the previous finish (up to 32 remembered; a 33rd target
  makes the driver finish and invalidate first). `vg_lite_flush_mapped_buffer` cleans **and** invalidates the
  buffer (both directions). Checked by `sw/tests/test_robust.c` on a non-coherent memory model.

## 1.1 Errors and freeing GPU-visible memory (v1.6, D-2..D-4)

Memory the GPU may still read or write is never freed or overwritten before the GPU is known to be done with it:
every such path (`vg_lite_free`, `vg_lite_unmap`, uploaded-path cache drops, ramp rewrites, the converted-image
cache, `vg_lite_set_command_buffer`, `vg_lite_close`) first waits for the GPU (`drv_quiesce`). If the wait fails
(timeout or halt), the driver soft-resets the GPU and checks `STATUS.IDLE` without `SOFT_RESET_BUSY`; only then is
the memory released (the error is still returned). If the GPU cannot be confirmed stopped, the memory stays
allocated (an intentional leak), the error is returned, and the driver enters a sticky fault state: every later
call returns `VG_LITE_GENERIC_IO` until `vg_lite_close`, which then leaves GPU memory allocated as well. After a
halt the scratch of the in-flight buffers is released only after the soft reset (before v1.6 it was released
first). `vg_lite_init` records each allocation at once and frees all of them itself on a failure (D-3).

## 2. Buffers the GPU caches internally

- **Source images** (texel cache, arch.md §6.4): invalidated at the start of every draw that uses an
  image. No driver action needed beyond §1.
- **Gradient ramps** (256 × ARGB32, rendering.md §10.1, arch.md §6.4): the GPU keeps the last loaded
  ramp and reuses it for a later draw with the same `GRAD_RAMP_ADDR` until a new kick is popped or the
  GPU writes any pixel. Consequence for the driver: **write (and clean, §1) the ramp before kicking the
  buffer that uses it**, and never change a ramp in memory while a kicked buffer that uses it may still
  be queued or executing. Reusing one ramp address for different ramps across kicks is fine.
- **CLUTs** of indexed sources (v1.4, 256 × ARGB32 at `CLUT_ADDR`): the same RAM and the same reuse rule as the
  ramps (a ramp and a CLUT at one address share the loaded copy). The driver copies the table of
  `vg_lite_set_CLUT` into the arena of the command buffer that draws with it (once per buffer and call).
- **Edge lists** (`DRAW_PATH`): read during the draw; same rule as ramps (write before the kick, do not
  modify while queued or executing).

## 3. Command buffers

- 8-byte aligned, size a multiple of 8, padded with `NOP` (commands.md §1).
- The GPU fetches a kicked buffer while executing; it must not be modified until a later `FENCE` shows
  that it has been consumed. GPU draws must never write into a command buffer that is queued or
  executing (arch.md §5.3).

## 4. Hazards inside one draw

A draw whose source image, ramp or edge list overlaps the bytes it writes gives an unspecified result
(rendering.md §13.3). Draw-to-draw ordering is guaranteed by the hardware (a draw sees all writes of the
earlier draws, arch.md §5.3), so render-to-texture needs no fence between the draws.

## 5. Errors and recovery

On `STATUS.HALTED` (any halting `ERR_STATUS` bit), read `ERR_STATUS`, `ERR_ADDR`, `ERR_CMD_ADDR`, then
recover with a soft reset (`CTRL.SOFT_RESET`, wait for `STATUS.SOFT_RESET_BUSY = 0`). The state
registers are cleared by the soft reset: the driver re-emits its full state (its shadow copy) before the
next draw (arch.md §2.2, §8).

After an `AXI_RD` error on command words the GPU still executes the commands before the faulting word
(arch.md §8). With no further kick queued, `STATUS.HALTED = 1` together with `STATUS.IDLE = 1` means it has
finished them (B-6.4); a soft reset issued earlier abandons them. The v1 driver soft-resets as soon as it
sees HALTED and reports the error to its caller, so it never relies on those commands.

## 6. The vg_lite driver (Phase 6, `sw/driver/vg_lite`)

C99, no libm, no allocation outside the HAL. Public API: the `vg_lite.h` subset of spec/api-subset.md.

- **Command buffers**: two buffers (`DRV_NBUF`) of 64 KB each (`vg_lite_set_command_buffer_size` before
  `vg_lite_init`; or, after it, the caller's memory split into two halves with `vg_lite_set_command_buffer`, v1.3),
  each with a 256 KB scratch arena for edge lists, ramps and converted images (larger
  requests get a dedicated allocation owned by the buffer). A buffer is closed with `FENCE` (IRQ = 1,
  sequence number) and kicked when full, on `vg_lite_flush` and on `vg_lite_finish`; the driver then
  switches to the other buffer and waits for its fence before reusing it (and its arena). `vg_lite_finish`
  waits for both. A kick first waits for `STATUS.KICK_FREE` (kick-queue back-pressure).
- **State shadow**: every state register write goes through a shadow and is emitted only when the value
  changes; after a soft reset (error recovery) the full shadow is re-emitted (§5).
- **Coherence**: before each kick the driver cleans the command buffer and the used part of its arena
  (§1); `vg_lite_flush_mapped_buffer` cleans a whole caller buffer (call it after the CPU wrote into it).
- **Errors**: validation of rendering.md §13 happens in the driver first (deterministic error codes, no
  draw emitted); a GPU halt is reported as `VG_LITE_GENERIC_IO` after the recovery soft reset; a fence that
  never arrives gives `VG_LITE_TIMEOUT` after 10 s (simulated time on simulation backends).
- **Driver-only features** (no hardware support needed, rendering.md §9): patterns on rectangular paths
  (§9.5: one blit with the hardware wrap mode since v1.4), 24-bit source formats RGB888 / BGR888 / *8565 / *5658
  (§9.6: converted into GPU memory, cached by address, format, size, stride and a 64-bit content hash; an
  unchanged image is converted once, the cache is flushed when full only after the GPU has finished with every
  entry; until v1.6; the 16 / 8-bit, sub-byte and indexed formats are read by the hardware since v1.4), solid strokes (§9.7:
  stroke outline built by `sw/lib/geom`, drawn as a non-zero fill; caps, joins, miter limit; dashes since v1.3,
  §9.7.1), arcs and `VLC_OP_BREAK` (§9.2.1, v1.3), projective path matrices (§9.8, v1.3: flattened, clipped at
  the near plane and projected by the driver). Everything else outside the v1 subset returns `VG_LITE_NOT_SUPPORT` (list:
  docs/LVGL_SURVEY.md, gaps: docs/FEATURES_LATER.md).

### 6.1 Threads and tasks (v2.0, audit C-1)

Every public `vg_lite_*` function runs between `g2d_hal_lock()` and `g2d_hal_unlock()`
(`sw/driver/vg_lite/drv_api.c`, generated by `scripts/gen_drv_api.py` from the API headers and the driver sources):
several threads or RTOS tasks may call the driver, one call at a time. The driver's own calls between API functions
use the internal names (`drv_api.h`), so the lock is never taken twice. Without an OS the driver's weak no-op hooks
are linked. Rules:
- the lock is a plain (non-recursive) mutex; it is never taken in interrupt context, and the interrupt handler of
  the backend only wakes the waiting task;
- the API's settings that persist between calls (scissor, blend extras, colour transform, mask layer, gamma,
  dither, command-buffer size) are process-wide: tasks that need different settings set them in the same task,
  or serialize their sequences;
- buffers are owned by the caller: two tasks must not draw into the same target at the same time without their own
  synchronization (the GPU keeps the order of one task's draws, `vg_lite_finish` waits for everything).
`sw/tests/test_threads.c` runs four threads at once and requires every thread's result to equal its single-thread
result, no nested locking and one thread inside the driver at a time.

### 6.2 Timeouts, interrupts and the memory window (v2.0, audit C-3, C-5, C-6)

- With a HAL clock (`time_us`) the 10 s timeout measures time; without one the driver counts `DRV_WAIT_US`
  intervals of `hal->wait`.
- `hal->wait` sleeps until the G2D interrupt or the interval ends: every command buffer ends with an IRQ fence,
  `IRQ_ENABLE.FENCE` / `ERROR` are set at `vg_lite_init`, and the fence wait re-arms `IRQ_STATUS.FENCE` before it
  sleeps, so a wake-up is never lost. A polling backend just delays.
- `hal->mem_base_hi` gives bits [63:32] of all GPU memory (one 4 GB window); the driver writes `MEM_BASE_HI`.

## 7. HAL backends (`sw/hal/g2d_hal.h`)

The driver talks to a GPU only through `g2d_hal_t` (CSR read / write, contiguous GPU memory, cache clean /
invalidate, wait). The backend is chosen at link time; the driver code is identical on all of them.

| Backend | Where | Use |
|---|---|---|
| Software (golden model) | `sw/hal/model` | CSRs emulated over the C++ model, a kick executes at once. `G2D_RECORD=<file>` records every kick with the memory it reads (format in `g2d_hal_model.h`), replayed on the RTL by SYS-06; `G2D_MODEL_IDENTITY=1` makes GPU address = host pointer (LVGL). |
| Live RTL | `dv/live` (`make live`) | The verilated `g2d_top` with the SVA checkers, a C++ AXI4-Lite master and AXI4 memory. `G2D_LIVE_PROFILE` = `random` (default; seeded by `G2D_LIVE_SEED`) / `ideal` / `latN`, `G2D_LIVE_STATS=<file>`, `G2D_LIVE_CYCLES_PER_US` (default 100). An SVA or protocol failure exits with code 3. |
| Bare metal (Cheshire) | `sw/hal/baremetal` (`make baremetal-check`, `make fpga-sw`) | MMIO; polling, or (v2.0, `G2D_HAL_IRQ=1`) PLIC interrupt + WFI with a CLINT timeout (`g2d_platform_wait_irq` in `sw/apps/baremetal/syscalls.c`); timeouts from CLINT mtime (`g2d_platform_time_us`); `mem_base_hi` = the pool's 4 GB window; pool with GPU address = low 32 bits of the CPU address (`.bss` array, or a fixed range with `G2D_POOL_ADDR`). Build-time `G2D_CSR_BASE`, `G2D_POOL_BYTES`, `G2D_POOL_ADDR`; weak hooks `g2d_platform_cache_clean`, `g2d_platform_cache_invalidate`, `g2d_platform_delay_us`. On Cheshire (Phase 7, docs/FPGA.md): CSR `0x4800_0000`, pool `0x8100_0000` (32 MB), the default `fence` hooks are correct (CVA6 write-back D-cache flushed and invalidated by `fence`), delay from CLINT mtime (`sw/apps/baremetal/g2d_fpga_test.c`). |
| Linux user space | `sw/hal/linux` over the kernel driver `sw/linux` (v2.0, §7.2) | ioctls on `/dev/g2d`; memory from the driver's DMA pool (mmap); interrupt wait in the kernel; pthread mutex as the driver lock. |

### 7.1 FreeRTOS (v2.0, `sw/hal/freertos`)

A thin layer over a device HAL (bare metal on Cheshire, the model HAL on the host); kernel FreeRTOS V11.3.3
(`scripts/fetch_freertos.sh`, MIT, not stored here).
- `g2d_freertos_init()` once before the first `vg_lite` call; it creates the driver mutex and the interrupt semaphore.
- `g2d_hal_lock` / `g2d_hal_unlock`: the mutex (§6.1); inactive before the scheduler starts.
- `g2d_platform_wait_irq`: the waiting task blocks on the semaphore (other tasks run) until the G2D interrupt or the
  timeout (at least one tick). The platform's machine-external-interrupt handler claims the PLIC and calls
  `g2d_freertos_irq_from_isr()` for the G2D source (`sw/apps/freertos/main_chs.c`:
  `freertos_risc_v_application_interrupt_handler`, PLIC source 58).
- Time: the platform clock (CLINT `mtime` on Cheshire); `G2D_FREERTOS_TIME=1` uses the tick count instead.
- Cheshire configuration: `sw/apps/freertos/FreeRTOSConfig.h` (CLINT `mtime` / `mtimecmp`, 1 MHz RTC, 1 kHz tick);
  the application sets `mtvec` to `freertos_risc_v_trap_handler` before `vTaskStartScheduler`.
- Tests: `sw/tests/freertos/test_freertos.c` (host, POSIX port: four tasks at once give the single-task CRCs; fails
  without the mutex), `fpga/scripts/build_freertos_sw.sh` + the SoC simulation (Cheshire, RISC-V port: CRCs equal to
  the host model's, interrupts taken).

### 7.2 Linux (v2.0, `sw/linux`, `sw/hal/linux`)

Two parts: a kernel platform driver that owns the device and a DMA pool, and a user-space HAL that the unchanged
vg_lite driver links with. Licences: kernel part GPL-2.0-only OR MIT, binding GPL-2.0-only OR BSD-2-Clause (LICENSE).

**Kernel driver** (`sw/linux/g2d_drv.c`, `g2d.ko`):
- Device tree: `compatible = "g2d,g2d"`, one 4 KB `reg`, one `interrupts` (binding `sw/linux/bindings/gpu/g2d.yaml`,
  Cheshire node `sw/linux/dts/g2d-cheshire.dtsi`: `0x4800_0000`, PLIC source 58). Probe checks `ID` = `0x47324420`.
- Memory confinement: one coherent DMA pool at probe (`pool_mb`, default 32, halved down to 4 MB on failure; it must
  not cross a 4 GB boundary). `ADDR_WIN_LO` / `HI` and `MEM_BASE_HI` are set to exactly the pool and cannot be written
  from user space (`EPERM`), so the GPU reaches no other memory, whatever command buffers user space builds. A buffer
  is an offset in the pool: GPU address = `pool_gpu` (from `INFO`) + offset, CPU address = mmap base + offset.
- One opener at a time (`EBUSY`): the vg_lite driver owns fences and soft-reset recovery. Threads of that process
  share it through the driver lock (§6.1). Several processes would need a kernel command scheduler (not in v2.0).
- ioctls (`sw/linux/uapi/g2d_ioctl.h`, `G2D_IOCTL_VERSION` 1): `INFO` (ID, version, pool size, GPU base, `mem_base_hi`),
  `REG_READ` / `REG_WRITE`, `ALLOC` / `FREE` (pool ranges, freed when the file is closed), `WAIT` (until the
  interrupt count differs from the caller's last seen count, or the timeout), `SYNC`; `mmap` maps the pool.
- Interrupt: level, acknowledged in the handler (`IRQ_STATUS` written back), counted, waiters woken.
- `SYNC` is a full fence (`mb()`): the pool is coherent DMA memory; on Cheshire's CVA6 the fence also writes back and
  invalidates the D-cache (OPEN_QUESTIONS Q-LNX-1).
- Access: give `/dev/g2d` (mode 0660) to trusted users only, as for UIO: the window confines GPU memory accesses,
  not every effect of the register interface (e.g. a soft reset).

**User-space HAL** (`sw/hal/linux/g2d_hal_linux.c`): `$G2D_DEVICE` or `/dev/g2d`; CSRs, memory, cache and wait
through the ioctls above; `time_us` from `CLOCK_MONOTONIC`; `mem_base_hi` from `INFO`; lock = pthread mutex.

**Build and checks** (the board boot is done on the board, not here):
- `make linux-kmod`: builds `g2d.ko` W=1 against each tree in `KDIRS` (default Linux v5.10.7 with the cva6-sdk
  patches and `linux64_defconfig`, the Cheshire kernel, and v6.12.117 LTS defconfig; `docs/TOOLS.md`) with
  `riscv64-unknown-elf-` and checks that every imported symbol is exported by that kernel (`scripts/build_kmod.py`).
  On the board's kernel tree: `make -C sw/linux KDIR=<tree> ARCH=riscv CROSS_COMPILE=<prefix>`.
- `make linux-dt-check`: dtschema validation of the binding and of the Cheshire device tree with the G2D node.
- `test_linux_hal` (ctest, host): the Linux HAL over a fake `/dev/g2d` with the kernel driver's semantics on the
  golden model (`sw/tests/linux/fake_g2d.c`): `EBUSY`, `EPERM` on the window registers, out-of-pool addresses
  refused, and the shared drawing sequence alone and in three threads giving the model HAL's CRCs.
- Board bring-up (user): `insmod g2d.ko`, `/dev/g2d` appears, `dmesg` shows the pool; then a vg_lite program linked
  with `sw/hal/linux` (a Linux-target RISC-V toolchain is needed for user space; not installed here).

## 8. HAL reference and porting guide

### 8.1 `g2d_hal_t` (`sw/hal/g2d_hal.h`)

| Member | Required | Contract |
|---|---|---|
| `reg_read`, `reg_write` | yes | 32-bit CSR access at a byte offset from the configuration-port base (`G2D_CSR_*`). |
| `mem_alloc` | yes | Physically contiguous, GPU-visible memory: returns the CPU pointer and the 32-bit GPU address (low half; the high half is `mem_base_hi`); `align` is a power of two >= 64. 0 on success. |
| `mem_free` | yes | Frees a `mem_alloc` buffer. The driver only frees memory the GPU has finished with (§1.1). |
| `mem_cpu` | yes (may return NULL) | CPU pointer for an existing GPU address range (`vg_lite_map`); NULL if the CPU cannot reach it. |
| `cache_clean`, `cache_invalidate` | yes (may be empty) | Write back / discard CPU cache lines of a range (§1); empty on coherent systems. |
| `wait` | yes | Sleep until the G2D interrupt or at most `timeout_us`; a spurious or early return is fine (the driver re-reads the registers). Without an interrupt: a delay of one polling interval. |
| `time_us` | optional | Monotonic microseconds; then the driver's timeouts measure time (§6.2). |
| `mem_base_hi` | optional | Bits [63:32] of all GPU addresses; written to `MEM_BASE_HI` by `vg_lite_init`. |
| `ctx` | - | Passed back to every member. |
| `g2d_hal_open` / `g2d_hal_close` | yes | Provided by the backend linked into the program (one backend per program). |
| `g2d_hal_lock` / `g2d_hal_unlock` | multi-threaded only | A non-recursive mutex around every public `vg_lite_*` call (§6.1); weak no-ops otherwise. Never called from an interrupt. |

### 8.2 Porting to a new platform

1. **Hardware facts**: configuration-port base address, interrupt line and its type (level / pulse, polarity; the
   build parameters must match the interrupt controller), the memory the GPU may use (contiguous, inside one 4 GB
   window), and whether the GPU's memory port is cache-coherent with the CPU.
2. **Start from the closest backend**: `sw/hal/baremetal` (MMIO, static pool, platform hooks), `sw/hal/freertos`
   (adds the lock and the interrupt-driven wait over a device HAL), `sw/hal/linux` (user space over the kernel
   driver `sw/linux`).
3. **Registers**: `reg_read` / `reg_write` as volatile 32-bit accesses (no write combining, no reordering across
   them: on a weakly ordered CPU the MMIO mapping must be device / strongly ordered).
4. **Memory**: a pool allocator returning 64-byte aligned blocks; GPU address = bus address of the block, low 32 bits.
   Clear new allocations, as the existing backends do (the bare-metal HAL with 64-bit stores, §7).
5. **Caches**: if not coherent, `cache_clean` must write back (not only invalidate) and `cache_invalidate` must
   discard; both by address range, rounded out to cache lines. Check with `sw/tests/test_robust.c` on the target if
   possible, otherwise with a drawing test that reads results back (§1).
6. **Waiting**: with an interrupt, the handler writes the pending bits back to `IRQ_STATUS` and wakes the waiter
   (semaphore, wait queue, WFI); without one, `wait` delays for a short interval (a few microseconds to 1 ms).
   Provide `time_us` from a free-running timer.
7. **Threads**: for an RTOS / OS, define `g2d_hal_lock` / `g2d_hal_unlock` with a mutex (FreeRTOS example:
   `sw/hal/freertos/g2d_freertos.c`).
8. **Bring-up tests**, in order: `ID` read; a clear and a CRC of the target against the host model
   (`sw/apps/baremetal/g2d_fpga_test.c` and its expected CRCs from `make fpga-sw`); the scene tests; then LVGL.
   On a new RTOS also the task test (`sw/tests/freertos`), on a new OS the threads test (`sw/tests/test_threads.c`).

### 8.3 Configuration knobs of the driver

| Knob | Where | Default |
|---|---|---|
| Command buffer size | `vg_lite_set_command_buffer_size` before `vg_lite_init`, or the caller's memory with `vg_lite_set_command_buffer` | 2 × 64 KB, 256 KB arena each (§6) |
| Fence timeout | `DRV_TIMEOUT_US` (`vg_lite_drv.h`) | 10 s |
| Wait interval | `DRV_WAIT_US` (`vg_lite_drv.h`) | 100 µs |
| Memory attributes / throttle | `vg_lite_g2d_set_mem_attr`, `vg_lite_g2d_set_mem_qos` (`vg_lite_g2d.h`) | reset values (arch.md §4.2) |
| Bare-metal build | `G2D_CSR_BASE`, `G2D_POOL_BYTES`, `G2D_POOL_ADDR`, `G2D_HAL_IRQ` (§7) | Cheshire values |

## LVGL integration notes (Phase 6)

- Enable `LV_USE_VECTOR_GRAPHIC` (with `LV_USE_MATRIX`, `LV_USE_FLOAT`): without it LVGL's VG-Lite unit skips
  every gradient fill (only a warning).
- With `LV_VG_LITE_USE_ASSERT 1` (LVGL's default) a vg_lite call that fails ends in `LV_ASSERT`, which hangs with
  the default handler. Calls the v1 driver does not support are listed in docs/LVGL_SURVEY.md; the demos in
  sw/apps/lvgl run with asserts off and their tests fail on any logged error instead.
- LVGL's VG-Lite decoder converts I1 / I2 / I4 to I8 (with `vg_lite_set_CLUT`), A1 / A2 to A8 and, as the driver
  does not report `gcFEATURE_BIT_VG_24BIT`, RGB888 / ARGB8565 to 32 bit; the driver reads A4 / ARGB1555 / ARGB4444 /
  ARGB2222 / I8 directly (v1.4). Draw targets (layers, the display) may be any 8 / 16 / 32-bit format of
  rendering.md §3.2 (v1.4: also the 4444 / 5551 / 2222 orders); sub-byte, indexed and 24-bit targets are NOT_SUPPORT.
  `vg_lite_g2d_set_pattern_color_mode` (`sw/include/vg_lite_g2d.h`) selects how `pattern_color` behaves in
  COLOR patterns (texel, default, or the v1.3 plain colour). `vg_lite_enable_dither` applies to every later draw (dithered fills take the general pipeline, slower than the
  fill fast path).
- Transformed images that LVGL clips with a fractional rectangle path (e.g. an image scaled about its centre) are
  drawn since v1.3 with centre-sampled borders (OPEN_QUESTIONS Q-API-14 (b), a listed difference of at most half a
  blend step on the one-pixel border). Dashed lv_vector strokes are drawn since v1.3; LVGL passes
  `dash_phase = width / 2`, which the driver applies (Q-API-17).
- Performance on the live RTL backend: `G2D_FRAME_STATS=<file>` makes the LVGL demo write per-frame GPU counters
  (spec/bandwidth.md §8).
