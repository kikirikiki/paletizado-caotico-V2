from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel


def test_pallet_model_overhang_and_layers() -> None:
    spec = PalletSpec(length_mm=10, width_mm=10, max_height_mm=12, overhang_mm=2)
    model = PalletModel(spec=spec, heuristic="baf")

    box1 = Box(box_id=1, length_mm=10, width_mm=10, height_mm=5, timestamp=0.0)
    preview1 = model.preview_place(box1)
    assert preview1.feasible
    assert preview1.placement is not None
    assert preview1.placement.x_mm == -2
    assert preview1.placement.y_mm == -2
    placement1 = model.commit_place(preview1)
    assert placement1.layer_id == 0

    box2 = Box(box_id=2, length_mm=10, width_mm=10, height_mm=6, timestamp=1.0)
    preview2 = model.preview_place(box2)
    assert preview2.feasible
    assert preview2.placement is not None
    assert preview2.placement.layer_id == 1


def test_pallet_model_oversize_reason() -> None:
    spec = PalletSpec(length_mm=10, width_mm=10, max_height_mm=10)
    model = PalletModel(spec=spec)
    box = Box(box_id=3, length_mm=20, width_mm=20, height_mm=5, timestamp=0.0)
    preview = model.preview_place(box)
    assert not preview.feasible
    assert preview.infeasible_reason == "OVERSIZE"
