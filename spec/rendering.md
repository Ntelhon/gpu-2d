# Rendering semantics (bit-exact definition)

This document defines the result of every v1 drawing operation as integer arithmetic. The C++ golden
model implements exactly these formulas. The RTL must match the model **bit-exactly** for all inputs
inside the documented ranges. The only place a tolerance applies is comparing the *driver* (float API →
fixed-point conversion, curve flattening) against an external renderer (§12).

Notation: integers are mathematical integers unless a width is given. `>>` on a signed value is an
arithmetic shift (floor division by a power of two). `floor_div(a,b)` rounds toward −∞.
`clamp(x,lo,hi)` saturates.

## 1. Coordinate system

- Origin is the top-left of the target buffer. x grows right, y grows down.
- Pixel `(x, y)` covers the half-open square `[x, x+1) × [y, y+1)`. Its centre is `(x+0.5, y+0.5)`.
- Every operation touches a pixel at most once, and only if it passes all clip tests (§6).
- Target and source dimensions: `1 ≤ width, height ≤ 8192`.

## 2. Common integer helpers

```
div255r(v)  = (v + 128 + ((v + 128) >> 8)) >> 8         for 0 ≤ v ≤ 255*255 (= round(v/255), exact)
mul8(a, b)  = div255r(a * b)                              a, b in [0,255]
lerp8(d, s, c) = div255r(s*c + d*(255 - c))               coverage blend, all in [0,255]
sat8(v)     = clamp(v, 0, 255)
```

`div255r` equals `round(v/255)`. Ties cannot occur because 255 is odd. This is checked exhaustively in the
model unit tests.

## 3. Pixel formats

### 3.1 Byte order and naming

Memory is little-endian. VGLite format names list channels starting at the least significant bit (lowest
byte address); see `spec/vglite-reference.md` for the evidence. Internally every pixel expands to four 8-bit
channels **A, R, G, B** ("ARGB32", packed as `A<<24 | R<<16 | G<<8 | B` in the model and RTL).

### 3.2 Formats and hardware format code

The hardware `FMT` field (state register, §commands) is 8 bits: `[3:0] class`, `[5:4] order`, `[6] x_alpha`,
`[7] extended` (v1.6b: a second class space for the 24-bit and YUV sources).
For the 8888, 4444, 5551 and 2222 classes `order` names the channel order from bit 0 up: 0 = R, G, B, A;
1 = B, G, R, A; 2 = A, R, G, B; 3 = A, B, G, R (565: 0 = R, G, B; 1 = B, G, R).

| VGLite enum | FMT | bpp | Memory layout (byte 0 first) / 16-bit word bits |
|---|---|---|---|
| `VG_LITE_RGBA8888` | 0x03 | 32 | R, G, B, A |
| `VG_LITE_BGRA8888` | 0x13 | 32 | B, G, R, A |
| `VG_LITE_ARGB8888` | 0x23 | 32 | A, R, G, B |
| `VG_LITE_ABGR8888` | 0x33 | 32 | A, B, G, R |
| `VG_LITE_RGBX8888` | 0x43 | 32 | R, G, B, X |
| `VG_LITE_BGRX8888` | 0x53 | 32 | B, G, R, X |
| `VG_LITE_XRGB8888` | 0x63 | 32 | X, R, G, B |
| `VG_LITE_XBGR8888` | 0x73 | 32 | X, B, G, R |
| `VG_LITE_RGB565`   | 0x02 | 16 | bits [4:0]=R, [10:5]=G, [15:11]=B |
| `VG_LITE_BGR565`   | 0x12 | 16 | bits [4:0]=B, [10:5]=G, [15:11]=R |
| `VG_LITE_A8`       | 0x00 | 8  | A |
| `VG_LITE_L8`       | 0x01 | 8  | L |
| `VG_LITE_RGBA4444` / `BGRA4444` / `ARGB4444` / `ABGR4444` | 0x04 / 0x14 / 0x24 / 0x34 | 16 | four 4-bit channels from bit 0 in `order` (v1.4) |
| `VG_LITE_RGBA5551` / `BGRA5551` / `ARGB1555` / `ABGR1555` | 0x05 / 0x15 / 0x25 / 0x35 | 16 | 5-bit colour channels and a 1-bit A from bit 0 in `order`, e.g. BGRA5551 = [4:0] B, [9:5] G, [14:10] R, [15] A (v1.4) |
| `VG_LITE_RGBA2222` / `BGRA2222` / `ARGB2222` / `ABGR2222` | 0x06 / 0x16 / 0x26 / 0x36 | 8 | four 2-bit channels from bit 0 in `order` (v1.4) |
| `VG_LITE_A4` / `A2` / `A1` | 0x07 / 0x08 / 0x09 | 4 / 2 / 1 | A, sub-byte (§3.5); **source only** (v1.4) |
| `VG_LITE_L4` | 0x0A | 4 | L, sub-byte (§3.5); **source only** (v1.4) |
| `VG_LITE_INDEX_8` / `_4` / `_2` / `_1` | 0x0B / 0x0C / 0x0D / 0x0E | 8 / 4 / 2 / 1 | index into the CLUT (§3.5); **source only** (v1.4) |
| `VG_LITE_A8L8` | 0x0F | 16 | bits [7:0] = A, [15:8] = L (channels from bit 0 in the order of the name, §3.1); **source only** (v1.6) |
| `VG_LITE_RGB888` / `VG_LITE_BGR888` | 0x80 / 0x90 | 24 | bytes R, G, B / B, G, R; **source only** (v1.6b) |
| `VG_LITE_ARGB8565` / `VG_LITE_ABGR8565` | 0x81 / 0x91 | 24 | 24-bit value bits [7:0] = A, [12:8] = R / B, [18:13] = G, [23:19] = B / R; **source only** (v1.6b) |
| `VG_LITE_RGBA5658` / `VG_LITE_BGRA5658` | 0x82 / 0x92 | 24 | bits [4:0] = R / B, [10:5] = G, [15:11] = B / R, [23:16] = A; **source only** (v1.6b) |
| `VG_LITE_YUY2` / `VG_LITE_AYUY2` | 0x83 / 0xC3 | 16 | packed 4:2:2, bytes Y0, U, Y1, V per pixel pair; AYUY2: + alpha plane; **source only** (v1.6b, §3.7) |
| `VG_LITE_NV12` / `VG_LITE_ANV12` / `VG_LITE_NV16` / `VG_LITE_NV24` | 0x84 / 0xC4 / 0x94 / 0xA4 | 8 + chroma | Y plane + interleaved U, V plane (4:2:0 / 4:2:0 / 4:2:2 / 4:4:4); ANV12: + alpha plane; **source only** (v1.6b, §3.7) |
| `VG_LITE_YV12` / `VG_LITE_YV16` / `VG_LITE_YV24` | 0x85 / 0x95 / 0xA5 | 8 + chroma | Y, U and V planes (4:2:0 / 4:2:2 / 4:4:4); **source only** (v1.6b, §3.7) |

FMT codes not in this table, and the source-only codes used as a target, are rejected by the hardware
(`ERR_STATUS.BAD_STATE`, §13.1). The 24-bit sources (extended classes 0–2, order 0 = R in the lowest field) are
read in hardware since v1.6b (driver conversion until v1.6, §9.6): a 3-byte texel may cross a 64-byte line (only the
cache traffic differs, arch.md §6.4). Not in hardware: tiled and compressed formats (closed in v1.6).

### 3.3 Expansion to ARGB32 (read)

- 8-bit channels are used as stored.
- 5-bit `c5`: `(c5 << 3) | (c5 >> 2)`. 6-bit `c6`: `(c6 << 2) | (c6 >> 4)` (bit replication).
- 565 formats: A = 255.
- X formats (`x_alpha = 1`): A = 255; the X byte is ignored.
- A8: `(A, R, G, B) = (a, 255, 255, 255)` (OPEN_QUESTIONS Q-API-3).
- L8: `(A, R, G, B) = (255, l, l, l)`.
- 4-bit `c4`: `c4·17`; 2-bit `c2`: `c2·85`; 1-bit `c1`: `c1·255` (all equal to bit replication).
- A4 / A2 / A1: the alpha expanded as above, `R = G = B = 255` (as A8). L4: `(255, l, l, l)` with `l = c4·17`.
- Indexed: `CLUT[i]` (§3.5), an ARGB32 word used as stored.
- A8L8 (v1.6): `(A, R, G, B) = (a, l, l, l)`.
- 24-bit (v1.6b): the little-endian value of the three bytes; 888: 8-bit channels, A = 255; 8565 / 5658: the 565
  part expanded as for RGB565 / BGR565, A as stored.

### 3.4 Compression from ARGB32 (write)

- 8-bit channels: stored as is.
- To `n < 8` bits: `cn = (c·(2^n − 1) + 127) / 255` (integer division, = round(c·(2^n−1)/255)), for n = 6, 5, 4, 2, 1
  (the 1-bit alpha of 5551 is `a ≥ 128`). With `TGT_FMT.DITHER` the R, G, B channels use §3.6 instead.
- X formats: the X byte is written as 0xFF.
- A8 target: writes A only.
- L8 target: `l = (77*R + 150*G + 29*B + 128) >> 8`. A is discarded.

Reading back a value written by §3.4 (without dither) and re-expanding it with §3.3 is idempotent; the model
tests this for every target format.

### 3.5 Sub-byte and indexed sources (v1.4)

