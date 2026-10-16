<task>
Rama: feat/metrica-congelada (ya creada desde feat/parametros-congelados; worktree
/home/ingen/code/Paletizador/09_Software/paletizado-autopilot). Objetivo único: una función ÚNICA y congelada para
el KPI de utilización volumétrica + un validador físico independiente de los palets producidos.
Todo experimento, sweep y cota posterior usará exclusivamente estas funciones.

Contexto (verifícalo en el código):
- `sim.run.run_simulation(..., dump_placements_path=...)` escribe `{"pallets": {"<pallet_id>": [items]}}` con
  items {box_id, length_mm, width_mm, height_mm, x_mm, y_mm, z_mm, rot90, orientation_family, orientation_name, ...}.
  Con `continuous_pallets=True` y `force_destination=1`, TODOS los palets físicos aparecen bajo la misma clave "1",
  en orden de colocación. El payload trae `metrics.pallet_kpis.continuous_pallet_sequence[dest]` = nº de cajas de cada
  palet físico en orden, y `continuous_closures_by_reason[dest]` = SOLO conteos por motivo (no el orden).
- El último palet de cada destino se cierra por `END` (agotamiento del flujo) y está incompleto.
- Coordenadas: envolvente con overhang 20 mm → x ∈ [-20, 1220], y ∈ [-20, 820], z ∈ [0, 2400].
- Parámetros físicos: usar `palca.study.params` (rama anterior). No escribir 1240/840/2400 a mano fuera de ahí.
- Deuda conocida: `pallet_volume_utilization` del JSON del DES incluye palets incompletos. No cambies su semántica
  (otros scripts lo usan); documenta en METRICA.md que NO es el KPI.
</task>

<deliverables>
1. Telemetría mínima en `src/sim/des.py`: añadir `pallet_kpis["continuous_closure_sequence"][dest]` = lista de
   motivos de cierre en orden (misma longitud que `continuous_pallet_sequence[dest]`, el último "END" si aplica).
   Cambio aditivo; ningún comportamiento del simulador cambia (los tests existentes deben seguir pasando).
2. `src/palca/study/metric.py` (congelado; cabecera con `METRIC_VERSION = "1.0.0"` y aviso de no modificar sin
   PR propio + revisión adversarial):
   - `ENVELOPE_MM = (1240, 840, 2400)` = `pallet_envelope_mm(20)` de params (palet nominal + 2×20 de overhang, CONSTANTE aunque un
     experimento cambie overhang_mm), `NOMINAL_MM = (1200, 800, 2400)`.
   - `PalletRecord` (dataclass): dest, index, n_boxes, box_volume_mm3, z_max_mm, closure_reason, closed (bool),
     items (lista de dicts).
   - `split_pallets(payload, dump) -> list[PalletRecord]`: parte los items por `continuous_pallet_sequence`,
     asigna `closure_reason` con `continuous_closure_sequence`; `closed = reason != "END"`. Debe lanzar error si
     Σ secuencia ≠ nº items o si faltan motivos. Funciona también con varios destinos.
   - `pallet_utilization(rec) = box_volume / prod(ENVELOPE_MM)` (volumen REAL de cada caja colocada).
   - `run_kpi(records) -> dict`: KPI oficial = Σ vol cajas de palets cerrados / (n_cerrados × V_envolvente);
     además: kpi_nominal (denominador 1200×800×2400), n_pallets_total, n_closed, boxes_placed, pallet_utils (lista),
     eff_real medio (vol / (1240·840·z_max)), h_fill medio (z_max/2400), kpi_all_pallets (incluye el último,
     solo informativo).
   - `aggregate_seeds(per_seed_kpis) -> dict`: media, sd, n, IC95 % (t de Student; sin scipy: tabla de cuantiles
     t_{0.975,df} para df 1..120 y 1.96 por encima), min, max. Unidad estadística = la seed.
   - `paired_comparison(kpis_a, kpis_b)` (mismas seeds, mismo orden): diferencia media en pp, sd, IC95 %,
     `significant = IC no contiene 0`.
3. `src/palca/study/validate.py` — validador físico INDEPENDIENTE (no reutilices el código del packer):
   - dentro de la envolvente; sin solapes 3D entre cajas (tolerancia 0,5 mm);
   - dims colocadas = permutación de las dims originales de esa caja (mapea box_id → caja de entrada; documenta cómo)
     y cara apoyada permitida según `palca.study.orientation_faces` (prohibida H×L para arm6). Si las dims son
     ambiguas (W==H o L==W), usa `orientation_family`/`orientation_name` del dump para desambiguar en vez de
     marcar violación por la regla conservadora de `resting_face`;
   - soporte: fracción del área de la base apoyada en el suelo (z=0) o en caras superiores de cajas cuya parte
     superior esté a z±1 mm, ≥ min_support (0,85 por defecto);
   - `validate_pallet(items, original_dims_by_box_id, min_support=0.85) -> list[Violation]`.
4. `scripts/kpi_from_run.py --run <payload.json> --dump <dump.json> [--validate]` imprime el KPI y las violaciones.
5. `docs/METRICA.md`: definición exacta, fórmula, qué es "palet cerrado", unidad estadística, IC, test de
   significación, por qué la envolvente es constante, relación con las cifras históricas (64,6 % / 72,1 % eran
   media_cajas × volumen_supuesto), y comando para reproducir la línea base de `arm6_4orient` con 1 seed.
6. Tests `tests/test_study_metric.py`, `tests/test_study_validate.py`:
   - split con dump sintético (2 palets cerrados + END); error si Σ secuencia ≠ nº items;
   - KPI exacto en un caso sintético calculado a mano; IC con valores conocidos; paired_comparison;
   - validador: detecta solape, fuera de envolvente, soporte 0,5 < 0,85, cara H×L; acepta un palet válido;
   - integración: `run_simulation` con `data/Flujo_smoke_60.xlsx` y `preset_params("arm6_4orient")` (con
     `time_budget_ms` bajo para que sea rápido) → split coherente, KPI en (0, 1], 0 violaciones (si el simulador
     produce violaciones reales, NO relajes el validador: márcalo como xfail con el detalle y repórtalo).
</deliverables>

<completeness_contract>
Hecho = tests nuevos en verde; smoke OK; `scripts/kpi_from_run.py` funciona sobre una ejecución real de
`arm6_4orient` con la seed 50021 y el Excel canónico, imprimiendo KPI y nº de violaciones (pega la salida).
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + `.venv/bin/python -m pytest -q tests/test_study_metric.py tests/test_study_validate.py`.
Si fallan, corrige y repite. No declares terminado con fallos.
</verification_loop>

<action_safety>
Solo los archivos necesarios. Sin refactors. El único cambio permitido en el simulador es la telemetría aditiva del
punto 1. No modifiques data/*.xlsx. No commitees out/, outputs/, logs/, results/, .venv, __pycache__/
(git add por ruta explícita). NO hagas commits (el sandbox no puede escribir en .git): deja los cambios en el working tree; el director revisa y commitea. No hagas push ni abras PR.
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos ejecutados con resultado, supuestos tomados,
salida de kpi_from_run sobre seed 50021 y cualquier violación física encontrada en palets reales.
</compact_output_contract>
