from __future__ import annotations

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


class FakePallet:
    def __init__(self, previews_by_box_id: dict[int, PlacementPreview]) -> None:
        self._previews = dict(previews_by_box_id)
        self.placements: list[Placement] = []

    def preview_place(self, box: Box) -> PlacementPreview:
        return self._previews[int(box.box_id)]

    def commit_place(self, preview: PlacementPreview) -> Placement:
        if preview.placement is None:
            raise ValueError("missing placement")
        self.placements.append(preview.placement)
        return preview.placement

    def current_height_mm(self) -> int:
        max_height = 0
        for placement in self.placements:
            max_height = max(max_height, int(placement.z_mm) + int(placement.height_mm))
        return int(max_height)


def _preview(*, box_id: int, z_mm: int, gain: float, orientation_family: str) -> PlacementPreview:
    return PlacementPreview(
        feasible=True,
        placement=Placement(
            x_mm=0,
            y_mm=0,
            z_mm=int(z_mm),
            rot90=False,
            layer_id=0,
            length_mm=100,
            width_mm=100,
            height_mm=100,
            box_id=int(box_id),
            orientation_family=str(orientation_family),
        ),
        packing_gain=float(gain),
        fragmentation=0.0,
        height_after_mm=int(z_mm) + 100,
    )


def _state_with_two_boxes(
    *,
    stacked_gain: float,
    floor_gain: float,
    floor_z_mm: int,
) -> SchedulerSimState:
    boxes = [
        Box(box_id=1, length_mm=100, width_mm=100, height_mm=100, timestamp=0.0, destination=1),
        Box(box_id=2, length_mm=100, width_mm=100, height_mm=100, timestamp=0.0, destination=1),
    ]
    pallet = FakePallet(
        {
            1: _preview(box_id=1, z_mm=200, gain=stacked_gain, orientation_family="planar"),
            2: _preview(box_id=2, z_mm=floor_z_mm, gain=floor_gain, orientation_family="stand_hw"),
        }
    )
    return SchedulerSimState(now=0.0, ramps={1: boxes}, pallets={1: pallet}, pallet_blocked=set())


def test_early_floor_reorder_rescue_greedy_prefers_floor_alternative() -> None:
    sim_state = _state_with_two_boxes(stacked_gain=2.0, floor_gain=1.0, floor_z_mm=0)
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=2, early_floor_reorder_end_step=8))

    plan = scheduler.choose_action(sim_state)

    assert plan is not None
    assert int(plan.box_id) == 2
    assert int(getattr(plan.preview.placement, "z_mm", -1)) == 0
    assert scheduler.early_floor_reorder_triggered_total == 1
    assert scheduler.early_floor_reorder_alternatives_seen_total == 1
    assert scheduler.early_floor_reorder_chosen_total == 1
    assert scheduler.early_floor_reorder_no_floor_alternative_total == 0


def test_early_floor_reorder_disabled_keeps_baseline_choice() -> None:
    sim_state = _state_with_two_boxes(stacked_gain=2.0, floor_gain=1.0, floor_z_mm=0)
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=2, early_floor_reorder_end_step=0))

    plan = scheduler.choose_action(sim_state)

    assert plan is not None
    assert int(plan.box_id) == 1
    assert int(getattr(plan.preview.placement, "z_mm", -1)) > 0
    assert scheduler.early_floor_reorder_triggered_total == 0
    assert scheduler.early_floor_reorder_chosen_total == 0
    assert scheduler.early_floor_reorder_no_floor_alternative_total == 0


def test_early_floor_reorder_no_floor_alternative_keeps_stacked_choice() -> None:
    sim_state = _state_with_two_boxes(stacked_gain=2.0, floor_gain=1.5, floor_z_mm=120)
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=2, early_floor_reorder_end_step=8))

    plan = scheduler.choose_action(sim_state)

    assert plan is not None
    assert int(plan.box_id) == 1
    assert int(getattr(plan.preview.placement, "z_mm", -1)) > 0
    assert scheduler.early_floor_reorder_triggered_total == 1
    assert scheduler.early_floor_reorder_alternatives_seen_total == 0
    assert scheduler.early_floor_reorder_chosen_total == 0
    assert scheduler.early_floor_reorder_no_floor_alternative_total == 1


def test_early_floor_reorder_rescue_applies_in_micro_root_depth0() -> None:
    sim_state = _state_with_two_boxes(stacked_gain=2.0, floor_gain=1.0, floor_z_mm=0)
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            early_floor_reorder_end_step=8,
            micro_plan_enabled=True,
            micro_plan_depth=2,
            micro_plan_width=6,
            micro_plan_topk_per_step=6,
        )
    )

    plan = scheduler.choose_action(sim_state)

    assert plan is not None
    assert int(plan.box_id) == 2
    assert int(getattr(plan.preview.placement, "z_mm", -1)) == 0
    assert scheduler.early_floor_reorder_triggered_total == 1
    assert scheduler.early_floor_reorder_alternatives_seen_total == 1
    assert scheduler.early_floor_reorder_chosen_total == 1
    assert scheduler.early_floor_reorder_no_floor_alternative_total == 0
