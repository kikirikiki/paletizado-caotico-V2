# H6 · Rollouts con llegadas futuras muestreadas

Implementación sobre `feat/h6-rollouts-estocasticos`, apilada sobre H5.
Referencia fija: `h5_rollout_l4` de `configs/sweeps/h5_eval.yaml`, H1+H3+H5,
72,040 % en las diez seeds de referencia.

## Información permitida y determinismo

El prior `catalog` está fijado en `src/palca/scoring/catalog_prior.py`.
Solo usa la tabla operativa de `docs/CONTEXT.md`, §3 «Tipos de contenedor reales»:

| Tipo | Peso | Ancho mm | Largo mm | Alto mm |
|---|---:|---|---|---|
| W450.L650 | 77 | 405–465 | 430–645 | 325–415 |
| W400.L650 | 10 | 350–400 | 410–635 | 330–435 |
| W400.L400 | 7 | 330–335 | 380–390 | 260–295 |
| W450.L685 | 3 | 440–450 | 655–660 | 360–400 |
| PICKING | 2 | 445–480 | 605–700 | 360–460 |
| W400.L685 | 2 | 355–365 | 655–660 | 370–395 |

Los porcentajes publicados suman 101: se normalizan por 101, sin usar los
recuentos exactos de cajas ni recalibrar con el flujo. Cada dimensión es una
uniforme discreta independiente en milímetros enteros, extremos incluidos.
No se usan dimensiones individuales, orden, tamaño restante, Excel ni seed
real del episodio para generar llegadas. El destino se muestrea uniformemente
entre palets activos no bloqueados: hipótesis operativa explícita; el estudio
canónico fuerza destino=1. Peso, loadbear y prioridad quedan sin especificar,
usando los defaults de producción.

La semilla es SHA-256 de la geometría colocada y las dimensiones/destinos de
la ventana visible congelada de H5. Se serializan con JSON compacto y orden
estable del estado; los IDs de caja y timestamps no intervienen. Un contador
SHA-256 por muestra genera enteros de 64 bits; rechazo de la cola elimina el
sesgo modular. No se usa `hash()`, RNG global ni reloj. Las hojas comparten las
mismas muestras para comparar estados con números aleatorios comunes.
La ruta H6 ni siquiera copia `upstream` al construir los estados de búsqueda.

## Algoritmo y presupuesto

Defaults: `rollout_future_boxes=0`, `rollout_future_samples=1`,
`rollout_prior="catalog"`. Se propagan por CLI, simulador, política, scheduler,
JSON de parámetros y perfil de benchmark. Con cero cajas futuras se invoca
la función original de H5 y se conserva su reserva/presupuesto.

Con H6 activo cada hoja ejecuta primero el greedy de producción sobre todas
las cajas visibles factibles. Si no queda ninguna factible, termina ese prefijo.
Se comparte el prefijo determinista entre las muestras. Cada continuación se
crea sobre una copia independiente del palet resultante; revela una llegada
sintética cada vez, en orden, con la misma política greedy y H1. Una llegada
que no cabe consume un intento y se descarta; se prosigue con la siguiente.
No se abren palets ficticios. El valor es la media del volumen total colocado
sobre las muestras, con los desempates de H5. Nada se coloca en el palet real
hasta que se elige la primera acción factible del beam.

Cada expansión y cada scan greedy, incluido el fallido, consume un nodo.
Se reserva `min(hojas,width) × (N_visible + future_boxes × samples)` para
rollouts, conservando al menos una ventana raíz para el beam. Las hojas tienen
cuotas enteras iguales; tras el prefijo visible se asigna la misma cuota futura
a cada muestra. La telemetría identifica truncaciones: futuros intentados y
muestras completas por decisión, además de nodos y hard-cap hits.
El watchdog de 10 s permanece intacto: cualquier hit invalida el experimento.

