from __future__ import annotations

from dataclasses import dataclass

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


@dataclass(frozen=True)
class _PreviewSpec:
    z_mm: int
    gain: float
    orientation_family: str


class _FakePallet:
    def __init__(self, specs: dict[int, _PreviewSpec]) -> None:
        self._specs = specs
        self.placements: list[Placement] = []
        self.layers: list[object] = []
        self.bin_area_mm2 = 1200 * 800

    def preview_place(self, box: Box) -> PlacementPreview:
        spec = self._specs[int(box.box_id)]
        placement = Placement(
            x_mm=0,
            y_mm=0,
            z_mm=int(spec.z_mm),
            rot90=False,
            layer_id=0,
            length_mm=int(box.length_mm),
            width_mm=int(box.width_mm),
            height_mm=int(box.height_mm),
            box_id=box.box_id,
            orientation_family=str(spec.orientation_family),
            orientation_name=str(spec.orientation_family),
        )
        return PlacementPreview(
            feasible=True,
            placement=placement,
            packing_gain=float(spec.gain),
            fragmentation=0.0,
            height_after_mm=int(spec.z_mm + box.height_mm),
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
        box_id=box_id,
        length_mm=200,
        width_mm=150,
        height_mm=100,
        timestamp=0.0,
        destination=1,
    )


def _state(specs: dict[int, _PreviewSpec]) -> SchedulerSimState:
    boxes = [_box(1), _box(2)]
    pallet = _FakePallet(specs)
    return SchedulerSimState(
        now=0.0,
        ramps={1: boxes},
        pallets={1: pallet},
        pallet_blocked=set(),
    )


def test_floor_first_rescue_prefers_floor_on_greedy_root() -> None:
    specs = {
        1: _PreviewSpec(z_mm=100, gain=10.0, orientation_family="planar"),
        2: _PreviewSpec(z_mm=0, gain=1.0, orientation_family="stand_hw"),
    }
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=2, floor_first_end_step=8))
    plan = scheduler.choose_action(_state(specs))
    assert plan is not None
    assert int(plan.box_id) == 2
    assert scheduler.floor_first_filter_applied_total == 1
    assert scheduler.floor_first_floor_candidates_seen_total == 1
    assert scheduler.floor_first_stacked_candidates_suppressed_total == 1


def test_floor_first_rescue_disabled_keeps_previous_selection() -> None:
    specs = {
        1: _PreviewSpec(z_mm=100, gain=10.0, orientation_family="planar"),
        2: _PreviewSpec(z_mm=0, gain=1.0, orientation_family="stand_hw"),
    }
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=2, floor_first_end_step=0))
    plan = scheduler.choose_action(_state(specs))
    assert plan is not None
    assert int(plan.box_id) == 1
    assert scheduler.floor_first_filter_applied_total == 0
    assert scheduler.floor_first_floor_candidates_seen_total == 0
    assert scheduler.floor_first_stacked_candidates_suppressed_total == 0


def test_floor_first_rescue_allows_stacked_when_no_floor_candidate() -> None:
    specs = {
        1: _PreviewSpec(z_mm=100, gain=10.0, orientation_family="planar"),
        2: _PreviewSpec(z_mm=120, gain=1.0, orientation_family="stand_hw"),
    }
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=2, floor_first_end_step=8))
    plan = scheduler.choose_action(_state(specs))
    assert plan is not None
    assert int(plan.box_id) == 1
    assert scheduler.floor_first_filter_applied_total == 0
    assert scheduler.floor_first_floor_candidates_seen_total == 0
    assert scheduler.floor_first_stacked_candidates_suppressed_total == 0


def test_floor_first_rescue_applies_to_micro_root_selection() -> None:
    specs = {
        1: _PreviewSpec(z_mm=100, gain=1.0, orientation_family="planar"),
        2: _PreviewSpec(z_mm=0, gain=1.0, orientation_family="stand_hw"),
    }
    baseline = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=True,
            micro_plan_depth=2,
            micro_plan_width=4,
            micro_plan_topk_per_step=4,
            floor_first_end_step=0,
        )
    )
    baseline_plan = baseline.choose_action(_state(specs))
    assert baseline_plan is not None
    assert int(baseline_plan.box_id) == 1

    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=True,
            micro_plan_depth=2,
            micro_plan_width=4,
            micro_plan_topk_per_step=4,
            floor_first_end_step=8,
        )
    )
    plan = scheduler.choose_action(_state(specs))
    assert plan is not None
    assert int(plan.box_id) == 2
    assert scheduler.floor_first_filter_applied_total == 1
    assert scheduler.floor_first_floor_candidates_seen_total == 1
    assert scheduler.floor_first_stacked_candidates_suppressed_total == 1
