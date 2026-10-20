# H3: búsqueda del buffer con presupuesto determinista

H3 se evalúa por separado según D-012, encima de H1 (`penalty`, peso 0.25,
banda 600 mm). No cambia la métrica congelada, el validador, la accesibilidad,
las orientaciones ni los Excel. La rama está apilada sobre H1.

## Contrato y regla de corte

`run_simulation`, CLI, `PolicyPackerScheduler.from_defaults` y `SchedulerConfig`
aceptan `micro_plan_node_budget=0` y `micro_plan_hard_cap_ms=10000`.
Se requiere `micro_plan=True` para activar la búsqueda.

- Presupuesto 0: se conserva el deadline `time_budget_ms`, el fallback anterior
  y el significado histórico del contador (solo expansiones factibles).
- Presupuesto positivo B: cada llamada a `_expand_beam_node` consume **una**
  unidad, incluso si no produce candidato factible. No se inicia la llamada
  B+1. `nodes_expanded <= B`, sin sobrepaso por anchura. Si el árbol se agota
  antes, se consumen menos unidades. Los intentos fallidos también cuestan.
- Al cortar se procesan las expansiones ya calculadas del padre actual, se
  ordena la profundidad parcial con las mismas funciones y orden estable del
  beam original y se conserva su mejor secuencia. No se completa esa profundidad
  realizando expansiones nuevas. Un empate conserva el orden de enumeración.
- El fallback greedy conserva su ventana finita y solo recibe el deadline de
  emergencia. `time_budget_ms` no limita ni el beam ni su fallback en H3.
- El reloj sirve para telemetría y **exclusivamente** para la salvaguarda H3.
  Se comprueba entre expansiones/padres/profundidades, antes del fallback y al
  terminar la decisión. `hard_cap_hits` cuenta una vez por decisión afectada,
  incluso si la última expansión rebasa el límite y no quedan más acciones.
  Cualquier run con hits > 0 es **NO determinista** y no es elegible.
  Es un corte cooperativo: no interrumpe una llamada Python a mitad de expansión;
  puede sobrepasar 10000 ms por esa operación y por la finalización parcial.
- Se rechazan `max_seconds_per_item>0` y `settle_timeout_ms>0` con H3 para evitar
  otros cortes temporales ocultos. Si se activa batchfill, usa únicamente la
  salvaguarda, no su deadline temporal local; no se usa en este experimento.
  Los parámetros de configuración son inmutables; no se cambian globales.

Con ventana 15, los máximos teóricos de intentos son 135 (d2/w8),
255 (d3/w8), 480 (d3/w16: en profundidad 1 solo hay 15 padres) y 375 (d4/w8).
Por ello varios presupuestos de la rejilla pueden recorrer el mismo árbol.
Un presupuesto mayor no fuerza trabajo cuando el árbol ya se agotó.

## Telemetría y pruebas

`metrics.pallet_kpis` contiene `micro_plan_nodes_expanded` (lista por llamada),
`micro_plan_nodes_expanded_mean/p95/max/total`,
`micro_plan_time_ms_min/mean/p95/max`, `hard_cap_hits` y
`micro_plan_deterministic`. En H3 el tiempo del microplanner incluye el fallback.
`metrics.decision_timing` mide la decisión completa de la política, incluidas
llamadas sin plan, y conserva todas las duraciones. Es la medida usada para D-007.
`kpi.json` de cada sweep incluye tiempos, nodos, hard_cap_hits y validación física;
los summaries incluyen el total de hits por configuración.

`tests/test_microplanner_deterministic.py` comprueba el presupuesto exacto,
los intentos no factibles, el fallback, el deadline legado con presupuesto 0,
la activación del hard cap y su marca de no determinismo, la propagación y
validación de parámetros y dos ejecuciones del Excel canónico de 186 cajas,
seed 50021, presupuesto 7: igualdad exacta de dump, KPI y lista de nodos.
El test canónico usa `time_budget_ms=1` para detectar una dependencia accidental.
Los tiempos observacionales de JSON no forman parte de la igualdad bit a bit.

