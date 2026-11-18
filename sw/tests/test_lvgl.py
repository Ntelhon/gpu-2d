# SPDX-License-Identifier: Apache-2.0
"""LVGL screenshots (Phase 6, spec/verification-plan.md "LVGL screenshots").

LVGL v9.6 demos rendered with simulated time by two builds of the same LVGL (sw/apps/lvgl): the VG-Lite draw
unit on the G2D driver (software backend) and LVGL's own software renderer (the reference). Both 32-bit
(ARGB8888) and 16-bit (RGB565) builds. Criterion per frame, on B, G, R (alpha is not compared):
  * at most 0.5 % of pixels with any channel |Δ| > 24, mean |Δ| <= 1.0, PSNR >= 32 dB;
  * listed rotated-image frames (ROTATED): PSNR >= 28 dB, at most 2 % of pixels off by more than 24, mean
    |Δ| <= 1.0, and every off pixel within 1 px of an image edge of the reference frame (OPEN_QUESTIONS
    Q-TEST-2: LVGL's software transform samples rotated images up to ~0.7 px off the pixel centre);
  * the reference frame is non-trivial (not a uniform screen).
Short mode (make test): widgets (60 frames) and benchmark up to frame 180; G2D_FULL=1: benchmark 600 frames.
Needs `make lvgl` (built here if LVGL is fetched but the demos are not built; skipped without LVGL).
"""

from __future__ import annotations

import fcntl
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "build" / "lvgl" / "sw"
LVGL_DIR = Path(os.environ.get("LVGL_DIR", Path.home() / "tools" / "src" / "lvgl-9.6.0"))
FULL = os.environ.get("G2D_FULL", "0") == "1"
W, H = 1280, 720
VARIANTS = ("vg32", "sw32", "vg16", "sw16")

# Frames of a demo whose content is rotated images (Q-TEST-2); the looser criterion applies to these only.
ROTATED = {("benchmark", 600)}


@pytest.fixture(scope="module")
def demos():
    if not all((BIN / f"lvgl_demo_{v}").exists() for v in VARIANTS):
        if not (LVGL_DIR / "lvgl.h").exists():
            pytest.skip("LVGL not fetched (scripts/fetch_lvgl.sh); LVGL screenshots skipped")
        lock = ROOT / "build" / ".lvgl.lock"
        lock.parent.mkdir(exist_ok=True)
        with open(lock, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)     # parallel regression seeds share one build
            if not all((BIN / f"lvgl_demo_{v}").exists() for v in VARIANTS):
                subprocess.run(["make", "-C", str(ROOT), "lvgl"], check=True, capture_output=True)
    return BIN


