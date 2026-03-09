from __future__ import annotations

from dataclasses import dataclass
from types import MethodType, SimpleNamespace
from typing import Callable

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.scheduler.hard_floor_early_stand import HardFloorEarlyStandConfig, HardFloorFutureMetrics, passes_access_gate
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


@dataclass(frozen=True)
class PreviewSpec:
    z_mm: int
    x_mm: int
    y_mm: int
    length_mm: int
    width_mm: int
    height_mm: int
    packing_gain: float = 0.0
    fragmentation: float = 0.0
    orientation_family: str | None = None
    orientation_name: str | None = None


class FakePallet:
    def __init__(
        self,
        previews_by_box: dict[int, PreviewSpec],
        *,
        bin_area_mm2: int = 12_000,
        bin_length_mm: int = 120,
        bin_width_mm: int = 100,
        feasible_if: Callable[[Box, list[Placement]], bool] | None = None,
    ) -> None:
        self._previews_by_box = dict(previews_by_box)
        self._feasible_if = feasible_if
        self.placements: list[Placement] = []
        self.bin_area_mm2 = int(bin_area_mm2)
        self.spec = SimpleNamespace(
            offset_mm=0,
            bin_length_mm=int(bin_length_mm),
            bin_width_mm=int(bin_width_mm),
        )

    def preview_place(self, box: Box) -> PlacementPreview:
        if self._feasible_if is not None and not bool(self._feasible_if(box, list(self.placements))):
            return PlacementPreview(
                feasible=False,
                placement=None,
                packing_gain=0.0,
                fragmentation=0.0,
                infeasible_reason="NO_SPACE",
            )
        spec = self._previews_by_box.get(int(box.box_id))
        if spec is None:
            return PlacementPreview(
                feasible=False,
                placement=None,
                packing_gain=0.0,
                fragmentation=0.0,
                infeasible_reason="NO_SPACE",
            )
        placement = Placement(
            x_mm=int(spec.x_mm),
            y_mm=int(spec.y_mm),
            z_mm=int(spec.z_mm),
            rot90=False,
            layer_id=0 if int(spec.z_mm) == 0 else 1,
            length_mm=int(spec.length_mm),
            width_mm=int(spec.width_mm),
            height_mm=int(spec.height_mm),
            box_id=box.box_id,
            orientation_family=spec.orientation_family,
            orientation_name=spec.orientation_name,
        )
        return PlacementPreview(
            feasible=True,
            placement=placement,
            packing_gain=float(spec.packing_gain),
            fragmentation=float(spec.fragmentation),
            height_after_mm=int(spec.z_mm) + int(spec.height_mm),
            infeasible_reason=None,
        )

    def commit_place(self, preview: PlacementPreview) -> Placement:
        assert preview.placement is not None
        self.placements.append(preview.placement)
        return preview.placement

    def current_height_mm(self) -> int:
        if not self.placements:
            return 0
        return max(int(p.z_mm) + int(p.height_mm) for p in self.placements)


def _box(box_id: int) -> Box:
    return Box(
        box_id=int(box_id),
        length_mm=1,
        width_mm=1,
        height_mm=1,
        timestamp=0.0,
        destination=1,
    )


def _placement(
    *,
    box_id: int,
    z_mm: int,
    x_mm: int,
    y_mm: int,
    length_mm: int,
    width_mm: int,
    height_mm: int,
    orientation_family: str | None = None,
    orientation_name: str | None = None,
) -> Placement:
    return Placement(
        x_mm=int(x_mm),
        y_mm=int(y_mm),
        z_mm=int(z_mm),
        rot90=False,
        layer_id=0 if int(z_mm) == 0 else 1,
        length_mm=int(length_mm),
        width_mm=int(width_mm),
        height_mm=int(height_mm),
        box_id=int(box_id),
        orientation_family=orientation_family,
        orientation_name=orientation_name,
    )


def _sim_state(*, boxes: list[Box], pallet: FakePallet) -> SchedulerSimState:
    return SchedulerSimState(
        now=0.0,
        ramps={1: list(boxes)},
        pallets={1: pallet},
        pallet_blocked=set(),
        remaining_total=len(boxes),
    )


