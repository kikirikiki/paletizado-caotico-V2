from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.controls import ControlConfig, StabilityConfig
from palca.packer.pallet_model import PalletModel


def test_heightfield_uses_local_height_under_footprint() -> None:
    spec = PalletSpec(length_mm=10, width_mm=10, max_height_mm=100, overhang_mm=0)
    model = PalletModel(spec=spec, stacking_mode="heightfield")

    tall = Box(box_id=1, length_mm=4, width_mm=10, height_mm=20, timestamp=0.0)
    preview_tall = model.preview_place(tall)
    assert preview_tall.feasible
    assert preview_tall.placement is not None
    assert preview_tall.placement.x_mm == 0
    model.commit_place(preview_tall)

    short = Box(box_id=2, length_mm=6, width_mm=10, height_mm=10, timestamp=1.0)
    preview_short = model.preview_place(short)
    assert preview_short.feasible
    assert preview_short.placement is not None
    assert preview_short.placement.x_mm == 4
    model.commit_place(preview_short)

    local_height = model._height_under_footprint_mm(4, 0, 6, 10)  # noqa: SLF001
    assert local_height == 10


def test_heightfield_bridge_commit_does_not_consume_projection_bin_area() -> None:
    spec = PalletSpec(length_mm=10, width_mm=10, max_height_mm=100, overhang_mm=0)
    model = PalletModel(
        spec=spec,
        stacking_mode="heightfield",
        control_config=ControlConfig(stability=StabilityConfig(mode="off")),
    )

    base = Box(box_id=10, length_mm=5, width_mm=10, height_mm=10, timestamp=0.0)
    preview_base = model.preview_place(base)
    assert preview_base.feasible
    assert preview_base.placement is not None
    assert preview_base.placement.z_mm == 0
    model.commit_place(preview_base)

    used_area_base = model.layers[0].bin.used_area
    assert used_area_base == 50

    bridge = Box(box_id=11, length_mm=10, width_mm=10, height_mm=10, timestamp=1.0)
    preview_bridge = model.preview_place(bridge)
    assert preview_bridge.feasible
    assert preview_bridge.placement is not None
    assert preview_bridge.placement.x_mm == 0
    assert preview_bridge.placement.y_mm == 0
    assert preview_bridge.placement.z_mm >= 10

    model.commit_place(preview_bridge)

    assert model.current_height_mm() == 20
    assert model.layers[0].bin.used_area == used_area_base
