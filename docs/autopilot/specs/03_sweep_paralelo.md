<task>
Rama: feat/sweep-paralelo (ya creada desde feat/metrica-congelada; worktree
/home/ingen/code/Paletizador/09_Software/paletizado-autopilot). Objetivo único: infraestructura de sweeps
paralelos reproducibles que mida TODO experimento con la métrica congelada y el validador físico.
Contexto: parámetros en `palca.study.params` (presets, seeds de referencia); KPI en `palca.study.metric`;
validador en `palca.study.validate`. Workstation de 16 núcleos; nunca dos sweeps de 15 workers a la vez.
</task>

<deliverables>
1. Telemetría aditiva de tiempo por decisión en `PolicyPackerScheduler` (o donde se decide cada caja): lista de
   duraciones en ms de cada llamada de decisión, exportada en el payload (`metrics.decision_timing` con n, mean_ms,
   p50_ms, p95_ms, max_ms). Sin cambiar decisiones.
2. `scripts/run_sweep.py --configs <yaml> [--seeds N | --seed-list a,b,c] [--workers 15] [--name <sweep>] [--baseline <config>]`:
   - YAML: lista de configs `{name, preset, overrides: {...}, role: official|contrast|control|bound}`; overrides se
     validan con `preset_params`. `--seeds N` = primeras N seeds de `REFERENCE_SEEDS`; si N > 10, genera seeds
     adicionales deterministas y documentadas.
   - Un proceso por (config, seed) con `ProcessPoolExecutor`, `OMP_NUM_THREADS=MKL_NUM_THREADS=OPENBLAS_NUM_THREADS=1`
     fijados ANTES de crear el pool, sin estado global compartido, semilla fija por tarea.
   - Por tarea: `run_simulation(..., dump_placements_path=...)`; guarda `out/sweeps/<name>/<config>/<seed>/{run.json,dump.json,kpi.json}`;
     kpi.json = `run_kpi` + violaciones del validador + timing + nº cajas no colocadas (186 − colocadas) + wall time.
   - Reanudable: si `kpi.json` existe y el hash de (params, git HEAD del código) coincide, no repite la tarea.
   - Bloqueo: `flock` sobre `/tmp/palca_sweep.lock` para impedir dos sweeps simultáneos (error claro si está cogido).
   - Resumen `out/sweeps/<name>/summary.json` y `summary.md`: por config: KPI medio ± sd ± IC95 (unidad = seed),
     kpi_nominal, palets totales/cerrados, cajas no colocadas, violaciones, ms/decisión (mean, p95), wall time;
     y comparaciones pareadas contra `--baseline` (diferencia en pp, IC95, significativo sí/no).
     Incluye en el resumen el comando exacto, git HEAD y fecha.
3. `scripts/summarize_sweep.py out/sweeps/<name>` (regenera el resumen sin re-ejecutar).
4. `configs/sweeps/baseline.yaml`: `arm6_4orient` (official), `gantry_4orient` (contrast), `arm6_planar` (control).
5. Tests `tests/test_run_sweep.py`: parsing del YAML, override inválido → error, reanudación (no re-ejecuta),
   lock, resumen con KPIs sintéticos; un test de integración pequeño con `data/Flujo_smoke_60.xlsx`, 2 seeds, 2 workers.
6. Ejecuta de verdad: `python scripts/run_sweep.py --configs configs/sweeps/baseline.yaml --seeds 10 --workers 15 --name baseline_v1 --baseline arm6_4orient`
   y pega `summary.md` en tu respuesta. Si aparecen violaciones del validador en palets reales, NO lo relajes: repórtalas.
</deliverables>

<completeness_contract>
Hecho = tests en verde; smoke OK; `out/sweeps/baseline_v1/summary.md` generado con 3 configs × 10 seeds, KPI con IC95.
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + `.venv/bin/python -m pytest -q tests/test_run_sweep.py`.
Si fallan, corrige y repite.
</verification_loop>

<action_safety>
Solo los archivos necesarios; la única modificación del simulador es la telemetría aditiva de tiempos. No modifiques
data/*.xlsx ni `palca/study/metric.py`. No commitees out/ (ni outputs/, logs/, results/, .venv, __pycache__/);
git add por ruta explícita. NO hagas commits (el sandbox no puede escribir en .git): deja los cambios en el working tree; el director revisa y commitea. No hagas push ni abras PR.
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos ejecutados con resultado, supuestos, y el summary.md del sweep baseline_v1.
</compact_output_contract>
