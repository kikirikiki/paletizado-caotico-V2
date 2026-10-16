# H4: plantilla de columnas online

Referencia vigente: arm6_4orient + H1 (`penalty`, peso 0.25, banda 600) +
H3 (`micro_plan=True`, presupuesto 100, depth 3, width 8, topk 15).
H4 usa la ventana visible k=15; no reordena ni consulta el Excel completo.

La envolvente 1240×840 se divide sin separaciones: A1/A2 miden 620×W_A;
B1/B2 miden 445×(840−W_A), y B3, 350×(840−W_A). Los rangos de la
especificación se expresan desde el origen de la envolvente: en coordenadas
físicas se trasladan −20 mm en ambos ejes por el overhang de producción.
`w445`, `w450` y `w455` fijan W_A. `auto` fija W_A una vez por palet físico:
moda del W de las cajas visibles con L entre 550 y 620 y W entre 400 y 500;
en empate, el menor W, y sin esa familia, 445. Las copias del beam conservan
ese ancho y no lo recalculan con llegadas simuladas.

Para cada caja y columna se enumeran **todas** las orientaciones permitidas
por `PalletModel._orientations`, incluido el gate oficial. Se exige que la
huella quepa y cubra al menos `column_min_fill` del área de columna. Se coloca
en su esquina mínima sobre el máximo techo que solapa la columna. Se usa la
misma ruta heightfield de producción para soporte, accesibilidad, settle y
puntuación; si settle cambia la esquina o la altura de columna, se descarta.
No hay desplazamientos de 2 mm ni separaciones configurables. La holgura que
queda porque una caja mide menos que su columna es geométrica y se mide con D-015.

Greedy maximiza lexicográficamente `(−techo_columna, fill, scalar_score_H1)`.
El beam genera una acción por `(caja, columna, orientación)` factible, sin
colapsarlas a la mejor posición por caja. Ordena esas acciones con la misma
regla antes de consumir el presupuesto H3. Entre secuencias conserva primero
el número de cajas colocadas y después minimiza la suma de techos resultantes,
maximiza la suma de fills y desempata por el score acumulado de producción,
con H1. Esta extensión aditiva permite a H3 anticipar el equilibrio de las
columnas a varias decisiones. Los empates conservan el orden de enumeración.
El presupuesto cuenta cada expansión intentada, como en H3; la generación y
puntuación de acciones también está incluida en el tiempo de decisión.

En cada nodo, solo si **ninguna** caja visible tiene candidato de columna se
habilita `column_fallback=free`: posiciones de producción y H1. `none` no
habilita posiciones libres. Las secuencias sin ninguna acción de columna
conservan el ranking H3; las mixtas contabilizan también el techo de las
acciones libres. `column_template=off` sigue la ruta anterior; los defaults
son `off`, 0.85 y `free`. Se requiere heightfield, envolvente 1240×840 y una
posición de palet por destino.

Telemetría JSON: `metrics.pallet_kpis.columns` y `kpi.json.columns` registran
selecciones de columna, selecciones libres, columnas de pie y los anchos
fijados al abrir palets. El tiempo completo incluye generación de candidatos,
controles, puntuación H1 y búsqueda. No se cambian KPI, validador, accesibilidad,
orientaciones, gate oficial ni Excel.

D-015 se calcula sobre cada palet físico, incluidos los END, en orden real de
colocación. `local_z` usa exclusivamente solape XY estricto con cajas anteriores;
una colocación cuenta una vez si alguna caja anterior con `gap_x <= G` y
`gap_y <= G` tiene techo `> local_z + delta`. Se conserva todo el historial
observado, incluso tras una infracción contrafactual, y se reinicia por palet.
Delta 0 desactiva el control. Los summaries muestran la media por seed de
`acc_violations_gap5/20/50`; son diagnósticos y no alteran el KPI.

Los artefactos grandes quedan en `out/sweeps/`, sin versionar. No hay commits,
push ni PR. La evaluación fija la ganadora exclusivamente con seeds 1001–1010;
la palanca 2400 se etiqueta `bound` y nunca compite para elegirla.

Reproducción desde la raíz del worktree (sweeps secuenciales):

