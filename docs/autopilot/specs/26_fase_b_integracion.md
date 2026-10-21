# Spec 26 — Fase B (D-039), módulo 2: red de valor G1 en la búsqueda H5/H6, evaluada por lotes

Rama `feat/investigacion-larga` (wt2). No commitees. Archivos: `src/palca/scheduler/scheduler_v1.py` (búsqueda micro, valores
de nodo y `_learned_tail_value`), `src/palca/integration/policy_packer_sched.py` y `src/sim/run.py` (paso de parámetros y dump,
como en spec 24a), `src/palca/learning/value_net.py` (solo si hace falta un helper), `scripts/benchmark_one_pallet_canonical.py`
(añade los parámetros nuevos a los excluidos del perfil congelado), `tests/test_value_net_search_g1.py`.
Antecedentes: R13 (`micro_plan_value` = `learned` / `rollout+learned`, `learned_value_weight`), spec 24a (estilo de los cambios).

## Qué
1. Parámetro nuevo `learned_value_model: "r13" | "g1"` (default `"r13"`: todo idéntico) y `learned_value_model_path` (str,
   obligatorio con g1) y `learned_value_device: "cpu" | "cuda"` (default cpu). Con `g1`, los modos existentes `learned` y
   `rollout+learned` usan la red G1 en lugar del ridge R13, con la misma semántica de valor: volumen colocado +
   `learned_value_weight` × `remaining_value_batch` (volumen adicional esperado por palet).
2. **Entrada de la red = solo lo observable en ese nodo**: `encode_state(pallet, cajas visibles que quedan en la cinta en ese
   nodo)` (las del haz, nunca aguas arriba ni el oráculo). Al final de un rollout de H6, las visibles son las que queden en la
   cinta del nodo hoja (las futuras sintéticas no se pasan como visibles).
3. **Evaluación por lotes**: todos los nodos que necesitan valor en una capa del haz (y todas las hojas de rollout de una
   decisión, si el flujo de código lo permite sin cambiar el orden de los desempates) se codifican y evalúan en UNA llamada a la
   red. Debe dar exactamente las mismas decisiones que la evaluación nodo a nodo (comprobado en test con un interruptor interno
   `_g1_batching=False`).
4. Carga del modelo una sola vez por proceso (caché por ruta + SHA del checkpoint); `torch.set_num_threads(1)` en CPU;
   `torch.use_deterministic_algorithms(True)`. Las estadísticas del plan y el dump registran modelo, SHA y dispositivo.
5. Telemetría de tiempo: ms de red por decisión (media) en `micro_plan` stats, para separar coste de red y de geometría.

## Tests (`tests/test_value_net_search_g1.py`)
- Defaults: dump completo idéntico (normalizando `git_head` y tiempos) en las seeds 50021 y 1 con `h6_sin_gate` y
  `micro_plan_hard_cap_ms: 600000` (como 24a; si tarda > 10 min déjalo preparado y dilo).
- Lotes vs nodo a nodo: mismas decisiones en un episodio corto (≥ 30 decisiones) con un checkpoint pequeño entrenado en el
  test (usa `scripts/train_value_g1.py` sobre 2 episodios greedy en `tmp_path`, CPU, 1 epoch).
- D-022 (mismo procedimiento que `tests/test_oracle_prior_g0.py`, prefijo 100 + sufijo permutado) con `rollout+learned`/g1:
  decisiones idénticas hasta que la caja cambiada entra en la cinta.
- D-032: con g1, un episodio no abre más ficheros que el checkpoint y su JSON (bloquea el resto).
- Ruta inexistente o SHA incorrecto → error claro.

## Entrega
`configs/sweeps/g1_tune.yaml` (copia `h6_sin_gate` de `configs/sweeps/g0_margen.yaml` con `micro_plan_hard_cap_ms: 600000`;
`learned_value_model_path: ~/code/Paletizador/09_Software/models/g1_v1/value_g1.pt` expandido a ruta absoluta en el runner o
documentado), miembros: `g1_learned_b57` (`learned`, w 1), `g1_learned_b150` (`learned`, nodos 150), `g1_mix_w05` y `g1_mix_w1`
(`rollout+learned`, w 0,5 y 1), `g1_mix_w1_b150_l4`. No ejecutes el sweep. Da el ms de red por decisión medido en 1 episodio
corto (CPU) para el modo más caro.