La rejilla usa dos hojas y `B=15+2×(15+future_boxes×samples)` para que todas
las continuaciones quepan incluso tras consumir las catorce visibles restantes.
El piloto previo con cuatro hojas, 3 futuras y 1 muestra (seed 1001), dio
1399,5 ms de media / 2777,9 ms p95 con un proceso y cero hits/violaciones;
se descartó por coste y se detuvo antes de completar el segundo extremo.
Ese intento queda en `out/sweeps/h6_pilot`, con diseño original y motivo
en `out/h6/pilot4_aborted.json`. Se reducen las hojas H6 a dos antes del
ajuste; la referencia H5 conserva cuatro. El piloto con dos hojas y el
mismo punto/seed da 701,2 ms de media / 1374,4 ms p95, cero hits/violaciones;
se registra en `out/sweeps/h6_pilot2`. El extremo 10 futuras × 3 muestras
da 811,3 / 1461,3 ms, también sin violaciones ni hits.
La búsqueda conserva d3/w8/topk15; esta reserva deja normalmente la ventana
raíz para beam. Frente a H5 B=25, H6 también completa más prefijo visible:
una eventual ganancia no aísla causalmente el efecto del prior del de completar
ese prefijo. No se cambia KPI, validador, accesibilidad ni gate.

## Diseño fijado antes de evaluar

Ajuste: seeds 1001–1010, referencia H5, cajas {3,6,10} × muestras {1,3}.
Orden de selección: KPI medio de ajuste descendente; exigir cero violaciones,
cero hard-cap hits, cero no colocadas; comprobar media ≤1000 ms y p95 agrupado
≤3000 ms con un solo proceso en seeds 1001 y 1002 antes de evaluar.
Si la mejor está en un borde, una sola ampliación adyacente: cajas 3→1 o
10→16; muestras amplía hasta 5, incluyendo el cruce si ambos ejes amplían.
Los dos niveles iniciales de muestras son bordes; si gana 1, el mínimo
natural, se amplía el extremo superior a 5 al no existir nivel positivo inferior.

La selección y los costes se registran en `out/h6/selection.json` antes de
usar las seeds de evaluación. Evaluación única: diez seeds de referencia,
dos ejecuciones completas independientes. Pareada H6−H5, IC95 t de Student,
unidad=seed. Regla preregistrada de réplica: IC95 incluye cero o alguno de sus
extremos está a ≤0,1 pp de cero. Se usan exactamente las veinte seeds D-016
publicadas en `docs/autopilot/H5_STATE_VALUE.md`, sin reajuste.

Todos los sweeps se ejecutan secuencialmente con lock global y
`--lock-retry-seconds 60`, BLAS/OMP=1; máximo quince workers. Los artefactos
quedan en `out/` y no se versionan.

## Verificación y reproducción

```bash
export PYTHONPATH=$(pwd)/src
.venv/bin/python -m pytest tests/test_stochastic_rollouts.py -m 'not slow' -q
./scripts/smoke_offline.sh
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h6_tune.yaml --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 --workers 15 --name h6_tune --baseline h5_rollout_l4 --lock-retry-seconds 60
```

Tests nuevos: rangos y prefijo fijo del sampler; importación en intérprete
limpio con guard contra loaders, pandas, openpyxl, simulador y scripts;
audit hook que bloquea Excel/data incluso durante la importación, más guard de I/O del muestreo;
futuro real que falla al iterarlo; determinismo y aislamiento; orden de llegadas,
media y presupuesto; propagación/validación; dos seeds canónicas comparadas
contra hashes de colocaciones, KPI, utilización por palet y nodos de H5 anterior.
Los fixtures provienen de `out/sweeps/h5_eval_a/h5_rollout_l4/{50021,1}`.

Verificación de implementación: **404 tests OK y smoke completo OK**.
Las seeds 50021 y 1 reproducen exactamente H5 con futuras=0.

## Resultados definitivos

### Ajuste inicial (seeds 1001–1010)

| Config | Hojas | B | KPI % | Media / p95 ms agrupados (15 workers) |
|---|---:|---:|---:|---:|
| h5_rollout_l4 | 4 | 25 | 73.369 | 800.1 / 1822.5 |
| h6_f3_s1 | 2 | 51 | 73.896 | 1280.7 / 2430.7 |
| h6_f3_s3 | 2 | 63 | 73.464 | 1331.8 / 2597.4 |
| h6_f6_s1 | 2 | 57 | 74.663 | 1324.1 / 2577.1 |
| h6_f6_s3 | 2 | 81 | 73.650 | 1426.5 / 2623.8 |
| h6_f10_s1 | 2 | 65 | 74.350 | 1348.5 / 2567.8 |
| h6_f10_s3 | 2 | 105 | 74.112 | 1263.8 / 2436.1 |

