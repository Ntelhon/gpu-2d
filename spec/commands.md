# G2D command stream format (v1)

Commands live in memory buffers submitted through `CMD_ADDR` / `CMD_KICK` (arch.md §5). A buffer starts
8-byte aligned and its size is a multiple of 8 bytes. It is a sequence of little-endian 32-bit words.
Every command is one header word followed by zero or more payload words. Commands may straddle burst
boundaries but not the end of the buffer (`CMD_DECODE` error). Unused tail words are padded with `NOP`.

State register indices and fields are defined in `spec/regs.yaml` (block `state`). The generated C
header `g2d_regs.h` gives `G2D_ST_*` index and field macros. This document only describes the commands.

## 1. Header word

```
 31        24 23                                   0
┌────────────┬──────────────────────────────────────┐
│  OPCODE    │  opcode-specific                     │
└────────────┴──────────────────────────────────────┘
```

| OPCODE | Name | Header bits [23:0] | Payload words |
|---|---|---|---|
| 0x00 | `NOP` | `[15:0] SKIP`: payload words to skip | SKIP |
| 0x01 | `LOAD_STATE` | `[23:16] COUNT` (1..128), `[7:0] INDEX` | COUNT |
| 0x10 | `DRAW_RECT` | reserved, 0 | 2 |
| 0x11 | `DRAW_PATH` | reserved, 0 | 4 |
| 0x20 | `FENCE` | `[0] IRQ` | 1 |
| others | reserved | | `CMD_DECODE` error |

Reserved header bits must be 0. Non-zero reserved bits are a `CMD_DECODE` error, so they can be reused in later versions.

## 2. Commands

### 2.1 `NOP`
Skips `SKIP` payload words (any content). `SKIP = 0` is a single-word no-op.

### 2.2 `LOAD_STATE`
Writes `COUNT` payload words into state registers `INDEX … INDEX+COUNT−1`.
`INDEX + COUNT > 128` (the state size, 64 until v1.6) or `COUNT == 0` is a `CMD_DECODE` error.

**State space layout (v1.6b, 128 words; `spec/regs.yaml` is authoritative).** Growing from 64 to 128 words changed no
existing index, field, CSR or driver API: every command stream valid before is valid now and means the same; the only
difference is that `LOAD_STATE` with `INDEX + COUNT` in 65..128, a `CMD_DECODE` error before, now writes the new words.

| Indices | Contents |
|---|---|
| 0x00–0x05 | target (address, stride, size, format), clip rectangle |
| 0x08–0x0C | blend, alpha control, paint control, paint colour, rasterizer control |
| 0x0D–0x0E | Gaussian weights (v1.6b) |
| 0x10–0x17 | four colour keys |
| 0x18–0x1F, 0x38–0x3C | gradient coefficients (linear and radial), ramp address |
| 0x20–0x27 | source (address, stride, size, format, rectangle), border colour, CLUT address |
| 0x28–0x30 | 3 × 3 transform |
| 0x31–0x36 | mask layer, Porter-Duff blend function (v1.6) |
| 0x3D–0x3F | YUV planes 1 and 2 (v1.6b) |
| 0x40–0x4C | colour unit (v1.6b) |
| 0x06, 0x07, 0x0F, 0x37, 0x4D–0x7F | free (56 words) |

Only the bits of defined fields are stored, so the cost is the registers actually used. **If it must grow:** up to 256
words needs no format change (`INDEX` is 8 bits, `COUNT` 8 bits; the driver emits runs of at most 128 words today and
would split longer runs); `STATE_SIZE` in `regs.yaml` is the only edit, and the generated RTL, model and driver follow.
Beyond 256 words the header has no room: a second `LOAD_STATE` opcode with a bank number (reserved header bits [15:8],
which must be 0 today, so old streams stay valid) would be the extension. State is persistent across draws and across
command buffers, and is cleared to the `regs.yaml` reset values only by hardware or soft reset. Writes to
reserved indices are ignored; reserved fields must be written as 0.

