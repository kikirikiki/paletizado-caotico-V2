from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from .des import SimConfig, simulate
from .io import load_arrivals


T_PICK_PLACE_VALUES = (10.0, 14.0, 18.0)
N_PER_PALLET_VALUES = (24, 30)
STAGING_CAP_VALUES = (0, 3, 6, 10)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch runner para barrido de parametros")
    parser.add_argument("--excel", required=True, help="Ruta al Excel de entradas")
    parser.add_argument("--model", choices=["M1", "M2"], default="M2")
    parser.add_argument("--out", type=str, default="batch_metrics.csv")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    arrivals = load_arrivals(args.excel)
    results: list[dict[str, Any]] = []
    for t_pick in T_PICK_PLACE_VALUES:
        for n_per in N_PER_PALLET_VALUES:
            for staging_cap in STAGING_CAP_VALUES:
                if args.model == "M1" and staging_cap != 0:
                    continue
                config = SimConfig(
                    model=args.model,
                    ramp_capacity=15,
                    staging_capacity=staging_cap,
                    n_per_pallet=n_per,
                    t_pick_place=t_pick,
                )
                sim_result = simulate(arrivals, config)
                payload: dict[str, Any] = {
                    "model": args.model,
                    "n_per_pallet": n_per,
                    "t_pick_place": t_pick,
                    "staging_cap": staging_cap,
                    "metrics": sim_result.to_dict(),
                }
                results.append(payload)

    out_path = Path(args.out)
    if out_path.suffix.lower() == ".json":
        _write_json(out_path, results)
    else:
        _write_csv(out_path, results)


def _write_json(path: Path, payload: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)


def _write_csv(path: Path, payload: list[dict[str, Any]]) -> None:
    rows = [_flatten_dict(item) for item in payload]
    fieldnames = sorted({key for row in rows for key in row.keys()})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


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
