<task>
Rama: indicada en la cabecera del trabajo (apilada sobre H1). Objetivo único: técnica H2 de la Fase 1 — **penalizar ranuras
estrechas**: que la política evite colocaciones que dejan, entre la caja y un obstáculo, un hueco demasiado estrecho para
cualquier caja razonable del flujo. Se evalúa ENCIMA de H1 (nueva referencia = arm6_4orient + H1 ganadora:
access_debt_mode=penalty, access_debt_weight=0.25, access_debt_band_mm=600).

(El nombre del archivo conserva "carriles" por trazabilidad; la primera versión de esta spec proponía carriles en y y se
sustituyó tras el análisis de docs/autopilot/RESEARCH.md §1ter.)

Diagnóstico (RESEARCH §1ter, 99 palets con H1): el suelo vacío se concentra en un valle a lo largo de x ≈ 380–780 mm
(25–52 % de suelo vacío, perfil ≈ 1,0 m frente a ≈ 2,1 m en los extremos). Explicación: cajas planas giradas (W=445 a lo largo
de x) apiladas en ambos extremos dejan 1240 − 2×445 = 350 mm, menos que cualquier caja de pie de la familia dominante
(H ≥ 345) y que casi todo el flujo. Esa ranura no se llena nunca y la regla de accesibilidad acaba cerrándola.
</task>

<deliverables>
1. Parámetros nuevos (defaults = comportamiento actual): `slot_weight: float = 0.0`, `slot_min_mm: int = 355`,
   propagados como en H1 (run_simulation → PolicyPackerScheduler → scheduler).
2. Función pura `src/palca/scoring/slots.py::narrow_slot_penalty(heightfield, footprint, base_z, slot_min_mm)` que reutilice la
   rejilla de `palca.scoring.access_debt` (HeightField de 10 mm). Para cada uno de los 4 lados del footprint del candidato:
   recorre hacia fuera, fila a fila (o columna a columna) dentro del tramo del lado, las celdas con altura ≤ base_z hasta
   encontrar una celda con altura > base_z o la pared de la envolvente; si la distancia g cumple 0 < g < slot_min_mm, esa
   franja es una ranura. Penalización = área total de ranuras / área del footprint del candidato (adimensional).
   La colocación pegada a pared u obstáculo (g = 0) NO se penaliza. Documenta las decisiones de borde.
   Rendimiento: vectorizado, < 0,5 ms por candidato (mídelo y repórtalo).
3. Integración en la selección junto a H1: `scalar_score -= slot_weight × penalización`; telemetría (penalización media del
   elegido, nº de elecciones cambiadas por H2).
4. Tests: casos sintéticos (caja pegada a pared → 0; ranura de 350 entre caja y pared → área exacta; ranura de 400 con
   slot_min 355 → 0); regresión: slot_weight=0 reproduce exactamente arm6_4orient + H1 (2 seeds canónicas).
5. Ajuste (seeds 1001..1010, `--workers 15 --lock-retry-seconds 60`): referencia = arm6_4orient + H1 ganadora; rejilla
   slot_weight ∈ {0.1, 0.25, 0.5, 1, 2} × slot_min_mm ∈ {300, 355, 400}. Si la ganadora está en un borde, amplía una vez.
6. Evaluación única con las 10 seeds de referencia (`--seeds 10`): configs arm6_4orient, arm6_4orient+H1 y
   arm6_4orient+H1+H2 ganadora, `--baseline arm6_4orient`; y la comparación pareada H1+H2 vs H1 con
   `palca.study.metric.paired_comparison` sobre los kpi.json (documenta el comando).
7. `docs/autopilot/H2_SLOTS.md` con método, tablas de ajuste/evaluación, coste y comandos.
</deliverables>

<completeness_contract>
Hecho = tests en verde; smoke OK; summary.md de ajuste y evaluación con 0 violaciones y 0 cortes de deadline; comparación
pareada H1+H2 vs H1 con IC95.
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + tests nuevos. Si fallan, corrige y repite.
</verification_loop>

<action_safety>
Solo los archivos necesarios; defaults sin cambios de comportamiento. No modifiques data/*.xlsx, ni la métrica, ni el
validador, ni la regla de accesibilidad. No commitees out/. NO hagas commits (el sandbox no puede escribir en .git). No hagas
push ni abras PR. Nunca dos sweeps de 15 workers a la vez (usa --lock-retry-seconds 60). Puede haber otro trabajo usando hasta
8 procesos: vigila los cortes de deadline.
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos ejecutados con resultado, ganadora del ajuste, summary de evaluación, pareada H1+H2 vs H1.
</compact_output_contract>
