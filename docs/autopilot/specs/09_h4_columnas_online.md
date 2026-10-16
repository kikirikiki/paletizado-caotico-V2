<task>
Rama: indicada en la cabecera del trabajo (apilada sobre la mejor técnica vigente). Objetivo único: técnica H4 de la Fase 1 —
**plantilla de columnas online**: versión online (buffer k=15, llegada en orden) del método offline de columnas que alcanzó
78,9–80,4 % con información completa (docs/COTAS.md parte B; `src/palca/study/offline_search.py`, método `columns`).
Se evalúa ENCIMA de H1 (access_debt_mode=penalty, weight=0.25, band=600).

Idea: la planta se reparte en columnas fijas adaptadas a la familia dominante (≈600–610 × 440–450 × 345–395): dos columnas
planas a lo largo de x (A1 = x∈[0,620), A2 = x∈[620,1240), y∈[0, W_A)) y, en la franja y∈[W_A, 840) (≈390 mm), columnas para
cajas de pie (footprint H×W: W≈445 a lo largo de x, H ≤ 390 en y) o cajas planas estrechas (W ≤ 390): B1 = x∈[0,445),
B2 = x∈[445,890), B3 = x∈[890,1240) (350 mm: solo cajas estrechas/pequeñas). Cada caja que llega se apila en la columna donde
mejor encaja, manteniendo las columnas equilibradas.

Restricciones (OBLIGATORIAS, DECISIONS D-015):
- Las columnas son contiguas (se tocan): está PROHIBIDO introducir huecos artificiales entre columnas para esquivar la regla de
  accesibilidad (el offline usaba 2 mm). La regla de accesibilidad de producción se aplica tal cual a todas las colocaciones.
- Todas las colocaciones pasan por los controles de producción del PalletModel (soporte 0,85, accesibilidad 400, orientaciones,
  gate stand_hw). El gate stand_hw oficial es 400 mm; con columnas contiguas puede impedir empezar columnas de pie desde el suelo:
  mídelo, no lo esquives (la variante con gate abierto se reporta SOLO como palanca).
</task>

<deliverables>
0. Métrica de robustez D-015 en `scripts/run_sweep.py`: por tarea, `acc_violations_gap{5,20,50}` = nº de colocaciones que
   serían infactibles si la regla de accesibilidad (delta del preset) considerara adyacentes las cajas con hueco ≤ G mm,
   calculado sobre el dump en orden de colocación (misma definición que `RobotAccessibilityControl`, solo cambia la tolerancia);
   media por config en summary.md/summary.json. No cambia el KPI ni la regla.
1. Parámetros (defaults = comportamiento actual): `column_template: str = "off"` ("off" | "w445" | "w450" | "w455" | "auto"),
   `column_min_fill: float = 0.85` (fracción mínima del área de la columna que debe cubrir la huella de la caja para que la
   columna la acepte), `column_fallback: str = "free"` ("free" = si ningún (caja, columna) es factible, se usa la política
   normal (con H1) en cualquier sitio; "none" = sin fallback). "auto" elige el ancho W_A al abrir cada palet según el W
   dominante de las cajas visibles.
2. Generación de candidatos por columna: para cada caja visible y cada columna, las orientaciones permitidas cuyo footprint cabe
   en la columna y cubre ≥ column_min_fill de su área, colocadas alineadas a la esquina de la columna (x mínimo, y mínimo de la
   columna; determinista) sobre la altura actual de la columna; factibilidad SIEMPRE con los controles de producción.
3. Selección: entre candidatos de columna factibles, minimizar la altura del techo resultante de la columna (crecimiento
   equilibrado), desempatar por fill y por el scalar score de producción (incluida la penalización H1). Documenta la regla.
4. Tests: plantillas correctas (columnas contiguas, dentro de la envolvente, sin solapes); un caso sintético con cajas 605×445×355
   llena A1/A2; `column_template="off"` reproduce exactamente H1 (2 seeds canónicas); ninguna colocación usa huecos > 0 entre
   columnas adyacentes (test explícito).
5. Ajuste (seeds 1001..1010, `--workers 15 --lock-retry-seconds 60`), referencia = arm6_4orient + H1:
   column_template ∈ {w445, w450, w455, auto} × column_min_fill ∈ {0.75, 0.85} × column_fallback ∈ {free}. Gate oficial (400).
   Si la mejor está en un borde, amplía una vez.
6. Evaluación única (10 seeds de referencia): arm6_4orient, +H1, +H1+H4 ganadora (oficial) y, como PALANCA etiquetada,
   +H1+H4 ganadora con `stand_hw_height_margin_gate_mm=2400`. Pareadas vs baseline y vs H1. Incluye acc_violations_gap50.
7. `docs/autopilot/H4_COLUMNS.md` con método, tablas, coste (ms/decisión) y comandos.
</deliverables>

<completeness_contract>
Hecho = tests en verde; smoke OK; summary.md de ajuste y evaluación con 0 violaciones del validador, métricas de robustez D-015
reportadas y pareadas H1+H4 vs H1 con IC95.
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + tests nuevos. Si fallan, corrige y repite. Si el smoke falla solo
porque /tmp/palca_sweep.lock está ocupado por otro sweep, espera a que se libere y repítelo.
</verification_loop>

<action_safety>
Solo los archivos necesarios; con column_template="off" nada cambia. No modifiques data/*.xlsx, ni la métrica, ni el validador,
ni la regla de accesibilidad, ni el gate oficial. No commitees out/. NO hagas commits (el sandbox no puede escribir en .git).
No hagas push ni abras PR. Nunca dos sweeps de 15 workers a la vez (--lock-retry-seconds 60).
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos ejecutados con resultado, ganadora del ajuste, summary de evaluación, pareadas y robustez D-015.
</compact_output_contract>
