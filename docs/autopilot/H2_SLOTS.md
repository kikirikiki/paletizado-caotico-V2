# H2: penalización de ranuras estrechas

Referencia vigente: `arm6_4orient + H1 + H3`. H1 usa `penalty`, peso 0.25,
banda 600 mm; H3 usa presupuesto 100, profundidad 3, anchura 8, topk 15.
H4 está descartada y no forma parte de este trabajo.

**Resultado: H2 no demuestra mejora sobre H1+H3 en evaluación.** La ganadora
de ajuste (`0.1`, 250 mm) obtiene 70.896 % frente a 70.935 %; diferencia
pareada −0.039 pp, IC95 [−1.414; +1.336], no significativa. D-015 tampoco
mejora (27.6 / 55.6 / 75.5 frente a 26.9 / 54.3 / 75.4). Se conserva H1+H3
como referencia y H2 queda desactivada por defecto. Esto no demuestra
equivalencia: el intervalo todavía admite ganancias y pérdidas.
Los tiempos son observacionales con carga variable; no se atribuye a H2 la
diferencia de tiempos entre configuraciones.

## Método y bordes

`narrow_slot_penalty(heightfield, footprint, base_z, slot_min_mm)` mide cuatro
franjas disjuntas que salen perpendicularmente de los lados del candidato.
Cada rayo recorre celdas con altura `<= base_z`, hasta el primer obstáculo
con altura `> base_z` o la pared de la envolvente, incluido el overhang.
Solo suma `0 < g < slot_min_mm`: contacto y umbral exacto no se penalizan.
Se suma `g × tramo transversal` y se divide por el área del footprint.
No incluye las esquinas ni suma dos veces una celda entre lados distintos.

Reutiliza el HeightField de H1 (10 mm, alturas muestreadas en los centros),
con sus bordes explícitos para respetar celdas parciales y orígenes trasladados.
La pared y la cara del candidato conservan sus coordenadas exactas. Los
obstáculos se aproximan por el borde cercano de su primera celda bloqueada;
el error de rasterización es menor que una celda. Un borde de celda que invade
la cara del candidato se recorta a contacto, `g=0`. Las alturas iguales a la
base son transitables. Por ejemplo, un obstáculo real a 355 mm puede quedar
a 350 mm en la rejilla; el umbral se aplica a la geometría rasterizada.
La función es vectorizada y no modifica la rejilla.

`scalar_score -= slot_weight * penalty` en la función compartida de scoring:
afecta a greedy, todas las expansiones H3 y la suma de scores de cada secuencia.
Defaults `slot_weight=0.0`, `slot_min_mm=355`: sin cálculo H2 ni modificación
del score. Parámetros propagados por `run_simulation`,
`PolicyPackerScheduler.from_defaults` y `SchedulerConfig`, sin mutar globales.
La rejilla se comparte con H1 por candidato y entre candidatos greedy.
H3 conserva la construcción por expansión de H1.

JSON: `metrics.pallet_kpis.slots` y `kpi.json.slots`, con penalización media
del elegido, número de elegidos, cambios de elección y coste por candidato.
En greedy el contrafactual retira solo H2 de la misma lista factible, mantiene
H1 y aplica el mismo selector. En H3 retira el coste acumulado H2 de la frontera
explorada, antes de la poda global del beam, y compara la primera acción.
Es un diagnóstico sobre secuencias exploradas: no repite la búsqueda sin H2,
y no mide los cambios que una poda diferente habría introducido.

D-015 se portó de `feat/h4-columnas-online:scripts/run_sweep.py` antes de H2:
solo `accessibility_gap_violations`, `acc_violations_gap5/20/50` y sus resúmenes.
Reproduce el orden real con las tres tolerancias, sin cambiar KPI, validador
ni regla de accesibilidad. Una colocación rechazada sigue en el historial;
`local_z` usa solape XY estricto. Los summaries muestran medias por seed.

## Verificación y coste

Tests sintéticos: contacto con pared/obstáculo, franjas exactas de 350 mm,
huecos anchos, umbral estricto, altura igual a la base, ambos ejes, cuatro
caras, celdas parciales, traslación, pureza, propagación, validación, elección
greedy y scoring de cada expansión H3. La regresión de peso cero compara
hashes SHA256 del contenido `pallets` de los dumps y KPI con los artefactos
H1+H3 de `out/sweeps/h3_eval`, seeds 50021 y 1 (186 cajas).
Se excluyen de la igualdad parámetros/HEAD y tiempos observacionales.

