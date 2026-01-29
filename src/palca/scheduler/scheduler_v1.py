from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Mapping, Sequence

from ..domain.box import Box
from ..domain.placement import PlacementPreview
from ..packer.pallet_model import PalletModel
from .costs import priority_bonus, selection_dt, starvation_penalty, time_penalty


@dataclass(frozen=True)
class SchedulerConfig:
    lookahead_k: int = 1
    t_select_base: float = 0.0
    t_select_step: float = 0.0
    time_penalty_weight: float = 1.0
    starvation_weight: float = 0.0
    time_budget_ms: int = 120
    priority_weight: float = 1.0


@dataclass(frozen=True)
class PickPlan:
    ramp_id: int
    buffer_index: int
    box_id: int | str
    pallet_id: int | str
    preview: PlacementPreview
    score: float
    dt_extra: float


@dataclass(frozen=True)
class SchedulerSimState:
    now: float
    ramps: Mapping[int, Sequence[Box]]
    pallets: Mapping[int | str, PalletModel]
    pallet_blocked: set[int | str]


class SchedulerV1:
    def __init__(self, config: SchedulerConfig | None = None) -> None:
        self.config = config or SchedulerConfig()
        self.last_blocked_pallets: dict[int | str, str] = {}
        self.deadline_cutoffs_count = 0
        self._logger = logging.getLogger(__name__)

    def choose_action(self, sim_state: SchedulerSimState) -> PickPlan | None:
        self.last_blocked_pallets = {}
        k = max(1, int(self.config.lookahead_k))

        deadline = None
        if self.config.time_budget_ms and self.config.time_budget_ms > 0:
            deadline = time.perf_counter() + (float(self.config.time_budget_ms) / 1000.0)

        best: PickPlan | None = None
        best_score = float("-inf")
        best_dt = float("inf")
        best_timestamp = float("inf")
        cutoff = False

        for ramp_id, ramp in sim_state.ramps.items():
            ramp_items = list(ramp)[:k]
            if deadline is not None and time.perf_counter() >= deadline:
                cutoff = True
                break

            max_priority = 0.0
            for item in ramp_items:
                try:
                    max_priority = max(max_priority, float(getattr(item, "priority", 0.0) or 0.0))
                except Exception:
                    continue

            for idx, box in enumerate(ramp_items):
                if deadline is not None and time.perf_counter() >= deadline:
                    cutoff = True
                    break
                pallet_id = box.destination
                if pallet_id is None:
                    continue
                if pallet_id in sim_state.pallet_blocked:
                    continue
                pallet = sim_state.pallets.get(pallet_id)
                if pallet is None:
                    continue

                preview = pallet.preview_place(box)
                if not preview.feasible:
                    if preview.infeasible_reason in ("NO_SPACE", "HEIGHT_LIMIT"):
                        self.last_blocked_pallets[pallet_id] = preview.infeasible_reason
                    continue

                dt_extra = selection_dt(idx, self.config.t_select_base, self.config.t_select_step)
                time_cost = time_penalty(dt_extra, self.config.time_penalty_weight)
                age = max(0.0, float(sim_state.now) - float(box.timestamp))
                starv_cost = starvation_penalty(age, self.config.starvation_weight)
                priority_val = float(getattr(box, "priority", 0.0) or 0.0)
                if max_priority > 0:
                    priority_norm = priority_val / max_priority
                else:
                    priority_norm = 0.0
                priority_score = priority_bonus(priority_norm, self.config.priority_weight)

                score = (
                    float(preview.packing_gain)
                    - float(preview.fragmentation)
                    + float(getattr(preview, "score_adjustment", 0.0) or 0.0)
                    - time_cost
                    - starv_cost
                    + priority_score
                )

                if (
                    score > best_score
                    or (
                        score == best_score
                        and (dt_extra < best_dt or (dt_extra == best_dt and box.timestamp < best_timestamp))
                    )
                ):
                    best_score = score
                    best_dt = dt_extra
                    best_timestamp = box.timestamp
                    best = PickPlan(
                        ramp_id=int(ramp_id),
                        buffer_index=int(idx),
                        box_id=box.box_id,
                        pallet_id=pallet_id,
                        preview=preview,
                        score=score,
                        dt_extra=dt_extra,
                    )

            if cutoff:
                break

        if cutoff:
            self.deadline_cutoffs_count += 1
            self._logger.info("Scheduler time budget hit (budget_ms=%s).", self.config.time_budget_ms)

        return best
