#!/usr/bin/env python3
"""Serial H10 audit: official equivalence and all six tuning variant timings.

No process pool. Writes only below the explicit --out directory (use /tmp).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
# Set native thread counts before importing the simulation / numpy.
for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS'):
    os.environ[key] = '1'

from scripts.run_sweep import load_configs, make_task, run_task, source_hash, sweep_lock, write_json


def compare_official(dump_path, reference, seed):
    actual = json.loads(dump_path.read_text())['pallets']
    expected = json.loads((reference / str(seed) / 'dump.json').read_text())['pallets']
    assert actual == expected, f'official pallets differ: seed {seed}'
    return dict(pallets_identical=True,
                pallets_sha256=hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=1001)
    parser.add_argument('--reference', type=Path, default=Path(
        '/home/ingen/code/Paletizador/09_Software/paletizado-autopilot/out/sweeps/h6_sin_gate_ref/h6_sin_gate'))
    parser.add_argument('--official-dumps', type=Path, nargs=2, metavar=('SEED_50021', 'SEED_1'),
                        help='Reuse the two dumps produced by the disabled-default regression tests')
    args = parser.parse_args()
    configs = load_configs(ROOT / 'configs/sweeps/h10_tune.yaml')
    head = subprocess.check_output(['git', '--no-pager', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    code_hash = source_hash()
    results = []
    with sweep_lock(retry_seconds=1):
        if args.official_dumps:
            for seed, path in zip((50021, 1), args.official_dumps):
                row = dict(config='h6_sin_gate', seed=seed, reused_dump=str(path),
                           **compare_official(path, args.reference, seed))
                write_json(args.out / 'h6_sin_gate' / str(seed) / 'dump.json',
                           json.loads(path.read_text()))
                results.append(row)
                print(json.dumps(row), flush=True)
        tasks = ([] if args.official_dumps else [(configs[0], seed) for seed in (50021, 1)])
        tasks += [(c, args.seed) for c in configs[1:]]
        for config, seed in tasks:
            print(f"Running {config['name']}/{seed}", flush=True)
            task = make_task(config, seed, args.out, head, code_hash)
            result = run_task(task)
            row = dict(config=config['name'], seed=seed, kpi=result['kpi'],
                       mean_ms=result['decision_timing']['mean_ms'],
                       p95_ms=result['decision_timing']['p95_ms'],
                       hard_cap_hits=result['hard_cap_hits'],
                       deadline_cutoffs_count=result['deadline_cutoffs_count'],
                       n_violations=result['n_violations'], n_unplaced=result['n_unplaced'])
            assert result['hard_cap_hits'] == result['deadline_cutoffs_count'] == 0
            assert result['n_violations'] == result['n_unplaced'] == 0
            if config['name'] == 'h6_sin_gate':
                row.update(compare_official(Path(task['directory']) / 'dump.json', args.reference, seed))
            else:
                partitions = result['columns']['open_partitions']
                row['max_partition_selection_ms'] = max(p['selection_ms'] for p in partitions)
                row['partition_examples'] = partitions[:3]
                assert row['max_partition_selection_ms'] < 200
            results.append(row)
            write_json(args.out / 'h10_audit.json', results)
            print(json.dumps({k: v for k, v in row.items() if k != 'partition_examples'}), flush=True)
    most_expensive = max((r for r in results if r['config'] != 'h6_sin_gate'), key=lambda r: r['mean_ms'])
    print('Most expensive:', most_expensive['config'], most_expensive['mean_ms'], 'ms/decision', flush=True)
    assert most_expensive['mean_ms'] <= 1000, 'D-007 mean decision time exceeds 1 s'


if __name__ == '__main__':
    main()
