from __future__ import annotations

import copy
from dataclasses import dataclass, field
import inspect
import logging
import time
from typing import Any, Mapping, Sequence

from ..domain.box import Box
from ..domain.placement import PlacementPreview
from ..packer.pallet_model import PalletModel
from ..scoring.height_slack import (
    ScoreMode,
    SlackDecisionStats,
    choose_with_height_slack,
    rank_for_expansion_with_height_slack,
)
from .costs import priority_bonus, selection_dt, starvation_penalty, time_penalty


ALLOWED_SCORE_MODES = tuple(mode.value for mode in ScoreMode)


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
    score_mode: str = "gain_frag"
    height_slack_mm: int = 0
    z_band_mm: int | None = None
    micro_plan_enabled: bool = False
    micro_plan_depth: int = 3
    micro_plan_width: int = 8
    micro_plan_topk_per_step: int = 15
    micro_plan_window_total: int = 15
    micro_plan_window_strategy: str = "fifo_ramp"
    batchfill_layer_starter: bool = False
    batchfill_starters_max: int = 6
    batchfill_budget_ms: int = 150
    batchfill_greedy_topk: int = 12

    def __post_init__(self) -> None:
        lookahead = max(1, int(self.lookahead_k))
        alias = self.pick_window
        if alias is not None:
            alias = max(1, int(alias))
            # Compatibilidad retroactiva:
            # - pick_window se mapea a lookahead_k.
            # - si lookahead_k viene no-default y contradice pick_window, se rechaza.
            if lookahead != 1 and lookahead != alias:
                raise ValueError("SchedulerConfig conflict: lookahead_k and pick_window differ")
            lookahead = alias
        object.__setattr__(self, "lookahead_k", lookahead)
        object.__setattr__(self, "pick_window", alias)
        object.__setattr__(self, "micro_plan_depth", max(1, int(self.micro_plan_depth)))
        object.__setattr__(self, "micro_plan_width", max(1, int(self.micro_plan_width)))
        object.__setattr__(self, "micro_plan_topk_per_step", max(1, int(self.micro_plan_topk_per_step)))
        object.__setattr__(self, "micro_plan_window_total", int(self.micro_plan_window_total))
        object.__setattr__(self, "batchfill_starters_max", max(1, int(self.batchfill_starters_max)))
        object.__setattr__(self, "batchfill_budget_ms", max(0, int(self.batchfill_budget_ms)))
        object.__setattr__(self, "batchfill_greedy_topk", max(1, int(self.batchfill_greedy_topk)))
        mode = str(self.score_mode or "gain_frag").strip().lower()
        if mode not in ALLOWED_SCORE_MODES:
            raise ValueError(f"SchedulerConfig invalid score_mode: {self.score_mode}")
        object.__setattr__(self, "score_mode", mode)
        object.__setattr__(self, "height_slack_mm", max(0, int(self.height_slack_mm)))
        z_band = self.z_band_mm
        if z_band is None:
            object.__setattr__(self, "z_band_mm", None)
        else:
            object.__setattr__(self, "z_band_mm", max(0, int(z_band)))


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
class SchedulerRampState:
    queue: tuple[Box, ...] = ()
    upstream: tuple[Box, ...] = ()
    capacity: int = 0


@dataclass(frozen=True)
class SchedulerSimState:
    now: float
    ramps: Mapping[int, Sequence[Box]]
    pallets: Mapping[int | str, PalletModel]
    pallet_blocked: set[int | str]
    ramp_states: Mapping[int, SchedulerRampState] = field(default_factory=dict)
    ramp_sizes: Mapping[int, int] = field(default_factory=dict)
    remaining_total: int = 0


@dataclass
class _BeamRampState:
    queue: list[Box]
    upstream: list[Box]
    capacity: int


@dataclass(frozen=True)
class _BeamAction:
    ramp_id: int
    buffer_index: int
    max_priority: float


@dataclass(frozen=True)
class _ScoreTerms:
    packing_gain: float
    fragmentation: float
    score_adjustment: float
    dt_extra: float
    time_cost: float
    starv_cost: float
    priority_score: float
    scalar_score: float
    height_after_mm: int


@dataclass
class _BeamNode:
    ramps: dict[int, _BeamRampState]
    pallets: dict[int | str, PalletModel]
    score_sum: float = 0.0
    gain_sum: float = 0.0
    fragmentation_sum: float = 0.0
    score_adjustment_sum: float = 0.0
    time_cost_sum: float = 0.0
    starv_cost_sum: float = 0.0
    priority_sum: float = 0.0
    height_after_mm: int = 0
    placed_count: int = 0
    first_plan: PickPlan | None = None


@dataclass(frozen=True)
class _ScoredCandidate:
    plan: PickPlan
    box: Box
    terms: _ScoreTerms


@dataclass(frozen=True)
class _BeamExpansion:
    node: _BeamNode
    box: Box
    terms: _ScoreTerms


