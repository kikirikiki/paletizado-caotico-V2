from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class ZoneConfig:
    name: str
    x_min_mm: int
    x_max_mm: int
    y_min_mm: int
    y_max_mm: int

    @property
    def usable_length_mm(self) -> int:
        return self.x_max_mm - self.x_min_mm

    @property
    def usable_width_mm(self) -> int:
        return self.y_max_mm - self.y_min_mm

    def fits_box(self, length_mm: int, width_mm: int) -> bool:
        l, w = int(length_mm), int(width_mm)
        return (
            (l <= self.usable_length_mm and w <= self.usable_width_mm)
            or
            (w <= self.usable_length_mm and l <= self.usable_width_mm)
        )

    def contains_placement(self, x_mm: int, y_mm: int,
                           length_mm: int, width_mm: int) -> bool:
        return (
            x_mm >= self.x_min_mm
            and x_mm + length_mm <= self.x_max_mm
            and y_mm >= self.y_min_mm
            and y_mm + width_mm <= self.y_max_mm
        )


PALLET_ZONES: list[ZoneConfig] = [
    ZoneConfig("A", x_min_mm=-20, x_max_mm=585,  y_min_mm=-20, y_max_mm=430),
    ZoneConfig("B", x_min_mm=585, x_max_mm=1215, y_min_mm=-20, y_max_mm=335),
    ZoneConfig("C", x_min_mm=-20, x_max_mm=615,  y_min_mm=430, y_max_mm=780),
    ZoneConfig("D", x_min_mm=615, x_max_mm=1220, y_min_mm=335, y_max_mm=785),
]
