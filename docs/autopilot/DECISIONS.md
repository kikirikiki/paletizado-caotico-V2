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

## D-012 · `micro_plan` es una palanca, no una técnica para T1; H3 = búsqueda determinista (2026-10-16)
- Contexto: el prompt fija `micro_plan=False` como restricción (§2) y prohíbe relajarla sin reportarla aparte (§7). No hay
  ningún motivo documentado en el repo (CONTEXT.md solo dice "único valor válido en producción"; ni PRs ni commits lo explican).
  Medido sobre H1 (director, D-011): seeds de referencia **70,90 % ± 1,64, +9,01 pp [7,77; 10,25] vs baseline** (+6,2 pp vs H1),
  0 violaciones, 722 ms/decisión de media (p95 911). **No es determinista**: repitiendo el mismo sweep, 8/10 seeds cambian de
  KPI (p. ej. seed 1: 72,25 % → 66,85 %), porque la búsqueda se corta por tiempo de pared (900 ms) y depende de la carga de CPU.
- Opción: (1) `micro_plan=True` se reporta SOLO como palanca (no cuenta para T1); (2) se desarrolla H3 = búsqueda sobre el
  buffer con presupuesto determinista (nodos/expansiones, no tiempo), reproducible bit a bit y con tiempo real acotado; H3 se
  reporta aparte de las técnicas "sin búsqueda" hasta que el responsable confirme que una búsqueda determinista es válida.
- Justificación: respeta la letra de §2/§7 y a la vez explora la línea con más ganancia medida.
- **[REVISAR]** ¿Por qué `micro_plan=True` es inválido en producción? Si el motivo era el no determinismo o el tiempo, H3 lo
  resuelve y su ganancia contaría para T1; si es otro (p. ej. un problema físico del plan), hay que saberlo.

## D-013 · Techo de referencia para el gap = testigo offline verificado de 80,40 % (2026-10-16)
- Contexto: el job 04B (techo offline) se cortó dos veces por cuota. Dejó soluciones válidas: columnas 78,87 % reproducible con
  `scripts/bound_offline.py`, y un testigo de 80,40 % generado por una versión intermedia del script que ya no existe (la actual
  reproduce 78,35 %). Ambos pasan `scripts/autopilot/verify_offline_witness.py` (mismas cajas que el Excel, 0 violaciones,
  0 accesibilidad).
- Opción: gap = 80,40 − 61,89 = 18,51 pp (el techo mayor verificado; el testigo se commitea en `docs/autopilot/evidence/`).
  T1 ≥ 71,15 %, T2 ≥ 78,55 % (o ≥ 80 % absoluto con palancas), T3 ≥ 92,84 %. Si se encuentra un techo offline mayor, las metas
  se recalculan al alza.
- Justificación: usar el techo más alto hace las metas más exigentes (criterio conservador); la validez de un testigo no depende
  de cómo se generó.
- **Actualización (2026-10-17):** con plantillas contiguas (`--column-gap-mm 0`, sin el resquicio de 2 mm) el método de columnas llega a
  **81,449 %** (reproducible con un comando, verificado). Metas vigentes: gap 19,56 pp, **T1 ≥ 71,67 %**, **T2 ≥ 79,49 %**, T3 ≥ 92,84 %.

## D-014 · Commits con smoke fallido por lock de sweeps ocupado (2026-10-16)
- Contexto: acd5bda y 956f527 (feat/cotas-superiores) se commitearon con dos tests de `test_run_sweep.py` en rojo, porque el
  sweep h3_tune_v2 (otro worktree) tenía `/tmp/palca_sweep.lock` cogido ("Ya hay un sweep activo"). El script de commit del
  director usaba `smoke && tail` bajo `set -e`, que no aborta.
- Opción: se re-ejecuta el smoke de esa rama cuando el lock quede libre y el resultado se anota en PROGRESS. Los scripts de commit
  pasan a `smoke || exit 1`. La rama de H1 ya añade reintentos de lock a esos tests (no hay regresión de código).
- Justificación: transparencia sobre la regla "smoke antes de cada commit".

## D-019 · Ciclo de pensamiento lateral n.º 1 (2026-10-17)
- Contexto: tras H6 (74,03 %), H7 no aporta y el análisis indica que, con las restricciones oficiales, `arm6_4orient` está cerca de su
  techo práctico: la franja trasera de 395 mm (y ≥ 425) solo se llena con cajas de pie (la política ya manda allí el 81 % de las de pie
  y el 76 % de las estrechas), y el gate las retrasa. El techo offline (81,45 %) planifica columnas completas con información total.
  No se ha llegado aún a 3 técnicas seguidas < 0,5 pp (contador: 1), pero el ciclo se adelanta para no gastar cuota en variantes.
