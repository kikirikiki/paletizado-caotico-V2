# Informe de compactidad — paletizado caótico con robot de 6 ejes

> **Borrador en curso** (autopiloto, actualizado el 2026-10-21). Cada cifra tiene su comando de reproducción en
> `docs/autopilot/PROGRESS.md`; las decisiones están en `docs/autopilot/DECISIONS.md`.

## 1. Resumen ejecutivo
- **Configuración objetivo:** robot articulado de 6 ejes, `arm6_4orient`: 4 orientaciones (nunca apoyado en la cara Alto × Largo),
  accesibilidad de 400 mm y soporte ≥ 0,85. El robot solo ve las 15 cajas de la cinta (D-022).
- **KPI congelado (v1.0.0):** volumen real de las cajas de los palets cerrados / (nº palets cerrados × 1240 × 840 × 2400 mm).
- **Baseline re-medido: 61,89 % ± 1,32.** Las cifras históricas (64,6 % brazo, 72,1 % gantry) usaban un volumen medio supuesto y una
  envolvente errónea (1240 × 820); además, el "stand_hl" de aquel estudio era un duplicado de "stand_hw".
- **Techo alcanzable offline (información completa): 81,45 %**, en 9 palets; igual con la regla de acceso R7. Gap = 19,56 pp.
  Metas: **T1 ≥ 71,67 %**, **T2 ≥ 79,49 %**, **T3 ≥ 92,84 %**.
- **Mejor resultado online: 77,06 % ± 0,90** (H1+H3+H5+H6, sin gate de cajas de pie): **+15,2 pp sobre el baseline, 77,6 % del gap
  → T1 alcanzado sin condiciones.** Réplica en 20 seeds nuevas: 76,21 %. Tiempo real: 0,957 s/decisión de media, p95 2,36 s.
- **Nueva oficial (2026-10-22, D-040/D-041): "pensar más"**, la misma búsqueda con 150 nodos y 4 hojas en lugar de 57 y 2:
  **77,74 % en referencia y 77,82 % en la réplica (+1,61 pp significativo)**, 1,79 s/decisión de media (p95 4,12 s), dentro del
  nuevo límite de tiempo del usuario (media ≤ 3 s, p95 ≤ 7 s; con un PC más potente se podrá bajar). Investigación con GPU (D-039):
  una red de valor entrenada con 716 k estados sintéticos estima bien (R² 0,59) pero no mejora las decisiones; saber el futuro
  real dentro de la búsqueda tampoco (+0,8 n.s.). La cinta de 18 visibles no es posible físicamente.
  Una red de política que poda las opciones (G3, 92 % de acierto entre sus 6 mejores) tampoco mejora (−0,15 n.s.):
  la búsqueda ya descarta bien; lo que limita es el valor de las hojas. Línea de redes cerrada.
  Imitar al planificador offline de columnas (G6) tampoco es viable: en flujos sintéticos rinde 6,9 pp MENOS que el robot
  online, porque sus plantillas están hechas a medida del flujo de referencia (D-042).
- **Palanca de hardware confirmada (D-044): una garra que pueda colocar cajas en huecos más profundos.** Con la regla de acceso
  ampliada de 400 a 800 mm: **79,15 % en la réplica (+1,33 pp, significativo)**, 79,06 % en 30 seeds; con 600 mm, +0,74 pp
  (78,56 %). Ataca la causa principal de pérdida (pozos a media altura). Relajar la regla de apoyo (0,75/0,70) o imaginar más
  futuros no aporta. Es la palanca física más clara después de la cinemática gantry (82 %).
- **T2 no se alcanza** (faltan 2,4 pp). Desde el 77,06 % se han probado y descartado ocho líneas más (sección 5), con tres ciclos
  de pensamiento lateral: **77,06 % es el límite práctico medido con software para esta célula viendo 15 cajas** (D-035).
- **Palancas de hardware nuevas (D-037):** un pulmón de 1–3 posiciones junto al palet **no mejora** (réplica +0,12 n.s.); en cambio,
  **ver 18 cajas en la cinta en vez de 15 sí: +1,57 pp en la réplica (significativo), 77,7 % en 30 seeds**, aunque hoy tarda
  ~1,2× más por decisión y hay que optimizarlo para cumplir el tiempo real.
- **T3 (+50 %) no se alcanza.** Las cotas formales relajadas no lo descartan, y el techo alcanzable queda 11,4 pp por debajo
  (refutación empírica, D-010).

