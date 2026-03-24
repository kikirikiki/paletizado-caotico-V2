"""
Genera Excel de input multi-rampa con destinos asignados aleatoriamente (seed reproducible).

Uso:
  python scripts/gen_multi_ramp_input.py --excel data/Flujo_rampas_Tipos_contenedores.xlsx --seed 42
  python scripts/gen_multi_ramp_input.py --excel data/Flujo_rampas_Tipos_contenedores.xlsx --seeds 42 123 999
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import pandas as pd


SHEET_RAMP1 = "Rampa1 - MS-CC-00029"
SHEET_RAMP2 = "Rampa2 - MS-CC-00030"

DESTS_RAMP1 = [1, 2, 3]
DESTS_RAMP2 = [4, 5, 6]


def generate_for_seed(df1: pd.DataFrame, df2: pd.DataFrame, seed: int) -> pd.DataFrame:
    rng = random.Random(seed)
    rows = []

    for _, row in df1.iterrows():
        dest = rng.choice(DESTS_RAMP1)
        rows.append({
            "timestamp": row["Hora bajada a rampa"],
            "destino": dest,
            "largo": row["Largo"],
            "ancho": row["Ancho"],
            "alto": row["Alto"],
            "peso": row["Peso Contenedor"],
            "rampa": 1,
            "seed": seed,
        })

    for _, row in df2.iterrows():
        dest = rng.choice(DESTS_RAMP2)
        rows.append({
            "timestamp": row["Hora bajada a rampa"],
            "destino": dest,
            "largo": row["Largo"],
            "ancho": row["Ancho"],
            "alto": row["Alto"],
            "peso": row["Peso Contenedor"],
            "rampa": 2,
            "seed": seed,
        })

    df = pd.DataFrame(rows, columns=["timestamp", "destino", "largo", "ancho", "alto", "peso", "rampa", "seed"])
    df = df.sort_values(["timestamp", "rampa"]).reset_index(drop=True)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera inputs multi-rampa con destinos asignados por seed.")
    parser.add_argument("--excel", required=True, help="Excel fuente con las dos hojas de rampas.")
    parser.add_argument("--seed", type=int, default=None, help="Seed único (genera 1 fichero).")
    parser.add_argument("--seeds", type=int, nargs="+", default=None, help="Múltiples seeds (genera N ficheros).")
    parser.add_argument("--out-dir", default="data/multi_ramp_inputs/", help="Directorio de salida.")
    parser.add_argument("--prefix", default="flujo_mr", help="Prefijo del nombre de fichero.")
    args = parser.parse_args()

    if args.seed is not None and args.seeds is not None:
        parser.error("--seed y --seeds son incompatibles, usa sólo uno.")
    if args.seed is None and args.seeds is None:
        parser.error("Debes especificar --seed o --seeds.")

    seeds = [args.seed] if args.seed is not None else args.seeds

    excel_path = Path(args.excel)
    if not excel_path.exists():
        print(f"Error: no se encuentra el fichero Excel: {excel_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Leyendo Excel: {excel_path}")
    df1 = pd.read_excel(excel_path, sheet_name=SHEET_RAMP1)
    df2 = pd.read_excel(excel_path, sheet_name=SHEET_RAMP2)
    print(f"  Rampa1: {len(df1)} filas | Rampa2: {len(df2)} filas")

    for seed in seeds:
        df = generate_for_seed(df1, df2, seed)
        out_path = out_dir / f"{args.prefix}_seed{seed:04d}.xlsx"
        df.to_excel(out_path, index=False)
        print(f"  Generado: {out_path}  ({len(df)} filas)")

    print(f"Listo. {len(seeds)} fichero(s) en {out_dir}")


if __name__ == "__main__":
    main()
