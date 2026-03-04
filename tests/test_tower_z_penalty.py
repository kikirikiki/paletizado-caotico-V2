from __future__ import annotations

from dataclasses import dataclass

from palca.domain.placement import PlacementPreview
from palca.scheduler.scheduler_v1 import PickPlan, SchedulerConfig, SchedulerV1, _ScoreTerms, _ScoredCandidate


@dataclass
class FakeBox:
    timestamp: float = 0.0
    box_id: int = 1
    priority: float = 0.0


def _candidate(height_after_mm: int, score: float) -> _ScoredCandidate:
    box = FakeBox(timestamp=0.0, box_id=1)
    preview = PlacementPreview(feasible=True, placement=None, packing_gain=0.0, fragmentation=0.0)
    plan = PickPlan(
        ramp_id=1,
        buffer_index=0,
        box_id=1,
        pallet_id=1,
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
        height_after_mm=int(height_after_mm),
    )
    return _ScoredCandidate(plan=plan, box=box, terms=terms)


def test_tower_z_penalty_prefers_lower_height_when_enabled() -> None:
    cfg = SchedulerConfig(
        lookahead_k=1,
        score_mode="gain_frag",
        tower_z_band_mm=0,
        tower_z_penalty_weight=1.0,
    )
    sched = SchedulerV1(cfg)
    c_low = _candidate(height_after_mm=1000, score=1.0)
    c_high = _candidate(height_after_mm=1300, score=1.0)
    out = sched._apply_tower_z_penalty_scored_candidates(
        candidates=[c_low, c_high],
        min_feasible_height_after_mm=1000,
    )
    assert out[0].terms.scalar_score > out[1].terms.scalar_score
    assert out[1].terms.tower_z_delta_mm == 300
    assert out[1].terms.tower_z_penalty == 0.3


def test_tower_z_penalty_is_noop_when_weight_zero() -> None:
    cfg = SchedulerConfig(
        lookahead_k=1,
        score_mode="gain_frag",
        tower_z_band_mm=0,
        tower_z_penalty_weight=0.0,
    )
    sched = SchedulerV1(cfg)
    c_low = _candidate(height_after_mm=1000, score=1.0)
    c_high = _candidate(height_after_mm=1300, score=1.0)
    out = sched._apply_tower_z_penalty_scored_candidates(
        candidates=[c_low, c_high],
        min_feasible_height_after_mm=1000,
    )
    assert out[0].terms.scalar_score == 1.0
    assert out[1].terms.scalar_score == 1.0
