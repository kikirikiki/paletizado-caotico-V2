#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RunKpis:
    variant: str
    returncode: int
    pallets_closed: int
    pallets_created: int | None
    seq_len: int
    first_pallet_boxes: int | None
    processed_boxes: int | None
    stop_reason: str | None
    seq_sum: int
    seq_avg: float | None
    seq_min: int | None
    vol_util: float | None
    zones0: float | None
    dom0: float | None
    tower0: float | None
    deadlock_stability: int
    close_height_full: int
    planar_count: int | None
    stand_hw_count: int | None
    stand_hw_used_total: int | None
    rejected_support_pct: float | None
    rejected_corner_pct: float | None
    micro_plan_time_ms_mean: float | None
    bf_calls: int | None
    bf_applied: int | None
    bf_boxes_mean: float | None
    out_json: str
    out_log: str


def _safe_get(d: dict[str, Any], path: list[str], default: Any = None) -> Any:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def extract_kpis(payload: dict[str, Any], *, dest: str = "1") -> dict[str, Any]:
    k = _safe_get(payload, ["metrics", "pallet_kpis"], default={}) or {}

    pallets_created = _safe_get(k, ["pallets_count", dest], default=None)

    seq = _safe_get(k, ["continuous_pallet_sequence", dest], default=[]) or []
    if not isinstance(seq, list):
        seq = []
    seq_int = [int(x) for x in seq] if seq else []
    seq_len = len(seq_int)
    pallets_closed = int(seq_len)
    first_pallet_boxes = int(seq_int[0]) if seq_len > 0 else None
    seq_sum = sum(seq_int)
    seq_avg = (seq_sum / seq_len) if seq_len > 0 else None
    seq_min = min(seq_int) if seq_len > 0 else None
    processed_boxes = _safe_get(payload, ["metrics", "processed_boxes"], default=None)
    stop_reason = _safe_get(payload, ["metrics", "stop_reason"], default=None)

    vol_util = _safe_get(k, ["pallet_volume_utilization", dest], default=None)
    zones0 = _safe_get(k, ["coverage_zones_touched_base", dest], default=None)
    dom0 = _safe_get(k, ["coverage_dominant_free_rect_ratio_base", dest], default=None)
    tower0 = _safe_get(k, ["coverage_tower_index_base", dest], default=None)

    closures = _safe_get(k, ["closures_by_reason"], default={}) or {}
    deadlock_stability = int(closures.get("DEADLOCK_STABILITY", 0) or 0)
    close_height_full = int(closures.get("CLOSE_HEIGHT_FULL", 0) or 0)

    orientation_counts = _safe_get(k, ["orientation_counts"], default={}) or {}
    planar_count = orientation_counts.get("planar")
    stand_hw_count = orientation_counts.get("stand_hw")

    stand_hw_used_total = _safe_get(k, ["stand_hw_used_total"], default=None)

    rejected_support_pct = _safe_get(k, ["rejected_by_support_ratio_pct"], default=None)
    rejected_corner_pct = _safe_get(k, ["rejected_by_corner_support_pct"], default=None)

    micro_plan_time_ms_mean = _safe_get(k, ["micro_plan_time_ms_mean"], default=None)
    bf_calls = _safe_get(k, ["batchfill_calls"], default=None)
    bf_applied = _safe_get(k, ["batchfill_applied"], default=None)
    bf_boxes_mean = _safe_get(k, ["batchfill_selected_layer_boxes_mean"], default=None)

    return {
        "pallets_closed": int(pallets_closed),
        "pallets_created": int(pallets_created) if pallets_created is not None else None,
        "seq_len": int(seq_len),
        "first_pallet_boxes": int(first_pallet_boxes) if first_pallet_boxes is not None else None,
        "processed_boxes": int(processed_boxes) if processed_boxes is not None else None,
        "stop_reason": str(stop_reason) if stop_reason is not None else None,
        "seq_sum": int(seq_sum),
        "seq_avg": float(seq_avg) if seq_avg is not None else None,
        "seq_min": int(seq_min) if seq_min is not None else None,
        "vol_util": float(vol_util) if vol_util is not None else None,
        "zones0": float(zones0) if zones0 is not None else None,
        "dom0": float(dom0) if dom0 is not None else None,
        "tower0": float(tower0) if tower0 is not None else None,
        "deadlock_stability": int(deadlock_stability),
        "close_height_full": int(close_height_full),
        "planar_count": int(planar_count) if planar_count is not None else None,
        "stand_hw_count": int(stand_hw_count) if stand_hw_count is not None else None,
        "stand_hw_used_total": int(stand_hw_used_total) if stand_hw_used_total is not None else None,
        "rejected_support_pct": float(rejected_support_pct) if rejected_support_pct is not None else None,
        "rejected_corner_pct": float(rejected_corner_pct) if rejected_corner_pct is not None else None,
        "micro_plan_time_ms_mean": float(micro_plan_time_ms_mean) if micro_plan_time_ms_mean is not None else None,
        "bf_calls": int(bf_calls) if bf_calls is not None else None,
        "bf_applied": int(bf_applied) if bf_applied is not None else None,
        "bf_boxes_mean": float(bf_boxes_mean) if bf_boxes_mean is not None else None,
    }


