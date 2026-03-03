from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.controls import ControlConfig, StabilityConfig
from palca.packer.pallet_model import PalletModel
from palca.packer.scoring import ScoringWeights


def _build_model(z_band_mm: int | None) -> PalletModel:
    spec = PalletSpec(length_mm=20, width_mm=10, max_height_mm=100, overhang_mm=0)
    return PalletModel(
        spec=spec,
        stacking_mode="heightfield",
        z_band_mm=z_band_mm,
        control_config=ControlConfig(stability=StabilityConfig(mode="off")),
        scoring_weights=ScoringWeights(fragmentation_weight=0.0, tower_penalty_ratio=0.0),
    )


def _place_base_box(model: PalletModel) -> None:
    base = Box(box_id=1, length_mm=10, width_mm=10, height_mm=10, timestamp=0.0)
    preview = model.preview_place(base)
    assert preview.feasible
    assert preview.placement is not None
    assert int(preview.placement.x_mm) == 0
    assert int(preview.placement.y_mm) == 0
    assert int(preview.placement.z_mm) == 0
    model.commit_place(preview)


def test_heightfield_packer_z_band_forces_lowest_z_candidate() -> None:
    box = Box(box_id=2, length_mm=10, width_mm=10, height_mm=10, timestamp=1.0)

    model_without_band = _build_model(z_band_mm=None)
    _place_base_box(model_without_band)
    preview_without_band = model_without_band.preview_place(box)
    assert preview_without_band.feasible
    assert preview_without_band.placement is not None
    assert int(preview_without_band.placement.z_mm) == 10
    assert preview_without_band.debug.get("z_band_enabled") is False

    model_with_zero_band = _build_model(z_band_mm=0)
    _place_base_box(model_with_zero_band)
    preview_with_zero_band = model_with_zero_band.preview_place(box)
    assert preview_with_zero_band.feasible
    assert preview_with_zero_band.placement is not None
    assert int(preview_with_zero_band.placement.z_mm) == 0
    assert preview_with_zero_band.debug.get("z_band_enabled") is True
    assert int(preview_with_zero_band.debug.get("z_band_mm", -1)) == 0
    assert int(preview_with_zero_band.debug.get("z_band_min_z", -1)) == 0
    assert int(preview_with_zero_band.debug.get("z_band_candidates_after", 0)) <= int(
        preview_with_zero_band.debug.get("z_band_candidates_before", 0)
    )
