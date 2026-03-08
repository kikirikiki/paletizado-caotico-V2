from __future__ import annotations

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


class FakePallet:
    def __init__(self, by_box_id: dict[int, tuple[str, float, int]]) -> None:
        self._by_box_id = dict(by_box_id)
        self.placements: list[Placement] = []

    def preview_place(self, box: Box) -> PlacementPreview:
        family, gain, z_mm = self._by_box_id[int(box.box_id)]
        placement = Placement(
            x_mm=0,
            y_mm=0,
            z_mm=int(z_mm),
            rot90=False,
            layer_id=(0 if int(z_mm) == 0 else 1),
            length_mm=int(box.length_mm),
            width_mm=int(box.width_mm),
            height_mm=int(box.height_mm),
            box_id=box.box_id,
            orientation_family=str(family),
            orientation_name=f"{family}_fake",
        )
        return PlacementPreview(
            feasible=True,
            placement=placement,
            packing_gain=float(gain),
            fragmentation=0.0,
            height_after_mm=int(z_mm) + int(box.height_mm),
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


def _sim_state_for_two_boxes(*, pallet: FakePallet) -> SchedulerSimState:
    boxes = [
        Box(box_id=1, length_mm=100, width_mm=100, height_mm=100, timestamp=0.0, destination=1),
        Box(box_id=2, length_mm=100, width_mm=100, height_mm=100, timestamp=0.0, destination=1),
    ]
    return SchedulerSimState(
        now=0.0,
        ramps={1: boxes},
        pallets={1: pallet},
        pallet_blocked=set(),
    )


def test_early_stand_floor_priority_prefers_floor_stand_in_early_steps() -> None:
    pallet = FakePallet(
        {
            1: ("planar", 1.0, 0),
            2: ("stand_hw", 0.4, 0),
        }
    )
    sched = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            score_mode="gain_frag",
            early_stand_floor_priority_end_step=8,
            early_stand_floor_priority_bonus=0.8,
        )
    )

    plan = sched.choose_action(_sim_state_for_two_boxes(pallet=pallet))
    assert plan is not None
    assert int(plan.buffer_index) == 1
    assert plan.preview.placement is not None
    assert str(plan.preview.placement.orientation_family) == "stand_hw"
    assert int(plan.preview.placement.z_mm) == 0
    assert int(sched.early_stand_floor_priority_triggered_total) == 1
    assert int(sched.early_stand_floor_priority_floor_stand_candidates_total) == 1
    assert int(sched.early_stand_floor_priority_chosen_total) == 1
    assert int(sched.early_stand_floor_priority_bonus_applied_total) == 1


def test_early_stand_floor_priority_disabled_keeps_baseline_behavior() -> None:
    pallet = FakePallet(
        {
            1: ("planar", 1.0, 0),
            2: ("stand_hw", 0.4, 0),
        }
    )
    sched = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            score_mode="gain_frag",
            early_stand_floor_priority_end_step=0,
            early_stand_floor_priority_bonus=0.8,
        )
    )

    plan = sched.choose_action(_sim_state_for_two_boxes(pallet=pallet))
    assert plan is not None
    assert int(plan.buffer_index) == 0
    assert plan.preview.placement is not None
    assert str(plan.preview.placement.orientation_family) == "planar"
    assert int(sched.early_stand_floor_priority_triggered_total) == 0
    assert int(sched.early_stand_floor_priority_floor_stand_candidates_total) == 0
    assert int(sched.early_stand_floor_priority_chosen_total) == 0
    assert int(sched.early_stand_floor_priority_bonus_applied_total) == 0


def test_early_stand_floor_priority_no_stand_floor_is_noop() -> None:
    pallet = FakePallet(
        {
            1: ("planar", 1.0, 0),
            2: ("planar", 0.4, 0),
        }
    )
    sched = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            score_mode="gain_frag",
            early_stand_floor_priority_end_step=8,
            early_stand_floor_priority_bonus=0.8,
        )
    )

    plan = sched.choose_action(_sim_state_for_two_boxes(pallet=pallet))
    assert plan is not None
    assert int(plan.buffer_index) == 0
    assert int(sched.early_stand_floor_priority_triggered_total) == 0
    assert int(sched.early_stand_floor_priority_floor_stand_candidates_total) == 0
    assert int(sched.early_stand_floor_priority_chosen_total) == 0
    assert int(sched.early_stand_floor_priority_bonus_applied_total) == 0


def test_early_stand_floor_priority_applies_to_micro_root_depth0() -> None:
    pallet = FakePallet(
        {
            1: ("planar", 1.0, 0),
            2: ("stand_hw", 0.4, 0),
        }
    )
    sched = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            score_mode="gain_frag",
            micro_plan_enabled=True,
            micro_plan_depth=1,
            micro_plan_width=4,
            micro_plan_topk_per_step=4,
            early_stand_floor_priority_end_step=8,
            early_stand_floor_priority_bonus=0.8,
        )
    )

    plan = sched.choose_action(_sim_state_for_two_boxes(pallet=pallet))
    assert plan is not None
    assert int(plan.buffer_index) == 1
    assert plan.preview.placement is not None
    assert str(plan.preview.placement.orientation_family) == "stand_hw"
    assert int(plan.preview.placement.z_mm) == 0
    assert int(sched.early_stand_floor_priority_triggered_total) >= 1
    assert int(sched.early_stand_floor_priority_chosen_total) >= 1


def test_early_stand_floor_priority_kpis_are_exported() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        early_stand_floor_priority_end_step=8,
        early_stand_floor_priority_bonus=0.75,
    )
    policy._scheduler.early_stand_floor_priority_triggered_total = 3
    policy._scheduler.early_stand_floor_priority_floor_stand_candidates_total = 7
    policy._scheduler.early_stand_floor_priority_chosen_total = 2
    policy._scheduler.early_stand_floor_priority_bonus_applied_total = 5

    kpis = policy.collect_kpis()
    assert int(kpis["early_stand_floor_priority_end_step"]) == 8
    assert float(kpis["early_stand_floor_priority_bonus"]) == 0.75
    assert int(kpis["early_stand_floor_priority_triggered_total"]) == 3
    assert int(kpis["early_stand_floor_priority_floor_stand_candidates_total"]) == 7
    assert int(kpis["early_stand_floor_priority_chosen_total"]) == 2
    assert int(kpis["early_stand_floor_priority_bonus_applied_total"]) == 5
