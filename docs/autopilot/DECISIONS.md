# DECISIONS — autopiloto de compactidad

Decisiones que normalmente se consultarían al responsable, tomadas con el criterio más conservador
para la trazabilidad. Cada una: contexto → opción elegida → justificación. **[REVISAR]** = marcada para revisión humana.

---

## D-001 · Dónde vive el repo y cómo se delega en Codex (2026-10-16)
- Contexto: la sesión de Claude Code se abrió en una carpeta de Windows vacía; el repo
  `kikirikiki/paletizado-caotico-V2` está en WSL (`~/code/Paletizador/09_Software/paletizado-caotico-V2`).
  El plugin Codex (`/codex:rescue`) ejecuta Codex en Windows, que no puede usar el `.venv` de WSL.
- Opción: delegar con `codex exec` dentro de WSL mediante `scripts/autopilot/codex_job.sh`
  (mismo Codex, misma cuenta, modelo de `~/.codex/config.toml`; `--full-auto`, sandbox workspace-write).
  `--effort xhigh` ↔ `-c model_reasoning_effort=xhigh`; `--resume` ↔ `codex exec resume <id>`; `--background` ↔ `nohup`.
  Logs de cada delegación fuera del repo: `~/code/Paletizador/09_Software/autopilot_logs/<job>.*`.
  Las especificaciones enviadas se commitean en `docs/autopilot/specs/` (trazabilidad de delegaciones).
- Justificación: es la única forma de que Codex ejecute smoke y tests reales.
- Actualización (2026-10-16): el CLI de WSL (0.104, `/usr/local/bin`) y el modelo de su config (`gpt-5.4`) ya no
  sirven con la cuenta. Se instaló `@openai/codex@latest` (0.163.0) en `~/.local` (sin sudo) y el wrapper pasa el
  modelo explícito: `gpt-6.1-sol` para implementar, `gpt-6-astra` para algoritmos difíciles (cotas, búsqueda) y
  revisiones adversariales. No se tocó `~/.codex/config.toml`.

## D-002 · Worktree aislado en lugar de cambiar de rama el checkout del usuario (2026-10-16)
- Contexto: el checkout principal está en `study/gantry-orientations` con archivos sin trackear del usuario
  (`studies/gantry_orientations/viz_replay.py`, `.agents/`, `skills-lock.json`).
- Opción: `git worktree add ../paletizado-autopilot` y trabajar ahí; `.venv` es un symlink al venv principal
  (añadido `.venv` a `.git/info/exclude`, cambio local no commiteado).
- Justificación: no se toca el estado de trabajo del usuario; las ramas y commits son los mismos.

## D-003 · Rama base y PRs apilados (2026-10-16)
- Contexto: el prompt fija `feat/fifo-scorer-coherencia` (existe, = origin, 8da6ee9). AGENTS.md dice
  `feature/stability-controls` y es el HEAD por defecto de origin.
- Opción: base = `feat/fifo-scorer-coherencia`. Cada PR del estudio se abre con `--base` = rama del PR anterior
  del que depende (PRs apilados), para que cada diff muestre solo su objetivo técnico.
- Justificación: instrucción explícita del prompt; el apilado evita PRs con diffs mezclados. Al mergear, el
  usuario puede cambiar la base en GitHub.

