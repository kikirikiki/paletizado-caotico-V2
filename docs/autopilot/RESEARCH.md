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

## 1ter. Autopsia con H1 (h1_eval2, 99 palets cerrados): ranuras estrechas
Con H1 el mínimo por palet sube del 28,6 % al 45,9 % (desaparecen los palets catastróficos), pero sigue habiendo un 32,7 % de
espacio libre sobre el perfil, un 16,2 % de suelo sin cubrir (24,8 % en el peor cuartil) y 1/3 de los cierres son
`DEADLOCK_STABILITY`.

Mapa promedio de suelo vacío (celdas de 100×105 mm): **no** está en la franja de 395 mm en y (y∈[425,820): 23 %, y∈[−20,425):
10 %), sino en un **valle a lo largo de x ≈ 380–780 mm** (25–52 % de suelo vacío, perfil medio ≈ 1,0–1,1 m frente a ≈ 2,1 m en
los extremos de x) y en la esquina x∈[−20,380], y∈[600,820] (49 %, perfil 0,57 m). La primera caja siempre va a la esquina de
origen.

Lectura geométrica: cuando las cajas planas se giran (W=445 a lo largo de x) y se apilan en los dos extremos de x, queda
1240 − 2 × 445 = **350 mm**, menos que cualquier caja de pie de la familia dominante (H ≥ 345–395) y que casi todo el flujo
(solo la familia W400.L400, 330 de ancho, cabe). Esa ranura no se puede llenar y además, al crecer las columnas laterales,
la regla de accesibilidad la cierra del todo. La orientación "buena" en x es L=605 a lo largo de x (605 + 605 = 1210).

→ **H2 (revisada): penalizar las ranuras estrechas**: al colocar una caja, si queda entre ella y un obstáculo (pared u otra
pila más alta que su base) un hueco de ancho 0 < g < s_min, penalizar el área de esa ranura. Generaliza la idea de carriles y
ataca directamente el valle observado. La spec 07 se reescribe en este sentido.

Herramientas de análisis (fuera del repo, en el scratchpad del director): descomposición cajas/huecos/espacio libre por palet y
mapa de suelo vacío, ambos sobre `out/sweeps/<sweep>/<config>/<seed>/{run,dump}.json` con `split_pallets`.

## 1quater. Autopsia con H1+H3+H5+H6 (h6_prior_eval/h6_generic, 87 palets cerrados)
| Config | Cajas | Espacio libre sobre el perfil | Suelo sin cubrir | z medio | Cierres por estabilidad |
|---|---|---|---|---|---|
| baseline | 61,9 % | 36,0 % | 18,7 % | 1535 | 33 % |
| H1+H3+H5 | 72,0 % | 25,5 % | 13,0 % | 1788 | 16 % |
| H1+H3+H5+H6 | 74,0 % | 23,7 % | 10,2 % | 1831 | 7 % |

La franja delantera y∈[−20,425) está prácticamente resuelta (3,3 % de suelo vacío, perfil medio 2030 mm). La pérdida se concentra
en la **franja trasera de 395 mm**, y∈[425,820) (17,8 % de suelo vacío, perfil 1613 mm), sobre todo en x≈380–780 (36–44 %) y en
las esquinas. Ahí no caben las cajas planas de la familia dominante (W = 445): solo cajas estrechas (W ≤ 395, ~34 en el flujo) o
cajas de pie, que el gate `stand_hw` solo permite cuando el palet está a ≤ 400 mm del techo. Es la causa dominante restante.
Más presupuesto de búsqueda no la resuelve (h6_tune_budget: paisaje ruidoso 69,5–75,0 % sin tendencia).

## 1quinquies. Autopsia de pozos con la oficial sin gate (h6_sin_gate_ref, 80 palets cerrados, 2026-10-20, D-025)
- Pérdida: cajas 77,1 % · huecos internos 2,2 % · libre sobre el perfil 20,7 %. De ese 20,7 %, **14,7 pp son pozos** (altura libre
  ≥ 750 y una celda > 400 mm más alta a ≤ 300 mm); **12,0 pp tienen más de 1500 mm de profundidad** (ninguna caja, ni de pie, asoma).
- **7,3 % de la planta interior no tiene ninguna caja encima, a ninguna altura** (el anillo de vuelo de 20 mm sí se usa: solo 0,5 %
  vacío). Es casi siempre **un único hueco por palet en el borde trasero, centrado en x ≈ 600, y ∈ [425, 820)**, de unos 780 × 380 mm
  de caja envolvente y ~48 % de relleno (forma irregular). 4,9 pp de suelo son huecos con lado menor de 350–450 mm.
