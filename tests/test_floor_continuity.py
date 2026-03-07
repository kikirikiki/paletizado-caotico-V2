from __future__ import annotations

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


class ContinuityFakePallet:
    def __init__(self) -> None:
        self.placements: list[Placement] = []
        self.z_band_mm: int | None = None
        self._state = "root"

    def preview_place(self, box: Box) -> PlacementPreview:
        box_id = int(box.box_id)
        if self._state == "root":
            if box_id not in (1, 2):
                return PlacementPreview(
                    feasible=False,
                    placement=None,
                    packing_gain=0.0,
                    fragmentation=0.0,
                    infeasible_reason="NO_SPACE",
                )
            gain = 10.0 if box_id == 1 else 9.0
            z_mm = 0
        else:
            floor_available = self._state == "after2" and box_id in (3, 4)
            z_mm = 0 if (self.z_band_mm == 0 and floor_available) else 100
            gain = 0.0

        placement = Placement(
            x_mm=10 * box_id,
            y_mm=0,
            z_mm=int(z_mm),
            rot90=False,
            layer_id=0,
            length_mm=int(box.length_mm),
            width_mm=int(box.width_mm),
            height_mm=int(box.height_mm),
            box_id=box.box_id,
        )
        return PlacementPreview(
            feasible=True,
            placement=placement,
            packing_gain=float(gain),
            fragmentation=0.0,
            height_after_mm=int(z_mm + int(box.height_mm)),
            infeasible_reason=None,
        )

    def commit_place(self, preview: PlacementPreview) -> Placement:
        if not preview.feasible or preview.placement is None:
            raise ValueError("infeasible preview")
        placement = preview.placement
        self.placements.append(placement)
        box_id = int(placement.box_id or 0)
        if box_id == 1:
            self._state = "after1"
        elif box_id == 2:
            self._state = "after2"
        return placement

    def current_height_mm(self) -> int:
        if not self.placements:
            return 0
        return max(int(p.z_mm) + int(p.height_mm) for p in self.placements)


def _build_boxes() -> list[Box]:
    return [
        Box(box_id=1, length_mm=1, width_mm=1, height_mm=1, timestamp=0.0, destination=1),
        Box(box_id=2, length_mm=1, width_mm=1, height_mm=1, timestamp=0.0, destination=1),
        Box(box_id=3, length_mm=1, width_mm=1, height_mm=1, timestamp=0.0, destination=1),
        Box(box_id=4, length_mm=1, width_mm=1, height_mm=1, timestamp=0.0, destination=1),
    ]


def _sim_state_with(pallet: ContinuityFakePallet) -> SchedulerSimState:
    boxes = _build_boxes()
    return SchedulerSimState(
        now=0.0,
        ramps={1: boxes},
        pallets={1: pallet},
        pallet_blocked=set(),
        remaining_total=len(boxes),
    )


def test_floor_continuity_disabled_keeps_current_choice() -> None:
    sched = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            floor_continuity_end_step=0,
            floor_continuity_lookahead_items=4,
        )
    )
    plan = sched.choose_action(_sim_state_with(ContinuityFakePallet()))
    assert plan is not None
    assert int(plan.box_id) == 1
    assert sched.floor_continuity_eval_total == 0
    assert sched.floor_continuity_tiebreak_used_total == 0


def test_floor_continuity_enabled_prefers_future_floor_preservation_and_exports_kpis() -> None:
    sched = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            floor_continuity_end_step=8,
            floor_continuity_lookahead_items=4,
        )
    )
    plan = sched.choose_action(_sim_state_with(ContinuityFakePallet()))
    assert plan is not None
    assert int(plan.box_id) == 2
    assert sched.floor_continuity_eval_total == 1
    assert sched.floor_continuity_candidates_scored_total >= 2
    assert sched.floor_continuity_tiebreak_used_total == 1
    assert sched.floor_continuity_best_future_floor_count_max >= 2

    policy = PolicyPackerScheduler.from_defaults(
        floor_continuity_end_step=8,
        floor_continuity_lookahead_items=4,
    )
    policy._scheduler = sched
    kpis = policy.collect_kpis()
    assert int(kpis["floor_continuity_eval_total"]) == 1
    assert int(kpis["floor_continuity_candidates_scored_total"]) >= 2
    assert int(kpis["floor_continuity_tiebreak_used_total"]) == 1
    assert float(kpis["floor_continuity_best_future_floor_count_mean"]) >= 2.0
    assert int(kpis["floor_continuity_best_future_floor_count_max"]) >= 2


def test_floor_continuity_applies_to_micro_root_depth0() -> None:
    baseline_sched = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=True,
            micro_plan_depth=1,
            micro_plan_width=8,
            micro_plan_topk_per_step=8,
            floor_continuity_end_step=0,
            floor_continuity_lookahead_items=4,
        )
    )
    baseline_plan = baseline_sched.choose_action(_sim_state_with(ContinuityFakePallet()))
    assert baseline_plan is not None
    assert int(baseline_plan.box_id) == 1

    enabled_sched = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=True,
            micro_plan_depth=1,
            micro_plan_width=8,
            micro_plan_topk_per_step=8,
            floor_continuity_end_step=8,
            floor_continuity_lookahead_items=4,
        )
    )
    enabled_plan = enabled_sched.choose_action(_sim_state_with(ContinuityFakePallet()))
    assert enabled_plan is not None
    assert int(enabled_plan.box_id) == 2
    assert enabled_sched.floor_continuity_eval_total == 1
    assert enabled_sched.floor_continuity_tiebreak_used_total == 1
