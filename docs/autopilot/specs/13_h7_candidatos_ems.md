<task>
Rama: feat/h7-candidatos-ems (ya creada desde feat/techo-offline-estricto; worktree /home/ingen/code/Paletizador/09_Software/paletizado-autopilot-2).
Objetivo único: técnica H7 de la Fase 1 (familia "Espacios" del prompt) — **ampliar la generación de posiciones candidatas** del
PalletModel con *extreme points* y esquinas de *empty maximal spaces* sobre el heightfield, además de las que ya genera el código
(maxrects/heightfield). Hipótesis (RESEARCH §1quater): la franja trasera de 395 mm se queda vacía; parte puede deberse a que el
generador actual no propone posiciones útiles para cajas estrechas o de pie en esa franja ni a alturas intermedias.

Referencia vigente: H1+H3+H5+H6 = 74,03 % (config `h6_generic` de configs/sweeps/h6_prior_eval.yaml).
</task>

<deliverables>
1. Parámetro `extra_candidates: str = "off"` ("off" | "ep" | "ems" | "ep+ems"), `extra_candidates_max: int = 32` (tope por caja y
   orientación, para acotar coste). Las posiciones extra se evalúan con EXACTAMENTE los mismos controles de producción y el mismo
   scoring que las existentes; se deduplican. Disponible en la selección greedy, en el beam y en los rollouts.
2. Prohibido introducir huecos artificiales para esquivar la accesibilidad (D-015): las posiciones deben estar apoyadas contra paredes o
   contra caras de cajas existentes (contacto), como hacen los extreme points clásicos.
3. Tests: con "off" se reproduce exactamente H6 (2 seeds); casos sintéticos (un extreme point conocido aparece; deduplicación; tope).
4. Ajuste (seeds 1001..1010, `--workers 15 --lock-retry-seconds 60`): referencia h6_generic; variantes ep / ems / ep+ems × max ∈ {16, 32};
   comprueba el tiempo real (media ≤ 1 s y p95 ≤ 3 s por decisión en 1 proceso) para la ganadora.
5. Evaluación única (10 seeds de referencia, dos ejecuciones idénticas); pareada vs h6_generic; si es limítrofe, réplica en las 20 seeds
   de D-016. Incluye la métrica D-015 (acc_violations_gap50) y la autopsia de la franja trasera (suelo vacío en y∈[425,820]) para
   ganadora y referencia.
6. `docs/autopilot/H7_EXTRA_CANDIDATES.md`.
</deliverables>

<completeness_contract>
Hecho = tests en verde; smoke OK; summaries con 0 violaciones y 0 hard_cap_hits; pareada con IC95; coste.
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + tests nuevos. Si fallan, corrige y repite.
</verification_loop>

<action_safety>
Solo los archivos necesarios; con extra_candidates="off" nada cambia. No modifiques data/*.xlsx, ni la métrica, ni el validador, ni la
accesibilidad, ni el gate. No commitees out/. NO hagas commits, push ni PR. Nunca dos sweeps de 15 workers a la vez. Cuota limitada.
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos con resultado, ganadora, summaries, pareada, coste.
</compact_output_contract>
