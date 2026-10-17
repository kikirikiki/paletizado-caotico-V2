# PALANCA: dos posiciones de palet por familia (D-019 R1, fase 1)

## Método y alcance

Se contrasta únicamente la asignación por familia con política greedy. Referencia
de esta fase: `arm6_4orient` + H1, N=1; no es una comparación contra H3–H6.
Las seis configuraciones están en
[`lever_family_pallets.yaml`](../../configs/sweeps/lever_family_pallets.yaml):
N=1, N=2 `score` y N=2 `family`, con H1 y sin H1. H1 usa
`access_debt_weight=0.25`, `access_debt_band_mm=600`, `access_debt_mode=penalty`.
El resto conserva el preset: ventana/buffer 15, `planar+stand_hw`, accesibilidad
400 mm, gate de pie 400 mm, estabilidad 0,85, overhang 20 mm, scorer
`min_height_slack_then_gain` (slack 120 mm), presupuesto 900 ms y cambio de palet
60 s. No se activa micro_plan ni online_controller.

Se usa el Excel de referencia `data/Flujo rampas - Editado.xlsx` (186 cajas),
con las diez seeds de referencia, en el mismo orden:
`50021, 1, 42, 100, 777, 5678, 9999, 12345, 99999, 314159`.
Cada variante usa exactamente el mismo flujo por seed; no se reordena por
familia ni se aumenta la información visible. Comparaciones pareadas contra
`h1_n1`; IC95 bilateral t de Student con unidad estadística = seed.

## Regla operativa

`open_pallets_assignment: str = "score"` conserva la selección conjunta actual
por caja y palet. Con `open_pallets=1`, `family` no tiene efecto.
Con N=2 y `family`, por destino:

- A = posición `dest:1`: familia dominante, con dimensiones **originales**
  L∈[600,610], W∈[440,450], H∈[345,395] mm, límites inclusivos.
- B = posición `dest:2`: todas las demás cajas. La clasificación no depende de
  la orientación elegida para colocarla.
- Para cada caja visible se evalúa primero su posición preferida. Si hay una
  colocación factible, solo esa posición entra en el scorer, aunque la otra
  tenga mejor altura o score. H1 puntúa/filtra las propuestas admitidas.
- Si no hay colocación factible en su posición preferida, se permite competir
  en la otra: desbordamiento en ambos sentidos, para cualquier motivo de
  inviabilidad del packer. Una posición bloqueada o en cambio de palet no
  está disponible y también permite desbordamiento. No hay desbordamiento por
  tener simplemente peor score o deuda de acceso.

Se comprueba también la factibilidad geométrica en la posición alternativa,
aunque su propuesta quede excluida por asignación. Así se conserva el cierre
existente: solo cierra un palet no vacío cuando **ninguna caja de la ventana
visible cabe en él**, sin inferir cierre desde una evaluación truncada.
No se cierra por ausencia de su familia en la ventana. Al cerrar, la posición
conserva su papel A/B en la siguiente generación. Con N>2 se rechaza `family`;
la ruta `score` sigue admitiendo los valores anteriores.

## Métricas y comprobación de las colas

El KPI oficial congelado v1.0.0 usa solo palets cerrados y excluye `END`.
No se cambia ni la métrica ni el validador. Se informa además, por seed:

`U_flujo = Σ volumen de todas las cajas colocadas / (n_palets_físicos · V)`

con `V = 1240 · 840 · 2400 mm³` (envolvente efectiva con overhang) y todos los
palets no vacíos, incluido `END`. Es el campo existente `kpi_all_pallets`.
La tabla usa la media por seed; el cociente agrupado sobre las diez seeds se
informa por separado para evitar confundir media de cocientes con cociente
de sumas. El número de palets incluye ambas posiciones y sus generaciones.

La finalización existente de N>1 ordena los palets activos por último uso:
los anteriores se registran como `DRAIN` (cuentan en el KPI de cerrados) y
solo el último como `END` (excluido). Por eso separar familias puede alterar
qué cola queda fuera del KPI aunque el consumo total de palets no mejore.
La identidad por seed permite comprobarlo sin modificar la métrica:

