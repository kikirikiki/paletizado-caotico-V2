#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import hashlib
import json
import os
import shlex
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any, Callable, Iterable


REQUIRED_SPATIAL_FLAGS = (
    "--dump-placements",
    "--spatial-xy-bin-mm",
    "--spatial-tower-penalty-weight",
    "--spatial-tower-penalty-end-step",
    "--spatial-tower-target-base",
    "--spatial-tower-target-step-div",
)

RUNS_CSV_FIELDS = [
    "status",
    "error_msg",
    "config_id",
    "rep_id",
    "bin_mm",
    "w",
    "end_step",
    "base",
    "div",
    "processed_boxes",
    "reached_step",
    "top3_pct",
    "max_boxes",
    "gini",
    "spatial_applied",
    "spatial_selected",
    "spatial_selected_mean",
    "run_dir",
]


@dataclass(frozen=True, slots=True)
class SweepConfig:
    bin_mm: int
    weight: float
    end_step: int
    target_base: int
    target_div: int
    config_id: str


def parse_csv_list(raw: str, cast: Callable[[str], Any]) -> list[Any]:
    out: list[Any] = []
    for piece in str(raw).split(","):
        token = piece.strip()
        if not token:
            continue
        out.append(cast(token))
    return out


def percentile(values: Iterable[float], p: float) -> float | None:
    xs = sorted(float(v) for v in values)
    if not xs:
        return None
    if p <= 0:
        return xs[0]
    if p >= 100:
        return xs[-1]
    k = (len(xs) - 1) * (p / 100.0)
    f = int(k)
    c = min(len(xs) - 1, f + 1)
    if f == c:
        return xs[f]
    d0 = xs[f] * (c - k)
    d1 = xs[c] * (k - f)
    return d0 + d1


def make_config_id(*, bin_mm: int, weight: float, end_step: int, target_base: int, target_div: int) -> str:
    packed = f"bin={bin_mm}|w={weight:.8g}|end={end_step}|base={target_base}|div={target_div}"
    digest = hashlib.sha1(packed.encode("utf-8")).hexdigest()[:8]
    w_txt = f"{weight:.8g}".replace("-", "m").replace(".", "p")
    return f"xy{bin_mm}_w{w_txt}_e{end_step}_b{target_base}_d{target_div}_{digest}"


def rank_key(row: dict[str, Any]) -> tuple[int, float, float, float]:
    status = str(row.get("status", ""))
    processed = row.get("processed_boxes")
    reached = row.get("reached_step")
    top3 = row.get("top3_pct")
    if status != "ok" or processed is None or reached is None or top3 is None:
        return (0, float("-inf"), float("-inf"), float("-inf"))
    return (1, float(processed), float(reached), -float(top3))


def _safe_load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def extract_metrics_from_files(out_json: Path, analysis_json: Path) -> dict[str, Any]:
    out_payload = _safe_load_json(out_json) or {}
    analysis_payload = _safe_load_json(analysis_json) or {}

    metrics = out_payload.get("metrics") if isinstance(out_payload.get("metrics"), dict) else {}
    pallet_kpis = metrics.get("pallet_kpis") if isinstance(metrics.get("pallet_kpis"), dict) else {}

    pallets = analysis_payload.get("pallets") if isinstance(analysis_payload.get("pallets"), dict) else {}
    pallet_data: dict[str, Any] = {}
    if "1" in pallets and isinstance(pallets["1"], dict):
        pallet_data = pallets["1"]
    else:
        for _, value in pallets.items():
            if isinstance(value, dict):
                pallet_data = value
                break

    def pick_spatial(name: str) -> Any:
        if name in metrics:
            return metrics.get(name)
        if name in pallet_kpis:
            return pallet_kpis.get(name)
        return None

    return {
        "processed_boxes": metrics.get("processed_boxes"),
        "spatial_applied": pick_spatial("spatial_tower_penalty_applied_count"),
        "spatial_selected": pick_spatial("spatial_tower_selected_penalty_count"),
        "spatial_selected_mean": pick_spatial("spatial_tower_selected_penalty_mean"),
        "max_boxes": pallet_data.get("xy_bins_max_boxes") if pallet_data else None,
        "reached_step": pallet_data.get("xy_bins_max_boxes_reached_step") if pallet_data else None,
        "top3_pct": pallet_data.get("xy_bins_top3_boxes_percent") if pallet_data else None,
        "gini": pallet_data.get("xy_bins_gini") if pallet_data else None,
    }


