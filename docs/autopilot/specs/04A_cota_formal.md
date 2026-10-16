Lee `docs/autopilot/specs/04_cotas_superiores.md` (contexto, entregables, contrato, verificación y seguridad).
Rama: feat/cotas-superiores (ya creada; worktree /home/ingen/code/Paletizador/09_Software/paletizado-autopilot).

En ESTE trabajo implementa SOLO la parte **A (cota superior formal)** y, en `docs/COTAS.md`, la sección
correspondiente (método, validez, comando de reproducción, resultado). No implementes B ni C.

Requisitos adicionales:
- La envolvente es 1240 × 840 × 2400 (`palca.study.metric.ENVELOPE_MM`; no escribas números a mano).
- Prueba la validez de cada DFF que uses con un test (función superaditiva / dual feasible en una rejilla de valores) y
  comprueba en tests que la cota nunca queda por debajo de soluciones factibles conocidas (patrones de RESEARCH.md §1:
  12 cajas planas, 18 cajas plana + de pie para 605×445×355; y un palet real del baseline leído de un dump).
- Reporta U* (utilización máxima demostrable de un palet) para el flujo completo y para la familia dominante, y el
  veredicto del moonshot frente a 1,5 × baseline (baseline provisional 61,9 %; si existe `out/sweeps/baseline_v1/summary.json`, usa su KPI).
- Si la cota DFF queda floja (> 95 %), intenta la cota por cortes horizontales y cualquier refuerzo que sea demostrable;
  documenta honestamente cuál es la mejor cota válida obtenida.
NO hagas commits; deja los cambios en el working tree. Devuelve: archivos cambiados, comandos y resultados, U* y supuestos.