- Reformulaciones (ganancia esperada / coste / viabilidad física, 1–5):
  | # | Reformulación | Gan. | Coste | Viab. | Nota |
  |---|---|---|---|---|---|
  | R1 | Palets por familia con 2 posiciones (palet de familia dominante + palet mixto) | 3 | 4 | 4 | palanca (2.ª posición); N>1 hoy incompatible con la búsqueda |
  | R2 | Quitar el gate stand_hw (franja trasera con cajas de pie desde el suelo) | 3 (+3,0 medido) | 1 | ? | pendiente del responsable (D-018) |
  | R3 | Zona de staging (aparcar cajas para colocarlas después) | 1–2 | 3 | 2 | con búsqueda, más buffer apenas aporta (k30/k75 n.s.) |
  | R4 | **Secuenciación aguas arriba** (el almacén entrega las cajas agrupadas por familia/altura en ventanas) | 4 | 2 | 3 | palanca operativa: acerca el problema online al offline (81,45 %) |
  | R5 | Cambiar qué se optimiza: palet "de residuos" que acumula los inencajables hasta el final | 2 | 2 | 3 | sesga el KPI (el último palet no cuenta): solo con métrica de flujo, no se adopta |
  | R6 | Valor aprendido (PCT/DRL) para los rollouts | 2–3 | 5 | 5 | solo si lo heurístico se estanca de verdad |
- Opción: atacar R4 (mayor ganancia/coste) como palanca operativa cuantificada: ordenar el flujo de llegada dentro de ventanas de W cajas
  según una clave (familia, altura) y medir H6 con W ∈ {15, 30, 60, 186}. Después, R1 si queda cuota. R2 depende del responsable.
- Justificación: protocolo anti-estancamiento §5 y prioridad por ganancia/coste.

## D-018 · El gate stand_hw es la palanca de software más potente restante: se reporta aparte (2026-10-17)
- Contexto: el prompt lista `stand_hw_height_margin_gate_mm=400` entre las "restricciones físicas" (§2), aunque §7 no lo menciona entre
  las relajaciones prohibidas, y en el código es una heurística de altura (permite cajas de pie solo si el palet está a ≤ 400 mm del
  techo), sin justificación física documentada. Sobre H1+H3+H5+H6 (10 seeds de referencia): gate 800 → −1,34 pp, 1200 → −0,98,
  **2400 (sin gate) → 77,06 % (+3,03 [1,72; 4,35])**. La autopsia (RESEARCH §1quater) explica por qué: la franja trasera de 395 mm
  solo admite cajas estrechas o de pie.
- Opción: se respeta el gate oficial (400) en todas las cifras oficiales; quitarlo se reporta como PALANCA con su ganancia aislada.
- **[REVISAR — prioridad alta]** ¿El gate de 400 mm responde a una restricción física (estabilidad de cajas de pie en capas bajas, carga,
  normativa) o es una heurística del algoritmo? Si es heurística, quitarlo daría 77,06 % en arm6_4orient, a 1,5 pp de T2.

## D-017 · H6: el prior del catálogo es información agregada del propio flujo → test de sensibilidad antes de adoptar (2026-10-17)
- Contexto: H6 (rollouts con 6 cajas futuras muestreadas) mejora a H5 en la réplica preregistrada (+1,54 pp [0,68; 2,39]; referencia
  +1,18 [−0,18; 2,54]). El prior "catalog" no lee la secuencia ni el Excel en ejecución, pero sus proporciones y rangos (CONTEXT.md)
  se calcularon a partir de este mismo flujo de 186 cajas: fuga leve de información agregada (no de orden ni de dimensiones exactas).
- Opción: antes de adoptar, repetir evaluación y réplica con priors mal especificados ("uniform_types": tipos equiprobables;
  "generic": rangos amplios 300–500 × 350–750 × 250–500 mm sin información del flujo). Si la ganancia sobre H5 se mantiene con
  "generic", H6 se adopta con el prior "generic" (el más limpio) salvo que "catalog" sea significativamente mejor, en cuyo caso se
  reporta la diferencia como dependiente del conocimiento previo de la mezcla de tipos.
