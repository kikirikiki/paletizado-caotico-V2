# RESEARCH — notas del director técnico (Fase 0/1)

## 1. Geometría dominante: por qué el brazo se queda en ~63 %
Familia dominante ≈ 600–610 × 440–450 × 345–395 mm. Envolvente útil **1240 × 840 × 2400** (palet 1200×800 +
overhang 20 por lado; el 1240×820 histórico era un error aritmético, ver PARAMETROS discrepancia 8).
Footprints permitidos en `arm6_4orient`: L×W (plana, vertical H≈355) y H×W (de pie, vertical L≈605).

| Patrón (caja 605×445×355) | Cajas/palet | Utilización (vol. real / 1240·840·2400) |
|---|---|---|
| Solo plana: 2 por nivel (1210×445; el resto 1240×395 no admite 445) × 6 niveles (2130 mm) | 12 | ≈ 46 % |
| Franja A 1240×445 plana (2×6) + franja B 1240×395 de pie (355 en y, 2 por nivel × 3 niveles = 1815 mm) | 18 | ≈ 69 % |
| **Prohibida** (canto, footprint L×H 605×355, vertical W 445): 2×2 por nivel × 5 niveles (2225 mm) | 20 | ≈ 77 % |

Lecturas:
- 840 = 445 + 395: la franja de 395 mm admite cajas "de pie" (355 ≤ 395) o planas de la familia W400 (W ≤ 395:
  p. ej. 505×395, 390×330). 1240 = 2×605 + 30.
- Referencia humana: los palets llenos del operario real (21–23 cajas, Excel `Flujo_rampas_Tipos_contenedores`)
  equivalen a ~74–76 % sobre 1240×840×2400 **si** su altura no superó 2400 mm (no se conoce). El operario puede
  usar el canto.
  La envolvente casa mal con la familia dominante en las 4 orientaciones permitidas: el techo estructural para
  esta familia está en torno al 70–75 % salvo que las cajas pequeñas rellenen los huecos.
- La orientación de canto (prohibida por la garra) encaja 2×2 en planta (1210×710 dentro de 1240×840). Es la palanca de hardware n.º 1 a
  cuantificar en PLAN_B (garra que permita apoyar H×L).
- La mezcla de alturas 355 (plana) y 605 (de pie) dentro del mismo palet genera escalones en el heightfield; con
  `min_support=0.85` esos escalones bloquean el apilado y cierran el palet antes de tiempo. Hipótesis principal
  de la Fase 1: **zonificar la planta por familia de altura** (franja de 445 plana, franja de 375 de pie) y
  coordinar las alturas de columna (p. ej. 3×605 = 1815 ≈ 5×355 = 1775) para que las franjas se "encuentren" y
  admitan capas puente.
- El T3 (+50 % relativo ≈ 95 %) parece físicamente inalcanzable con 4 orientaciones: hay que demostrarlo con una cota
  formal (Fase 0).

## 1bis. Autopsia de los palets reales (exploración, 10 seeds de referencia, palets cerrados)
Descomposición del volumen de la envolvente (rejilla de 10 mm sobre el perfil de alturas final de cada palet):

| Config | Cajas | Huecos internos (bajo el perfil) | Espacio libre sobre el perfil | z máx. | z medio del perfil | Suelo sin cubrir |
|---|---|---|---|---|---|---|
| arm (delta 400) | 61,9 % | 2,1 % | **36,0 %** | 2273 mm | 1535 mm | 18,7 % |
| gantry (delta 0) | 66,8 % | 2,1 % | 31,1 % | 2287 mm | 1655 mm | 14,4 % |

Peor cuartil brazo: 51 % (14 cajas, z medio 1269, 2,3 de pie). Mejor cuartil: 73 % (20 cajas, z medio 1792, 7,6 de pie).
Peores palets: 8–13 cajas con 30–46 % del suelo sin tocar y z máx. 1465–2200.

