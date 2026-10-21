# Spec 25 — Fase B (D-039), módulo 1: red de valor G1 en PyTorch (modelo + entrenamiento en GPU)

Rama `feat/investigacion-larga` (wt2). No commitees. No toques `src/palca/scheduler/`, `packer/`, `sim/` (la integración en la
búsqueda es la spec 26). Lee solo: `src/palca/learning/{state_encoding,episode}.py`, `scripts/gen_value_data.py`,
`src/palca/scoring/learned_value.py` (semántica de `remaining_value`), RESEARCH §4 (G1) y D-039.
torch 2.11+cu128 ya está en `.venv` (GPU Quadro RTX 4000, 8 GB, sm_75).

## 1. `src/palca/learning/value_net.py`
- `ValueNetG1(nn.Module)`, entrada = la codificación `g1-v1` (lote): `height` [B,62,42] (→ float /2400, canal 1) + canales
  derivados calculados dentro del modelo (diferencias con vecinos en x e y, para que vea escalones/pozos), `boxes` [B,15,4],
  `scalars` [B,4].
  - Tronco CNN: 5–6 convoluciones 3×3 (32→64 canales, GroupNorm o sin normalización por lotes para que la inferencia sea
    idéntica con lote 1 y lote N), un stride 2, pooling global medio + máximo.
  - Conjunto de cajas visibles: MLP compartido 4→64 + suma y máximo sobre las presentes (DeepSets, invariante al orden).
  - Cabeza MLP → `util_final` (sigmoide) y auxiliar `cajas_hasta_cierre/100`. ≤ 300 k parámetros.
- `predict_utilization_batch(model, encodings, device)` y `remaining_value_batch(...)` con la misma semántica que
  `learned_value.remaining_value`: volumen adicional esperado = max(0, pred × envolvente − volumen colocado), con pred acotada a
  [utilización actual, 1].
- `load_value_net(path, device="cpu")`: carga `state_dict` + JSON de metadatos (versión de codificación, arquitectura, SHA256
  de pesos, semillas, datos, métricas); valida esquema y SHA.

## 2. `scripts/train_value_g1.py`
- `--data DIR [DIR ...]` (directorios de `gen_value_data.py`; exige su `manifest.json` y que cada uno declare generador
  sintético: rechaza rutas dentro del repo, `out/` y cualquier fichero que no sea `ep_*.npz` + manifiesto — D-032),
  `--weights` por directorio (p. ej. official 2, greedy 1), `--out DIR` (fuera del repo), `--epochs`, `--batch 1024`,
  `--lr`, `--device cuda|cpu`, `--seed`, `--max-states` (para pruebas), `--aux-weight 0.1`.
- Partición por EPISODIO, fija y documentada: SHA256 del nombre del episodio → 80/10/10 entrenamiento/validación/test. Nunca
  mezclar estados de un mismo episodio entre grupos.
- Carga todo en memoria si cabe (uint16 para alturas); si no, por bloques. AdamW + coseno, pérdida Huber sobre `util_final`
  + auxiliar. Selección del mejor epoch por MAE de validación; test solo al final.
- Determinismo D-028: `torch.manual_seed`, `torch.use_deterministic_algorithms(True)`, `CUBLAS_WORKSPACE_CONFIG=:4096:8`,
  `cudnn.benchmark=False`, orden de lotes sembrado.
- Informe JSON + Markdown en `--out`: MAE/RMSE/R² en pp por grupo y por fuente (greedy/official, generic/families),
  constante-media como referencia, curva por epoch, tiempo por epoch en GPU.
- **Latencia de inferencia** (para D-007): lotes de 1, 16, 64 y 256 estados, en CPU con 1 hilo (`torch.set_num_threads(1)`)
  y en GPU (sincronizando), media y p95 de 200 repeticiones, incluida la codificación numpy→tensor.
- Comprobación CPU vs GPU: las predicciones del test en CPU y GPU difieren < 1e-5 (informa el máximo).

## 3. Tests (`tests/test_value_net_g1.py`, solo este archivo; rápidos, CPU)
- Formas y rango (0..1); invariancia al orden de las cajas visibles; lote 1 == fila del lote N (< 1e-6).
- Determinismo: dos entrenamientos de 2 epochs con 2 episodios sintéticos pequeños (genéralos en `tmp_path` con
  `gen_value_data.py --policy greedy --episodes 2 --workers 1`) dan pesos idénticos en CPU.
- `remaining_value_batch` ≥ 0 y 0 en un palet "lleno" (pred acotada).
- Guardas D-032 del script (ruta dentro del repo, falta de manifiesto, fichero ajeno) → error.
- Guardar/cargar: mismo SHA y mismas predicciones.

## 4. Comprobación final (sin entrenamiento grande)
Un entrenamiento corto en GPU sobre `/tmp/g1_check/official` (+ 4 episodios greedy que generes en `/tmp/g1_check/greedy`),
2 epochs, para demostrar que corre en CUDA; da la latencia medida. Deja el comando exacto del entrenamiento grande sobre
`~/code/Paletizador/09_Software/data/g1_v1_greedy` y `g1_v1_official` (salida `~/code/Paletizador/09_Software/models/g1_v1/`):
lo lanzo yo cuando terminen los datos.
