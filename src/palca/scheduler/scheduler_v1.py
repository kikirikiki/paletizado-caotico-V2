from __future__ import annotations

from collections import deque
import copy
from dataclasses import dataclass, field, replace
import inspect
import logging
import time
from typing import TYPE_CHECKING, Any, Mapping, Sequence

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

if TYPE_CHECKING:
    from ..integration.early_layer_pattern_planner import (
        EarlyLayerPatternPlanner,
        LayerOpeningPlan,
        PlannedLayerPlacement,
    )


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
    tower_z_band_mm: int = 0
    tower_z_penalty_weight: float = 0.0
    spatial_xy_bin_mm: int = 150
    spatial_tower_penalty_weight: float = 0.0
    spatial_tower_penalty_end_step: int = 0
    spatial_tower_target_base: int = 2
    spatial_tower_target_step_div: int = 6
    hard_floor_phase_end_step: int = 0
    hard_floor_phase_min_base_candidates: int = 1
    hard_floor_phase_lookahead_items: int = 8
    hard_floor_phase_stand_mix_bonus: float = 0.0
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
    use_early_layer_pattern_planner: bool = False
    layer_pattern_prefix_depth: int = 3
    layer_pattern_beam_width: int = 4
    layer_pattern_candidate_cap: int = 8

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
        object.__setattr__(
            self,
            "layer_pattern_prefix_depth",
            max(1, int(self.layer_pattern_prefix_depth)),
        )
        object.__setattr__(
            self,
            "layer_pattern_beam_width",
            max(1, int(self.layer_pattern_beam_width)),
        )
        object.__setattr__(
            self,
            "layer_pattern_candidate_cap",
            max(1, int(self.layer_pattern_candidate_cap)),
        )
        mode = str(self.score_mode or "gain_frag").strip().lower()
        if mode not in ALLOWED_SCORE_MODES:
            raise ValueError(f"SchedulerConfig invalid score_mode: {self.score_mode}")
        object.__setattr__(self, "score_mode", mode)
        object.__setattr__(self, "height_slack_mm", max(0, int(self.height_slack_mm)))
        object.__setattr__(self, "tower_z_band_mm", max(0, int(self.tower_z_band_mm)))
        object.__setattr__(self, "tower_z_penalty_weight", max(0.0, float(self.tower_z_penalty_weight)))
        object.__setattr__(self, "spatial_xy_bin_mm", max(1, int(self.spatial_xy_bin_mm)))
        object.__setattr__(self, "spatial_tower_penalty_weight", max(0.0, float(self.spatial_tower_penalty_weight)))
        object.__setattr__(self, "spatial_tower_penalty_end_step", max(0, int(self.spatial_tower_penalty_end_step)))
        object.__setattr__(self, "spatial_tower_target_base", max(1, int(self.spatial_tower_target_base)))
        object.__setattr__(self, "spatial_tower_target_step_div", max(1, int(self.spatial_tower_target_step_div)))
        object.__setattr__(self, "hard_floor_phase_end_step", max(0, int(self.hard_floor_phase_end_step)))
        object.__setattr__(
            self,
            "hard_floor_phase_min_base_candidates",
            max(1, int(self.hard_floor_phase_min_base_candidates)),
        )
        object.__setattr__(self, "hard_floor_phase_lookahead_items", max(1, int(self.hard_floor_phase_lookahead_items)))
        object.__setattr__(self, "hard_floor_phase_stand_mix_bonus", max(0.0, float(self.hard_floor_phase_stand_mix_bonus)))


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
    tower_z_delta_mm: int = 0
    tower_z_penalty: float = 0.0
    spatial_tower_delta: int = 0
    spatial_tower_penalty: float = 0.0


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


@dataclass(frozen=True)
class _HardFloorScoredCandidate:
    candidate: _ScoredCandidate
    base_score: float
    stand_mix_bonus_applied: bool = False


