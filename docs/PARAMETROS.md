# Parámetros congelados del estudio de compactidad

La fuente de verdad es `src/palca/study/params.py`. El preset **oficial** es
`arm6_4orient`; `gantry_4orient` es un contraste informativo y `arm6_planar`,
un control. Se devuelven copias profundas; los overrides se validan con
`inspect.signature(sim.run.run_simulation)` y los presets arm6 comprueban las
caras permitidas después de aplicar overrides (`src/palca/study/params.py:66`).
No se alteran los defaults ni la lógica del simulador.

## Procedencia y reproducción

Se ha contrastado el código local con estas referencias Git (las fuentes con
prefijo de rama se consultan con `git show rama:ruta`):

- `study/gantry-orientations`, commit `a435f1c759c6fccaadd4884292b0eb073e7600dc`:
  `studies/gantry_orientations/benchmark.py:64` (BASE_PARAMS), `:36` (seeds),
  `:38` y `:141` (Excel). Se conserva también `out_path=None`, obligatorio en
  `src/sim/run.py:412`; el Excel del worker se incorpora al dict base.
- `feat/stand-hl-orientation`, commit `614940201fbd0f94c7e407978f47b5745d41ecf5`:
  `src/palca/packer/pallet_model.py:188` y `:215` (geometría de ambas familias).
- Rama de partida indicada para esta tarea: `feat/fifo-scorer-coherencia`,
  commit `8da6ee9ba70c4c748ee4181238b0b9fd5ac95df6`. Se trabaja en la rama
  existente `feat/parametros-congelados` según la instrucción de la tarea.

Desde la raíz del worktree, usando el entorno existente:

```bash
export PATH="$PWD/.venv/bin:$PATH"
export PYTHONPATH="$PWD/src"
python -c "from palca.study.params import preset_params; print(preset_params('arm6_4orient'))"
python scripts/profile_flow.py --out out/profile_flow.json
.venv/bin/python -m pytest -q tests/test_study_params.py tests/test_orientation_faces.py tests/test_profile_flow.py
./scripts/smoke_offline.sh
```

El perfil imprime el mismo JSON que guarda. Lee la primera hoja canónica como
`src/sim/io.py:37`; del Excel del operario lee todas las hojas de cajas y excluye
el catálogo (`scripts/profile_flow.py:92`). Solo escribe JSON fuera de ambos
directorios `data/` (`scripts/profile_flow.py:120`). Los Excel son solo lectura.

## Parámetros físicos y de simulación

Los valores compartidos se heredan de BASE_PARAMS. Las constantes de palet se
obtienen de `PalletSpec()`, sin duplicarlas (`src/palca/study/params.py:55`).