def _add_pythonpath(env: dict[str, str], repo_root: Path) -> dict[str, str]:
    out = dict(env)
    src = str(repo_root / "src")
    current = out.get("PYTHONPATH", "")
    out["PYTHONPATH"] = src if not current else f"{src}{os.pathsep}{current}"
    return out


def detect_spatial_flags(python_exec: str, repo_root: Path) -> tuple[bool, list[str], str]:
    env = _add_pythonpath(os.environ, repo_root)
    cmd = [python_exec, "-m", "sim.run", "--help"]
    help_text = ""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_root), env=env)
        help_text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except Exception as exc:
        help_text = f"ERROR running sim.run --help: {exc}"

    missing = [flag for flag in REQUIRED_SPATIAL_FLAGS if flag not in help_text]
    return (len(missing) == 0, missing, help_text)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Sweep 2-fases para spatial penalty anti-torre temprana")

    ap.add_argument("--excel", default="data/Flujo rampas - Editado.xlsx")
    ap.add_argument("--out-root", default=None)
    ap.add_argument("--phase", choices=["explore", "confirm"], default="explore")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--topk", type=int, default=15)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--fail-fast", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-artifacts", action=argparse.BooleanOptionalAction, default=True)

    ap.add_argument("--xy-bin-mm-list", default="150,100")
    ap.add_argument("--weight-list", default="0.2,0.4,0.8,1.2")
    ap.add_argument("--end-step-list", default="10,12,14,16,18")
    ap.add_argument("--target-base-list", default="1,2")
    ap.add_argument("--target-div-list", default="4,6,8,10")

    ap.add_argument("--k", type=int, default=15)
    ap.add_argument("--time-budget-ms", type=int, default=1100)
    ap.add_argument("--micro-depth", type=int, default=4)
    ap.add_argument("--micro-width", type=int, default=36)
    ap.add_argument("--micro-topk", type=int, default=15)
    ap.add_argument("--height-slack-mm", type=int, default=20)
    ap.add_argument("--stand-hw-gate-mm", type=int, default=200)
    ap.add_argument("--coverage-grid-x", type=int, default=3)
    ap.add_argument("--coverage-grid-y", type=int, default=2)
    ap.add_argument("--coverage-weight", type=float, default=4.0)
    ap.add_argument("--arrival-mode", default="immediate")
    ap.add_argument("--ramp-cap", type=int, default=15)
    ap.add_argument("--dest", type=int, default=1)
    ap.add_argument("--max-pallets", type=int, default=1)
    ap.add_argument("--stacking-mode", default="heightfield")
    ap.add_argument("--z-band-mm", type=int, default=0)
    ap.add_argument("--overhang-mm", type=int, default=20)
    ap.add_argument("--heuristic", default="bssf")
    ap.add_argument("--min-support", type=float, default=0.85)
    ap.add_argument("--stability-mode", default="ratio+corners+settle")
    ap.add_argument("--score-mode", default="min_height_slack_then_gain")

    return ap


def make_out_root(raw: str | None) -> Path:
    if raw:
        return Path(raw)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(f"/tmp/palca_spatial_sweep_{ts}")


def enumerate_configs(args: argparse.Namespace) -> list[SweepConfig]:
    bins = parse_csv_list(args.xy_bin_mm_list, int)
    weights = parse_csv_list(args.weight_list, float)
    end_steps = parse_csv_list(args.end_step_list, int)
    bases = parse_csv_list(args.target_base_list, int)
    divisors = parse_csv_list(args.target_div_list, int)
    configs: list[SweepConfig] = []
    for bin_mm, weight, end_step, base, div in product(bins, weights, end_steps, bases, divisors):
        configs.append(
            SweepConfig(
                bin_mm=int(bin_mm),
                weight=float(weight),
                end_step=int(end_step),
                target_base=int(base),
                target_div=int(div),
                config_id=make_config_id(
                    bin_mm=int(bin_mm),
                    weight=float(weight),
                    end_step=int(end_step),
                    target_base=int(base),
                    target_div=int(div),
                ),
            )
        )
    return configs


