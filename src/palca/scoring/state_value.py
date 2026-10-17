"""H5: conservative flat zones and visible-only fractional height completion.

No candidate generation or production control is changed by this estimator.
"""
from __future__ import annotations

import numpy as np

from ..domain.box import Box
from ..packer.pallet_model import PalletModel, orientation_dims_for_mode


def placed_volume(pallets) -> float:
    return float(sum(p.length_mm * p.width_mm * p.height_mm
                     for pallet in pallets.values() for p in pallet.placements))


def flat_zones(pallet: PalletModel):
    """Disjoint rectangles, obtained by merging equal-height horizontal runs.

    Exact placement edges avoid a grid rounding away a tightly fitting box.
    Each rectangle is fully supported by the upper envelope, approximately;
    load bearing, corners and settling remain the production controls' job.
    """
    spec = pallet.spec
    x0 = y0 = spec.offset_mm
    xs = sorted({x0, x0 + spec.bin_length_mm} | {
        max(x0, min(x0 + spec.bin_length_mm, x))
        for p in pallet.placements for x in (p.x_mm, p.x_mm + p.length_mm)})
    ys = sorted({y0, y0 + spec.bin_width_mm} | {
        max(y0, min(y0 + spec.bin_width_mm, y))
        for p in pallet.placements for y in (p.y_mm, p.y_mm + p.width_mm)})
    heights = np.zeros((len(ys) - 1, len(xs) - 1), dtype=np.int32)
    for p in pallet.placements:
        a, b = np.searchsorted(xs, [p.x_mm, p.x_mm + p.length_mm])
        c, d = np.searchsorted(ys, [p.y_mm, p.y_mm + p.width_mm])
        np.maximum(heights[c:d, a:b], p.z_mm + p.height_mm, out=heights[c:d, a:b])
    active = {}
    zones = []
    for iy, row in enumerate(heights):
        current = {}
        starts = [0, *(np.flatnonzero(np.diff(row)) + 1), len(row)]
        for a, b in zip(starts, starts[1:]):
            key = (xs[a], xs[b], int(row[a]))
            current[key] = active.pop(key, ys[iy])
        zones.extend((a, start, b-a, ys[iy]-start, z) for (a,b,z), start in active.items())
        active = current
    zones.extend((a, start, b-a, ys[-1]-start, z) for (a,b,z), start in active.items())
    return sorted(zones, key=lambda r: (r[4], -r[2]*r[3], r[1], r[0]))


def _accessible(pallet, x, y, length, width, z):
    delta = pallet.accessibility_delta_mm
    if delta <= 0:
        return True
    # Same touching/overlapping footprint convention as production, at the
    # known flat local height z. No artificial gaps or expanded tolerances.
    return not any(
        max(p.x_mm, x) - min(p.x_mm+p.length_mm, x+length) <= 0
        and max(p.y_mm, y) - min(p.y_mm+p.width_mm, y+width) <= 0
        and p.z_mm+p.height_mm > z+delta
        for p in pallet.placements)


def visible_completion_potential(pallet: PalletModel, boxes: list[Box]) -> float:
    """Fractional height knapsack per disjoint flat zone; each box used once.

    For zone r with headroom C_r, candidate i has height h_ir and value V_i.
    Greedy decreasing V_i/h_ir solves the fractional relaxation for that zone.
    Fractions consumed in an earlier zone are unavailable in later zones.
    This is an estimate, not a feasibility certificate or a global upper bound.
    """
    if not boxes:
        return 0.0
    headroom = pallet.spec.max_height_mm - pallet.current_height_mm()
    mode = pallet.orientation_mode
    if headroom > pallet.stand_hw_height_margin_gate_mm:
        mode = "planar"
    dims = [orientation_dims_for_mode(b.length_mm, b.width_mm, b.height_mm,
            mode=mode, allow_rotate=pallet.spec.allow_rotate) for b in boxes]
    remaining = [1.0] * len(boxes)
    total = 0.0
    for x, y, length, width, z in flat_zones(pallet):
        capacity = pallet.spec.max_height_mm - z
        if capacity <= 0:
            continue
        candidates = []
        for i, variants in enumerate(dims):
            if remaining[i] <= 0:
                continue
            heights = [h for l,w,h in variants if l <= length and w <= width
                       and h <= capacity and any(
                           _accessible(pallet, px, py, l, w, z)
                           for px,py in ((x,y), (x+length-l,y),
                                         (x,y+width-w), (x+length-l,y+width-w)))]
            if heights:
                h = min(heights)
                candidates.append((boxes[i].volume_mm3 / h, i, h))
        for _, i, h in sorted(candidates, key=lambda item: (-item[0], item[1])):
            fraction = min(remaining[i], capacity / h)
            total += fraction * boxes[i].volume_mm3
            remaining[i] -= fraction
            capacity -= fraction * h
            if capacity <= 0:
                break
    return float(total)