| Parámetro | Valor / interpretación | Fuente archivo:línea |
|---|---|---|
| Palet nominal L×W | 1200×800 mm | `src/palca/domain/pallet_spec.py:8` |
| Altura máxima de carga | 2400 mm desde z=0 | `src/palca/domain/pallet_spec.py:10`; `src/palca/packer/pallet_model.py:1203` |
| Overhang | 20 mm **en cada lado** de ambos ejes | `src/palca/study/params.py:27`; `src/palca/domain/pallet_spec.py:15` |
| Envolvente efectiva con overhang=20 | **1240×840×2400 mm**; x desde −20 a 1220, y desde −20 a 820 | `src/palca/domain/pallet_spec.py:15`, `:19`, `:23` |
| Envolvente histórica del estudio | **1240×820×2400 mm**, denominador histórico, incompatible con overhang=20 simétrico | `study/gantry-orientations:studies/gantry_orientations/benchmark.py:92`; `scripts/profile_flow.py:22` |
| arm6_4orient (oficial) | planar+stand_hw: LWH, WLH, HWL, WHL; delta=400 mm | `src/palca/study/params.py:44`; `src/palca/packer/pallet_model.py:137` |
| gantry_4orient (contraste) | planar+stand_hw: mismas cuatro variantes; delta=0 (control desactivado) | `src/palca/study/params.py:47`; `src/palca/packer/controls.py:324` |
| arm6_planar (control) | planar: LWH, WLH; delta=400 mm | `src/palca/study/params.py:50`; `src/palca/packer/pallet_model.py:153` |
| Garra arm6 / cara prohibida | H×L (vertical W); requisito físico del cliente codificado y comprobado por el preset | `src/palca/study/orientation_faces.py:15`; `src/palca/study/params.py:77` |
| Accesibilidad | Rechaza si una caja adyacente/solapada tiene techo > altura local bajo candidato + delta; delta=400 mm en arm6, 0 en gantry | `src/palca/packer/controls.py:335`, `:345`, `:356`; `src/palca/study/params.py:43` |
| Gate stand_hw | Margen = max(0, 2400−altura actual); permite stand_hw **si margen ≤400 mm**, además de los límites geométricos por candidato | `src/palca/study/params.py:34`; `src/palca/packer/pallet_model.py:115`, `:1510`, `:1205` |
| Stacking / heurística | heightfield / bssf | `src/palca/study/params.py:28` |
| Estabilidad / min_support | ratio+corners+settle / 0,85 | `src/palca/study/params.py:30` |
| Score / height_slack_mm | min_height_slack_then_gain / 120 mm | `src/palca/study/params.py:32` |
| micro_plan | False (producción y estudio) | `src/palca/study/params.py:36`; `docs/CONTEXT.md:237` |
| Modelo / política | M1 / palca | `src/palca/study/params.py:16`, `:22` |
| Buffer ramp_cap | 15 cajas **por rampa**; con destino forzado solo se ocupa la 1 | `src/palca/study/params.py:21`; `src/sim/des.py:291`, `:385` |
| staging_cap | 0; M1 no usa staging | `src/palca/study/params.py:19`; `src/sim/des.py:289` |
| lookahead_k | 15 | `src/palca/study/params.py:23` |
| Rampas DES | Crea 2; destinos 1–3 → rampa 1, destinos 4–6 → rampa 2. Con force_destination=1 todas las cajas van a rampa 1, la 2 queda vacía | `src/sim/des.py:209`, `:291`, `:303`; `src/sim/run.py:544` |
| arrival_mode | immediate: todas las llegadas en t=0; mantiene orden row_idx tras shuffle. La cola física sigue limitada a 15; exceso espera upstream | `src/palca/study/params.py:24`; `src/sim/des.py:232`, `:391` |
| Shuffle | Ventana 15, fuerza 1,0, semilla episode_seed por ejecución | `src/palca/study/params.py:25`; `src/sim/run.py:560` |
| time_budget_ms | 900 ms | `src/palca/study/params.py:35` |
| t_pick_place | 14,0 s | `src/palca/study/params.py:18` |
| n_per_pallet | 999999 (no es una capacidad volumétrica) | `src/palca/study/params.py:17` |
| force_destination / continuous_pallets / max_pallets | 1 / True / 0 (sin límite de palets) | `src/palca/study/params.py:37`; `src/sim/des.py:324` |
| Excel canónico | data/Flujo rampas - Editado.xlsx | `src/palca/study/params.py:10`, `:40` |
| Seeds de referencia | [50021, 1, 42, 100, 777, 5678, 9999, 12345, 99999, 314159] | `src/palca/study/params.py:11` |
| Salida de simulación base | out_path=None | `src/palca/study/params.py:20` |

### Qué significa z=0 y si los 2400 mm incluyen madera

El modelo solo representa cajas. Sin cajas bajo el footprint, la altura de apoyo
es cero (`src/palca/packer/pallet_model.py:984`, inicialización `max_top_z=0` en
`:989`). La cota de techo se calcula como `z_mm + h_mm` y se compara con
`max_height_mm` (`src/palca/packer/pallet_model.py:1203`). No hay espesor de madera
en `PalletSpec` (`src/palca/domain/pallet_spec.py:7`). Por tanto, **2400 mm son
altura de carga sobre el plano de apoyo**; z=0 equivale al tablero superior del
palet. La madera no se suma ni se descuenta: el código no acredita una altura
total suelo→techo de 2400 mm. El catálogo del Excel real sí contiene un palet
de 145 mm, que no entra en este cálculo (hoja `TIpos de contenedores`, fila Excel
2; excluida por `scripts/profile_flow.py:97`).

