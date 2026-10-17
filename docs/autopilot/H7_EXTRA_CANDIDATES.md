# H7 · Extreme points y esquinas de espacios vacíos máximos

Rama `feat/h7-candidatos-ems`, sobre `feat/techo-offline-estricto`.
Referencia H1+H3+H5+H6: `h6_generic`, 74,030 % en referencia.

## Implementación

`extra_candidates="off"` por defecto; opciones `ep`, `ems`, `ep+ems`.
`extra_candidates_max=32`, entero no negativo, limita las posiciones **añadidas**
por caja y orientación; cero desactiva su adición. Se conserva el prefijo legacy
con su tope de 120. Los extras se deduplican contra ese prefijo y entre familias.
Orden determinista: cota local, x, y; ningún bonus ni scoring nuevo.

EP añade esquinas adyacentes a las caras y sus proyecciones hacia los planos
origen. EMS reconstruye rectángulos vacíos máximos solapados para cada cota
superior existente (más suelo), restando las huellas con top superior a la cota.
Se usan las cuatro esquinas si la huella cabe y su altura real coincide con la
cota. No se buscan cavidades debajo de la envolvente del heightfield.
La geometría y las consultas por orientación se cachean en la instancia del
palet, con clave de todas las colocaciones y memoria limitada a 32 consultas.
Las copias comparten únicamente tuplas/rectángulos inmutables; cada copia
tiene su propio diccionario. Una geometría distinta invalida la caché.
No hay estado global mutable.

Todos los extras deben tocar una pared, una cara lateral a su altura base,
o el borde alineado de una cara superior de apoyo. Coordenadas exactas, sin
holguras añadidas ni discretización. La altura se calcula con el mismo
heightfield y cada posición entra en los mismos controles, settling, gate y
scoring de producción que el prefijo existente. Greedy, beam y rollouts usan
este mismo método. El modo `layers` no cambia.

## Protocolo fijado antes del ajuste

1. Ajuste en seeds 1001–1010: H6 y EP/EMS/EP+EMS × topes 16/32,
   `configs/sweeps/h7_tune.yaml`. Un sweep cada vez, workers=15,
   `--lock-retry-seconds 60` y BLAS/OMP=1 (runner existente).
2. Selección por KPI medio de ajuste descendente entre variantes con cero
   violaciones, cero hard-cap hits y cero no colocadas; desempate por menor
   tope, después nombre. Sin ampliar la rejilla ni ajustar tras evaluar.
3. Medición real de la seleccionada en seeds 1001 y 1002 con workers=1.
   Umbrales sobre todas las decisiones agrupadas: media ≤1000 ms y p95 ≤3000 ms.
   Si falla el coste, se informa como inadmisible; no se selecciona otra usando
   las seeds de referencia.
4. Evaluación única con las diez seeds de referencia, H6 y seleccionada,
   dos ejecuciones completas independientes. Se comparan todas las colocaciones,
   KPI y nodos, excluyendo reloj y metadatos de salida.
5. Pareada seleccionada−H6, IC95 t de Student, unidad=seed, métrica congelada.
   Limítrofe si IC95 contiene cero o algún extremo está a ≤0,1 pp de cero.
   En ese caso una única réplica en las veinte seeds extra de D-016, sin reajuste.
6. D-015: informar `acc_violations_gap50` (robustez, distinto del validador).
   Autopsia sobre palets cerrados: área de suelo vacío (heightfield=0) en y∈[425,820], huellas
   exactas de todas las cajas y unión geométrica, agregada por palet y por seed.
   También se informa suelo sin cajas z=0, para distinguir posibles puentes.
   El extremo y=820 es la envolvente de producción con overhang=20.

## Verificación

Los tests sintéticos cubren EP conocido/proyectado, espacios máximos solapados,
deduplicación, tope, overhang, nivel intermedio, caché y su invalidación,
filtro regional, validación de parámetros y uso del generador en las copias
beam/rollout. Dos tests lentos reproducen el hash de todas las colocaciones,
KPI, utilidades y nodos de H6 (seeds 1 y 50021) con `off`.
Fixture: `tests/fixtures/h7_h6_legacy.json`, extraída de los artefactos intactos
`out/sweeps/h6_prior_eval/h6_generic/{1,50021}` y con su hash de código original.

```bash
export PYTHONPATH=$(pwd)/src
.venv/bin/python -m pytest tests/test_extra_candidates.py -m 'not slow' -q
./scripts/smoke_offline.sh
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h7_tune.yaml --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 --workers 15 --lock-retry-seconds 60 --name h7_tune --baseline h6_generic
```

