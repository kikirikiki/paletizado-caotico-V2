# H1: deuda de accesibilidad

H1 puntúa las propuestas que llegan al scheduler: una por caja de la ventana.
No amplía la búsqueda interna de posiciones/orientaciones del packer ni altera
la restricción física de accesibilidad, el KPI o el validador.

`run_simulation` y `PolicyPackerScheduler.from_defaults` aceptan:

- `access_debt_weight=0.0`.
- `access_debt_band_mm=300`.
- `access_debt_mode="penalty"` (`penalty` o `filter`).

Con los defaults se omite el cálculo y se conserva la elección original.
`filter` activa H1 independientemente del peso: conserva los candidatos de deuda
cero cuando existe alguno, antes de aplicar el selector de altura con slack;
si todos tienen deuda, conserva todos. En `penalty` se resta del scalar score
`weight * dead_volume_mm3 / volumen_caja`. La rejilla se comparte entre las
propuestas de un mismo palet en cada decisión greedy; no se modifica estado
global ni se reutiliza un perfil obsoleto entre decisiones o cierres de palet.

La rejilla representa el perfil superior **antes** de colocar. Paso 10 mm,
muestreo en el centro de celda, coordenadas con el overhang del palet y área
real para celdas parciales de borde. Banda euclídea: distancia del centro al
rectángulo candidato <= banda, excluyendo los centros dentro de la huella.
Se suma `max(0, techo - delta - altura) * área` y el área con déficit positivo.
Con delta cero devuelve siempre cero. En suelo vacío una caja plana de 355 mm
no genera deuda (delta=400); una caja cuyo techo exceda delta sí puede generarla,
como exige la fórmula. No se descuentan deudas preexistentes ni se comprueba
que cada zona baja admita alguna caja pendiente: es la aproximación local H1.

Telemetría en `metrics.pallet_kpis.access_debt` (y `kpi.json` de cada seed):
media del volumen/área de deuda elegidos, elecciones distintas de la elección
sin H1 sobre las mismas propuestas factibles, y coste de cálculo por candidato
(media/p95, incluye la construcción de rejilla cargada al primer candidato).
La comparación de elección se mide en el selector greedy del experimento;
no compara trayectorias contrafactuales completas. Los experimentos usan
`micro_plan=False`, sin hard floor ni batchfill, igual que el preset oficial.
La deuda también entra en las puntuaciones/filtro de expansión de microplan,
pero su comparación contrafactual no forma parte de esta telemetría.

Reproducción desde la raíz, con `.venv` disponible:

```bash
export PYTHONPATH=$(pwd)/src
./scripts/smoke_offline.sh
.venv/bin/python -m pytest tests/test_access_debt.py tests/test_run_sweep.py -q
.venv/bin/python scripts/benchmark_access_debt.py
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h1_tune.yaml --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 --workers 15 --name h1_tune --baseline arm6_4orient --lock-retry-seconds 60
# Solo tras terminar ajuste y fijar la ganadora por KPI medio de ajuste:
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h1_eval.yaml --seeds 10 --workers 15 --name h1_eval --baseline arm6_4orient --lock-retry-seconds 60
```

El runner adquiere `/tmp/palca_sweep.lock` con flock antes de ejecutar tareas;
si está ocupado, con la opción indicada reintenta cada 60 s sin borrar el inode.
Los dos sweeps deben ejecutarse en secuencia. `summary.md` y `summary.json`
contienen IC95 t de Student entre seeds, diferencias pareadas frente al baseline
remedido dentro del sweep, tiempo por decisión, no colocadas y violaciones.
El p95 de la tabla es la media entre seeds de sus p95; no es un p95 agrupado.
Los artefactos permanecen bajo `out/` y no se versionan.

## Resultados medidos (2026-10-16)

