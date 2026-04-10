"""
Estudio comparativo de orientaciones de caja — gantry vs brazo robotico.

Hipotesis: permitir stand_hl (6 orientaciones totales) mejora vol_util%
respecto a stand_hw (4 orientaciones) en escenario gantry
(accessibility_delta_mm=0).

Uso:
    cd <repo_root>
    source .venv/bin/activate
    export PYTHONPATH=$(pwd)/src
    python studies/gantry_orientations/benchmark.py

Resultados guardados en studies/gantry_orientations/results/ (en .gitignore).
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sim.run import run_simulation  # noqa: E402

# ---------------------------------------------------------------------------
# Configuracion del estudio
# ---------------------------------------------------------------------------

SEEDS = [50021, 1, 42, 100, 777, 5678, 9999, 12345, 99999, 314159]

EXCEL_PATH = str(REPO_ROOT / "data" / "Flujo rampas - Editado.xlsx")

CONFIGS: dict[str, dict] = {
    "brazo_hw": dict(
        orientation_mode="planar+stand_hw",
        accessibility_delta_mm=400,
        _desc="Brazo robotico actual — baseline de produccion",
    ),
    "gantry_planar": dict(
        orientation_mode="planar",
        accessibility_delta_mm=0,
        _desc="Gantry sin rotaciones verticales — control",
    ),
    "gantry_hw": dict(
        orientation_mode="planar+stand_hw",
        accessibility_delta_mm=0,
        _desc="Gantry con stand_hw (4 orientaciones)",
    ),
    "gantry_all": dict(
        orientation_mode="planar+stand_hw+stand_hl",
        accessibility_delta_mm=0,
        _desc="Gantry con stand_hw + stand_hl (6 orientaciones)",
    ),
}

# Parametros fijos de produccion validados
BASE_PARAMS: dict = dict(
    model="M1",
    n_per_pallet=999999,
    t_pick_place=14.0,
    staging_cap=0,
    out_path=None,
    ramp_cap=15,
    policy="palca",
    lookahead_k=15,
    arrival_mode="immediate",
    shuffle_window=15,
    shuffle_strength=1.0,
    overhang_mm=20,
    heuristic="bssf",
    stacking_mode="heightfield",
    stability_mode="ratio+corners+settle",
    min_support=0.85,
    score_mode="min_height_slack_then_gain",
    height_slack_mm=120,
    stand_hw_height_margin_gate_mm=400,
    time_budget_ms=900,
    micro_plan=False,
    force_destination=1,
    continuous_pallets=True,
    max_pallets=0,
)

# Volumetria de referencia
VOL_PALET_MM3 = 1240 * 820 * 2400
VOL_MEDIO_MM3 = 0.77 * (605 * 445 * 355) + 0.23 * 78_000_000


# ---------------------------------------------------------------------------
# Extraccion de metricas (igual que benchmark_divert_rules.py)
# ---------------------------------------------------------------------------

def _extract_boxes_per_pallet(payload: dict) -> list[int]:
    """
    Extrae lista de cajas por palet completo del resultado de run_simulation.
    Excluye el ultimo palet de cada seed (incompleto por agotamiento del flujo).
    """
    metrics = payload.get("metrics", {})
    kpis = metrics.get("pallet_kpis", {})
    seq_by_dest: dict = kpis.get("continuous_pallet_sequence", {})
    reasons_by_dest: dict = kpis.get("continuous_closures_by_reason", {})

    all_completos: list[int] = []

    for dest_key in sorted(seq_by_dest.keys(), key=lambda x: int(x)):
        seq: list[int] = list(seq_by_dest[dest_key])
        reasons: dict[str, int] = dict(reasons_by_dest.get(dest_key, {}))
        n_end = int(reasons.get("END", 0))
        # Excluir ultimo palet (cierre por END = agotamiento de flujo)
        completos = seq[:-n_end] if n_end > 0 else list(seq)
        all_completos.extend(completos)

    return all_completos


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

@dataclass
class SeedResult:
    config: str
    seed: int
    boxes_per_pallet: list[int]
    error: str | None = None


def run_one(config_name: str, seed: int, extra: dict) -> SeedResult:
    # Re-insertar path en el worker (necesario con ProcessPoolExecutor)
    sys.path.insert(0, str(REPO_ROOT / "src"))
    params = {
        **BASE_PARAMS,
        **{k: v for k, v in extra.items() if not k.startswith("_")},
        "excel_path": EXCEL_PATH,
        "episode_seed": seed,
    }
    try:
        payload = run_simulation(**params)
        boxes = _extract_boxes_per_pallet(payload)
        return SeedResult(config=config_name, seed=seed, boxes_per_pallet=boxes)
    except Exception as exc:
        return SeedResult(config=config_name, seed=seed, boxes_per_pallet=[], error=str(exc))


# ---------------------------------------------------------------------------
# Agregacion y reporte
# ---------------------------------------------------------------------------

def _stats(values: list[int]) -> dict:
    if not values:
        return {"media": None, "min": None, "max": None, "n": 0, "vol_util_pct": None}
    media = sum(values) / len(values)
    return {
        "media": round(media, 2),
        "min": int(min(values)),
        "max": int(max(values)),
        "n": len(values),
        "vol_util_pct": round(media * VOL_MEDIO_MM3 / VOL_PALET_MM3 * 100, 1),
    }


def print_table(aggregated: dict[str, dict]) -> None:
    header = (
        f"{'config':<28} | {'media':>6} | {'min':>4} | {'max':>4}"
        f" | {'palets':>6} | {'vol_util%':>9}"
    )
    sep = "-" * len(header)
    print()
    print(header)
    print(sep)
    for cfg in CONFIGS:
        s = aggregated.get(cfg, {})
        if s.get("media") is None:
            print(f"{cfg:<28} | {'ERROR':>6}")
            continue
        print(
            f"{cfg:<28} | {s['media']:>6.1f} | {s['min']:>4} | {s['max']:>4}"
            f" | {s['n']:>6} | {s['vol_util_pct']:>8.1f}%"
        )
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    tasks = [
        (cfg_name, seed, extra)
        for cfg_name, extra in CONFIGS.items()
        for seed in SEEDS
    ]
    total = len(tasks)
    raw: dict[str, list[int]] = {k: [] for k in CONFIGS}
    errors: list[str] = []

    print(f"\nEstudio: gantry-orientations")
    print(f"Excel: {EXCEL_PATH}")
    print(f"Configs: {len(CONFIGS)}  |  Seeds: {len(SEEDS)}  |  Total jobs: {total}")
    print(f"Workers: 14  |  Inicio: {datetime.now().strftime('%H:%M:%S')}\n")

    t0 = time.perf_counter()
    done = 0

    with ProcessPoolExecutor(max_workers=14) as executor:
        futures = {
            executor.submit(run_one, cfg, seed, extra): (cfg, seed)
            for cfg, seed, extra in tasks
        }
        for future in as_completed(futures):
            cfg, seed = futures[future]
            done += 1
            r: SeedResult = future.result()
            if r.error:
                msg = (
                    f"  [{done:>2}/{total}] ERROR  {cfg:<28}"
                    f" seed={seed}  -> {r.error}"
                )
                print(msg)
                errors.append(msg)
            else:
                raw[r.config].extend(r.boxes_per_pallet)
                print(
                    f"  [{done:>2}/{total}] OK     {cfg:<28}"
                    f" seed={seed}  -> {len(r.boxes_per_pallet)} palets"
                )

    elapsed = time.perf_counter() - t0
    print(f"\nTiempo total: {elapsed:.1f}s")

    aggregated = {cfg: _stats(raw[cfg]) for cfg in CONFIGS}
    print_table(aggregated)

    # Guardar resultados en JSON
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = results_dir / f"benchmark_{ts}.json"
    with open(out_path, "w") as f:
        json.dump(
            {
                "timestamp": ts,
                "excel": EXCEL_PATH,
                "seeds": SEEDS,
                "configs": {
                    k: {ck: cv for ck, cv in v.items() if not ck.startswith("_")}
                    for k, v in CONFIGS.items()
                },
                "base_params": {
                    k: v for k, v in BASE_PARAMS.items() if k != "out_path"
                },
                "aggregated": aggregated,
                "errors": errors,
            },
            f,
            indent=2,
        )
    print(f"Resultados guardados en: {out_path.relative_to(REPO_ROOT)}")

    if errors:
        print(f"\nATENCION: {len(errors)} errores durante el benchmark.")
        sys.exit(1)


if __name__ == "__main__":
    main()
