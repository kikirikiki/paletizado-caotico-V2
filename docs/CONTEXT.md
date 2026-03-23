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

## ANÁLISIS DE MEJORAS — CONCLUSIONES

### Métrica de evaluación corregida

La métrica principal no debe ser cajas/palet sino eficiencia volumétrica:
- vol_util% = vol_cajas / vol_palet_total (1240×820×2400mm) → 68.8% consistente
- eff_real% = vol_cajas / (1240×820×altura_max) → 71-75%
- h_fill% = altura_max / 2400mm → 88-96%

Estas métricas son consistentes en todos los seeds y configuraciones probadas.

### Alternativas de mejora probadas

#### 1. Reducción de min_support_ratio (0.85 → 0.80 → 0.75)
Resultado: sin impacto. Las tres configuraciones dan 18.6 cajas/palet de media.
Conclusión: los DEADLOCK son por falta de posiciones físicamente estables,
no por el umbral de soporte. Mantener 0.85 como valor más seguro físicamente.

#### 2. Ampliación del buffer (ramp_cap: 15 → 20)
Resultado: sin impacto. Buffer de 20 da exactamente 18.6 cajas/palet.
Conclusión: los DEADLOCK son por STABILITY (palet lleno), no por falta
de opciones en el buffer. Un buffer lateral físico de 5 cajas no se justifica
— coste operativo alto, beneficio cero.

#### 3. Análisis del 29% de cajas "sin zona"
Resultado: todas las 186 cajas del flujo caben en el palet completo (100%).
El concepto de "sin zona" era relativo a la geometría de 4 zonas fijas (A/B/C/D).
El greedy sin restricción de zona ya coloca el 100% del flujo.
Conclusión: el 29% sin zona no es un problema real del sistema actual.

### Techo de densidad del sistema actual

68.8% de vol_util es el techo natural de este flujo con este algoritmo.
El limitante es el empaquetado geométrico con cajas heterogéneas, no el algoritmo.

Para superar este techo, las únicas palancas reales son:
1. Mayor lookahead (micro_depth, micro_width) — coste: tiempo de cómputo
2. Pre-ordenación del flujo de entrada — decisión operativa, no algorítmica
3. Aceptar 68.8% como resultado competitivo para paletizado caótico heterogéneo

### Próximos pasos recomendados

1. Evaluar impacto de aumentar micro_depth (4→6) y micro_width (40→60)
2. Analizar si pre-ordenación parcial del flujo mejora la densidad
3. Validar el sistema con flujo real de producción (no solo el Excel de prueba)