## D-004 · Orientaciones de `arm6_4orient` = `planar+stand_hw` (2026-10-16)
- Contexto: el prompt asumía que `stand_hl` podría apoyar la cara Alto × Largo. En el código
  (`pallet_model.py::_orientation_variants_for_mode`): `planar` = LWH/WLH (apoya L×W), `stand_hw` = HWL/WHL
  (apoya H×W, caja "de pie"). Ninguna variante apoya H×L (canto). En la rama `feat/stand-hl-orientation`
  (PR #139) las variantes `stand_hl` son geométricamente idénticas a `stand_hw` (HWL/WHL con otra etiqueta).
- Opción: `arm6_4orient` = `orientation_mode="planar+stand_hw"`, `accessibility_delta_mm=400`. Son exactamente las
  4 orientaciones válidas (plana, plana_rot, pie, pie_rot). Se añade test que impide la cara H×L.
- Justificación: se sigue la regla física del prompt (por cara apoyada), no el nombre del modo.
- **[REVISAR]** El "+3,2 pp de stand_hl" (PR #139/#140) no procede de una orientación nueva: las candidatas son
  duplicados de stand_hw que escapan a la lógica que el scheduler aplica solo a la familia `stand_hw`
  (`scheduler_v1.py::_placement_is_stand_hw`). Además el 72,1 % usa la métrica antigua (ver D-005).
  El "gantry 6 orientaciones" real (con canto) no existe en el código.

## D-005 · Métrica: volumen real de las cajas y envolvente 1240 × 840 × 2400 (2026-10-16)
- Contexto: las cifras históricas (64,6 % brazo, 72,1 % gantry) son `media_cajas × vol_medio_supuesto / (1240·820·2400)`.
  Además 1240×820 es un error aritmético: el código (`PalletSpec.bin_width_mm`) usa 800 + 2×20 = **840**
  (detectado por Codex, PARAMETROS discrepancia 8). Con el volumen real de cada caja, solo palets cerrados y
  1240×840×2400, exploración rápida (10 seeds de referencia): brazo `planar+stand_hw`/400 = **61,9 %**
  (media por palet), gantry `planar+stand_hw`/0 = 66,8 %.
- Opción: KPI oficial = Σ volumen real de cajas de palets cerrados / (nº palets cerrados × 1240 × 840 × 2400),
  con la envolvente = palet nominal 1200 × 800 + 2 × overhang 20 mm, **congelada como constante** (no se
  recalcula si en un experimento cambia el overhang). Se reporta además con el palet nominal 1200 × 800
  (×1,085) para comparabilidad.
- Justificación: la envolvente es el volumen físico que el algoritmo puede ocupar (igual que el código); no se
  puede "ganar" KPI aumentando el overhang. Es la opción más exigente para T2 (80 % absoluto).
- **[REVISAR]** El denominador (1240×840 vs 1200×800 vs el histórico 1240×820) mueve el KPI absoluto entre 1,5 y
  5 pp. Las ganancias relativas y el % de gap cerrado no dependen de esta elección.

## D-011 · Con Codex sin cuota, el director ejecuta sweeps ya revisados (2026-10-16)
- Contexto: Codex agotó su límite de uso (vuelve a las 19:01) con el segundo ajuste de H1 a medias (135/170 tareas) y el
  job 04B (techo offline) recién empezado (solo un borrador sin terminar de `offline_search.py`, sin commitear).
- Opción: el director completa y lanza sweeps con `scripts/run_sweep.py` (código ya revisado y commiteado) y configs YAML
  nuevas, sin tocar código de `src/`. El desarrollo de código (04B, H2, canto) espera a Codex.
- Justificación: ejecutar un benchmark ya implementado es mecánico y no infringe "el director no escribe código de
  producción"; esperar 4 h con la CPU parada no aporta trazabilidad.
- Resultado: H1 ajuste 2/3 y evaluación final → ganadora w0,25 b600: +2,81 pp [1,04; 4,57] (docs/autopilot/H1_ACCESS_DEBT.md).

## D-010 · Criterio para "T3 refutado" (2026-10-16)
- Contexto: las cotas formales por relajación (04A: DFF + cortes horizontales, sin soporte ni accesibilidad) dan
  U* = 100 % para el flujo y 96,97 % para la familia dominante: no pueden refutar el 92,8 % del moonshot. El prompt admite
  "demostración formal (cota con margen de error)".
- Opción: T3 se considerará refutado si (a) una cota formal válida queda por debajo de 1,5 × baseline, o, en su defecto,
  (b) el techo alcanzable con información completa (búsqueda offline clarividente con las restricciones físicas reales,
  esfuerzo de búsqueda documentado y creciente sin mejora material) queda por debajo con un margen amplio
  (≥ 10 pp) y con su IC95 sobre seeds/reinicios. (b) se etiqueta siempre como cota empírica, no como demostración.
- Justificación: es la lectura más estricta posible del prompt que sigue siendo calculable.
- **[REVISAR]** Si se prefiere exigir solo (a), la condición de parada 2 podría no cumplirse nunca.

## D-009 · Seeds de ajuste distintas de las de evaluación (2026-10-16)
- Contexto: cada técnica de la Fase 1 tiene hiperparámetros; ajustarlos con las mismas 10 seeds con las que se
  declara la mejora sobreestima la ganancia.
- Opción: ajuste con seeds 1001–1010; la configuración ganadora se elige solo con ellas y se evalúa una única vez con
  las 10 seeds de referencia (`REFERENCE_SEEDS`). La mejora oficial es la de la evaluación (comparación pareada, IC95).
- Justificación: criterio conservador anti-autoengaño (§7).

## D-008 · Codex no puede commitear: commitea el director tras revisar (2026-10-16)
- Contexto: el sandbox `workspace-write` de Codex deja `.git` en solo lectura (incluso con `--add-dir`):
  `git add` falla con "Read-only file system".
- Opción: Codex deja los cambios en el working tree; el director revisa el diff, ejecuta smoke y commitea por ruta
  explícita, separando el trabajo de Codex de los documentos del autopiloto. No se usa
  `--dangerously-bypass-approvals-and-sandbox`.
- Justificación: mantiene el sandbox y obliga a revisar antes de cada commit (requisito §1: no aceptar "hecho" sin diff).

## D-006 · Escenario de referencia del estudio (2026-10-16)
- Contexto: el "baseline producción ~64–65 %" es la config `brazo_hw` del estudio gantry: `BASE_PARAMS` de
  `studies/gantry_orientations/benchmark.py` (force_destination=1, continuous_pallets, ramp_cap=15,
  lookahead_k=15, arrival_mode=immediate, shuffle_window=15 con seed, micro_plan=False).
- Opción: ese es el escenario de referencia, congelado en `palca.study.params`. Las 10 seeds de referencia
  son las del estudio gantry. El escenario multi-destino (`--multi-ramp`) queda como escenario secundario.
- Justificación: es el escenario con el que se midieron las cifras que el prompt toma como referencia.

## D-007 · Límite de tiempo real por decisión (2026-10-16)
- Contexto: `t_pick_place=14 s`; producción usa `time_budget_ms=900`; la línea base tarda ~80 ms/decisión
  (15 s por flujo de 186 cajas en un núcleo).
- Opción: una técnica cumple tiempo real si la decisión tarda de media ≤ 1,0 s y p95 ≤ 3,0 s en un núcleo de
  esta workstation. Las cotas offline no tienen límite (no son políticas desplegables).
- Justificación: deja ≥ 4× de margen frente al ciclo de robot de 14 s aunque la planificación no se solape con el
  movimiento; respeta el orden de magnitud del presupuesto ya validado (900 ms).
