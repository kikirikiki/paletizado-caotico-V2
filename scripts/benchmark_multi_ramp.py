#!/usr/bin/env python3
"""
Benchmark multi-rampa reproducible con ProcessPoolExecutor.

Corre run_simulation() en paralelo sobre los Excel de data/multi_ramp_inputs/
y genera un JSON de resultados + tabla de consola.

Uso:
  python scripts/benchmark_multi_ramp.py \\
    --input-dir data/multi_ramp_inputs/ \\
    --out results/benchmark_multi_ramp.json \\
    --seeds 42 123 777 999 1234 \\
    --workers 5
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure src/ is importable in both main process and worker processes (fork-safe)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = str(_REPO_ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from sim.run import run_simulation  # noqa: E402

# ──────────────────────────────────────────────────────────────────────────────
# Validated simulation parameters
# ──────────────────────────────────────────────────────────────────────────────
_SIM_KWARGS: dict[str, Any] = dict(
    model="M1",
    n_per_pallet=999999,
    t_pick_place=14.0,
    staging_cap=0,
    out_path=None,
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

BASELINE_REAL: float = 15.5


# ──────────────────────────────────────────────────────────────────────────────
# Worker (runs in forked process — module-level for picklability)
# ──────────────────────────────────────────────────────────────────────────────
def _run_one(excel_path: str, seed_label: str) -> tuple[str, dict[str, Any]]:
    """Called by worker process. Returns (seed_label, raw_result)."""
    result = run_simulation(excel_path=excel_path, **_SIM_KWARGS)
    return seed_label, result


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _completos_seq(seq: list[int], closures: dict[str, int]) -> list[int]:
    """Excluye el último palet de cada destino (cierre END = palet incompleto)."""
    n_end = int(closures.get("END", 0))
    if n_end > 0 and len(seq) > n_end:
        return seq[:-n_end]
    if seq:
        return seq[:-1]
    return []


def _process_result(seed_label: str, raw: dict[str, Any]) -> dict[str, Any]:
    metrics = raw["metrics"]
    pallet_kpis = metrics.get("pallet_kpis", {})
    sequences: dict[Any, list[int]] = pallet_kpis.get("continuous_pallet_sequence", {})
    closures_by_reason: dict[Any, dict[str, int]] = pallet_kpis.get("continuous_closures_by_reason", {})

    all_completos: list[int] = []
    por_destino: dict[str, dict[str, Any]] = {}

    for dest, seq in sequences.items():
        if not seq:
            continue
        dest_closures = closures_by_reason.get(dest, {})
        completos = _completos_seq(list(seq), dict(dest_closures))
        por_destino[str(dest)] = {
            "todos": list(seq),
            "completos": completos,
            "motivos": dict(dest_closures),
        }
        all_completos.extend(completos)

    cajas_completas = sum(all_completos)
    palets_completos = len(all_completos)
    media = round(cajas_completas / palets_completos, 1) if palets_completos > 0 else 0.0
    min_v = min(all_completos) if all_completos else 0
    max_v = max(all_completos) if all_completos else 0

    # Secuencia global: todos los palets cerrados, todos los destinos
    secuencia_completa: list[int] = []
    for seq in sequences.values():
        secuencia_completa.extend(seq)

    entry: dict[str, Any] = {
        "processed": int(metrics.get("processed_boxes", 0)),
        "total": int(metrics.get("total_boxes", 0)),
        "stop_reason": metrics.get("stop_reason"),
        "palets_completos": palets_completos,
        "cajas_completas": cajas_completas,
        "media": media,
        "min": min_v,
        "max": max_v,
        "secuencia_completa": secuencia_completa,
        "por_destino": por_destino,
    }

    print(f"[seed={seed_label}] palets={palets_completos} media={media}", flush=True)
    return entry


def _git_head() -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "unknown"


def _discover_inputs(input_dir: Path, seeds: list[int] | None) -> list[tuple[str, str]]:
    """Returns list of (absolute_excel_path, seed_label) pairs."""
    if seeds:
        pairs: list[tuple[str, str]] = []
        for seed in seeds:
            pattern = f"*seed{seed:04d}.xlsx"
            matches = sorted(input_dir.glob(pattern))
            if not matches:
                print(f"[WARN] No se encontró fichero para seed={seed:04d} en {input_dir}", file=sys.stderr)
                continue
            pairs.append((str(matches[0].resolve()), f"{seed:04d}"))
        return pairs
    # Auto-discover: todos los xlsx del directorio
    files = sorted(input_dir.glob("*.xlsx"))
    result: list[tuple[str, str]] = []
    for f in files:
        m = re.search(r"seed(\d+)", f.name)
        label = m.group(1) if m else f.stem
        result.append((str(f.resolve()), label))
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark multi-rampa reproducible")
    parser.add_argument(
        "--input-dir",
        default="data/multi_ramp_inputs/",
        help="Directorio con los Excel de input",
    )
    parser.add_argument(
        "--out",
        default="results/benchmark_multi_ramp.json",
        help="Ruta del JSON de salida",
    )
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="*",
        default=None,
        help="Seeds a procesar (default: todos los xlsx del directorio)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="Número de workers para ProcessPoolExecutor",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.is_absolute():
        input_dir = _REPO_ROOT / input_dir

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = _REPO_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pairs = _discover_inputs(input_dir, args.seeds)
    if not pairs:
        print("[ERROR] No se encontraron inputs.", file=sys.stderr)
        sys.exit(1)

    print(f"Corriendo {len(pairs)} seeds con {args.workers} workers...")

    seed_results: dict[str, dict[str, Any]] = {}

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(_run_one, excel_path, label): label
            for excel_path, label in pairs
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                _, raw = future.result()
                seed_results[label] = _process_result(label, raw)
            except Exception as exc:
                import traceback

                print(f"[ERROR] seed={label}: {exc}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)

    if not seed_results:
        print("[ERROR] Ninguna simulación completó correctamente.", file=sys.stderr)
        sys.exit(1)

    # ── Summary ──────────────────────────────────────────────────────────────
    medias = [v["media"] for v in seed_results.values() if v["palets_completos"] > 0]
    all_mins = [v["min"] for v in seed_results.values() if v["palets_completos"] > 0]
    all_maxs = [v["max"] for v in seed_results.values() if v["palets_completos"] > 0]
    all_palets = [v["palets_completos"] for v in seed_results.values()]

    media_global = round(statistics.mean(medias), 1) if medias else 0.0
    std_media = round(statistics.stdev(medias), 2) if len(medias) > 1 else 0.0
    min_global = min(all_mins) if all_mins else 0
    max_global = max(all_maxs) if all_maxs else 0
    vs_baseline_pct = (
        round((media_global - BASELINE_REAL) / BASELINE_REAL * 100, 1) if BASELINE_REAL else 0.0
    )

    summary: dict[str, Any] = {
        "n_seeds": len(seed_results),
        "media_global": media_global,
        "std_media": std_media,
        "min_global": min_global,
        "max_global": max_global,
        "vs_baseline_pct": vs_baseline_pct,
    }

    # ── JSON payload ──────────────────────────────────────────────────────────
    params_for_json = {k: v for k, v in _SIM_KWARGS.items() if k != "out_path"}
    payload: dict[str, Any] = {
        "benchmark_config": {
            "arrival_mode": "immediate",
            "params": params_for_json,
            "git_head": _git_head(),
            "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "baseline_real": BASELINE_REAL,
        "seeds": seed_results,
        "summary": summary,
    }

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    # ── Console table ─────────────────────────────────────────────────────────
    print()
    print("=== BENCHMARK MULTI-RAMP ===")
    print(f"{'seed':<6}  {'palets':>6}  {'media':>6}  {'min':>4}  {'max':>4}  stop")
    for label in sorted(seed_results):
        r = seed_results[label]
        print(
            f"{label:<6}  {r['palets_completos']:>6}  {r['media']:>6.1f}"
            f"  {r['min']:>4}  {r['max']:>4}  {r['stop_reason']}"
        )
    print("-" * 42)
    avg_palets = round(statistics.mean(all_palets), 1) if all_palets else 0.0
    sign = "+" if vs_baseline_pct >= 0 else ""
    print(
        f"{'GLOBAL':<6}  {avg_palets:>6.1f}  {media_global:>6.1f}"
        f"  {min_global:>4}  {max_global:>4}"
    )
    print(f"vs real ({sign}{vs_baseline_pct}%) baseline={BASELINE_REAL}")
    print()
    print(f"Resultados guardados en: {out_path}")


if __name__ == "__main__":
    main()
