#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare two images: exact match, max/mean absolute difference, PSNR, tolerance check, diff image.

Library:
    rep = compare(a, b, tol=0)          # a, b: uint8 arrays (H, W) or (H, W, C)
    rep.ok, rep.max_abs, rep.n_diff, rep.psnr, rep.first_diffs

CLI:
    image_compare.py A.png B.png [--tol N] [--psnr-min DB] [--diff out.png]
    exit 0 if within tolerance (and PSNR >= psnr-min if given), 1 otherwise.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field

import numpy as np


@dataclass
class CompareReport:
    shape_ok: bool
    max_abs: int = 0
    mean_abs: float = 0.0
    n_diff: int = 0          # pixels with any channel different
    n_over_tol: int = 0      # pixels with any channel difference > tol
    psnr: float = math.inf   # dB over all channels, peak 255
    tol: int = 0
    first_diffs: list = field(default_factory=list)  # up to 16 (y, x, a_px, b_px)

    @property
    def exact(self) -> bool:
        return self.shape_ok and self.n_diff == 0

    @property
    def ok(self) -> bool:
        return self.shape_ok and self.n_over_tol == 0

    def summary(self) -> str:
        if not self.shape_ok:
            return "shape mismatch"
        return (f"exact={self.exact} max_abs={self.max_abs} mean_abs={self.mean_abs:.4f} "
                f"diff_px={self.n_diff} over_tol({self.tol})={self.n_over_tol} psnr={self.psnr:.2f}dB")


def compare(a, b, tol: int = 0, mask=None) -> CompareReport:
    """mask: optional bool array (H, W); only True pixels are compared."""
    a = np.asarray(a)
    b = np.asarray(b)
    if a.shape != b.shape:
        return CompareReport(shape_ok=False, tol=tol)
    d = np.abs(a.astype(np.int32) - b.astype(np.int32))
    if d.ndim == 2:
        d = d[..., None]
    if mask is not None:
        d = d * np.asarray(mask, bool)[..., None]
    per_px = d.max(axis=-1)
    mse = float((d.astype(np.float64) ** 2).mean()) if d.size else 0.0
    rep = CompareReport(
        shape_ok=True,
        max_abs=int(d.max()) if d.size else 0,
        mean_abs=float(d.mean()) if d.size else 0.0,
        n_diff=int((per_px > 0).sum()),
        n_over_tol=int((per_px > tol).sum()),
        psnr=math.inf if mse == 0 else 10 * math.log10(255.0 ** 2 / mse),
        tol=tol,
    )
    ys, xs = np.nonzero(per_px > 0)
    for y, x in list(zip(ys, xs))[:16]:
        rep.first_diffs.append((int(y), int(x), a[y, x].tolist(), b[y, x].tolist()))
    return rep


def diff_image(a, b, gain: int = 8):
    """Visual diff: |a - b| per channel times gain, saturated, alpha forced opaque."""
    d = np.abs(np.asarray(a, np.int32) - np.asarray(b, np.int32)) * gain
    d = np.clip(d, 0, 255).astype(np.uint8)
    if d.ndim == 3 and d.shape[-1] == 4:
        d[..., 3] = 255
    return d


def _load(path):
    from PIL import Image

    return np.asarray(Image.open(path).convert("RGBA"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--tol", type=int, default=0, help="max allowed per-channel abs difference")
    ap.add_argument("--psnr-min", type=float, default=None)
    ap.add_argument("--diff", help="write a diff PNG here")
    args = ap.parse_args(argv)
    a, b = _load(args.a), _load(args.b)
    rep = compare(a, b, tol=args.tol)
    print(rep.summary())
    for y, x, pa, pb in rep.first_diffs:
        print(f"  ({x},{y}): {pa} vs {pb}")
    if args.diff and rep.shape_ok:
        from PIL import Image

        Image.fromarray(diff_image(a, b)).save(args.diff)
    ok = rep.ok and (args.psnr_min is None or rep.psnr >= args.psnr_min)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