- Justificación: anti-autoengaño (§7): la mejora no puede depender de conocer de antemano el flujo evaluado.
- **Resultado (2026-10-17):** con "generic" la ganancia se mantiene: referencia 74,03 % (+1,99 [1,23; 2,75]) y réplica D-016
  +1,27 [0,17; 2,37]; "catalog" no es significativamente mejor (referencia +1,18 [−0,18; 2,54], réplica +1,54 [0,68; 2,39]).
  → **H6 se adopta con `rollout_prior=generic`** (sin información del flujo). Config oficial: `h6_generic` de
  `configs/sweeps/h6_prior_eval.yaml`.

## D-016 · Resultado limítrofe de H5: réplica preregistrada en 20 seeds nuevas (2026-10-17, antes de ejecutarla)
- Contexto: H5 (rollout, 4 hojas, 25 nodos) sobre H1+H3 da 72,04 % en las 10 seeds de referencia, +1,105 pp con IC95
  [−0,004; +2,214]: no significativo al 95 % por muy poco. Añadir seeds a la misma evaluación tras ver el resultado sería parada
  opcional (sesgo).
- Opción (fijada ANTES de ver la réplica): una única réplica independiente con 20 seeds nuevas = las 20 seeds extra deterministas del
  runner (índices 10–29, `SHA256('palca-sweep-seed-v1:<i>')[:4]`), nunca usadas para ajustar ni evaluar. Configs: H1+H3 y H1+H3+H5
  ganadora, sin cambiar parámetros. Criterio: H5 se adopta si la pareada H5 − H1+H3 en esas 20 seeds es significativa (IC95 > 0). Si no,
  H5 se descarta (cuenta como técnica < 0,5 pp a efectos del protocolo anti-estancamiento solo si su media en la réplica es < 0,5 pp).
- Justificación: criterio conservador (§7) sin sobreajuste a las seeds de referencia.

## D-015 · La regla de accesibilidad ignora vecinas casi pegadas: se mide la robustez, no se cambia la regla (2026-10-16)
- Contexto: `RobotAccessibilityControl` solo considera adyacente una caja que toca o solapa la huella (hueco ≤ 0 en x e y). El
  método offline de columnas separa las columnas 2 mm a propósito. Recuento de colocaciones que serían infactibles si
  "adyacente" incluyera huecos de hasta G mm:
  | Fuente | G=5 | G=20 | G=50 | G=100 |
  |---|---|---|---|---|
  | Testigo offline 80,40 % (186 cajas) | 19 | 97 | **117 (63 %)** | 119 |
  | Baseline producción (1860 cajas, 10 seeds) | 207 | 377 | **557 (30 %)** | 613 |
  | H1 (1860 cajas) | 145 | 386 | **611 (33 %)** | 667 |
  | H1 + H3 (1860 cajas, h3_eval) | 269 | 543 | **754 (40,5 %)** | 786 |
  Todas las políticas, incluida la de producción, dependen de esta lectura de la regla; el testigo offline, mucho más.
- Opción: la regla es una restricción física congelada (§2) y no se modifica; todas las cifras oficiales usan la regla tal cual.
  Se añade a los sweeps la métrica de robustez `acc_violations_gap{5,20,50}` y se reporta junto al KPI. Ninguna técnica nueva
  puede introducir huecos artificiales para esquivar la regla (las especificaciones lo prohíben).
- **[REVISAR — prioridad alta]** ¿Qué holgura lateral necesita realmente la garra? Si la regla física debe considerar vecinas a
  pocos centímetros, el baseline, las técnicas y el techo offline cambian (el techo, previsiblemente, mucho). Con la respuesta
  se puede fijar `adjacency_gap_mm` y re-medir todo con el mismo método.

## D-020 · Pausa por uso semanal de Claude ≥ 60 % (instrucción del usuario, 2026-10-17)
- Contexto: el usuario pide que, si el uso semanal de Claude llega al 60 %, el autopiloto se pause hasta que él diga que continúe.
  Uso al recibir la instrucción: 41 % ("Weekly · all models", reinicio el 2026-10-21 a las 12:00 CEST).
- Opción: comprobar `mcp__ccd_session_mgmt__get_usage` al reanudar y antes de cada delegación a Codex, sweep o tarea larga. Al llegar
  al 60 %: no lanzar trabajo nuevo, dejar terminar lo que ya corre (y revisarlo/commitearlo solo si el usuario lo autoriza), dejar el
  estado escrito en PROGRESS.md y avisar al usuario. No se escribe `PARADA:` (reservada a las condiciones de §9).
