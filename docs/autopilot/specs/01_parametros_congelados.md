<task>
Rama: feat/parametros-congelados (ya creada desde feat/fifo-scorer-coherencia; estás en un git worktree
en /home/ingen/code/Paletizador/09_Software/paletizado-autopilot; `.venv` es un symlink al venv del checkout principal).
Objetivo único: congelar en UNA fuente de verdad los parámetros físicos del estudio de compactidad,
documentar el mapeo orientación → cara apoyada con un test que lo garantice, y crear el preset `arm6_4orient`.

Contexto (verificado por el director técnico, compruébalo tú también en el código):
- Orientaciones: `src/palca/packer/pallet_model.py::_orientation_variants_for_mode`. Caja con dims originales
  (L=largo, W=ancho, H=alto) tal y como vienen del Excel (columnas Largo/Ancho/Alto; en el flujo L>=W siempre).
  Variantes: `planar` LWH y WLH (footprint L×W, vertical H → apoya cara L×W, "plana" / "plana_rot");
  `stand_hw` HWL y WHL (footprint H×W, vertical L → apoya cara H×W, "pie" / "pie_rot").
  Ninguna variante del código apoya la cara H×L (Alto × Largo, footprint L×H, vertical W), que es la que la garra
  del cliente NO permite. Con `orientation_mode="planar+stand_hw"` salen exactamente las 4 válidas.
- Configuración de referencia del estudio = `BASE_PARAMS` de `studies/gantry_orientations/benchmark.py`
  en la rama `study/gantry-orientations` (no está en esta rama; consúltala con
  `git show study/gantry-orientations:studies/gantry_orientations/benchmark.py`). Cópiala tal cual:
  model=M1, n_per_pallet=999999, t_pick_place=14.0, staging_cap=0, ramp_cap=15, policy=palca, lookahead_k=15,
  arrival_mode=immediate, shuffle_window=15, shuffle_strength=1.0, overhang_mm=20, heuristic=bssf,
  stacking_mode=heightfield, stability_mode=ratio+corners+settle, min_support=0.85,
  score_mode=min_height_slack_then_gain, height_slack_mm=120, stand_hw_height_margin_gate_mm=400,
  time_budget_ms=900, micro_plan=False, force_destination=1, continuous_pallets=True, max_pallets=0,
  excel_path="data/Flujo rampas - Editado.xlsx".
- Seeds de referencia del estudio: [50021, 1, 42, 100, 777, 5678, 9999, 12345, 99999, 314159].
- El Excel `data/*.xlsx` es canónico: SOLO lectura (pandas). No lo modifiques nunca.
</task>

<deliverables>
1. `src/palca/study/__init__.py` y `src/palca/study/params.py` (módulo nuevo, sin tocar lógica existente):
   - `REFERENCE_EXCEL`, `REFERENCE_SEEDS`, `BASE_PARAMS` (dict con lo de arriba).
   - `PRESETS: dict[str, dict]` con al menos:
     `arm6_4orient` = BASE + orientation_mode="planar+stand_hw" + accessibility_delta_mm=400  (OFICIAL)
     `gantry_4orient` = BASE + orientation_mode="planar+stand_hw" + accessibility_delta_mm=0  (contraste informativo)
     `arm6_planar` = BASE + orientation_mode="planar" + accessibility_delta_mm=400  (control)
   - `preset_params(name: str, **overrides) -> dict`: copia profunda; valida que cada clave de override sea un
     parámetro real de `sim.run.run_simulation` (inspect.signature) y lanza ValueError si no.
   - `PALLET_NOMINAL_MM = (1200, 800)`, `MAX_LOAD_HEIGHT_MM = 2400` leídos de `palca.domain.pallet_spec.PalletSpec`
     (no reescritos a mano), y `pallet_envelope_mm(overhang_mm)` → (1200+2·oh, 800+2·oh, 2400).
2. `src/palca/study/orientation_faces.py`:
   - `RestingFace` (enum/str): "LxW", "HxW", "HxL".
   - `resting_face(original_lwh, placed_lwh) -> RestingFace`: determina la cara apoyada a partir de la dimensión
     que queda en vertical (H→"LxW", L→"HxW", W→"HxL"); robusto a dims iguales (si ambigua, devuelve la
     más restrictiva: si la vertical coincide con W, "HxL").
   - `FORBIDDEN_FACES_ARM6 = {"HxL"}` y `assert_mode_allowed(orientation_mode, forbidden=FORBIDDEN_FACES_ARM6)`
     que genera las variantes con `_orientation_variants_for_mode` sobre cajas de prueba (L>W>H distintas) y
     lanza ValueError si alguna apoya una cara prohibida. `preset_params("arm6_4orient")` debe llamarla.
