# PROGRESS — autopiloto de compactidad (fuente única de verdad del estado)

Última actualización: 2026-10-20 ~13:30 CEST · Director: Claude Code · Ejecutor: Codex (`codex exec` en WSL, DECISIONS D-001/D-008)

> **REGLA DEL USUARIO (2026-10-17, D-020; umbral subido al 80 % el 2026-10-20): si el uso semanal de Claude ("Weekly · all models",
> herramienta `mcp__ccd_session_mgmt__get_usage`) llega al 80 %, PAUSAR el autopiloto hasta que el usuario diga que continúe.** Comprobarlo al
> reanudar y antes de cada delegación a Codex, sweep o tarea larga. En pausa: no lanzar trabajo nuevo, dejar terminar lo que ya
> corre, registrar estado aquí y avisar al usuario. La pausa NO es una línea `PARADA:` (esa queda reservada a §9).

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
- **Fase 0 cerrada** (docs/COTAS.md): techo offline verificado **81,45 %** (columnas contiguas, reproducible) → gap **19,56 pp** (D-013).
- **Metas vigentes:** **T1 ≥ 71,67 %** · **T2 ≥ 79,49 %** (o ≥ 80 % absoluto con palancas justificadas, solo en arm6_4orient) · **T3 ≥ 92,84 %**.
  (Primera fijación con techo 80,40: T1 71,15 / T2 78,55.)
- **Respuestas del usuario (2026-10-17):** gate eliminado si no crea torres → verificado, eliminado (D-021); el robot solo ve las 15 cajas
  de la cinta → H5/H6 cumplen (test de fuga), H3 sola y micro_plan por tiempo tenían fuga (D-022); holgura de 2 cm → pendiente de
  confirmar si las cajas pueden ir pegadas (D-023).
- **T1 ALCANZADO sin condiciones** (H5/H6 solo usan información visible).
- **MEJOR ACTUAL: H1+H3+H5+H6 sin gate = 77,06 % ± 0,90 (10 seeds de referencia; +3,03 [1,72; 4,35] vs H6 con gate); réplica 20 seeds
  +2,62 [1,52; 3,71] → 77,6 % del gap.** Faltan 2,4 pp para T2 (79,49).
- **2026-10-20: Codex vuelve a tener cuota. Ciclo lateral n.º 2 (D-025):** el 72 % de la pérdida (14,8 pp de la envolvente) son
  pozos profundos de acceso; la regla de 400 mm se mide desde la base de la caja e ignora su altura. En marcha: (a) reajuste de H1
  sobre h6_sin_gate (sweep `h1_retune_h6`, wt2 en detached b05704c, seeds de ajuste 1001–1010); (b) Codex job `18_pozos`
  (spec autopilot_logs/18_spec.md, wt1, rama `feat/pozos-acceso`): palanca R7 `accessibility_tall_box_exemption` + técnica H8
  altura muerta. Pregunta al usuario pendiente sobre R7 [REVISAR].
- **Laterales libres (D-024): NO adoptado** (desempate exacto −1,33 pp significativo en referencia); la oficial ya tiene 94,7 % de
  cajas con 3–4 laterales libres y 0 % con 0. Rama feat/laterales-libres (borrador).
- **Tiempo resuelto (feat/h6-rapido):** optimización sin cambiar decisiones (KPI y dumps idénticos 10/10): media 1,156 → 0,957 s,
  p95 2,36 s → cumple D-007. Siguiente: laterales libres como desempate (D-024, spec autopilot_logs/17_spec.md).
- (histórico) **Problema abierto:** sin gate tarda 1,12 s/decisión de media en 1 proceso (p95 2,52 s): incumple D-007 (media ≤ 1 s). Bajar el
  presupuesto de nodos no ayuda (B35 1,02 s, B45 1,11 s, KPI de ajuste 76,2 / 77,1 / 76,3 con B35/45/57). Siguiente: optimizar el código
  sin cambiar decisiones (bit a bit) hasta ≤ 1 s (Codex). Config vigente: `h6_sin_gate` de configs/sweeps/h6_sin_gate.yaml.
- Descartadas desde el ciclo lateral D-019: H7 (PR 153), secuenciación (PR 154), palets por familia (−0,74 con H1; rama
  feat/palanca-palets-familia en wt2, PR pendiente). Contador anti-estancamiento: 3 → toca ciclo lateral n.º 2 tras la optimización.
- Fase 1 en curso.
- Configuración oficial: `arm6_4orient` (= `planar+stand_hw`, `accessibility_delta_mm=400`; D-004).