def _fmt(v: Any, *, nd: int = 3) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _short_stop_reason(value: str | None, *, max_len: int = 22) -> str:
    if value is None:
        return "-"
    text = str(value)
    if len(text) <= max_len:
        return text
    return f"{text[:max_len - 3]}..."


def build_base_cmd(args: argparse.Namespace) -> list[str]:
    py = str(args.python)
    cmd: list[str] = [
        py, "-m", "sim.run",
        "--excel", str(args.excel),
        "--model", "M1",
        "--policy", "palca",
        "--arrival-mode", "immediate",
        "--force-destination", str(args.force_destination),
        "--continuous-pallets",
        "--k", str(args.k),
        "--heuristic", str(args.heuristic),
        "--overhang_mm", str(args.overhang_mm),
        "--stability-mode", str(args.stability_mode),
        "--min-support", str(args.min_support),
        "--time-budget-ms", str(args.time_budget_ms),
        "--micro-plan",
        "--micro-depth", str(args.micro_depth),
        "--micro-width", str(args.micro_width),
        "--micro-topk", str(args.micro_topk),
        "--score-mode", str(args.score_mode),
        "--height-slack-mm", str(args.height_slack_mm),
    ]
    stacking_mode = str(getattr(args, "stacking_mode", "layers") or "layers")
    if stacking_mode != "layers":
        cmd.extend(["--stacking-mode", stacking_mode])
    z_band_mm = getattr(args, "z_band_mm", None)
    if z_band_mm is not None:
        cmd.extend(["--z-band-mm", str(int(z_band_mm))])
    if int(args.max_pallets) > 0:
        cmd.extend(["--max-pallets", str(int(args.max_pallets))])
    coverage_grid_x = int(getattr(args, "coverage_grid_x", 0) or 0)
    coverage_grid_y = int(getattr(args, "coverage_grid_y", 0) or 0)
    coverage_weight = float(getattr(args, "coverage_weight", 0.0) or 0.0)
    dominant_free_rect_weight = float(getattr(args, "dominant_free_rect_weight", 0.0) or 0.0)
    dominant_free_rect_ratio_gate = float(getattr(args, "dominant_free_rect_ratio_gate", 0.35) or 0.35)
    if coverage_grid_x > 0:
        cmd.extend(["--coverage-grid-x", str(coverage_grid_x)])
    if coverage_grid_y > 0:
        cmd.extend(["--coverage-grid-y", str(coverage_grid_y)])
    if coverage_weight > 0.0:
        cmd.extend(["--coverage-weight", str(coverage_weight)])
    if dominant_free_rect_weight > 0.0:
        cmd.extend(["--dominant-free-rect-weight", str(dominant_free_rect_weight)])
    if (
        dominant_free_rect_ratio_gate != 0.35
        or dominant_free_rect_weight > 0.0
    ):
        cmd.extend(["--dominant-free-rect-ratio-gate", str(dominant_free_rect_ratio_gate)])
    batchfill_layer_starter = bool(getattr(args, "batchfill_layer_starter", False))
    batchfill_starters_max = int(getattr(args, "batchfill_starters_max", 6) or 6)
    batchfill_budget_ms = int(getattr(args, "batchfill_budget_ms", 150) or 150)
    batchfill_greedy_topk = int(getattr(args, "batchfill_greedy_topk", 12) or 12)
    if batchfill_layer_starter:
        cmd.append("--batchfill-layer-starter")
    if batchfill_layer_starter or batchfill_starters_max != 6:
        cmd.extend(["--batchfill-starters-max", str(batchfill_starters_max)])
    if batchfill_layer_starter or batchfill_budget_ms != 150:
        cmd.extend(["--batchfill-budget-ms", str(batchfill_budget_ms)])
    if batchfill_layer_starter or batchfill_greedy_topk != 12:
        cmd.extend(["--batchfill-greedy-topk", str(batchfill_greedy_topk)])
    return cmd


