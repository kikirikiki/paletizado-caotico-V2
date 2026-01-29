from __future__ import annotations

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


class FakePallet:
    def __init__(self, gains: dict[int, float]) -> None:
        self._gains = gains

    def preview_place(self, box: Box) -> PlacementPreview:
        gain = float(self._gains.get(int(box.box_id), 0.0))
        placement = Placement(
            x_mm=0,
            y_mm=0,
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=box.length_mm,
            width_mm=box.width_mm,
            height_mm=box.height_mm,
            box_id=box.box_id,
        )
        return PlacementPreview(
            feasible=True,
            placement=placement,
            packing_gain=gain,
            fragmentation=0.0,
            infeasible_reason=None,
            debug=None,
        )


def test_scheduler_lookahead_k_selects_non_head() -> None:
    boxes = [
        Box(box_id=1, length_mm=1, width_mm=1, height_mm=1, timestamp=0.0, destination=1),
        Box(box_id=2, length_mm=1, width_mm=1, height_mm=1, timestamp=0.0, destination=1),
    ]
    pallets = {1: FakePallet({1: 0.2, 2: 1.0})}
    sim_state = SchedulerSimState(now=0.0, ramps={1: boxes}, pallets=pallets, pallet_blocked=set())
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            t_select_base=0.5,
            t_select_step=0.0,
            time_penalty_weight=1.0,
            starvation_weight=0.0,
        )
    )
    plan = scheduler.choose_action(sim_state)
    assert plan is not None
    assert plan.buffer_index == 1
    assert plan.dt_extra == 0.5


def test_scheduler_k1_picks_head() -> None:
    boxes = [
        Box(box_id=1, length_mm=1, width_mm=1, height_mm=1, timestamp=0.0, destination=1),
        Box(box_id=2, length_mm=1, width_mm=1, height_mm=1, timestamp=0.0, destination=1),
    ]
    pallets = {1: FakePallet({1: 0.9, 2: 1.0})}
    sim_state = SchedulerSimState(now=0.0, ramps={1: boxes}, pallets=pallets, pallet_blocked=set())
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=1))
    plan = scheduler.choose_action(sim_state)
    assert plan is not None
    assert plan.buffer_index == 0
