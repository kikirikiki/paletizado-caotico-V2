#!/usr/bin/env python3
"""D-036 acceptance runs, serialized, with all artifacts under /tmp/pulmon.

Examples (activate .venv and export PYTHONPATH=$PWD/src first):
  python scripts/verify_pulmon.py equivalence
  python scripts/verify_pulmon.py short --rows 60
  python scripts/verify_pulmon.py timing
The short check is one truncated seed per config, never the tuning sweep.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

# Set before numpy/BLAS are imported. All acceptance episodes run in series.
for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ[key] = "1"

from scripts.run_sweep import (execute_tasks, load_configs, make_task, run_task, source_hash,
                               sweep_lock, write_json)

REFERENCE = Path("/home/ingen/code/Paletizador/09_Software/paletizado-autopilot/out/sweeps/h6_sin_gate_ref/h6_sin_gate")


def run_checks(mode: str, output: Path, reference: Path = REFERENCE, rows: int = 60, workers: int = 1):
    import numpy as np
    import pandas as pd

    output.mkdir(parents=True, exist_ok=True)
    configs = load_configs(ROOT / "configs/sweeps/pulmon_tune.yaml")
    head = subprocess.check_output(["git", "--no-pager", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    code_hash = source_hash()
    results = []

    def episode(config, seed):
        task = make_task(config, seed, output, head, code_hash)
        print(f"[pulmon] {mode}: {config['name']} seed={seed}", flush=True)
        result = run_task(task)
        assert result["n_unplaced"] == result["n_violations"] == result["hard_cap_hits"] == 0
        if "pallet_staging" in result:
            s = result["pallet_staging"]
            assert s["staging_remaining"] == 0 and s["staging_parks"] == s["staging_unparks"]
            assert s["staging_max_occupancy"] <= config["overrides"]["pallet_staging_slots"]
        results.append(result)
        return result, json.loads((Path(task["directory"])/"dump.json").read_text())

    summary = dict(mode=mode, source_hash=code_hash, git_head=head)
    with sweep_lock():
        if mode == "equivalence":
            checks = []
            for seed in (50021, 1, 42):
                expected = json.loads((reference/str(seed)/"dump.json").read_text())["pallets"]
                dumps = []
                for explicit_zero in (False, True):
                    config = deepcopy(configs[0])
                    config["name"] += "_zero" if explicit_zero else "_absent"
                    if explicit_zero:
                        config["overrides"]["pallet_staging_slots"] = 0
                    _, dump = episode(config, seed)
                    assert dump["pallets"] == expected, (seed, explicit_zero)
                    assert "pallet_staging" not in dump and "pallet_staging_slots" not in dump["params"]
                    dumps.append(dump)
                assert dumps[0] == dumps[1]
                checks.append(dict(seed=seed, absent=True, explicit_zero=True, identical_dumps=True))
                write_json(output/"equivalence.json", dict(summary, checks=checks))
            summary["checks"] = checks
        elif mode == "short":
            excel = output/"short.xlsx"
            pd.read_excel(ROOT/"data/Flujo rampas - Editado.xlsx").iloc[:rows].to_excel(excel, index=False)
            checks = []
            tasks = []
            for config in deepcopy(configs):
                config["overrides"]["excel_path"] = str(excel)
                tasks.append(make_task(config, 1001, output, head, code_hash))
            execute_tasks(tasks, workers)
            for task in tasks:
                config = next(c for c in configs if c["name"] == task["config"])
                result = json.loads((Path(task["directory"])/"kpi.json").read_text())
                assert result["n_unplaced"] == result["n_violations"] == result["hard_cap_hits"] == 0
                if "pallet_staging" in result:
                    staging = result["pallet_staging"]
                    assert staging["staging_remaining"] == 0
                    assert staging["staging_parks"] == staging["staging_unparks"]
                    assert staging["staging_max_occupancy"] <= config["overrides"]["pallet_staging_slots"]
                run = json.loads((output/config["name"]/"1001/run.json").read_text())
                max_window = run["metrics"]["pallet_kpis"]["accessible_window_stats"]["max"]
                if config["name"] == "ventana18":
                    assert max_window == 18
                checks.append(dict(config=config["name"], seed=1001, boxes=result["boxes_placed"],
                    parks=result.get("pallet_staging", {}).get("staging_parks", 0), max_window=max_window))
                write_json(output/"short.json", dict(summary, rows=rows, checks=checks))
            summary.update(rows=rows, checks=checks,
                           mean_staging_parks=float(np.mean([c["parks"] for c in checks if c["config"].startswith("pulmon_")])))
            write_json(output/"short.json", summary)
        else:
            config = next(c for c in configs if c["name"] == "pulmon_k3_hole_f80")
            samples, per_seed = [], []
            load_before = os.getloadavg()
            for seed in (1, 42):
                result, _ = episode(config, seed)
                values = result["decision_timing"]["durations_ms"]
                samples.extend(values)
                per_seed.append(dict(seed=seed, decisions=len(values), mean_ms=float(np.mean(values)),
                    p95_ms=float(np.percentile(values, 95)), staging=result["pallet_staging"]))
            mean, p95 = float(np.mean(samples)), float(np.percentile(samples, 95))
            summary.update(config=config["name"], per_seed=per_seed, decisions=len(samples),
                mean_ms=mean, p95_ms=p95, load_before=load_before, load_after=os.getloadavg(),
                workers=1, thread_env={k: os.environ[k] for k in (
                    "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")},
                passes=mean <= 1000 and p95 <= 3000)
            write_json(output/"timing.json", summary)
            assert summary["passes"], (mean, p95)
    print(json.dumps(summary, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("equivalence", "short", "timing"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reference", type=Path, default=REFERENCE)
    parser.add_argument("--rows", type=int, default=60)
    parser.add_argument("--workers", type=int, default=1, help="Parallelism for the short startup check only")
    args = parser.parse_args()
    output = (args.output or Path("/tmp/pulmon")/args.mode).resolve()
    if not output.is_relative_to(Path("/tmp/pulmon")):
        parser.error("Acceptance artifacts must stay under /tmp/pulmon/")
    if not 19 <= args.rows <= 60:
        parser.error("--rows must be between 19 and 60")
    if args.workers < 1 or (args.mode != "short" and args.workers != 1):
        parser.error("--workers > 1 is allowed only for the short startup check")
    run_checks(args.mode, output, args.reference, args.rows, args.workers)


if __name__ == "__main__":
    main()
