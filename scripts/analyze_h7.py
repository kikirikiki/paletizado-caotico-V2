#!/usr/bin/env python3
"""Reproduce H7 selection, pooled cost, determinism and rear-strip autopsy."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from palca.study.metric import aggregate_seeds, split_pallets


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n')


def check_summary(summary):
    for name, row in summary['configs'].items():
        for key in ('violations_total', 'hard_cap_hits_total'):
            if row[key] != 0:
                raise ValueError(f'{name}: {key}={row[key]}')
        if row['n_unplaced']['max'] != 0 or row['deadline_cutoffs_count']['max'] != 0:
            raise ValueError(f'{name}: unplaced or deadline cutoff')


def rear_strip(items, x_bounds=(-20, 1220), y_bounds=(425, 820)):
    """Exact upper envelope: empty ground means heightfield==0.

    Split on every footprint edge, integrate the max top over each cell. Also
    report ground without z=0 boxes, which can differ under bridging boxes.
    """
    x0, x1 = x_bounds
    y0, y1 = y_bounds
    clipped = []
    for p in items:
        a, b = max(x0, p['x_mm']), min(x1, p['x_mm']+p['length_mm'])
        c, d = max(y0, p['y_mm']), min(y1, p['y_mm']+p['width_mm'])
        if a < b and c < d:
            clipped.append((a, b, c, d, p['z_mm'], p['z_mm']+p['height_mm']))
    xs = sorted({x0, x1, *(x for p in clipped for x in p[:2])})
    ys = sorted({y0, y1, *(y for p in clipped for y in p[2:4])})
    empty = no_base = profile = 0.0
    for a, b in zip(xs, xs[1:]):
        for c, d in zip(ys, ys[1:]):
            covering = [p for p in clipped if p[0] < b and a < p[1] and p[2] < d and c < p[3]]
            area = (b-a)*(d-c)
            top = max((p[5] for p in covering), default=0)
            empty += area if top == 0 else 0
            no_base += area if not any(p[4] == 0 for p in covering) else 0
            profile += top*area
    area = (x1-x0)*(y1-y0)
    return dict(area_mm2=area, empty_mm2=empty, empty_fraction=empty/area,
                no_base_fraction=no_base/area, mean_profile_mm=profile/area)


def autopsy(folder, name):
    manifest = read(folder/'manifest.json')
    seeds = []
    pallets = []
    for seed in manifest['seeds']:
        directory = folder/name/str(seed)
        run, dump = read(directory/'run.json'), read(directory/'dump.json')
        params = run['params']
        overhang = params['overhang_mm']
        rows = [dict(seed=seed, dest=r.dest, index=r.index,
                     **rear_strip(r.items, (-overhang, 1200+overhang)))
                for r in split_pallets(run, dump) if r.closed]
        if not rows:
            raise ValueError(f'No closed pallets: {name}/{seed}')
        seeds.append(dict(seed=seed, n_closed=len(rows),
                          empty_fraction=float(np.mean([r['empty_fraction'] for r in rows])),
                          mean_profile_mm=float(np.mean([r['mean_profile_mm'] for r in rows]))))
        pallets.extend(rows)
    return dict(n_closed=len(pallets), by_seed=seeds, by_pallet=pallets,
                empty_fraction_pallet_mean=float(np.mean([r['empty_fraction'] for r in pallets])),
                no_base_fraction_pallet_mean=float(np.mean([r['no_base_fraction'] for r in pallets])),
                mean_profile_mm=float(np.mean([r['mean_profile_mm'] for r in pallets])),
                empty_fraction_seed_stats=aggregate_seeds([r['empty_fraction'] for r in seeds]))


def select():
    folder = ROOT/'out/sweeps/h7_tune'
    summary, manifest = read(folder/'summary.json'), read(folder/'manifest.json')
    configs = {c['name']: c for c in manifest['configs']}
    check_summary({'configs': {'h6_generic': summary['configs']['h6_generic']}})
    variants, rejected = [], {}
    for name, config in configs.items():
        if name == 'h6_generic':
            continue
        try:
            check_summary({'configs': {name: summary['configs'][name]}})
        except ValueError as exc:
            rejected[name] = str(exc)
        else:
            variants.append(config)
    if not variants:
        raise ValueError('No admissible H7 variant in tuning')
    variants.sort(key=lambda c: (-summary['configs'][c['name']]['kpi']['mean'],
                                c['overrides']['extra_candidates_max'], c['name']))
    winner = variants[0]
    result = dict(winner=winner['name'], config=winner, source_hash=summary['source_hash'],
                  ranking=[dict(name=c['name'], kpi=summary['configs'][c['name']]['kpi']['mean'])
                           for c in variants], rejected=rejected,
                  protocol='out/h7/preregistration.json',
                  selection_method='preregistered eligibility per variant; entire raw grid retained')
    # Selection is frozen before evaluating any reference seeds.
    write(ROOT/'out/h7/selection.json', result)
    for filename, entries in [('h7_eval.yaml', [configs['h6_generic'], winner]),
                              ('h7_cost.yaml', [winner])]:
        (ROOT/'configs/sweeps'/filename).write_text(yaml.safe_dump(entries, sort_keys=False))
    return result


def cost():
    selection = read(ROOT/'out/h7/selection.json')
    folder = ROOT/'out/sweeps/h7_cost'
    manifest, summary = read(folder/'manifest.json'), read(folder/'summary.json')
    if manifest['workers'] != 1:
        raise ValueError('Cost requires exactly one worker')
    check_summary(summary)
    durations = [t for seed in manifest['seeds'] for t in
                 read(folder/selection['winner']/str(seed)/'run.json')['metrics']['decision_timing']['durations_ms']]
    mean, p95 = float(np.mean(durations)), float(np.percentile(durations, 95))
    result = dict(n=len(durations), seeds=manifest['seeds'], workers=1, mean_ms=mean, p95_ms=p95,
                  max_ms=max(durations), admissible=mean <= 1000 and p95 <= 3000,
                  source_hash=summary['source_hash'])
    write(ROOT/'out/h7/cost.json', result)
    return result


def report():
    selection = read(ROOT/'out/h7/selection.json')
    winner = selection['winner']
    result = dict(selection=selection, cost=read(ROOT/'out/h7/cost.json'), evaluations={})
    a, b = [ROOT/'out/sweeps'/name for name in ('h7_eval_a', 'h7_eval_b')]
    ma, mb = read(a/'manifest.json'), read(b/'manifest.json')
    if ma['seeds'] != mb['seeds'] or ma['configs'] != mb['configs']:
        raise ValueError('Different evaluation designs')
    count = 0
    for name in ('h6_generic', winner):
        for seed in ma['seeds']:
            da, db = a/name/str(seed), b/name/str(seed)
            if read(da/'dump.json')['pallets'] != read(db/'dump.json')['pallets']:
                raise ValueError(f'Placement nondeterminism: {name}/{seed}')
            ka, kb = read(da/'kpi.json'), read(db/'kpi.json')
            # Exact counters and decisions, excluding measured durations.
            for key in ('kpi', 'pallet_utils', 'boxes_placed', 'n_closed',
                        'acc_violations_gap50', 'hard_cap_hits', 'n_violations'):
                if ka[key] != kb[key]:
                    raise ValueError(f'Nondeterminism: {name}/{seed}/{key}')
            for key in ('micro_plan_nodes_expanded', 'micro_plan_future_nodes',
                        'micro_plan_future_samples_completed'):
                if ka['micro_plan'][key] != kb['micro_plan'][key]:
                    raise ValueError(f'Node nondeterminism: {name}/{seed}/{key}')
            count += 1
    result['identical_pairs'] = count
    legacy_count = 0
    for seed in ma['seeds']:
        legacy = ROOT/'out/sweeps/h6_prior_eval/h6_generic'/str(seed)
        current = a/'h6_generic'/str(seed)
        if read(legacy/'dump.json')['pallets'] != read(current/'dump.json')['pallets']:
            raise ValueError(f'H6 off compatibility: seed {seed}')
        legacy_count += 1
    result['legacy_h6_identical_pairs'] = legacy_count
    for folder in (a, b, ROOT/'out/sweeps/h7_replica'):
        if not (folder/'summary.json').exists():
            continue
        summary = read(folder/'summary.json')
        check_summary(summary)
        if summary['source_hash'] != selection['source_hash']:
            raise ValueError('Source changed since tuning')
        result['evaluations'][folder.name] = dict(summary=summary,
            rear_strip={name: autopsy(folder, name) for name in ('h6_generic', winner)})
    comparison = result['evaluations']['h7_eval_a']['summary']['comparisons'][winner]['result']
    lo, hi = comparison['ci95_pp']
    result['replica_required'] = lo <= 0 <= hi or min(abs(lo), abs(hi)) <= .1
    write(ROOT/'out/h7/report.json', result)
    return dict(winner=winner, identical_pairs=count, comparison=comparison,
                cost=result['cost'], replica_required=result['replica_required'],
                report='out/h7/report.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('select', 'cost', 'report'))
    args = parser.parse_args()
    print(json.dumps(dict(select=select, cost=cost, report=report)[args.command](), indent=2))
