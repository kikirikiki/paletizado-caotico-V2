from __future__ import annotations

from types import SimpleNamespace

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


def test_choose_action_height_limit_skips_retry_attempt_2(monkeypatch) -> None:
    policy = PolicyPackerScheduler.from_defaults(online_controller=True, controller_debug=True)
    attempt_count = 0

    def _run_attempt(*, sim_state, overrides):
        nonlocal attempt_count
        attempt_count += 1
        return None, "HEIGHT_LIMIT", {}, "DEADLOCK", {"reason": "HEIGHT_LIMIT"}

    monkeypatch.setattr(policy, "_run_scheduler_attempt", _run_attempt)

    plan = policy.choose_action(
        ramps={1: SimpleNamespace(queue=[], upstream=[], staging=[], capacity=0)},
        destinations={},
        now=0.0,
    )

    assert plan is None
    assert attempt_count == 1
    metrics = policy.collect_controller_metrics()
    assert metrics["retry_attempts_total"] == 0
    assert metrics["retry_fail_total"] == 0
    assert metrics["retry_by_reason"] == {}
    assert metrics["retry_skipped_by_reason"] == {"HEIGHT_LIMIT": 1}
    assert metrics["consecutive_failures_max"] == 0

    debug_events = metrics.get("debug_events")
    assert isinstance(debug_events, list)
    assert [event["attempt_index"] for event in debug_events] == [1]


def test_choose_action_stability_runs_retry_attempt_2(monkeypatch) -> None:
    policy = PolicyPackerScheduler.from_defaults(online_controller=True, controller_debug=True)
    attempt_count = 0

    def _run_attempt(*, sim_state, overrides):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            return None, "STABILITY", {}, "DEADLOCK", {"reason": "STABILITY"}
        return SimpleNamespace(buffer_index=0, dt_extra=0.0), None, {}, None, {}

    monkeypatch.setattr(policy, "_run_scheduler_attempt", _run_attempt)

    plan = policy.choose_action(
        ramps={1: SimpleNamespace(queue=[], upstream=[], staging=[], capacity=0)},
        destinations={},
        now=0.0,
    )

    assert plan is not None
    assert attempt_count == 2
    metrics = policy.collect_controller_metrics()
    assert metrics["retry_attempts_total"] == 1
    assert metrics["retry_success_total"] == 1
    assert metrics["retry_fail_total"] == 0
    assert metrics["retry_by_reason"] == {"STABILITY": 1}
    assert metrics["retry_skipped_by_reason"] == {}

    debug_events = metrics.get("debug_events")
    assert isinstance(debug_events, list)
    assert [event["attempt_index"] for event in debug_events] == [1, 2]
