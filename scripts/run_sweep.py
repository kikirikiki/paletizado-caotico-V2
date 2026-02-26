#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any, Mapping

THREAD_ENV_KEYS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def default_jobs() -> int:
    return max(1, min(12, os.cpu_count() or 1))


def parse_seeds(*, seeds_csv: str | None, seed_start: int | None, seed_count: int | None) -> list[int]:
    has_csv = bool(seeds_csv and str(seeds_csv).strip())
    has_range = seed_start is not None or seed_count is not None

    if has_csv and has_range:
        raise ValueError("Use either --seeds or (--seed-start and --seed-count), not both.")

    if has_csv:
        seeds: list[int] = []
        for token in str(seeds_csv).split(","):
            raw = token.strip()
            if not raw:
                continue
            seeds.append(int(raw))
        if not seeds:
            raise ValueError("--seeds must contain at least one integer seed.")
        return seeds

    if has_range:
        if seed_start is None or seed_count is None:
            raise ValueError("--seed-start and --seed-count must be provided together.")
        if int(seed_count) <= 0:
            raise ValueError("--seed-count must be > 0.")
        start = int(seed_start)
        count = int(seed_count)
        return [start + offset for offset in range(count)]

    raise ValueError("Provide --seeds or (--seed-start and --seed-count).")


def _sanitize_prefix(prefix: str) -> str:
    safe = "".join(char if (char.isalnum() or char in ("-", "_")) else "_" for char in str(prefix))
    safe = safe.strip("_")
    return safe or "sweep"


def make_out_path(
    *,
    base_out_dir: str | Path,
    out_prefix: str,
    seed: int,
    run_args: str,
) -> Path:
    raw = f"{int(seed)}\n{run_args}"
    short_hash = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    filename = f"{_sanitize_prefix(out_prefix)}_seed{int(seed)}_{short_hash}.json"
    return Path(base_out_dir) / filename


def build_sim_run_cmd(
    *,
    python_exe: str | Path,
    run_args: str,
    seed: int,
    out_json: str | Path,
    seed_flag: str = "--episode-seed",
) -> list[str]:
    extra_args = shlex.split(str(run_args)) if str(run_args).strip() else []

    reserved_flags = {"--out", "--seed", "--episode-seed"}
    for token in extra_args:
        if token in reserved_flags:
            raise ValueError(
                f"--run-args must not include {token}; run_sweep manages seed/out flags for reproducibility."
            )

    cmd = [str(python_exe), "-m", "sim.run"]
    cmd.extend(extra_args)
    cmd.extend([str(seed_flag), str(int(seed)), "--out", str(out_json)])
    return cmd


def _lookup_dest(data: Any, destination: int, default: Any) -> Any:
    if not isinstance(data, Mapping):
        return default
    if destination in data:
        return data[destination]
    as_str = str(destination)
    if as_str in data:
        return data[as_str]
    return default


