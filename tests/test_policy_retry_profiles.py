from __future__ import annotations

from palca.control.types import Overrides
from palca.integration.policy_packer_sched import PolicyPackerScheduler


def test_retry_overrides_height_limit_uses_l2_h_and_burst_budget() -> None:
    overrides, profile = PolicyPackerScheduler._build_retry_overrides(
        retry_reason="HEIGHT_LIMIT",
        attempt1_effective_budget_ms=3500,
        fallback_overrides=Overrides(time_budget_ms=3000),
    )

    assert profile == "L2-H"
    assert overrides.to_dict() == {
        "score_mode": "min_height_then_gain",
        "height_slack_mm": 0,
        "micro_depth": 8,
        "micro_width": 140,
        "micro_topk": 25,
        "time_budget_ms": 4500,
    }


def test_retry_overrides_stability_uses_l2_s_and_burst_budget() -> None:
    overrides, profile = PolicyPackerScheduler._build_retry_overrides(
        retry_reason="STABILITY",
        attempt1_effective_budget_ms=3500,
        fallback_overrides=Overrides(time_budget_ms=3000),
    )

    assert profile == "L2-S"
    assert overrides.to_dict() == {
        "score_mode": "min_height_slack_then_gain",
        "height_slack_mm": 80,
        "micro_depth": 8,
        "micro_width": 160,
        "micro_topk": 30,
        "time_budget_ms": 4500,
    }