Se prueban además velocidades de reloj distintas por debajo del hard cap, el
sobrepaso en la última expansión sin intento siguiente y la eliminación del
presupuesto temporal local de batchfill en H3.

## Diseño experimental

Coste: dos seeds de ajuste (1001, 1002), depth 3, width 8, topk 15,
presupuestos 100, 250, 500, 1000 y 2000; `--workers 1`, BLAS/OMP=1.
Se comprueba `/tmp/palca_sweep.lock` inmediatamente antes de lanzar el runner;
el runner adquiere y mantiene el mismo flock durante toda la medición.
No se ejecutan otros sweeps simultáneos bajo ese lock. Se mide **con la carga
externa de techo offline indicada por el usuario (hasta 8 procesos en otro
worktree)**; no se presenta como máquina desocupada.
`out/h3/cost_environment.json` registra hora, comprobación y load average.

Se complementa la tabla solicitada con `h3_cost_geometry` (también un worker,
las mismas dos seeds y el mismo bloqueo) para medir d2/w8, d3/w16 y d4/w8.
No se presupone que el coste de d3/w8 certifique otras geometrías.
Para árboles agotados se comparte la medida entre presupuestos superiores al
máximo teórico: d2/w8 con B>=250, d3/w16 y d4/w8 con B>=500.
La rejilla completa queda en `h3_tune.yaml`; `h3_tune_eligible.yaml` contiene
solo las alternativas admitidas y la referencia H1. `cost_screening.json`
registra el origen de cada medida y cada exclusión.

D-007 exige media <=1000 ms y p95 <=3000 ms por decisión. Se calculan agrupando
todas las duraciones de las dos seeds (incluidas decisiones sin colocación),
no promediando percentiles. Los summaries generales del runner siguen mostrando
la media entre seeds de sus percentiles; ambas convenciones se distinguen. La selección se hace
solo con las seeds de ajuste 1001–1010 y con cero violaciones/hard_cap_hits.
La evaluación usa una configuración ganadora fija y las 10 seeds de referencia;
se repite con dos nombres independientes sin volver a seleccionar.
Comparación pareada H3−H1 e IC95 t de Student, unidad = seed.

Los artefactos grandes se guardan bajo `out/` y no se versionan.
No se hacen commits, push ni PR en este trabajo.

## Comandos

Desde la raíz del worktree:

```bash
export PYTHONPATH=$(pwd)/src
.venv/bin/python -m pytest tests/test_microplanner_deterministic.py -q
./scripts/smoke_offline.sh
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h3_cost.yaml --seed-list 1001,1002 --workers 1 --name h3_cost --baseline h3_n100_d3_w8 --lock-retry-seconds 60
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h3_cost_geometry.yaml --seed-list 1001,1002 --workers 1 --name h3_cost_geometry --lock-retry-seconds 60
# Ajuste y evaluación se lanzan secuencialmente, tras filtrar por coste:
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h3_tune_eligible.yaml --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 --workers 15 --name h3_tune --baseline h1_w0.25_b600 --lock-retry-seconds 60
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h3_eval.yaml --seeds 10 --workers 15 --name h3_eval_a --baseline h1_w0.25_b600 --lock-retry-seconds 60
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h3_eval.yaml --seeds 10 --workers 15 --name h3_eval_b --baseline h1_w0.25_b600 --lock-retry-seconds 60
```

## Resultados (2026-10-16)

Codex se quedó sin cuota durante la medición de coste (quedaron medidos n100, n250 y n500 con d3/w8; n1000 solo en la seed 1001).
El director revisó el código (13 tests de determinismo en verde) y ejecutó el ajuste y la evaluación con el runner ya revisado
(DECISIONS D-011). Diferencias frente al diseño previsto: `h3_tune_v2` sustituye n=2000 por n=100 a la vista del coste medido.
Además, se amplió una vez hacia abajo (`h3_tune_v3`: n=25, 50), porque la ganadora quedó en el borde inferior.

