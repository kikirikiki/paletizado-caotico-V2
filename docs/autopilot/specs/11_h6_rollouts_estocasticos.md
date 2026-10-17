<task>
Rama: indicada en la cabecera del trabajo (apilada sobre H5). Objetivo único: técnica H6 de la Fase 1 — **rollouts con llegadas
futuras muestreadas**: en el valor de estado de H5, además de las cajas visibles, simular las próximas cajas que todavía no se ven
con una distribución de tipos conocida a priori, para que la búsqueda reserve espacio útil para lo que probablemente vendrá.

Referencia vigente: arm6_4orient + H1 + H3 + H5 = 72,04 % (configs/sweeps/h5_eval.yaml, config h5_rollout_l4).

Restricción anti-fuga (OBLIGATORIA): la política online NO puede usar la secuencia real futura ni las dimensiones exactas de las cajas
futuras del flujo que se está paletizando. La distribución a priori debe venir de información operativa conocida de antemano: la
proporción de **tipos de contenedor** del catálogo (docs/CONTEXT.md, sección "Tipos de contenedor reales": W450.L650 77 %, W400.L650
10 %, W400.L400 7 %, W450.L685 3 %, PICKING 2 %, W400.L685 2 %, con sus rangos de ancho/largo/alto). Muestrea dimensiones uniformes
dentro de esos rangos (o la mediana de cada tipo); documenta exactamente qué información se usa. Semilla del muestreo derivada de forma
determinista del estado (determinismo bit a bit).
</task>

<deliverables>
1. Parámetros (defaults = H5 tal cual): `rollout_future_boxes: int = 0` (nº de cajas futuras simuladas por rollout),
   `rollout_future_samples: int = 1` (nº de muestras promediadas), `rollout_prior: str = "catalog"`.
2. En el rollout de H5: tras colocar las cajas visibles restantes, continuar con `rollout_future_boxes` cajas muestreadas del prior
   (en orden), con la misma política greedy de producción (con H1); valor = volumen colocado medio sobre las muestras. Todo consume
   presupuesto de nodos (determinismo y tiempo real: media ≤ 1 s, p95 ≤ 3 s por decisión en 1 proceso).
3. Tests: determinismo; `rollout_future_boxes=0` reproduce exactamente H5 (2 seeds); el prior no lee el Excel del flujo (test que
   falle si se importa la secuencia real).
4. Ajuste (seeds 1001..1010): referencia H1+H3+H5; rejilla rollout_future_boxes ∈ {3, 6, 10} × samples ∈ {1, 3}, con el presupuesto
   de nodos necesario para que quepa (y su coste medido); amplía una vez si la ganadora está en un borde.
5. Evaluación única (10 seeds de referencia, dos ejecuciones idénticas) + pareada vs H1+H3+H5. Si es limítrofe (IC95 roza 0), réplica
   preregistrada en las mismas 20 seeds nuevas de D-016 (lista en docs/autopilot/H5_STATE_VALUE.md).
6. `docs/autopilot/H6_STOCHASTIC_ROLLOUTS.md`.
</deliverables>

<completeness_contract>
Hecho = tests en verde; smoke OK; summaries de ajuste/evaluación con 0 violaciones y 0 hard_cap_hits; pareada con IC95; coste.
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + tests nuevos. Si fallan, corrige y repite.
</verification_loop>

<action_safety>
Solo los archivos necesarios; con rollout_future_boxes=0 nada cambia. No modifiques data/*.xlsx, ni la métrica, ni el validador, ni la
accesibilidad, ni el gate. Nada de información del flujo futuro real. No commitees out/. NO hagas commits ni push ni PR. Nunca dos sweeps
de 15 workers a la vez (--lock-retry-seconds 60). Cuota limitada: prioriza implementación, tests, ajuste y evaluación.
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos con resultado, ganadora, summaries, pareada, coste.
</compact_output_contract>
