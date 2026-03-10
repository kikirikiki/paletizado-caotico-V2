# Benchmarking canónico (1 pallet)

Fuente de verdad única:
- `configs/benchmarks/one_pallet_canonical.json`

Script oficial:
- `scripts/benchmark_one_pallet_canonical.py`

## Objetivo del benchmark canónico

El benchmark canónico debe ser **discriminativo** para el escenario industrial objetivo de 1 pallet:
- seeds fijas: `50021,50022,50023,50024,50025`
- resultados esperados en `feature/stability-controls`: alrededor de `21/22` cajas
- con divergencia real entre seeds (no una línea plana)

Si el benchmark queda plano (mismo `processed_boxes` en todas las seeds), no sirve como juez de regresiones/mejoras.

## Ejecución oficial

```bash
PYTHONPATH=src ./.venv/bin/python scripts/benchmark_one_pallet_canonical.py \
  --profile configs/benchmarks/one_pallet_canonical.json \
  --outdir /tmp/palca-benchmark-canonical
```

Outputs:
- `summary.csv`
- `summary.json`
- `baseline/seed_<seed>.json`
- `baseline/seed_<seed>_placements.json`

## Verificar que las seeds afectan de verdad

Revisar en `summary.json`:
- `discriminative.baseline.processed_boxes_unique_count`
- `discriminative.baseline.is_flat_processed_boxes`
- `rows[*].processed_boxes` por seed

Criterio mínimo:
- `processed_boxes_unique_count >= 2`
- `is_flat_processed_boxes == false`

## Guardas de contrato config -> run_simulation

El harness valida y falla si:
- faltan parámetros requeridos por `run_simulation`
- el perfil incluye claves que `run_simulation` no acepta
- los overrides (`--set` o `--variant-config`) contienen parámetros huérfanos/no usados

Auditoría en `summary.json`:
- `param_contract.run_simulation_param_keys`
- `param_contract.profile_required_param_keys`
- `param_contract.profile_param_keys`
- `param_contract.missing_required_in_profile`
- `param_contract.unknown_in_profile`

## Señal de benchmark inválido (“plano”)

Síntoma:
- todas las seeds con mismo `processed_boxes` (por ejemplo `14,14,14,14,14`)

Interpretación:
- el episodio quedó no discriminativo y no representa el caso objetivo real.

Acción:
- no usar ese resultado para comparar ramas;
- recalibrar perfil canónico antes de sacar conclusiones.