def _future_metrics_for_access_gate(*, mouth_mm: float) -> HardFloorFutureMetrics:
    return HardFloorFutureMetrics(
        placed_count=0,
        largest_free_rect_area_mm2=0.0,
        free_components=1,
        inaccessible_pocket_area_mm2=0.0,
        boundary_connected_free_area_mm2=1_000.0,
        height_std_mm=0.0,
        min_boundary_mouth_mm=float(mouth_mm),
    )


def test_access_gate_mouth_passes_when_baseline_below_threshold_and_stand_matches_baseline() -> None:
    config = HardFloorEarlyStandConfig(min_access_mouth_mm=180)
    baseline = _future_metrics_for_access_gate(mouth_mm=42.0)
    stand = _future_metrics_for_access_gate(mouth_mm=42.0)

    assert passes_access_gate(baseline=baseline, candidate=stand, config=config)


def test_access_gate_mouth_fails_when_baseline_below_threshold_and_stand_is_worse() -> None:
    config = HardFloorEarlyStandConfig(min_access_mouth_mm=180)
    baseline = _future_metrics_for_access_gate(mouth_mm=42.0)
    stand = _future_metrics_for_access_gate(mouth_mm=41.0)

    assert not passes_access_gate(baseline=baseline, candidate=stand, config=config)


def test_access_gate_mouth_fails_when_baseline_above_threshold_and_stand_drops_below_threshold() -> None:
    config = HardFloorEarlyStandConfig(min_access_mouth_mm=180)
    baseline = _future_metrics_for_access_gate(mouth_mm=200.0)
    stand = _future_metrics_for_access_gate(mouth_mm=179.0)

    assert not passes_access_gate(baseline=baseline, candidate=stand, config=config)


def test_hard_floor_phase_never_chooses_stacking_while_floor_exists() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(z_mm=120, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=9.0),
            2: PreviewSpec(z_mm=0, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=0.1),
            3: PreviewSpec(z_mm=0, x_mm=40, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=0.1),
        }
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=3,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
        )
    )

    plan1 = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan1 is not None
    assert int(plan1.preview.placement.z_mm) == 0
    pallet.commit_place(plan1.preview)

    plan2 = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(3)], pallet=pallet))
    assert plan2 is not None
    assert int(plan2.preview.placement.z_mm) == 0


def test_hard_floor_phase_exits_when_no_floor_candidates_and_falls_back_to_normal() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(z_mm=120, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=8.0),
            2: PreviewSpec(z_mm=140, x_mm=40, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=6.0),
        }
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=5,
            hard_floor_phase_min_base_candidates=1,
        )
    )

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan is not None
    assert int(plan.preview.placement.z_mm) > 0
    assert int(scheduler.hard_floor_phase_exit_no_floor_total) == 1


def test_hard_floor_phase_stand_mix_bonus_can_choose_stand_hw_on_floor() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(
                z_mm=0,
                x_mm=60,
                y_mm=0,
                length_mm=60,
                width_mm=40,
                height_mm=50,
                packing_gain=3.0,
                orientation_family="planar",
                orientation_name="planar_lw",
            ),
            2: PreviewSpec(
                z_mm=0,
                x_mm=0,
                y_mm=40,
                length_mm=40,
                width_mm=60,
                height_mm=30,
                packing_gain=3.0,
                orientation_family="stand_hw",
                orientation_name="stand_hw_lh",
            ),
        }
    )
    pallet.placements.append(
        _placement(
            box_id=100,
            z_mm=0,
            x_mm=0,
            y_mm=0,
            length_mm=60,
            width_mm=40,
            height_mm=30,
            orientation_family="stand_hw",
            orientation_name="stand_hw_seed",
        )
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=3,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_stand_mix_bonus=1.0,
        )
    )

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan is not None
    assert str(plan.preview.placement.orientation_family) == "stand_hw"
    assert int(scheduler.hard_floor_phase_stand_mix_candidates_total) == 1
    assert int(scheduler.hard_floor_phase_stand_mix_bonus_applied_total) == 1
    assert int(scheduler.hard_floor_phase_stand_mix_chosen_total) == 1
    assert int(scheduler.hard_floor_phase_stand_hw_chosen_total) == 1