Los artefactos quedan en `out/`, sin versionar. No se realizan commits, push ni PR.

Continuación reproducible, después del ajuste (comandos secuenciales):

```bash
.venv/bin/python scripts/analyze_h7.py select
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h7_cost.yaml --seed-list 1001,1002 --workers 1 --lock-retry-seconds 60 --name h7_cost
.venv/bin/python scripts/analyze_h7.py cost
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h7_eval.yaml --seeds 10 --workers 15 --lock-retry-seconds 60 --name h7_eval_a --baseline h6_generic
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h7_eval.yaml --seeds 10 --workers 15 --lock-retry-seconds 60 --name h7_eval_b --baseline h6_generic
.venv/bin/python scripts/analyze_h7.py report
```

Si `replica_required=true`, ejecutar una vez y regenerar el informe:

```bash
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h7_eval.yaml --seed-list 947538410,2468649430,1657320841,1519295179,140304910,2447885127,863902929,4100422140,3947398718,1133686776,537973825,3280072339,1368370141,2238740691,1660244179,350049937,1151828140,1689782132,597050397,1842584672 --workers 15 --lock-retry-seconds 60 --name h7_replica --baseline h6_generic
.venv/bin/python scripts/analyze_h7.py report
```

`select` escribe las configs de evaluación/coste y congela la selección en
`out/h7/selection.json`. `cost` integra todas las duraciones de decisión de
ambas seeds. `report` comprueba ceros del validador/hits/no colocadas/deadlines,
identidad de colocaciones/KPI/nodos entre A/B y compatibilidad de los diez
baselines con H6 anterior; escribe `out/h7/report.json`, incluyendo las
autopsias por palet, por seed y agregadas. Los summaries del runner contienen
las pareadas oficiales sin alterar la métrica congelada.


## Incidencia de coste antes de evaluar

El primer ajuste se detuvo tras 15 episodios completos: EP/16, seed 1004,
registró 6 hard-cap hits (máximo de decisión 10045 ms, media 3357 ms con
15 workers). Se conserva sin sobrescribir en `out/sweeps/h7_tune_aborted_v0`
y `out/h7/aborted_v0.json`. No se habían utilizado seeds de evaluación.
Ese intento no se usa para seleccionar ni para estimar la mejora final.

El perfil identificó consultas repetidas de altura/contacto. Se optimizaron
comparaciones, se memoizaron coordenadas por consulta y resultados por
geometría, y se evitó recopiado recursivo de datos inmutables en el beam.
No cambian puntos, orden, parámetros, scoring, controles ni presupuestos.
Un fixture de **324 casos** (EP/EMS/combinación, tres topes, tres orientaciones,
seis tamaños de estado y dos overhangs) conserva exactamente el hash de los
resultados originales; incluye reutilización entre topes y exclusiones.
Microbenchmark de una geometría real: 2,573 ms originales, 0,541 ms optimizados
sin caché, 0,010 ms con caché. Son tiempos del generador, no de una decisión.
Artefacto: `out/h7/generator_cost.json`.

Antes del ajuste definitivo se repite EP/16 en seed 1004 con un proceso como
piloto de coste y el smoke completo. El ajuste definitivo conserva la misma
rejilla y todas las seeds solicitadas; ejecuta de nuevo sus 70 episodios con
un hash de fuentes común. Se mantienen intactos protocolo y regla de réplica.


Piloto EP/16 optimizado, seed 1004, un proceso:
**1264,98 ms de media / 2615,26 ms p95**, máximo 4433,97 ms;
cero violaciones, hard-cap hits, no colocadas y cortes por deadline.
Elimina los hits del prototipo, pero **no cumple el límite de media de 1 s**
en esta seed; no se presenta como ganador por coste. Se conserva la rejilla
fijada para comparar con EMS y medir después la variante seleccionada en
1001/1002, sin usar referencia para ajustar.
Summary: `out/sweeps/h7_pilot_optimized/summary.{md,json}`.

## Ajuste definitivo y selección

Fuente común de las simulaciones: `1ebb72d4b81ba7d30a99740d318827f11e1776512d94bfec41bb0c74a202fae3`.
70 episodios en seeds 1001–1010, workers=15, lock-retry=60.

| Config | KPI medio % | Violaciones | Hard-cap hits | Selección |
|---|---:|---:|---:|---|
| h6_generic | 73,841788 | 0 | 0 | referencia |
| ep/16 | 73,968658 | 0 | 0 | válida |
| ep/32 | 73,599949 | 0 | 0 | válida |
| ems/16 | 73,699264 | 0 | 0 | válida |
| ems/32 | 73,699264 | 0 | 0 | válida |
| ep+ems/16 | **74,318324** | 0 | 0 | **ganadora de ajuste** |
| ep+ems/32 | 73,372018 | 0 | **1** | excluida |

