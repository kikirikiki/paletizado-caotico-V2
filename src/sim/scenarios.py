from __future__ import annotations

import argparse
import csv
import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .paths import resolve_repo_path
from .run import run_simulation

FIELDNAMES = [
    "model",
    "n_per_pallet",
    "t_pick_place",
    "staging_cap",
    "policy",
    "lookahead_k",
    "time_scale",
    "throughput_per_hour",
    "robot_utilization_percent",
    "upstream_blocked_r1",
    "upstream_blocked_r2",
    "hol_blocked_r1",
    "hol_blocked_r2",
    "max_ramp_occ_r1",
    "max_ramp_occ_r2",
    "wait_p99_r1",
    "wait_p99_r2",
    "staging_max_r1",
    "staging_max_r2",
    "staging_full_pct_r1",
    "staging_full_pct_r2",
    "changeovers_total",
    "changeover_time_total",
]

LOOKAHEAD_K_VALUES = (1, 3, 5, 10, 15)
TIME_SCALE_VALUES = (1.0, 2.0, 3.0, 4.0)


@dataclass(frozen=True)
class ScenarioParams:
    model: str
    n_per_pallet: int
    t_pick_place: float
    staging_cap: int
    policy: str
    lookahead_k: int
    time_scale: float

    def json_name(self) -> str:
        t_pick = _format_float(self.t_pick_place)
        k_tag = f"k{self.lookahead_k}"
        ts_tag = f"ts{_format_float(self.time_scale)}"
        return f"{self.model}_n{self.n_per_pallet}_t{t_pick}_s{self.staging_cap}_{self.policy}_{k_tag}_{ts_tag}.json"


@dataclass(frozen=True)
class ScenarioRow:
    model: str
    n_per_pallet: int
    t_pick_place: float
    staging_cap: int
    policy: str
    lookahead_k: int
    time_scale: float
    throughput_per_hour: float
    robot_utilization_percent: float
    upstream_blocked_r1: float
    upstream_blocked_r2: float
    hol_blocked_r1: float
    hol_blocked_r2: float
    max_ramp_occ_r1: int
    max_ramp_occ_r2: int
    wait_p99_r1: float
    wait_p99_r2: float
    staging_max_r1: int
    staging_max_r2: int
    staging_full_pct_r1: float
    staging_full_pct_r2: float
    changeovers_total: int
    changeover_time_total: float

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "n_per_pallet": self.n_per_pallet,
            "t_pick_place": self.t_pick_place,
            "staging_cap": self.staging_cap,
            "policy": self.policy,
            "lookahead_k": self.lookahead_k,
            "time_scale": self.time_scale,
            "throughput_per_hour": self.throughput_per_hour,
            "robot_utilization_percent": self.robot_utilization_percent,
            "upstream_blocked_r1": self.upstream_blocked_r1,
            "upstream_blocked_r2": self.upstream_blocked_r2,
            "hol_blocked_r1": self.hol_blocked_r1,
            "hol_blocked_r2": self.hol_blocked_r2,
            "max_ramp_occ_r1": self.max_ramp_occ_r1,
            "max_ramp_occ_r2": self.max_ramp_occ_r2,
            "wait_p99_r1": self.wait_p99_r1,
            "wait_p99_r2": self.wait_p99_r2,
            "staging_max_r1": self.staging_max_r1,
            "staging_max_r2": self.staging_max_r2,
            "staging_full_pct_r1": self.staging_full_pct_r1,
            "staging_full_pct_r2": self.staging_full_pct_r2,
            "changeovers_total": self.changeovers_total,
            "changeover_time_total": self.changeover_time_total,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Runner de escenarios de simulacion")
    parser.add_argument("--excel", required=True, help="Ruta al Excel de entradas")
    parser.add_argument("--models", nargs="+", choices=["M1", "M2"], default=["M1", "M2"])
    parser.add_argument("--n_per_pallet", nargs="+", type=int, default=[24, 30])
    parser.add_argument("--t_pick_place", nargs="+", type=float, default=[10.0, 14.0, 18.0])
    parser.add_argument("--staging_cap", nargs="+", type=int, default=[0, 3, 6, 10])
    parser.add_argument("--policy", choices=["legacy", "palca"], default="legacy")
    parser.add_argument("--lookahead_k", nargs="+", type=int, default=[1])
    parser.add_argument("--time_scale", nargs="+", type=float, default=[1.0])
    parser.add_argument("--out_csv", type=str, default="outputs/grid_results.csv")
    parser.add_argument("--out_json_dir", type=str, default="outputs/grid_json")
    return parser


