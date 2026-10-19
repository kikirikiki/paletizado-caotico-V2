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
