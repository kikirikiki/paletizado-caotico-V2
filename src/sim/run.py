from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any

from palca.tuning.episodes import apply_shuffle

from .des import Arrival, SimConfig, simulate
from .io import load_arrivals
from .paths import resolve_repo_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simulador DES de paletizado")

    # Inputs
    parser.add_argument("--excel", required=True, help="Ruta al Excel de entradas")
    parser.add_argument("--model", choices=["M1", "M2"], default="M1")

    # Base DES params
    parser.add_argument("--n_per_pallet", type=int, default=24)
    parser.add_argument("--t_pick_place", type=float, default=14.0)
    parser.add_argument("--t_changeover", type=float, default=60.0)
    parser.add_argument("--t_stage", type=float, default=6.0)
    parser.add_argument("--t_unstage", type=float, default=10.0)
    parser.add_argument("--ramp_cap", type=int, default=15)
    parser.add_argument("--staging_cap", type=int, default=0)
    parser.add_argument("--time_scale", type=float, default=1.0, help="Escala de tiempo (1,2,3,4,...)")
    parser.add_argument(
        "--force-destination",
        type=int,
        default=None,
        help="Si se define, fuerza ese destino (1..6) para todas las cajas",
    )
    parser.add_argument(
        "--continuous-pallets",
        action="store_true",
        help="Modo continuo: cierra pallet por DEADLOCK y sigue con uno nuevo",
    )
    parser.add_argument(
        "--max-pallets",
        type=int,
        default=0,
        help="Si >0, detiene al cerrar N pallets del destino forzado (requiere --force-destination).",
    )

    parser.add_argument(
        "--arrival-mode",
        choices=["excel", "immediate"],
        default="excel",
        help="excel=usa timestamps del Excel; immediate=ignora timestamps y hace arrivals en t=0 para simular ventana física constante (ramp_cap)",
    )
    parser.add_argument("--episode-seed", type=int, default=0, help="Seed para episodio reproducible")
    parser.add_argument(
        "--shuffle-window",
        type=int,
        default=0,
        help="Ventana de shuffle por bloques (<=1 deshabilita)",
    )
    parser.add_argument(
        "--shuffle-strength",
        type=float,
        default=0.0,
        help="Intensidad de shuffle (<=0 deshabilita)",
    )
    parser.add_argument("--episode-id", type=str, default=None, help="ID de episodio para metadata")

    # Policy
    parser.add_argument("--policy", choices=["legacy", "palca"], default="legacy")
    parser.add_argument("--k", type=int, default=1, help="Lookahead K (1,3,5,10,15) para palca")

    # palca knobs (packer + scheduler)
    parser.add_argument("--overhang_mm", type=int, default=0, help="Overhang permitido (0/20/40...)")
    parser.add_argument("--heuristic", choices=["baf", "bssf"], default="baf", help="Heurística MaxRects")

    parser.add_argument("--t_select_base", type=float, default=0.0, help="Coste base por selección (s)")
    parser.add_argument("--t_select_step", type=float, default=0.0, help="Coste incremental por índice i (s)")
    parser.add_argument("--time_penalty_weight", type=float, default=1.0, help="Peso de penalización temporal")
    parser.add_argument("--starvation_weight", type=float, default=0.0, help="Peso anti-starvation")
    parser.add_argument(
        "--stability-mode",
        type=str,
        default="ratio+corners",
        choices=["off", "ratio", "ratio+corners", "ratio+corners+settle"],
        help="Modo de estabilidad",
    )
    parser.add_argument("--min-support", type=float, default=0.75, help="Ratio mínimo de soporte")
    parser.add_argument("--stability-eps-mm", type=float, default=1.0, help="Epsilon de estabilidad (mm)")
    parser.add_argument("--settle-snap-grid", action="store_true", help="Snap de settle a grid")
    parser.add_argument(
        "--settle-max-iter",
        type=int,
        default=0,
        help="Max iteraciones para settle (0 = sin limite)",
    )
    parser.add_argument(
        "--settle-timeout-ms",
        type=int,
        default=0,
        help="Timeout de settle (ms, 0 = sin limite)",
    )
    parser.add_argument("--grid-mm", type=int, default=None, help="Tamaño de grid (mm)")
    parser.add_argument("--heavy-bottom", action="store_true", help="Penaliza pesado sobre débil")
    parser.add_argument("--max-overweight-ratio", type=float, default=1.5, help="Ratio máximo peso/soporte")
    parser.add_argument("--loadbear-penalty-weight", type=float, default=1.0, help="Peso de penalización loadbear")
    parser.add_argument("--loadbear-factor", type=float, default=1.0, help="Factor para capacidad loadbear")
    parser.add_argument("--priority-mode", type=str, default="none", help="none | weight | excel[:colname]")
    parser.add_argument("--priority-weight", type=float, default=1.0, help="Peso del bonus por prioridad")
    parser.add_argument("--balance-weight", type=float, default=0.0, help="Peso del balance en score")
    parser.add_argument("--coverage-grid-x", type=int, default=0, help="Grid X para coverage control (0 deshabilita)")
    parser.add_argument("--coverage-grid-y", type=int, default=0, help="Grid Y para coverage control (0 deshabilita)")
    parser.add_argument("--coverage-weight", type=float, default=0.0, help="Peso coverage control (0 deshabilita)")
    parser.add_argument(
        "--dominant-free-rect-weight",
        type=float,
        default=0.0,
        help="Peso dominant free-rect targeting (0 deshabilita)",
    )
    parser.add_argument(
        "--dominant-free-rect-ratio-gate",
        type=float,
        default=0.35,
        help="Gate de ratio para dominant free-rect targeting",
    )
    parser.add_argument(
        "--score-mode",
        choices=["gain_frag", "min_height_then_gain", "min_height_slack_then_gain"],
        default="gain_frag",
        help=(
            "gain_frag=score actual; min_height_then_gain=prioriza menor altura final, luego gain/frag; "
            "min_height_slack_then_gain=prioriza altura con slack, luego gain/frag"
        ),
    )
    parser.add_argument(
        "--height-slack-mm",
        type=int,
        default=0,
        help="Slack de altura para min_height_slack_then_gain (mm)",
    )
    parser.add_argument(
        "--orientation-mode",
        choices=["planar", "planar+stand_hw"],
        default="planar",
        help="Modo de orientaciones de caja: planar (2) o planar+stand_hw (4).",
    )
    parser.add_argument(
        "--stand-hw-height-margin-gate-mm",
        type=int,
        default=400,
        help="Permite stand_hw solo si (max_height-current_height) <= gate (mm).",
    )
    parser.add_argument("--time-budget-ms", type=int, default=120, help="Presupuesto por decision (ms)")
    parser.add_argument("--micro-plan", action="store_true", help="Habilita micro-planner beam search (solo palca)")
    parser.add_argument("--micro-depth", type=int, default=3, help="Profundidad del micro-planner")
    parser.add_argument("--micro-width", type=int, default=8, help="Ancho del beam del micro-planner")
    parser.add_argument(
        "--micro-topk",
        type=int,
        default=15,
        help="Max candidatos factibles a expandir por paso del micro-planner",
    )
    parser.add_argument(
        "--batchfill-layer-starter",
        action="store_true",
        help="Activa BatchFill para elegir mejor starter al abrir capa nueva",
    )
    parser.add_argument(
        "--batchfill-starters-max",
        type=int,
        default=6,
        help="Max starters evaluados por pallet en BatchFill",
    )
    parser.add_argument(
        "--batchfill-budget-ms",
        type=int,
        default=150,
        help="Presupuesto de tiempo BatchFill por decision (ms)",
    )
    parser.add_argument(
        "--batchfill-greedy-topk",
        type=int,
        default=12,
        help="Top-K de candidatos usados por el fill greedy interno de BatchFill",
    )
    parser.add_argument(
        "--online-controller",
        action="store_true",
        help="Habilita controller online de modos NORMAL/PUSH/RESCUE sobre palca",
    )
    parser.add_argument(
        "--controller-debug",
        action="store_true",
        help="Guarda eventos de debug del online-controller (max 200)",
    )
    parser.add_argument("--weight-col", type=str, default=None, help="Columna peso (opcional)")
    parser.add_argument(
        "--max-tries-per-item",
        type=int,
        default=0,
        help="Limite de intentos/candidatos por item (0 = sin limite)",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="Limite de candidatos evaluados por decision (0 = sin limite)",
    )
    parser.add_argument(
        "--max-seconds-per-item",
        type=float,
        default=0.0,
        help="Timeout por item (segundos, 0 = sin limite)",
    )
    parser.add_argument(
        "--watchdog-heartbeat-sec",
        type=float,
        default=1.0,
        help="Intervalo de heartbeat watchdog (segundos, 0 = deshabilitar)",
    )

    # Output
    parser.add_argument("--out", type=str, default=None, help="Ruta de salida .json o .csv (opcional)")
    parser.add_argument("--print", action="store_true", help="Imprime el JSON aunque uses --out")

    # Visualization
    parser.add_argument("--viz", action="store_true", help="Habilita visor en tiempo real (matplotlib)")
    parser.add_argument("--viz-mode", choices=["2d", "3d"], default="2d", help="Modo de visor (2d/3d)")
    parser.add_argument("--viz-every", type=int, default=10, help="Refresco cada N colocaciones")
    parser.add_argument("--viz-labels", action="store_true", help="Muestra box_id/orientacion en cada rect")
    parser.add_argument("--viz-block", action="store_true", help="Mantiene la ventana abierta al final")
    parser.add_argument(
        "--viz-dest",
        type=int,
        default=0,
        help="Si >0, visualiza solo ese destino/palet (1..6). 0 = todos los detectados.",
    )
    parser.add_argument(
        "--viz-debug",
        action="store_true",
        help="Debug del visor (imprime destinos detectados y confirma attach)",
    )

    return parser


