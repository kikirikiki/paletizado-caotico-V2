from __future__ import annotations

import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.domain.placement import Placement, PlacementPreview
from palca.packer.controls import StabilityConfig, StabilityPlacementControl
from palca.packer.pallet_model import PalletModel


def _commit_manual(model: PalletModel, placement: Placement) -> None:
    preview = PlacementPreview(
        feasible=True,
        placement=placement,
        packing_gain=0.0,
        fragmentation=0.0,
    )
    model.commit_place(preview)


@pytest.mark.parametrize("heuristic", ["baf", "bssf"])
def test_com_supported_over_corners(heuristic: str) -> None:
    spec = PalletSpec(length_mm=1200, width_mm=800, max_height_mm=2400, allow_rotate=True)
    model = PalletModel(spec=spec, heuristic=heuristic)

    _commit_manual(
        model,
        Placement(
            x_mm=0,
            y_mm=0,
            z_mm=0,
            rot90=True,
            layer_id=0,
            length_mm=430,
            width_mm=575,
            height_mm=330,
            box_id=1,
            weight_kg=1.0,
        ),
    )
    _commit_manual(
        model,
        Placement(
            x_mm=430,
            y_mm=0,
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=605,
            width_mm=450,
            height_mm=345,
            box_id=2,
            weight_kg=1.0,
        ),
    )
    _commit_manual(
        model,
        Placement(
            x_mm=430,
            y_mm=0,
            z_mm=345,
            rot90=False,
            layer_id=1,
            length_mm=570,
            width_mm=435,
            height_mm=330,
            box_id=3,
            weight_kg=1.0,
        ),
    )

    z_mm = model.current_height_mm()
    assert z_mm == 675

    placement = Placement(
        x_mm=430,
        y_mm=0,
        z_mm=z_mm,
        rot90=False,
        layer_id=len(model.layers),
        length_mm=635,
        width_mm=350,
        height_mm=330,
        box_id=4,
        weight_kg=1.0,
    )

    ratio, _ = model.support_surface_ratio(placement, eps_mm=1e-6)
    assert ratio >= 0.75
    assert model.corners_supported(placement, eps_mm=1e-6) is False

    com_supported, overlaps = model.com_support_info(placement, eps_mm=1e-6)
    assert com_supported is True
    assert overlaps >= 1

    control = StabilityPlacementControl(
        StabilityConfig(mode="ratio+corners", min_support_ratio=0.75, eps_mm=1e-6)
    )
    result = control.evaluate(
        pallet=model,
        box=Box(box_id=4, length_mm=635, width_mm=350, height_mm=330, timestamp=3.0),
        placement=placement,
    )
    assert result.feasible
