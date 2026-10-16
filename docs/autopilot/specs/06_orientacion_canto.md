<task>
Rama: indicada en la cabecera del trabajo. Objetivo único: añadir la orientación de **canto** (la caja apoya su cara
Alto × Largo: footprint L×H, vertical W) como modo de orientación opcional, para cuantificar la palanca de hardware
"garra que permita apoyar H×L" (PLAN_B) y el contraste real "gantry 6 orientaciones". NO forma parte de la configuración
oficial `arm6_4orient`, que debe seguir prohibiéndola.

Contexto: `src/palca/packer/pallet_model.py::_orientation_variants_for_mode` genera planar (LWH, WLH) y stand_hw (HWL, WHL).
En la rama feat/stand-hl-orientation, `stand_hl` era un duplicado geométrico de stand_hw (no lo uses ni lo copies).
Análisis previo (docs/autopilot/RESEARCH.md §1): la caja dominante de canto encaja 2×2 en planta (1210×710 en 1240×840).
</task>

<deliverables>
1. Nuevo modo `orientation_mode="planar+stand_hw+canto"` (constante en pallet_model; añadirlo a ALLOWED_ORIENTATION_MODES):
   variantes adicionales LHW (length=L, width=H, height=W) y HLW (length=H, width=L, height=W), `family="canto"`,
   deduplicadas como el resto. Sin gate de altura (el gate sigue aplicándose solo a stand_hw). Los modos existentes no cambian.
2. `palca.study.orientation_faces`: el modo nuevo produce la cara HxL → `assert_mode_allowed` lo rechaza para arm6
   (test). Añade `allowed_faces_for_preset(name)`.
3. `palca.study.validate.validate_pallet(..., forbidden_faces=FORBIDDEN_FACES_ARM6)`: parámetro nuevo con ese default (no
   cambia el comportamiento actual); la familia "canto" mapea a HxL. `run_sweep.py` pasa `forbidden_faces` según el preset
   (vacío para los presets con canto).
4. Presets nuevos en `palca.study.params` (sin prefijo `arm6_` para no disparar el guard, y documentados como PALANCA o
   CONTRASTE): `lever_arm_canto` = arm6_4orient + modo canto (garra alternativa, delta 400);
   `gantry_6orient` = gantry_4orient + modo canto (contraste informativo con 6 orientaciones reales).
5. Tests: variantes exactas del modo nuevo para L>W>H; modos antiguos idénticos (regresión); validador acepta canto solo
   si no está prohibido; preset arm6 sigue rechazando canto.
6. `configs/sweeps/lever_canto.yaml` con arm6_4orient (baseline), lever_arm_canto y gantry_6orient; ejecútalo con
   `--seeds 10 --workers 15 --baseline arm6_4orient` (si el lock /tmp/palca_sweep.lock está cogido, espera reintentando cada
   60 s). Pega el summary.md.
</deliverables>

<completeness_contract>
Hecho = tests en verde; smoke OK; summary.md del sweep lever_canto con 0 violaciones (con las caras permitidas de cada preset).
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + tests nuevos. Si fallan, corrige y repite.
</verification_loop>

<action_safety>
Solo los archivos necesarios; los defaults no cambian ningún resultado existente. No modifiques data/*.xlsx ni
`palca/study/metric.py`. No commitees out/. NO hagas commits (el sandbox no puede escribir en .git): deja los cambios en el
working tree. No hagas push ni abras PR. Nunca dos sweeps de 15 workers a la vez.
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos ejecutados con resultado, summary.md y supuestos.
</compact_output_contract>
