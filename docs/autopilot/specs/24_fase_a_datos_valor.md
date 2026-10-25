# Spec 24 — Fase A (D-039): generador masivo de estados sintéticos para la red de valor

Rama `feat/investigacion-larga` (wt2). No commitees (D-008). Lee solo los archivos citados.
Contexto mínimo: RESEARCH §4 (G1, G1b) y D-039. R13 (`src/palca/scoring/synthetic_value_data.py`, `learned_value.py`) es el
antecedente: reutiliza su estilo (sin E/S de ficheros dentro del episodio, semillas SHA256 de dominio propio).

## Objetivo
Un paquete nuevo `src/palca/learning/` (sin torch: solo NumPy, para que corra en los 15 workers de CPU) y un script
`scripts/gen_value_data.py` que generan episodios sintéticos completos y guardan, para cada decisión, la codificación del
estado, la acción tomada y la etiqueta. **No se toca nada de `src/palca/scheduler/`, `src/palca/packer/`, `src/sim/` ni los
defaults**: la política oficial debe dar decisiones idénticas a las de los sweeps.

## 1. `src/palca/learning/synthetic_flows.py`
- `synthetic_flow(seed: int, prior: str, count: int) -> list[Box]`, semilla `SHA256("palca-g1-synthetic-v1:<prior>:<seed>")`.
- `prior="generic"`: idéntico a `sample_catalog(..., prior="generic")` de H6 (300–500 × 350–750 × 250–500 mm, enteras uniformes).
- `prior="families"` (prior jerárquico, fijado a priori en esta spec, sin mirar ningún flujo real): nº de familias K uniforme en
  {1, …, 12}; medidas de cada familia con los MISMOS rangos que el genérico; pesos de familia ~ Dirichlet(0,5); cada caja elige
  familia según esos pesos y hereda sus medidas exactas. Solo añade estructura de repetición, no rangos nuevos.
- `prior="mix"`: la paridad de `seed` decide generic (par) o families (impar).
- `count` por defecto 186. Destino único (1), como R13.

## 2. `src/palca/learning/state_encoding.py`
`encode_state(pallet, visible_boxes) -> dict[str, np.ndarray]`, versión `ENCODING_VERSION = "g1-v1"`:
- `height`: `uint16 [62, 42]`, altura máxima (mm) por celda de 20 × 20 mm sobre la envolvente 1240 × 840 (incluye el vuelo de
  20 mm por lado; deduce el origen de coordenadas de `pallet_model`, no lo supongas). Rasteriza las colocaciones del palet.
- `boxes`: `float32 [15, 4]`: (largo, ancho, alto ordenados de mayor a menor en m, presente=1), en el orden de la cinta, relleno a 0.
- `scalars`: `float32 [4]`: volumen colocado / envolvente, nº de cajas / 100, altura máxima / 2400, nº de visibles / 15.
Solo lee las colocaciones del palet y las cajas visibles que recibe (nada de aguas arriba).

## 3. `src/palca/learning/episode.py` + `scripts/gen_value_data.py`
- `run_episode(seed, prior, policy, epsilon=0.0) -> dict`: reproduce el bucle de la cinta de 15 visibles (FIFO, sustitución del palet
  cuando no cabe nada, como `generate_episode` de R13), con dos políticas:
  - `policy="official"`: exactamente `h6_sin_gate` (preset `arm6_4orient` + overrides de `configs/sweeps/pulmon_tune.yaml`, miembro
    `h6_sin_gate`, con `micro_plan_hard_cap_ms: 600000`). Constrúyela por el mismo camino que usa `scripts/run_sweep.py` /
    `src/sim/run.py` para que las decisiones sean las de los sweeps.
  - `policy="greedy"`: la de R13 (`training_policy()`), para volumen barato.
  - `epsilon` > 0: con esa probabilidad (generador sembrado por la semilla del episodio) se toma, en lugar del plan elegido, otro de
    los 3 mejores candidatos raíz si la búsqueda los expone; si no los expone, documenta por qué y deja `epsilon` solo para greedy.
- Por decisión guarda: `encode_state(...)` ANTES de colocar, índice de la cinta y colocación elegida (x, y, z, l, w, h), y si la
  búsqueda los expone sin cambiar el código del scheduler, los candidatos raíz con su valor (para G3; si no, omítelo y dilo).
- Etiquetas por palet CERRADO: `util_final` (volumen / envolvente, mismo cálculo que el KPI congelado), `cajas_hasta_cierre` y
  `pallet_id`. Los estados del palet abierto al acabar el flujo se descartan (censurados), como R13.
- El script: `--policy {official,greedy} --prior {generic,families,mix} --seed-start N --episodes M --workers 15
  --epsilon 0 --out DIR`. Un `.npz` comprimido por episodio (`ep_<prior>_<seed>.npz`), reanudable (salta los existentes),
  `manifest.json` con versión de codificación, SHA256 de los fuentes de `src/palca/learning/`, git HEAD, argumentos, y al final
  totales (episodios, estados, palets cerrados, censurados, segundos, estados/hora). `--out` debe estar fuera del repo
  (rechaza rutas dentro del worktree). `OMP_NUM_THREADS=1` en cada worker.

## 4. Tests (`tests/test_value_data_g1.py`, solo este archivo)
- Determinismo: el mismo episodio dos veces da arrays idénticos (greedy, 186 cajas; y official con `count=30`).
- D-032: un episodio greedy completo y uno official con `count=30` con `builtins.open`/`io.open`/`os.open` bloqueados no abren
  ningún fichero, y `synthetic_flows` no importa cargadores de Excel/dumps.
- Codificación: una caja colocada en el origen marca exactamente sus celdas con su altura; palet vacío = ceros.
- `prior="families"`: con K=1 todas las cajas son iguales; las medidas siempre dentro de los rangos del genérico.
- Etiquetas en [0, 1]; los estados censurados no aparecen.
- Equivalencia: `git diff --stat` vacío en `src/palca/scheduler src/palca/packer src/sim` (compruébalo y dilo en la entrega).

## 5. Comprobación de throughput (sin lanzar la generación grande)
Ejecuta `--policy greedy --prior mix --episodes 4 --workers 4` y `--policy official --prior mix --episodes 2 --workers 2` en
`/tmp/g1_check/` y da estados/hora por worker de cada política. Deja en la entrega el comando exacto para la generación grande
(official, mix, 6000 episodios, 15 workers, salida `~/code/Paletizador/09_Software/data/g1_v1/`): la lanzo yo.