Código SHA256 de ambos sweeps:
`9e3d4ff0694759001da015638512eb3f647f7da19bdf64f472968f305008f6e5`.
Las 160 ejecuciones terminaron con **0 violaciones, 0 no colocadas y 0 cortes
por deadline**. Los conjuntos de seeds de ajuste y evaluación son disjuntos.
El baseline de evaluación reproduce exactamente, seed por seed, los 10 KPI
oficiales de `baseline_v1`; gantry+H1 reproduce los 10 KPI oficiales de gantry
(delta cero). Las dos seeds de regresión unitarias son 50021 y 1.

Ganadora del **primer** ajuste: `access_debt_mode=penalty`, `access_debt_weight=0.25`,
`access_debt_band_mm=445` (sustituida por la del segundo ajuste, ver más abajo). Se eligió por el **mayor KPI medio del ajuste** entre
las 12 alternativas H1, antes de ejecutar evaluación. El criterio y los valores
quedaron registrados en `out/sweeps/h1_tune/selection.json`.

| Sweep/config | KPI % | IC95 % | Diferencia pareada vs baseline (pp) | IC95 diferencia (pp) | ms/decisión p95 |
|---|---:|---|---:|---|---:|
| Ajuste / baseline | 61.735 | [60.373, 63.098] | — | — | 154.675 |
| Ajuste / H1 ganadora | 65.117 | [64.019, 66.215] | +3.381 | [1.769, 4.994] | 178.588 |
| Evaluación / baseline | 61.890 | [60.948, 62.833] | — | — | 155.075 |
| Evaluación / H1 ganadora | 65.255 | [64.187, 66.322] | +3.364 | [1.595, 5.133] | 167.116 |
| Evaluación / gantry+H1 | 66.917 | [65.301, 68.534] | +5.027 | [2.739, 7.315] | 106.080 |

La comparación gantry es informativa, frente al baseline del brazo, no una
estimación del efecto de H1 en gantry: delta cero hace que H1 no actúe.

Coste del kernel aislado (5000 repeticiones/banda, rejilla 1240×840, 40
placements): media 0.074/0.096 ms por candidato para bandas 200/445; p95
0.111/0.158 ms. Construcción de rejilla: media 0.423 ms, equivalente a
0.028 ms por candidato si hay 15 propuestas. Artefacto: `out/h1_benchmark.json`.
En la evaluación de la ganadora, con 15 workers: **0.285 ms/candidato de media**,
incluyendo construcción; media de los p95 por seed **0.564 ms**. El objetivo
<0.5 ms se cumple en media; el p95 observado bajo carga supera ese umbral.

Telemetría de la ganadora en evaluación: deuda elegida media
253,520,017.742 mm³; H1 cambia 861 de 1860 elecciones respecto al selector
sin H1 sobre la misma ventana. Son decisiones locales, no dos trayectorias
completas ejecutadas desde un mismo estado.

## Segundo ajuste y evaluación final (resultado oficial de H1)

La ganadora del primer ajuste estaba en el borde de la rejilla (peso mínimo, banda máxima). Se amplió la rejilla con las mismas
seeds de ajuste 1001–1010 (`h1_tune2`: peso {0,05; 0,1; 0,15; 0,25} × banda {445, 600, 800, 1240}; `h1_tune3`: peso
{0,35; 0,5} con banda 600, una única ampliación porque la ganadora de `h1_tune2` estaba en el peso máximo de esa rejilla).
`h1_tune2` lo completó el director con el mismo runner reanudable (Codex agotó su límite de uso a mitad; 135 tareas ya
estaban hechas), y `h1_tune3`/`h1_eval2` los ejecutó el director (D-011).

| Ajuste (seeds 1001–1010) | b445 | b600 | b800 | b1240 |
|---|---:|---:|---:|---:|
| w 0,05 | 65,14 | 64,13 | 64,88 | 65,12 |
| w 0,10 | 64,61 | 65,43 | 65,10 | 65,72 |
| w 0,15 | 65,63 | 65,42 | 64,25 | 64,87 |
| w 0,25 | 65,12 | **66,71** | 65,94 | 65,73 |
| w 0,35 | — | 65,96 | — | — |
| w 0,50 | 64,12 | 65,89 | — | — |