- Justificación: instrucción explícita del usuario; prevalece sobre el modo autopiloto.
- **Enmienda (2026-10-20):** con el uso semanal al 65 %, el usuario ordena "continúa hasta el 80 %". **El umbral de pausa pasa a 80 %**
  (mismo procedimiento). El uso semanal se reinicia el 2026-10-21 a las 12:00 CEST.

## D-021 · Gate stand_hw eliminado por indicación del usuario (2026-10-17)
- Respuesta del usuario a D-018: el gate de 400 mm era una regla para evitar torres; si anulándolo no se hacen torres, se elimina.
- Comprobación (H1+H3+H5+H6, 10 seeds de referencia, autopsia de palets cerrados): sin gate hay MENOS torres. Espacio libre sobre el
  perfil 23,7 % → 20,7 %; altura media de la superficie 1831 → 1902 mm; suelo sin cubrir 10,2 % → 7,8 %; fracción > 400 mm bajo el
  máximo 28,6 % → 28,1 %; peor palet 59,1 % → 65,5 %; cierres por estabilidad 6 → 1.
- Opción: la configuración oficial pasa a stand_hw_height_margin_gate_mm=2400 (sin gate). El preset rm6_4orient y el baseline
  congelado (61,89 %) no cambian: el gate se quita como override de la mejor configuración. Réplica en las 20 seeds de D-016 en curso.

## D-022 · D-012 resuelto: el robot solo ve las 15 cajas de la cinta (2026-10-17)
- Respuesta del usuario: el robot solo ve lo que está en la cinta (15 cajas), no el Excel completo.
- Comprobación en código: el micro_plan original (y H3, micro_plan_value=score) rellena la cinta simulada con cajas reales de
  upstream (scheduler_v1 líneas ~1286–1292): mira cajas que aún no han llegado. H5 y H6 (micro_plan_value ≠ score) congelan la
  ventana visible y nunca usan upstream (líneas ~710–715); H6 usa cajas futuras sintéticas de un prior genérico.
- Test de fuga (flujo idéntico hasta la fila 100 y permutado después): H6 toma decisiones idénticas hasta el paso 86, exactamente
  cuando la primera caja cambiada entra en la cinta → **H6 solo usa información visible**.
- Opción: H5/H6 cuentan para las metas sin condición. H3 sola y la palanca micro_plan por tiempo quedan marcadas como "con fuga" y
  no se usan. Cualquier técnica futura debe pasar el mismo test de fuga (decisiones idénticas mientras las cajas cambiadas no estén
  en la cinta).

## D-023 · Holgura lateral de la garra: interpretación pendiente de confirmar (2026-10-17)
- Respuesta del usuario a D-015: "basta con que no se toquen, pero si se dejan 2 cm de margen de maniobra para que al bajar con la
  caja no se golpee con la caja ya posicionada, es suficiente"; y la regla de los 400 mm tiene que ver con el margen en Z para que el
  brazo (la parte que queda por encima de las cajas) no golpee.
- Interpretación provisional: la regla de los 400 mm (margen en Z del brazo) se mantiene tal cual. Queda por confirmar si debe haber
  ~2 cm de hueco lateral entre la caja que se baja y sus vecinas (lo que cambiaría todas las cifras, porque hoy el simulador coloca las
  cajas pegadas) o si basta con que no se solapen. Se ha preguntado al usuario; mientras tanto no se cambia la regla.

## D-024 · Laterales libres como desempate, sin perder compactación (requisito del usuario, 2026-10-17)
- Respuesta del usuario a D-023: las cajas pueden ir pegadas (hay que aprovechar el espacio al máximo). Requisito nuevo de maniobrabilidad:
  priorizar huecos con más laterales libres (4 > 3 > 2 > 1 > 0), para bajar la caja desplazada y deslizarla a su posición final.
- Definiciones confirmadas: (1) la compactación manda, los laterales libres solo desempatan; (2) el borde del palet cuenta como libre;
  (3) contigua = vecina que toca o está a < 20 mm del lado, se solapa a lo largo del lado y en altura con [z, z+h); el apoyo y las vecinas
  más bajas que la base no cuentan; (4) el robot puede entrar por cualquier lado libre.
