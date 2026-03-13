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
