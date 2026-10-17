# Cotas superiores — parte A: cota formal

Este trabajo calcula cotas superiores de la utilización de **un palet** en
`arm6_4orient`. No calcula una solución alcanzable ni ejecuta las partes B/C.
`U*` designa la mejor **cota superior válida obtenida**, no el óptimo físico.
Una cota por encima del objetivo no demuestra que ese objetivo sea alcanzable.

## Datos, población y resultado

Ejecución del 16-10-2026, OR-Tools 9.15.6755, un worker, seed del solver 0 y
30 s por modelo CP-SAT. Artefacto completo: `out/bounds/formal.json` (ignorado
por Git). Las dimensiones del contenedor se importan de
`palca.study.metric.ENVELOPE_MM`; no se duplican en el código del estudio.
En esta versión son 1240 × 840 × 2400 mm, volumen 2 499 840 000 mm³.
La métrica y el validador congelados permanecen intactos.

Entrada: `data/Flujo rampas - Editado.xlsx`, 186 cajas, 111 tipos L/W/H,
volumen real 16 789 012 000 mm³. SHA-256:
`3f1cf01269d17dd5b53e9a14023f31e10d777bd50e14dba16a0d183f15d1b7ab`.
La familia dominante es el subconjunto definido por RESEARCH §1:
**L ∈ [600,610], W ∈ [440,450], H ∈ [345,395] mm**, extremos incluidos,
sobre las dimensiones originales, sin ordenarlas. Son **85 cajas / 24 tipos**.
No se sustituye esta familia por la caja ilustrativa 605×445×355.

| Método / referencia | Flujo completo | Familia dominante |
|---|---:|---:|
| Baseline provisional, KPI de cerrados | 61,9000 % | No es un baseline de familia |
| Objetivo 1,5 × baseline | **92,8500 %** | 92,8500 % como referencia |
| LP DFF con mínimo por orientación | 100,0000 % | 100,0000 % |
| LP DFF con orientaciones compartidas | 100,0000 % | 97,0150364 % |
| Cortes horizontales, inventario real, óptimo 2D probado | 100,0000 % | 98,6727151 % |
| LP DFF + cortes transformados en altura | 100,0000 % | 97,0150364 % |
| Refuerzo de conteos enteros, cota del solver | 100,0000 % | **96,9697211 %** |
| **Mejor U*** | **100,0000 %** | **96,9697211 %** |

Fracción certificada de la mejor cota de familia: **19392703 / 19998720**.
El CP-SAT entero terminó `FEASIBLE`, no `OPTIMAL`: la tabla usa su
`best_objective_bound`, redondeado hacia arriba, nunca el objetivo de su
incumbente. El LP certificado ya da una cota independiente de ese timeout.
El límite por tiempo puede producir ligeras variaciones entre máquinas; cada
JSON conserva la fracción y el estado correspondientes a su ejecución.

**Veredicto: el moonshot no queda descartado por estas cotas**, ni para el
flujo ni para la familia aislada. Tampoco queda demostrado alcanzable.
Los márgenes baseline→cota son 38,1000 pp y 35,0697 pp, respectivamente;
no son mejoras realizables medidas. Una conclusión exclusiva sobre la familia
no se podría trasladar al flujo mixto. Estas cotas deterministas no tienen IC95.
El baseline provisional tampoco tiene un IC95 disponible aquí.

La cota trivial es **Σvol/V = 6,716034626 palets**, luego hacen falta al menos
**7 palets** para todas las cajas. El 6,88 de la especificación no coincide con
el Excel y denominador congelado actuales. Este cociente es una cota inferior
del número de palets, no una utilización del 688 % ni una cota del KPI de
cerrados. La cota trivial de utilización de un palet es 100 %.

No existía `out/sweeps/baseline_v1/summary.json` en este worktree. Si existe al
reproducir, el CLI usa `configs.arm6_4orient.kpi.mean` y conserva su IC95;
también admite resumen de una sola config y listas identificadas por nombre.
Un archivo ambiguo, mal formado o con porcentajes en vez de ratios falla;
no activa silenciosamente el valor provisional ni toma el KPI de otro preset.

## Método DFF y validez

Se admiten `(L,W,H)`, `(W,L,H)`, `(H,W,L)` y `(W,H,L)`, deduplicando empates
y eliminando variantes que no caben. La cara H×L permanece prohibida.
Se relajan soporte, accesibilidad, gate de altura para poner de pie, cargas,
orden de llegada y cualquier obligación de consumir el flujo en orden.
Por tanto toda solución física válida está incluida en las relajaciones.
Se permite elegir cualquier subconjunto del inventario finito para el palet.
Una media de palets cerrados también está acotada por la cota individual.

Para longitudes normalizadas t∈[0,1] se usan 60 funciones:

- Identidad `f(t)=t`.
- `u_k(t)=t` si `(k+1)t` es entero; en otro caso `floor((k+1)t)/k`, k=1..20.
- `U_e(t)=0` si t<e, `t` si e≤t≤1−e, `1` si t>1−e; e=j/40, j=1..20.
- `phi_e(t)=0` si t<e, `1/floor(1/e)` si e≤t≤1/2,
  `1−floor((1−t)/e)/floor(1/e)` si t>1/2; e=j/40, j=1..19.