def _is_episode_shuffle_enabled(*, shuffle_window: int, shuffle_strength: float) -> bool:
    return int(shuffle_window) > 1 and float(shuffle_strength) > 0.0


def _detect_destinations(arrivals: list[Arrival]) -> list[int]:
    dests: set[int] = set()
    for a in arrivals:
        try:
            dests.add(int(a.destination))
        except Exception:
            continue
    return sorted(dests)


def _create_viewer_if_enabled(
    *,
    enabled: bool,
    viz_mode: str,
    viz_every: int,
    viz_labels: bool,
    viz_debug: bool,
    pallet_ids: list[int] | None,
) -> tuple[Any | None, Any | None]:
    if not enabled:
        return None, None

    try:
        if viz_mode == "3d":
            from palca.viz import PalletViewer3D
            from palca.viz.pallet_viewer3d import Rect2D
        else:
            from palca.viz import PalletViewer
            from palca.viz.pallet_viewer import Rect2D
    except ImportError as exc:
        raise SystemExit("ERROR: matplotlib es requerido para --viz. Instala con `pip install matplotlib`.") from exc

    # si no hay destinos detectables, al menos crea 1
    if not pallet_ids:
        pallet_ids = [1]

    if viz_debug:
        print(f"[VIZ] pallet_ids for viewer: {pallet_ids}", flush=True)

    show_current_layer_only = False  # más fácil depurar

    if viz_mode == "3d":
        viewer = PalletViewer3D(
            enabled=True,
            pallet_ids=tuple(pallet_ids),
            update_every=viz_every,
            show_current_layer_only=show_current_layer_only,
            debug=viz_debug,
        )
    else:
        viewer = PalletViewer(
            enabled=True,
            pallet_ids=tuple(pallet_ids),
            update_every=viz_every,
            show_labels=viz_labels,
            show_current_layer_only=show_current_layer_only,
        )

    return viewer, Rect2D


