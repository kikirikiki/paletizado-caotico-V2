# PALANCA OPERATIVA: secuenciación aguas arriba (D-019, R4)

## Método

El almacén reordena las cajas **antes** de entregarlas a la rampa. Se aplica el
shuffle habitual por seed (`shuffle_window=15`, intensidad 1), se divide el
flujo resultante en bloques consecutivos de W cajas y se ordena cada bloque
de forma estable. El último bloque puede ser más corto. Los empates conservan
el orden posterior al shuffle. No se mueven cajas entre bloques.

`run_simulation` y el CLI (`--arrival-sort-window`, `--arrival-sort-key`)
aceptan:

- `arrival_sort_window: int = 0`: 0 conserva exactamente el flujo actual;
  1 tampoco cambia el orden; W ≥ número de cajas ordena el flujo completo.
  Los valores negativos o no enteros se rechazan.
- `arrival_sort_key: str = "family_height"`:
  - `family_height`: primero la familia dominante, definida sobre dimensiones
    originales L ∈ [600,610], W ∈ [440,450], H ∈ [345,395] mm (intervalos
    inclusivos); después el resto. En ambos grupos, altura descendente.
  - `height_desc`: altura original descendente.
  - `volume_desc`: L×W×H original descendente (disponible y probado; fuera del sweep).

La secuenciación conserva los horarios de las posiciones de llegada y los
atributos de las cajas; renumera las filas para que el DES respete el orden.
En modo `excel`, las posiciones se toman por (tiempo, fila); en `immediate`,
por fila. Los parámetros se guardan en `run.json` y en el dump. La reconstrucción
de dimensiones originales por ID reproduce la transformación antes de llamar
al validador físico existente.

La política **sigue siendo online: solo ve el buffer de 15 cajas**. Ni W=186
le permite consultar el futuro real. H6 mantiene el prior genérico y todos los
overrides de `h6_generic` en `configs/sweeps/h6_prior_eval.yaml` (H1+H3+H5+H6).
La familia de la clave es conocimiento del secuenciador aguas arriba, no una
modificación del prior de H6.

Se evalúan 12 configuraciones × 10 seeds de referencia sobre las 186 cajas
de `data/Flujo rampas - Editado.xlsx`:

- H6: W=0 como referencia, y W ∈ {15,30,60,186} × {family_height,height_desc}.
- `arm6_4orient` sin técnicas: W ∈ {0,30,186}, clave `family_height` (la clave
  por defecto), para medir también el efecto de secuenciar con la política base.

La familia dominante contiene 85 de las 186 cajas de este Excel. W=15,30,60,186
produce respectivamente 13,7,4,1 bloques (incluyendo el bloque parcial final).
Con W=15 se conserva la pertenencia a cada bloque del shuffle; cambia el orden
en que sus cajas reponen el buffer. Las ventanas mayores también permiten
agrupar cajas procedentes de distintos bloques del shuffle.

Todas las comparaciones del resumen son pareadas contra H6 W=0. Para separar
la contribución de secuenciar de la de las técnicas, se calculan además
arm6 W−arm6 W=0 y H6 W−arm6 W con la misma clave y las mismas seeds.

KPI congelado v1.0.0: utilización de la envolvente efectiva de los palets
cerrados; excluye el último palet END, media por seed, IC95 t de Student.
Se reportan también no colocadas, palets, cortes y validación para contextualizar
el KPI. Se mantienen gate=400 mm, accesibilidad=400 mm, overhang=20 mm,
orientaciones y soporte originales. No hay cambios de métrica ni de validador.
Los IC95 son nominales, sin corrección por comparaciones múltiples; seleccionar
el máximo entre ocho variantes requiere una réplica independiente para confirmar.

## Reproducción

Desde la raíz del repositorio, en `feat/palanca-secuenciacion`:

```bash
export PYTHONPATH=$(pwd)/src
.venv/bin/python -m pytest -q tests/test_arrival_sequencing.py tests/test_benchmark_one_pallet_canonical.py
./scripts/smoke_offline.sh
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/lever_sequencing.yaml --seeds 10 --workers 15 --lock-retry-seconds 60 --name lever_sequencing --baseline h6_generic
```

El runner guarda parámetros, seeds, hashes de código/entrada y resultados por
seed, y permite reanudar sin repetir resultados válidos. Artefactos locales:
`out/sweeps/lever_sequencing/summary.{md,json}` y `manifest.json`.
`out/` no se incorpora al repositorio.