Summary: `out/sweeps/h6_tune/summary.{md,json}`. Todas las filas: cero violaciones, hard-cap hits, no colocadas y cortes por deadline.

### Única ampliación de borde (mismas seeds)

| Config | Hojas | B | KPI % | Media / p95 ms agrupados (15 workers) |
|---|---:|---:|---:|---:|
| h5_rollout_l4 | 4 | 25 | 73.369 | 799.7 / 1855.9 |
| h6_f6_s5 | 2 | 105 | 73.814 | 1227.3 / 2409.3 |

Summary: `out/sweeps/h6_tune_edge/summary.{md,json}`. Todas las filas: cero violaciones, hard-cap hits, no colocadas y cortes por deadline.

Ganadora congelada antes de evaluación: **h6_f6_s1**. Parámetros: `rollout_future_boxes=6`, `rollout_future_samples=1`, dos hojas, `B=57`. Registro UTC: `2026-10-17T04:17:33.100663+00:00`; `out/h6/selection.json`.

### Coste con un proceso

| Config | Seeds | Decisiones | Media ms | p95 ms agrupado | Máximo ms |
|---|---:|---:|---:|---:|---:|---:|
| h6_f6_s1 | 2 | 391 | 715.6 | 1481.1 | 1903.2 |

Seeds 1001 y 1002, un worker, sin otro sweep propio simultáneo, BLAS/OMP=1. Incluye decisiones sin colocación. La configuración elegida cumple media ≤1000 ms y p95 ≤3000 ms. Summary: `out/sweeps/h6_cost_h6_f6_s1/summary.{md,json}`.

### Evaluación única y repetición

| Config | KPI % ± sd pp | IC95 KPI % | Media / p95 ms agrupados |
|---|---:|---|---:|
| h5_rollout_l4 | 72.040 ± 1.053 | [71.287; 72.793] | 789.5 / 1720.8 |
| h6_f6_s1 | 73.221 ± 1.969 | [71.812; 74.630] | 1030.5 / 2129.6 |

Pareada H6−H5: **+1.181 pp, IC95 [-0.175; 2.538]**, n=10. IC95 t de Student, unidad=seed. La repetición confirma determinismo y no aumenta el n estadístico.

`h6_eval_a` y `h6_eval_b`: **20/20 pares configuración/seed idénticos** en colocaciones completas (incluidos orden y timestamps simulados), todos los KPI congelados, nodos, futuros intentados y muestras completadas. Se excluyen duraciones observacionales y rutas/metadatos. Mismo hash de código entre ambas ejecuciones y el coste con un proceso.

### Réplica preregistrada D-016

Se activa la regla fijada antes de evaluar. Las veinte seeds son exactamente las de D-016, sin ajustar parámetros.

| Config | KPI % ± sd pp | IC95 KPI % |
|---|---:|---|
| h5_rollout_l4 | 72.326 ± 1.427 | [71.658; 72.993] |
| h6_f6_s1 | 73.860 ± 1.244 | [73.278; 74.443] |

Pareada réplica H6−H5: **+1.535 pp, IC95 [0.682; 2.388]**, n=20.

La comparación final demuestra mejora al 95 % con el diseño fijado. El resultado de referencia sigue siendo el de las diez seeds oficiales; la réplica, si existe, se informa por separado.

Todos los summaries definitivos de ajuste, ampliación, coste, evaluación y réplica (si procede) tienen cero violaciones y cero hard-cap hits; también cero no colocadas y cortes por deadline. Los contadores verifican que todos los nodos están dentro del presupuesto y que los futuros intentados corresponden a muestras completas. Los tiempos de las tablas de barrido se miden bajo carga de quince workers; el límite de producción se comprueba exclusivamente con un proceso.

