# Informe de compactidad — paletizado caótico con robot de 6 ejes

> **Borrador en curso** (autopiloto, actualizado el 2026-10-20). Cada cifra tiene su comando de reproducción en
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
- **T2 no se alcanza** (faltan 2,4 pp). Desde el 77,06 % se han probado y descartado ocho líneas más (sección 5), con tres ciclos
  de pensamiento lateral: **77,06 % es el límite práctico medido con software para esta célula viendo 15 cajas** (D-035).
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
| Buffer ×2 / ×5 | +0,87 / +0,53 n.s. | — | no compensa más rampa |
| Secuenciación aguas arriba | −0,81 n.s. | — | no compensa pedirla al almacén |
| Palets por familia (2 posiciones) | −0,74 n.s. | — | no compensa una segunda posición |
| Gantry 4 orient. (contraste) | 79,29 % | +2,23 | otra cinemática |
| Gantry 6 orient. (contraste) | 82,00 % | +4,94 | otra cinemática y garra |

La única palanca que supera T2 es cambiar de cinemática (gantry), que no es la célula objetivo.

## 7. Riesgos y preguntas abiertas
- **D-015:** la regla de accesibilidad solo considera vecinas las cajas que se tocan. El responsable confirma que las cajas pueden ir
  pegadas (D-024); conviene validar con ROBOGUIDE las trayectorias junto a vecinas pegadas.
- **Laterales libres (D-024):** la configuración recomendada ya coloca el 94,7 % de las cajas en huecos con 3–4 laterales libres y
  ninguna en un hueco cerrado.

## 8. Recomendación de implantación (provisional)
1. Implantar **H1+H3+H5+H6 sin gate** (77,06 %, +15,2 pp sobre el baseline; 9,0 palets por flujo de 186 cajas frente a 11,1, un 19 %
   menos), validando con ROBOGUIDE las trayectorias junto a vecinas pegadas.
2. No invertir en garra de canto, en más buffer, en secuenciación aguas arriba ni en una segunda posición de palet: con la búsqueda no
   aportan.
3. Lo que queda hasta T2 (~3,5 pp de pozos a media altura, RESEARCH R12) se crea con información que el robot aún no ve. Ver más cajas
   no ayuda (buffer ×2/×5 n.s.), y tampoco una función de valor ligera entrenada con flujos sintéticos (R13). Con software y esta
   célula, 77,06 % es el límite práctico medido. Superar T2 exige otra cinemática (gantry: 82,0 % medido) o una investigación larga
   (RL / MCTS con valor entrenado a gran escala) sin garantía de resultado.
