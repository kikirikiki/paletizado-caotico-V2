# Mejoras de estabilidad y controles (palca)

Este documento resume los nuevos mecanismos de estabilidad, balance y controls/plugin,
ademas de los KPIs exportados.

## Nuevos flags (run_sim.py)

- `--stability-mode {off,ratio,ratio+corners,ratio+corners+settle}`
  - `ratio`: exige soporte minimo por superficie.
  - `ratio+corners`: ademas valida las 4 esquinas inferiores.
  - `ratio+corners+settle`: aplica "settle" antes de validar.
- `--min-support <float>`: ratio minimo `area_soportada / area_base`.
- `--stability-eps-mm <float>`: tolerancia numerica para soporte y contactos.
- `--settle-snap-grid`: snap de z a grid (si hay).
- `--grid-mm <int>`: tamano de grid en mm.
- `--heavy-bottom`: penaliza/limita colocar pesado sobre soporte debil.
- `--max-overweight-ratio <float>`: umbral para rechazo por loadbear.
- `--loadbear-penalty-weight <float>`: peso de penalizacion (soft).
- `--loadbear-factor <float>`: factor para capacidad de soporte si no hay columna.
- `--priority-mode none|weight|excel[:colname]`: prioridad de seleccion.
- `--priority-weight <float>`: peso del bonus de prioridad en scheduler.
- `--balance-weight <float>`: peso del balance en el score (0 = solo KPI).
- `--time-budget-ms <int>`: presupuesto de tiempo por decision (ms).
- `--weight-col <col>`: nombre de columna de peso (opcional).

## Controles (arquitectura)

Se introdujo un flujo de "controls/plugins" en `palca.packer`:

- **ManifestControl**: filtra items elegibles.
- **PointControl**: genera candidatos 2D (por defecto MaxRects).
- **PlacementControl**: valida/scorea placement (estabilidad, loadbear, balance).

El comportamiento por defecto se mantiene, pero ahora se puede activar/desactivar
por flags al usar la politica `palca`.

## KPIs nuevos

Los KPIs exportados incluyen:

- `rejected_by_support_ratio_count` / `rejected_by_support_ratio_pct`
- `rejected_by_corner_support_count` / `rejected_by_corner_support_pct`
- `settle_adjustments_count`, `avg_settle_mm`, `max_settle_mm`
- `deadline_cutoffs_count`
- `balance_quadrant_weights` (array de 4)
- `com_offset_mm` (x,y)
- `balance_score`
- `floating_boxes_count`

## Ejemplo de ejecucion

```bash
python -m sim.run \
  --excel "data/Flujo rampas - Editado.xlsx" \
  --model M1 \
  --policy palca \
  --k 3 \
  --stability-mode ratio+corners+settle \
  --min-support 0.75 \
  --heavy-bottom \
  --balance-weight 0.2 \
  --time-budget-ms 120 \
  --out outputs/run_palca.json
```

## Tests

```bash
pytest -q
```
