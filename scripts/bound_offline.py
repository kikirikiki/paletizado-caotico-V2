#!/usr/bin/env python3
"""Part B only: offline constructive search with production feasibility/replay.

One process, one BLAS thread; run at most eight simultaneous processes externally.
No run_sweep lock is acquired. Checkpoints are atomic, human-readable JSON.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from math import isfinite
import os
from pathlib import Path
import sys

for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from palca.study.offline_search import (atomic_json, beam_search, column_search, fingerprint, load_boxes,
                                       order_search, production_policy, summarize)
from palca.study.params import PRESETS, REFERENCE_EXCEL
from palca.study.metric import aggregate_seeds


def comparison(path, config):
    payload = json.loads(path.read_text())
    selected = dict(payload["configs"][config])
    value = selected["kpi"]["mean"]
    if not isfinite(value) or not 0 < value <= 1:
        raise ValueError("Invalid reference KPI")
    per_seed_sources = []
    if "kpi_all_pallets" not in selected:
        values = []
        for seed in payload["seeds"]:
            source = path.parent / config / str(seed) / "kpi.json"
            row = json.loads(source.read_text())
            values.append(row["kpi_all_pallets"])
            per_seed_sources.append(dict(path=str(source), sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
        selected["kpi_all_pallets"] = aggregate_seeds(values)
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                config=config, metrics=selected, per_seed_sources=per_seed_sources)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=("beam", "order", "columns"), required=True)
    parser.add_argument("--excel", type=Path, default=Path(REFERENCE_EXCEL))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--cpu-seconds", type=float, default=1800)
    parser.add_argument("--width", type=int, default=16)
    parser.add_argument("--top-m", type=int, default=8)
    parser.add_argument("--objective", choices=("volume", "debt"), default="volume")
    parser.add_argument("--locations", choices=("production", "all"), default="production")
    parser.add_argument("--debt-weight", type=float, default=1.0)
    parser.add_argument("--roughness-weight", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--evaluations", type=int, default=12)
    parser.add_argument("--templates", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--group-geometry", action="store_true",
                        help="Column search only: merge equal dimensions regardless of inactive mass")
    parser.add_argument("--baseline-summary", type=Path, default=Path("out/sweeps/baseline_v1/summary.json"))
    parser.add_argument("--buffer-summary", type=Path, default=Path("out/sweeps/bound_buffer_k_v1/summary.json"))
    parser.add_argument("--validate", type=Path, help="Only replay/validate an existing result; no search")
    args = parser.parse_args()
    if not isfinite(args.cpu_seconds) or not 0 < args.cpu_seconds <= 7200:
        parser.error("--cpu-seconds must be in (0, 7200]; reserve time for completion/validation")
    if any(not isfinite(x) or x < 0 for x in (args.debt_weight, args.roughness_weight)):
        parser.error("Weights must be finite and nonnegative")
    boxes = load_boxes(args.excel)
    if args.validate:
        payload = json.loads(args.validate.read_text())
        result = summarize(payload["pallets"], boxes)
        for key in ("kpi", "n_pallets_total", "n_closed", "boxes_placed", "pallet_utils", "sequence"):
            assert result[key] == payload[key], key
        print(json.dumps({k: result[k] for k in ("kpi", "n_pallets_total", "n_violations", "n_production_violations")}))
        return
    output = args.out or Path(f"out/bounds/offline_{args.method}.json")
    checkpoint = args.checkpoint or output.parent / "checkpoints" / (output.stem + ".json")
    if (output.exists() or checkpoint.exists()) and not args.resume:
        parser.error("Output/checkpoint already exists; use --resume or a new --out")
    references = dict(baseline=comparison(args.baseline_summary, "arm6_4orient"),
                      buffer_k186=comparison(args.buffer_summary, "buffer_k186"),
                      h1_micro_plan=dict(kpi=0.7090, source="director supplied; different branch; not rerun"))
    started = datetime.now(timezone.utc).isoformat()
    source_fingerprint = fingerprint(boxes, {})
    previous = json.loads(output.read_text()) if args.resume and output.exists() else {}
    common = dict(cpu_seconds=args.cpu_seconds, checkpoint=checkpoint, resume=args.resume,
                  progress=lambda row: print(json.dumps(row), flush=True))
    if args.method == "order":
        result = order_search(boxes, seed=args.seed, evaluations=args.evaluations, **common)
    elif args.method == "columns":
        result = column_search(boxes, width=args.width, templates=args.templates, seed=args.seed,
                               group_geometry=args.group_geometry, **common)
    else:
        result = beam_search(boxes, width=args.width, top_m=args.top_m, objective=args.objective,
                             locations=args.locations, debt_weight=args.debt_weight,
                             roughness_weight=args.roughness_weight, **common)
    result.update(schema_version=1, method=args.method, started_at=previous.get("started_at", started),
                  invocation_started_at=started,
                  finished_at=datetime.now(timezone.utc).isoformat(), command=sys.argv,
                  preset="arm6_4orient", effective_policy=asdict(production_policy().config),
                  production_params=PRESETS["arm6_4orient"], references=references,
                  input=dict(path=str(args.excel), sha256=hashlib.sha256(args.excel.read_bytes()).hexdigest(),
                             n_boxes=len(boxes), volume_mm3=sum(b.volume_mm3 for b in boxes),
                             ids="1-based original load_arrivals order, before permutation",
                             boxes=[asdict(b) for b in boxes]),
                  source_fingerprint=source_fingerprint, ci95=None,
                  result_kind="best constructive witness, not a formal upper bound or seed mean")
    if result["kpi"] is not None:
        result["gap_pp"] = {key: 100 * (result["kpi"] - ref["metrics"]["kpi"]["mean"])
                            for key, ref in references.items() if "metrics" in ref}
        result["gap_pp"]["h1_micro_plan"] = 100 * (result["kpi"] - 0.709)
        result["gap_all_pallets_pp"] = {
            key: 100 * (result["kpi_all_pallets"] - ref["metrics"]["kpi_all_pallets"]["mean"])
            for key, ref in references.items() if "metrics" in ref}
    atomic_json(output, result)
    print(json.dumps(dict(output=str(output), **{k: result[k] for k in (
        "kpi", "n_pallets_total", "n_closed", "kpi_all_pallets", "n_violations",
        "cpu_seconds", "wall_seconds")})), flush=True)


if __name__ == "__main__":
    main()
