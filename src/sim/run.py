from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any

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
    parser.add_argument("--grid-mm", type=int, default=None, help="Tamaño de grid (mm)")
    parser.add_argument("--heavy-bottom", action="store_true", help="Penaliza pesado sobre débil")
    parser.add_argument("--max-overweight-ratio", type=float, default=1.5, help="Ratio máximo peso/soporte")
    parser.add_argument("--loadbear-penalty-weight", type=float, default=1.0, help="Peso de penalización loadbear")
    parser.add_argument("--loadbear-factor", type=float, default=1.0, help="Factor para capacidad loadbear")
    parser.add_argument(
        "--priority-mode",
        type=str,
        default="none",
        help="none | weight | excel[:colname]",
    )
    parser.add_argument("--priority-weight", type=float, default=1.0, help="Peso del bonus por prioridad")
    parser.add_argument("--balance-weight", type=float, default=0.0, help="Peso del balance en score")
    parser.add_argument("--time-budget-ms", type=int, default=120, help="Presupuesto por decision (ms)")
    parser.add_argument("--weight-col", type=str, default=None, help="Columna peso (opcional)")

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
        "--viz-debug",
        action="store_true",
        help="Debug del visor (imprime destinos detectados y confirma attach)",
    )

    return parser


def _detect_destinations(arrivals: list[Arrival]) -> list[int]:
    """
    Intenta detectar destinos reales del excel para configurar el viewer con pallet_ids correctos.
    Normalmente serán 1..6 o 0..5.
    """
    dests: set[int] = set()
    for a in arrivals:
        try:
            dests.add(int(a.destination))
        except Exception:
            # Si algún destino no es int, lo ignoramos aquí; en tu proyecto deberían ser 0..5 o 1..6.
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
    """
    Crea el viewer de forma perezosa. Si matplotlib no está instalado, falla solo si enabled=True.
    Devuelve (viewer, Rect2DClass).
    """
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

    # Si no detectamos destinos o no son 6, por defecto usamos 1..6
    if not pallet_ids or len(pallet_ids) != 6:
        pallet_ids = [1, 2, 3, 4, 5, 6]

    if viz_debug:
        print(f"[VIZ] pallet_ids for viewer: {pallet_ids}", flush=True)

    # IMPORTANTE: para depurar, NO limpiar por capa.
    # Esto evita el efecto “solo veo una caja que cambia” si layer_idx está mal.
    show_current_layer_only = False

    if viz_mode == "3d":
        viewer = PalletViewer3D(
            enabled=True,
            pallet_ids=tuple(pallet_ids),
            update_every=viz_every,
            show_current_layer_only=show_current_layer_only,
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
    grid_mm: int | None = None,
    heavy_bottom: bool = False,
    max_overweight_ratio: float = 1.5,
    loadbear_penalty_weight: float = 1.0,
    loadbear_factor: float = 1.0,
    priority_mode: str = "none",
    priority_weight: float = 1.0,
    balance_weight: float = 0.0,
    time_budget_ms: int = 120,
    weight_col: str | None = None,
    # viz
    viz: bool = False,
    viz_mode: str = "2d",
    viz_every: int = 10,
    viz_labels: bool = False,
    viz_block: bool = False,
    viz_debug: bool = False,
) -> dict[str, Any]:
    if out_path is None and out_json is not None:
        out_path = out_json
    priority_col = None
    if isinstance(priority_mode, str) and priority_mode.lower().startswith("excel"):
        parts = priority_mode.split(":", 1)
        if len(parts) == 2 and parts[1].strip():
            priority_col = parts[1].strip()
    arrivals = load_arrivals(excel_path, weight_col=weight_col, priority_col=priority_col)

    if time_scale <= 0:
        raise ValueError("time_scale debe ser positivo")

    # time_scale afecta a la llegada (más picos)
    if time_scale != 1.0:
        arrivals = [
            Arrival(
                time=a.time / float(time_scale),
                destination=a.destination,
                row_idx=a.row_idx,
                length_mm=a.length_mm,
                width_mm=a.width_mm,
                height_mm=a.height_mm,
            )
            for a in arrivals
        ]

    # Crear viewer *después* de cargar arrivals, para detectar destinos reales
    pallet_ids = _detect_destinations(arrivals) if viz else None
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
        # Esto ayuda a policies que lean env var
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
            priority_weight=priority_weight,
            stability_mode=stability_mode,
            min_support_ratio=min_support,
            stability_eps_mm=stability_eps_mm,
            settle_snap_grid=settle_snap_grid,
            grid_mm=grid_mm,
            heavy_bottom=heavy_bottom,
            max_overweight_ratio=max_overweight_ratio,
            loadbear_penalty_weight=loadbear_penalty_weight,
            loadbear_factor=loadbear_factor,
            balance_weight=balance_weight,
            priority_mode=priority_mode,
        )

        # Attach viewer de forma robusta:
        if viewer is not None and rect_cls is not None:
            if hasattr(decision_policy, "attach_viewer"):
                # preferido (si lo implementaste)
                decision_policy.attach_viewer(viewer, rect_cls, debug=viz_debug)  # type: ignore[attr-defined]
                if viz_debug:
                    print("[VIZ] attached via decision_policy.attach_viewer()", flush=True)
            else:
                # fallback a tu método actual (privado)
                decision_policy._viewer = viewer  # type: ignore[attr-defined]
                decision_policy._viewer_rect_cls = rect_cls  # type: ignore[attr-defined]
                if viz_debug:
                    print("[VIZ] attached via decision_policy._viewer/_viewer_rect_cls (fallback)", flush=True)

    try:
        result = simulate(arrivals, config, decision_policy=decision_policy)
    finally:
        if viewer is not None:
            viewer.finalize(block=bool(viz_block))

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
            # palca
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
            "grid_mm": grid_mm,
            "heavy_bottom": heavy_bottom,
            "max_overweight_ratio": max_overweight_ratio,
            "loadbear_penalty_weight": loadbear_penalty_weight,
            "loadbear_factor": loadbear_factor,
            "priority_mode": priority_mode,
            "priority_weight": priority_weight,
            "balance_weight": balance_weight,
            "time_budget_ms": time_budget_ms,
            "weight_col": weight_col,
        },
        "metrics": result.to_dict(),
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
        grid_mm=args.grid_mm,
        heavy_bottom=bool(args.heavy_bottom),
        max_overweight_ratio=args.max_overweight_ratio,
        loadbear_penalty_weight=args.loadbear_penalty_weight,
        loadbear_factor=args.loadbear_factor,
        priority_mode=str(args.priority_mode),
        priority_weight=args.priority_weight,
        balance_weight=args.balance_weight,
        time_budget_ms=args.time_budget_ms,
        weight_col=args.weight_col,
        viz=bool(args.viz),
        viz_mode=str(args.viz_mode),
        viz_every=int(args.viz_every),
        viz_labels=bool(args.viz_labels),
        viz_block=bool(args.viz_block),
        viz_debug=bool(args.viz_debug),
    )

    if (not args.out) or args.print:
        print(json.dumps(payload, indent=2, ensure_ascii=True))


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