def test_hard_floor_phase_stand_mix_bonus_zero_keeps_current_behavior() -> None:
    previews = {
        1: PreviewSpec(
            z_mm=0,
            x_mm=60,
            y_mm=0,
            length_mm=60,
            width_mm=40,
            height_mm=50,
            packing_gain=3.0,
            orientation_family="planar",
            orientation_name="planar_lw",
        ),
        2: PreviewSpec(
            z_mm=0,
            x_mm=0,
            y_mm=40,
            length_mm=40,
            width_mm=60,
            height_mm=30,
            packing_gain=3.0,
            orientation_family="stand_hw",
            orientation_name="stand_hw_lh",
        ),
    }
    pallet_a = FakePallet(previews)
    pallet_b = FakePallet(previews)
    seed = _placement(
        box_id=100,
        z_mm=0,
        x_mm=0,
        y_mm=0,
        length_mm=60,
        width_mm=40,
        height_mm=30,
        orientation_family="stand_hw",
        orientation_name="stand_hw_seed",
    )
    pallet_a.placements.append(seed)
    pallet_b.placements.append(seed)
    baseline = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=3,
            hard_floor_phase_min_base_candidates=1,
        )
    )
    with_zero_bonus = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=3,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_stand_mix_bonus=0.0,
        )
    )

    baseline_plan = baseline.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet_a))
    zero_bonus_plan = with_zero_bonus.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet_b))

    assert baseline_plan is not None and zero_bonus_plan is not None
    assert str(baseline_plan.preview.placement.orientation_family) == "planar"
    assert str(zero_bonus_plan.preview.placement.orientation_family) == str(
        baseline_plan.preview.placement.orientation_family
    )
    assert int(with_zero_bonus.hard_floor_phase_stand_mix_bonus_applied_total) == 0
    assert int(with_zero_bonus.hard_floor_phase_stand_mix_chosen_total) == 0


def test_hard_floor_phase_policy_off_keeps_legacy_behavior() -> None:
    previews = {
        1: PreviewSpec(
            z_mm=0,
            x_mm=60,
            y_mm=0,
            length_mm=60,
            width_mm=40,
            height_mm=50,
            packing_gain=3.0,
            orientation_family="planar",
            orientation_name="planar_lw",
        ),
        2: PreviewSpec(
            z_mm=0,
            x_mm=0,
            y_mm=40,
            length_mm=40,
            width_mm=60,
            height_mm=30,
            packing_gain=3.0,
            orientation_family="stand_hw",
            orientation_name="stand_hw_lh",
        ),
    }
    pallet_legacy = FakePallet(previews)
    pallet_off = FakePallet(previews)
    seed = _placement(
        box_id=100,
        z_mm=0,
        x_mm=0,
        y_mm=0,
        length_mm=60,
        width_mm=40,
        height_mm=30,
        orientation_family="stand_hw",
        orientation_name="stand_hw_seed",
    )
    pallet_legacy.placements.append(seed)
    pallet_off.placements.append(seed)
    legacy = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=3,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_stand_mix_bonus=1.0,
        )
    )
    policy_off = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=3,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_stand_mix_bonus=1.0,
            hard_floor_phase_early_stand_policy="off",
        )
    )
    legacy_plan = legacy.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet_legacy))
    off_plan = policy_off.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet_off))
    assert legacy_plan is not None and off_plan is not None
    assert str(legacy_plan.preview.placement.orientation_family) == "stand_hw"
    assert str(off_plan.preview.placement.orientation_family) == str(legacy_plan.preview.placement.orientation_family)
    assert int(policy_off.hard_floor_phase_stand_mix_bonus_applied_total) == 1
    assert int(policy_off.hard_floor_phase_stand_hw_chosen_total) == 1