## Mejor resultado actual por configuración (KPI congelado v1.0.0: vol. real / 1240×840×2400, palets cerrados, 10 seeds de referencia)
| Config | KPI | Comando / carpeta | Estado |
|---|---|---|---|
| arm6_4orient (baseline oficial) | 61,89 % ± 1,32 · IC95 [60,95; 62,83] · 75 ms/dec (p95 155) | `run_sweep.py --configs configs/sweeps/baseline.yaml --seeds 10 --workers 15 --name baseline_v1 --baseline arm6_4orient` → out/sweeps/baseline_v1 (wt1) | verificado |
| **H1+H3+H5+H6 SIN GATE (D-021) — MEJOR** | **77,06 % ± 0,90 · IC95 [76,42; 77,71]; réplica 20 seeds 76,21 % (+2,62 [1,52; 3,71] vs con gate)** · 1,12 s/dec en 1 proceso (incumple D-007 por 0,12 s) | `run_sweep.py --configs configs/sweeps/h6_sin_gate.yaml --seeds 10 --workers 15 --name h6_sin_gate_ref --baseline h6_generic` y `h6_sin_gate_replica` (wt1) | 77,6 % del gap; pendiente optimizar tiempo |
| H1+H3+H5+H6 rollouts estocásticos, prior genérico (6 futuras, 1 muestra, 2 hojas, B=57), con gate | **74,03 % ± 1,36 (10 seeds de referencia); vs H5 +1,99 [1,23; 2,75]; réplica D-016 +1,27 [0,17; 2,37]** · ~716 ms/dec en 1 proceso (p95 ~1,5 s) | `run_sweep.py --configs configs/sweeps/h6_prior_eval.yaml --seeds 10 --workers 15 --name h6_prior_eval --baseline h5_rollout_l4` (config h6_generic) y `h6_prior_replica` (wt2) | **65,6 % del gap** (D-017: prior sin información del flujo) |
| arm6_4orient + H1 (w0,25 b600) | **64,70 % · IC95 [63,47; 65,92] · +2,81 pp [1,04; 4,57]** · 66 ms/dec (p95 146) | `run_sweep.py --configs configs/sweeps/h1_eval2.yaml --seeds 10 --workers 15 --name h1_eval2 --baseline arm6_4orient` → out/sweeps/h1_eval2 (wt2) | adoptada (D-009/D-011) |
| **H1 + H3 + H5 valor de estado por rollout (4 hojas, 25 nodos) — MEJOR (aparte, D-012)** | **72,04 % ± 1,05 (10 seeds de referencia); vs H1+H3 +1,11 [−0,00; 2,21]; réplica preregistrada en 20 seeds nuevas +2,12 [1,08; 3,16]** · 437 ms/dec en 1 proceso (p95 911) · determinista | `run_sweep.py --configs configs/sweeps/h5_eval.yaml --seeds 10 --workers 15 --name h5_eval_a --baseline arm6_4orient` y `h5_replica` (D-016) (wt2) | **54,8 % del gap → T1 ALCANZADO (condicionado a D-012)** |
| H1 + H3 búsqueda determinista (n100 d3 w8) (reportada aparte, D-012) | **70,94 % ± 1,50 · IC95 [69,86; 72,01] · +9,04 pp [7,23; 10,86]; vs H1 +6,24 [5,11; 7,36]** · 486 ms/dec (p95 1044) · determinista (2 ejecuciones idénticas) | `run_sweep.py --configs configs/sweeps/h3_eval.yaml --seeds 10 --workers 15 --name h3_eval --baseline arm6_4orient` (wt2) | 48,9 % del gap; a 0,2 pp de T1 |
| *PALANCA* H1 + micro_plan (d3 w8, corte por tiempo) | 70,90 % ± 1,64 · +9,01 pp [7,77; 10,25] vs baseline · 722 ms/dec (p95 911) | `run_sweep.py --configs configs/sweeps/lever_microplan_eval.yaml --seeds 10 --workers 15 --name lever_microplan_eval --baseline arm6_4orient` (wt2) | NO cuenta para T1 (micro_plan=False es restricción, D-012) y NO es determinista (8/10 seeds cambian al repetir) → H3 |
| gantry_4orient (contraste) | 66,92 % ± 2,26 (+5,03 pp) | baseline_v1 | informativo; H1 no actúa con delta 0 |
| gantry_4orient + H1 + H3 (contraste) | 73,36 % ± 1,33 (+6,45 pp sobre gantry) | `run_sweep.py --configs configs/sweeps/contrast_gantry_h3.yaml --seeds 10 --workers 15 --name contrast_gantry_h3 --baseline gantry_4orient` (wt2) | informativo: la desventaja del brazo baja de 5,0 a 2,4 pp |
| arm6_planar (control) | 47,42 % | baseline_v1 | control |

