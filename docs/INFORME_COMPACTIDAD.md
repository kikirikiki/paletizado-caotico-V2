# Informe de compactidad — paletizado caótico con robot de 6 ejes

> **Versión final — estudio cerrado el 2026-10-25** (D-045). Cada cifra tiene su comando de reproducción en
> `docs/autopilot/PROGRESS.md`; las decisiones están en `docs/autopilot/DECISIONS.md`.

## 1. Resumen ejecutivo
- **Configuración objetivo:** robot articulado de 6 ejes, `arm6_4orient`: 4 orientaciones (nunca apoyado en la cara Alto × Largo),
  accesibilidad de 400 mm (con la excepción R7) y soporte ≥ 0,85. El robot solo ve las 15 cajas de la cinta (D-022), sin catálogo
  previo (D-032); puede recordar las cajas que ya han pasado (D-043).
- **KPI congelado (v1.0.0):** volumen real de las cajas de los palets cerrados / (nº palets cerrados × 1240 × 840 × 2400 mm).
- **Baseline re-medido: 61,89 % ± 1,32.** Techo alcanzable offline (información completa, plantillas a medida del flujo): **81,45 %**.
  Metas: **T1 ≥ 71,67 %**, **T2 ≥ 79,49 %**, **T3 ≥ 92,84 %**.
- **Resultado final de software (oficial, D-041): "pensar más"** = H1+H3+H5+H6 sin gate de cajas de pie, con la búsqueda ampliada
  a 150 nodos y 4 hojas: **77,74 % en las 10 seeds de referencia y 77,82 % en la réplica de 20 seeds nuevas** (77,79 % en 30 seeds).
  **+15,9 pp sobre el baseline (≈ 81 % del gap); T1 alcanzado.** Tiempo real: 1,79 s/decisión de media, p95 4,12 s, dentro del
  límite fijado por el usuario (media ≤ 3 s, p95 ≤ 7 s, D-040; con un PC más potente se podrá bajar). Determinista, 0 cortes,
  test de fuga superado.
- **Mejor palanca de hardware para el brazo (D-044): garra que pueda colocar cajas en huecos más profundos.** Ampliando el acceso
  de 400 a **800 mm: 78,89 % ref. / 79,15 % réplica (+1,33 pp significativo), 79,06 % en 30 seeds**; con 600 mm, +0,74 pp
  (78,64 % en 30 seeds). Ataca la causa principal de pérdida (pozos a media altura).
- **T2 no se alcanza con el brazo:** el mejor resultado en referencia es 78,89 % (garra de 800 mm), a 0,6 pp. Solo otra cinemática
  la supera (gantry de 6 orientaciones: 82,0 % medido). **T3 no se alcanza** (refutación empírica, D-010).
- **Investigación larga con GPU (D-039–D-042), sin ganancia adicional:** una red de valor entrenada con 716 k estados sintéticos
  (R² 0,59) y una red de política/poda (92 % de acierto entre sus 6 mejores) no mejoran las decisiones; ni siquiera conocer el
  futuro real dentro de la búsqueda (+0,8 n.s.). El límite está en cómo valora la búsqueda la continuación de cada opción.
  Imitar al planificador offline tampoco es viable: en flujos sintéticos rinde 6,9 pp menos que el robot, porque sus plantillas
  están hechas para el flujo de referencia. La mejora adoptada vino de dar más cálculo a la búsqueda.

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
| **"Pensar más": búsqueda con 150 nodos y 4 hojas (D-041)** | ref. +0,67 n.s.; **réplica +1,61 [0,81; 2,40]** | **77,74 % (réplica 77,82 %)** | **81 %** | **adoptada (oficial final)** |
| Más futuros por hoja (2/4 muestras, 12 cajas) | +0,49 / −0,67 / −0,91 n.s. (ajuste) | — | — | descartada |
| Red de valor CNN en GPU (G1, D-039) | sola −3,55 (sig.); mezclada −0,05 n.s. (ajuste) | — | — | descartada |
| Red de política / poda (G3, D-039) | −0,15…−1,75 n.s. (ajuste) | — | — | descartada |
| Imitar al planificador offline (G6, D-042) | profesor −6,85 pp en flujos sintéticos | — | — | descartada sin implementar |
| Columnas con memoria de cajas vistas (D-043/D-044) | — | — | — | descartada sin implementar (plantilla H4 = medidas de la familia de referencia) |