## 2. Datos y parámetros
Ver `docs/PARAMETROS.md`. 186 cajas, 111 referencias distintas, Σ vol = 16,79 m³ (mínimo trivial: 7 palets). Familia dominante
≈ 600–610 × 440–450 × 345–395 mm (85 cajas). Envolvente real: 1240 × 840 (palet 1200 × 800 + 20 mm de vuelo por lado).

Respuestas del responsable incorporadas: el gate de cajas de pie era una regla anti-torres y se elimina porque sin él hay menos torres
(D-021); las cajas pueden ir pegadas (D-024); una caja que, ya colocada, asoma por encima de todas sus vecinas puede bajar a un pozo de
más de 400 mm (regla R7, D-026).

## 3. Techo real medido (Fase 0, `docs/COTAS.md`)
| Cota / referencia | Valor | Tipo |
|---|---|---|
| Baseline arm6_4orient | 61,89 % | medida |
| Buffer infinito (k = 186), política de producción | 66,82 % | palanca de información |
| Beam offline con el scorer de producción | 71,64 % | techo alcanzable |
| **Columnas offline con plantillas contiguas** | **81,45 %** (9 palets; igual con R7) | **techo alcanzable usado para el gap** |
| Cota formal por palet (DFF + cortes, relajada) | 100 % flujo / 96,97 % familia dominante | cota formal (no informativa) |
| Cota trivial | ≥ 6,72 → 7 palets | cota formal |

## 4. Diagnóstico: dónde se pierde el volumen
- **Baseline:** el 36 % de la envolvente queda libre por encima del perfil (torres), frente a solo un 2 % de huecos internos.
- **Mejor configuración (77,06 %):** cajas 77,1 % · huecos internos 2,2 % · libre sobre el perfil 20,7 %. De ese 20,7 %, 14,7 pp son
  **pozos** que el brazo ya no puede rellenar (una vecina más de 400 mm más alta); 12 pp tienen más de 1500 mm de profundidad.
- **Causa:** en cada palet queda un único hueco de suelo junto al borde trasero (≈ 780 × 380 mm, 7,3 % de la planta) que ninguna caja
  cubre a ninguna altura. La familia dominante no cabe plana en la franja trasera de 395 mm; de pie caben dos y sobran ~350 mm que solo
  llenan cajas estrechas. Cuando la fila delantera pasa de 400 mm (en el primer quinto del palet), el hueco queda muerto.
- **Ese hueco es estructural:** el techo offline (información completa) también deja vacío el 7,4 % de la planta y un 11,1 % en pozos.
  Lo que separa online de offline son ~3,5 pp de pozos adicionales y ~0,9 pp de huecos internos (RESEARCH §1quinquies).

## 5. Ganancia por técnica (10 seeds de referencia; réplica de 20 seeds cuando fue limítrofe)
| Técnica | Δ KPI (pp, IC95) | KPI acumulado | % del gap | Estado |
|---|---|---|---|---|
| H1 deuda de accesibilidad | +2,81 [1,04; 4,57] | 64,70 % | 14 % | adoptada |
| H3 búsqueda determinista sobre el buffer | +6,24 [5,11; 7,36] | 70,94 % | 46 % | adoptada |
| H5 valor de estado por rollout | +1,11 (réplica +2,12 [1,08; 3,16]) | 72,04 % | 52 % | adoptada |
| H6 rollouts con cajas futuras (prior genérico) | +1,99 [1,23; 2,75] (réplica +1,27) | 74,03 % | 62 % | adoptada |
| Sin gate de cajas de pie (D-021, autorizado) | +3,03 [1,72; 4,35] (réplica +2,62 [1,52; 3,71]) | **77,06 %** | **78 %** | adoptada |
| H2 ranuras estrechas | −0,04 n.s. | — | — | descartada |
| H4 plantilla de columnas online | −0,62 n.s. | — | — | descartada |
| H7 candidatos extreme points / EMS | −0,53 n.s.; fuera de tiempo real | — | — | descartada |
| Laterales libres como desempate (D-024) | −1,33 [−2,38; −0,27] | — | — | no adoptada (cuesta compactación) |
| R7 cajas altas en pozos (D-026) | −0,29 n.s. | — | — | regla vigente, sin ganancia |
| H8 altura muerta bajo el techo | +0,01…+0,62 n.s. (ajuste) | — | — | descartada |
| H1 fuerte (peso 2, banda 900) | réplica +0,53 [−0,41; 1,45] n.s. | — | — | descartada (D-029) |
| Fase de suelo (primero llenar el suelo) | −1,0…−8,4 (ajuste) | — | — | descartada |
| H10 columnas adaptativas (solo 15 visibles) | −0,4…−2,1 (ajuste) | — | — | descartada |
| R13 valor de estado aprendido (flujos sintéticos) | réplica +0,78 [−0,13; 1,69] n.s. | — | — | descartada (D-034) |
| Re-ajuste de mandos existentes / gate solo | 0 / n.s. | — | — | descartadas |