def test_hard_floor_phase_regret_gated_exits_hard_floor_when_only_stand_floor_exists() -> None:
    previews = {
        1: PreviewSpec(
            z_mm=0,
            x_mm=0,
            y_mm=0,
            length_mm=90,
            width_mm=70,
            height_mm=40,
            packing_gain=3.0,
            orientation_family="stand_hw",
            orientation_name="stand_hw_a",
        ),
        2: PreviewSpec(
            z_mm=0,
            x_mm=90,
            y_mm=0,
            length_mm=90,
            width_mm=70,
            height_mm=40,
            packing_gain=2.5,
            orientation_family="stand_hw",
            orientation_name="stand_hw_b",
        ),
    }
    pallet_off = FakePallet(previews, bin_length_mm=220, bin_width_mm=160)
    pallet_regret = FakePallet(previews, bin_length_mm=220, bin_width_mm=160)

    off_mode = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_early_stand_policy="off",
        )
    )
    regret_mode = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_early_stand_policy="regret_gated",
            hard_floor_phase_early_stand_min_access_mouth_mm=180,
        )
    )

    off_plan = off_mode.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet_off))
    regret_plan = regret_mode.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet_regret))
    assert off_plan is not None and regret_plan is not None
    assert str(off_plan.preview.placement.orientation_family) == "stand_hw"
    assert int(off_mode.hard_floor_phase_chosen_total) == 1
    assert int(off_mode.hard_floor_phase_stand_hw_chosen_total) == 1
    assert int(regret_mode.hard_floor_phase_chosen_total) == 0
    assert int(regret_mode.hard_floor_phase_exit_no_floor_total) == 1
    assert int(regret_mode.hard_floor_phase_stand_hw_chosen_total) == 0
    assert int(regret_mode.early_stand_selected_total) == 0


def test_hard_floor_phase_regret_gated_rejects_when_projected_placed_loss_increases() -> None:
    def feasible_if(box: Box, placements: list[Placement]) -> bool:
        has_stand = any(str(getattr(p, "orientation_family", "") or "").lower() == "stand_hw" for p in placements)
        box_id = int(box.box_id)
        if box_id == 2:
            return len(placements) == 0
        if box_id in {1, 3}:
            return not has_stand
        return True

    pallet = FakePallet(
        {
            1: PreviewSpec(
                z_mm=0,
                x_mm=0,
                y_mm=0,
                length_mm=80,
                width_mm=80,
                height_mm=40,
                packing_gain=4.0,
                orientation_family="planar",
                orientation_name="planar_base",
            ),
            2: PreviewSpec(
                z_mm=0,
                x_mm=0,
                y_mm=0,
                length_mm=80,
                width_mm=80,
                height_mm=40,
                packing_gain=4.0,
                orientation_family="stand_hw",
                orientation_name="stand_hw_base",
            ),
            3: PreviewSpec(
                z_mm=0,
                x_mm=80,
                y_mm=0,
                length_mm=80,
                width_mm=80,
                height_mm=40,
                packing_gain=3.5,
                orientation_family="planar",
                orientation_name="planar_followup",
            ),
        },
        bin_length_mm=220,
        bin_width_mm=220,
        feasible_if=feasible_if,
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=3,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_early_stand_policy="regret_gated",
            hard_floor_phase_early_stand_max_placed_loss=0,
        )
    )
    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2), _box(3)], pallet=pallet))
    assert plan is not None
    assert str(plan.preview.placement.orientation_family) == "planar"
    assert int(scheduler.early_stand_reject_regret_total) >= 1


