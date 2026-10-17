<task>
Rama: feat/h6-rapido (ya creada desde feat/techo-offline-estricto; worktree /home/ingen/code/Paletizador/09_Software/paletizado-autopilot).
Objetivo único: **reducir el tiempo por decisión de la configuración oficial sin cambiar NINGUNA decisión**. La configuración es la config
`h6_sin_gate` de configs/sweeps/h6_sin_gate.yaml (H1+H3+H5+H6 con stand_hw_height_margin_gate_mm=2400). Hoy tarda 1,12 s de media por
decisión en un proceso (p95 2,52 s); el límite (DECISIONS D-007) es media ≤ 1,0 s y p95 ≤ 3,0 s. Objetivo: media ≤ 0,8 s.
</task>

<deliverables>
1. Perfilado (cProfile/py-spy si está disponible) de 1 seed con la config oficial: tabla de las funciones con más tiempo acumulado.
2. Optimizaciones que conserven exactamente el comportamiento: cachés por decisión (heightfield, controles de accesibilidad/soporte por
   posición repetida), evitar deepcopy innecesarios en el beam y los rollouts (copias más baratas o deshacer), vectorizar bucles calientes,
   etc. Nada de cambiar parámetros, presupuestos, orden de evaluación ni desempates.
3. Test de equivalencia: con la config oficial, 2 seeds de referencia producen placements y KPI IDÉNTICOS (comparación completa de los
   dumps) antes y después; guarda los dumps de referencia como fixture.
4. Medición de coste en 1 proceso (2 seeds de ajuste 1001, 1002, sin otros sweeps corriendo): media, p95 y máximo por decisión, antes y
   después. Y una evaluación de las 10 seeds de referencia con `run_sweep.py` que reproduzca exactamente los KPI de out/sweeps/h6_sin_gate_ref
   (h6_sin_gate) seed a seed.
5. `docs/autopilot/PERF_H6.md` con el perfil, los cambios y la tabla de tiempos.
</deliverables>

<completeness_contract>
Hecho = tests en verde (incluida la equivalencia); smoke OK; KPI idénticos seed a seed; media ≤ 1,0 s (objetivo 0,8 s) en 1 proceso.
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + tests nuevos. Si fallan, corrige y repite.
</verification_loop>

<action_safety>
Solo optimizaciones que no cambien decisiones. No modifiques data/*.xlsx, ni la métrica, ni el validador, ni los controles físicos. No
commitees out/. NO hagas commits, push ni PR. Cuota limitada: prioriza perfilar y atacar el mayor consumidor primero.
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, perfil antes/después, tabla de tiempos, prueba de equivalencia y comandos.
</compact_output_contract>
