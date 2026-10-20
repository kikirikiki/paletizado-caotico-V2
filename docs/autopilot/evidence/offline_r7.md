# Spec 19 — Techo offline con R7 (D-026)

Medición del 2026-10-20 en `feat/pozos-acceso`, sin commits. Regla efectiva:
`accessibility_delta_mm=400`, `accessibility_tall_box_exemption=True`,
`accessibility_top_clearance_mm=0`. Una vecina que toca bloquea cuando su
techo supera la base local más `max(400, altura de la caja colocada)`.
El buscador y su replay usan el `AccessibilityConfig` de producción; el
verificador calcula la misma regla de forma independiente.

El mejor testigo encontrado alcanza **81,449472766 %**, con **186 cajas,
9 palets totales y 8 cerrados**. La métrica congelada divide el volumen de
los palets cerrados entre `8 × (1240 × 840 × 2400)`; la utilización global,
incluyendo END, es **74,622606958 %**. END ocupa el **20,007680492 %**.
Es un testigo constructivo alcanzable; el óptimo offline sigue abierto.
El testigo guardado utiliza la exención: la comprobación independiente
contaría **1 bloqueo con la regla antigua** y **0 con R7**. Aunque el KPI
coincide, su secuencia de colocaciones difiere del testigo de la ejecución
antigua. Hay que indicar `--tall-box-exemption` también al verificarlo.

## Comparación y presupuesto

Todas las búsquedas usan columnas contiguas (`--column-gap-mm 0`), plantillas
`0 1 2`, seed 0 y presupuesto de búsqueda de 900 s CPU por ejecución.
Los valores de CPU incluyen arranque y validación (`/usr/bin/time`, usuario
más sistema). Como máximo se ejecutan dos búsquedas simultáneas, con un hilo
BLAS por proceso. Ninguna ejecución usa el fallback por presupuesto.

| Regla | Variante | Ancho | KPI cerrado % | Global % | Palets total/cerrados | CPU s |
|---|---|---:|---:|---:|---:|---:|
| Antigua | Familias originales | 8 | 81,449473 | 74,622607 | 9/8 | 315,18 |
| R7 | Familias originales, **testigo guardado** | 8 | **81,449473** | 74,622607 | 9/8 | 267,59 |
| R7 | `--column-mixed-families` | 8 | 80,528014 | 74,622607 | 9/8 | 517,16 |
| R7 | `--column-mixed-families --group-geometry` | 16 | 80,651929 | 74,622607 | 9/8 | 492,02 |
| R7 | `--group-geometry` | 8 | 80,906069 | 74,622607 | 9/8 | 99,67 |

Total: **1691,62 s CPU (28,19 min)**, inferior al límite de una hora.
La variante mixta permite cajas de pie también en las dos zonas planas.
Mantiene el gate de pie, soporte y todos los controles de producción.
Las mejoras locales en sus primeros palets no mejoran el KPI del flujo completo.
Con estas búsquedas, el techo de referencia queda igual que con la regla
antigua: **81,449473 %**. Frente al baseline congelado de 61,89 %, el gap
permanece en **19,559473 pp**.

## Reproducción del mejor resultado

Desde la raíz del worktree, con una salida nueva:

```bash
. .venv/bin/activate
export PYTHONPATH=$(pwd)/src
r7_out=$(mktemp -d /tmp/palca-r7-repro-XXXXXX)
python scripts/bound_offline.py --method columns --column-gap-mm 0 \
  --templates 0 1 2 --width 8 --cpu-seconds 900 --tall-box-exemption \
  --out "$r7_out/witness.json"
python scripts/bound_offline.py --method columns --tall-box-exemption \
  --validate "$r7_out/witness.json"
python scripts/autopilot/verify_offline_witness.py "$r7_out/witness.json" \
  --tall-box-exemption --top-clearance-mm 0
```

Para las otras filas, añadir los flags de la tabla y cambiar el ancho cuando
corresponda. Para la regla antigua, omitir `--tall-box-exemption`.
Los dos flags nuevos tienen defaults `False` y `0`, respectivamente;
`--top-clearance-mm` solo afecta al umbral si la exención está activada.
La configuración efectiva queda registrada en el resultado y en la identidad
del checkpoint. Cambiar la regla impide reanudar ese checkpoint.

