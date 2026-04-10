"""
Estudio comparativo de orientaciones de caja — gantry vs brazo robotico.

Hipotesis: permitir stand_hl (6 orientaciones) mejora vol_util% respecto
a stand_hw (4 orientaciones) en escenario gantry (accessibility_delta_mm=0).

Uso:
    cd <repo_root>
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
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sim.run import run_simulation  # noqa: E402

# ---------------------------------------------------------------------------
# Configuracion del estudio
# ---------------------------------------------------------------------------

SEEDS = [50021, 1, 42, 100, 777, 5678, 9999, 12345, 99999, 314159]

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

# Parametros fijos — cargados desde configs/base_params.json
_PARAMS_PATH = Path(__file__).parent / "configs" / "base_params.json"
with open(_PARAMS_PATH) as _f:
    _RAW = json.load(_f)

BASE_PARAMS: dict = {k: v for k, v in _RAW.items() if not k.startswith("_")}

# Volumetria de referencia
VOL_PALET_MM3 = 1240 * 820 * 2400
# Mix: 77% caja dominante 605x445x355, 23% resto (volumen medio estimado)
VOL_MEDIO_MM3 = 0.77 * (605 * 445 * 355) + 0.23 * 78_000_000


# ---------------------------------------------------------------------------
# Ejecucion por seed
# ---------------------------------------------------------------------------

@dataclass
class SeedResult:
    config: str
    seed: int
    boxes_per_pallet: list[float]
    error: str | None = None


def _extract_boxes_per_pallet(result: dict) -> list[float]:
    """Extrae lista de cajas por palet del resultado de run_simulation."""
    for key in ("pallets", "pallet_stats", "pallet_results"):
        pallets = result.get(key)
        if not pallets:
            continue
        out = []
        for p in pallets:
            if isinstance(p, dict):
                n = (
                    p.get("n_boxes")
                    or p.get("boxes_placed")
                    or p.get("num_boxes")
                    or 0
                )
            else:
                n = (
                    getattr(p, "n_boxes", None)
                    or getattr(p, "boxes_placed", None)
                    or getattr(p, "num_boxes", None)
                    or 0
                )
            out.append(float(n))
        if out:
            return out
    return []


def run_one(config_name: str, seed: int, extra: dict) -> SeedResult:
    params = {
        **BASE_PARAMS,
        **{k: v for k, v in extra.items() if not k.startswith("_")},
        "seed": seed,
    }
    try:
        result = run_simulation(**params)
        boxes = _extract_boxes_per_pallet(result)
        # Excluir ultimo palet: suele estar incompleto por agotamiento del flujo
        if len(boxes) > 1:
            boxes = boxes[:-1]
        return SeedResult(config=config_name, seed=seed, boxes_per_pallet=boxes)
    except Exception as exc:
        return SeedResult(config=config_name, seed=seed, boxes_per_pallet=[], error=str(exc))


# ---------------------------------------------------------------------------
# Agregacion y reporte
# ---------------------------------------------------------------------------

def _stats(values: list[float]) -> dict:
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
    header = f"{'config':<28} | {'media':>6} | {'min':>4} | {'max':>4} | {'palets':>6} | {'vol_util%':>9}"
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
    raw: dict[str, list[float]] = {k: [] for k in CONFIGS}
    errors: list[str] = []

    print(f"\nEstudio: gantry-orientations")
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
                msg = f"  [{done:>2}/{total}] ERROR  {cfg:<28} seed={seed}  → {r.error}"
                print(msg)
                errors.append(msg)
            else:
                raw[r.config].extend(r.boxes_per_pallet)
                print(
                    f"  [{done:>2}/{total}] OK     {cfg:<28} seed={seed}"
                    f"  → {len(r.boxes_per_pallet)} palets"
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
                "seeds": SEEDS,
                "configs": {k: {ck: cv for ck, cv in v.items() if not ck.startswith("_")}
                            for k, v in CONFIGS.items()},
                "base_params": BASE_PARAMS,
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
