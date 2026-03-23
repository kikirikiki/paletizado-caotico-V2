#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from palca.tuning.halving import successive_halving
from palca.tuning.metrics import merge_metrics, parse_metrics
from palca.tuning.rank import serialize_rank_key
from palca.tuning.runner import run_sim_subprocess


def _parse_csv_tokens(raw: str) -> list[str]:
    return [token.strip() for token in str(raw).split(",") if token.strip()]


def _parse_csv_ints(raw: str) -> list[int]:
    return [int(token) for token in _parse_csv_tokens(raw)]


def _parse_csv_strings(raw: str) -> list[str]:
    return _parse_csv_tokens(raw)


def _default_run_name() -> str:
    return datetime.now().strftime("run_%Y%m%d_%H%M%S")


def _default_jobs() -> int:
    cpus = os.cpu_count() or 1
    return max(1, int(cpus) - 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline auto tuner with successive halving for sim.run")
    parser.add_argument("--excel", required=True, help="Excel de entrada para sim.run")
    parser.add_argument("--run-name", type=str, default=None, help="Nombre de corrida (si no, timestamp)")
    parser.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="Directorio de salida (default: /tmp/palca_tuner/<run_name>)",
    )
    parser.add_argument("--python-exe", type=str, default=sys.executable, help="Python para ejecutar sim.run")
    parser.add_argument("--jobs", type=int, default=_default_jobs(), help="Subprocesos paralelos")
    parser.add_argument("--eta", type=int, default=2, help="Factor de halving (top 1/eta)")
    parser.add_argument("--top-n", type=int, default=10, help="Top N finalistas para top.json")
    parser.add_argument("--dry-run", action="store_true", help="Imprime configs y no ejecuta simulaciones")
    parser.add_argument("--phases", type=str, default="A,B,C", help="Fases activas: A,B,C")
    parser.add_argument("--base-episode-seed", type=int, default=0, help="Seed base para episodios")
    parser.add_argument("--shuffle-window", type=int, default=0, help="Ventana shuffle por episodio")
    parser.add_argument("--shuffle-strength", type=float, default=0.0, help="Intensidad shuffle por episodio")
    parser.add_argument(
        "--timeout-margin-sec",
        type=float,
        default=2.0,
        help="Margen fijo para timeout: 2x(time_budget_ms/1000 + margen)",
    )

    parser.add_argument("--phase-a-budget-ms", type=int, default=1000)
    parser.add_argument("--phase-a-episodes", type=int, default=4)
    parser.add_argument("--phase-a-micro-depth", type=int, default=None)

    parser.add_argument("--phase-b-budget-ms", type=int, default=2800)
    parser.add_argument("--phase-b-episodes", type=int, default=10)
    parser.add_argument("--phase-b-micro-depth", type=int, default=None)

    parser.add_argument("--phase-c-budget-ms", type=int, default=5000)
    parser.add_argument("--phase-c-episodes", type=int, default=20)
    parser.add_argument("--phase-c-micro-depth", type=int, default=None)

    parser.add_argument(
        "--score-modes",
        type=str,
        default="min_height_then_gain,gain_frag,min_height_slack_then_gain",
    )
    parser.add_argument("--height-slacks", type=str, default="0,40,80,120")
    parser.add_argument("--micro-depths", type=str, default="5,6")
    parser.add_argument("--micro-widths", type=str, default="60,80,100")
    parser.add_argument("--micro-topks", type=str, default="12,15,18")

    parser.add_argument("--model", choices=["M1", "M2"], default="M1")
    parser.add_argument("--policy", type=str, default="palca")
    parser.add_argument("--k", type=int, default=15)
    parser.add_argument("--arrival-mode", choices=["excel", "immediate"], default="immediate")
    parser.add_argument("--force-destination", type=int, default=1)
    parser.add_argument("--continuous-pallets", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--heuristic", choices=["baf", "bssf"], default="bssf")
    parser.add_argument(
        "--stability-mode",
        choices=["off", "ratio", "ratio+corners", "ratio+corners+settle"],
        default="ratio+corners+settle",
    )
    parser.add_argument("--min-support", type=float, default=0.85)
    parser.add_argument("--overhang-mm", type=int, default=20)
    parser.add_argument("--n-per-pallet", type=int, default=999999)
    parser.add_argument("--t-pick-place", type=float, default=14.0)
    parser.add_argument("--time-scale", type=float, default=1.0)
    parser.add_argument("--micro-plan", action=argparse.BooleanOptionalAction, default=True)
    return parser


def _build_phase_list(args: argparse.Namespace) -> list[dict[str, Any]]:
    selected = {token.upper() for token in _parse_csv_tokens(args.phases)}
    all_phases = {
        "A": {
            "name": "A",
            "budget_ms": int(args.phase_a_budget_ms),
            "episodes": int(args.phase_a_episodes),
            "micro_depth": args.phase_a_micro_depth,
        },
        "B": {
            "name": "B",
            "budget_ms": int(args.phase_b_budget_ms),
            "episodes": int(args.phase_b_episodes),
            "micro_depth": args.phase_b_micro_depth,
        },
        "C": {
            "name": "C",
            "budget_ms": int(args.phase_c_budget_ms),
            "episodes": int(args.phase_c_episodes),
            "micro_depth": args.phase_c_micro_depth,
        },
    }

    phases: list[dict[str, Any]] = []
    running_offset = 0
    for name in ("A", "B", "C"):
        if name not in selected:
            continue
        phase = dict(all_phases[name])
        phase["seed_offset"] = int(running_offset)
        running_offset += max(0, int(phase["episodes"]))
        if int(phase["budget_ms"]) <= 0:
            raise ValueError(f"phase {name} budget must be > 0")
        if int(phase["episodes"]) <= 0:
            raise ValueError(f"phase {name} episodes must be > 0")
        phases.append(phase)
    if not phases:
        raise ValueError("no phases selected")
    return phases


def _enumerate_configs(args: argparse.Namespace) -> list[dict[str, Any]]:
    score_modes = _parse_csv_strings(args.score_modes)
    height_slacks = _parse_csv_ints(args.height_slacks)
    micro_depths = _parse_csv_ints(args.micro_depths)
    micro_widths = _parse_csv_ints(args.micro_widths)
    micro_topks = _parse_csv_ints(args.micro_topks)

    configs: list[dict[str, Any]] = []
    for score_mode in score_modes:
        slacks = height_slacks if score_mode == "min_height_slack_then_gain" else [0]
        for height_slack in slacks:
            for micro_depth in micro_depths:
                for micro_width in micro_widths:
                    for micro_topk in micro_topks:
                        configs.append(
                            {
                                "config_id": f"cfg_{len(configs) + 1:04d}",
                                "score_mode": str(score_mode),
                                "height_slack_mm": int(height_slack),
                                "micro_depth": int(micro_depth),
                                "micro_width": int(micro_width),
                                "micro_topk": int(micro_topk),
                            }
                        )
    return configs


def _failed_metrics(error: str) -> dict[str, Any]:
    payload = parse_metrics({})
    payload["error"] = str(error)
    return payload


def _build_run_command(
    *,
    args: argparse.Namespace,
    config: Mapping[str, Any],
    phase: Mapping[str, Any],
    episode_seed: int,
    episode_id: str,
    out_json: Path,
) -> tuple[list[str], int]:
    micro_depth = int(config.get("micro_depth", 0))
    phase_micro_depth = phase.get("micro_depth")
    if phase_micro_depth is not None:
        micro_depth = int(phase_micro_depth)

    budget_ms = int(phase["budget_ms"])
    cmd = [
        str(args.python_exe),
        "-m",
        "sim.run",
        "--excel",
        str(args.excel),
        "--model",
        str(args.model),
        "--policy",
        str(args.policy),
        "--k",
        str(args.k),
        "--arrival-mode",
        str(args.arrival_mode),
        "--score-mode",
        str(config["score_mode"]),
        "--height-slack-mm",
        str(config["height_slack_mm"]),
        "--time-budget-ms",
        str(budget_ms),
        "--micro-depth",
        str(micro_depth),
        "--micro-width",
        str(config["micro_width"]),
        "--micro-topk",
        str(config["micro_topk"]),
        "--heuristic",
        str(args.heuristic),
        "--stability-mode",
        str(args.stability_mode),
        "--min-support",
        str(args.min_support),
        "--overhang_mm",
        str(args.overhang_mm),
        "--n_per_pallet",
        str(args.n_per_pallet),
        "--t_pick_place",
        str(args.t_pick_place),
        "--time_scale",
        str(args.time_scale),
        "--episode-seed",
        str(episode_seed),
        "--shuffle-window",
        str(args.shuffle_window),
        "--shuffle-strength",
        str(args.shuffle_strength),
        "--episode-id",
        str(episode_id),
        "--out",
        str(out_json),
    ]

    if args.force_destination is not None:
        cmd.extend(["--force-destination", str(args.force_destination)])
    if args.continuous_pallets:
        cmd.append("--continuous-pallets")
    if args.micro_plan:
        cmd.append("--micro-plan")

    return cmd, budget_ms


def _evaluate_config_phase(
    *,
    args: argparse.Namespace,
    repo_root: Path,
    json_root: Path,
    config: Mapping[str, Any],
    phase: Mapping[str, Any],
) -> dict[str, Any]:
    jobs = max(1, int(args.jobs))
    phase_name = str(phase["name"])
    episodes = int(phase["episodes"])
    phase_seed_offset = int(phase.get("seed_offset", 0))
    timeout_sec = 2.0 * (float(int(phase["budget_ms"])) / 1000.0 + float(args.timeout_margin_sec))

    py_path = str(repo_root / "src")
    env = {"PYTHONPATH": py_path}

    episode_rows: list[dict[str, Any]] = []
    metrics_per_episode: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {}
        for episode_idx in range(episodes):
            episode_seed = int(args.base_episode_seed) + phase_seed_offset + episode_idx
            episode_id = f"{phase_name}-{config['config_id']}-ep{episode_idx + 1:03d}"
            out_json = json_root / phase_name / str(config["config_id"]) / f"{episode_id}.json"
            out_json.parent.mkdir(parents=True, exist_ok=True)

            command, budget_ms = _build_run_command(
                args=args,
                config=config,
                phase=phase,
                episode_seed=episode_seed,
                episode_id=episode_id,
                out_json=out_json,
            )

            future = pool.submit(
                run_sim_subprocess,
                command,
                json_path=str(out_json),
                timeout_sec=timeout_sec,
                cwd=str(repo_root),
                env=env,
            )
            futures[future] = {
                "episode_seed": episode_seed,
                "episode_id": episode_id,
                "out_json": out_json,
                "budget_ms": budget_ms,
            }

        for future in as_completed(futures):
            meta = futures[future]
            run_result = future.result()
            episode_row = {
                "episode_id": meta["episode_id"],
                "episode_seed": int(meta["episode_seed"]),
                "json_path": str(meta["out_json"]),
                "returncode": int(run_result.returncode),
                "duration_sec": float(run_result.duration_sec),
                "timeout_sec": float(timeout_sec),
            }

            if not run_result.ok:
                error = str(run_result.error or "subprocess_failed")
                episode_row["status"] = "fail"
                episode_row["error"] = error
                metrics_per_episode.append(_failed_metrics(error))
                episode_rows.append(episode_row)
                continue

            if not Path(run_result.json_path).exists():
                error = "missing_output_json"
                episode_row["status"] = "fail"
                episode_row["error"] = error
                metrics_per_episode.append(_failed_metrics(error))
                episode_rows.append(episode_row)
                continue

            try:
                with Path(run_result.json_path).open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except Exception as exc:
                error = f"json_load_error:{exc}"
                episode_row["status"] = "fail"
                episode_row["error"] = error
                metrics_per_episode.append(_failed_metrics(error))
                episode_rows.append(episode_row)
                continue

            metrics = parse_metrics(payload)
            episode_row["status"] = str(metrics.get("status", "fail"))
            episode_row["error"] = metrics.get("error")
            metrics_per_episode.append(metrics)
            episode_rows.append(episode_row)

    merged = merge_metrics(metrics_per_episode)
    merged["episodes"] = int(episodes)

    episode_rows_sorted = sorted(episode_rows, key=lambda row: str(row["episode_id"]))
    seeds = [int(row["episode_seed"]) for row in episode_rows_sorted]

    return {
        "config_id": config["config_id"],
        "phase_budget_ms": int(phase["budget_ms"]),
        "phase_episodes": int(episodes),
        "episode_seeds": seeds,
        "episode_rows": episode_rows_sorted,
        "metrics": merged,
    }


def _flatten_runs(halving_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    history = halving_result.get("history", [])
    if not isinstance(history, list):
        return rows

    for phase_info in history:
        if not isinstance(phase_info, Mapping):
            continue
        phase_name = str(phase_info.get("phase", ""))
        settings = phase_info.get("settings", {})
        keep = int(phase_info.get("keep", 0))
        evaluated = phase_info.get("evaluated", [])
        if not isinstance(evaluated, list):
            continue

        for rank_idx, row in enumerate(evaluated, start=1):
            if not isinstance(row, Mapping):
                continue
            config = row.get("config", {})
            metrics = row.get("metrics", {})
            rank_key = row.get("rank_key")
            if not isinstance(config, Mapping):
                config = {}
            if not isinstance(metrics, Mapping):
                metrics = {}
            rank_key_value = tuple(rank_key) if isinstance(rank_key, (list, tuple)) else (0, 0, 0, 0, 0)

            rows.append(
                {
                    "phase": phase_name,
                    "rank": int(rank_idx),
                    "selected": int(1 if rank_idx <= keep else 0),
                    "config_id": str(config.get("config_id", "")),
                    "score_mode": str(config.get("score_mode", "")),
                    "height_slack_mm": int(config.get("height_slack_mm", 0) or 0),
                    "micro_depth": int(config.get("micro_depth", 0) or 0),
                    "micro_width": int(config.get("micro_width", 0) or 0),
                    "micro_topk": int(config.get("micro_topk", 0) or 0),
                    "time_budget_ms": int(settings.get("budget_ms", 0) if isinstance(settings, Mapping) else 0),
                    "episodes": int(settings.get("episodes", 0) if isinstance(settings, Mapping) else 0),
                    "status": str(metrics.get("status", "fail")),
                    "error": str(metrics.get("error", "") or ""),
                    "pallets": int(metrics.get("pallets", 0) or 0),
                    "p50_boxes_per_pallet": float(metrics.get("p50_boxes_per_pallet", 0.0) or 0.0),
                    "p95_boxes_per_pallet": float(metrics.get("p95_boxes_per_pallet", 0.0) or 0.0),
                    "avg_boxes": float(metrics.get("avg_boxes", 0.0) or 0.0),
                    "max_boxes": int(metrics.get("max_boxes", 0) or 0),
                    "pct_ge_21": float(metrics.get("pct_ge_21", 0.0) or 0.0),
                    "deadlocks_stability": int(metrics.get("deadlocks_stability", 0) or 0),
                    "close_height_full": int(metrics.get("close_height_full", 0) or 0),
                    "closures_total": int(metrics.get("closures_total", 0) or 0),
                    "time_penalty": float(metrics.get("time_penalty", 0.0) or 0.0),
                    "ok_count": int(metrics.get("ok_count", 0) or 0),
                    "fail_count": int(metrics.get("fail_count", 0) or 0),
                    "episode_seeds": json.dumps(row.get("episode_seeds", []), ensure_ascii=True),
                    "rank_key": serialize_rank_key(rank_key_value),
                }
            )
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with path.open("w", newline="", encoding="utf-8") as handle:
            handle.write("")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    repo_root = REPO_ROOT
    run_name = args.run_name or _default_run_name()
    out_dir = Path(args.out_dir) if args.out_dir else (Path("/tmp/palca_tuner") / run_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_root = out_dir / "json"

    phases = _build_phase_list(args)
    configs = _enumerate_configs(args)
    if not configs:
        raise SystemExit("No configs to evaluate.")

    if args.dry_run:
        print(f"run_name={run_name}")
        print(f"out_dir={out_dir}")
        print(f"configs={len(configs)} phases={[phase['name'] for phase in phases]}")
        for config in configs:
            print(json.dumps(config, ensure_ascii=True))
        return

    def evaluator(config: dict[str, Any], phase: dict[str, Any]) -> dict[str, Any]:
        return _evaluate_config_phase(
            args=args,
            repo_root=repo_root,
            json_root=json_root,
            config=config,
            phase=phase,
        )

    halving_result = successive_halving(configs=configs, evaluator=evaluator, phases=phases, eta=int(args.eta))
    run_rows = _flatten_runs(halving_result)

    runs_csv_path = out_dir / "runs.csv"
    _write_csv(runs_csv_path, run_rows)

    finalists = halving_result.get("finalists", [])
    if not isinstance(finalists, list):
        finalists = []
    top_n = max(1, int(args.top_n))
    top_payload = []
    for item in finalists[:top_n]:
        if not isinstance(item, Mapping):
            continue
        top_payload.append(
            {
                "phase": item.get("phase"),
                "config": item.get("config"),
                "rank_key": list(item.get("rank_key", [])),
                "metrics": item.get("metrics"),
                "episode_seeds": item.get("episode_seeds", []),
            }
        )

    top_path = out_dir / "top.json"
    with top_path.open("w", encoding="utf-8") as handle:
        json.dump(top_payload, handle, indent=2, ensure_ascii=True)

    best = halving_result.get("best")
    best_payload: dict[str, Any] = {}
    if isinstance(best, Mapping):
        best_payload = {
            "run_name": run_name,
            "phase": best.get("phase"),
            "best_config": best.get("config"),
            "rank_key": list(best.get("rank_key", [])),
            "metrics": best.get("metrics"),
            "episode_seeds": best.get("episode_seeds", []),
            "fixed_params": {
                "excel": str(args.excel),
                "model": str(args.model),
                "policy": str(args.policy),
                "k": int(args.k),
                "arrival_mode": str(args.arrival_mode),
                "force_destination": args.force_destination,
                "continuous_pallets": bool(args.continuous_pallets),
                "heuristic": str(args.heuristic),
                "stability_mode": str(args.stability_mode),
                "min_support": float(args.min_support),
                "overhang_mm": int(args.overhang_mm),
                "n_per_pallet": int(args.n_per_pallet),
                "t_pick_place": float(args.t_pick_place),
                "time_scale": float(args.time_scale),
                "micro_plan": bool(args.micro_plan),
                "shuffle_window": int(args.shuffle_window),
                "shuffle_strength": float(args.shuffle_strength),
                "base_episode_seed": int(args.base_episode_seed),
            },
        }

    best_path = out_dir / "best_profile.json"
    with best_path.open("w", encoding="utf-8") as handle:
        json.dump(best_payload, handle, indent=2, ensure_ascii=True)

    print(f"run_name={run_name}")
    print(f"out_dir={out_dir}")
    print(f"runs_csv={runs_csv_path}")
    print(f"top_json={top_path}")
    print(f"best_profile={best_path}")


if __name__ == "__main__":
    main()