La regresión W=0 contrasta tanto el default como el cero explícito con el KPI
y el hash de todas las colocaciones de un run real de 60 cajas capturado antes
de este cambio (seed=42, arm6). Los tests cubren estabilidad, bloques parciales,
W≥n, los tres criterios, shuffle previo, ambos modos temporales, reconstrucción
de IDs y conservación del buffer y de los parámetros H6.

Además, las diez seeds H6 W=0 del sweep reproducen **exactamente el KPI y
todas las colocaciones** de `out/sweeps/h6_prior_eval/h6_generic` (comparación
del campo `pallets` de los dumps). Evidencia:
`out/sweeps/lever_sequencing/baseline_check.json` (10/10 comprobaciones OK).

## Resultados

Fecha UTC del manifiesto: 2026-10-17T11:04:30.184676+00:00; HEAD `774441c7c94ac5515d36bef145a95335d4c62b1e`;
SHA256 del código `a178a63052a54974d44fceed9d2466ce7bb8d8e3a8e1468a3767de3add790ebb`.

Las doce configuraciones completaron las diez seeds, con **0 violaciones**,
**0 cajas sin colocar** y **0 hard_cap_hits** en conjunto.

| Política | W | Clave | KPI % | IC95 % | Δ vs H6 W=0 (pp) | IC95 Δ (pp) |
|---|---:|---|---:|---|---:|---|
| H1+H3+H5+H6 | 0 | — | 74.030 | [73.056; 75.004] | 0.000 | — |
| H1+H3+H5+H6 | 15 | family_height | 72.797 | [71.881; 73.713] | -1.233 | [-2.593; 0.127] |
| H1+H3+H5+H6 | 15 | height_desc | 73.221 | [72.444; 73.997] | -0.809 | [-1.758; 0.140] |
| H1+H3+H5+H6 | 30 | family_height | 72.285 | [70.945; 73.626] | -1.745 | [-3.472; -0.018] |
| H1+H3+H5+H6 | 30 | height_desc | 72.646 | [71.606; 73.686] | -1.384 | [-2.494; -0.274] |
| H1+H3+H5+H6 | 60 | family_height | 72.537 | [71.253; 73.822] | -1.493 | [-2.803; -0.182] |
| H1+H3+H5+H6 | 60 | height_desc | 73.137 | [72.490; 73.784] | -0.893 | [-2.040; 0.253] |
| H1+H3+H5+H6 | 186 | family_height | 71.701 | [70.725; 72.676] | -2.329 | [-3.585; -1.074] |
| H1+H3+H5+H6 | 186 | height_desc | 72.478 | [71.574; 73.382] | -1.552 | [-2.912; -0.193] |
| arm6 base | 0 | — | 61.890 | [60.948; 62.833] | -12.139 | [-13.409; -10.870] |
| arm6 base | 30 | family_height | 63.117 | [62.411; 63.822] | -10.913 | [-11.845; -9.982] |
| arm6 base | 186 | family_height | 64.525 | [63.994; 65.057] | -9.505 | [-10.712; -8.297] |

Separación de efectos (misma clave `family_height`, diez pares por contraste):

| Contraste | Diferencia pp | IC95 pp |
|---|---:|---|
| arm6_w30_family_height - arm6_w0_family_height | 1.226 | [0.556; 1.896] |
| h6_w30_family_height - arm6_w30_family_height | 9.169 | [7.554; 10.783] |
| arm6_w186_family_height - arm6_w0_family_height | 2.635 | [1.780; 3.490] |
| h6_w186_family_height - arm6_w186_family_height | 7.175 | [6.327; 8.023] |
| h6_generic - arm6_w0_family_height | 12.139 | [10.870; 13.409] |

Para reproducir estos contrastes adicionales:

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from palca.study.metric import paired_comparison
p = Path('out/sweeps/lever_sequencing')
seeds = json.loads((p/'manifest.json').read_text())['seeds']
def rows(name): return [json.loads((p/name/str(s)/'kpi.json').read_text()) for s in seeds]
for w in (30, 186):
    arm, h6 = f'arm6_w{w}_family_height', f'h6_w{w}_family_height'
    for a, b in [('arm6_w0_family_height', arm), (arm, h6)]:
        print(b, '-', a, paired_comparison(rows(a), rows(b)))