Sources with `bpp < 8` pack `8 / bpp` pixels per byte. Pixel `x` of a row lies in byte `x·bpp / 8` (integer
division) of the row, at slot `j = x mod (8 / bpp)`. `SRC_FMT.SUB_ORDER = 0`: slot `j` is bits
`[bpp·j + bpp − 1 : bpp·j]` (first pixel in the low bits, VGLite `VG_LITE_INDEX_LITTLE_ENDIAN`);
`SUB_ORDER = 1`: bits `[7 − bpp·j : 8 − bpp·(j+1)]` (first pixel in the high bits, `VG_LITE_INDEX_BIG_ENDIAN`,
and LVGL's A4 layout). `SUB_ORDER` is ignored for `bpp ≥ 8`. A row starts at a byte (`SRC_STRIDE` in bytes).

Indexed sources (`INDEX_1/2/4/8`) look the value up in a 256-entry colour table of ARGB32 words
(`A<<24 | R<<16 | G<<8 | B`, little-endian) at `CLUT_ADDR` (4-byte aligned, always 1024 bytes read,
whatever the index width). The hardware reads the table at the draw's start (same reuse rule as the gradient
ramp, commands.md §3). **Indexed sources are point-sampled**: `PAINT_CTRL.FILTER` is ignored (treated as POINT)
when the source is indexed (listed difference; VGLite's behaviour is not public).

### 3.7 YUV sources (v1.6b)

Extended FMT classes (bit 7 set): 3 = packed 4:2:2 (YUY2), 4 = semi-planar (Y plane + one interleaved U, V plane),
5 = three planes (Y, U, V). `[5:4]` is the chroma subsampling of classes 4 and 5 (0 = 4:2:0, 1 = 4:2:2, 2 = 4:4:4),
`[6]` adds an alpha plane (AYUY2 0xC3, ANV12 0xC4; no other alpha variants). Planes: plane 0 at `SRC_ADDR` /
`SRC_STRIDE`; plane 1 (classes 4, 5: the U, V plane, resp. the U plane) at `SRC_PLANE1_ADDR` /
`SRC_PLANE_STRIDE.S1`; plane 2 (class 5: the V plane; alpha variants: the A plane, 8 bit per pixel, full
resolution) at `SRC_PLANE2_ADDR` / `SRC_PLANE_STRIDE.S2`. Address and stride alignment: YUY2 / AYUY2 plane 0 a
multiple of 4 (the Y0 U Y1 V pair), the U V plane of the semi-planar formats a multiple of 2, every other plane any
byte (so no sample crosses a 64-byte line).

Texel `(x, y)` (luma coordinates after the wrap mapping of §7.5; `xs` = 1 for 4:2:0 / 4:2:2 else 0, `ys` = 1 for
4:2:0 else 0):

| Class | Y | U, V |
|---|---|---|
| 3 YUY2 | byte `2·(x & 1)` of the 4 bytes at `SRC_ADDR + y·S0 + 4·(x >> 1)` | bytes 1 and 3 of the same 4 bytes |
| 4 semi-planar | byte at `SRC_ADDR + y·S0 + x` | bytes 0, 1 at `PLANE1 + (y >> ys)·S1 + 2·(x >> xs)` |
| 5 three planes | byte at `SRC_ADDR + y·S0 + x` | byte at `PLANE1 + (y >> ys)·S1 + (x >> xs)`, byte at `PLANE2 + (y >> ys)·S2 + (x >> xs)` |

`SRC_FMT.UV_SWAP` exchanges U and V (`VG_LITE_SWIZZLE_VU`). A = the alpha plane's byte at `PLANE2 + y·S2 + x`
for the alpha variants, else 255. Chroma is taken from the sample whose block contains the texel (nearest, no
interpolation between chroma samples other than the texel filter of §7.3).

`SRC_FMT.YUV_FULL` (bit 13) is reserved for a future full-range conversion (decision 2026-10-26: reserved, not
implemented): it must be 0, and an image draw with it set is BAD_STATE (§13.1) rather than silently using video range.

Conversion (§3.3 for these classes, limited / "video" range; `SRC_FMT.YUV_709` selects BT.709, else BT.601), with
`y' = Y − 16`, `u' = U − 128`, `v' = V − 128`, arithmetic `>>`, `clamp` to [0, 255]:

```
R = clamp((Ky·y'            + Krv·v' + 512) >> 10)
G = clamp((Ky·y' − Kgu·u'   − Kgv·v' + 512) >> 10)
B = clamp((Ky·y' + Kbu·u'            + 512) >> 10)
```

| | Ky | Krv | Kgu | Kgv | Kbu |
|---|---|---|---|---|---|
| BT.601 (Kr 0.299, Kb 0.114) | 1192 | 1634 | 401 | 832 | 2066 |
| BT.709 (Kr 0.2126, Kb 0.0722) | 1192 | 1836 | 218 | 546 | 2163 |

The coefficients are `round(1024·k)` of the standard limited-range matrix (`Ky = 255/219`, chroma scale `255/224`,
`Krv = 2(1 − Kr)·255/224`, `Kgu = 2(1 − Kb)·Kb/Kg·255/224`, `Kgv = 2(1 − Kr)·Kr/Kg·255/224`, `Kbu = 2(1 − Kb)·255/224`).
The converted texel then goes through §5.2 steps 2–4 and the filter like any other source. YUV sources are
not allowed with the Gaussian filter (§13.1). Tiled YUV, `VG_LITE_YUYV` and the `*_PLANAR` 24-bit formats are
closed (layouts not public, docs/PHASES.md v1.6b question 5).

### 3.6 Dither (v1.4, `TGT_FMT.DITHER`)

Ordered 4 × 4 dither on the R, G, B channels when they are compressed to `n < 8` bits (565, 4444, 5551, 2222
targets; ignored for 8-bit channels, A8, L8 and the alpha channel). For the pixel at target `(x, y)`:

```
d  = BAYER[y & 3][x & 3],  BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]
cn = (c·(2^n − 1)·32 + 255·(2d + 1)) / 8160          (integer division, 8160 = 255·32)
```

i.e. `floor(c·(2^n−1)/255 + (d + 0.5)/16)`: never above `2^n − 1`, `c = 0` stays 0, `c = 255` stays full, and
over a 4 × 4 tile the mean equals the exact value to within 1/32 of a step. G2D definition (VGLite's dither
is not public; listed difference). The destination read for blending is not dithered.

## 4. Pixel pipeline order

For every pixel that survives clipping (§6), the hardware computes:

1. **Coverage** `cov ∈ [0,255]`: 255 for RECT and for blit pixels that pass the source test (§7.4), or
   the path coverage (§8); a DRAW_PATH with an IMAGE paint (v1.5, F-1) has the path coverage where the pixel
   passes the source test (§7.4) and 0 elsewhere. `cov == 0` → pixel skipped (no read, no write). With `SRC_FMT.TRANSPARENT` an
   IMAGE pixel whose sampled source has `A = 0` (§5.2 step 6) is skipped as well: not written (its
   destination may have been read).
2. **Source colour `S`** (ARGB32) from the paint (§5).
3. **Destination `D`**: read from the target and expanded (§3.3) when the blend mode reads D (every mode
   except `NONE`) or `cov < 255`. Then destination global alpha is applied (§5.5).
4. **Blend** `B = blend(S, D)` (§5.6).
5. **Coverage blend** per channel: `O = (cov == 255) ? B : lerp8(D, B, cov)`.
6. **Write** `O` compressed to the target format (§3.4, with dither §3.6 if `TGT_FMT.DITHER`).

## 5. Paint and blend

### 5.1 Paint sources (`PAINT_CTRL.SRC`)

| SRC | Name | S before §5.3 |
|---|---|---|
| 0 | SOLID | `PAINT_COLOR` (ARGB32, used exactly as given; the driver premultiplies if the API asks) |
| 1 | IMAGE | sampled source image (§7) |
| 2 | LINEAR_GRAD | gradient ramp lookup (§10) |
| 3 | RADIAL_GRAD | gradient ramp lookup of the radial index (§10.4, v1.5) |

### 5.2 Image source per-texel steps (IMAGE only, applied to every fetched texel before filtering)

1. Expand to ARGB32 (§3.3; indexed sources: the CLUT entry, §3.5).
2. If `SRC_FMT.force_opaque`: A = 255. (Not driven by `transparency_mode`, OPEN_QUESTIONS Q-API-11; the
   driver leaves it 0.)
3. Colour key (§5.4).
4. If `PAINT_CTRL.SRC_PREMUL` (API `apply_premult`): `R = mul8(R,A)`, `G = mul8(G,A)`, `B = mul8(B,A)`.
   A texel outside the source rectangle under `WRAP = COLOR` (§7.5) is `BORDER_COLOR` instead of steps 1–3, and
   step 4 applies to it as well (v1.5, OPEN_QUESTIONS Q-API-19 (b): with `SRC_PREMUL` it is premultiplied like the
   image texels, so a border colour equal to a constant image continues it seamlessly; until v1.4 it was used as
   given). Under `BORDER_SOLID = 1` the plain colour is used as given (§7.5).
5. Filtering (§7.3) combines texels into one ARGB32 value.
6. If `SRC_FMT.TRANSPARENT` (API `transparency_mode = VG_LITE_IMAGE_TRANSPARENT`, v1.4, OPEN_QUESTIONS Q-API-11)
   and the result has `A = 0`: the pixel is skipped (§4 step 1). Checked before §5.3 and before step 7.
7. Colour unit (v1.6b, §5.2.1), then §5.3.

### 5.2.1 Colour unit (v1.6b, `COLOR_CTRL`)

Applied in hardware to the filtered IMAGE colour; the driver applies the same integer function to `PAINT_COLOR`
(SOLID), to the ramp entries (gradients) and to `BORDER_COLOR` under `BORDER_SOLID` (§9.10), so every paint source
sees it alike. Not applied by `vg_lite_clear` and the mask-layer operations (the driver turns it off for them).

1. Gamma (`COLOR_CTRL.GAMMA`; 3 is BAD_STATE): 1 = sRGB → linear, `c = LIN[c]`; 2 = linear → sRGB, `c = SRGB[c]`;
   R, G, B only. `LIN[c] = round(255·f(c/255))`, `f(s) = s/12.92` for `s ≤ 0.04045` else `((s + 0.055)/1.055)^2.4`;
   `SRGB[c] = round(255·g(c/255))`, `g(l) = 12.92·l` for `l ≤ 0.0031308` else `1.055·l^(1/2.4) − 0.055` (IEC
   61966-2-1). The tables are generated by `scripts/gen_gamma.py` (round half up, in double) into the RTL package and
   the model.
2. Matrix (`COLOR_CTRL.EN`): for each output channel `o ∈ {A, R, G, B}`:
   `o' = clamp((M[o][A]·A + M[o][R]·R + M[o][G]·G + M[o][B]·B + Bo[o] + 128) >> 8, 0, 255)`, arithmetic `>>`,
   `M` signed 16-bit (s7.8, 256 = 1.0) in `COLOR_M0..M7` (word `2k`: `M[o_k][A]` [15:0], `M[o_k][R]` [31:16]; word
   `2k + 1`: `M[o_k][G]` [15:0], `M[o_k][B]` [31:16]; rows `o_0..o_3` = A, R, G, B), `Bo` signed 20-bit in 1/256 of a
   channel step (`COLOR_B0..B3` = A, R, G, B). The identity is `M = 256·I`, `Bo = 0`.

The unit works on the values as they are (premultiplied or not). **Intentional deviation from OpenVG 1.1**, which
un-premultiplies before its colour transform (decision 2026-10-26, OPEN_QUESTIONS Q-API-29); `COLOR_CTRL` bit 3 is
earmarked for a future "un-premultiply first" mode (not implemented, reads 0).

### 5.3 Image mode and source global alpha (all paint sources)

1. Image mode MULTIPLY (`PAINT_CTRL.IMG_MUL`, only meaningful for IMAGE): every channel
   `S.c = mul8(S.c, PAINT_COLOR.c)`, alpha included.
2. Source global alpha (`ALPHA_CTRL.SRC_MODE`, `SRC_VALUE g`): NORMAL: unchanged. GLOBAL: `S.A = g`.
   SCALED: `S.A = mul8(S.A, g)`. Only the A channel changes (OPEN_QUESTIONS Q-API-4). A caller with a
   premultiplied source that needs RGB scaled as well uses image mode MULTIPLY with the colour
   `(g, g, g, g)` (step 1), which scales all four channels (what LVGL does for opacity).

### 5.3.1 Stencil image mode (v1.6, `PAINT_CTRL.STENCIL`, OpenVG 1.1 §10.8 `VG_DRAW_IMAGE_STENCIL`)

The image (after §5.2 steps 1–6) is a stencil through which `PAINT_COLOR` `P` is applied. With the image value `I`:
`αa = m(I.A, P.A)`, and per colour channel `αc = m(m(I.c, I.A), P.A)`. The source of the blend is
`S = (αa, m(P.R, αR), m(P.G, αG), m(P.B, αB))` and **each colour channel's equation of §5.6 uses its own `αc` in
place of `S.A`** (OpenVG: the blend is applied per channel with the channel's alpha); the alpha channel uses `αa`.
This is OpenVG's formula in the premultiplied (stored-value) form of §5.6: e.g. SRC_OVER gives
`R = m(P.R, αR) + m(D.R, ~αR)`, `A = αa + m(D.A, ~αa)`. Only with `BLEND.FAMILY = 0` and
`ALPHA_CTRL.SRC_MODE = NORMAL` (else BAD_STATE, §13.1); `IMG_MUL` must be 0. The driver folds source global alpha
into `P.A` (GLOBAL: `P.A = g`, SCALED: `P.A = m(P.A, g)`).

### 5.4 Colour key

Four keys `k = 0..3`, each with `enable`, `low = (lr,lg,lb)`, `high = (hr,hg,hb)`, `alpha`. For a texel with
expanded `(R,G,B)`, the lowest-numbered enabled key with `lr ≤ R ≤ hr`, `lg ≤ G ≤ hg`, `lb ≤ B ≤ hb`
replaces the texel's A with `k.alpha`. RGB are unchanged. With no match the texel is unchanged
(OPEN_QUESTIONS Q-API-2).

### 5.5 Destination global alpha

`ALPHA_CTRL.DST_MODE`, `DST_VALUE g`: NORMAL: unchanged. GLOBAL: `D.A = g`. SCALED: `D.A = mul8(D.A, g)`.
This modifies only the blend input, not the stored destination of untouched pixels. In step 5 of §4,
`lerp8` uses this modified D.

### 5.6 Blend equations

All values are in [0,255]. `m = mul8`, `~x = 255 - x`. Every `+` and `-` result is saturated with `sat8`
**after each two-operand step, evaluated left to right as written**. The equations take S and D as given
(stored values, after §5.2–§5.5); no hidden premultiplication or division happens. The table matches the
header comments; integer rounding is ours.

| Code | Mode | RGB (each c ∈ {R,G,B}) | A |
|---|---|---|---|
| 0 | NONE | `S.c` | `S.A` |
| 1 | SRC_OVER | `S.c + m(D.c, ~S.A)` | `S.A + m(D.A, ~S.A)` |
| 2 | DST_OVER | `m(S.c, ~D.A) + D.c` | `m(S.A, ~D.A) + D.A` |
| 3 | SRC_IN | `m(S.c, D.A)` | `m(S.A, D.A)` |
| 4 | DST_IN | `m(D.c, S.A)` | `m(D.A, S.A)` |
| 5 | MULTIPLY | `m(S.c, ~D.A) + m(S.c, D.c) + m(D.c, ~S.A)` | `m(S.A, ~D.A) + m(D.A, ~S.A) + m(S.A, D.A)` |
| 6 | SCREEN | `S.c + (D.c - m(S.c, D.c))` | `S.A + (D.A - m(S.A, D.A))` |
| 7 | DARKEN | `min(SO.c, DO.c)` | `min(SO.A, DO.A)` |
| 8 | LIGHTEN | `max(SO.c, DO.c)` | `max(SO.A, DO.A)` |
| 9 | ADDITIVE | `S.c + D.c` | `S.A + D.A` |
| 10 | SUBTRACT | `m(D.c, ~S.A)` | `m(D.A, ~S.A)` |
| 11 | NORMAL_LVGL | `m(S.c, S.A) + m(D.c, ~S.A)` | `255` |
| 12 | ADDITIVE_LVGL | `m(S.c + D.c, S.A) + m(D.c, ~S.A)` | `255` |
| 13 | SUBTRACT_LVGL | `m(D.c - S.c, S.A) + m(D.c, ~S.A)` | `255` |
| 14 | DIFFERENCE_LVGL | `m(|D.c - S.c|, S.A) + m(D.c, ~S.A)` | `255` |
| 15 | MULTIPLY_LVGL | `m(m(S.c, D.c), S.A) + m(D.c, ~S.A)` | `255` |

`SO` = the SRC_OVER result and `DO` = the DST_OVER result, each computed with its own row.
Parentheses come first: in SCREEN, `D.c - m(S.c, D.c)` (never below 0) is added to `S.c` (never above 255,
because `S + D - round(S·D/255) ≤ 255`), so no step saturates and the result is `round(S + D - S·D/255)`
exactly. (Phase 0–5 evaluated `(S.c + D.c) - m(S.c, D.c)`, which saturated the sum first and was wrong by
up to 255 whenever `S + D > 255`; found in Phase 6 with LVGL's vector demo, docs/STATUS.md B-6.1.)
In code 13, `D.c - S.c` saturates at 0.

`VG_LITE_BLEND_*` enum values 0–15 map 1:1 to these codes (`BLEND.FAMILY = 0`).

### 5.7 Blend families (v1.6, `BLEND.FAMILY`)

`BLEND.FAMILY` 1–3 select other blend sets; `BLEND.MODE` then numbers the mode inside the family. In every family
`S` and `D` are the values after §5.2–§5.5, taken as **non-premultiplied** (the header's convention for these
families), `Sa = S.A`, `Da = D.A`, `m = mul8`, `~x = 255 − x`, `+` / `−` saturate per two-operand step (§5.6).

**Family 1, OpenVG (`OPENVG_BLEND_SRC .. ADDITIVE` = MODE 0–9).** Per colour channel `sp = m(S.c, Sa)`,
`dp = m(D.c, Da)`; numerator `n` and result alpha `ra` exactly as the header's formulas:

| MODE | Mode | n | ra |
|---|---|---|---|
| 0 | SRC | `sp` | `Sa` |
| 1 | SRC_OVER | `sp + m(dp, ~Sa)` | `Sa + m(Da, ~Sa)` |
| 2 | DST_OVER | `m(sp, ~Da) + dp` | `m(Sa, ~Da) + Da` |
| 3 | SRC_IN | `m(sp, Da)` | `m(Sa, Da)` |
| 4 | DST_IN | `m(dp, Sa)` | `m(Sa, Da)` |
| 5 | MULTIPLY | `m(sp, dp) + m(sp, ~Da) + m(dp, ~Sa)` | `Sa + m(Da, ~Sa)` |
| 6 | SCREEN | `sp + (dp − m(sp, dp))` | `Sa + m(Da, ~Sa)` |
| 7 | DARKEN | `min(m(sp, Da), m(dp, Sa)) + m(sp, ~Da) + m(dp, ~Sa)` | `Sa + m(Da, ~Sa)` |
| 8 | LIGHTEN | `max(m(sp, Da), m(dp, Sa)) + m(sp, ~Da) + m(dp, ~Sa)` | `Sa + m(Da, ~Sa)` |
| 9 | ADDITIVE | `sp + dp` | `Sa + Da` |

Result: `B.A = ra`; per colour channel (the division by the result alpha that turns the premultiplied result back
into the non-premultiplied form):

```
B.c = (ra == 0) ? 0 : (n ≥ ra) ? 255 : (n·255 + (ra >> 1)) / ra        (integer division, < 255 when n < ra)
```

**Family 2, SVG 2 / W3C Compositing and Blending Level 1 (`SVG2_BLEND_NORMAL, MULTIPLY, SCREEN, LIGHTEN, DARKEN,
DIFFERENCE, EXCLUSION` = MODE 0–6), separable blend function `Bf(cb, cs)` with source-over compositing.** Per colour
channel with `cs = S.c`, `cb = D.c`: `Bf` = NORMAL `cs`, MULTIPLY `m(cs, cb)`, SCREEN `cs + (cb − m(cs, cb))`,
LIGHTEN `max(cs, cb)`, DARKEN `min(cs, cb)`, DIFFERENCE `|cb − cs|`, EXCLUSION `(cs + cb) − m(cs, cb) − m(cs, cb)`;
`n = m(sp, ~Da) + m(dp, ~Sa) + m(m(Sa, Da), Bf)`, `ra = Sa + m(Da, ~Sa)`, and `B` as family 1 (W3C's
`co = cs·αs·(1 − αb) + cb·αb·(1 − αs) + αs·αb·B(cb, cs)`, divided by `αo`). The header gives no formulas for these
codes: the W3C definition is chosen (v1.6 question 5).

**Family 3, Porter-Duff (`VG_LITE_PORTER_DUFF_BLEND` with `vg_lite_blend_func`), state `BLEND_FUNC`.** UNVERIFIED
interpretation of the header's enums (no formulas given). Per channel, the colour channels use the colour factor
and channel-mode fields, the alpha channel the alpha ones; `Cs, Cd` the channel of S / D, `As = Sa`, `Ad = Da`,
`Ac`, `Cc` the alpha / channel of `PAINT_COLOR` (the constant; the header has no setter for it):

- operands `Xs = cm(SCM)`, `Xd = cm(DCM)` with the channel mode `cm`: 0 PREMULTIPLY (`m(Cs, As)` for `Xs`,
  `m(Cd, Ad)` for `Xd`), 1 `Cs`, 2 `Cd`, 3 `Cs + Cd`, 4 `Cd − Cs`, 5 `m(Cs, Cd)`, 6 `|Cd − Cs|`, 7 `Cs + (Cd − m(Cs,
  Cd))`, 8 `max(Cs, Cd)`, 9 `min(Cs, Cd)`, 10 `(Cs + Cd) − m(Cs, Cd) − m(Cs, Cd)`, 15 BYPASS (`Cs` for `Xs`, `Cd` for
  `Xd`);
- factors: colour (`FSC`, `FDC`) 0 `0`, 1 `255`, 2 `Cs`, 3 `~Cs`, 4 `As`, 5 `~As`, 6 `Ad`, 7 `~Ad`, 8 `Cd`, 9 `~Cd`,
  10 `min(As, ~Ad)`, 11 `Ac`, 12 `~Ac`, 13 `Cc`, 14 `~Cc`, 15 `~Ad + m(Ad, Cd)`, 16 `~m(Ad, Cd)`; alpha (`FSA`, `FDA`)
  0 `0`, 1 `255`, 2 `As`, 3 `~As`, 4 `Ad`, 5 `~Ad`, 6 `min(As, ~Ad)`, 7 `Ac`, 8 `~Ac`;
- equation `EQ`: 0 ADD `m(Fs, Xs) + m(Fd, Xd)`, 1 SUB `m(Fs, Xs) − m(Fd, Xd)`, 2 REVERSE_SUB `m(Fd, Xd) − m(Fs, Xs)`,
  3 MIN `min(Xs, Xd)`, 4 MAX `max(Xs, Xd)`.

Every family then continues with §4 step 5 (coverage blend with `D`) and step 6. The destination is always read
for families 1–3. Codes outside these tables are BAD_STATE (§13.1).

### 5.8 Target mirror (v1.6, `TGT_FMT.FLIP_Y`)

With `TGT_FMT.FLIP_Y = 1` target pixel `(x, y)` (all coordinates of §1, §6–§10) is stored at byte address
`TGT_ADDR + (H − 1 − y)·TGT_STRIDE + x·bpp` instead of row `y` (`vg_lite_set_mirror(VG_LITE_ORIENTATION_BOTTOM_TOP)`:
the image appears upside down in memory, row 0 at the bottom). Reads of the destination use the same address.
Extents (§13.2) are unchanged.

## 6. Clipping

A pixel `(x,y)` is processed only if all of these hold:

1. `0 ≤ x < TGT_SIZE.W` and `0 ≤ y < TGT_SIZE.H` (target bounds, always on).
2. `CLIP_MIN.X0 ≤ x < CLIP_MAX.X1` and `CLIP_MIN.Y0 ≤ y < CLIP_MAX.Y1` (scissor; the driver writes the full target when
   the scissor is disabled). `vg_lite_set_scissor(x, y, right, bottom)` maps to `[x,right) × [y,bottom)`
   (OPEN_QUESTIONS Q-API-5 on inclusive vs exclusive).
3. The pixel is inside the primitive's rectangle (RECT/blit: payload rect; PATH: payload bbox).
4. The primitive's coverage is non-zero (§4 step 1).

`vg_lite_scissor_rects` with several rectangles (v1.6, driver-only): rule 2 uses their union. The driver splits the
union into disjoint rectangles and emits the draw once per rectangle with `CLIP_MIN / CLIP_MAX` set to it, so a
pixel inside the union is drawn exactly once (blends see it once) and no other pixel is touched.

An empty intersection is a valid no-op draw. It produces no memory traffic except command fetch.

### 6.1 Mask layer (v1.6, `MASK_CTRL.EN`)

With `MASK_CTRL.EN = 1` every draw's coverage (§4 step 1) is multiplied by an 8-bit mask: an A8 buffer at
`MASK_ADDR` (stride `MASK_STRIDE`, size `MASK_SIZE.W × MASK_SIZE.H`, any byte alignment) placed with its pixel
`(0, 0)` on target pixel `(MASK_ORIGIN.X, MASK_ORIGIN.Y)` (signed 16-bit). For target pixel `(x, y)`:

```
mx = x − MASK_ORIGIN.X,  my = y − MASK_ORIGIN.Y
m  = (0 ≤ mx < W and 0 ≤ my < H) ? mask[my][mx] : 0        (outside the mask buffer: masked out)
cov' = mul8(cov, m)
```

`cov'` replaces `cov` in §4 (a pixel with `cov' = 0` is skipped; the destination is read whenever the mask is on,
since `cov' < 255` is possible on any draw). The mask is read with the draw (draw-to-draw ordering, arch.md §5.3,
makes earlier writes to it visible); a draw that writes the mask buffer it reads gives an unspecified result
(§13.3). Driver use (`vg_lite_*masklayer`): §9.9.

## 7. Image sampling (blit)

### 7.1 Inverse transform

The driver computes `M⁻¹` (target → source) from the API matrix and loads it as nine signed 32-bit
coefficients:

| Coefficients | Format | Meaning |
|---|---|---|
| `XF_A, XF_B, XF_C` | s15.16 | `u` row |
| `XF_D, XF_E, XF_F` | s15.16 | `v` row |
| `XF_G, XF_H, XF_I` | s1.30 | `w` row (perspective only) |

For pixel `(x,y)` let `xh = 2x+1`, `yh = 2y+1` (centre in half-pixel units). Then:

```
U = XF_A*xh + XF_B*yh + 2*XF_C            (LSB = 2^-17)
V = XF_D*xh + XF_E*yh + 2*XF_F
affine      (PAINT_CTRL.PERSP=0):  u = U >> 1,  v = V >> 1                   (s15.16)
perspective (PAINT_CTRL.PERSP=1):  W = XF_G*xh + XF_H*yh + 2*XF_I           (LSB = 2^-31)
                                    if W ≤ 0: pixel skipped (cov = 0)
                                    u = floor_div(U * 2^30, W),  v = floor_div(V * 2^30, W)
```

**OpenVG 1.1 subset / superset** (§10.8, OPEN_QUESTIONS Q-API-19 (c), decision 2026-10-24): OpenVG draws a projective
image only when every image corner has `w > 0` and then ignores MULTIPLY (draws NORMAL). Where OpenVG defines the
result ours equals it (`test_perspective_openvg_subset`: pixel set, point and bilinear values, MULTIPLY with white
= NORMAL). Intentional superset: MULTIPLY applies under projection too, and with corners behind the eye every pixel
with `W > 0` is drawn.

When perspective is used, the driver scales `M⁻¹` so that `|W| ≤ 2^31` (up to rounding) at the corners
of the draw rectangle (§9.3). The hardware result is exact for **all** coefficient values (Phase 3,
decision A5): `U`, `V`, `W` are computed at full width, and only `u, v ∈ [0, 2^29)` can pass the source
test (§7.4, `sx1, sy1 ≤ 8192`). Every other pixel is skipped, which is exactly what the formula above
gives. So the divider only has to produce quotients below `2^29`: with `U ≥ 0` and `2U < W` it computes
`floor(2U·2^29 / W)` by radix-2 restoring division, one quotient bit per pipeline stage (`g2d_xform`).

### 7.2 Source rectangle

`[sx0, sx1) × [sy0, sy1)` = `[SRC_RECT_MIN.X0, SRC_RECT_MAX.X1) × [SRC_RECT_MIN.Y0, SRC_RECT_MAX.Y1)` inside the source image (the whole image, or the `blit_rect` rect).

### 7.3 Filters

- **POINT**: texel `(floor(u / 2^16), floor(v / 2^16))`, i.e. `(u >> 16, v >> 16)`.
- **BILINEAR**: `u' = u - 2^15`, `v' = v - 2^15`, `x0 = u' >> 16`, `y0 = v' >> 16`,
  `fx = (u' >> 8) & 0xFF`, `fy = (v' >> 8) & 0xFF`. The four texels `(x0..x0+1, y0..y0+1)` have their
  coordinates mapped by the wrap mode (§7.5; `WRAP = NONE`: clamped to `[sx0, sx1-1] × [sy0, sy1-1]`).
  Weights: `w00=(256-fx)(256-fy)`,
  `w10=fx(256-fy)`, `w01=(256-fx)fy`, `w11=fx·fy` (sum 65536). Each channel is
  `(Σ c·w + 32768) >> 16`. The weights apply to the texels after §5.2 steps 1–4.
- **GAUSSIAN** (v1.6b, `PAINT_CTRL.GAUSS`, `vg_lite_gaussian_filter`): `x = u >> 16`, `y = v >> 16` (as POINT); the
  nine texels `(x + i, y + j)`, `i, j ∈ {−1, 0, 1}`, with every coordinate mapped by the wrap mode (§7.5; `WRAP =
  NONE`: clamped to `[sx0, sx1-1] × [sy0, sy1-1]`, as for BILINEAR). Weights `W(0,0) = W0`, `W(i,j) = W1` for
  `|i| + |j| = 1`, `W2` for the corners: signed 16-bit in units of 1/4096 (s3.12, range [−8, 8)), `GAUSS_W.W1`
  [15:0], `GAUSS_W.W2` [31:16], `GAUSS_W0.W0` [15:0]. Any values are legal (negative weights, sums other than 1,
  v1.6b decision 2026-10-26): each channel is `clamp((Σ c·W + 2048) >> 12, 0, 255)` over the nine texels after §5.2
  steps 1–4, arithmetic shift, so a sharpening kernel or one with a gain saturates instead of wrapping. Source test
  (§7.4) as POINT. Indexed sources are point-sampled (§3.5), YUV sources are rejected (§13.1). `GAUSS = 1` overrides
  `FILTER`.
- **Difference from OpenVG 1.1** (§9.4 / §10.8, OPEN_QUESTIONS Q-API-19 (a), decision 2026-10-24): OpenVG always
  interpolates in premultiplied form and first clamps the colour channels to `[0, alpha]`; G2D filters the stored
  values and premultiply only with `SRC_PREMUL` (no double premultiply of premultiplied sources), so a
  non-premultiplied source with varying alpha filters differently without `SRC_PREMUL`. With `SRC_PREMUL` the clamp is
  never needed: every texel (border included, §5.2 step 4) has `c = mul8(c, a) ≤ a`, and one weight set with monotone
  rounding keeps `c ≤ a` in the result (model test `test_premultiplied_filter_stays_in_alpha_range`), so no
  hardware clamp exists (area 0). Sources declared premultiplied (`premultiplied = 1`, no `SRC_PREMUL`) are used as
  stored and not clamped (documented deviation).

### 7.4 Source coverage test

The test applies to every IMAGE pixel, of DRAW_RECT and (v1.5, F-1) DRAW_PATH: a path pixel that fails it has
`cov = 0`, one that passes keeps its path coverage (§4 step 1).

`WRAP = NONE`: a blit pixel has `cov = 255` iff `sx0 ≤ (u >> 16) < sx1` and `sy0 ≤ (v >> 16) < sy1`
(point-sample test at the centre, same for both filters); otherwise `cov = 0`. Blit edges are not
anti-aliased. `WRAP ≠ NONE`: `cov = 255` iff `−2^31 ≤ u, v < 2^31` (and `W > 0` under perspective), i.e. the
texel coordinates lie in `[−2^15, 2^15)`; otherwise `cov = 0`.

### 7.5 Wrap modes (v1.4, `PAINT_CTRL.WRAP`, F-2)

Each integer texel coordinate (POINT: `u >> 16`; BILINEAR: `x0` and `x0 + 1`; the same for `v`) is mapped
per axis before the texel is read. For the x axis with `n = sx1 − sx0` and `r = x − sx0` (y likewise):

| WRAP | Name | Mapped coordinate |
|---|---|---|
| 0 | NONE | §7.4 test; bilinear neighbours clamped to `[sx0, sx1 − 1]` |
| 1 | COLOR | `x` if `sx0 ≤ x < sx1`; otherwise the texel is `BORDER_COLOR` (§5.2) |
| 2 | PAD | `clamp(x, sx0, sx1 − 1)` |
| 3 | REPEAT | `sx0 + (r mod n)` |
| 4 | REFLECT | `k = r mod 2n`, `sx0 + (k < n ? k : 2n − 1 − k)` |

`mod` is the mathematical modulo (result in `[0, n)` for negative `r` too). A texel under COLOR is outside if
either of its coordinates is.

**COLOR border modes** (`PAINT_CTRL.BORDER_SOLID`, v1.4, decision 2026-10-24; OPEN_QUESTIONS Q-API-12):
- `0` (default, texel): as above: an outside texel is `BORDER_COLOR` (§5.2), filtered with its neighbours and then
  processed like every texel (§5.2 step 6 TRANSPARENT, §5.3 MULTIPLY).
- `1` (solid, the v1.0–v1.3 pattern behaviour): a pixel whose sample centre is outside the source rectangle
  (`u >> 16` or `v >> 16` outside `[sx0, sx1)` / `[sy0, sy1)`) gets `S = BORDER_COLOR` as a plain colour: no
  filtering, no TRANSPARENT skip, no MULTIPLY (§5.3 step 2 global alpha and the blend still apply). A pixel whose
  centre is inside is sampled as under `WRAP = NONE` (bilinear neighbours clamped to the rectangle).
The bit is ignored unless `WRAP = COLOR`. `BORDER_COLOR` is an ARGB32 state word. VGLite pattern modes map 1:1
(`VG_LITE_PATTERN_COLOR / PAD / REPEAT / REFLECT`); the wrap region is the source rectangle, so a
`blit_rect` sub-image repeats inside itself.

## 8. Path coverage

### 8.1 Edge input

The driver delivers a list of directed line edges `(x0,y0) → (x1,y1)`. Each coordinate is a signed integer
in 1/16-pixel units (s.4). The hardware clamps every coordinate to `[−(2^18−1), 2^18−1]`
(±16384 px guard band) when it reads the edge. There is no error: the driver never sends larger values
(§9.2), and clamping keeps the hardware deterministic if it does. Edges with `y0 == y1` contribute
nothing. Edge order is irrelevant.

### 8.2 Samples

All arithmetic uses **1/32-pixel units**. Vertex `(X,Y) = (2·x16, 2·y16)` is always even.

- AA (`RAST_CTRL.AA=1`, 16 samples): sample `j = 0..15` of pixel `(px,py)` is at
  `X = 32·px + 2·q[j] + 1`, `Y = 32·py + 2·j + 1`, where
  `q = [4, 12, 8, 1, 15, 6, 10, 2, 13, 5, 9, 0, 14, 7, 3, 11]`. This is an n-rooks pattern: one sample per
  sub-row and per sub-column, so near-horizontal and near-vertical edges get all 17 coverage levels.
  Among n-rooks patterns it was chosen by minimising the RMS coverage error against the exact area over
  200 000 random straight edges (Phase 1, 2026-10-15). Coverage error on the 0..255 scale: mean 5.3,
  RMS 7.5, max 28. The first candidate, the lattice `q[j] = (5j+3) mod 16`, gave mean 6.5, RMS 9.3,
  max 43. On the edge pixels of random convex polygons with vertices on the 1/16 grid (pixels crossed by one
  edge and holding no vertex; long edges weigh more), the chosen pattern gives mean 6.27 and max 28.9 (Phase
  6, 400 × 48 polygons, model/tests/test_ideal.py): the figures depend on the edge population, so the 28
  above is an observed maximum, not a bound. Bounds used by tests: any pixel ≤ 56 (§12).
- No AA (`AA=0`): one sample at `X = 32·px + 16`, `Y = 32·py + 16`.

### 8.3 Winding

For edge `e` let `(Xa,Ya)` be the endpoint with the smaller Y and `(Xb,Yb)` the other, and `dir = +1` if the
edge goes downwards (`y1 > y0`), otherwise `−1`. Edge `e` crosses sample `(X,Y)` iff

```
Ya ≤ Y < Yb     and     Xa*(Yb - Ya) + (Y - Ya)*(Xb - Xa) ≤ X*(Yb - Ya)
```

i.e. the edge passes at or left of the sample. The winding number of the sample is
`w = (Σ dir over crossing edges) mod 256`, kept as 8-bit two's complement. The sample is inside if:

- `NON_ZERO`: `w ≠ 0`;
- `EVEN_ODD`: `w` is odd.

The mod-256 counter is part of the specification. Shapes with 256 or more overlapping windings at one
sample are outside the v1 contract, but the model reproduces the hardware anyway.
In AA mode sample Y coordinates are odd and vertex Y coordinates even, so `Ya ≤ Y` and `Y < Yb` never tie.

### 8.4 Coverage value

- AA: `n` = number of inside samples (0..16), `cov = (n*255 + 8) >> 4` (16 → 255, 1 → 16).
- No AA: `cov = inside ? 255 : 0`.

Pixels are restricted to the DRAW_PATH bounding box (§6). The bbox is an optimisation only. The driver
makes it cover the whole path, so the bbox never changes the result except through the scissor.

## 9. Driver-side geometry (software; shared C99 code in `sw/lib/geom`)

The driver and the golden model's test harness use the same C99 library (`sw/lib/geom`), so these steps
are deterministic. They are **not** part of the hardware contract; the hardware sees only their results
(edges, coefficients, ramps).

### 9.1 Arithmetic rules

- IEEE-754 `double`, compiled with `-ffp-contract=off` and without `-ffast-math`. Only `+ − × ÷`, `sqrt`,
  `floor`, `ceil` are used. These are correctly rounded, so CVA6 and the host produce identical results.
  `sin`/`cos` (for `vg_lite_rotate` and the stroke discs of §9.7) come from the driver's own polynomial
  `g2d_geom_sincos_deg` (sw/lib/geom: range reduction to [−45°, 45°] in degrees, Taylor series to x¹⁷ / x¹⁶),
  and `atan2` (for arcs, §9.2.1) from `g2d_geom_atan2_deg` (degrees, (−180°, 180°]; argument reduced to
  `t = min / max ∈ [0, 1]`, for `t > 2 − √3` by `atan t = 30° + atan((√3·t − 1) / (√3 + t))`, then the Taylor series
  to t³¹; octant fix-ups exact), never from libm (Phase 7 fix: the Phase 6 stroke discs used libm `sin` / `cos` / `acos`, which are not
  correctly rounded and may differ between the host and CVA6).
- API `float` values are converted to `double` exactly before use.
- `rha(v)` (round half away from zero) is `v ≥ 0 ? floor(v + 0.5) : −floor(−v + 0.5)`, evaluated in double.
- Expressions are evaluated exactly as written below, left to right.

### 9.2 Paths

1. **Decode.** Path data in S8/S16/S32 (integers) or FP32, opcode stream `VLC_OP_*`. Every opcode takes one
   element of the coordinate size (1, 2 or 4 bytes, FP32 stores it as an unsigned 32-bit integer). The
   opcode value is the element's low byte, followed by its coordinate elements. Source: LVGL
   `lv_vg_lite_path.c` (`lv_vg_lite_path_append_op`, `lv_vg_lite_path_for_each_data`). Relative ops add the
   current point. `HLINE`/`VLINE` keep the other coordinate. `SQUAD`/`SCUBIC` reflect the previous
   segment's last control point about the current point; if the previous segment is not a quad
   (respectively cubic), the control point is the current point (an arc counts as neither). `CLOSE` draws a
   line to the subpath start. `MOVE` starts a new subpath, and every subpath is implicitly closed (fill
   semantics). `END` stops decoding. Arcs (`VLC_OP_SCCWARC` … `VLC_OP_LCWARC_REL`, 0x13–0x1A, five elements
   `rh, rv, rot, x, y`; `_REL` adds the current point to `x, y` only): §9.2.1. `BREAK` (0x0A, no elements;
   **UNVERIFIED**, assumption A3 approved 2026-10-19, OPEN_QUESTIONS Q-API-16): ends the current subpath
   without closing it and starts a new one at the current point (an implicit `MOVE` there); fills close every
   subpath anyway, strokes put caps at both sides of the break. An unknown opcode gives `INVALID_ARGUMENT`.
2. **Transform.** Affine path matrix (`m[2][0] == 0`, `m[2][1] == 0`, `m[2][2] == 1`): each point (including
   control points) becomes `x' = m00*x + m01*y + m02`, `y' = m10*x + m11*y + m12`. Affine maps take Béziers to
   Béziers, so flattening happens in target space. Any other matrix is projective: §9.8.
3. **Flatten** with tolerance `tol = 1/32` px. Chords always lie inside a convex arc, so the tolerance is a
   systematic coverage bias on curved edges. At 1/8 px it was up to 32/255 (measured in Phase 1); at
   1/32 px it is at most 8/255. `|v|` is the Euclidean norm `sqrt(vx*vx + vy*vy)`.
   - Quad `P0,P1,P2`: `d = |P0 − 2·P1 + P2|`, `n = clamp(ceil(sqrt(d / (4·tol))), 1, 256)`.
     Points for `i = 1..n−1`: `t = i/n`, `mt = 1 − t`,
     `B = (mt*mt)*P0 + (2*mt*t)*P1 + (t*t)*P2` (per coordinate, summed left to right). Then `P2` exactly.
   - Cubic `P0..P3`: `M = max(|P0 − 2·P1 + P2|, |P1 − 2·P2 + P3|)`, `n = clamp(ceil(sqrt(3·M / (4·tol))), 1, 256)`.
     `B = (mt*mt*mt)*P0 + (3*mt*mt*t)*P1 + (3*mt*t*t)*P2 + (t*t*t)*P3`. Then `P3` exactly.
   - (For uniform subdivision these `n` bound the chord error by `tol`.)
4. **Guard-band clip.** Only if some flattened point has `|x| ≥ 16383` or `|y| ≥ 16383`: each closed
   subpath polygon is clipped with Sutherland–Hodgman against `[−16383, 16383]²` (left, right, top,
   bottom, in that order). The intersection with a vertical bound `X` is
   `y = y0 + (X − x0) * (y1 − y0) / (x1 − x0)`, and symmetrically for horizontal bounds. Clipping each
   closed subpath separately preserves the winding number of every point inside the guard band.
5. **Quantize** each point to s.4: `x16 = rha(x*16)`, `y16 = rha(y*16)`.
6. **Emit edges** between consecutive points of every closed subpath. Edges with `y0 == y1` are dropped
   (they never contribute, §8.3).
7. **Bounding box** from the quantized edges: `BX0 = floor(min x16 / 16)`, `BY0 = floor(min y16 / 16)`,
   `BX1 = ceil(max x16 / 16)`, `BY1 = ceil(max y16 / 16)` (exclusive). No sample outside this box can be
   inside a closed path. The box is clamped to the signed 16-bit range of the `DRAW_PATH` payload.

### 9.2.1 Elliptical arcs

Semantics of OpenVG 1.1 §8.4 (public Khronos specification; VGLite's opcodes and argument order are OpenVG's):
the arc joins the current point `P0` to `P1 = (x, y)` along an ellipse with radii `rh`, `rv` rotated by `rot`
degrees; `S`/`L`: the small / large of the two candidate arcs, `CCW`/`CW`: increasing / decreasing angle in the
path's own coordinate system (with LVGL's y-down targets a CCW arc appears clockwise on screen; assumption A1).
Construction in path space (SVG F.6.5 / F.6.6, which OpenVG Appendix A cites; the tests derive the centre with
Appendix A's unit-circle method instead):

1. `rh = |rh|`, `rv = |rv|`. `P0 == P1`, or `rh == 0` or `rv == 0` → a line to `P1`. (OpenVG draws an arc with
   exactly one zero radius projected onto the chord; drawing the chord itself is a listed simplification.)
2. `(sinφ, cosφ)` of `rot` (§9.1), `dx = (x0 − x1) / 2`, `dy = (y0 − y1) / 2`, `x' = cosφ·dx + sinφ·dy`,
   `y' = −sinφ·dx + cosφ·dy`. `Λ = x'²/rh² + y'²/rv²`; `Λ > 1` (end points too far apart): `rh`, `rv` × `sqrt(Λ)`.
3. `q = (rh²·rv² − rh²·y'² − rv²·x'²) / (rh²·y'² + rv²·x'²)`, `f = sqrt(max(q, 0))`, negated when
   `large == ccw`; `c' = (f·rh·y'/rv, −f·rv·x'/rh)`; centre `C = R(φ)·c' + (P0 + P1) / 2`.
4. `u = ((x' − c'x)/rh, (y' − c'y)/rv)`, `v = ((−x' − c'x)/rh, (−y' − c'y)/rv)`, `θ1 = atan2(uy, ux)`,
   `Δθ = atan2(ux·vy − uy·vx, ux·vx + uy·vy)` (§9.1, degrees); `ccw` and `Δθ < 0`: `Δθ += 360`; `cw` and
   `Δθ > 0`: `Δθ −= 360`.
5. Points `C + R(φ)·(rh·cos θi, rv·sin θi)`, `θi = θ1 + Δθ·i / k` for `i = 1 … k−1` (sin / cos §9.1), then `P1`
   exactly. Affine matrix: the points are transformed (§9.2 step 2) and `k = clamp(ceil(n·|Δθ| / 360), 1, 1024)`
   with `n` the vertex count of a full circle of radius `σ·max(rh, rv)` at tolerance `tol` (the bisection rule of
   §9.7's discs: smallest `n` in 8..1024 with `cos(180°/n) ≥ 1 − tol / r`), `σ` the largest singular value of the
   matrix's 2×2 part, `sqrt((F + sqrt(F² − 4·det²)) / 2)` with `F` the sum of its squared entries. That is at
   most 256 segments per quarter turn (assumption A4). Projective matrix: §9.8.

`vg_lite_init_arc_path` is `vg_lite_init_path` (assumption A2): both accept every opcode.

### 9.3 Blit setup

Inputs: API matrix `M` (source texel space → target space; texel `i` covers `[i, i+1)`), source rect
`[sx0,sx1) × [sy0,sy1)`, clip rect (scissor ∩ target).

1. **Draw rectangle.** Transform the four source-rect corners with `M` (`x = (m00*sx + m01*sy + m02) / w`
   with `w = m20*sx + m21*sy + m22`, same for y). If any corner has `w ≤ 0`, the draw rectangle is the clip
   rect. Otherwise `X0 = floor(min x) − 1`, `Y0 = floor(min y) − 1`, `X1 = ceil(max x) + 1`,
   `Y1 = ceil(max y) + 1`, intersected with the clip rect. An empty rectangle means no draw (success).
   Draws with a wrap mode (pattern draws, §9.5; v1.4) skip this step: the draw rectangle is the clip rect
   (`g2d_geom_blit_setup_full`), since the wrap mode decides every pixel the image does not cover.
2. **Inverse.** `det = m00*(m11*m22 − m12*m21) − m01*(m10*m22 − m12*m20) + m02*(m10*m21 − m11*m20)`.
   `det == 0` gives `VG_LITE_INVALID_ARGUMENT` (OPEN_QUESTIONS Q-API-8). Listed difference: OpenVG 1.1 draws
   nothing (and reports no error) when the user-to-surface transformation of a path or image is (near-)singular (§9 / §10.8 of
   its specification); VGLite's behaviour is not public. `inv[r][c] = cof[c][r] / det`,
   where `cof[i][j]` is the signed 2×2 cofactor written in the same product order as `det` (for example
   `cof[0][1] = −(m10*m22 − m12*m20)`; the reference listing is `g2d_geom_inverse` in
   `sw/lib/geom/g2d_geom.c`).
   Rows: `(a,b,c) = inv[0]`, `(d,e,f) = inv[1]`, `(g,h,i) = inv[2]`.
3. **Affine** (`g == 0 && h == 0`): divide all nine by `i`. `PERSP = 0`, `XF_G = XF_H = 0`,
   `XF_I = 2^30`.
   **Perspective**: `s = max |g*px + h*py + i|` over the four pixel centres of the draw rectangle corners
   (`px ∈ {X0+0.5, X1−0.5}`, `py ∈ {Y0+0.5, Y1−0.5}`). If `s == 0` there is no draw. Divide all nine by `s`.
   `PERSP = 1`, `XF_G = rha(g*2^30)`, `XF_H = rha(h*2^30)`, `XF_I = rha(i*2^30)`.
4. `XF_A = rha(a*2^16)`, `XF_B = rha(b*2^16)`, `XF_C = rha(c*2^16)`, and likewise `XF_D..XF_F` from
   `d,e,f`. Any coefficient outside the signed 32-bit range gives `VG_LITE_INVALID_ARGUMENT`.
   With these, the hardware evaluates `u = a·(x+0.5) + b·(y+0.5) + c` (§7.1).
5. Filter: `POINT` gives `FILTER = 0`. `BI_LINEAR` and `LINEAR` give `FILTER = 1`.

### 9.4 Colours

`vg_lite_color_t` (`A<<24 | B<<16 | G<<8 | R`) is swizzled to ARGB32. Nothing is premultiplied by the
driver unless an API function says so.

### 9.5 Pattern draws (`vg_lite_draw_pattern`)

v1.5 (F-1): every path and every path matrix (affine or projective, §9.8) is supported: the image is the IMAGE paint
of a DRAW_PATH (§4 step 1: path coverage × sampled image, gated by the source test). Before v1.5 the hardware had no
image paint on paths and the driver drew only rectangular paths (steps 1–2 below, then the only case). Semantics (from LVGL's use and its ThorVG-based reference
`vg_lite_tvg.cpp`): the path, transformed by `path_matrix`, is the clip shape; the image is placed by
`pattern_matrix` (image texel space → target, independent of `path_matrix`, as for a blit); `pattern_mode`
says what lies outside the image; `color` is the MULTIPLY image-mode colour, as in a blit. Steps:

1. **Rectangle.** The path is flattened and quantized as in §9.2 (with `path_matrix`, affine or projective). Its
   coverage is exactly a pixel rectangle iff it has exactly two non-horizontal edges, both vertical at X positions
   `xl < xr` (s.4), with the same Y span `[ya, yb)` and opposite directions (so both fill rules give winding ±1
   inside), **and** either AA is off or `xl, xr, ya, yb` are whole pixels (multiples of 16). The covered pixels are
   then `[c(xl), c(xr)) × [c(ya), c(yb))` with `c(a) = ceil((2a − 16) / 32)` (§8.2–8.3: sample X is inside iff
   `2·xl ≤ X < 2·xr`), and the draw is one blit over `R` = that rectangle ∩ clip rect (§6) (step 2): the same pixels
   as the path draw, without a rasterizer pass. No edges → success, no draw.
   **Any other path** (more edges, slanted edges, fractional AA borders; v1.5): one DRAW_PATH of the path's edges
   and bounding box (§9.2) with the IMAGE paint of step 2, its coefficients computed for `R` = bbox ∩ clip rect.
   Fractional AA rectangles therefore get the exact 16-sample border coverage (OPEN_QUESTIONS Q-API-14, closed
   in v1.5; v1.3–v1.4 centre-sampled them, the listed difference Q-API-14 (b)).
   Projective pattern matrices (any `pattern_matrix`, §9.3) are an extension of OpenVG, whose pattern paint has an
   affine paint transform only (OPEN_QUESTIONS Q-API-19 (d), kept, decision 2026-10-24).
2. **One image draw** over `R` (v1.4; v1.5 also as the paint of the path draw): `XF_A..XF_I` of `pattern_matrix` over the whole image as in §9.3 with the
   draw rectangle `R` (step 1 of §9.3 skipped), any matrix (perspective included), `PAINT_CTRL.WRAP` from
   `pattern_mode` (`VG_LITE_PATTERN_COLOR / PAD / REPEAT / REFLECT` → COLOR / PAD / REPEAT / REFLECT, §7.5) and
   `BORDER_COLOR = pattern_color` (§9.4, used as given). The rest is the blit of §7: filters, image mode, global
   alpha, blend. Under COLOR the pattern colour is by default a texel (§5.2, §7.5 `BORDER_SOLID = 0`): MULTIPLY
   image mode multiplies it by `color` like the image texels, and bilinear filtering blends image and pattern
   colour at the image border (locked by `test_pattern_color_multiplied`). The driver extension
   `vg_lite_g2d_set_pattern_color_mode(VG_LITE_G2D_PATTERN_COLOR_SOLID)` (`sw/include/vg_lite_g2d.h`) selects
   `BORDER_SOLID = 1`, the v1.0–v1.3 behaviour (unmultiplied plain colour; `test_pattern_color_solid`). UNVERIFIED
   which one Vivante implements (OPEN_QUESTIONS Q-API-12). REPEAT / REFLECT wrap the bilinear neighbours as well (exact seams; v1.0–v1.3 drew one blit per tile
   and clamped at each tile border).

Validation as for blits and paths: invalid `fill_rule` or `pattern_mode` → `INVALID_ARGUMENT`; unsupported
image mode, filter or blend → `NOT_SUPPORT`; a transform outside 32 bits → `INVALID_ARGUMENT`.

### 9.6 24-bit source formats (driver conversion until v1.6, hardware since v1.6b)

**v1.6b:** the hardware reads the 24-bit sources (§3.2 codes 0x80–0x92); the driver passes them through like any
other source format, reports `gcFEATURE_BIT_VG_24BIT` = 1 (LVGL then hands RGB888 / ARGB8565 images to the GPU
directly), and the CPU conversion and its cache described below are gone. Targets in these formats:
`VG_LITE_NOT_SUPPORT` (sources only, v1.6 question 2). The rest of this section is the v1.3–v1.6 behaviour, kept
for reference.

The hardware read the formats of §3.2 (v1.4: also the 16 / 8-bit packed formats, A4 / A2 / A1 / L4 and the
indexed formats). The driver accepted the 24-bit formats as **sources** (blit, blit_rect, pattern image) and
converted them on the CPU into a `VG_LITE_BGRA8888` GPU buffer, which the draw then read (LVGL converts them itself
unless `gcFEATURE_BIT_VG_24BIT` is set, which the driver did not report until v1.6b). Layouts: channels from bit 0 of the little-endian 24-bit value in the order of the name;
5 / 6 bits expanded by replication as §3.3; A = 255 where the format has none.

| Source | Layout (bit 0 first) |
|---|---|
| `VG_LITE_RGB888` / `VG_LITE_BGR888` | R, G, B / B, G, R, 8 bits each (`BGR888` is LVGL's RGB888) |
| `VG_LITE_ARGB8565` / `VG_LITE_ABGR8565` | A 8, R 5, G 6, B 5 / A 8, B 5, G 6, R 5 |
| `VG_LITE_BGRA5658` / `VG_LITE_RGBA5658` | B 5, G 6, R 5, A 8 (LVGL's ARGB8565) / R 5, G 6, B 5, A 8 |

The draw is then exactly the draw of the converted image (all other fields unchanged). **Cache:** converted
copies are kept across draws and command buffers, keyed by (address, format, width, height, stride, 64-bit
FNV-1a hash of the source bytes): every use reads the source once to hash it, an unchanged image is converted
once, a changed one gets a new copy. When a new copy would exceed 4 MB or 64 entries, the driver waits for the
GPU and frees all copies; `vg_lite_close` frees them too. Copy size: `align64(width·4)·height` bytes. Targets in
these formats: `VG_LITE_NOT_SUPPORT`. `vg_lite_allocate` / `vg_lite_upload_buffer` accept them (stride
`align64(width·3)`).

**Sub-byte and indexed sources (hardware, v1.4).** `SRC_FMT.SUB_ORDER` (§3.5) comes from `index_endian` for the
indexed formats (`VG_LITE_INDEX_BIG_ENDIAN` = first pixel in the high bits) and is 1 (first pixel in the high
bits, LVGL's layout) for A4 / A2 / A1 / L4. UNVERIFIED: VGLite's own A4 order (OPEN_QUESTIONS Q-API-15) and
whether `VG_LITE_INDEX_LITTLE_ENDIAN` is what LVGL's I1 / I2 / I4 data need (LVGL converts them to I8 before
drawing, so its demos do not depend on it; Q-API-18). Listed differences: OpenVG 1.1's `VG_A_4` puts the first
pixel in the low nibble; LVGL's VG-Lite simulator (`vg_lite_tvg.cpp`) expands A4 as `v << 4` (0xF → 240), ours
as `v·17` (0xF → 255). `vg_lite_set_CLUT(count, colors)` stores `count ≤ 256` ARGB words (the rest 0); the driver
copies the table into GPU memory once per command buffer and `vg_lite_set_CLUT` call and points `CLUT_ADDR` at
it. `transparency_mode`: `VG_LITE_IMAGE_OPAQUE` leaves alpha unchanged (Q-API-11), `VG_LITE_IMAGE_TRANSPARENT`
sets `SRC_FMT.TRANSPARENT` (§5.2 step 6). `vg_lite_enable_dither` sets `TGT_FMT.DITHER` on every later draw
(§3.6) until `vg_lite_disable_dither`.

### 9.7 Strokes (driver-only, `sw/lib/geom` `g2d_geom_stroke_edges`)

`vg_lite_set_stroke` (cap, join, width, miter limit, dash pattern and phase, §9.7.1; the driver keeps a copy of
the pattern) and `vg_lite_update_stroke` store the parameters. A draw of a `VG_LITE_DRAW_STROKE_PATH` path renders the stroke
outline in `path->stroke_color`; `VG_LITE_DRAW_FILL_STROKE_PATH` first fills the path with `color` (its fill
rule), then strokes it. The outline is a DRAW_PATH with `FILL_RULE = NON_ZERO`, any requested fill rule
notwithstanding. Construction, per subpath, in path space (so a non-uniform matrix gives an elliptical pen):

1. Walk and flatten the path as §9.2 steps 1 and 3, in path space, with tolerance `tol / s`, where
   `s = sqrt(m00² + m01² + m10² + m11²)` (at least the largest scale of the matrix). `s = 0`: nothing.
2. Drop zero-length segments (equal consecutive points; for a closed subpath also a last point equal to the
   first). A lone `MOVE` is not stroked. A subpath that is left with one point (`M a L a`, `M a Z`) is a
   dot: a disc for round caps, an axis-aligned square of side `w` for square caps (tangent (1, 0), OpenVG 1.1
   §8.7.4), nothing for butt caps; dashing does not apply to it. With a dash pattern the polyline is then cut
   into dashes (§9.7.1), each an open polyline for step 3.
3. The outline is the union of convex pieces, every one emitted with positive orientation (reversed if its
   signed area is negative, skipped if zero), so NON_ZERO gives exactly the union. `hw = w / 2`:
   - every segment: the rectangle `a ± hw·n`, `b ± hw·n` (`n` the unit left normal);
   - every interior vertex `P` (all vertices of a closed subpath), incoming direction `d0`, outgoing `d1`:
     round: a disc of radius `hw` (the polygon of `n` vertices `P + hw·(cos θᵢ, sin θᵢ)`, `θᵢ = 360°·i / n`, with
     `n` the smallest value in 8..1024 for which `cos(180° / n) ≥ 1 − (tol / s) / hw`, or 1024; found by bisection,
     sin / cos as in §9.1); otherwise the outer
     side `σ = −1` if `d0 × d1 > 0` else `+1`, `A = P + σ·hw·n0`, `B = P + σ·hw·n1`, and the polygon
     `(P − e·d0, A, [M], B, P + e·d1)` with the miter tip `M = P + σ·hw·(n0 + n1) / (1 + n0·n1)` included only
     for a miter join with `1 + d0·d1 > 0` and `1 / sqrt((1 + d0·d1) / 2) ≤ max(limit, 1)` (miter length over
     stroke width, as SVG / OpenVG); exactly straight vertices (`d0 × d1 = 0`, `d0·d1 > 0`) get the patch
     `P ± e·d ± hw·n`;
   - every end of an open subpath: round: a disc; square: the rectangle from `e` inside the segment to `hw`
     beyond the end; butt: nothing.
   `e = min(hw, L0, L1) / 2` (the adjacent segment lengths): join and cap pieces reach into the segment
   rectangles instead of sharing an edge with them, because two collinear edges quantized separately can leave
   a sliver of up to 1/32 px between them (a crack, B-6.3). The overlap lies inside the exact stroke.
4. Transform every piece by the matrix, then guard-band clip, quantize and emit as §9.2 steps 4–7. Projective
   matrix: §9.8 (the curves of step 1 are flattened against the projection, every piece is clipped at the near
   plane and projected, and the disc vertex count uses the local scale).
5. Limits: winding counts are 8 bit (§8.3), so more than 255 pieces overlapping one sample would wrap (not
   reachable with ordinary paths: a point lies in at most a few pieces per vertex it is near).

Tolerance against the exact stroke (`{p : dist(p, polyline) ≤ hw}` for round caps and joins, the region of the
definitions above otherwise): the path criteria of §12.

### 9.7.1 Dashes (driver-only, F-5)

OpenVG 1.1 §8.7.3–8.7.4 (public Khronos specification): the pattern lists alternating "on" / "off" lengths in
path units, starting with "on".

- Normalisation: an odd count ignores the last entry (OpenVG, decision 2026-10-19; SVG would repeat the list);
  negative (and NaN) entries count as 0; fewer than two entries, or an all-zero pattern, stroke solid (G2D choice:
  OpenVG leaves the all-zero case open). `P` = sum of the entries.
- Phase: the pattern coordinate of the subpath start is `phase − P·floor(phase / P)` (a negative phase adds a
  multiple of `P`, OpenVG). Starting from entry 0, entries are skipped while the phase passes them (an entry ending
  exactly at the phase is passed, unless it has length 0). `path->stroke->dash_reset` (0 after the first
  `vg_lite_set_stroke`, kept by later calls; OpenVG's `VG_STROKE_DASH_PHASE_RESET`, default false): 0 carries the
  dash state over to the next subpath, ≠ 0 restarts every subpath at the phase.
- Walk: along each segment `a → b` (length `L`, unit direction `u`; a closed subpath includes its closing
  segment) the boundaries lie at `a + u·pos` (`b` exactly when `pos == L`); an "on" entry that ends emits its dash
  (the points since its start, with the vertices it passed); an "off" entry that ends starts a dash there.
  Zero-length entries produce boundaries at the same position.
- Every dash is an open polyline (caps at both ends, joins only at the vertices inside it; the first and last
  dash of a closed subpath are not joined, as OpenVG marks the dash end points as END vertices). A dash of length
  0 is a dot along the tangent of the segment it lies on (round: a disc, square: a square of side `w` aligned with
  the tangent, butt: nothing; OpenVG additionally draws the second tangent's cap and a join when such a dash falls
  exactly on a vertex, which this implementation leaves out). A positive-length entry that would start exactly at
  the end of an open subpath draws nothing there (no dot).
- Limit: more than 2²² dash boundaries in one stroke draw → `VG_LITE_OUT_OF_RESOURCES`.

Dashes are measured in path space, so the matrix scales them with the path. LVGL v9.6 passes
`dash_phase = width / 2` with every dashed stroke and its VG-Lite simulator ignores the phase (OPEN_QUESTIONS
Q-API-17); the driver applies the phase as given.

### 9.8 Projective path matrix (driver-only)

A path matrix with `m[2][0] ≠ 0`, `m[2][1] ≠ 0` or `m[2][2] ≠ 1` (fills, strokes, gradient fills;
`vg_lite_draw_pattern` too since v1.5, §9.5). `w(x, y) = m20·x + m21·y + m22`.

1. Points stay in path space. Lines stay lines under a projection, so only curves and arcs are subdivided:
   adaptively on their parameter (Bézier `t`, arc angle as §9.2.1 step 5) in halves, depth 2 at least (four
   pieces) and 8 at most (256): a piece `[t0, t1]` is split at `tm = (t0 + t1) / 2` when some but not all of
   its points `t0, tm, t1` have `w ≤ ε`, or all have `w > ε` and the projected point `tm` lies more than
   `tol = 1/32` px from the line through the projected end points (the end point distance if they coincide).
   The final pieces' end points are emitted, the op's own end point exactly.
2. Near-plane clip: every subpath polygon (fills) or stroke piece (strokes, built in path space as §9.7) is
   clipped against `w ≥ ε`, `ε = max(|m20|, |m21|, |m22|) / 2²⁰` (Sutherland–Hodgman; the intersection of an
   edge `a → b` is `a + t·(b − a)` with `t = (ε − w_a) / (w_b − w_a)`): what lies behind the eye is not drawn,
   as a camera would. All three zero → nothing is drawn.
3. Projection: `x' = (m00·x + m01·y + m02) / w`, `y' = (m10·x + m11·y + m12) / w`; then guard band, quantize and
   emit as §9.2 steps 4–7. A projection with `w > 0` keeps the orientation sign of `det m` everywhere, so stroke
   pieces stay of one orientation (NON_ZERO union, §9.7).
4. Stroke discs (round caps / joins, §9.7) take their vertex count from `tol / s` with `s` the largest singular
   value of the projection's Jacobian `(A − p'·gᵀ) / w` (`A` the 2×2 part, `g = (m20, m21)`, `p'` the projected
   point) maximised over the corners of the disc's bounding square; a corner with `w ≤ ε` gives 1024 vertices.

Tolerance against the exact projected shape (every target sample mapped back through the inverse matrix: covered
iff its `w > 0` and its preimage lies in the path-space region): the path criteria of §12.

### 9.9 Mask layers (v1.6, `vg_lite_*masklayer`)

OpenVG 1.1 §7.2 semantics (the header's `vg_lite_mask_operation_t` "Match OpenVG enum VGMaskOperation"): a mask
layer is an A8 buffer (`vg_lite_create_masklayer`: "default format A8 and default pixel value 255"). Operations
write the layer with ordinary draws (the scissor and an active mask do not apply to them), on A8 values `m` (the
layer) and `s` (the source: another layer, or the path coverage × the alpha of `color`):

| Operation | Result | Driver draw |
|---|---|---|
| CLEAR / FILL | 0 / 255 | clear (blend NONE) |
| SET | `s` | blend NONE of the source |
| UNION | `1 − (1 − m)(1 − s)` = `m + s − m·s` | SCREEN |
| INTERSECT | `m · s` | DST_IN |
| SUBTRACT | `m · (1 − s)` | SUBTRACT |

`vg_lite_fill_masklayer(layer, rect, value)`: a clear of `rect` with `value`. `vg_lite_blend_masklayer(dst, src, op,
rect)`: `src` blended into `dst` over `rect` (same position in both). `vg_lite_render_masklayer(layer, op, path,
fill_rule, color, matrix)`: the source is the path's coverage (§8, its fill rule, quality) × the alpha of `color`
over the whole layer (0 outside the path): UNION and SUBTRACT draw the path straight into the layer (outside the
path `s = 0` leaves `m` unchanged); SET and INTERSECT, which change pixels outside the path, render the path into
a temporary zeroed A8 layer and blend that over the whole layer. `vg_lite_set_masklayer(layer, x, y)` selects the
layer and its origin on the target (`MASK_ORIGIN`, assumption: pixel (0, 0) of the layer lies on target (x, y));
`vg_lite_enable_masklayer` / `_disable_masklayer` switch `MASK_CTRL.EN` for later draws (off by default);
`vg_lite_destroy_masklayer` frees the layer (and disables masking if it is the current one).

### 9.10 Gaussian, YUV and colour unit in the driver (v1.6b)

- `vg_lite_gaussian_filter(w0, w1, w2)` (decision 2026-10-26): only NaN and ±Inf are `VG_LITE_INVALID_ARGUMENT`; every
  other weight set is accepted (real drivers very likely accept them; rejecting could break existing software).
  `Wk = round(4096·wk)` (half away from zero) saturated to [−32768, 32767]; when `|w0 + 4·w1 + 4·w2 − 1| ≤ 1e-3` the
  centre is `W0 = 4096 − 4·(W1 + W2)` instead, so unity-gain kernels stay exact (a constant image stays constant).
  The result saturates in hardware (§7.3). Build option `G2D_DRV_STRICT_GAUSSIAN=1` restores the v1.6b-draft check
  (negative weight or sum off by more than 1e-3: `INVALID_ARGUMENT`) for debugging. Before the first call the kernel
  is `[1 2 1] × [1 2 1] / 16` (`W0 = 1024`, `W1 = 512`, `W2 = 256`; assumption, OPEN_QUESTIONS Q-API-27).
  `VG_LITE_FILTER_GAUSSIAN` on a blit / pattern sets `PAINT_CTRL.GAUSS` and loads `GAUSS_W` / `GAUSS_W0`.
- YUV buffers (`vg_lite_allocate`): plane 0 as the luma (YUY2: 2 bytes per pixel), the chroma / alpha planes
  allocated after it (each row 64-byte aligned) and described in `buffer->yuv` (`uv_planar`, `v_planar`,
  `alpha_planar`, strides, heights); a caller that fills `yuv` itself keeps its planes. `yuv.swizzle` →
  `SRC_FMT.UV_SWAP`, `yuv.yuv2rgb` → `SRC_FMT.YUV_709`. Targets: `VG_LITE_NOT_SUPPORT`.
- `vg_lite_set_pixel_matrix(m, channel)`: `M` rows from `m` (row `o` = `m[5o .. 5o+3]`, bias `m[5o+4]`) for the
  enabled channels, identity rows for the others. `vg_lite_set_color_transform(v)`: `c' = c·scale + bias` per channel,
  enabled by `vg_lite_enable_color_transform`. Both active: composed in double as transform ∘ matrix (the matrix
  first, no clamp in between), then `M = round(256·m)` saturated to 16 bits, `Bo = round(65280·b)` saturated to 20
  bits (biases are normalised: 1.0 = 255). `vg_lite_set_gamma`: `COLOR_CTRL.GAMMA` (NO_CONVERSION 0, LINEAR 1,
  NON_LINEAR 2). The draw colour of SOLID paints, the 256 ramp entries and a solid border colour go through the same
  integer function on the CPU (`drv_color_apply`, sw/driver/vg_lite/drv_color.c), the image colour through the
  hardware; a gradient draw reads a transformed copy of its ramp (one per command buffer). `vg_lite_set_pixel_matrix`
  with `channel = NULL` is `INVALID_ARGUMENT`.
- `OPENVG_*` buffer formats (v1.6b): mapped to the FMT code with the same memory layout (the OpenVG names list
  channels from the MSB: `OPENVG_sRGBA_8888` = `VG_LITE_ABGR8888`); `_PRE` sets `premultiplied`; when the source
  and target colour spaces (`s` / `l`; every `VG_LITE_*` format counts as sRGB) differ and `vg_lite_set_gamma` is
  NO_CONVERSION, the draw converts the source to the target's space (`GAMMA` 1 or 2, OpenVG 1.1 §3.4).
  `OPENVG_BW_1` is `VG_LITE_NOT_SUPPORT` (no 1-bit luminance class).

## 10. Linear gradient

### 10.1 Hardware function

- `T = GRAD_P*xh + GRAD_Q*yh + GRAD_R`, evaluated in 64-bit signed arithmetic. `GRAD_P` and `GRAD_Q`
  are signed 32-bit. `GRAD_R` is signed 48-bit: `{GRAD_R_HI[15:0], GRAD_R[31:0]}`. Ramp index:
  `i = T >> 17`.
- Spread (`PAINT_CTRL.SPREAD`): PAD: `i = clamp(i, 0, 255)`. REPEAT: `i = i & 255`.
  REFLECT: `k = i & 511`, `i = (k < 256) ? k : 511 − k`. FILL (3, v1.4, Q-API-6): `S = PAINT_COLOR` when
  `i < 0` or `i > 255` (the gradient parameter outside [0, 1]), otherwise as PAD.
- `S = ramp[i]`, with the ramp at `GRAD_RAMP_ADDR`: 256 × ARGB32 words (`A<<24|R<<16|G<<8|B`). Then §5.3
  and the rest of the pipeline.

### 10.2 Driver mapping

Gradient space is mapped to **target** space by the gradient's own matrix (`vg_lite_get_grad_matrix`,
`vg_lite_get_linear_grad_matrix`). It is independent of the path matrix; LVGL's VGLite simulator
documents this, see spec/vglite-reference.md. Let `x_g(p)` be the ramp coordinate (0..256 spans the ramp)
of target point `p`:

- **Legacy** `vg_lite_linear_gradient_t`: the ramp lies along gradient-space x, 256 units long.
  `x_g = row 0 of inv(grad.matrix)` applied to `p`, i.e. `x_g = a·px + b·py + c`.
- **Extended** `vg_lite_ext_linear_gradient_t` with points `P0=(X0,Y0)`, `P1=(X1,Y1)` in gradient space:
  with `(gx, gy) = inv(grad.matrix)·p`, `dx = X1 − X0`, `dy = Y1 − Y0`, `L2 = dx*dx + dy*dy`,
  `x_g = 256 * (dx*(gx − X0) + dy*(gy − Y0)) / L2`. `L2 == 0` gives `VG_LITE_INVALID_ARGUMENT`.

Both collapse to `x_g = a·px + b·py + c`. Legacy: `(a, b, c) = (inv00, inv01, inv02)`. Extended:
`a = 256*(dx*inv00 + dy*inv10)/L2`, `b = 256*(dx*inv01 + dy*inv11)/L2`,
`c = 256*(dx*(inv02 − X0) + dy*(inv12 − Y0))/L2`, with `inv` the inverse of the gradient matrix (§9.3 step 2). Then `GRAD_P = rha(a*2^16)`, `GRAD_Q = rha(b*2^16)` and the
48-bit `GRAD_R = rha(c*2^17)`. Since `GRAD_P*xh = 2^17·a·(x+0.5)`, this gives
`T ≈ 2^17·x_g(x+0.5, y+0.5)`, so `i = floor(x_g)` at the pixel centre.
A perspective gradient matrix gives `VG_LITE_NOT_SUPPORT`. Out-of-range coefficients (`P`, `Q` beyond
32 bits, `R` beyond 48 bits) give `VG_LITE_INVALID_ARGUMENT`.

### 10.3 Ramp construction

- **Legacy** (`count ≤ 16`, `stops[k]` in 0..255 non-decreasing, `colors[k]` as `vg_lite_color_t`). For
  each entry `n = 0..255`: if `n ≤ stops[0]`, take colour 0. If `n ≥ stops[count−1]`, take the last colour.
  Otherwise take the largest `k` with `stops[k] ≤ n < stops[k+1]`, set `d = stops[k+1] − stops[k]`
  and `f = n − stops[k]`, and per channel `c = (c_k*(d − f) + c_{k+1}*f + d/2) / d` (integer division).
  Stored as given (no premultiplication). Spread is PAD.
- **Extended** (`count ≤ 256` float stops `stop ∈ [0,1]`, sorted non-decreasing, float RGBA in [0,1]):
  entry `n` samples `t = (n + 0.5)/256`. Before the first stop take the first colour; at or after the last
  stop take the last. Otherwise take the largest `k` with `stop_k ≤ t < stop_{k+1}` and interpolate
  `c = c_k + (c_{k+1} − c_k) * (t − stop_k) / (stop_{k+1} − stop_k)`. If `pre_mult` is set, `r,g,b` are
  multiplied by `a` (double) before quantisation (OPEN_QUESTIONS Q-API-9). Each channel becomes
  `rha(clamp(c, 0, 1) * 255)`. Spread from `spread_mode` (PAD / REPEAT / REFLECT / FILL; FILL uses the draw's
  `paint_color`, as given, v1.4).

### 10.4 Radial gradient (v1.5, F-4, `PAINT_CTRL.SRC = 3`)

**Hardware function.** Besides `T` of §10.1 (same registers `GRAD_P / Q / R / R_HI`), two more affine functions in
the same format: `U` from `GRAD_UP`, `GRAD_UQ`, `{GRAD_UR_HI[15:0], GRAD_UR}` and `V` from `GRAD_VP`, `GRAD_VQ`,
`{GRAD_VR_HI[15:0], GRAD_VR}`, each `P*xh + Q*yh + R` exactly (51 bits). Then

```
u = sat32(U >> 9),  v = sat32(V >> 9)           sat32: clamp to [−2^31, 2^31 − 1]
S = isqrt(u*u + v*v)                            exact floor square root (the argument is at most 2^63)
i = ((T >> 9) + S) >> 8                         arithmetic shifts
```

and `i` goes through the spread of §10.1 (PAD / REPEAT / REFLECT / FILL) and the ramp exactly as for the linear
gradient. With `U = V = 0` this is the linear gradient (`i = T >> 17`). `T >> 9`, `u`, `v` and `S` have 8 fraction
bits of a ramp step; `|u|, |v| < 2^31` covers ramp coordinates up to 2^23 (2^15 ramp lengths) from the focal
point, beyond which `u` / `v` saturate (deterministic, documented range).

**Driver mapping** (OpenVG 1.1 §9.3.2, public Khronos specification; the VGLite header says the spread modes
match OpenVG). Gradient space is mapped to target space by the gradient matrix (independent of the path matrix,
as §10.2); a perspective gradient matrix gives `VG_LITE_NOT_SUPPORT`. With centre `c = (cx, cy)`, radius `r` and
focal point `f = (fx, fy)` in gradient space, OpenVG's function is

```
g(p) = ((d·f') + sqrt(r²·|d|² − (d × f')²)) / (r² − |f'|²),     f' = f − c,  d = p − f
```

(`d × f' = dx·fy' − dy·fx'`): 0 at the focal point, 1 on the circle. Driver steps (double, §9.1):

1. `r ≤ 0`: `g = 1` everywhere (OpenVG): `U = V = 0`, `T = 2^17·256` (i = 256, then the spread).
2. Focal point: `ρ = |f'| / r`. If `ρ > 0.99` (outside the circle, OpenVG replaces it by the intersection of the
   centre-focus line with the circle; on or just inside it the division above is unstable) the focal point is
   moved along the centre-focus line to distance `0.99·r` from the centre, which OpenVG allows ("at least .99r").
   `κ = min(ρ, 0.99)`.
3. Rotate gradient space so that `f'` lies on +x: `e = f' / |f'|` (`e = (1, 0)` when `f' = 0`); for a target point `p`,
   `q = inv(grad matrix)·p` (gradient space), `dx' = (qx − fx)·ex + (qy − fy)·ey`,
   `dy' = −(qx − fx)·ey + (qy − fy)·ex` (with the moved focal point `f`). In this frame OpenVG's function is
   `g = (κ·dx' + sqrt(dx'² + (1 − κ²)·dy'²)) / (r·(1 − κ²))`.
4. With `k = 256 / (r·(1 − κ²))` the ramp coordinate is `256·g = k·κ·dx' + |(k·dx', k·sqrt(1 − κ²)·dy')|`: all three
   terms are affine in the target pixel centre, so `T = 2^17·k·κ·dx'`, `U = 2^17·k·dx'`, `V = 2^17·k·sqrt(1 − κ²)·dy'`,
   each folded into `(P, Q, R)` exactly as §10.2 (`P = rha(a·2^16)`, `Q = rha(b·2^16)`, `R = rha(c·2^17)` for
   `a·px + b·py + c`). Centred gradients (`f = c`) have `T = 0`. Coefficients beyond 32 bits (`P`, `Q`) or 48 bits
   (`R`) give `VG_LITE_INVALID_ARGUMENT`.

Ramp: built like the extended linear gradient (§10.3, up to 256 stops, `pre_mult`), spread from `spread_mode`
(FILL: the draw's `paint_color`). `filter` is ignored (nearest ramp entry, as linear). Tolerance against the
float function: ramp index ±1 (§12).

## 11. Clear

`vg_lite_clear` is a RECT draw with SOLID paint, blend NONE, global alpha NORMAL, and colour keys off.
It honours the scissor. The hardware has no special clear semantics; the fast path is only an
implementation detail (no destination read happens because blend NONE with `cov = 255` does not read D).

## 12. Tolerances

| Comparison | Criterion |
|---|---|
| RTL vs golden model | Bit-exact, every pixel, every test. |
| Software backend vs RTL-sim backend (API tests) | Bit-exact (same model code). |
| Driver + model vs independent float renderer, **paths** (coverage on 0..255; reference = 32×32 supersampled float geometry, curves flattened at 256 segments) | Every pixel `|Δ| ≤ 56`: a single edge errs by at most 28 (worst of 2·10⁵ random edges, §8.2), and a pixel holding a vertex or sliver has two edges. Mean over edge pixels: ≤ 12 per shape and ≤ 10 pooled over all edge pixels of a test's shapes (Phase 6, about 2100 random polygons and circles / ellipses on 64×64 through the driver: per-shape median 7.4, p99 9.6, max 11.8, the high values only on shapes with few edge pixels, where the 16-level coverage makes the mean noisy; the earlier per-shape bound 10 came from 30 polygons. Random self-intersecting Bézier paths can go higher, OPEN_QUESTIONS Q-TEST-1). The per-shape mean assumes edges at random sub-pixel phases: a long straight edge at a fixed unlucky phase reaches 12.2 (a filled triangle with a 45° hypotenuse, 2 of 16 offsets, Phase 6), so tests with chosen geometry place it at random sub-pixel offsets. Pixels the reference sees as fully inside/outside: `|Δ| ≤ 16` (one sample). PSNR ≥ 38 dB. Measured in Phase 1: max 41 (even-odd random polygons), mean ≤ 8.4, PSNR ≥ 39.2; curves max 28, mean ≤ 8.2. |
| Driver + model vs float renderer, **bilinear blits** (affine and perspective) | `|model − float| ≤ 2.5` per channel on pixels at least 1 texel inside the source rect. Measured: 2.08. |
| Driver + model vs float renderer, **point-sampled blits** | Exact, except pixels whose float sample lies within 10⁻³ texel of a texel boundary (excluded). Measured: 0 mismatches in 1.1·10⁵ pixels. |
| Driver + model vs float renderer, **gradient ramp index** | `|Δindex| ≤ 1`. Measured: ≤ 1, at index boundaries only. |
| Driver + model vs float renderer, **radial gradient index** (v1.5, §10.4) | `|Δindex| ≤ 1` against `floor(256·g)` with OpenVG 1.1's `g` in double (the focal point moved as §10.4 step 2), inside the documented range (ramp coordinate < 2^22). Measured: ≤ 1 (`test_radial`). |
| Driver + model vs float renderer, **colour unit** (v1.6b, §5.2.1) | Gamma tables exact (the IEC 61966-2-1 formula, round half up). Matrix: `|Δ| ≤ 3` per channel against the composed float matrix applied in double (coefficients quantised to 1/256, biases to 1/65280, final rounding; `test_color_unit_api`). Solid colours, ramps: bit-exact to the integer function. |
| Driver + model vs float renderer, **YUV conversion** (v1.6b, §3.7) | `|Δ| ≤ 1` per R, G, B channel against the BT.601 / BT.709 limited-range matrix in double from Kr / Kb (coefficients rounded to 1/1024, final rounding); A exact (`test_yuv_vs_float`). |
| Driver + model vs float renderer, **Gaussian filter** (v1.6b, §7.3) | `|Δ| ≤ 1` per channel against the float 3 × 3 convolution with the given float weights (weights quantised to 1/4096, the final rounding; unity-gain kernels exact on constant images; `test_gaussian_vs_float`); other kernels (sharpening, gain) `|Δ| ≤ 1` against the float convolution clamped to [0, 255]. |
| Driver + model vs float renderer, **image paint on paths** (v1.5, §9.5) | Coverage: the path criteria above; colour on fully covered pixels: the bilinear (2.5) / point-sampled blit criteria (`test_pattern_paths`). |
| Driver + model vs ThorVG / LVGL `vg_lite_tvg` | Phase 6 (LVGL screenshots), same path criteria. |

These tolerances apply to driver-level geometry only. Between model and hardware everything is
bit-exact.

## 13. Draw validation and errors (deterministic)

Each draw command is validated **before any memory access of that draw**. A failing draw writes no pixel,
sets its error bit, sets `STATUS.HALTED`, and stops the command processor (arch.md §8). `ERR_CMD_ADDR` is
the address of the draw's header word.

### 13.1 BAD_STATE (`ERR_ADDR = 0`)

| Check | Applies to |
|---|---|
| `TGT_FMT.FMT` is a code of §3.2 that is not source-only | all draws |
| `1 ≤ TGT_SIZE.W, TGT_SIZE.H ≤ 8192` | all draws |
| `TGT_ADDR` aligned to the pixel size; `TGT_STRIDE` a multiple of the pixel size and `≥ W·bpp/8` | all draws |
| `ALPHA_CTRL.SRC_MODE ≤ 2` and `ALPHA_CTRL.DST_MODE ≤ 2` | all draws |
| `PAINT_CTRL.STENCIL = 1`: `SRC = IMAGE`, `BLEND.FAMILY = 0`, `ALPHA_CTRL.SRC_MODE = 0`, `IMG_MUL = 0` | all draws (v1.6, §5.3.1) |
| `BLEND.FAMILY = 1`: `MODE ≤ 9`; `= 2`: `MODE ≤ 6`; `= 3`: `BLEND_FUNC` fields in the §5.7 tables (`FSA, FDA ≤ 8`, `FSC, FDC ≤ 16`, `EQ ≤ 4`, `SCM, DCM ≤ 10` or `15`) | all draws (v1.6) |
| `SRC_FMT.FMT` any code of §3.2; size / address / stride rules as for the target, with `bpp < 8` and 24-bit (v1.6b) sources at any byte address and stride, `SRC_STRIDE ≥ ceil(W·bpp/8)`; `SRC_RECT_MIN.X0 < SRC_RECT_MAX.X1 ≤ SRC_SIZE.W`, same for Y | SRC = IMAGE |
| `PAINT_CTRL.WRAP ≤ 4` | SRC = IMAGE |
| `PAINT_CTRL.GAUSS = 1`: source not YUV (any weights, §7.3) | SRC = IMAGE (v1.6b) |
| `SRC_FMT.YUV_FULL = 0` (reserved, §3.7) | SRC = IMAGE (v1.6b) |
| Units left out of the build (`FEATURES`, `HWCFG.FEAT_*`, v1.7): `BLEND.FAMILY = 0` without the families; no `PAINT_CTRL.GAUSS`, no YUV source, `COLOR_CTRL = 0` (EN and GAMMA) on image draws without the Gaussian / YUV / colour unit; `SRC ≠ RADIAL_GRAD` without the radial gradient; `MASK_CTRL.EN = 0` without the mask layer | all draws |
| YUV source (§3.7): `SRC_PLANE_STRIDE.Sn ≥` the row bytes of each used plane (§13.2); YUY2 plane 0 address and stride multiples of 4, the semi-planar U V plane address and stride multiples of 2 | SRC = IMAGE (v1.6b) |
| `COLOR_CTRL.GAMMA ≤ 2` | SRC = IMAGE (v1.6b) |
| `CLUT_ADDR` 4-byte aligned | SRC = IMAGE with an indexed source |
| `PAINT_CTRL.SPREAD ≤ 3`; `GRAD_RAMP_ADDR` 4-byte aligned | SRC = LINEAR_GRAD or RADIAL_GRAD |
| `EDGE_ADDR` 16-byte aligned | DRAW_PATH with `EDGE_COUNT > 0` |
| `1 ≤ MASK_SIZE.W, MASK_SIZE.H ≤ 8192`, `MASK_STRIDE ≥ MASK_SIZE.W` (A8, any address) | `MASK_CTRL.EN = 1` (v1.6) |

The temporary hardware restriction of Phases 2–3 (`RESTRICT`: draws the pipeline could not execute yet
were reported as BAD_STATE) was removed in Phase 4: the hardware executes every draw of this section.

### 13.2 ADDR_RANGE (`ERR_ADDR` = start of the first failing extent, in table order)

The window is `[ADDR_WIN_LO, ADDR_WIN_HI]`. `ADDR_WIN_LO` is 64-byte aligned and `ADDR_WIN_HI` ends a
64-byte block (its low 6 bits read as 1), so any 64-byte-aligned line fetch that overlaps a legal
extent stays inside the window.

| Extent (inclusive) | Checked for |
|---|---|
| target `[TGT_ADDR, TGT_ADDR + TGT_STRIDE·(H−1) + W·bpp/8 − 1]` | all draws |
| source, same formula with `ceil(W·bpp/8)` bytes per row (24-bit: `3·W`; YUY2: `4·ceil(W/2)`; YUV classes 4, 5: the Y plane, `W` bytes) | SRC = IMAGE |
| YUV plane 1 `[PLANE1, + S1·(ceil(H/2^ys) − 1) + n·ceil(W/2^xs) − 1]`, `n` = 2 (class 4) or 1 (class 5); plane 2: the V plane (same with `S2`, `n` = 1) or the alpha plane (`S2·(H − 1) + W` bytes) | SRC = IMAGE with a YUV source (v1.6b) |
| CLUT `[CLUT_ADDR, + 1023]` | SRC = IMAGE with an indexed source |
| ramp `[GRAD_RAMP_ADDR, + 1023]` | SRC = LINEAR_GRAD or RADIAL_GRAD |
| edges `[EDGE_ADDR, + 16·EDGE_COUNT − 1]` | DRAW_PATH with `EDGE_COUNT > 0` |
| mask `[MASK_ADDR, MASK_ADDR + MASK_STRIDE·(H−1) + W − 1]` | `MASK_CTRL.EN = 1` (v1.6) |
| command buffer `[addr, addr + size − 1]` | at the start of each kicked buffer (`ERR_CMD_ADDR` = buffer address) |

An extent that wraps past `2^32 − 1` is an ADDR_RANGE error.
A kick with `SIZE == 0` or `SIZE` not a multiple of 8 is a `CMD_DECODE` error (`ERR_CMD_ADDR` = buffer address).

### 13.3 Hazards

A draw whose source image, CLUT, ramp, edge list or mask layer overlaps bytes it writes in the target gives an
unspecified result, and the driver must avoid it. Draw-to-draw ordering is guaranteed (arch.md §5.3).
The golden model reads all draw inputs as they were before the draw.

AXI response errors (SLVERR/DECERR) are not modelled by the golden model. The testbench predicts them
from its error-injection map and masks the affected draw's target region.

