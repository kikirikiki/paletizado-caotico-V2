# Rendimiento de H6 sin cambiar decisiones

Configuración: miembro `h6_sin_gate` de `configs/sweeps/h6_sin_gate.yaml`.
H1+H3+H5+H6, gate 2400 mm, presupuesto 57 nodos, profundidad 3,
anchura 8, top-k 15, dos hojas y seis llegadas sintéticas por muestra.
No se modifican parámetros, orden, desempates, métrica, validador ni controles.

Entorno: Intel Xeon W-2145 @ 3.70 GHz, Python 3.12.3, Linux WSL2.
Las medidas de coste se ejecutan en serie, un proceso y un hilo BLAS,
sin otros sweeps. `decision_timing.durations_ms` incluye decisiones sin plan.
El perfil cProfile se mide por separado y no cuenta para D-007.

## Equivalencia

Fixtures completos: `tests/fixtures/h6_perf/{1,42}_dump.json` y sus KPI.
Procedencia: `out/sweeps/h6_sin_gate_ref/h6_sin_gate`, hash de entrada y
configuración preservados en `manifest.json`. Antes de optimizar se comprobó
que `git diff a1950a4 HEAD -- src` estaba vacío.

Se compara **todo** el dump, incluidos parámetros, palets y placements,
normalizando únicamente `git_head` (procedencia). Los KPI se comparan con
igualdad exacta, sin tolerancias: todos los campos salvo procedencia, rutas de
salida y tiempos observados. También se comparan los arrays de nodos y
rollouts, las violaciones y los contadores de deuda. Se excluyen explícitamente
`decision_timing`, `wall_time_s`, `worker_pid`, `thread_env`, hashes, `params`
(ya comparados en el dump), `micro_plan_time_ms_{min,mean,max,p95}` y
`access_debt.candidate_{mean,p95}_ms`. Estas duraciones deben cambiar con una
optimización; no forman parte del KPI congelado.

## Reproducción

Desde la raíz, con la configuración y el Excel originales:

```bash
export PYTHONPATH=$(pwd)/src:$(pwd)
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
.venv/bin/python scripts/perf_h6.py --out out/perf_h6/after --seed-list 1001,1002
.venv/bin/python scripts/perf_h6.py --out out/perf_h6/profile_after --seed-list 42 --profile-seed 42
.venv/bin/python scripts/run_sweep.py --configs out/perf_h6/after/official.yaml --seeds 10 --workers 4 --name h6_rapido_ref
.venv/bin/python scripts/perf_h6.py --out out/sweeps/h6_rapido_ref --reference out/sweeps/h6_sin_gate_ref --compare-only
.venv/bin/python -m pytest tests/test_h6_perf.py
./scripts/smoke_offline.sh
```

`perf_h6.py` extrae el miembro oficial del YAML sin cambiarlo y guarda una
copia `official.yaml`; así el sweep no ejecuta también `h6_generic`.
Los perfiles, tiempos individuales y resultados de comparación quedan en
`out/`, fuera de los fixtures y sin añadirlos a Git. Para medir «antes», ejecutar
los mismos comandos sobre el código original; los fixtures nunca se regeneran
con el código optimizado.

## Optimización elegida

El perfil descarta priorizar `deepcopy`: solo consume 3,82 s acumulados
frente a 440,59 s en rollouts (486,24 s de episodio perfilado).
Se incorpora una caché local a cada `PalletModel`, válida para un snapshot
exacto de placements inmutables y rectángulos libres. Reutiliza:

- Altura bajo un footprint repetido.
- Lista ordenada de candidatos XY para unas dimensiones repetidas, con el mismo tope de 120.
- Fragmentación y número de rectángulos tras la proyección 2D del footprint.
- Resultado del control robot integrado, que depende solo del footprint,
  delta y geometría previa. Al reutilizarlo se conserva el placement actual
  con su ID, altura, orientación, peso, prioridad y capacidad de carga.

`commit_place` invalida la caché. También se detectan cambios externos de
geometría, incluso reemplazar un placement manteniendo la longitud de la lista.
Las copias profundas de beam/rollout descartan la caché derivada; el estado
mutable real se sigue copiando como antes. No se comparten diccionarios entre
ramas, ni se mutan parámetros globales. Los controles de estabilidad siguen
ejecutándose en cada candidato, manteniendo sus contadores y orden originales.
La ruta sin caché permanece para geometría, proyección o controles personalizados.

