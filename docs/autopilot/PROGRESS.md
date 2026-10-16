# PROGRESS — autopiloto de compactidad (fuente única de verdad del estado)

Última actualización: 2026-10-16 ~15:40 CEST · Director: Claude Code · Ejecutor: Codex (`codex exec` en WSL, DECISIONS D-001/D-008)

## Cómo reanudar
0. **La versión vigente de este archivo está en la rama más alta del apilado.** Puntero fuera de git:
   `~/code/Paletizador/09_Software/autopilot_logs/CURRENT.md` (dice qué rama/worktree es la tope).
1. Leer este archivo entero, `docs/autopilot/DECISIONS.md` y `docs/autopilot/RESEARCH.md`.
2. Worktrees (WSL): **1** = `~/code/Paletizador/09_Software/paletizado-autopilot`, **2** = `.../paletizado-autopilot-2`.
   No usar el checkout principal (`paletizado-caotico-V2`, rama del usuario `study/gantry-orientations`).
3. Verificar: `git -C <worktree> status -sb`, `git log --oneline -10`; trabajos Codex en
   `~/code/Paletizador/09_Software/autopilot_logs/` (`<job>.exit` existe ⇒ terminado; `<job>.last.md` = resultado;
   el 1.er evento de `<job>.jsonl` lleva el thread id para `resume`).
4. Delegar: `bash scripts/autopilot/codex_job.sh <job> <modelo> <effort> <spec> [thread_id]` (CLI `~/.local/bin/codex` 0.163;
   modelos `gpt-6.1-sol` / `gpt-6-astra`; worktree 2 con `WT=<ruta>`). Codex NO commitea (D-008).
5. Continuar por **Siguiente acción**. No repetir nada marcado como completado.

## Objetivo vigente
- Fase 0 casi cerrada (falta B: techo offline). Fase 1 empezada (H1 adoptada).
- Metas T1/T2/T3: se fijan cuando esté B (gap = techo offline − baseline). Provisional: T3 = 1,5 × 61,89 = **92,84 %**.
- Configuración oficial: `arm6_4orient` (= `planar+stand_hw`, `accessibility_delta_mm=400`; D-004).

## Mejor resultado actual por configuración (KPI congelado v1.0.0: vol. real / 1240×840×2400, palets cerrados, 10 seeds de referencia)
| Config | KPI | Comando / carpeta | Estado |
|---|---|---|---|
| arm6_4orient (baseline oficial) | 61,89 % ± 1,32 · IC95 [60,95; 62,83] · 75 ms/dec (p95 155) | `run_sweep.py --configs configs/sweeps/baseline.yaml --seeds 10 --workers 15 --name baseline_v1 --baseline arm6_4orient` → out/sweeps/baseline_v1 (wt1) | verificado |
| **arm6_4orient + H1 (w0,25 b600) — MEJOR** | **64,70 % · IC95 [63,47; 65,92] · +2,81 pp [1,04; 4,57]** · 66 ms/dec (p95 146) | `run_sweep.py --configs configs/sweeps/h1_eval2.yaml --seeds 10 --workers 15 --name h1_eval2 --baseline arm6_4orient` → out/sweeps/h1_eval2 (wt2) | adoptada (D-009/D-011) |
| *PALANCA* H1 + micro_plan (d3 w8, corte por tiempo) | 70,90 % ± 1,64 · +9,01 pp [7,77; 10,25] vs baseline · 722 ms/dec (p95 911) | `run_sweep.py --configs configs/sweeps/lever_microplan_eval.yaml --seeds 10 --workers 15 --name lever_microplan_eval --baseline arm6_4orient` (wt2) | NO cuenta para T1 (micro_plan=False es restricción, D-012) y NO es determinista (8/10 seeds cambian al repetir) → H3 |
| gantry_4orient (contraste) | 66,92 % ± 2,26 (+5,03 pp) | baseline_v1 | informativo; H1 no actúa con delta 0 |
| arm6_planar (control) | 47,42 % | baseline_v1 | control |

## Cotas y palancas (Fase 0)
| Cota / palanca | Resultado | Fuente |
|---|---|---|
| Trivial (Σvol / V) | ≥ 6,716 palets → mínimo 7 | docs/COTAS.md (A) |
| Formal por palet (DFF + cortes horizontales, sin soporte/accesibilidad) | U* = 100 % flujo; 96,97 % familia dominante → no refuta T3 | docs/COTAS.md (A), `scripts/bound_formal.py` |
| Buffer k (política de producción) | k=30 +1,13 · k=75 +3,04 · **k=186 66,82 % (+4,93 pp)** (cortes 900 ms con k ≥ 75 → conservador) | out/sweeps/bound_buffer_k_v1 (wt1) |
| N palets abiertos (política de producción) | N=2 −1,98 · N=3 −5,73 · N=4 −5,96 pp: empeora con el scorer actual (no es cota) | out/sweeps/bound_open_pallets_v1 (wt1) |
| Techo offline clarividente (B) | **PENDIENTE** (job 04B murió por cuota de Codex) | — |