```bash
export PYTHONPATH=$(pwd)/src
.venv/bin/python -m pytest tests/test_columns_online.py tests/test_run_sweep.py tests/test_microplanner_deterministic.py tests/test_benchmark_one_pallet_canonical.py -q
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h4_tune.yaml --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 --workers 15 --name h4_tune --baseline h1_h3 --lock-retry-seconds 60
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h4_tune_edge.yaml --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 --workers 15 --name h4_tune_edge --lock-retry-seconds 60
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h4_eval.yaml --seeds 10 --workers 15 --name h4_eval --baseline arm6_4orient --lock-retry-seconds 60
./scripts/smoke_offline.sh
```

Pruebas: teselado completo sin solapes ni separaciones, colocaciones que se
tocan, rechazo real por accesibilidad y soporte, gate de producción frente a
2400, equilibrio A1/A2 con cajas 605×445×355 en greedy y beam, acciones de
columna distintas para una misma caja en el beam, prioridad techo/fill,
`auto` fijado con cajas visibles y ambos fallbacks. Con `off`, las seeds
50021 y 1 reproducen el hash del dump completo y el KPI H1 de `h3_eval`,
congelados antes de H4. Las pruebas D-015 cubren orden, solape local, umbral
estricto de altura, tolerancia inclusiva en ambos ejes, delta 0 y concordancia
con `RobotAccessibilityControl` en G=0. Sobre los dumps históricos de 1860
cajas se reproduce exactamente D-015: baseline 207/377/557, H1 145/386/611 y
H1+H3 269/543/754 para G=5/20/50.

## Resultados finales (2026-10-17)

Ganadora fijada antes de evaluar: `h4_w450_f85`, `column_template=w450`, `column_min_fill=0.85`, `column_fallback=free`, gate oficial 400.
Selección: máximo KPI con diez seeds completas, cero violaciones, cero cajas sin colocar y cero hard_cap_hits; en empate, fill más cercano a 0.85 y después nombre.
Se amplió una vez a fill 0.95 para las cuatro plantillas. Las plantillas son categorías cerradas de la API, no una variable continua de anchura.

Ajuste: seeds 1001–1010. Coste de esta tabla = media entre seeds de media/p95 por decisión, con 15 workers.

| Config | KPI % | ms/decisión media / p95 | gap50/seed |
|---|---:|---:|---:|
| h1_h3 | 70.942 | 619 / 1383 | 77.7 |
| h4_w445_f75 | 71.216 | 421 / 1095 | 69.1 |
| h4_w445_f85 | 71.690 | 437 / 1137 | 71.3 |
| h4_w450_f75 | 69.582 | 273 / 835 | 69.0 |
| h4_w450_f85 | 71.976 | 297 / 888 | 68.8 |
| h4_w455_f75 | 67.686 | 231 / 725 | 71.3 |
| h4_w455_f85 | 70.982 | 240 / 789 | 71.6 |
| h4_auto_f75 | 69.859 | 396 / 1089 | 70.3 |
| h4_auto_f85 | 70.393 | 335 / 913 | 67.1 |
| h4_w445_f95 | 71.207 | 520 / 1267 | 77.3 |
| h4_w450_f95 | 70.071 | 460 / 1230 | 71.9 |
| h4_w455_f95 | 70.365 | 494 / 1297 | 76.3 |
| h4_auto_f95 | 70.790 | 400 / 1023 | 71.4 |

Evaluación única: seeds 50021, 1, 42, 100, 777, 5678, 9999, 12345, 99999, 314159.
Coste de evaluación = media/p95 de **todas** las duraciones agrupadas, incluidas decisiones sin colocación; observacional con 15 workers. No se promedian percentiles en esta tabla.

| Config | KPI % | IC95 % | ms/decisión media / p95 | gap5 / gap20 / gap50 por seed |
|---|---:|---|---:|---:|
| arm6_4orient | 61.890 | [60.948; 62.833] | 70 / 147 | 20.7 / 37.7 / 55.7 |
| h1 | 64.697 | [63.473; 65.922] | 78 / 165 | 14.5 / 38.6 / 61.1 |
| h1_h3 | 70.935 | [69.862; 72.007] | 626 / 1373 | 26.9 / 54.3 / 75.4 |
| h1_h3_h4 | 70.314 | [69.224; 71.404] | 299 / 897 | 30.9 / 57.9 / 66.3 |
| PALANCA_h4_gate2400 | 72.497 | [71.925; 73.069] | 265 / 1149 | 34.3 / 50.0 / 58.5 |

