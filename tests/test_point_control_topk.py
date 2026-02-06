from __future__ import annotations

from palca.packer.controls import DefaultPointControl
from palca.packer.layer import LayerState
from palca.packer.maxrects2d import MaxRects2D


def test_point_control_returns_multiple_candidates() -> None:
    layer = LayerState(layer_id=0, z_mm=0, bin=MaxRects2D(1200, 800, heuristic="baf"))

    cand = layer.bin.find_candidate(575, 430)
    assert cand is not None
    assert layer.bin.place(cand) is True

    cands = list(
        DefaultPointControl().candidates(
            layer=layer,
            length_mm=200,
            width_mm=200,
            height_mm=100,
            is_new_layer=False,
        )
    )

    assert len(cands) >= 2