Benchmark determinista: semilla 1001, 40 colocaciones, envolvente 1240×840 mm,
100 footprints y 5000 llamadas; mide kernel y construcción de rejilla aparte.
La construcción es compartida con H1; no es coste incremental de H2.
Resultados numéricos y validación completa en las tablas siguientes.

## Diseño experimental y comandos

Ajuste: seeds 1001–1010, referencia H1+H3 y rejilla completa
`weight ∈ {0.1, 0.25, 0.5, 1, 2} × min_mm ∈ {300, 355, 400}`.
Se elige el mayor KPI medio entre configuraciones sin violaciones, cortes
por deadline ni hard caps. Si gana un borde, se amplía una vez en los ejes
correspondientes, manteniendo las seeds de ajuste. La evaluación única usa
las diez seeds de referencia y las tres configuraciones: arm6_4orient,
H1+H3 y H1+H3+H2 ganadora. Comparación pareada con unidad seed e IC95 t de
Student. Los sweeps son secuenciales y conservan `/tmp/palca_sweep.lock`, con
reintentos de 60 s. Puede haber carga externa de otro trabajo.

```bash
export PYTHONPATH=$(pwd)/src
.venv/bin/python -m pytest tests/test_slots.py tests/test_run_sweep.py tests/test_access_debt.py tests/test_microplanner_deterministic.py tests/test_benchmark_one_pallet_canonical.py -m 'not slow' -q
./scripts/smoke_offline.sh
.venv/bin/python scripts/benchmark_slots.py
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h2_tune.yaml --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 --workers 15 --lock-retry-seconds 60 --name h2_tune --baseline h1_h3
# Después de fijar ganadora:
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h2_eval.yaml --seeds 10 --workers 15 --lock-retry-seconds 60 --name h2_eval --baseline arm6_4orient
```

Artefactos grandes bajo `out/`, sin commits, push ni PR.

## Resultados

Ganadora de ajuste: **h2_w0.1_m250**, `slot_weight=0.1`, `slot_min_mm=250`.
Ganadora inicial: `h2_w0.1_m300`. Ampliación única: {"new_weights": [0.05], "new_minima": [250]}.

Kernel H2: media **0.211 ms/candidato**, p95 0.246 ms; rejilla sola 0.782 ms (compartida con H1).
Si se construyera una rejilla nueva por candidato, kernel + rejilla = 0.993 ms; no es el coste incremental sobre H1.

Verificación: 64 tests específicos OK; smoke completo OK (376 tests más escenario height-slack). `git diff --check` OK.
La regresión de peso cero reproduce exactamente las colocaciones y KPI H1+H3 en las dos seeds canónicas.

### Ajuste: h2_tune

Fuente SHA256: `ab4ce19fcd5ee05627e81cdee5792cab045831343d92109d43b23cfa9c07e18a`.

| Config | KPI % ± sd pp | IC95 % | ms/decisión media / p95 | D-015 gap5 / 20 / 50 |
|---|---:|---|---:|---:|
| h1_h3 | 70.942 ± 1.716 | [69.714; 72.169] | 618.3 / 1382.3 | 27.500 / 50.600 / 77.700 |
| h2_w0.1_m300 | 71.985 ± 1.923 | [70.610; 73.360] | 641.9 / 1349.1 | 26.800 / 55.400 / 80.000 |
| h2_w0.1_m355 | 71.527 ± 2.112 | [70.016; 73.038] | 637.7 / 1368.9 | 26.300 / 52.100 / 78.400 |
| h2_w0.1_m400 | 68.171 ± 1.434 | [67.145; 69.197] | 543.8 / 1119.3 | 24.700 / 49.700 / 69.400 |
| h2_w0.25_m300 | 71.013 ± 2.046 | [69.549; 72.476] | 629.4 / 1290.0 | 29.900 / 56.500 / 77.900 |
| h2_w0.25_m355 | 71.278 ± 1.546 | [70.172; 72.384] | 621.2 / 1305.3 | 30.200 / 52.600 / 77.300 |
| h2_w0.25_m400 | 66.891 ± 2.704 | [64.957; 68.826] | 546.4 / 1120.5 | 22.300 / 42.800 / 62.800 |
| h2_w0.5_m300 | 71.032 ± 1.694 | [69.821; 72.243] | 626.7 / 1317.4 | 29.400 / 53.800 / 77.100 |
| h2_w0.5_m355 | 71.604 ± 1.331 | [70.652; 72.556] | 636.6 / 1311.0 | 29.200 / 52.200 / 77.400 |
| h2_w0.5_m400 | 64.521 ± 1.805 | [63.229; 65.812] | 552.5 / 1175.3 | 15.900 / 39.300 / 66.500 |
| h2_w1_m300 | 71.574 ± 1.679 | [70.373; 72.775] | 616.3 / 1289.1 | 26.800 / 54.300 / 76.000 |
| h2_w1_m355 | 71.079 ± 1.346 | [70.116; 72.041] | 610.7 / 1270.3 | 28.700 / 53.000 / 75.500 |
| h2_w1_m400 | 62.739 ± 2.432 | [60.999; 64.479] | 548.0 / 1121.6 | 16.300 / 35.500 / 61.600 |
| h2_w2_m300 | 71.673 ± 1.373 | [70.691; 72.655] | 598.5 / 1207.4 | 29.100 / 55.500 / 76.200 |
| h2_w2_m355 | 71.477 ± 0.973 | [70.781; 72.173] | 601.9 / 1211.9 | 25.600 / 54.600 / 77.500 |
| h2_w2_m400 | 58.894 ± 5.076 | [55.263; 62.525] | 481.8 / 1016.7 | 13.900 / 34.300 / 59.000 |

