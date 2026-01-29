from __future__ import annotations

from dataclasses import dataclass

from .maxrects2d import MaxRects2D


@dataclass
class LayerState:
    layer_id: int
    z_mm: int
    bin: MaxRects2D
    height_mm: int = 0
