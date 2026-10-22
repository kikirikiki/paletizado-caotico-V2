# Spec 25b — Entrenamiento G1 con visibles enmascaradas (D-039 fase B, retoque de la spec 25)

Rama `feat/investigacion-larga` (wt2). No commitees. Archivos: SOLO `scripts/train_value_g1.py` y `tests/test_value_net_g1.py`.
No toques `src/` (otra tarea está validando el scheduler).

## Por qué
En la búsqueda (spec 26), el modo `rollout+learned` evalúa la red al final de un rollout, cuando ya no quedan cajas visibles
en la cinta (0 visibles), y el modo `learned` la evalúa con 12–15 visibles. Los datos de entrenamiento tienen casi siempre 15.
La red debe estimar la utilización final con cualquier subconjunto de lo visible.

## Qué
- `--mask-visible-prob p` (default 0,5): para cada estado de ENTRENAMIENTO, con probabilidad p se elige k uniforme en {0, …, n}
  (n = visibles presentes) y se conserva un subconjunto aleatorio de k cajas (en su orden original, compactadas al principio);
  el resto de filas a 0 y `scalars[3] = k/15`. La etiqueta no cambia. Aleatoriedad determinista: generador sembrado con
  (seed, epoch, índice del estado); con p = 0 el entrenamiento es idéntico bit a bit al actual.
- Validación y test: además de la métrica sin máscara, MAE/R² con k fijo ∈ {0, 5, 10} (subconjunto determinista por estado,
  mismo procedimiento). La selección del mejor epoch sigue siendo el MAE sin máscara + el de k = 0 (media de ambos); documéntalo.
- Informe: tabla por k.

## Tests (añádelos a `tests/test_value_net_g1.py`)
- p = 0 → pesos idénticos a los de antes (mismo test de determinismo).
- Máscara: filas ocultas a 0, `boxes[:,3]` y `scalars[3]` coherentes con k; misma máscara con la misma semilla.
