#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import sys

sys.path.append(str(Path(__file__).resolve().parent / "src"))

from sim.discrete import Box, SimConfig, assign_ramp, simulate


def _find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    candidates = [c.lower() for c in candidates]
    for name in df.columns:
        lowered = str(name).strip().lower()
        if lowered in candidates:
            return name
    for name in df.columns:
        lowered = str(name).strip().lower()
        for c in candidates:
            if c in lowered:
                return name
    return None


def _pick_input_file(path: Path | None) -> Path:
    if path is not None:
        return path

    data_dir = Path("data")
    matches = sorted(data_dir.glob("*.xlsx")) + sorted(data_dir.glob("*.csv"))
    if not matches:
        raise FileNotFoundError(
            "No input file found in data/. Pass a path with --input."
        )
    return matches[0]


def _parse_time_series(series: pd.Series) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce", dayfirst=True)
    if dt.notna().sum() >= 2:
        t0 = dt.min()
        return (dt - t0).dt.total_seconds()
    return pd.to_numeric(series, errors="coerce")


def _load_boxes(
    df: pd.DataFrame,
    default_height: float,
    time_col: str | None = None,
    dest_col: str | None = None,
    height_col: str | None = None,
) -> list[Box]:
    time_candidates = [
        "arrival", "arrival_time", "arribo", "timestamp", "time", "fecha", "hora",
        "fecha_hora", "t",
    ]
    dest_candidates = ["destino", "dest", "destination", "dock", "zona", "zone"]
    height_candidates = ["z", "alto", "height", "h", "altura"]

    time_col = time_col or _find_column(df, time_candidates)
    dest_col = dest_col or _find_column(df, dest_candidates)
    height_col = height_col or _find_column(df, height_candidates)

    if time_col is None:
        raise ValueError("Time column not found.")
    if dest_col is None:
        raise ValueError("Destination column not found.")

    times = _parse_time_series(df[time_col])
    dests = pd.to_numeric(df[dest_col], errors="coerce")
    if height_col is None:
        heights = pd.Series([default_height] * len(df))
    else:
        heights = pd.to_numeric(df[height_col], errors="coerce").fillna(default_height)

    data = pd.DataFrame(
        {
            "arrival_time": times,
            "destination": dests,
            "height": heights,
        }
    )
    data = data.dropna(subset=["arrival_time", "destination"]).copy()
    data["destination"] = data["destination"].astype(int)

    invalid = data[(data["destination"] < 1) | (data["destination"] > 6)]
    if not invalid.empty:
        invalid_set = sorted(invalid["destination"].unique().tolist())
        raise ValueError(f"Destinos fuera de rango (1-6): {invalid_set}")

    data = data.sort_values("arrival_time", kind="mergesort")
    boxes: list[Box] = []
    for i, row in enumerate(data.itertuples(index=False)):
        dest = int(row.destination)
        boxes.append(
            Box(
                box_id=i,
                arrival_time=float(row.arrival_time),
                destination=dest,
                height=float(row.height),
                ramp_id=assign_ramp(dest),
            )
        )
    return boxes


def _format_percentiles(percentiles: dict[str, float]) -> str:
    if not percentiles:
        return "n/a"
    keys = ["p50", "p90", "p95", "p99"]
    return ", ".join(f"{k}={percentiles[k]:.3f}" for k in keys if k in percentiles)


def main() -> int:
    parser = argparse.ArgumentParser(description="Discrete palletizer simulator.")
    parser.add_argument("--input", type=str, default=None, help="Excel/CSV path.")
    parser.add_argument("--time-col", type=str, default=None)
    parser.add_argument("--dest-col", type=str, default=None)
    parser.add_argument("--height-col", type=str, default=None)
    parser.add_argument("--default-height", type=float, default=200.0)
    parser.add_argument("--ramp-capacity", type=int, default=15)
    parser.add_argument("--pallet-max-height", type=float, default=2400.0)
    parser.add_argument("--t-pick", type=float, default=1.0)
    parser.add_argument("--t-place", type=float, default=1.0)
    parser.add_argument("--t-travel-ramp-pallet", type=float, default=1.0)
    parser.add_argument("--t-travel-pallet-home", type=float, default=1.0)
    parser.add_argument("--t-change-pallet", type=float, default=60.0)
    parser.add_argument("--t-z", type=float, default=0.0)
    parser.add_argument(
        "--extra-height-source",
        type=str,
        default="pallet",
        choices=["pallet", "box"],
    )
    parser.add_argument(
        "--policy",
        type=str,
        default="oldest",
        choices=["oldest", "longest", "ramp1_first", "ramp2_first"],
    )
    parser.add_argument("--json-out", type=str, default=None)
    args = parser.parse_args()

    input_path = _pick_input_file(Path(args.input) if args.input else None)
    if input_path.suffix.lower() == ".csv":
        df = pd.read_csv(input_path)
    else:
        df = pd.read_excel(input_path)

    boxes = _load_boxes(
        df,
        default_height=args.default_height,
        time_col=args.time_col,
        dest_col=args.dest_col,
        height_col=args.height_col,
    )

    config = SimConfig(
        ramp_capacity=args.ramp_capacity,
        pallet_max_height=args.pallet_max_height,
        t_pick=args.t_pick,
        t_place=args.t_place,
        t_travel_ramp_pallet=args.t_travel_ramp_pallet,
        t_travel_pallet_home=args.t_travel_pallet_home,
        t_change_pallet=args.t_change_pallet,
        t_z=args.t_z,
        extra_height_source=args.extra_height_source,
        default_box_height=args.default_height,
        policy=args.policy,
    )

    result = simulate(boxes, config)

    print(f"Input: {input_path}")
    print(f"Boxes: {result.total_boxes}, processed: {result.processed_boxes}")
    print(f"Sim time: {result.sim_time:.3f}")
    print(f"Throughput: {result.throughput:.6f} boxes/time")
    print(f"Robot utilization: {result.robot_utilization * 100:.2f}%")
    print()
    print("Ramp blocked time (%):")
    for rid in sorted(result.ramp_blocked_time):
        percent = result.ramp_blocked_percent[rid]
        print(f"  Ramp {rid}: {result.ramp_blocked_time[rid]:.3f} ({percent:.2f}%)")
    print("Upstream blocked time:")
    for rid in sorted(result.upstream_blocked_time):
        print(
            f"  Ramp {rid}: {result.upstream_blocked_time[rid]:.3f} "
            f"(boxes blocked: {result.upstream_blocked_count[rid]})"
        )
    print()
    print(
        "Queue wait mean: "
        f"{result.queue_wait_mean:.3f} | {_format_percentiles(result.queue_wait_percentiles)}"
    )
    print()
    print("Boxes by destination:")
    for dest in sorted(result.boxes_by_destination):
        print(f"  Dest {dest}: {result.boxes_by_destination[dest]}")
    print("Pallets completed by destination:")
    for dest in sorted(result.pallets_completed_by_destination):
        print(f"  Dest {dest}: {result.pallets_completed_by_destination[dest]}")

    if args.json_out:
        out_path = Path(args.json_out)
        payload = result.to_dict()
        payload["queue_wait_count"] = len(result.queue_wait_times)
        payload["queue_wait_std"] = (
            float(np.std(result.queue_wait_times)) if result.queue_wait_times else 0.0
        )
        out_path.write_text(json.dumps(payload, indent=2))
        print(f"\nSaved JSON KPIs to {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