- Opción: desempate con tolerancia configurable; se adopta solo si el KPI no baja (no inferioridad en referencia y réplica).
- **Resultado (2026-10-17):** el desempate por laterales libres baja la compactación incluso con tolerancia 0: referencia −1,33 pp
  [−2,38; −0,27] (significativo), réplica −0,17 [−1,06; 0,73]; tolerancias 0,001 y 0,01 peores o iguales. → **NO se adopta** (el
  usuario no quiere perder compactación). La configuración oficial ya coloca el 94,7 % de las cajas con 3–4 laterales libres, el 0,4 %
  con 1 y ninguna con 0 (con desempate: 97,8 % con 3–4). Implementado con `free_sides_tiebreak=False` por defecto (rama feat/laterales-libres).

## D-025 · Ciclo de pensamiento lateral n.º 2: la pérdida está en pozos de acceso (2026-10-20)
- Contexto: contador anti-estancamiento = 3 (H7 −0,53, secuenciación −0,81, palets por familia −0,74). Mejor: 77,06 % (T2 = 79,49).
- Diagnóstico nuevo (palets cerrados de h6_sin_gate, 10 seeds de referencia = 80 palets; réplica 20 seeds = 164 palets, igual):
  cajas 77,1 % · huecos internos 2,2 % · **libre sobre el perfil 20,7 %**. Cierres: 79 de 80 por altura (no cabe ninguna de las 15
  cajas visibles). Ese 20,7 % por altura libre restante sobre cada celda (entre paréntesis, la parte en "pozo": a < 300 mm hay una
  celda más de 400 mm por encima):
  | Altura libre | < 150 | 150–250 | 250–350 | 350–500 | 500–750 | ≥ 750 |
  |---|---|---|---|---|---|---|
  | % envolvente | 0,6 | 2,3 | 0,7 | 0,3 (0,15) | 2,0 (1,6) | **14,9 (14,8)** |
  **El 72 % de la pérdida (14,8 pp) son pozos profundos junto a columnas que los superan en > 400 mm**; el "techo irregular" (< 350)
  solo suma 3,6 pp. Los pozos son definitivos con la regla actual: `RobotAccessibilityControl` mide desde la **base** de la caja nueva
  (vecina tocando con techo > base + 400 ⇒ prohibido), **sin tener en cuenta la altura de la caja**: una caja de pie de 750 mm no
  puede entrar en un pozo de 600 mm aunque su cara superior (donde va la garra) quede por encima de todas sus vecinas.
- Reformulaciones (ganancia / coste / viabilidad, 1–5):
  | # | Reformulación | Gan. | Coste | Viab. | Nota |
  |---|---|---|---|---|---|
  | R7 | **Regla de acceso referida a la cara superior**: prohibido solo si vecina > base + máx(400, h_caja + holgura) | 5 | 1 | ? | cambia una restricción física (§2): PALANCA hasta que el usuario confirme |
  | R8 | No crear pozos: reajustar H1 (peso/banda) ahora que hay búsqueda y rollouts | 2 | 1 | 5 | sin código (sweep del director) |
  | R9 | H8 "altura muerta": penalizar dejar < 250 mm libres hasta 2400 sobre la caja | 1–2 | 2 | 5 | ataca 2,9 pp como máximo |
  | R10 | Rellenar pozos con cajas de pie que no toquen la pared | — | — | — | prohibido (D-015: nada de huecos artificiales) |
  | R11 | Valor aprendido para rollouts largos (el pozo se forma muchas cajas antes) | 3 | 5 | 4 | solo si R8/R9 no bastan |
- Opción: (1) R8 ya, sin Codex (sweep h1_retune_h6 en seeds de ajuste; evaluación en referencia + réplica si mejora). (2) Codex
  implementa R7 como palanca desactivada por defecto (`accessibility_tall_box_exemption`, también en H1, la búsqueda y las cachés de
  rendimiento) y R9 como técnica (H8). (3) Pregunta [REVISAR — prioridad alta] al usuario sobre R7. Si R7 se acepta, el techo offline
  debe recalcularse con la misma regla antes de recalcular el gap.
- Justificación: protocolo anti-estancamiento §5; la autopsia apunta a una única causa dominante y R7 es la única palanca con
  ganancia potencial > 2,4 pp (lo que falta para T2).

## D-026 · R7 confirmada por el usuario: la regla de acceso admite cajas que asoman por encima de sus vecinas (2026-10-20)
- Respuesta del usuario: "si la caja, ya colocada, sobresale por encima de todas sus vecinas, el brazo no choca. Puede entrar aunque el
  pozo tenga más de 400 mm."
- Regla física vigente desde ahora: una vecina que toca bloquea solo si su techo > base + máx(400, h_caja). Equivale a
  `accessibility_tall_box_exemption=True`, `accessibility_top_clearance_mm=0` (techo igual al de la caja: permitido).
