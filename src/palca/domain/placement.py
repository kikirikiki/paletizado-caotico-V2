from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Placement:
    x_mm: int
    y_mm: int
    z_mm: int
    rot90: bool
    layer_id: int
    length_mm: int
    width_mm: int
    height_mm: int
    box_id: int | str | None = None
    weight_kg: float | None = None
    loadbear: float | None = None
    priority: float | None = None
    orientation_name: str | None = None
    orientation_family: str | None = None


@dataclass(frozen=True)
class PlacementPreview:
    feasible: bool
    placement: Placement | None
    packing_gain: float
    fragmentation: float
    height_after_mm: int | None = None
    free_rects_after_n: int | None = None
    largest_free_rect_area_after_mm2: int | None = None
    free_area_after_mm2: int | None = None
    infeasible_reason: str | None = None
    score_adjustment: float = 0.0
    debug: dict[str, Any] | None = None
