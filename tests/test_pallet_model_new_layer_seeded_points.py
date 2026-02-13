from __future__ import annotations

import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel


@pytest.mark.parametrize("heuristic", ["baf", "bssf"])
def test_new_layer_seeded_points_from_top_surfaces(heuristic: str) -> None:
    spec = PalletSpec(length_mm=1200, width_mm=800, max_height_mm=2400, allow_rotate=True)
    model = PalletModel(spec=spec, heuristic=heuristic)

    box1 = Box(box_id=1, length_mm=575, width_mm=430, height_mm=330, timestamp=0.0)
    prev1 = model.preview_place(box1)
    assert prev1.feasible
    assert prev1.placement is not None
    model.commit_place(prev1)

    box2 = Box(box_id=2, length_mm=605, width_mm=450, height_mm=345, timestamp=1.0)
    prev2 = model.preview_place(box2)
    assert prev2.feasible
    assert prev2.placement is not None
    model.commit_place(prev2)

    z = model.current_height_mm()
    box3 = Box(box_id=3, length_mm=570, width_mm=435, height_mm=330, timestamp=2.0)
    prev3 = model.preview_place(box3)
    assert prev3.feasible is True
    assert prev3.placement is not None
    assert prev3.placement.z_mm == z

    ratio, _ = model.support_surface_ratio(prev3.placement, eps_mm=1e-6)
    assert ratio >= 0.75
    assert model.corners_supported(prev3.placement, eps_mm=1e-6) is True
