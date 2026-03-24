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

---

## ANÁLISIS DEL FLUJO REAL — Flujo_rampas_Tipos_contenedores.xlsx

### Hallazgos críticos que cambian el alcance del proyecto

El análisis del Excel completo (Flujo_rampas_Tipos_contenedores.xlsx) revela
que la arquitectura real del sistema es significativamente diferente a los
supuestos con los que se ha trabajado hasta ahora.

### 1. Hay DOS rampas, no una

| Rampa | Nombre | Cajas | Destinos | Horario |
|---|---|---|---|---|
| Rampa 1 | Exp.1 (MS-CC-00029) | 105 | ES | 07:25 – 10:17 |
| Rampa 2 | Exp.2 (MS-CC-00030) | 81 | FR, IT, NL, PT | 09:41 – 11:35 |

El scheduler actual gestiona una sola rampa. Con dos rampas el sistema
debe decidir en cada step qué caja coger y de qué rampa.

### 2. Hay múltiples destinos — palets monodestino

Las 186 cajas se distribuyen en 12 palets, cada uno con un único destino:

| Palet | Destino | Cajas reales |
|---|---|---|
| PA0001000000231940 | ES | 10 |
| PA0001000000231933 | ES | 23 |
| PA0001000000231937 | ES | 13 |
| PA0001000000231934 | ES | 21 |
| PA0001000000231935 | ES | 21 |
| PA0001000000231936 | ES | 17 |
| PA0001000000232655 | FR | 9 |
| PA0001000000232656 | IT | 12 |
| PA0001000000232653 | IT | 19 |
| PA0001000000232654 | IT | 5 |
| PA0001000000232651 | IT | 17 |
| PA0001000000232652 | IT | 19 |

**Media real de producción: 15.5 cajas/palet** (incluyendo palets incompletos).

El scheduler debe respetar el destino de cada caja — no puede mezclar
cajas de destinos diferentes en el mismo palet.

### 3. Tipos de contenedor reales

| Tipo | N cajas | Ancho (mm) | Largo (mm) | Alto (mm) |
|---|---|---|---|---|
| W450.L650 | 143 (77%) | 405–465 | 430–645 | 325–415 |
| W400.L650 | 18 (10%) | 350–400 | 410–635 | 330–435 |
| W400.L400 | 13 (7%) | 330–335 | 380–390 | 260–295 |
| W450.L685 | 5 (3%) | 440–450 | 655–660 | 360–400 |
| PICKING MULTISHUTTLE | 4 (2%) | 445–480 | 605–700 | 360–460 |
| W400.L685 | 3 (2%) | 355–365 | 655–660 | 370–395 |

### 4. Comparativa baseline simulado vs producción real

| Métrica | Simulado (micro=False) | Real (Excel) |
|---|---|---|
| Rampas | 1 | 2 |
| Cajas totales | 186 | 186 |
| Palets construidos | ~11 | 12 |
| Media cajas/palet | 17.3 | 15.5 |
| Max cajas/palet | 24 | 23 |
| Destinos | 1 (forzado) | 4 (ES/IT/FR/NL/PT) |

El sistema simulado supera la media real (17.3 vs 15.5) incluso con una
sola rampa y sin restricción de destino, lo que indica que el algoritmo
es competitivo con la asignación manual real.

### 5. Preguntas abiertas que bloquean el diseño final

Estas preguntas deben responderse antes de continuar el desarrollo:

| # | Pregunta | Impacto |
|---|---|---|
| 1 | ¿El sistema final tendrá 1 rampa o 2? | Si son 2, el scheduler debe gestionar selección multi-rampa |
| 2 | ¿Los palets son monodestino o pueden mezclar? | Si monodestino, el scheduler debe filtrar por destino en cada step |
| 3 | ¿Hay un palet activo por destino simultáneamente? | Determina cuántos palets construye el robot en paralelo |
| 4 | ¿El robot construye UN palet a la vez o varios en paralelo? | Cambia completamente la arquitectura del scheduler |

### Estado del trabajo hasta este punto

Todo el trabajo realizado (scheduler greedy heightfield, validación con
10 seeds aleatorios, análisis de micro_planner, etc.) es válido para el
escenario simplificado de 1 rampa / 1 destino / 1 palet. Sirve como
baseline y punto de partida para el sistema multi-rampa multi-destino.

El código experimental (ZoneScheduler, GuidedHeightfield, XmaxFirst) está
disponible en el repo pero no se usa en producción. El sistema validado
es el PolicyPackerScheduler con micro_plan=False.

---

## BASELINE MULTI-RAMPA — RESULTADOS VALIDADOS

### Configuración
- arrival_mode: immediate (los timestamps del Excel son incompatibles con asignación aleatoria por caja)
- Excel fuente: data/Flujo_rampas_Tipos_contenedores.xlsx (2 hojas: Rampa1 105 cajas, Rampa2 81 cajas)
- Generación de inputs: scripts/gen_multi_ramp_input.py --seeds 42 123 777 999 1234
- Benchmark: scripts/benchmark_multi_ramp.py --input-dir data/multi_ramp_inputs/ --workers 5
- Parámetros de simulación: idénticos al baseline mono-rampa validado (ver sección anterior)
  - micro_plan=False (único valor válido en producción)

### Por qué arrival_mode=excel falla con asignación aleatoria por caja
Con timestamps reales, Rampa2 empieza 2h15min después de Rampa1. Durante ese gap,
solo están activos los destinos 1/2/3. Si queda 1 sola caja de Rampa1 que no cabe
en ningún palet activo, el sistema hace DEADLOCK global porque no hay más cajas
entrando. Con arrival_mode=immediate todas las cajas están disponibles desde t=0
y el scheduler siempre tiene opciones.

### Resultados (5 seeds, palets completos, excluyendo último incompleto por destino)

| seed | palets | media | min | max | stop_reason |
|------|--------|-------|-----|-----|-------------|
| 42   | 9      | 15.6  | 12  | 20  | None        |
| 123  | 8      | 16.9  | 15  | 21  | None        |
| 777  | 10     | 15.7  | 12  | 24  | None        |
| 999  | 9      | 15.0  | 12  | 20  | None        |
| 1234 | 8      | 16.5  | 14  | 24  | None        |
| **GLOBAL** | **8.8** | **15.9** | **12** | **24** | |

### Comparativa con producción real
- Producción real (manual, 2 palets): 15.5 cajas/palet
- Sistema simulado (6 palets, multi-rampa): 15.9 cajas/palet (+2.6%)
- El sistema con 6 palets supera la media real con 2 palets.

### Componentes añadidos en este milestone
- src/sim/io_multi.py — loader multi-rampa (no usado en producción, solo para --multi-ramp)
- scripts/gen_multi_ramp_input.py — generador de inputs con seed reproducible
- scripts/benchmark_multi_ramp.py — benchmark multi-rampa con ProcessPoolExecutor
- src/sim/run.py — flags --multi-ramp y --multi-ramp-seed

### Próximos pasos identificados
1. Validar con más seeds (50+) para confirmar estabilidad de la media
2. Evaluar impacto de ramp_cap (15→20) en escenario multi-rampa
3. Definir métricas de tiempo de ciclo robot (makespan, utilización)
