# H5 · Valor de estado del beam

H5 conserva por defecto `micro_plan_value=score`: misma poda y elección de H3.
`micro_plan_value_weight=1.0`, `micro_plan_rollout_leaves=4`. Se propagan por
CLI, simulador, política, scheduler, JSON y perfil de benchmark canónico.
No modifica el KPI, el validador, los controles, la accesibilidad ni el gate.

## Método

Para H5 se congela la ventana inicial de acciones visibles de H3 (15 cajas en
este estudio). Las copias no reponen cajas desde `upstream`. Tanto el potencial
como el rollout y la expansión del beam usan exclusivamente esa ventana menos
las cajas ya consumidas. La ruta `score` conserva incluso el comportamiento de
refill anterior para garantizar compatibilidad exacta.

El perfil superior se rasteriza con las coordenadas exactas de los bordes de
las colocaciones, sin redondeo de huellas. Se fusionan tramos horizontales de
igual altura y después tramos verticales idénticos en rectángulos disjuntos.
Cada zona r tiene huella (L_r,W_r), suelo z_r y capacidad C_r=H_max−z_r.
Se visitan en orden (z ascendente, área descendente, y, x).

Una orientación de la caja i es elegible si cabe completamente en la huella,
su altura h_ir cabe inicialmente en C_r y al menos una de las cuatro esquinas
permite el descenso según la convención de contacto de producción:
ninguna caja que toca/solapa su huella tiene techo > z_r+delta. El soporte se
aproxima mediante una superficie plana completa; el gate de las orientaciones
se calcula con la altura actual del palet, sin mutar sus estadísticas.

En cada zona se resuelve una mochila fraccionaria de alturas: ordenar por
V_i/h_ir descendente (usar la menor altura elegible; empate por orden visible)
y consumir f_ir=min(fracción restante_i, capacidad restante_r/h_ir).

```
P(s) = Σ_r Σ_i f_ir V_i
V_potential(s) = volumen colocado(s) + micro_plan_value_weight × P(s)
Σ_r f_ir ≤ 1 para toda caja visible i
```

Zonas bloqueadas o sin huella compatible aportan cero. Las fracciones no se
reutilizan entre zonas. Es una estimación heurística: ni certificado físico
ni cota global. La descomposición puede subestimar superficies que requerirían
unir varios rectángulos; apilar fracciones puede sobreestimar la completabilidad.
El potencial puede saturarse cuando todas las cajas visibles parecen caber:
no estima distribuciones desconocidas de futuras llegadas.

El beam H5 ordena por (valor, nº colocadas, score acumulado), también antes de
`topk`; la primera acción siempre procede de una expansión con preview factible.
No se utiliza el nodo raíz sin acción como candidato de elección.

`rollout` usa el potencial para podar y ordenar antes de la última profundidad.
Sobre las L mejores hojas de esa profundidad ejecuta el greedy de producción
(incluida H1) hasta agotar las cajas visibles o no encontrar ninguna factible,
sobre copias de los palets y con un scheduler aislado. El valor final es el
volumen total colocado, desempate por la clave anterior. No abre nuevos palets.

Se reservan min(L,width)×N_visible unidades antes del beam. El resto se dedica
a expansiones, dejando al menos espacio para la ventana raíz. Cada scan greedy
consume una unidad, incluso el último sin colocación: toda colocación consume
presupuesto. Las hojas reciben la misma cuota entera del presupuesto restante.
Un scan examina como máximo la ventana visible, por lo que el coste está acotado.
Con presupuestos insuficientes el rollout puede quedar truncado; se registra
`rollout_completed_leaves`. Tras medir el coste se usa B=25 para rollouts. El watchdog global
sigue vigente y cualquier hit invalida la afirmación de determinismo.

## Verificación y experimento

`tests/test_state_value.py`: zonas accesibles, estrechas y bloqueadas; mochila
fraccionaria sin doble conteo; peso; aislamiento del futuro y del palet real;
determinismo; presupuesto; propagación; replay D-015. Dos seeds canónicas
(50021,1) comparan hashes de todas las colocaciones, KPI y utilización por palet
contra `out/sweeps/h3_eval` anterior a H5, conservados en un fixture pequeño.

Ajuste: seeds 1001–1010, potential pesos {0.25,0.5,1,2}, rollout hojas {2,4,8},
referencia H1+H3 n100 d3 w8 topk15. Una ampliación si la mejor H5 elegible
está en el borde. Selección antes de mirar evaluación; se exige 0 violaciones,
0 hard_cap_hits y coste media ≤1000 ms / p95 ≤3000 ms por decisión con 1 proceso.
Evaluación: las 10 seeds de referencia, dos ejecuciones independientes,
comparación pareada H5−H3 con IC95 t de Student (unidad=seed).
D-015 se mide mediante replay del dump por palet físico y en orden de colocación,
con tolerancias {5,20,50} mm, sin cambiar local_z ni la regla en producción.
Los artefactos se guardan en `out/`, fuera del control de versiones.