- Opción: R7 deja de ser palanca y pasa a restricción oficial. Como con el gate (D-021), el preset `arm6_4orient` y el baseline
  congelado (61,89 %) no cambian; la regla entra como override de la configuración oficial y se reporta también el baseline con la
  regla nueva para separar el efecto de la regla del de las técnicas. Antes de recalcular el gap hay que recalcular el techo offline
  con la misma regla (si no, el gap quedaría infravalorado). Las variantes de `access_debt_exempt_delta_mm` se ajustan en seeds de ajuste.
- **Resultado (2026-10-20, sweep determinista tall_det_ref, D-028, 0 cortes):** con el código nuevo y la exención desactivada,
  `h6_sin_gate` reproduce exactamente 77,064 ± 0,895. Con R7: `tall_c0` −0,29 pp [−1,05; 0,46] n.s.; informativas `tall_c100` +0,31,
  `tall_c0_d400` +0,31, `tall_c0_d500` −0,37 (todas n.s.). La exención solo se usa en ~7 colocaciones por seed: los pozos de la
  autopsia D-025 son en su mayoría más hondos que la caja más alta (750 mm), así que no se pueden rellenar ni con la regla nueva.
  Techo offline con R7: 81,45 % (igual). → R7 es la regla física vigente, pero no aporta compactación: la configuración oficial
  sigue siendo `h6_sin_gate` (una política que no usa la exención también cumple R7). R7 no cuenta como técnica (contador sin cambios).

## D-027 · Cuota semanal de Codex: no pasar del 60 % (instrucción del usuario, 2026-10-20)
- Instrucción: "no quiero que el límite semanal de CODEX pase del 60 %". Uso al recibirla: semanal 3 %, 5 h 19 %.
- Medición: las sesiones de Codex (`~/.codex/sessions/AAAA/MM/DD/*.jsonl`) registran `rate_limits`; `secondary` = ventana semanal
  (10080 min). `scripts/autopilot/codex_quota.sh check` la lee (0 % si la ventana ya se reinició); `codex_job.sh` no lanza nada si
  está ≥ 60 %; `codex_quota.sh watch 60` es un guardián en segundo plano que mata los `codex exec` en marcha al llegar al 60 %
  (quedan reanudables por thread id).
- Al llegar al 60 %: el director sigue solo con trabajo sin Codex (sweeps de configuraciones ya implementadas, revisión, informes)
  y avisa al usuario. Usar `gpt-6.1-sol`; `gpt-6-astra` solo si es imprescindible (consume la cuota muy deprisa).

## D-028 · Sweeps de evaluación deterministas: corte de reloj de la búsqueda a 600 s (2026-10-20)
- Contexto: la búsqueda (micro_plan/H3–H6) tiene un corte de reloj `micro_plan_hard_cap_ms = 10000` además del presupuesto de nodos.
  Con la CPU saturada (sweep de 13–15 procesos + Codex en 16 CPUs) algunas decisiones superan 10 s y se cortan: los sweeps de hoy
  tuvieron 20–82 cortes y `h6_sin_gate` dio 76,93 % en lugar de 77,06 % con el mismo código. Todas las cifras oficiales anteriores
  tienen 0 cortes (se ejecutaron sin carga concurrente), así que siguen siendo válidas.
- Opción: en todos los sweeps de evaluación y ajuste se fija `micro_plan_hard_cap_ms: 600000`: las decisiones dependen solo del
  presupuesto de nodos (deterministas, independientes de la carga). Con 0 cortes es idéntico a la configuración oficial. El
  cumplimiento de tiempo real (D-007) se mide aparte en un solo proceso sin carga. Un sweep con cortes > 0 no se usa.
- Consecuencia: `h1_retune_h6` (82 cortes) y `tall_ref` (20–52) quedan invalidados y se repiten con esta regla.

## D-029 · H1 fuerte (peso 2,0, banda 900) como candidata preregistrada (2026-10-20, ANTES de ver referencia y réplica)
- Contexto: la autopsia RESEARCH §1quinquies muestra que la pérdida dominante (~7 pp) es un hueco de suelo trasero que muere cuando la
  fila delantera supera 400 mm en el primer quinto del palet: exactamente lo que penaliza H1. En el ajuste determinista (h8_det_tune,
  seeds 1001–1010, 0 cortes): `h1_w20_b900` +1,37 pp [−0,10; 2,84], `h1_w10_b900` +1,05 [−0,43; 2,53]; H8 0,01–0,62 n.s. sin
  tendencia → **H8 descartada** (contador anti-estancamiento: 4).
