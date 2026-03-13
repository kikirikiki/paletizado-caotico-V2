from __future__ import annotations

from dataclasses import dataclass

from palca.integration.kpi_hooks import default_frontier_kpis, merge_frontier_kpis
from palca.integration.two_layer_frontier import TwoLayerFrontierController


@dataclass(frozen=True)
class _Candidate:
    layer: int
    score: float


def _best(candidates: list[_Candidate]) -> _Candidate:
    return max(candidates, key=lambda candidate: float(candidate.score))


def test_no_candidate_below_l_minus_two_or_lower() -> None:
    frontier = TwoLayerFrontierController(enabled=True, opening_span_moves=2, repair_burst_max=2)
    pallet_id = 1

    frontier.register_selection(pallet_id=pallet_id, layer=0)
    frontier.register_selection(pallet_id=pallet_id, layer=1)
    frontier.register_selection(pallet_id=pallet_id, layer=2)

    filtered = frontier.select_candidates(
        pallet_id=pallet_id,
        candidates=[_Candidate(layer=2, score=0.8), _Candidate(layer=1, score=0.9), _Candidate(layer=0, score=1.0)],
        layer_fn=lambda candidate: candidate.layer,
        best_candidate_fn=_best,
    )

    assert all(int(candidate.layer) >= 1 for candidate in filtered)
    assert frontier.is_candidate_legal(pallet_id=pallet_id, layer=0) is False


def test_frontier_width_max_never_exceeds_two_and_opening_next_closes_previous_repair() -> None:
    frontier = TwoLayerFrontierController(enabled=True, opening_span_moves=2, repair_burst_max=2)
    pallet_id = 1

    frontier.register_selection(pallet_id=pallet_id, layer=0)
    frontier.register_selection(pallet_id=pallet_id, layer=1)
    frontier.register_selection(pallet_id=pallet_id, layer=2)

    snapshot = frontier.snapshot()
    state = snapshot[pallet_id]
    assert state.active_layer == 2
    assert state.repair_layer == 1
    assert frontier.frontier_width_max <= 2
    assert state.layers[0].phase == "closed"


def test_closed_layer_does_not_reopen() -> None:
    frontier = TwoLayerFrontierController(enabled=True, opening_span_moves=2, repair_burst_max=2)
    pallet_id = 1

    frontier.register_selection(pallet_id=pallet_id, layer=0)
    frontier.register_selection(pallet_id=pallet_id, layer=1)
    frontier.register_selection(pallet_id=pallet_id, layer=2)
    frontier.register_selection(pallet_id=pallet_id, layer=0)

    assert frontier.layer_reopen_events_total == 1
    assert frontier.two_layer_frontier_violations >= 1
    assert frontier.frontier_violation_closed_reopen_total == 1


def test_max_backstep_depth_stays_bounded_to_one() -> None:
    frontier = TwoLayerFrontierController(enabled=True, opening_span_moves=2, repair_burst_max=2)
    pallet_id = 1

    frontier.register_selection(pallet_id=pallet_id, layer=0)
    frontier.register_selection(pallet_id=pallet_id, layer=1)
    frontier.register_selection(pallet_id=pallet_id, layer=2)
    frontier.register_selection(pallet_id=pallet_id, layer=1)

    assert frontier.max_backstep_depth == 1


def test_frontier_kpis_are_emitted() -> None:
    frontier = TwoLayerFrontierController(enabled=True, opening_span_moves=2, repair_burst_max=2)
    pallet_id = 1

    frontier.register_selection(pallet_id=pallet_id, layer=0)
    frontier.register_selection(pallet_id=pallet_id, layer=1)
    merged = merge_frontier_kpis({}, frontier.frontier_kpis())
    defaults = default_frontier_kpis()

    for key in defaults:
        assert key in merged


