from __future__ import annotations

import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel


@pytest.mark.parametrize("heuristic", ["baf", "bssf"])
def test_pallet_model_rotation_future_fit(heuristic: str) -> None:
    spec = PalletSpec(length_mm=1200, width_mm=800, allow_rotate=True)
    model = PalletModel(spec=spec, heuristic=heuristic)

    box1 = Box(box_id=1, length_mm=575, width_mm=430, height_mm=330, timestamp=0.0)
    box2 = Box(box_id=2, length_mm=605, width_mm=450, height_mm=345, timestamp=1.0)

    prev1 = model.preview_place(box1)
    assert prev1.feasible
    assert prev1.placement is not None
    assert prev1.placement.rot90 is True or (
        prev1.placement.length_mm,
        prev1.placement.width_mm,
    ) == (430, 575)

    model.commit_place(prev1)

    prev2 = model.preview_place(box2)
    assert prev2.feasible
    assert prev2.placement is not None
    assert prev2.placement.z_mm == 0
    assert prev2.placement.layer_id == 0
