from __future__ import annotations

import random
import re
from pathlib import Path

import pandas as pd

from .des import Arrival
from .paths import resolve_repo_path

TIME_COLUMNS = ("timestamp", "time", "hora", "hora_bajada", "hora_bajada_a_rampa")
LENGTH_COLUMNS = ("largo", "length", "len", "longitud")
WIDTH_COLUMNS = ("ancho", "width", "wid")
HEIGHT_COLUMNS = ("alto", "height", "h", "altura")
WEIGHT_COLUMNS = ("peso", "weight", "kg", "masa")


def load_arrivals_multi_ramp(
    excel_path: str | Path,
    *,
    seed: int = 42,
    weight_col: str | None = None,
) -> list[Arrival]:
    path = resolve_repo_path(str(excel_path))
    if not path.exists():
        raise FileNotFoundError(f"Excel no encontrado: {excel_path} | intentado: {path.resolve(strict=False)}")

    xls = pd.ExcelFile(path)
    sheet_names = xls.sheet_names

    sheet1 = _find_sheet(sheet_names, ("rampa1", "exp.1", "exp1"))
    sheet2 = _find_sheet(sheet_names, ("rampa2", "exp.2", "exp2"))

    if sheet1 is None:
        raise ValueError(f"No se encontró hoja Rampa1 entre: {sheet_names}")
    if sheet2 is None:
        raise ValueError(f"No se encontró hoja Rampa2 entre: {sheet_names}")

    df1 = pd.read_excel(xls, sheet_name=sheet1).reset_index(drop=True)
    df2 = pd.read_excel(xls, sheet_name=sheet2).reset_index(drop=True)

    times1 = _parse_times(_find_series(df1, TIME_COLUMNS, "tiempo"))
    times2 = _parse_times(_find_series(df2, TIME_COLUMNS, "tiempo"))

    t0 = min(min(times1), min(times2))

    rng = random.Random(seed)
    arrivals: list[Arrival] = []
    row_idx = 0

    for i, row in df1.iterrows():
        dest = rng.choice([1, 2, 3])
        arrivals.append(
            Arrival(
                time=float(times1[i] - t0),
                destination=dest,
                row_idx=row_idx,
                length_mm=_get_int(row, df1.columns, LENGTH_COLUMNS),
                width_mm=_get_int(row, df1.columns, WIDTH_COLUMNS),
                height_mm=_get_int(row, df1.columns, HEIGHT_COLUMNS),
                weight_kg=_get_float(row, df1.columns, WEIGHT_COLUMNS, override_col=weight_col),
            )
        )
        row_idx += 1

    for i, row in df2.iterrows():
        dest = rng.choice([4, 5, 6])
        arrivals.append(
            Arrival(
                time=float(times2[i] - t0),
                destination=dest,
                row_idx=row_idx,
                length_mm=_get_int(row, df2.columns, LENGTH_COLUMNS),
                width_mm=_get_int(row, df2.columns, WIDTH_COLUMNS),
                height_mm=_get_int(row, df2.columns, HEIGHT_COLUMNS),
                weight_kg=_get_float(row, df2.columns, WEIGHT_COLUMNS, override_col=weight_col),
            )
        )
        row_idx += 1

    return sorted(arrivals, key=lambda a: (a.time, a.row_idx))


def _normalize(name: str) -> str:
    base = str(name).strip().lower()
    base = re.sub(r"[^a-z0-9]+", "", base)
    return base


def _find_sheet(sheet_names: list[str], candidates: tuple[str, ...]) -> str | None:
    for sheet in sheet_names:
        norm = _normalize(sheet)
        for c in candidates:
            c_norm = _normalize(c)
            if c_norm in norm:
                return sheet
    return None


def _find_col(columns: pd.Index, candidates: tuple[str, ...]) -> str | None:
    normalized = {col: _normalize(col) for col in columns}
    for c in candidates:
        c_norm = _normalize(c)
        for col, norm in normalized.items():
            if c_norm in norm:
                return col
    return None


def _find_series(df: pd.DataFrame, candidates: tuple[str, ...], label: str) -> pd.Series:
    col = _find_col(df.columns, candidates)
    if col is None:
        raise ValueError(f"No se encontró columna '{label}' entre: {list(df.columns)}")
    return df[col]


def _parse_times(series: pd.Series) -> list[float]:
    if pd.api.types.is_datetime64_any_dtype(series):
        dt = pd.to_datetime(series, errors="coerce")
        return [float(v.timestamp()) for v in dt.to_list()]
    num = pd.to_numeric(series, errors="coerce")
    if num.isna().any():
        dt = pd.to_datetime(series, errors="coerce", dayfirst=True)
        if dt.isna().any():
            raise ValueError("No se pudieron interpretar los timestamps")
        return [float(v.timestamp()) for v in dt.to_list()]
    return [float(v) for v in num.to_list()]


def _get_int(row: pd.Series, columns: pd.Index, candidates: tuple[str, ...]) -> int | None:
    col = _find_col(columns, candidates)
    if col is None:
        return None
    val = row[col]
    if pd.isna(val):
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _get_float(
    row: pd.Series,
    columns: pd.Index,
    candidates: tuple[str, ...],
    override_col: str | None = None,
) -> float | None:
    col = override_col if override_col and override_col in columns else _find_col(columns, candidates)
    if col is None:
        return None
    val = row[col]
    if pd.isna(val):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