def run_simulation(
    excel_path: str,
    model: str,
    n_per_pallet: int,
    t_pick_place: float,
    staging_cap: int,
    out_path: str | None,
    out_json: str | None = None,
    *,
    t_changeover: float = 60.0,
    t_stage: float = 6.0,
    t_unstage: float = 10.0,
    ramp_cap: int = 15,
    policy: str = "legacy",
    lookahead_k: int = 1,
    time_scale: float = 1.0,
    arrival_mode: str = "excel",
    episode_seed: int = 0,
    shuffle_window: int = 0,
    shuffle_strength: float = 0.0,
    episode_id: str | None = None,
    # palca
    overhang_mm: int = 0,
    heuristic: str = "baf",
    t_select_base: float = 0.0,
    t_select_step: float = 0.0,
    time_penalty_weight: float = 1.0,
    starvation_weight: float = 0.0,
    stability_mode: str = "ratio+corners",
    min_support: float = 0.75,
    stability_eps_mm: float = 1.0,
    settle_snap_grid: bool = False,
    settle_max_iter: int = 0,
    settle_timeout_ms: int = 0,
    grid_mm: int | None = None,
    heavy_bottom: bool = False,
    max_overweight_ratio: float = 1.5,
    loadbear_penalty_weight: float = 1.0,
    loadbear_factor: float = 1.0,
    priority_mode: str = "none",
    priority_weight: float = 1.0,
    balance_weight: float = 0.0,
    coverage_grid_x: int = 0,
    coverage_grid_y: int = 0,
    coverage_weight: float = 0.0,
    dominant_free_rect_weight: float = 0.0,
    dominant_free_rect_ratio_gate: float = 0.35,
    score_mode: str = "gain_frag",
    height_slack_mm: int = 0,
    orientation_mode: str = "planar",
    stand_hw_height_margin_gate_mm: int = 400,
    time_budget_ms: int = 120,
    micro_plan: bool = False,
    micro_depth: int = 3,
    micro_width: int = 8,
    micro_topk: int = 15,
    batchfill_layer_starter: bool = False,
    batchfill_starters_max: int = 6,
    batchfill_budget_ms: int = 150,
    batchfill_greedy_topk: int = 12,
    online_controller: bool = False,
    controller_debug: bool = False,
    weight_col: str | None = None,
    max_tries_per_item: int = 0,
    max_candidates: int = 0,
    max_seconds_per_item: float = 0.0,
    watchdog_heartbeat_sec: float = 1.0,
    force_destination: int | None = None,
    continuous_pallets: bool = False,
    max_pallets: int = 0,
    # viz
    viz: bool = False,
    viz_mode: str = "2d",
    viz_every: int = 10,
    viz_labels: bool = False,
    viz_block: bool = False,
    viz_debug: bool = False,
    viz_dest: int = 0,
) -> dict[str, Any]:
    if out_path is None and out_json is not None:
        out_path = out_json

    priority_col = None
    if isinstance(priority_mode, str) and priority_mode.lower().startswith("excel"):
        parts = priority_mode.split(":", 1)
        if len(parts) == 2 and parts[1].strip():
            priority_col = parts[1].strip()

    if force_destination is not None and not (1 <= int(force_destination) <= 6):
        raise ValueError("force_destination debe estar entre 1 y 6")
    max_pallets_value = int(max_pallets)
    if max_pallets_value < 0:
        raise ValueError("max_pallets debe ser >= 0")
    if max_pallets_value > 0 and force_destination is None:
        raise ValueError("max_pallets requiere force_destination (usa --force-destination)")
    if time_scale <= 0:
        raise ValueError("time_scale debe ser positivo")

    arrivals = load_arrivals(excel_path, weight_col=weight_col, priority_col=priority_col)

    if time_scale != 1.0:
        arrivals = [
            Arrival(
                time=a.time / float(time_scale),
                destination=a.destination,
                row_idx=a.row_idx,
                length_mm=a.length_mm,
                width_mm=a.width_mm,
                height_mm=a.height_mm,
                weight_kg=a.weight_kg,
                priority=a.priority,
            )
            for a in arrivals
        ]

    if force_destination is not None:
        forced_dest = int(force_destination)
        arrivals = [
            Arrival(
                time=a.time,
                destination=forced_dest,
                row_idx=a.row_idx,
                length_mm=a.length_mm,
                width_mm=a.width_mm,
                height_mm=a.height_mm,
                weight_kg=a.weight_kg,
                priority=a.priority,
            )
            for a in arrivals
        ]

    if _is_episode_shuffle_enabled(shuffle_window=shuffle_window, shuffle_strength=shuffle_strength):
        ordered = sorted(arrivals, key=lambda a: int(a.row_idx))
        shuffled = apply_shuffle(
            ordered,
            seed=int(episode_seed),
            window=int(shuffle_window),
            strength=float(shuffle_strength),
        )
        arrivals = [
            Arrival(
                time=a.time,
                destination=a.destination,
                row_idx=idx,
                length_mm=a.length_mm,
                width_mm=a.width_mm,
                height_mm=a.height_mm,
                weight_kg=a.weight_kg,
                priority=a.priority,
            )
            for idx, a in enumerate(shuffled, start=1)
        ]

    pallet_ids = _detect_destinations(arrivals) if viz else None

    # aplicar filtro de un solo destino para ver 1 palet
    if viz and viz_dest and viz_dest > 0:
        if pallet_ids and viz_dest in pallet_ids:
            pallet_ids = [viz_dest]
        else:
            pallet_ids = [viz_dest]
        if viz_debug:
            print(f"[VIZ] viz_dest={viz_dest} => pallet_ids={pallet_ids}", flush=True)

    viewer, rect_cls = _create_viewer_if_enabled(
        enabled=viz,
        viz_mode=viz_mode,
        viz_every=viz_every,
        viz_labels=viz_labels,
        viz_debug=viz_debug,
        pallet_ids=pallet_ids,
    )

    if viz_debug and viz:
        print(f"[VIZ] detected destinations from excel: {pallet_ids}", flush=True)
        os.environ["PALCA_VIZ_DEBUG"] = "1"

    config = SimConfig(
        model=model,
        ramp_capacity=ramp_cap,
        staging_capacity=staging_cap,
        n_per_pallet=n_per_pallet,
        t_changeover=t_changeover,
        t_pick_place=t_pick_place,
        t_stage=t_stage,
        t_unstage=t_unstage,
    )

    decision_policy = None
    if policy == "palca":
        from palca.integration.policy_packer_sched import PolicyPackerScheduler, SUPPORTED_LOOKAHEAD_K

        if lookahead_k not in SUPPORTED_LOOKAHEAD_K:
            raise ValueError(f"K no soportado: {lookahead_k}")

        decision_policy = PolicyPackerScheduler.from_defaults(
            lookahead_k=lookahead_k,
            overhang_mm=overhang_mm,
            heuristic=heuristic,
            t_select_base=t_select_base,
            t_select_step=t_select_step,
            time_penalty_weight=time_penalty_weight,
            starvation_weight=starvation_weight,
            time_budget_ms=time_budget_ms,
            micro_plan_enabled=bool(micro_plan),
            micro_plan_depth=int(micro_depth),
            micro_plan_width=int(micro_width),
            micro_plan_topk_per_step=int(micro_topk),
            batchfill_layer_starter=bool(batchfill_layer_starter),
            batchfill_starters_max=int(batchfill_starters_max),
            batchfill_budget_ms=int(batchfill_budget_ms),
            batchfill_greedy_topk=int(batchfill_greedy_topk),
            online_controller=bool(online_controller),
            controller_debug=bool(controller_debug),
            priority_weight=priority_weight,
            stability_mode=stability_mode,
            min_support_ratio=min_support,
            stability_eps_mm=stability_eps_mm,
            settle_snap_grid=settle_snap_grid,
            settle_max_iter=settle_max_iter,
            settle_timeout_ms=settle_timeout_ms,
            grid_mm=grid_mm,
            heavy_bottom=heavy_bottom,
            max_overweight_ratio=max_overweight_ratio,
            loadbear_penalty_weight=loadbear_penalty_weight,
            loadbear_factor=loadbear_factor,
            balance_weight=balance_weight,
            coverage_grid_x=int(coverage_grid_x),
            coverage_grid_y=int(coverage_grid_y),
            coverage_weight=float(coverage_weight),
            dominant_free_rect_weight=float(dominant_free_rect_weight),
            dominant_free_rect_ratio_gate=float(dominant_free_rect_ratio_gate),
            score_mode=score_mode,
            height_slack_mm=max(0, int(height_slack_mm)),
            orientation_mode=str(orientation_mode),
            stand_hw_height_margin_gate_mm=max(0, int(stand_hw_height_margin_gate_mm)),
            priority_mode=priority_mode,
            max_tries_per_item=max_tries_per_item,
            max_candidates=max_candidates,
            max_seconds_per_item=max_seconds_per_item,
            heartbeat_sec=watchdog_heartbeat_sec,
        )

        if viewer is not None and rect_cls is not None:
            if hasattr(decision_policy, "attach_viewer"):
                decision_policy.attach_viewer(viewer, rect_cls, debug=viz_debug)  # type: ignore[attr-defined]
                if viz_debug:
                    print("[VIZ] attached via decision_policy.attach_viewer()", flush=True)
            else:
                decision_policy._viewer = viewer  # type: ignore[attr-defined]
                decision_policy._viewer_rect_cls = rect_cls  # type: ignore[attr-defined]
                if viz_debug:
                    print("[VIZ] attached via decision_policy._viewer/_viewer_rect_cls (fallback)", flush=True)

    try:
        result = simulate(
            arrivals,
            config,
            decision_policy=decision_policy,
            continuous_pallets=continuous_pallets,
            arrival_mode=arrival_mode,
            max_pallets=max_pallets_value,
            max_pallets_destination=(int(force_destination) if max_pallets_value > 0 else None),
        )
    finally:
        if viewer is not None:
            if viz_block:
                print("[VIZ] Press Enter to continue...", flush=True)
                viewer.finalize(block=False)
                try:
                    input()
                except EOFError:
                    pass
            else:
                viewer.finalize(block=False)

    metrics_payload = result.to_dict()
    controller_metrics: dict[str, Any] = {"enabled": False}
    if policy == "palca" and decision_policy is not None and hasattr(decision_policy, "collect_controller_metrics"):
        try:
            raw_controller_metrics = decision_policy.collect_controller_metrics()  # type: ignore[attr-defined]
            if isinstance(raw_controller_metrics, dict):
                controller_metrics = dict(raw_controller_metrics)
        except Exception:
            controller_metrics = {"enabled": False}
    if "enabled" not in controller_metrics:
        controller_metrics["enabled"] = bool(policy == "palca" and online_controller)
    metrics_payload["controller"] = controller_metrics

    payload: dict[str, Any] = {
        "model": model,
        "params": {
            "n_per_pallet": n_per_pallet,
            "t_pick_place": t_pick_place,
            "t_changeover": t_changeover,
            "t_stage": t_stage,
            "t_unstage": t_unstage,
            "ramp_cap": ramp_cap,
            "staging_cap": staging_cap,
            "policy": policy,
            "lookahead_k": lookahead_k,
            "time_scale": time_scale,
            "arrival_mode": arrival_mode,
            "episode_seed": int(episode_seed),
            "shuffle_window": int(shuffle_window),
            "shuffle_strength": float(shuffle_strength),
            "episode_id": episode_id,
            "overhang_mm": overhang_mm,
            "heuristic": heuristic,
            "t_select_base": t_select_base,
            "t_select_step": t_select_step,
            "time_penalty_weight": time_penalty_weight,
            "starvation_weight": starvation_weight,
            "stability_mode": stability_mode,
            "min_support": min_support,
            "stability_eps_mm": stability_eps_mm,
            "settle_snap_grid": settle_snap_grid,
            "settle_max_iter": settle_max_iter,
            "settle_timeout_ms": settle_timeout_ms,
            "grid_mm": grid_mm,
            "heavy_bottom": heavy_bottom,
            "max_overweight_ratio": max_overweight_ratio,
            "loadbear_penalty_weight": loadbear_penalty_weight,
            "loadbear_factor": loadbear_factor,
            "priority_mode": priority_mode,
            "priority_weight": priority_weight,
            "balance_weight": balance_weight,
            "coverage_grid_x": int(coverage_grid_x),
            "coverage_grid_y": int(coverage_grid_y),
            "coverage_weight": float(coverage_weight),
            "dominant_free_rect_weight": float(dominant_free_rect_weight),
            "dominant_free_rect_ratio_gate": float(dominant_free_rect_ratio_gate),
            "score_mode": score_mode,
            "height_slack_mm": int(max(0, int(height_slack_mm))),
            "orientation_mode": str(orientation_mode),
            "stand_hw_height_margin_gate_mm": int(max(0, int(stand_hw_height_margin_gate_mm))),
            "time_budget_ms": time_budget_ms,
            "micro_plan": bool(micro_plan),
            "micro_depth": int(micro_depth),
            "micro_width": int(micro_width),
            "micro_topk": int(micro_topk),
            "batchfill_layer_starter": bool(batchfill_layer_starter),
            "batchfill_starters_max": int(batchfill_starters_max),
            "batchfill_budget_ms": int(batchfill_budget_ms),
            "batchfill_greedy_topk": int(batchfill_greedy_topk),
            "online_controller": bool(online_controller),
            "controller_debug": bool(controller_debug),
            "weight_col": weight_col,
            "max_tries_per_item": max_tries_per_item,
            "max_candidates": max_candidates,
            "max_seconds_per_item": max_seconds_per_item,
            "watchdog_heartbeat_sec": watchdog_heartbeat_sec,
            "force_destination": force_destination,
            "continuous_pallets": continuous_pallets,
            "max_pallets": int(max_pallets_value),
            "viz_dest": viz_dest,
        },
        "metrics": metrics_payload,
    }

    if out_path:
        resolved = resolve_repo_path(out_path)
        if resolved.suffix.lower() == ".csv":
            _write_csv(resolved, payload)
        else:
            _write_json(resolved, payload)

    return payload


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    payload = run_simulation(
        excel_path=args.excel,
        model=args.model,
        n_per_pallet=args.n_per_pallet,
        t_pick_place=args.t_pick_place,
        staging_cap=args.staging_cap,
        out_path=args.out,
        t_changeover=args.t_changeover,
        t_stage=args.t_stage,
        t_unstage=args.t_unstage,
        ramp_cap=args.ramp_cap,
        policy=args.policy,
        lookahead_k=args.k,
        time_scale=args.time_scale,
        arrival_mode=str(args.arrival_mode),
        episode_seed=int(args.episode_seed),
        shuffle_window=int(args.shuffle_window),
        shuffle_strength=float(args.shuffle_strength),
        episode_id=args.episode_id,
        overhang_mm=args.overhang_mm,
        heuristic=args.heuristic,
        t_select_base=args.t_select_base,
        t_select_step=args.t_select_step,
        time_penalty_weight=args.time_penalty_weight,
        starvation_weight=args.starvation_weight,
        stability_mode=args.stability_mode,
        min_support=args.min_support,
        stability_eps_mm=args.stability_eps_mm,
        settle_snap_grid=bool(args.settle_snap_grid),
        settle_max_iter=int(args.settle_max_iter),
        settle_timeout_ms=int(args.settle_timeout_ms),
        grid_mm=args.grid_mm,
        heavy_bottom=bool(args.heavy_bottom),
        max_overweight_ratio=args.max_overweight_ratio,
        loadbear_penalty_weight=args.loadbear_penalty_weight,
        loadbear_factor=args.loadbear_factor,
        priority_mode=str(args.priority_mode),
        priority_weight=args.priority_weight,
        balance_weight=args.balance_weight,
        coverage_grid_x=int(args.coverage_grid_x),
        coverage_grid_y=int(args.coverage_grid_y),
        coverage_weight=float(args.coverage_weight),
        dominant_free_rect_weight=float(args.dominant_free_rect_weight),
        dominant_free_rect_ratio_gate=float(args.dominant_free_rect_ratio_gate),
        score_mode=str(args.score_mode),
        height_slack_mm=int(args.height_slack_mm),
        orientation_mode=str(args.orientation_mode),
        stand_hw_height_margin_gate_mm=int(args.stand_hw_height_margin_gate_mm),
        time_budget_ms=args.time_budget_ms,
        micro_plan=bool(args.micro_plan),
        micro_depth=int(args.micro_depth),
        micro_width=int(args.micro_width),
        micro_topk=int(args.micro_topk),
        batchfill_layer_starter=bool(args.batchfill_layer_starter),
        batchfill_starters_max=int(args.batchfill_starters_max),
        batchfill_budget_ms=int(args.batchfill_budget_ms),
        batchfill_greedy_topk=int(args.batchfill_greedy_topk),
        online_controller=bool(args.online_controller),
        controller_debug=bool(args.controller_debug),
        weight_col=args.weight_col,
        max_tries_per_item=int(args.max_tries_per_item),
        max_candidates=int(args.max_candidates),
        max_seconds_per_item=float(args.max_seconds_per_item),
        watchdog_heartbeat_sec=float(args.watchdog_heartbeat_sec),
        force_destination=(
            int(args.force_destination) if args.force_destination is not None else None
        ),
        continuous_pallets=bool(args.continuous_pallets),
        max_pallets=int(args.max_pallets),
        viz=bool(args.viz),
        viz_mode=str(args.viz_mode),
        viz_every=int(args.viz_every),
        viz_labels=bool(args.viz_labels),
        viz_block=bool(args.viz_block),
        viz_debug=bool(args.viz_debug),
        viz_dest=int(args.viz_dest),
    )

    if args.print and args.continuous_pallets:
        _print_continuous_summary(payload, args.force_destination)

    if (not args.out) or args.print:
        print(json.dumps(payload, indent=2, ensure_ascii=True))


