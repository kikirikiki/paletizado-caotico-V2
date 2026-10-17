<task>
Rama: feat/h5-valor-estado (ya creada desde feat/h3-busqueda-determinista; worktree /home/ingen/code/Paletizador/09_Software/paletizado-autopilot-2).
Objetivo único: técnica H5 de la Fase 1 — **dar al beam de H3 un valor de estado que estime el llenado futuro**, en lugar de
ordenar y elegir solo por la suma de scores inmediatos de producción.

Referencia vigente: arm6_4orient + H1 + H3 = 70,94 % (out/sweeps/h3_eval). H1: access_debt_mode=penalty, weight=0.25, band=600.
H3: micro_plan=True, micro_plan_node_budget=100, micro_depth=3, micro_width=8, micro_topk=15 (determinista).

Diagnóstico (docs/COTAS.md B, docs/autopilot/H3_DETERMINISTIC_SEARCH.md):
- Más presupuesto de nodos en H3 no mejora (n250–n1000 ≤ n100): el límite es el objetivo del beam, no la profundidad.
- Offline con información completa: el beam con el scorer de producción llega solo al 71,64 %, igual que el online H1+H3.
  El método de columnas, que poda con un estimador de llenado futuro (`column_completion_value` en
  `src/palca/study/offline_search.py`: mochila fraccionaria de alturas con las cajas restantes), llega a 78,9–80,4 %.
- H2 (ranuras) y H4 (columnas) no aportan sobre H1+H3: las penalizaciones locales ya no añaden información.
</task>

<deliverables>
1. Parámetros (defaults = comportamiento actual de H3): `micro_plan_value: str = "score"` ("score" | "potential" | "rollout"),
   `micro_plan_value_weight: float = 1.0`, `micro_plan_rollout_leaves: int = 4`. Propagados como los demás.
2. "potential" (barato): valor de un nodo = volumen colocado en el palet + weight × potencial de llenado estimado con las cajas
   VISIBLES restantes en ese nodo (no las futuras desconocidas). Diseña un estimador barato y defendible, por ejemplo: para cada
   zona baja accesible del heightfield (respetando la regla de accesibilidad y el soporte de forma aproximada), mochila
   fraccionaria de alturas/volúmenes con las cajas visibles que caben en su huella. Debe penalizar implícitamente las zonas
   muertas (inaccesibles o más estrechas que cualquier caja visible). Documenta la fórmula.
3. "rollout": para los `micro_plan_rollout_leaves` mejores nodos de la última profundidad, completar con la política greedy de
   producción (con H1) las cajas visibles restantes sobre una copia del palet hasta que no quepa ninguna; valor = volumen total
   colocado. Cada colocación del rollout consume presupuesto de nodos (determinismo y tiempo real: el presupuesto total sigue
   acotado; reporta ms/decisión).
4. El valor se usa para ordenar/podar el beam y para elegir la primera acción. La primera acción elegida debe seguir siendo
   factible con los controles de producción (no cambia nada de la física).
5. Tests: determinismo (dos ejecuciones idénticas), "score" reproduce exactamente H1+H3 (2 seeds), casos sintéticos del potencial
   (zona muerta → potencial 0; zona accesible con caja visible que encaja → potencial > 0).
6. Ajuste (seeds 1001..1010, `--workers 15 --lock-retry-seconds 60`): referencia H1+H3; variantes potential × weight ∈ {0.25, 0.5,
   1, 2} y rollout × leaves ∈ {2, 4, 8} (con el presupuesto de nodos que haga falta para cumplir tiempo real: media ≤ 1 s y
   p95 ≤ 3 s por decisión; mide el coste con 1 proceso para la ganadora). Amplía una vez si la ganadora está en un borde.
7. Evaluación única (10 seeds de referencia): arm6_4orient, H1+H3, H1+H3+H5 ganadora; dos ejecuciones para demostrar
   determinismo; pareada H5 vs H1+H3; métricas D-015.
8. `docs/autopilot/H5_STATE_VALUE.md` con método, fórmula, tablas, coste y comandos.
</deliverables>

<completeness_contract>
Hecho = tests en verde; smoke OK; summaries de ajuste y evaluación (0 violaciones, 0 hard_cap_hits, repetición idéntica);
pareada H5 vs H1+H3 con IC95; coste de la ganadora con 1 proceso.
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + tests nuevos. Si fallan, corrige y repite. Si el smoke falla solo
porque /tmp/palca_sweep.lock está ocupado, espera y repítelo.
</verification_loop>

<action_safety>
Solo los archivos necesarios; con micro_plan_value="score" nada cambia. No modifiques data/*.xlsx, ni la métrica, ni el validador,
ni la regla de accesibilidad, ni el gate. No uses información de cajas no visibles (el flujo futuro es desconocido online).
No commitees out/. NO hagas commits. No hagas push ni abras PR. Nunca dos sweeps de 15 workers a la vez. Cuota limitada:
prioriza implementación, tests, ajuste y evaluación sobre documentación extensa.
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos con resultado, ganadora, summaries, pareada, coste.
</compact_output_contract>
