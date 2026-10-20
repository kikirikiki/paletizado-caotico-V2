# Simulador 3D paso a paso (sim3d)

Visor HTML autocontenido (Three.js r147 incrustado, sin servidor) para ver cómo se
forma cada palet caja a caja y comprobar su viabilidad física.

## 1. Generar la secuencia y el visor (WSL, desde la raíz del repo)

```bash
export PYTHONPATH=$(pwd)/src
# Configuración oficial h6_sin_gate, seed 50021 (por defecto), con verificación de bit-identidad
.venv/bin/python scripts/export_sim_trace.py --verify --html out/sim3d/sim3d_h6_sin_gate_50021.html

# Comparar baseline y oficial en varias seeds (un solo HTML con selector de ejecución)
.venv/bin/python scripts/export_sim_trace.py --run arm6_4orient --run h6_sin_gate --seeds 50021,1,42 \
    --html out/sim3d/comparativa.html

# Caso sintético con defectos a propósito (+ palet sano de 150 cajas para medir fluidez)
.venv/bin/python scripts/export_sim_trace.py --no-sim --synthetic --stress 150 --html out/sim3d/sintetico.html

# Rehacer el HTML (o juntar varios JSON) sin volver a simular
.venv/bin/python scripts/export_sim_trace.py --from-json out/sim3d/a.json --from-json out/sim3d/b.json \
    --html out/sim3d/juntos.html
```

Cada ejecución tarda unos 3 min (≈1 s por decisión); `--verify` la repite sin captura.
`--reference <kpi.json>` compara además con el `kpi.json` de un sweep oficial.

El exportador reutiliza `make_task`/`run_task` de `scripts/run_sweep.py` (mismos
parámetros, seeds y KPI congelado) y captura los datos envolviendo, solo durante la
ejecución y en su propio proceso, `choose_action`, `commit_plan` y `on_changeover_start`
de `PolicyPackerScheduler`. No modifica `src/` ni el lock de los sweeps.

## 2. Abrir el visor en Windows

Copia el `.html` a Windows y ábrelo con doble clic (Chrome o Edge):

```bash
cp out/sim3d/sim3d_h6_sin_gate_50021.html /mnt/c/Users/ingen/Documents/JSP/01_Paletizador/03_Software/simulador/
```

`tools/sim3d/sim3d.html` también abre directamente: carga un JSON con «Abrir JSON…» o
arrastrándolo a la ventana.

## Controles

- **▶ / →** siguiente caja (baja desde 300 mm sobre el palet hasta su posición exacta);
  **◀ / ←** caja anterior; **⏮ ⏭** (Inicio/Fin) inicio y final del palet; **espacio** play/pausa.
- Barra deslizante y «Ir al paso» para saltar. El estado depende solo del índice de paso;
  la animación es visual y se completa al instante si se pulsa otra vez.
- Ratón: orbitar (izq.), pan (der.), zoom (rueda). Botones de cámara arriba a la izquierda.
- Pasar o hacer clic sobre una caja muestra su ficha.
- «Bajada con aproximación»: la caja baja desplazada 20 mm hacia su lateral libre con más
  holgura y al llegar a su z se desliza a la posición final.

## Robot FANUC R-2000iC/165F (estudio de alcance y ciclo animado)

Resultados y supuestos: [docs/ESTUDIO_ROBOT_R2000.md](../../docs/ESTUDIO_ROBOT_R2000.md).

```bash
# 1) Mallas por eslabón desde el STEP (venv aparte con OpenCascade; el STEP y el JSON no se suben)
python3 -m venv ~/.cache/sim3d-cad-venv
~/.cache/sim3d-cad-venv/bin/pip install cadquery-ocp numpy fast-simplification
~/.cache/sim3d-cad-venv/bin/python scripts/step_to_robot_mesh.py robot.stp out/sim3d/robot_r2000ic165f.json

# 2) Trazas de las 10 seeds de referencia (una por seed, ~3 min cada una)
for s in 50021 1 42 100 777 5678 9999 12345 99999 314159; do
  .venv/bin/python scripts/export_sim_trace.py --run h6_sin_gate --seeds $s --out out/sim3d/seeds/h6_sin_gate_$s.json
done

# 3) Barrido de posición/pedestal/cinta (~5 min) y celda elegida con ciclos para el visor
.venv/bin/python scripts/robot_reach_study.py out/sim3d/seeds/*.json --mesh out/sim3d/robot_r2000ic165f.json \
    --out out/sim3d/robot_study --top-per-side 25 --workers 14 --peds 0,200,400,600,800,1000,1200,1400
.venv/bin/python scripts/robot_reach_study.py out/sim3d/seeds/*.json --mesh out/sim3d/robot_r2000ic165f.json \
    --out out/sim3d/robot_cell --cell N,700,1000,900
.venv/bin/python scripts/robot_study_report.py out/sim3d/robot_study/study.json out/sim3d/robot_cell/study.json \
    --traces out/sim3d/seeds/*.json --out docs/ESTUDIO_ROBOT_R2000.md

# 4) Visor con robot, pedestal, cinta y ciclo completo
.venv/bin/python scripts/export_sim_trace.py --from-json out/sim3d/seeds/h6_sin_gate_50021.json \
    --robot-study out/sim3d/robot_cell/study.json --robot-mesh out/sim3d/robot_r2000ic165f.json \
    --html out/sim3d/sim3d_robot_50021.html
```

En el visor, con el robot activo, «Siguiente caja» anima el ciclo real: la cinta lleva la caja elegida
a la estación, el robot la coge (las de canto por la cara H×W, en horizontal, y la reorienta), entra
librando el pasillo, baja desplazada 20 mm al lateral libre, se desliza a su sitio, suelta y se retira.
Los fallos del robot (alcance, límites, colisión de J2/J3/J5, garra o caja transportada) entran en la
lista de avisos y el eslabón que choca se pinta en rojo en la pose que falla.

## Coordenadas (verificadas en `pallet_model.py`, `validate.py` y `PalletSpec`)

mm; origen en la esquina del palet nominal 1200×800; z vertical, z=0 es la cara superior
del palet; `x,y,z` es la esquina mínima; `dx,dy,dz` son las dimensiones ya orientadas.
Envolvente x∈[−20,1220], y∈[−20,820], z∈[0,2400].

## Comprobaciones de viabilidad (calculadas en el visor, `checks.js`)

Cada caja se evalúa contra las anteriores de su palet, es decir, con el estado que el robot
encuentra al colocarla.

| Código | Gravedad | Regla |
|---|---|---|
| Solape | error | intersección > tolerancia (1 mm) en x, y y z |
| Fuera de envolvente | error | fuera de la envolvente ± tolerancia |
| Flotando | error | z > 0 y ninguna cara superior debajo (z ± 1 mm) |
| Bajada bloqueada | error | hay una caja ya colocada encima de su huella |
| Apoyo insuficiente | aviso | % de base apoyada < umbral (70 %) |
| Accesibilidad | aviso | `RobotAccessibilityControl`: vecina que toca con cara superior > altura local + 400 mm |
| Torre | aviso | altura exenta de la columna / lado menor de su base > 3; una vecina a menos de 50 mm arriostra |
| 0 lados libres | aviso | hueco cerrado (`free_sides` = 0, contigüidad < 20 mm): bajada 100 % cenital |

Los umbrales se cambian en el panel. Pruebas: `node tools/sim3d/test_checks.js <json con --synthetic>`
(también lo ejecuta `tests/test_export_sim_trace.py`).
