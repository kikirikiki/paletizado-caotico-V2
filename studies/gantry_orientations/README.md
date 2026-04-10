# Estudio: impacto de orientaciones adicionales en gantry

## Hipotesis
Permitir `stand_hl` (6 orientaciones totales: planar x2 + stand_hw x2 + stand_hl x2)
mejora la utilizacion volumetrica del palet respecto a `stand_hw` (4 orientaciones)
en un escenario de gantry cartesiano (accessibility_delta_mm=0).

## Motivacion
El brazo robotico de produccion opera con `accessibility_delta_mm=400`, que restringe
la colocacion en zonas de la ultima capa. Un gantry cartesiano no tiene esta restriccion
y puede acceder a toda la superficie del palet, lo que hace relevante explorar mas
orientaciones de caja.

## Configuraciones comparadas

| Config | orientation_mode | accessibility_delta_mm | Descripcion |
|---|---|---|---|
| brazo_hw | planar+stand_hw | 400 | Brazo actual — baseline de produccion |
| gantry_planar | planar | 0 | Gantry sin rotaciones verticales — control |
| gantry_hw | planar+stand_hw | 0 | Gantry con 4 orientaciones |
| gantry_all | planar+stand_hw+stand_hl | 0 | Gantry con 6 orientaciones |

## Parametros fijos
Ver `configs/base_params.json`. Configuracion de produccion validada:
`min_support=0.85`, `stand_hw_height_margin_gate_mm=400`,
`stacking_mode=heightfield`, `micro_plan=False`.

## Metodologia
- 10 seeds por configuracion
- Ultimo palet de cada seed excluido (incompleto por agotamiento del flujo)
- Metrica principal: `vol_util%` = cajas_media * vol_medio_caja / vol_palet * 100
- vol_palet = 1240 x 820 x 2400 mm
- vol_medio_caja = 0.77 * (605x445x355) + 0.23 * 78e6 mm3

## Advertencia sobre stand_hl
La caja dominante (605x445x355) en stand_hl tiene h=605mm.
Con palet de 2400mm max solo caben 3 capas completas (3x605=1815mm) frente a
las 4-5 capas en planar. La ganancia en densidad por capa puede no compensar
la perdida de capas. El benchmark mide si esto ocurre o no.

## Como ejecutar

```bash
cd <repo_root>
export PYTHONPATH=$(pwd)/src
python studies/gantry_orientations/benchmark.py
```

## Resultados

| Config | media | min | max | palets | vol_util% |
|---|---|---|---|---|---|
| brazo_hw      | 17.2 | 8  | 25 | 101 | 64.6% |
| gantry_planar | 14.0 | 8  | 23 | 129 | 52.6% |
| gantry_hw     | 18.6 | 13 | 25 |  95 | 69.6% |
| gantry_all    | 19.2 | 13 | 25 |  90 | 72.1% |

## Conclusion

stand_hl confirma la hipotesis con una ganancia modesta: +0.6 cajas/palet (+3.2%)
sobre gantry_hw, pasando de 69.6% a 72.1% de utilizacion volumetrica.
El mismo flujo se empaqueta en 90 palets en lugar de 95.

El gate de 400mm funciona correctamente: stand_hl solo entra en la fase final
del palet, evitando que la altura de 605mm de la caja dominante agote capas
prematuramente.

El techo fisico no mejora (max=25 en ambos casos), la ganancia es en la media.

Recomendacion: stand_hl es beneficioso en escenario gantry. La feature
(PR #139) puede mergearse a feat/fifo-scorer-coherencia cuando se valide
fisicamente con ROBOGUIDE.