def _to_float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _to_int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _extract_stats(payload: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "pallets": None,
        "avg": None,
        "max": None,
        "closures_top1": None,
    }

    params = payload.get("params", {})
    destination = 1
    if isinstance(params, Mapping):
        maybe_destination = _to_int_or_none(params.get("force_destination"))
        if maybe_destination is not None and maybe_destination > 0:
            destination = maybe_destination

    metrics = payload.get("metrics", {})
    pallet_kpis = metrics.get("pallet_kpis", {}) if isinstance(metrics, Mapping) else {}
    if not isinstance(pallet_kpis, Mapping):
        return result

    sequence = _lookup_dest(pallet_kpis.get("continuous_pallet_sequence", {}), destination, [])
    total_by_dest = pallet_kpis.get("continuous_pallets_total", {})

    boxes_values: list[int] = []
    if isinstance(sequence, list):
        for item in sequence:
            converted = _to_int_or_none(item)
            if converted is not None and converted >= 0:
                boxes_values.append(converted)

    if boxes_values:
        result["pallets"] = _to_int_or_none(_lookup_dest(total_by_dest, destination, len(boxes_values)))
        if result["pallets"] is None:
            result["pallets"] = len(boxes_values)
        result["avg"] = float(sum(boxes_values)) / float(len(boxes_values))
        result["max"] = int(max(boxes_values))
    else:
        stats_candidates = [
            _lookup_dest(pallet_kpis.get("continuous_pallet_sequence_stats", {}), destination, None),
            _lookup_dest(pallet_kpis.get("boxes_per_pallet_stats", {}), destination, None),
            pallet_kpis.get("continuous_pallet_sequence_stats"),
            pallet_kpis.get("boxes_per_pallet_stats"),
        ]
        for stats in stats_candidates:
            if not isinstance(stats, Mapping):
                continue
            pallets = _to_int_or_none(stats.get("pallets", stats.get("count", stats.get("n"))))
            avg = _to_float_or_none(stats.get("avg_boxes", stats.get("avg", stats.get("mean"))))
            max_boxes = _to_int_or_none(stats.get("max_boxes", stats.get("max", stats.get("maximum"))))
            if pallets is not None:
                result["pallets"] = pallets
            if avg is not None:
                result["avg"] = avg
            if max_boxes is not None:
                result["max"] = max_boxes
            if any(value is not None for value in (pallets, avg, max_boxes)):
                break

    closures = _lookup_dest(pallet_kpis.get("continuous_closures_by_reason", {}), destination, None)
    if not isinstance(closures, Mapping):
        closures = pallet_kpis.get("closures_by_reason")
    if isinstance(closures, Mapping) and closures:
        ranked: list[tuple[str, int]] = []
        for key, value in closures.items():
            count = _to_int_or_none(value)
            if count is None:
                continue
            ranked.append((str(key), count))
        if ranked:
            top_reason, top_count = sorted(ranked, key=lambda item: (-item[1], item[0]))[0]
            result["closures_top1"] = f"{top_reason}:{int(top_count)}"

    return result


def _extract_stats_from_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"pallets": None, "avg": None, "max": None, "closures_top1": None}
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {"pallets": None, "avg": None, "max": None, "closures_top1": None}
    if not isinstance(payload, Mapping):
        return {"pallets": None, "avg": None, "max": None, "closures_top1": None}
    return _extract_stats(payload)


@dataclass(frozen=True)
class SweepTask:
    seed: int
    command: list[str]
    out_json: str
    repo_root: str


@dataclass(frozen=True)
class SweepResult:
    seed: int
    command: list[str]
    out_json: str
    returncode: int
    wall_ms: int
    stdout: str
    stderr: str
    pallets: int | None
    avg: float | None
    max_boxes: int | None
    closures_top1: str | None
    error: str | None = None


def _build_child_env(*, repo_root: str) -> dict[str, str]:
    child_env = os.environ.copy()
    child_env.setdefault("PYTHONPATH", "src")
    for key in THREAD_ENV_KEYS:
        child_env.setdefault(key, "1")
    return child_env


def _run_one(task: SweepTask) -> SweepResult:
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            task.command,
            capture_output=True,
            text=True,
            check=False,
            cwd=task.repo_root,
            env=_build_child_env(repo_root=task.repo_root),
        )
        returncode = int(proc.returncode)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        error = None if returncode == 0 else f"returncode_{returncode}"
    except Exception as exc:
        returncode = 1
        stdout = ""
        stderr = ""
        error = str(exc)

    wall_ms = int((time.perf_counter() - start) * 1000.0)
    stats = _extract_stats_from_json(Path(task.out_json))
    return SweepResult(
        seed=int(task.seed),
        command=list(task.command),
        out_json=str(task.out_json),
        returncode=returncode,
        wall_ms=wall_ms,
        stdout=stdout,
        stderr=stderr,
        pallets=_to_int_or_none(stats.get("pallets")),
        avg=_to_float_or_none(stats.get("avg")),
        max_boxes=_to_int_or_none(stats.get("max")),
        closures_top1=(str(stats["closures_top1"]) if stats.get("closures_top1") is not None else None),
        error=error,
    )


def _clip_text(raw: str, *, max_lines: int = 30) -> str:
    lines = [line for line in str(raw).splitlines() if line.strip()]
    if len(lines) <= max_lines:
        return "\n".join(lines)
    tail = lines[-max_lines:]
    return "\n".join(["..."] + tail)


