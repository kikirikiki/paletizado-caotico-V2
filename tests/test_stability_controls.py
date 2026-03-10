from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.domain.placement import Placement
from palca.packer.controls import StabilityConfig, StabilityPlacementControl
from palca.packer.pallet_model import PalletModel


def _support_box(
    x: int,
    y: int,
    size: int = 5,
    *,
    length_mm: int | None = None,
    width_mm: int | None = None,
    height: int = 5,
    box_id: int = 1,
) -> Placement:
    length = size if length_mm is None else length_mm
    width = size if width_mm is None else width_mm
    return Placement(
        x_mm=x,
        y_mm=y,
        z_mm=0,
        rot90=False,
        layer_id=0,
        length_mm=length,
        width_mm=width,
        height_mm=height,
        box_id=box_id,
        weight_kg=1.0,
    )


def test_com_support_rule() -> None:
    spec = PalletSpec(length_mm=10, width_mm=10, max_height_mm=50)
    pallet = PalletModel(spec=spec)
    control = StabilityPlacementControl(
        StabilityConfig(mode="ratio+corners", min_support_ratio=0.1, eps_mm=0.01)
    )
    box = Box(box_id=99, length_mm=10, width_mm=10, height_mm=5, timestamp=0.0)

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

    # Ratio passes but COM is unsupported.
    pallet.placements = [
        _support_box(0, 0, length_mm=10, width_mm=4, height=5, box_id=1),
        _support_box(0, 6, length_mm=10, width_mm=4, height=5, box_id=2),
    ]
    result = control.evaluate(pallet=pallet, box=box, placement=candidate)
    assert not result.feasible
    assert result.reason == "CORNER_SUPPORT"
    assert result.debug["com_supported"] is False

    # COM supported -> feasible.
    pallet.placements = [_support_box(0, 4, length_mm=10, width_mm=2, height=5, box_id=3)]
    result_ok = control.evaluate(pallet=pallet, box=box, placement=candidate)
    assert result_ok.feasible
    assert result_ok.debug["com_supported"] is True


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


def test_stand_hw_uses_stricter_support_threshold() -> None:
    spec = PalletSpec(length_mm=30, width_mm=30, max_height_mm=80)
    pallet = PalletModel(spec=spec)
    control = StabilityPlacementControl(
        StabilityConfig(mode="ratio", min_support_ratio=0.85, eps_mm=0.01)
    )
    box = Box(box_id=21, length_mm=20, width_mm=20, height_mm=5, timestamp=0.0)

    stand_candidate = Placement(
        x_mm=0,
        y_mm=0,
        z_mm=10,
        rot90=False,
        layer_id=1,
        length_mm=20,
        width_mm=20,
        height_mm=5,
        box_id=box.box_id,
        weight_kg=1.0,
        orientation_family="stand_hw",
    )

    pallet.placements = [_support_box(0, 0, length_mm=20, width_mm=18, height=10, box_id=1)]
    result_low_support = control.evaluate(pallet=pallet, box=box, placement=stand_candidate)
    assert not result_low_support.feasible
    assert result_low_support.reason == "SUPPORT_RATIO"
    assert pallet.stats.stand_hw_rejected_support_total == 1

    pallet.placements = [_support_box(0, 0, length_mm=20, width_mm=19, height=10, box_id=2)]
    result_good_support = control.evaluate(pallet=pallet, box=box, placement=stand_candidate)
    assert result_good_support.feasible

    planar_candidate = Placement(
        x_mm=stand_candidate.x_mm,
        y_mm=stand_candidate.y_mm,
        z_mm=stand_candidate.z_mm,
        rot90=stand_candidate.rot90,
        layer_id=stand_candidate.layer_id,
        length_mm=stand_candidate.length_mm,
        width_mm=stand_candidate.width_mm,
        height_mm=stand_candidate.height_mm,
        box_id=stand_candidate.box_id,
        weight_kg=stand_candidate.weight_kg,
        orientation_family="planar",
    )
    pallet.placements = [_support_box(0, 0, length_mm=20, width_mm=18, height=10, box_id=3)]
    result_planar = control.evaluate(pallet=pallet, box=box, placement=planar_candidate)
    assert result_planar.feasible