## Comandos

```bash
export PYTHONPATH=$(pwd)/src
.venv/bin/python -m pytest tests/test_state_value.py -q
./scripts/smoke_offline.sh
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h5_tune.yaml --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 --workers 15 --name h5_tune --baseline h3_ref --lock-retry-seconds 60
```

## Control del coste y trazabilidad

El primer intento reservaba B=100+15L para completar todas las hojas. Con 4
hojas apareció un hard-cap hit; se interrumpió y se descartaron los rollouts de
ese intento. `out/sweeps/h5_tune/manifest_aborted.json` conserva el diseño original
y las filas parciales siguen en ese directorio. Su `summary` incluye únicamente
las 50 tareas completas de referencia/potencial, todas con cero hits. El ajuste
de rollouts se repite en `h5_rollout_tune` con una referencia nueva.
El ensayo B=50 también excedió D-007; sus filas completas se conservan en
`h5_rollout_cost_b50`, con la variante de 8 hojas parcial fuera de su summary.
El ajuste definitivo usa B=25 para las tres variantes y reutiliza únicamente
la referencia, cuyo hash de tarea no cambia.
La truncación por presupuesto es determinista; no se interpreta un rollout
truncado como una completación factible de toda la ventana.

Entre ambos se corrigió que el scheduler aislado del rollout conservara el
flag H3 al invocar greedy directamente: así `batchfill` opcional no introduce
un deadline local. `batchfill=False` en todas las configuraciones del estudio;
la corrección no afecta las rutas score/potential del primer ajuste. Los hashes
de código distintos se conservan en sus manifests, sin reetiquetar resultados.

```bash
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h5_rollout_tune.yaml --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 --workers 15 --name h5_rollout_tune --baseline h3_ref --lock-retry-seconds 60
```

## Ajuste definitivo (seeds 1001–1010)

| Configuración | B | KPI % | media / p95 ms (agrupados) |
|---|---:|---:|---:|
| h3_ref | 100 | 70.942 | 709 / 1677 |
| h5_potential_w0.25 | 100 | 73.169 | 938 / 2112 |
| h5_potential_w0.5 | 100 | 73.005 | 789 / 1745 |
| h5_potential_w1 | 100 | 72.677 | 775 / 1677 |
| h5_potential_w2 | 100 | 70.600 | 709 / 1454 |
| h5_rollout_l2 | 25 | 71.722 | 826 / 1705 |
| h5_rollout_l4 | 25 | 73.369 | 791 / 1824 |
| h5_rollout_l8 | 25 | 72.232 | 781 / 1869 |

Ganadora de ajuste: **rollout, 4 hojas, B=25**, KPI 73,369 % (+2,427 pp
frente a H3). Hojas=4 es interior a {2,4,8}; no corresponde ampliar la rejilla.
Todas las filas definitivas: 0 violaciones, 0 hard_cap_hits, 186 cajas por seed.
La referencia repetida después de la corrección da los mismos dumps en las
10 seeds, KPI 70,942 %, coste 618 / 1377 ms bajo la carga de ese barrido.
Los percentiles de esta tabla agrupan las duraciones de todas las decisiones;
los summaries del runner mantienen además la media de percentiles por seed.
Se exige D-007 también bajo la carga de 15 procesos antes de medir con 1 proceso.

## Coste de la ganadora con un proceso

Seeds 1001 y 1002; 391 decisiones, incluidas llamadas sin colocación.
Media **436.9 ms**, p95 agrupado **910.5 ms**, máximo
**1312.0 ms**. Cero violaciones, hard_cap_hits y cajas sin colocar.
Un worker, BLAS/OMP=1, lock global, sin otros sweeps propios simultáneos.
Load average antes: 0,58 / 4,59 / 9,10; después: 1.12 / 3.04 / 7.72.
Datos en `out/sweeps/h5_cost` y `out/h5/cost_environment.json`.

Con ventana de 15 cajas, B=25 reserva 15 intentos de beam y una cuota igual de
dos scans greedy por cada una de las cuatro hojas (23 unidades utilizadas).
Así, la ganadora suele valorar un prefijo de tres colocaciones visibles,
incluida la raíz, y no una completación exhaustiva de la ventana. Si no cabe
ninguna caja antes, el rollout termina; con ventanas menores puede completarlas.

```bash
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h5_cost.yaml --seed-list 1001,1002 --workers 1 --name h5_cost --lock-retry-seconds 60
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h5_eval.yaml --seeds 10 --workers 15 --name h5_eval_a --baseline h3_ref --lock-retry-seconds 60
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h5_eval.yaml --seeds 10 --workers 15 --name h5_eval_b --baseline h3_ref --lock-retry-seconds 60
```