### Coste en un proceso (con la carga de fondo del techo offline, seeds 1001–1002, d3/w8)
| Presupuesto | KPI 1001 / 1002 | ms/decisión media | p95 | máx. |
|---|---|---|---|---|
| 100 | 71,70 / 71,01 % | 364–390 | ~800 | 1425 |
| 250 | 70,97 / 69,25 % | 904–928 | ~2040–2110 | 2793 |
| 500 | 67,99 / 69,25 % | 899–961 | ~2000–2050 | 2659 |

### Ajuste (seeds 1001–1010, 15 workers; ms/decisión bajo carga)
| Config | KPI % | ms/dec media / p95 |
|---|---|---|
| H1 (referencia) | 66,71 | 83 / 181 |
| n25 d3 w8 | 66,87 | 169 / 338 |
| n50 d3 w8 | 69,74 | 273 / 589 |
| **n100 d3 w8 (ganadora)** | **70,94** | 651 / 1454 |
| n100 d4 w8 | 70,94 (idéntico: con 100 nodos no se llega a d4) | 650 / 1442 |
| n100 d3 w16 / d2 w8 | 70,89 / 70,67 | ~655 / ~1450 |
| n250 (d2/d3/d3w16/d4) | 70,24 / 70,35 / 69,45 / 70,77 | 744–1518 / 1818–3371 |
| n500, n1000 | 68,77–70,71 | hasta 2294 / 5090 (algunas incumplen D-007) |

Más presupuesto no mejora: el factor limitante es el objetivo del beam, no la profundidad de búsqueda.

### Evaluación (10 seeds de referencia, ejecutada dos veces: `h3_eval`, `h3_eval_rep`)
| Config | KPI % | IC95 % | Δ vs baseline (pp, IC95) | ms/dec media / p95 |
|---|---|---|---|---|
| baseline arm6_4orient | 61,890 | [60,948; 62,833] | — | 70 / 148 |
| H1 | 64,697 | [63,473; 65,922] | +2,807 [1,041; 4,572] | 77 / 166 |
| **H1 + H3 (n100 d3 w8)** | **70,935 ± 1,50** | [69,862; 72,007] | **+9,044 [7,231; 10,858]** | 486 / 1044 |

- **H1+H3 vs H1: +6,24 pp, IC95 [5,11; 7,36]**, significativo.
- **Determinismo demostrado**: las dos ejecuciones dan KPI y utilización por palet idénticos en las 10 seeds. Hay 0 `hard_cap_hits`,
  0 violaciones y 0 cajas sin colocar.
- Tiempo real (D-007): media 0,49 s y p95 1,04 s por decisión con 15 procesos en paralelo; en un solo proceso, unos 0,39 / 0,8 s.
- Respecto a las metas (docs/COTAS.md): 70,94 % cierra el **48,9 % del gap**, a 0,2 pp de T1 (71,15 %). Por D-012, H3 se
  reporta aparte hasta que el responsable confirme que una búsqueda determinista sobre el buffer es válida en producción.

```bash
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h3_tune_v2.yaml --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 --workers 15 --name h3_tune_v2 --baseline h1_w0.25_b600 --lock-retry-seconds 60
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h3_tune_v3.yaml --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 --workers 15 --name h3_tune_v3 --baseline h1_w0.25_b600 --lock-retry-seconds 60
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h3_eval.yaml --seeds 10 --workers 15 --name h3_eval --baseline arm6_4orient --lock-retry-seconds 60
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h3_eval.yaml --seeds 10 --workers 15 --name h3_eval_rep --baseline arm6_4orient --lock-retry-seconds 60
```
