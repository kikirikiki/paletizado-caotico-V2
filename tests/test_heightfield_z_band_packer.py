from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.domain.placement import Placement
from palca.packer.controls import ControlConfig, StabilityConfig
from palca.packer.pallet_model import PalletModel
from palca.packer.scoring import ScoringWeights


def test_heightfield_preview_respects_z_band_mm() -> None:
    spec = PalletSpec(length_mm=10, width_mm=10, max_height_mm=500, overhang_mm=0)
    model = PalletModel(
        spec=spec,
        stacking_mode="heightfield",
        control_config=ControlConfig(stability=StabilityConfig(mode="off")),
        scoring_weights=ScoringWeights(tower_penalty_ratio=0.0),
        z_band_mm=0,
    )

    model.placements.append(
        Placement(
            x_mm=0,
            y_mm=0,
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=5,
            width_mm=10,
            height_mm=200,
            box_id=100,
            weight_kg=1.0,
            loadbear=1.0,
            priority=0.0,
            orientation_name="LWH",
            orientation_family="planar",
        )
    )

    candidate = Box(box_id=200, length_mm=5, width_mm=10, height_mm=50, timestamp=1.0)
    preview = model.preview_place(candidate)
    assert preview.feasible
    assert preview.placement is not None
    assert int(preview.placement.x_mm) == 5
    assert int(preview.placement.z_mm) == 0
