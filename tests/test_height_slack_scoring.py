from __future__ import annotations

from dataclasses import dataclass

from palca.scoring.height_slack import (
    ScoreMode,
    choose_with_height_slack,
    rank_for_expansion_with_height_buckets,
)


@dataclass(frozen=True)
class DummyCandidate:
    name: str
    height_after_mm: int
    gain_frag_sort_key: tuple[float, ...]


def _height_after(candidate: DummyCandidate) -> int:
    return int(candidate.height_after_mm)


def _gain_key(candidate: DummyCandidate) -> tuple[float, ...]:
    return candidate.gain_frag_sort_key


def test_choose_with_height_slack_prefers_gain_inside_slack_set() -> None:
    candidates = [
        DummyCandidate(name="min_height", height_after_mm=1000, gain_frag_sort_key=(1.00,)),
        DummyCandidate(name="best_gain_inside_slack", height_after_mm=1060, gain_frag_sort_key=(1.40,)),
        DummyCandidate(name="best_gain_outside_slack", height_after_mm=1120, gain_frag_sort_key=(9.00,)),
    ]

    selected, stats = choose_with_height_slack(
        candidates=candidates,
        score_mode=ScoreMode.MIN_HEIGHT_SLACK_THEN_GAIN,
        height_slack_mm=80,
        height_after_mm_fn=_height_after,
        gain_frag_key_fn=_gain_key,
    )

    assert selected is not None
    assert selected.name == "best_gain_inside_slack"
    assert stats.min_height_after_mm == 1000
    assert stats.slack_set_n == 2
    assert stats.slack_set_used is True


def test_slack_zero_matches_min_height_then_gain_pool() -> None:
    candidates = [
        DummyCandidate(name="min_height_low_gain", height_after_mm=900, gain_frag_sort_key=(0.8,)),
        DummyCandidate(name="min_height_high_gain", height_after_mm=900, gain_frag_sort_key=(1.2,)),
        DummyCandidate(name="higher_height_best_gain", height_after_mm=980, gain_frag_sort_key=(9.9,)),
    ]

    selected_min_height, _stats_min = choose_with_height_slack(
        candidates=candidates,
        score_mode=ScoreMode.MIN_HEIGHT_THEN_GAIN,
        height_slack_mm=0,
        height_after_mm_fn=_height_after,
        gain_frag_key_fn=_gain_key,
    )
    selected_slack_zero, stats_slack_zero = choose_with_height_slack(
        candidates=candidates,
        score_mode=ScoreMode.MIN_HEIGHT_SLACK_THEN_GAIN,
        height_slack_mm=0,
        height_after_mm_fn=_height_after,
        gain_frag_key_fn=_gain_key,
    )

    assert selected_min_height is not None
    assert selected_slack_zero is not None
    assert selected_min_height.name == "min_height_high_gain"
    assert selected_slack_zero.name == selected_min_height.name
    assert stats_slack_zero.slack_set_n == 2


def test_rank_for_expansion_with_height_buckets_orders_by_bucket_then_gain() -> None:
    candidates = [
        DummyCandidate(name="h1000_g1", height_after_mm=1000, gain_frag_sort_key=(1.0,)),
        DummyCandidate(name="h1040_g10", height_after_mm=1040, gain_frag_sort_key=(10.0,)),
        DummyCandidate(name="h1120_g5", height_after_mm=1120, gain_frag_sort_key=(5.0,)),
        DummyCandidate(name="h1300_g100", height_after_mm=1300, gain_frag_sort_key=(100.0,)),
    ]

    ordered = rank_for_expansion_with_height_buckets(
        candidates=candidates,
        height_slack_mm=320,
        height_bucket_mm=80,
        height_after_mm_fn=_height_after,
        gain_frag_key_fn=_gain_key,
    )

    assert [candidate.name for candidate in ordered] == [
        "h1040_g10",
        "h1000_g1",
        "h1120_g5",
        "h1300_g100",
    ]


def test_rank_for_expansion_with_height_buckets_slack_zero_only_min_height() -> None:
    candidates = [
        DummyCandidate(name="h1000_g1", height_after_mm=1000, gain_frag_sort_key=(1.0,)),
        DummyCandidate(name="h1040_g10", height_after_mm=1040, gain_frag_sort_key=(10.0,)),
        DummyCandidate(name="h1120_g5", height_after_mm=1120, gain_frag_sort_key=(5.0,)),
        DummyCandidate(name="h1300_g100", height_after_mm=1300, gain_frag_sort_key=(100.0,)),
    ]

    ordered = rank_for_expansion_with_height_buckets(
        candidates=candidates,
        height_slack_mm=0,
        height_bucket_mm=80,
        height_after_mm_fn=_height_after,
        gain_frag_key_fn=_gain_key,
    )

    assert [candidate.name for candidate in ordered] == ["h1000_g1"]