## Cotas y palancas (Fase 0)
| Cota / palanca | Resultado | Fuente |
|---|---|---|
| Trivial (Σvol / V) | ≥ 6,716 palets → mínimo 7 | docs/COTAS.md (A) |
| Formal por palet (DFF + cortes horizontales, sin soporte/accesibilidad) | U* = 100 % flujo; 96,97 % familia dominante → no refuta T3 | docs/COTAS.md (A), `scripts/bound_formal.py` |
| Buffer k (política de producción) | k=30 +1,13 · k=75 +3,04 · **k=186 66,82 % (+4,93 pp)** (cortes 900 ms con k ≥ 75 → conservador) | out/sweeps/bound_buffer_k_v1 (wt1) |
| N palets abiertos (política de producción) | N=2 −1,98 · N=3 −5,73 · N=4 −5,96 pp: empeora con el scorer actual (no es cota) | out/sweeps/bound_open_pallets_v1 (wt1) |
| Techo offline con la regla R7 (D-026) | **81,45 %** (igual; el método de columnas no deja pozos) · 9 palets, 0 violaciones con R7 | `bound_offline.py --method columns --column-gap-mm 0 --templates 0 1 2 --width 8 --cpu-seconds 900 --tall-box-exemption`; docs/autopilot/evidence/offline_witness_r7.json (Codex 19) |
| Techo offline clarividente (B) | **80,40 %** testigo verificado (9 palets); 78,87 % reproducible (`bound_offline.py --method columns --width 8 --templates 1`); beam 71,64 %; orden 68,34 % | docs/COTAS.md (B), docs/autopilot/evidence/, `scripts/autopilot/verify_offline_witness.py` |

## Ramas y PRs (apilados, D-003)
| Rama | Base | Objetivo | PR | Estado |
|---|---|---|---|---|
| feat/parametros-congelados | feat/fifo-scorer-coherencia | parámetros, preset arm6_4orient, caras | #141 | abierto |
| feat/metrica-congelada | feat/parametros-congelados | métrica v1.0.0 + validador | #142 | abierto |
| feat/sweep-paralelo | feat/metrica-congelada | run_sweep + baseline oficial | #143 | abierto |
| feat/cotas-superiores (wt1) | feat/sweep-paralelo | cotas A + B + C, headroom, metas | #144 | abierto (smoke re-verificado OK con el lock libre, D-014) |
| feat/h1-deuda-accesibilidad | feat/cotas-superiores | H1 | #145 | abierto (rebase verificado: 20 KPIs idénticos) |
| feat/h3-busqueda-determinista | feat/h1-deuda-accesibilidad | H3 | #146 | abierto |
| feat/h4-columnas-online | feat/h3 | H4 (descartada) | #147 borrador | no mergear |
| feat/h2-ranuras | feat/h3 | H2 (descartada) | #148 borrador | no mergear |
| feat/h5-valor-estado | feat/h3 | H5 | #149 | abierto |
| feat/palanca-canto | feat/h5 | palanca canto + contraste gantry 6 orient. | #150 | abierto (defaults neutros) |
| feat/h6-rollouts-estocasticos (wt2, TOPE) | feat/palanca-canto | H6 | — | PR en curso |

## Trabajos Codex
| Job | Spec | Modelo / effort | WT | Estado |
|---|---|---|---|---|
| 01_parametros, 02_metrica, 03_sweep | specs/01–03 | sol / high | 1 | terminados → #141–#143 |
| 04A_formal | specs/04A | astra / xhigh | 2 | terminado → 382ee4f |
| 04C_palancas | specs/04C | sol / high | 1 | terminado → 2483261 |
| 05_h1 (+05b resume) | specs/05 + autopilot_logs/05b_delta.md | sol / high | 2 | terminado (05b cortado por cuota; completado por el director, D-011) |
| **04B_offline** | autopilot_logs/04B_spec.md | astra / xhigh | 1 | **CORTADO por cuota de Codex** (14:4x). Dejó `src/palca/study/offline_search.py` sin terminar (sin trackear). Relanzar tras las 19:01 con resume del thread de `04B_offline.jsonl` |