def test_repair_candidates_available_but_blocked_by_state_are_counted() -> None:
    frontier = TwoLayerFrontierController(enabled=True, opening_span_moves=2, repair_burst_max=2)
    pallet_id = 1

    frontier.register_selection(pallet_id=pallet_id, layer=0)
    frontier.register_selection(pallet_id=pallet_id, layer=1)

    filtered = frontier.select_candidates(
        pallet_id=pallet_id,
        candidates=[_Candidate(layer=1, score=0.6), _Candidate(layer=0, score=0.9)],
        layer_fn=lambda candidate: candidate.layer,
        best_candidate_fn=_best,
    )

    assert [candidate.layer for candidate in filtered] == [1]

    kpis = frontier.frontier_kpis()
    assert int(kpis["repair_candidates_available_total"]) == 1
    assert int(kpis["repair_candidates_selected_total"]) == 0
    assert int(kpis["repair_candidates_blocked_total"]) == 1
    assert int(kpis["repair_candidates_blocked_by_state_total"]) == 1
    assert int(kpis["repair_candidates_blocked_by_closure_total"]) == 0
    assert int(kpis["repair_candidates_blocked_by_frontier_total"]) == 0

    trace = kpis["frontier_decision_trace"]
    assert isinstance(trace, list)
    decision = trace[-1]
    assert decision["active_layer"] == 1
    assert decision["repair_layer"] == 0
    assert decision["num_candidates_active_layer"] == 1
    assert decision["num_candidates_repair_layer"] == 1
    assert decision["reason_selected_layer"] == "opening_window_active"
    assert decision["repair_not_selected_reason"] == "opening_window_active"


def test_closed_lower_layer_candidates_are_counted_as_blocked_by_closure() -> None:
    frontier = TwoLayerFrontierController(enabled=True, opening_span_moves=1, repair_burst_max=2)
    pallet_id = 1

    frontier.register_selection(pallet_id=pallet_id, layer=0)
    frontier.register_selection(pallet_id=pallet_id, layer=1)
    frontier.register_selection(pallet_id=pallet_id, layer=2)
    frontier.register_selection(pallet_id=pallet_id, layer=3)

    filtered = frontier.select_candidates(
        pallet_id=pallet_id,
        candidates=[_Candidate(layer=3, score=0.7), _Candidate(layer=1, score=0.9)],
        layer_fn=lambda candidate: candidate.layer,
        best_candidate_fn=_best,
    )

    assert [candidate.layer for candidate in filtered] == [3]

    kpis = frontier.frontier_kpis()
    assert int(kpis["repair_candidates_available_total"]) == 0
    assert int(kpis["repair_candidates_selected_total"]) == 0
    assert int(kpis["repair_candidates_blocked_total"]) == 1
    assert int(kpis["repair_candidates_blocked_by_closure_total"]) == 1

    trace = kpis["frontier_decision_trace"]
    assert isinstance(trace, list)
    decision = trace[-1]
    assert decision["reason_selected_layer"] == "active_only"
    assert decision["repair_not_selected_reason"] == "repair_layer_closed"


def test_frontier_violations_are_split_by_cause() -> None:
    frontier = TwoLayerFrontierController(enabled=True, opening_span_moves=1, repair_burst_max=2)
    pallet_id = 1

    frontier.register_selection(pallet_id=pallet_id, layer=0)
    frontier.register_selection(pallet_id=pallet_id, layer=1)
    frontier.register_selection(pallet_id=pallet_id, layer=2)
    frontier.register_selection(pallet_id=pallet_id, layer=0)
    frontier.select_candidates(
        pallet_id=pallet_id,
        candidates=[_Candidate(layer=2, score=0.8), _Candidate(layer=0, score=0.9)],
        layer_fn=lambda candidate: candidate.layer,
        best_candidate_fn=_best,
    )
    frontier._update_frontier_width(3, mutate_metrics=True)  # noqa: SLF001

    kpis = frontier.frontier_kpis()
    assert int(kpis["frontier_violation_closed_reopen_total"]) == 1
    assert int(kpis["frontier_violation_width_overflow_total"]) == 1
    assert int(kpis["frontier_violation_below_frontier_total"]) >= 1
    assert int(kpis["two_layer_frontier_violations"]) == (
        int(kpis["frontier_violation_closed_reopen_total"])
        + int(kpis["frontier_violation_width_overflow_total"])
        + int(kpis["frontier_violation_below_frontier_total"])
    )
