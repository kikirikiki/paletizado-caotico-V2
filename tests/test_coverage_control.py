from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.domain.placement import Placement
from palca.packer.layer import LayerState
from palca.packer.maxrects2d import MaxRects2D, Rect
from palca.packer.pallet_model import PalletModel


def _objective(cand: object) -> float:
    return float(cand.packing_gain) - float(cand.fragmentation) + float(cand.score_delta)  # type: ignore[attr-defined]


def test_coverage_bonus_prefers_less_filled_zone() -> None:
    spec = PalletSpec(length_mm=100, width_mm=50, max_height_mm=500, allow_rotate=False)
    model = PalletModel(
        spec=spec,
        coverage_grid_x=2,
        coverage_grid_y=1,
        coverage_weight=10.0,
    )
    model.placements.append(
        Placement(
            x_mm=0,
            y_mm=0,
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=20,
            width_mm=20,
            height_mm=10,
            box_id=1,
        )
    )

    layer = LayerState(
        layer_id=0,
        z_mm=0,
        bin=MaxRects2D(spec.bin_length_mm, spec.bin_width_mm, heuristic="baf"),
    )
    layer.bin.free_rects = [Rect(0, 0, 50, 50), Rect(50, 0, 50, 50)]

    box = Box(box_id=2, length_mm=20, width_mm=20, height_mm=10, timestamp=0.0)
    orientations = model._orientations(20, 20, 10)  # noqa: SLF001
    candidates, _, _, _ = model._preview_in_layer(  # noqa: SLF001
        layer,
        box,
        orientations,
        is_new_layer=False,
    )

    best_by_zone: dict[int, object] = {}
    for cand in candidates:
        placement = cand.placement  # type: ignore[attr-defined]
        zone_id = model._coverage_zone_id(  # noqa: SLF001
            int(placement.x_mm),
            int(placement.y_mm),
            int(placement.length_mm),
            int(placement.width_mm),
            grid_x=2,
            grid_y=1,
        )
        if zone_id is None:
            continue
        prev = best_by_zone.get(int(zone_id))
        if prev is None or _objective(cand) > _objective(prev):
            best_by_zone[int(zone_id)] = cand

    assert 0 in best_by_zone
    assert 1 in best_by_zone
    assert _objective(best_by_zone[1]) > _objective(best_by_zone[0])
    assert float(best_by_zone[1].debug.get("coverage_bonus", 0.0)) > float(best_by_zone[0].debug.get("coverage_bonus", 0.0))  # type: ignore[attr-defined]
