#!/usr/bin/env python3
"""Carga un Excel y muestra un resumen basico de cajas."""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

# Permite importar desde src/ sin instalar el paquete.
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from io_excel import load_boxes  # noqa: E402

DEFAULT_EXCEL = Path("data") / "Flujo rampas - Editado.xlsx"

TARGET_COLUMNS = {
    "peso": ["peso", "weight", "kg", "masa"],
    "ancho": ["ancho", "width"],
    "alto": ["alto", "height"],
    "largo": ["largo", "length", "len", "longitud"],
    "hora": [
        "hora",
        "time",
        "timestamp",
        "fecha",
        "fecha_hora",
        "datetime",
        "hora_bajada_a_rampa",
        "hora_bajada",
        "bajada_rampa",
        "hora_rampa",
        "arribo",
        "arrival",
    ],
    "destino": ["destino", "destination", "dest", "dock", "zona", "zone"],
}


def _normalize_name(name: str) -> str:
    base = str(name).strip()
    base = unicodedata.normalize("NFKD", base)
    base = base.encode("ascii", "ignore").decode("ascii")
    base = base.lower()
    base = re.sub(r"[^a-z0-9]+", "_", base)
    return base.strip("_")


def _tokens(value: str) -> list[str]:
    return [t for t in value.split("_") if t]


def _match_score(norm: str, tokens: Iterable[str], synonyms: list[str]) -> int:
    if norm in synonyms:
        return 3
    if any(t in synonyms for t in tokens):
        return 2
    if any(s in norm for s in synonyms):
        return 1
    return 0


def _detect_columns(df: pd.DataFrame) -> tuple[dict[str, str], list[str]]:
    normalized = {col: _normalize_name(col) for col in df.columns}
    available = set(df.columns)
    mapping: dict[str, str] = {}

    for target, synonyms in TARGET_COLUMNS.items():
        best_col = None
        best_score = 0
        for col in list(available):
            norm = normalized[col]
            score = _match_score(norm, _tokens(norm), synonyms)
            if score > best_score:
                best_col = col
                best_score = score
        if best_col and best_score > 0:
            mapping[target] = best_col
            available.remove(best_col)

    missing = [target for target in TARGET_COLUMNS if target not in mapping]
    return mapping, missing


def _extract_time_series(
    df: pd.DataFrame, mapping: dict[str, str]
) -> tuple[str | None, pd.Series, str]:
    if "hora" not in mapping:
        return None, pd.Series(dtype="float64"), "missing"

    col = mapping["hora"]
    series = df[col]

    if pd.api.types.is_datetime64_any_dtype(series):
        dt = pd.to_datetime(series, errors="coerce")
        return col, dt, "datetime"

    if pd.api.types.is_numeric_dtype(series):
        num = pd.to_numeric(series, errors="coerce")
        return col, num, "numeric"

    dt = pd.to_datetime(series, errors="coerce", dayfirst=True)
    if dt.notna().sum() > 0:
        return col, dt, "datetime"

    num = pd.to_numeric(series, errors="coerce")
    if num.notna().sum() > 0:
        return col, num, "numeric"

    return col, pd.Series(dtype="float64"), "insufficient"


def _format_hhmmss(seconds: float) -> str:
    if not np.isfinite(seconds):
        return "n/a"
    total = int(round(seconds))
    if total < 0:
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _format_timestamp(value: pd.Timestamp) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _compute_deltas(times: pd.Series, time_kind: str) -> tuple[pd.Series, str]:
    if times.shape[0] < 2:
        return pd.Series(dtype="float64"), "s" if time_kind == "datetime" else "unidades"

    ordered = times.sort_values()
    if time_kind == "datetime":
        deltas = ordered.diff().dropna().dt.total_seconds()
        unit = "s"
    else:
        deltas = ordered.diff().dropna()
        unit = "unidades"
    return deltas, unit


def _extract_destination_number(value: object) -> int | None:
    if pd.isna(value):
        return None
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value)
    match = re.search(r"\d+", text)
    if not match:
        return None
    return int(match.group(0))


