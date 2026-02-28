from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import logging
import os
from typing import Any, Iterable, Mapping

from ..domain.box import Box
from ..domain.pallet_spec import PalletSpec
from ..packer.controls import BalanceConfig, ControlConfig, LoadBearConfig, StabilityConfig
from ..packer.pallet_model import PalletModel
from ..packer.scoring import ScoringWeights
from ..scheduler.scheduler_v1 import PickPlan, SchedulerConfig, SchedulerRampState, SchedulerSimState, SchedulerV1
from ..control.controller import OnlineController
from ..control.types import ControllerEvent, ControllerMode, DecisionContext, Overrides
from .kpi_hooks import aggregate_pallet_kpis


SUPPORTED_LOOKAHEAD_K = (1, 3, 5, 10, 15)
RESCUE_RETRY_REASONS = {"STABILITY", "NO_FEASIBLE"}


@dataclass(frozen=True)
class PolicyConfig:
    pallet_spec: PalletSpec = PalletSpec()
    heuristic: str = "baf"
    scoring_weights: ScoringWeights = ScoringWeights()
    scheduler: SchedulerConfig = SchedulerConfig()
    default_box_length_mm: int = 400
    default_box_width_mm: int = 300
    default_box_height_mm: int = 200
    stability_mode: str = "ratio+corners"
    min_support_ratio: float = 0.75
    stability_eps_mm: float = 1.0
    settle_snap_grid: bool = False
    grid_mm: int | None = None
    heavy_bottom: bool = False
    max_overweight_ratio: float = 1.5
    loadbear_penalty_weight: float = 1.0
    loadbear_factor: float = 1.0
    balance_weight: float = 0.0
    score_mode: str = "gain_frag"
    height_slack_mm: int = 0
    orientation_mode: str = "planar"
    stand_hw_height_margin_gate_mm: int = 400
    priority_mode: str = "none"
    max_tries_per_item: int = 0
    max_candidates: int = 0
    max_seconds_per_item: float = 0.0
    heartbeat_sec: float = 1.0
    settle_max_iter: int = 0
    settle_timeout_ms: int = 0
    micro_plan_enabled: bool = False
    micro_plan_depth: int = 3
    micro_plan_width: int = 8
    micro_plan_topk_per_step: int = 15
    online_controller: bool = False
    controller_debug: bool = False


