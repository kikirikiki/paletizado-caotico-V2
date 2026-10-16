# Sweeps del estudio

Desde la raíz del repositorio, con las dependencias de `requirements.txt` instaladas:

```bash
export PYTHONPATH="$PWD/src"
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/baseline.yaml --seeds 10 --workers 15 --name baseline_v1 --baseline arm6_4orient
.venv/bin/python scripts/summarize_sweep.py out/sweeps/baseline_v1
```

El YAML es una lista de `{name, preset, overrides, role}`. Los roles admitidos son
`official`, `contrast`, `control` y `bound`. Se validan presets y overrides con
`preset_params`; nombres de config y sweep no pueden contener rutas. El runner
reserva `episode_seed` y las rutas de salida. Requiere `policy=palca`, palets
continuos y el loader Excel estándar para reconstruir las identidades originales.

`--seeds N` toma las primeras N `REFERENCE_SEEDS`. A partir del índice 10 se
calculan seeds con los primeros 4 bytes big-endian del SHA256 de
`palca-sweep-seed-v1:<índice>`, omitiendo colisiones. `--seed-list 1,42,777`
selecciona seeds explícitas, únicas y en [0, 2**32). Ambas opciones son excluyentes.

Se fija el límite de hilos BLAS/OMP a 1 antes del pool y se usa `spawn`. En
Python >=3.11 cada tarea usa un proceso nuevo; en Python 3.10 el pool reutiliza
procesos, reseeding Python/NumPy en cada tarea. Cada tarea instancia el simulador
con una copia de parámetros y una seed fija. Un `flock` exclusivo no bloqueante en
`/tmp/palca_sweep.lock` impide ejecutar dos sweeps. El archivo del lock se conserva
para evitar carreras de inode; solo el descriptor mantiene el bloqueo.

Cada carpeta config/seed contiene `run.json`, `dump.json` y `kpi.json`. Este último
se publica atómicamente tras calcular el KPI congelado y validar cada palet físico
(incluido END), usando dimensiones de entrada. Incluye las violaciones completas,
la lista de duraciones y sus percentiles, wall time y cajas no colocadas
(entradas Excel menos colocadas: 186 en referencia, 60 en smoke).

Repetir el comando reanuda las tareas cuyos tres archivos existen y cuyo hash
coincide. El hash incluye parámetros efectivos y defaults de la API, seed, HEAD,
contenido local de `src/**/*.py`, `scripts/**/*.py`, manifiestos de dependencias
y contenido del Excel. Así no se reutilizan resultados de código sin commit
modificado. `manifest.json` enumera las tareas esperadas; el resumidor rechaza
resultados ausentes u obsoletos y no agrega carpetas antiguas ajenas al manifiesto.

El resumen usa media por seed, sd muestral e IC95 t de Student de la métrica
congelada. Las comparaciones son config menos baseline, pareadas por seed. Si una
seed no tiene palets cerrados, su KPI es indefinido y se comunica la exclusión y el
número de pares. Los contadores y tiempos también tienen estadísticos en JSON;
el Markdown muestra sus medias. El p95 mostrado es la media de los p95 de las
seeds. Las violaciones se conservan y se cuentan, sin alterar el validador.

Las decisiones con deadline conservan los límites del preset: una seed fija no
elimina la dependencia respecto a carga de CPU. `deadline_cutoffs_count` permite
identificar esta limitación; los tiempos son mediciones observadas, no resultados
deterministas. No ejecutar dos sweeps de 15 workers simultáneamente, aunque otro
runner no use este lock.