Pareadas en evaluación, unidad = seed, IC95 t de Student. La palanca se presenta separada y no interviene en la selección.

| Variante | Referencia | Δ pp | IC95 pp | Significativa |
|---|---|---:|---|---|
| h1_h3_h4 | arm6_4orient | +8.424 | [6.597; 10.251] | sí |
| h1_h3_h4 | h1 | +5.617 | [4.135; 7.100] | sí |
| h1_h3_h4 | h1_h3 | -0.620 | [-1.596; 0.355] | no |
| PALANCA_h4_gate2400 | arm6_4orient | +10.606 | [9.430; 11.782] | sí |
| PALANCA_h4_gate2400 | h1 | +7.799 | [6.588; 9.011] | sí |
| PALANCA_h4_gate2400 | h1_h3 | +1.562 | [0.448; 2.676] | sí |

Gate medido sobre 1860 cajas por variante: con 400 hay 566 colocaciones de pie (138 desde suelo), y la primera por palet solo aparece con altura previa mínima 2010 mm. Con la PALANCA 2400 hay 692 de pie (204 desde suelo), primera altura previa mínima 0 mm.
La variante oficial selecciona 1215 acciones de columna y 645 libres; la palanca selecciona 1464 y 396. El test de palet vacío confirma que 400 bloquea las orientaciones stand_hw iniciales y 2400 las permite; el contraste cuantifica la limitación sin modificarla.

En los 180 runs finales (90 ajuste + 40 ampliación + 50 evaluación): **0 violaciones del validador, 0 infracciones de accesibilidad G=0, 0 cajas sin colocar, 0 hard_cap_hits y 0 cortes temporales**. Los controles baseline, H1 y H1+H3 reproducen exactamente KPI, utilización por palet y dump completo de `h3_eval` en las diez seeds (30/30). El hash del Excel coincide; la auditoría recalcula D-015 sobre todos los dumps.

Artefactos: [ajuste](../../out/sweeps/h4_tune/summary.md), [ampliación](../../out/sweeps/h4_tune_edge/summary.md), [evaluación](../../out/sweeps/h4_eval/summary.md), [selección](../../out/sweeps/h4_selection.json), [auditoría](../../out/sweeps/h4_audit.json).

Hash del código local usado en todos los runs finales: `d26c5ad37180c56d39ad2f4d582ad978995eb81fe5eafa74cbb3f5211cf5bfc1`.

**H4 no demuestra una mejora significativa frente a H1+H3 en esta evaluación**; el IC95 pareado incluye cero. La referencia vigente sigue siendo H1+H3.

La integración completa de columnas en el beam cumple D-007: 299 ms de media
por decisión y p95 897 ms, frente a 626/1373 ms de H1+H3 en esta evaluación.
Ambos percentiles se calculan agrupando duraciones completas. El resultado
oficial mantiene el gate de 400; 2400 es exclusivamente una palanca.

Archivos cambiados:

- `src/palca/scheduler/columns.py`, `src/palca/scheduler/scheduler_v1.py`.
- `src/palca/packer/pallet_model.py`, `src/palca/integration/policy_packer_sched.py`, `src/sim/run.py`.
- `scripts/run_sweep.py`, `scripts/benchmark_one_pallet_canonical.py`.
- `configs/benchmarks/one_pallet_canonical.json`.
- `configs/sweeps/h4_tune.yaml`, `configs/sweeps/h4_tune_edge.yaml`, `configs/sweeps/h4_eval.yaml`.
- `tests/test_columns_online.py`, `tests/test_run_sweep.py`.
- `docs/autopilot/H4_COLUMNS.md`.

Verificación final: `export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh`
**OK: 386 tests aprobados y escenario height-slack OK**.
`git --no-pager diff --check`: **OK**. Los sweeps finales ejecutaron 90, 40 y
50 tareas, respectivamente, todos con `--workers 15 --lock-retry-seconds 60`
y sin solapamiento. La auditoría final se conserva en `out/sweeps/h4_audit.json`.