def _load_confirm_configs(out_root: Path) -> list[SweepConfig]:
    path = out_root / "top_configs.json"
    payload = _safe_load_json(path)
    if payload is None:
        raise SystemExit(f"Missing or invalid top_configs.json: {path}")
    raw_configs = payload.get("top_configs") if isinstance(payload.get("top_configs"), list) else payload
    if not isinstance(raw_configs, list):
        raise SystemExit(f"top_configs.json must contain a list or top_configs list: {path}")

    out: list[SweepConfig] = []
    for item in raw_configs:
        if not isinstance(item, dict):
            continue
        params = item.get("params") if isinstance(item.get("params"), dict) else item
        try:
            bin_mm = int(params["bin_mm"])
            weight = float(params["w"])
            end_step = int(params["end_step"])
            base = int(params["base"])
            div = int(params["div"])
        except Exception:
            continue
        out.append(
            SweepConfig(
                bin_mm=bin_mm,
                weight=weight,
                end_step=end_step,
                target_base=base,
                target_div=div,
                config_id=str(item.get("config_id") or make_config_id(
                    bin_mm=bin_mm,
                    weight=weight,
                    end_step=end_step,
                    target_base=base,
                    target_div=div,
                )),
            )
        )
    if not out:
        raise SystemExit(f"No valid configs found in {path}")
    return out


def build_sim_cmd(args: argparse.Namespace, cfg: SweepConfig, run_dir: Path, python_exec: str) -> list[str]:
    return [
        python_exec,
        "-m",
        "sim.run",
        "--excel",
        str(args.excel),
        "--policy",
        "palca",
        "--k",
        str(args.k),
        "--arrival-mode",
        str(args.arrival_mode),
        "--ramp_cap",
        str(args.ramp_cap),
        "--stacking-mode",
        str(args.stacking_mode),
        "--z-band-mm",
        str(args.z_band_mm),
        "--overhang_mm",
        str(args.overhang_mm),
        "--heuristic",
        str(args.heuristic),
        "--stability-mode",
        str(args.stability_mode),
        "--min-support",
        str(args.min_support),
        "--score-mode",
        str(args.score_mode),
        "--height-slack-mm",
        str(args.height_slack_mm),
        "--micro-plan",
        "--micro-depth",
        str(args.micro_depth),
        "--micro-width",
        str(args.micro_width),
        "--micro-topk",
        str(args.micro_topk),
        "--time-budget-ms",
        str(args.time_budget_ms),
        "--orientation-mode",
        "planar+stand_hw",
        "--stand-hw-height-margin-gate-mm",
        str(args.stand_hw_gate_mm),
        "--coverage-grid-x",
        str(args.coverage_grid_x),
        "--coverage-grid-y",
        str(args.coverage_grid_y),
        "--coverage-weight",
        str(args.coverage_weight),
        "--max-pallets",
        str(args.max_pallets),
        "--force-destination",
        str(args.dest),
        "--spatial-xy-bin-mm",
        str(cfg.bin_mm),
        "--spatial-tower-penalty-weight",
        str(cfg.weight),
        "--spatial-tower-penalty-end-step",
        str(cfg.end_step),
        "--spatial-tower-target-base",
        str(cfg.target_base),
        "--spatial-tower-target-step-div",
        str(cfg.target_div),
        "--dump-placements",
        str(run_dir / "placements.json"),
        "--out",
        str(run_dir / "out.json"),
    ]


def build_analyze_cmd(cfg: SweepConfig, run_dir: Path, python_exec: str, repo_root: Path) -> list[str]:
    return [
        python_exec,
        str(repo_root / "scripts" / "analyze_placements.py"),
        str(run_dir / "placements.json"),
        "--xy-bin-mm",
        str(cfg.bin_mm),
        "--out",
        str(run_dir / "analysis.json"),
    ]


