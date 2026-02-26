from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Sequence, TypeVar


T = TypeVar("T")


class ScoreMode(str, Enum):
    GAIN_FRAG = "gain_frag"
    MIN_HEIGHT_THEN_GAIN = "min_height_then_gain"
    MIN_HEIGHT_SLACK_THEN_GAIN = "min_height_slack_then_gain"

    @classmethod
    def parse(cls, value: str | "ScoreMode" | None) -> "ScoreMode":
        if isinstance(value, cls):
            return value
        normalized = str(value or cls.GAIN_FRAG.value).strip().lower()
        return cls(normalized)


@dataclass(frozen=True)
class SlackDecisionStats:
    feasible_n: int
    min_height_after_mm: int | None
    slack_set_n: int
    slack_set_used: bool
    slack_mm: int
    score_mode: str


def choose_with_height_slack(
    candidates: Sequence[T],
    score_mode: str | ScoreMode,
    height_slack_mm: int,
    height_after_mm_fn: Callable[[T], int],
    gain_frag_key_fn: Callable[[T], Any],
) -> tuple[T | None, SlackDecisionStats]:
    selected, _ordered, stats = _choose_and_rank_candidates(
        candidates=candidates,
        score_mode=score_mode,
        height_slack_mm=height_slack_mm,
        height_after_mm_fn=height_after_mm_fn,
        gain_frag_key_fn=gain_frag_key_fn,
    )
    return selected, stats


def rank_for_expansion_with_height_slack(
    candidates: Sequence[T],
    score_mode: str | ScoreMode,
    height_slack_mm: int,
    height_after_mm_fn: Callable[[T], int],
    gain_frag_key_fn: Callable[[T], Any],
) -> list[T]:
    _selected, ordered, _stats = _choose_and_rank_candidates(
        candidates=candidates,
        score_mode=score_mode,
        height_slack_mm=height_slack_mm,
        height_after_mm_fn=height_after_mm_fn,
        gain_frag_key_fn=gain_frag_key_fn,
    )
    return ordered


def rank_for_expansion_with_height_buckets(
    candidates: Sequence[T],
    height_slack_mm: int,
    height_bucket_mm: int,
    height_after_mm_fn: Callable[[T], int],
    gain_frag_key_fn: Callable[[T], Any],
) -> list[T]:
    feasible = list(candidates)
    if not feasible:
        return []

    slack_mm = max(0, int(height_slack_mm))
    bucket_mm = max(1, int(height_bucket_mm))

    indexed = [
        (idx, candidate, int(height_after_mm_fn(candidate)), gain_frag_key_fn(candidate))
        for idx, candidate in enumerate(feasible)
    ]
    min_height_after_mm = min(height for _idx, _candidate, height, _gain_key in indexed)
    slack_limit = int(min_height_after_mm) + int(slack_mm)

    eligible = [
        (
            idx,
            candidate,
            (int(height) - int(min_height_after_mm)) // int(bucket_mm),
            gain_key,
        )
        for idx, candidate, height, gain_key in indexed
        if int(height) <= int(slack_limit)
    ]

    # Orden determinista: bucket asc, gain_frag desc, luego índice original.
    eligible.sort(key=lambda rec: int(rec[0]))
    eligible.sort(key=lambda rec: rec[3], reverse=True)
    eligible.sort(key=lambda rec: int(rec[2]))
    return [candidate for _idx, candidate, _bucket, _gain_key in eligible]


def _choose_and_rank_candidates(
    *,
    candidates: Sequence[T],
    score_mode: str | ScoreMode,
    height_slack_mm: int,
    height_after_mm_fn: Callable[[T], int],
    gain_frag_key_fn: Callable[[T], Any],
) -> tuple[T | None, list[T], SlackDecisionStats]:
    mode = ScoreMode.parse(score_mode)
    feasible = list(candidates)
    slack_mm = max(0, int(height_slack_mm))

    if not feasible:
        stats = SlackDecisionStats(
            feasible_n=0,
            min_height_after_mm=None,
            slack_set_n=0,
            slack_set_used=False,
            slack_mm=slack_mm,
            score_mode=mode.value,
        )
        return None, [], stats

    heights = [int(height_after_mm_fn(candidate)) for candidate in feasible]
    min_height_after_mm = min(heights)
    slack_limit = int(min_height_after_mm) + int(slack_mm)

    slack_set = [candidate for candidate, height in zip(feasible, heights) if int(height) <= slack_limit]
    min_height_set = [candidate for candidate, height in zip(feasible, heights) if int(height) == int(min_height_after_mm)]

    if mode == ScoreMode.MIN_HEIGHT_THEN_GAIN:
        eligible = min_height_set
    elif mode == ScoreMode.MIN_HEIGHT_SLACK_THEN_GAIN:
        eligible = slack_set
    else:
        eligible = feasible

    ordered = sorted(eligible, key=gain_frag_key_fn, reverse=True)
    selected = ordered[0] if ordered else None

    stats = SlackDecisionStats(
        feasible_n=len(feasible),
        min_height_after_mm=int(min_height_after_mm),
        slack_set_n=len(slack_set),
        slack_set_used=(mode == ScoreMode.MIN_HEIGHT_SLACK_THEN_GAIN and bool(slack_set)),
        slack_mm=slack_mm,
        score_mode=mode.value,
    )
    return selected, ordered, stats