class SchedulerV1:
    def __init__(self, config: SchedulerConfig | None = None) -> None:
        self.config = config or SchedulerConfig()
        self.last_blocked_pallets: dict[int | str, str] = {}
        self.deadline_cutoffs_count = 0
        self.last_deadlock = False
        self.last_deadlock_item: dict[str, Any] | None = None
        self.last_eval_stats: dict[str, Any] = {}
        self.last_micro_plan_stats: dict[str, Any] = {}
        self.last_micro_feasible_first_candidates = 0
        self._logger = logging.getLogger(__name__)

        self.micro_plan_calls = 0
        self.micro_plan_fallback_greedy = 0
        self.micro_plan_time_ms_sum = 0.0
        self.micro_plan_time_ms_min: float | None = None
        self.micro_plan_time_ms_max = 0.0
        self.micro_plan_time_ms_count = 0
        self.micro_plan_nodes_expanded_total = 0
        self.micro_plan_depth_effective_sum = 0.0
        self.micro_plan_depth_effective_count = 0
        self.micro_plan_best_seq_len_hist: dict[int, int] = {}
        self.selected_height_after_mm_hist: list[int] = []
        self.selected_height_above_min_feasible_count = 0
        self.selected_height_choices_count = 0
        self.selected_height_slack_decisions_count = 0
        self.selected_height_slack_filtered_count = 0
        self.selected_height_slack_set_size_sum = 0.0
        self.batchfill_calls = 0
        self.batchfill_applied = 0
        self.batchfill_selected_boxes_sum = 0
        self.batchfill_selected_boxes_count = 0

    def choose_action(self, sim_state: SchedulerSimState) -> PickPlan | None:
        self.last_blocked_pallets = {}
        self.last_deadlock = False
        self.last_deadlock_item = None
        self.last_eval_stats = {}
        self.last_micro_plan_stats = {}
        self.last_micro_feasible_first_candidates = 0
        k = max(1, int(self.config.lookahead_k))

        deadline = None
        if self.config.time_budget_ms and self.config.time_budget_ms > 0:
            deadline = time.perf_counter() + (float(self.config.time_budget_ms) / 1000.0)

        micro_enabled = bool(self.config.micro_plan_enabled)
        micro_stats: dict[str, Any] = {}
        if micro_enabled:
            self.micro_plan_calls += 1
            micro_start = time.perf_counter()
            micro_plan, micro_stats, micro_slack_stats = self._choose_action_micro(sim_state, deadline)
            micro_elapsed_ms = (time.perf_counter() - micro_start) * 1000.0
            self._record_micro_time(micro_elapsed_ms)

            self.last_micro_plan_stats = dict(micro_stats)
            self.last_micro_feasible_first_candidates = int(
                micro_stats.get("feasible_first_candidates", 0) or 0
            )
            self.micro_plan_nodes_expanded_total += int(micro_stats.get("nodes_expanded", 0) or 0)
            self.micro_plan_depth_effective_sum += float(micro_stats.get("depth_effective", 0.0) or 0.0)
            self.micro_plan_depth_effective_count += 1
            best_seq_len = int(micro_stats.get("best_seq_len", 0) or 0)
            self.micro_plan_best_seq_len_hist[best_seq_len] = int(
                self.micro_plan_best_seq_len_hist.get(best_seq_len, 0)
            ) + 1

            if micro_plan is not None:
                self._record_height_decision(
                    selected_height=micro_stats.get("selected_height_after_mm"),
                    min_feasible_height=micro_stats.get("feasible_first_min_height_mm"),
                )
                self._record_slack_decision(micro_slack_stats)
                self.last_eval_stats = {
                    "items_evaluated": 0,
                    "items_feasible": 0,
                    "cutoff": bool(micro_stats.get("cutoff", False)),
                    "cutoff_reason": str(micro_stats.get("cutoff_reason", "")),
                    "z_band_enabled": bool(micro_stats.get("z_band_enabled", False)),
                    "z_band_mm": micro_stats.get("z_band_mm"),
                    "z_band_removed": int(micro_stats.get("z_band_removed", 0) or 0),
                    "micro_plan": dict(micro_stats),
                    "mode": "micro",
                }
                return micro_plan

            self.micro_plan_fallback_greedy += 1

        plan = self._choose_action_greedy(sim_state, deadline=deadline, lookahead_k=k)
        if micro_stats:
            self.last_eval_stats["micro_plan"] = dict(micro_stats)
        if self.last_deadlock and self.last_deadlock_item is not None and micro_enabled:
            details = dict(self.last_deadlock_item)
            details["micro_plan_enabled"] = True
            details["micro_plan_feasible_first_candidates"] = int(self.last_micro_feasible_first_candidates)
            self.last_deadlock_item = details
        return plan

    def _record_micro_time(self, elapsed_ms: float) -> None:
        elapsed = max(0.0, float(elapsed_ms))
        self.micro_plan_time_ms_sum += elapsed
        self.micro_plan_time_ms_count += 1
        if self.micro_plan_time_ms_min is None:
            self.micro_plan_time_ms_min = elapsed
        else:
            self.micro_plan_time_ms_min = min(float(self.micro_plan_time_ms_min), elapsed)
        self.micro_plan_time_ms_max = max(float(self.micro_plan_time_ms_max), elapsed)

    def _choose_action_greedy(
        self,
        sim_state: SchedulerSimState,
        *,
        deadline: float | None,
        lookahead_k: int,
    ) -> PickPlan | None:
        heartbeat_sec = float(self.config.heartbeat_sec) if self.config.heartbeat_sec else 0.0
        next_heartbeat = time.perf_counter() + heartbeat_sec if heartbeat_sec > 0 else None

        cutoff = False
        cutoff_reason = ""

        items_evaluated = 0
        items_feasible = 0
        feasible_candidates: list[_ScoredCandidate] = []
        window_boxes_by_pallet_id: dict[int | str, list[Box]] = {}
        deadlock_item: dict[str, Any] | None = None

        for ramp_id, ramp in sim_state.ramps.items():
            ramp_items = list(ramp)[:lookahead_k]
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
                pallet_id = box.destination
                if pallet_id is not None:
                    window_boxes_by_pallet_id.setdefault(pallet_id, []).append(box)
                if deadline is not None and time.perf_counter() >= deadline:
                    cutoff = True
                    cutoff_reason = "time_budget"
                    break
                if self.config.max_candidates and items_evaluated >= int(self.config.max_candidates):
                    cutoff = True
                    cutoff_reason = "max_candidates"
                    break
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

                height_after_mm = self._resolve_height_after_mm(preview, pallet)

                terms = self._score_candidate(
                    now=float(sim_state.now),
                    box=box,
                    idx=idx,
                    preview=preview,
                    max_priority=max_priority,
                    height_after_mm=height_after_mm,
                )
                feasible_candidates.append(
                    _ScoredCandidate(
                        plan=PickPlan(
                            ramp_id=int(ramp_id),
                            buffer_index=int(idx),
                            box_id=box.box_id,
                            pallet_id=pallet_id,
                            preview=preview,
                            score=float(terms.scalar_score),
                            dt_extra=float(terms.dt_extra),
                        ),
                        box=box,
                        terms=terms,
                    )
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

        batchfill_stats = {
            "batchfill_calls": 0,
            "batchfill_applied": 0,
            "batchfill_selected_layer_boxes_mean": 0.0,
        }
        if feasible_candidates and bool(self.config.batchfill_layer_starter):
            feasible_candidates, batchfill_stats = self._apply_batchfill_on_scored_candidates(
                feasible_candidates=feasible_candidates,
                pallets=sim_state.pallets,
                window_boxes_by_pallet_id=window_boxes_by_pallet_id,
                deadline=deadline,
            )

        z_band_enabled = self.config.z_band_mm is not None
        z_band_removed = 0
        if feasible_candidates and z_band_enabled:
            feasible_candidates, z_band_removed = self._filter_by_z_band_scored(
                feasible_candidates=feasible_candidates,
                z_band_mm=int(self.config.z_band_mm or 0),
            )

        self.last_eval_stats = {
            "items_evaluated": int(items_evaluated),
            "items_feasible": int(items_feasible),
            "cutoff": bool(cutoff),
            "cutoff_reason": cutoff_reason,
            "batchfill_calls": int(batchfill_stats["batchfill_calls"]),
            "batchfill_applied": int(batchfill_stats["batchfill_applied"]),
            "batchfill_selected_layer_boxes_mean": float(batchfill_stats["batchfill_selected_layer_boxes_mean"]),
            "z_band_enabled": bool(z_band_enabled),
            "z_band_mm": (int(self.config.z_band_mm) if self.config.z_band_mm is not None else None),
            "z_band_removed": int(z_band_removed),
            "mode": "greedy",
        }

        best_plan: PickPlan | None = None
        if feasible_candidates:
            best_by_slack, slack_stats = choose_with_height_slack(
                candidates=feasible_candidates,
                score_mode=self.config.score_mode,
                height_slack_mm=int(self.config.height_slack_mm),
                height_after_mm_fn=lambda candidate: int(candidate.terms.height_after_mm),
                gain_frag_key_fn=self._gain_frag_candidate_key,
            )
            if self.config.score_mode == ScoreMode.MIN_HEIGHT_THEN_GAIN.value:
                selected = min(
                    feasible_candidates,
                    key=lambda candidate: self._min_height_then_gain_key(terms=candidate.terms, box=candidate.box),
                )
            else:
                selected = best_by_slack

            if selected is not None:
                best_plan = selected.plan
                self._record_height_decision(
                    selected_height=selected.terms.height_after_mm,
                    min_feasible_height=slack_stats.min_height_after_mm,
                )
                self._record_slack_decision(slack_stats)

        if items_evaluated > 0 and items_feasible == 0 and not cutoff and not self.last_blocked_pallets:
            self.last_deadlock = True
            self.last_deadlock_item = deadlock_item or {
                "box_id": None,
                "pallet_id": None,
                "reason": "NO_FEASIBLE_PLACEMENT",
                "dims": None,
            }

        return best_plan

    def _choose_action_micro(
        self,
        sim_state: SchedulerSimState,
        deadline: float | None,
    ) -> tuple[PickPlan | None, dict[str, Any], SlackDecisionStats | None]:
        depth_limit = max(1, int(self.config.micro_plan_depth))
        beam_width = max(1, int(self.config.micro_plan_width))
        topk_per_step = max(1, int(self.config.micro_plan_topk_per_step))

        root = _BeamNode(
            ramps=self._build_beam_ramps(sim_state),
            pallets=dict(sim_state.pallets),
            score_sum=0.0,
            gain_sum=0.0,
            fragmentation_sum=0.0,
            score_adjustment_sum=0.0,
            time_cost_sum=0.0,
            starv_cost_sum=0.0,
            priority_sum=0.0,
            height_after_mm=self._state_height_after_mm(dict(sim_state.pallets)),
            placed_count=0,
            first_plan=None,
        )
        beam: list[_BeamNode] = [root]
        best_node = root

        nodes_expanded = 0
        feasible_first_candidates = 0
        feasible_first_min_height: int | None = None
        root_slack_stats: SlackDecisionStats | None = None
        depth_effective = 0
        cutoff = False
        cutoff_reason = ""
        batchfill_calls_local = 0
        batchfill_applied_local = 0
        batchfill_selected_boxes_sum_local = 0
        batchfill_selected_boxes_count_local = 0
        z_band_removed_local = 0

        for depth in range(depth_limit):
            if deadline is not None and time.perf_counter() >= deadline:
                cutoff = True
                cutoff_reason = "time_budget"
                break

            next_beam: list[_BeamNode] = []
            for node in beam:
                if deadline is not None and time.perf_counter() >= deadline:
                    cutoff = True
                    cutoff_reason = "time_budget"
                    break

                actions = self._enumerate_beam_actions(node.ramps)
                if not actions:
                    continue

                expansions: list[_BeamExpansion] = []
                for action in actions:
                    if deadline is not None and time.perf_counter() >= deadline:
                        cutoff = True
                        cutoff_reason = "time_budget"
                        break
                    expansion = self._expand_beam_node(
                        node=node,
                        action=action,
                        sim_state=sim_state,
                    )
                    if expansion is None:
                        continue
                    nodes_expanded += 1
                    expansions.append(expansion)

                if depth == 0 and node.first_plan is None and expansions and bool(self.config.batchfill_layer_starter):
                    expansions, batchfill_stats = self._apply_batchfill_on_beam_expansions(
                        node=node,
                        expansions=expansions,
                        deadline=deadline,
                    )
                    batchfill_calls_local += int(batchfill_stats["batchfill_calls"])
                    batchfill_applied_local += int(batchfill_stats["batchfill_applied"])
                    batchfill_selected_boxes_sum_local += int(batchfill_stats["selected_boxes_sum"])
                    batchfill_selected_boxes_count_local += int(batchfill_stats["selected_boxes_count"])

                if depth == 0:
                    if expansions and self.config.z_band_mm is not None:
                        expansions, z_removed = self._filter_by_z_band_expansions(
                            expansions=expansions,
                            z_band_mm=int(self.config.z_band_mm),
                        )
                        z_band_removed_local += int(z_removed)
                    feasible_first_candidates += len(expansions)
                    for expansion in expansions:
                        feasible_first_min_height = (
                            int(expansion.terms.height_after_mm)
                            if feasible_first_min_height is None
                            else min(int(feasible_first_min_height), int(expansion.terms.height_after_mm))
                        )
                    if root_slack_stats is None:
                        _selected_root, root_slack_stats = choose_with_height_slack(
                            candidates=expansions,
                            score_mode=self.config.score_mode,
                            height_slack_mm=int(self.config.height_slack_mm),
                            height_after_mm_fn=lambda candidate: int(candidate.terms.height_after_mm),
                            gain_frag_key_fn=self._beam_expansion_gain_frag_key,
                        )

                if expansions:
                    if self.config.score_mode == ScoreMode.MIN_HEIGHT_SLACK_THEN_GAIN.value:
                        ordered_expansions = rank_for_expansion_with_height_slack(
                            candidates=expansions,
                            score_mode=self.config.score_mode,
                            height_slack_mm=int(self.config.height_slack_mm),
                            height_after_mm_fn=lambda candidate: int(candidate.terms.height_after_mm),
                            gain_frag_key_fn=self._beam_expansion_gain_frag_key,
                        )
                    else:
                        ordered_expansions = sorted(
                            expansions,
                            key=lambda candidate: self._beam_rank_key(candidate.node),
                            reverse=True,
                        )
                    keep = min(len(ordered_expansions), topk_per_step)
                    next_beam.extend(candidate.node for candidate in ordered_expansions[:keep])

                if cutoff:
                    break

            if not next_beam:
                break

            next_beam.sort(key=self._beam_rank_key, reverse=True)
            beam = next_beam[:beam_width]
            depth_effective = depth + 1

            for candidate in beam:
                if self._beam_rank_key(candidate) > self._beam_rank_key(best_node):
                    best_node = candidate

            if cutoff:
                break

        best_seq_len = int(best_node.placed_count)
        stats = {
            "enabled": True,
            "score_mode": str(self.config.score_mode),
            "depth_limit": int(depth_limit),
            "width": int(beam_width),
            "topk_per_step": int(topk_per_step),
            "nodes_expanded": int(nodes_expanded),
            "depth_effective": int(depth_effective),
            "best_seq_len": int(best_seq_len),
            "feasible_first_candidates": int(feasible_first_candidates),
            "feasible_first_min_height_mm": (
                int(feasible_first_min_height) if feasible_first_min_height is not None else None
            ),
            "selected_height_after_mm": (
                self._resolve_height_after_mm(best_node.first_plan.preview)
                if best_node.first_plan is not None
                else None
            ),
            "height_slack_mm": int(self.config.height_slack_mm),
            "cutoff": bool(cutoff),
            "cutoff_reason": str(cutoff_reason),
            "batchfill_calls": int(batchfill_calls_local),
            "batchfill_applied": int(batchfill_applied_local),
            "batchfill_selected_layer_boxes_mean": float(
                float(batchfill_selected_boxes_sum_local) / max(1, int(batchfill_selected_boxes_count_local))
            ),
            "z_band_enabled": bool(self.config.z_band_mm is not None),
            "z_band_mm": (int(self.config.z_band_mm) if self.config.z_band_mm is not None else None),
            "z_band_removed": int(z_band_removed_local),
        }

        if best_node.first_plan is None:
            return None, stats, root_slack_stats
        return best_node.first_plan, stats, root_slack_stats

    def _beam_rank_key(self, node: _BeamNode) -> tuple[Any, ...]:
        if self.config.score_mode == "min_height_then_gain":
            return (
                int(node.placed_count),
                -int(node.height_after_mm),
                float(node.gain_sum),
                -float(node.fragmentation_sum),
                -float(node.time_cost_sum),
                -float(node.starv_cost_sum),
                float(node.priority_sum),
                float(node.score_adjustment_sum),
                float(node.score_sum),
            )
        return (int(node.placed_count), float(node.score_sum))

    def _build_beam_ramps(self, sim_state: SchedulerSimState) -> dict[int, _BeamRampState]:
        result: dict[int, _BeamRampState] = {}
        if sim_state.ramp_states:
            merged_ramp_ids = sorted(set(sim_state.ramps.keys()) | set(sim_state.ramp_states.keys()))
            for rid in merged_ramp_ids:
                snapshot = sim_state.ramp_states.get(rid)
                if snapshot is None:
                    queue_items = list(sim_state.ramps.get(rid, []))
                    result[int(rid)] = _BeamRampState(
                        queue=queue_items,
                        upstream=[],
                        capacity=max(0, len(queue_items)),
                    )
                    continue
                queue_items = list(snapshot.queue)
                upstream_items = list(snapshot.upstream)
                cap = int(snapshot.capacity) if int(snapshot.capacity) > 0 else len(queue_items)
                result[int(rid)] = _BeamRampState(
                    queue=queue_items,
                    upstream=upstream_items,
                    capacity=max(0, cap),
                )
            return result

        for rid, queue in sim_state.ramps.items():
            queue_items = list(queue)
            result[int(rid)] = _BeamRampState(
                queue=queue_items,
                upstream=[],
                capacity=max(0, len(queue_items)),
            )
        return result

    def _enumerate_beam_actions(self, ramps: Mapping[int, _BeamRampState]) -> list[_BeamAction]:
        if not ramps:
            return []
        queue_lens = {int(rid): len(state.queue) for rid, state in ramps.items()}
        allocation = self._allocate_micro_window(queue_lens)
        actions: list[_BeamAction] = []
        for rid in sorted(allocation):
            limit = int(allocation[rid])
            if limit <= 0:
                continue
            queue = ramps[rid].queue
            max_priority = self._max_priority(queue[:limit])
            for idx in range(limit):
                actions.append(_BeamAction(ramp_id=int(rid), buffer_index=int(idx), max_priority=max_priority))
        return actions

    def _allocate_micro_window(self, queue_lens: Mapping[int, int]) -> dict[int, int]:
        ramp_ids = sorted(int(rid) for rid in queue_lens)
        if not ramp_ids:
            return {}

        total_limit = int(self.config.micro_plan_window_total)
        if total_limit <= 0:
            return {rid: max(0, int(queue_lens.get(rid, 0))) for rid in ramp_ids}

        strategy = str(self.config.micro_plan_window_strategy or "fifo_ramp").strip().lower()
        allocation = {rid: 0 for rid in ramp_ids}
        remaining = int(total_limit)

        if strategy == "round_robin":
            while remaining > 0:
                progressed = False
                for rid in ramp_ids:
                    if allocation[rid] >= max(0, int(queue_lens.get(rid, 0))):
                        continue
                    allocation[rid] += 1
                    remaining -= 1
                    progressed = True
                    if remaining <= 0:
                        break
                if not progressed:
                    break
            return allocation

        # default: FIFO por rampa (rampa 1, luego 2, ...)
        for rid in ramp_ids:
            if remaining <= 0:
                break
            can_take = max(0, int(queue_lens.get(rid, 0)))
            take = min(can_take, remaining)
            allocation[rid] = int(take)
            remaining -= take
        return allocation

    def _expand_beam_node(
        self,
        *,
        node: _BeamNode,
        action: _BeamAction,
        sim_state: SchedulerSimState,
    ) -> _BeamExpansion | None:
        ramp = node.ramps.get(action.ramp_id)
        if ramp is None:
            return None
        idx = int(action.buffer_index)
        if idx < 0 or idx >= len(ramp.queue):
            return None

        box = ramp.queue[idx]
        pallet_id = box.destination
        if pallet_id is None:
            return None
        if pallet_id in sim_state.pallet_blocked:
            return None

        pallet = node.pallets.get(pallet_id)
        if pallet is None:
            return None

        preview = self._preview_place(pallet, box)
        if not preview.feasible:
            return None

        height_after_mm = self._resolve_height_after_mm(preview, pallet)
        terms = self._score_candidate(
            now=float(sim_state.now),
            box=box,
            idx=idx,
            preview=preview,
            max_priority=float(action.max_priority),
            height_after_mm=height_after_mm,
        )

        first_plan = node.first_plan
        if first_plan is None:
            first_plan = PickPlan(
                ramp_id=int(action.ramp_id),
                buffer_index=int(idx),
                box_id=box.box_id,
                pallet_id=pallet_id,
                preview=preview,
                score=float(terms.scalar_score),
                dt_extra=float(terms.dt_extra),
            )

        pallet_clone = copy.deepcopy(pallet)
        try:
            pallet_clone.commit_place(preview)
        except Exception:
            return None

        new_pallets = dict(node.pallets)
        new_pallets[pallet_id] = pallet_clone

        new_ramps = dict(node.ramps)
        new_ramps[action.ramp_id] = self._beam_pick_and_refill(ramp, idx)

        child = _BeamNode(
            ramps=new_ramps,
            pallets=new_pallets,
            score_sum=float(node.score_sum) + float(terms.scalar_score),
            gain_sum=float(node.gain_sum) + float(terms.packing_gain),
            fragmentation_sum=float(node.fragmentation_sum) + float(terms.fragmentation),
            score_adjustment_sum=float(node.score_adjustment_sum) + float(terms.score_adjustment),
            time_cost_sum=float(node.time_cost_sum) + float(terms.time_cost),
            starv_cost_sum=float(node.starv_cost_sum) + float(terms.starv_cost),
            priority_sum=float(node.priority_sum) + float(terms.priority_score),
            height_after_mm=int(self._state_height_after_mm(new_pallets)),
            placed_count=int(node.placed_count) + 1,
            first_plan=first_plan,
        )
        return _BeamExpansion(node=child, box=box, terms=terms)

    @staticmethod
    def _beam_pick_and_refill(ramp: _BeamRampState, idx: int) -> _BeamRampState:
        queue = list(ramp.queue)
        upstream = list(ramp.upstream)
        if 0 <= idx < len(queue):
            queue.pop(idx)

        cap = max(0, int(ramp.capacity))
        while cap > 0 and len(queue) < cap and upstream:
            queue.append(upstream.pop(0))

        return _BeamRampState(queue=queue, upstream=upstream, capacity=cap)

    @staticmethod
    def _placement_z_mm(preview: PlacementPreview | None) -> int | None:
        if preview is None:
            return None
        placement = getattr(preview, "placement", None)
        if placement is None:
            return None
        try:
            return int(getattr(placement, "z_mm"))
        except (TypeError, ValueError):
            return None

    def _filter_by_z_band_scored(
        self,
        feasible_candidates: list[_ScoredCandidate],
        z_band_mm: int,
    ) -> tuple[list[_ScoredCandidate], int]:
        band = max(0, int(z_band_mm))
        min_z_by_pallet: dict[int | str, int] = {}
        for candidate in feasible_candidates:
            pallet_id = candidate.plan.pallet_id
            z_mm = self._placement_z_mm(candidate.plan.preview)
            if z_mm is None:
                continue
            prev = min_z_by_pallet.get(pallet_id)
            if prev is None or int(z_mm) < int(prev):
                min_z_by_pallet[pallet_id] = int(z_mm)

        if not min_z_by_pallet:
            return feasible_candidates, 0

        filtered: list[_ScoredCandidate] = []
        removed = 0
        for candidate in feasible_candidates:
            pallet_id = candidate.plan.pallet_id
            z_mm = self._placement_z_mm(candidate.plan.preview)
            min_z = min_z_by_pallet.get(pallet_id)
            if z_mm is None or min_z is None or int(z_mm) <= int(min_z) + band:
                filtered.append(candidate)
            else:
                removed += 1
        return filtered, int(removed)

    def _filter_by_z_band_expansions(
        self,
        expansions: list[_BeamExpansion],
        z_band_mm: int,
    ) -> tuple[list[_BeamExpansion], int]:
        band = max(0, int(z_band_mm))
        min_z_by_pallet: dict[int | str, int] = {}
        for expansion in expansions:
            pallet_id = expansion.box.destination
            if pallet_id is None:
                continue
            first_plan = expansion.node.first_plan
            preview = first_plan.preview if first_plan is not None else None
            z_mm = self._placement_z_mm(preview)
            if z_mm is None:
                continue
            prev = min_z_by_pallet.get(pallet_id)
            if prev is None or int(z_mm) < int(prev):
                min_z_by_pallet[pallet_id] = int(z_mm)

        if not min_z_by_pallet:
            return expansions, 0

        filtered: list[_BeamExpansion] = []
        removed = 0
        for expansion in expansions:
            pallet_id = expansion.box.destination
            if pallet_id is None:
                filtered.append(expansion)
                continue
            min_z = min_z_by_pallet.get(pallet_id)
            first_plan = expansion.node.first_plan
            preview = first_plan.preview if first_plan is not None else None
            z_mm = self._placement_z_mm(preview)
            if z_mm is None or min_z is None or int(z_mm) <= int(min_z) + band:
                filtered.append(expansion)
            else:
                removed += 1
        return filtered, int(removed)

    @staticmethod
    def _preview_layer_id(preview: PlacementPreview | None) -> int | None:
        if preview is None:
            return None
        placement = getattr(preview, "placement", None)
        if placement is None:
            return None
        try:
            return int(getattr(placement, "layer_id"))
        except (TypeError, ValueError):
            return None

    def _batchfill_deadline(self, deadline: float | None) -> float | None:
        budget_ms = max(0, int(self.config.batchfill_budget_ms))
        local_deadline = time.perf_counter() + (float(budget_ms) / 1000.0)
        if deadline is None:
            return local_deadline
        return min(float(deadline), float(local_deadline))

    def _simulate_batchfill_layer(
        self,
        *,
        pallet: PalletModel,
        starter_preview: PlacementPreview,
        starter_box: Box,
        pool_boxes: Sequence[Box],
        deadline: float | None,
    ) -> tuple[int, float, int] | None:
        try:
            pallet_clone = copy.deepcopy(pallet)
        except Exception:
            return None

        try:
            starter_preview_local = self._preview_place(pallet_clone, starter_box)
            if not starter_preview_local.feasible:
                return None
            starter_placement = pallet_clone.commit_place(starter_preview_local)
        except Exception:
            return None

        starter_layer_id = int(getattr(starter_placement, "layer_id", -1))
        if starter_layer_id < 0:
            return None

        remaining_boxes = [box for box in list(pool_boxes) if box is not starter_box]
        greedy_topk = max(1, int(self.config.batchfill_greedy_topk))

        while remaining_boxes:
            if deadline is not None and time.perf_counter() >= deadline:
                break

            feasible_fillers: list[tuple[float, int, PlacementPreview, Box]] = []
            for box in remaining_boxes:
                if deadline is not None and time.perf_counter() >= deadline:
                    break
                preview = self._preview_place(pallet_clone, box)
                if not preview.feasible:
                    continue
                preview_layer_id = self._preview_layer_id(preview)
                if preview_layer_id != starter_layer_id:
                    continue
                rank_score = (
                    float(preview.packing_gain)
                    - float(preview.fragmentation)
                    + float(getattr(preview, "score_adjustment", 0.0) or 0.0)
                )
                height_after_mm = self._resolve_height_after_mm(preview, pallet_clone)
                feasible_fillers.append((rank_score, -int(height_after_mm), preview, box))

            if not feasible_fillers:
                break

            feasible_fillers.sort(key=lambda item: (float(item[0]), int(item[1])), reverse=True)
            _score, _neg_height, chosen_preview, chosen_box = feasible_fillers[:greedy_topk][0]
            try:
                pallet_clone.commit_place(chosen_preview)
            except Exception:
                break

            removed = False
            for idx, queued_box in enumerate(remaining_boxes):
                if queued_box is chosen_box:
                    remaining_boxes.pop(idx)
                    removed = True
                    break
            if not removed:
                chosen_box_id = getattr(chosen_box, "box_id", None)
                for idx, queued_box in enumerate(remaining_boxes):
                    if getattr(queued_box, "box_id", None) == chosen_box_id:
                        remaining_boxes.pop(idx)
                        removed = True
                        break
            if not removed:
                break

        layer_placements = [
            placement
            for placement in list(getattr(pallet_clone, "placements", []) or [])
            if int(getattr(placement, "layer_id", -1)) == starter_layer_id
        ]
        boxes_in_layer = int(len(layer_placements))
        used_area = int(
            sum(int(getattr(placement, "length_mm", 0)) * int(getattr(placement, "width_mm", 0)) for placement in layer_placements)
        )
        bin_area = max(1, int(getattr(pallet_clone, "bin_area_mm2", 1) or 1))
        fill_ratio = float(used_area) / float(bin_area)

        layer_height = 0
        layers = list(getattr(pallet_clone, "layers", []) or [])
        if 0 <= starter_layer_id < len(layers):
            try:
                layer_height = int(getattr(layers[starter_layer_id], "height_mm", 0))
            except (TypeError, ValueError):
                layer_height = 0

        return boxes_in_layer, fill_ratio, layer_height

    def _accumulate_batchfill_stats(
        self,
        *,
        calls: int,
        applied: int,
        selected_boxes_sum: int,
        selected_boxes_count: int,
    ) -> None:
        self.batchfill_calls += max(0, int(calls))
        self.batchfill_applied += max(0, int(applied))
        self.batchfill_selected_boxes_sum += max(0, int(selected_boxes_sum))
        self.batchfill_selected_boxes_count += max(0, int(selected_boxes_count))

    def _apply_batchfill_on_scored_candidates(
        self,
        *,
        feasible_candidates: list[_ScoredCandidate],
        pallets: Mapping[int | str, PalletModel],
        window_boxes_by_pallet_id: Mapping[int | str, Sequence[Box]],
        deadline: float | None,
    ) -> tuple[list[_ScoredCandidate], dict[str, float]]:
        grouped: dict[int | str, list[_ScoredCandidate]] = {}
        pallet_order: list[int | str] = []
        for candidate in feasible_candidates:
            pallet_id = candidate.plan.pallet_id
            if pallet_id not in grouped:
                grouped[pallet_id] = []
                pallet_order.append(pallet_id)
            grouped[pallet_id].append(candidate)

        batchfill_calls_local = 1 if grouped else 0
        batchfill_applied_local = 0
        batchfill_selected_boxes_sum_local = 0
        batchfill_selected_boxes_count_local = 0
        filtered_candidates: list[_ScoredCandidate] = []

        for pallet_id in pallet_order:
            group = grouped.get(pallet_id, [])
            pallet = pallets.get(pallet_id)
            if pallet is None or not group:
                filtered_candidates.extend(group)
                continue

            layers = list(getattr(pallet, "layers", []) or [])
            start_layer_id = len(layers)
            if start_layer_id <= 0:
                filtered_candidates.extend(group)
                continue
            active_layer_id = (start_layer_id - 1) if start_layer_id > 0 else None
            has_active_layer_candidate = False
            new_layer_candidates: list[_ScoredCandidate] = []
            for candidate in group:
                preview_layer_id = self._preview_layer_id(candidate.plan.preview)
                if preview_layer_id is None:
                    continue
                if active_layer_id is not None and preview_layer_id == active_layer_id:
                    has_active_layer_candidate = True
                if preview_layer_id == start_layer_id:
                    new_layer_candidates.append(candidate)

            if has_active_layer_candidate or not new_layer_candidates:
                filtered_candidates.extend(group)
                continue

            batchfill_applied_local += 1
            starters_cap = max(1, int(self.config.batchfill_starters_max))
            starters = sorted(
                new_layer_candidates,
                key=lambda candidate: float(candidate.terms.scalar_score),
                reverse=True,
            )[:starters_cap]
            pool_boxes = list(window_boxes_by_pallet_id.get(pallet_id, []) or [])
            if not pool_boxes:
                pool_boxes = [candidate.box for candidate in group]

            batchfill_deadline = self._batchfill_deadline(deadline)
            best_candidate: _ScoredCandidate | None = None
            best_key: tuple[Any, ...] | None = None
            best_boxes_in_layer = 0

            for starter in starters:
                if batchfill_deadline is not None and time.perf_counter() >= batchfill_deadline:
                    break
                sim = self._simulate_batchfill_layer(
                    pallet=pallet,
                    starter_preview=starter.plan.preview,
                    starter_box=starter.box,
                    pool_boxes=pool_boxes,
                    deadline=batchfill_deadline,
                )
                if sim is None:
                    continue
                boxes_in_layer, fill_ratio, layer_height = sim
                key = (
                    int(boxes_in_layer),
                    float(fill_ratio),
                    -int(layer_height),
                    float(starter.terms.scalar_score),
                )
                if best_key is None or key > best_key:
                    best_key = key
                    best_candidate = starter
                    best_boxes_in_layer = int(boxes_in_layer)

            if best_candidate is None:
                filtered_candidates.extend(group)
                continue

            filtered_candidates.append(best_candidate)
            batchfill_selected_boxes_sum_local += int(best_boxes_in_layer)
            batchfill_selected_boxes_count_local += 1

        self._accumulate_batchfill_stats(
            calls=batchfill_calls_local,
            applied=batchfill_applied_local,
            selected_boxes_sum=batchfill_selected_boxes_sum_local,
            selected_boxes_count=batchfill_selected_boxes_count_local,
        )
        return filtered_candidates, {
            "batchfill_calls": int(batchfill_calls_local),
            "batchfill_applied": int(batchfill_applied_local),
            "batchfill_selected_layer_boxes_mean": float(
                float(batchfill_selected_boxes_sum_local) / max(1, int(batchfill_selected_boxes_count_local))
            ),
        }

    def _apply_batchfill_on_beam_expansions(
        self,
        *,
        node: _BeamNode,
        expansions: list[_BeamExpansion],
        deadline: float | None,
    ) -> tuple[list[_BeamExpansion], dict[str, int]]:
        grouped: dict[int | str, list[_BeamExpansion]] = {}
        pallet_order: list[int | str] = []
        for expansion in expansions:
            pallet_id = expansion.box.destination
            if pallet_id is None:
                continue
            if pallet_id not in grouped:
                grouped[pallet_id] = []
                pallet_order.append(pallet_id)
            grouped[pallet_id].append(expansion)

        batchfill_calls_local = 1 if grouped else 0
        batchfill_applied_local = 0
        batchfill_selected_boxes_sum_local = 0
        batchfill_selected_boxes_count_local = 0
        filtered: list[_BeamExpansion] = []
        handled_pallets: set[int | str] = set()

        for expansion in expansions:
            pallet_id = expansion.box.destination
            if pallet_id is None or pallet_id in handled_pallets:
                if pallet_id is None:
                    filtered.append(expansion)
                continue
            handled_pallets.add(pallet_id)

            group = grouped.get(pallet_id, [])
            pallet = node.pallets.get(pallet_id)
            if pallet is None or not group:
                filtered.extend(group)
                continue

            layers = list(getattr(pallet, "layers", []) or [])
            start_layer_id = len(layers)
            if start_layer_id <= 0:
                filtered.extend(group)
                continue
            active_layer_id = (start_layer_id - 1) if start_layer_id > 0 else None
            has_active_layer_candidate = False
            new_layer_starters: list[tuple[_BeamExpansion, PlacementPreview]] = []
            for item in group:
                first_plan = item.node.first_plan
                starter_preview = first_plan.preview if first_plan is not None else None
                preview_layer_id = self._preview_layer_id(starter_preview)
                if preview_layer_id is None:
                    continue
                if active_layer_id is not None and preview_layer_id == active_layer_id:
                    has_active_layer_candidate = True
                if preview_layer_id == start_layer_id and starter_preview is not None:
                    new_layer_starters.append((item, starter_preview))

            if has_active_layer_candidate or not new_layer_starters:
                filtered.extend(group)
                continue

            batchfill_applied_local += 1
            starters_cap = max(1, int(self.config.batchfill_starters_max))
            starters = sorted(
                new_layer_starters,
                key=lambda item: float(item[0].terms.scalar_score),
                reverse=True,
            )[:starters_cap]
            pool_boxes = [item.box for item in group]
            batchfill_deadline = self._batchfill_deadline(deadline)
            best_expansion: _BeamExpansion | None = None
            best_key: tuple[Any, ...] | None = None
            best_boxes_in_layer = 0

            for starter_expansion, starter_preview in starters:
                if batchfill_deadline is not None and time.perf_counter() >= batchfill_deadline:
                    break
                sim = self._simulate_batchfill_layer(
                    pallet=pallet,
                    starter_preview=starter_preview,
                    starter_box=starter_expansion.box,
                    pool_boxes=pool_boxes,
                    deadline=batchfill_deadline,
                )
                if sim is None:
                    continue
                boxes_in_layer, fill_ratio, layer_height = sim
                key = (
                    int(boxes_in_layer),
                    float(fill_ratio),
                    -int(layer_height),
                    float(starter_expansion.terms.scalar_score),
                )
                if best_key is None or key > best_key:
                    best_key = key
                    best_expansion = starter_expansion
                    best_boxes_in_layer = int(boxes_in_layer)

            if best_expansion is None:
                filtered.extend(group)
                continue

            filtered.append(best_expansion)
            batchfill_selected_boxes_sum_local += int(best_boxes_in_layer)
            batchfill_selected_boxes_count_local += 1

        self._accumulate_batchfill_stats(
            calls=batchfill_calls_local,
            applied=batchfill_applied_local,
            selected_boxes_sum=batchfill_selected_boxes_sum_local,
            selected_boxes_count=batchfill_selected_boxes_count_local,
        )
        return filtered, {
            "batchfill_calls": int(batchfill_calls_local),
            "batchfill_applied": int(batchfill_applied_local),
            "selected_boxes_sum": int(batchfill_selected_boxes_sum_local),
            "selected_boxes_count": int(batchfill_selected_boxes_count_local),
        }

    @staticmethod
    def _max_priority(boxes: Sequence[Box]) -> float:
        max_priority = 0.0
        for item in boxes:
            try:
                max_priority = max(max_priority, float(getattr(item, "priority", 0.0) or 0.0))
            except Exception:
                continue
        return float(max_priority)

    def _score_candidate(
        self,
        *,
        now: float,
        box: Box,
        idx: int,
        preview: PlacementPreview,
        max_priority: float,
        height_after_mm: int,
    ) -> _ScoreTerms:
        dt_extra = selection_dt(idx, self.config.t_select_base, self.config.t_select_step)
        time_cost = time_penalty(dt_extra, self.config.time_penalty_weight)
        age = max(0.0, float(now) - float(box.timestamp))
        starv_cost = starvation_penalty(age, self.config.starvation_weight)
        priority_val = float(getattr(box, "priority", 0.0) or 0.0)
        priority_norm = (priority_val / max_priority) if max_priority > 0 else 0.0
        priority_score = priority_bonus(priority_norm, self.config.priority_weight)

        score = (
            float(preview.packing_gain)
            - float(preview.fragmentation)
            + float(getattr(preview, "score_adjustment", 0.0) or 0.0)
            - time_cost
            - starv_cost
            + priority_score
        )
        return _ScoreTerms(
            packing_gain=float(preview.packing_gain),
            fragmentation=float(preview.fragmentation),
            score_adjustment=float(getattr(preview, "score_adjustment", 0.0) or 0.0),
            dt_extra=float(dt_extra),
            time_cost=float(time_cost),
            starv_cost=float(starv_cost),
            priority_score=float(priority_score),
            scalar_score=float(score),
            height_after_mm=int(height_after_mm),
        )

    @staticmethod
    def _gain_frag_sort_key(terms: _ScoreTerms, box: Box) -> tuple[Any, ...]:
        return (
            float(terms.scalar_score),
            -float(terms.dt_extra),
            -float(box.timestamp),
        )

    @staticmethod
    def _gain_frag_candidate_key(candidate: _ScoredCandidate) -> tuple[Any, ...]:
        return SchedulerV1._gain_frag_sort_key(candidate.terms, candidate.box)

    @staticmethod
    def _beam_expansion_gain_frag_key(candidate: _BeamExpansion) -> tuple[Any, ...]:
        return SchedulerV1._gain_frag_sort_key(candidate.terms, candidate.box)

    @staticmethod
    def _min_height_then_gain_key(terms: _ScoreTerms, box: Box) -> tuple[Any, ...]:
        return (
            int(terms.height_after_mm),
            -float(terms.packing_gain),
            float(terms.fragmentation),
            -float(terms.score_adjustment),
            float(terms.time_cost),
            float(terms.starv_cost),
            -float(terms.priority_score),
            float(terms.dt_extra),
            float(box.timestamp),
            str(getattr(box, "box_id", "")),
        )

    @staticmethod
    def _resolve_height_after_mm(preview: PlacementPreview, pallet: PalletModel | None = None) -> int:
        height_after = getattr(preview, "height_after_mm", None)
        if height_after is not None:
            try:
                return int(height_after)
            except (TypeError, ValueError):
                pass

        placement = getattr(preview, "placement", None)
        if placement is not None:
            try:
                return int(getattr(placement, "z_mm", 0)) + int(getattr(placement, "height_mm", 0))
            except (TypeError, ValueError):
                pass

        if pallet is not None:
            try:
                return int(pallet.current_height_mm())
            except Exception:
                pass
        return 0

    @staticmethod
    def _state_height_after_mm(pallets: Mapping[int | str, PalletModel]) -> int:
        if not pallets:
            return 0
        max_height = 0
        for pallet in pallets.values():
            try:
                max_height = max(max_height, int(pallet.current_height_mm()))
            except Exception:
                continue
        return int(max_height)

    def _record_height_decision(self, *, selected_height: Any, min_feasible_height: Any) -> None:
        if selected_height is None:
            return
        try:
            selected = int(selected_height)
        except (TypeError, ValueError):
            return

        self.selected_height_after_mm_hist.append(selected)
        self.selected_height_choices_count += 1

        if min_feasible_height is None:
            return
        try:
            min_height = int(min_feasible_height)
        except (TypeError, ValueError):
            return
        if selected > min_height:
            self.selected_height_above_min_feasible_count += 1

    def _record_slack_decision(self, stats: SlackDecisionStats | None) -> None:
        if stats is None:
            return
        self.selected_height_slack_decisions_count += 1
        self.selected_height_slack_set_size_sum += float(max(0, int(stats.slack_set_n)))
        if bool(stats.slack_set_used) and int(stats.slack_set_n) > 0:
            self.selected_height_slack_filtered_count += 1

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