`KPI_cerrados = (n · U_flujo − U_END) / (n − 1)`.

Los artefactos del sweep incluyen `run.json`, `dump.json`, `kpi.json`,
`manifest.json`, `summary.json` y `summary.md`. El análisis complementario
reconstruye todos los palets con `split_pallets`, comprueba el volumen,
los conteos, los hashes del runner y la identidad anterior, y guarda
`family_audit.json` con colas, asignaciones y comparaciones de flujo.

## Resultados (2026-10-17)

60/60 ejecuciones completas: 186 cajas por ejecución (11.160 colocaciones),
**0 violaciones físicas, 0 cajas sin colocar y 0 cortes por deadline**.
Los diagnósticos de robustez D-015 con gaps 5/20/50 se conservan en el
resumen; no son violaciones de la regla de accesibilidad vigente.

Código local SHA256: `5bdcbc82579ac85056de34d0d1a7ee19b69594ebdcd1e7a90e15b64f5df97060`; HEAD de partida
`774441c7c94ac5515d36bef145a95335d4c62b1e`. Los cambios locales están sin commit.

Todas las diferencias e intervalos siguientes están pareados contra H1 N=1
(diez pares), salvo donde se indica expresamente `family − score`.

| Variante | KPI cerrados % | Δ KPI pp [IC95] | U_flujo % | Δ U_flujo pp [IC95] | Palets media (total 10 seeds) |
|---|---:|---|---:|---|---:|
| H1 N=1 (referencia) | 64.697 | — | 61.665 | — | 10.900 (109) |
| H1 N=2 score | 63.763 | -0.934 [-3.234; 1.365] | 60.546 | -1.119 [-2.816; 0.577] | 11.100 (111) |
| H1 N=2 family | 63.953 | -0.744 [-3.868; 2.380] | 60.319 | -1.346 [-4.355; 1.662] | 11.200 (112) |
| Sin H1 N=1 | 61.890 | -2.807 [-4.572; -1.041] | 60.546 | -1.119 [-2.816; 0.577] | 11.100 (111) |
| Sin H1 N=2 score | 59.908 | -4.789 [-6.892; -2.685] | 58.002 | -3.663 [-6.281; -1.045] | 11.600 (116) |
| Sin H1 N=2 family | 59.971 | -4.727 [-7.552; -1.901] | 58.182 | -3.483 [-7.123; 0.157] | 11.600 (116) |

El resumen del runner añade sd, IC95 del KPI y número de palets cerrados:
[`summary.md`](../../out/sweeps/lever_family_pallets/summary.md).
Sin H1, N=2 `score` frente a N=1 reproduce la pérdida contextual de
−1,982 pp. Con H1, esa pérdida es −0,934 pp; son referencias distintas.

El volumen es idéntico en todas las variantes y seeds:
`16.789.012.000 mm³/seed`, `167.890.120.000 mm³` al agrupar las diez.
El cociente agrupado Σvol/(Σn·V), que pondera cada seed por sus palets, es:

| Variante | Σvol/(Σn·V) % | IC95 de la media U_flujo % |
|---|---:|---|
| H1 N=1 (referencia) | 61.615 | [60.284; 63.047] |
| H1 N=2 score | 60.505 | [59.395; 61.697] |
| H1 N=2 family | 59.965 | [56.871; 63.768] |
| Sin H1 N=1 | 60.505 | [59.395; 61.697] |
| Sin H1 N=2 score | 57.897 | [56.123; 59.882] |
| Sin H1 N=2 family | 57.897 | [55.051; 61.313] |

Al aislar la asignación (`family − score`, ambas con N=2):

| H1 | Δ KPI pp [IC95] | Δ U_flujo pp [IC95] |
|---|---|---|
| Con H1 | 0.190 [-3.855; 4.235] | -0.227 [-3.921; 3.467] |
| Sin H1 | 0.062 [-3.917; 4.042] | 0.180 [-3.701; 4.062] |