Evidencia consolidada: `out/h6/report.json`; reglas/selección: `out/h6/selection.json`; summaries: `out/sweeps/h6_{tune,tune_edge,eval_a,eval_b,replica}/summary.{md,json}` (solo directorios aplicables). Artefactos excluidos de Git. No se realizaron commits, push ni PR.

### Comandos de coste y evaluación

```bash
export PYTHONPATH=$(pwd)/src
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h6_cost.yaml --seed-list 1001,1002 --workers 1 --name h6_cost_h6_f6_s1 --lock-retry-seconds 60
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h6_eval.yaml --seeds 10 --workers 15 --name h6_eval_a --baseline h5_rollout_l4 --lock-retry-seconds 60
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h6_eval.yaml --seeds 10 --workers 15 --name h6_eval_b --baseline h5_rollout_l4 --lock-retry-seconds 60
```

Ampliación fijada:

```bash
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h6_tune_edge.yaml --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 --workers 15 --name h6_tune_edge --baseline h5_rollout_l4 --lock-retry-seconds 60
```

Réplica fijada:

```bash
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h6_replica.yaml --seed-list 947538410,2468649430,1657320841,1519295179,140304910,2447885127,863902929,4100422140,3947398718,1133686776,537973825,3280072339,1368370141,2238740691,1660244179,350049937,1151828140,1689782132,597050397,1842584672 --workers 15 --name h6_replica --baseline h5_rollout_l4 --lock-retry-seconds 60
```

## Archivos cambiados

Motor e integración: `src/palca/scheduler/scheduler_v1.py`, `src/palca/scoring/catalog_prior.py`, `src/palca/integration/policy_packer_sched.py`, `src/sim/run.py`.

Configuración y benchmark: `configs/benchmarks/one_pallet_canonical.json`, `scripts/benchmark_one_pallet_canonical.py`, `configs/sweeps/h6_tune.yaml`, `configs/sweeps/h6_tune_edge.yaml`, `configs/sweeps/h6_cost.yaml`, `configs/sweeps/h6_eval.yaml`, `configs/sweeps/h6_replica.yaml`.

Verificación y documentación: `tests/test_stochastic_rollouts.py`, `tests/fixtures/h6_h5_legacy.json`, `docs/autopilot/H6_STOCHASTIC_ROLLOUTS.md`.


## Sensibilidad del prior

El director confirma que las proporciones y rangos de `catalog` se obtuvieron
originalmente del mismo flujo de 186 cajas. Esto aporta información agregada
sobre la evaluación, aunque la política no lea su secuencia futura. Se añade
una sensibilidad sin ajustar la configuración ganadora: f6, s1, dos hojas,
B=57, H1 y los demás parámetros intactos. El default continúa siendo `catalog`.

- `catalog`: seis tipos y rangos anteriores, pesos 77/10/7/3/2/2 normalizados por 101.
- `uniform_types`: los mismos seis tipos/rangos, probabilidad 1/6 por tipo.
  Elimina el conocimiento de las proporciones; conserva los rangos derivados del flujo.
- `generic`: un único tipo, ancho 300–500, largo 350–750 y alto 250–500 mm.
  Son rangos amplios fijados por el director, independientes del flujo y del catálogo.
  Las tres dimensiones son uniformes discretas independientes, extremos incluidos.

Los tres usan el mismo mecanismo SHA-256 del estado observado y el mismo
presupuesto. El test de independencia sustituye `CATALOG` por un objeto que
falla al iterarse, indexarse o inspeccionarse: `generic` produce exactamente las
mismas 2000 cajas, dentro de sus rangos. Otro test cambia todos los pesos del
catálogo sin alterar `uniform_types` y comprueba las seis frecuencias.
Se mantienen las pruebas contra imports/lecturas del flujo real para los tres.

Diseño fijado antes de ejecutar: cada prior frente a H5 `h5_rollout_l4`,
10 seeds de referencia y exactamente las 20 nuevas D-016, por separado;
sin seleccionar prior ni cambiar hiperparámetros tras ver los resultados.
La comparación principal para la independencia es `generic−H5` en la réplica:
se exige límite inferior del IC95 pareado >0 para respaldar mejora sin
estadísticas del catálogo. Esto no constituye una prueba de equivalencia
entre los priors. Se informa el IC95 t de Student, unidad=seed, de cada contraste.
Registro: `out/h6_prior/preregistration.json`.

