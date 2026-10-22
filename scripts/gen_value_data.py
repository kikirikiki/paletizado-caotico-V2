#!/usr/bin/env python3
"""Generate resumable G1 synthetic episodes outside the worktree.

OMP_NUM_THREADS is set before NumPy imports, including in spawned workers.
Official roots are not exposed; epsilon exploration and root scalar scores are
available only for greedy. NPZ arrays contain successful pre-placement decisions
on closed pallets; censored_states counts discarded final-pallet decisions.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import time

os.environ["OMP_NUM_THREADS"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
# Only these modules determine the episodes; other learning modules (value_net.py) and this
# script's own text are recorded but do not block resuming (git_head is recorded per run).
GENERATION_MODULES = ("__init__.py", "episode.py", "state_encoding.py", "synthetic_flows.py")
SCRIPT_KEY = "scripts/gen_value_data.py"


def _compatible(previous, identity):
    for key, value in identity.items():
        old = previous.get(key)
        if key == "source_sha256":
            old = {k: h for k, h in (old or {}).items() if Path(k).name in GENERATION_MODULES}
        if key == "configuration_sha256":
            old = {k: h for k, h in (old or {}).items() if k != SCRIPT_KEY}
            value = {k: h for k, h in value.items() if k != SCRIPT_KEY}
        if old != value:
            return False
    return True


def _worker(task):
    os.environ["OMP_NUM_THREADS"] = "1"
    import numpy as np
    from palca.learning.episode import run_episode

    seed, args = task
    result = run_episode(seed, args["prior"], args["policy"], args["epsilon"])
    path = Path(args["out"]) / f'ep_{args["prior"]}_{seed}.npz'
    temporary = path.with_suffix(".npz.tmp")
    try:
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, **result)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return len(result["util_final"])


def _atomic_json(path, data):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=["official", "greedy"], required=True)
    parser.add_argument("--prior", choices=["generic", "families", "mix"], required=True)
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--episodes", type=int, required=True)
    parser.add_argument("--workers", type=int, default=15)
    parser.add_argument("--epsilon", type=float, default=0.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out = args.out.expanduser().resolve()
    if args.out.is_relative_to(ROOT):
        parser.error("--out must be outside the repository worktree")
    if args.episodes < 1 or args.workers < 1 or not 0 <= args.epsilon <= 1:
        parser.error("episodes/workers must be positive and epsilon in [0, 1]")
    if args.policy == "official" and args.epsilon:
        parser.error("official roots are not exposed; epsilon is greedy-only")
    import numpy as np
    from palca.learning.state_encoding import ENCODING_VERSION

    arguments = dict(vars(args), out=str(args.out))
    sources = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted((ROOT / "src/palca/learning").glob("*.py"))
               if p.name in GENERATION_MODULES}
    config_paths = [ROOT / "configs/sweeps/pulmon_tune.yaml", Path(__file__).resolve(),
                    ROOT / "src/palca/study/params.py"]
    config_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in config_paths}
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    identity = dict(encoding_version=ENCODING_VERSION, source_sha256=sources,
                    configuration_sha256=config_hashes,
                    policy=args.policy, prior=args.prior, epsilon=args.epsilon)
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / "manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if previous is not None and not _compatible(previous, identity):
        parser.error("Output manifest is incompatible with this source/configuration/policy")
    if previous is None and any(args.out.glob("ep_*.npz")):
        parser.error("Existing episodes have no provenance manifest")
    for stale in args.out.glob("*.npz.tmp"):
        stale.unlink()
    manifest = dict(identity, git_head=previous.get("git_head", head) if previous else head,
                    arguments=arguments, runs=previous.get("runs", []) if previous else [])
    _atomic_json(manifest_path, manifest)
    seeds = range(args.seed_start, args.seed_start + args.episodes)
    tasks = [(seed, arguments) for seed in seeds
             if not (args.out / f"ep_{args.prior}_{seed}.npz").exists()]
    started = time.perf_counter()
    generated_states = 0
    with ProcessPoolExecutor(max_workers=args.workers,
                             mp_context=multiprocessing.get_context("spawn")) as pool:
        for states in pool.map(_worker, tasks):
            generated_states += states
    seconds = time.perf_counter() - started
    totals = dict(episodes=0, states=0, closed_pallets=0, censored_states=0)
    for path in sorted(args.out.glob(f"ep_{args.prior}_*.npz")):
        with np.load(path, allow_pickle=False) as episode:
            totals["episodes"] += 1
            totals["states"] += len(episode["util_final"])
            totals["closed_pallets"] += int(episode["closed_pallets"])
            totals["censored_states"] += int(episode["censored_states"])
    run = dict(arguments=arguments, git_head=head, generated_episodes=len(tasks), skipped=args.episodes-len(tasks),
               generated_states=generated_states, seconds=seconds,
               states_per_hour=generated_states * 3600 / seconds,
               states_per_hour_per_worker=generated_states * 3600 / seconds / args.workers)
    manifest["runs"].append(run)
    elapsed = sum(r["seconds"] for r in manifest["runs"])
    manifest["totals"] = dict(totals, seconds=elapsed,
                               states_per_hour=totals["states"] * 3600 / elapsed)
    _atomic_json(manifest_path, manifest)
    print(json.dumps(dict(totals=manifest["totals"], run=run), sort_keys=True))


if __name__ == "__main__":
    main()