def variant_flags(variant: str) -> list[str]:
    v = variant.strip().lower()
    if v == "planar":
        return ["--orientation-mode", "planar"]
    if v == "gate0":
        return ["--orientation-mode", "planar+stand_hw", "--stand-hw-height-margin-gate-mm", "0"]
    if v == "gate400":
        return ["--orientation-mode", "planar+stand_hw", "--stand-hw-height-margin-gate-mm", "400"]
    if v == "gate2400":
        return ["--orientation-mode", "planar+stand_hw", "--stand-hw-height-margin-gate-mm", "2400"]
    raise ValueError(f"Unknown variant: {variant!r}. Allowed: planar, gate0, gate400, gate2400")


def run_one(
    *,
    base_cmd: list[str],
    variant: str,
    outdir: Path,
    env: dict[str, str],
    dest: str,
    dry_run: bool,
) -> RunKpis:
    out_json = outdir / f"{variant}.json"
    out_log = outdir / f"{variant}.log"

    cmd = list(base_cmd) + variant_flags(variant) + ["--out", str(out_json)]

    if dry_run:
        out_log.write_text("DRY RUN\n" + " ".join(shlex.quote(x) for x in cmd) + "\n", encoding="utf-8")
        return RunKpis(
            variant=variant,
            returncode=0,
            pallets_closed=0,
            pallets_created=None,
            seq_len=0,
            first_pallet_boxes=None,
            processed_boxes=None,
            stop_reason=None,
            seq_sum=0,
            seq_avg=None,
            seq_min=None,
            vol_util=None,
            zones0=None,
            dom0=None,
            tower0=None,
            deadlock_stability=0,
            close_height_full=0,
            planar_count=None,
            stand_hw_count=None,
            stand_hw_used_total=None,
            rejected_support_pct=None,
            rejected_corner_pct=None,
            micro_plan_time_ms_mean=None,
            bf_calls=None,
            bf_applied=None,
            bf_boxes_mean=None,
            out_json=str(out_json),
            out_log=str(out_log),
        )

    with out_log.open("w", encoding="utf-8") as f:
        f.write("CMD:\n")
        f.write(" ".join(shlex.quote(x) for x in cmd) + "\n\n")
        proc = subprocess.run(cmd, stdout=f, stderr=f, env=env, cwd=str(Path.cwd()))
        rc = int(proc.returncode)

    payload: dict[str, Any] = {}
    if out_json.exists():
        try:
            payload = json.loads(out_json.read_text(encoding="utf-8"))
        except Exception:
            payload = {}

    k = extract_kpis(payload, dest=dest) if payload else {
        "pallets_closed": 0,
        "pallets_created": None,
        "seq_len": 0,
        "first_pallet_boxes": None,
        "processed_boxes": None,
        "stop_reason": None,
        "seq_sum": 0,
        "seq_avg": None,
        "seq_min": None,
        "vol_util": None,
        "zones0": None,
        "dom0": None,
        "tower0": None,
        "deadlock_stability": 0,
        "close_height_full": 0,
        "planar_count": None,
        "stand_hw_count": None,
        "stand_hw_used_total": None,
        "rejected_support_pct": None,
        "rejected_corner_pct": None,
        "micro_plan_time_ms_mean": None,
        "bf_calls": None,
        "bf_applied": None,
        "bf_boxes_mean": None,
    }

    return RunKpis(
        variant=variant,
        returncode=rc,
        pallets_closed=k["pallets_closed"],
        pallets_created=k["pallets_created"],
        seq_len=k["seq_len"],
        first_pallet_boxes=k["first_pallet_boxes"],
        processed_boxes=k["processed_boxes"],
        stop_reason=k["stop_reason"],
        seq_sum=k["seq_sum"],
        seq_avg=k["seq_avg"],
        seq_min=k["seq_min"],
        vol_util=k["vol_util"],
        zones0=k["zones0"],
        dom0=k["dom0"],
        tower0=k["tower0"],
        deadlock_stability=k["deadlock_stability"],
        close_height_full=k["close_height_full"],
        planar_count=k["planar_count"],
        stand_hw_count=k["stand_hw_count"],
        stand_hw_used_total=k["stand_hw_used_total"],
        rejected_support_pct=k["rejected_support_pct"],
        rejected_corner_pct=k["rejected_corner_pct"],
        micro_plan_time_ms_mean=k["micro_plan_time_ms_mean"],
        bf_calls=k["bf_calls"],
        bf_applied=k["bf_applied"],
        bf_boxes_mean=k["bf_boxes_mean"],
        out_json=str(out_json),
        out_log=str(out_log),
    )


