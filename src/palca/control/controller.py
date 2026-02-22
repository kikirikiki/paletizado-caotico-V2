from __future__ import annotations

from .types import ControllerEvent, ControllerMode, DecisionContext, Overrides


class OnlineController:
    def __init__(
        self,
        *,
        baseline_score_mode: str,
        baseline_height_slack_mm: int,
        baseline_micro_depth: int,
        baseline_micro_width: int,
        baseline_micro_topk: int,
        baseline_time_budget_ms: int,
        push_time_budget_ms: int = 3500,
        rescue_score_mode: str = "min_height_then_gain",
        rescue_height_slack_mm: int = 0,
        rescue_micro_width: int = 60,
        rescue_micro_topk: int = 12,
        rescue_time_budget_ms: int | None = None,
        push_picks: int = 6,
        cooldown_picks: int = 8,
        rescue_min_picks: int = 6,
        rescue_exit_ok: int = 3,
        enter_rescue_fail: int = 2,
        enter_push_ok: int = 6,
        allow_rescue_early_exit: bool = True,
    ) -> None:
        self.mode = ControllerMode.NORMAL
        self.push_picks = max(1, int(push_picks))
        self.cooldown_picks = max(0, int(cooldown_picks))
        self.rescue_min_picks = max(1, int(rescue_min_picks))
        self.rescue_exit_ok = max(1, int(rescue_exit_ok))
        self.enter_rescue_fail = max(1, int(enter_rescue_fail))
        self.enter_push_ok = max(1, int(enter_push_ok))
        self.allow_rescue_early_exit = bool(allow_rescue_early_exit)

        self.push_remaining = 0
        self.cooldown_remaining = 0
        self.rescue_picks = 0
        self.rescue_ok_streak = 0

        if rescue_time_budget_ms is None:
            # Si la base ya usa 3000ms (tuning estable), mantener 3000 en RESCUE
            # evita introducir una agresividad extra no validada para anti-deadlock.
            rescue_time_budget_ms = 3000 if int(baseline_time_budget_ms) >= 3000 else 2500

        self._baseline = {
            "score_mode": str(baseline_score_mode),
            "height_slack_mm": int(baseline_height_slack_mm),
            "micro_depth": int(baseline_micro_depth),
            "micro_width": int(baseline_micro_width),
            "micro_topk": int(baseline_micro_topk),
            "time_budget_ms": int(baseline_time_budget_ms),
        }
        self._push_time_budget_ms = int(push_time_budget_ms)
        self._rescue_values = {
            "score_mode": str(rescue_score_mode),
            "height_slack_mm": int(rescue_height_slack_mm),
            "micro_width": int(rescue_micro_width),
            "micro_topk": int(rescue_micro_topk),
            "time_budget_ms": int(rescue_time_budget_ms),
        }

    def step(self, ctx: DecisionContext) -> tuple[Overrides, ControllerEvent | None]:
        from_mode = self.mode
        trigger: str | None = None
        note: str | None = None

        if self.mode == ControllerMode.PUSH and self.push_remaining <= 0:
            self.mode = ControllerMode.NORMAL
            self.cooldown_remaining = max(self.cooldown_remaining, self.cooldown_picks)
            trigger = f"push_window_done:{self.push_picks}"
            note = f"cooldown={self.cooldown_remaining}"

        if self.mode != ControllerMode.RESCUE and int(ctx.consec_fail) >= self.enter_rescue_fail:
            self.mode = ControllerMode.RESCUE
            self.rescue_picks = 0
            self.rescue_ok_streak = 0
            self.push_remaining = 0
            trigger = f"enter_rescue_consec_fail>={self.enter_rescue_fail}"
        elif self.mode == ControllerMode.RESCUE:
            self.rescue_ok_streak = int(ctx.consec_ok) if bool(ctx.last_ok) else 0
            can_exit_rescue = self.rescue_ok_streak >= self.rescue_exit_ok and (
                self.allow_rescue_early_exit or self.rescue_picks >= self.rescue_min_picks
            )
            if can_exit_rescue:
                self.mode = ControllerMode.NORMAL
                self.rescue_ok_streak = 0
                self.rescue_picks = 0
                trigger = f"exit_rescue_consec_ok>={self.rescue_exit_ok}"
        elif self.mode == ControllerMode.NORMAL:
            if self.cooldown_remaining <= 0 and int(ctx.consec_ok) >= self.enter_push_ok:
                self.mode = ControllerMode.PUSH
                self.push_remaining = self.push_picks
                trigger = f"enter_push_consec_ok>={self.enter_push_ok}"

        overrides = self._overrides_for_mode(self.mode)
        changed_mode = self.mode != from_mode

        event: ControllerEvent | None = None
        if changed_mode:
            event = ControllerEvent(
                pick_index=int(ctx.pick_index),
                from_mode=from_mode,
                to_mode=self.mode,
                trigger=trigger or "mode_changed",
                overrides=dict(overrides.to_dict()),
                note=note,
            )

        if self.mode == ControllerMode.PUSH:
            self.push_remaining = max(0, int(self.push_remaining) - 1)
            if self.push_remaining <= 0:
                self.cooldown_remaining = max(self.cooldown_remaining, self.cooldown_picks)
        elif self.mode == ControllerMode.NORMAL:
            if self.cooldown_remaining > 0:
                self.cooldown_remaining = max(0, int(self.cooldown_remaining) - 1)
        elif self.mode == ControllerMode.RESCUE:
            self.rescue_picks += 1

        return overrides, event

    def overrides_for_mode(self, mode: ControllerMode) -> Overrides:
        return self._overrides_for_mode(mode)

    def _overrides_for_mode(self, mode: ControllerMode) -> Overrides:
        if mode == ControllerMode.PUSH:
            return Overrides(time_budget_ms=int(self._push_time_budget_ms))
        if mode == ControllerMode.RESCUE:
            return Overrides(
                score_mode=str(self._rescue_values["score_mode"]),
                height_slack_mm=int(self._rescue_values["height_slack_mm"]),
                micro_width=int(self._rescue_values["micro_width"]),
                micro_topk=int(self._rescue_values["micro_topk"]),
                time_budget_ms=int(self._rescue_values["time_budget_ms"]),
            )
        return Overrides()