**Codex sin cuota otra vez desde 19:21 hasta las 00:02 del 2026-10-17** (astra xhigh consume la cuota en ~15 min: usar
gpt-6.1-sol salvo necesidad real).
- 04B_offline: cortado; el director cerró la parte B con los artefactos (D-013).
- 08_h3: cortado tras implementar y medir coste; el director revisó el código (13 tests OK) y completó ajuste
  (h3_tune_v2, h3_tune_v3) y evaluación doble (h3_eval, h3_eval_rep) → +6,24 pp sobre H1, determinista.
Pendientes con Codex (≥ 00:02), en orden: **09 H4 plantilla de columnas online** (spec escrita; sobre H1+H3), 07 H2 ranuras,
06 canto. Usar gpt-6.1-sol (astra solo si hace falta).

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

## Siguiente acción (vigente, 2026-10-20)
1. (hecho) R7 confirmada (D-026), implementada (Codex 18/19), medida: sin ganancia; techo igual. Reajuste H1 invalidado (D-028).
2. (hecho) Commit d0ce744, PR #160. H8, H1 fuerte (D-029) y fase de suelo descartadas. INFORME actualizado (77,06 %).
3. (hecho) Ciclo lateral n.º 3 (D-030): el hueco trasero es estructural (también en el techo offline).
4. Siguiente: R12, autopsia diferencial online vs offline (qué pozos crea la online que la offline no; sin Codex). Después, según el
   resultado, R13 (función de valor aprendida, Codex) o documentar el límite práctico de la célula.

## Siguiente acción (anterior, 2026-10-17)
1. (hecho 00:05–00:40) H4 → descartada, PR borrador #147.
2. (hecho) H2 → descartada, PR borrador #148. Gate sobre H1+H3 → n.s.
3. (hecho) H5 adoptada tras réplica (D-016) → T1 alcanzado (condicionado a D-012). PR desde feat/h5-valor-estado (base feat/h3).
4. Siguiente: 06 orientación canto (PLAN_B + contraste 6 orientaciones reales) y nueva técnica hacia T2 (≥ 78,55 %).

## Siguiente acción (histórico)
1. (hecho) PR #144 (cotas A+C) y PR #145 (H1). Autopsia con H1 → RESEARCH §1ter → spec 07 reescrita (H2 ranuras estrechas).
2. (en curso) 04B en wt1 y 08_h3 en wt2. Al terminar cada uno: revisar diff, verificar cifras en out/, smoke, commit, PR.
3. Cuando esté B: tabla de headroom en docs/COTAS.md (wt1), T1/T2/T3 aquí, push a #144.

