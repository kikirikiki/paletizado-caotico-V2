from __future__ import annotations

from dataclasses import dataclass
import logging
import math
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class PackEval:
    fits: bool
    packing_score: float
    cycle_time_s: float | None = None


@dataclass(frozen=True)
class PalletSlot:
    state: Any
    blocked: bool = False


@dataclass(frozen=True)
class SchedulerConfig:
    blocked_score: float = -1.0e9
    no_fit_score: float = -1.0e9
    empty_score: float = -math.inf
    time_penalty_weight: float = 1.0
    starvation_penalty_per_step: float = 1.0
    starvation_threshold_steps: int = 0


@dataclass(frozen=True)
class CandidateDecision:
    ramp_id: str
    case_id: str | None
    pallet_id: str | None
    blocked: bool
    fits: bool
    packing_score: float | None
    time_penalty: float
    starvation_penalty: float
    total_score: float
    reason: str


@dataclass(frozen=True)
class Decision:
    step: int
    chosen_ramp: str | None
    candidates: tuple[CandidateDecision, CandidateDecision]


class RampScheduler:
    def __init__(
        self,
        packer_fn: Callable[[Any, Any], PackEval],
        config: SchedulerConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._packer_fn = packer_fn
        self._config = config or SchedulerConfig()
        self._logger = logger or logging.getLogger(__name__)
        self._waiting_steps: dict[str, int] = {"rampa1": 0, "rampa2": 0}
        self._step = 0

    def choose(
        self,
        ramp1: Sequence[Any],
        ramp2: Sequence[Any],
        pallets: Mapping[str, PalletSlot],
    ) -> Decision:
        self._step += 1

        c1 = self._score_candidate(
            ramp_id="rampa1",
            ramp=ramp1,
            pallets=pallets,
            other_waiting=self._waiting_steps["rampa2"],
            other_has_boxes=bool(ramp2),
        )
        c2 = self._score_candidate(
            ramp_id="rampa2",
            ramp=ramp2,
            pallets=pallets,
            other_waiting=self._waiting_steps["rampa1"],
            other_has_boxes=bool(ramp1),
        )

        chosen = self._pick_best(c1, c2)
        self._update_waiting(chosen, ramp1, ramp2)

        decision = Decision(step=self._step, chosen_ramp=chosen, candidates=(c1, c2))
        self._log_decision(decision)
        return decision

    def _pick_best(self, c1: CandidateDecision, c2: CandidateDecision) -> str | None:
        if c1.total_score == self._config.empty_score and c2.total_score == self._config.empty_score:
            return None
        if c2.total_score > c1.total_score:
            return "rampa2"
        return "rampa1"

    def _score_candidate(
        self,
        ramp_id: str,
        ramp: Sequence[Any],
        pallets: Mapping[str, PalletSlot],
        other_waiting: int,
        other_has_boxes: bool,
    ) -> CandidateDecision:
        if not ramp:
            return CandidateDecision(
                ramp_id=ramp_id,
                case_id=None,
                pallet_id=None,
                blocked=False,
                fits=False,
                packing_score=None,
                time_penalty=0.0,
                starvation_penalty=0.0,
                total_score=self._config.empty_score,
                reason="empty",
            )

        case = ramp[0]
        case_id = _case_id(case)
        pallet_id = _pallet_dest(case)
        if pallet_id is None:
            return CandidateDecision(
                ramp_id=ramp_id,
                case_id=case_id,
                pallet_id=None,
                blocked=True,
                fits=False,
                packing_score=None,
                time_penalty=0.0,
                starvation_penalty=0.0,
                total_score=self._config.blocked_score,
                reason="missing_pallet_dest",
            )

        pallet_slot = pallets.get(pallet_id)
        if pallet_slot is None:
            return CandidateDecision(
                ramp_id=ramp_id,
                case_id=case_id,
                pallet_id=pallet_id,
                blocked=True,
                fits=False,
                packing_score=None,
                time_penalty=0.0,
                starvation_penalty=0.0,
                total_score=self._config.blocked_score,
                reason="pallet_unknown",
            )

        if pallet_slot.blocked:
            return CandidateDecision(
                ramp_id=ramp_id,
                case_id=case_id,
                pallet_id=pallet_id,
                blocked=True,
                fits=False,
                packing_score=None,
                time_penalty=0.0,
                starvation_penalty=0.0,
                total_score=self._config.blocked_score,
                reason="pallet_blocked",
            )

        pack_eval = self._safe_pack(case, pallet_slot.state, ramp_id, pallet_id)
        if pack_eval is None or not pack_eval.fits or not _is_finite(pack_eval.packing_score):
            return CandidateDecision(
                ramp_id=ramp_id,
                case_id=case_id,
                pallet_id=pallet_id,
                blocked=False,
                fits=False,
                packing_score=None,
                time_penalty=0.0,
                starvation_penalty=0.0,
                total_score=self._config.no_fit_score,
                reason="no_fit",
            )

        time_penalty = self._config.time_penalty_weight * float(pack_eval.cycle_time_s or 0.0)
        starvation_penalty = self._starvation_penalty(other_waiting, other_has_boxes)
        total_score = float(pack_eval.packing_score) - time_penalty - starvation_penalty

        return CandidateDecision(
            ramp_id=ramp_id,
            case_id=case_id,
            pallet_id=pallet_id,
            blocked=False,
            fits=True,
            packing_score=float(pack_eval.packing_score),
            time_penalty=time_penalty,
            starvation_penalty=starvation_penalty,
            total_score=total_score,
            reason="ok",
        )

    def _safe_pack(self, case: Any, pallet_state: Any, ramp_id: str, pallet_id: str) -> PackEval | None:
        try:
            return self._packer_fn(case, pallet_state)
        except Exception:
            self._logger.exception(
                "scheduler packer error ramp=%s pallet=%s case=%s",
                ramp_id,
                pallet_id,
                _case_id(case),
            )
            return None

    def _starvation_penalty(self, other_waiting: int, other_has_boxes: bool) -> float:
        if not other_has_boxes:
            return 0.0
        extra = max(0, int(other_waiting) - int(self._config.starvation_threshold_steps))
        return float(extra) * float(self._config.starvation_penalty_per_step)

    def _update_waiting(self, chosen: str | None, ramp1: Sequence[Any], ramp2: Sequence[Any]) -> None:
        for ramp_id, ramp in (("rampa1", ramp1), ("rampa2", ramp2)):
            if not ramp:
                self._waiting_steps[ramp_id] = 0
                continue
            if ramp_id == chosen:
                self._waiting_steps[ramp_id] = 0
            else:
                self._waiting_steps[ramp_id] = int(self._waiting_steps[ramp_id]) + 1

    def _log_decision(self, decision: Decision) -> None:
        for cand in decision.candidates:
            self._logger.info(
                "scheduler candidate step=%s ramp=%s case=%s pallet=%s blocked=%s fits=%s "
                "packing_score=%s time_penalty=%.3f starvation_penalty=%.3f total_score=%.3f reason=%s",
                decision.step,
                cand.ramp_id,
                cand.case_id,
                cand.pallet_id,
                cand.blocked,
                cand.fits,
                cand.packing_score,
                cand.time_penalty,
                cand.starvation_penalty,
                cand.total_score,
                cand.reason,
            )
        self._logger.info(
            "scheduler choice step=%s chosen=%s",
            decision.step,
            decision.chosen_ramp,
        )


def _case_id(case: Any) -> str | None:
    if isinstance(case, dict):
        for key in ("cid", "case_id", "box_id", "id"):
            if key in case and case[key] is not None:
                return str(case[key])
        return None
    for attr in ("cid", "case_id", "box_id", "id"):
        if hasattr(case, attr):
            value = getattr(case, attr)
            if value is not None:
                return str(value)
    return None


def _pallet_dest(case: Any) -> str | None:
    if isinstance(case, dict):
        for key in ("pallet_dest", "dest", "destino", "destination"):
            if key in case and case[key] is not None:
                return str(case[key])
        return None
    for attr in ("pallet_dest", "dest", "destino", "destination"):
        if hasattr(case, attr):
            value = getattr(case, attr)
            if value is not None:
                return str(value)
    return None


def _is_finite(value: float | None) -> bool:
    if value is None:
        return False
    return math.isfinite(float(value))