- **Se crean pronto:** 6,9 de 14,7 pp en el primer quinto de cajas del palet; el creador suele ser una caja plana de 350–450 mm de alto
  (fila delantera que sube a dos capas, 690 mm > 400, y deja inaccesible el suelo trasero). La mediana del fondo del pozo es 350 mm.
- Mecanismo: la familia dominante (600 × 445 × 345) llena la fila delantera (y ∈ [−20, 425)); la franja trasera de 395 mm solo
  admite esa familia de pie (huella 445 × 345, dos por fila = 890 mm) más cajas estrechas para los ~350 mm restantes. Si en las 15
  visibles no hay cajas que encajen mientras la fila delantera está por debajo de 400 mm, el hueco queda muerto para todo el palet.
- R7 (cajas que asoman) apenas ayuda: la fila delantera supera enseguida los 750 mm de la caja más alta.
- Implicación: la palanca es **no dejar crecer la fila delantera más de 400 mm mientras haya suelo trasero rellenable** (H1 más
  fuerte o una guarda específica de huecos de suelo) o **elegir la disposición de la primera capa** para que la franja trasera sea
  rellenable con la familia dominante de pie.

### R12 — Pozos online vs offline (D-030; h6_sin_gate_ref vs testigo offline_witness_r7)
| Fondo del pozo | Online (77,06 %) | Offline (81,45 %) |
|---|---|---|
| En el suelo | 7,8 % | 9,3 % |
| **1–800 mm (sobre la 1.ª–2.ª capa)** | **3,9 %** | **0,4 %** |
| > 800 mm | 3,1 % | 1,4 % |
La diferencia online − offline (~3,7 pp) son **pozos a media altura**: columnas que se quedan en 1–2 capas mientras sus vecinas
suben > 400 mm. El método offline apila columnas completas y no los crea; además coloca el hueco estructural en una esquina
(trasera derecha) en vez del centro. → Reabre H4 (columnas online), que con el gate quitado dio +1,56 [0,45; 2,68] sobre H1+H3 y
nunca se probó sobre la oficial sin gate (spec 20).

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

## 4. Investigación larga con GPU: catálogo de ideas (D-039, 2026-10-21)
Contexto: 77,06 % oficial (`h6_sin_gate`), techo offline 81,45 %, T2 79,49 %. La pérdida recuperable está en pozos a media altura
(~3,7 pp online − offline, R12) creados por cajas que aún no se ven; el hueco trasero es estructural y lo comparte el techo.
R13 (ridge, 88 características) falló: R² 0,33, etiquetas de una política greedy distinta de la que busca, y flujos sintéticos
i.i.d. uniformes, muy distintos de un flujo real con familias repetidas (85 de 186 cajas son una familia). GPU: Quadro RTX 4000,
8 GB, Turing sm_75; PyTorch 2.11+cu128. CPU: Xeon W-2145, 16 hilos (los sweeps usan 15).

Reglas comunes a todas: solo las 15 cajas visibles en la decisión (D-022, test de fuga obligatorio); entrenamiento SOLO con flujos
sintéticos de un prior genérico o más amplio, nunca el Excel ni `out/` (D-032; test que bloquea la apertura de ficheros);
determinismo (D-028: semillas fijas, `torch.use_deterministic_algorithms(True)`, evaluación oficial con inferencia en CPU y
comprobación de que una evaluación repetida da el mismo dump); tiempo real D-007 medido en CPU y en GPU por separado.

