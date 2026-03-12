from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.early_layer_pattern_planner import LayerOpeningPlan, PlannedLayerPlacement
from palca.packer.pallet_model import PalletModel
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


class _GainPallet:
    def __init__(self, gains: dict[int, float]) -> None:
        self._gains = dict(gains)
        self.placements: list[Placement] = []
        self.layers: list[object] = []

    def preview_place(self, box: Box) -> PlacementPreview:
        gain = float(self._gains.get(int(box.box_id), 0.0))
        placement = Placement(
            x_mm=0,
            y_mm=0,
            z_mm=0,
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
            packing_gain=gain,
            fragmentation=0.0,
            height_after_mm=100,
            infeasible_reason=None,
            score_adjustment=0.0,
            debug=None,
        )

    def current_height_mm(self) -> int:
        return 0


def _box(*, box_id: int, l: int, w: int, h: int = 10, destination: int = 1) -> Box:
    return Box(
        box_id=box_id,
        length_mm=l,
        width_mm=w,
        height_mm=h,
        timestamp=0.0,
        destination=destination,
    )


def _pallet_with_full_base_layer() -> PalletModel:
    pallet = PalletModel(spec=PalletSpec(length_mm=10, width_mm=10, max_height_mm=100, overhang_mm=0), heuristic="baf")
    base = _box(box_id=100, l=10, w=10, h=10)
    preview = pallet.preview_place(base)
    assert preview.feasible
    pallet.commit_place(preview)
    return pallet


def test_baseline_intact_when_early_layer_planner_flag_is_off() -> None:
    boxes = [_box(box_id=1, l=1, w=1), _box(box_id=2, l=1, w=1)]
    sim_state = SchedulerSimState(
        now=0.0,
        ramps={1: boxes},
        pallets={1: _GainPallet({1: 0.2, 2: 1.0})},
        pallet_blocked=set(),
    )

    baseline = SchedulerV1(SchedulerConfig(lookahead_k=2, micro_plan_enabled=False))
    with_flag_off = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=False,
            use_early_layer_pattern_planner=False,
            layer_pattern_prefix_depth=3,
            layer_pattern_beam_width=4,
            layer_pattern_candidate_cap=8,
        )
    )

    plan_baseline = baseline.choose_action(sim_state)
    plan_flag_off = with_flag_off.choose_action(sim_state)
    assert plan_baseline is not None
    assert plan_flag_off is not None
    assert int(plan_flag_off.buffer_index) == int(plan_baseline.buffer_index)
    assert int(plan_flag_off.box_id) == int(plan_baseline.box_id)


def test_planner_activates_on_new_layer_opening() -> None:
    pallet = _pallet_with_full_base_layer()
    boxes = [_box(box_id=1, l=5, w=10), _box(box_id=2, l=5, w=10)]
    sim_state = SchedulerSimState(
        now=0.0,
        ramps={1: boxes},
        pallets={1: pallet},
        pallet_blocked=set(),
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=False,
            use_early_layer_pattern_planner=True,
            layer_pattern_prefix_depth=3,
            layer_pattern_beam_width=4,
            layer_pattern_candidate_cap=8,
        )
    )

    plan = scheduler.choose_action(sim_state)
    assert plan is not None
    assert int(getattr(plan.preview.placement, "layer_id", -1)) == 1
    assert int(scheduler.planner_invocations) == 1
    assert int(scheduler.planner_abstains) == 0
    assert int(scheduler.planned_prefix_len_count) == 1
    assert int(scheduler.planned_prefix_len_sum) >= 1


def test_pending_layer_plan_is_consumed_fifo() -> None:
    pallet = _pallet_with_full_base_layer()
    boxes = [_box(box_id=1, l=5, w=10), _box(box_id=2, l=5, w=10)]
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=False,
            use_early_layer_pattern_planner=True,
            layer_pattern_prefix_depth=3,
            layer_pattern_beam_width=4,
            layer_pattern_candidate_cap=8,
        )
    )
    scheduler._set_pending_layer_plan(
        LayerOpeningPlan(
            pallet_id=1,
            layer_id=1,
            z_mm=10,
            placements=(
                PlannedLayerPlacement(ramp_id=1, box_id=1, pallet_id=1, layer_id=1, z_mm=10),
                PlannedLayerPlacement(ramp_id=1, box_id=2, pallet_id=1, layer_id=1, z_mm=10),
            ),
        )
    )

    state_1 = SchedulerSimState(now=0.0, ramps={1: list(boxes)}, pallets={1: pallet}, pallet_blocked=set())
    first = scheduler.choose_action(state_1)
    assert first is not None
    assert int(first.box_id) == 1
    pallet.commit_place(first.preview)
    assert len(scheduler.pending_layer_plan) == 1
    expected_second_box_id = scheduler.pending_layer_plan[0].box_id

    remaining = [box for box in boxes if int(box.box_id) != int(first.box_id)]
    state_2 = SchedulerSimState(now=1.0, ramps={1: remaining}, pallets={1: pallet}, pallet_blocked=set())
    second = scheduler.choose_action(state_2)
    assert second is not None
    assert int(second.box_id) == int(expected_second_box_id)
    assert len(scheduler.pending_layer_plan) == 0


def test_pending_plan_rejects_backstep_to_previous_layer(monkeypatch) -> None:
    pallet = _pallet_with_full_base_layer()
    boxes = [_box(box_id=1, l=5, w=10), _box(box_id=2, l=5, w=10)]
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=False,
            use_early_layer_pattern_planner=True,
            layer_pattern_prefix_depth=3,
            layer_pattern_beam_width=4,
            layer_pattern_candidate_cap=8,
        )
    )
    scheduler._set_pending_layer_plan(
        LayerOpeningPlan(
            pallet_id=1,
            layer_id=1,
            z_mm=10,
            placements=(PlannedLayerPlacement(ramp_id=1, box_id=1, pallet_id=1, layer_id=1, z_mm=10),),
        )
    )
    pending_box_id = 1
    original_preview = scheduler._preview_place
    calls: dict[int, int] = {}

    def fake_preview(pallet_arg: PalletModel, box: Box) -> PlacementPreview:
        box_id = int(box.box_id)
        calls[box_id] = int(calls.get(box_id, 0)) + 1
        if box_id == pending_box_id:
            if int(calls[box_id]) == 1:
                placement = Placement(
                    x_mm=0,
                    y_mm=0,
                    z_mm=0,
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
                    packing_gain=0.0,
                    fragmentation=0.0,
                    height_after_mm=10,
                    infeasible_reason=None,
                    score_adjustment=0.0,
                    debug=None,
                )
            return PlacementPreview(
                feasible=False,
                placement=None,
                packing_gain=0.0,
                fragmentation=0.0,
                height_after_mm=None,
                infeasible_reason="NO_SPACE",
                score_adjustment=0.0,
                debug=None,
            )
        return original_preview(pallet_arg, box)

    monkeypatch.setattr(scheduler, "_preview_place", fake_preview)
    state = SchedulerSimState(now=1.0, ramps={1: list(boxes)}, pallets={1: pallet}, pallet_blocked=set())
    second = scheduler.choose_action(state)
    assert second is not None
    assert int(second.box_id) != int(pending_box_id)
    assert int(getattr(second.preview.placement, "layer_id", -1)) >= 1
    assert len(scheduler.pending_layer_plan) == 0