def test_hard_floor_phase_regret_gated_rejects_when_access_mouth_is_too_small() -> None:
    def block_stand_after_first_place(box: Box, placements: list[Placement]) -> bool:
        if int(box.box_id) == 2 and placements:
            return False
        return True

    pallet = FakePallet(
        {
            1: PreviewSpec(
                z_mm=0,
                x_mm=0,
                y_mm=0,
                length_mm=90,
                width_mm=90,
                height_mm=40,
                packing_gain=3.0,
                orientation_family="planar",
                orientation_name="planar_lw",
            ),
            2: PreviewSpec(
                z_mm=0,
                x_mm=0,
                y_mm=0,
                length_mm=180,
                width_mm=20,
                height_mm=40,
                packing_gain=3.0,
                orientation_family="stand_hw",
                orientation_name="stand_hw_strip",
            ),
        },
        bin_length_mm=200,
        bin_width_mm=200,
        feasible_if=block_stand_after_first_place,
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_early_stand_policy="regret_gated",
            hard_floor_phase_early_stand_min_access_mouth_mm=180,
        )
    )
    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan is not None
    assert str(plan.preview.placement.orientation_family) == "planar"
    assert int(scheduler.early_stand_admitted_total) == 0
    assert int(scheduler.early_stand_selected_total) == 0
    assert int(scheduler.hard_floor_phase_stand_hw_chosen_total) == 0
    assert int(scheduler.early_stand_reject_access_total) >= 1
    assert int(scheduler.early_stand_reject_debug_total) >= 1
    assert len(scheduler.early_stand_reject_debug_samples) >= 1
    sample = scheduler.early_stand_reject_debug_samples[0]
    assert str(sample["reject_reason"]) == "access"
    assert "min_boundary_mouth_mm_below_threshold" in list(sample["access_reject_checks"])
    assert "min_boundary_mouth_mm(" in str(sample["access_reject_reason"])
    assert int(sample["step"]) == 0
    assert int(sample["pallet_id"]) == 1
    assert sample["boundary_connected_free_area_mm2"]["baseline"] is not None
    assert sample["boundary_connected_free_area_mm2"]["stand"] is not None
    assert sample["inaccessible_pocket_area_mm2"]["baseline"] is not None
    assert sample["inaccessible_pocket_area_mm2"]["stand"] is not None
    assert sample["min_boundary_mouth_mm"]["baseline"] is not None
    assert sample["min_boundary_mouth_mm"]["stand"] is not None
    assert sample["largest_free_rect_area_mm2"]["baseline"] is not None
    assert sample["largest_free_rect_area_mm2"]["stand"] is not None
    assert sample["placed_count"]["baseline"] is not None
    assert sample["placed_count"]["stand"] is not None
    assert sample["height_std_mm"]["baseline"] is not None
    assert sample["height_std_mm"]["stand"] is not None


def test_hard_floor_phase_regret_gated_rejected_stand_cannot_enter_legacy_pool() -> None:
    def block_stand_after_first_place(box: Box, placements: list[Placement]) -> bool:
        if int(box.box_id) == 2 and placements:
            return False
        return True

    pallet = FakePallet(
        {
            1: PreviewSpec(
                z_mm=0,
                x_mm=0,
                y_mm=0,
                length_mm=90,
                width_mm=90,
                height_mm=40,
                packing_gain=2.0,
                orientation_family="planar",
                orientation_name="planar_lw",
            ),
            2: PreviewSpec(
                z_mm=0,
                x_mm=0,
                y_mm=0,
                length_mm=180,
                width_mm=20,
                height_mm=40,
                packing_gain=8.0,
                orientation_family="stand_hw",
                orientation_name="stand_hw_strip",
            ),
        },
        bin_length_mm=200,
        bin_width_mm=200,
        feasible_if=block_stand_after_first_place,
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_early_stand_policy="regret_gated",
            hard_floor_phase_early_stand_min_access_mouth_mm=180,
        )
    )

    def stand_pref_score(
        self: SchedulerV1,
        *,
        pallet: FakePallet,  # type: ignore[override]
        preview: PlacementPreview,
        future_boxes: list[Box] | None = None,
        selected_box: Box | None = None,
        include_stand_bias: bool = True,
    ) -> float:
        _ = pallet, future_boxes, selected_box, include_stand_bias
        placement = getattr(preview, "placement", None)
        if placement is None:
            return -1e9
        family = str(getattr(placement, "orientation_family", "") or "").lower()
        return 10.0 if family == "stand_hw" else 5.0

    scheduler._hard_floor_phase_score_preview = MethodType(stand_pref_score, scheduler)  # type: ignore[method-assign]
    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan is not None
    assert str(plan.preview.placement.orientation_family) == "planar"
    assert int(scheduler.early_stand_admitted_total) == 0
    assert int(scheduler.early_stand_selected_total) == 0
    assert int(scheduler.hard_floor_phase_stand_hw_chosen_total) == 0
    assert int(scheduler.early_stand_reject_access_total) >= 1


