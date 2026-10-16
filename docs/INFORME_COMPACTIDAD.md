# Informe de compactidad — paletizado caótico con robot de 6 ejes

> **BORRADOR EN CURSO** (autopiloto, 2026-10-16). Las secciones marcadas *pendiente* se completan a medida que avanzan las
> fases. Cifras con su comando de reproducción en `docs/autopilot/PROGRESS.md`.

## 1. Resumen ejecutivo (provisional)
- Configuración objetivo: robot articulado de 6 ejes, `arm6_4orient` (4 orientaciones sin apoyar la cara Alto × Largo,
  accesibilidad 400 mm, soporte ≥ 0,85, `micro_plan=False`).
- KPI congelado (v1.0.0): volumen real de las cajas de los palets cerrados / (nº palets cerrados × 1240 × 840 × 2400 mm).
- **Baseline re-medido: 61,89 % ± 1,32** (IC95 [60,95; 62,83]), 10 seeds de referencia. Las cifras históricas (64,6 % brazo,
  72,1 % gantry) usaban un volumen medio supuesto y un denominador erróneo (1240×820).
- **Mejor técnica adoptada hasta ahora: H1 "deuda de accesibilidad" → 64,70 % (+2,81 pp [1,04; 4,57])**, sin coste de tiempo.
- Palanca medida más potente: búsqueda sobre el buffer (`micro_plan`) → 70,90 % (+9,0 pp), pero hoy no es determinista y está
  fijada como no válida en producción; se está desarrollando una versión determinista (H3).
- Techo real: *pendiente* (techo offline clarividente). Las cotas formales relajadas no descartan el +50 %.

## 2. Datos y parámetros
Ver `docs/PARAMETROS.md`. 186 cajas, 111 referencias distintas, Σ vol = 16,79 m³ (mínimo 7 palets). Familia dominante
≈ 600–610 × 440–450 × 345–395 mm (85 cajas). Envolvente real 1240 × 840 (palet 1200 × 800 + 20 mm de overhang por lado).

## 3. Techo real medido (Fase 0)
| Cota / referencia | Valor | Tipo |
|---|---|---|
| Baseline arm6_4orient | 61,89 % | medida |
| Buffer infinito (k=186) con la política de producción | 66,82 % (+4,93 pp) | palanca de información |
| Techo offline clarividente | *pendiente* | cota empírica alcanzable |
| Cota formal por palet (DFF + cortes, relajada) | 100 % flujo / 96,97 % familia dominante | cota formal (no informativa) |
| Cota trivial de palets | ≥ 6,72 → 7 palets | cota formal |

Metas T1/T2/T3: *pendiente de la cota offline*.

## 4. Diagnóstico: dónde se pierde el volumen
- 36 % de la envolvente queda libre **sobre** el perfil final (torres); solo el 2 % son huecos internos.
- 18,7 % del suelo nunca se cubre. La regla de accesibilidad del brazo (no colocar junto a una caja > 400 mm más alta que la
  base) deja zonas bajas inaccesibles para siempre.
- Con H1, el suelo vacío se concentra en un valle central en x: ranuras de < 355 mm entre columnas (1240 − 2 × 445 = 350)
  que ninguna caja de pie de la familia dominante puede ocupar.
- La caja dominante encaja mal en 1240 × 840 con 4 orientaciones (patrón ideal ≈ 69 %); de canto (prohibido) encaja 2 × 2 (≈ 77 %).

## 5. Ganancia por técnica
| Técnica | Δ KPI (pp, IC95) | % del gap | Estado |
|---|---|---|---|
| H1 deuda de accesibilidad | +2,81 [1,04; 4,57] | *pendiente* | adoptada |
| Re-ajuste de mandos existentes | 0 | 0 | descartada |
| Gate stand_hw | n.s. | 0 | descartada |
| H3 búsqueda determinista | *en curso* | | |
| H2 ranuras estrechas | *pendiente* | | |

## 6. Palancas operativas (implicación de hardware)
| Palanca | Δ KPI | Implicación |
|---|---|---|
| Buffer ×2 / ×5 / ∞ (sin H1) | +1,13 / +3,04 / +4,93 pp | más longitud de rampa o almacén intermedio |
| Buffer ×2 / ×5 (con H1) | +0,71 n.s. / +2,79 [1,02; 4,57] pp sobre H1 | ídem; con H1 el ×2 deja de ser significativo |
| N palets abiertos 2–4 (política actual, sin H1) | −2,0 a −6,0 pp | más posiciones de palet; requiere política de asignación propia |
| N = 2 con H1 | −0,93 pp n.s. sobre H1 | ídem |
| micro_plan (búsqueda por tiempo) | +9,0 pp con H1 | solo software; hay que validar por qué estaba prohibido |
| Garra que permita canto (H×L) | *pendiente* | cambio de garra |
| Gantry (sin regla de accesibilidad) | +5,0 pp (sin H1) | cambio de cinemática |

## 7. Técnicas descartadas y por qué
*pendiente de completar al cierre*.

## 8. Nivel alcanzado y recomendación de implantación
*pendiente*.
