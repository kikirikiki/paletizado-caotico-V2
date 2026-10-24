# Spec 28 — G3 (D-039): red de política y poda en la búsqueda oficial

Rama `feat/investigacion-larga` (wt2). No commitees. Archivos: `src/palca/learning/policy_net.py` (nuevo),
`scripts/train_policy_g3.py` (nuevo), `src/palca/scheduler/scheduler_v1.py` + `src/palca/integration/policy_packer_sched.py` +
`src/sim/run.py` (parámetros, como spec 26), `scripts/benchmark_one_pallet_canonical.py` (excluir parámetros nuevos),
`tests/test_policy_net_g3.py`, `configs/sweeps/g3_tune.yaml`. Reutiliza el estilo de `value_net.py` / `train_value_g1.py`
(checkpoint con SHA, caché, guardas D-032, partición fija por episodio, `--fast`, progreso por epoch con flush).

## Datos (spec 27)
Episodios `official_b150` con `cand_actions` [64,7], `cand_values` [64], `cand_chosen`, `cand_count`, `cand_height_after`
[64,62,42] y la codificación g1 del estado (`height`, `boxes`, `scalars`).

## 1. Red `PolicyNetG3`
Puntúa cada candidato: entrada por candidato = mapa antes (62×42), mapa después de colocarlo, diferencia, y un vector de la
acción (x, y, z, l, w, h normalizados, índice en la cinta / 15) + resumen DeepSets de las cajas visibles que QUEDAN tras
cogerlo + escalares. Tronco CNN compartido sobre (antes, después, diferencia) → logit escalar por candidato; softmax sobre los
candidatos válidos. ≤ 300 k parámetros. Inferencia de todos los candidatos de un nodo en una llamada.

## 2. Entrenamiento (`train_policy_g3.py`)
Pérdida = entropía cruzada hacia un objetivo suave: softmax(cand_values normalizados / τ) sobre los candidatos con valor
(los NaN fuera; τ por defecto 0,5 pp de utilización, ajustable) + término duro hacia `cand_chosen` (peso 0,5). Métricas en
validación/test: top-1 (acierta la elegida), top-k recall de la elegida para k ∈ {2, 4, 6, 8}, y el "regret" medio en pp
(valor de la elegida − valor de la mejor según la red). `--fast` como en G1.

## 3. Poda en la búsqueda
Parámetros nuevos (defaults = desactivado, todo idéntico): `policy_prune_model_path` (str, vacío = off),
`policy_prune_k` (int), `policy_prune_device` (cpu|cuda), `policy_prune_depths` ("root" | "all").
Con la poda activa, en cada nodo expandido (raíz o todos, según `policy_prune_depths`) se generan los candidatos como hoy,
se puntúan en UNA llamada con la red y se conservan solo los `k` mejores por la red (desempate determinista por el orden
actual) antes del ranking/beam existente. Entradas de la red = solo lo observable en el nodo (D-022). Telemetría: ms de red
por decisión. Carga y caché como `cached_value_net`.

## 4. Tests (`tests/test_policy_net_g3.py`)
Formas; invariancia al orden de las cajas visibles; lote 1 == fila del lote N; determinismo del entrenamiento sin `--fast`
(2 episodios pequeños); defaults: dump completo idéntico 50021/1 (si > 10 min, comando preparado); con poda k ≥ nº de
candidatos ⇒ decisiones idénticas a sin poda; D-022 con poda activa (prefijo 100 + sufijo permutado, como
`tests/test_oracle_prior_g0.py`); D-032 (solo se abren el checkpoint y su JSON).

## 5. `configs/sweeps/g3_tune.yaml` (base = miembro `computo_b150_l4` de `configs/sweeps/computo_eval.yaml`, rol `official`)
Miembros `control`: `g3_root_k6_b150`, `g3_all_k6_b150`, `g3_all_k4_b150`, `g3_all_k6_b150_d4` (micro_depth 4),
`g3_all_k6_b300_d4` (nodos 300), con `policy_prune_model_path: ~/code/Paletizador/09_Software/models/g3_v1/policy_g3.pt`.
No ejecutes el sweep ni el entrenamiento grande; da los comandos y la latencia de la red en CPU (1 hilo) para 16/64 candidatos.
