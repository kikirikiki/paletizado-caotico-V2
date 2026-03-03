from __future__ import annotations

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


class _GreedyZBandFakePallet:
    def preview_place(self, box: Box) -> PlacementPreview:
        is_high = int(box.box_id) == 2
        z_mm = 200 if is_high else 0
        gain = 2.0 if is_high else 1.0
        placement = Placement(
            x_mm=0,
            y_mm=0,
            z_mm=z_mm,
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
            packing_gain=float(gain),
            fragmentation=0.0,
            height_after_mm=int(z_mm + box.height_mm),
            infeasible_reason=None,
            debug=None,
        )


def test_scheduler_greedy_z_band_filters_high_z_candidate() -> None:
    boxes = [
        Box(box_id=1, length_mm=1, width_mm=1, height_mm=1, timestamp=0.0, destination=1),
        Box(box_id=2, length_mm=1, width_mm=1, height_mm=1, timestamp=0.0, destination=1),
    ]
    state = SchedulerSimState(
        now=0.0,
        ramps={1: boxes},
        pallets={1: _GreedyZBandFakePallet()},
        pallet_blocked=set(),
    )

    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=2, z_band_mm=0))
    plan = scheduler.choose_action(state)
    assert plan is not None
    assert int(plan.box_id) == 1
    assert int(plan.preview.placement.z_mm) == 0  # type: ignore[union-attr]
    assert int(scheduler.last_eval_stats.get("z_band_removed", 0)) == 1
