#!/usr/bin/env python3
"""
Benchmark de reglas de desvío en el divergente físico.

Uso:
  python scripts/benchmark_divert_rules.py \\
    --input-dir data/multi_ramp_inputs/ \\
    --seeds 42 123 777 999 1234 \\
    --rules random size_natural size_median round_robin load_balance \\
    --out results/benchmark_divert_rules.json \\
    --workers 5
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sim.run import run_simulation

BASELINE_REAL = 15.5

SIM_PARAMS = dict(
    model="M1",
    n_per_pallet=999999,
    t_pick_place=14.0,
    staging_cap=0,
    ramp_cap=15,
    policy="palca",
    lookahead_k=15,
    arrival_mode="immediate",
    stacking_mode="heightfield",
    stability_mode="ratio+corners+settle",
    min_support=0.85,
    overhang_mm=20,
    orientation_mode="planar+stand_hw",
    stand_hw_height_margin_gate_mm=400,
    accessibility_delta_mm=400,
    score_mode="min_height_slack_then_gain",
    height_slack_mm=120,
    heuristic="bssf",
    time_budget_ms=2000,
    continuous_pallets=True,
)


def _get_git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=Path(__file__).parent.parent,
        ).decode().strip()
    except Exception:
        return "unknown"


def _excel_path(input_dir: Path, rule: str, seed: int) -> Path:
    """Devuelve la ruta del xlsx para la combinación (regla, seed)."""
    if rule == "random":
        # usa los ficheros baseline existentes (sin prefijo de regla)
        return input_dir / f"flujo_mr_seed{seed:04d}.xlsx"
    return input_dir / f"flujo_mr_{rule}_seed{seed:04d}.xlsx"


def _compute_seed_metrics(
    excel_path: str,
    seed: int,
) -> dict:
    """Corre la simulación y extrae métricas de densidad de paletizado."""
    payload = run_simulation(
        excel_path=excel_path,
        out_path=None,
        **SIM_PARAMS,
    )

    metrics = payload["metrics"]
    kpis = metrics.get("pallet_kpis", {})
    seq_by_dest: dict = kpis.get("continuous_pallet_sequence", {})
    reasons_by_dest: dict = kpis.get("continuous_closures_by_reason", {})

    all_completos: list[int] = []
    all_todos: list[int] = []
    por_destino: dict[str, dict] = {}

    for dest_key in sorted(seq_by_dest.keys(), key=lambda x: int(x)):
        seq: list[int] = list(seq_by_dest[dest_key])
        reasons: dict[str, int] = dict(reasons_by_dest.get(dest_key, {}))
        n_end = int(reasons.get("END", 0))
        completos = seq[:-n_end] if n_end > 0 else list(seq)

        all_todos.extend(seq)
        all_completos.extend(completos)
        por_destino[str(dest_key)] = {
            "todos": seq,
            "completos": completos,
            "motivos": reasons,
        }

    palets_completos = len(all_completos)
    cajas_completas = sum(all_completos)
    media = round(cajas_completas / palets_completos, 1) if palets_completos > 0 else 0.0
    mn = min(all_completos) if all_completos else 0
    mx = max(all_completos) if all_completos else 0

    return {
        "processed": metrics.get("processed_boxes", 0),
        "total": metrics.get("total_boxes", 0),
        "stop_reason": metrics.get("stop_reason", None),
        "palets_completos": palets_completos,
        "cajas_completas": cajas_completas,
        "media": media,
        "min": mn,
        "max": mx,
        "secuencia_completa": all_todos,
        "por_destino": por_destino,
    }


def _run_one(args: tuple[str, str, int, str]) -> tuple[str, int, dict | str]:
    """Worker para ProcessPoolExecutor."""
    rule, excel_path, seed, _ = args
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    try:
        result = _compute_seed_metrics(excel_path, seed)
        return rule, seed, result
    except Exception as exc:
        return rule, seed, f"ERROR: {exc}"


def _build_rule_summary(seed_results: dict[str, dict | str]) -> dict:
    medias = [v["media"] for v in seed_results.values() if isinstance(v, dict)]
    if not medias:
        return {"media": 0.0, "std": 0.0, "min": 0, "max": 0}
    return {
        "media": round(statistics.mean(medias), 1),
        "std": round(statistics.stdev(medias) if len(medias) > 1 else 0.0, 2),
        "min": min(int(v["min"]) for v in seed_results.values() if isinstance(v, dict)),
        "max": max(int(v["max"]) for v in seed_results.values() if isinstance(v, dict)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark de reglas de desvío")
    parser.add_argument("--input-dir", required=True, help="Directorio con los xlsx de entrada")
    parser.add_argument("--seeds", type=int, nargs="+", required=True)
    parser.add_argument(
        "--rules",
        nargs="+",
        default=["random", "size_natural", "size_median", "round_robin", "load_balance"],
    )
    parser.add_argument("--out", required=True, help="Ruta del JSON de salida")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Verificar que existen los ficheros necesarios
    missing = []
    for rule in args.rules:
        for seed in args.seeds:
            p = _excel_path(input_dir, rule, seed)
            if not p.exists():
                missing.append(str(p))
    if missing:
        print("ERROR: Faltan ficheros de entrada:")
        for m in missing:
            print(f"  {m}")
        sys.exit(1)

    # Lanzar tareas en paralelo
    tasks = [
        (rule, str(_excel_path(input_dir, rule, seed)), seed, rule)
        for rule in args.rules
        for seed in args.seeds
    ]

    print(f"Lanzando {len(tasks)} simulaciones con {args.workers} worker(s)...\n")

    results_raw: dict[str, dict[str, dict | str]] = {rule: {} for rule in args.rules}
    completed = 0

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(_run_one, t): t for t in tasks}
        for fut in as_completed(futures):
            rule, seed, result = fut.result()
            results_raw[rule][str(seed)] = result
            completed += 1
            status = result["media"] if isinstance(result, dict) else result
            print(f"  [{completed}/{len(tasks)}] {rule} seed={seed}  media={status}")

    # Construir JSON de salida
    rules_out: dict[str, dict] = {}
    for rule in args.rules:
        seed_results = {}
        for seed in args.seeds:
            r = results_raw[rule].get(str(seed), {})
            seed_results[f"{seed:04d}"] = r
        summary = _build_rule_summary(seed_results)
        rules_out[rule] = {"seeds": seed_results, "summary": summary}

    # Ranking por media descendente
    ranking = sorted(
        [
            {
                "rule": rule,
                "media": rules_out[rule]["summary"]["media"],
                "vs_baseline_pct": round(
                    (rules_out[rule]["summary"]["media"] - BASELINE_REAL) / BASELINE_REAL * 100, 1
                ),
            }
            for rule in args.rules
        ],
        key=lambda x: x["media"],
        reverse=True,
    )

    output = {
        "benchmark_config": {
            "arrival_mode": SIM_PARAMS["arrival_mode"],
            "params": {k: v for k, v in SIM_PARAMS.items()},
            "git_head": _get_git_head(),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "baseline_real": BASELINE_REAL,
        "rules": rules_out,
        "ranking": ranking,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    print(f"\nJSON guardado en: {out_path}\n")

    # Tabla resumen en consola
    random_media = rules_out.get("random", {}).get("summary", {}).get("media", BASELINE_REAL)
    print("=== BENCHMARK REGLAS DE DESVÍO ===")
    header = f"{'rule':<16} {'seeds':>5}  {'media':>6}  {'std':>5}  {'min':>4}  {'max':>4}  {'vs_baseline':>12}"
    print(header)
    print("-" * len(header))
    for entry in ranking:
        rule = entry["rule"]
        s = rules_out[rule]["summary"]
        n_seeds = sum(1 for v in results_raw[rule].values() if isinstance(v, dict))
        vs = entry["vs_baseline_pct"]
        vs_str = f"+{vs:.1f}%" if vs >= 0 else f"{vs:.1f}%"
        print(
            f"{rule:<16} {n_seeds:>5}  {s['media']:>6.1f}  {s['std']:>5.2f}  "
            f"{s['min']:>4}  {s['max']:>4}  {vs_str:>12}"
        )
    print("-" * len(header))
    print(f"baseline_real: {BASELINE_REAL} cajas/palet")


if __name__ == "__main__":
    main()