def _numeric_series(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce").dropna()


def _summary_range_mean(series: pd.Series) -> str:
    if series.empty:
        return "sin datos"
    return f"{series.min():.3g}–{series.max():.3g} (mean {series.mean():.3g})"


def _summarize_dimensions(df: pd.DataFrame, mapping: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for key in ["largo", "ancho", "alto", "peso"]:
        if key not in mapping:
            lines.append(f"- {key}: columna no encontrada")
            continue
        col = mapping[key]
        series = _numeric_series(df, col)
        lines.append(f"- {key} ({col}): {_summary_range_mean(series)}")
    return lines


def _summarize_destinations(df: pd.DataFrame, mapping: dict[str, str]) -> list[str]:
    if "destino" not in mapping:
        return ["- destino: columna no encontrada"]
    col = mapping["destino"]
    counts = df[col].fillna("(vacio)").value_counts(dropna=False)
    lines = [f"- {col}:"]
    for key, value in counts.items():
        lines.append(f"  {key}: {value}")
    return lines


def _interarrival_lines(
    times: pd.Series, time_kind: str, label: str
) -> list[str]:
    if times.shape[0] < 2:
        return [f"- {label}: no hay suficientes datos para inter-arrival"]

    deltas, unit = _compute_deltas(times, time_kind)
    if deltas.empty:
        return [f"- {label}: no hay suficientes datos para inter-arrival"]

    quantiles = deltas.quantile([0.5, 0.9, 0.99]).to_dict()
    return [
        f"- {label} (inter-arrival, {unit}):",
        "  "
        + (
            f"min={deltas.min():.3g}, "
            f"median={quantiles.get(0.5, np.nan):.3g}, "
            f"p90={quantiles.get(0.9, np.nan):.3g}, "
            f"p99={quantiles.get(0.99, np.nan):.3g}"
        ),
    ]


def _summarize_interarrival(
    time_col: str | None, time_series: pd.Series, time_kind: str
) -> list[str]:
    if time_kind == "missing" or time_col is None:
        return ["- hora: columna no encontrada"]
    times = time_series.dropna()
    if time_kind == "insufficient" or times.shape[0] < 2:
        return [f"- {time_col}: no hay suficientes datos para inter-arrival"]
    return _interarrival_lines(times, time_kind, time_col)


def _summarize_interarrival_by_rampa(
    df: pd.DataFrame,
    mapping: dict[str, str],
    time_series: pd.Series,
    time_kind: str,
) -> list[str]:
    if "destino" not in mapping:
        return ["- destino: columna no encontrada"]
    if time_kind == "missing":
        return ["- hora: columna no encontrada"]

    dest_col = mapping["destino"]
    dest_nums = pd.to_numeric(
        df[dest_col].apply(_extract_destination_number), errors="coerce"
    )

    lines: list[str] = []
    for label, mask in [("1-3", dest_nums.between(1, 3)), ("4-6", dest_nums.between(4, 6))]:
        subset_times = time_series[mask].dropna()
        if time_kind == "insufficient" or subset_times.shape[0] < 2:
            lines.append(f"- rampa {label}: no hay suficientes datos para inter-arrival")
            continue
        lines.extend(_interarrival_lines(subset_times, time_kind, f"rampa {label}"))
    return lines


def _summarize_time_window(
    df: pd.DataFrame, time_col: str | None, time_series: pd.Series, time_kind: str
) -> list[str]:
    if time_kind == "missing" or time_col is None:
        return ["- hora: columna no encontrada"]

    valid = time_series.dropna()
    if valid.shape[0] < 2:
        return [f"- {time_col}: no hay suficientes datos para inicio/fin"]

    start = valid.min()
    end = valid.max()

    if time_kind == "datetime":
        duration_seconds = (end - start).total_seconds()
        duration_label = _format_hhmmss(duration_seconds)
        rate = len(df) / (duration_seconds / 3600) if duration_seconds > 0 else np.nan
        rate_label = f"{rate:.3g}" if np.isfinite(rate) else "n/a"
        return [
            f"- inicio: {_format_timestamp(start)}",
            f"- fin: {_format_timestamp(end)}",
            f"- duracion: {duration_label}",
            f"- cajas/h: {rate_label}",
        ]

    duration_value = float(end - start)
    duration_label = _format_hhmmss(duration_value)
    rate = len(df) / (duration_value / 3600) if duration_value > 0 else np.nan
    rate_label = f"{rate:.3g}" if np.isfinite(rate) else "n/a"
    return [
        f"- inicio: {start}",
        f"- fin: {end}",
        f"- duracion (asumiendo s): {duration_label}",
        f"- cajas/h: {rate_label}",
    ]


def _summarize_duplicate_timestamps(
    time_col: str | None, time_series: pd.Series, time_kind: str
) -> list[str]:
    if time_kind == "missing" or time_col is None:
        return ["- hora: columna no encontrada"]

    valid = time_series.dropna()
    if valid.empty:
        return [f"- {time_col}: no hay timestamps validos"]

    counts = valid.value_counts()
    duplicate_rows = int(counts[counts > 1].sum())
    max_simultaneous = int(counts.max()) if not counts.empty else 0
    return [
        f"- filas con timestamp duplicado: {duplicate_rows}",
        f"- max cajas simultaneas: {max_simultaneous}",
    ]


def _print_missing_info(df: pd.DataFrame, mapping: dict[str, str], missing: list[str]) -> None:
    if not missing:
        return

    found = ", ".join(f"{k}->{v}" for k, v in mapping.items()) or "ninguna"
    print("Columnas faltantes:", ", ".join(missing))
    print("Columnas encontradas:", found)
    print("Columnas disponibles:", ", ".join(str(c) for c in df.columns))
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Carga un Excel y muestra un resumen del flujo de cajas."
    )
    parser.add_argument(
        "--excel",
        default=str(DEFAULT_EXCEL),
        help="Ruta al Excel (default: data/Flujo rampas - Editado.xlsx)",
    )
    args = parser.parse_args()

    excel_path = Path(args.excel)
    df = load_boxes(excel_path)

    mapping, missing = _detect_columns(df)
    time_col, time_series, time_kind = _extract_time_series(df, mapping)

    print(f"Archivo: {excel_path}")
    print(f"N cajas: {len(df)}")
    print()

    _print_missing_info(df, mapping, missing)

    print("Ventana temporal (Hora bajada a rampa):")
    for line in _summarize_time_window(df, time_col, time_series, time_kind):
        print(line)
    print()

    print("Timestamps duplicados:")
    for line in _summarize_duplicate_timestamps(time_col, time_series, time_kind):
        print(line)
    print()

    print("Rango y media dimensiones/peso:")
    for line in _summarize_dimensions(df, mapping):
        print(line)
    print()

    print("Reparto por destino:")
    for line in _summarize_destinations(df, mapping):
        print(line)
    print()

    print("Inter-arrival stats:")
    for line in _summarize_interarrival(time_col, time_series, time_kind):
        print(line)
    print()

    print("Inter-arrival por rampa:")
    for line in _summarize_interarrival_by_rampa(
        df, mapping, time_series, time_kind
    ):
        print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
