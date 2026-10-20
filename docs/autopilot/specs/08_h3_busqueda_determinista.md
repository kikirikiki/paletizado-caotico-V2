<task>
Rama: indicada en la cabecera del trabajo (apilada sobre H1). Objetivo único: técnica H3 de la Fase 1 — **búsqueda sobre el buffer
con presupuesto determinista**: que el beam search existente (`SchedulerV1._choose_action_micro`, activado con micro_plan) pueda
cortarse por número de nodos expandidos en lugar de por tiempo de pared, para que sea reproducible bit a bit y con un coste de
tiempo real acotado. Se evalúa ENCIMA de H1 (access_debt_mode=penalty, weight=0.25, band=600).

Contexto (docs/autopilot/DECISIONS.md D-012): con H1 + micro_plan (depth 3, width 8, topk 15) el KPI sube a 70,90 % (+9,0 pp vs
baseline, +6,2 vs H1) con ~720 ms/decisión, pero NO es determinista: el corte por `time_budget_ms` (900 ms) depende de la carga
de CPU y al repetir el mismo sweep 8/10 seeds cambian de KPI. `micro_plan=True` está fijado como prohibido en el prompt (motivo
no documentado); H3 debe ser una opción nueva, reproducible, y se reportará aparte.
Cortes por tiempo actuales: `scheduler_v1.py` ~líneas 296–305 (deadline) y 658–680 (tres comprobaciones dentro del beam).
</task>

<deliverables>
1. Parámetro nuevo `micro_plan_node_budget: int = 0` propagado como los demás (run_simulation → PolicyPackerScheduler →
   SchedulerConfig). 0 = comportamiento actual (corte por tiempo). Si > 0: el beam NO consulta el reloj; se detiene al alcanzar
   exactamente ese número de expansiones (`nodes_expanded`), terminando la profundidad en curso de forma determinista (define y
   documenta la regla: p. ej. no se inicia una expansión nueva si se alcanzó el presupuesto). El fallback greedy, si se usa,
   tampoco debe depender del reloj en este modo.
   Salvaguarda: si una decisión supera `micro_plan_hard_cap_ms` (nuevo, por defecto 10000), se registra en telemetría
   (`hard_cap_hits`) y se corta; un run con hard_cap_hits > 0 se considera NO determinista (repórtalo).
2. Telemetría: nodos expandidos por decisión (media, p95, máx.), ms/decisión, hard_cap_hits.
3. Tests: (a) determinismo: dos ejecuciones idénticas con node_budget > 0 producen exactamente los mismos placements y KPI (usa
   el Excel canónico, 1 seed, presupuesto pequeño para que sea rápido); (b) con micro_plan_node_budget=0 el comportamiento es el
   actual; (c) el presupuesto se respeta (nodes_expanded ≤ presupuesto + anchura de la última expansión, o lo que defina tu regla).
4. Medición de coste: para node_budget ∈ {100, 250, 500, 1000, 2000} con depth 3 / width 8 / topk 15, mide ms/decisión en UN
   proceso sin otros sweeps corriendo (2 seeds) y deja la tabla en el documento. Real-time (DECISIONS D-007): media ≤ 1,0 s y
   p95 ≤ 3,0 s por decisión.
5. Ajuste (seeds 1001..1010, `--workers 15 --lock-retry-seconds 60`): referencia = arm6_4orient + H1; rejilla
   node_budget ∈ {250, 500, 1000, 2000} × (depth, width) ∈ {(2, 8), (3, 8), (3, 16), (4, 8)}, topk 15. Descarta las combinaciones
   que no cumplan el tiempo real medido en el punto 4. Ganadora = mayor KPI medio de ajuste entre las que cumplen tiempo real.
6. Evaluación única con las 10 seeds de referencia: arm6_4orient, arm6_4orient+H1, arm6_4orient+H1+H3 ganadora; ejecútala DOS veces
   (nombres distintos) y demuestra que H1+H3 da KPIs idénticos seed a seed en ambas. Comparación pareada H1+H3 vs H1.
7. `docs/autopilot/H3_DETERMINISTIC_SEARCH.md` con método, regla de corte, coste, tablas y comandos.
</deliverables>

<completeness_contract>
Hecho = tests en verde (incluido el de determinismo); smoke OK; tabla de coste; summary.md de ajuste y de las dos evaluaciones con 0
violaciones, 0 hard_cap_hits y KPIs idénticos entre repeticiones; pareada H1+H3 vs H1 con IC95.
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + tests nuevos. Si fallan, corrige y repite.
</verification_loop>

<action_safety>
Solo los archivos necesarios; con micro_plan_node_budget=0 nada cambia. No modifiques data/*.xlsx, ni la métrica, ni el validador,
ni la regla de accesibilidad. No commitees out/. NO hagas commits (el sandbox no puede escribir en .git). No hagas push ni abras PR.
Nunca dos sweeps de 15 workers a la vez (--lock-retry-seconds 60). La medición de coste del punto 4 debe hacerse sin sweeps en marcha
(comprueba que /tmp/palca_sweep.lock está libre justo antes y repórtalo).
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos ejecutados con resultado, tabla de coste, ganadora del ajuste, summaries de evaluación,
prueba de determinismo y pareada H1+H3 vs H1.
</compact_output_contract>
