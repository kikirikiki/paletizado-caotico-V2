from __future__ import annotations

from dataclasses import dataclass

import pytest

from palca.domain.placement import Placement, PlacementPreview
from palca.scheduler.scheduler_v1 import (
    PickPlan,
    SchedulerConfig,
    SchedulerV1,
    _BeamExpansion,
    _BeamNode,
    _ScoreTerms,
)


@dataclass
class FakeBox:
    destination: int
    box_id: int = 1
    timestamp: float = 0.0
    priority: float = 0.0


def _preview_with_xy(*, box_id: int, x_mm: int, y_mm: int) -> PlacementPreview:
    return PlacementPreview(
        feasible=True,
        placement=Placement(
            x_mm=int(x_mm),
            y_mm=int(y_mm),
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=100,
            width_mm=100,
            height_mm=100,
            box_id=int(box_id),
        ),
        packing_gain=0.0,
        fragmentation=0.0,
        height_after_mm=100,
    )


def _beam_expansion(*, pallet_id: int, x_mm: int, y_mm: int, score: float) -> _BeamExpansion:
    box = FakeBox(destination=int(pallet_id), box_id=10)
    first_plan = PickPlan(
        ramp_id=1,
        buffer_index=0,
        box_id=10,
        pallet_id=int(pallet_id),
        preview=_preview_with_xy(box_id=10, x_mm=int(x_mm), y_mm=int(y_mm)),
        score=float(score),
        dt_extra=0.0,
    )
    node = _BeamNode(
        ramps={},
        pallets={},
        score_sum=float(score),
        first_plan=first_plan,
    )
    terms = _ScoreTerms(
        packing_gain=0.0,
        fragmentation=0.0,
        score_adjustment=0.0,
        dt_extra=0.0,
        time_cost=0.0,
        starv_cost=0.0,
        priority_score=0.0,
        scalar_score=float(score),
        height_after_mm=100,
    )
    return _BeamExpansion(node=node, box=box, terms=terms)


def test_spatial_tower_penalty_helper_gating_and_target_ramp() -> None:
    sched = SchedulerV1(
        SchedulerConfig(
            spatial_tower_penalty_weight=0.4,
            spatial_tower_penalty_end_step=14,
            spatial_tower_target_base=2,
            spatial_tower_target_step_div=6,
        )
    )

    delta0, penalty0 = sched._spatial_tower_penalty_for_after_count(after_count=3, step_idx=0)
    assert delta0 == 1
    assert penalty0 == pytest.approx(0.4)

    delta13, penalty13 = sched._spatial_tower_penalty_for_after_count(after_count=4, step_idx=13)
    assert delta13 == 0
    assert penalty13 == pytest.approx(0.0)

    delta20, penalty20 = sched._spatial_tower_penalty_for_after_count(after_count=100, step_idx=20)
    assert delta20 == 0
    assert penalty20 == pytest.approx(0.0)


def test_spatial_penalty_applies_to_micro_root_expansion_scores() -> None:
    sched = SchedulerV1(
        SchedulerConfig(
            spatial_xy_bin_mm=100,
            spatial_tower_penalty_weight=0.4,
            spatial_tower_penalty_end_step=14,
            spatial_tower_target_base=2,
            spatial_tower_target_step_div=6,
        )
    )
    sched._spatial_bin_counts = {1: {(0, 0): 3}}
    sched._spatial_step_index_by_pallet = {1: 0}

    expansion = _beam_expansion(pallet_id=1, x_mm=10, y_mm=10, score=1.0)
    out = sched._apply_spatial_tower_penalty_to_expansions(expansions=[expansion], adjust_first_plan=True)

    assert len(out) == 1
    assert out[0].terms.scalar_score == pytest.approx(0.2)
    assert out[0].node.score_sum == pytest.approx(0.2)
    assert out[0].node.first_plan is not None
    assert out[0].node.first_plan.score == pytest.approx(0.2)
    assert out[0].terms.spatial_tower_delta == 2
    assert out[0].terms.spatial_tower_penalty == pytest.approx(0.8)
    assert sched.spatial_tower_penalty_applied_count == 1
    assert sched.spatial_tower_penalty_sum == pytest.approx(0.8)