def print_table(rows: list[RunKpis]) -> None:
    headers = [
        "variant", "rc", "pallets", "p_created",
        "first_pal", "proc", "stop", "seq_avg", "seq_min", "seq_sum",
        "vol_util", "zones0", "dom0", "tower0", "deadl", "hfull",
        "stand_used", "rej_sup%", "rej_cor%", "micro_ms", "bf_calls", "bf_appl", "bf_bmean",
    ]
    print(" | ".join(headers))
    print("-" * (len(" | ".join(headers)) + 10))
    for r in rows:
        print(
            " | ".join(
                [
                    r.variant,
                    str(r.returncode),
                    _fmt(r.pallets_closed, nd=0),
                    _fmt(r.pallets_created, nd=0),
                    _fmt(r.first_pallet_boxes, nd=0),
                    _fmt(r.processed_boxes, nd=0),
                    _short_stop_reason(r.stop_reason),
                    _fmt(r.seq_avg),
                    _fmt(r.seq_min, nd=0),
                    _fmt(r.seq_sum, nd=0),
                    _fmt(r.vol_util),
                    _fmt(r.zones0),
                    _fmt(r.dom0),
                    _fmt(r.tower0),
                    _fmt(r.deadlock_stability, nd=0),
                    _fmt(r.close_height_full, nd=0),
                    _fmt(r.stand_hw_used_total, nd=0),
                    _fmt(r.rejected_support_pct),
                    _fmt(r.rejected_corner_pct),
                    _fmt(r.micro_plan_time_ms_mean),
                    _fmt(r.bf_calls, nd=0),
                    _fmt(r.bf_applied, nd=0),
                    _fmt(r.bf_boxes_mean),
                ]
            )
        )