Todas las configuraciones: 0 violaciones físicas, 0 cortes por deadline y 0 hard caps.
Resumen completo: `out/sweeps/h2_tune/summary.md` y `summary.json`.

### Ajuste: h2_tune_edge

Fuente SHA256: `ab4ce19fcd5ee05627e81cdee5792cab045831343d92109d43b23cfa9c07e18a`.

| Config | KPI % ± sd pp | IC95 % | ms/decisión media / p95 | D-015 gap5 / 20 / 50 |
|---|---:|---|---:|---:|
| h1_h3 | 70.942 ± 1.716 | [69.714; 72.169] | 618.5 / 1378.1 | 27.500 / 50.600 / 77.700 |
| h2_w0.1_m250 | 72.117 ± 1.605 | [70.969; 73.265] | 642.5 / 1354.2 | 26.300 / 55.100 / 79.900 |
| h2_w0.25_m250 | 71.603 ± 1.130 | [70.794; 72.411] | 626.5 / 1303.5 | 28.900 / 55.400 / 76.900 |
| h2_w0.5_m250 | 71.142 ± 1.651 | [69.961; 72.322] | 638.9 / 1322.5 | 29.300 / 55.000 / 76.600 |
| h2_w1_m250 | 71.816 ± 1.705 | [70.597; 73.036] | 612.9 / 1293.0 | 27.400 / 54.600 / 77.500 |
| h2_w2_m250 | 71.095 ± 1.159 | [70.265; 71.924] | 616.7 / 1258.0 | 27.200 / 54.600 / 78.100 |
| h2_w0.05_m300 | 70.765 ± 1.941 | [69.376; 72.153] | 632.0 / 1391.5 | 26.500 / 52.700 / 77.200 |
| h2_w0.05_m355 | 71.302 ± 1.924 | [69.926; 72.678] | 632.6 / 1375.4 | 26.000 / 52.500 / 77.800 |
| h2_w0.05_m400 | 70.331 ± 3.016 | [68.173; 72.488] | 571.8 / 1188.0 | 21.900 / 43.700 / 67.000 |
| h2_w0.05_m250 | 71.554 ± 2.010 | [70.116; 72.992] | 503.1 / 1112.9 | 27.900 / 53.200 / 77.000 |

Todas las configuraciones: 0 violaciones físicas, 0 cortes por deadline y 0 hard caps.
Resumen completo: `out/sweeps/h2_tune_edge/summary.md` y `summary.json`.

### Evaluación única: diez seeds de referencia

Fuente SHA256: `ab4ce19fcd5ee05627e81cdee5792cab045831343d92109d43b23cfa9c07e18a`.

| Config | KPI % ± sd pp | IC95 % | Δ vs arm6 (pp; IC95) | ms/decisión media / p95 | D-015 gap5 / 20 / 50 |
|---|---:|---|---|---:|---:|
| arm6_4orient | 61.890 ± 1.318 | [60.948; 62.833] | — | 70.0 / 144.8 | 20.700 / 37.700 / 55.700 |
| h1_h3 | 70.935 ± 1.499 | [69.862; 72.007] | 9.044; [7.231; 10.858] | 618.3 / 1341.5 | 26.900 / 54.300 / 75.400 |
| h2_w0.1_m250 | 70.896 ± 2.105 | [69.390; 72.401] | 9.005; [6.860; 11.151] | 508.3 / 1059.5 | 27.600 / 55.600 / 75.500 |

