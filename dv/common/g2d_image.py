# SPDX-License-Identifier: Apache-2.0
"""Framebuffer helpers: decode a G2D buffer from memory into ARGB32 / RGBA8 arrays and PNG files."""

from __future__ import annotations

import numpy as np

import refimpl


def buffer_argb(read, addr: int, w: int, h: int, stride: int, fmt: int) -> np.ndarray:
    """read(addr, n) -> bytes (e.g. g2d_model.Memory.read). Returns (h, w) uint32 ARGB32 (§3.3)."""
    nb = refimpl.fmt_bytes(fmt)
    raw = np.zeros((h, w), dtype=np.uint32)
    for y in range(h):
        row = np.frombuffer(read(addr + y * stride, w * nb), dtype=np.uint8).reshape(w, nb).astype(np.uint32)
        raw[y] = sum(row[:, i] << (8 * i) for i in range(nb))
    return refimpl.expand(fmt, raw).reshape(h, w)


def buffer_raw(read, addr: int, w: int, h: int, stride: int, fmt: int) -> np.ndarray:
    """Stored bytes of the pixels only (row padding excluded): (h, w * bpp) uint8."""
    nb = refimpl.fmt_bytes(fmt)
    return np.stack([np.frombuffer(read(addr + y * stride, w * nb), dtype=np.uint8) for y in range(h)])


def argb_to_rgba8(argb: np.ndarray) -> np.ndarray:
    a = np.asarray(argb, dtype=np.uint32)
    return np.stack([(a >> 16) & 255, (a >> 8) & 255, a & 255, a >> 24], axis=-1).astype(np.uint8)


def save_png(path, argb: np.ndarray) -> None:
    from PIL import Image

    Image.fromarray(argb_to_rgba8(argb), "RGBA").save(path)


def load_png_argb(path) -> np.ndarray:
    from PIL import Image

    rgba = np.asarray(Image.open(path).convert("RGBA")).astype(np.uint32)
    return (rgba[..., 3] << 24) | (rgba[..., 0] << 16) | (rgba[..., 1] << 8) | rgba[..., 2]
