# Paletizado Caótico (palca)

## Setup rápido (WSL / Linux)

1) Crear venv e instalar dependencias:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip wheel
pip install -r requirements.txt
pip install pytest
```

2) Regla de oro (ejecutar siempre con wrapper):
```bash
./scripts/dev_shell_cmd.sh <comando...>
```

Ejemplos:
```bash
./scripts/dev_shell_cmd.sh ./scripts/smoke_offline.sh
./scripts/dev_shell_cmd.sh python -m pytest -q
```

## VS Code
El repo incluye `.vscode/` con:
- intérprete por defecto: `.venv/bin/python`
- tareas: `palca: smoke_offline`, `palca: pytest -q`

## Smoke test (obligatorio antes de commit)
```bash
./scripts/dev_shell_cmd.sh ./scripts/smoke_offline.sh
```

## Workstation: parallel sweeps
Para acelerar iteraciones independientes (seeds/configs) en una workstation, usa `scripts/run_sweep.py` para lanzar varios `sim.run` en paralelo.
`run_sweep` se encarga de inyectar `--episode-seed` y de gestionar `--out` por ejecución para evitar colisiones. Usa `--dry-run` para ver los comandos.

Ejemplo:
```bash
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
PYTHONPATH=src ./.venv/bin/python scripts/run_sweep.py \
  --jobs 12 \
  --seeds 314,315,316 \
  --base-out-dir /tmp/palca-sweeps \
  --run-args "--excel data/Flujo_smoke_60.xlsx --model M1 --n_per_pallet 999999 --t_pick_place 14.0 --staging_cap 0 --policy palca --k 15 --stability-mode ratio+corners+settle --min-support 0.90 --overhang_mm 20 --force-destination 1 --continuous-pallets --arrival-mode immediate --time-budget-ms 900 --score-mode min_height_slack_then_gain --height-slack-mm 80 --micro-plan --micro-depth 3 --micro-width 8 --micro-topk 15"
```

Recordatorio:
- No commitear outputs (`out/`, `outputs/`, `logs/`); usar rutas locales temporales (`/tmp`) o `out/` solo localmente.

## Reglas Git (resumen)
- Cambios siempre en rama nueva: `feat/<tema>` o `fix/<tema>`
- No commitear: `out/`, `outputs/`, `logs/`, `.venv/`, `__pycache__/`