## Mapeo orientación → cara apoyada

L/W/H siempre son Largo/Ancho/Alto originales del Excel; no se renombrarán tras
rotar. El loader toma esas columnas y no las ordena (`src/sim/io.py:42`, `:53`);
el perfil comprueba L≥W en el flujo (`scripts/profile_flow.py:78`). Para L>W>H:

| Orientación / nombre descriptivo | Footprint | Vertical | Cara apoyada | Permitida arm6_4orient | Fuente archivo:línea |
|---|---|---|---|---|---|
| LWH / plana | L×W | H | LxW | Sí | `src/palca/packer/pallet_model.py:153`; `src/palca/study/orientation_faces.py:36` |
| WLH / plana_rot | W×L | H | LxW | Sí | `src/palca/packer/pallet_model.py:163`; `src/palca/study/orientation_faces.py:36` |
| HWL / pie | H×W | L | HxW | Sí, sujeta al gate | `src/palca/packer/pallet_model.py:179`; `src/palca/study/orientation_faces.py:34` |
| WHL / pie_rot | W×H | L | HxW | Sí, sujeta al gate | `src/palca/packer/pallet_model.py:190`; `src/palca/study/orientation_faces.py:34` |
| LHW / canto (sintética) | L×H | W | HxL | No | `src/palca/study/orientation_faces.py:32`; `tests/test_orientation_faces.py:46` |
| HLW / canto_rot (sintética) | H×L | W | HxL | No | `src/palca/study/orientation_faces.py:32`; `src/palca/study/orientation_faces.py:15` |

Actualmente los únicos modos existentes son `planar` y `planar+stand_hw`
(`src/palca/packer/pallet_model.py:82`); ninguno genera el canto H×L. Los tests
recorren todos los modos de `ALLOWED_ORIENTATION_MODES`, con/sin giro, y simulan
una futura variante prohibida para verificar tanto el guard como el preset
(`tests/test_orientation_faces.py:33`, `:46`). Con dimensiones iguales se aplica
la clasificación conservadora HxL > HxW > LxW: si la vertical coincide con W,
devuelve HxL, aunque otra interpretación física sea posible
(`src/palca/study/orientation_faces.py:18`). El guard usa cajas distintas L>W>H
para comprobar el modo sin esa ambigüedad (`src/palca/study/orientation_faces.py:44`).

## Perfil del flujo

Datos copiados del JSON producido por `python scripts/profile_flow.py --out
out/profile_flow.json`, con el entorno del comando de reproducción anterior.
Las tablas siguientes transcriben sus campos; se redondea solo la presentación.
Fuente primaria: `data/Flujo rampas - Editado.xlsx`, primera hoja
`Rampa1 - MS-CC-00029`, filas Excel 2–187, columnas Largo/Ancho/Alto,
Palet destino y Peso Contenedor. Cálculo: `scripts/profile_flow.py:46`.

### Resumen canónico

Campos `canonical.boxes`, `distinct_references`, `total_volume_m3`,
`length_ge_width_always` y `families_round25mm`; fuente de cálculo:
`scripts/profile_flow.py:73`.

| Métrica | Valor |
|---|---|
| Cajas | 186 |
| Referencias exactas L/W/H | 111 |
| Familias a 25 mm | 35 |
| Volumen total geométrico | 16,789012 m³ |
| L≥W en todas las cajas | Sí |

### Top 10 de referencias exactas

Campo `canonical.top10`; conteo y desempate por L/W/H ascendentes:
`scripts/profile_flow.py:37`.