El hit de ep+ems/32 corresponde a seed 1004, máximo 10012,06 ms.
Se conserva y reporta: **la rejilla completa no tiene cero hits**.
La regla de elegibilidad fijada antes de ejecutar excluye esa variante;
no se repiten episodios para eliminar el hit. La ganadora habría sido la
misma incluso antes de aplicar la exclusión. No hay violaciones físicas en
ninguna variante. La selección precede a cualquier evaluación H7 en referencia.

Ganadora: `extra_candidates="ep+ems"`, `extra_candidates_max=16`,
+0,476536 pp de ajuste frente a H6. `out/h7/selection.json` conserva ranking,
configuración, exclusión y hash. `configs/sweeps/h7_cost.yaml` y
`configs/sweeps/h7_eval.yaml` fijan coste y evaluación. Summary completo:
`out/sweeps/h7_tune/summary.{md,json}`.

El helper inicial de selección comprobaba toda la rejilla antes de filtrar
variantes y abortó ante ese hit. Se aplicó la elegibilidad **por variante**
sin modificar ni borrar el summary original y se generaron las configs;
se corrigió únicamente ese posprocesado al finalizar las simulaciones,
conservando el mismo hash de fuentes durante todos los episodios. La versión
usada al medir queda en `out/h7/evaluated_analyze_h7.py`.

## Coste real de la ganadora de ajuste

Seeds 1001/1002, un único worker, 392 decisiones agrupadas:
**1190,086 ms de media, 2476,889 ms p95, 3199,482 ms máximo**.
Cero violaciones, hard-cap hits, no colocadas y cortes por deadline.
Cumple p95 ≤3000 ms; **no cumple media ≤1000 ms** (+19,0 %).
Por tanto, esta configuración **no es admisible por coste**, aunque su eventual
pareada fuera positiva. La evaluación solicitada continúa sin reajustar ni
seleccionar otra configuración usando las seeds de referencia; el default
permanece `off`. No se atribuye este resultado a tiempos de quince workers.
Artefactos: `out/h7/cost.json`, `out/sweeps/h7_cost/summary.{md,json}`.

## Evaluación única en referencia y reproducibilidad

Dos ejecuciones completas independientes, diez seeds de referencia cada una:
`out/sweeps/h7_eval_a/summary.{md,json}` y
`out/sweeps/h7_eval_b/summary.{md,json}`.
**20/20 pares idénticos** (dos configuraciones × diez seeds), en todas las
colocaciones, KPI y contadores de nodos/futuros/muestras. Los diez baselines
coinciden también con H6 anterior. La unidad estadística sigue siendo **seed,
n=10**; B comprueba reproducibilidad y no duplica el tamaño de muestra.
Cero violaciones, hard-cap hits, no colocadas y cortes por deadline en los
40 episodios. Las configs y las seeds se mantuvieron idénticas, sin reajuste.

| Config | KPI % | acc_violations_gap50 media/seed (total) | Palets cerrados |
|---|---:|---:|---:|
| H6 generic | 74,029907 | 83,8 (838) | 87 |
| H7 ep+ems/16 | 73,496638 | 84,6 (846) | 89 |

Pareada H7−H6: **−0,533270 pp, IC95 [−2,104179; +1,037640]**, n=10,
t de Student. No respalda mejora. Como el IC95 contiene cero, se activa
exactamente la réplica D-016 fijada antes de ejecutar, sin seleccionar otra
variante y sin agrupar ambos conjuntos para inferencia.

Autopsia de y∈[425,820], x∈[−20,1220], solo palets cerrados:

| Config | Suelo vacío %, media por seed | Suelo vacío %, media por palet | Perfil medio mm, media por seed |
|---|---:|---:|---:|
| H6 generic | 17,797351 | 17,922868 | 1617,952 |
| H7 ep+ems/16 | 18,011497 | 18,043939 | 1624,792 |

H7 deja **+0,214146 pp** más de suelo vacío en la franja trasera en referencia.
No se observa que esta ampliación resuelva la franja. El contador D-015
crece 0,8 colocaciones/seed; no demuestra una mejora de robustez. Estos
contadores no son violaciones del validador físico congelado, que permanece
con cero. Se mantienen separados de ese resultado.
Desglose exacto por palet/seed: `out/h7/autopsy_a.json` y `out/h7/report.json`.


## Réplica D-016 y decisión

