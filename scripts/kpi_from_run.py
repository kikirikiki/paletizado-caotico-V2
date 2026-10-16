#!/usr/bin/env python3
"""Calcula el KPI congelado y, opcionalmente, valida los palets físicos."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from palca.study.metric import run_kpi, split_pallets
from palca.study.params import REFERENCE_EXCEL
from palca.study.validate import validate_pallet
from palca.tuning.episodes import apply_shuffle
from sim.io import load_arrivals


def original_dims_for_run(payload: dict, excel_path: str) -> dict[int, tuple]:
    """Reproduce identidad de entrada del DES, sin usar dimensiones del packer.

    load_arrivals conserva row_idx de Excel; run_simulation baraja por row_idx
    y lo renumera; simulate enumera desde 1 por row_idx (immediate) o por
    (time,row_idx) (excel). time_scale positivo no altera ese orden.
    Solo loader Excel estándar; el payload actual no identifica multi_ramp.
    """
    params = payload["params"]
    priority = params.get("priority_mode", "")
    priority_col = priority.split(":", 1)[1].strip() if priority.lower().startswith("excel:") else None
    arrivals = load_arrivals(excel_path, weight_col=params.get("weight_col"), priority_col=priority_col)
    window, strength = params.get("shuffle_window", 0), params.get("shuffle_strength", 0.0)
    if int(window) > 1 and float(strength) > 0:
        arrivals = apply_shuffle(sorted(arrivals, key=lambda a: a.row_idx),
                                 seed=params.get("episode_seed", 0), window=window, strength=strength)
        indexed = list(enumerate(arrivals, start=1))
    else:
        indexed = [(a.row_idx, a) for a in arrivals]
    mode = params.get("arrival_mode", "excel").strip().lower()
    if mode == "excel":
        indexed.sort(key=lambda pair: (pair[1].time, pair[0]))
    elif mode == "immediate":
        indexed.sort(key=lambda pair: pair[0])
    else:
        raise ValueError(f"arrival_mode desconocido: {mode}")
    result = {}
    for box_id, (_, arrival) in enumerate(indexed, start=1):
        dims = (arrival.length_mm, arrival.width_mm, arrival.height_mm)
        if any(dim is None or dim <= 0 for dim in dims):
            raise ValueError(f"box_id {box_id}: faltan dimensiones originales positivas en Excel")
        result[box_id] = dims
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--dump", required=True, type=Path)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--excel", default=REFERENCE_EXCEL,
                        help="Excel de entrada usado en el run; por defecto el canónico")
    args = parser.parse_args()
    try:
        payload = json.loads(args.run.read_text())
        dump = json.loads(args.dump.read_text())
        if "params" in dump and dump["params"] != payload.get("params"):
            raise ValueError("Los params del run y dump no coinciden")
        records = split_pallets(payload, dump)
        result = run_kpi(records)
        if args.validate:
            originals = original_dims_for_run(payload, args.excel)
            violations = []
            for rec in records:
                for violation in validate_pallet(rec.items, originals,
                                                  min_support=payload["params"].get("min_support", 0.85)):
                    violations.append({"dest": rec.dest, "index": rec.index, **asdict(violation)})
            result["n_violations"] = len(violations)
            result["violations"] = violations
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    except (OSError, KeyError, ValueError, TypeError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