def _format_cell(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _print_summary(results: list[SweepResult]) -> None:
    headers = ["seed", "returncode", "wall_ms", "pallets", "avg", "max", "closures (top1)"]
    rows = []
    for item in results:
        rows.append(
            [
                _format_cell(item.seed),
                _format_cell(item.returncode),
                _format_cell(item.wall_ms),
                _format_cell(item.pallets),
                _format_cell(item.avg),
                _format_cell(item.max_boxes),
                _format_cell(item.closures_top1),
            ]
        )

    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    def _line(parts: list[str]) -> str:
        return " | ".join(text.ljust(widths[idx]) for idx, text in enumerate(parts))

    print(_line(headers))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(_line(row))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parallel sweep launcher for independent sim.run seeds")
    parser.add_argument("--jobs", type=int, default=default_jobs(), help="Parallel subprocesses (default: min(12, cpu_count))")
    parser.add_argument(
        "--seeds",
        type=str,
        default=None,
        help="CSV integer seeds, e.g. 314,315,316 (required if --seed-start/--seed-count are omitted).",
    )
    parser.add_argument("--seed-start", type=int, default=None, help="Start seed for range mode.")
    parser.add_argument("--seed-count", type=int, default=None, help="Seed count for range mode.")
    parser.add_argument("--base-out-dir", type=str, default="/tmp/palca-sweeps", help="Directory for output JSON files.")
    parser.add_argument("--out-prefix", type=str, default="sweep", help="Output filename prefix.")
    parser.add_argument("--run-args", type=str, default="", help="Raw args string passed to sim.run.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands/paths without executing subprocesses.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        seeds = parse_seeds(seeds_csv=args.seeds, seed_start=args.seed_start, seed_count=args.seed_count)
    except ValueError as exc:
        parser.error(str(exc))

    seen: set[int] = set()
    duplicates = [seed for seed in seeds if seed in seen or seen.add(seed)]
    if duplicates:
        parser.error(f"Duplicate seeds are not supported: {duplicates}")

    jobs = max(1, int(args.jobs))
    run_args = str(args.run_args)

    repo_root = Path(__file__).resolve().parents[1]
    python_exe = repo_root / ".venv" / "bin" / "python"
    if not python_exe.exists():
        parser.error(f"Python executable not found: {python_exe}")

    base_out_dir = Path(args.base_out_dir)
    base_out_dir.mkdir(parents=True, exist_ok=True)

    tasks: list[SweepTask] = []
    for seed in seeds:
        out_path = make_out_path(
            base_out_dir=base_out_dir,
            out_prefix=str(args.out_prefix),
            seed=seed,
            run_args=run_args,
        )
        cmd = build_sim_run_cmd(
            python_exe=python_exe,
            run_args=run_args,
            seed=seed,
            out_json=out_path,
        )
        tasks.append(
            SweepTask(
                seed=seed,
                command=cmd,
                out_json=str(out_path),
                repo_root=str(repo_root),
            )
        )

    if args.dry_run:
        print(f"dry-run: jobs={jobs} seeds={len(tasks)} base_out_dir={base_out_dir}")
        for task in tasks:
            print(f"seed={task.seed} out={task.out_json}")
            print(f"cmd: {shlex.join(task.command)}")
        return 0

    print(f"running sweep: jobs={jobs} seeds={len(tasks)} base_out_dir={base_out_dir}")

    results_by_seed: dict[int, SweepResult] = {}
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        future_map = {pool.submit(_run_one, task): task.seed for task in tasks}
        for future in as_completed(future_map):
            seed = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                result = SweepResult(
                    seed=seed,
                    command=[],
                    out_json="",
                    returncode=1,
                    wall_ms=0,
                    stdout="",
                    stderr="",
                    pallets=None,
                    avg=None,
                    max_boxes=None,
                    closures_top1=None,
                    error=str(exc),
                )
            results_by_seed[seed] = result
            status = "OK" if result.returncode == 0 else "FAIL"
            print(f"[{status}] seed={seed} rc={result.returncode} wall_ms={result.wall_ms} out={result.out_json}")

    ordered = [results_by_seed[seed] for seed in seeds]

    failed = [item for item in ordered if item.returncode != 0]
    if failed:
        print("\nfailed run details:")
        for item in failed:
            print(f"\nseed={item.seed} rc={item.returncode} error={item.error} out={item.out_json}")
            if item.command:
                print(f"cmd: {shlex.join(item.command)}")
            if item.stdout.strip():
                print("stdout:")
                print(_clip_text(item.stdout))
            if item.stderr.strip():
                print("stderr:")
                print(_clip_text(item.stderr))

    print("\nsummary:")
    _print_summary(ordered)

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
