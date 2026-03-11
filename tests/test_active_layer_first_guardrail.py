from __future__ import annotations

from dataclasses import dataclass

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


@dataclass(frozen=True)
class PreviewSpec:
    z_mm: int
    height_mm: int
    packing_gain: float
    orientation_family: str | None = None
    orientation_name: str | None = None


class FakePallet:
    def __init__(self, previews_by_box: dict[int, PreviewSpec], *, placements: list[Placement] | None = None) -> None:
        self._previews_by_box = dict(previews_by_box)
        self.placements: list[Placement] = list(placements or [])

    def preview_place(self, box: Box) -> PlacementPreview:
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
            x_mm=0,
            y_mm=0,
            z_mm=int(spec.z_mm),
            rot90=False,
            layer_id=0 if int(spec.z_mm) <= 0 else 1,
            length_mm=box.length_mm,
            width_mm=box.width_mm,
            height_mm=int(spec.height_mm),
            box_id=box.box_id,
            orientation_family=spec.orientation_family,
            orientation_name=spec.orientation_name,
        )
        return PlacementPreview(
            feasible=True,
            placement=placement,
            packing_gain=float(spec.packing_gain),
            fragmentation=0.0,
            height_after_mm=int(spec.z_mm) + int(spec.height_mm),
            infeasible_reason=None,
        )

    def current_height_mm(self) -> int:
        if not self.placements:
            return 0
        return max(int(p.z_mm) + int(p.height_mm) for p in self.placements)


def _box(box_id: int) -> Box:
    return Box(
        box_id=int(box_id),
        length_mm=100,
        width_mm=100,
        height_mm=100,
        timestamp=0.0,
        destination=1,
    )


def _seed_placement(z_mm: int, height_mm: int = 100) -> Placement:
    return Placement(
        x_mm=0,
        y_mm=0,
        z_mm=int(z_mm),
        rot90=False,
        layer_id=0 if int(z_mm) <= 0 else 1,
        length_mm=100,
        width_mm=100,
        height_mm=int(height_mm),
        box_id=999,
    )


def _sim_state(*, boxes: list[Box], pallet: FakePallet) -> SchedulerSimState:
    return SchedulerSimState(
        now=0.0,
        ramps={1: list(boxes)},
        pallets={1: pallet},
        pallet_blocked=set(),
        remaining_total=len(boxes),
    )


def test_active_layer_first_blocks_upper_open_when_active_layer_has_feasible_candidates() -> None:
    pallet = FakePallet(
        previews_by_box={
            1: PreviewSpec(z_mm=100, height_mm=40, packing_gain=0.1),
            2: PreviewSpec(z_mm=200, height_mm=40, packing_gain=3.0),
        },
        placements=[_seed_placement(z_mm=100)],
    )
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=2, enforce_active_layer_first=True))

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))

    assert plan is not None
    assert int(plan.box_id) == 1
    assert int(scheduler.blocked_upper_layer_open_attempts) == 1
    assert int(scheduler.active_layer_exhaustion_events) == 0


def test_active_layer_first_allows_upper_open_when_active_layer_is_exhausted() -> None:
    pallet = FakePallet(
        previews_by_box={
            2: PreviewSpec(z_mm=200, height_mm=40, packing_gain=3.0),
        },
        placements=[_seed_placement(z_mm=100)],
    )
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=1, enforce_active_layer_first=True))

    plan = scheduler.choose_action(_sim_state(boxes=[_box(2)], pallet=pallet))

    assert plan is not None
    assert int(plan.preview.placement.z_mm) == 200
    assert int(scheduler.blocked_upper_layer_open_attempts) == 0
    assert int(scheduler.active_layer_exhaustion_events) == 1


def test_active_layer_first_uses_base_z_even_for_stand_hw_candidates() -> None:
    pallet = FakePallet(
        previews_by_box={
            10: PreviewSpec(
                z_mm=100,
                height_mm=320,
                packing_gain=0.2,
                orientation_family="stand_hw",
                orientation_name="HWL",
            ),
            20: PreviewSpec(
                z_mm=150,
                height_mm=40,
                packing_gain=4.0,
                orientation_family="planar",
                orientation_name="LWH",
            ),
        },
        placements=[_seed_placement(z_mm=100)],
    )
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=2, enforce_active_layer_first=True))

    plan = scheduler.choose_action(_sim_state(boxes=[_box(10), _box(20)], pallet=pallet))

    assert plan is not None
    assert int(plan.box_id) == 10
    assert str(plan.preview.placement.orientation_family) == "stand_hw"
    assert int(scheduler.blocked_upper_layer_open_attempts) == 1
    assert scheduler.active_layer_first_trace
    assert int(scheduler.active_layer_first_trace[-1]["active_base_z_mm"]) == 100


def test_active_layer_first_disabled_keeps_existing_selection_behavior() -> None:
    pallet = FakePallet(
        previews_by_box={
            1: PreviewSpec(z_mm=100, height_mm=40, packing_gain=0.1),
            2: PreviewSpec(z_mm=200, height_mm=40, packing_gain=3.0),
        },
        placements=[_seed_placement(z_mm=100)],
    )
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=2, enforce_active_layer_first=False))

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))

    assert plan is not None
    assert int(plan.box_id) == 2
    assert int(scheduler.blocked_upper_layer_open_attempts) == 0