No hay evidencia de mejora por asignación en ninguno de los dos casos.
Con H1, `family` consume tres palets más que N=1 y uno más que N=2
`score`. Además aumenta la dispersión del KPI entre seeds: sd 5,033 pp
frente a 1,712 pp de H1 N=1.

La separación sí se aplica al flujo real. Cada seed contiene 85 cajas
dominantes y 101 del resto. En las diez ejecuciones H1 `family`,
780/850 dominantes quedan en A y 842/1.010 del resto en B; hay 238
desbordamientos (70 dominantes a B y 168 del resto a A). Sin H1 son
228 desbordamientos (72 y 156). No se modifica el destino lógico.

### Sesgo observado al excluir END

| Variante | END: n / ocupación media % | DRAIN: n / ocupación media % | KPI − U_flujo pp |
|---|---:|---:|---:|
| H1 N=1 (referencia) | 10 / 31.347 | 0 / — | 3.032 |
| H1 N=2 score | 10 / 27.899 | 7 / 31.908 | 3.217 |
| H1 N=2 family | 10 / 23.219 | 5 / 28.037 | 3.634 |
| Sin H1 N=1 | 10 / 46.730 | 0 / — | 1.344 |
| Sin H1 N=2 score | 10 / 37.331 | 8 / 47.470 | 1.906 |
| Sin H1 N=2 family | 10 / 38.929 | 6 / 25.130 | 1.788 |

La identidad de las colas se verifica por seed. Con H1, `family` parece
ganar +0,190 pp frente a N=2 `score` en cerrados, pero pierde −0,227 pp
incluyendo todos los palets. Los 0,417 pp de diferencia entre ambas
lecturas provienen de excluir colas distintas: `END` tiene ocupación
media 23,219 % con `family`, frente a 27,899 % con `score`.
Por tanto el leve aumento del KPI cerrado no demuestra ahorro de palets.
Los `DRAIN` también cuentan: no se omiten las dos colas de N=2 ni se
recodifican cierres para favorecer una variante.

### Comprobación por seed: palets / U_flujo %

| Seed | H1 N=1 | H1 N=2 score | H1 N=2 family | Sin H1 N=1 | Sin H1 N=2 score | Sin H1 N=2 family |
|---:|---:|---:|---:|---:|---:|---:|
| 50021 | 11 / 61.055 | 11 / 61.055 | 10 / 67.160 | 11 / 61.055 | 11 / 61.055 | 12 / 55.967 |
| 1 | 11 / 61.055 | 11 / 61.055 | 11 / 61.055 | 11 / 61.055 | 12 / 55.967 | 12 / 55.967 |
| 42 | 11 / 61.055 | 11 / 61.055 | 13 / 51.662 | 12 / 55.967 | 11 / 61.055 | 13 / 51.662 |
| 100 | 11 / 61.055 | 12 / 55.967 | 11 / 61.055 | 11 / 61.055 | 12 / 55.967 | 12 / 55.967 |
| 777 | 11 / 61.055 | 11 / 61.055 | 12 / 55.967 | 11 / 61.055 | 11 / 61.055 | 11 / 61.055 |
| 5678 | 11 / 61.055 | 11 / 61.055 | 11 / 61.055 | 11 / 61.055 | 11 / 61.055 | 11 / 61.055 |
| 9999 | 11 / 61.055 | 11 / 61.055 | 12 / 55.967 | 11 / 61.055 | 12 / 55.967 | 12 / 55.967 |
| 12345 | 11 / 61.055 | 11 / 61.055 | 11 / 61.055 | 11 / 61.055 | 12 / 55.967 | 11 / 61.055 |
| 99999 | 10 / 67.160 | 11 / 61.055 | 10 / 67.160 | 11 / 61.055 | 12 / 55.967 | 12 / 55.967 |
| 314159 | 11 / 61.055 | 11 / 61.055 | 11 / 61.055 | 11 / 61.055 | 12 / 55.967 | 10 / 67.160 |