@dataclass(frozen=True)
class _HardFloorScoredExpansion:
    expansion: _BeamExpansion
    base_score: float
    stand_mix_bonus_applied: bool = False


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
        self.tower_z_penalty_applied_count = 0
        self.tower_z_penalty_sum = 0.0
        self.tower_z_delta_mm_sum = 0
        self.tower_z_selected_count = 0
        self.tower_z_selected_delta_mm_sum = 0
        self.spatial_tower_penalty_applied_count = 0
        self.spatial_tower_penalty_sum = 0.0
        self.spatial_tower_selected_penalty_count = 0
        self.spatial_tower_selected_penalty_sum = 0.0
        self.hard_floor_phase_active_total = 0
        self.hard_floor_phase_floor_candidates_seen_total = 0
        self.hard_floor_phase_chosen_total = 0
        self.hard_floor_phase_stand_hw_chosen_total = 0
        self.hard_floor_phase_exit_no_floor_total = 0
        self.hard_floor_phase_exit_end_step_total = 0
        self.hard_floor_phase_score_sum = 0.0
        self.hard_floor_phase_stand_mix_bonus_applied_total = 0
        self.hard_floor_phase_stand_mix_candidates_total = 0
        self.hard_floor_phase_stand_mix_chosen_total = 0
        self._hard_floor_phase_exited_no_floor_pallets: set[int | str] = set()
        self._hard_floor_phase_exit_end_step_recorded_pallets: set[int | str] = set()
        self._hard_floor_phase_active_counted_this_decision = False
        self._spatial_bin_counts: dict[int | str, dict[tuple[int, int], int]] = {}
        self._spatial_step_index_by_pallet: dict[int | str, int] = {}
        self.batchfill_calls = 0
        self.batchfill_applied = 0
        self.batchfill_selected_boxes_sum = 0
        self.batchfill_selected_boxes_count = 0
        self.pending_layer_plan: deque[PlannedLayerPlacement] = deque()
        self._pending_layer_plan_pallet_id: int | str | None = None
        self._pending_layer_plan_layer_id: int | None = None
        self._pending_layer_plan_planned_len = 0
        self._pending_layer_plan_executed = 0
        self.planner_invocations = 0
        self.planner_abstains = 0
        self.planned_prefix_len_sum = 0
        self.planned_prefix_len_count = 0
        self.planned_prefix_executed_sum = 0
        self.planned_prefix_executed_count = 0
        self.active_committed_layer_index: int | None = None
        self._active_committed_layer_z_mm: int | None = None
        self._active_committed_pallet_id: int | str | None = None
        self._active_layer_commit_started = False
        self._active_layer_commit_min_layer_index: int | None = None
        self.active_layer_commit_replans_total = 0
        self.active_layer_commit_fallback_same_layer_total = 0
        self.active_layer_commit_closures_total = 0
        self._early_layer_pattern_planner: EarlyLayerPatternPlanner | None = None
        self._early_layer_pattern_signature: tuple[int, int, int] | None = None

    def choose_action(self, sim_state: SchedulerSimState) -> PickPlan | None:
        self.last_blocked_pallets = {}
        self.last_deadlock = False
        self.last_deadlock_item = None
        self.last_eval_stats = {}
        self.last_micro_plan_stats = {}
        self.last_micro_feasible_first_candidates = 0
        self._hard_floor_phase_active_counted_this_decision = False
        self._rebuild_spatial_state(sim_state.pallets)
        k = max(1, int(self.config.lookahead_k))

        deadline = None
        if self.config.time_budget_ms and self.config.time_budget_ms > 0:
            deadline = time.perf_counter() + (float(self.config.time_budget_ms) / 1000.0)

        planned_action = self._try_layer_opening_plan(sim_state)
        if planned_action is not None:
            self._record_selected_spatial_tower_penalty(planned_action)
            self._update_spatial_state_from_selected_plan(planned_action)
            selected_height = self._resolve_height_after_mm(planned_action.preview)
            self._record_height_decision(
                selected_height=selected_height,
                min_feasible_height=selected_height,
            )
            self.last_eval_stats = {
                "items_evaluated": 0,
                "items_feasible": 0,
                "cutoff": False,
                "cutoff_reason": "",
                "mode": "early_layer_pattern_planner",
                "planner_invocations": int(self.planner_invocations),
                "planner_abstains": int(self.planner_abstains),
            }
            return planned_action

        micro_enabled = bool(self.config.micro_plan_enabled)
        if self._active_layer_commit_enabled() and self._active_layer_commit_started:
            micro_enabled = False
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
                self._maybe_activate_committed_layer_from_plan(micro_plan)
                self._record_selected_spatial_tower_penalty(micro_plan)
                self._update_spatial_state_from_selected_plan(micro_plan)
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
        if plan is not None:
            self._maybe_activate_committed_layer_from_plan(plan)
            self._record_selected_spatial_tower_penalty(plan)
            self._update_spatial_state_from_selected_plan(plan)
        return plan

    def _ensure_early_layer_pattern_planner(self) -> EarlyLayerPatternPlanner | None:
        if not bool(getattr(self.config, "use_early_layer_pattern_planner", False)):
            self._early_layer_pattern_planner = None
            self._early_layer_pattern_signature = None
            return None

        signature = (
            int(getattr(self.config, "layer_pattern_prefix_depth", 3) or 3),
            int(getattr(self.config, "layer_pattern_beam_width", 4) or 4),
            int(getattr(self.config, "layer_pattern_candidate_cap", 8) or 8),
        )
        if self._early_layer_pattern_planner is None or self._early_layer_pattern_signature != signature:
            from ..integration.early_layer_pattern_planner import EarlyLayerPatternPlanner

            self._early_layer_pattern_planner = EarlyLayerPatternPlanner(
                prefix_depth=int(signature[0]),
                beam_width=int(signature[1]),
                candidate_cap=int(signature[2]),
            )
            self._early_layer_pattern_signature = signature
        return self._early_layer_pattern_planner

    @staticmethod
    def _box_id_matches(lhs: int | str | None, rhs: int | str | None) -> bool:
        if lhs == rhs:
            return True
        return str(lhs) == str(rhs)

    def _active_layer_commit_enabled(self) -> bool:
        return bool(getattr(self.config, "use_early_layer_pattern_planner", False))

    def _reset_active_layer_commit_state(self) -> None:
        self.active_committed_layer_index = None
        self._active_committed_layer_z_mm = None
        self._active_committed_pallet_id = None
        self._active_layer_commit_started = False
        self._active_layer_commit_min_layer_index = None

    def _activate_committed_layer(self, *, pallet_id: int | str, layer_id: int, z_mm: int) -> None:
        self._active_committed_pallet_id = pallet_id
        self.active_committed_layer_index = int(layer_id)
        self._active_committed_layer_z_mm = int(z_mm)
        self._active_layer_commit_started = True
        if self._active_layer_commit_min_layer_index is None:
            self._active_layer_commit_min_layer_index = int(layer_id)
        else:
            self._active_layer_commit_min_layer_index = max(
                int(self._active_layer_commit_min_layer_index),
                int(layer_id),
            )

    def _maybe_activate_committed_layer_from_plan(self, plan: PickPlan | None) -> None:
        if plan is None:
            return
        if not self._active_layer_commit_enabled():
            return
        if self.active_committed_layer_index is not None:
            return
        layer_id = self._preview_layer_id(plan.preview)
        z_mm = self._preview_z_mm(plan.preview)
        if layer_id is None or z_mm is None:
            return
        if int(layer_id) <= 0:
            return
        self._activate_committed_layer(
            pallet_id=plan.pallet_id,
            layer_id=int(layer_id),
            z_mm=int(z_mm),
        )

    def _release_active_committed_layer(self, *, closed: bool) -> None:
        layer_id = self.active_committed_layer_index
        if closed and layer_id is not None:
            self.active_layer_commit_closures_total += 1
            next_layer = int(layer_id) + 1
            if self._active_layer_commit_min_layer_index is None:
                self._active_layer_commit_min_layer_index = int(next_layer)
            else:
                self._active_layer_commit_min_layer_index = max(
                    int(self._active_layer_commit_min_layer_index),
                    int(next_layer),
                )
        self.active_committed_layer_index = None
        self._active_committed_layer_z_mm = None
        self._finalize_pending_layer_plan_metrics(reset_plan=True)

    def _active_layer_commit_allows_preview(
        self,
        *,
        pallet_id: int | str,
        preview: PlacementPreview,
    ) -> bool:
        if not self._active_layer_commit_enabled():
            return True
        if not self._active_layer_commit_started:
            return True

        preview_layer_id = self._preview_layer_id(preview)
        if preview_layer_id is None:
            return False

        active_pallet_id = self._active_committed_pallet_id
        active_layer_id = self.active_committed_layer_index

        if active_layer_id is not None:
            if active_pallet_id != pallet_id or int(preview_layer_id) != int(active_layer_id):
                return False
            active_z_mm = self._active_committed_layer_z_mm
            if active_z_mm is None:
                return True
            preview_z_mm = self._preview_z_mm(preview)
            if preview_z_mm is None:
                return False
            return int(preview_z_mm) >= int(active_z_mm)

        if active_pallet_id is not None and active_pallet_id != pallet_id:
            return False

        min_layer = self._active_layer_commit_min_layer_index
        if min_layer is None:
            return True
        return int(preview_layer_id) >= int(min_layer)

    def _finalize_pending_layer_plan_metrics(self, *, reset_plan: bool) -> None:
        if self._pending_layer_plan_planned_len > 0:
            self.planned_prefix_executed_sum += int(self._pending_layer_plan_executed)
            self.planned_prefix_executed_count += 1
        if reset_plan:
            self.pending_layer_plan.clear()
            self._pending_layer_plan_pallet_id = None
            self._pending_layer_plan_layer_id = None
            self._pending_layer_plan_planned_len = 0
            self._pending_layer_plan_executed = 0

    def _set_pending_layer_plan(self, plan: LayerOpeningPlan) -> None:
        self._finalize_pending_layer_plan_metrics(reset_plan=True)
        self.pending_layer_plan = deque(plan.placements)
        self._pending_layer_plan_pallet_id = plan.pallet_id
        self._pending_layer_plan_layer_id = int(plan.layer_id)
        self._pending_layer_plan_planned_len = int(len(plan.placements))
        self._pending_layer_plan_executed = 0
        self.planned_prefix_len_sum += int(len(plan.placements))
        self.planned_prefix_len_count += 1

    def _consume_pending_layer_plan(self, sim_state: SchedulerSimState) -> PickPlan | None:
        if not self.pending_layer_plan:
            return None
        planned = self.pending_layer_plan[0]
        if self._active_layer_commit_enabled() and self.active_committed_layer_index is not None:
            if (
                planned.pallet_id != self._active_committed_pallet_id
                or int(planned.layer_id) != int(self.active_committed_layer_index)
            ):
                self._finalize_pending_layer_plan_metrics(reset_plan=True)
                return None
        pallet = sim_state.pallets.get(planned.pallet_id)
        if pallet is None or planned.pallet_id in sim_state.pallet_blocked:
            self._finalize_pending_layer_plan_metrics(reset_plan=True)
            return None

        ramp_items = list(sim_state.ramps.get(int(planned.ramp_id), []) or [])
        selected_idx = -1
        selected_box: Box | None = None
        for idx, box in enumerate(ramp_items):
            if getattr(box, "destination", None) != planned.pallet_id:
                continue
            if self._box_id_matches(getattr(box, "box_id", None), planned.box_id):
                selected_idx = int(idx)
                selected_box = box
                break

        if selected_box is None:
            self._finalize_pending_layer_plan_metrics(reset_plan=True)
            return None

        preview = self._preview_place(pallet, selected_box)
        if not preview.feasible:
            self._finalize_pending_layer_plan_metrics(reset_plan=True)
            return None
        preview_layer_id = self._preview_layer_id(preview)
        preview_z_mm = self._preview_z_mm(preview)
        if (
            preview_layer_id is None
            or preview_z_mm is None
            or int(preview_layer_id) != int(planned.layer_id)
            or int(preview_z_mm) != int(planned.z_mm)
        ):
            self._finalize_pending_layer_plan_metrics(reset_plan=True)
            return None
        if self._active_layer_commit_enabled() and self.active_committed_layer_index is not None:
            active_z_mm = self._active_committed_layer_z_mm
            if active_z_mm is not None and int(preview_z_mm) < int(active_z_mm):
                self._finalize_pending_layer_plan_metrics(reset_plan=True)
                return None

        terms = self._score_candidate(
            now=float(sim_state.now),
            box=selected_box,
            idx=int(selected_idx),
            preview=preview,
            max_priority=self._max_priority(ramp_items),
            height_after_mm=self._resolve_height_after_mm(preview, pallet),
        )
        self.pending_layer_plan.popleft()
        self._pending_layer_plan_executed += 1
        if not self.pending_layer_plan:
            self._finalize_pending_layer_plan_metrics(reset_plan=True)
        if self._active_layer_commit_enabled() and self.active_committed_layer_index is not None:
            active_z_mm = self._active_committed_layer_z_mm
            if active_z_mm is None:
                self._active_committed_layer_z_mm = int(preview_z_mm)
            else:
                self._active_committed_layer_z_mm = max(int(active_z_mm), int(preview_z_mm))

        return PickPlan(
            ramp_id=int(planned.ramp_id),
            buffer_index=int(selected_idx),
            box_id=selected_box.box_id,
            pallet_id=planned.pallet_id,
            preview=preview,
            score=float(terms.scalar_score),
            dt_extra=float(terms.dt_extra),
        )

    def _has_layer_opening_transition(
        self,
        *,
        sim_state: SchedulerSimState,
        pallet_id: int | str,
        pallet: PalletModel,
    ) -> bool:
        top_z_mm = 0
        for placement in list(getattr(pallet, "placements", []) or []):
            try:
                top_z_mm = max(int(top_z_mm), int(getattr(placement, "z_mm", 0) or 0))
            except Exception:
                continue
        has_active = False
        has_opening = False

        for ramp_id in sorted(sim_state.ramps):
            for box in list(sim_state.ramps.get(ramp_id, []) or []):
                if getattr(box, "destination", None) != pallet_id:
                    continue
                preview = self._preview_place(pallet, box)
                if not preview.feasible:
                    continue
                z_mm = self._preview_z_mm(preview)
                if z_mm is None:
                    continue
                if int(z_mm) == int(top_z_mm):
                    has_active = True
                if int(z_mm) > int(top_z_mm):
                    has_opening = True
                if has_active and has_opening:
                    break
            if has_active and has_opening:
                break
        return bool(has_opening and not has_active)

    def _try_layer_opening_plan(self, sim_state: SchedulerSimState) -> PickPlan | None:
        planner = self._ensure_early_layer_pattern_planner()
        if planner is None:
            self._finalize_pending_layer_plan_metrics(reset_plan=True)
            self._reset_active_layer_commit_state()
            return None

        if not self._active_layer_commit_enabled():
            pending = self._consume_pending_layer_plan(sim_state)
            if pending is not None:
                return pending
            return self._try_open_next_layer_plan(
                sim_state=sim_state,
                planner=planner,
            )

        if self.active_committed_layer_index is not None:
            pending = self._consume_pending_layer_plan(sim_state)
            if pending is not None:
                return pending

            active_pallet_id = self._active_committed_pallet_id
            active_layer_id = self.active_committed_layer_index
            active_pallet = sim_state.pallets.get(active_pallet_id) if active_pallet_id is not None else None
            if (
                active_pallet_id is None
                or active_layer_id is None
                or active_pallet is None
                or active_pallet_id in sim_state.pallet_blocked
            ):
                self._release_active_committed_layer(closed=False)
            else:
                self.planner_invocations += 1
                self.active_layer_commit_replans_total += 1
                plan = planner.plan_for_layer(
                    pallet_id=active_pallet_id,
                    pallet=active_pallet,
                    ramp_queues=sim_state.ramps,
                    target_layer_id=int(active_layer_id),
                    target_z_mm=None,
                    preview_place_fn=self._preview_place,
                )
                if plan is None or not plan.placements:
                    self.planner_abstains += 1
                else:
                    self._set_pending_layer_plan(plan)
                    pending = self._consume_pending_layer_plan(sim_state)
                    if pending is not None:
                        return pending

                fallback = self._try_same_layer_fallback_greedy(
                    sim_state=sim_state,
                    pallet_id=active_pallet_id,
                    layer_id=int(active_layer_id),
                )
                if fallback is not None:
                    fallback_z_mm = self._preview_z_mm(fallback.preview)
                    if fallback_z_mm is not None:
                        active_z_mm = self._active_committed_layer_z_mm
                        if active_z_mm is None:
                            self._active_committed_layer_z_mm = int(fallback_z_mm)
                        else:
                            self._active_committed_layer_z_mm = max(int(active_z_mm), int(fallback_z_mm))
                    self.active_layer_commit_fallback_same_layer_total += 1
                    return fallback

                self._release_active_committed_layer(closed=True)

        pending = self._consume_pending_layer_plan(sim_state)
        if pending is not None:
            return pending
        return self._try_open_next_layer_plan(
            sim_state=sim_state,
            planner=planner,
        )

    def _try_open_next_layer_plan(
        self,
        *,
        sim_state: SchedulerSimState,
        planner: EarlyLayerPatternPlanner,
    ) -> PickPlan | None:
        for pallet_id, pallet in sorted(sim_state.pallets.items(), key=lambda item: str(item[0])):
            if pallet_id in sim_state.pallet_blocked:
                continue
            if self._active_layer_commit_started and self._active_committed_pallet_id is not None:
                if pallet_id != self._active_committed_pallet_id:
                    continue
            if not self._has_layer_opening_transition(sim_state=sim_state, pallet_id=pallet_id, pallet=pallet):
                continue
            self.planner_invocations += 1
            plan = planner.plan_opening(
                pallet_id=pallet_id,
                pallet=pallet,
                ramp_queues=sim_state.ramps,
                preview_place_fn=self._preview_place,
            )
            if plan is None or not plan.placements:
                self.planner_abstains += 1
                continue
            if self._active_layer_commit_enabled() and self._active_layer_commit_started:
                min_layer = self._active_layer_commit_min_layer_index
                if min_layer is not None and int(plan.layer_id) < int(min_layer):
                    self.planner_abstains += 1
                    continue
            self._set_pending_layer_plan(plan)
            if self._active_layer_commit_enabled():
                self._activate_committed_layer(
                    pallet_id=plan.pallet_id,
                    layer_id=int(plan.layer_id),
                    z_mm=int(plan.z_mm),
                )
            pending = self._consume_pending_layer_plan(sim_state)
            if pending is not None:
                return pending
        return None

    def _try_same_layer_fallback_greedy(
        self,
        *,
        sim_state: SchedulerSimState,
        pallet_id: int | str,
        layer_id: int,
    ) -> PickPlan | None:
        pallet = sim_state.pallets.get(pallet_id)
        if pallet is None or pallet_id in sim_state.pallet_blocked:
            return None

        mode = str(getattr(self.config, "score_mode", ScoreMode.GAIN_FRAG.value) or ScoreMode.GAIN_FRAG.value)
        best_max: tuple[tuple[Any, ...], PickPlan] | None = None
        best_min: tuple[tuple[Any, ...], PickPlan] | None = None

        for ramp_id in sorted(sim_state.ramps):
            ramp_items = list(sim_state.ramps.get(ramp_id, []) or [])
            max_priority = self._max_priority(ramp_items)
            for idx, box in enumerate(ramp_items):
                if getattr(box, "destination", None) != pallet_id:
                    continue
                preview = self._preview_place(pallet, box)
                if not preview.feasible:
                    continue
                preview_layer_id = self._preview_layer_id(preview)
                preview_z_mm = self._preview_z_mm(preview)
                if preview_layer_id is None or preview_z_mm is None:
                    continue
                if int(preview_layer_id) != int(layer_id):
                    continue
                active_z_mm = self._active_committed_layer_z_mm
                if active_z_mm is not None and int(preview_z_mm) < int(active_z_mm):
                    continue

                terms = self._score_candidate(
                    now=float(sim_state.now),
                    box=box,
                    idx=int(idx),
                    preview=preview,
                    max_priority=max_priority,
                    height_after_mm=self._resolve_height_after_mm(preview, pallet),
                )
                plan = PickPlan(
                    ramp_id=int(ramp_id),
                    buffer_index=int(idx),
                    box_id=box.box_id,
                    pallet_id=pallet_id,
                    preview=preview,
                    score=float(terms.scalar_score),
                    dt_extra=float(terms.dt_extra),
                )
                if mode == ScoreMode.MIN_HEIGHT_THEN_GAIN.value:
                    key = self._min_height_then_gain_key(terms=terms, box=box)
                    if best_min is None or key < best_min[0]:
                        best_min = (key, plan)
                else:
                    key = self._gain_frag_sort_key(terms=terms, box=box)
                    if best_max is None or key > best_max[0]:
                        best_max = (key, plan)

        if mode == ScoreMode.MIN_HEIGHT_THEN_GAIN.value:
            return best_min[1] if best_min is not None else None
        return best_max[1] if best_max is not None else None

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
            ramp_items = list(ramp)[: max(1, int(lookahead_k))]
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
                if not self._active_layer_commit_allows_preview(pallet_id=pallet_id, preview=preview):
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

        self.last_eval_stats = {
            "items_evaluated": int(items_evaluated),
            "items_feasible": int(items_feasible),
            "cutoff": bool(cutoff),
            "cutoff_reason": cutoff_reason,
            "batchfill_calls": int(batchfill_stats["batchfill_calls"]),
            "batchfill_applied": int(batchfill_stats["batchfill_applied"]),
            "batchfill_selected_layer_boxes_mean": float(batchfill_stats["batchfill_selected_layer_boxes_mean"]),
        }

        best_plan: PickPlan | None = None
        if feasible_candidates:
            hard_floor_candidates = self._hard_floor_phase_filter_scored_candidates(
                candidates=feasible_candidates,
                pallets=sim_state.pallets,
            )
            selected: _ScoredCandidate | None = None
            slack_stats: SlackDecisionStats | None = None
            if hard_floor_candidates:
                hard_floor_selected = max(
                    hard_floor_candidates,
                    key=lambda item: (
                        float(item.base_score),
                        float(item.candidate.terms.packing_gain),
                        -float(item.candidate.terms.fragmentation),
                        -float(item.candidate.terms.dt_extra),
                    ),
                )
                selected = hard_floor_selected.candidate
                self._record_hard_floor_phase_choice(
                    selected.plan,
                    selected.terms.scalar_score,
                    stand_mix_bonus_applied=bool(hard_floor_selected.stand_mix_bonus_applied),
                )
            else:
                feasible_candidates = self._apply_spatial_tower_penalty_scored_candidates(candidates=feasible_candidates)
                min_feasible_height_after_mm = min(int(c.terms.height_after_mm) for c in feasible_candidates)
                feasible_candidates = self._apply_tower_z_penalty_scored_candidates(
                    candidates=feasible_candidates,
                    min_feasible_height_after_mm=int(min_feasible_height_after_mm),
                )
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
                    min_feasible_height=(
                        slack_stats.min_height_after_mm if slack_stats is not None else selected.terms.height_after_mm
                    ),
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

                if expansions:
                    min_h = min(int(e.terms.height_after_mm) for e in expansions)
                    expansions = self._apply_tower_z_penalty_expansions(
                        expansions=expansions,
                        min_feasible_height_after_mm=int(min_h),
                        adjust_first_plan=bool(depth == 0 and node.first_plan is None),
                    )

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

                if depth == 0 and node.first_plan is None and expansions:
                    expansions = self._apply_spatial_tower_penalty_to_expansions(
                        expansions=expansions,
                        adjust_first_plan=True,
                    )
                    hard_floor_expansions = self._hard_floor_phase_filter_beam_expansions(
                        expansions=expansions,
                        pallets=node.pallets,
                    )
                    if hard_floor_expansions:
                        chosen = max(
                            hard_floor_expansions,
                            key=lambda item: (
                                float(item.base_score),
                                float(item.expansion.terms.packing_gain),
                                -float(item.expansion.terms.fragmentation),
                                -float(item.expansion.terms.dt_extra),
                            ),
                        )
                        chosen_plan = chosen.expansion.node.first_plan
                        if chosen_plan is not None:
                            self._record_hard_floor_phase_choice(
                                chosen_plan,
                                chosen.base_score,
                                stand_mix_bonus_applied=bool(chosen.stand_mix_bonus_applied),
                            )
                            stats = {
                                "enabled": True,
                                "score_mode": str(self.config.score_mode),
                                "depth_limit": int(depth_limit),
                                "width": int(beam_width),
                                "topk_per_step": int(topk_per_step),
                                "nodes_expanded": int(nodes_expanded),
                                "depth_effective": 1,
                                "best_seq_len": 1,
                                "feasible_first_candidates": int(len(hard_floor_expansions)),
                                "feasible_first_min_height_mm": min(
                                    int(item.expansion.terms.height_after_mm) for item in hard_floor_expansions
                                ),
                                "selected_height_after_mm": self._resolve_height_after_mm(chosen_plan.preview),
                                "height_slack_mm": int(self.config.height_slack_mm),
                                "cutoff": bool(cutoff),
                                "cutoff_reason": str(cutoff_reason),
                                "batchfill_calls": int(batchfill_calls_local),
                                "batchfill_applied": int(batchfill_applied_local),
                                "batchfill_selected_layer_boxes_mean": float(
                                    float(batchfill_selected_boxes_sum_local)
                                    / max(1, int(batchfill_selected_boxes_count_local))
                                ),
                            }
                            return chosen_plan, stats, None

                if depth == 0:
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

    @staticmethod
    def _preview_z_mm(preview: PlacementPreview | None) -> int | None:
        if preview is None:
            return None
        placement = getattr(preview, "placement", None)
        if placement is None:
            return None
        try:
            return int(getattr(placement, "z_mm"))
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

    def _hard_floor_phase_enabled(self) -> bool:
        return int(getattr(self.config, "hard_floor_phase_end_step", 0) or 0) > 0

    def _hard_floor_phase_may_be_active(self, pallets: Mapping[int | str, PalletModel]) -> bool:
        if not self._hard_floor_phase_enabled():
            return False
        end_step = int(getattr(self.config, "hard_floor_phase_end_step", 0) or 0)
        for pallet_id, pallet in pallets.items():
            if pallet_id in self._hard_floor_phase_exited_no_floor_pallets:
                continue
            step_idx = int(
                self._spatial_step_index_by_pallet.get(
                    pallet_id,
                    len(list(getattr(pallet, "placements", []) or [])),
                )
            )
            if int(step_idx) < int(end_step):
                return True
        return False

    def _hard_floor_phase_mark_active_decision(self) -> None:
        if self._hard_floor_phase_active_counted_this_decision:
            return
        self.hard_floor_phase_active_total += 1
        self._hard_floor_phase_active_counted_this_decision = True

    def _hard_floor_phase_filter_scored_candidates(
        self,
        *,
        candidates: list[_ScoredCandidate],
        pallets: Mapping[int | str, PalletModel],
    ) -> list[_HardFloorScoredCandidate]:
        if not candidates or not self._hard_floor_phase_enabled():
            return []

        end_step = int(getattr(self.config, "hard_floor_phase_end_step", 0) or 0)
        min_floor = max(1, int(getattr(self.config, "hard_floor_phase_min_base_candidates", 1) or 1))

        by_pallet: dict[int | str, list[_ScoredCandidate]] = {}
        for cand in candidates:
            by_pallet.setdefault(cand.plan.pallet_id, []).append(cand)

        active_found = False
        scored: list[_HardFloorScoredCandidate] = []
        for pallet_id, group in by_pallet.items():
            if pallet_id in self._hard_floor_phase_exited_no_floor_pallets:
                continue

            pallet = pallets.get(pallet_id)
            if pallet is None:
                continue

            step_idx = int(
                self._spatial_step_index_by_pallet.get(
                    pallet_id,
                    len(list(getattr(pallet, "placements", []) or [])),
                )
            )
            if int(step_idx) >= int(end_step):
                if pallet_id not in self._hard_floor_phase_exit_end_step_recorded_pallets:
                    self._hard_floor_phase_exit_end_step_recorded_pallets.add(pallet_id)
                    self.hard_floor_phase_exit_end_step_total += 1
                continue

            active_found = True
            floor_group = [cand for cand in group if self._preview_is_floor(cand.plan.preview)]
            self.hard_floor_phase_floor_candidates_seen_total += int(len(floor_group))
            if len(floor_group) < int(min_floor):
                self._hard_floor_phase_exited_no_floor_pallets.add(pallet_id)
                self.hard_floor_phase_exit_no_floor_total += 1
                continue

            for cand in floor_group:
                base_score = self._hard_floor_phase_score_preview(
                    pallet=pallet,
                    preview=cand.plan.preview,
                    future_boxes=[item.box for item in group],
                    selected_box=cand.box,
                )
                placement = getattr(cand.plan.preview, "placement", None)
                is_stand_hw = self._placement_is_stand_hw(placement)
                if is_stand_hw:
                    self.hard_floor_phase_stand_mix_candidates_total += 1
                scored_value, stand_mix_bonus_applied = self._hard_floor_phase_apply_stand_mix_bonus(
                    base_score=float(base_score),
                    placement=placement,
                )
                if stand_mix_bonus_applied:
                    self.hard_floor_phase_stand_mix_bonus_applied_total += 1
                new_terms = replace(cand.terms, scalar_score=float(scored_value))
                new_plan = replace(cand.plan, score=float(scored_value))
                scored.append(
                    _HardFloorScoredCandidate(
                        candidate=_ScoredCandidate(plan=new_plan, box=cand.box, terms=new_terms),
                        base_score=float(scored_value),
                        stand_mix_bonus_applied=bool(stand_mix_bonus_applied),
                    )
                )

        if active_found:
            self._hard_floor_phase_mark_active_decision()
        return scored

    def _hard_floor_phase_filter_beam_expansions(
        self,
        *,
        expansions: list[_BeamExpansion],
        pallets: Mapping[int | str, PalletModel],
    ) -> list[_HardFloorScoredExpansion]:
        if not expansions or not self._hard_floor_phase_enabled():
            return []

        end_step = int(getattr(self.config, "hard_floor_phase_end_step", 0) or 0)
        min_floor = max(1, int(getattr(self.config, "hard_floor_phase_min_base_candidates", 1) or 1))
        by_pallet: dict[int | str, list[_BeamExpansion]] = {}
        for exp in expansions:
            pallet_id = exp.box.destination
            if pallet_id is None:
                continue
            by_pallet.setdefault(pallet_id, []).append(exp)

        active_found = False
        scored: list[_HardFloorScoredExpansion] = []
        for pallet_id, group in by_pallet.items():
            if pallet_id in self._hard_floor_phase_exited_no_floor_pallets:
                continue

            pallet = pallets.get(pallet_id)
            if pallet is None:
                continue

            step_idx = int(
                self._spatial_step_index_by_pallet.get(
                    pallet_id,
                    len(list(getattr(pallet, "placements", []) or [])),
                )
            )
            if int(step_idx) >= int(end_step):
                if pallet_id not in self._hard_floor_phase_exit_end_step_recorded_pallets:
                    self._hard_floor_phase_exit_end_step_recorded_pallets.add(pallet_id)
                    self.hard_floor_phase_exit_end_step_total += 1
                continue

            active_found = True
            floor_group = [
                exp
                for exp in group
                if exp.node.first_plan is not None and self._preview_is_floor(exp.node.first_plan.preview)
            ]
            self.hard_floor_phase_floor_candidates_seen_total += int(len(floor_group))
            if len(floor_group) < int(min_floor):
                self._hard_floor_phase_exited_no_floor_pallets.add(pallet_id)
                self.hard_floor_phase_exit_no_floor_total += 1
                continue

            for exp in floor_group:
                assert exp.node.first_plan is not None
                base_score = self._hard_floor_phase_score_preview(
                    pallet=pallet,
                    preview=exp.node.first_plan.preview,
                    future_boxes=[item.box for item in group],
                    selected_box=exp.box,
                )
                placement = getattr(exp.node.first_plan.preview, "placement", None)
                is_stand_hw = self._placement_is_stand_hw(placement)
                if is_stand_hw:
                    self.hard_floor_phase_stand_mix_candidates_total += 1
                scored_value, stand_mix_bonus_applied = self._hard_floor_phase_apply_stand_mix_bonus(
                    base_score=float(base_score),
                    placement=placement,
                )
                if stand_mix_bonus_applied:
                    self.hard_floor_phase_stand_mix_bonus_applied_total += 1
                exp.node.score_sum = float(scored_value)
                exp.node.first_plan = replace(exp.node.first_plan, score=float(scored_value))
                new_terms = replace(exp.terms, scalar_score=float(scored_value))
                scored.append(
                    _HardFloorScoredExpansion(
                        expansion=_BeamExpansion(node=exp.node, box=exp.box, terms=new_terms),
                        base_score=float(scored_value),
                        stand_mix_bonus_applied=bool(stand_mix_bonus_applied),
                    )
                )

        if active_found:
            self._hard_floor_phase_mark_active_decision()
        return scored

    @staticmethod
    def _preview_is_floor(preview: PlacementPreview | None) -> bool:
        if preview is None:
            return False
        placement = getattr(preview, "placement", None)
        if placement is None:
            return False
        try:
            return int(getattr(placement, "z_mm", 0) or 0) == 0
        except Exception:
            return False

    @staticmethod
    def _placement_is_stand_hw(placement: object | None) -> bool:
        if placement is None:
            return False
        family = str(getattr(placement, "orientation_family", "") or "").lower()
        name = str(getattr(placement, "orientation_name", "") or "").lower()
        return family == "stand_hw" or "stand_hw" in name

    def _hard_floor_phase_apply_stand_mix_bonus(
        self,
        *,
        base_score: float,
        placement: object | None,
    ) -> tuple[float, bool]:
        bonus = float(getattr(self.config, "hard_floor_phase_stand_mix_bonus", 0.0) or 0.0)
        if bonus <= 0.0 or not self._placement_is_stand_hw(placement):
            return float(base_score), False
        return float(base_score) + float(bonus), True

    @staticmethod
    def _rectangles_touch(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        overlap_x = min(ax1, bx1) - max(ax0, bx0)
        overlap_y = min(ay1, by1) - max(ay0, by0)
        touch_x = overlap_x > 0 and (ay1 == by0 or by1 == ay0)
        touch_y = overlap_y > 0 and (ax1 == bx0 or bx1 == ax0)
        return bool(touch_x or touch_y)

    def _hard_floor_phase_score_preview(
        self,
        *,
        pallet: PalletModel,
        preview: PlacementPreview,
        future_boxes: Sequence[Box] | None = None,
        selected_box: Box | None = None,
    ) -> float:
        placement = getattr(preview, "placement", None)
        if placement is None:
            return -1e9

        floor_rects: list[tuple[int, int, int, int]] = []
        stand_count = 0
        for p in list(getattr(pallet, "placements", []) or []):
            try:
                z_mm = int(getattr(p, "z_mm", 0) or 0)
            except Exception:
                continue
            if z_mm != 0:
                continue
            try:
                x0 = int(getattr(p, "x_mm", 0) or 0)
                y0 = int(getattr(p, "y_mm", 0) or 0)
                x1 = x0 + int(getattr(p, "length_mm", 0) or 0)
                y1 = y0 + int(getattr(p, "width_mm", 0) or 0)
            except Exception:
                continue
            floor_rects.append((x0, y0, x1, y1))
            if self._placement_is_stand_hw(p):
                stand_count += 1

        try:
            px0 = int(getattr(placement, "x_mm", 0) or 0)
            py0 = int(getattr(placement, "y_mm", 0) or 0)
            px1 = px0 + int(getattr(placement, "length_mm", 0) or 0)
            py1 = py0 + int(getattr(placement, "width_mm", 0) or 0)
        except Exception:
            return -1e9
        candidate_rect = (px0, py0, px1, py1)
        floor_rects_after = floor_rects + [candidate_rect]

        areas = [max(0, (x1 - x0)) * max(0, (y1 - y0)) for (x0, y0, x1, y1) in floor_rects_after]
        area_sum = float(sum(areas))
        bin_area = float(max(1, int(getattr(pallet, "bin_area_mm2", 1) or 1)))
        coverage_ratio = area_sum / bin_area

        min_x = min(r[0] for r in floor_rects_after)
        min_y = min(r[1] for r in floor_rects_after)
        max_x = max(r[2] for r in floor_rects_after)
        max_y = max(r[3] for r in floor_rects_after)
        bbox_w = max(1, int(max_x - min_x))
        bbox_h = max(1, int(max_y - min_y))
        bbox_area = float(max(1, bbox_w * bbox_h))
        compactness = area_sum / bbox_area
        dead_gap_ratio = max(0.0, bbox_area - area_sum) / bin_area
        elongation_penalty = abs(float(bbox_w) - float(bbox_h)) / float(max(bbox_w, bbox_h))

        bin_mm = max(1, int(getattr(self.config, "spatial_xy_bin_mm", 150) or 150))
        bin_counts: dict[tuple[int, int], int] = {}
        for rect in floor_rects_after:
            bx = int(rect[0]) // int(bin_mm)
            by = int(rect[1]) // int(bin_mm)
            key = (bx, by)
            bin_counts[key] = int(bin_counts.get(key, 0)) + 1
        total_floor = max(1, len(floor_rects_after))
        dominant_ratio = float(max(bin_counts.values())) / float(total_floor)
        dominance_penalty = max(0.0, dominant_ratio - 0.45)

        bbox_bins_x = max(1, (bbox_w + bin_mm - 1) // bin_mm)
        bbox_bins_y = max(1, (bbox_h + bin_mm - 1) // bin_mm)
        potential_bins = max(1, bbox_bins_x * bbox_bins_y)
        occupied_bins = len(bin_counts)
        continuity_ratio = float(occupied_bins) / float(potential_bins)

        touches = 0
        for rect in floor_rects:
            if self._rectangles_touch(rect, candidate_rect):
                touches += 1
        adjacency_ratio = float(touches) / float(max(1, len(floor_rects)))

        stand_after = int(stand_count) + (1 if self._placement_is_stand_hw(placement) else 0)
        stand_mix_ratio = float(stand_after) / float(total_floor)
        stand_mix_bonus = 1.0 - abs(stand_mix_ratio - 0.35)
        stand_early_bonus = 0.0
        if total_floor <= 4 and self._placement_is_stand_hw(placement):
            stand_early_bonus = 1.0

        next_floor_options = 0
        future_total = 0
        if future_boxes:
            try:
                pallet_clone = copy.deepcopy(pallet)
                if selected_box is not None:
                    chosen_preview = self._preview_place(pallet_clone, selected_box)
                    if chosen_preview.feasible:
                        pallet_clone.commit_place(chosen_preview)
                selected_consumed = False
                selected_box_id = getattr(selected_box, "box_id", None) if selected_box is not None else None
                lookahead_cap = max(1, int(getattr(self.config, "hard_floor_phase_lookahead_items", 8) or 8))
                for box in list(future_boxes)[:lookahead_cap]:
                    if selected_box is not None and not selected_consumed:
                        if box is selected_box or getattr(box, "box_id", None) == selected_box_id:
                            selected_consumed = True
                            continue
                    future_total += 1
                    future_preview = self._preview_place(pallet_clone, box)
                    if future_preview.feasible and self._preview_is_floor(future_preview):
                        next_floor_options += 1
            except Exception:
                next_floor_options = 0
                future_total = 0
        next_floor_ratio = float(next_floor_options) / float(max(1, future_total))

        early_l_penalty = 0.0
        if total_floor >= 3 and compactness < 0.72 and elongation_penalty > 0.45:
            early_l_penalty = 1.0

        return (
            3.2 * float(coverage_ratio)
            + 1.6 * float(compactness)
            + 0.6 * float(adjacency_ratio)
            + 0.6 * float(continuity_ratio)
            + 0.8 * float(next_floor_ratio)
            - 1.8 * float(dominance_penalty)
            - 1.2 * float(dead_gap_ratio)
            - 0.7 * float(elongation_penalty)
            - 0.6 * float(early_l_penalty)
            + 0.20 * float(stand_mix_bonus)
            + 0.15 * float(stand_early_bonus)
        )

    def _record_hard_floor_phase_choice(
        self,
        plan: PickPlan,
        base_score: float,
        *,
        stand_mix_bonus_applied: bool = False,
    ) -> None:
        self.hard_floor_phase_chosen_total += 1
        self.hard_floor_phase_score_sum += float(base_score)
        placement = getattr(plan.preview, "placement", None)
        if self._placement_is_stand_hw(placement):
            self.hard_floor_phase_stand_hw_chosen_total += 1
            if stand_mix_bonus_applied:
                self.hard_floor_phase_stand_mix_chosen_total += 1

    def _spatial_tower_penalty_for_after_count(
        self,
        *,
        after_count: int,
        step_idx: int,
    ) -> tuple[int, float]:
        weight = float(getattr(self.config, "spatial_tower_penalty_weight", 0.0) or 0.0)
        end_step = int(getattr(self.config, "spatial_tower_penalty_end_step", 0) or 0)
        if weight <= 0.0 or end_step <= 0 or int(step_idx) >= end_step:
            return 0, 0.0
        target_base = int(getattr(self.config, "spatial_tower_target_base", 2) or 2)
        target_step_div = max(1, int(getattr(self.config, "spatial_tower_target_step_div", 6) or 6))
        target = int(target_base) + (int(step_idx) // int(target_step_div))
        delta = max(0, int(after_count) - int(target))
        penalty = float(weight) * float(delta)
        if penalty <= 0.0:
            return 0, 0.0
        return int(delta), float(penalty)

    def _apply_spatial_tower_penalty_scored_candidates(
        self,
        *,
        candidates: list[_ScoredCandidate],
    ) -> list[_ScoredCandidate]:
        weight = float(getattr(self.config, "spatial_tower_penalty_weight", 0.0) or 0.0)
        end_step = int(getattr(self.config, "spatial_tower_penalty_end_step", 0) or 0)
        if weight <= 0.0 or end_step <= 0:
            return candidates

        out: list[_ScoredCandidate] = []
        for cand in candidates:
            pallet_id = cand.plan.pallet_id
            step_idx = int(self._spatial_step_index_by_pallet.get(pallet_id, 0))
            counts = self._spatial_bin_counts.get(pallet_id, {})
            bx_by = self._placement_bin_xy(getattr(cand.plan.preview, "placement", None))
            if bx_by is None:
                out.append(cand)
                continue
            current_count = int(counts.get(bx_by, 0))
            after_count = int(current_count) + 1
            delta, penalty = self._spatial_tower_penalty_for_after_count(
                after_count=after_count,
                step_idx=step_idx,
            )
            if penalty > 0.0:
                self.spatial_tower_penalty_applied_count += 1
                self.spatial_tower_penalty_sum += float(penalty)
                new_terms = replace(
                    cand.terms,
                    scalar_score=float(cand.terms.scalar_score) - float(penalty),
                    spatial_tower_delta=int(delta),
                    spatial_tower_penalty=float(penalty),
                )
                new_plan = replace(cand.plan, score=float(new_terms.scalar_score))
                out.append(_ScoredCandidate(plan=new_plan, box=cand.box, terms=new_terms))
                continue
            out.append(cand)
        return out

    def _apply_spatial_tower_penalty_to_expansions(
        self,
        *,
        expansions: list[_BeamExpansion],
        adjust_first_plan: bool,
    ) -> list[_BeamExpansion]:
        weight = float(getattr(self.config, "spatial_tower_penalty_weight", 0.0) or 0.0)
        end_step = int(getattr(self.config, "spatial_tower_penalty_end_step", 0) or 0)
        if weight <= 0.0 or end_step <= 0:
            return expansions

        out: list[_BeamExpansion] = []
        for exp in expansions:
            pallet_id = exp.box.destination
            step_idx = int(self._spatial_step_index_by_pallet.get(pallet_id, 0))
            counts = self._spatial_bin_counts.get(pallet_id, {})
            placement = None
            if exp.node.first_plan is not None:
                placement = getattr(exp.node.first_plan.preview, "placement", None)
            bx_by = self._placement_bin_xy(placement)
            if bx_by is None:
                out.append(exp)
                continue
            current_count = int(counts.get(bx_by, 0))
            after_count = int(current_count) + 1
            delta, penalty = self._spatial_tower_penalty_for_after_count(
                after_count=after_count,
                step_idx=step_idx,
            )
            if penalty > 0.0:
                self.spatial_tower_penalty_applied_count += 1
                self.spatial_tower_penalty_sum += float(penalty)
                exp.node.score_sum = float(exp.node.score_sum) - float(penalty)
                if adjust_first_plan and exp.node.first_plan is not None:
                    exp.node.first_plan = replace(exp.node.first_plan, score=float(exp.node.first_plan.score) - float(penalty))
                new_terms = replace(
                    exp.terms,
                    scalar_score=float(exp.terms.scalar_score) - float(penalty),
                    spatial_tower_delta=int(delta),
                    spatial_tower_penalty=float(penalty),
                )
                out.append(_BeamExpansion(node=exp.node, box=exp.box, terms=new_terms))
                continue
            out.append(exp)
        return out

    def _rebuild_spatial_state(self, pallets: Mapping[int | str, PalletModel]) -> None:
        prev_step_index = dict(self._spatial_step_index_by_pallet)
        self._spatial_bin_counts = {}
        self._spatial_step_index_by_pallet = {}
        bin_mm = max(1, int(getattr(self.config, "spatial_xy_bin_mm", 150) or 150))
        for pallet_id, pallet in pallets.items():
            counts: dict[tuple[int, int], int] = {}
            step_idx = 0
            for placement in list(getattr(pallet, "placements", []) or []):
                step_idx += 1
                xy = self._placement_xy_mm(placement)
                if xy is None:
                    continue
                bx = int(xy[0]) // bin_mm
                by = int(xy[1]) // bin_mm
                key = (int(bx), int(by))
                counts[key] = int(counts.get(key, 0)) + 1
            self._spatial_bin_counts[pallet_id] = counts
            self._spatial_step_index_by_pallet[pallet_id] = int(step_idx)
            prev_idx = int(prev_step_index.get(pallet_id, 0) or 0)
            if int(step_idx) == 0 and prev_idx > 0:
                self._hard_floor_phase_exited_no_floor_pallets.discard(pallet_id)
                self._hard_floor_phase_exit_end_step_recorded_pallets.discard(pallet_id)

        live_ids = set(pallets.keys())
        self._hard_floor_phase_exited_no_floor_pallets = {
            pid for pid in self._hard_floor_phase_exited_no_floor_pallets if pid in live_ids
        }
        self._hard_floor_phase_exit_end_step_recorded_pallets = {
            pid for pid in self._hard_floor_phase_exit_end_step_recorded_pallets if pid in live_ids
        }

    def _update_spatial_state_from_selected_plan(self, plan: PickPlan) -> None:
        pallet_id = plan.pallet_id
        current_step = int(self._spatial_step_index_by_pallet.get(pallet_id, 0))
        self._spatial_step_index_by_pallet[pallet_id] = current_step + 1

        counts = self._spatial_bin_counts.setdefault(pallet_id, {})
        placement = getattr(plan.preview, "placement", None)
        bx_by = self._placement_bin_xy(placement)
        if bx_by is None:
            return
        counts[bx_by] = int(counts.get(bx_by, 0)) + 1

    def _record_selected_spatial_tower_penalty(self, plan: PickPlan) -> None:
        pallet_id = plan.pallet_id
        step_idx = int(self._spatial_step_index_by_pallet.get(pallet_id, 0))
        counts = self._spatial_bin_counts.get(pallet_id, {})
        placement = getattr(plan.preview, "placement", None)
        bx_by = self._placement_bin_xy(placement)
        if bx_by is None:
            return
        current_count = int(counts.get(bx_by, 0))
        after_count = int(current_count) + 1
        _delta, penalty = self._spatial_tower_penalty_for_after_count(
            after_count=after_count,
            step_idx=step_idx,
        )
        if penalty > 0.0:
            self.spatial_tower_selected_penalty_count += 1
            self.spatial_tower_selected_penalty_sum += float(penalty)

    @staticmethod
    def _placement_xy_mm(placement: object | None) -> tuple[int, int] | None:
        if placement is None:
            return None
        try:
            x_mm = int(getattr(placement, "x_mm"))
            y_mm = int(getattr(placement, "y_mm"))
        except Exception:
            return None
        return int(x_mm), int(y_mm)

    def _placement_bin_xy(self, placement: object | None) -> tuple[int, int] | None:
        xy = self._placement_xy_mm(placement)
        if xy is None:
            return None
        bin_mm = max(1, int(getattr(self.config, "spatial_xy_bin_mm", 150) or 150))
        return int(xy[0]) // bin_mm, int(xy[1]) // bin_mm


    def _apply_tower_z_penalty_scored_candidates(
        self,
        *,
        candidates: list[_ScoredCandidate],
        min_feasible_height_after_mm: int,
    ) -> list[_ScoredCandidate]:
        weight = float(getattr(self.config, "tower_z_penalty_weight", 0.0) or 0.0)
        if weight <= 0.0:
            return candidates
        band = max(0, int(getattr(self.config, "tower_z_band_mm", 0) or 0))
        base = int(min_feasible_height_after_mm)

        out: list[_ScoredCandidate] = []
        for cand in candidates:
            h = int(cand.terms.height_after_mm)
            delta = max(0, h - (base + band))
            penalty = float(weight) * (float(delta) / 1000.0) if delta > 0 else 0.0
            if penalty > 0.0:
                self.tower_z_penalty_applied_count += 1
                self.tower_z_penalty_sum += float(penalty)
                self.tower_z_delta_mm_sum += int(delta)
            new_terms = cand.terms
            new_plan = cand.plan
            if penalty > 0.0:
                from dataclasses import replace
                new_terms = replace(
                    cand.terms,
                    scalar_score=float(cand.terms.scalar_score) - float(penalty),
                    tower_z_delta_mm=int(delta),
                    tower_z_penalty=float(penalty),
                )
                new_plan = replace(cand.plan, score=float(new_terms.scalar_score))
            out.append(_ScoredCandidate(plan=new_plan, box=cand.box, terms=new_terms))
        return out

    def _apply_tower_z_penalty_expansions(
        self,
        *,
        expansions: list[_BeamExpansion],
        min_feasible_height_after_mm: int,
        adjust_first_plan: bool,
    ) -> list[_BeamExpansion]:
        weight = float(getattr(self.config, "tower_z_penalty_weight", 0.0) or 0.0)
        if weight <= 0.0:
            return expansions
        band = max(0, int(getattr(self.config, "tower_z_band_mm", 0) or 0))
        base = int(min_feasible_height_after_mm)

        out: list[_BeamExpansion] = []
        for exp in expansions:
            h = int(exp.terms.height_after_mm)
            delta = max(0, h - (base + band))
            penalty = float(weight) * (float(delta) / 1000.0) if delta > 0 else 0.0
            if penalty > 0.0:
                self.tower_z_penalty_applied_count += 1
                self.tower_z_penalty_sum += float(penalty)
                self.tower_z_delta_mm_sum += int(delta)
                # Important: keep node.score_sum consistent with penalized scalar_score
                exp.node.score_sum = float(exp.node.score_sum) - float(penalty)
                if adjust_first_plan and exp.node.first_plan is not None:
                    from dataclasses import replace
                    exp.node.first_plan = replace(exp.node.first_plan, score=float(exp.node.first_plan.score) - float(penalty))
            new_terms = exp.terms
            if penalty > 0.0:
                from dataclasses import replace
                new_terms = replace(
                    exp.terms,
                    scalar_score=float(exp.terms.scalar_score) - float(penalty),
                    tower_z_delta_mm=int(delta),
                    tower_z_penalty=float(penalty),
                )
            out.append(_BeamExpansion(node=exp.node, box=exp.box, terms=new_terms))
        return out

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
        should_relax_stand_gate = self._hard_floor_phase_stand_mix_gate_enabled_for_pallet(pallet)
        original_stand_gate = None
        if should_relax_stand_gate:
            try:
                original_stand_gate = int(getattr(pallet, "stand_hw_height_margin_gate_mm", 0) or 0)
                setattr(pallet, "stand_hw_height_margin_gate_mm", max(int(original_stand_gate), 1_000_000_000))
            except Exception:
                should_relax_stand_gate = False
                original_stand_gate = None
        try:
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
        finally:
            if should_relax_stand_gate and original_stand_gate is not None:
                try:
                    setattr(pallet, "stand_hw_height_margin_gate_mm", int(original_stand_gate))
                except Exception:
                    pass

    def _hard_floor_phase_stand_mix_gate_enabled_for_pallet(self, pallet: PalletModel) -> bool:
        if not self._hard_floor_phase_enabled():
            return False
        bonus = float(getattr(self.config, "hard_floor_phase_stand_mix_bonus", 0.0) or 0.0)
        if bonus <= 0.0:
            return False
        end_step = int(getattr(self.config, "hard_floor_phase_end_step", 0) or 0)
        if end_step <= 0:
            return False
        try:
            step_idx = len(list(getattr(pallet, "placements", []) or []))
        except Exception:
            step_idx = 0
        return int(step_idx) < int(end_step)

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
