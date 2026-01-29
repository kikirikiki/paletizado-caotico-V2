from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PalletSpec:
    length_mm: int = 1200
    width_mm: int = 800
    max_height_mm: int = 2400
    overhang_mm: int = 0
    allow_rotate: bool = True

    @property
    def bin_length_mm(self) -> int:
        return int(self.length_mm) + 2 * int(self.overhang_mm)

    @property
    def bin_width_mm(self) -> int:
        return int(self.width_mm) + 2 * int(self.overhang_mm)

    @property
    def offset_mm(self) -> int:
        return -int(self.overhang_mm)

    @property
    def bin_area_mm2(self) -> int:
        return self.bin_length_mm * self.bin_width_mm
