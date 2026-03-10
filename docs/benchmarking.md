# Benchmarking canonico (1 pallet)

Fuente de verdad unica para benchmark 1-pallet:
- `configs/benchmarks/one_pallet_canonical.json`

## Ejecutar benchmark canonico

```bash
PYTHONPATH=src ./.venv/bin/python scripts/benchmark_one_pallet_canonical.py \
  --profile configs/benchmarks/one_pallet_canonical.json \
  --outdir /tmp/palca-benchmark-canonical
```

Por defecto usa seeds fijas:
- `50021, 50022, 50023, 50024, 50025`

Outputs principales:
- `summary.csv`
- `summary.json`
- JSON por seed (`baseline/seed_<seed>.json`)
- dump de placements por seed (`baseline/seed_<seed>_placements.json`)

## Comparar baseline canonico vs variante

Con archivo de variante (overrides):

```bash
PYTHONPATH=src ./.venv/bin/python scripts/benchmark_one_pallet_canonical.py \
  --profile configs/benchmarks/one_pallet_canonical.json \
  --variant-config configs/benchmarks/my_variant.json \
  --outdir /tmp/palca-benchmark-compare
```

Con overrides inline:

```bash
PYTHONPATH=src ./.venv/bin/python scripts/benchmark_one_pallet_canonical.py \
  --profile configs/benchmarks/one_pallet_canonical.json \
  --set lookahead_k=10 micro_width=60 \
  --outdir /tmp/palca-benchmark-compare
```

## Metricas minimas a revisar

- `processed_boxes`
- `first_stack_step`
- `first_stand_hw_step`
- `stand_hw_used_total`
- `hard_floor_phase_stand_hw_chosen_total`

## Fingerprint de reproducibilidad

`summary.json` incluye:
- rama git
- commit SHA
- ruta del perfil usado
- seeds
- timestamp UTC
- hash del config efectivo
- version de Python

## Regla de evaluacion

Para cambios futuros del benchmark 1-pallet, esta referencia canonica es la unica valida de comparacion. No usar scripts ad hoc ni perfiles temporales.
