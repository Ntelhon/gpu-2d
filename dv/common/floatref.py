# SPDX-License-Identifier: Apache-2.0
"""Float reference renderers (independent of the model and of the fixed-point spec).

Used only for tolerance-level sanity checks of the driver geometry + model (rendering.md §12):
  - path_coverage: winding-rule coverage by dense supersampling (S x S regular grid per pixel)
    of float polygons (curves densely flattened in float)
  - blit_bilinear: exact float inverse mapping and float bilinear filtering
  - grad_index_float: float ramp coordinate
"""

from __future__ import annotations

import numpy as np


def bezier_points(p0, p1, p2, p3=None, n=256):
    t = np.linspace(0.0, 1.0, n + 1)[1:]
    p0, p1, p2 = (np.asarray(p, float) for p in (p0, p1, p2))
    if p3 is None:
        pts = ((1 - t) ** 2)[:, None] * p0 + (2 * (1 - t) * t)[:, None] * p1 + (t ** 2)[:, None] * p2
    else:
        p3 = np.asarray(p3, float)
        pts = (((1 - t) ** 3)[:, None] * p0 + (3 * (1 - t) ** 2 * t)[:, None] * p1 +
               (3 * (1 - t) * t ** 2)[:, None] * p2 + (t ** 3)[:, None] * p3)
    return [tuple(p) for p in pts]


def polygons_to_edges(polys):
    """polys: list of closed point lists (float). Returns (N, 4) float array of edges."""
    edges = []
    for pts in polys:
        n = len(pts)
        for i in range(n):
            (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
            if y0 != y1:
                edges.append((x0, y0, x1, y1))
    return np.asarray(edges, float).reshape(-1, 4)


def path_coverage(edges, w, h, even_odd, ss=16):
    """Coverage in [0,1] for each pixel of a w x h image, ss*ss regular samples per pixel."""
    off = (np.arange(ss) + 0.5) / ss
    xs = (np.arange(w)[:, None] + off[None, :]).ravel()  # (w*ss,)
    ys = (np.arange(h)[:, None] + off[None, :]).ravel()  # (h*ss,)
    cov = np.zeros((h, w))
    X = xs[None, :]
    for yi, y in enumerate(ys):
        e = edges[(np.minimum(edges[:, 1], edges[:, 3]) <= y) & (y < np.maximum(edges[:, 1], edges[:, 3]))]
        if len(e) == 0:
            continue
        x0, y0, x1, y1 = e.T
        xc = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
        d = np.where(y1 > y0, 1, -1)
        wnd = ((xc[:, None] <= X) * d[:, None]).sum(axis=0)
        inside = (wnd % 2 != 0) if even_odd else (wnd != 0)
        cov[yi // ss] += inside.reshape(w, ss).sum(axis=1)
    return cov / (ss * ss)


def blit_bilinear(src_argb, rect, m_inv, w, h):
    """Float bilinear blit of a (sh, sw) uint32 ARGB image through the inverse matrix (target -> source).
    Returns (h, w, 4) float channel values (A, R, G, B) and a bool mask of pixels inside the source rect,
    and the float (u, v) per pixel."""
    sx0, sy0, sx1, sy1 = rect
    px, py = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    U = m_inv[0][0] * px + m_inv[0][1] * py + m_inv[0][2]
    V = m_inv[1][0] * px + m_inv[1][1] * py + m_inv[1][2]
    W = m_inv[2][0] * px + m_inv[2][1] * py + m_inv[2][2]
    u, v = U / W, V / W
    inside = (W > 0) & (np.floor(u) >= sx0) & (np.floor(u) < sx1) & (np.floor(v) >= sy0) & (np.floor(v) < sy1)
    ch = np.stack([(src_argb >> s) & 255 for s in (24, 16, 8, 0)], axis=-1).astype(float)
    up, vp = u - 0.5, v - 0.5
    x0, y0 = np.floor(up), np.floor(vp)
    fx, fy = up - x0, vp - y0
    cx = lambda c: np.clip(c, sx0, sx1 - 1).astype(int)  # noqa: E731
    cy = lambda c: np.clip(c, sy0, sy1 - 1).astype(int)  # noqa: E731
    t00, t10 = ch[cy(y0), cx(x0)], ch[cy(y0), cx(x0 + 1)]
    t01, t11 = ch[cy(y0 + 1), cx(x0)], ch[cy(y0 + 1), cx(x0 + 1)]
    fx, fy = fx[..., None], fy[..., None]
    out = t00 * (1 - fx) * (1 - fy) + t10 * fx * (1 - fy) + t01 * (1 - fx) * fy + t11 * fx * fy
    return out, inside, u, v