**H1+H3+H2 − H1+H3: -0.039 pp; IC95 [-1.414; 1.336]; n=10; significativo: no.**

Todas las configuraciones: 0 violaciones físicas, 0 cortes por deadline y 0 hard caps.
D-015 es un análisis con tolerancias adicionales: sus conteos se reportan separados de la validación física.
Tiempo observacional bajo 15 workers; p95 de las tablas = media de p95 por seed.

Telemetría H2 (media entre seeds):

| Config | Penalización elegida | Cambios de elección | Kernel media / p95 ms |
|---|---:|---:|---:|
| arm6_4orient | 0.0000 | 0.0 | 0.000 / 0.000 |
| h1_h3 | 0.0000 | 0.0 | 0.000 / 0.000 |
| h2_w0.1_m250 | 0.0997 | 3.3 | 0.213 / 0.288 |

Artefactos: `out/sweeps/h2_eval/summary.md`, `summary.json`,
`paired_h2_vs_h1_h3.json` y `out/sweeps/h2_selection.json`.

Comando de comparación pareada sobre los `kpi.json` (mismo orden de seeds):

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from palca.study.metric import paired_comparison
from palca.study.params import REFERENCE_SEEDS
folder = Path('out/sweeps/h2_eval')
winner = json.loads(Path('out/sweeps/h2_selection.json').read_text())['winner']['name']
rows = lambda name: [json.loads((folder / name / str(seed) / 'kpi.json').read_text()) for seed in REFERENCE_SEEDS]
print(json.dumps(paired_comparison(rows('h1_h3'), rows(winner)), indent=2))
PY
```

Comando de ampliación única ejecutado:

```bash
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h2_tune_edge.yaml --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 --workers 15 --lock-retry-seconds 60 --name h2_tune_edge --baseline h1_h3
```

## Archivos cambiados y ejecución

- `src/palca/scoring/slots.py`: kernel puro H2.
- `src/palca/scoring/access_debt.py`: bordes exactos en HeightField; scoring H1 sin cambios.
- `src/palca/scheduler/scheduler_v1.py`: parámetros, scoring greedy/expansiones H3 y telemetría.
- `src/palca/integration/policy_packer_sched.py`, `src/sim/run.py`: propagación y JSON.
- `scripts/run_sweep.py`: D-015 y resumen/JSON de H2.
- `scripts/benchmark_slots.py`: benchmark reproducible del kernel y rejilla.
- `scripts/benchmark_one_pallet_canonical.py`, `configs/benchmarks/one_pallet_canonical.json`: contrato de los dos defaults nuevos.
- `tests/test_slots.py`, `tests/test_run_sweep.py`: tests H2 y D-015.
- `configs/sweeps/h2_tune.yaml`, `h2_tune_edge.yaml`, `h2_eval.yaml`: ajuste, ampliación y evaluación.
- `docs/autopilot/H2_SLOTS.md`: método, resultados y reproducción.

| Comando (completo en los bloques anteriores) | Resultado |
|---|---|
| pytest específico, cinco archivos, `-m 'not slow'` | 64 tests OK |
| `export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` | 376 tests y escenario height-slack OK |
| `scripts/benchmark_slots.py` | Kernel 0.211 ms media / 0.246 ms p95 |
| `scripts/run_sweep.py`, `h2_tune` | 160 ejecuciones OK |
| `scripts/run_sweep.py`, `h2_tune_edge` | 100 ejecuciones OK |
| `scripts/run_sweep.py`, `h2_eval` | 30 ejecuciones OK; evaluación única |
| `paired_comparison`, kpi.json en orden REFERENCE_SEEDS | n=10; −0.039 pp, IC95 [−1.414; +1.336] |
| `git --no-pager diff --check` | OK |

Las 290 ejecuciones de sweeps: 0 violaciones físicas, 0 cortes por deadline,
0 hard caps y 0 cajas sin colocar. Las tablas D-015 registran un diagnóstico
adicional, separado de estos ceros. No se cambian la métrica congelada,
el validador, la regla de accesibilidad ni los Excel. Sin commits, push ni PR.