def test_hard_floor_phase_regret_gated_limits_to_one_early_stand_per_pallet() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(
                z_mm=0,
                x_mm=0,
                y_mm=0,
                length_mm=70,
                width_mm=70,
                height_mm=40,
                packing_gain=2.0,
                orientation_family="planar",
                orientation_name="planar_a",
            ),
            2: PreviewSpec(
                z_mm=0,
                x_mm=0,
                y_mm=0,
                length_mm=70,
                width_mm=70,
                height_mm=40,
                packing_gain=2.0,
                orientation_family="stand_hw",
                orientation_name="stand_hw_a",
            ),
            3: PreviewSpec(
                z_mm=0,
                x_mm=70,
                y_mm=0,
                length_mm=70,
                width_mm=70,
                height_mm=40,
                packing_gain=2.0,
                orientation_family="planar",
                orientation_name="planar_b",
            ),
            4: PreviewSpec(
                z_mm=0,
                x_mm=70,
                y_mm=0,
                length_mm=70,
                width_mm=70,
                height_mm=40,
                packing_gain=2.0,
                orientation_family="stand_hw",
                orientation_name="stand_hw_b",
            ),
        },
        bin_length_mm=240,
        bin_width_mm=240,
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=6,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_early_stand_policy="regret_gated",
            hard_floor_phase_early_stand_max_count=1,
            hard_floor_phase_early_stand_min_access_mouth_mm=20,
        )
    )

    def stand_pref_score(
        self: SchedulerV1,
        *,
        pallet: FakePallet,  # type: ignore[override]
        preview: PlacementPreview,
        future_boxes: list[Box] | None = None,
        selected_box: Box | None = None,
        include_stand_bias: bool = True,
    ) -> float:
        _ = pallet, future_boxes, selected_box, include_stand_bias
        placement = getattr(preview, "placement", None)
        if placement is None:
            return -1e9
        family = str(getattr(placement, "orientation_family", "") or "").lower()
        return 10.0 if family == "stand_hw" else 5.0

    scheduler._hard_floor_phase_score_preview = MethodType(stand_pref_score, scheduler)  # type: ignore[method-assign]

    plan1 = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan1 is not None
    assert str(plan1.preview.placement.orientation_family) == "stand_hw"
    pallet.commit_place(plan1.preview)

    plan2 = scheduler.choose_action(_sim_state(boxes=[_box(3), _box(4)], pallet=pallet))
    assert plan2 is not None
    assert str(plan2.preview.placement.orientation_family) == "planar"
    assert int(scheduler.early_stand_selected_total) == 1