def run_grid(
    excel_path: str,
    models: Iterable[str],
    n_per_pallet: Iterable[int],
    t_pick_place: Iterable[float],
    staging_caps: Iterable[int],
    policy: str = "legacy",
    lookahead_ks: Iterable[int] = (1,),
    time_scales: Iterable[float] = (1.0,),
    out_csv: str | Path = 'outputs/grid_results.csv',
    out_json_dir: str | Path = "outputs/grid_json",
) -> list[dict[str, object]]:
    out_csv_path = _resolve_output_path(out_csv)
    out_json_path = _resolve_output_path(out_json_dir)
    out_csv_path.parent.mkdir(parents=True, exist_ok=True)
    out_json_path.mkdir(parents=True, exist_ok=True)

    rows: list[ScenarioRow] = []
    if policy == "palca":
        for k in lookahead_ks:
            if int(k) not in LOOKAHEAD_K_VALUES:
                raise ValueError(f"K no soportado: {k}")

    for model, n_per, t_pick, staging_cap, lookahead_k, time_scale in itertools.product(
        models, n_per_pallet, t_pick_place, staging_caps, lookahead_ks, time_scales
    ):
        if model == "M1" and staging_cap != 0:
            continue

        params = ScenarioParams(
            model=model,
            n_per_pallet=int(n_per),
            t_pick_place=float(t_pick),
            staging_cap=int(staging_cap),
            policy=str(policy),
            lookahead_k=int(lookahead_k),
            time_scale=float(time_scale),
        )
        out_json = out_json_path / params.json_name()
        payload = run_simulation(
            excel_path=excel_path,
            model=params.model,
            n_per_pallet=params.n_per_pallet,
            t_pick_place=params.t_pick_place,
            staging_cap=params.staging_cap,
            out_path=str(out_json),
            policy=params.policy,
            lookahead_k=params.lookahead_k,
            time_scale=params.time_scale,
        )
        rows.append(_payload_to_row(payload))

    row_dicts = [row.to_dict() for row in rows]
    _write_csv(out_csv_path, row_dicts)
    print(f"Wrote {out_csv_path} ({len(row_dicts)} rows)")
    return row_dicts


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    run_grid(
        excel_path=args.excel,
        models=args.models,
        n_per_pallet=args.n_per_pallet,
        t_pick_place=args.t_pick_place,
        staging_caps=args.staging_cap,
        policy=args.policy,
        lookahead_ks=args.lookahead_k,
        time_scales=args.time_scale,
        out_csv=args.out_csv,
        out_json_dir=args.out_json_dir,
    )


def _resolve_output_path(path: str | Path) -> Path:
    if isinstance(path, Path):
        if path.is_absolute():
            return path
        return resolve_repo_path(str(path))
    if Path(path).is_absolute():
        return Path(path)
    return resolve_repo_path(path)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _payload_to_row(payload: dict[str, object]) -> ScenarioRow:
    params = payload.get("params", {})
    metrics = payload.get("metrics", {})

    upstream = _get_metric(metrics, "upstream_blocked_time")
    hol = _get_metric(metrics, "hol_blocked_time")
    max_ramp = _get_metric(metrics, "max_ramp_occupancy")
    staging_max = _get_metric(metrics, "staging_max_occupancy")
    staging_full = _get_metric(metrics, "staging_full_percent")
    wait = _get_metric(metrics, "ramp_wait_percentiles")

    wait_r1 = _get_nested(wait, 1)
    wait_r2 = _get_nested(wait, 2)

    changeovers = _get_metric(metrics, "changeovers_by_destination")
    changeover_time = _get_metric(metrics, "changeover_time_by_destination")

    return ScenarioRow(
        model=str(payload.get("model")),
        n_per_pallet=int(_get_value(params, "n_per_pallet", 0)),
        t_pick_place=float(_get_value(params, "t_pick_place", 0.0)),
        staging_cap=int(_get_value(params, "staging_cap", 0)),
        policy=str(_get_value(params, "policy", "legacy")),
        lookahead_k=int(_get_value(params, "lookahead_k", 1)),
        time_scale=float(_get_value(params, "time_scale", 1.0)),
        throughput_per_hour=float(_get_value(metrics, "throughput_per_hour", 0.0)),
        robot_utilization_percent=float(_get_value(metrics, "robot_utilization_percent", 0.0)),
        upstream_blocked_r1=float(_get_ramp_value(upstream, 1, 0.0)),
        upstream_blocked_r2=float(_get_ramp_value(upstream, 2, 0.0)),
        hol_blocked_r1=float(_get_ramp_value(hol, 1, 0.0)),
        hol_blocked_r2=float(_get_ramp_value(hol, 2, 0.0)),
        max_ramp_occ_r1=int(_get_ramp_value(max_ramp, 1, 0)),
        max_ramp_occ_r2=int(_get_ramp_value(max_ramp, 2, 0)),
        wait_p99_r1=float(_get_value(wait_r1, "p99", 0.0)),
        wait_p99_r2=float(_get_value(wait_r2, "p99", 0.0)),
        staging_max_r1=int(_get_ramp_value(staging_max, 1, 0)),
        staging_max_r2=int(_get_ramp_value(staging_max, 2, 0)),
        staging_full_pct_r1=float(_get_ramp_value(staging_full, 1, 0.0)),
        staging_full_pct_r2=float(_get_ramp_value(staging_full, 2, 0.0)),
        changeovers_total=int(_sum_metric(changeovers)),
        changeover_time_total=float(_sum_metric(changeover_time)),
    )


def _format_float(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value}".replace(".", "p")


def _get_metric(metrics: dict[str, object], key: str) -> dict[object, object]:
    value = metrics.get(key, {})
    if isinstance(value, dict):
        return value
    return {}


def _get_value(data: dict[object, object], key: object, default: object) -> object:
    if isinstance(data, dict) and key in data:
        return data[key]
    return default


def _get_ramp_value(data: dict[object, object], ramp_id: int, default: object) -> object:
    if ramp_id in data:
        return data[ramp_id]
    if str(ramp_id) in data:
        return data[str(ramp_id)]
    return default


def _get_nested(data: dict[object, object], ramp_id: int) -> dict[object, object]:
    nested = _get_ramp_value(data, ramp_id, {})
    if isinstance(nested, dict):
        return nested
    return {}


def _sum_metric(metric: dict[object, object]) -> float:
    if not metric:
        return 0.0
    return sum(float(value) for value in metric.values())


if __name__ == "__main__":
    main()