class PolicyPackerScheduler:
    def __init__(self, config: PolicyConfig | None = None, logger: logging.Logger | None = None) -> None:
        self.config = config or PolicyConfig()
        self._scheduler = SchedulerV1(self.config.scheduler)
        self._logger = logger or logging.getLogger(__name__)

        self._pallets: dict[int | str, PalletModel] = {}
        self._completed: dict[int | str, list[PalletModel]] = {}
        self._pending_closures: dict[int | str, str] = {}
        self._viewer = None
        self._viewer_rect_cls = None
        self._viewer_event_count = 0

        self._viz_debug = bool(int(os.getenv("PALCA_VIZ_DEBUG", "0") or "0"))
        self._viz_print_every = int(os.getenv("PALCA_VIZ_PRINT_EVERY", "100") or "100")
        self._dest_indexing: str | None = None  # None | 'zero_based' | 'one_based'

        # KPI de cierres
        self._closures_by_reason: dict[str, int] = {}
        self._closed_early: dict[int | str, int] = {}
        self._closed_early_by_reason: dict[str, int] = {}

        # stop conditions
        self.stop_reason: str | None = None
        self.stop_details: dict[str, Any] = {}

        # KPI de lookahead
        self.total_picks = 0
        self.non_head_picks = 0
        self.sum_pick_index = 0
        self.dt_extra_total = 0.0
        self.dt_extra_non_head_total = 0.0

        scheduler_cfg = self.config.scheduler
        self._online_controller_enabled = bool(self.config.online_controller)
        self._controller_debug = bool(self.config.controller_debug)
        self._controller_debug_max_events = 200
        self._controller_debug_events: list[dict[str, object]] = []
        self._controller_mode_counts: dict[str, int] = {mode.value: 0 for mode in ControllerMode}
        self._controller_transitions: list[dict[str, object]] = []
        self._controller_overrides_applied: dict[str, int] = {}
        self._controller_retry_attempts_total = 0
        self._controller_retry_success_total = 0
        self._controller_retry_fail_total = 0
        self._controller_retry_by_reason: dict[str, int] = {}
        self._controller_retry_skipped_by_reason: dict[str, int] = {}
        self._controller_consec_ok = 0
        self._controller_consec_fail = 0
        self._controller_consecutive_failures_max = 0
        self._controller_last_ok = True
        self._controller_last_fail_reason: str | None = None
        self._controller_pick_index = 0
        self._controller: OnlineController | None = None
        if self._online_controller_enabled:
            self._controller = OnlineController(
                baseline_score_mode=str(scheduler_cfg.score_mode),
                baseline_height_slack_mm=int(scheduler_cfg.height_slack_mm),
                baseline_micro_depth=int(scheduler_cfg.micro_plan_depth),
                baseline_micro_width=int(scheduler_cfg.micro_plan_width),
                baseline_micro_topk=int(scheduler_cfg.micro_plan_topk_per_step),
                baseline_time_budget_ms=int(scheduler_cfg.time_budget_ms),
            )

    @classmethod
    def from_defaults(
        cls,
        *,
        lookahead_k: int = 1,
        overhang_mm: int = 0,
        heuristic: str = "baf",
        t_select_base: float = 0.0,
        t_select_step: float = 0.0,
        time_penalty_weight: float = 1.0,
        starvation_weight: float = 0.0,
        time_budget_ms: int = 120,
        priority_weight: float = 1.0,
        stability_mode: str = "ratio+corners",
        min_support_ratio: float = 0.75,
        stability_eps_mm: float = 1.0,
        settle_snap_grid: bool = False,
        grid_mm: int | None = None,
        heavy_bottom: bool = False,
        max_overweight_ratio: float = 1.5,
        loadbear_penalty_weight: float = 1.0,
        loadbear_factor: float = 1.0,
        balance_weight: float = 0.0,
        score_mode: str = "gain_frag",
        height_slack_mm: int = 0,
        orientation_mode: str = "planar",
        stand_hw_height_margin_gate_mm: int = 400,
        priority_mode: str = "none",
        max_tries_per_item: int = 0,
        max_candidates: int = 0,
        max_seconds_per_item: float = 0.0,
        heartbeat_sec: float = 1.0,
        settle_max_iter: int = 0,
        settle_timeout_ms: int = 0,
        micro_plan_enabled: bool = False,
        micro_plan_depth: int = 3,
        micro_plan_width: int = 8,
        micro_plan_topk_per_step: int = 15,
        online_controller: bool = False,
        controller_debug: bool = False,
    ) -> "PolicyPackerScheduler":
        if lookahead_k not in SUPPORTED_LOOKAHEAD_K:
            raise ValueError(f"K no soportado: {lookahead_k}")
        pallet_spec = PalletSpec(overhang_mm=overhang_mm)
        scheduler = SchedulerConfig(
            lookahead_k=lookahead_k,
            t_select_base=t_select_base,
            t_select_step=t_select_step,
            time_penalty_weight=time_penalty_weight,
            starvation_weight=starvation_weight,
            time_budget_ms=time_budget_ms,
            priority_weight=priority_weight,
            score_mode=score_mode,
            height_slack_mm=height_slack_mm,
            max_tries_per_item=max_tries_per_item,
            max_candidates=max_candidates,
            max_seconds_per_item=max_seconds_per_item,
            heartbeat_sec=heartbeat_sec,
            micro_plan_enabled=micro_plan_enabled,
            micro_plan_depth=micro_plan_depth,
            micro_plan_width=micro_plan_width,
            micro_plan_topk_per_step=micro_plan_topk_per_step,
        )
        config = PolicyConfig(
            pallet_spec=pallet_spec,
            heuristic=heuristic,
            scheduler=scheduler,
            stability_mode=stability_mode,
            min_support_ratio=min_support_ratio,
            stability_eps_mm=stability_eps_mm,
            settle_snap_grid=settle_snap_grid,
            grid_mm=grid_mm,
            heavy_bottom=heavy_bottom,
            max_overweight_ratio=max_overweight_ratio,
            loadbear_penalty_weight=loadbear_penalty_weight,
            loadbear_factor=loadbear_factor,
            balance_weight=balance_weight,
            score_mode=score_mode,
            height_slack_mm=max(0, int(height_slack_mm)),
            orientation_mode=str(orientation_mode),
            stand_hw_height_margin_gate_mm=max(0, int(stand_hw_height_margin_gate_mm)),
            priority_mode=priority_mode,
            max_tries_per_item=max_tries_per_item,
            max_candidates=max_candidates,
            max_seconds_per_item=max_seconds_per_item,
            heartbeat_sec=heartbeat_sec,
            settle_max_iter=settle_max_iter,
            settle_timeout_ms=settle_timeout_ms,
            micro_plan_enabled=micro_plan_enabled,
            micro_plan_depth=micro_plan_depth,
            micro_plan_width=micro_plan_width,
            micro_plan_topk_per_step=micro_plan_topk_per_step,
            online_controller=online_controller,
            controller_debug=controller_debug,
        )
        return cls(config=config)

    def choose_action(
        self,
        *,
        ramps: Mapping[int, Any],
        destinations: Mapping[int, Any],
        now: float,
    ) -> PickPlan | None:
        self.stop_reason = None
        self.stop_details = {}
        pallets = self._collect_pallets(ramps)
        ramp_boxes = self._collect_ramp_boxes(ramps)
        ramp_states = self._collect_ramp_states(ramps)
        blocked = {
            dest_id
            for dest_id, state in destinations.items()
            if getattr(state, "state", "ACTIVE") != "ACTIVE"
        }

        ramp_sizes: dict[int, int] = {}
        remaining_total = 0
        for ramp_id, ramp in ramps.items():
            queue = getattr(ramp, "queue", [])
            upstream = getattr(ramp, "upstream", [])
            staging = getattr(ramp, "staging", [])
            count = len(queue) + len(upstream) + len(staging)
            ramp_sizes[int(ramp_id)] = count
            remaining_total += count

        sim_state = SchedulerSimState(
            now=float(now),
            ramps=ramp_boxes,
            ramp_states=ramp_states,
            pallets=pallets,
            pallet_blocked=blocked,
            ramp_sizes=ramp_sizes,
            remaining_total=int(remaining_total),
        )

        overrides = Overrides()
        override_attempts: list[Overrides] = []
        controller_events: list[ControllerEvent] = []
        if self._online_controller_enabled and self._controller is not None:
            controller_ctx = DecisionContext(
                last_ok=bool(self._controller_last_ok),
                last_fail_reason=self._controller_last_fail_reason,
                consec_ok=int(self._controller_consec_ok),
                consec_fail=int(self._controller_consec_fail),
                pick_index=int(self._controller_pick_index),
            )
            controller_overrides, controller_event = self._controller.step(controller_ctx)
            overrides = controller_overrides
            if controller_event is not None:
                controller_events.append(controller_event)
        override_attempts.append(overrides)

        plan, fail_reason, pending_closures, stop_reason, stop_details = self._run_scheduler_attempt(
            sim_state=sim_state,
            overrides=overrides,
        )
        self._record_controller_debug_attempt(
            attempt_index=1,
            mode=self._controller.mode if self._controller is not None else ControllerMode.NORMAL,
            overrides=overrides,
            plan=plan,
            fail_reason=fail_reason,
        )

        treat_fail_as_normal_close = False
        if (
            plan is None
            and self._online_controller_enabled
            and self._controller is not None
        ):
            retry_reason = self._normalize_retry_reason(fail_reason)
            if retry_reason == "HEIGHT_LIMIT":
                self._controller_retry_skipped_by_reason[retry_reason] = int(
                    self._controller_retry_skipped_by_reason.get(retry_reason, 0)
                ) + 1
                treat_fail_as_normal_close = True
            elif retry_reason in RESCUE_RETRY_REASONS:
                self._controller_retry_attempts_total += 1
                self._controller_retry_by_reason[retry_reason] = int(
                    self._controller_retry_by_reason.get(retry_reason, 0)
                ) + 1

                retry_controller = deepcopy(self._controller)
                retry_ctx = DecisionContext(
                    last_ok=False,
                    last_fail_reason=retry_reason,
                    consec_ok=0,
                    consec_fail=int(self._controller_consec_fail) + 1,
                    pick_index=int(self._controller_pick_index),
                )
                _retry_ctx_overrides, retry_event = retry_controller.step(retry_ctx)
                from_mode = retry_controller.mode
                retry_controller.mode = ControllerMode.RESCUE
                attempt1_effective_budget_ms = int(
                    self._effective_scheduler_config(self._scheduler.config, overrides).time_budget_ms
                )
                rescue_overrides, retry_profile = self._build_retry_overrides(
                    retry_reason=retry_reason,
                    attempt1_effective_budget_ms=attempt1_effective_budget_ms,
                    fallback_overrides=retry_controller.overrides_for_mode(ControllerMode.RESCUE),
                )
                override_attempts.append(rescue_overrides)
                if from_mode != ControllerMode.RESCUE:
                    retry_event = ControllerEvent(
                        pick_index=int(self._controller_pick_index),
                        from_mode=from_mode,
                        to_mode=ControllerMode.RESCUE,
                        trigger=f"retry_force_rescue:{retry_reason}",
                        overrides=rescue_overrides.to_dict(),
                        note="emergency_retry_attempt",
                    )

                retry_plan, retry_fail_reason, retry_pending, retry_stop_reason, retry_stop_details = (
                    self._run_scheduler_attempt(
                        sim_state=sim_state,
                        overrides=rescue_overrides,
                    )
                )
                self._record_controller_debug_attempt(
                    attempt_index=2,
                    mode=ControllerMode.RESCUE,
                    overrides=rescue_overrides,
                    plan=retry_plan,
                    fail_reason=retry_fail_reason,
                    note=f"retry_reason={retry_reason} retry_profile={retry_profile}",
                )

                if retry_plan is not None:
                    self._controller = retry_controller
                    if retry_event is not None:
                        controller_events.append(retry_event)
                    overrides = rescue_overrides
                    plan = retry_plan
                    fail_reason = retry_fail_reason
                    pending_closures = retry_pending
                    stop_reason = retry_stop_reason
                    stop_details = retry_stop_details
                    self._controller_retry_success_total += 1
                else:
                    self._controller_retry_fail_total += 1
                    fail_reason = retry_fail_reason
                    pending_closures = retry_pending
                    stop_reason = retry_stop_reason
                    stop_details = retry_stop_details

        self._pending_closures = dict(pending_closures)
        self.stop_reason = stop_reason
        self.stop_details = dict(stop_details)
        if plan is None and self.stop_reason == "DEADLOCK":
            self._logger.error(
                "DEADLOCK: no feasible placement. item=%s dims=%s reason=%s",
                self.stop_details.get("box_id"),
                self.stop_details.get("dims"),
                self.stop_details.get("reason"),
            )

        # KPI: medir non-head picks + dt_extra
        if plan is not None:
            self.total_picks += 1
            idx = int(getattr(plan, "buffer_index", 0) or 0)
            self.sum_pick_index += idx

            dt_extra = float(getattr(plan, "dt_extra", 0.0) or 0.0)
            self.dt_extra_total += dt_extra

            if idx > 0:
                self.non_head_picks += 1
                self.dt_extra_non_head_total += dt_extra

        self._update_controller_metrics(
            plan=plan,
            fail_reason=fail_reason,
            treat_fail_as_normal_close=treat_fail_as_normal_close,
            override_attempts=override_attempts,
            controller_events=controller_events,
        )
        return plan

    def commit_plan(self, plan: PickPlan, time: float | None = None) -> None:
        pallet = self._pallets.get(plan.pallet_id)
        if pallet is None:
            pallet = self._new_pallet()
            self._pallets[plan.pallet_id] = pallet
        try:
            placement = pallet.commit_place(plan.preview)
        except Exception:
            self._logger.exception("commit_place failed for pallet=%s", plan.pallet_id)
            placement = None

        if placement is None:
            return

        viewer = self._viewer
        if viewer is None:
            return
        rect_cls = self._viewer_rect_cls
        if rect_cls is None:
            return

        pallet_id = self._map_viewer_pallet_id(plan.pallet_id)

        try:
            layer_idx = int(getattr(placement, "layer_id", 0))

            z0_mm = self._coerce_float(
                getattr(placement, "z_mm", None)
                if getattr(placement, "z_mm", None) is not None
                else getattr(placement, "z0", None)
            )
            if z0_mm is None:
                z0_mm = self._coerce_float(getattr(placement, "z", None))

            h_mm = self._coerce_float(
                getattr(placement, "height_mm", None)
                if getattr(placement, "height_mm", None) is not None
                else getattr(placement, "h", None)
            )
            if h_mm is None:
                h_mm = self._coerce_float(getattr(placement, "height", None))

            if z0_mm is None:
                z0_mm = float(layer_idx)
            if h_mm is None:
                h_mm = 1.0

            # >>> AQUÍ está el fix clave: elegir xywh correcto para dibujar
            x_mm, y_mm, w_mm, h_mm_2d, src = self._choose_xywh_source(plan.preview, placement)

            viewer.on_place(
                pallet_id=pallet_id,
                layer_idx=layer_idx,
                rect=rect_cls(float(x_mm), float(y_mm), float(w_mm), float(h_mm_2d)),
                box_id=str(getattr(placement, "box_id", None)) if getattr(placement, "box_id", None) is not None else None,
                orientation=90 if getattr(placement, "rot90", False) else 0,
                meta={
                    "z_mm": float(z0_mm),
                    "height_mm": float(h_mm),
                    # dejamos también las antiguas por compatibilidad
                    "z0": float(z0_mm),
                    "h": float(h_mm),
                },

            )

            if self._viz_debug:
                self._viewer_event_count += 1
                if self._viewer_event_count <= 20 or (self._viewer_event_count % self._viz_print_every) == 0:
                    px, py, pw, ph = self._extract_rect_xywh(plan.preview)
                    cx, cy, cw, ch = self._extract_rect_xywh(placement)
                    print(
                        f"[VIZ] n={self._viewer_event_count} dest={plan.pallet_id} mapped={pallet_id} "
                        f"layer_id={getattr(placement,'layer_id',None)} layer_idx={getattr(placement,'layer_idx',None)} "
                        f"z0={z0_mm:.1f} h={h_mm:.1f} "
                        f"src={src} chosen=({x_mm:.1f},{y_mm:.1f},{w_mm:.1f},{h_mm_2d:.1f}) "
                        f"preview=({px},{py},{pw},{ph}) committed=({cx},{cy},{cw},{ch})",
                        flush=True,
                    )

        except Exception:
            self._logger.exception("viewer on_place failed for pallet=%s", plan.pallet_id)

    def drain_pending_closures(self) -> dict[int | str, str]:
        closures = dict(self._pending_closures)
        self._pending_closures = {}
        return closures

    def on_changeover_start(self, destination: int, reason: str, open_next_pallet: bool = True) -> None:
        pallet = self._pallets.get(destination)
        if pallet is not None:
            self._completed.setdefault(destination, []).append(pallet)

        r = str(reason)
        self._closures_by_reason[r] = int(self._closures_by_reason.get(r, 0)) + 1

        if r.startswith("EARLY_"):
            self._closed_early[destination] = int(self._closed_early.get(destination, 0)) + 1
            self._closed_early_by_reason[r] = int(self._closed_early_by_reason.get(r, 0)) + 1

        viewer = self._viewer
        if viewer is not None:
            try:
                viewer.on_close(
                    self._map_viewer_pallet_id(destination),
                    reason=str(reason) if reason is not None else None,
                )
            except Exception:
                self._logger.exception("viewer on_close failed for dest=%s", destination)

        if open_next_pallet:
            self._pallets[destination] = self._new_pallet()
        else:
            self._pallets.pop(destination, None)

    def collect_kpis(self) -> dict[str, object]:
        pallets_by_dest: dict[int | str, Iterable[PalletModel]] = {}
        for dest_id, pallets in self._completed.items():
            pallets_by_dest[dest_id] = list(pallets)
        for dest_id, pallet in self._pallets.items():
            pallets_by_dest.setdefault(dest_id, []).append(pallet)

        kpis = aggregate_pallet_kpis({int(k): v for k, v in pallets_by_dest.items() if str(k).isdigit()})

        kpis["closures_by_reason"] = dict(self._closures_by_reason)
        kpis["pallets_closed_early"] = dict(self._closed_early)
        kpis["pallets_closed_early_by_reason"] = dict(self._closed_early_by_reason)

        total = max(1, int(self.total_picks))
        non_head = int(self.non_head_picks)
        deadline_cutoffs = int(getattr(self._scheduler, "deadline_cutoffs_count", 0))
        kpis["deadline_cutoffs_count"] = deadline_cutoffs
        kpis["scheduler_kpis"] = {
            "total_picks": int(self.total_picks),
            "non_head_picks": non_head,
            "non_head_pick_percent": float(100.0 * non_head / total),
            "avg_pick_index": float(self.sum_pick_index / total),
            "dt_extra_total": float(self.dt_extra_total),
            "dt_extra_avg_per_pick": float(self.dt_extra_total / total),
            "dt_extra_non_head_total": float(self.dt_extra_non_head_total),
            "dt_extra_avg_non_head": float(self.dt_extra_non_head_total / max(1, non_head)),
            "deadline_cutoffs_count": deadline_cutoffs,
        }
        micro_count = int(getattr(self._scheduler, "micro_plan_time_ms_count", 0) or 0)
        micro_sum = float(getattr(self._scheduler, "micro_plan_time_ms_sum", 0.0) or 0.0)
        micro_min_raw = getattr(self._scheduler, "micro_plan_time_ms_min", None)
        micro_min = float(micro_min_raw) if micro_min_raw is not None else 0.0
        micro_max = float(getattr(self._scheduler, "micro_plan_time_ms_max", 0.0) or 0.0)
        kpis["micro_plan_calls"] = int(getattr(self._scheduler, "micro_plan_calls", 0) or 0)
        kpis["micro_plan_fallback_greedy"] = int(getattr(self._scheduler, "micro_plan_fallback_greedy", 0) or 0)
        kpis["micro_plan_time_ms_min"] = float(micro_min)
        kpis["micro_plan_time_ms_mean"] = float(micro_sum / max(1, micro_count))
        kpis["micro_plan_time_ms_max"] = float(micro_max)
        kpis["micro_plan_nodes_expanded_total"] = int(
            getattr(self._scheduler, "micro_plan_nodes_expanded_total", 0) or 0
        )
        depth_count = int(getattr(self._scheduler, "micro_plan_depth_effective_count", 0) or 0)
        depth_sum = float(getattr(self._scheduler, "micro_plan_depth_effective_sum", 0.0) or 0.0)
        kpis["micro_plan_depth_effective_mean"] = float(depth_sum / max(1, depth_count))
        kpis["micro_plan_best_seq_len_hist"] = {
            int(k): int(v)
            for k, v in dict(getattr(self._scheduler, "micro_plan_best_seq_len_hist", {}) or {}).items()
        }
        score_mode = str(getattr(self._scheduler.config, "score_mode", "gain_frag") or "gain_frag")
        height_hist = [int(v) for v in list(getattr(self._scheduler, "selected_height_after_mm_hist", []) or [])]
        height_hist_sorted = sorted(height_hist)
        height_count = len(height_hist_sorted)

        def _percentile(values: list[int], q: float) -> float:
            if not values:
                return 0.0
            if len(values) == 1:
                return float(values[0])
            pos = (len(values) - 1) * max(0.0, min(1.0, float(q)))
            lo = int(pos)
            hi = min(lo + 1, len(values) - 1)
            if lo == hi:
                return float(values[lo])
            frac = pos - lo
            return float(values[lo] + (values[hi] - values[lo]) * frac)

        height_min = float(height_hist_sorted[0]) if height_hist_sorted else 0.0
        height_max = float(height_hist_sorted[-1]) if height_hist_sorted else 0.0
        height_mean = float(sum(height_hist_sorted) / max(1, height_count))
        above_min_count = int(getattr(self._scheduler, "selected_height_above_min_feasible_count", 0) or 0)
        choices_count = int(getattr(self._scheduler, "selected_height_choices_count", 0) or 0)
        slack_decisions_count = int(getattr(self._scheduler, "selected_height_slack_decisions_count", 0) or 0)
        slack_filtered_count = int(getattr(self._scheduler, "selected_height_slack_filtered_count", 0) or 0)
        slack_set_size_sum = float(getattr(self._scheduler, "selected_height_slack_set_size_sum", 0.0) or 0.0)
        height_slack_mm = int(getattr(self._scheduler.config, "height_slack_mm", 0) or 0)

        kpis["score_mode"] = score_mode
        kpis["height_slack_mm"] = int(height_slack_mm)
        kpis["orientation_mode"] = str(self.config.orientation_mode or "planar")
        kpis["selected_height_after_mm_count"] = int(height_count)
        kpis["selected_height_after_mm_min"] = height_min
        kpis["selected_height_after_mm_mean"] = height_mean
        kpis["selected_height_after_mm_max"] = height_max
        kpis["selected_height_after_mm_p50"] = _percentile(height_hist_sorted, 0.50)
        kpis["selected_height_after_mm_p90"] = _percentile(height_hist_sorted, 0.90)
        kpis["selected_height_after_mm_p99"] = _percentile(height_hist_sorted, 0.99)
        kpis["selected_height_above_min_feasible_count"] = above_min_count
        kpis["selected_height_above_min_feasible_rate"] = float(above_min_count / max(1, choices_count))
        kpis["selected_height_slack_filtered_rate"] = float(slack_filtered_count / max(1, slack_decisions_count))
        kpis["selected_height_slack_set_size_mean"] = float(slack_set_size_sum / max(1, slack_decisions_count))
        return kpis

    def collect_controller_metrics(self) -> dict[str, object]:
        mode_counts = {mode.value: int(self._controller_mode_counts.get(mode.value, 0)) for mode in ControllerMode}
        metrics: dict[str, object] = {
            "enabled": bool(self._online_controller_enabled),
            "mode_counts": mode_counts,
            "transitions": list(self._controller_transitions),
            "overrides_applied": dict(self._controller_overrides_applied),
            "retry_attempts_total": int(self._controller_retry_attempts_total),
            "retry_success_total": int(self._controller_retry_success_total),
            "retry_fail_total": int(self._controller_retry_fail_total),
            "retry_by_reason": dict(self._controller_retry_by_reason),
            "retry_skipped_by_reason": dict(self._controller_retry_skipped_by_reason),
            "consecutive_failures_max": int(self._controller_consecutive_failures_max),
        }
        if self._controller_debug:
            metrics["debug_events"] = list(self._controller_debug_events)
        return metrics

    def _choose_action_with_overrides(self, sim_state: SchedulerSimState, overrides: Overrides) -> PickPlan | None:
        base_config = self._scheduler.config
        effective_config = self._effective_scheduler_config(base_config, overrides)
        if effective_config == base_config:
            return self._scheduler.choose_action(sim_state)

        # Cambio temporal: el scheduler consume la config en runtime y se restaura al finalizar.
        self._scheduler.config = effective_config
        try:
            return self._scheduler.choose_action(sim_state)
        finally:
            self._scheduler.config = base_config

    def _effective_scheduler_config(self, base_config: SchedulerConfig, overrides: Overrides) -> SchedulerConfig:
        data = overrides.to_dict()
        if not data:
            return base_config

        updates: dict[str, object] = {}
        if "score_mode" in data:
            updates["score_mode"] = str(data["score_mode"])
        if "height_slack_mm" in data:
            updates["height_slack_mm"] = max(0, int(data["height_slack_mm"]))
        if "micro_depth" in data:
            updates["micro_plan_depth"] = max(1, int(data["micro_depth"]))
        if "micro_width" in data:
            updates["micro_plan_width"] = max(1, int(data["micro_width"]))
        if "micro_topk" in data:
            updates["micro_plan_topk_per_step"] = max(1, int(data["micro_topk"]))
        if "time_budget_ms" in data:
            updates["time_budget_ms"] = int(data["time_budget_ms"])
        if not updates:
            return base_config
        return replace(base_config, **updates)

    def _infer_fail_reason(self) -> str | None:
        details = self._scheduler.last_deadlock_item or {}
        reason = details.get("reason")
        if reason:
            return str(reason)

        blocked = dict(self._scheduler.last_blocked_pallets or {})
        blocked_reasons = [str(val).upper().strip() for val in blocked.values() if val is not None]
        if blocked_reasons:
            if "STABILITY" in blocked_reasons:
                return "STABILITY"
            if "HEIGHT_LIMIT" in blocked_reasons:
                return "HEIGHT_LIMIT"
            return blocked_reasons[0]

        eval_stats = dict(getattr(self._scheduler, "last_eval_stats", {}) or {})
        cutoff_reason = eval_stats.get("cutoff_reason")
        if cutoff_reason:
            return str(cutoff_reason).upper().strip()
        return "NO_PLAN"

    def _update_controller_metrics(
        self,
        *,
        plan: PickPlan | None,
        fail_reason: str | None,
        treat_fail_as_normal_close: bool,
        override_attempts: Iterable[Overrides],
        controller_events: Iterable[ControllerEvent] | None,
    ) -> None:
        if not self._online_controller_enabled or self._controller is None:
            return

        mode_name = self._controller.mode.value
        self._controller_mode_counts[mode_name] = int(self._controller_mode_counts.get(mode_name, 0)) + 1
        applied_override_keys: set[str] = set()
        for attempt_overrides in override_attempts:
            applied_override_keys.update(attempt_overrides.to_dict().keys())
        for key in applied_override_keys:
            self._controller_overrides_applied[key] = int(self._controller_overrides_applied.get(key, 0)) + 1

        if controller_events is not None:
            for controller_event in controller_events:
                if controller_event.from_mode == controller_event.to_mode:
                    continue
                self._controller_transitions.append(self._serialize_controller_event(controller_event))

        ok = plan is not None or bool(treat_fail_as_normal_close)
        if ok:
            self._controller_consec_ok += 1
            self._controller_consec_fail = 0
            self._controller_last_ok = True
            self._controller_last_fail_reason = None
        else:
            self._controller_consec_fail += 1
            self._controller_consec_ok = 0
            self._controller_last_ok = False
            self._controller_last_fail_reason = str(fail_reason) if fail_reason is not None else None

        self._controller_consecutive_failures_max = max(
            int(self._controller_consecutive_failures_max),
            int(self._controller_consec_fail),
        )

        self._controller_pick_index += 1

    def _run_scheduler_attempt(
        self,
        *,
        sim_state: SchedulerSimState,
        overrides: Overrides,
    ) -> tuple[PickPlan | None, str | None, dict[int | str, str], str | None, dict[str, object]]:
        plan = self._choose_action_with_overrides(sim_state, overrides)
        fail_reason = self._infer_fail_reason()
        pending_closures: dict[int | str, str] = {}
        stop_reason: str | None = None
        stop_details: dict[str, object] = {}

        if plan is None and self._scheduler.last_blocked_pallets:
            pending_closures = dict(self._scheduler.last_blocked_pallets)
            blocked_reasons = [str(v) for v in self._scheduler.last_blocked_pallets.values() if v is not None]
            if blocked_reasons:
                fail_reason = blocked_reasons[0]

        if plan is None and self._scheduler.last_deadlock:
            stop_reason = "DEADLOCK"
            details = self._scheduler.last_deadlock_item or {}
            stop_details = dict(details)
            fail_reason = str(details.get("reason", fail_reason or "DEADLOCK"))

        return plan, fail_reason, pending_closures, stop_reason, stop_details

    @staticmethod
    def _normalize_retry_reason(reason: str | None) -> str:
        if reason is None:
            return "NO_FEASIBLE"

        normalized = str(reason).upper().strip()
        if not normalized:
            return "NO_FEASIBLE"
        if "STABILITY" in normalized:
            return "STABILITY"
        if "HEIGHT_LIMIT" in normalized:
            return "HEIGHT_LIMIT"
        if "NO_FEASIBLE" in normalized or normalized in {"NO_PLAN", "NO_SPACE"}:
            return "NO_FEASIBLE"
        return normalized

    @staticmethod
    def _build_retry_overrides(
        *,
        retry_reason: str,
        attempt1_effective_budget_ms: int,
        fallback_overrides: Overrides,
    ) -> tuple[Overrides, str]:
        attempt2_budget_ms = max(int(attempt1_effective_budget_ms), 4500)

        if retry_reason == "HEIGHT_LIMIT":
            return (
                Overrides(
                    score_mode="min_height_then_gain",
                    height_slack_mm=0,
                    micro_depth=8,
                    micro_width=140,
                    micro_topk=25,
                    time_budget_ms=attempt2_budget_ms,
                ),
                "L2-H",
            )

        if retry_reason in {"STABILITY", "NO_FEASIBLE"}:
            return (
                Overrides(
                    score_mode="min_height_slack_then_gain",
                    height_slack_mm=80,
                    micro_depth=8,
                    micro_width=160,
                    micro_topk=30,
                    time_budget_ms=attempt2_budget_ms,
                ),
                "L2-S",
            )

        return (
            replace(
                fallback_overrides,
                time_budget_ms=max(int(fallback_overrides.time_budget_ms or 0), attempt2_budget_ms),
            ),
            "L2-S",
        )

    def _record_controller_debug_attempt(
        self,
        *,
        attempt_index: int,
        mode: ControllerMode,
        overrides: Overrides,
        plan: PickPlan | None,
        fail_reason: str | None,
        note: str | None = None,
    ) -> None:
        if not self._controller_debug or len(self._controller_debug_events) >= self._controller_debug_max_events:
            return

        self._controller_debug_events.append(
            {
                "pick_index": int(self._controller_pick_index),
                "attempt_index": int(attempt_index),
                "mode": mode.value,
                "overrides": overrides.to_dict(),
                "result": "ok" if plan is not None else "fail",
                "fail_reason": str(fail_reason) if fail_reason is not None else None,
                "note": note,
            }
        )

    @staticmethod
    def _serialize_controller_event(event: ControllerEvent) -> dict[str, object]:
        return {
            "pick_index": int(event.pick_index),
            "from_mode": event.from_mode.value,
            "to_mode": event.to_mode.value,
            "trigger": str(event.trigger),
            "overrides": dict(event.overrides),
            "note": event.note,
        }

    def _collect_pallets(self, ramps: Mapping[int, Any]) -> dict[int | str, PalletModel]:
        pallets: dict[int | str, PalletModel] = {}
        for ramp in ramps.values():
            queue = getattr(ramp, "queue", [])
            for box in list(queue):
                dest = getattr(box, "destination", None)
                if dest is None:
                    continue
                if dest not in self._pallets:
                    self._pallets[dest] = self._new_pallet()
                pallets[dest] = self._pallets[dest]
        return pallets

    def _collect_ramp_boxes(self, ramps: Mapping[int, Any]) -> dict[int, list[Box]]:
        ramp_boxes: dict[int, list[Box]] = {}
        k = max(1, int(self.config.scheduler.lookahead_k))
        for ramp_id, ramp in ramps.items():
            queue = getattr(ramp, "queue", [])
            items = list(queue)[:k]
            ramp_boxes[int(ramp_id)] = [self._to_box(item) for item in items]
        return ramp_boxes

    def _collect_ramp_states(self, ramps: Mapping[int, Any]) -> dict[int, SchedulerRampState]:
        snapshots: dict[int, SchedulerRampState] = {}
        for ramp_id, ramp in ramps.items():
            queue = tuple(self._to_box(item) for item in list(getattr(ramp, "queue", [])))
            upstream = tuple(self._to_box(item) for item in list(getattr(ramp, "upstream", [])))
            capacity = int(getattr(ramp, "capacity", len(queue)) or len(queue))
            snapshots[int(ramp_id)] = SchedulerRampState(
                queue=queue,
                upstream=upstream,
                capacity=max(0, capacity),
            )
        return snapshots

    def _to_box(self, item: Any) -> Box:
        length_mm = getattr(item, "length_mm", None) or self.config.default_box_length_mm
        width_mm = getattr(item, "width_mm", None) or self.config.default_box_width_mm
        height_mm = getattr(item, "height_mm", None) or self.config.default_box_height_mm
        weight_kg = getattr(item, "weight_kg", None)
        loadbear = getattr(item, "loadbear", None)
        priority = getattr(item, "priority", None)
        timestamp = getattr(item, "ramp_enter_time", None)
        if timestamp is None:
            timestamp = getattr(item, "arrival_time", 0.0)

        priority_mode = str(self.config.priority_mode or "none").lower().strip()
        if priority_mode == "weight":
            try:
                if weight_kg is not None:
                    priority = float(weight_kg)
                else:
                    volume = float(int(length_mm) * int(width_mm) * int(height_mm))
                    priority = volume / 1_000_000.0
            except Exception:
                priority = None
        elif priority_mode.startswith("excel"):
            priority = priority
        else:
            priority = None
        return Box(
            box_id=getattr(item, "box_id", None) or 0,
            length_mm=int(length_mm),
            width_mm=int(width_mm),
            height_mm=int(height_mm),
            timestamp=float(timestamp),
            destination=getattr(item, "destination", None),
            weight_kg=float(weight_kg) if weight_kg is not None else None,
            loadbear=float(loadbear) if loadbear is not None else None,
            priority=float(priority) if priority is not None else None,
        )

    def _new_pallet(self) -> PalletModel:
        control_config = ControlConfig(
            stability=StabilityConfig(
                mode=self.config.stability_mode,
                min_support_ratio=self.config.min_support_ratio,
                eps_mm=self.config.stability_eps_mm,
                settle_snap_grid=self.config.settle_snap_grid,
                grid_mm=self.config.grid_mm,
                settle_max_iter=self.config.settle_max_iter,
                settle_timeout_ms=self.config.settle_timeout_ms,
            ),
            loadbear=LoadBearConfig(
                heavy_bottom=self.config.heavy_bottom,
                max_overweight_ratio=self.config.max_overweight_ratio,
                penalty_weight=self.config.loadbear_penalty_weight,
                loadbear_factor=self.config.loadbear_factor,
            ),
            balance=BalanceConfig(balance_weight=self.config.balance_weight),
        )
        return PalletModel(
            spec=self.config.pallet_spec,
            heuristic=self.config.heuristic,
            scoring_weights=self.config.scoring_weights,
            control_config=control_config,
            orientation_mode=str(self.config.orientation_mode),
            stand_hw_height_margin_gate_mm=int(self.config.stand_hw_height_margin_gate_mm),
        )

    def _get_first_attr(self, obj: Any, names: tuple[str, ...]) -> Any:
        for n in names:
            if hasattr(obj, n):
                return getattr(obj, n)
        return None

    def _extract_rect_xywh(self, obj: Any) -> tuple[float | None, float | None, float | None, float | None]:
        """Try to extract (x,y,w,h) in mm from a placement-like object."""
        x = self._coerce_float(self._get_first_attr(obj, ("x_mm", "x", "left", "x0")))
        y = self._coerce_float(self._get_first_attr(obj, ("y_mm", "y", "top", "y0")))
        w = self._coerce_float(self._get_first_attr(obj, ("length_mm", "w_mm", "w", "width_x_mm", "dx")))
        h = self._coerce_float(self._get_first_attr(obj, ("width_mm", "h_mm", "h", "width_y_mm", "dy")))

        if (x is None or y is None or w is None or h is None) and hasattr(obj, "rect"):
            r = getattr(obj, "rect")
            try:
                x = x if x is not None else self._coerce_float(getattr(r, "x", None))
                y = y if y is not None else self._coerce_float(getattr(r, "y", None))
                w = w if w is not None else self._coerce_float(getattr(r, "w", None))
                h = h if h is not None else self._coerce_float(getattr(r, "h", None))
            except Exception:
                pass

        return x, y, w, h

    def _choose_xywh_source(
        self,
        preview_obj: Any,
        committed_obj: Any,
    ) -> tuple[float, float, float, float, str]:
        """Choose the best xywh between preview and committed for visualization."""
        px, py, pw, ph = self._extract_rect_xywh(preview_obj)
        cx, cy, cw, ch = self._extract_rect_xywh(committed_obj)

        def _valid(x, y, w, h) -> bool:
            if x is None or y is None or w is None or h is None:
                return False
            if w <= 0 or h <= 0:
                return False
            return True

        prev_ok = _valid(px, py, pw, ph)
        comm_ok = _valid(cx, cy, cw, ch)

        # Heurística: si committed cae en (0,0) pero preview no, preferimos preview
        if comm_ok and prev_ok:
            if abs(cx) < 1e-9 and abs(cy) < 1e-9 and (abs(px) > 1e-6 or abs(py) > 1e-6):
                return float(px), float(py), float(pw), float(ph), "preview"
            return float(cx), float(cy), float(cw), float(ch), "committed"

        if prev_ok:
            return float(px), float(py), float(pw), float(ph), "preview"
        if comm_ok:
            return float(cx), float(cy), float(cw), float(ch), "committed"

        return float(cx or 0.0), float(cy or 0.0), float(cw or 0.0), float(ch or 0.0), "fallback"

    def _map_viewer_pallet_id(self, dest_id: int | str) -> int | str:
        """Map DES destination id to viewer pallet id.

        Viewer expects 1..6. Some simulations use 0..5.
        Auto-detect indexing to avoid shifting 1..5 incorrectly.
        """
        try:
            val = int(dest_id)
        except Exception:
            return dest_id

        if self._dest_indexing is None:
            if val == 0:
                self._dest_indexing = "zero_based"
            elif val == 6:
                self._dest_indexing = "one_based"
            else:
                # SAFE default: one_based (prevents shifting 1..5 -> 2..6)
                self._dest_indexing = "one_based"

            if self._viz_debug:
                print(f"[VIZ] dest indexing detected: {self._dest_indexing}", flush=True)

        if self._dest_indexing == "zero_based":
            if 0 <= val <= 5:
                return val + 1
            return val

        # one_based
        if 1 <= val <= 6:
            return val
        if val == 0:
            return 1
        return val

    def _coerce_float(self, value: Any | None) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None
