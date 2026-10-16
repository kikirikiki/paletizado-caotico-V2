# Métrica volumétrica congelada — versión 1.0.0

La única implementación autorizada para experimentos, sweeps y cotas del estudio
es `palca.study.metric`. No modificarla sin PR propio y revisión adversarial.
El validador independiente es `palca.study.validate`; no llama a la geometría ni
a las decisiones del packer. Las constantes físicas se obtienen exclusivamente
de `palca.study.params`.

## Definición exacta

Sean `C` los palets físicos cerrados y `v_i = length_mm × width_mm × height_mm`
el volumen **real** de cada caja colocada. Para cada palet `p`, `V_p = Σ_i v_i`
y `z_p = max_i(z_mm + height_mm)`. El KPI oficial de una ejecución es:

```text
ENVELOPE_MM = pallet_envelope_mm(20) = (1240, 840, 2400) mm
V_env = prod(ENVELOPE_MM) = 2 499 840 000 mm³
KPI = Σ_{p ∈ C} V_p / (|C| × V_env)
```

Se informa como ratio; multiplicar por 100 para porcentaje. No se recorta a uno:
un resultado imposible debe poder ser detectado. La envolvente es constante
aunque un experimento cambie `overhang_mm`: cambiar el denominador junto con la
política impediría comparar los resultados. El nominal es
`NOMINAL_MM = (*PALLET_NOMINAL_MM, MAX_LOAD_HEIGHT_MM)` (1200×800×2400 mm).

Un **palet cerrado** tiene `closure_reason != "END"`. Incluye los cierres por
conteo, altura, fragmentación o deadlock, según el motivo registrado por el DES.
`END` identifica el remanente final de cada destino y se excluye del KPI,
independientemente de su llenado. Si el último palet alcanzó un cierre normal,
cuenta como cerrado y no se inventa un END vacío. Si no hay cerrados, `kpi`,
`kpi_nominal`, `eff_real` y `h_fill` son `null`; no equivalen a cero.

`split_pallets` usa `continuous_pallet_sequence[dest]` para cortar las
colocaciones acumuladas del dump y `continuous_closure_sequence[dest]` para
asignar motivos **en orden**. Devuelve destinos normalizados a texto e índices
físicos desde 1 dentro de cada destino. Rechaza conteos que no suman el número
de items, motivos ausentes/desalineados y END fuera de la última posición.
Los conteos agregados de `continuous_closures_by_reason` no permiten reconstruir
el orden: un payload anterior sin la nueva telemetría requiere repetir el run.

`run_kpi` devuelve:

| Campo | Población y fórmula |
|---|---|
| `kpi` | KPI oficial; palets cerrados |
| `kpi_nominal` | Mismo numerador/cerrados, denominador nominal |
| `eff_real` | Media por palet cerrado de `V_p / (ENVELOPE_MM[0] × ENVELOPE_MM[1] × z_p)` |
| `h_fill` | Media por palet cerrado de `z_p / MAX_LOAD_HEIGHT_MM` |
| `pallet_utils` | Lista de `pallet_utilization(p) = V_p / V_env`, **todos** los palets, en orden de los registros |
| `n_pallets_total`, `boxes_placed` | Totales, incluyendo END |
| `n_closed` | Número de palets con motivo distinto de END |
| `kpi_all_pallets` | `Σ_p V_p / (n_pallets_total × V_env)`, solo informativo; `null` sin palets |
| `metric_version` | `1.0.0` |

La deuda `metrics.pallet_kpis.pallet_volume_utilization` del DES incluye palets
incompletos y **NO es el KPI oficial**. Su semántica se conserva para los scripts
existentes. Ningún experimento posterior debe usarla como sustituto.

## Seeds, incertidumbre y comparación

La unidad estadística es **una seed**, no una caja ni un palet. Cada seed aporta
un único `run_kpi(...)["kpi"]`; `aggregate_seeds` calcula su media sin ponderarla
por el número de palets, desviación estándar muestral (`ddof=1`), `n`, mínimo,
máximo e IC95 % bilateral:

```text
sd = sqrt(Σ_s (KPI_s - media)² / (n - 1))
IC95 = [media - t_(0.975,n-1) × sd/√n, media + t_(0.975,n-1) × sd/√n]
```

La tabla incorporada contiene df=1..120, con nueve decimales, sin scipy;
por encima se usa 1,96. Los IC no se recortan a [0,1]. Una seed permite informar
la media pero no estimar `sd` ni IC: ambos son `null`. Una lista vacía o un KPI
indefinido/no finito produce error; no se descartan seeds silenciosamente.
Las entradas pueden ser los dicts de `run_kpi` o una lista de sus ratios.