- Opción (fijada antes de ver los datos de evaluación): candidata = `h1_w20_b900` (h6_sin_gate con access_debt_weight 2,0 y
  access_debt_band_mm 900). Se evalúa en las 10 seeds de referencia y en la réplica de 20 seeds de D-016, deterministas (D-028).
  **Se adopta si la diferencia pareada en la réplica es significativa (IC95 > 0).** La extensión del ajuste (h1_strong_tune: peso 4/8,
  banda 1200) es informativa; si una variante la supera claramente, necesitará su propia réplica.
- **Resultado (2026-10-20, 0 cortes):** referencia −0,20 pp [−1,06; 0,66]; **réplica +0,53 [−0,41; 1,45] n.s. → NO se adopta.**
  Extensión del ajuste: w4 b900 +0,95, w2 b1200 +0,95, w4 b1200 +0,66, w8 b1200 +0,86 (todas n.s.). La ganancia en seeds de ajuste
  era en su mayor parte ruido. Contador anti-estancamiento: 5. Condición de parada §9.2 no aplicable (T3 no refutado formalmente).
  Siguiente: H9 "fase de suelo" (mecanismo existente `hard_floor_phase_*`, nunca probado en este estudio), en seeds de ajuste.

## D-030 · Ciclo de pensamiento lateral n.º 3: el hueco trasero es estructural (2026-10-20)
- Resultados desde D-025: R7 sin ganancia; H8, H1 fuerte y fase de suelo descartadas (fase de suelo: −1,0…−2,5 pp sin premio a cajas
  de pie, −8 con premio; h9_floor_tune). Contador anti-estancamiento: 6.
- Hallazgo: el testigo offline (81,45 %) **también** deja vacío el 7,4 % de la planta interior y un 11,1 % en pozos (online: 7,3 % y
  14,7 %). El hueco trasero viene de la mezcla de cajas del flujo, no de la política. Lo que separa online de offline: ~3,5 pp de pozos
  adicionales y ~0,9 pp de huecos internos. T2 exige recuperar 2,4 de esos 4,4 pp (55 %) viendo solo 15 cajas.
- Reformulaciones (ganancia / coste / viabilidad, 1–5):
  | # | Reformulación | Gan. | Coste | Viab. | Nota |
  |---|---|---|---|---|---|
  | R12 | Autopsia diferencial online vs offline: qué pozos crea la online que la offline no | — | 1 | 5 | diagnóstico previo a cualquier técnica |
  | R13 | Función de valor aprendida (entrenada con flujos sintéticos del prior genérico) para los rollouts | 2–3 | 5 | 4 | la única vía de software con margen; horizonte más allá de la ventana |
  | R14 | Más información: ver más cajas | 1 | 2 | 3 | medido: buffer ×2/×5 +0,87/+0,53 n.s.; la búsqueda no lo aprovecha |
  | R15 | Garra / cinemática distinta | 3–5 | 5 | ? | gantry 82,0 %; fuera de la célula objetivo |
- Opción: R12 primero (sin Codex), y según lo que muestre, R13 con Codex como técnica de mayor coste. Si R12 indica que los pozos
  adicionales no se pueden prever con 15 cajas, se documenta como límite práctico de la célula.
- Condición de parada §9.2: no aplicable (T3 no refutado formalmente con cotas).
- **R12 hecho (RESEARCH):** la diferencia online − offline son pozos a media altura (fondo a 1–800 mm: 3,9 % vs 0,4 %) → H4.

## D-031 · H4 (columnas online, ancho 455, f85) sobre h6_sin_gate: candidata preregistrada (2026-10-20, ANTES de evaluar)
- Integración: rama `feat/h4-sobre-h6` (merge 01faacb, conflictos resueltos por Codex, spec 20); con defaults, dumps idénticos (2/2) y
  KPI idéntico en las 10 seeds de ajuste. 0,691 s/decisión con H4 activa.
- Ajuste determinista (h4_h6_tune, seeds 1001–1010): w455 f85 +1,07 [−0,50; 2,65]; w450 f85 +0,59; w450 f75 −0,19; w445 f85 −1,51;
  ancho automático −2,61 [−4,27; −0,95] (significativo).
