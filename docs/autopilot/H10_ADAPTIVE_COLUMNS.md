# H10: columnas adaptativas con ventana visible

`column_template=adaptive` elige una partición una vez, en la primera decisión
del palet físico, antes de crear copias para el beam. La partición queda en el
palet y sus copias; ni el beam ni los rollouts la recalculan. El default sigue
siendo `off`. `column_slack_mm` es un entero no negativo, por defecto 10.

La única entrada de geometría son las primeras 15 cajas visibles, sus
orientaciones de `PalletModel._orientations` (con el gate vigente), la
envolvente 1240 × 840 y la holgura explícita. No se consulta el catálogo,
upstream, Excel, seeds ni cajas de palets anteriores. Coordenadas físicas:
origen (−20, −20), extremo (1220, 820), como H4.

Se enumeran franjas en ambos ejes. Los tamaños de franja y de corte son
dimensiones de huella visibles más la holgura. Se generan repeticiones de una
dimensión y mezclas de dos dimensiones, hasta cubrir cada eje. Si el sobrante
es al menos la menor dimensión de huella visible, queda como segmento residual;
si es menor, se distribuye por igual entre los segmentos anteriores, con los
milímetros indivisibles asignados por orden. No se dejan separaciones.

La búsqueda es aproximada y tiene presupuestos geométricos fijos: hasta 64
planes por eje, 16 segmentos regulares por plan, 16 planes interiores
muestreados a lo largo de la enumeración y hasta 256 particiones completas.
Los planes interiores se ordenan por su puntuación local; cuatro combinaciones
deterministas permiten cortes distintos en cada franja. Los ejes se intercalan
antes de consumir el presupuesto. Los empates conservan el primer candidato;
ninguna elección depende de un corte de reloj. Una entrada vacía, demasiado
grande o demasiado pequeña para estos presupuestos conserva al menos la
envolvente completa como columna residual.
Los planes de eje idénticos se calculan una sola vez por elección y se
reutilizan entre franjas compatibles; la caché se destruye al terminar esa
elección. No conserva geometría ni observaciones entre palets. La optimización
conservó exactamente 30/30 particiones de ventanas sintéticas diversas.

Puntuación: se enumeran las orientaciones por área descendente, conservando el
orden visible/orientación en empate, y se asigna cada caja a la primera columna
libre donde quepa. Cada caja y columna se usan como máximo una vez. Se suma el
área real de las huellas asignadas y se resta el área de las columnas residuales
en las que no cabe ninguna huella visible, con peso 1. Esta puntuación mide área
de suelo; `column_min_fill` se exige después, en los candidatos de colocación
de H4. Greedy, beam, rollouts y `column_fallback` reutilizan esa ruta de H4.
Con H10, el beam congela la ventana visible también en modo `score`; no lee ni
rellena desde upstream en ninguno de los tres modos de valor. Una prueba con
un upstream que lanza una excepción al iterarlo cubre los tres modos.

Telemetría adicional solo con H10 activa: `columns.slack_mm` y
`columns.open_partitions`, incluyendo rectángulos, eje y anchos de franjas,
índices residuales, cajas visibles, candidatas evaluadas, puntuación/áreas y
tiempo de elección. El tiempo completo por decisión incluye esa elección.

El YAML `configs/sweeps/h10_tune.yaml` contiene la oficial `h6_sin_gate` y seis
variantes: `h10_s{0,10,20}_f{75,85}`, todas con fallback `free`, gate 2400,
prior genérico, presupuesto H6 de 57 nodos y hard cap de 600000 ms. Los nombres
no fijan ninguna dimensión del flujo. La medición de una seed de ajuste es una
comprobación de coste, no una selección de ganadora ni una evaluación D-016.

## Reproducción

Desde este worktree, sin escribir en `out/`:

```bash
. .venv/bin/activate
export PYTHONPATH=$(pwd)/src
python -m pytest tests/test_columns_adaptive.py tests/test_columns_online.py tests/test_benchmark_one_pallet_canonical.py tests/test_run_sweep.py tests/test_h6_perf.py -q -k 'not slow and not d022'
./scripts/smoke_offline.sh
python scripts/check_h10.py --out /tmp/h10-audit --seed 1001
git --no-pager diff --check
```

El script de auditoría ejecuta todo en un único proceso, con un hilo nativo por
biblioteca, sin profiler ni workers. Primero compara directamente los objetos
`dump.json["pallets"]` de las seeds 50021 y 1 con `h6_sin_gate_ref` del worktree
oficial; después mide las seis variantes en la seed 1001 y exige media ≤ 1 s
en la más cara. Verifica también elección < 200 ms, cero cortes, violaciones y
cajas sin colocar. Escribe episodios y `h10_audit.json` bajo el directorio
explícito. Los tests conservan además hashes de ambos objetos oficiales en
`tests/fixtures/h10_official.json`.
`--official-dumps RUTA_50021 RUTA_1` permite comparar directamente dumps ya
generados por los tests de defaults, evitando repetir los dos episodios.

El test automatizado D-022 usa 130 filas, las primeras 100 idénticas y las 30
restantes invertidas. Compara cada decisión completa y la partición durante
86 colocaciones, incluidas aperturas de nuevos palets y rollouts genéricos,
hasta que entra la primera caja cambiada en la ventana de 15. El scheduler
recibe también el upstream real distinto para detectar cualquier lectura con
efecto antes de esa frontera.

