#!/usr/bin/env python3
"""D-024: report both seed cohorts and measure the recommendation in series."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from scripts.run_sweep import (THREAD_ENV, load_configs, make_task, run_task,
                               source_hash, sweep_lock, write_json)

BASELINE = 'h6_sin_gate'
VARIANTS = {'free_sides_exact': 0.0, 'free_sides_001': .001, 'free_sides_01': .01}


def choose_configuration(summaries):
    eligible = []
    for name, tolerance in VARIANTS.items():
        accepted = True
        for summary in summaries:
            result = summary['comparisons'][name]['result']
            config = summary['configs'][name]
            baseline = summary['configs'][BASELINE]
            accepted &= bool(result and result['n'] == len(summary['seeds'])
                             and result['ci95_pp'] and result['ci95_pp'][0] > -.5
                             and result['mean_diff_pp'] >= -.1
                             # The operational requirement also says no mean KPI loss.
                             and result['mean_diff_pp'] >= 0
                             and config['violations_total'] == baseline['violations_total'] == 0
                             and config['hard_cap_hits_total'] == baseline['hard_cap_hits_total'] == 0
                             and config['deadline_cutoffs_count']['max'] == baseline['deadline_cutoffs_count']['max'] == 0)
        if accepted:
            eligible.append((tolerance, name))
    return max(eligible)[1] if eligible else BASELINE


def render_report(summaries, selected, timing, commands):
    lines = ['# D-024 · Laterales libres', '',
        'Un lateral ±x/±y está ocupado si una caja previa tiene contacto o separación normal <20 mm, '
        'con solape tangencial y vertical de longitud positiva con [z,z+h). El apoyo y cualquier caja '
        'con techo ≤z no cuentan. Una esquina no ocupa el lateral. El borde del palet es libre.', '',
        '`free_sides(candidate, placements, gap_mm=20)` es pura y devuelve 0–4. '
        'Se usan las dimensiones ya orientadas y el placement final tras settle.', '',
        'Defaults: `free_sides_tiebreak=False`, `free_sides_tolerance=0.0`. '
        'Los overrides viajan por llamada; no se mutan parámetros globales.', '',
        'Con el desempate activo, cada grupo se ancla en el mejor valor: '
        '`abs(best-value) <= tolerance * max(abs(best), abs(value))`. Cero exige empate exacto. '
        'Dentro del grupo manda el número de laterales; si también empata, se conserva el orden anterior. '
        'No se encadenan empates. Se mantienen los filtros físicos y de altura. '
        'El packer compara su objetivo gain−fragmentation+ajustes; greedy compara su score; '
        'H3 compara score acumulado tras número de colocaciones; H5/H6 comparan valor de estado/rollout. '
        'El beam conserva los laterales de la primera acción en su geometría original. '
        'Los rollouts heredan la misma configuración.', '',
        'Base: miembro `h6_sin_gate` de `configs/sweeps/h6_sin_gate.yaml`, sin cambiar sus overrides. '
        'KPI congelado v1.0.0. Pareada = variante−oficial, en pp; IC95 t de Student, unidad seed. '
        'Las dos tandas se informan por separado, sin ajustar el tamaño tras ver resultados.', '',
        'Adopción: mayor tolerancia con límite inferior IC95 >−0,5 pp y media ≥−0,1 pp en ambas tandas; '
        'además se exige media ≥0 para cumplir literalmente «el KPI medio NO puede bajar», '
        'cero violaciones y ausencia de cortes temporales. Si ninguna pasa, se mantiene desactivado.', '',
        '| Tanda | Config | KPI % | Δ pp [IC95] | 4 / 3 / 2 / 1 / 0 laterales (%) | Violaciones | Hard caps |',
        '|---|---|---:|---|---|---:|---:|']
    for title, summary in zip(('Referencia (10)', 'D-016 (20)'), summaries):
        for name in (BASELINE, *VARIANTS):
            row = summary['configs'][name]
            distribution = row['free_sides']
            if name == BASELINE:
                comparison = '—'
            else:
                c = summary['comparisons'][name]['result']
                comparison = f"{c['mean_diff_pp']:+.3f} [{c['ci95_pp'][0]:+.3f}; {c['ci95_pp'][1]:+.3f}]"
            sides = ' / '.join(f"{100*distribution['proportions'][str(n)]:.2f}" for n in (4,3,2,1,0))
            lines.append(f"| {title} | {name} | {100*row['kpi']['mean']:.3f} | {comparison} | {sides} | {row['violations_total']} | {row['hard_cap_hits_total']} |")
    lines += ['', 'Distribución medida inmediatamente antes de cada colocación, contra las cajas anteriores '
              'del mismo palet físico. Incluye palets END; los porcentajes agregan cajas, mientras '
              '`per_seed_proportions` en summary.json conserva media e IC por seed. '
              'No se mide sobre la geometría final, donde las vecinas futuras distorsionarían la maniobrabilidad.', '',
              f'Recomendación: **{selected}**. ' + (
                  'Ninguna tolerancia cumple todos los criterios; conservar el comportamiento oficial sin desempate.'
                  if selected == BASELINE else f"Activar con tolerancia **{VARIANTS[selected]:g}**."), '',
              '## Tiempo en un proceso', '',
              'Seeds 1001 y 1002, un proceso, un hilo BLAS, sin sweep concurrente. '
              'Se concatenan todas las duraciones individuales, incluidas decisiones sin plan.', '',
              f"{timing['count']} decisiones: media **{timing['mean_s']:.6f} s**, p95 **{timing['p95_s']:.6f} s**, "
              f"máximo {timing['max_s']:.6f} s; hard caps {timing['hard_cap_hits']}, cortes {timing['deadline_cutoffs_count']}. "
              f"D-007: **{'CUMPLE' if timing['passes_d007'] else 'NO CUMPLE'}** (media ≤1 s, p95 ≤3 s).", '',
              '## Reproducción', '', '```bash', 'export PYTHONPATH=$(pwd)/src:$(pwd)',
              'export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1',
              *commands, '```', '',
              'El smoke ejecuta toda la suite, incluidos los golden oficiales completos de seeds 1 y 42. '
              'El comparador conserva todos los placements y KPI preexistentes; normaliza solo los nuevos '
              'defaults y la telemetría aditiva ausentes en los fixtures anteriores a D-024. '
              'Los tests adicionales verifican las fronteras geométricas, el desempate sintético en packer '
              'y greedy/beam/H5/H6, prioridad de compactación, aislamiento, telemetría y dos seeds con False. '
              'Resultados y duraciones crudas permanecen en out/, sin añadirlos a Git.']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--replica', type=Path, required=True)
    parser.add_argument('--timing-out', type=Path, required=True)
    parser.add_argument('--doc', type=Path, default=ROOT / 'docs/autopilot/FREE_SIDES.md')
    args = parser.parse_args()
    summaries = [json.loads((path/'summary.json').read_text()) for path in (args.reference, args.replica)]
    assert len(summaries[0]['seeds']) == 10 and len(summaries[1]['seeds']) == 20
    assert not set(summaries[0]['seeds']) & set(summaries[1]['seeds'])
    assert summaries[0]['source_hash'] == summaries[1]['source_hash'] == source_hash()
    selected = choose_configuration(summaries)
    configs = load_configs(ROOT / 'configs/sweeps/free_sides.yaml')
    config = deepcopy(next(c for c in configs if c['name'] == selected))
    for key in THREAD_ENV:
        os.environ[key] = '1'
    head = subprocess.check_output(['git', '--no-pager', 'rev-parse', 'HEAD'], text=True).strip()
    rows = []
    with sweep_lock(retry_seconds=60):
        code_hash = source_hash()
        for seed in (1001, 1002):
            rows.append(run_task(make_task(config, seed, args.timing_out, head, code_hash)))
            print(f"[free-sides] timing {seed} complete", flush=True)
    import numpy as np
    durations = [d/1000 for row in rows for d in row['decision_timing']['durations_ms']]
    timing = dict(config=selected, seeds=[1001,1002], count=len(durations),
                  mean_s=float(np.mean(durations)), p95_s=float(np.percentile(durations,95)),
                  max_s=max(durations), hard_cap_hits=sum(r['hard_cap_hits'] for r in rows),
                  deadline_cutoffs_count=sum(r['deadline_cutoffs_count'] for r in rows), durations_s=durations)
    timing['passes_d007'] = (timing['mean_s'] <= 1 and timing['p95_s'] <= 3
                            and timing['hard_cap_hits'] == timing['deadline_cutoffs_count'] == 0)
    write_json(args.timing_out / 'timings.json', timing)
    replica_seeds = ','.join(map(str, summaries[1]['seeds']))
    commands = [f".venv/bin/python scripts/run_sweep.py --configs configs/sweeps/free_sides.yaml --seeds 10 --workers 15 --lock-retry-seconds 60 --name {args.reference.name} --baseline {BASELINE}",
                f".venv/bin/python scripts/run_sweep.py --configs configs/sweeps/free_sides.yaml --seed-list {replica_seeds} --workers 15 --lock-retry-seconds 60 --name {args.replica.name} --baseline {BASELINE}",
                f".venv/bin/python scripts/report_free_sides.py --reference {args.reference} --replica {args.replica} --timing-out {args.timing_out}",
                '.venv/bin/python -m pytest tests/test_free_sides.py tests/test_run_sweep.py -q',
                './scripts/smoke_offline.sh']
    args.doc.write_text(render_report(summaries, selected, timing, commands))
    print(json.dumps({k:v for k,v in timing.items() if k != 'durations_s'}, indent=2))


if __name__ == '__main__':
    main()
