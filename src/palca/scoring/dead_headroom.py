"""H8: unusable headroom using only the candidate and the pallet ceiling."""
from __future__ import annotations

from ..domain.placement import Placement


def dead_headroom_volume(placement: Placement, ceiling_mm: int, min_mm: int = 250) -> float:
    headroom = ceiling_mm - (placement.z_mm + placement.height_mm)
    if 0 < headroom < min_mm:
        return float(placement.length_mm * placement.width_mm * headroom)
    return 0.0