## Evaluación fija (10 seeds de referencia)

| Config | KPI % ± sd pp | IC95 KPI % | media / p95 ms agrupados |
|---|---:|---|---:|
| arm6_4orient | 61.890 ± 1.318 | [60.948; 62.833] | 70 / 147 |
| h3_ref | 70.935 ± 1.499 | [69.862; 72.007] | 617 / 1338 |
| h5_rollout_l4 | 72.040 ± 1.053 | [71.287; 72.793] | 647 / 1425 |

Pareada H5−H3: **+1.105 pp, IC95 [-0.004; 2.214]**, n=10.
**No se demuestra mejora al 95 %**: el intervalo incluye cero. La configuración
se fijó con el ajuste y el coste antes de iniciar esta evaluación; no se vuelve
a seleccionar ni a ajustar con estas seeds.

| Config | D-015 gap5 | gap20 | gap50 |
|---|---:|---:|---:|
| arm6_4orient | 20.7 | 37.7 | 55.7 |
| h3_ref | 26.9 | 54.3 | 75.4 |
| h5_rollout_l4 | 25.2 | 47.3 | 75.0 |

D-015: nº medio de colocaciones por seed, entre las 186 cajas del flujo.
La cifra gap50 apenas cambia (754 → 750 colocaciones sobre 1860 entre H3 y H5).
Todas las configuraciones: 0 violaciones, 0 hard_cap_hits, 0 no colocadas,
0 cutoffs por deadline. El replay de robustez no modifica la accesibilidad.

## Determinismo y cierre

`h5_eval_a` y `h5_eval_b`: **30/30 pares configuración/seed idénticos** en
colocaciones completas (incluidos orden y timestamps simulados), todos los
indicadores de `run_kpi`, utilización por palet, lista de nodos por decisión y
D-015. Los tiempos observacionales y metadatos de salida se excluyen de la
igualdad. Ambas ejecuciones usan el mismo hash de código que el coste.
`out/h5/report.json` conserva cada comprobación por seed y los summaries.

Verificación: **370 tests OK**, incluidos los 10 casos nuevos, y
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` **OK**.
El subconjunto nuevo sin Excel también pasó por separado (8 tests).
No se modificaron Excel, KPI congelado, validador, controles ni gate.
No se hicieron commits, push ni PR; los cambios previos de `PROGRESS.md`
y la especificación aportada se conservaron.

Reproducción de las comprobaciones sobre los artefactos locales:

```bash
.venv/bin/python -m pytest tests/test_state_value.py -m 'not slow' -q
.venv/bin/python out/h5/report.py --winner h5_rollout_l4
```

Artefactos: `out/sweeps/h5_tune/summary.{md,json}` (potencial),
`out/sweeps/h5_rollout_tune/summary.{md,json}` (rollout definitivo),
`out/sweeps/h5_cost/summary.{md,json}`,
`out/sweeps/h5_eval_{a,b}/summary.{md,json}`, `out/h5/selection.json` y
`out/h5/report.json`. La selección se registró antes de la evaluación.

## Réplica preregistrada (director, DECISIONS D-016)

La evaluación en las 10 seeds de referencia fue limítrofe: +1,105 pp, IC95 [−0,004; +2,214]. Antes de ver nuevos datos se fijó una
única réplica: H1+H3 frente a H1+H3+H5, con los mismos parámetros, en 20 seeds nuevas (extras deterministas del runner, índices 10–29),
nunca usadas para ajustar ni evaluar. Criterio de adopción: IC95 de la pareada > 0.

| Réplica (20 seeds nuevas) | KPI % | IC95 % | ms/decisión media / p95 |
|---|---|---|---|
| H1+H3 | 70,206 ± 1,609 | [69,453; 70,959] | 594 / 1290 |
| **H1+H3+H5** | **72,326 ± 1,427** | [71,658; 72,993] | 696 / 1551 |

**Pareada H5 − H1+H3: +2,120 pp, IC95 [1,078; 3,162]** → significativo → **H5 adoptada**. 0 violaciones, 0 hard_cap_hits.

Resultado oficial (seeds de referencia): **H1+H3+H5 = 72,04 %** → 54,8 % del gap de docs/COTAS.md → **supera el umbral de T1
(71,15 %)**, condicionado a que se confirme D-012 (validez en producción de la búsqueda sobre el buffer).

```bash
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/h5_replica.yaml --seed-list 947538410,2468649430,1657320841,1519295179,140304910,2447885127,863902929,4100422140,3947398718,1133686776,537973825,3280072339,1368370141,2238740691,1660244179,350049937,1151828140,1689782132,597050397,1842584672 --workers 15 --name h5_replica --baseline h3_ref --lock-retry-seconds 60
```