def render(demos: Path, demo: str, frames: int, every: int, tmp: Path):
    procs = {v: subprocess.Popen([str(demos / f"lvgl_demo_{v}"), demo, str(W), str(H), str(frames),
                                  str(tmp / f"{demo}_{v}"), str(every)],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for v in VARIANTS}
    for v, p in procs.items():
        out, _ = p.communicate()
        assert p.returncode == 0, f"{v}: exit {p.returncode}\n{out[-2000:]}"
        # LVGL logs every non-SUCCESS vg_lite return at ERROR level (LV_VG_LITE_CHECK_ERROR)
        errors = [ln for ln in out.splitlines() if "[Error]" in ln]
        assert not errors, f"{v}: {len(errors)} LVGL errors, first: {errors[0]}"


def load(path: Path, bits: int, h: int = H, w: int = W) -> np.ndarray:
    """B, G, R as int, 8 bits per channel (RGB565 expanded the same way for both builds)."""
    raw = np.fromfile(path, np.uint8)
    if bits == 32:
        return raw.reshape(h, w, 4)[:, :, :3].astype(np.int64)
    v = raw.view("<u2").reshape(h, w).astype(np.int64)
    return np.stack([(v & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, ((v >> 11) & 31) * 255 // 31], -1)


def near_edges(ref: np.ndarray) -> np.ndarray:
    """Pixels within 1 px (8-neighbourhood) of an image edge of the reference frame: a strong local gradient
    (> 48 on any channel between the two neighbours along x or y)."""
    g = np.zeros(ref.shape[:2], bool)
    g[1:-1, 1:-1] = (np.abs(ref[2:, 1:-1] - ref[:-2, 1:-1]).max(2) > 48) | \
                    (np.abs(ref[1:-1, 2:] - ref[1:-1, :-2]).max(2) > 48)
    out = g.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            out |= np.roll(np.roll(g, dy, 0), dx, 1)
    return out


def compare(demo: str, frame: int, got: np.ndarray, ref: np.ndarray) -> str:
    d = np.abs(got - ref)
    off = d.max(2) > 24
    mse = float((d.astype(float) ** 2).mean())
    psnr = 99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)
    frac, mean = float(off.mean()), float(d.mean())
    msg = f"{demo} frame {frame}: PSNR {psnr:.1f} dB, mean {mean:.3f}, {frac * 100:.3f} % px > 24"
    assert (ref != ref[0, 0]).any(axis=2).mean() > 0.01, f"{msg}: reference frame is (nearly) uniform"
    if (demo, frame) in ROTATED:
        assert psnr >= 28 and frac <= 0.02 and mean <= 1.0, msg + " (rotated-image criterion)"
        stray = off & ~near_edges(ref)
        assert not stray.any(), f"{msg}: {stray.sum()} off pixels away from image edges, first {np.argwhere(stray)[0]}"
    else:
        assert psnr >= 32 and frac <= 0.005 and mean <= 1.0, msg
    return msg


def check_demo(demos, tmp_path, demo, frames, every):
    render(demos, demo, frames, every, tmp_path)
    dumps = sorted({int(p.stem.rsplit("_", 1)[1]) for p in tmp_path.glob(f"{demo}_vg32_*.raw")})
    assert dumps, "no frames dumped"
    for bits in (32, 16):
        for f in dumps:
            got = load(tmp_path / f"{demo}_vg{bits}_{f:04d}.raw", bits)
            ref = load(tmp_path / f"{demo}_sw{bits}_{f:04d}.raw", bits)
            print(f"{bits}-bit", compare(demo, f, got, ref))


def test_widgets(demos, tmp_path):
    check_demo(demos, tmp_path, "widgets", 60, 30)


def test_benchmark(demos, tmp_path):
    # short: up to frame 180 (image tiles / pattern draws); full: the whole run incl. rotated images (600)
    check_demo(demos, tmp_path, "benchmark", 600 if FULL else 180, 60)


# vg_lite calls of LVGL's vector demo that fail with the driver (docs/LVGL_SURVEY.md). Until v1.4: one image fill on
# a curved path (vg_lite_draw_pattern, FEATURES_LATER F-1) and one radial gradient refused by LVGL itself (feature
# bit off, a warning); both are drawn since v1.5 (F-1, F-4), so the list is empty and the two regions are compared
# with LVGL's software renderer (ThorVG) like the rest. Solid and (since v1.3) dashed strokes are supported
# (rendering.md §9.7, §9.7.1). This pins the gap list so that a new failure is noticed.
VECTOR_GAPS = {
    "[Error] lv_vg_lite_draw_pattern: Execute 'vg_lite_draw_pattern(": 0,
    "[Warn] lv_vg_lite_draw_grad: radial gradient is not supported": 0,
}


def test_vector_known_gaps(demos, tmp_path):
    vw, vh = 640, 480                       # every shape of the demo lies in 600 x 420
    p = subprocess.run([str(demos / "lvgl_demo_vg32"), "vector", str(vw), str(vh), "4", str(tmp_path / "vec"), "4"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-2000:]
    lines = [" ".join(ln.replace("\t", " ").split()) for ln in p.stdout.splitlines()
             if ("[Error]" in ln or "[Warn]" in ln) and "not aligned" not in ln]
    # drop the "(time, +delta)" field LVGL puts after the level
    lines = [ln.split(" ", 1)[0] + " " + ln.split(") ", 1)[1] if ") " in ln else ln for ln in lines]
    got = {k: sum(ln.startswith(k) for ln in lines) for k in VECTOR_GAPS}
    unknown = [ln for ln in lines if not any(ln.startswith(k) for k in VECTOR_GAPS)]
    assert got == VECTOR_GAPS and not unknown, (got, unknown[:5])
    # the whole frame against ThorVG (LVGL's software build), excluding the dashed line (v1.5: the image fill on a
    # curved path and the radial gradient rectangle are compared too); default screenshot criterion on the rest. The dashed
    # line is drawn since v1.3, but LVGL passes dash_phase = width / 2 to vg_lite_set_stroke while its software
    # renderer uses no phase (OPEN_QUESTIONS Q-API-17), so its dashes lie a few pixels apart from the reference:
    # excluded (its pixels in either frame, green and where its screen blend turns white, dilated by 1 px) and
    # checked by its amount instead (same pattern and length: dash pixel counts within 20 %). The dash geometry
    # itself is checked in sw/tests/test_stroke.py.
    ref = subprocess.run([str(demos / "lvgl_demo_sw32"), "vector", str(vw), str(vh), "4", str(tmp_path / "vsw"), "4"],
                         capture_output=True, text=True)
    assert ref.returncode == 0
    a = load(tmp_path / "vec_0004.raw", 32, vh, vw)
    b = load(tmp_path / "vsw_0004.raw", 32, vh, vw)
    green = lambda x: (x[:, :, 1] > 150) & (x[:, :, 2] < 120) & (x[:, :, 0] < 120)   # noqa: E731
    na, nb = int(green(a).sum()), int(green(b).sum())
    assert nb > 1000 and abs(na - nb) <= 0.2 * nb, ("dashed line amount", na, nb)
    dash = green(a) | green(b) | \
        ((np.maximum(a.min(2), b.min(2)) > 235) & (np.abs(a - b).max(2) > 24) & (np.arange(vh)[:, None] < 230))
    excl = dash.copy()                                                                         # SCREEN shape
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            excl |= np.roll(np.roll(dash, dy, 0), dx, 1)
    # image fill on a curved path (F-1, v1.5): ThorVG's scan-line texture mapper samples about (+0.75, +1.0) px away
    # from the pixel centres (OPEN_QUESTIONS Q-TEST-4), which on this rotated photo moves fine texture by up to a
    # pixel. Its own criterion: the region is drawn (mean |d| <= 12, <= 20 % px > 24) and our frame, resampled by that
    # offset, meets the reference within the resampling noise (mean <= 9 on the inner part; 0.25-px self-resample
    # noise ~6). The radial gradient (F-4) is compared under the default criterion with the rest of the frame.
    img = (slice(190, 310), slice(184, 315))
    di = np.abs(a - b)[img]
    assert di.mean() <= 12 and (di.max(2) > 24).mean() <= 0.20, ("image fill", di.mean(), (di.max(2) > 24).mean())
    inner = (slice(235, 290), slice(225, 280))
    ys, xs = np.mgrid[0:vh, 0:vw].astype(float)
    X, Y = xs + 0.75, ys + 1.0
    x0, y0 = np.clip(np.floor(X).astype(int), 0, vw - 2), np.clip(np.floor(Y).astype(int), 0, vh - 2)
    fx, fy = (X - x0)[..., None], (Y - y0)[..., None]
    af = a.astype(float)
    sh = af[y0, x0] * (1 - fx) * (1 - fy) + af[y0, x0 + 1] * fx * (1 - fy) + af[y0 + 1, x0] * (1 - fx) * fy + \
        af[y0 + 1, x0 + 1] * fx * fy
    assert np.abs(sh - b)[inner].max(2).mean() <= 9, ("image fill, offset-compensated", np.abs(sh - b)[inner].mean())
    excl[img] = True
    d = np.abs(a - b)[~excl]
    off = (d.max(1) > 24).mean()
    mse = float((d.astype(float) ** 2).mean())
    psnr = 99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)
    assert off <= 0.005 and d.mean() <= 1.0 and psnr >= 32, (off, d.mean(), psnr)
    assert (~excl).mean() > 0.85, "too much of the frame excluded"


# ---- "formats" demo: images in A4 / ARGB1555 / ARGB4444 / ARGB2222 / ARGB8888 (sw/apps/lvgl/main.c) ----------

FMT_W, FMT_H = 48, 40
FMT_BPP = (4, 16, 16, 8, 32)
BG = np.array([0xA0, 0x90, 0x80])          # B, G, R of the 0x8090a0 screen background


def fmt_sources():
    """The demo's image bytes (same xorshift sequence as formats_demo), decoded to straight B, G, R, A floats
    per LVGL's layouts (bit 0 first: ARGB1555 = B5 G5 R5 A1, ARGB4444 = B4 G4 R4 A4, ARGB2222; A4 high nibble
    first; expansion by bit replication)."""
    s, out = 0x12345, []
    for bpp in FMT_BPP:
        stride = (FMT_W * bpp + 7) // 8
        d = np.zeros((FMT_H, stride), np.int64)
        for y in range(FMT_H):
            for x in range(stride):
                s ^= (s << 13) & 0xFFFFFFFF
                s ^= s >> 17
                s ^= (s << 5) & 0xFFFFFFFF
                d[y, x] = ((x * 5 + y * 3) ^ (s >> 28)) & 0xFF
        if bpp == 4:
            a = np.stack([d >> 4, d & 15], -1).reshape(FMT_H, -1)[:, :FMT_W] * 17
            img = np.stack([np.zeros_like(a)] * 3 + [a], -1)
        elif bpp == 8:
            img = np.stack([((d >> sh) & 3) * 85 for sh in (0, 2, 4, 6)], -1)
        elif bpp == 32:
            img = d.reshape(FMT_H, FMT_W, 4)
        else:
            v = d[:, 0::2] | (d[:, 1::2] << 8)
            if len(out) == 2:      # ARGB4444
                img = np.stack([((v >> sh) & 15) * 17 for sh in (0, 4, 8, 12)], -1)
            else:                  # ARGB1555
                r5 = lambda c: (c << 3) | (c >> 2)  # noqa: E731
                img = np.stack([r5(v & 31), r5((v >> 5) & 31), r5((v >> 10) & 31), (v >> 15) * 255], -1)
        out.append(img.astype(float))
    return out


SCALED_A4_BAND_TEXELS = 1.0      # D9 band width (texels of the 48 x 40 A4 image, drawn at scale 1.5 from (-4, 70))


def scaled_a4_band():
    """Mask over the compared region [68, 132) x [0, 56): pixels inside the scaled A4 image whose sample centre
    is less than SCALED_A4_BAND_TEXELS texels from its edge (image space = (target - origin) / 1.5)."""
    ys, xs = np.mgrid[68:132, 0:56]
    u, v = (xs + 0.5 + 4) / 1.5, (ys + 0.5 - 70) / 1.5
    inside = (u >= 0) & (u < FMT_W) & (v >= 0) & (v < FMT_H)
    edge = np.minimum(np.minimum(u, FMT_W - u), np.minimum(v, FMT_H - v))
    return inside & (edge < SCALED_A4_BAND_TEXELS)


def test_formats_band_on_rtl(demos, tmp_path):
    """D9 band (and the whole frame) of the formats demo bit-exact between the live RTL and the golden model, in both
    COLOR modes: the band's values are ours by definition (rendering.md §7.5), not an unchecked exemption."""
    exe = ROOT / "build" / "live" / "live" / "lvgl_demo_live32_dw64"
    if not exe.exists():
        pytest.skip("live RTL not built (make live)")
    band = scaled_a4_band()
    assert band.sum() > 0
    for solid in (False, True):
        env = {**os.environ, **({"G2D_PATTERN_COLOR_SOLID": "1"} if solid else {})}
        out = {}
        for name, prog in (("gm", demos / "lvgl_demo_vg32"), ("rtl", exe)):
            r = subprocess.run([str(prog), "formats", "320", "240", "3", str(tmp_path / f"{name}{int(solid)}"), "3"],
                               capture_output=True, text=True, env=env, timeout=7200)
            assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
            out[name] = load(tmp_path / f"{name}{int(solid)}_0003.raw", 32, 240, 320)
        assert (out["gm"][68:132, 0:56][band] == out["rtl"][68:132, 0:56][band]).all(), ("band", solid)
        assert (out["gm"] == out["rtl"]).all(), ("frame", solid)


def test_formats(demos, tmp_path):
    """Converted-format images through LVGL (its VG-Lite decoder converts image assets to BGRA8888 / A8; the
    driver's own conversion is covered by test_scenes.py). Per region the reference that applies:
      * A4 (recoloured) and ARGB8888 at 1:1: LVGL's software renderer, default criterion;
      * 1555 / 4444 / 2222 at 1:1: LVGL's software renderer does not draw these formats at all (no decoder in
        draw/sw), so the float reference (straight colour, NORMAL blend) within 1.5;
      * ARGB8888 scaled 1.5 (matrix as LVGL passes it, tx = x - 12 + 0.5): float reference within 2.5 (rendering.md
        §12 bilinear); LVGL's software renderer differs there by up to ~40 (its own sampling convention, a known
        third-party difference, docs/VERIFICATION_MATRIX.md), so it is not used as the reference;
      * the scaled A4 image (a pattern draw with a fractional rectangle path, drawn since v1.3 with centre-sampled
        borders, OPEN_QUESTIONS Q-API-14 (b)): LVGL's software renderer within the path tolerance (max 56, mean 8:
        measured 28 / 5.7, its sampling convention differs as for the scaled ARGB8888) except on the image's top and
        bottom border rows (v1.4, D9: bilinear with the COLOR pattern colour outside, up to 75); no LVGL error."""
    p = {}
    for v in ("vg32", "sw32"):
        r = subprocess.run([str(demos / f"lvgl_demo_{v}"), "formats", "320", "240", "3", str(tmp_path / v), "3"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout[-2000:]
        p[v] = load(tmp_path / f"{v}_0003.raw", 32, 240, 320)
        errs = [ln for ln in r.stdout.splitlines() if "[Error]" in ln]
        if v == "vg32":
            assert not errs, errs
    vg, sw = p["vg32"], p["sw32"]
    d = np.abs(vg[68:132, 0:56] - sw[68:132, 0:56]).max(2)     # scaled A4 at [-4, 68) x [70, 130), 1555 beyond 56
    # v1.4, D9 (docs/VERIFICATION_MATRIX.md): with the default COLOR mode (BORDER_SOLID = 0, rendering.md §7.5) the
    # pixels whose sample centre lies less than one texel (SCALED_A4_BAND_TEXELS) from the image edge are bilinear-
    # blended with the transparent pattern colour; LVGL's software renderer clamps there. Checks: every pixel above
    # the max bound lies in that band (the band's own pixels are checked bit-exactly against the golden model on the
    # RTL by test_formats_band_on_rtl); the max bound holds outside it; the mean bound over the whole region.
    band = scaled_a4_band()
    assert not (d > 56)[~band].any(), ("scaled A4: difference > 56 outside the D9 band", np.argwhere((d > 56) & ~band))
    assert d[~band].max() <= 56 and d.mean() <= 8.0, ("scaled A4", d[~band].max(), d.mean())
    # BORDER_SOLID = 1 (the v1.0-v1.3 behaviour): no exemption, the max bound over the whole region
    r = subprocess.run([str(demos / "lvgl_demo_vg32"), "formats", "320", "240", "3", str(tmp_path / "solid"), "3"],
                       capture_output=True, text=True, env={**os.environ, "G2D_PATTERN_COLOR_SOLID": "1"})
    assert r.returncode == 0, r.stdout[-2000:]
    solid = load(tmp_path / "solid_0003.raw", 32, 240, 320)
    ds = np.abs(solid[68:132, 0:56] - sw[68:132, 0:56]).max(2)
    assert ds.max() <= 56 and ds.mean() <= 8.0, ("scaled A4, BORDER_SOLID", ds.max(), ds.mean())
    srcs = fmt_sources()
    for k, name in enumerate(("A4", "ARGB1555", "ARGB4444", "ARGB2222", "ARGB8888")):
        x0 = 8 + 60 * k
        reg = (slice(8, 8 + FMT_H), slice(x0, x0 + FMT_W))
        if name in ("A4", "ARGB8888"):
            d = np.abs(vg[reg] - sw[reg])
            assert d.max() <= 24 and d.mean() <= 1.5, (name, d.max(), d.mean())
        else:
            img = srcs[k]
            a = img[:, :, 3:4] / 255
            ref = img[:, :, :3] * a + BG * (1 - a)
            d = np.abs(vg[reg] - ref)
            assert d.max() <= 1.5, (name, d.max())
    # ARGB8888 scaled 1.5 about its centre: target = 1.5 * src + (x - 12 + 0.5, 80 - 10 + 0.5)
    img = srcs[4]
    ys, xs = np.mgrid[0:240, 0:320]
    u, v = (xs + 0.5 - 236.5) / 1.5, (ys + 0.5 - 70.5) / 1.5
    up, vp = u - 0.5, v - 0.5
    ix, iy = np.floor(up).astype(int), np.floor(vp).astype(int)
    fx, fy = (up - ix)[..., None], (vp - iy)[..., None]
    g = lambda yy, xx: img[np.clip(yy, 0, FMT_H - 1), np.clip(xx, 0, FMT_W - 1)]  # noqa: E731
    t = g(iy, ix) * (1 - fx) * (1 - fy) + g(iy, ix + 1) * fx * (1 - fy) + g(iy + 1, ix) * (1 - fx) * fy + \
        g(iy + 1, ix + 1) * fx * fy
    ref = t[..., :3] * (t[..., 3:4] / 255) + BG * (1 - t[..., 3:4] / 255)
    inner = (u > 1) & (u < FMT_W - 1) & (v > 1) & (v < FMT_H - 1) & (xs >= 250)   # x < 250: overlaps the 2222 image
    d = np.abs(vg - ref).max(2)[inner]
    assert d.max() <= 2.5, d.max()


def test_three_way_with_vg_lite_tvg(demos, tmp_path):
    """Third-party reading of the vg_lite API: LVGL's own VG-Lite simulator on ThorVG (src/debugging/vg_lite_tvg,
    build variant tv32). Wherever the simulator and LVGL's software renderer agree (|d| <= 8), our frame must agree
    too: at most 0.1 % of those pixels off by more than 24, none by more than 56 (the path tolerance: what remains
    are anti-aliasing differences of edge pixels; measured on the vector demo, ours is the closer one to the exact
    stroke coverage, docs/VERIFICATION_MATRIX.md). The simulator's own deviations (it greys semi-transparent fills:
    widgets tab and slider (206,210,214) where LVGL's renderer and ours give (209..210,233..234,251..252)) do not
    count, because there the two LVGL models disagree. The vector demo's known gaps are excluded as in
    test_vector_known_gaps."""
    tv = demos / "lvgl_demo_tv32"
    if not tv.exists():
        pytest.skip("tv32 variant not built (make lvgl)")
    for demo, w, h, frames in (("widgets", W, H, 30), ("vector", 640, 480, 4)):
        img = {}
        for v in ("tv32", "vg32", "sw32"):
            r = subprocess.run([str(demos / f"lvgl_demo_{v}"), demo, str(w), str(h), str(frames), str(tmp_path / f"{demo}_{v}"),
                                str(frames)], capture_output=True, text=True)
            assert r.returncode == 0, (v, r.stdout[-1000:])
            img[v] = load(tmp_path / f"{demo}_{v}_{frames:04d}.raw", 32, h, w)
        t, v, s = img["tv32"], img["vg32"], img["sw32"]
        agree = np.abs(t - s).max(2) <= 8
        if demo == "vector":                 # known gaps (image fill on a curved path, radial gradient) and the
            agree[190:310, 184:315] = False  # dashed line (in ours and in LVGL's renderers: dash phase, Q-API-17)
            agree[45:205, 395:505] = False
            green = lambda x: (x[:, :, 1] > 150) & (x[:, :, 2] < 120) & (x[:, :, 0] < 120)   # noqa: E731
            dash = green(s) | green(v)
            near = dash.copy()
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    near |= np.roll(np.roll(dash, dy, 0), dx, 1)
            agree &= ~near & ~((np.maximum(s.min(2), v.min(2)) > 235) & (np.arange(h)[:, None] < 230) &
                               (np.abs(v - s).max(2) > 24))
        d = np.abs(v - s).max(2)[agree]
        assert (d > 24).mean() <= 0.001 and d.max() <= 56, (demo, (d > 24).mean(), d.max())
