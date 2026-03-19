from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, TYPE_CHECKING

from .maxrects2d import MaxRectsCandidate

if TYPE_CHECKING:
    from .layer import LayerState


@dataclass(frozen=True)
class ZoneConfig:
    name: str
    x_min_mm: int
    x_max_mm: int
    y_min_mm: int
    y_max_mm: int

    def fits_box(self, length_mm: int, width_mm: int) -> bool:
        """True if box fits in either orientation (0° or 90°)."""
        zone_w = self.x_max_mm - self.x_min_mm
        zone_d = self.y_max_mm - self.y_min_mm
        # 0° orientation
        if length_mm <= zone_w and width_mm <= zone_d:
            return True
        # 90° orientation
        if width_mm <= zone_w and length_mm <= zone_d:
            return True
        return False

    def contains_placement(self, x_mm: int, y_mm: int, length_mm: int, width_mm: int) -> bool:
        """True if placement footprint is fully within zone bounds."""
        return (
            x_mm >= self.x_min_mm
            and x_mm + length_mm <= self.x_max_mm
            and y_mm >= self.y_min_mm
            and y_mm + width_mm <= self.y_max_mm
        )


@dataclass(frozen=True)
class ZonePointControl:
    zone: ZoneConfig
    k: int = 25

    def candidates(
        self,
        *,
        layer: "LayerState",
        length_mm: int,
        width_mm: int,
        height_mm: int,
        is_new_layer: bool,
    ) -> Iterable[MaxRectsCandidate]:
        all_candidates = layer.bin.find_candidates(length_mm, width_mm, k=self.k)
        return [
            c
            for c in all_candidates
            if self.zone.contains_placement(c.x, c.y, length_mm, width_mm)
        ]


PALLET_ZONES: list[ZoneConfig] = [
    ZoneConfig(name="A", x_min_mm=-20, x_max_mm=585,  y_min_mm=-20, y_max_mm=430),
    ZoneConfig(name="B", x_min_mm=585, x_max_mm=1215, y_min_mm=-20, y_max_mm=335),
    ZoneConfig(name="C", x_min_mm=-20, x_max_mm=615,  y_min_mm=430, y_max_mm=780),
    ZoneConfig(name="D", x_min_mm=615, x_max_mm=1220, y_min_mm=335, y_max_mm=785),
]
