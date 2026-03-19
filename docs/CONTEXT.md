# Estado del proyecto — Paletizado Caótico

## Fecha: Marzo 2026

## Decisión arquitectural
Se ha decidido rediseñar el scheduler desde cero con enfoque de zonas sincronizadas.
El sistema actual (heightfield + min_height_slack_then_gain) no puede construir torres
en paralelo por limitaciones estructurales del algoritmo greedy.

## Conclusiones del estudio
- Baseline actual: 21.4 cajas/palet (89.2% del target de 24)
- El gap residual NO es de scorer ni de parámetros — es arquitectural
- Simulación manual por zonas confirma: 24 cajas con rango final 245mm entre torres ES alcanzable
- Condición: scheduler por zonas + buffer mínimo de 8 cajas + cajas pequeñas disponibles para zonas B/C

## Geometría del palet (crítica para el rediseño)
- Palet: 1200x800mm, overhang 20mm
- 4 zonas naturales dictadas por la caja dominante (605x445mm):
  - Zona A: x=[-20..585], y=[-20..430] → 605x450mm
  - Zona B: x=[585..1215], y=[-20..335] → 630x355mm (requiere cajas ≤355mm de ancho)
  - Zona C: x=[-20..615], y=[430..780] → 635x350mm (requiere cajas ≤350mm de ancho)
  - Zona D: x=[615..1220], y=[335..785] → 605x450mm
- Zonas A y D: compatibles con 69.9% del flujo
- Zonas B y C: compatibles con solo 7.5% del flujo (cajas ≤355mm de ancho)
- 29% de cajas no caben en ninguna zona sin rotación

## Arquitectura del nuevo sistema (a implementar en nueva rama/repo)
Scheduler basado en zonas con restricción de sincronía de altura:
1. Zonas fijas definidas en configuración
2. En cada step: elegir zona de menor altura dentro de delta configurable
3. Buscar en buffer la mejor caja para esa zona
4. Solo avanzar franja cuando todas las zonas están dentro del delta
5. Fallback: si ninguna zona tiene candidato, cerrar palet

## Parámetros clave validados
- delta_max_mm: 400 (diferencia máxima tolerable entre zonas)
- buffer_size: 8 cajas mínimo
- max_height_mm: 2400mm
- Robot: FANUC R2000i (tentativo), acceso cenital, altura máxima 3000mm

## Trabajo completado en esta rama (no tirar)
- CoherenciaCapaScorer: implementado, descartado como scorer único (-22% vs baseline)
- RobotAccessibilityControl: implementado, válido como filtro de feasibility
- Infraestructura de benchmark: configs/benchmarks/one_pallet_canonical.json
- PYTHONPATH correcto: src/ del repo actual, NO /home/ingen/code/Paletizador/09_Software/src

## Próximo paso
Nuevo chat, nueva rama o repo, arquitectura de scheduler por zonas desde cero.
