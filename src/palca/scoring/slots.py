"""H2: narrow free strips along the four faces, on H1's 10 mm grid.

Each ray stops at the near edge of the first sampled cell strictly higher
than base_z, or the envelope wall. Equal heights are free. Faces and transverse
cell widths are clipped to the exact footprint; corner regions are excluded,
so strips never overlap. Strict 0 < gap < slot_min_mm excludes contact and the
threshold itself. Obstacles inherit H1's centre-sampled raster approximation
(up to one grid cell); envelope walls and candidate faces use exact coordinates.
Exact cell edges also support partial cells and translated envelopes.
"""
from __future__ import annotations

import numpy as np

from .access_debt import HeightField


def narrow_slot_penalty(
    heightfield: HeightField, footprint: tuple[float, float, float, float],
    base_z: float, slot_min_mm: int,
) -> float:
    """Return total narrow-strip area / footprint area without changing the field."""
    x0, y0, length, width = footprint
    if slot_min_mm <= 0 or length <= 0 or width <= 0:
        return 0.0
    x1, y1 = x0 + length, y0 + width
    xe, ye = heightfield.x_edges_mm, heightfield.y_edges_mm
    ix0, ix1 = np.searchsorted(heightfield.x_mm, [x0, x1])
    iy0, iy1 = np.searchsorted(heightfield.y_mm, [y0, y1])
    # Include partial transverse cells even when their centres lie outside.
    tx = np.flatnonzero((xe[:-1] < x1) & (xe[1:] > x0))
    ty = np.flatnonzero((ye[:-1] < y1) & (ye[1:] > y0))
    wx = np.maximum(0, np.minimum(xe[tx + 1], x1) - np.maximum(xe[tx], x0))
    wy = np.maximum(0, np.minimum(ye[ty + 1], y1) - np.maximum(ye[ty], y0))
    h = heightfield.heights_mm

    def strip(blocked, near_edges, face, wall, spans, direction):
        if blocked.shape[1]:
            first = blocked.argmax(axis=1)
            boundary = np.where(blocked.any(axis=1), near_edges[first], wall)
        else:
            boundary = wall
        gaps = np.maximum(0, direction * (boundary - face))
        return np.sum(np.where((gaps > 0) & (gaps < slot_min_mm), gaps * spans, 0))

    area = strip(h[ty, :ix0][:, ::-1] > base_z, xe[1:ix0 + 1][::-1], x0, xe[0], wy, -1)
    area += strip(h[ty, ix1:] > base_z, xe[ix1:-1], x1, xe[-1], wy, 1)
    area += strip(h[:iy0, tx].T[:, ::-1] > base_z, ye[1:iy0 + 1][::-1], y0, ye[0], wx, -1)
    area += strip(h[iy1:, tx].T > base_z, ye[iy1:-1], y1, ye[-1], wx, 1)
    return float(area / (length * width))
