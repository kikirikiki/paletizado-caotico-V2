Lee `docs/autopilot/specs/04_cotas_superiores.md` (contexto, entregables, contrato, verificación y seguridad).
Rama: feat/cotas-superiores (ya existe con la parte A; worktree /home/ingen/code/Paletizador/09_Software/paletizado-autopilot).

En ESTE trabajo implementa SOLO la parte **B (techo alcanzable offline)** y su sección en `docs/COTAS.md`.

Requisitos adicionales:
- Factibilidad con exactamente las reglas de producción de `arm6_4orient` (PalletModel + controles: soporte 0,85,
  accesibilidad 400, gate stand_hw, 4 orientaciones) y validación con `palca.study.validate` (0 violaciones).
- Lanza los cálculos largos en segundo plano dentro de tu sesión solo si puedes esperar a que terminen; el presupuesto
  total es de unas 4 h de pared con hasta 15 procesos. Guarda checkpoints en out/bounds/ para poder reanudar.
- Reporta para cada método: KPI oficial (palets cerrados), nº de palets totales, Σvol/(n_total·V), tiempo de cómputo,
  y compara con el baseline online (`out/sweeps/baseline_v1/summary.json`).
- Si el offline no supera al online, investiga por qué (p. ej. el scorer de producción como función de expansión es
  miope) y prueba un objetivo de búsqueda mejor antes de rendirte; documenta lo que probaste.
NO hagas commits; deja los cambios en el working tree. Devuelve: archivos cambiados, comandos y resultados, supuestos.
