from __future__ import annotations

from dataclasses import dataclass

from palca.scoring.height_slack import ScoreMode, choose_with_height_slack


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