| # | Idea | Mecanismo | Uso de GPU | Coste (Codex / Claude / máquina) | Riesgo | Ganancia esperada |
|---|---|---|---|---|---|---|
| G0 | **Diagnóstico de margen** (cotas, no candidatas) | (a) escalado de cómputo: H6 con 57/150/400 nodos y 2/4/8 hojas, sin límite de tiempo; (b) **oráculo de futuro**: rollouts de H6 con las cajas REALES siguientes (6 y 30) en lugar de muestras sintéticas. Mide cuánto margen hay en "más búsqueda" y en "mejor estimación del futuro" | ninguno | 1 encargo pequeño (prior `oracle`, marcado CON FUGA) + 2 sweeps de CPU | bajo | 0 (decide el orden de B/C) |
| G1 | **Red de valor CNN** V(mapa de alturas, 15 visibles) | CNN sobre el mapa de alturas 64 × 44 (20 mm, incluye el vuelo) + codificador de conjunto (DeepSets) de las cajas visibles restantes → utilización final del palet; objetivos auxiliares (volumen de pozos al cierre, cajas hasta el cierre). Sustituye o mezcla `_learned_tail_value` en las hojas de H5/H6 | entrenamiento (minutos–horas); inferencia por lotes | 2–3 encargos (datos, red, integración) / medio | medio (R13 falló; ruido irreducible de la etiqueta) | 0–2 pp |
| G1b | **Datos on-policy y prior amplio** (parte de G1) | etiquetas generadas con la propia `h6_sin_gate` (no con H1 greedy) y flujos sintéticos de un **prior jerárquico de familias** (nº de familias, frecuencias tipo Zipf y medidas aleatorias amplias, fijados a priori, sin mirar el flujo evaluado) mezclado con el genérico i.i.d. | — | incluido en el encargo de datos; CPU 1–2 días | bajo (es diseño de datos) | condición necesaria de G1 |
| G2 | **Inferencia por lotes de las hojas** | recoger todas las hojas del haz y evaluarlas en una llamada; si la red sustituye los rollouts, libera tiempo para más nodos o más hojas dentro de D-007 | sí (o CPU por lotes) | incluido en la integración de G1 | bajo | solo vale si G0(a) muestra margen |
| G3 | **Red de política / PUCT** (AlphaZero) | prior sobre las colocaciones candidatas (cabeza de política sobre el mismo tronco) para podar el haz y ordenar la expansión | entrenamiento + inferencia | 1–2 encargos / medio | medio | +0–1 pp sobre G1 |
| G4 | **Expert iteration / self-play** | búsqueda con la red → nuevos datos → reentrenar, en bucle durante días de GPU; validación en flujos sintéticos reservados | días de GPU y CPU | 1 encargo (orquestador) / bajo por iteración | medio-alto (inestabilidad, sobreajuste al prior) | +0–2 pp si G1 funciona |
| G5 | **Simulador vectorizado en GPU** (torch) | factibilidad, apoyo (ratio + esquinas + asentamiento), voladizo y accesibilidad de 400 mm con R7, evaluados en paralelo sobre el mapa de alturas; miles de rollouts por decisión | sí, intensivo | 2–3 encargos con `high` / alto (equivalencia exacta con `pallet_model`) | alto (divergencias con el validador) | depende de G0(a); también podría bajar `ventana18` a ≤ 1 s |
| G6 | **Destilación de un profesor privilegiado** (DAgger) | el profesor busca con el futuro REAL (oráculo de G0(b)) o con el planificador offline de columnas sobre flujos sintéticos; el alumno (red de valor/política) solo ve lo visible y aprende a imitar sus valores/decisiones en los estados que visita | entrenamiento | 1–2 encargos / medio | medio (el planificador offline no planifica desde estados arbitrarios: se usa el oráculo de búsqueda) | 0–2 pp; la cota es G0(b) |
| G7 | **Literatura**: PCT (Zhao et al., ICLR 2022; arXiv 2504.04421), MCTS con lookahead (arXiv 2601.02649), máscara de acciones (2404.04772), buffer (2502.13443) | 2601.02649 mezcla una política RL con una aleatoria en la selección de MCTS y descuenta el espacio desperdiciado en el backup; PCT genera hojas por EMS. Se usan como diseño de G1/G3, no como reimplementación (PCT entrenado desde cero con nuestras reglas de acceso y apoyo costaría más que G1–G4) | — | 0 | — | inspiración |
| G8 | **Optuna** sobre mandos existentes + `ventana18` | búsqueda bayesiana determinista de pesos y presupuestos en 1001–1010; combinada con la palanca de 18 visibles | no (CPU) | 1 script / bajo | sobreajuste a 10 seeds (antecedentes: paisajes planos o ruidosos) | 0–0,5 pp |
| G9 | Prior de rollouts condicionado a lo visible | las futuras de H6 copian con probabilidad p una caja visible y si no, el genérico (lo visible está permitido, D-032) | no | 1 encargo pequeño | bajo | baja: con el catálogo real como prior H6 no mejoró (D-017) |

**Orden por ganancia esperada por unidad de cuota:** G0 (barato y decide todo lo demás) → G1+G1b+G2 (fase B) → G6/G3/G4 (fase C,
solo si B pasa su corte) → G5 (solo si G0(a) muestra que más cómputo da ≥ +1 pp, o para hacer implantable `ventana18`) → G8/G9
(relleno de CPU ociosa).

Fuentes de la búsqueda de 2026-10-21: arXiv 2601.02649 (MCTS con paquetes de lookahead; crítico para el bootstrap de hojas),
arXiv 2504.04421 (PCT deliberado), arXiv 2507.09123 (estabilidad rápida), Jumanji (entornos JAX vectorizados; su BinPack no usa
mapa de alturas, no se reutiliza).