| L×W×H (mm) | Cajas |
|---|---|
| 605×445×350 | 16 |
| 600×445×350 | 12 |
| 605×445×390 | 8 |
| 600×450×350 | 6 |
| 600×445×355 | 5 |
| 605×445×355 | 5 |
| 605×445×385 | 5 |
| 505×395×375 | 4 |
| 605×450×350 | 4 |
| 605×450×355 | 4 |

### Cuantiles de volumen y peso

Campos `canonical.volume_quantiles_dm3` y `canonical.weight.quantiles`;
cuantiles con interpolación lineal de pandas (`scripts/profile_flow.py:33`),
volumen en dm³ (`scripts/profile_flow.py:74`) y peso sin conversión
(`scripts/profile_flow.py:63`).

| Cuantil | Volumen (dm³) | Peso (unidad original Excel) |
|---|---|---|
| 0 | 32,604 | 1020 |
| 0.1 | 59,345 | 5510 |
| 0.25 | 89,0615625 | 8170 |
| 0.5 | 94,5 | 9520 |
| 0.75 | 100,99509375 | 10855 |
| 0.9 | 105,51175 | 11610 |
| 1 | 129,375 | 14060 |

Peso Contenedor: 186 valores, 0 ausentes; media
9006,666666667, suma 1675240 en la unidad original del Excel
(`scripts/profile_flow.py:64`). No se presupone que sean kg.

### Familias por redondeo a 25 mm

Campo `canonical.families_round25mm`: múltiplo más cercano por eje;
empates hacia arriba (`scripts/profile_flow.py:53`, `:77`).

| Familia L×W×H redondeada (mm) | Cajas |
|---|---|
| 600×450×350 | 67 |
| 600×450×400 | 14 |
| 600×450×375 | 9 |
| 625×450×375 | 9 |
| 500×400×375 | 8 |
| 575×425×325 | 8 |
| 600×425×350 | 8 |
| 625×425×375 | 7 |
| 375×325×250 | 6 |
| 650×450×375 | 6 |
| 375×325×275 | 3 |
| 375×325×300 | 3 |
| 400×400×325 | 3 |
| 625×425×350 | 3 |
| 625×450×350 | 3 |
| 650×425×350 | 3 |
| 425×400×325 | 2 |
| 425×400×350 | 2 |
| 500×400×400 | 2 |
| 600×425×375 | 2 |
| 600×475×350 | 2 |
| 625×350×425 | 2 |
| 700×450×375 | 2 |
| 400×325×275 | 1 |
| 575×425×350 | 1 |
| 600×400×325 | 1 |
| 600×450×325 | 1 |
| 600×450×425 | 1 |
| 625×450×400 | 1 |
| 625×450×450 | 1 |
| 650×350×375 | 1 |
| 650×350×400 | 1 |
| 650×375×400 | 1 |
| 650×450×350 | 1 |
| 650×450×400 | 1 |

### Mínimos ideales por volumen

Campo `canonical.pallet_volume_lower_bounds`; cociente Σ(L×W×H) / volumen
de envolvente y techo entero (`scripts/profile_flow.py:55`, `:79`).

| Denominador (mm³) | Cociente | Mínimo entero ideal |
|---|---|---|
| 1240×820×2400 | 6,879840349 | 7 |
| 1200×800×2400 | 7,286897569 | 8 |
| 1240×840×2400 | 6,716034626 | 7 |

### Distribución original de Palet destino

Campo `canonical.destination_distribution` (`scripts/profile_flow.py:84`).
Estos destinos se sustituyen por 1 en el preset (`src/sim/run.py:544`).

| Palet destino del Excel | Cajas |
|---|---|
| 1 | 41 |
| 2 | 20 |
| 3 | 37 |
| 4 | 29 |
| 5 | 31 |
| 6 | 28 |

### Palets reales del operario: estimación equivalente

