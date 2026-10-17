# Informe de compactidad — paletizado caótico con robot de 6 ejes

> **Borrador en curso** (autopiloto, actualizado el 2026-10-17). Cada cifra tiene su comando de reproducción en
> `docs/autopilot/PROGRESS.md`; las decisiones están en `docs/autopilot/DECISIONS.md`.

## 1. Resumen ejecutivo
- **Configuración objetivo:** robot articulado de 6 ejes, `arm6_4orient`. Son 4 orientaciones, sin apoyar la cara Alto × Largo, con
  accesibilidad de 400 mm, soporte ≥ 0,85, gate de cajas de pie a 400 mm del techo y `micro_plan=False`.
- **KPI congelado (v1.0.0):** volumen real de las cajas de los palets cerrados / (nº palets cerrados × 1240 × 840 × 2400 mm).
- **Baseline re-medido: 61,89 % ± 1,32.** Las cifras históricas (64,6 % brazo, 72,1 % gantry) usaban un volumen medio supuesto y una
  envolvente errónea (1240 × 820); además, el "stand_hl" de aquel estudio era un duplicado de "stand_hw".
- **Techo alcanzable offline (información completa): 81,45 %**, en 9 palets. Gap = 19,56 pp.
  Metas: **T1 ≥ 71,67 %**, **T2 ≥ 79,49 %**, **T3 ≥ 92,84 %**.
- **Mejor resultado online: 74,03 %** (H1+H3+H5+H6) → **62,1 % del gap → T1 alcanzado**, condicionado a la validez en producción
  de la búsqueda sobre el buffer (D-012).
- **T2 no se alcanza en `arm6_4orient`, ni con palancas.** El máximo medido con palancas en el brazo es 77,76 % (gate abierto + garra
  de canto). Con cinemática gantry y 6 orientaciones se llegaría al 82,0 %, pero no es la célula objetivo.
- **T3 (+50 %) no se alcanza.** Las cotas formales relajadas no lo descartan, y el techo alcanzable queda 11,4 pp por debajo
  (refutación empírica, D-010).

## 2. Datos y parámetros
Ver `docs/PARAMETROS.md`. 186 cajas, 111 referencias distintas, Σ vol = 16,79 m³ (mínimo trivial: 7 palets). Familia dominante
≈ 600–610 × 440–450 × 345–395 mm (85 cajas). Envolvente real: 1240 × 840 (palet 1200 × 800 + 20 mm de overhang por lado).

## 3. Techo real medido (Fase 0, `docs/COTAS.md`)
| Cota / referencia | Valor | Tipo |
|---|---|---|
| Baseline arm6_4orient | 61,89 % | medida |
| Buffer infinito (k = 186), política de producción | 66,82 % | palanca de información |
| Beam offline con el scorer de producción | 71,64 % | techo alcanzable |
| **Columnas offline con plantillas contiguas** | **81,45 %** (9 palets) | **techo alcanzable usado para el gap** |
| Cota formal por palet (DFF + cortes, relajada) | 100 % flujo / 96,97 % familia dominante | cota formal (no informativa) |
| Cota trivial | ≥ 6,72 → 7 palets | cota formal |

## 4. Diagnóstico: dónde se pierde el volumen
- **Baseline:** el 36 % de la envolvente queda libre por encima del perfil (torres), frente a solo un 2 % de huecos internos. El 18,7 %
  del suelo nunca se cubre. La regla de accesibilidad del brazo deja inaccesibles las zonas bajas junto a una pila más de 400 mm más alta.
- **Con la mejor técnica:** el espacio libre baja al 23,7 % y el suelo sin cubrir al 10,2 %. La franja delantera (y < 425 mm) queda
  prácticamente llena. La pérdida restante está en la **franja trasera de 395 mm** (840 − 445): ahí no caben las cajas planas de la
  familia dominante, solo cajas estrechas (escasas) o de pie, y el gate solo permite las de pie cerca del techo.