## Ramas y PRs (apilados, D-003)
| Rama | Base | Objetivo | PR | Estado |
|---|---|---|---|---|
| feat/parametros-congelados | feat/fifo-scorer-coherencia | parámetros, preset arm6_4orient, caras | #141 | abierto |
| feat/metrica-congelada | feat/parametros-congelados | métrica v1.0.0 + validador | #142 | abierto |
| feat/sweep-paralelo | feat/metrica-congelada | run_sweep + baseline oficial | #143 | abierto |
| feat/cotas-superiores (wt1) | feat/sweep-paralelo | cotas A + C (B pendiente) | — | commits 2483261 (C), 382ee4f (A); PR a abrir con A+C, B se añade después |
| feat/h1-deuda-accesibilidad (wt2, TOPE) | feat/cotas-superiores | H1 | #145 | abierto (rebase verificado: 20 KPIs idénticos) |
| feat/cotas-superiores | — | (PR #144 abierto con A + C; B pendiente en wt1) | #144 | abierto |

## Trabajos Codex
| Job | Spec | Modelo / effort | WT | Estado |
|---|---|---|---|---|
| 01_parametros, 02_metrica, 03_sweep | specs/01–03 | sol / high | 1 | terminados → #141–#143 |
| 04A_formal | specs/04A | astra / xhigh | 2 | terminado → 382ee4f |
| 04C_palancas | specs/04C | sol / high | 1 | terminado → 2483261 |
| 05_h1 (+05b resume) | specs/05 + autopilot_logs/05b_delta.md | sol / high | 2 | terminado (05b cortado por cuota; completado por el director, D-011) |
| **04B_offline** | autopilot_logs/04B_spec.md | astra / xhigh | 1 | **CORTADO por cuota de Codex** (14:4x). Dejó `src/palca/study/offline_search.py` sin terminar (sin trackear). Relanzar tras las 19:01 con resume del thread de `04B_offline.jsonl` |

**Codex sin cuota hasta las 19:01 CEST (2026-10-16).** Delegaciones pendientes, en orden:
1. 04B (resume del thread de `04B_offline.jsonl`, wt1, astra xhigh, ≤ 8 procesos).
2. **08 H3 búsqueda determinista** (wt2, rama nueva `feat/h3-busqueda-determinista` desde feat/h1-deuda-accesibilidad; astra high):
   mayor ganancia medida disponible (micro_plan sobre H1: +6,2 pp, pero no determinista y prohibido tal cual, D-012).
3. 07 H2 ranuras estrechas (spec reescrita tras RESEARCH §1ter; se evalúa encima de la mejor configuración vigente).
4. 06 orientación canto (palanca PLAN_B + contraste gantry 6 orientaciones reales).

## Sweeps lanzados
| Sweep | WT | Estado |
|---|---|---|
| baseline_v1 | 1 | hecho |
| bound_buffer_k_v1, bound_open_pallets_v1 | 1 | hechos |
| h1_tune (130), h1_eval (30) | 2 | hechos (Codex) |
| h1_tune2 (170), h1_tune3 (30), h1_eval2 (20) | 2 | hechos (director, D-011) |

## Hallazgos ya establecidos (no rehacer)
- Flujo: 186 cajas, 111 referencias, Σ vol 16,79 m³; envolvente real 1240 × 840 (el 820 histórico era un error).
- Causa dominante de pérdida (RESEARCH §1bis): 36 % espacio libre sobre el perfil (torres), 2 % huecos internos, 18,7 % del
  suelo sin cubrir; la regla de accesibilidad (400 mm) deja zonas bajas inaccesibles → H1 la ataca (+2,8 pp).
- `stand_hl` (PR #139) duplica `stand_hw` (D-004). Gate stand_hw no es palanca. Simulador determinista.
- H1: banda 200 mm empeora; banda ≥ 445 mejora; paisaje plano con ruido ~2 pp/seed.

## Siguiente acción
1. (hecho) PR #144 (cotas A+C) y PR #145 (H1). Autopsia con H1 → RESEARCH §1ter → spec 07 reescrita (H2 ranuras estrechas).
2. (≥ 19:01) Resume 04B en wt1; crear `feat/h3-busqueda-determinista` en wt2 y lanzar 08 (astra, high).
3. Cuando esté B: tabla de headroom en docs/COTAS.md (wt1), T1/T2/T3 aquí, push a #144.

## Registro de técnicas cerradas
| Técnica | Resultado (seeds de referencia) | Decisión |
|---|---|---|
| Gate stand_hw 800/1600/2400 | +0,5 / −1,0 / −0,5 pp, n.s. | descartada (no es palanca) |
| H1 deuda de accesibilidad (w0,25, b600) | +2,81 pp [1,04; 4,57], 0 violaciones, sin coste de tiempo | **adoptada** |
| Re-ajuste de mandos existentes con H1 (height_slack 0–500, score_mode, heurística 2D, tower_z) | ajuste: todos ≤ H1; heurística y tower_z inertes en heightfield (KPI idéntico) | cerrada (out/sweeps/h1_knobs_tune, wt2) |
| micro_plan sobre H1 (palanca) | +6,2 pp sobre H1 en referencia, no determinista | palanca [REVISAR] D-012 → origina H3 |