def test_hard_floor_phase_regret_gated_ignores_legacy_stand_mix_bonus() -> None:
    previews = {
        1: PreviewSpec(
            z_mm=0,
            x_mm=60,
            y_mm=0,
            length_mm=60,
            width_mm=40,
            height_mm=50,
            packing_gain=3.0,
            orientation_family="planar",
            orientation_name="planar_lw",
        ),
        2: PreviewSpec(
            z_mm=0,
            x_mm=0,
            y_mm=40,
            length_mm=40,
            width_mm=60,
            height_mm=30,
            packing_gain=3.0,
            orientation_family="stand_hw",
            orientation_name="stand_hw_lh",
        ),
    }
    pallet_bonus = FakePallet(previews)
    pallet_regret = FakePallet(previews)
    seed = _placement(
        box_id=100,
        z_mm=0,
        x_mm=0,
        y_mm=0,
        length_mm=60,
        width_mm=40,
        height_mm=30,
        orientation_family="stand_hw",
        orientation_name="stand_hw_seed",
    )
    pallet_bonus.placements.append(seed)
    pallet_regret.placements.append(seed)

    bonus_mode = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=3,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_stand_mix_bonus=1.0,
            hard_floor_phase_early_stand_policy="bonus",
        )
    )
    regret_mode = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=3,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_stand_mix_bonus=1.0,
            hard_floor_phase_early_stand_policy="regret_gated",
            hard_floor_phase_early_stand_min_access_mouth_mm=180,
        )
    )

    bonus_plan = bonus_mode.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet_bonus))
    regret_plan = regret_mode.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet_regret))
    assert bonus_plan is not None and regret_plan is not None
    assert str(bonus_plan.preview.placement.orientation_family) == "stand_hw"
    assert str(regret_plan.preview.placement.orientation_family) == "planar"
    assert int(bonus_mode.hard_floor_phase_stand_hw_chosen_total) == 1
    assert int(bonus_mode.hard_floor_phase_stand_mix_bonus_applied_total) == 1
    assert int(regret_mode.hard_floor_phase_stand_mix_bonus_applied_total) == 0


def test_hard_floor_phase_disabled_mode_keeps_existing_behavior() -> None:
    pallet_a = FakePallet(
        {
            1: PreviewSpec(z_mm=120, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=9.0),
            2: PreviewSpec(z_mm=0, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=0.1),
        }
    )
    pallet_b = FakePallet(
        {
            1: PreviewSpec(z_mm=120, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=9.0),
            2: PreviewSpec(z_mm=0, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=0.1),
        }
    )

    baseline = SchedulerV1(SchedulerConfig(lookahead_k=2))
    disabled = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=0,
            hard_floor_phase_min_base_candidates=1,
        )
    )

    baseline_plan = baseline.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet_a))
    disabled_plan = disabled.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet_b))

    assert baseline_plan is not None and disabled_plan is not None
    assert int(baseline_plan.box_id) == int(disabled_plan.box_id)
    assert int(baseline_plan.preview.placement.z_mm) == int(disabled_plan.preview.placement.z_mm)


def test_hard_floor_phase_applies_same_root_logic_with_micro_planner_depth0() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(z_mm=120, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=9.0),
            2: PreviewSpec(z_mm=0, x_mm=0, y_mm=0, length_mm=50, width_mm=40, height_mm=40, packing_gain=0.1),
        }
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=True,
            micro_plan_depth=3,
            micro_plan_width=6,
            micro_plan_topk_per_step=6,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_stand_mix_bonus=0.7,
        )
    )

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan is not None
    assert int(plan.preview.placement.z_mm) == 0
    assert int(scheduler.hard_floor_phase_chosen_total) == 1


