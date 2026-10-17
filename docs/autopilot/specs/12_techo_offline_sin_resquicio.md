<task>
Rama: feat/techo-offline-estricto (ya creada desde feat/h6-rollouts-estocasticos; worktree /home/ingen/code/Paletizador/09_Software/paletizado-autopilot-2).
Objetivo único: medir el **techo offline alcanzable SIN el resquicio de los 2 mm** (DECISIONS D-015): el método offline de columnas
(`src/palca/study/offline_search.py`, método columns; el testigo de 80,40 % usaba columnas separadas 2 mm a propósito
—ver `out/bounds/columns_candidate_v2.py` en el worktree paletizado-autopilot— para que la regla de accesibilidad de producción no
considere adyacentes las columnas vecinas y para poder empezar las columnas de pie antes de que el gate las permita).
</task>

<deliverables>
1. Parámetro `column_gap_mm` (por defecto el valor actual) en el método offline de columnas; con 0, las columnas de cada plantilla son
   contiguas (sin separación). Comprueba que con 0 ninguna colocación usa huecos entre columnas.
2. Métrica estricta opcional para el verificador `scripts/autopilot/verify_offline_witness.py`: `--adjacency-gap G` (por defecto 0 =
   regla de producción) que considera adyacentes las cajas con hueco ≤ G al comprobar la accesibilidad.
3. Ejecuta el método columns con `column_gap_mm=0` (plantillas 0, 1, 2; anchos 8 y 16; presupuesto ≤ 20 min de CPU por ejecución,
   ≤ 8 procesos) y, aparte, el beam offline existente (que no usa huecos artificiales). Para cada resultado: KPI oficial, palets,
   0 violaciones del validador y número de violaciones de accesibilidad con `--adjacency-gap 0` y `--adjacency-gap 50`.
4. Añade a docs/COTAS.md (está en esta rama) una sección "Techo offline sin resquicio" con la tabla y la lectura, sin borrar nada.
</deliverables>

<completeness_contract>
Hecho = tests nuevos en verde; smoke OK; resultados de columns con gap 0 (al menos 2 ejecuciones) verificados; sección en COTAS.md.
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + tests nuevos. Si fallan, corrige y repite.
</verification_loop>

<action_safety>
Solo los archivos necesarios; los valores por defecto no cambian ningún resultado existente. No modifiques data/*.xlsx, ni la métrica,
ni el validador congelado, ni la regla de accesibilidad de producción. No commitees out/. NO hagas commits, push ni PR. Cuota limitada.
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos con resultado y la tabla.
</compact_output_contract>
