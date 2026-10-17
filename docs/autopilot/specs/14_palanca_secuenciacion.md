<task>
Rama: feat/palanca-secuenciacion (ya creada desde feat/techo-offline-estricto; worktree /home/ingen/code/Paletizador/09_Software/paletizado-autopilot-2).
Objetivo único: cuantificar la PALANCA OPERATIVA "secuenciación aguas arriba" (DECISIONS D-019, R4): si el almacén pudiera entregar las
cajas agrupadas dentro de ventanas de W cajas consecutivas, ¿cuánto sube el KPI con la mejor política (H1+H3+H5+H6, config `h6_generic`
de configs/sweeps/h6_prior_eval.yaml)?
</task>

<deliverables>
1. Parámetros de `run_simulation` (defaults = comportamiento actual): `arrival_sort_window: int = 0` (0 = sin reordenar) y
   `arrival_sort_key: str = "family_height"`. Se aplica DESPUÉS del shuffle por seed: se parte la secuencia en bloques consecutivos de W
   cajas y cada bloque se reordena de forma estable con la clave. Claves: "family_height" (primero la familia dominante
   L∈[600,610], W∈[440,450], H∈[345,395], luego el resto; dentro, alto descendente), "height_desc" (alto descendente), "volume_desc".
   Documenta que es una palanca operativa (requiere que el sistema aguas arriba secuencie) y que la política sigue siendo online
   (solo ve el buffer de 15).
2. Tests: W=0 reproduce exactamente la secuencia y el KPI actuales; un bloque se ordena según la clave; W ≥ nº de cajas ordena todo.
3. Sweep (10 seeds de referencia, `--workers 15 --lock-retry-seconds 60`): h6_generic con W=0 (referencia) y con
   W ∈ {15, 30, 60, 186} × clave ∈ {family_height, height_desc}; además baseline arm6_4orient con W ∈ {0, 30, 186} (para separar el
   efecto de la secuenciación del de las técnicas). Pareadas vs h6_generic W=0.
4. `docs/autopilot/LEVER_SEQUENCING.md` con método, tabla y lectura (incluye implicación operativa).
</deliverables>

<completeness_contract>
Hecho = tests en verde; smoke OK; summary.md del sweep con 0 violaciones; documento con tabla.
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + tests nuevos. Si fallan, corrige y repite.
</verification_loop>

<action_safety>
Solo los archivos necesarios; con arrival_sort_window=0 nada cambia. No modifiques data/*.xlsx, ni la métrica, ni el validador, ni la
accesibilidad, ni el gate. No commitees out/. NO hagas commits, push ni PR. Cuota limitada.
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos con resultado, summary.md.
</compact_output_contract>
