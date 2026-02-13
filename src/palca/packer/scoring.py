from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .maxrects2d import Rect


@dataclass(frozen=True)
class ScoringWeights:
    packing_gain_weight: float = 1.0
    fragmentation_weight: float = 1.0
    tower_penalty_ratio: float = 0.1
    # Penalty applied when opening a new layer instead of placing on an existing one.
    new_layer_penalty_ratio: float = 0.0
    # Penalty applied for height increase (opening/tall growth).
    height_increase_penalty_ratio: float = 0.0


def packing_gain(box_area: int, bin_area: int) -> float:
    if bin_area <= 0:
        return 0.0
    return float(box_area) / float(bin_area)


def fragmentation(free_rects: Iterable[Rect]) -> float:
    free_rects = list(free_rects)
    if not free_rects:
        return 0.0
    free_area = sum(rect.area for rect in free_rects)
    if free_area <= 0:
        return 0.0
    largest = max(rect.area for rect in free_rects)
    return 1.0 - (float(largest) / float(free_area))
