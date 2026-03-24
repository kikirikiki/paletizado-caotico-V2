#!/usr/bin/env python3
"""
Genera ficheros xlsx de entrada multi-rampa con distintas reglas de desvío.

Uso:
  python scripts/gen_multi_ramp_input.py \\
    --excel "data/Flujo_rampas_Tipos_contenedores.xlsx" \\
    --seeds 42 123 777 999 1234 \\
    --divert-rule size_natural \\
    --out-dir data/multi_ramp_inputs/

Reglas disponibles:
  random        Cada caja va a la rampa de origen (Rampa1→dest 1/2/3, Rampa2→dest 4/5/6).
                El seed controla el destino dentro de la rampa.
  size_natural  W450*/PICKING/MULTISHUTTLE → Ramp1; W400* → Ramp2.
  size_median   volumen >= mediana → Ramp1; < mediana → Ramp2.
  round_robin   Alternando Ramp1/Ramp2 por orden de timestamp.
  load_balance  Siempre a la rampa con menos cajas hasta ese momento (empate → Ramp1).
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from pathlib import Path
from typing import NamedTuple

import pandas as pd

RULES = ["random", "size_natural", "size_median", "round_robin", "load_balance"]

LARGE_KEYWORDS = ("W450", "PICKING", "MULTISHUTTLE")
SMALL_KEYWORDS = ("W400",)

TIME_CANDIDATES = ("hora bajada a rampa", "hora_bajada_a_rampa", "timestamp", "time", "hora")
TYPE_CANDIDATES = ("tipo de contenedor", "tipo_de_contenedor", "tipo contenedor", "tipo")
LENGTH_CANDIDATES = ("largo", "length", "len", "longitud")
WIDTH_CANDIDATES = ("ancho", "width", "wid")
HEIGHT_CANDIDATES = ("alto", "height", "h", "altura")
WEIGHT_CANDIDATES = ("peso contenedor", "peso_contenedor", "peso", "weight", "kg", "masa")


class BoxRow(NamedTuple):
    ts: float
    tipo: str
    largo: int | None
    ancho: int | None
    alto: int | None
    peso: float | None
    sheet_ramp: int  # ramp original (1 o 2) según hoja Excel


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(name).strip().lower()).strip()


def _find_col(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    norm_map = {col: _normalize(col) for col in columns}
    for c in candidates:
        c_norm = c.lower()
        for col, norm in norm_map.items():
            if c_norm == norm or c_norm in norm:
                return col
    return None


def _find_sheet(sheet_names: list[str], candidates: tuple[str, ...]) -> str | None:
    for sheet in sheet_names:
        norm = re.sub(r"[^a-z0-9]+", "", sheet.lower())
        for c in candidates:
            if re.sub(r"[^a-z0-9]+", "", c.lower()) in norm:
                return sheet
    return None


def _parse_times(series: pd.Series) -> list[float]:
    if pd.api.types.is_datetime64_any_dtype(series):
        return [float(v.timestamp()) for v in pd.to_datetime(series).tolist()]
    num = pd.to_numeric(series, errors="coerce")
    if num.isna().any():
        dt = pd.to_datetime(series, errors="coerce", dayfirst=True)
        if dt.isna().any():
            raise ValueError(f"No se pudieron parsear los timestamps: {series.name}")
        return [float(v.timestamp()) for v in dt.tolist()]
    return [float(v) for v in num.tolist()]


def _parse_int(val: object) -> int | None:
    if pd.isna(val):  # type: ignore[arg-type]
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _parse_float(val: object) -> float | None:
    if pd.isna(val):  # type: ignore[arg-type]
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _load_sheet_rows(df: pd.DataFrame, sheet_ramp: int) -> tuple[list[BoxRow], list[float]]:
    cols = list(df.columns)
    time_col = _find_col(cols, TIME_CANDIDATES)
    type_col = _find_col(cols, TYPE_CANDIDATES)
    largo_col = _find_col(cols, LENGTH_CANDIDATES)
    ancho_col = _find_col(cols, WIDTH_CANDIDATES)
    alto_col = _find_col(cols, HEIGHT_CANDIDATES)
    peso_col = _find_col(cols, WEIGHT_CANDIDATES)

    if time_col is None:
        raise ValueError(f"No se encontró columna timestamp entre: {cols}")

    times = _parse_times(df[time_col])
    rows = []
    for i, row in df.iterrows():
        tipo = str(row[type_col]) if type_col and not pd.isna(row[type_col]) else ""
        largo = _parse_int(row[largo_col]) if largo_col else None
        ancho = _parse_int(row[ancho_col]) if ancho_col else None
        alto = _parse_int(row[alto_col]) if alto_col else None
        peso = _parse_float(row[peso_col]) if peso_col else None
        rows.append(BoxRow(ts=times[i], tipo=tipo, largo=largo, ancho=ancho, alto=alto, peso=peso, sheet_ramp=sheet_ramp))
    return rows, times


def _assign_ramp_random(boxes: list[BoxRow]) -> list[int]:
    """Cada caja mantiene la rampa de origen (comportamiento actual)."""
    return [b.sheet_ramp for b in boxes]


def _assign_ramp_size_natural(boxes: list[BoxRow]) -> list[int]:
    """W450*/PICKING/MULTISHUTTLE → Ramp1; W400* → Ramp2."""
    ramps = []
    for b in boxes:
        tipo_up = b.tipo.upper()
        if any(kw in tipo_up for kw in LARGE_KEYWORDS):
            ramps.append(1)
        elif any(kw in tipo_up for kw in SMALL_KEYWORDS):
            ramps.append(2)
        else:
            # fallback: rampa de origen
            ramps.append(b.sheet_ramp)
    return ramps


def _assign_ramp_size_median(boxes: list[BoxRow]) -> list[int]:
    """volumen >= mediana → Ramp1; < mediana → Ramp2."""
    vols = []
    for b in boxes:
        l = b.largo or 0
        a = b.ancho or 0
        h = b.alto or 0
        vols.append(l * a * h)
    sorted_vols = sorted(vols)
    n = len(sorted_vols)
    if n == 0:
        return [1] * len(boxes)
    mid = n // 2
    if n % 2 == 0:
        median = (sorted_vols[mid - 1] + sorted_vols[mid]) / 2
    else:
        median = sorted_vols[mid]
    return [1 if v >= median else 2 for v in vols]


def _assign_ramp_round_robin(boxes: list[BoxRow]) -> list[int]:
    """Alternando Ramp1/Ramp2 en orden de timestamp."""
    order = sorted(range(len(boxes)), key=lambda i: boxes[i].ts)
    ramps = [0] * len(boxes)
    for rank, orig_idx in enumerate(order):
        ramps[orig_idx] = 1 if rank % 2 == 0 else 2
    return ramps


def _assign_ramp_load_balance(boxes: list[BoxRow]) -> list[int]:
    """Siempre a la rampa con menos cajas asignadas (empate → Ramp1)."""
    order = sorted(range(len(boxes)), key=lambda i: boxes[i].ts)
    ramps = [0] * len(boxes)
    counts = {1: 0, 2: 0}
    for orig_idx in order:
        if counts[1] <= counts[2]:
            ramp = 1
        else:
            ramp = 2
        ramps[orig_idx] = ramp
        counts[ramp] += 1
    return ramps


def _build_rows(
    df1: pd.DataFrame,
    df2: pd.DataFrame,
    *,
    rule: str,
    seed: int,
) -> list[dict]:
    boxes_r1, _ = _load_sheet_rows(df1, sheet_ramp=1)
    boxes_r2, _ = _load_sheet_rows(df2, sheet_ramp=2)
    all_boxes = boxes_r1 + boxes_r2

    # Ordenar por timestamp antes de aplicar regla
    all_boxes_sorted_idx = sorted(range(len(all_boxes)), key=lambda i: all_boxes[i].ts)
    all_boxes = [all_boxes[i] for i in all_boxes_sorted_idx]

    # Asignación de rampa
    if rule == "random":
        ramp_assignments = _assign_ramp_random(all_boxes)
    elif rule == "size_natural":
        ramp_assignments = _assign_ramp_size_natural(all_boxes)
    elif rule == "size_median":
        ramp_assignments = _assign_ramp_size_median(all_boxes)
    elif rule == "round_robin":
        ramp_assignments = _assign_ramp_round_robin(all_boxes)
    elif rule == "load_balance":
        ramp_assignments = _assign_ramp_load_balance(all_boxes)
    else:
        raise ValueError(f"Regla desconocida: {rule}")

    # Asignación de destino con seed (solo controla destino dentro de rampa)
    rng = random.Random(seed)
    t0 = min(b.ts for b in all_boxes)

    rows = []
    for box, ramp in zip(all_boxes, ramp_assignments):
        if ramp == 1:
            dest = rng.choice([1, 2, 3])
        else:
            dest = rng.choice([4, 5, 6])
        ts = pd.Timestamp(box.ts, unit="s")
        rows.append({
            "timestamp": ts,
            "destino": dest,
            "largo": box.largo,
            "ancho": box.ancho,
            "alto": box.alto,
            "peso": box.peso,
            "rampa": ramp,
            "seed": seed,
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera inputs multi-rampa con distintas reglas de desvío")
    parser.add_argument("--excel", required=True, help="Excel fuente (2 hojas Rampa1/Rampa2)")
    parser.add_argument("--seeds", type=int, nargs="+", required=True, help="Seeds a generar")
    parser.add_argument("--divert-rule", choices=RULES, default="random", help="Regla de desvío")
    parser.add_argument("--out-dir", required=True, help="Directorio de salida")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    xls = pd.ExcelFile(args.excel)
    sheet1 = _find_sheet(xls.sheet_names, ("rampa1", "exp.1", "exp1"))
    sheet2 = _find_sheet(xls.sheet_names, ("rampa2", "exp.2", "exp2"))
    if sheet1 is None:
        raise ValueError(f"No se encontró hoja Rampa1 entre: {xls.sheet_names}")
    if sheet2 is None:
        raise ValueError(f"No se encontró hoja Rampa2 entre: {xls.sheet_names}")

    df1 = pd.read_excel(xls, sheet_name=sheet1)
    df2 = pd.read_excel(xls, sheet_name=sheet2)

    rule = args.divert_rule
    for seed in args.seeds:
        rows = _build_rows(df1, df2, rule=rule, seed=seed)
        out_fname = f"flujo_mr_{rule}_seed{seed:04d}.xlsx"
        out_path = out_dir / out_fname
        df_out = pd.DataFrame(rows, columns=["timestamp", "destino", "largo", "ancho", "alto", "peso", "rampa", "seed"])
        df_out.to_excel(out_path, index=False)
        ramp1 = sum(1 for r in rows if r["rampa"] == 1)
        ramp2 = sum(1 for r in rows if r["rampa"] == 2)
        print(f"  {out_fname}  total={len(rows)}  ramp1={ramp1}  ramp2={ramp2}")

    print(f"\nGenerados {len(args.seeds)} fichero(s) en {out_dir}/")


if __name__ == "__main__":
    main()
