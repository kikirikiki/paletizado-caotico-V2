#!/usr/bin/env python3
"""Perfila los Excel en lectura; solo escribe el informe JSON fuera de data/."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from palca.study.params import BASE_PARAMS, REFERENCE_EXCEL, pallet_envelope_mm  # noqa: E402

REAL_EXCEL = REPO_ROOT.parent / "paletizado-caotico-V2/data/Flujo_rampas_Tipos_contenedores.xlsx"
DIM_COLUMNS = ["Largo", "Ancho", "Alto"]
# Denominadores históricos pedidos para contrastar el estudio. El efectivo es 1240x840.
HISTORICAL_ENVELOPE_MM = (1240, 820, 2400)
QUANTILES = [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1]


def dimensions(df: pd.DataFrame) -> pd.DataFrame:
    dims = df[DIM_COLUMNS].apply(pd.to_numeric, errors="raise")
    if dims.isna().any().any() or (dims <= 0).any().any():
        raise ValueError("Cada caja debe tener L/W/H positivos y presentes")
    return dims


def quantiles(series: pd.Series) -> dict[str, float]:
    return {str(q): float(series.quantile(q)) for q in QUANTILES}


def references(dims: pd.DataFrame) -> list[dict]:
    counts = dims.groupby(DIM_COLUMNS).size().reset_index(name="boxes")
    counts = counts.sort_values(["boxes", *DIM_COLUMNS], ascending=[False, True, True, True])
    return [
        {"lwh_mm": [float(row[c]) for c in DIM_COLUMNS], "boxes": int(row["boxes"])}
        for _, row in counts.iterrows()
    ]


def profile_frame(df: pd.DataFrame) -> dict:
    dims = dimensions(df)
    if df.empty:
        raise ValueError("El flujo está vacío")
    volumes = dims.prod(axis=1)  # mm³ geométricos; NO la columna Volumen.
    exact = references(dims)
    # Al múltiplo más cercano; los empates .5 se redondean hacia arriba.
    rounded = ((dims / 25 + 0.5).apply(lambda col: col.map(math.floor)) * 25)
    total_mm3 = float(volumes.sum())
    denominators = {
        "historical_1240x820x2400": HISTORICAL_ENVELOPE_MM,
        "nominal_1200x800x2400": pallet_envelope_mm(0),
        "effective_overhang20": pallet_envelope_mm(BASE_PARAMS["overhang_mm"]),
    }
    weights = next((c for c in df.columns if "peso" in str(c).lower()), None)
    weight_profile = {"present": False}
    if weights is not None:
        values = pd.to_numeric(df[weights], errors="raise").dropna()
        weight_profile = {
            "present": True, "column": str(weights),
            "unit": "unidad original del Excel (sin conversión ni suposición de kg)",
            "count": int(values.count()), "missing": int(df[weights].isna().sum()),
            "mean": float(values.mean()) if len(values) else None,
            "total": float(values.sum()),
            "quantiles": quantiles(values) if len(values) else {},
        }
    return {
        "boxes": len(df), "distinct_references": len(exact), "top10": exact[:10],
        "volume_quantiles_dm3": quantiles(volumes / 1_000_000),
        "total_volume_m3": total_mm3 / 1_000_000_000,
        "families_round25mm_rule": "múltiplo más cercano; empate hacia arriba, cada eje por separado",
        "families_round25mm": references(rounded),
        "length_ge_width_always": bool((dims["Largo"] >= dims["Ancho"]).all()),
        "pallet_volume_lower_bounds": {
            name: {"envelope_mm": list(envelope), "ratio": total_mm3 / math.prod(envelope),
                   "ceil_pallets": math.ceil(total_mm3 / math.prod(envelope))}
            for name, envelope in denominators.items()
        },
        "destination_distribution": {
            str(key): int(count)
            for key, count in df["Palet destino"].value_counts(dropna=False).sort_index().items()
        },
        "weight": weight_profile,
    }


def real_pallets_profile(path: Path) -> dict:
    if not path.exists():
        return {"available": False, "path": str(path), "message": "Excel real no disponible"}
    # Ignora la hoja de catálogo: no representa cajas del flujo.
    sheets = pd.read_excel(path, sheet_name=None)
    flows = {name: df for name, df in sheets.items()
             if {*DIM_COLUMNS, "Etiqueta pallet destino"}.issubset(df.columns)}
    if not flows:
        raise ValueError("Excel real sin hojas de cajas con Etiqueta pallet destino")
    df = pd.concat(flows.values(), ignore_index=True)
    df = df.assign(volume_mm3=dimensions(df).prod(axis=1))
    pallets = []
    for label, group in df.groupby("Etiqueta pallet destino", dropna=False, sort=True):
        volume = float(group["volume_mm3"].sum())
        pallets.append({
            "pallet": str(label), "boxes": len(group), "volume_m3": volume / 1e9,
            "estimated_equivalent_volumetric_utilization": volume / math.prod(HISTORICAL_ENVELOPE_MM),
        })
    return {
        "available": True, "path": str(path), "sheets_boxes": {k: len(v) for k, v in flows.items()},
        "ignored_sheets": [name for name in sheets if name not in flows],
        "boxes": len(df), "pallet_count": len(pallets),
        "utilization_label": "ESTIMACIÓN: utilización volumétrica equivalente; altura real desconocida",
        "utilization_denominator_mm": list(HISTORICAL_ENVELOPE_MM),
        "pallets": pallets,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("out/profile_flow.json"))
    args = parser.parse_args()
    out = args.out.resolve()
    if out.suffix.lower() != ".json" or any(
        out.is_relative_to(root.resolve()) for root in (REPO_ROOT / "data", REAL_EXCEL.parent)
    ):
        parser.error("--out debe ser JSON y estar fuera de los directorios data/")
    canonical = REPO_ROOT / REFERENCE_EXCEL
    # Igual que sim.io.load_arrivals: primera hoja del Excel canónico.
    profile = {
        "canonical_excel": REFERENCE_EXCEL,
        "volume_source": "Largo × Ancho × Alto (mm³); columna Volumen ignorada",
        "canonical": profile_frame(pd.read_excel(canonical)),
        "real_operator_pallets": real_pallets_profile(REAL_EXCEL),
    }
    payload = json.dumps(profile, ensure_ascii=False, indent=2, allow_nan=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