**Causa dominante: no son huecos internos, son torres.** El palet cierra cuando una zona llega arriba mientras el resto
de la superficie queda ~740 mm más abajo y parte del suelo ni se usa. Mecanismo en el brazo
(`controls.py::RobotAccessibilityControl`): una colocación es infactible si cualquier caja que toque su huella
(incluidos contactos por arista o esquina, en todas las direcciones) tiene la parte superior a más de 400 mm por encima
de la altura de apoyo del candidato. Cuando una pila supera en 400 mm a una zona vecina sin rellenar, esa zona queda
**inaccesible para siempre**. El gantry (sin regla) recupera 5 pp, pero sigue con 31 % de espacio libre: la franja de
395 mm (840 − 445) solo admite cajas de pie o estrechas, y los pozos estrechos no cumplen el soporte 0,85.

Comprobación descartada (2026-10-16, exploratoria, 10 seeds de referencia, métrica congelada): el gate
`stand_hw_height_margin_gate_mm` (cajas de pie solo cerca del techo) NO explica el suelo vacío. Con gate 800 / 1600 / 2400
el KPI cambia +0,51 / −1,04 / −0,54 pp frente a 400, sin significación (IC95 pareados con 0 dentro). El scorer actual
no aprovecha las cajas de pie aunque se le permitan antes: el problema es de decisión (dónde y cuándo), no de permiso.
El gate 400 reprodujo el baseline exacto (61,89 %) con la CPU cargada → el simulador es determinista en la práctica.

Hipótesis de la Fase 1, por orden:
- H1 "deuda de accesibilidad": penalizar (o prohibir, con lookahead) las colocaciones que dejan zonas bajas
  rellenables a más de 400 mm por debajo de una vecina, es decir, mantener el crecimiento por niveles. Techo de referencia: el gap brazo↔gantry (≈ 5 pp).
- H2 planta por carriles: primera capa = 2 cajas planas (1210×445) + carril de 395 mm con cajas de pie o estrechas, y
  sincronización de alturas entre carriles para que las capas puente cumplan el soporte.
- H3 búsqueda sobre el buffer con rollouts, con el valor del estado = volumen colocado − penalización por zonas muertas.

## 2. Literatura relevante (2021–2026)
- Zhao et al., PCT (ICLR 2022) y *Deliberate Planning of 3D Bin Packing on Packing Configuration Trees*
  (arXiv 2504.04421): hojas por corner points / extreme points / EMS; planificación con lookahead, buffer y offline
  en un único marco. En robot real: 57,4 % de utilización con cajas grandes, ~10 s/caja. Repo: alexfrom0815/Online-3D-BPP-PCT.
- *Effective Online 3D Bin Packing with Lookahead Parcels Using MCTS* (arXiv 2601.02649): lookahead como MPC +
  MCTS, test A/B en sistema real (JD Logistics). Candidato directo para "búsqueda sobre el buffer".
- *Buffered palletization with density and rigidity* (arXiv 2502.13443) y *Iterative Action Masking* (arXiv 2404.04772):
  RL con buffer y máscaras de factibilidad (solo si lo heurístico se estanca).
- Rolling-horizon MILP + heurística (arXiv 2111.01072, MPackLite): lookahead en tiempo real.
- Estabilidad: *Fast Stability Validation and Stable Rearrangement* (arXiv 2507.09123) — COM dentro del polígono de
  contacto; SDF-Pack (arXiv 2307.07356) — test de polígono de apoyo. Patentes US 5908283 / 7210894: umbral de
  área soportada ~50–65 % según peso (el nuestro, 0,85, es estricto: relajarlo es una **palanca** y se reporta aparte, §7).
- Patente US 12441560 (re-planificación de paletizado): círculo de contención alrededor del centroide.

## 3. Familias a evaluar en la Fase 1 (orden previsto, por causa dominante)
1. Zonificación por familia de altura + sincronización de columnas (ataca la causa dominante de §1).
2. Búsqueda sobre el buffer (beam / MCTS con rollouts de la política base) dentro del presupuesto de tiempo real.
3. Generación de candidatos EMS / extreme points frente al heightfield actual.
4. Multipalet: N palets abiertos y asignación caja→palet (palanca operativa: requiere más posiciones de palet).
5. Estabilidad por polígono de apoyo / COM (palanca: relaja la regla 0,85, se reporta aparte).
