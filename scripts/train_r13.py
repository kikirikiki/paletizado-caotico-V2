#!/usr/bin/env python3
"""Generate GENERIC-only episodes and fit a small JSON ridge state value.

Artifacts must be outside out/. Training/validation/test splits are entire
independent streams with disjoint seeds. No evaluated stream is read/imported.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import time

# Set these before NumPy imports and propagate to spawned workers.
for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ[key] = "1"

import numpy as np

from palca.scoring.catalog_prior import GENERIC
from palca.scoring.learned_value import FEATURE_NAMES, FEATURE_VERSION
from palca.scoring.synthetic_value_data import EPISODE_BOXES, POLICY, generate_episode


def metrics(y, predictions):
    error = predictions - y
    return dict(states=len(y), r2=float(1 - np.sum(error**2) / np.sum((y-y.mean())**2)),
                mae=float(np.abs(error).mean()), rmse=float(np.sqrt(np.mean(error**2))),
                bias=float(error.mean()))


def fit_ridge(x, y, alpha):
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-9] = 1
    z = (x - mean) / scale
    intercept = float(y.mean())
    coef = np.linalg.solve(z.T @ z + alpha * np.eye(x.shape[1]), z.T @ (y-intercept))
    return dict(feature_version=FEATURE_VERSION, feature_names=list(FEATURE_NAMES),
                mean=mean.tolist(), scale=scale.tolist(), coef=coef.tolist(),
                intercept=intercept, alpha=alpha)


def predict(model, x):
    raw = (x - model["mean"]) / model["scale"] @ model["coef"] + model["intercept"]
    # Same physical bounds as inference (placed_volume feature).
    return np.clip(raw, x[:, FEATURE_NAMES.index("placed_volume")], 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path("/tmp/r13/data"))
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--train-flows", type=int, default=240)
    parser.add_argument("--validation-flows", type=int, default=60)
    parser.add_argument("--test-flows", type=int, default=60)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--seed-start", type=int, default=1300000)
    parser.add_argument("--budget-seconds", type=float, default=5400)
    args = parser.parse_args()
    counts = [args.train_flows, args.validation_flows, args.test_flows]
    if (not 1 <= args.workers <= 14 or min(counts) < 1
            or not 0 < args.budget_seconds <= 5400):
        parser.error("positive split sizes; 1..14 workers; budget <= 5400 s required")
    work_root = Path("/tmp/r13").resolve()
    if args.work_dir.resolve() != work_root and work_root not in args.work_dir.resolve().parents:
        parser.error("Intermediate synthetic data must stay under /tmp/r13/")
    for path in (args.work_dir, args.model):
        if "out" in path.resolve().parts:
            parser.error("R13 artifacts cannot go under out/")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    seeds = list(range(args.seed_start, args.seed_start + sum(counts)))
    rows = {}
    # Resume data only with exact provenance; never ingest arbitrary NPZ files.
    provenance = dict(schema=FEATURE_VERSION, policy=POLICY, prior=GENERIC, boxes=EPISODE_BOXES)
    fingerprint = hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()
    missing = []
    for seed in seeds:
        path = args.work_dir / f"synthetic_{seed}.npz"
        if path.exists():
            with np.load(path, allow_pickle=False) as data:
                if data["provenance"].item() != fingerprint:
                    raise ValueError(f"Provenance mismatch: {path}")
                rows[seed] = {k: data[k] for k in ("x", "y", "closed_pallets", "censored_states")}
        else:
            missing.append(seed)
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = {pool.submit(generate_episode, seed): seed for seed in missing}
        for future in as_completed(futures):
            row = future.result()
            seed = futures[future]
            np.savez_compressed(args.work_dir / f"synthetic_{seed}.npz", provenance=fingerprint, **row)
            rows[seed] = row
            if len(rows) % 10 == 0 or len(rows) == len(seeds):
                print(f"{len(rows)}/{len(seeds)} episodes; elapsed={time.monotonic()-start:.1f}s", flush=True)
            if time.monotonic() - start > args.budget_seconds:
                # Workers finish their current bounded episode; do not train on
                # a load-dependent subset. Rerun with a smaller fixed split.
                for f in futures:
                    f.cancel()
                raise RuntimeError("Generation budget exceeded; reduce fixed split sizes")
    splits = {}
    offset = 0
    for name, count in zip(("train", "validation", "test"), counts):
        group = seeds[offset:offset+count]
        offset += count
        splits[name] = (np.concatenate([rows[s]["x"] for s in group]),
                        np.concatenate([rows[s]["y"] for s in group]))
    x, y = splits["train"]
    candidates = []
    for alpha in (1., 10., 100., 1000.):
        model = fit_ridge(x, y, alpha)
        candidates.append((metrics(splits["validation"][1], predict(model, splits["validation"][0]))["mae"], alpha, model))
    _, _, model = min(candidates, key=lambda row: row[:2])
    report = dict(provenance=provenance, source="synthetic_generic_only", workers=args.workers,
                  generation_seconds=time.monotonic()-start, resumed_flows=len(seeds)-len(missing),
                  flows=sum(counts), states=sum(len(r[1]) for r in splits.values()),
                  closed_pallets=sum(int(rows[s]["closed_pallets"]) for s in seeds),
                  censored_states=sum(int(rows[s]["censored_states"]) for s in seeds),
                  splits={}, selected_alpha=model["alpha"],
                  validation_candidates=[dict(alpha=a, mae=m) for m,a,_ in candidates])
    offset = 0
    for name, count in zip(("train", "validation", "test"), counts):
        sx, sy = splits[name]
        report["splits"][name] = dict(flows=count, seeds=seeds[offset:offset+count],
            **metrics(sy, predict(model, sx)),
            constant_baseline=metrics(sy, np.full_like(sy, y.mean())))
        offset += count
    model["training"] = report
    args.model.parent.mkdir(parents=True, exist_ok=True)
    args.model.write_text(json.dumps(model, indent=2, allow_nan=False) + "\n")
    (args.work_dir / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