def _print_continuous_summary(payload: dict[str, Any], forced_destination: int | None) -> None:
    metrics = payload.get("metrics", {})
    pallet_kpis = metrics.get("pallet_kpis", {}) if isinstance(metrics, dict) else {}
    seq_by_dest = pallet_kpis.get("continuous_pallet_sequence", {}) if isinstance(pallet_kpis, dict) else {}
    total_by_dest = pallet_kpis.get("continuous_pallets_total", {}) if isinstance(pallet_kpis, dict) else {}
    reason_by_dest = pallet_kpis.get("continuous_closures_by_reason", {}) if isinstance(pallet_kpis, dict) else {}

    def _lookup(d: Any, dest: int, default: Any) -> Any:
        if not isinstance(d, dict):
            return default
        if dest in d:
            return d[dest]
        key = str(dest)
        if key in d:
            return d[key]
        return default

    destination = int(forced_destination) if forced_destination is not None else 1
    sequence = _lookup(seq_by_dest, destination, [])
    pallets_total = _lookup(total_by_dest, destination, len(sequence))
    closures = _lookup(reason_by_dest, destination, {})
    boxes_total = int(sum(sequence)) if isinstance(sequence, list) else 0

    print(f"continuous dest={destination} pallets={pallets_total} boxes_total={boxes_total}")
    print(f"sequence: {sequence} (len={len(sequence)} sum={boxes_total})")
    print(f"closures_by_reason: {closures}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)


def _write_csv(path: Path, payload: dict[str, Any]) -> None:
    flat = _flatten_dict(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat.keys()))
        writer.writeheader()
        writer.writerow(flat)


def _flatten_dict(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in data.items():
        full_key = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten_dict(value, prefix=f"{full_key}_"))
        else:
            flat[full_key] = value
    return flat


if __name__ == "__main__":
    main()