Excel disponible: `../paletizado-caotico-V2/data/Flujo_rampas_Tipos_contenedores.xlsx`.
Hojas de cajas: `Rampa1 - MS-CC-00029` (105 filas) y
`Rampa2 - MS-CC-00030` (81 filas); catálogo `TIpos de contenedores` excluido.
Campos `real_operator_pallets.sheets_boxes`, `ignored_sheets`, `pallets`;
fuente de cálculo: `scripts/profile_flow.py:92`. Son 186 cajas y 12 palets
(campo `pallet_count`, `scripts/profile_flow.py:113`), media 15,5 cajas/palet.

**ESTIMACIÓN: utilización volumétrica equivalente** = volumen geométrico de
las cajas / (1240×820×2400). Se desconoce la altura real de los palets;
no es la fracción ocupada de su envolvente real (`scripts/profile_flow.py:108`, `:114`).

| Etiqueta pallet destino | Cajas | Volumen (m³) | Utilización equivalente estimada (%) |
|---|---|---|---|
| PA0001000000231933 | 23 | 1,874768375 | 76,824694 |
| PA0001000000231934 | 21 | 1,8596695 | 76,205969 |
| PA0001000000231935 | 21 | 1,896457375 | 77,713471 |
| PA0001000000231936 | 17 | 1,360599125 | 55,754947 |
| PA0001000000231937 | 13 | 1,202991625 | 49,29647 |
| PA0001000000231940 | 10 | 0,847348375 | 34,722839 |
| PA0001000000232651 | 17 | 1,661233875 | 68,074428 |
| PA0001000000232652 | 19 | 1,813676125 | 74,321242 |
| PA0001000000232653 | 19 | 1,779635125 | 72,926302 |
| PA0001000000232654 | 5 | 0,4920665 | 20,164015 |
| PA0001000000232655 | 9 | 0,798059125 | 32,703052 |
| PA0001000000232656 | 12 | 1,202506875 | 49,276606 |


## Discrepancias entre fuentes

1. **vol_util% histórico es una aproximación.** Los 64,6 % / 72,1 % de
   `study/gantry-orientations:studies/gantry_orientations/README.md:53`, `:56`
   se calcularon como media_cajas × volumen_medio_supuesto / (1240×820×2400).
   El supuesto es `0.77*(605*445*355) + 0.23*78000000`, no la suma del volumen
   real de cada caja (`study/gantry-orientations:studies/gantry_orientations/benchmark.py:92`,
   `:93`, `:165`). No equivalen a una medición geométrica de utilización.
2. **stand_hl del PR #139 duplica stand_hw.** En
   `feat/stand-hl-orientation:src/palca/packer/pallet_model.py:188`, `:215`,
   ambas familias producen HWL y WHL, vertical L y cara H×W. Los nombres
   `HWL_hl`/`WHL_hl` no cambian su geometría; la deduplicación incluye la
   familia (`:244`), por lo que conserva duplicados geométricos. No añade la
   cara H×L ni seis orientaciones geométricas distintas. No puede atribuirse
   la mejora histórica a una nueva cara sin revisar el experimento.
3. **micro_plan contradictorio.** La «configuración validada» de
   `docs/CONTEXT.md:16` tiene true, mientras su producción declara False en
   `docs/CONTEXT.md:225`, `:237`. Se congela False del estudio
   (`src/palca/study/params.py:36`).
4. **Excel diferente.** `docs/CONTEXT.md:233` cita
   `Flujo_rampas_Tipos_contenedores.xlsx`; el canónico del estudio es
   `data/Flujo rampas - Editado.xlsx` (`src/palca/study/params.py:10`).
   El primero tiene dos hojas de cajas y un catálogo; el segundo una hoja.
   En los archivos disponibles, el multiconjunto L/W/H de las 186 cajas
   coincide, pero destinos y estructura no son intercambiables
   (`scripts/profile_flow.py:97`, `:134`; hojas citadas en el perfil).
