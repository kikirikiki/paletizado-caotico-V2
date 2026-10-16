Lee `docs/autopilot/specs/04_cotas_superiores.md` (contexto, entregables, contrato, verificación y seguridad).
Rama: feat/cotas-superiores (ya existe con las partes A y B; worktree /home/ingen/code/Paletizador/09_Software/paletizado-autopilot).

En ESTE trabajo implementa SOLO la parte **C (buffer/lookahead k y nº de palets abiertos N)**, ejecuta los dos sweeps
(uno detrás de otro) y completa `docs/COTAS.md` con la parte C y la **tabla de headroom** (D) con todas las cifras reales
de A, B y C leídas de out/.

Requisitos adicionales:
- `open_pallets=1` y `k=15` deben reproducir EXACTAMENTE el baseline (mismo KPI por seed): test de regresión.
- Con N > 1 la política elige (caja, palet, colocación) con el scorer de producción; documenta la regla de cierre.
  Es una palanca operativa (más posiciones de palet en la célula): etiquétala así.
- Reporta para cada valor: KPI ± IC95, palets, cajas no colocadas, ms/decisión (mean, p95).
NO hagas commits; deja los cambios en el working tree. Devuelve: archivos cambiados, comandos y resultados, la tabla de headroom.