## 5. Ganancia por técnica (10 seeds de referencia; las adoptadas, con réplica cuando fue limítrofe)
| Técnica | Δ KPI (pp, IC95) | KPI acumulado | % del gap | Estado |
|---|---|---|---|---|
| H1 deuda de accesibilidad | +2,81 [1,04; 4,57] | 64,70 % | 14 % | adoptada |
| H3 búsqueda determinista sobre el buffer | +6,24 [5,11; 7,36] | 70,94 % | 46 % | adoptada (D-012) |
| H5 valor de estado por rollout | +1,11 (réplica +2,12 [1,08; 3,16]) | 72,04 % | 52 % | adoptada (D-012) |
| H6 rollouts con cajas futuras (prior genérico) | +1,99 [1,23; 2,75] (réplica +1,27) | **74,03 %** | **62 %** | adoptada (D-012) |
| H2 ranuras estrechas | −0,04 n.s. | — | — | descartada |
| H4 plantilla de columnas online | −0,62 n.s. | — | — | descartada |
| H7 candidatos extreme points / EMS | −0,53 n.s.; fuera de tiempo real | — | — | descartada |
| Re-ajuste de mandos existentes / gate solo | 0 / n.s. | — | — | descartadas |

Tiempo por decisión de la mejor configuración: ~0,72 s de media en un proceso (p95 ~1,5 s), dentro del límite de tiempo real (D-007).
Determinista: las ejecuciones repetidas son idénticas.

## 6. Palancas operativas y de hardware (sobre la mejor configuración salvo indicación)
| Palanca | KPI | Δ | Implicación |
|---|---|---|---|
| Quitar el gate de cajas de pie (2400) | 77,06 % | +3,03 [1,72; 4,35] | solo software; **depende de si el gate es físico (D-018)** |
| Gate abierto + garra de canto | 77,76 % | +3,73 | cambio de garra; tiempo por decisión ~1,6 s |
| Garra de canto (con el gate oficial) | 72,54 % (sobre H1+H3+H5) | +0,50 n.s. | no justifica cambio de garra |
| Buffer ×2 / ×5 | +0,87 / +0,53 n.s. | — | no compensa más rampa |
| Secuenciación aguas arriba | −0,81 n.s. | — | no compensa pedirla al almacén |
| N palets abiertos (política actual) | empeora | — | en estudio la asignación por familia |
| Gantry 4 orient. + gate abierto (contraste) | 79,29 % | +5,26 | otra cinemática |
| Gantry 6 orient. + gate abierto (contraste) | 82,00 % | +7,97 | otra cinemática y garra |

## 7. Riesgos y preguntas abiertas
- **D-012:** H3, H5 y H6 usan la maquinaria de `micro_plan`, fijada a `False` sin motivo documentado. Si el motivo era el tiempo o el no
  determinismo, la búsqueda determinista lo resuelve.
- **D-015:** la regla de accesibilidad solo considera vecinas las cajas que se tocan. Todas las políticas (también la de producción) y
  el techo offline dependen de ello: con una holgura de 50 mm serían infactibles el 30–40 % de las colocaciones online y todas las
  soluciones offline. Hace falta saber qué holgura lateral necesita la garra.
- **D-018:** si el gate de 400 mm no es físico, el KPI sube a 77,06 % solo con software.

## 8. Recomendación de implantación (provisional)
1. Implantar **H1+H3+H5+H6** (74,03 %, +12,1 pp sobre el baseline; 9,7 palets por flujo de 186 cajas frente a 11,1, un 13 % menos),
   tras confirmar D-012 y validar con
   ROBOGUIDE las trayectorias junto a vecinas casi pegadas (D-015).
2. Revisar con el fabricante el gate de 400 mm (D-018): es la mayor ganancia restante sin hardware.
3. No invertir en garra de canto, en más buffer ni en secuenciación aguas arriba: con la búsqueda, no aportan.
