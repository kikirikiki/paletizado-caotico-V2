from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.domain.placement import Placement
from palca.packer.controls import ControlConfig, StabilityConfig
from palca.packer.maxrects2d import Rect
from palca.packer.pallet_model import PalletModel


def test_heightfield_coverage_bonus_prefers_less_filled_floor_zone() -> None:
    spec = PalletSpec(length_mm=20, width_mm=10, max_height_mm=100, allow_rotate=False)
    model = PalletModel(
        spec=spec,
        stacking_mode="heightfield",
        control_config=ControlConfig(stability=StabilityConfig(mode="off")),
        coverage_grid_x=2,
        coverage_grid_y=1,
        coverage_weight=1000.0,
    )

    floor_box = Box(box_id=1, length_mm=2, width_mm=10, height_mm=5, timestamp=0.0)
    floor_preview = model.preview_place(floor_box)
    assert floor_preview.feasible
    model.commit_place(floor_preview)
    layer_id = int(model.placements[0].layer_id)
    model.placements.append(
        Placement(
            x_mm=10,
            y_mm=0,
            z_mm=5,
            rot90=False,
            layer_id=layer_id,
            length_mm=5,
            width_mm=10,
            height_mm=5,
            box_id=999,
            weight_kg=1.0,
            loadbear=1.0,
            priority=0,
            orientation_name="LWH",
            orientation_family="planar",
        )
    )

    candidate_box = Box(box_id=2, length_mm=5, width_mm=10, height_mm=5, timestamp=1.0)
    preview = model.preview_place(candidate_box)
    assert preview.feasible
    assert preview.placement is not None

    zone_id = model._coverage_zone_id(  # noqa: SLF001
        int(preview.placement.x_mm),
        int(preview.placement.y_mm),
        int(preview.placement.length_mm),
        int(preview.placement.width_mm),
        grid_x=2,
        grid_y=1,
    )
    assert zone_id == 1
    assert float(preview.debug.get("coverage_bonus", 0.0)) > 0.0


def test_heightfield_dominant_free_rect_prefers_candidate_inside_dominant() -> None:
    spec = PalletSpec(length_mm=100, width_mm=50, max_height_mm=200, allow_rotate=False)
    model = PalletModel(
        spec=spec,
        stacking_mode="heightfield",
        control_config=ControlConfig(stability=StabilityConfig(mode="off")),
        dominant_free_rect_weight=100.0,
        dominant_free_rect_ratio_gate=0.35,
    )

    layer = model._ensure_heightfield_base_layer()  # noqa: SLF001
    layer.bin.free_rects = [Rect(0, 0, 70, 50), Rect(70, 0, 30, 50)]
    layer.bin.used_area = 0

    box = Box(box_id=3, length_mm=30, width_mm=50, height_mm=10, timestamp=0.0)
    preview = model.preview_place(box)
    assert preview.feasible
    assert preview.placement is not None
    assert int(preview.placement.x_mm) <= 40
    assert bool(preview.debug.get("dominant_free_rect_in")) is True
    assert float(preview.debug.get("dominant_free_rect_ratio", 0.0)) > 0.0
    assert float(preview.debug.get("dominant_free_rect_delta", 0.0)) > 0.0