La ventaja de la búsqueda solo usa las 15 cajas visibles (test de fuga, D-022). Es determinista: todas las cifras de evaluación se
miden sin cortes de reloj (D-028). Tiempo por decisión de la oficial final, en un proceso: 1,79 s de media, p95 4,12 s (la anterior,
57 nodos: 0,957 s / 2,36 s).

## 6. Palancas operativas y de hardware (sobre la oficial de cada momento)
| Palanca | KPI | Δ | Implicación |
|---|---|---|---|
| **Garra con acceso a huecos de hasta 800 mm (D-044)** | **78,89 % ref. / 79,15 % réplica; 79,06 % (30 seeds)** | **réplica +1,33 [0,79; 1,87]** | **la palanca más clara para el brazo**; requiere una herramienta que baje a pozos profundos sin colisionar |
| Garra con acceso a 600 mm (D-044) | 78,81 % ref. / 78,56 % réplica | réplica +0,74 [0,10; 1,38] | versión intermedia, también confirmada |
| Regla de apoyo 0,75 / 0,70 (en lugar de 0,85) | 77,70 % / 78,01 % (ajuste) | +0,00 / +0,31 n.s. | no compensa relajar la estabilidad |
| Cinta con 18 cajas visibles (D-037) | 77,74 % (30 seeds, sobre la oficial anterior) | réplica +1,57 [0,67; 2,46] | **no es posible físicamente** (usuario, D-040) |
| Columnas con ancho fijado por catálogo (H4, 455 mm) | 77,30 % (30 seeds) | réplica +1,38 | no implantable: no hay catálogo previo (D-032) |
| Garra de canto | 77,76 % | +0,70 | no justifica la inversión |
| Pulmón físico de 1–3 posiciones (D-036/D-037) | 76,67 % (ref.) | réplica +0,12 n.s. | no compensa |
| Secuenciación aguas arriba / palets por familia | −0,81 / −0,74 n.s. | — | no compensan |
| Gantry 4 orient. (contraste) | 79,29 % | +2,23 | otra cinemática |
| Gantry 6 orient. (contraste) | 82,00 % | +4,94 | otra cinemática y garra |

Con el brazo, la garra de acceso profundo deja el resultado a 0,6 pp de T2 en referencia; solo la gantry supera T2.

## 7. Riesgos y preguntas abiertas
- **D-015:** la regla de accesibilidad solo considera vecinas las cajas que se tocan. El responsable confirma que las cajas pueden ir
  pegadas (D-024); conviene validar con ROBOGUIDE las trayectorias junto a vecinas pegadas, y con la garra de acceso profundo, las
  bajadas a pozos de 600–800 mm.
- **Laterales libres (D-024):** la configuración recomendada coloca casi todas las cajas en huecos con 3–4 laterales libres.
- **Tiempo de cálculo:** la oficial necesita ~1,8 s/decisión (p95 4,1 s) en esta workstation; el ciclo del robot es de 14 s. Un PC
  más potente permitiría bajarlo; más cálculo por sí solo no da más compactación (400 nodos no mejoran).
- **GPU:** la Quadro RTX 4000 bajo WSL se queda en estado de reposo (P8) aunque trabaje al 100 % (~1,4 TFLOPS fp32), pese al modo de
  máximo rendimiento, el reloj fijado y un reinicio. No afecta a la implantación (la oficial no usa redes ni GPU).

## 8. Recomendación de implantación (final)
1. Implantar la oficial **"pensar más"** (H1+H3+H5+H6 sin gate, 150 nodos y 4 hojas): **77,8 %** frente al 61,9 % del baseline, unos
   8–9 palets por flujo de 186 cajas en lugar de 11, con 1,8 s de cálculo por decisión. Validar con ROBOGUIDE las trayectorias junto
   a vecinas pegadas.
2. **Estudiar una garra o herramienta que coloque cajas en huecos de hasta 600–800 mm de profundidad** respecto a sus vecinas:
   +0,7 a +1,3 pp confirmados (hasta ~79,1 %). Es la única mejora física del brazo que ha salido significativa en la réplica.
3. No invertir en: pulmón junto al palet, segunda posición de palet, secuenciación aguas arriba, garra de canto, relajar la regla de
   apoyo ni, por ahora, en redes neuronales o GPU para la célula: ninguna aporta.
4. Para superar T2 hace falta otra cinemática (gantry: 82,0 % medido).

