# Spec 24a — G0(b) oráculo de futuro para H6 (COTA CON FUGA, D-039; nunca candidata)

Rama `feat/investigacion-larga` (wt2). No commitees. Archivos: `src/palca/scheduler/scheduler_v1.py` (solo `_rollout_value` y la
validación de la config), `src/palca/scoring/catalog_prior.py` si hace falta, el camino que pasa la cola aguas arriba al
scheduler (encuéntralo desde `SchedulerSimState`), `tests/test_oracle_prior_g0.py`.

## Qué
Nuevo `rollout_prior: "oracle"`: en los rollouts de H6 las llegadas futuras son las cajas REALES que siguen a la ventana visible
(las primeras `rollout_future_boxes` de aguas arriba, en orden), iguales en todas las muestras, en lugar de muestras sintéticas.
Mide cuánto ganaría H6 con un futuro perfecto (cota de G1/G6). Es una fuga deliberada:
- Solo se acepta con un override explícito `allow_oracle_leak: true` (si no, `ValueError`). Default `false`.
- Las estadísticas del plan y el dump marcan `"oracle_leak": true`.
- Si quedan menos cajas reales que `rollout_future_boxes`, se usan las que haya (fin de flujo).
- Defaults y cualquier otro prior: comportamiento idéntico. Comprueba equivalencia completa del dump (como `PERF_H6.md`,
  normalizando solo `git_head` y tiempos) en las seeds 50021 y 1 con `h6_sin_gate` y `micro_plan_hard_cap_ms: 600000`.

## Tests (`tests/test_oracle_prior_g0.py`)
- Sin `allow_oracle_leak` → `ValueError`.
- El test de fuga D-022 (flujo idéntico hasta la fila 100 y permutado después) **debe detectar** la fuga con `oracle`
  (decisiones distintas antes de que la caja cambiada entre en la cinta): demuestra que el test sirve. Usa el mismo
  procedimiento que el test D-022 existente (búscalo en `tests/` por "D-022").
- Con `generic` el mismo test sigue pasando.

## Entrega
Además del JSON, añade a `configs/sweeps/g0_margen.yaml` dos miembros (copiando `h6_sin_gate` de `configs/sweeps/pulmon_tune.yaml`
con `micro_plan_hard_cap_ms: 600000`): `oraculo_f6` (rollout_prior oracle, resto igual) y `oraculo_f30_b400_l4`
(oracle, `rollout_future_boxes: 30`, `micro_plan_node_budget: 400`, `micro_plan_rollout_leaves: 4`), ambos `role: bound`
(o el rol que el runner acepte para cotas; si no hay, `control`). No ejecutes el sweep.
