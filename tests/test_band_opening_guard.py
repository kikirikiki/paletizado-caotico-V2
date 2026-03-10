from __future__ import annotations

from dataclasses import dataclass

from palca.domain.placement import Placement, PlacementPreview
from palca.scheduler.scheduler_v1 import (
    PickPlan,
    SchedulerConfig,
    SchedulerV1,
    _BeamExpansion,
    _BeamNode,
    _ScoreTerms,
    _ScoredCandidate,
)


@dataclass
class _FakeBox:
    timestamp: float = 0.0
    box_id: int = 1
    priority: float = 0.0
    destination: int = 1


@dataclass
class _FakePallet:
    placements: list[Placement]
    bin_area_mm2: int = 10_000


def _placement(*, box_id: int, z_mm: int, x_mm: int = 0, y_mm: int = 0, l_mm: int = 20, w_mm: int = 20) -> Placement:
    return Placement(
        x_mm=int(x_mm),
        y_mm=int(y_mm),
        z_mm=int(z_mm),
        rot90=False,
        layer_id=0 if int(z_mm) == 0 else 1,
        length_mm=int(l_mm),
        width_mm=int(w_mm),
        height_mm=100,
        box_id=int(box_id),
    )


def _candidate(*, box_id: int, z_mm: int, score: float, pallet_id: int = 1) -> _ScoredCandidate:
    box = _FakeBox(box_id=int(box_id), destination=int(pallet_id))
    placement = _placement(box_id=int(box_id), z_mm=int(z_mm))
    preview = PlacementPreview(
        feasible=True,
        placement=placement,
        packing_gain=0.0,
        fragmentation=0.0,
        height_after_mm=int(z_mm) + 100,
    )
    plan = PickPlan(
        ramp_id=1,
        buffer_index=0,
        box_id=box_id,
        pallet_id=pallet_id,
        preview=preview,
        score=float(score),
        dt_extra=0.0,
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
        height_after_mm=int(z_mm) + 100,
    )
    return _ScoredCandidate(plan=plan, box=box, terms=terms)


def _expansion(*, box_id: int, z_mm: int, score: float, pallet: _FakePallet, pallet_id: int = 1) -> _BeamExpansion:
    cand = _candidate(box_id=box_id, z_mm=z_mm, score=score, pallet_id=pallet_id)
    node = _BeamNode(
        ramps={},
        pallets={pallet_id: pallet},
        score_sum=float(score),
        first_plan=cand.plan,
        height_after_mm=int(z_mm) + 100,
        placed_count=1,
    )
    return _BeamExpansion(node=node, box=cand.box, terms=cand.terms)


def test_opening_next_band_is_delayed_when_current_band_not_closed() -> None:
    sched = SchedulerV1(SchedulerConfig(lookahead_k=1, score_mode="gain_frag"))
    pallet = _FakePallet(
        placements=[
            _placement(box_id=100, z_mm=1000),
            _placement(box_id=101, z_mm=1000, x_mm=20),
        ]
    )
    keep_same_band = _candidate(box_id=1, z_mm=1000, score=0.92)
    open_next_band = _candidate(box_id=2, z_mm=1300, score=0.95)

    out = sched._apply_opening_next_band_guard_scored_candidates(
        candidates=[open_next_band, keep_same_band],
        pallets={1: pallet},
    )

    assert out[0].terms.scalar_score < out[1].terms.scalar_score
    assert int(sched.band_opening_guard_considered_total) == 1
    assert int(sched.band_opening_guard_delayed_total) == 1


def test_opening_next_band_keeps_priority_when_structurally_justified() -> None:
    sched = SchedulerV1(SchedulerConfig(lookahead_k=1, score_mode="gain_frag"))
    pallet = _FakePallet(
        placements=[
            _placement(box_id=100, z_mm=1000),
            _placement(box_id=101, z_mm=1000, x_mm=20),
        ]
    )
    keep_same_band = _candidate(box_id=1, z_mm=1000, score=0.80)
    open_next_band = _candidate(box_id=2, z_mm=1300, score=1.90)

    out = sched._apply_opening_next_band_guard_scored_candidates(
        candidates=[open_next_band, keep_same_band],
        pallets={1: pallet},
    )

    assert out[0].terms.scalar_score > out[1].terms.scalar_score
    assert int(sched.band_opening_guard_delayed_total) == 0
    assert int(sched.band_opening_guard_structural_override_total) == 1


def test_opening_next_band_guard_applies_on_micro_root_expansions() -> None:
    sched = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=True,
            micro_plan_depth=2,
            micro_plan_width=4,
            micro_plan_topk_per_step=4,
        )
    )
    pallet = _FakePallet(
        placements=[
            _placement(box_id=100, z_mm=1000),
            _placement(box_id=101, z_mm=1000, x_mm=20),
        ]
    )
    open_exp = _expansion(box_id=2, z_mm=1300, score=0.95, pallet=pallet)
    keep_exp = _expansion(box_id=1, z_mm=1000, score=0.92, pallet=pallet)

    out = sched._apply_opening_next_band_guard_expansions(
        expansions=[open_exp, keep_exp],
        pallets={1: pallet},
        adjust_first_plan=True,
    )

    assert out[0].terms.scalar_score < out[1].terms.scalar_score
    assert out[0].node.first_plan is not None
    assert float(out[0].node.first_plan.score) == float(out[0].terms.scalar_score)
