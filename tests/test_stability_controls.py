from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.domain.placement import Placement
from palca.packer.controls import StabilityConfig, StabilityPlacementControl
from palca.packer.pallet_model import PalletModel


def _support_box(x: int, y: int, size: int = 5, height: int = 5, box_id: int = 1) -> Placement:
    return Placement(
        x_mm=x,
        y_mm=y,
        z_mm=0,
        rot90=False,
        layer_id=0,
        length_mm=size,
        width_mm=size,
        height_mm=height,
        box_id=box_id,
        weight_kg=1.0,
    )


def test_corner_support_rule() -> None:
    spec = PalletSpec(length_mm=10, width_mm=10, max_height_mm=50)
    pallet = PalletModel(spec=spec)
    control = StabilityPlacementControl(
        StabilityConfig(mode="ratio+corners", min_support_ratio=0.1, eps_mm=0.01)
    )
    box = Box(box_id=99, length_mm=10, width_mm=10, height_mm=5, timestamp=0.0)

    positions = [(0, 0), (5, 0), (0, 5), (5, 5)]
    for count in range(4, -1, -1):
        pallet.placements = []
        for i in range(count):
            pallet.placements.append(_support_box(*positions[i], box_id=i + 1))

        candidate = Placement(
            x_mm=0,
            y_mm=0,
            z_mm=5,
            rot90=False,
            layer_id=1,
            length_mm=10,
            width_mm=10,
            height_mm=5,
            box_id=box.box_id,
            weight_kg=1.0,
        )
        result = control.evaluate(pallet=pallet, box=box, placement=candidate)
        if count == 4:
            assert result.feasible
        else:
            assert not result.feasible
            assert result.reason == "CORNER_SUPPORT"


def test_support_ratio_rule() -> None:
    spec = PalletSpec(length_mm=10, width_mm=10, max_height_mm=50)
    pallet = PalletModel(spec=spec)
    control = StabilityPlacementControl(
        StabilityConfig(mode="ratio", min_support_ratio=0.6, eps_mm=0.01)
    )
    box = Box(box_id=10, length_mm=10, width_mm=10, height_mm=5, timestamp=0.0)

    pallet.placements = [_support_box(0, 0, size=5, height=5)]
    candidate = Placement(
        x_mm=0,
        y_mm=0,
        z_mm=5,
        rot90=False,
        layer_id=1,
        length_mm=10,
        width_mm=10,
        height_mm=5,
        box_id=box.box_id,
        weight_kg=1.0,
    )
    result = control.evaluate(pallet=pallet, box=box, placement=candidate)
    assert not result.feasible
    assert result.reason == "SUPPORT_RATIO"

    pallet.placements = [_support_box(0, 0, size=10, height=5)]
    result_ok = control.evaluate(pallet=pallet, box=box, placement=candidate)
    assert result_ok.feasible


def test_settle_lowers_without_penetration() -> None:
    spec = PalletSpec(length_mm=10, width_mm=10, max_height_mm=50)
    pallet = PalletModel(spec=spec)
    control = StabilityPlacementControl(
        StabilityConfig(mode="ratio+corners+settle", min_support_ratio=0.1, eps_mm=0.01)
    )
    box = Box(box_id=7, length_mm=10, width_mm=10, height_mm=4, timestamp=0.0)

    # Base support at z=0 with height 5.
    pallet.placements = [_support_box(0, 0, size=10, height=5, box_id=1)]
    candidate = Placement(
        x_mm=0,
        y_mm=0,
        z_mm=12,
        rot90=False,
        layer_id=1,
        length_mm=10,
        width_mm=10,
        height_mm=4,
        box_id=box.box_id,
        weight_kg=1.0,
    )
    result = control.evaluate(pallet=pallet, box=box, placement=candidate)
    assert result.feasible
    assert result.placement.z_mm == 5

    # Add a blocking box at z=8..10; settle should stop at 10, not penetrate.
    pallet.placements.append(
        Placement(
            x_mm=0,
            y_mm=0,
            z_mm=8,
            rot90=False,
            layer_id=1,
            length_mm=10,
            width_mm=10,
            height_mm=2,
            box_id=2,
            weight_kg=1.0,
        )
    )
    candidate_high = Placement(
        x_mm=0,
        y_mm=0,
        z_mm=12,
        rot90=False,
        layer_id=2,
        length_mm=10,
        width_mm=10,
        height_mm=2,
        box_id=box.box_id,
        weight_kg=1.0,
    )
    result_blocked = control.evaluate(pallet=pallet, box=box, placement=candidate_high)
    assert result_blocked.feasible
    assert result_blocked.placement.z_mm == 10