def test_support_frontier_refinement_rescues_near_support_ratio() -> None:
    spec = PalletSpec(length_mm=40, width_mm=20, max_height_mm=60)
    pallet = PalletModel(spec=spec)
    pallet.placements = [_support_box(0, 0, length_mm=9, width_mm=10, height=5, box_id=1)]
    box = Box(box_id=42, length_mm=10, width_mm=10, height_mm=5, timestamp=0.0)
    candidate = Placement(
        x_mm=1,
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
    control = StabilityPlacementControl(
        StabilityConfig(
            mode="ratio",
            min_support_ratio=0.85,
            eps_mm=0.01,
            support_frontier_refine_enabled=True,
            support_frontier_ratio_margin=0.06,
            support_frontier_xy_step_mm=1,
            support_frontier_max_xy_attempts=2,
        )
    )

    result = control.evaluate(pallet=pallet, box=box, placement=candidate)
    assert result.feasible
    assert result.placement.x_mm == 0
    assert result.debug["support_frontier_rescued"] is True
    assert result.debug["support_frontier_rescue_reason_support"] is True
    assert result.debug["support_frontier_rescue_reason_corners"] is False


def test_support_frontier_refinement_does_not_rescue_far_support_ratio() -> None:
    spec = PalletSpec(length_mm=40, width_mm=20, max_height_mm=60)
    pallet = PalletModel(spec=spec)
    pallet.placements = [_support_box(0, 0, length_mm=4, width_mm=10, height=5, box_id=1)]
    box = Box(box_id=43, length_mm=10, width_mm=10, height_mm=5, timestamp=0.0)
    candidate = Placement(
        x_mm=1,
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
    control = StabilityPlacementControl(
        StabilityConfig(
            mode="ratio",
            min_support_ratio=0.85,
            eps_mm=0.01,
            support_frontier_refine_enabled=True,
            support_frontier_ratio_margin=0.05,
            support_frontier_xy_step_mm=1,
            support_frontier_max_xy_attempts=4,
        )
    )

    result = control.evaluate(pallet=pallet, box=box, placement=candidate)
    assert not result.feasible
    assert result.reason == "SUPPORT_RATIO"
    assert bool(result.debug.get("support_frontier_refine_attempted", False)) is False
    assert bool(result.debug.get("support_frontier_rescued", False)) is False


def test_support_frontier_refinement_can_rescue_marginal_corner_support() -> None:
    spec = PalletSpec(length_mm=30, width_mm=20, max_height_mm=60)
    pallet = PalletModel(spec=spec)
    pallet.placements = [
        _support_box(1, 0, length_mm=4, width_mm=10, height=5, box_id=1),
        _support_box(7, 0, length_mm=4, width_mm=10, height=5, box_id=2),
    ]
    box = Box(box_id=44, length_mm=10, width_mm=10, height_mm=5, timestamp=0.0)
    candidate = Placement(
        x_mm=1,
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
    control = StabilityPlacementControl(
        StabilityConfig(
            mode="ratio+corners",
            min_support_ratio=0.65,
            eps_mm=0.01,
            support_frontier_refine_enabled=True,
            support_frontier_ratio_margin=0.05,
            support_frontier_xy_step_mm=1,
            support_frontier_max_xy_attempts=4,
        )
    )

    result = control.evaluate(pallet=pallet, box=box, placement=candidate)
    assert result.feasible
    assert result.placement.x_mm == 0
    assert result.debug["support_frontier_rescued"] is True
    assert result.debug["support_frontier_rescue_reason_support"] is False
    assert result.debug["support_frontier_rescue_reason_corners"] is True


def test_support_frontier_refinement_keeps_legacy_when_not_applicable() -> None:
    spec = PalletSpec(length_mm=40, width_mm=20, max_height_mm=60)
    pallet = PalletModel(spec=spec)
    pallet.placements = [_support_box(0, 0, length_mm=4, width_mm=10, height=5, box_id=1)]
    box = Box(box_id=45, length_mm=10, width_mm=10, height_mm=5, timestamp=0.0)
    candidate = Placement(
        x_mm=1,
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
    enabled = StabilityPlacementControl(
        StabilityConfig(
            mode="ratio",
            min_support_ratio=0.85,
            eps_mm=0.01,
            support_frontier_refine_enabled=True,
            support_frontier_ratio_margin=0.05,
            support_frontier_xy_step_mm=1,
            support_frontier_max_xy_attempts=4,
        )
    )
    disabled = StabilityPlacementControl(
        StabilityConfig(
            mode="ratio",
            min_support_ratio=0.85,
            eps_mm=0.01,
            support_frontier_refine_enabled=False,
            support_frontier_ratio_margin=0.05,
            support_frontier_xy_step_mm=1,
            support_frontier_max_xy_attempts=4,
        )
    )

    result_enabled = enabled.evaluate(pallet=pallet, box=box, placement=candidate)
    result_disabled = disabled.evaluate(pallet=pallet, box=box, placement=candidate)
    assert not result_enabled.feasible
    assert not result_disabled.feasible
    assert result_enabled.reason == result_disabled.reason == "SUPPORT_RATIO"
    assert result_enabled.placement == result_disabled.placement == candidate