3. Tests nuevos `tests/test_study_params.py` y `tests/test_orientation_faces.py`:
   - planar+stand_hw genera exactamente 4 variantes para L>W>H: {LWH, WLH} → LxW, {HWL, WHL} → HxW.
   - ninguna variante de los modos existentes apoya HxL (test explícito que fallaría si alguien añade el canto).
   - `resting_face` con un caso sintético de canto (vertical=W) devuelve HxL y `assert_mode_allowed` lo detecta
     (usa monkeypatch o una función de variantes inyectable para simularlo).
   - preset_params: copia independiente, override inválido → ValueError, arm6_4orient tiene delta 400 y modo planar+stand_hw.
4. `scripts/profile_flow.py` (solo lectura del Excel, sin escribir en data/): imprime y guarda en JSON
   (`--out out/profile_flow.json`, por defecto) el perfil del flujo canónico:
   nº cajas, nº referencias distintas (L,W,H exactos), top-10 por frecuencia, cuantiles de volumen (dm3),
   volumen total (m3), familias por redondeo a 25 mm, si L>=W siempre, nº palets mínimo por volumen
   (Σ vol / (1240·820·2400) y / (1200·800·2400)), distribución de la columna "Palet destino",
   distribución de peso si existe. Si existe `../paletizado-caotico-V2/data/Flujo_rampas_Tipos_contenedores.xlsx`
   (ignorado por git; solo lectura), calcula para los palets REALES del operario: nº cajas y volumen por palet y
   "utilización volumétrica equivalente" = vol_cajas/(1240·820·2400), etiquetada claramente como estimación
   (no se conoce la altura real de esos palets). Si no existe, lo indica.
5. `docs/PARAMETROS.md` (en castellano), con:
   - Tabla de parámetros físicos y de simulación: valor + fuente `archivo:línea` (palet, altura máx., overhang,
     envolvente 1240×820, orientaciones por configuración, garra/caras prohibidas, accesibilidad, min_support,
     micro_plan, buffer ramp_cap, lookahead_k, rampas (cuántas usa el DES con force_destination=1 y cómo se
     reparten las cajas; documenta lo que hace `src/sim/des.py` de verdad), arrival_mode, time_budget_ms,
     t_pick_place, gate stand_hw).
   - Indica si la altura 2400 incluye o no el palet de madera según el código (z=0 = tablero del palet?).
   - Tabla del mapeo orientación → cara apoyada → permitida en arm6_4orient (sí/no).
   - Perfil del flujo (copiado de la salida de `scripts/profile_flow.py`, con el comando que lo reproduce).
   - Sección "Discrepancias entre fuentes" con, como mínimo, las que encuentres tú más estas ya detectadas:
     (a) `vol_util%` histórico (64,6 % / 72,1 %, `studies/gantry_orientations`) se calculó como
         media_cajas × volumen_medio_supuesto / (1240·820·2400), no con el volumen real de cada caja;
     (b) en la rama feat/stand-hl-orientation (PR #139), las variantes `stand_hl` tienen exactamente la misma
         geometría que `stand_hw` (footprint H×W, vertical L): no son una orientación nueva
         (compruébalo con `git show feat/stand-hl-orientation:src/palca/packer/pallet_model.py`);
     (c) CONTEXT.md "configuración validada" usa micro_plan=true; producción usa micro_plan=False;
     (d) el Excel que cita CONTEXT.md (Flujo_rampas_Tipos_contenedores.xlsx) no es el canónico del estudio;
     (e) no existe una caja única 605×445×355: es una familia (dice el perfil cuál es la referencia exacta más frecuente);
     (f) AGENTS.md dice rama base feature/stability-controls; el estudio parte de feat/fifo-scorer-coherencia;
     (g) el smoke usa min_support=0.90, el estudio 0.85.
</deliverables>

<completeness_contract>
Hecho = los tests nuevos pasan; `./scripts/smoke_offline.sh` OK; `python scripts/profile_flow.py` produce
`out/profile_flow.json`; `docs/PARAMETROS.md` existe con todas las secciones y cada valor con su fuente archivo:línea;
`python -c "from palca.study.params import preset_params; print(preset_params('arm6_4orient'))"` funciona.
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + `.venv/bin/python -m pytest -q tests/test_study_params.py tests/test_orientation_faces.py`.
Si fallan, corrige y repite. No declares terminado con fallos.
</verification_loop>

<action_safety>
Solo los archivos necesarios. Sin refactors no pedidos: NO cambies `pallet_model.py` ni ningún comportamiento
del simulador. No modifiques data/*.xlsx. No commitees out/, outputs/, logs/, results/, .venv, __pycache__/
(añade siempre los archivos por ruta explícita, nunca `git add -A` ni `git add .`).
Commits pequeños en la rama actual, mensajes en castellano. No hagas push ni abras PR.
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos ejecutados con resultado (incluida la salida final del smoke y de pytest),
hashes de los commits, supuestos tomados y cualquier discrepancia nueva encontrada.
</compact_output_contract>
