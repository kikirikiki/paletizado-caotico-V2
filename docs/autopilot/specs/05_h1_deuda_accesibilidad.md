<task>
Rama: feat/h1-deuda-accesibilidad (ya creada; worktree /home/ingen/code/Paletizador/09_Software/paletizado-autopilot).
Objetivo único: técnica H1 de la Fase 1 — "deuda de accesibilidad": que la política evite colocaciones que dejan zonas
bajas rellenables permanentemente inaccesibles para el brazo.

Diagnóstico (docs/autopilot/RESEARCH.md §1bis): en arm6_4orient el 36 % de la envolvente queda como espacio libre
sobre el perfil final y el 18,7 % del suelo nunca se cubre. `RobotAccessibilityControl` (src/palca/packer/controls.py:314)
declara infactible una colocación si alguna caja que toque su huella (gap ≤ 0 en x e y) tiene la parte superior por encima
de `local_z + delta` (delta = 400). Por tanto, al colocar una caja con techo t, toda zona vecina con altura actual
h < t − delta queda bloqueada para cualquier caja futura que toque esa caja.
</task>

<deliverables>
1. Parámetros nuevos, propagados de `run_simulation` → `PolicyPackerScheduler.from_defaults` → configuración del scheduler,
   con valores por defecto que reproducen EXACTAMENTE el comportamiento actual:
   `access_debt_weight: float = 0.0`, `access_debt_band_mm: int = 300`, `access_debt_mode: str = "penalty"` ("penalty" | "filter").
   Si delta de accesibilidad es 0, la deuda es 0 siempre.
2. Cálculo de la deuda de un candidato (función pura, testeable, en un módulo nuevo `src/palca/scoring/access_debt.py`):
   - entrada: heightfield del palet ANTES de colocar (rejilla; reutiliza la representación de alturas existente si la hay,
     o construye una rejilla de 10 mm a partir de las placements), footprint y techo t del candidato, delta, banda;
   - banda = celdas a distancia ≤ band_mm del footprint (fuera de él) dentro de la envolvente;
   - `dead_volume_mm3 = Σ_banda max(0, (t − delta) − h_celda) · área_celda`, solo celdas con h_celda < t − delta;
   - devuelve también `dead_area_mm2`.
   - Rendimiento: vectorizado con numpy; objetivo < 0,5 ms por candidato. Mide y reporta.
3. Integración en la selección del scheduler (scheduler_v1, donde se calcula `scalar_score`/`_score_candidate` y se aplica
   `choose_with_height_slack`):
   - "penalty": `scalar_score -= access_debt_weight · dead_volume_mm3 / volumen_caja_candidata`;
   - "filter": se descartan los candidatos con deuda > 0 si queda al menos uno con deuda 0 (si no, todos siguen);
   - telemetría en el payload: media de deuda del candidato elegido, nº de decisiones donde la deuda cambió la elección.
4. Tests: deuda 0 en suelo vacío y con delta 0; caso sintético de torre junto a hueco → deuda > 0 exacta; regresión:
   con `access_debt_weight=0` el KPI por seed de `arm6_4orient` (2 seeds del Excel canónico) es idéntico al baseline.
5. Sweep de AJUSTE (seeds distintas de las de evaluación para no sobreajustar: `--seed-list 1001,1002,...,1010`):
   `configs/sweeps/h1_tune.yaml` con arm6_4orient y la rejilla weight ∈ {0.25, 0.5, 1, 2, 4} × band ∈ {200, 445} en modo
   penalty, más modo filter con band ∈ {200, 445}. Baseline = arm6_4orient sin H1. Ejecútalo con 15 workers.
6. Sweep de EVALUACIÓN con las 10 seeds de referencia (`--seeds 10`): baseline, la mejor config del ajuste (elegida
   SOLO por el KPI medio del ajuste) y su contraste gantry_4orient + H1 (informativo). Ejecútalo.
</deliverables>

<completeness_contract>
Hecho = tests en verde; smoke OK; summary.md de los dos sweeps (out/sweeps/h1_tune, out/sweeps/h1_eval) con KPI ± IC95,
comparación pareada vs baseline, ms/decisión p95, cajas no colocadas y violaciones del validador (deben ser 0).
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + tests nuevos. Si fallan, corrige y repite.
</verification_loop>

<action_safety>
Solo los archivos necesarios; todo detrás de parámetros con default que no cambia nada. No modifiques data/*.xlsx, ni
`palca/study/metric.py`, ni `palca/study/validate.py`, ni la regla de accesibilidad (es una restricción física).
No commitees out/ (ni outputs/, logs/, results/, .venv, __pycache__/). NO hagas commits (el sandbox no puede escribir en
.git): deja los cambios en el working tree. No hagas push ni abras PR. Nunca dos sweeps de 15 workers a la vez.
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos ejecutados con resultado, la config ganadora del ajuste y por qué, los dos
summary.md, coste por candidato y supuestos.
</compact_output_contract>
