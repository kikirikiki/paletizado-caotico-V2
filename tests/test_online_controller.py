from __future__ import annotations

from palca.control.controller import OnlineController
from palca.control.types import ControllerMode, DecisionContext


def _baseline_controller() -> OnlineController:
    return OnlineController(
        baseline_score_mode="min_height_slack_then_gain",
        baseline_height_slack_mm=80,
        baseline_micro_depth=6,
        baseline_micro_width=80,
        baseline_micro_topk=15,
        baseline_time_budget_ms=3000,
    )


def test_entra_rescue_con_dos_fallos_consecutivos() -> None:
    controller = _baseline_controller()

    controller.step(
        DecisionContext(last_ok=False, last_fail_reason="HEIGHT_LIMIT", consec_ok=0, consec_fail=1, pick_index=0)
    )
    overrides, event = controller.step(
        DecisionContext(last_ok=False, last_fail_reason="HEIGHT_LIMIT", consec_ok=0, consec_fail=2, pick_index=1)
    )

    assert controller.mode == ControllerMode.RESCUE
    assert overrides.to_dict().get("score_mode") == "min_height_then_gain"
    assert event is not None
    assert event.from_mode == ControllerMode.NORMAL
    assert event.to_mode == ControllerMode.RESCUE


def test_sale_rescue_a_normal_con_tres_exitos() -> None:
    controller = _baseline_controller()
    controller.step(
        DecisionContext(last_ok=False, last_fail_reason="HEIGHT_LIMIT", consec_ok=0, consec_fail=2, pick_index=0)
    )

    for pick_index in range(1, 6):
        controller.step(
            DecisionContext(last_ok=False, last_fail_reason="HEIGHT_LIMIT", consec_ok=0, consec_fail=2, pick_index=pick_index)
        )
    overrides, event = controller.step(
        DecisionContext(last_ok=True, last_fail_reason=None, consec_ok=3, consec_fail=0, pick_index=6)
    )

    assert controller.mode == ControllerMode.NORMAL
    assert overrides.to_dict() == {}
    assert event is not None
    assert event.from_mode == ControllerMode.RESCUE
    assert event.to_mode == ControllerMode.NORMAL


def test_push_dura_6_picks_y_respeta_cooldown_8() -> None:
    controller = _baseline_controller()

    for pick_index in range(0, 6):
        overrides, _event = controller.step(
            DecisionContext(last_ok=True, last_fail_reason=None, consec_ok=6 + pick_index, consec_fail=0, pick_index=pick_index)
        )
        assert controller.mode == ControllerMode.PUSH
        assert overrides.to_dict() == {"time_budget_ms": 3500}

    overrides_after_push, _event_after_push = controller.step(
        DecisionContext(last_ok=True, last_fail_reason=None, consec_ok=12, consec_fail=0, pick_index=6)
    )
    assert controller.mode == ControllerMode.NORMAL
    assert overrides_after_push.to_dict() == {}

    for pick_index in range(7, 14):
        overrides_cooldown, _event_cooldown = controller.step(
            DecisionContext(last_ok=True, last_fail_reason=None, consec_ok=12, consec_fail=0, pick_index=pick_index)
        )
        assert controller.mode == ControllerMode.NORMAL
        assert overrides_cooldown.to_dict() == {}

    overrides_reenter, _event_reenter = controller.step(
        DecisionContext(last_ok=True, last_fail_reason=None, consec_ok=12, consec_fail=0, pick_index=14)
    )
    assert controller.mode == ControllerMode.PUSH
    assert overrides_reenter.to_dict() == {"time_budget_ms": 3500}


def test_overrides_correctos_por_modo() -> None:
    controller_normal = _baseline_controller()
    overrides_normal, _event_normal = controller_normal.step(
        DecisionContext(last_ok=True, last_fail_reason=None, consec_ok=0, consec_fail=0, pick_index=0)
    )
    assert controller_normal.mode == ControllerMode.NORMAL
    assert overrides_normal.to_dict() == {}

    controller_push = _baseline_controller()
    overrides_push, _event_push = controller_push.step(
        DecisionContext(last_ok=True, last_fail_reason=None, consec_ok=6, consec_fail=0, pick_index=0)
    )
    assert controller_push.mode == ControllerMode.PUSH
    assert overrides_push.to_dict() == {"time_budget_ms": 3500}

    controller_rescue = _baseline_controller()
    overrides_rescue, _event_rescue = controller_rescue.step(
        DecisionContext(last_ok=False, last_fail_reason="HEIGHT_LIMIT", consec_ok=0, consec_fail=2, pick_index=0)
    )
    assert controller_rescue.mode == ControllerMode.RESCUE
    assert overrides_rescue.to_dict() == {
        "score_mode": "min_height_then_gain",
        "height_slack_mm": 0,
        "micro_width": 60,
        "micro_topk": 12,
        "time_budget_ms": 3000,
    }


def test_transitions_only_on_mode_change() -> None:
    controller = _baseline_controller()

    first_event = None
    for pick_index in range(0, 6):
        _overrides, event = controller.step(
            DecisionContext(last_ok=True, last_fail_reason=None, consec_ok=6 + pick_index, consec_fail=0, pick_index=pick_index)
        )
        if pick_index == 0:
            first_event = event
        else:
            assert event is None

    assert first_event is not None
    assert first_event.from_mode == ControllerMode.NORMAL
    assert first_event.to_mode == ControllerMode.PUSH


def test_anti_height_blocks_push() -> None:
    controller = _baseline_controller()

    overrides, event = controller.step(
        DecisionContext(
            last_ok=True,
            last_fail_reason=None,
            consec_ok=6,
            consec_fail=0,
            pick_index=0,
            height_margin_mm=150,
        )
    )

    assert controller.mode == ControllerMode.NORMAL
    assert event is None
    assert overrides.to_dict() == {
        "score_mode": "min_height_then_gain",
        "height_slack_mm": 0,
        "micro_depth": 8,
    }
    assert controller.anti_height_picks_total == 1
    assert controller.anti_height_entries_total == 1


def test_anti_height_only_when_margin_small() -> None:
    controller = _baseline_controller()

    overrides, _event = controller.step(
        DecisionContext(
            last_ok=True,
            last_fail_reason=None,
            consec_ok=6,
            consec_fail=0,
            pick_index=0,
            height_margin_mm=500,
        )
    )

    assert controller.mode == ControllerMode.PUSH
    assert overrides.to_dict() == {"time_budget_ms": 3500}
    assert controller.anti_height_picks_total == 0
    assert controller.anti_height_entries_total == 0