Las alturas aún no almacenadas se calculan en bloque con comparaciones enteras
NumPy: exactamente los mismos solapes estrictos XY y máximo de cotas superiores
que el bucle escalar. Los candidatos se siguen procesando en su orden original.
Con presupuesto local de candidatos/tiempo se conserva la consulta escalar;
también hay fallback escalar para enteros fuera de int64. No se vectorizan ni
se modifican los controles físicos.

Los tests unitarios contrastan previews completos y estadísticas contra una
proyección idéntica sin caché, prueban presupuestos de candidatos, cambios de
geometría/delta y aislamiento de las copias. Los episodios golden verifican
además decisiones, orden, desempates y KPI sobre el flujo completo.

## Coste en un proceso

Duraciones en segundos; mismo proceso por fase, sin perfilador ni otros sweeps.
El agregado concatena las duraciones individuales, no promedia percentiles.

| Seed | Decisiones | Media antes | Media después | p95 antes | p95 después | Máximo antes | Máximo después |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1001 | 196 | 1,094927 | 0,904953 | 2,500000 | 2,209294 | 3,179843 | 2,920644 |
| 1002 | 195 | 1,217989 | 1,009196 | 2,763780 | 2,391198 | 3,508809 | 2,949253 |
| Conjunto | 391 | 1,156301 | 0,956941 | 2,619199 | 2,361064 | 3,508809 | 2,949253 |

La media conjunta baja **17,24 %** y cumple D-007 (media ≤ 1 s y p95 ≤ 3 s).
**No se alcanza el objetivo de 0,8 s**; la seed 1002 individual queda en 1,009 s.
Ambas fases tienen cero hard-cap hits y cero deadline cutoffs. Los dumps y KPI
de estas dos seeds también coinciden por completo según el comparador anterior.
La caja no colocada de 1001 y su aviso de deadlock son idénticos antes y después.

Datos: `out/perf_h6/{before,after}/h6_sin_gate/{1001,1002}/kpi.json` y
`out/perf_h6/comparison_timing.json`. La base se capturó con el módulo original
ya importado en un proceso antes de editarlo; las tres ejecuciones de ese proceso
usaron ese mismo código cargado. Los hashes de fuentes en disco calculados para
las tareas posteriores pueden incluir las ediciones preparadas mientras corría
el proceso; la versión del motor base es `904a8fc` (idéntica a la referencia).

## cProfile, seed 42 oficial

Tiempo acumulado en segundos. Las filas se solapan por llamadas anidadas;
no se deben sumar. Se omiten wrappers repetidos de integración/simulador.

| Función / trabajo | Antes | Después |
|---|---:|---:|
| `run_task` (episodio completo) | 486,236 | 409,718 |
| `_choose_action_micro` | 485,071 | 409,133 |
| `_preview_place_heightfield` | 465,101 | 385,883 |
| `_stochastic_rollout_value` | 440,588 | 369,298 |
| `_choose_action_greedy` | 439,918 | 366,762 |
| `StabilityPlacementControl.evaluate` | 127,394 | 131,898 |
| `RobotAccessibilityControl.evaluate` | 111,560 | 89,248 |
| Alturas: `_height_under_footprint_mm` → `fill_heights` | 70,297 | 11,982 |
| `settle_placement` | 48,721 | 50,854 |
| `MaxRects2D.simulate_place` | 41,936 | 35,075 |
| `_supporting_placements` | 29,413 | 30,488 |
| `_heightfield_xy_candidates` | 26,506 | 23,214 |
| `_collides` | 19,844 | 20,857 |
| `copy.deepcopy` | 3,820 | 4,024 |

Ambos perfiles ejecutan exactamente 34 003 previews, 3 012 expansiones y
370 rollouts estocásticos. Estabilidad sigue evaluándose 1 769 734 veces;
accesibilidad baja de 1 870 154 a 1 434 354 evaluaciones físicas por reutilización
exacta. El tiempo restante se concentra en estabilidad y accesibilidad.
Las copias profundas no son el principal consumidor y se mantienen.