- Opción: candidata = `h4_h6_w455_f85`; misma regla que D-029 (referencia 10 seeds + réplica 20 seeds D-016, determinista; se adopta
  si la pareada en la réplica es significativa). **Advertencia de fuga de conocimiento (como D-017):** el ancho de columna fijo (455)
  está ajustado a la familia dominante de este flujo, y la variante sin ese conocimiento (auto) es significativamente peor; si se adopta,
  se reporta aparte como dependiente de conocer el catálogo de cajas, no como técnica general.
- **Resultado (2026-10-20, 0 cortes):** referencia 76,73 % vs 77,06 % (−0,33 [−0,98; 0,31] n.s.); **réplica 77,59 % vs 76,21 %
  (+1,38 [0,48; 2,29], significativa)**; 30 seeds juntas: 77,30 % vs 76,49 % (+0,81). Tiempo: más rápida que la oficial.
  → Cumple el criterio preregistrado, pero el ancho fijo de 455 mm depende de conocer la familia dominante de este flujo (la variante
  sin ese conocimiento es −2,61 significativa). Siguiendo D-017: **no cuenta como técnica general; se reporta como PALANCA "configuración
  por catálogo" (+0,81 pp en 30 seeds) y la oficial sigue siendo h6_sin_gate (77,06 %)**. [REVISAR — usuario] Si en producción el
  sistema conoce el catálogo de cajas y es legítimo configurar el ancho de columna con él, H4 w455 pasaría a oficial.

## D-032 · No hay catálogo previo: el sistema solo conoce las 15 cajas de la cinta (respuesta del usuario, 2026-10-20)
- Respuesta: "No hay un catálogo de cajas que se conozca con anterioridad, solo se conocen las 15 de la cinta".
- Consecuencias: H4 w455 queda como palanca informativa, no implantable tal cual; la oficial sigue siendo h6_sin_gate (77,06 %).
  H6 ya usa el prior genérico sin información del flujo (D-017), conforme. Ninguna técnica futura puede usar parámetros que codifiquen
  dimensiones del flujo evaluado (anchos, alturas o familias concretas); los pesos y presupuestos ajustados en seeds de ajuste siguen
  permitidos, pero cualquier parámetro geométrico debe deducirse de las 15 cajas visibles o del prior genérico.
- Contador anti-estancamiento: 7 (H4 no cuenta como técnica general).
- **H10 (columnas adaptativas con solo las 15 visibles, commit 64b9fb7):** ajuste determinista (h10_tune) −0,42…−2,09 pp en las seis
  variantes (s0 f75 significativa) y peores laterales libres → **descartada**. Contador: 8. Sin conocer las medidas de antemano, la
  plantilla de columnas no compensa.

## D-033 · R13: función de valor aprendida con flujos sintéticos del prior genérico (2026-10-20)
- Contexto: 8 técnicas seguidas < 0,5 pp. Lo que falta (pozos a media altura, D-030/R12) se crea muchas cajas antes de que se note; los
  rollouts de H5/H6 miran 2 hojas × 6 cajas. La única vía de software restante con margen es una estimación del valor de un estado de
  palet a largo plazo.
- Opción: Codex entrena un modelo ligero V(estado del palet) → utilización final esperada del palet, con episodios simulados sobre
  flujos SINTÉTICOS del prior genérico (sin información del flujo evaluado, D-032), y lo usa como valor de las hojas de H5/H6. Se ajusta
  en seeds 1001–1010 y se evalúa con el protocolo preregistrado (referencia + réplica 20 seeds). Límite de tiempo D-007.
- Si R13 no aporta: se documenta el límite práctico de la célula en el INFORME (sin escribir `PARADA:`, que exige T3 refutado).
- Implementación (commit 87e083e): ridge de 88 características, 360 flujos sintéticos (68.613 estados), R² 0,33, MAE 4,2 pp; defaults
  idénticos; fugas D-022/D-032 OK; 0,749 s/decisión.
- Ajuste determinista (r13_tune, seeds 1001–1010): solo aprendido −5,02 (sig.); mezcla w0,5 +1,20 [−0,20; 2,61]; w1 −0,69; w2 −0,26.

## D-034 · R13 mezcla w0,5 como candidata preregistrada (2026-10-21, ANTES de evaluar)
- Candidata = `r13_mix_w05`; misma regla que D-029/D-031: referencia 10 seeds + réplica 20 seeds (D-016), deterministas; se adopta si
  la pareada en la réplica es significativa. Advertencia previa: el ajuste no es monótono en el peso (0,5 bien, 1 y 2 mal), patrón
  parecido al de H1 fuerte (D-029), que no se confirmó.
