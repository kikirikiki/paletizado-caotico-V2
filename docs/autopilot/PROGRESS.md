# PROGRESS — autopiloto de compactidad (fuente única de verdad del estado)

Última actualización: 2026-10-16 · Director: Claude Code · Ejecutor: Codex (`codex exec` en WSL, ver DECISIONS D-001)

## Cómo reanudar
1. Leer este archivo entero y `docs/autopilot/DECISIONS.md`.
2. Worktree del autopiloto: `~/code/Paletizador/09_Software/paletizado-autopilot` (WSL). No usar el checkout
   principal (`paletizado-caotico-V2`, rama del usuario `study/gantry-orientations`).
3. Verificar: `git -C <worktree> status -sb`, `git log --oneline -10`, trabajos Codex en
   `~/code/Paletizador/09_Software/autopilot_logs/` (`<job>.exit` existe ⇒ terminado; `<job>.last.md` = resultado).
4. Continuar por **Siguiente acción**. No repetir nada marcado como completado.

## Objetivo vigente
- Fase actual: **2bis — parámetros congelados** (antes de Fase 0).
- Metas T1/T2/T3: se fijan al terminar la Fase 0 (pendiente de las cotas).
- Configuración oficial: `arm6_4orient` (= `planar+stand_hw`, `accessibility_delta_mm=400`; D-004).

## Mejor resultado actual por configuración (KPI oficial D-005: vol. real / 1240×820×2400, solo palets cerrados)
| Config | KPI medio | Fuente | Estado |
|---|---|---|---|
| arm6_4orient (baseline) | 63,4 % (sd entre seeds 1,35 pp; 101 palets) | exploración rápida del director, 10 seeds de referencia | provisional, se re-mide con la métrica congelada |
| gantry_4orient (contraste) | 68,5 % (sd 2,31 pp; 95 palets) | ídem | informativo |

Comando provisional (exploratorio, fuera del repo): runs de `run_simulation` con `BASE_PARAMS` del estudio gantry
+ `dump_placements_path`, partición de placements por `continuous_pallet_sequence`, último palet (END) excluido.
Se sustituirá por `scripts/run_sweep.py` + métrica congelada.

## Ramas y PRs
| Rama | Base | Objetivo | PR | Estado |
|---|---|---|---|---|
| feat/parametros-congelados | feat/fifo-scorer-coherencia | PARAMETROS.md, preset arm6_4orient, test de caras | — | en curso (Codex) |

## Técnica en curso
- Paso: delegación 01 (`docs/autopilot/specs/01_parametros_congelados.md`) a Codex.
- Hipótesis: n/a (infraestructura).

## Trabajos Codex en background
| Job | Spec | Effort | Estado |
|---|---|---|---|
| 01_parametros | specs/01_parametros_congelados.md | high | lanzado |

## Sweeps lanzados
(ninguno todavía)

## Hallazgos ya establecidos (no rehacer)
- Flujo canónico: 186 cajas, 111 dimensiones distintas, Σ vol = 16,79 m³ → cota trivial 6,88 palets de
  1240×820×2400 (7,29 de 1200×800×2400). L ≥ W en todas las cajas.
- Línea base: ~80 ms por decisión, ~15 s por flujo completo; los palets cierran casi siempre por `CLOSE_HEIGHT_FULL`
  (z máx. 2155–2400 mm): la pérdida está en huecos internos, no en altura sin usar.
- `stand_hl` (PR #139) duplica la geometría de `stand_hw` (D-004).

## Siguiente acción
Esperar al job `01_parametros`; revisar diff, smoke y PARAMETROS.md; corregir; push + PR (base feat/fifo-scorer-coherencia).
En paralelo: redactar spec 02 (métrica congelada) e investigación bibliográfica de Fase 1.

## Registro de técnicas cerradas
(ninguna todavía)