La ampliación de código cambia el hash global del runner. Su comprobación de
caché rechaza los 30 resultados previos de `catalog` (0/30 reutilizables);
no se cambia ningún hash ni se reetiquetan resultados. Se vuelve a ejecutar
`catalog` y H5 bajo el mismo código que los nuevos priors, en directorios nuevos,
y se conservan todos los artefactos anteriores. La caché nativa sí permite
reanudar estos nuevos sweeps con el código sin cambios.

Comandos (secuenciales, lock global):

```bash
export PYTHONPATH=$(pwd)/src
.venv/bin/python -m pytest tests/test_stochastic_rollouts.py tests/test_state_value.py -m 'not slow' -q
./scripts/smoke_offline.sh
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h6_prior_eval.yaml --seeds 10 --workers 15 --lock-retry-seconds 60 --name h6_prior_eval --baseline h5_rollout_l4
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h6_prior_replica.yaml --seed-list 947538410,2468649430,1657320841,1519295179,140304910,2447885127,863902929,4100422140,3947398718,1133686776,537973825,3280072339,1368370141,2238740691,1660244179,350049937,1151828140,1689782132,597050397,1842584672 --workers 15 --lock-retry-seconds 60 --name h6_prior_replica --baseline h5_rollout_l4
```

### Resultados de sensibilidad

| Prior | Referencia: KPI % (n=10) | Δ H6−H5 pp [IC95] | D-016: KPI % (n=20) | Δ H6−H5 pp [IC95] |
|---|---:|---|---:|---|
| catalog | 73.221 | +1.181 [-0.175; 2.538] | 73.860 | +1.535 [0.682; 2.388] |
| uniform_types | 73.472 | +1.432 [0.098; 2.767] | 73.818 | +1.492 [0.385; 2.599] |
| generic | 74.030 | +1.990 [1.233; 2.747] | 73.595 | +1.269 [0.166; 2.372] |

H5: **72.040 %** en referencia y **72.326 %** en D-016. IC95 t de Student, unidad=seed; diferencias en puntos porcentuales. Son tres contrastes descriptivos por conjunto; el contraste principal fijado para la independencia es generic−H5 en D-016.

**El prior genérico mantiene una mejora positiva sin estadísticas del catálogo**: +1.269 pp, IC95 [0.166; 2.372]. Respalda que la mejora de esta configuración H6 frente a H5 no requiere las proporciones ni los rangos específicos del flujo. No demuestra igualdad de ganancia entre priors ni aísla el efecto del prior de completar más prefijo visible con el presupuesto H6.

Compatibilidad comprobada con los artefactos anteriores: **60/60 pares idénticos** (30 de H5 y 30 de catalog), en colocaciones completas, KPI y nodos. El catálogo conserva también el prefijo fijo del sampler en los tests. Todos los nuevos resultados comparten el hash de código `8ed9cd130725f9e8031c8cda42260f7c7bacc27569c283c4c6bea21a2d499c89`. Los originales conservan sus hashes y no se sobrescriben.

**120 ejecuciones definitivas: cero violaciones, hard-cap hits, no colocadas y cortes por deadline.** Se verifican los presupuestos por decisión y las muestras completas. Los tiempos de estos barridos corresponden a hasta quince workers; no son una medición nueva con un proceso.

Summaries: `out/sweeps/h6_prior_eval/summary.{md,json}` y `out/sweeps/h6_prior_replica/summary.{md,json}`. Tabla, validaciones y contrastes: `out/h6_prior/report.json`. Caché: `out/h6_prior/cache_check.json`. Smoke completo: **412 tests OK**; tests H6/H5 rápidos: **24 OK**. Sin commits, push ni PR. Se conserva intacto el cambio previo en `docs/autopilot/DECISIONS.md`.

Archivos de este delta: `src/palca/scoring/catalog_prior.py`, `src/palca/scheduler/scheduler_v1.py`, `src/sim/run.py`, `tests/test_stochastic_rollouts.py`, `configs/sweeps/h6_prior_eval.yaml`, `configs/sweeps/h6_prior_replica.yaml` y este documento.
