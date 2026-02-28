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

## Reglas Git (resumen)
- Cambios siempre en rama nueva: `feat/<tema>` o `fix/<tema>`
- No commitear: `out/`, `outputs/`, `logs/`, `.venv/`, `__pycache__/`

## Ablation runner
Ejecuta variantes de orientación y resume KPIs sin `--print` gigante:
```bash
PYTHONPATH=src ./.venv/bin/python scripts/ablation_runner.py --excel "data/Flujo rampas - Editado.xlsx" --outdir /tmp/palca-ablation
```
El runner guarda `<variant>.json`, `<variant>.log`, `summary.csv` y `summary.json` en `--outdir` (recomendado fuera del repo, por ejemplo `/tmp/...`).
Para resumir un JSON individual:
```bash
PYTHONPATH=src ./.venv/bin/python scripts/summarize_run.py --in /tmp/palca-ablation/gate400.json --csv /tmp/palca-ablation/gate400_summary.csv
```
