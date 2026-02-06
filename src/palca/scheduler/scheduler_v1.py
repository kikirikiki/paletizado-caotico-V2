from __future__ import annotations

from dataclasses import dataclass, field
import inspect
import logging
import time
from typing import Any, Mapping, Sequence

from ..domain.box import Box
from ..domain.placement import PlacementPreview
from ..packer.pallet_model import PalletModel
from .costs import priority_bonus, selection_dt, starvation_penalty, time_penalty


@dataclass(frozen=True)
class SchedulerConfig:
    lookahead_k: int = 1
    pick_window: int | None = None
    t_select_base: float = 0.0
    t_select_step: float = 0.0
    time_penalty_weight: float = 1.0
    starvation_weight: float = 0.0
    time_budget_ms: int = 120
    priority_weight: float = 1.0
    max_tries_per_item: int = 0
    max_candidates: int = 0
    max_seconds_per_item: float = 0.0
    heartbeat_sec: float = 1.0


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
    ramp_sizes: Mapping[int, int] = field(default_factory=dict)
    remaining_total: int = 0


class SchedulerV1:
    def __init__(self, config: SchedulerConfig | None = None) -> None:
        self.config = config or SchedulerConfig()
        self.last_blocked_pallets: dict[int | str, str] = {}
        self.deadline_cutoffs_count = 0
        self.last_deadlock = False
        self.last_deadlock_item: dict[str, Any] | None = None
        self.last_eval_stats: dict[str, Any] = {}
        self._logger = logging.getLogger(__name__)

    def choose_action(self, sim_state: SchedulerSimState) -> PickPlan | None:
        self.last_blocked_pallets = {}
        self.last_deadlock = False
        self.last_deadlock_item = None
        self.last_eval_stats = {}
        lookahead_k = max(1, int(self.config.lookahead_k))
        pw = self.config.pick_window
        window = lookahead_k if (pw is None or int(pw) <= 0) else int(pw)

        deadline = None
        if self.config.time_budget_ms and self.config.time_budget_ms > 0:
            deadline = time.perf_counter() + (float(self.config.time_budget_ms) / 1000.0)

        heartbeat_sec = float(self.config.heartbeat_sec) if self.config.heartbeat_sec else 0.0
        next_heartbeat = time.perf_counter() + heartbeat_sec if heartbeat_sec > 0 else None

        best: PickPlan | None = None
        best_score = float("-inf")
        best_dt = float("inf")
        best_timestamp = float("inf")
        cutoff = False
        cutoff_reason = ""

        items_evaluated = 0
        items_feasible = 0
        deadlock_item: dict[str, Any] | None = None

        for ramp_id, ramp in sim_state.ramps.items():
            ramp_items = list(ramp)[:window]
            if deadline is not None and time.perf_counter() >= deadline:
                cutoff = True
                cutoff_reason = "time_budget"
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
                    cutoff_reason = "time_budget"
                    break
                if self.config.max_candidates and items_evaluated >= int(self.config.max_candidates):
                    cutoff = True
                    cutoff_reason = "max_candidates"
                    break
                pallet_id = box.destination
                if pallet_id is None:
                    continue
                if pallet_id in sim_state.pallet_blocked:
                    continue
                pallet = sim_state.pallets.get(pallet_id)
                if pallet is None:
                    continue

                items_evaluated += 1
                preview = self._preview_place(pallet, box)

                if next_heartbeat is not None and time.perf_counter() >= next_heartbeat:
                    dims = (
                        getattr(box, "length_mm", None),
                        getattr(box, "width_mm", None),
                        getattr(box, "height_mm", None),
                    )
                    n_placed = len(getattr(pallet, "placements", []) or [])
                    n_remaining = int(sim_state.remaining_total or 0)
                    free_rects = 0
                    free_area = 0
                    layers = len(getattr(pallet, "layers", []) or [])
                    height_mm = 0
                    if layers > 0:
                        active = pallet.layers[-1]
                        free_rects = len(getattr(active.bin, "free_rects", []) or [])
                        try:
                            free_area = int(active.bin.free_area())
                        except Exception:
                            free_area = 0
                        try:
                            height_mm = int(pallet.current_height_mm())
                        except Exception:
                            height_mm = 0
                    else:
                        free_rects = 1
                        try:
                            free_area = int(pallet.bin_area_mm2)
                        except Exception:
                            free_area = 0
                        height_mm = 0

                    print(
                        "[WATCHDOG] item_id=%s dims=%s n_placed=%s n_remaining=%s "
                        "n_candidates=%s free_rects=%s free_area_mm2=%s layers=%s height_mm=%s"
                        % (
                            getattr(box, "box_id", None),
                            dims,
                            n_placed,
                            n_remaining,
                            items_evaluated,
                            free_rects,
                            free_area,
                            layers,
                            height_mm,
                        ),
                        flush=True,
                    )
                    next_heartbeat = time.perf_counter() + heartbeat_sec

                if not preview.feasible:
                    if preview.infeasible_reason in ("NO_SPACE", "HEIGHT_LIMIT"):
                        self.last_blocked_pallets[pallet_id] = preview.infeasible_reason
                    else:
                        if deadlock_item is None:
                            deadlock_item = {
                                "box_id": getattr(box, "box_id", None),
                                "pallet_id": pallet_id,
                                "reason": preview.infeasible_reason,
                                "dims": (
                                    getattr(box, "length_mm", None),
                                    getattr(box, "width_mm", None),
                                    getattr(box, "height_mm", None),
                                ),
                            }
                    continue
                items_feasible += 1

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
            self._logger.info(
                "Scheduler cutoff (%s). budget_ms=%s max_candidates=%s.",
                cutoff_reason,
                self.config.time_budget_ms,
                self.config.max_candidates,
            )

        self.last_eval_stats = {
            "items_evaluated": int(items_evaluated),
            "items_feasible": int(items_feasible),
            "cutoff": bool(cutoff),
            "cutoff_reason": cutoff_reason,
        }

        if items_evaluated > 0 and items_feasible == 0 and not cutoff and not self.last_blocked_pallets:
            self.last_deadlock = True
            self.last_deadlock_item = deadlock_item or {
                "box_id": None,
                "pallet_id": None,
                "reason": "NO_FEASIBLE_PLACEMENT",
                "dims": None,
            }

        return best

    def _preview_place(self, pallet: PalletModel, box: Box) -> PlacementPreview:
        kwargs: dict[str, object] = {}
        max_tries = int(self.config.max_tries_per_item) if self.config.max_tries_per_item else 0
        max_seconds = float(self.config.max_seconds_per_item) if self.config.max_seconds_per_item else 0.0
        if max_tries > 0:
            kwargs["max_tries_per_item"] = max_tries
        if max_seconds > 0:
            kwargs["max_seconds_per_item"] = max_seconds

        preview_fn = pallet.preview_place
        if not kwargs:
            return preview_fn(box)

        filtered = self._filter_preview_kwargs(preview_fn, kwargs)
        if not filtered:
            return preview_fn(box)

        try:
            return preview_fn(box, **filtered)
        except TypeError as exc:
            if self._is_unexpected_kwarg(exc):
                return preview_fn(box)
            raise

    @staticmethod
    def _filter_preview_kwargs(preview_fn: object, kwargs: dict[str, object]) -> dict[str, object]:
        try:
            sig = inspect.signature(preview_fn)
        except (TypeError, ValueError):
            return dict(kwargs)

        params = sig.parameters.values()
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
            return dict(kwargs)

        accepted = {p.name for p in params}
        return {k: v for k, v in kwargs.items() if k in accepted}

    @staticmethod
    def _is_unexpected_kwarg(exc: TypeError) -> bool:
        msg = str(exc)
        return "unexpected keyword argument" in msg or "got an unexpected keyword argument" in msg