def test_hard_floor_phase_kpis_are_exposed() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        hard_floor_phase_end_step=8,
        hard_floor_phase_min_base_candidates=1,
        hard_floor_phase_lookahead_items=9,
        hard_floor_phase_stand_mix_bonus=0.5,
        hard_floor_phase_early_stand_policy="regret_gated",
        hard_floor_phase_early_stand_max_count=1,
        hard_floor_phase_early_stand_candidate_cap=3,
        hard_floor_phase_early_stand_max_placed_loss=0,
        hard_floor_phase_early_stand_max_largest_free_rect_loss_ratio=0.08,
        hard_floor_phase_early_stand_max_height_std_increase_mm=40.0,
        hard_floor_phase_early_stand_min_access_mouth_mm=180,
    )
    policy._scheduler.hard_floor_phase_active_total = 3
    policy._scheduler.hard_floor_phase_floor_candidates_seen_total = 12
    policy._scheduler.hard_floor_phase_chosen_total = 2
    policy._scheduler.hard_floor_phase_stand_hw_chosen_total = 1
    policy._scheduler.hard_floor_phase_exit_no_floor_total = 1
    policy._scheduler.hard_floor_phase_exit_end_step_total = 1
    policy._scheduler.hard_floor_phase_score_sum = 5.0
    policy._scheduler.hard_floor_phase_stand_mix_bonus_applied_total = 4
    policy._scheduler.hard_floor_phase_stand_mix_candidates_total = 6
    policy._scheduler.hard_floor_phase_stand_mix_chosen_total = 1
    policy._scheduler.early_stand_candidates_total = 7
    policy._scheduler.early_stand_eval_total = 5
    policy._scheduler.early_stand_admitted_total = 2
    policy._scheduler.early_stand_selected_total = 1
    policy._scheduler.early_stand_reject_geom_total = 1
    policy._scheduler.early_stand_reject_regret_total = 1
    policy._scheduler.early_stand_reject_access_total = 1
    policy._scheduler.early_stand_selected_step_first = 0
    policy._scheduler.early_stand_projected_placed_loss_sum = 2.0
    policy._scheduler.early_stand_projected_lfr_loss_ratio_sum = 0.2
    policy._scheduler.early_stand_projected_height_std_increase_sum = 12.0
    policy._scheduler.early_stand_reject_debug_limit = 2
    policy._scheduler.early_stand_reject_debug_total = 3
    policy._scheduler.early_stand_reject_debug_samples = [
        {
            "step": 0,
            "pallet_id": 1,
            "orientation": "stand_hw_lh",
            "x": 0,
            "y": 0,
            "l": 40,
            "w": 60,
            "reject_reason": "access",
            "access_reject_checks": ["min_boundary_mouth_mm_below_threshold"],
            "access_reject_reason": "min_boundary_mouth_mm(120.0<180.0)",
            "boundary_connected_free_area_mm2": {"baseline": 1000.0, "stand": 950.0},
            "inaccessible_pocket_area_mm2": {"baseline": 0.0, "stand": 0.0},
            "min_boundary_mouth_mm": {"baseline": 200.0, "stand": 120.0},
            "largest_free_rect_area_mm2": {"baseline": 4000.0, "stand": 3600.0},
            "placed_count": {"baseline": 4, "stand": 4},
            "height_std_mm": {"baseline": 20.0, "stand": 30.0},
        }
    ]

    kpis = policy.collect_kpis()

    assert int(kpis["hard_floor_phase_active_total"]) == 3
    assert int(kpis["hard_floor_phase_floor_candidates_seen_total"]) == 12
    assert int(kpis["hard_floor_phase_chosen_total"]) == 2
    assert int(kpis["hard_floor_phase_stand_hw_chosen_total"]) == 1
    assert int(kpis["hard_floor_phase_exit_no_floor_total"]) == 1
    assert int(kpis["hard_floor_phase_exit_end_step_total"]) == 1
    assert float(kpis["hard_floor_phase_stand_mix_bonus"]) == 0.5
    assert int(kpis["hard_floor_phase_stand_mix_bonus_applied_total"]) == 4
    assert int(kpis["hard_floor_phase_stand_mix_candidates_total"]) == 6
    assert int(kpis["hard_floor_phase_stand_mix_chosen_total"]) == 1
    assert float(kpis["hard_floor_phase_score_mean"]) == 2.5
    assert str(kpis["hard_floor_phase_early_stand_policy"]) == "regret_gated"
    assert int(kpis["early_stand_candidates_total"]) == 7
    assert int(kpis["early_stand_eval_total"]) == 5
    assert int(kpis["early_stand_admitted_total"]) == 2
    assert int(kpis["early_stand_selected_total"]) == 1
    assert int(kpis["early_stand_reject_geom_total"]) == 1
    assert int(kpis["early_stand_reject_regret_total"]) == 1
    assert int(kpis["early_stand_reject_access_total"]) == 1
    assert int(kpis["early_stand_selected_step_first"]) == 0
    assert int(kpis["early_stand_reject_debug_limit"]) == 2
    assert int(kpis["early_stand_reject_debug_total"]) == 3
    assert bool(kpis["early_stand_reject_debug_truncated"]) is True
    samples = kpis["early_stand_reject_debug_samples"]
    assert isinstance(samples, list) and len(samples) == 1
    assert str(samples[0]["reject_reason"]) == "access"
