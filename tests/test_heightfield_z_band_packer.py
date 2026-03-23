from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.controls import ControlConfig, StabilityConfig
from palca.packer.pallet_model import PalletModel


def test_heightfield_z_band_zero_prefers_floor_candidate() -> None:
    spec = PalletSpec(length_mm=10, width_mm=10, max_height_mm=1000, overhang_mm=0)
    model = PalletModel(
        spec=spec,
        stacking_mode="heightfield",
        z_band_mm=0,
        control_config=ControlConfig(stability=StabilityConfig(mode="off")),
    )

    base = Box(box_id=1, length_mm=5, width_mm=10, height_mm=200, timestamp=0.0)
    base_preview = model.preview_place(base)
    assert base_preview.feasible
    assert base_preview.placement is not None
    assert int(base_preview.placement.z_mm) == 0
    model.commit_place(base_preview)

    candidate = Box(box_id=2, length_mm=5, width_mm=10, height_mm=50, timestamp=1.0)
    preview = model.preview_place(candidate)
    assert preview.feasible
    assert preview.placement is not None
    assert int(preview.placement.z_mm) == 0

    assert bool(preview.debug.get("z_band_enabled")) is True
    assert int(preview.debug.get("z_band_mm", -1)) == 0
    assert int(preview.debug.get("z_band_min_z", -1)) == 0
    before = int(preview.debug.get("z_band_candidates_before", -1))
    after = int(preview.debug.get("z_band_candidates_after", -1))
    assert before >= 0
    assert after >= 0
    assert after <= before
