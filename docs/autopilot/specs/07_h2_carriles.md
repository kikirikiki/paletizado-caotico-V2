<task>
Rama: indicada en la cabecera del trabajo (apilada sobre H1). Objetivo único: técnica H2 de la Fase 1 — "carriles de planta":
preferencia suave para que la planta se organice en dos carriles a lo largo del eje largo (x, 1240 mm):
carril A de ~445 mm de ancho en y para cajas planas anchas (W > 395) y carril B de ~395 mm para cajas de pie (footprint H×W
con H ≤ 395 en y) o cajas estrechas (W ≤ 395). Se evalúa ENCIMA de H1 (la nueva referencia es arm6_4orient + H1 ganadora).

Diagnóstico (docs/autopilot/RESEARCH.md §1 y §1bis): 840 = 445 + 395. Una caja plana de la familia dominante (W≈445)
ocupa 445 en y y deja una franja de 395 que solo admiten cajas de pie o estrechas; si las cajas se colocan sin respetar esa
partición, la franja se fragmenta, quedan pozos que no cumplen el soporte 0,85 y el palet cierra con ~31–36 % de espacio libre
sobre el perfil. Los intentos anteriores de zonas (ZoneScheduler 4 zonas, 2 zonas izquierda/derecha) partían la planta en x, no
en y, y eran restricciones duras: H2 es una preferencia suave en y.
</task>

<deliverables>
1. Parámetros nuevos (defaults = comportamiento actual): `lane_weight: float = 0.0`, `lane_split_mm: int = 445`,
   `lane_side: str = "auto"` ("low_y" | "high_y" | "auto": el carril A en el lado donde se coloque la primera caja ancha
   del palet). Propagados como en H1 (run_simulation → PolicyPackerScheduler → scheduler).
2. Función pura `src/palca/scoring/lanes.py::lane_penalty(footprint, box_dims_original, orientation_family, split, side, envelope)`:
   - clasifica la caja colocada como "ancha" (footprint en y > 395) o "estrecha/de pie" (footprint en y ≤ 395);
   - penaliza en proporción al área del footprint que cae en el carril equivocado y al área que cruza la frontera de carriles;
   - devuelve un valor adimensional en [0, 1]; score -= lane_weight × penalización.
3. Integración en la selección (donde se aplica la penalización H1), con telemetría (fracción de decisiones en las que el
   carril cambió la elección).
4. Tests: casos sintéticos de las tres clases; regresión con lane_weight=0 idéntica a la referencia H1.
5. Ajuste (seeds 1001..1010, `--lock-retry-seconds 60`): referencia = arm6_4orient + H1 ganadora; rejilla
   lane_weight ∈ {0.05, 0.1, 0.25, 0.5, 1} × lane_side ∈ {low_y, auto}. Si la ganadora está en un borde, amplía una vez.
6. Evaluación única con las 10 seeds de referencia: arm6_4orient (baseline), arm6_4orient+H1, arm6_4orient+H1+H2 ganadora,
   con `--baseline arm6_4orient`; además la comparación pareada H1+H2 vs H1 (documenta cómo la calculas, p. ej. con
   `paired_comparison` sobre los kpi.json).
7. Documento `docs/autopilot/H2_LANES.md` con método, tablas y comandos.
</deliverables>

<completeness_contract>
Hecho = tests en verde; smoke OK; summary.md de ajuste y evaluación con 0 violaciones y 0 cortes de deadline; la
comparación pareada H1+H2 vs H1 reportada con IC95.
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + tests nuevos. Si fallan, corrige y repite.
</verification_loop>

<action_safety>
Solo los archivos necesarios; defaults sin cambios de comportamiento. No modifiques data/*.xlsx, ni la métrica, ni el
validador, ni la regla de accesibilidad. No commitees out/. NO hagas commits (el sandbox no puede escribir en .git). No hagas
push ni abras PR. Nunca dos sweeps de 15 workers a la vez (usa --lock-retry-seconds 60).
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos ejecutados con resultado, ganadora del ajuste, summary de evaluación, comparación H1+H2 vs H1.
</compact_output_contract>