La ventaja de la búsqueda solo usa las 15 cajas visibles (test de fuga, D-022). Es determinista: todas las cifras de evaluación se
miden sin cortes de reloj (D-028). Tiempo por decisión de la mejor configuración, en un proceso: 0,957 s de media, p95 2,36 s.

## 6. Palancas operativas y de hardware (sobre la mejor configuración salvo indicación)
| Palanca | KPI | Δ | Implicación |
|---|---|---|---|
| Columnas online con ancho fijado por catálogo (H4, 455 mm) | 77,30 % (30 seeds) | +0,81 (réplica +1,38 [0,48; 2,29]) | exige conocer de antemano la familia dominante; **no implantable**: no hay catálogo previo (D-032); sin ese dato empeora (−2,6) |
| Garra de canto | 77,76 % | +0,70 | cambio de garra; ~1,6 s/decisión; no justifica la inversión |
| **Cinta con 18 cajas visibles (+3, D-037)** | **77,68 % (ref.); 77,74 % (30 seeds)** | +0,61 n.s. ref.; **réplica +1,57 [0,67; 2,46]** | **única palanca de la célula brazo confirmada**; ~1,23× tiempo por decisión: hay que optimizar para cumplir ≤ 1 s |
| Pulmón físico de 1–3 posiciones junto al palet (D-036/D-037) | 76,67 % (ref.) | −0,39 n.s.; réplica +0,12 n.s. | no compensa: reservar cajas estrechas no rellena el hueco trasero |
| Buffer ×2 / ×5 (medido sobre H1+H3+H5, sin réplica) | +0,87 / +0,53 n.s. | — | superado por la medición de la cinta de 18 sobre la oficial |
| Secuenciación aguas arriba | −0,81 n.s. | — | no compensa pedirla al almacén |
| Palets por familia (2 posiciones) | −0,74 n.s. | — | no compensa una segunda posición |
| Gantry 4 orient. (contraste) | 79,29 % | +2,23 | otra cinemática |
| Gantry 6 orient. (contraste) | 82,00 % | +4,94 | otra cinemática y garra |

La única palanca que supera T2 es cambiar de cinemática (gantry), que no es la célula objetivo. Con el brazo, la cinta de 18 visibles
recorta el gap a T2 de 2,4 a ~1,8 pp (77,7 %), sin alcanzarlo.

## 7. Riesgos y preguntas abiertas
- **D-015:** la regla de accesibilidad solo considera vecinas las cajas que se tocan. El responsable confirma que las cajas pueden ir
  pegadas (D-024); conviene validar con ROBOGUIDE las trayectorias junto a vecinas pegadas.
- **Laterales libres (D-024):** la configuración recomendada ya coloca el 94,7 % de las cajas en huecos con 3–4 laterales libres y
  ninguna en un hueco cerrado.

## 8. Recomendación de implantación (provisional)
1. Implantar **H1+H3+H5+H6 sin gate** (77,06 %, +15,2 pp sobre el baseline; 9,0 palets por flujo de 186 cajas frente a 11,1, un 19 %
   menos), validando con ROBOGUIDE las trayectorias junto a vecinas pegadas.
2. No invertir en garra de canto, en un pulmón junto al palet, en secuenciación aguas arriba ni en una segunda posición de palet: con
   la búsqueda no aportan. Sí merece estudio una cinta/visión con 18 cajas visibles (+1,57 pp confirmados), si se optimiza la búsqueda
   para mantener ≤ 1 s por decisión.
3. Lo que queda hasta T2 (~3,5 pp de pozos a media altura, RESEARCH R12) se crea con información que el robot aún no ve. Ver algunas cajas
   más sí ayuda (cinta de 18 visibles: +1,57 pp en la réplica, D-037), pero no basta para T2 y exige optimizar el tiempo; guardar
   cajas en un pulmón no ayuda (D-037), y tampoco una función de valor ligera entrenada con flujos sintéticos (R13). Con software y esta
   célula, 77,06 % es el límite práctico medido. Superar T2 exige otra cinemática (gantry: 82,0 % medido) o una investigación larga
   (RL / MCTS con valor entrenado a gran escala) sin garantía de resultado.