Baseline de ajuste: 61,74 %. El paisaje es plano (sd entre seeds ≈ 2 pp): las diferencias entre configuraciones con banda
≥ 445 son mayoritariamente ruido; la banda de 200 mm es claramente peor (tabla anterior).

Ganadora combinada (solo por KPI medio de ajuste): **penalty, peso 0,25, banda 600 mm**. Evaluación única con las 10 seeds
de referencia:

| Evaluación (seeds de referencia) | KPI % | IC95 % | Diferencia pareada vs baseline (pp) | IC95 diferencia (pp) | ms/decisión media / p95 |
|---|---:|---|---:|---|---:|
| baseline arm6_4orient | 61,890 | [60,948; 62,833] | — | — | 72,8 / 151,3 |
| **H1 w0,25 b600 (oficial)** | **64,697** | [63,473; 65,922] | **+2,807** | [1,041; 4,572] | 66,0 / 145,9 |
| H1 w0,25 b445 (primera evaluación) | 65,255 | [64,187; 66,322] | +3,364 | [1,595; 5,133] | — / 167,1 |

Se declara oficial la ganadora del procedimiento (b600, +2,81 pp), aunque la primera evaluación (b445) dio una cifra mayor:
elegir entre ambas mirando las seeds de evaluación sería sobreajuste (D-009). La diferencia entre ellas es compatible con ruido.
0 violaciones, 0 no colocadas y 0 cortes por deadline en todas las ejecuciones.

```bash
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h1_tune2.yaml --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 --workers 15 --name h1_tune2 --baseline arm6_4orient --lock-retry-seconds 60
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h1_tune3.yaml --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 --workers 15 --name h1_tune3 --baseline arm6_4orient --lock-retry-seconds 60
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h1_eval2.yaml --seeds 10 --workers 15 --name h1_eval2 --baseline arm6_4orient --lock-retry-seconds 60
```

Comprobaciones ejecutadas y resultado:

- `export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh`: OK (suite completa y escenario final). Log: `out/h1_smoke.log`.
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_access_debt.py -k 'not canonical' -q`: 11 casos OK; las regresiones canónicas también pasan dentro del smoke.
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_benchmark_one_pallet_canonical.py -q`: 8 casos OK.
- Benchmark y los dos comandos de sweep indicados arriba: salida 0; 130/130 y 30/30 resultados, respectivamente.
- Verificación de hashes, seeds disjuntas, KPI oficiales por seed y ceros físicos sobre todos los `kpi.json`: OK.
- `git --no-pager diff --check`: OK.

La primera ejecución del smoke detectó el contrato de firma del benchmark
canónico y contención del lock en dos tests existentes. Se añadieron los
nuevos defaults al perfil/contrato y reintentos explícitos a los tests que
usan el lock compartido; el smoke completo posterior terminó en verde.

Archivos de implementación y soporte modificados/añadidos:

- `src/palca/scoring/access_debt.py`
- `src/palca/scheduler/scheduler_v1.py`
- `src/palca/integration/policy_packer_sched.py`
- `src/sim/run.py`
- `tests/test_access_debt.py`
- `tests/test_run_sweep.py`
- `scripts/run_sweep.py`
- `scripts/summarize_sweep.py`
- `scripts/benchmark_access_debt.py`
- `scripts/benchmark_one_pallet_canonical.py`
- `configs/benchmarks/one_pallet_canonical.json`
- `configs/sweeps/h1_tune.yaml`
- `configs/sweeps/h1_eval.yaml`
- `docs/autopilot/H1_ACCESS_DEBT.md`
- `configs/sweeps/h1_tune2.yaml`, `h1_tune3.yaml`, `h1_eval2.yaml` (segundo ajuste y evaluación final)