## Recomendación sobre la fase 2

**No acometer ahora la integración con H3–H6.** Esta fase no muestra
potencial favorable con greedy: frente a H1 N=1, `family` pierde
0,744 pp de KPI y 1,346 pp de ocupación de flujo, consume más palets
y añade una segunda posición física. Frente a N=2 `score` tampoco
mejora el flujo con H1. Sin H1 no reduce el total de palets frente a
`score` (116 en ambos casos).

Los intervalos amplios incluyen mejoras y pérdidas: el experimento
no prueba que una futura búsqueda conjunta sea incapaz de mejorar.
Sí falta la señal positiva que justificaba gastar cuota en esa
integración. Conservar esta opción como palanca experimental, con
`score` por defecto, y no promoverla como mejora de producción.

## Reproducción y validación

Desde la raíz del worktree:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_family_pallets.py tests/test_benchmark_one_pallet_canonical.py -q
export PYTHONPATH=$(pwd)/src && ./scripts/smoke_offline.sh
.venv/bin/python scripts/run_sweep.py --configs configs/sweeps/lever_family_pallets.yaml --seeds 10 --workers 15 --lock-retry-seconds 60 --name lever_family_pallets --baseline h1_n1
PYTHONPATH=src:. .venv/bin/python out/analyze_family_pallets.py
git --no-pager diff --check
```

- Tests específicos y contrato del benchmark: **36 OK** (28 nuevos y 8 existentes).
- Smoke: **476 tests OK**, escenario height-slack final OK; salida 0.
- Sweep: **60/60**, salida 0; todas las seeds con KPI definido y sin cortes.
- Auditoría complementaria: salida 0; identidad de volumen y de colas,
  unicidad de las 186 cajas, hashes y conteos verificados.
- `score`, por defecto y explícito, reproduce exactamente los KPI y el
  SHA256 de las colocaciones del código anterior en el Excel smoke de
  60 cajas, con y sin H1 (`tests/fixtures/open_pallets_score_v1.json`,
  generado con las fuentes originales del HEAD indicado). El hash
  excluye metadatos de parámetros, que ahora incluyen la opción nueva.
- Se ha contrastado además H1 N=1 y H1 N=2 `score` con los artefactos
  anteriores de `lever_h1_operativas`: las diez seeds reproducen
  exactamente colocaciones, KPI, número de palets y utilización.
- Tests de familia: límites inclusivos y dimensiones originales,
  preferencia A/B aunque el scorer prefiera el otro palet, H1 penalty
  y filter, desbordamiento en ambos sentidos, posición no disponible,
  cierre geométrico conservado y N=1 sin cambios en colocaciones.

Artefactos locales (ignorados por Git):

- [Resumen oficial](../../out/sweeps/lever_family_pallets/summary.md) y
  [JSON](../../out/sweeps/lever_family_pallets/summary.json).
- [Auditoría de flujo y colas](../../out/sweeps/lever_family_pallets/family_audit.json);
  [script del análisis](../../out/analyze_family_pallets.py).
- Logs: `out/family_tests.log`, `out/family_smoke.log`,
  `out/family_sweep.log`, `out/family_analysis.log`.

Para reconstruir únicamente la tabla de flujo sin depender del script
local, los `kpi.json` ya contienen la métrica existente:

```python
import json
from pathlib import Path
from statistics import mean
root = Path("out/sweeps/lever_family_pallets")
manifest = json.loads((root / "manifest.json").read_text())
for config in manifest["configs"]:
    rows = [json.loads((root / config["name"] / str(s) / "kpi.json").read_text())
            for s in manifest["seeds"]]
    print(config["name"], mean(r["kpi_all_pallets"] for r in rows),
          sum(r["n_pallets_total"] for r in rows))
```

No se han modificado el Excel, la métrica, el validador, la accesibilidad
ni el gate. No se han hecho commits, push ni PR.
