# Spec 20: H4 sobre H6 sin gate

H4 queda desactivada por defecto (`column_template=off`, fill 0.85,
fallback `free`). Se conservan H1/H3/H5/H6, laterales libres, R7, H8 y D-028.
El beam enumera las acciones de columna por techo/fill/score H1. En modo
`score` conserva el ranking acumulado H4; con `potential`/`rollout` conserva
la valoración H5/H6, incluida la continuación sintética H6. Los rollouts
usan la misma plantilla y copian el ancho fijado por palet, incluso en `auto`.

Las llamadas de columna no leen ni escriben la caché heightfield: su altura
se calcula sobre toda la columna y no sobre la huella de la caja. Las llamadas
libres y el fallback conservan la caché original. La caché del heightfield H1
sigue siendo local al escaneo y al palet sin modificar; no se comparte entre
nodos. D-015 usa el módulo actual `study.accessibility_robustness`, con la
regla de accesibilidad del run y su exención de cajas altas.

`configs/sweeps/h4_h6_tune.yaml` incluye `h6_sin_gate` y cinco variantes:
`w450/f85` (ganadora histórica H4), `w445/f85`, `w455/f85`, `auto/f85` y
`w450/f75`. Todas son `official`, gate 2400, hard cap 600000 ms, fallback
`free` y el mismo presupuesto H6 de 57 nodos. La ganadora histórica es el
punto de partida; esta integración no vuelve a seleccionar una ganadora
estadística sobre H6.

Reproducción desde la raíz:

```bash
. .venv/bin/activate
export PYTHONPATH=$(pwd)/src
python -m pytest -q tests/test_columns_online.py tests/test_run_sweep.py tests/test_spec18.py
./scripts/smoke_offline.sh

# Equivalencia: solo el miembro oficial del nuevo YAML, dos seeds en serie.
python scripts/perf_h6.py --configs configs/sweeps/h4_h6_tune.yaml \
  --config h6_sin_gate --seed-list 50021,1 --out out/perf/spec20_defaults
python - <<'PY'
import json
from pathlib import Path
actual = Path('out/perf/spec20_defaults/h6_sin_gate')
reference = Path('/home/ingen/code/Paletizador/09_Software/paletizado-autopilot/out/sweeps/h6_sin_gate_ref/h6_sin_gate')
for seed in (50021, 1):
    read = lambda root: json.loads((root / str(seed) / 'dump.json').read_text())['pallets']
    assert read(actual) == read(reference), seed
    print(seed, 'idéntica')
PY

# Tiempo completo por decisión, un proceso y dos seeds, sin cProfile.
python scripts/perf_h6.py --configs configs/sweeps/h4_h6_tune.yaml \
  --config h4_h6_w450_f85 --seed-list 50021,1 --out out/perf/spec20_h4_h6

# Ajuste posterior (no ejecutado como parte de la integración).
python scripts/run_sweep.py --configs configs/sweeps/h4_h6_tune.yaml \
  --seed-list 1001,1002,1003,1004,1005,1006,1007,1008,1009,1010 \
  --workers 2 --name h4_h6_tune --baseline h6_sin_gate --lock-retry-seconds 10
```

La comprobación de integración usa las dos seeds en paralelo mediante
`run_sweep.py`, con los mismos overrides del miembro `h6_sin_gate` de este
YAML, guardados en `out/sweeps/spec20_equivalence/official.yaml`.
Los tiempos se miden después con un solo proceso. Los artefactos de
verificación y tiempos quedan bajo `out/`, sin versionar.

Verificación de defaults: **2/2 idénticas**, seeds 50021 y 1, comparando
`dump.json["pallets"]` completo y sin normalizaciones. Cero violaciones,
hard cap hits y cortes temporales en ambas. Evidencia:
`out/sweeps/spec20_equivalence/equivalence.json` y `summary.md`.

Tiempo H4 `w450/f85`: seeds 50021 y 1, un proceso, 391 decisiones completas
(incluidas las decisiones sin colocación), sin cProfile. Media agrupada:
**691.120 ms/decisión** (p95 agrupado 1918.677 ms), dentro de D-007 (media <= 1000 ms). Medias por seed:
682.816 y 699.466 ms. Cero violaciones, cajas sin colocar, hard cap hits y
cortes temporales. Evidencia: `out/perf/spec20_h4_h6/timings.json`,
`summary.json` y los `kpi.json` de cada seed.

Pruebas específicas: **108/108 OK** (`test_columns_online.py`,
`test_run_sweep.py`, `test_spec18.py`, incluidas las lentas). Además,
**103/103 pruebas rápidas OK** de caché heightfield, laterales libres,
H5/H6, microplanner, perf H6 y benchmark canónico. Nuevas regresiones:
aislamiento entre previews de columna/libres, H4 con rollouts H6 y ancho
`auto` copiado, equivalencia legacy que admite solo defaults H4 apagados,
y validación de todas las variantes con H6/D-028.

Archivos editados al resolver e integrar: `scripts/run_sweep.py`,
`src/palca/integration/policy_packer_sched.py`,
`src/palca/packer/pallet_model.py`, `src/palca/scheduler/scheduler_v1.py`,
`src/sim/run.py`, `tests/test_run_sweep.py`, `scripts/perf_h6.py`,
`tests/test_columns_online.py`, `tests/test_h6_perf.py`,
`configs/sweeps/h4_h6_tune.yaml` y este documento. Se conservan además los
archivos H4 ya incorporados por el merge. No se ejecuta `git add`, commit,
push ni ninguna escritura en `.git`: el índice seguirá mostrando `UU`
hasta que el director registre la resolución.

Archivos H4 incorporados automáticamente y conservados:
`configs/benchmarks/one_pallet_canonical.json`, `configs/sweeps/h4_eval.yaml`,
`configs/sweeps/h4_tune.yaml`, `configs/sweeps/h4_tune_edge.yaml`,
`docs/autopilot/H4_COLUMNS.md`, `scripts/benchmark_one_pallet_canonical.py` y
`src/palca/scheduler/columns.py`.

Smoke final: **615/615 OK**, escenario height-slack **OK**.
`git diff --check` y búsqueda de marcadores de conflicto: **OK**.
Logs y resumen de validación: `out/spec20_validation/`.