def _render_cmd(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def _new_row(cfg: SweepConfig, rep_id: int, run_dir: Path) -> dict[str, Any]:
    return {
        "status": "error",
        "error_msg": "",
        "config_id": cfg.config_id,
        "rep_id": rep_id,
        "bin_mm": cfg.bin_mm,
        "w": cfg.weight,
        "end_step": cfg.end_step,
        "base": cfg.target_base,
        "div": cfg.target_div,
        "processed_boxes": None,
        "reached_step": None,
        "top3_pct": None,
        "max_boxes": None,
        "gini": None,
        "spatial_applied": None,
        "spatial_selected": None,
        "spatial_selected_mean": None,
        "run_dir": str(run_dir),
    }


def _stderr_tail(stderr: str, max_lines: int = 30) -> str:
    lines = stderr.splitlines()
    return "\n".join(lines[-max_lines:]) if lines else ""


def _format_error(returncode: int | str, stderr: str) -> str:
    msg = f"returncode={returncode}; stderr_tail={_stderr_tail(stderr)}"
    if len(msg) > 500:
        return msg[-500:]
    return msg


def _write_process_log(log_path: Path, cmd: list[str], proc: subprocess.CompletedProcess[str]) -> None:
    with log_path.open("w", encoding="utf-8") as f:
        f.write(_render_cmd(cmd) + "\n")
        f.write("\n[stdout]\n")
        f.write(proc.stdout or "")
        f.write("\n[stderr]\n")
        f.write(proc.stderr or "")


def _normalize_loaded_row(raw: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in RUNS_CSV_FIELDS:
        value = raw.get(key, "")
        out[key] = value if value != "" else None

    out["status"] = raw.get("status", "")
    out["error_msg"] = raw.get("error_msg", "")

    int_fields = ("rep_id", "bin_mm", "end_step", "base", "div")
    for key in int_fields:
        try:
            out[key] = int(str(raw.get(key, "")).strip())
        except Exception:
            out[key] = None

    float_fields = (
        "w",
        "processed_boxes",
        "reached_step",
        "top3_pct",
        "max_boxes",
        "gini",
        "spatial_applied",
        "spatial_selected",
        "spatial_selected_mean",
    )
    for key in float_fields:
        txt = str(raw.get(key, "")).strip()
        if txt == "":
            out[key] = None
            continue
        try:
            out[key] = float(txt)
        except Exception:
            out[key] = None
    return out


def load_existing_runs(path: Path) -> tuple[list[dict[str, Any]], set[tuple[str, int]]]:
    rows: list[dict[str, Any]] = []
    done_ok: set[tuple[str, int]] = set()
    if not path.exists():
        return rows, done_ok

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            row = _normalize_loaded_row(raw)
            rows.append(row)
            if row.get("status") == "ok":
                cfg_id = str(row.get("config_id") or "")
                rep_id = row.get("rep_id")
                if cfg_id and isinstance(rep_id, int):
                    done_ok.add((cfg_id, rep_id))
    return rows, done_ok


def run_single(
    *,
    args: argparse.Namespace,
    cfg: SweepConfig,
    rep_id: int,
    out_root: Path,
    python_exec: str,
    env: dict[str, str],
    repo_root: Path,
    dry_run: bool,
) -> dict[str, Any]:
    run_dir = out_root / "runs" / cfg.config_id / f"rep_{rep_id}"
    row = _new_row(cfg, rep_id, run_dir)

    sim_cmd = build_sim_cmd(args, cfg, run_dir, python_exec)
    analyze_cmd = build_analyze_cmd(cfg, run_dir, python_exec, repo_root)

    if dry_run:
        print(f"[DRY-RUN] config={cfg.config_id} rep={rep_id}")
        print(_render_cmd(sim_cmd))
        print(_render_cmd(analyze_cmd))
        return row

    run_dir.mkdir(parents=True, exist_ok=True)
    sim_log = run_dir / "sim.log"
    analyze_log = run_dir / "analyze.log"

    try:
        sim_proc = subprocess.run(
            sim_cmd,
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except Exception as exc:
        row["error_msg"] = _format_error("exception", str(exc))
        return row
    _write_process_log(sim_log, sim_cmd, sim_proc)
    if sim_proc.returncode != 0:
        row["error_msg"] = _format_error(sim_proc.returncode, sim_proc.stderr or "")
        return row

    try:
        analyze_proc = subprocess.run(
            analyze_cmd,
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except Exception as exc:
        row["error_msg"] = _format_error("exception", str(exc))
        return row
    _write_process_log(analyze_log, analyze_cmd, analyze_proc)
    if analyze_proc.returncode != 0:
        row["error_msg"] = _format_error(analyze_proc.returncode, analyze_proc.stderr or "")
        return row

    out_json = run_dir / "out.json"
    analysis_json = run_dir / "analysis.json"
    if not out_json.exists() or not analysis_json.exists():
        row["error_msg"] = "returncode=0; stderr_tail=missing out.json or analysis.json"
        return row

    try:
        m = extract_metrics_from_files(out_json, analysis_json)
    except Exception as exc:
        row["error_msg"] = _format_error("exception", str(exc))
        return row

    row.update(m)
    row["status"] = "ok"
    row["error_msg"] = ""

    if not args.keep_artifacts:
        for path in (out_json, analysis_json, run_dir / "placements.json"):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    return row


def _run_single_worker(payload: dict[str, Any]) -> dict[str, Any]:
    return run_single(**payload)


def _write_error_row_for_exception(cfg: SweepConfig, rep_id: int, out_root: Path, exc: Exception) -> dict[str, Any]:
    run_dir = out_root / "runs" / cfg.config_id / f"rep_{rep_id}"
    row = _new_row(cfg, rep_id, run_dir)
    row["error_msg"] = _format_error("exception", str(exc))
    return row


def _stream_runs(
    *,
    tasks: list[tuple[SweepConfig, int]],
    args: argparse.Namespace,
    out_root: Path,
    python_exec: str,
    env: dict[str, str],
    repo_root: Path,
    dry_run: bool,
    on_emit: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    emitted: list[dict[str, Any]] = []
    fail_fast_triggered = False
    jobs = max(1, int(args.jobs))

    if jobs == 1:
        for cfg, rep_id in tasks:
            row = run_single(
                args=args,
                cfg=cfg,
                rep_id=rep_id,
                out_root=out_root,
                python_exec=python_exec,
                env=env,
                repo_root=repo_root,
                dry_run=dry_run,
            )
            emitted.append(row)
            if on_emit is not None:
                on_emit(row)
            if args.fail_fast and row.get("status") != "ok":
                fail_fast_triggered = True
                break
        return emitted, fail_fast_triggered

    executor = ProcessPoolExecutor(max_workers=jobs)
    futures = {}
    try:
        for cfg, rep_id in tasks:
            payload = {
                "args": args,
                "cfg": cfg,
                "rep_id": rep_id,
                "out_root": out_root,
                "python_exec": python_exec,
                "env": env,
                "repo_root": repo_root,
                "dry_run": dry_run,
            }
            futures[executor.submit(_run_single_worker, payload)] = (cfg, rep_id)

        for future in as_completed(futures):
            cfg, rep_id = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = _write_error_row_for_exception(cfg, rep_id, out_root, exc)
            emitted.append(row)
            if on_emit is not None:
                on_emit(row)
            if args.fail_fast and row.get("status") != "ok":
                fail_fast_triggered = True
                for pending in futures:
                    if pending is not future:
                        pending.cancel()
                break
    finally:
        executor.shutdown(wait=not fail_fast_triggered, cancel_futures=fail_fast_triggered)

    return emitted, fail_fast_triggered


def _append_runs_csv(path: Path, rows: list[dict[str, Any]], append: bool) -> None:
    mode = "a" if append else "w"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not append or not path.exists() or path.stat().st_size == 0
    with path.open(mode, encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RUNS_CSV_FIELDS)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)
            f.flush()


def _open_runs_csv(path: Path, append: bool) -> tuple[Any, csv.DictWriter]:
    mode = "a" if append else "w"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not append or not path.exists() or path.stat().st_size == 0
    handle = path.open(mode, encoding="utf-8", newline="")
    writer = csv.DictWriter(handle, fieldnames=RUNS_CSV_FIELDS)
    if write_header:
        writer.writeheader()
        handle.flush()
    return handle, writer


def write_runs_csv(rows: list[dict[str, Any]], out_root: Path) -> None:
    _append_runs_csv(out_root / "runs.csv", rows, append=False)


def write_top_configs(rows: list[dict[str, Any]], configs: list[SweepConfig], out_root: Path, topk: int) -> list[dict[str, Any]]:
    by_cfg: dict[str, dict[str, Any]] = {}
    for row in rows:
        cur = by_cfg.get(str(row["config_id"]))
        if cur is None or rank_key(row) > rank_key(cur):
            by_cfg[str(row["config_id"])] = row

    cfg_map = {cfg.config_id: cfg for cfg in configs}
    ranked = sorted(by_cfg.values(), key=rank_key, reverse=True)
    top_rows = ranked[: max(0, int(topk))]

    top_configs: list[dict[str, Any]] = []
    for row in top_rows:
        cfg = cfg_map.get(str(row["config_id"]))
        if cfg is None:
            continue
        top_configs.append(
            {
                "config_id": cfg.config_id,
                "params": {
                    "bin_mm": cfg.bin_mm,
                    "w": cfg.weight,
                    "end_step": cfg.end_step,
                    "base": cfg.target_base,
                    "div": cfg.target_div,
                },
                "best_run": {
                    "status": row.get("status"),
                    "processed_boxes": row.get("processed_boxes"),
                    "reached_step": row.get("reached_step"),
                    "top3_pct": row.get("top3_pct"),
                    "run_dir": row.get("run_dir"),
                },
            }
        )

    payload = {
        "objective": "(processed_boxes, reached_step, -top3_pct)",
        "top_configs": top_configs,
    }
    path = out_root / "top_configs.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    print("Top configs (explore):")
    for i, item in enumerate(top_configs, start=1):
        best = item["best_run"]
        print(
            f"{i:>2}. {item['config_id']} "
            f"processed={best.get('processed_boxes')} "
            f"reached={best.get('reached_step')} "
            f"top3={best.get('top3_pct')} "
            f"status={best.get('status')}"
        )

    return top_configs


def write_confirm_configs(rows: list[dict[str, Any]], out_root: Path, topk: int) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["config_id"]), []).append(row)

    rows_out: list[dict[str, Any]] = []
    for cfg_id, items in grouped.items():
        ok = [r for r in items if r.get("status") == "ok"]
        processed = [float(r["processed_boxes"]) for r in ok if r.get("processed_boxes") is not None]
        reached = [float(r["reached_step"]) for r in ok if r.get("reached_step") is not None]
        top3 = [float(r["top3_pct"]) for r in ok if r.get("top3_pct") is not None]

        row = {
            "config_id": cfg_id,
            "processed_boxes_median": statistics.median(processed) if processed else None,
            "processed_boxes_p10": percentile(processed, 10),
            "processed_boxes_p90": percentile(processed, 90),
            "reached_step_median": statistics.median(reached) if reached else None,
            "reached_step_p10": percentile(reached, 10),
            "reached_step_p90": percentile(reached, 90),
            "top3_pct_mean": (sum(top3) / len(top3)) if top3 else None,
        }
        rows_out.append(row)

    def cfg_rank_key(row: dict[str, Any]) -> tuple[int, float, float, float]:
        if (
            row.get("processed_boxes_median") is None
            or row.get("reached_step_median") is None
            or row.get("top3_pct_mean") is None
        ):
            return (0, float("-inf"), float("-inf"), float("-inf"))
        return (
            1,
            float(row["processed_boxes_median"]),
            float(row["reached_step_median"]),
            -float(row["top3_pct_mean"]),
        )

    rows_out = sorted(rows_out, key=cfg_rank_key, reverse=True)

    path = out_root / "configs.csv"
    fields = [
        "config_id",
        "processed_boxes_median",
        "processed_boxes_p10",
        "processed_boxes_p90",
        "reached_step_median",
        "reached_step_p10",
        "reached_step_p90",
        "top3_pct_mean",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows_out:
            writer.writerow(row)

    print("Top configs (confirm):")
    for i, row in enumerate(rows_out[: max(0, int(topk))], start=1):
        print(
            f"{i:>2}. {row['config_id']} "
            f"processed_med={row['processed_boxes_median']} "
            f"reached_med={row['reached_step_median']} "
            f"top3_mean={row['top3_pct_mean']}"
        )


def _reps_for_phase(args: argparse.Namespace, argv: list[str]) -> int:
    has_reps_flag = any(tok == "--reps" or tok.startswith("--reps=") for tok in argv)
    if args.phase == "explore":
        return 1
    if args.phase == "confirm" and not has_reps_flag:
        return 5
    return max(1, int(args.reps))


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    if int(args.jobs) < 1:
        parser.error("--jobs must be >= 1")
    repo_root = Path(__file__).resolve().parents[1]

    out_root = make_out_root(args.out_root)
    reps = _reps_for_phase(args, raw_argv)
    out_root.mkdir(parents=True, exist_ok=True)

    if args.phase == "explore":
        configs = enumerate_configs(args)
    else:
        try:
            configs = _load_confirm_configs(out_root)
        except SystemExit:
            if args.dry_run:
                print(
                    f"WARN: top_configs.json no encontrado en {out_root}; "
                    "dry-run confirm usara grid completo de explore.",
                    file=sys.stderr,
                )
                configs = enumerate_configs(args)
            else:
                raise

    python_exec = sys.executable
    env = _add_pythonpath(
        {
            **os.environ,
            "PYTHONHASHSEED": "0",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "MALLOC_ARENA_MAX": "2",
            "PYTHONUNBUFFERED": "1",
        },
        repo_root,
    )

    supports_spatial, missing_flags, _help_text = detect_spatial_flags(python_exec, repo_root)
    if not args.dry_run and not supports_spatial:
        print(
            "ERROR: sim.run no soporta flags spatial requeridos para modo real. "
            "Implementado dry-run + tests; el modo real requiere rebase/merge "
            "sobre la rama que incorpora esos flags (p.ej. feat/spatial-tower-penalty-early). "
            f"Faltan: {', '.join(missing_flags)}",
            file=sys.stderr,
        )
        return 2

    runs_csv_path = out_root / "runs.csv"
    rows: list[dict[str, Any]] = []
    done_ok: set[tuple[str, int]] = set()
    if args.resume and runs_csv_path.exists():
        rows, done_ok = load_existing_runs(runs_csv_path)

    tasks: list[tuple[SweepConfig, int]] = []
    for cfg in configs:
        for rep_id in range(1, reps + 1):
            key = (cfg.config_id, rep_id)
            if args.resume and key in done_ok:
                continue
            tasks.append((cfg, rep_id))

    if args.dry_run:
        _stream_runs(
            tasks=tasks,
            args=args,
            out_root=out_root,
            python_exec=python_exec,
            env=env,
            repo_root=repo_root,
            dry_run=True,
        )
        return 0

    new_rows: list[dict[str, Any]] = []
    append_mode = bool(args.resume and runs_csv_path.exists())
    fail_fast_triggered = False
    handle, writer = _open_runs_csv(runs_csv_path, append=append_mode)
    try:
        def _on_emit(row: dict[str, Any]) -> None:
            writer.writerow(row)
            handle.flush()
            new_rows.append(row)

        _, fail_fast_triggered = _stream_runs(
            tasks=tasks,
            args=args,
            out_root=out_root,
            python_exec=python_exec,
            env=env,
            repo_root=repo_root,
            dry_run=False,
            on_emit=_on_emit,
        )
    finally:
        handle.close()

    rows.extend(new_rows)

    if args.phase == "explore":
        write_top_configs(rows, configs, out_root, args.topk)
    else:
        write_confirm_configs(rows, out_root, args.topk)

    if fail_fast_triggered:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
