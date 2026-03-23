# AGENTS.md — Paletizado caótico (workflow y reglas)

## Objetivo
Optimizar y desplegar un motor de paletizado autónomo industrial. Cambios pequeños, reproducibles, con smoke test.

## Rama base
La rama base del repo es: `feature/stability-controls` (NO `main`).

## Git workflow (obligatorio)
- Crear rama nueva por tarea:
  - `git checkout -b feat/<tema>` o `git checkout -b fix/<tema>`
- Commits pequeños y descriptivos.
- Antes de cada commit: ejecutar `./scripts/smoke_offline.sh`
- Push: `git push -u origin <rama>`
- PR: si `gh` funciona, `gh pr create --fill`; si no, dejar la rama pusheada y pedir al usuario crear PR desde GitHub UI.

## No commitear (prohibido)
- `out/`, `outputs/`, `logs/`, `.venv/`, `__pycache__/`

## Estilo de trabajo
- Minimizar refactors grandes. Preferir cambios “mínimos y seguros”.
- Overrides/configs en runtime: NO mutar estado global (usar copias/effective_params por decisión).
- Añadir tests unitarios para lógica nueva.
- Añadir métricas/telemetría en JSON cuando aplique.

## Robustez terminal / “cuelgues”
- Evitar paginadores: usar siempre `git --no-pager ...` o `GIT_PAGER=cat`.
- Si un comando parece colgado, comprobar primero si está en el paginador (`less`). Salir con `q`.
- Si `gh` o comandos de red se cuelgan:
  - abortar (Ctrl+C),
  - reportar el punto exacto,
  - continuar sin red: inspección local y preparar commits,
  - pedir al usuario que cierre/cree PR desde la UI.

## Definition of Done (DoD)
- `./scripts/smoke_offline.sh` OK.
- Tests nuevos OK.
- Sin archivos prohibidos commiteados.
- Report final:
  1) lista de archivos cambiados
  2) comandos ejecutados
  3) cómo reproducir (incluye smoke)
