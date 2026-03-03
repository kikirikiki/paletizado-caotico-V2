from __future__ import annotations

from typing import Any

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.scheduler.scheduler_v1 import (
    PickPlan,
    SchedulerConfig,
    SchedulerSimState,
    SchedulerV1,
    _ScoredCandidate,
    _ScoreTerms,
)


def _make_scored_candidate(*, box_id: int, pallet_id: int, z_mm: int, scalar_score: float) -> _ScoredCandidate:
    box = Box(
        box_id=box_id,
        length_mm=200,
        width_mm=150,
        height_mm=120,
        timestamp=0.0,
        destination=pallet_id,
    )
    placement = Placement(
        x_mm=0,
        y_mm=0,
        z_mm=int(z_mm),
        rot90=False,
        layer_id=0,
        length_mm=box.length_mm,
        width_mm=box.width_mm,
        height_mm=box.height_mm,
        box_id=box.box_id,
    )
    preview = PlacementPreview(
        feasible=True,
        placement=placement,
        packing_gain=float(scalar_score),
        fragmentation=0.0,
        height_after_mm=int(z_mm + box.height_mm),
        infeasible_reason=None,
    )
    terms = _ScoreTerms(
        packing_gain=float(scalar_score),
        fragmentation=0.0,
        score_adjustment=0.0,
        dt_extra=0.0,
        time_cost=0.0,
        starv_cost=0.0,
        priority_score=0.0,
        scalar_score=float(scalar_score),
        height_after_mm=int(z_mm + box.height_mm),
    )
    plan = PickPlan(
        ramp_id=1,
        buffer_index=0,
        box_id=box.box_id,
        pallet_id=pallet_id,
        preview=preview,
        score=float(scalar_score),
        dt_extra=0.0,
    )
    return _ScoredCandidate(plan=plan, box=box, terms=terms)


def test_filter_by_z_band_zero_keeps_lowest_z_for_same_pallet() -> None:
    scheduler = SchedulerV1(SchedulerConfig(z_band_mm=0))
    low_z = _make_scored_candidate(box_id=1, pallet_id=1, z_mm=0, scalar_score=10.0)
    high_z_better_score = _make_scored_candidate(box_id=2, pallet_id=1, z_mm=200, scalar_score=99.0)

    assert max([low_z, high_z_better_score], key=lambda c: c.terms.scalar_score).plan.box_id == 2

    filtered, removed = scheduler._filter_by_z_band_scored([low_z, high_z_better_score], 0)

    assert removed == 1
    assert len(filtered) == 1
    assert filtered[0].plan.box_id == 1


class _DummyPallet:
    def current_height_mm(self) -> int:
        return 0

    def commit_place(self, _preview: PlacementPreview) -> None:
        return None


def _preview_for_box(box: Box) -> PlacementPreview:
    z_mm = 0 if int(box.box_id) == 1 else 200
    placement = Placement(
        x_mm=0,
        y_mm=0,
        z_mm=int(z_mm),
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
        packing_gain=float(1000 if int(box.box_id) == 2 else 1),
        fragmentation=0.0,
        height_after_mm=int(z_mm + int(box.height_mm)),
        infeasible_reason=None,
    )


def _score_for_box(box: Box, height_after_mm: int) -> _ScoreTerms:
    scalar = 1000.0 if int(box.box_id) == 2 else 1.0
    return _ScoreTerms(
        packing_gain=scalar,
        fragmentation=0.0,
        score_adjustment=0.0,
        dt_extra=0.0,
        time_cost=0.0,
        starv_cost=0.0,
        priority_score=0.0,
        scalar_score=scalar,
        height_after_mm=int(height_after_mm),
    )


def _build_state() -> SchedulerSimState:
    low = Box(box_id=1, length_mm=200, width_mm=150, height_mm=120, timestamp=0.0, destination=1)
    high = Box(box_id=2, length_mm=200, width_mm=150, height_mm=120, timestamp=0.0, destination=1)
    return SchedulerSimState(
        now=0.0,
        ramps={1: [low, high]},
        pallets={1: _DummyPallet()},
        pallet_blocked=set(),
    )


def test_choose_action_greedy_applies_z_band_and_reports_removed(monkeypatch: Any) -> None:
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=2, z_band_mm=0, micro_plan_enabled=False))
    sim_state = _build_state()

    monkeypatch.setattr(scheduler, "_preview_place", lambda pallet, box: _preview_for_box(box))
    monkeypatch.setattr(
        scheduler,
        "_score_candidate",
        lambda **kwargs: _score_for_box(kwargs["box"], int(kwargs["height_after_mm"])),
    )

    plan = scheduler.choose_action(sim_state)
    assert plan is not None
    assert int(plan.box_id) == 1
    assert scheduler.last_eval_stats["mode"] == "greedy"
    assert int(scheduler.last_eval_stats.get("z_band_removed", 0)) == 1


def test_choose_action_micro_applies_z_band_and_reports_removed(monkeypatch: Any) -> None:
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            z_band_mm=0,
            micro_plan_enabled=True,
            micro_plan_depth=2,
            micro_plan_width=8,
            micro_plan_topk_per_step=8,
        )
    )
    sim_state = _build_state()

    monkeypatch.setattr(scheduler, "_preview_place", lambda pallet, box: _preview_for_box(box))
    monkeypatch.setattr(
        scheduler,
        "_score_candidate",
        lambda **kwargs: _score_for_box(kwargs["box"], int(kwargs["height_after_mm"])),
    )

    plan = scheduler.choose_action(sim_state)
    assert plan is not None
    assert int(plan.box_id) == 1
    assert scheduler.last_eval_stats["mode"] == "micro"
    assert int(scheduler.last_eval_stats.get("z_band_removed", 0)) == 1