### 2.3 `DRAW_RECT`
```
payload[0] = X0[15:0] | Y0[31:16]     (signed 16-bit, inclusive)
payload[1] = X1[15:0] | Y1[31:16]     (signed 16-bit, exclusive)
```
Draws the rectangle `[X0,X1) × [Y0,Y1)` with the current paint (`PAINT_CTRL.SRC`), blend, alpha and
clip state (rendering.md §4–7). With `SRC = IMAGE` this is a blit: the rectangle is the destination
bounding box and the source coverage test applies (rendering.md §7.4). `X1 ≤ X0` or `Y1 ≤ Y0` is an
empty draw (valid, no-op).

The state is validated at the draw, before any of its memory accesses (rendering.md §13). A violation
raises `BAD_STATE` or `ADDR_RANGE`, and the draw writes nothing.

### 2.4 `DRAW_PATH`
```
payload[0] = BX0[15:0] | BY0[31:16]   bounding box, signed, inclusive
payload[1] = BX1[15:0] | BY1[31:16]   bounding box, signed, exclusive
payload[2] = EDGE_ADDR                16-byte aligned GPU address of the edge list
payload[3] = EDGE_COUNT[23:0]         number of edges, [31:24] reserved 0
```
Fills the path given by the edge list with the current paint, using `RAST_CTRL.FILL_RULE` and
`RAST_CTRL.AA` (rendering.md §8). Edge record (16 bytes, little-endian):

```
+0  int32 x0   (1/16 px, |x0| < 2^18)
+4  int32 y0
+8  int32 x1
+12 int32 y1
```
Coordinates outside the guard band are clamped by the hardware (rendering.md §8.1); there is no error.
`EDGE_COUNT = 0` is a valid no-op. Any paint source (v1.5: IMAGE and RADIAL_GRAD too, rendering.md §4, §10.4, §13.1).

### 2.5 `FENCE`
```
payload[0] = VALUE
```
Waits until every earlier draw has completed (all writes acknowledged), then writes `VALUE` to
`FENCE_SEQ`. If `IRQ = 1`, it also sets `IRQ_STATUS.FENCE`. Later commands may be fetched and parsed
while the fence waits, but no later draw writes memory before the fence completes.

## 3. Execution model summary

- Strict in-order semantics (arch.md §5.3). A draw latches the full state at its header, so a
  `LOAD_STATE` after a draw never affects that draw.
- The source cache and gradient ramp (also the CLUT of indexed sources, v1.4) are invalidated at the start of
  each draw that uses them, unless the hardware can prove they are still valid (same address and no writes
  since the last load).
  Coherence with CPU writes is the driver's job: CPU caches are flushed before kick.

## 4. Driver usage examples (informative)

Clear a 1280×720 BGRA8888 target to opaque blue:
```
LOAD_STATE idx=TGT_ADDR count=4 : addr, stride=5120, W=1280|H=720<<16, FMT=0x13
LOAD_STATE idx=CLIP_MIN count=2 : 0|0<<16, 1280|720<<16
LOAD_STATE idx=BLEND    count=4 : BLEND=NONE, ALPHA_CTRL=0, PAINT_CTRL=SOLID, PAINT_COLOR=0xFF0000FF
DRAW_RECT  0|0<<16, 1280|720<<16
FENCE IRQ=1 value=n
```
Blit with rotation: LOAD_STATE of `SRC_*`, `XF_A..XF_I`, `PAINT_CTRL{SRC=IMAGE, FILTER=BILINEAR}`, then
`DRAW_RECT` with the transformed bounding box.
Path: upload the edge list (from `vg_lite_upload_path` or into the per-frame edge arena), then
`LOAD_STATE RAST_CTRL`, `DRAW_PATH`.

The driver keeps a shadow copy of the state and only emits `LOAD_STATE` for words that changed.

## 5. Error behaviour

See arch.md §8. `ERR_CMD_ADDR` points at the header word of the failing command.
