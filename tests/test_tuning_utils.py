from __future__ import annotations

from palca.tuning.episodes import apply_shuffle
from palca.tuning.metrics import parse_metrics
from palca.tuning.rank import make_rank_key


def test_apply_shuffle_reproducible_and_preserves_multiset() -> None:
    order = list(range(1, 41))
    shuffled_a = apply_shuffle(order, seed=123, window=10, strength=0.35)
    shuffled_b = apply_shuffle(order, seed=123, window=10, strength=0.35)

    assert shuffled_a == shuffled_b
    assert sorted(shuffled_a) == sorted(order)
    assert apply_shuffle(order, seed=123, window=1, strength=0.5) == order
    assert apply_shuffle(order, seed=123, window=10, strength=0.0) == order


def test_parse_metrics_missing_fields_marks_fail_without_crash() -> None:
    payload = {
        "params": {"force_destination": 1},
        "metrics": {"pallet_kpis": {}},
    }

    parsed = parse_metrics(payload)

    assert parsed["status"] == "fail"
    assert "missing_boxes_per_pallet" in str(parsed["error"])
    assert "missing_closures_by_reason" in str(parsed["error"])


def test_rank_key_prioritizes_deadlocks_before_other_metrics() -> None:
    better_deadlocks = {
        "status": "ok",
        "deadlocks_stability": 0,
        "p95_boxes_per_pallet": 10.0,
        "pct_ge_21": 0.10,
        "avg_boxes": 10.0,
        "time_penalty": 100.0,
    }
    worse_deadlocks_but_better_rest = {
        "status": "ok",
        "deadlocks_stability": 1,
        "p95_boxes_per_pallet": 99.0,
        "pct_ge_21": 1.0,
        "avg_boxes": 99.0,
        "time_penalty": 0.0,
    }

    assert make_rank_key(better_deadlocks) < make_rank_key(worse_deadlocks_but_better_rest)
