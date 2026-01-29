from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    box_id: int | str
    length_mm: int
    width_mm: int
    height_mm: int
    timestamp: float
    destination: int | str | None = None
    weight_kg: float | None = None
    loadbear: float | None = None
    priority: float | None = None

    @property
    def area_mm2(self) -> int:
        return int(self.length_mm) * int(self.width_mm)

    @property
    def volume_mm3(self) -> int:
        return int(self.length_mm) * int(self.width_mm) * int(self.height_mm)

    def effective_weight_kg(self) -> float:
        if self.weight_kg is not None:
            return float(self.weight_kg)
        # Heurística: densidad ~1 kg/L (1e6 mm^3 = 1 L).
        return float(self.volume_mm3) / 1_000_000.0

    def loadbear_capacity(self, factor: float = 1.0) -> float:
        if self.loadbear is not None:
            return float(self.loadbear)
        return float(self.effective_weight_kg()) * float(factor)
