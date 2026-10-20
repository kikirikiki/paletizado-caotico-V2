#!/usr/bin/env python3
"""Measure the unchanged official H6 config in series, optionally with cProfile.

Outputs stay under out/. Compare complete placement dumps (only git provenance
is normalized) and every deterministic run_sweep KPI field against a reference.
"""
from __future__ import annotations

import argparse
import copy
import cProfile
import json
import os
from pathlib import Path
import pstats
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from scripts.run_sweep import (  # noqa: E402
    THREAD_ENV, load_configs, make_task, run_task, source_hash, sweep_lock, write_json,
)

# These fields describe execution/provenance, not the simulation's result.
# Operational params are compared in full in dump.json, which has no output paths.
OBSERVATIONAL_FIELDS = frozenset({
    'git_head', 'source_hash', 'input_hash', 'task_hash', 'params',
    'decision_timing', 'wall_time_s', 'worker_pid', 'thread_env',
})


def official_config():
    return next(c for c in load_configs(ROOT / 'configs/sweeps/h6_sin_gate.yaml')
                if c['name'] == 'h6_sin_gate')


def deterministic_kpi(result):
    value = {k: v for k, v in result.items() if k not in OBSERVATIONAL_FIELDS}
    for section, fields in {
        'micro_plan': {f'micro_plan_time_ms_{stat}' for stat in ('min', 'mean', 'max', 'p95')},
        'access_debt': {'candidate_mean_ms', 'candidate_p95_ms'},
    }.items():
        if section in value:
            value[section] = {k: v for k, v in value[section].items() if k not in fields}
    return value


def assert_equivalent(actual_dump, actual_kpi, expected_dump, expected_kpi):
    actual_dump, expected_dump = copy.deepcopy(actual_dump), copy.deepcopy(expected_dump)
    actual_kpi, expected_kpi = copy.deepcopy(actual_kpi), copy.deepcopy(expected_kpi)
    # Pre-D-024 goldens lack these additive defaults/telemetry. Nondefault
    # settings must still fail equality against the original configuration.
    for actual, expected in ((actual_dump, expected_dump), (actual_kpi, expected_kpi)):
        for key, default in (("free_sides_tiebreak", False), ("free_sides_tolerance", 0.0),
                             ("accessibility_tall_box_exemption", False), ("accessibility_top_clearance_mm", 0),
                             ("access_debt_exempt_delta_mm", 750), ("dead_headroom_weight", 0.0),
                             ("dead_headroom_min_mm", 250)):
            if key not in expected.get("params", {}) and key in actual.get("params", {}):
                assert actual["params"][key] == default
                del actual["params"][key]
    if "free_sides" not in expected_kpi:
        actual_kpi.pop("free_sides", None)
    for key in ("acc_violations", "acc_violations_base_rule"):
        if key not in expected_kpi and key in actual_kpi:
            assert actual_kpi.pop(key) == 0
    if "dead_headroom" not in expected_kpi and "dead_headroom" in actual_kpi:
        assert actual_kpi.pop("dead_headroom") == dict(
            enabled=False, weight=0.0, min_mm=250, selected_count=0,
            selected_dead_volume_mm3_mean=0.0, selected_penalty_mean=0.0)
    # HEAD changes when a patch is committed; never discard any placement field.
    assert dict(actual_dump, git_head=expected_dump['git_head']) == expected_dump
    assert json.loads(json.dumps(deterministic_kpi(actual_kpi))) == json.loads(
        json.dumps(deterministic_kpi(expected_kpi)))


def profile_table(profile, limit=25):
    stats = pstats.Stats(profile)
    rows = []
    for (filename, line, name), (primitive, calls, own, cumulative, callers) in stats.stats.items():
        try:
            filename = str(Path(filename).relative_to(ROOT))
        except ValueError:
            filename = Path(filename).name
        rows.append(dict(function=f'{filename}:{line}({name})', calls=calls,
                         own_s=own, cumulative_s=cumulative))
    return sorted(rows, key=lambda row: row['cumulative_s'], reverse=True)[:limit]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed-list', default='1001,1002')
    parser.add_argument('--profile-seed', type=int)
    parser.add_argument('--reference', type=Path, help='run_sweep directory with h6_sin_gate/<seed>')
    parser.add_argument('--compare-only', action='store_true', help='Compare existing --out and --reference')
    args = parser.parse_args()
    if args.compare_only:
        if args.reference is None:
            parser.error('--compare-only requires --reference')
        comparisons = []
        for expected in sorted((args.reference / 'h6_sin_gate').iterdir()):
            if not expected.is_dir():
                continue
            actual = args.out / 'h6_sin_gate' / expected.name
            read = lambda folder, name: json.loads((folder / name).read_text())
            assert_equivalent(read(actual, 'dump.json'), read(actual, 'kpi.json'),
                              read(expected, 'dump.json'), read(expected, 'kpi.json'))
            comparisons.append(dict(seed=int(expected.name), identical=True,
                                    kpi=read(actual, 'kpi.json')['kpi']))
        assert comparisons, 'No reference seeds found'
        write_json(args.out / 'equivalence.json', comparisons)
        print(json.dumps(comparisons, indent=2))
        return
    config = official_config()
    seeds = [int(s) for s in args.seed_list.split(',')]
    for key in THREAD_ENV:
        os.environ[key] = '1'
    head = subprocess.check_output(['git', '--no-pager', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    args.out.mkdir(parents=True, exist_ok=True)
    # Let the normal sweep runner evaluate just the official member of the YAML.
    import yaml
    (args.out / 'official.yaml').write_text(yaml.safe_dump([config], sort_keys=False))
    results = []
    with sweep_lock():
        code_hash = source_hash()
        for seed in seeds:
            task = make_task(config, seed, args.out, head, code_hash)
            profiler = cProfile.Profile() if seed == args.profile_seed else None
            if profiler:
                profiler.enable()
            result = run_task(task)
            if profiler:
                profiler.disable()
                profiler.dump_stats(str(args.out / f'{seed}.prof'))
                write_json(args.out / 'profile.json', profile_table(profiler))
            if args.reference:
                expected = args.reference / config['name'] / str(seed)
                actual = Path(task['directory'])
                assert_equivalent(json.loads((actual / 'dump.json').read_text()), result,
                                  json.loads((expected / 'dump.json').read_text()),
                                  json.loads((expected / 'kpi.json').read_text()))
            results.append(dict(seed=seed, decision_timing=result['decision_timing'],
                                hard_cap_hits=result['hard_cap_hits'],
                                deadline_cutoffs_count=result['deadline_cutoffs_count'],
                                profiled=profiler is not None))
            write_json(args.out / 'timings.json', results)
            print(seed, {k: v for k, v in result['decision_timing'].items() if k != 'durations_ms'},
                  f"hard_cap_hits={result['hard_cap_hits']}", flush=True)


if __name__ == '__main__':
    main()