def write_csv(rows: list[RunKpis], outpath: Path) -> None:
    outpath.parent.mkdir(parents=True, exist_ok=True)
    with outpath.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Run sim.run ablations and print compact KPI summary.")
    p.add_argument("--excel", required=True, help="Path to Excel dataset (e.g. data/Flujo rampas - Editado.xlsx)")
    p.add_argument("--outdir", required=True, help="Output directory OUTSIDE the repo (recommended: /tmp/...)")
    p.add_argument("--variants", nargs="*", default=["planar", "gate0", "gate400", "gate2400"])
    p.add_argument("--dest", default="1")
    p.add_argument("--dry-run", action="store_true")

    # base flags (defaults match our typical run)
    p.add_argument("--python", default="./.venv/bin/python")
    p.add_argument("--force-destination", dest="force_destination", default=1, type=int)
    p.add_argument("--max-pallets", dest="max_pallets", default=0, type=int)
    p.add_argument("--k", default=15, type=int)
    p.add_argument("--heuristic", default="bssf", choices=["baf", "bssf"])
    p.add_argument("--overhang_mm", default=20, type=int)
    p.add_argument("--stability-mode", dest="stability_mode", default="ratio+corners+settle",
                   choices=["off", "ratio", "ratio+corners", "ratio+corners+settle"])
    p.add_argument("--min-support", dest="min_support", default=0.85, type=float)
    p.add_argument("--time-budget-ms", dest="time_budget_ms", default=900, type=int)
    p.add_argument("--micro-depth", default=4, type=int)
    p.add_argument("--micro-width", default=40, type=int)
    p.add_argument("--micro-topk", default=15, type=int)
    p.add_argument("--score-mode", dest="score_mode", default="min_height_slack_then_gain",
                   choices=["gain_frag", "min_height_then_gain", "min_height_slack_then_gain"])
    p.add_argument("--height-slack-mm", dest="height_slack_mm", default=120, type=int)
    p.add_argument("--stacking-mode", dest="stacking_mode", default="layers", choices=["layers", "heightfield"])
    p.add_argument("--z-band-mm", dest="z_band_mm", default=None, type=int)
    p.add_argument("--coverage-grid-x", dest="coverage_grid_x", default=0, type=int)
    p.add_argument("--coverage-grid-y", dest="coverage_grid_y", default=0, type=int)
    p.add_argument("--coverage-weight", dest="coverage_weight", default=0.0, type=float)
    p.add_argument("--dominant-free-rect-weight", dest="dominant_free_rect_weight", default=0.0, type=float)
    p.add_argument("--dominant-free-rect-ratio-gate", dest="dominant_free_rect_ratio_gate", default=0.35, type=float)
    p.add_argument("--batchfill-layer-starter", dest="batchfill_layer_starter", action="store_true")
    p.add_argument("--batchfill-starters-max", dest="batchfill_starters_max", default=6, type=int)
    p.add_argument("--batchfill-budget-ms", dest="batchfill_budget_ms", default=150, type=int)
    p.add_argument("--batchfill-greedy-topk", dest="batchfill_greedy_topk", default=12, type=int)

    args = p.parse_args(argv)

    outdir = Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    base_cmd = build_base_cmd(args)

    env = dict(os.environ)
    # Make it resilient: if user forgets to pass PYTHONPATH=src, set it.
    if not env.get("PYTHONPATH"):
        env["PYTHONPATH"] = "src"

    rows: list[RunKpis] = []
    for v in args.variants:
        r = run_one(
            base_cmd=base_cmd,
            variant=v,
            outdir=outdir,
            env=env,
            dest=str(args.dest),
            dry_run=bool(args.dry_run),
        )
        rows.append(r)

    print_table(rows)
    write_csv(rows, outdir / "summary.csv")
    (outdir / "summary.json").write_text(
        json.dumps([asdict(r) for r in rows], indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