Son las DFF de las proposiciones 3/4 y teorema 5 de
[Fekete–Schepers, *A General Framework for Bounds for Higher-Dimensional
Orthogonal Packing Problems*](https://arxiv.org/abs/cs/0402044).
Su corolario 8 y lema 9 justifican la restricción de volumen transformado.
Los extremos de los intervalos se evalúan con `Fraction`, sin tolerancias
que cambien el lado de una discontinuidad.

Para cada terna (f,g,h) y tipo i, sea
`a_i = min_o f(l_o/E_x) g(w_o/E_y) h(h_o/E_z)`.
El volumen transformado de cualquier colocación factible suma como máximo uno.
Tomar el mínimo por orientación reduce los coeficientes: sigue siendo una
condición necesaria. El primer LP maximiza `Σ_i (vol_i/V) x_i`, sujeto a
`Σ_i a_i x_i ≤ 1`, `0 ≤ x_i ≤ cantidad_i`. Agrupar cajas idénticas equivale
al LP de una variable en [0,1] por caja individual.

El segundo LP usa variables `y_io ≥ 0`, con `Σ_o y_io ≤ cantidad_i` y
coeficientes específicos de cada orientación en todas las ternas. Evita que
una caja elija independientemente una orientación distinta para cada corte.
Sigue relajando la integralidad y la geometría de colocación.

Se consideran todas las ternas del conjunto. Las funciones iguales o dominadas
en todas las longitudes de un eje, y las filas duplicadas, se eliminan sin
perder fuerza. En el flujo quedan 29/35/19 evaluaciones distintas no dominadas
por eje y 18 064 filas orientadas, antes de las 111 restricciones de inventario.
GLOP añade por separación los cortes violados, comprobando el conjunto completo
tras cada solución restringida.

### Certificado numérico

Cada evaluación racional se redondea **hacia abajo** a una rejilla Q=10⁶;
el producto se vuelve a redondear hacia abajo usando enteros de 64 bits. Esto
solo debilita restricciones. La cota final no se toma del objetivo primal
aproximado de GLOP. Para el LP `Ax≤b, 0≤x≤n`, cualquier λ≥0 cumple:

```text
c·x ≤ λ·b + Σ_j n_j max(0, c_j − (λA)_j).
```

Se convierten los multiplicadores del solver a racionales no negativos de
denominador 10⁹ y se calcula esta expresión exactamente. Así se corrigen incluso
los residuos de factibilidad dual. El JSON incluye filas activas, multiplicadores,
corrección positiva y fracción final. Las filas se reconstruyen de forma
determinista con el inventario del JSON y `build_dff_system`; para el LP reforzado
se añaden las filas de cortes de altura antes de ordenar/deduplicar. Los valores
decimales son de presentación; las fracciones son el certificado.
El mínimo con la cota geométrica exacta 1 también es válido.

## Cortes horizontales y refuerzos

En casi toda altura z, las huellas de las cajas que la atraviesan forman un
packing 2D sin solapes. CP-SAT maximiza su área en la base de la envolvente,
con todas las huellas permitidas, giros y cantidades por tipo. Se usan intervalos
opcionales, `NoOverlap2D`, restricciones acumulativas y una tabla que vincula
huella, área y tipo. El número de posiciones opcionales es
`floor(área_base / menor_área_huella)`, suficiente para cualquier corte factible.

Las coordenadas enteras no son una discretización que recorte soluciones:
fijadas las separaciones de cada par en una disposición ortogonal, compactar
por caminos más largos produce coordenadas que son sumas de dimensiones.
Puede dividirse por su máximo común divisor con los lados del contenedor
(5 mm en estos datos). No se imponen franjas, capas uniformes ni cortes guillotina.

Si A es una cota superior del área máxima, integrar en z da `volumen ≤ A·E_z`,
y por tanto `U≤A/área_base`. En esta ejecución los máximos 2D están probados:
**1 041 600 mm²** (flujo) y **1 027 775 mm²** (familia). El JSON conserva una
disposición 2D testigo; no es una solución 3D ni de producción. Si el solver
termina antes de probar el óptimo, se usa su cota superior; sin resultado
utilizable se conserva la cota de área de la envolvente.

Además, aplicar una DFF solo a z conserva la existencia de un packing con las
mismas huellas. Integrar en esa altura transformada justifica, para cada f:
`Σ_io área_io f(h_io/E_z) y_io ≤ A`. Se añaden estas filas al LP; en los datos
canónicos no mejoran la cota obtenida con orientaciones compartidas.

El último refuerzo exige conteos enteros por orientación en CP-SAT, preserva
cantidades y volumen real y utiliza las filas DFF activas del certificado del
LP reforzado. Quitar otras filas es otra relajación válida. Sus incumbentes
son soluciones de conteos, **sin garantía de packing 3D**. Solo se usa la cota
superior de maximización. La mejora sobre el LP de familia es pequeña:
aproximadamente 0,0453 pp; no se ha demostrado una cota <95 %.

Como exploración también se resolvió el catálogo horizontal con copias
ilimitadas: dio exactamente 100 % y 99,3303571 %. Una disposición escalonada
de seis huellas explica el segundo valor; limitar las copias al inventario
real baja a 98,6727151 %. Esto muestra por qué no sería válido imponer los
patrones por franjas de RESEARCH como si fueran todos los packings posibles.

## Verificación y reproducción

Desde la raíz, con el entorno del proyecto y el extra `study` de `pyproject.toml`
(OR-Tools 9.15 instalado en el entorno de esta ejecución):

```bash
export PYTHONPATH="$PWD/src"
.venv/bin/python scripts/bound_formal.py --seconds 30
.venv/bin/python -m pytest tests/test_bounds_formal.py --disable-warnings -ra
./scripts/smoke_offline.sh
```

Resultado: **76 tests nuevos OK** (`76 passed in 1.49s`); smoke completo
**OK**, incluida la suite del repositorio y el escenario de height-slack.
La validación del baseline devuelve `boxes_placed=186`, `n_violations=0`.
También se comprobaron contención y ausencia de solapes exactas del fixture,
sin aprovechar tolerancias, y los dos testigos horizontales canónicos.

El CLI escribe `out/bounds/formal.json`; no requiere `scripts/run_sweep.py`.
Para explorar el catálogo horizontal ilimitado por separado:

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from palca.study.bounds_formal import horizontal_bound, is_dominant
from palca.study.params import REFERENCE_EXCEL
from sim.io import load_arrivals
boxes = [(a.length_mm, a.width_mm, a.height_mm) for a in load_arrivals(REFERENCE_EXCEL)]
for subset in (boxes, [d for d in boxes if is_dominant(d)]):
    print(horizontal_bound(subset, seconds=30, finite_inventory=False))
PY
```

Los tests comprueban cada una de las 60 DFF mediante programación dinámica
de mochila ilimitada: exploran **todos los multiconjuntos de una rejilla** cuya
suma es ≤1, no solo parejas. Cubren todos los saltos de epsilon y las igualdades
de `u_k`, con pruebas adicionales a ambos lados de las discontinuidades.
No se presupone que `phi` sea superaditiva. El test finito sirve de regresión;
la justificación en todo [0,1] es el resultado matemático citado.

También verifican llenados exactos, el óptimo de ocho cubos de lado 4 en un
cubo de lado 10, certificados duales con residuos, inventario, timeout seguro,
selección del baseline y todas las cotas frente a:

- **12 cajas planas** 605×445×355: 45,8788762 %.
- **18 cajas planas + de pie**: 68,8183144 %.
- Un **palet real de baseline**, leído desde
  `tests/fixtures/bounds_baseline_pallet.json`: 19 cajas, **72,9295875 %**.

Los patrones tienen coordenadas explícitas y pasan el validador congelado.
El fixture conserva el palet físico 10 del dump del preset `arm6_4orient`,
seed 50021, presupuesto original 900 ms. Sus dimensiones originales se
contrastan de nuevo con el Excel, shuffle e identidad DES, y tiene 0 violaciones.
El run completo utilizado para extraerlo colocó 186 cajas en 11 palets,
10 cerrados, KPI **64,0956641 %**, y 0 violaciones del validador en los 11.
Ese resultado de una seed no sustituye al baseline provisional de diez seeds.
El validador congelado comprueba geometría y soporte; no certifica trayectoria
ni accesibilidad del brazo.

Para regenerar ese run y su dump, sin depender del fixture:

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from pathlib import Path
from palca.study.params import preset_params
from sim.run import run_simulation
folder = Path('out/bounds')
folder.mkdir(parents=True, exist_ok=True)
run_simulation(**preset_params(
    'arm6_4orient', episode_seed=50021,
    out_path=str(folder / 'baseline_50021_run.json'),
    dump_placements_path=str(folder / 'baseline_50021_dump.json'),
))
PY
.venv/bin/python scripts/kpi_from_run.py \
  --run out/bounds/baseline_50021_run.json \
  --dump out/bounds/baseline_50021_dump.json --validate
```

Supuesto geométrico: dimensiones enteras y cajas rígidas ortogonales sin
penetración física. Las tolerancias del validador (0,5 mm en geometría) se
interpretan como tolerancias de medición, no como capacidad adicional que
permita solapes físicos. Los patrones y el dump de regresión cumplen la
geometría exacta. Las cotas no certifican cualquier disposición artificial
que aproveche sistemáticamente dichas tolerancias.


# Cotas superiores — parte C: palancas de información

Esta sección mide solo la parte C de `autopilot/specs/04_cotas_superiores.md`.
Las partes A, B y la tabla final de headroom se integran en otros trabajos.

## Método y alcance

Se usa el flujo canónico de 186 cajas, el preset `arm6_4orient` y las diez seeds
oficiales: 50021, 1, 42, 100, 777, 5678, 9999, 12345, 99999 y 314159.
Se mantienen las cuatro orientaciones, soporte mínimo 0,85, envolvente
1240 × 840 × 2400 mm y `RobotAccessibilityControl` con delta 400 mm.
La métrica y el validador congelados no se modifican. El validador independiente
comprueba geometría, orientación y soporte; la accesibilidad se comprueba en el
`PalletModel` de producción durante cada preview/commit.

El baseline oficial es `out/sweeps/baseline_v1/arm6_4orient`: **61,890 %**, sd
**1,318 pp**, IC95 **[60,948; 62,833] %**. El «± 1,32» de la referencia es la
desviación típica entre seeds, no la semiamplitud del IC95 (0,943 pp).
Cada sweep incluye una reproducción independiente llamada `arm6_4orient`
(`role: bound`, k=15, N=1) y utiliza `--baseline arm6_4orient`.

- **Buffer/información:** `ramp_cap = lookahead_k = k`, para k ∈ {15, 30, 75, 186},
  con N=1. k=15 es el buffer físico; k=186 permite ver todo el flujo restante.
  Se mantiene `shuffle_window=15`, para comparar la misma secuencia de entrada
  por seed; solo cambia la ventana visible y seleccionable.
- **Palanca operativa: más posiciones de palet:** N ∈ {1, 2, 3, 4}, con k=15.
  N>1 supone N posiciones físicas por destino y no constituye una mejora que
  pueda aplicarse solo cambiando software en una instalación de una posición.
  Cada posición tiene su propio `PalletModel`. El scheduler enumera
  (caja visible, posición disponible), obtiene la colocación con el packer de
  producción y aplica el scorer de producción sobre el conjunto completo de
  candidatos, incluido el filtro global de altura/slack de 120 mm.
  Los empates conservan el orden caja/posición. No se comparan únicamente
  puntuaciones escalares de ganadores independientes por palet.

### Regla de cierre y fin del flujo

Con N>1, un palet no vacío se cierra con `NO_VISIBLE_FIT` cuando ninguna caja de
su destino en la ventana visible tiene preview factible. Se exige una evaluación
completa: un cutoff de tiempo/candidatos no demuestra que ninguna caja quepa y
no provoca ese cierre. Se cierra aunque otra posición tenga una acción factible.
Se sustituye solo esa posición, que queda bloqueada durante `t_changeover=60 s`
simulados; las demás pueden seguir recibiendo cajas con el mismo robot.
Los palets vacíos no se cuentan ni se cierran por falta de ajuste.

Al agotarse el flujo, los palets residuales se ordenan por instante de último
uso y posición. Todos salvo el último se cierran con `DRAIN`; solo el último
recibe `END`. Por tanto el KPI congelado incluye los parciales residuales de
las otras posiciones: no se excluyen N colas para favorecer a N>1. El dump se
agrupa por palet físico en orden de cierre y conserva `physical_slot` y
`slot_generation`, además de los timestamps de colocación. La telemetría JSON
incluye los cierres, cambios de posición y la etiqueta de palanca operativa.

`open_pallets=1` usa la ruta original del DES y de la política. La variante N>1
se limita a `policy=palca`, M1, llegadas inmediatas y palets continuos; rechaza
microplanner, controlador online, multi-rampa, visualizador y `max_pallets`.
Son restricciones explícitas de esta variante experimental, no cambios de los
defaults de producción.

## Reproducción

Desde la raíz del worktree, con el venv existente y PyYAML instalado:

```bash
export PYTHONPATH="$(pwd)/src"
.venv/bin/python -m pytest tests/test_bound_information.py -q
./scripts/smoke_offline.sh

# Ejecutar secuencialmente: esperar a que termine el primer comando.
.venv/bin/python scripts/run_sweep.py \
  --configs configs/sweeps/bound_buffer_k.yaml \
  --seeds 10 --workers 15 --name bound_buffer_k_v1 --baseline arm6_4orient
.venv/bin/python scripts/run_sweep.py \
  --configs configs/sweeps/bound_open_pallets.yaml \
  --seeds 10 --workers 15 --name bound_open_pallets_v1 --baseline arm6_4orient
```

El runner mantiene `/tmp/palca_sweep.lock`, fija un hilo BLAS por worker,
guarda params efectivos y hashes de código/entrada y permite reanudar resultados
con el mismo hash. No se ejecutan ambos sweeps a la vez.
Se mantiene el presupuesto de producción de 900 ms por decisión.
Los tiempos medidos son observacionales e incluyen decisiones sin colocación.

Los IC95 son t de Student bilaterales sobre diez valores (unidad = seed).
Las diferencias pareadas usan las mismas seeds, config − baseline, en pp.
Los ms de las tablas son la media entre seeds del tiempo medio y del p95 de
cada seed, respectivamente; este último no es el p95 de una mezcla de runs.
Los palets se expresan como media total/cerrados por seed; las cajas no colocadas
son entradas menos colocadas. Los summaries también incluyen sd y KPI nominal.

## Resultados

### Buffer/lookahead (N=1)

| Valor | KPI % ± semiamplitud IC95 (pp) | IC95 KPI % | Δ pareada pp [IC95] | Palets total/cerrados | No colocadas | ms/decisión media / p95 | Cutoffs/seed |
|---|---:|---|---|---:|---:|---:|---:|
| 15 (físico) | 61.890 ± 0.943 | [60.948; 62.833] | 0,000 [0,000; 0,000] | 11.1 / 10.1 | 0 | 75.630 / 157.601 | 0.0 |
| 30 | 63.021 ± 0.970 | [62.050; 63.991] | +1.130 [+0.038; +2.222] | 11.0 / 10.0 | 0 | 141.804 / 298.316 | 0.0 |
| 75 | 64.926 ± 1.244 | [63.682; 66.169] | +3.035 [+1.580; +4.490] | 11.1 / 10.1 | 0 | 292.200 / 721.934 | 2.5 |
| 186 (todo el flujo) | 66.817 ± 0.513 | [66.303; 67.330] | +4.926 [+3.938; +5.914] | 10.6 / 9.6 | 0 | 321.123 / 882.530 | 13.8 |

Summary completo: [`bound_buffer_k_v1/summary.md`](../out/sweeps/bound_buffer_k_v1/summary.md).
Ejecución UTC: 2026-10-16T12:26:36.662265+00:00 → 2026-10-16T12:28:52.796352+00:00.

### Palanca operativa: posiciones abiertas (k=15)

| Valor | KPI % ± semiamplitud IC95 (pp) | IC95 KPI % | Δ pareada pp [IC95] | Palets total/cerrados | No colocadas | ms/decisión media / p95 | Cutoffs/seed |
|---|---:|---|---|---:|---:|---:|---:|
| 1 (control) | 61.890 ± 0.943 | [60.948; 62.833] | 0,000 [0,000; 0,000] | 11.1 / 10.1 | 0 | 73.037 / 151.594 | 0.0 |
| 2 | 59.908 ± 1.417 | [58.492; 61.325] | -1.982 [-3.759; -0.206] | 11.6 / 10.6 | 0 | 143.698 / 283.825 | 0.0 |
| 3 | 56.162 ± 2.628 | [53.533; 58.790] | -5.729 [-8.842; -2.616] | 12.5 / 11.5 | 0 | 224.027 / 420.949 | 0.0 |
| 4 | 55.934 ± 3.016 | [52.917; 58.950] | -5.957 [-9.184; -2.729] | 12.4 / 11.4 | 0 | 244.310 / 520.933 | 0.0 |

Summary completo: [`bound_open_pallets_v1/summary.md`](../out/sweeps/bound_open_pallets_v1/summary.md).
Ejecución UTC: 2026-10-16T12:29:01.619090+00:00 → 2026-10-16T12:30:49.333500+00:00.

### Control adicional: todos los palets, incluido END

Este diagnóstico evita atribuir al empaquetado mejoras debidas solo a qué volumen queda en el último palet. Usa `kpi_all_pallets` de la misma métrica congelada; no sustituye al KPI oficial.

| Configuración | Utilización de todos los palets % [IC95] |
|---|---|
| arm6_4orient | 60.546 [59.395; 61.697] |
| buffer_k30 | 61.055 [61.055; 61.055] |
| buffer_k75 | 60.546 [59.395; 61.697] |
| buffer_k186 | 63.497 [61.242; 65.752] |
| open_pallets_n2 | 58.002 [56.123; 59.882] |
| open_pallets_n3 | 54.016 [51.069; 56.963] |
| open_pallets_n4 | 54.603 [50.889; 58.316] |

### Lectura de los resultados y límites

En las 80 ejecuciones se colocan las 186 cajas y el validador registra **cero
violaciones**. Ambos controles k=15, N=1 reproducen exactamente el KPI y los
palets oficiales por seed (20/20 reproducciones entre los dos sweeps).
Los dos sweeps usan el mismo hash de código local
`abf5b72050bbb87eca7243753f8ef80edea9deef9ae79a675241c70b597b383e`.
El primero termina antes del inicio del segundo; las fechas constan arriba y
en los manifests/summaries. Los artefactos guardan el HEAD de partida y el hash
de los cambios sin commit, no un supuesto commit de esta implementación.

El mejor resultado de C es k=186: **66,817 % ± 0,513 pp de IC95**, con mejora
pareada **+4,926 pp [3,938; 5,914]** frente al baseline. No es un óptimo ni una
cota formal. k=75 y k=186 registran, respectivamente, **25 y 138 cutoffs** en
sus diez seeds. Sus resultados dependen también del presupuesto computacional
de producción y de la carga de CPU: disponer del flujo completo no implica
examinar siempre toda la ventana antes del deadline. N=1 conserva también la
regla de cierre original, sin alterarla para estos experimentos.

El aumento de posiciones **no mejora** el KPI con esta política: los IC95
pareados de N=2, 3 y 4 quedan enteramente por debajo de cero. No hubo cutoffs en
este sweep, por lo que ese empeoramiento no procede del límite de 900 ms.
La regla de altura favorece repartir cajas entre posiciones bajas; al final
quedan, en promedio, 0,8 / 1,9 / 2,9 palets `DRAIN` para N=2 / 3 / 4. Esto es
compatible con una penalización por dispersión y colas parciales; es una
interpretación del comportamiento, no una descomposición causal medida. La
utilización incluyendo END también empeora, así que no se explica solo por
la exclusión del último palet. N=1 sigue siendo el mejor valor de esta palanca
operativa para el scorer y flujo evaluados.

Otro resultado relevante: k=75 mejora el KPI oficial en 3,035 pp, pero deja
la misma media de palets totales (11,1) y la misma utilización media sobre
todos los palets (60,546 %) que el baseline. No debe interpretarse como una
reducción equivalente del número total de palets. k=186 sí lo reduce a 10,6
palets de media y mejora ese diagnóstico a 63,497 %.

## Verificación

`tests/test_bound_information.py` contiene 25 casos: regresión exacta contra
un fixture extraído del baseline oficial para las diez seeds (KPI, número y
secuencia de palets y utilizaciones individuales), configs/defaults, selección
conjunta con slack global, cierre solo tras ventana completa, cambio de posición
incluido tiempo cero, reconstrucción física y validador, colas `DRAIN`/`END`,
caja imposible sin bucle de palets vacíos y rechazo de modos no soportados.
El contrato del benchmark de un único palet conserva `open_pallets=1`.

Verificación final: `./scripts/smoke_offline.sh` **OK**, incluida la suite
completa y el escenario height-slack; `git diff --check` **OK**. La auditoría de
los 80 runs confirma también IDs de caja únicos y el mismo hash del Excel que
el baseline oficial. Una ejecución anterior del smoke encontró el lock global
ocupado por otro proceso; se repitió tras finalizar los dos sweeps, sin quitar
ni eludir el bloqueo.

Para comprobar además los artefactos reales de ambos sweeps:

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path

root = Path('out/sweeps')
summaries = []
for name in ('bound_buffer_k_v1', 'bound_open_pallets_v1'):
    summary = json.loads((root / name / 'summary.json').read_text())
    summaries.append(summary)
    assert len(summary['seeds']) == 10
    for config in summary['configs']:
        for seed in summary['seeds']:
            row = json.loads((root / name / config / str(seed) / 'kpi.json').read_text())
            assert row['n_unplaced'] == row['n_violations'] == 0
            assert row['boxes_placed'] == 186
            if config == 'arm6_4orient':
                ref = json.loads((root / 'baseline_v1' / config / str(seed) / 'kpi.json').read_text())
                for key in ('kpi', 'n_pallets_total', 'n_closed', 'boxes_placed', 'pallet_utils'):
                    assert row[key] == ref[key], (name, seed, key)
assert summaries[0]['generated_at'] < summaries[1]['date']
assert summaries[0]['source_hash'] == summaries[1]['source_hash']
print('OK: controles exactos 20/20; 80/80 runs completos y válidos; sweeps secuenciales')
PY
```

No se han modificado la métrica, el validador ni los Excel. Los resultados en
`out/` quedan como artefactos locales; no se realizan commits ni push.


# Cotas superiores — parte B: techo alcanzable offline (clarividente)

Información completa: las 186 cajas disponibles desde el inicio y orden de colocación libre. Mismas reglas físicas que
`arm6_4orient`: la factibilidad se comprueba con `PalletModel` del preset (soporte 0,85, accesibilidad 400 mm, gate
stand_hw, 4 orientaciones). Código: `src/palca/study/offline_search.py` y `scripts/bound_offline.py` (tests en
`tests/test_offline_search.py`). Implementado por Codex (gpt-6-astra). El trabajo se cortó dos veces por la cuota de Codex;
esta sección la redacta el director con los artefactos producidos (DECISIONS D-011, D-013).

Es un **techo alcanzable** (cota inferior del óptimo real con información completa), no una cota superior formal: más búsqueda
puede subirlo.

## Métodos y resultados (verificados de forma independiente)

Cada resultado se verificó con `scripts/autopilot/verify_offline_witness.py`, que no usa el packer. El verificador comprueba:
- que el multiconjunto de dimensiones colocadas es igual al del Excel (cada caja exactamente una vez);
- el validador congelado: envolvente, solapes, cara apoyada y soporte ≥ 0,85;
- la regla de accesibilidad de 400 mm, re-implementada en el orden de colocación.

| Método | KPI oficial (palets cerrados) | Σvol/(n·V) | Palets | CPU | Comando |
|---|---|---|---|---|---|
| Orden de entrada optimizado (búsqueda local) + política de producción | 68,34 % | — | 10 | 376 s | `scripts/bound_offline.py --method order --evaluations 8 --cpu-seconds 900 --out out/bounds/offline_order.json` |
| Beam palet a palet (ancho 16, top-M 8, scorer de producción) | 71,64 % | 67,16 % | 10 | 891 s | `scripts/bound_offline.py --method beam --width 16 --top-m 8 --cpu-seconds 1600 --out out/bounds/offline_beam_production.json` |
| **Columnas por plantilla de planta** (beam por columna, ancho 8, plantilla 1) | **78,87 %** | 74,62 % | **9** | 29 s | `scripts/bound_offline.py --method columns --width 8 --templates 1 --cpu-seconds 600 --out out/bounds/offline_columns_w8.json` |
| **Columnas, testigo verificado** (versión intermedia del método, plantillas 0–2, ancho 16) | **80,40 %** | 74,62 % | **9** | 230 s | testigo: `docs/autopilot/evidence/offline_witness_columns_80p40.json` (`verify_offline_witness.py` → rc 0) |

Las cuatro soluciones tienen **0 violaciones** del validador y **0 de accesibilidad**. El testigo del 80,40 % no se puede regenerar
con el código actual: salió de una versión intermedia del script de columnas, sobrescrita antes del corte (la versión actual
reproduce 78,35 % con los mismos argumentos). Se conserva como evidencia porque su validez no depende de cómo se generó.

**Lectura.** El método de columnas fija una partición de la planta y apila columnas en ella: dos columnas planas de
≈ 619 × 448 a lo largo de x, más tres columnas de cajas de pie en la franja de ≈ 390 mm. Con eso coloca el flujo en
**9 palets** frente a los 11,1 de media del baseline online, y el mínimo trivial es 7. Es la confirmación offline de la
hipótesis de carriles (RESEARCH §1, §1ter). Con reordenación libre, el último palet recibe los restos (28–45 %), así que el KPI
de palets cerrados favorece algo al offline; por eso se reporta también Σvol/(n·V).

## Tabla de headroom (arm6_4orient, KPI congelado v1.0.0)

| Referencia | KPI | Δ vs baseline | Tipo |
|---|---|---|---|
| Baseline online `arm6_4orient` (10 seeds) | 61,89 % ± 1,32 · IC95 [60,95; 62,83] | — | medida |
| H1 (mejor técnica online adoptada, 10 seeds) | 64,70 % · IC95 [63,47; 65,92] | +2,81 pp | medida (PR #145) |
| Buffer k = 186 (política de producción) | 66,82 % | +4,93 pp | palanca de información |
| H1 + micro_plan por tiempo (no determinista) | 70,90 % | +9,01 pp | palanca (D-012) |
| Offline beam (scorer de producción) | 71,64 % | +9,75 pp | techo alcanzable |
| Offline columnas (reproducible) | 78,87 % | +16,98 pp | techo alcanzable |
| **Offline columnas (testigo verificado)** | **80,40 %** | **+18,51 pp** | **techo alcanzable usado para el gap** |
| Cota formal por palet (relajada) | 100 % (flujo) / 96,97 % (familia dominante) | — | cota superior formal, no informativa |
| Cota trivial de palets | ≥ 6,72 → 7 palets | — | cota formal |

**Gap de referencia = 80,40 − 61,89 = 18,51 pp** (se usa el techo mayor, lo que hace las metas más exigentes; D-013). Si una
búsqueda offline posterior supera el 80,40 %, el gap y las metas se recalculan al alza.

| Meta | Definición | Umbral |
|---|---|---|
| T1 (obligatoria) | ≥ 50 % del gap, algoritmo puro en arm6_4orient | **≥ 71,15 %** |
| T2 (ambiciosa) | ≥ 90 % del gap (**≥ 78,55 %**), o ≥ 80 % absoluto en una configuración físicamente justificable con palancas cuantificadas | ≥ 78,55 % / ≥ 80 % |
| T3 (moonshot) | 1,5 × baseline, o refutación formal o empírica con margen (D-010) | ≥ 92,84 % |

Sobre T3: el mejor techo alcanzable encontrado (80,40 %) queda 12,4 pp por debajo del moonshot. Hace falta más esfuerzo de
búsqueda offline y el margen exigido por D-010 (≥ 10 pp, con su IC) antes de declararlo refutado empíricamente.

## Techo offline sin resquicio

Medición del 2026-10-17 en `feat/techo-offline-estricto` (D-015). Se elimina
la separación **explícita** entre zonas mediante `column_gap_mm=0`
(`--column-gap-mm 0`). El default sigue siendo **2 mm** y reproduce exactamente
las tres plantillas anteriores. Con cero, las cinco zonas de cada plantilla
recubren los 1240 × 840 mm sin solaparse ni dejar franjas entre columnas:
dos zonas planas de 620 mm de largo y tres zonas en la franja restante.
Se recupera el área que antes ocupaban las separaciones.

Las cajas siguen centradas dentro de su zona y sus dimensiones pueden ser
menores que ella: **zonas contiguas no significa que todas las huellas de las
cajas se toquen**. El relleno final sigue usando las posiciones de producción.
Por eso se mide además `--adjacency-gap 50`; eliminar el hueco explícito de
2 mm no certifica robustez a 50 mm. No se cambia el gate de pie, el control de
accesibilidad de producción, los Excel, el KPI ni el validador congelado.

Se ejecuta cada combinación de plantilla 0/1/2 y ancho 8/16 por separado,
además de dos ejecuciones con las tres plantillas disponibles en cada palet,
con seed 0 y `group_geometry=False`. El beam usa ancho 16, top-M 8,
`objective=volume` y `locations=production`, sin particiones artificiales.
Presupuesto de búsqueda: **900 s CPU**, con límite duro de **1200 s CPU**
por proceso (`prlimit`); máximo **4 búsquedas simultáneas**, un hilo BLAS por
búsqueda. La CPU de la tabla es usuario + sistema del proceso completo,
incluidos arranque, referencias y validación (`*.time.json`).

| Método | Plantilla | Ancho | KPI oficial % | Σvol/(n·V) % | Palets total/cerrados | Viol. validador | Acc. G=0 | Acc. G=50 | CPU s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Columns, gap 0 | 0 | 8 | 75,690 | 74,623 | 9/8 | 0 | 0 | 103 | 38,54 |
| Columns, gap 0 | 0 | 16 | 77,092 | 74,623 | 9/8 | 0 | 0 | 103 | 71,33 |
| Columns, gap 0 | 1 | 8 | 79,200 | 74,623 | 9/8 | 0 | 0 | 118 | 45,66 |
| Columns, gap 0 | 1 | 16 | 78,735 | 74,623 | 9/8 | 0 | 0 | 111 | 85,34 |
| Columns, gap 0 | 2 | 8 | 68,328 | 67,160 | 10/9 | 0 | 0 | 80 | 37,31 |
| Columns, gap 0 | 2 | 16 | 68,328 | 67,160 | 10/9 | 0 | 0 | 80 | 55,07 |
| **Columns, gap 0** | **0, 1, 2** | **8** | **81,449** | **74,623** | **9/8** | **0** | **0** | **123** | **117,04** |
| Columns, gap 0 | 0, 1, 2 | 16 | 79,549 | 74,623 | 9/8 | 0 | 0 | 118 | 206,86 |
| Beam de producción | — | 16 | 71,643 | 67,160 | 10/9 | 0 | 0 | 63 | 835,67 |

Las nueve ejecuciones completan la búsqueda sin fallback de
presupuesto. Todas colocan exactamente las **186 cajas**, conservan sus IDs
y pasan tanto el replay con los controles de producción (`--validate`) como
la comprobación independiente del multiconjunto del Excel y del validador.
G se aplica al hueco en **ambos ejes**, con comparación inclusiva `≤ G`;
la altura local se calcula solo con solape real, sin expandir la huella de
soporte. Se cuenta una violación por colocación bloqueada, sobre la secuencia
original, sin recolocar cajas ni modificar el KPI. El verificador devuelve
rc 0 para G=0 y rc 1 para G=50 cuando detecta esas violaciones.

El mejor testigo de columnas sin separación explícita alcanza **81,449 %**,
con **9 palets** y utilización global **74,623 %**. Es una cota inferior
constructiva del óptimo offline bajo la regla de producción, no un óptimo
demostrado. Queda 1,049 pp por encima del testigo histórico de 80,40 % y
19,559 pp por encima del baseline online de 61,89 %. La diferencia frente
al histórico **no es una ablación pareada del hueco**: aquel testigo proviene
de una versión intermedia del buscador. Tampoco se observa mejora monótona
al aumentar el ancho: las decisiones locales de un palet cambian el
inventario disponible para los siguientes.

El testigo conjunto de ancho 8 deja un último palet de aproximadamente
20,008 % de utilización. El KPI oficial excluye ese palet: el salto frente
al 79,200 % de la plantilla 1 no reduce los nueve palets ni cambia el
74,623 % global. También depende de cómo se distribuye el volumen en END.

El beam reproduce el **71,643 %** de la búsqueda anterior con scorer de
producción, en diez palets. Registra menos violaciones con G=50 (**63**),
pero tampoco es factible con esa definición de adyacencia.

Las **123 violaciones con G=50** del mejor testigo impiden presentarlo como
un techo factible para esa regla más exigente. Ningún testigo de
esta tabla es factible con G=50. Los datos confirman un techo alcanzable alto
sin separación explícita de 2 mm, pero no resuelven la holgura física de la
garra señalada en D-015. Las tablas históricas anteriores se conservan como
registro de sus respectivas condiciones.

Artefactos locales, sin versionar: `out/bounds/techo_gap0/columns_t{T}_w{W}.json`
(T = 0, 1, 2 o 012),
`beam_w16.json`, sus checkpoints, tiempos, logs de verificación G=0/G=50 y
`summary.json` con comandos y resultados. Los JSON guardan configuración
efectiva, hashes de código y Excel, referencias y secuencia completa.
Los summaries de baseline y buffer se leen del worktree vecino
`../paletizado-autopilot/out/sweeps/`; no se copian ni se modifican.

Reproducción desde la raíz, con rutas de salida nuevas o todavía inexistentes:

```bash
export PYTHONPATH="$(pwd)/src"
refs=../paletizado-autopilot/out/sweeps
dest=out/bounds/techo_gap0
mkdir -p "$dest"
common=(--cpu-seconds 900
        --baseline-summary "$refs/baseline_v1/summary.json"
        --buffer-summary "$refs/bound_buffer_k_v1/summary.json")
for t in 0 1 2; do
  for w in 8 16; do
    prlimit --cpu=1200:1200 /usr/bin/time \
      .venv/bin/python scripts/bound_offline.py --method columns \
      --column-gap-mm 0 --templates "$t" --width "$w" "${common[@]}" \
      --out "$dest/columns_t${t}_w${w}.json"
  done
done
for w in 8 16; do
  prlimit --cpu=1200:1200 /usr/bin/time \
    .venv/bin/python scripts/bound_offline.py --method columns \
    --column-gap-mm 0 --templates 0 1 2 --width "$w" "${common[@]}" \
    --out "$dest/columns_t012_w${w}.json"
done
prlimit --cpu=1200:1200 /usr/bin/time \
  .venv/bin/python scripts/bound_offline.py --method beam --width 16 --top-m 8 \
  "${common[@]}" --out "$dest/beam_w16.json"

for witness in "$dest"/columns_t*_w*.json "$dest/beam_w16.json"; do
  .venv/bin/python scripts/bound_offline.py --method columns --validate "$witness"
  .venv/bin/python scripts/autopilot/verify_offline_witness.py "$witness" --adjacency-gap 0
  # rc 1 indica violaciones de la métrica estricta; consultar el recuento.
  .venv/bin/python scripts/autopilot/verify_offline_witness.py "$witness" --adjacency-gap 50
done
.venv/bin/python -m pytest tests/test_offline_search.py tests/test_verify_offline_witness.py -q
./scripts/smoke_offline.sh
git --no-pager diff --check
```

Los tests comprueban la igualdad exacta de las plantillas por defecto,
la cobertura contigua con gap 0, candidatos contenidos en sus zonas, rechazo
real de una columna que toca una vecina alta, replay e identidad del
checkpoint al cambiar el gap. También comprueban los umbrales inclusivos en
ambos ejes, orden cronológico, altura local y el recuento histórico de D-015
(0 con G=0, 117 con G=50).

Verificación: los **36 tests dirigidos** pasan; `export PYTHONPATH=$(pwd)/src &&
./scripts/smoke_offline.sh` **OK** (448 tests y escenario height-slack).
Auditoría de los nueve testigos **OK**: mismo hash de código y Excel,
186 IDs únicos por ejecución, replay y verificador G=0 con rc 0, y CPU
individual inferior a 1200 s. `git --no-pager diff --check` **OK**.

## Metas recalculadas con el techo sin resquicio (director, D-013)

El método de columnas con plantillas contiguas (con --column-gap-mm 0) supera al testigo anterior: **81,449 %** (9 palets, 0 violaciones,
0 de accesibilidad con la regla de producción). Además es reproducible con un comando (ver arriba). Por D-013 las metas se recalculan al alza:

| Meta | Definición | Umbral anterior (techo 80,40) | **Umbral vigente (techo 81,45)** |
|---|---|---|---|
| T1 | ≥ 50 % del gap | 71,15 % | **71,67 %** |
| T2 | ≥ 90 % del gap (o ≥ 80 % absoluto con palancas justificadas) | 78,55 % | **79,49 %** |
| T3 | 1,5 × baseline | 92,84 % | 92,84 % |

Gap vigente = 81,45 − 61,89 = **19,56 pp**. La mejor técnica online (H1+H3+H5+H6, 74,03 %) cierra el 62,1 % del gap.

Advertencia (D-015): ninguna solución offline sería factible si la regla de accesibilidad considerara adyacentes las cajas con
hueco ≤ 50 mm (123 colocaciones lo violarían en el testigo de 81,45 %). Con la regla de producción tal cual, el techo es válido.
