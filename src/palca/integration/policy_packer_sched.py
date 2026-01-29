from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from typing import Any, Iterable, Mapping

from ..domain.box import Box
from ..domain.pallet_spec import PalletSpec
from ..packer.controls import BalanceConfig, ControlConfig, LoadBearConfig, StabilityConfig
from ..packer.pallet_model import PalletModel
from ..packer.scoring import ScoringWeights
from ..scheduler.scheduler_v1 import PickPlan, SchedulerConfig, SchedulerSimState, SchedulerV1
from .kpi_hooks import aggregate_pallet_kpis


SUPPORTED_LOOKAHEAD_K = (1, 3, 5, 10, 15)


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
    priority_mode: str = "none"
    max_tries_per_item: int = 0
    max_candidates: int = 0
    max_seconds_per_item: float = 0.0
    heartbeat_sec: float = 1.0
    settle_max_iter: int = 0
    settle_timeout_ms: int = 0


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
        priority_mode: str = "none",
        max_tries_per_item: int = 0,
        max_candidates: int = 0,
        max_seconds_per_item: float = 0.0,
        heartbeat_sec: float = 1.0,
        settle_max_iter: int = 0,
        settle_timeout_ms: int = 0,
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
            max_tries_per_item=max_tries_per_item,
            max_candidates=max_candidates,
            max_seconds_per_item=max_seconds_per_item,
            heartbeat_sec=heartbeat_sec,
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
            priority_mode=priority_mode,
            max_tries_per_item=max_tries_per_item,
            max_candidates=max_candidates,
            max_seconds_per_item=max_seconds_per_item,
            heartbeat_sec=heartbeat_sec,
            settle_max_iter=settle_max_iter,
            settle_timeout_ms=settle_timeout_ms,
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
            pallets=pallets,
            pallet_blocked=blocked,
            ramp_sizes=ramp_sizes,
            remaining_total=int(remaining_total),
        )

        plan = self._scheduler.choose_action(sim_state)

        # reset pending closures (se rellenará si plan es None)
        self._pending_closures = {}
        if plan is None and self._scheduler.last_blocked_pallets:
            self._pending_closures = dict(self._scheduler.last_blocked_pallets)
            return None

        if plan is None and self._scheduler.last_deadlock:
            self.stop_reason = "DEADLOCK"
            details = self._scheduler.last_deadlock_item or {}
            self.stop_details = dict(details)
            self._logger.error(
                "DEADLOCK: no feasible placement. item=%s dims=%s reason=%s",
                details.get("box_id"),
                details.get("dims"),
                details.get("reason"),
            )
            return None

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
                meta={"z0": float(z0_mm), "h": float(h_mm)},
            )

            if self._viz_debug:
                self._viewer_event_count += 1
                if self._viewer_event_count <= 20 or (self._viewer_event_count % self._viz_print_every) == 0:
                    px, py, pw, ph = self._extract_rect_xywh(plan.preview)
                    cx, cy, cw, ch = self._extract_rect_xywh(placement)
                    print(
                        f"[VIZ] n={self._viewer_event_count} dest={plan.pallet_id} mapped={pallet_id} "
                        f"layer_id={getattr(placement,'layer_id',None)} layer_idx={getattr(placement,'layer_idx',None)} "
                        f"z0={z0_mm:.1f} "
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

    def on_changeover_start(self, destination: int, reason: str) -> None:
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

        self._pallets[destination] = self._new_pallet()

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
        return kpis

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
