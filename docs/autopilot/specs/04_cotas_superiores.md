<task>
Rama: feat/cotas-superiores (ya creada desde feat/sweep-paralelo; worktree
/home/ingen/code/Paletizador/09_Software/paletizado-autopilot). Objetivo único: medir el techo real de la
utilización volumétrica sobre el flujo canónico (186 cajas) en `arm6_4orient`, con cotas de tres tipos:
(A) cota superior FORMAL (demostrable), (B) techo ALCANZABLE offline (mejor solución encontrada con información
completa y secuencia reordenable), (C) palancas de información: buffer/lookahead k y nº de palets abiertos N.
Contexto: KPI y validador congelados en `palca.study.metric` / `palca.study.validate` (no los modifiques);
parámetros en `palca.study.params`; sweeps con `scripts/run_sweep.py`. OR-Tools 9.15 está instalado.
Restricciones físicas de arm6_4orient: 4 orientaciones (sin cara H×L), min_support 0,85, envolvente
1240×840×2400, accesibilidad del brazo delta 400 mm (`RobotAccessibilityControl`).
</task>

<deliverables>
A. Cota superior formal — `src/palca/study/bounds_formal.py` + `scripts/bound_formal.py`:
   1. Cota trivial: Σ vol / V_envolvente (nº mínimo de palets, 6,88) — referencia absoluta.
   2. Cota superior de la utilización de UN palet (vale para cualquier palet y por tanto para el KPI medio):
      max Σ vol_i·x_i, x_i ∈ [0,1], sujeto a, para cada terna de Dual Feasible Functions (Fekete & Schepers:
      u^(k), U^(ε), φ^(ε), identidad) aplicada a los ejes x(1240), y(840), z(2400):
      Σ_i min_{o ∈ orientaciones permitidas de i} f_x(o.l)·f_y(o.w)·f_z(o.h) · x_i ≤ 1240·840·2400 (normalizado).
      Resuélvelo como LP (GLOP o CP-SAT) con un conjunto amplio de DFF (k = 1..20, ε en una rejilla). Documenta
      por qué es una cota válida (relaja soporte y accesibilidad, lo que la hace más alta, nunca más baja).
      Añade la cota por cortes horizontales si es más fuerte: en cada altura z, el área de las huellas que cortan z
      ≤ máx. área cubrible en 1240×840 con huellas permitidas (calcula ese máximo exacto con CP-SAT por familias).
   3. Valida la cota con tests: en instancias pequeñas donde el óptimo se conoce (cajas idénticas que llenan
      exactamente, y la caja 605×445×355 donde la cota debe quedar ≥ las configuraciones de patrón conocidas:
      12 cajas solo planas, 18 cajas plana+de pie), la cota nunca está por debajo de una solución factible.
   Salida: `out/bounds/formal.json` con cada cota, la utilización máxima U* y el veredicto sobre el moonshot
   (¿U* < 1,5 × baseline?).
B. Techo alcanzable offline — `src/palca/study/offline_search.py` + `scripts/bound_offline.py`:
   - Información completa: las 186 cajas disponibles desde el inicio, orden de colocación libre, mismas
     restricciones físicas (usa `PalletModel` del código con el preset arm6_4orient para la factibilidad, para que las
     reglas sean idénticas a las de producción) y validador congelado sobre el resultado.
   - Al menos dos métodos y quedarse con el mejor: (1) beam search palet a palet (estado = palet parcial + cajas
     restantes, expansión = (caja, colocación) top-M por el scorer de producción, ancho ≥ 16, objetivo = volumen del
     palet al cerrarse); (2) búsqueda sobre el orden de entrada (GA o búsqueda local con reinicios) usando como
     decodificador la política de producción con buffer = todo el flujo restante. Presupuesto: hasta 2 h de CPU por
     método usando hasta 15 procesos. Las cotas offline no necesitan tiempo real.
   - KPI con la métrica congelada (palets cerrados = todos salvo el último) y además nº total de palets y
     Σvol/(n_total·V) (porque con reordenación libre el último palet puede recibir las cajas peores: reporta ambos).
   Salida: `out/bounds/offline_<metodo>.json` con KPI, palets, violaciones (deben ser 0) y la secuencia.
C. Palancas de información (configs YAML para `run_sweep.py`, 10 seeds, role=bound):
   - `configs/sweeps/bound_buffer_k.yaml`: k (ramp_cap y lookahead_k a la vez) ∈ {15 (físico), 30, 75, 186 (∞)}.
     Amplía `SUPPORTED_LOOKAHEAD_K` de forma aditiva si hace falta (hoy (1, 3, 5, 10, 15)); comprueba que con
     k=15 los resultados son idénticos a la línea base.
   - Nº de palets abiertos N ∈ {1, 2, 3, 4}: el DES actual abre un palet por destino. Implementa la variante
     mínima que permita N palets abiertos para el mismo flujo (la política elige caja Y palet con el scorer de producción;
     un palet se cierra cuando ninguna caja visible cabe en él), detrás de un parámetro nuevo `open_pallets` (por
     defecto 1 = comportamiento idéntico, demuéstralo con un test de regresión que compare KPI y palets con la línea base).
     `configs/sweeps/bound_open_pallets.yaml`.
   Ejecuta ambos sweeps (uno detrás de otro, nunca a la vez) y la parte A y B.
D. `docs/COTAS.md`: método de cada cota, validez, comandos exactos de reproducción, y la **tabla de headroom**:
   baseline arm6_4orient | cota formal U* | offline alcanzable | buffer k | N palets | cota trivial,
   con KPI medio ± IC95, gap baseline→offline en pp y qué parte del gap explica cada palanca.
</deliverables>

<completeness_contract>
Hecho = tests nuevos en verde; smoke OK; existen out/bounds/formal.json, out/bounds/offline_*.json (0 violaciones),
summary.md de los dos sweeps; docs/COTAS.md con la tabla rellena con cifras reales y sus comandos.
</completeness_contract>

<verification_loop>
`export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh` + tests nuevos. Si algo falla, corrige y repite.
Si una cota formal queda POR DEBAJO de una solución factible conocida, hay un error: no lo ocultes, arréglalo.
</verification_loop>

<action_safety>
Solo los archivos necesarios. Cambios en el simulador solo aditivos y detrás de parámetros con valor por defecto
que reproduce exactamente el comportamiento actual. No modifiques data/*.xlsx ni la métrica/validador congelados.
Nunca dos sweeps de 15 workers a la vez. No commitees out/ (ni outputs/, logs/, results/, .venv, __pycache__/);
git add por ruta explícita. NO hagas commits (el sandbox no puede escribir en .git): deja los cambios en el working tree; el director revisa y commitea. No hagas push ni abras PR.
</action_safety>

<compact_output_contract>
Devuelve: archivos cambiados, comandos ejecutados con resultado, supuestos, la tabla de headroom
y cualquier resultado sorprendente (p. ej. offline peor que online, o cota formal más baja de lo esperado).
</compact_output_contract>
