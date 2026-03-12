from __future__ import annotations

from dataclasses import dataclass

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.layer_skeleton_planner import LayerSkeletonPlan, PlannedLayerPlacement
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


@dataclass
class _PlannerStub:
    plan_opening_result: LayerSkeletonPlan | None = None
    plan_for_layer_result: LayerSkeletonPlan | None = None
    plan_for_layer_calls: int = 0

    def plan_opening(self, **_kwargs) -> LayerSkeletonPlan | None:
        return self.plan_opening_result

    def plan_for_layer(self, **_kwargs) -> LayerSkeletonPlan | None:
        self.plan_for_layer_calls += 1
        return self.plan_for_layer_result


def _skeleton_plan(
    *,
    pallet_id: int | str,
    layer_id: int,
    z_mm: int,
    placements: tuple[PlannedLayerPlacement, ...],
    is_terminal: bool = False,
    packed_area_same_layer: int = 100,
    largest_free_rect_area: int = 100,
    fragmentation_penalty: int = 0,
    min_support_ratio: float = 1.0,
    area_fill_ratio: float = 0.5,
) -> LayerSkeletonPlan:
    return LayerSkeletonPlan(
        pallet_id=pallet_id,
        layer_id=layer_id,
        z_mm=z_mm,
        placements=placements,
        is_terminal=is_terminal,
        packed_area_same_layer=packed_area_same_layer,
        largest_free_rect_area=largest_free_rect_area,
        fragmentation_penalty=fragmentation_penalty,
        min_support_ratio=min_support_ratio,
        area_fill_ratio=area_fill_ratio,
    )


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
    assert int(scheduler.active_committed_layer_index or -1) == 1


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
        _skeleton_plan(
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
        _skeleton_plan(
            pallet_id=1,
            layer_id=1,
            z_mm=10,
            placements=(PlannedLayerPlacement(ramp_id=1, box_id=1, pallet_id=1, layer_id=1, z_mm=10),),
            is_terminal=True,
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


def test_pending_exhaustion_replans_same_active_layer(monkeypatch) -> None:
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
    scheduler._activate_committed_layer(pallet_id=1, layer_id=1, z_mm=10)
    scheduler._set_pending_layer_plan(
        _skeleton_plan(
            pallet_id=1,
            layer_id=1,
            z_mm=10,
            placements=(PlannedLayerPlacement(ramp_id=1, box_id=1, pallet_id=1, layer_id=1, z_mm=10),),
            is_terminal=True,
        )
    )
    planner = _PlannerStub(
        plan_for_layer_result=_skeleton_plan(
            pallet_id=1,
            layer_id=1,
            z_mm=10,
            placements=(PlannedLayerPlacement(ramp_id=1, box_id=2, pallet_id=1, layer_id=1, z_mm=10),),
            is_terminal=True,
        )
    )
    monkeypatch.setattr(scheduler, "_ensure_early_layer_pattern_planner", lambda: planner)

    first_state = SchedulerSimState(now=0.0, ramps={1: list(boxes)}, pallets={1: pallet}, pallet_blocked=set())
    first = scheduler.choose_action(first_state)
    assert first is not None
    assert int(first.box_id) == 1
    pallet.commit_place(first.preview)

    second_state = SchedulerSimState(now=1.0, ramps={1: [boxes[1]]}, pallets={1: pallet}, pallet_blocked=set())
    second = scheduler.choose_action(second_state)
    assert second is not None
    assert int(second.box_id) == 2
    assert int(planner.plan_for_layer_calls) == 1
    assert int(scheduler.active_layer_commit_replans_total) == 1
    assert int(scheduler.skeleton_rebuilds_total) == 1


def test_non_terminal_singleton_skeleton_is_abstain(monkeypatch) -> None:
    pallet = _pallet_with_full_base_layer()
    lower_layer_box = _box(box_id=41, l=5, w=10)
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=1,
            micro_plan_enabled=False,
            use_early_layer_pattern_planner=True,
            layer_pattern_prefix_depth=3,
            layer_pattern_beam_width=4,
            layer_pattern_candidate_cap=8,
        )
    )
    scheduler._activate_committed_layer(pallet_id=1, layer_id=1, z_mm=10)
    planner = _PlannerStub(
        plan_for_layer_result=_skeleton_plan(
            pallet_id=1,
            layer_id=1,
            z_mm=10,
            placements=(PlannedLayerPlacement(ramp_id=1, box_id=99, pallet_id=1, layer_id=1, z_mm=10),),
            is_terminal=False,
        )
    )
    monkeypatch.setattr(scheduler, "_ensure_early_layer_pattern_planner", lambda: planner)

    def fake_preview(_pallet_arg: PalletModel, box: Box) -> PlacementPreview:
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
            packing_gain=5.0,
            fragmentation=0.0,
            height_after_mm=10,
            infeasible_reason=None,
            score_adjustment=0.0,
            debug=None,
        )

    monkeypatch.setattr(scheduler, "_preview_place", fake_preview)
    state = SchedulerSimState(now=0.0, ramps={1: [lower_layer_box]}, pallets={1: pallet}, pallet_blocked=set())
    plan = scheduler.choose_action(state)
    assert plan is None
    assert int(scheduler.planner_abstains) == 1
    assert int(scheduler.active_layer_commit_closures_total) == 1


