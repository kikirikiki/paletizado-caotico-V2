# C. Buffer/lookahead y posiciones de palet abiertas

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