## Registro de técnicas cerradas
| Técnica | Resultado (seeds de referencia) | Decisión |
|---|---|---|
| Gate stand_hw 800/1600/2400 | +0,5 / −1,0 / −0,5 pp, n.s. | descartada (no es palanca) |
| H1 deuda de accesibilidad (w0,25, b600) | +2,81 pp [1,04; 4,57], 0 violaciones, sin coste de tiempo | **adoptada** |
| Re-ajuste de mandos existentes con H1 (height_slack 0–500, score_mode, heurística 2D, tower_z) | ajuste: todos ≤ H1; heurística y tower_z inertes en heightfield (KPI idéntico) | cerrada (out/sweeps/h1_knobs_tune, wt2) |
| micro_plan sobre H1 (palanca) | +6,2 pp sobre H1 en referencia, no determinista | palanca [REVISAR] D-012 → origina H3 |
| H3 búsqueda determinista sobre H1 (n100 d3 w8) | +6,24 pp [5,11; 7,36] vs H1; total 70,94 %; determinista | **adoptada** (reportada aparte, D-012) |
| H4 plantilla de columnas online (sobre H1+H3) | −0,62 pp [−1,60; +0,36] n.s. (70,31 %); PALANCA gate 2400: 72,50 % (+1,56 [0,45; 2,68]) | **descartada** (PR borrador #147). Contador anti-estancamiento: 1 técnica < 0,5 pp |
| H2 ranuras estrechas (sobre H1+H3; w0,1, min 250) | −0,04 pp [−1,41; +1,34] n.s. (70,90 %) | **descartada** (PR borrador #148). Contador anti-estancamiento: **2** técnicas seguidas < 0,5 pp |
| H5 valor de estado (rollout 4 hojas, 25 nodos) sobre H1+H3 | referencia +1,11 [−0,00; 2,21]; réplica 20 seeds +2,12 [1,08; 3,16] | **adoptada** (D-016). Contador anti-estancamiento: 0 |
| H6 rollouts con cajas futuras muestreadas (sobre H1+H3+H5) | prior genérico: +1,99 [1,23; 2,75] ref.; réplica +1,27 [0,17; 2,37]; catalog/uniform similares (D-017) | **adoptada** con prior genérico |
| Palancas hacia T2 sobre H6 (levers_t2) | gate abierto 77,06; + canto 77,76; + k30 77,19; contrastes gantry4 79,29 / gantry6 82,00 (gate abierto) | ninguna configuración brazo llega a T2 |
| Techo offline sin resquicio (columnas contiguas) | **81,45 %** (columnas 0,1,2 / ancho 8), beam 71,64 %; 0 accesibilidad con G=0, 123 con G=50 | nuevo techo → metas recalculadas (D-013) |
| Ajuste del margen de tiempo real de H6 (B 57/80/110 × hojas 2/3/4 × futuras 6/10) | paisaje ruidoso 69,5–75,0 % sin tendencia; las mayores rozan o superan el límite de tiempo real | cerrada: H6 se mantiene (B57, 2 hojas, 6 futuras) |
| PALANCA garra de canto (cara H×L) y CONTRASTE gantry 6 orientaciones reales | canto: 68,67 % sin técnicas (+6,78 vs baseline); **72,54 % con H1+H3+H5 (+0,50 [−0,97; 1,96] n.s.)**; gantry 6 orient.: 76,39 % / **77,74 % con H1+H3+H5** (+5,70 [4,50; 6,91]) | `run_sweep.py --configs configs/sweeps/lever_canto.yaml --name lever_canto --seeds 10 --workers 15 --baseline arm6_4orient_H1H3H5` (wt2). Con la búsqueda, la garra de canto deja de aportar; la cinemática gantry sí (+5,7) |
| Palancas buffer sobre H1+H3+H5 (k30, k75) | k30 +0,87 [−0,40; 2,13]; k75 +0,53 [−0,27; 1,34], n.s.; N=2 incompatible con micro_plan (open_pallets>1 lo prohíbe) | informativo (sweep levers_h5 en /tmp/wt_ro_h5): con búsqueda, más buffer apenas aporta |
| Palanca gate stand_hw sobre H1+H3 sin columnas | gate 2400: −0,40 [−2,09; 1,30]; gate 800: +0,27 [−0,90; 1,43], n.s. (sweep lever_gate_h3 en /tmp/wt_ro_h3) | el gate solo importa combinado con columnas (H4) |
| H9 fase de suelo (`hard_floor_phase_end_step` 8/12/20 × premio de pie 0/0,5; ajuste determinista) | −1,0…−2,5 sin premio; −8,1…−8,4 con premio (h9_floor_tune, wt1) | **descartada**. Contador: 6 → ciclo lateral n.º 3 (D-030) |
| H8 altura muerta (peso 0,1–1,0, mín 250/350; ajuste determinista) | +0,01…+0,62 n.s. sin tendencia (h8_det_tune, wt1) | **descartada**. Contador anti-estancamiento: 4 |
| H1 fuerte (ajuste determinista) | w2,0 b900 +1,37 [−0,10; 2,84]; w1,0 b900 +1,05 [−0,43; 2,53] (h8_det_tune) | **no adoptada (D-029):** referencia −0,20 [−1,06; 0,66], réplica +0,53 [−0,41; 1,45] n.s.; w4–8 / b1200 +0,66…+0,95 n.s. Contador: 5 |
| R7 regla de acceso desde la cara superior (D-026, confirmada por el usuario) | determinista (D-028): tall_c0 −0,29 [−1,05; 0,46] n.s.; c100 +0,31, d400 +0,31, d500 −0,37 n.s.; ~7 colocaciones/seed usan la exención; techo offline igual (81,45 %) | regla vigente, sin ganancia: la oficial no cambia (sweep tall_det_ref, wt1) |
| R8 reajuste de H1 sobre h6_sin_gate (peso 0,25–2,0 × banda 600/900; seeds de ajuste) | todas n.s.: mejor w2,0 b900 +0,63 [−0,85; 2,10]; w1,0 b900 +0,47; resto −0,35…+0,12 (sweep h1_retune_h6, wt2) | **INVÁLIDO (D-028: 82 cortes de reloj por carga de CPU)**; se repetirá, con R7, con corte de 600 s |
| Palancas operativas con H1 (k30, k75, N2) | k30 +0,71 n.s.; k75 +2,79 [1,02; 4,57] (4 cortes/seed); N2 −0,93 n.s. | informativo (out/sweeps/lever_h1_operativas, wt2) |
