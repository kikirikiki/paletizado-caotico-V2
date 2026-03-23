## VALIDACIÓN DEL SISTEMA — RESULTADOS FINALES

### Configuración validada
```json
{
  "stacking_mode": "heightfield",
  "accessibility_delta_mm": 400,
  "score_mode": "min_height_slack_then_gain",
  "height_slack_mm": 120,
  "orientation_mode": "planar+stand_hw",
  "stand_hw_height_margin_gate_mm": 400,
  "heuristic": "bssf",
  "overhang_mm": 20,
  "stability_mode": "ratio+corners+settle",
  "min_support_ratio": 0.85,
  "micro_plan": true,
  "micro_depth": 4,
  "micro_width": 40,
  "micro_topk": 15,
  "lookahead_k": 15,
  "time_budget_ms": 900
}
```

### Resultados sobre el flujo completo (186 cajas, seed=50021)

- Total palets construidos: 10
- Total cajas procesadas: 186
- Media cajas/palet: 18.6 (excluyendo último incompleto: 19.9)
- Secuencia: [20, 22, 20, 24, 16, 18, 20, 19, 20, 7]

### Validación por palet individual (seeds 50021-50025)

- Palet 1 aislado: 20 cajas, consistente en todos los seeds
- Restricción robótica RobotAccessibilityControl (delta=400mm): activa y válida

### Modelo de robot validado

Robot articulado FANUC R2000i (tentativo), posicionado en Y-max.
Patrón de construcción: frontal (y<400mm) primero, trasero después.
El brazo articulado sortea cajas intermedias — solo las cajas adyacentes
al punto de destino restringen el acceso (delta 400mm).

### Análisis del patrón de construcción

- Steps 1-10: cara frontal (y≈-20mm) sube hasta ~2085mm
- Steps 11-20: cara trasera (y≈430mm) sube desde z=0
- El robot accede desde Y-max en ambas fases sin colisión
- Delta máximo entre zonas Y: 2085mm (aceptable porque el robot opera desde Y-max)

### Variabilidad por zona del flujo

Los palets 5 y 6 (16 y 18 cajas) coinciden con la zona más adversa del flujo
(posiciones 81-105): alta concentración de cajas sin zona asignada (largo>605mm)
y cajas en orientación stand_hw de gran altura (460-635mm).
Este comportamiento es esperado — no es un bug del algoritmo.

### Alternativas descartadas

| Alternativa | Resultado | Motivo descarte |
|---|---|---|
| ZoneScheduler 4 zonas (A/B/C/D) | 8 cajas/palet | Zonas B/C incompatibles con 92.5% del flujo |
| ZoneScheduler 2 zonas (L/R) | 13 cajas/palet | Split XY limita a 1 caja dominante por capa |
| GuidedHeightfield soft bonus | 14 cajas/palet | preview_place siempre prefiere la mitad más densa |
| XmaxFirstScheduler | 14 cajas/palet | Misma limitación geométrica |
| XminWallAccessControl | 12 cajas/palet | Restricción incorrecta para robot articulado |
| orientation_mode=planar | 12.4 cajas/palet | stand_hw necesario para aprovechar cajas altas |

### Estado del código

Ficheros añadidos en este milestone:
- src/palca/packer/zones.py — ZoneConfig y PALLET_ZONES (no usado en producción)
- src/palca/scheduler/zone_scheduler.py — ZoneScheduler y GuidedHeightfieldScheduler (no usado en producción)
- src/palca/scheduler/xmax_first_scheduler.py — XmaxFirstScheduler (no usado en producción)
- src/palca/packer/pallet_model.py — añadido preview_place_in_region()
- src/palca/packer/controls.py — añadido XminWallAccessControl y XminWallConfig
- scripts/benchmark_zone_scheduler.py — benchmark standalone del ZoneScheduler
- scripts/benchmark_xmax_first.py — benchmark standalone del XmaxFirstScheduler

El sistema de producción NO usa ninguno de los schedulers experimentales.
Usa el PolicyPackerScheduler existente con los parámetros de configuración validados.

### Próximos pasos identificados

1. Investigar palets 5 y 6 — gestión del flujo adverso (cajas sin zona)
2. Mejora del patrón de construcción para construcción uniforme en Y
3. Extensión a flujo multi-palet con asignación dinámica de destino
