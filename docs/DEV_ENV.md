# Entorno de desarrollo (Paletizado Caótico)

## Regla de oro
En este repo, cualquier comando se ejecuta con el wrapper:

```bash
./scripts/dev_shell_cmd.sh <comando...>
```

Esto garantiza:
- venv activado (`.venv`)
- `PYTHONPATH` apuntando a `src`

## Ejemplos

Smoke:
```bash
./scripts/dev_shell_cmd.sh ./scripts/smoke_offline.sh
```

Pytest:
```bash
./scripts/dev_shell_cmd.sh python -m pytest -q
```

## VS Code
El repo incluye `.vscode/` con:
- intérprete por defecto: `.venv/bin/python`
- tareas: `palca: smoke_offline`, `palca: pytest -q`
