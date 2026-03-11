from __future__ import annotations

from dataclasses import dataclass

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


@dataclass(frozen=True)
class PreviewSpec:
    z_mm: int
    height_mm: int
    packing_gain: float = 0.0
    orientation_family: str | None = None
    orientation_name: str | None = None


class FakePallet:
    def __init__(
        self,
        previews_by_box: dict[int, PreviewSpec],
        *,
        existing_placements: list[Placement] | None = None,
        bin_area_mm2: int = 12_000,
    ) -> None:
        self._previews_by_box = dict(previews_by_box)
        self.placements: list[Placement] = list(existing_placements or [])
        self.bin_area_mm2 = int(bin_area_mm2)

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
            layer_id=0,
            length_mm=100,
            width_mm=100,
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


def _placement(*, box_id: int, z_mm: int, height_mm: int = 100) -> Placement:
    return Placement(
        x_mm=0,
        y_mm=0,
        z_mm=int(z_mm),
        rot90=False,
        layer_id=0,
        length_mm=100,
        width_mm=100,
        height_mm=int(height_mm),
        box_id=int(box_id),
        orientation_family="planar",
        orientation_name="planar",
    )


def _sim_state(*, boxes: list[Box], pallet: FakePallet) -> SchedulerSimState:
    return SchedulerSimState(
        now=0.0,
        ramps={1: list(boxes)},
        pallets={1: pallet},
        pallet_blocked=set(),
        remaining_total=len(boxes),
    )


def test_max_layer_backstep_1_allows_l_minus_1_when_highest_is_2() -> None:
    pallet = FakePallet(
        {1: PreviewSpec(z_mm=100, height_mm=500)},
        existing_placements=[
            _placement(box_id=10, z_mm=0),
            _placement(box_id=11, z_mm=100),
            _placement(box_id=12, z_mm=200),
        ],
    )
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=1, max_layer_backstep=1))

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1)], pallet=pallet))
    assert plan is not None
    assert int(plan.preview.placement.z_mm) == 100
    assert int(scheduler.placements_rejected_bounded_backstep) == 0


def test_max_layer_backstep_1_rejects_l_minus_2_when_highest_is_2() -> None:
    pallet = FakePallet(
        {1: PreviewSpec(z_mm=0, height_mm=500)},
        existing_placements=[
            _placement(box_id=10, z_mm=0),
            _placement(box_id=11, z_mm=100),
            _placement(box_id=12, z_mm=200),
        ],
    )
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=1, max_layer_backstep=1))

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1)], pallet=pallet))
    assert plan is None
    assert int(scheduler.placements_rejected_bounded_backstep) == 1
    assert bool(scheduler.last_deadlock) is True
    assert str((scheduler.last_deadlock_item or {}).get("reason")) == "BOUNDED_BACKSTEP"


def test_max_layer_backstep_1_allows_l_minus_1_when_highest_is_3() -> None:
    pallet = FakePallet(
        {1: PreviewSpec(z_mm=200, height_mm=120)},
        existing_placements=[
            _placement(box_id=10, z_mm=0),
            _placement(box_id=11, z_mm=100),
            _placement(box_id=12, z_mm=200),
            _placement(box_id=13, z_mm=300),
        ],
    )
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=1, max_layer_backstep=1))

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1)], pallet=pallet))
    assert plan is not None
    assert int(plan.preview.placement.z_mm) == 200


def test_max_layer_backstep_1_rejects_l_minus_2_when_highest_is_3() -> None:
    pallet = FakePallet(
        {1: PreviewSpec(z_mm=100, height_mm=120)},
        existing_placements=[
            _placement(box_id=10, z_mm=0),
            _placement(box_id=11, z_mm=100),
            _placement(box_id=12, z_mm=200),
            _placement(box_id=13, z_mm=300),
        ],
    )
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=1, max_layer_backstep=1))

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1)], pallet=pallet))
    assert plan is None
    assert int(scheduler.placements_rejected_bounded_backstep) == 1


def test_max_layer_backstep_classifies_by_base_z_not_stand_hw_height() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(
                z_mm=100,
                height_mm=900,
                packing_gain=1.0,
                orientation_family="stand_hw",
                orientation_name="stand_hw_tall",
            )
        },
        existing_placements=[
            _placement(box_id=10, z_mm=0),
            _placement(box_id=11, z_mm=100),
            _placement(box_id=12, z_mm=200),
        ],
    )
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=1, max_layer_backstep=1))

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1)], pallet=pallet))
    assert plan is not None
    assert str(plan.preview.placement.orientation_family) == "stand_hw"
    assert int(plan.preview.placement.z_mm) == 100
    assert int(scheduler.placements_rejected_bounded_backstep) == 0


def test_without_flag_behavior_is_unchanged_vs_bounded_backstep_enabled() -> None:
    initial = [
        _placement(box_id=10, z_mm=0),
        _placement(box_id=11, z_mm=100),
        _placement(box_id=12, z_mm=200),
    ]
    previews = {
        1: PreviewSpec(z_mm=0, height_mm=120, packing_gain=10.0),
        2: PreviewSpec(z_mm=100, height_mm=120, packing_gain=1.0),
    }

    scheduler_no_flag = SchedulerV1(SchedulerConfig(lookahead_k=2, max_layer_backstep=None))
    scheduler_with_flag = SchedulerV1(SchedulerConfig(lookahead_k=2, max_layer_backstep=1))

    pallet_a = FakePallet(previews, existing_placements=initial)
    pallet_b = FakePallet(previews, existing_placements=initial)
    boxes = [_box(1), _box(2)]

    plan_no_flag = scheduler_no_flag.choose_action(_sim_state(boxes=boxes, pallet=pallet_a))
    plan_with_flag = scheduler_with_flag.choose_action(_sim_state(boxes=boxes, pallet=pallet_b))

    assert plan_no_flag is not None
    assert plan_with_flag is not None
    assert int(plan_no_flag.box_id) == 1
    assert int(plan_with_flag.box_id) == 2
    assert int(scheduler_no_flag.placements_rejected_bounded_backstep) == 0
    assert int(scheduler_with_flag.placements_rejected_bounded_backstep) == 1