def test_same_layer_fallback_greedy_is_used_when_planner_abstains(monkeypatch) -> None:
    pallet = _pallet_with_full_base_layer()
    same_layer_box = _box(box_id=11, l=5, w=10)
    lower_layer_box = _box(box_id=12, l=5, w=10)
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
    scheduler._activate_committed_layer(pallet_id=1, layer_id=1, z_mm=10)
    planner = _PlannerStub(plan_for_layer_result=None)
    monkeypatch.setattr(scheduler, "_ensure_early_layer_pattern_planner", lambda: planner)

    def fake_preview(_pallet_arg: PalletModel, box: Box) -> PlacementPreview:
        if int(box.box_id) == 11:
            placement = Placement(
                x_mm=0,
                y_mm=0,
                z_mm=10,
                rot90=False,
                layer_id=1,
                length_mm=int(box.length_mm),
                width_mm=int(box.width_mm),
                height_mm=int(box.height_mm),
                box_id=box.box_id,
            )
            return PlacementPreview(
                feasible=True,
                placement=placement,
                packing_gain=0.2,
                fragmentation=0.0,
                height_after_mm=20,
                infeasible_reason=None,
                score_adjustment=0.0,
                debug=None,
            )
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
            packing_gain=9.0,
            fragmentation=0.0,
            height_after_mm=10,
            infeasible_reason=None,
            score_adjustment=0.0,
            debug=None,
        )

    monkeypatch.setattr(scheduler, "_preview_place", fake_preview)
    state = SchedulerSimState(
        now=0.0,
        ramps={1: [same_layer_box, lower_layer_box]},
        pallets={1: pallet},
        pallet_blocked=set(),
    )
    plan = scheduler.choose_action(state)
    assert plan is not None
    assert int(plan.box_id) == 11
    assert int(scheduler.active_layer_commit_fallback_same_layer_total) == 1


def test_active_layer_closes_only_when_no_same_layer_candidate_exists(monkeypatch) -> None:
    pallet = _pallet_with_full_base_layer()
    lower_layer_box = _box(box_id=21, l=5, w=10)
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=1,
            micro_plan_enabled=False,
            use_early_layer_pattern_planner=True,
            layer_pattern_prefix_depth=3,
            layer_pattern_beam_width=4,
            layer_pattern_candidate_cap=8,
        )
    )
    scheduler._activate_committed_layer(pallet_id=1, layer_id=1, z_mm=10)
    planner = _PlannerStub(plan_for_layer_result=None)
    monkeypatch.setattr(scheduler, "_ensure_early_layer_pattern_planner", lambda: planner)

    def fake_preview(_pallet_arg: PalletModel, box: Box) -> PlacementPreview:
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
            packing_gain=5.0,
            fragmentation=0.0,
            height_after_mm=10,
            infeasible_reason=None,
            score_adjustment=0.0,
            debug=None,
        )

    monkeypatch.setattr(scheduler, "_preview_place", fake_preview)
    state = SchedulerSimState(
        now=0.0,
        ramps={1: [lower_layer_box]},
        pallets={1: pallet},
        pallet_blocked=set(),
    )
    plan = scheduler.choose_action(state)
    assert plan is None
    assert scheduler.active_committed_layer_index is None
    assert int(scheduler.active_layer_commit_closures_total) == 1


def test_feature_on_never_backsteps_to_previous_layers(monkeypatch) -> None:
    pallet = _pallet_with_full_base_layer()
    lower_layer_box = _box(box_id=31, l=5, w=10)
    allowed_layer_box = _box(box_id=32, l=5, w=10)
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=True,
            use_early_layer_pattern_planner=True,
            layer_pattern_prefix_depth=3,
            layer_pattern_beam_width=4,
            layer_pattern_candidate_cap=8,
        )
    )
    scheduler._active_layer_commit_started = True
    scheduler._active_committed_pallet_id = 1
    scheduler._active_layer_commit_min_layer_index = 2
    monkeypatch.setattr(scheduler, "_try_layer_opening_plan", lambda _sim_state: None)

    def fake_preview(_pallet_arg: PalletModel, box: Box) -> PlacementPreview:
        if int(box.box_id) == 31:
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
                packing_gain=100.0,
                fragmentation=0.0,
                height_after_mm=10,
                infeasible_reason=None,
                score_adjustment=0.0,
                debug=None,
            )
        placement = Placement(
            x_mm=0,
            y_mm=0,
            z_mm=20,
            rot90=False,
            layer_id=2,
            length_mm=int(box.length_mm),
            width_mm=int(box.width_mm),
            height_mm=int(box.height_mm),
            box_id=box.box_id,
        )
        return PlacementPreview(
            feasible=True,
            placement=placement,
            packing_gain=1.0,
            fragmentation=0.0,
            height_after_mm=30,
            infeasible_reason=None,
            score_adjustment=0.0,
            debug=None,
        )

    monkeypatch.setattr(scheduler, "_preview_place", fake_preview)
    state = SchedulerSimState(
        now=0.0,
        ramps={1: [lower_layer_box, allowed_layer_box]},
        pallets={1: pallet},
        pallet_blocked=set(),
    )
    plan = scheduler.choose_action(state)
    assert plan is not None
    assert int(plan.box_id) == 32