print('h6_generic - arm6_w0_family_height', paired_comparison(rows('arm6_w0_family_height'), rows('h6_generic')))
PY
```

Coste observado y palets (media por seed):

| Config | Palets total / cerrados | ms/decisión media / p95 | Wall s/seed | Cortes deadline/seed |
|---|---:|---:|---:|---:|
| h6_generic | 9.700 / 8.700 | 1328.011 / 2524.039 | 260.183 | 0.000 |
| h6_w15_family_height | 9.900 / 8.900 | 1322.045 / 2519.246 | 259.260 | 0.000 |
| h6_w15_height_desc | 10.000 / 9.000 | 1351.398 / 2561.789 | 265.150 | 0.000 |
| h6_w30_family_height | 9.800 / 8.800 | 1275.319 / 2549.716 | 249.974 | 0.000 |
| h6_w30_height_desc | 9.900 / 8.900 | 1351.964 / 2666.644 | 265.119 | 0.000 |
| h6_w60_family_height | 9.900 / 8.900 | 1378.247 / 3127.589 | 270.291 | 0.000 |
| h6_w60_height_desc | 10.000 / 9.000 | 1365.146 / 2889.516 | 267.841 | 0.000 |
| h6_w186_family_height | 10.000 / 9.000 | 1197.680 / 2638.907 | 235.030 | 0.000 |
| h6_w186_height_desc | 10.000 / 9.000 | 1263.116 / 2735.072 | 247.838 | 0.000 |
| arm6_w0_family_height | 11.100 / 10.100 | 72.357 / 150.290 | 14.516 | 0.000 |
| arm6_w30_family_height | 11.000 / 10.000 | 68.481 / 143.568 | 13.760 | 0.000 |
| arm6_w186_family_height | 11.000 / 10.000 | 64.614 / 153.254 | 12.968 | 0.000 |

Los tiempos son observacionales con 15 workers. El resumen íntegro incluye
la robustez D-015 con gaps 5/20/50 mm sin cambiar la regla de accesibilidad.


## Lectura

**Con H1+H3+H5+H6, ninguna de las ocho secuenciaciones ensayadas sube la media
del KPI. Se conserva W=0 (74,030 %).** La mejor variante ordenada es W=15 con
`height_desc`: 73,221 %, diferencia −0,809 pp, IC95 [−1,758; 0,140]. No hay
mejora estadísticamente respaldada; este intervalo tampoco permite afirmar una
degradación en esa variante. Los ocho cambios medios quedan entre −0,809 y
−2,329 pp. Cinco intervalos nominales quedan completamente por debajo de cero.
W=186 no ayuda: `family_height` da −2,329 pp y `height_desc` −1,552 pp.

En la política arm6 base, secuenciar sí aporta: W=30 da **+1,226 pp**
[0,556; 1,896] y W=186 **+2,635 pp** [1,780; 3,490] frente a su propio W=0.
La palanca y las técnicas no son aditivas: H6 supera a arm6 en +12,139 pp sin
secuenciar, +9,169 pp con W=30 y +7,175 pp con W=186, usando `family_height`
en los dos últimos casos. La mejora observada en arm6 no se puede trasladar
como incremento esperado de la mejor política.

La hipótesis operativa de D-019 R4 no queda respaldada con estas dos claves
y este flujo. Ordenar el lote completo por familia/altura no equivale a la
planificación offline de columnas que motivó la hipótesis: aquí la política
sigue online con 15 cajas visibles. El experimento no busca la mejor
permutación posible dentro de cada ventana ni descarta otras secuenciaciones.
No se justifica implantar un secuenciador para mejorar H6 con estas reglas.

## Implicación operativa

Esta es una **palanca operativa**, no una mejora de software bajo el flujo de
llegada actual. Requiere capacidad física o coordinación del WMS para agrupar
y despachar cada ventana en el orden solicitado. W mide el horizonte que debe
gestionar ese sistema; no aumenta el buffer de entrada del robot. W=186 exige
controlar el lote completo y es un escenario extremo de secuenciación.

La simulación mantiene la cadencia y no imputa esperas, espacio adicional,
manipulaciones ni coste del secuenciador. Por tanto, cuantifica la respuesta del
KPI a ese orden de entrega, no la rentabilidad ni el throughput neto del cambio.
El resultado debe interpretarse junto al coste operativo de disponer de W cajas
y al hecho de que END queda fuera del KPI congelado.