`paired_comparison(A, B)` requiere las mismas seeds en el mismo orden, garantizado
por el llamante. Para cada seed calcula `d_s = 100 × (B_s - A_s)`, en **puntos
porcentuales**, y aplica el mismo IC t a las diferencias. Devuelve
`mean_diff_pp`, `sd_pp`, `ci95_pp`, `n` y `significant`. Es significativo si el
IC **no contiene 0**; tocar 0 no basta. Con una sola pareja no se puede afirmar
significación. Diferencias positivas favorecen B. Es un contraste pareado
bilateral al 5 %, sin corrección por comparaciones múltiples.

Las cifras históricas **64,6 % / 72,1 %** eran `media_cajas × volumen_supuesto`
dividido por un volumen de referencia; no sumaban el volumen real de las cajas
colocadas. Tampoco debe recuperarse el denominador histórico 1240×820×2400,
incompatible con overhang simétrico de 20 mm. Esas cifras no son comparables
directamente al KPI congelado: hay que volver a ejecutar y medir.

## Validación física e identidad de las cajas

`validate_pallet(items, original_dims_by_box_id, min_support=0.85)` comprueba:

- Contención en x=[−20,1220], y=[−20,820], z=[0,2400] mm. Las cotas se derivan
  de params; tolerancia geométrica de 0,5 mm.
- Ausencia de solapes 3D: se considera solape si la penetración supera 0,5 mm
  en **los tres ejes**. El contacto entre caras se permite.
- Dimensiones positivas/finitas, permutación exacta de las L/W/H de entrada,
  identidad conocida/no duplicada dentro del palet y cara admitida para arm6.
  H×L (vertical W original) está prohibida según `orientation_faces`.
- Si las dimensiones empatan, usa `orientation_name` (orden de ejes originales)
  o `orientation_family` (`planar` → L×W, `stand_hw` → H×W) para desambiguar.
  Comprueba su coherencia con las dimensiones y entre ambos metadatos; si no hay
  metadatos suficientes, informa una violación, sin aplicar el rechazo
  conservador por empate de `resting_face`. Los metadatos siguen siendo una
  declaración del simulador: cajas con dimensiones iguales no revelan su cara
  física únicamente por geometría.
- Soporte ≥ `min_support`: unión de intersecciones de la base con caras
  superiores a z±1 mm, dividida por el área de la base. No suma áreas duplicadas.
  Con |z|≤0,5 mm la base está en el suelo. El CLI usa el `min_support` del run.

Valida el estado final, sin comprobar accesibilidad, trayectoria, resistencia
de carga ni estabilidad dinámica. También valida los palets END.

El mapa original **no** se obtiene de las dimensiones del dump. El CLI lee el
Excel mediante `sim.io.load_arrivals`, conserva sus `row_idx`, aplica el shuffle
de `episode_seed`/ventana/intensidad y la renumeración que hace `run_simulation`,
y ordena como el DES: por `row_idx` en `immediate` o `(time,row_idx)` en `excel`.
Entonces enumera `box_id` desde 1. `time_scale` positivo y `force_destination`
no cambian esta identidad. Los tests contrastan las permutaciones del dump
contra esas dimensiones de entrada.

El payload actual no guarda `excel_path` ni identifica el loader multi-rampa:
el CLI admite el loader Excel estándar y toma el canónico por defecto. Para otra
entrada es obligatorio pasar su `--excel`. No usar esta reconstrucción para
`multi_ramp`; en ese caso el llamante debe suministrar al validador el mapa de
entrada correspondiente. Run y dump deben corresponder a la misma ejecución;
el CLI contrasta sus `params` cuando el dump los incluye.

## Reproducción de la línea base (una seed)

Desde la raíz, sin modificar el Excel; los artefactos se escriben en `/tmp`:

```bash
export PYTHONPATH="$PWD/src"
.venv/bin/python - <<'PY'
from palca.study.params import preset_params
from sim.run import run_simulation
run_simulation(**preset_params(
    "arm6_4orient", episode_seed=50021,
    out_path="/tmp/palca-metric-50021-run.json",
    dump_placements_path="/tmp/palca-metric-50021-dump.json",
))
PY
.venv/bin/python scripts/kpi_from_run.py \
  --run /tmp/palca-metric-50021-run.json \
  --dump /tmp/palca-metric-50021-dump.json --validate
.venv/bin/python -m pytest -q tests/test_study_metric.py tests/test_study_validate.py
./scripts/smoke_offline.sh
```

Este comando usa el Excel canónico de params y el presupuesto de tiempo oficial,
sin overrides de velocidad. El test de integración usa `Flujo_smoke_60.xlsx`,
seed 50021 y presupuesto de 30 ms para ser rápido. Si encuentra violaciones reales,
se marca `xfail` con los detalles y se reporta; nunca se relaja el validador.