5. **605×445×355 no describe una caja única de todo el flujo.** Esa referencia
   exacta sí existe, pero solo aparece 5 veces; representa un supuesto de
   familia en el estudio (`study/gantry-orientations:studies/gantry_orientations/benchmark.py:93`).
   La referencia exacta más frecuente es **605×445×350**, 16 de 186 cajas;
   hay 111 referencias exactas y 35 familias al redondear a 25 mm
   (`scripts/profile_flow.py:37`, `:53`, `:73`; perfil anterior).
6. **Rama base.** `AGENTS.md:7` prescribe `feature/stability-controls`; esta
   tarea parte de `feat/fifo-scorer-coherencia` y mantiene la rama ya creada
   `feat/parametros-congelados`, según instrucción explícita de la tarea.
7. **Smoke y estudio no son la misma configuración.** El smoke usa
   min_support=0,90 (`scripts/smoke_offline.sh:46`), frente a 0,85 del estudio
   (`src/palca/study/params.py:31`). Además usa height_slack=80, micro_plan=True,
   profundidad 3 y ancho 8 (`scripts/smoke_offline.sh:53`), y el Excel reducido
   `Flujo_smoke_60.xlsx` (`:38`). Pasar smoke no reproduce el benchmark.
8. **Envolvente histórica errónea respecto al código.** 800+2×20=840,
   no 820. La envolvente efectiva es **1240×840**, aunque el estudio y
   `docs/CONTEXT.md:94` usen 1240×820. Se conservan los cocientes históricos
   pedidos para comparación y se añade el efectivo, sin cambiar el motor
   (`src/palca/domain/pallet_spec.py:19`; `scripts/profile_flow.py:55`).
9. **La columna Volumen no es L×W×H.** Primera caja: Largo=580, Ancho=430,
   Alto=330 → 82 302 000 mm³, pero Volumen=1 316 250 (Excel canónico, fila 2).
   No se supone una unidad ni un factor corrector; el perfil calcula siempre
   con dimensiones (`scripts/profile_flow.py:50`).
10. **Peso sin unidad explícita.** Peso Contenedor está entre 1020 y 14060 en
    el Excel; el perfil conserva su unidad original (`scripts/profile_flow.py:66`).
    El loader pasa esos números a `weight_kg` sin convertir
    (`src/sim/io.py:56`, `:74`): no se presentan como kg verificados. No se
    modifica esa interpretación existente.
11. **Dos rampas creadas frente a una utilizada.** La descripción «una rampa»
    de `docs/CONTEXT.md:219` describe el uso efectivo del baseline, pero el DES
    crea dos (`src/sim/des.py:291`). Con el destino forzado todas las cajas
    llegan a la 1 por la regla de destinos, sin reparto equilibrado ni uso
    de las rampas originales del operario (`src/sim/run.py:544`; `src/sim/des.py:209`).
12. **Cantidad de destinos en CONTEXT.** `docs/CONTEXT.md:199` dice
    «4 (ES/IT/FR/NL/PT)», aunque enumera cinco. El Excel real disponible contiene
    ES=105, IT=68, FR=9, NL=3, PT=1 (hojas de cajas, columna Destino del pedido).
    El estudio fuerza un destino numérico único y no compara palets monodestino
    reales en igualdad de condiciones (`src/palca/study/params.py:37`).

## Supuestos y límites

La familia de 25 mm es solo una agrupación descriptiva (redondeo por eje,
empates hacia arriba), nunca sustituye dimensiones de simulación
(`scripts/profile_flow.py:53`). Los mínimos volumétricos son cotas inferiores
ideales, sin garantía de empaquetado, estabilidad, accesibilidad ni separación
por destino (`scripts/profile_flow.py:79`). Las utilizaciones de palets reales
son estimaciones equivalentes con el denominador histórico solicitado, no
compactidad medida a su altura real (`scripts/profile_flow.py:108`, `:114`).
Si falta el Excel real en otra máquina, el JSON informa `available=false`
(`scripts/profile_flow.py:93`). Los restantes argumentos de `run_simulation`
conservan los defaults existentes; BASE_PARAMS congela el conjunto solicitado,
no toda la firma (`src/sim/run.py:414`).
