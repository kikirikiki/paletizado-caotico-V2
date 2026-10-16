# PROGRESS — autopiloto de compactidad (fuente única de verdad del estado)

Última actualización: 2026-10-16 · Director: Claude Code · Ejecutor: Codex (`codex exec` en WSL, ver DECISIONS D-001/D-008)

## Cómo reanudar
1. Leer este archivo entero, `docs/autopilot/DECISIONS.md` y `docs/autopilot/RESEARCH.md`.
2. Worktree del autopiloto: `~/code/Paletizador/09_Software/paletizado-autopilot` (WSL). No usar el checkout
   principal (`paletizado-caotico-V2`, rama del usuario `study/gantry-orientations`).
3. Verificar: `git -C <worktree> status -sb`, `git log --oneline -10`, trabajos Codex en
   `~/code/Paletizador/09_Software/autopilot_logs/` (`<job>.exit` existe ⇒ terminado; `<job>.last.md` = resultado).
4. Delegar: `bash scripts/autopilot/codex_job.sh <job> <modelo> <effort> <spec>` (CLI `~/.local/bin/codex` 0.163;
   modelos `gpt-6.1-sol` / `gpt-6-astra`). Codex NO commitea (D-008): revisar diff, smoke y commitear a mano.
5. Continuar por **Siguiente acción**. No repetir nada marcado como completado.

## Objetivo vigente
- Fase actual: **2bis completada → Fase 0** (métrica congelada, sweep, cotas).
- Metas T1/T2/T3: se fijan al terminar la Fase 0 (pendiente de las cotas).
- Configuración oficial: `arm6_4orient` (= `planar+stand_hw`, `accessibility_delta_mm=400`; D-004).

## Mejor resultado actual por configuración (KPI oficial D-005: vol. real / 1240×840×2400, solo palets cerrados)
| Config | KPI medio | Fuente | Estado |
|---|---|---|---|
| arm6_4orient (baseline) | 61,9 % (101 palets, 10 seeds) | exploración rápida del director | provisional, se re-mide con la métrica congelada (spec 03) |
| gantry_4orient (contraste) | 66,8 % (95 palets) | ídem | informativo |

## Ramas y PRs (apilados, D-003)
| Rama | Base | Objetivo | PR | Estado |
|---|---|---|---|---|
| feat/parametros-congelados | feat/fifo-scorer-coherencia | PARAMETROS.md, preset arm6_4orient, test de caras, perfil del flujo | — | revisado, commit + PR en curso |
| feat/metrica-congelada | feat/parametros-congelados | métrica congelada + validador físico | — | spec 02 lista |
| feat/sweep-paralelo | feat/metrica-congelada | run_sweep.py + baseline re-medido | — | spec 03 lista |
| feat/cotas-superiores | feat/sweep-paralelo | cota formal, offline, buffer k, N palets | — | spec 04 lista |

## Trabajos Codex
| Job | Spec | Modelo / effort | Estado |
|---|---|---|---|
| 01_parametros | specs/01_parametros_congelados.md | gpt-6.1-sol / high | terminado, revisado |

## Sweeps lanzados
(ninguno todavía)

## Hallazgos ya establecidos (no rehacer)
- Flujo canónico: 186 cajas, 111 referencias, 35 familias a 25 mm, Σ vol = 16,79 m³ → cota trivial 6,72 palets de
  1240×840×2400. Referencia más frecuente 605×445×350 (16).
- Envolvente real del código: **1240 × 840** (el 1240×820 histórico era un error).
- Línea base: ~80–120 ms por decisión, 15–23 s por flujo; los palets cierran casi siempre por `CLOSE_HEIGHT_FULL`.
- **Causa dominante de la pérdida (RESEARCH §1bis):** 36 % de la envolvente es espacio libre sobre el perfil
  (torres), solo 2 % son huecos internos; 18,7 % del suelo queda sin cubrir. La regla de accesibilidad (delta 400) deja
  zonas bajas inaccesibles para siempre.
- `stand_hl` (PR #139) duplica la geometría de `stand_hw` (D-004).
- Palets llenos del operario real ≈ 74–76 % equivalentes (si altura ≤ 2400; no verificado).

## Siguiente acción
1. Commit del PR 1 (smoke antes), push y `gh pr create --base feat/fifo-scorer-coherencia`.
2. Crear `feat/metrica-congelada` y lanzar job 02 (`gpt-6.1-sol`, high).
3. Mientras: preparar la división de la spec 04 en jobs 04A (formal, astra xhigh), 04B (offline, astra xhigh), 04C (palancas, sol high).

## Registro de técnicas cerradas
(ninguna todavía)
