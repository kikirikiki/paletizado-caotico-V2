# Spec 27 — G3 (D-039, rediseño único): candidatos raíz de la búsqueda oficial y datos para una red de política

Rama `feat/investigacion-larga` (wt2). No commitees. Archivos: `src/palca/scheduler/scheduler_v1.py` (solo telemetría de la
búsqueda micro), `src/palca/learning/episode.py`, `scripts/gen_value_data.py`, `tests/test_policy_data_g3.py`.
Oficial vigente (D-041): `computo_b150_l4` en `configs/sweeps/computo_eval.yaml` (= `h6_sin_gate` con nodos 150 y hojas 4).

## 1. Exponer los candidatos raíz SIN cambiar decisiones
En la búsqueda micro, al terminar una decisión, guarda en `self.last_root_candidates` (no en el dump ni en el KPI) la lista de
**todas las acciones raíz expandidas** (depth 0) con: `buffer_index`, colocación (x, y, z, l, w, h), su **valor de búsqueda**
(el mejor valor de hoja alcanzado bajo esa raíz, con la misma escala que el criterio que elige al ganador; `None` si ninguna
hoja suya se evaluó) y si fue la elegida. Solo telemetría: el dump completo de `h6_sin_gate` y de `computo_b150_l4` debe ser
idéntico (seeds 50021 y 1, `micro_plan_hard_cap_ms: 600000`, normalizando `git_head` y tiempos).

## 2. Datos
- `run_episode(..., policy="official_b150")`: la oficial de D-041 (misma construcción que `official` pero con los overrides del
  miembro `computo_b150_l4`). Para `official` y `official_b150`, por decisión guarda además (padding a K = 64 candidatos):
  `cand_actions` int32 [64, 7] (buffer_index, x, y, z, l, w, h; −1 = vacío), `cand_values` float32 [64] (NaN si sin valor),
  `cand_chosen` int16 (índice), `cand_count` int16, y `cand_height_after` uint16 [64, 62, 42] **solo si** `--with-after`
  (mapa de alturas tras colocar cada candidato, con `encode_state` sobre una copia; puede ser grande: comprímelo).
- `gen_value_data.py --policy official_b150 [--with-after]`: igual que ahora (reanudable, manifiesto), compatibilidad por los
  módulos de generación (ya implementada).
- No uses aguas arriba ni el futuro (D-022/D-032); flujos sintéticos `mix`.

## 3. Tests (`tests/test_policy_data_g3.py`)
- Dumps idénticos con y sin la telemetría (o sea: el cambio no altera decisiones) en un episodio corto (30 decisiones) y la
  comprobación completa 50021/1 preparada (si > 10 min, deja el comando).
- Un episodio `official_b150` con `count=30`: `cand_chosen` apunta a la acción realmente colocada; la elegida tiene el valor
  máximo entre los candidatos con valor (o explica el desempate si no); determinismo (dos ejecuciones iguales).
- D-032: sin apertura de ficheros durante el episodio.

## Entrega
Throughput medido (estados/h/worker) de `official_b150` con y sin `--with-after` en 2 episodios, tamaño medio por episodio en
disco, y el comando de la generación grande (mix, `--seed-start 200000`, salida
`~/code/Paletizador/09_Software/data/g3_v1_b150/`, 15 workers): la lanzo yo.
