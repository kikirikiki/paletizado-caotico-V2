from __future__ import annotations

from dataclasses import dataclass

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


@dataclass
class FakePallet:
    previews_by_box_id: dict[int, PlacementPreview]

    def __post_init__(self) -> None:
        self.placements: list[Placement] = []

    def preview_place(self, box: Box) -> PlacementPreview:
        return self.previews_by_box_id[int(box.box_id)]

    def commit_place(self, preview: PlacementPreview) -> Placement:
        if not preview.feasible or preview.placement is None:
            raise ValueError("infeasible preview")
        self.placements.append(preview.placement)
        return preview.placement

    def current_height_mm(self) -> int:
        if not self.placements:
            return 0
        return max(int(p.z_mm) + int(p.height_mm) for p in self.placements)


def _preview(*, box_id: int, z_mm: int, gain: float, height_mm: int = 100) -> PlacementPreview:
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
            height_mm=int(height_mm),
            box_id=int(box_id),
        ),
        packing_gain=float(gain),
        fragmentation=0.0,
        height_after_mm=int(z_mm) + int(height_mm),
    )


def _sim_state_two_boxes(*, pallet: FakePallet) -> SchedulerSimState:
    boxes = [
        Box(box_id=1, length_mm=100, width_mm=100, height_mm=100, timestamp=0.0, destination=1),
        Box(box_id=2, length_mm=100, width_mm=100, height_mm=100, timestamp=0.0, destination=1),
    ]
    return SchedulerSimState(
        now=0.0,
        ramps={1: boxes},
        pallets={1: pallet},
        pallet_blocked=set(),
        remaining_total=len(boxes),
    )


def test_early_window_floor_probe_enabled_picks_floor_alternative() -> None:
    pallet = FakePallet(
        previews_by_box_id={
            1: _preview(box_id=1, z_mm=120, gain=5.0),
            2: _preview(box_id=2, z_mm=0, gain=1.0),
        }
    )
    sim_state = _sim_state_two_boxes(pallet=pallet)
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            early_window_floor_probe_end_step=8,
        )
    )

    plan = scheduler.choose_action(sim_state)
    assert plan is not None
    assert int(plan.box_id) == 2
    assert int(getattr(plan.preview.placement, "z_mm", -1)) == 0
    assert scheduler.early_window_floor_probe_triggered_total == 1
    assert scheduler.early_window_floor_probe_items_scanned_total == 1
    assert scheduler.early_window_floor_probe_floor_alternative_found_total == 1
    assert scheduler.early_window_floor_probe_chosen_total == 1
    assert scheduler.early_window_floor_probe_no_floor_alternative_total == 0


def test_early_window_floor_probe_disabled_keeps_default_behavior() -> None:
    pallet = FakePallet(
        previews_by_box_id={
            1: _preview(box_id=1, z_mm=120, gain=5.0),
            2: _preview(box_id=2, z_mm=0, gain=1.0),
        }
    )
    sim_state = _sim_state_two_boxes(pallet=pallet)
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            early_window_floor_probe_end_step=0,
        )
    )

    plan = scheduler.choose_action(sim_state)
    assert plan is not None
    assert int(plan.box_id) == 1
    assert scheduler.early_window_floor_probe_triggered_total == 0
    assert scheduler.early_window_floor_probe_items_scanned_total == 0
    assert scheduler.early_window_floor_probe_floor_alternative_found_total == 0
    assert scheduler.early_window_floor_probe_chosen_total == 0
    assert scheduler.early_window_floor_probe_no_floor_alternative_total == 0


def test_early_window_floor_probe_no_floor_alternative_keeps_default() -> None:
    pallet = FakePallet(
        previews_by_box_id={
            1: _preview(box_id=1, z_mm=120, gain=5.0),
            2: _preview(box_id=2, z_mm=80, gain=1.0),
        }
    )
    sim_state = _sim_state_two_boxes(pallet=pallet)
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            early_window_floor_probe_end_step=8,
        )
    )

    plan = scheduler.choose_action(sim_state)
    assert plan is not None
    assert int(plan.box_id) == 1
    assert scheduler.early_window_floor_probe_triggered_total == 1
    assert scheduler.early_window_floor_probe_items_scanned_total == 1
    assert scheduler.early_window_floor_probe_floor_alternative_found_total == 0
    assert scheduler.early_window_floor_probe_chosen_total == 0
    assert scheduler.early_window_floor_probe_no_floor_alternative_total == 1


def test_early_window_floor_probe_applies_to_micro_root_depth0() -> None:
    pallet = FakePallet(
        previews_by_box_id={
            1: _preview(box_id=1, z_mm=120, gain=5.0),
            2: _preview(box_id=2, z_mm=0, gain=1.0),
        }
    )
    sim_state = _sim_state_two_boxes(pallet=pallet)
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=True,
            micro_plan_depth=2,
            micro_plan_width=8,
            micro_plan_topk_per_step=8,
            micro_plan_window_total=2,
            early_window_floor_probe_end_step=8,
        )
    )

    plan = scheduler.choose_action(sim_state)
    assert plan is not None
    assert int(plan.box_id) == 2
    assert int(getattr(plan.preview.placement, "z_mm", -1)) == 0
    assert scheduler.early_window_floor_probe_triggered_total == 1
    assert scheduler.early_window_floor_probe_floor_alternative_found_total == 1
    assert scheduler.early_window_floor_probe_chosen_total == 1


def test_early_window_floor_probe_kpis_are_exposed() -> None:
    policy = PolicyPackerScheduler.from_defaults()
    policy._scheduler.early_window_floor_probe_triggered_total = 3
    policy._scheduler.early_window_floor_probe_items_scanned_total = 11
    policy._scheduler.early_window_floor_probe_floor_alternative_found_total = 2
    policy._scheduler.early_window_floor_probe_chosen_total = 2
    policy._scheduler.early_window_floor_probe_no_floor_alternative_total = 1

    kpis = policy.collect_kpis()
    assert int(kpis.get("early_window_floor_probe_triggered_total", 0)) == 3
    assert int(kpis.get("early_window_floor_probe_items_scanned_total", 0)) == 11
    assert int(kpis.get("early_window_floor_probe_floor_alternative_found_total", 0)) == 2
    assert int(kpis.get("early_window_floor_probe_chosen_total", 0)) == 2
    assert int(kpis.get("early_window_floor_probe_no_floor_alternative_total", 0)) == 1

