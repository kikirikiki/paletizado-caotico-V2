"""D-024: geometry and deterministic, compactness-first tie breaking."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from math import isfinite
from typing import TypeVar

from ..domain.placement import Placement

T = TypeVar("T")


def free_sides(candidate: Placement, placements: Iterable[Placement], gap_mm: float = 20,
               *, bounds: tuple[float, float, float, float] | None = None) -> int:
    """Count unoccupied ±x/±y faces; by default pallet edges are free (D-024).

    Optional (xmin, ymin, xmax, ymax) envelope bounds act as walls for D-036.

    Tangential and vertical overlap must have positive length. A normal gap
    of exactly gap_mm is free; contact is occupied even when gap_mm is zero.
    Dimensions are the already oriented dimensions of the placement.
    """
    if not isfinite(gap_mm) or gap_mm < 0:
        raise ValueError("gap_mm must be finite and nonnegative")
    x, y, z = candidate.x_mm, candidate.y_mm, candidate.z_mm
    xx, yy, zz = x + candidate.length_mm, y + candidate.width_mm, z + candidate.height_mm
    occupied = 0
    if bounds is not None:
        xmin, ymin, xmax, ymax = bounds
        if x - xmin < gap_mm or x <= xmin:
            occupied |= 1
        if xmax - xx < gap_mm or xx >= xmax:
            occupied |= 2
        if y - ymin < gap_mm or y <= ymin:
            occupied |= 4
        if ymax - yy < gap_mm or yy >= ymax:
            occupied |= 8
    for p in placements:
        if p.z_mm >= zz or p.z_mm + p.height_mm <= z:
            continue
        px, py = p.x_mm, p.y_mm
        pxx, pyy = px + p.length_mm, py + p.width_mm
        if py < yy and pyy > y:
            if pxx <= x and (pxx == x or x - pxx < gap_mm):
                occupied |= 1
            if px >= xx and (px == xx or px - xx < gap_mm):
                occupied |= 2
        if px < xx and pxx > x:
            if pyy <= y and (pyy == y or y - pyy < gap_mm):
                occupied |= 4
            if py >= yy and (py == yy or py - yy < gap_mm):
                occupied |= 8
        if occupied == 15:
            return 0
    return 4 - occupied.bit_count()


def validate_tolerance(tolerance: float) -> None:
    if not isfinite(tolerance) or tolerance < 0:
        raise ValueError("free_sides_tolerance must be finite and nonnegative")


def rank_free_sides(items: Iterable[T], *, value: Callable[[T], float],
                    sides: Callable[[T], int], legacy_key: Callable,
                    tolerance: float, prefix: Callable = lambda _: ()) -> list[T]:
    """Rank descending, preserving hard priorities and legacy residual ties.

    Each near-tie group is anchored to its greatest compactness value, with
    |best - value| <= tolerance * max(|best|, |value|). This avoids transitive
    chains of near ties accepting arbitrarily worse compactness. Zero means
    exact equality, including at value zero. Scores are never modified.
    """
    validate_tolerance(tolerance)
    ordered = sorted(items, key=lambda item: (prefix(item), value(item), legacy_key(item)), reverse=True)
    result = []
    start = 0
    while start < len(ordered):
        anchor = ordered[start]
        best = value(anchor)
        stop = start + 1
        while stop < len(ordered):
            item = ordered[stop]
            other = value(item)
            if prefix(item) != prefix(anchor) or abs(best - other) > tolerance * max(abs(best), abs(other)):
                break
            stop += 1
        if stop == start + 1:
            result.append(anchor)
        else:
            # Start from the old order, so a tie in sides retains all old keys.
            group = sorted(ordered[start:stop], key=legacy_key, reverse=True)
            result.extend(sorted(group, key=sides, reverse=True))
        start = stop
    return result


def free_sides_distribution(histories: Iterable[Iterable[Placement]]) -> dict:
    """Measure at insertion, resetting neighbors at each physical pallet."""
    counts = {str(n): 0 for n in (4, 3, 2, 1, 0)}
    for history in histories:
        previous = []
        for placement in history:
            counts[str(free_sides(placement, previous))] += 1
            previous.append(placement)
    total = sum(counts.values())
    return dict(count=total, counts=counts,
                proportions={key: count / total if total else 0.0 for key, count in counts.items()},
                gap_mm=20, observation="before_commit", scope="all_placed_including_END")
