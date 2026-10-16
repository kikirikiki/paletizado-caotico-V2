"""H1: local accessibility debt on a 10 mm, centre-sampled heightfield.

Coordinates use the pallet envelope (including overhang). Boundary cells carry
only their actual area. The band uses Euclidean distance from cell centres to
the closed candidate rectangle, excluding centres inside its footprint.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from ..domain.placement import Placement


@dataclass(frozen=True)
class HeightField:
    heights_mm: np.ndarray
    x_mm: np.ndarray
    y_mm: np.ndarray
    cell_areas_mm2: np.ndarray
    x_edges_mm: np.ndarray
    y_edges_mm: np.ndarray


@dataclass(frozen=True)
class AccessDebt:
    dead_volume_mm3: float = 0.0
    dead_area_mm2: float = 0.0


def build_heightfield(
    placements: Iterable[Placement], length_mm: int, width_mm: int,
    *, origin_mm: tuple[int, int] = (0, 0), grid_mm: int = 10,
) -> HeightField:
    """Pure rasterization of the pre-placement upper profile at cell centres."""
    if min(length_mm, width_mm, grid_mm) <= 0:
        raise ValueError("Envelope dimensions and grid_mm must be positive")
    x_edges = np.append(np.arange(0, length_mm, grid_mm), length_mm)
    y_edges = np.append(np.arange(0, width_mm, grid_mm), width_mm)
    x = (x_edges[:-1] + x_edges[1:]) / 2 + origin_mm[0]
    y = (y_edges[:-1] + y_edges[1:]) / 2 + origin_mm[1]
    heights = np.zeros((len(y), len(x)), dtype=np.int32)
    for p in placements:
        x0, x1 = np.searchsorted(x, [p.x_mm, p.x_mm + p.length_mm])
        y0, y1 = np.searchsorted(y, [p.y_mm, p.y_mm + p.width_mm])
        view = heights[y0:y1, x0:x1]
        np.maximum(view, p.z_mm + p.height_mm, out=view)
    return HeightField(heights, x, y, np.diff(y_edges)[:, None] * np.diff(x_edges)[None, :],
                       x_edges + origin_mm[0], y_edges + origin_mm[1])


def access_debt(
    heightfield: HeightField, footprint: tuple[float, float, float, float],
    top_mm: float, delta_mm: int, band_mm: int,
) -> AccessDebt:
    """Return volume/area below top-delta in the outside band, without mutation.

    footprint = (x, y, length, width). delta=0 means the physical accessibility
    control is disabled and therefore always returns zero debt.
    """
    threshold = top_mm - delta_mm
    if delta_mm == 0 or threshold <= 0 or band_mm <= 0:
        return AccessDebt()
    x0, y0, length, width = footprint
    x1, y1 = x0 + length, y0 + width
    # Restrict vector work to the bounding rectangle of the band.
    ix0, ix1 = np.searchsorted(heightfield.x_mm, [x0 - band_mm, x1 + band_mm], side="left")
    iy0, iy1 = np.searchsorted(heightfield.y_mm, [y0 - band_mm, y1 + band_mm], side="left")
    # Include centres exactly on the outer boundary.
    ix1 = np.searchsorted(heightfield.x_mm, x1 + band_mm, side="right")
    iy1 = np.searchsorted(heightfield.y_mm, y1 + band_mm, side="right")
    x = heightfield.x_mm[ix0:ix1]
    y = heightfield.y_mm[iy0:iy1]
    dx = np.maximum(np.maximum(x0 - x, x - x1), 0)
    dy = np.maximum(np.maximum(y0 - y, y - y1), 0)
    distance2 = dy[:, None] ** 2 + dx[None, :] ** 2
    outside = ((x < x0) | (x >= x1))[None, :] | ((y < y0) | (y >= y1))[:, None]
    deficit = np.maximum(0, threshold - heightfield.heights_mm[iy0:iy1, ix0:ix1])
    dead = outside & (distance2 <= band_mm ** 2) & (deficit > 0)
    areas = heightfield.cell_areas_mm2[iy0:iy1, ix0:ix1]
    return AccessDebt(float(np.sum(deficit * areas, where=dead)), float(np.sum(areas, where=dead)))