Los dumps completos y KPI de la seed perfilada también son idénticos a la
referencia; ambos perfiles tienen cero hard-cap hits. Los archivos son
`out/perf_h6/before.prof`, `out/perf_h6/before_profile.txt`,
`out/perf_h6/before_profile.json`, `out/perf_h6/after/42.prof` y
`out/perf_h6/after/profile.json`. Pueden abrirse con `python -m pstats`.

Captura original (antes de editar `src/`): un proceso importó `run_task`, perfiló
42 mediante `cProfile.Profile`, y después ejecutó 1001 y 1002 sin perfilador,
con los tres límites de hilos fijados a 1 y `sweep_lock` durante toda la fase.
Captura final, incluida comparación con esos mismos dumps:

```bash
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONPATH=src:. \
  .venv/bin/python scripts/perf_h6.py --out out/perf_h6/after \
  --seed-list 1001,1002,42 --profile-seed 42 --reference out/perf_h6/before
```

## Diez seeds de referencia

`run_sweep.py` ejecutó 10 tareas nuevas, sin reutilizar resultados, con cuatro workers.
Se compararon **los dumps completos y todos los KPI deterministas**, no solo el valor agregado.

| Seed | KPI antes = después (sin redondeo) | Cajas | Palets cerrados | Violaciones |
|---|---:|---:|---:|---:|
| 50021 | 0.7653824782286066 | 186 | 8 | 0 |
| 1 | 0.7659301132772497 | 186 | 8 | 0 |
| 42 | 0.7610128798243088 | 186 | 8 | 0 |
| 100 | 0.7788434147285426 | 186 | 8 | 0 |
| 777 | 0.7710935249855991 | 186 | 8 | 0 |
| 5678 | 0.7838614358818965 | 186 | 8 | 0 |
| 9999 | 0.7649621763292851 | 186 | 8 | 0 |
| 12345 | 0.7794228955653162 | 186 | 8 | 0 |
| 99999 | 0.778276634704621 | 186 | 8 | 0 |
| 314159 | 0.7576461643545187 | 186 | 8 | 0 |

Cero hard-cap hits y cero deadline cutoffs en las diez seeds. Los arrays de nodos,
rollouts, violaciones y deuda coinciden exactamente. El informe de comparación
está en `out/sweeps/h6_rapido_ref/equivalence.json`; las salidas individuales,
manifest y resumen están en ese mismo directorio.

Comandos ejecutados:

```bash
.venv/bin/python scripts/run_sweep.py --configs out/perf_h6/after/official.yaml --seeds 10 --workers 4 --name h6_rapido_ref
.venv/bin/python scripts/perf_h6.py --out out/sweeps/h6_rapido_ref --reference out/sweeps/h6_sin_gate_ref --compare-only
```

## Validación final

- `smoke_offline.sh`: **OK**, incluido su escenario final height-slack.
- Suite completa: **459 tests pasados, 0 fallos, 0 errores, 0 omitidos**
  (821,635 s de pytest). Incluye los **11 tests nuevos**; los golden completos
  de 1 y 42 pasaron en 200,752 y 198,389 s, respectivamente.
- Los dumps frescos antes/después de **42, 1001 y 1002 son idénticos byte a byte**,
  incluido su `git_head`; hashes en `out/perf_h6/dump_hashes.json`.
- Los 15 campos de contadores físicos de 1001/1002 coinciden antes/después.
- Las diez seeds de referencia coinciden en dumps completos y KPI deterministas;
  cero violaciones, hard-cap hits y deadline cutoffs.
- `git diff --check`: OK. Sin cambios en Excel, métrica, validador o `controls.py`.
  Sin commits, push ni PR; resultados generados bajo `out/` sin añadir a Git.

Comandos de tests ejecutados:

```bash
PYTHONPATH=src:. .venv/bin/python -m pytest -q \
  tests/test_heightfield_preview_cache.py tests/test_h6_perf.py -m 'not slow'
export PYTHONPATH=$(pwd)/src
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export PYTEST_ADDOPTS='--junitxml=out/perf_h6/pytest.xml'
./scripts/smoke_offline.sh
```

Log completo: `out/perf_h6/smoke.log`; informe de tests: `out/perf_h6/pytest.xml`.
`PYTEST_ADDOPTS` solo añade el informe XML, sin excluir tests.