Archivos de implementación: `src/palca/scheduler/columns.py`,
`src/palca/scheduler/scheduler_v1.py`,
`src/palca/integration/policy_packer_sched.py`, `src/sim/run.py`.
Propagación de configuración/compatibilidad:
`scripts/benchmark_one_pallet_canonical.py`,
`configs/benchmarks/one_pallet_canonical.json`, `scripts/perf_h6.py`.
Configuración, pruebas y auditoría: `configs/sweeps/h10_tune.yaml`,
`tests/test_columns_adaptive.py`, `tests/fixtures/h10_official.json`,
`scripts/check_h10.py` y este documento.

## Verificación y ejemplos (2026-10-20)

Defaults: igualdad directa de `dump["pallets"]` frente a las referencias
indicadas en las seeds **50021 y 1 (2/2)**, con hard cap de 600000 ms.
Los hashes congelados proceden de los dumps originales de `h6_sin_gate_ref`.
Test D-022: **86 decisiones completas idénticas**, hasta la primera llegada
cambiada, incluidas aperturas de nuevos palets y rollouts genéricos.

`./scripts/smoke_offline.sh`: **OK**, 646 tests recogidos sin fallos y escenario
height-slack OK. Tras añadir la prohibición de lectura de upstream en los tres
modos de valor y la caché local, se ejecutaron de nuevo las **31 pruebas
dirigidas** de H10 sin los dos episodios lentos y el test de 86 pasos. El test
de 86 pasos ya estaba incluido en el smoke y las seis variantes finales
ejercitan la misma ruta H6 con 57 nodos.

Ejemplos de los primeros tres palets físicos de `h10_s0_f75`, seed 1001;
todos los números siguientes son resultados de la ventana de apertura, no
parámetros configurados. Los cortes se expresan desde el origen de la
envolvente (trasladar −20 mm en ambos ejes para las coordenadas físicas):

| Palet | Franjas | Cortes interiores | Columnas (L × W), mm |
|---|---|---|---|
| 1 | X: 638 + 602 | Y: 450 + 390; 395 + 445 | 638×450, 638×390; 602×395, 602×445 |
| 2 | X: 620 + 620 | Y: 450 + 390 en ambas | 2×(620×450), 2×(620×390) |
| 3 | Y: 440 + 400 | X: 635 + 605; 510 + 395 + 335 | 635×440, 605×440; 510×400, 395×400, 335×400 |

Las áreas de huellas asignadas son 938675, 876025 y 1029350 mm²,
respectivamente; en los tres casos la penalización residual es cero.

Auditoría final: un proceso (mismo PID en los seis episodios), un hilo nativo
por biblioteca, seed 1001. Duraciones completas, incluidas decisiones sin
colocación. Medias y p95 por episodio; la apertura de plantilla está incluida.

| Variante | Decisiones | Media, ms | p95, ms | Máximo elección, ms | KPI, % |
|---|---:|---:|---:|---:|---:|
| h10_s0_f75 | 195 | 349.3 | 841.2 | 81.7 | 77.257 |
| h10_s0_f85 | 195 | 382.3 | 1052.7 | 85.7 | 77.833 |
| h10_s10_f75 | 196 | 391.1 | 1068.2 | 74.5 | 73.720 |
| h10_s10_f85 | 196 | **477.6** | 1252.3 | 81.6 | 72.621 |
| h10_s20_f75 | 195 | 419.4 | 1533.8 | 83.1 | 77.639 |
| h10_s20_f85 | 195 | 398.2 | 969.9 | 86.5 | 78.338 |

**D-007 cumplido:** la mayor media es 0.478 s/decisión. Máximo global de
elección: 86.54 ms (< 200). Los seis episodios tienen cero hard_cap_hits,
cortes temporales, violaciones del validador y cajas sin colocar. Estos KPI
de una sola seed no seleccionan ganadora ni demuestran mejora estadística.

La primera auditoría detectó un pico de 208.84 ms en la elección de una
plantilla de `h10_s20_f75`. Tras reutilizar planes de eje idénticos dentro de
cada apertura, se repitieron las seis variantes. Los cinco episodios completos
ya ejecutados conservan **exactamente 5/5 objetos `dump["pallets"]`**, además
de las 30/30 particiones sintéticas. No se redujo la enumeración ni se cambió
la puntuación para cumplir el límite.

Artefactos finales: `/tmp/h10-audit-final/h10_audit.json`, episodios
`/tmp/h10-audit-final/<variante>/1001/`, dumps oficiales reutilizados bajo
`/tmp/h10-audit-final/h6_sin_gate/{50021,1}/dump.json`, log
`/tmp/h10-audit-final.log`. Smoke: `/tmp/h10-smoke.log`. Pruebas dirigidas
finales: `/tmp/h10-targeted-final.log`.

Comando de la auditoría final (reutiliza los dumps ya verificados del smoke):

```bash
python scripts/check_h10.py --out /tmp/h10-audit-final --seed 1001 --official-dumps /tmp/h10-audit/h6_sin_gate/50021/dump.json /tmp/h10-audit/h6_sin_gate/1/dump.json
```

`git --no-pager diff --check`: OK. Sin `git add`, commits ni escrituras en
`out/`. Se conservaron los cambios previos ajenos a H10 en `DECISIONS.md`,
`PROGRESS.md` y `configs/sweeps/h1_retune_h6.yaml`.