## Verificación y tests

El testigo durable es `offline_witness_r7.json`. Los resultados completos de
verificación, hashes, comandos originales, comparación de inventarios y
resumen de tests se guardan en `offline_r7_summary.json`.
El testigo histórico `offline_witness_columns_80p40.json` sigue verificando
con la regla antigua: rc 0 y cero violaciones geométricas y de accesibilidad.

```bash
python scripts/autopilot/verify_offline_witness.py \
  docs/autopilot/evidence/offline_witness_r7.json --tall-box-exemption
python scripts/bound_offline.py --method columns --tall-box-exemption \
  --validate docs/autopilot/evidence/offline_witness_r7.json
python scripts/autopilot/verify_offline_witness.py \
  docs/autopilot/evidence/offline_witness_columns_80p40.json
python -m pytest -o addopts='' tests/test_offline_r7.py \
  tests/test_offline_search.py tests/test_verify_offline_witness.py -q
```

Los **57 tests dirigidos pasan**. Cubren defaults antiguos, propagación por
los tres métodos y sus CLI, umbrales inclusivos y holgura, aislamiento de los
presets, identidad del checkpoint, gate y replay de columnas mixtas. El caso
pequeño de dos cajas que se tocan tiene una vecina de 500 mm y una caja que
desciende de 600 mm: la regla antigua rechaza y R7 permite la colocación.

El aislamiento final utiliza un `sitecustomize.py` temporal, heredado también
por los intérpretes Python hijos. Cambia únicamente la ruta por defecto del
lock de las herramientas de sweep; los locks explícitos conservan sus rutas.
El aislamiento inicial con un plugin de pytest era insuficiente: el test de
resumen lanzó `summarize_sweep.py` en un hijo que intentó adquirir el lock
global. Se detuvo ese intento y se corrigió el aislamiento. No se borró ni
reemplazó `/tmp/palca_sweep.lock`.

```bash
r7_smoke=$(mktemp -d /tmp/palca-r7-smoke-XXXXXX)
cat > "$r7_smoke/sitecustomize.py" <<'PY'
import os
from pathlib import Path

if os.environ.get('PALCA_SPEC19_PRIVATE_LOCK'):
    from scripts import run_sweep as sweep
    original_sweep_lock = sweep.sweep_lock
    def private_lock(path=Path(os.environ['PALCA_SPEC19_PRIVATE_LOCK']), retry_seconds=0):
        return original_sweep_lock(path, retry_seconds)
    sweep.sweep_lock = private_lock
PY
PYTHONPATH="$r7_smoke:$(pwd):$PYTHONPATH" \
  PALCA_SPEC19_PRIVATE_LOCK="$r7_smoke/sweep.lock" \
  OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
  ./scripts/smoke_offline.sh
git --no-pager diff --check
```

El intento de smoke completo detectó un fallo en
`tests/test_h6_perf.py::test_official_h6_complete_dump_and_kpi[1]`:
la ejecución produjo 11 `hard_cap_hits` (fixture: 0), KPI 0,770574973048275
(fixture: 0,7659301132772497), con 0 violaciones físicas y 832,89 s de pared.
La comparación completa del dump también falla. Se interrumpió el segundo
episodio H6 para contener el consumo y se ejecutó después el smoke con
`PYTEST_ADDOPTS='-m "not slow"'`. Este segundo intento mostró un fallo en
la regresión del baseline y se detuvo al detectar el subproceso sin aislar.
El último intento usa el aislamiento heredado y
`PYTEST_ADDOPTS='-m "not slow" -k "not test_n1_k15_exact_official_baseline"'`.
Los resultados de cada intento y el detalle de los fallos están registrados
en el resumen JSON.
Los archivos protegidos de la spec 18 permanecen sin editar por esta tarea.

Resultado del último intento acotado: **563 tests pasan, 0 fallos y 0 errores**,
seguido del escenario height-slack **OK**. Se excluyen explícitamente los
tests marcados `slow` y las diez regresiones temporizadas del baseline.
El caso que había fallado en el intento anterior era
`test_n1_k15_exact_official_baseline[42]`; su único reintento aislado pasa
en **11,44 s**. El estado del smoke completo sigue siendo **no verde** por
el fallo H6 observado. `git --no-pager diff --check`: **OK**.