Veinte seeds fijadas, 40 episodios, cero violaciones, hard-cap hits,
no colocadas y cortes por deadline. Summary:
`out/sweeps/h7_replica/summary.{md,json}`.

| Config | KPI % | gap50 media/seed (total) | Palets cerrados | Suelo vacío trasero %, media/seed | Perfil trasero mm, media/seed |
|---|---:|---:|---:|---:|---:|
| H6 generic | 73,594697 | 82,85 (1657) | 176 | 19,166270 | 1603,595 |
| H7 ep+ems/16 | 73,836872 | 85,30 (1706) | 172 | 18,244936 | 1614,677 |

Pareada H7−H6: **+0,242175 pp, IC95 [−0,883625; +1,367975]**, n=20,
t de Student. No significativa; no se añaden más seeds ni se agrupan con
referencia. En esta réplica la franja mejora 0,921333 pp de suelo vacío,
pero el beneficio no es consistente con referencia y no se traduce en una
mejora respaldada del KPI. El gap50 empeora 2,45 colocaciones/seed.

**H7 no se adopta**: mejora no respaldada en referencia ni D-016 y media de
coste 1190 ms >1000 ms. La implementación queda experimental y `off` por
defecto. El resultado no descarta todas las variantes de EP/EMS: se refiere
a esta generación, topes y política H6 fija. No se tocaron accesibilidad,
gate, scoring, métrica, validador ni datos Excel.

Las evaluaciones A/B y réplica (80 episodios) y el coste (2 episodios) tienen
cero violaciones y cero hits. **No se afirma que toda la rejilla cumpla el
DoD estricto**: ep+ems/32 dio un hit y fue descartada; además la elegida no
cumple el presupuesto de media. Los resultados adversos permanecen visibles.

## Cierre y procedencia

Después de terminar las simulaciones se corrigió únicamente la elegibilidad
por variante del helper `select`, conservando todos los registros. Tres tests
cubren exclusión de la inválida con mayor KPI, desempate por menor tope,
rechazo de referencia inválida y ausencia de variantes admisibles. El helper
corregido vuelve a elegir exactamente ep+ems/16 y regenera el mismo informe.
No se alteró ningún fingerprint de ejecución.

El hash global actual es `9fbbc380809cea82153f8bc3b22b68f660d8d8de4463454575540eb768539e53`;
difiere del hash común medido por esa corrección del posprocesado. El hash de
**todas las fuentes de producción src/** permanece idéntico:
`2d1067c0e329991461faad7a6bbfafa2e40432a5d9a655e0af7634dc2857c783`.
Prueba: `out/h7/postprocessing_delta.json`; helper previo archivado en
`out/h7/evaluated_analyze_h7.py`. La caché global nativa invalidará resultados
anteriores con el código final; no se fuerza su reutilización.

Compatibilidad adicional sin nuevos episodios: las colocaciones completas
de los 30 baselines (10 referencia +20 D-016) coinciden con H6 anterior;
las diez seeds de ajuste H6 también coinciden entre versiones. Los dos tests
lentos requeridos con `off` siguen comparando además KPI y nodos exactos.

Archivos del cambio (se conserva intacta la spec preexistente 13):

- `src/palca/packer/extra_candidates.py`
- `src/palca/packer/pallet_model.py`
- `src/palca/integration/policy_packer_sched.py`
- `src/sim/run.py`
- `scripts/analyze_h7.py`
- `scripts/benchmark_one_pallet_canonical.py`
- `configs/benchmarks/one_pallet_canonical.json`
- `configs/sweeps/h7_tune.yaml`
- `configs/sweeps/h7_cost.yaml`
- `configs/sweeps/h7_eval.yaml`
- `tests/test_extra_candidates.py`
- `tests/fixtures/h7_candidates_v0.json`
- `tests/fixtures/h7_h6_legacy.json`
- `docs/autopilot/H7_EXTRA_CANDIDATES.md`

Artefacto principal: `out/h7/report.json`. Todas las tablas conservan la unidad
estadística indicada; `acc_violations_gap50` es robustez adicional y nunca
se confunde con el validador físico. Sin commits, push ni PR; `out/` no se
versiona.


Verificación final sobre el árbol final: **smoke OK, 480 tests**, más escenario
height-slack OK. Tests rápidos H7: **30 OK**; los dos tests lentos de H6 con
`off` pasan dentro del smoke. `git diff --check` OK.
Comando ejecutado: `export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh`;
tests enfocados: `.venv/bin/python -m pytest tests/test_extra_candidates.py -m 'not slow' -q`.
Los comandos de barrido y análisis anteriores se completaron con los resultados
y exclusiones indicados. Resumen de verificación: `out/h7/verification.json`.
