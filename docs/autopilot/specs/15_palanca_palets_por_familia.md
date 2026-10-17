<task>
Rama: feat/palanca-palets-familia (ya creada desde feat/techo-offline-estricto; worktree /home/ingen/code/Paletizador/09_Software/paletizado-autopilot-2).
Objetivo único: cuantificar la PALANCA "2 posiciones de palet con asignación por familia" (DECISIONS D-019, R1): un palet recibe la
familia dominante (L∈[600,610], W∈[440,450], H∈[345,395]) y otro el resto, con desbordamiento cuando una caja no cabe en su palet.
Contexto: `open_pallets>1` ya existe (`src/palca/integration/policy_open_pallets.py`), pero con el scorer de producción empeora
(N=2: −1,98 pp) y hoy es incompatible con micro_plan (H3/H5/H6). Fase 1 = medir el potencial con H1 y política greedy; solo si es
prometedor se integrará con la búsqueda (fase 2, otro trabajo).
</task>

<deliverables>
1. `open_pallets_assignment: str = "score"` ("score" = comportamiento actual | "family"). En "family": las cajas de la familia dominante
   solo se proponen al palet A y el resto al palet B; si una caja no tiene colocación factible en su palet, se permite en el otro
   (desbordamiento, documenta la regla). Regla de cierre: la existente (un palet se cierra cuando ninguna caja visible cabe en él).
   Debe funcionar con H1 (access_debt_*).
2. Tests: "score" reproduce exactamente lo actual; en "family" una caja dominante va a A si cabe; desbordamiento cuando no cabe.
3. Sweep (10 seeds de referencia, `--workers 15 --lock-retry-seconds 60`): arm6_4orient+H1 (N=1, referencia de esta fase), +H1 N=2 "score",
   +H1 N=2 "family"; y lo mismo sin H1. Pareadas vs arm6_4orient+H1 N=1. Reporta también Σvol/(n·V) y nº de palets (con N>1 el KPI de
   palets cerrados puede sesgarse por el palet que queda abierto al final: compruébalo y explícalo).
4. `docs/autopilot/LEVER_FAMILY_PALLETS.md` con método, tabla y recomendación de si merece la pena la fase 2 (integración con H3–H6).
</deliverables>

<completeness_contract>
Hecho = tests en verde; smoke OK; summary.md con 0 violaciones; documento.
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + tests nuevos. Si fallan, corrige y repite.
</verification_loop>

<action_safety>
Solo los archivos necesarios; con open_pallets=1 o assignment="score" nada cambia. No modifiques data/*.xlsx, ni la métrica, ni el
validador, ni la accesibilidad, ni el gate. No commitees out/. NO hagas commits, push ni PR. Cuota limitada.
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos con resultado, summary.md y la recomendación sobre la fase 2.
</compact_output_contract>
