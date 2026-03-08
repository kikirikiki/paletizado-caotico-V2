from __future__ import annotations

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


class TwoStepLayoutFakePallet:
    def __init__(self) -> None:
        self.placements: list[Placement] = []
        self._placed_ids: list[int] = []

    def _preview_spec(self, box_id: int) -> tuple[bool, int, float, int, int]:
        state = tuple(self._placed_ids)
        if box_id in self._placed_ids:
            return False, 0, 0.0, 0, 0
        if state == ():
            if box_id == 1:
                return True, 0, 10.0, 0, 0
            if box_id == 2:
                return True, 0, 9.0, 300, 0
            return False, 0, 0.0, 0, 0
        if state == (1,):
            if box_id == 2:
                return True, 100, 8.0, 0, 0
            return False, 0, 0.0, 0, 0
        if state == (2,):
            if box_id == 1:
                return True, 100, 8.0, 300, 0
            if box_id == 3:
                return True, 0, 6.0, 600, 0
            if box_id == 4:
                return True, 0, 5.5, 900, 0
            return False, 0, 0.0, 0, 0
        if state == (2, 3):
            if box_id == 4:
                return True, 0, 4.0, 900, 0
            return False, 0, 0.0, 0, 0
        if state == (2, 4):
            if box_id == 3:
                return True, 0, 4.0, 600, 0
            return False, 0, 0.0, 0, 0
        return False, 0, 0.0, 0, 0

    def preview_place(self, box: Box) -> PlacementPreview:
        feasible, z_mm, gain, x_mm, y_mm = self._preview_spec(int(box.box_id))
        if not feasible:
            return PlacementPreview(
                feasible=False,
                placement=None,
                packing_gain=0.0,
                fragmentation=0.0,
                infeasible_reason="NO_SPACE",
            )
        placement = Placement(
            x_mm=int(x_mm),
            y_mm=int(y_mm),
            z_mm=int(z_mm),
            rot90=False,
            layer_id=0 if int(z_mm) == 0 else 1,
            length_mm=int(box.length_mm),
            width_mm=int(box.width_mm),
            height_mm=int(box.height_mm),
            box_id=int(box.box_id),
        )
        return PlacementPreview(
            feasible=True,
            placement=placement,
            packing_gain=float(gain),
            fragmentation=0.0,
            height_after_mm=int(z_mm) + int(box.height_mm),
        )

    def commit_place(self, preview: PlacementPreview) -> Placement:
        if not preview.feasible or preview.placement is None:
            raise ValueError("infeasible preview")
        placement = preview.placement
        self._placed_ids.append(int(placement.box_id))
        self.placements.append(placement)
        return placement

    def current_height_mm(self) -> int:
        if not self.placements:
            return 0
        return max(int(p.z_mm) + int(p.height_mm) for p in self.placements)


def _build_state() -> SchedulerSimState:
    boxes = [
        Box(box_id=1, length_mm=100, width_mm=100, height_mm=100, timestamp=0.0, destination=1),
        Box(box_id=2, length_mm=100, width_mm=100, height_mm=100, timestamp=0.0, destination=1),
        Box(box_id=3, length_mm=100, width_mm=100, height_mm=100, timestamp=0.0, destination=1),
        Box(box_id=4, length_mm=100, width_mm=100, height_mm=100, timestamp=0.0, destination=1),
    ]
    return SchedulerSimState(
        now=0.0,
        ramps={1: boxes},
        pallets={1: TwoStepLayoutFakePallet()},
        pallet_blocked=set(),
        remaining_total=len(boxes),
    )


def test_two_step_floor_layout_enabled_prefers_better_two_step_floor_root() -> None:
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=4,
            score_mode="gain_frag",
            two_step_floor_layout_end_step=8,
            two_step_floor_layout_lookahead_items=4,
        )
    )
    plan = scheduler.choose_action(_build_state())
    assert plan is not None
    assert int(plan.box_id) == 2


def test_two_step_floor_layout_disabled_keeps_baseline_choice() -> None:
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=4,
            score_mode="gain_frag",
            two_step_floor_layout_end_step=0,
            two_step_floor_layout_lookahead_items=4,
        )
    )
    plan = scheduler.choose_action(_build_state())
    assert plan is not None
    assert int(plan.box_id) == 1


def test_two_step_floor_layout_applies_to_micro_root_depth0() -> None:
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=4,
            score_mode="gain_frag",
            micro_plan_enabled=True,
            micro_plan_depth=1,
            micro_plan_width=16,
            micro_plan_topk_per_step=16,
            two_step_floor_layout_end_step=8,
            two_step_floor_layout_lookahead_items=4,
        )
    )
    plan = scheduler.choose_action(_build_state())
    assert plan is not None
    assert int(plan.box_id) == 2


def test_two_step_floor_layout_kpis_are_exposed() -> None:
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=4,
            score_mode="gain_frag",
            two_step_floor_layout_end_step=8,
            two_step_floor_layout_lookahead_items=4,
        )
    )
    _plan = scheduler.choose_action(_build_state())
    assert scheduler.two_step_floor_layout_eval_total == 1
    assert scheduler.two_step_floor_layout_candidates_scored_total == 2
    assert scheduler.two_step_floor_layout_tiebreak_used_total == 1
    assert scheduler.two_step_floor_layout_future_floor_count_step1_sum > 0.0
    assert scheduler.two_step_floor_layout_future_floor_count_step2_sum > 0.0
    assert scheduler.two_step_floor_layout_best_score_max > 0.0

    policy = PolicyPackerScheduler.from_defaults()
    policy._scheduler = scheduler  # noqa: SLF001
    kpis = policy.collect_kpis()
    assert int(kpis.get("two_step_floor_layout_eval_total", -1)) == 1
    assert int(kpis.get("two_step_floor_layout_candidates_scored_total", -1)) == 2
    assert int(kpis.get("two_step_floor_layout_tiebreak_used_total", -1)) == 1
    assert float(kpis.get("two_step_floor_layout_future_floor_count_step1_mean", 0.0)) > 0.0
    assert float(kpis.get("two_step_floor_layout_future_floor_count_step2_mean", 0.0)) > 0.0
    assert float(kpis.get("two_step_floor_layout_best_score_max", 0.0)) > 0.0
