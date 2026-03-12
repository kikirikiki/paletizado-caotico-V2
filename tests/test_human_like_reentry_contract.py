from __future__ import annotations

from types import MethodType, SimpleNamespace

from palca.domain.box import Box
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1, _ScoreTerms


class _DummyPallet:
    def __init__(self, *, layers_count: int) -> None:
        self.layers = [object() for _ in range(layers_count)]
        self.placements: list[object] = []


def _make_scheduler(
    *,
    scores_by_box_id: dict[int, float],
    layers_by_box_id: dict[int, int],
    config: SchedulerConfig,
) -> SchedulerV1:
    scheduler = SchedulerV1(config)

    def _preview_place(_self: SchedulerV1, _pallet: _DummyPallet, box: Box) -> SimpleNamespace:
        box_id = int(box.box_id)
        layer_id = int(layers_by_box_id[box_id])
        return SimpleNamespace(
            feasible=True,
            infeasible_reason=None,
            placement=SimpleNamespace(layer_id=layer_id, x_mm=0, y_mm=0, z_mm=0, height_mm=100),
            packing_gain=float(scores_by_box_id[box_id]),
            fragmentation=0.0,
            score_adjustment=0.0,
            height_after_mm=100 + layer_id,
        )

    def _score_candidate(
        _self: SchedulerV1,
        *,
        now: float,
        box: Box,
        idx: int,
        preview: object,
        max_priority: float,
        height_after_mm: int,
    ) -> _ScoreTerms:
        _ = (now, idx, preview, max_priority, height_after_mm)
        box_id = int(box.box_id)
        score = float(scores_by_box_id[box_id])
        return _ScoreTerms(
            packing_gain=score,
            fragmentation=0.0,
            score_adjustment=0.0,
            dt_extra=0.0,
            time_cost=0.0,
            starv_cost=0.0,
            priority_score=0.0,
            scalar_score=score,
            height_after_mm=100 + int(layers_by_box_id[box_id]),
        )

    scheduler._preview_place = MethodType(_preview_place, scheduler)  # type: ignore[method-assign]
    scheduler._score_candidate = MethodType(_score_candidate, scheduler)  # type: ignore[method-assign]
    return scheduler


def _make_state(*, box_ids: list[int], layers_count: int = 4) -> SchedulerSimState:
    pallet = _DummyPallet(layers_count=layers_count)
    boxes = [
        Box(
            box_id=box_id,
            length_mm=100,
            width_mm=100,
            height_mm=100,
            timestamp=0.0,
            destination=1,
        )
        for box_id in box_ids
    ]
    return SchedulerSimState(
        now=0.0,
        ramps={1: boxes},
        pallets={1: pallet},  # type: ignore[arg-type]
        pallet_blocked=set(),
        remaining_total=len(boxes),
    )


def test_human_like_reentry_contract_flag_off_preserves_baseline_choice() -> None:
    scores = {1: 10.0, 2: 9.9}
    layers = {1: 1, 2: 3}  # box 1 = deep (drop 2), box 2 = active layer
    state = _make_state(box_ids=[1, 2], layers_count=4)

    baseline = _make_scheduler(
        scores_by_box_id=scores,
        layers_by_box_id=layers,
        config=SchedulerConfig(lookahead_k=2, micro_plan_enabled=False),
    )
    with_contract_off = _make_scheduler(
        scores_by_box_id=scores,
        layers_by_box_id=layers,
        config=SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=False,
            human_like_reentry_contract_enabled=False,
        ),
    )

    baseline_plan = baseline.choose_action(state)
    off_plan = with_contract_off.choose_action(state)
    assert baseline_plan is not None
    assert off_plan is not None
    assert int(off_plan.box_id) == int(baseline_plan.box_id)


def test_human_like_reentry_contract_vetoes_deep_when_shallow_within_margin() -> None:
    scores = {1: 10.0, 2: 9.85}
    layers = {1: 1, 2: 3}  # active layer is 3
    scheduler = _make_scheduler(
        scores_by_box_id=scores,
        layers_by_box_id=layers,
        config=SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=False,
            human_like_reentry_contract_enabled=True,
            human_like_reentry_contract_require_opener=False,
            human_like_reentry_contract_deep_reentry_advantage_margin=0.2,
        ),
    )
    plan = scheduler.choose_action(_make_state(box_ids=[1, 2], layers_count=4))

    assert plan is not None
    assert int(plan.box_id) == 2
    assert int(scheduler.deep_reentry_attempts) == 1
    assert int(scheduler.deep_reentry_vetoed) == 1
    assert int(scheduler.reentries_drop_ge_2_count) == 0
    assert int(scheduler.max_layer_drop) <= 1


def test_human_like_reentry_contract_allows_deep_if_no_shallow_candidate() -> None:
    scores = {1: 10.0}
    layers = {1: 1}  # only deep candidate, active layer is 3
    scheduler = _make_scheduler(
        scores_by_box_id=scores,
        layers_by_box_id=layers,
        config=SchedulerConfig(
            lookahead_k=1,
            micro_plan_enabled=False,
            human_like_reentry_contract_enabled=True,
            human_like_reentry_contract_require_opener=False,
        ),
    )
    plan = scheduler.choose_action(_make_state(box_ids=[1], layers_count=4))

    assert plan is not None
    assert int(plan.box_id) == 1
    assert int(scheduler.deep_reentry_attempts) == 1
    assert int(scheduler.deep_reentry_allowed_no_shallow) == 1
    assert int(scheduler.reentries_drop_ge_2_count) == 1
    assert int(scheduler.max_layer_drop) == 2


def test_human_like_reentry_contract_enforces_l1_correction_budget_per_layer() -> None:
    scores = {1: 10.0, 2: 9.0}
    layers = {1: 2, 2: 3}  # box 1 = L-1 correction, box 2 = active layer
    scheduler = _make_scheduler(
        scores_by_box_id=scores,
        layers_by_box_id=layers,
        config=SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=False,
            human_like_reentry_contract_enabled=True,
            human_like_reentry_contract_require_opener=False,
            human_like_reentry_contract_l1_correction_budget_per_layer=1,
        ),
    )
    state = _make_state(box_ids=[1, 2], layers_count=4)

    first = scheduler.choose_action(state)
    second = scheduler.choose_action(state)

    assert first is not None and second is not None
    assert int(first.box_id) == 1
    assert int(second.box_id) == 2
    assert int(scheduler.l1_corrections_used) == 1
