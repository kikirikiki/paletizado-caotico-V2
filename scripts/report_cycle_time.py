#!/usr/bin/env python3
"""Reporte de métricas de tiempo de ciclo para simulaciones multi-rampa.

Uso:
    python scripts/report_cycle_time.py --input-dir data/multi_ramp_inputs/ \
        --rule load_balance --seeds 1-100

    python scripts/report_cycle_time.py --json /tmp/result.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any


# ── Producción real (baseline manual) ────────────────────────────────────────
BASELINE_REAL = {
    "cajas_palet": 15.5,
    "palets_turno": 12,
    "cajas_turno": 186,
    "operarios": 2,
    "turno_min": 255,          # 07:25 → 11:35 del Excel real
}

SIM_PARAMS = [
    "--model", "M1",
    "--n_per_pallet", "999999",
    "--t_pick_place", "14.0",
    "--ramp_cap", "15",
    "--staging_cap", "0",
    "--policy", "palca",
    "--k", "15",
    "--stacking-mode", "heightfield",
    "--stability-mode", "ratio+corners+settle",
    "--min-support", "0.85",
    "--overhang_mm", "20",
    "--orientation-mode", "planar+stand_hw",
    "--stand-hw-height-margin-gate-mm", "400",
    "--accessibility-delta-mm", "400",
    "--score-mode", "min_height_slack_then_gain",
    "--height-slack-mm", "120",
    "--heuristic", "bssf",
    "--time-budget-ms", "2000",
    "--continuous-pallets",
    "--arrival-mode", "immediate",
]


def _run_one(args: tuple[str, str]) -> dict[str, Any]:
    excel, out = args
    _run_sim(excel, out)
    return _load(out)


def _run_sim(excel: str, out: str) -> None:
    python = str(Path(sys.executable))
    cmd = [python, "-m", "sim.run", "--excel", excel, "--out", out] + SIM_PARAMS
    subprocess.run(cmd, check=True, capture_output=True)


def _load(path: str) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def _extract(data: dict[str, Any]) -> dict[str, Any]:
    m = data.get("metrics", {})
    pk = m.get("pallet_kpis", {})

    makespan_s = float(m.get("makespan", 0))
    makespan_min = makespan_s / 60.0
    robot_util = float(m.get("robot_utilization_percent", 0))
    throughput = float(m.get("throughput_per_hour", 0))
    processed = int(m.get("processed_boxes", 0))
    total = int(m.get("total_boxes", 0))

    # Changeover total
    chg_by_dest = m.get("changeover_time_by_destination", {})
    changeover_total_s = sum(float(v) for v in chg_by_dest.values())
    changeover_total_min = changeover_total_s / 60.0

    # Densidad volumétrica por palet
    vol_by_dest = pk.get("pallet_volume_utilization", {})
    vol_vals = [float(v) * 100 for v in vol_by_dest.values() if v is not None]
    vol_mean = statistics.mean(vol_vals) if vol_vals else 0.0

    # Palets completos y media cajas
    seq = pk.get("continuous_pallet_sequence", {})
    reason = pk.get("continuous_closures_by_reason", {})
    all_completos = []
    for dest in range(1, 7):
        s = seq.get(dest, seq.get(str(dest), []))
        r = reason.get(dest, reason.get(str(dest), {}))
        n_end = int(r.get("END", 0))
        completos = s[:-n_end] if n_end > 0 and len(s) > n_end else s[:-1] if s else []
        all_completos.extend(completos)

    n_palets = len(all_completos)
    media_cajas = statistics.mean(all_completos) if all_completos else 0.0

    # Ramp wait p50/p90
    ramp_wait = m.get("ramp_wait_percentiles", {})
    wait_p50 = statistics.mean(
        [v.get("p50", 0) for v in ramp_wait.values()]
    ) if ramp_wait else 0.0
    wait_p90 = statistics.mean(
        [v.get("p90", 0) for v in ramp_wait.values()]
    ) if ramp_wait else 0.0

    return {
        "makespan_min": makespan_min,
        "robot_util_pct": robot_util,
        "throughput_cajas_h": throughput,
        "processed": processed,
        "total": total,
        "changeover_total_min": changeover_total_min,
        "vol_util_pct": vol_mean,
        "n_palets_completos": n_palets,
        "media_cajas_palet": media_cajas,
        "ramp_wait_p50_s": wait_p50,
        "ramp_wait_p90_s": wait_p90,
        "stop_reason": m.get("stop_reason"),
    }


def _print_report(results: list[dict[str, Any]], rule: str = "") -> None:
    if not results:
        print("Sin resultados.")
        return

    def _stats(key: str) -> tuple[float, float, float, float]:
        vals = [r[key] for r in results if r.get(key) is not None]
        if not vals:
            return 0.0, 0.0, 0.0, 0.0
        mean = statistics.mean(vals)
        std = statistics.stdev(vals) if len(vals) > 1 else 0.0
        return mean, std, min(vals), max(vals)

    b = BASELINE_REAL
    n = len(results)

    print()
    print("=" * 62)
    print(f"  REPORTE TIEMPO DE CICLO — {rule or 'simulación'}  (n={n} seeds)")
    print("=" * 62)

    mean_mk, std_mk, min_mk, max_mk = _stats("makespan_min")
    mean_ru, std_ru, _, _ = _stats("robot_util_pct")
    mean_th, std_th, _, _ = _stats("throughput_cajas_h")
    mean_chg, std_chg, _, _ = _stats("changeover_total_min")
    mean_vol, std_vol, _, _ = _stats("vol_util_pct")
    mean_cp, std_cp, min_cp, max_cp = _stats("media_cajas_palet")
    mean_wp50, _, _, _ = _stats("ramp_wait_p50_s")
    mean_wp90, _, _, _ = _stats("ramp_wait_p90_s")

    vs_cajas = 100.0 * (mean_cp - b["cajas_palet"]) / b["cajas_palet"]
    vs_makespan = 100.0 * (mean_mk - b["turno_min"]) / b["turno_min"]

    print(f"\n  {'MÉTRICA':<35} {'MEDIA':>8}  {'STD':>6}  NOTA")
    print(f"  {'-'*35} {'-'*8}  {'-'*6}  {'-'*25}")
    print(f"  {'Makespan (min)':<35} {mean_mk:>7.1f}  {std_mk:>5.1f}  arrival=immediate (sin esperas)")
    print(f"  {'Utilización robot (%)':<35} {mean_ru:>7.1f}  {std_ru:>5.1f}")
    print(f"  {'Throughput (cajas/hora)':<35} {mean_th:>7.1f}  {std_th:>5.1f}")
    print(f"  {'Changeover total (min)':<35} {mean_chg:>7.1f}  {std_chg:>5.1f}")
    print(f"  {'Media cajas/palet':<35} {mean_cp:>7.1f}  {std_cp:>5.1f}  real={b['cajas_palet']} ({vs_cajas:+.1f}%)")
    print(f"  {'Espera rampa p50 (s)':<35} {mean_wp50:>7.1f}  {'—':>5}")
    print(f"  {'Espera rampa p90 (s)':<35} {mean_wp90:>7.1f}  {'—':>5}")
    print(f"  {'Vol util palet (%)':<35} {'68.8':>7}  {'—':>5}  ref CONTEXT.md")

    print(f"\n  Variabilidad (std / min-max sobre {n} seeds):")
    print(f"  {'Makespan':<20} std={std_mk:.1f}  [{min_mk:.0f}–{max_mk:.0f}] min")
    print(f"  {'Cajas/palet':<20} std={std_cp:.1f}  [{min_cp:.1f}–{max_cp:.1f}]")
    print(f"  {'Robot util':<20} std={std_ru:.1f}%")

    stops = sum(1 for r in results if r.get("stop_reason"))
    if stops:
        print(f"\n  ⚠ {stops}/{n} seeds con stop_reason != None")
    else:
        print(f"\n  ✓ {n}/{n} seeds procesados sin stop_reason")

    print("=" * 62)
    print()


def _parse_seed_range(s: str) -> list[int]:
    """Acepta '1-100', '42', '42,123,999'."""
    seeds = []
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            seeds.extend(range(int(a), int(b) + 1))
        else:
            seeds.append(int(part))
    return seeds


def main() -> None:
    parser = argparse.ArgumentParser(description="Reporte métricas tiempo de ciclo")
    parser.add_argument("--json", type=str, default=None,
                        help="JSON de simulación ya existente (modo rápido)")
    parser.add_argument("--input-dir", type=str, default="data/multi_ramp_inputs/",
                        help="Directorio con Excel de inputs")
    parser.add_argument("--rule", type=str, default="load_balance",
                        help="Regla de desvío (prefijo de fichero)")
    parser.add_argument("--seeds", type=str, default="1-100",
                        help="Seeds: '1-100', '42', '42,123,999'")
    parser.add_argument("--workers", type=int, default=16,
                        help="Número de procesos paralelos (default=16)")
    args = parser.parse_args()

    if args.json:
        data = _load(args.json)
        result = _extract(data)
        _print_report([result], rule="single run")
        return

    seeds = _parse_seed_range(args.seeds)
    input_dir = Path(args.input_dir)
    rule = args.rule

    tasks = []
    for seed in seeds:
        fname = f"flujo_mr_{rule}_seed{seed:04d}.xlsx" if rule != "random" \
            else f"flujo_mr_seed{seed:04d}.xlsx"
        excel = str(input_dir / fname)
        if not Path(excel).exists():
            print(f"  [skip] {fname}", file=sys.stderr)
            continue
        out = f"/tmp/ct_{rule}_{seed:04d}.json"
        tasks.append((excel, out))

    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(_run_one, t): t for t in tasks}
        for future in as_completed(futures):
            try:
                data = future.result()
                results.append(_extract(data))
            except Exception as e:
                print(f"  [error] {futures[future]}: {e}", file=sys.stderr)

    _print_report(results, rule=rule)


if __name__ == "__main__":
    main()
