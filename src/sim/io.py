from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable

import pandas as pd

from .des import Arrival
from .paths import resolve_repo_path


TIME_COLUMNS = ("timestamp", "time", "hora", "hora_bajada", "hora_bajada_a_rampa")
DEST_COLUMNS = ("destino", "destination", "dest")
LENGTH_COLUMNS = ("largo", "length", "len", "longitud")
WIDTH_COLUMNS = ("ancho", "width", "wid")
HEIGHT_COLUMNS = ("alto", "height", "h", "altura")
WEIGHT_COLUMNS = ("peso", "weight", "kg", "masa")
PRIORITY_COLUMNS = ("prioridad", "priority", "prio", "rank", "urgencia", "urgency")


def load_arrivals(
    excel_path: str | Path,
    *,
    weight_col: str | None = None,
    priority_col: str | None = None,
) -> list[Arrival]:
    original = str(excel_path)
    path = resolve_repo_path(original)
    if not path.exists():
        attempted = path.resolve(strict=False)
        cwd = Path.cwd()
        raise FileNotFoundError(
            f"Excel no encontrado: {original} | intentado: {attempted} | cwd: {cwd}"
        )

    df = pd.read_excel(path)
    df = df.reset_index(drop=True)

    time_col = _find_column(df.columns, TIME_COLUMNS)
    dest_col = _find_column(df.columns, DEST_COLUMNS)
    length_col = _find_column(df.columns, LENGTH_COLUMNS)
    width_col = _find_column(df.columns, WIDTH_COLUMNS)
    height_col = _find_column(df.columns, HEIGHT_COLUMNS)
    weight_col = weight_col or _find_column(df.columns, WEIGHT_COLUMNS)
    priority_col = priority_col or _find_column(df.columns, PRIORITY_COLUMNS)
    if time_col is None or dest_col is None:
        raise ValueError("No se encontraron columnas requeridas: timestamp/destino")

    times = _parse_times(df[time_col])
    destinations = [_parse_destination(value) for value in df[dest_col].tolist()]
    lengths = _parse_optional_numeric(df[length_col]) if length_col else [None] * len(df)
    widths = _parse_optional_numeric(df[width_col]) if width_col else [None] * len(df)
    heights = _parse_optional_numeric(df[height_col]) if height_col else [None] * len(df)
    weights = _parse_optional_float(df[weight_col]) if weight_col else [None] * len(df)
    priorities = _parse_optional_float(df[priority_col]) if priority_col else [None] * len(df)

    if any(dest is None for dest in destinations):
        raise ValueError("Hay destinos sin valor numerico valido (1-6)")

    t0 = min(times)
    arrivals: list[Arrival] = []
    for idx, (t, dest, length, width, height, weight, priority) in enumerate(
        zip(times, destinations, lengths, widths, heights, weights, priorities)
    ):
        arrivals.append(
            Arrival(
                time=float(t - t0),
                destination=int(dest),
                row_idx=idx,
                length_mm=length,
                width_mm=width,
                height_mm=height,
                weight_kg=weight,
                priority=priority,
            )
        )

    return sorted(arrivals, key=lambda a: (a.time, a.row_idx))


def _find_column(columns: Iterable[str], candidates: Iterable[str]) -> str | None:
    normalized = {col: _normalize(col) for col in columns}
    for candidate in candidates:
        for col, norm in normalized.items():
            if norm == candidate:
                return col
    for candidate in candidates:
        for col, norm in normalized.items():
            if candidate in norm:
                return col
    return None


def _normalize(name: str) -> str:
    base = str(name).strip().lower()
    base = re.sub(r"[^a-z0-9]+", "_", base)
    return base.strip("_")


def _parse_times(series: pd.Series) -> list[float]:
    if pd.api.types.is_datetime64_any_dtype(series):
        dt = pd.to_datetime(series, errors="coerce")
        if dt.isna().any():
            raise ValueError("Hay timestamps invalidos en la columna de tiempo")
        return [float(val.timestamp()) for val in dt.to_list()]

    num = pd.to_numeric(series, errors="coerce")
    if num.isna().any():
        dt = pd.to_datetime(series, errors="coerce", dayfirst=True)
        if dt.isna().any():
            raise ValueError("No se pudieron interpretar los timestamps")
        return [float(val.timestamp()) for val in dt.to_list()]

    return [float(val) for val in num.to_list()]


def _parse_optional_numeric(series: pd.Series) -> list[int | None]:
    num = pd.to_numeric(series, errors="coerce")
    values: list[int | None] = []
    for value in num.to_list():
        if pd.isna(value):
            values.append(None)
        else:
            values.append(int(value))
    return values


def _parse_optional_float(series: pd.Series) -> list[float | None]:
    num = pd.to_numeric(series, errors="coerce")
    values: list[float | None] = []
    for value in num.to_list():
        if pd.isna(value):
            values.append(None)
        else:
            values.append(float(value))
    return values


def _parse_destination(value: object) -> int | None:
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)) and float(value).is_integer():
        return int(value)
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    return int(match.group(0))
