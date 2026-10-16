"""KPI congelado. NO modificar sin PR propio y revisión adversarial.

Las razones END se excluyen del KPI oficial. Los ratios se expresan en [0, 1]
(sin recortar valores inválidos); solo paired_comparison usa puntos porcentuales.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import isfinite, prod, sqrt
from statistics import mean, stdev
from typing import Mapping, Sequence

from .params import MAX_LOAD_HEIGHT_MM, PALLET_NOMINAL_MM, pallet_envelope_mm

METRIC_VERSION = "1.0.0"
ENVELOPE_MM = pallet_envelope_mm(20)
NOMINAL_MM = (*PALLET_NOMINAL_MM, MAX_LOAD_HEIGHT_MM)


@dataclass
class PalletRecord:
    dest: str
    index: int  # Índice físico dentro del destino, empezando en 1.
    n_boxes: int
    box_volume_mm3: float
    z_max_mm: float
    closure_reason: str
    closed: bool
    items: list[dict]


def _by_dest(mapping: Mapping) -> dict:
    normalized = {str(key): value for key, value in mapping.items()}
    if len(normalized) != len(mapping):
        raise ValueError("Claves de destino duplicadas tras normalizar a str")
    return normalized


def split_pallets(payload: dict, dump: dict) -> list[PalletRecord]:
    """Reconstruye palets físicos; rechaza dumps truncados y cierres sin orden."""
    try:
        kpis = payload["metrics"]["pallet_kpis"]
        sequences = _by_dest(kpis["continuous_pallet_sequence"])
        reasons = _by_dest(kpis["continuous_closure_sequence"])
        pallets = _by_dest(dump["pallets"])
    except KeyError as exc:
        raise ValueError(f"Falta telemetría necesaria: {exc}") from exc
    if reasons.keys() - sequences.keys() or pallets.keys() - sequences.keys():
        raise ValueError("Destinos sin continuous_pallet_sequence")
    records = []
    for dest, counts in sequences.items():
        items = pallets.get(dest, [])
        closures = reasons.get(dest)
        if closures is None or len(counts) != len(closures):
            raise ValueError(f"Destino {dest}: faltan motivos de cierre o longitud distinta")
        if any(type(count) is not int or count <= 0 for count in counts):
            raise ValueError(f"Destino {dest}: conteos de palet deben ser enteros positivos")
        if sum(counts) != len(items):
            raise ValueError(f"Destino {dest}: suma secuencia {sum(counts)} != nº items {len(items)}")
        offset = 0
        for index, (count, reason) in enumerate(zip(counts, closures), start=1):
            if not isinstance(reason, str) or not reason.strip() or reason != reason.strip():
                raise ValueError(f"Destino {dest}: motivo de cierre inválido")
            if reason == "END" and index != len(counts):
                raise ValueError(f"Destino {dest}: END debe ser el último palet")
            group = deepcopy(items[offset:offset + count])
            volume = 0.0
            z_max = 0.0
            for item in group:
                dims = [float(item[key]) for key in ("length_mm", "width_mm", "height_mm")]
                z = float(item["z_mm"])
                if any(not isfinite(dim) or dim <= 0 for dim in dims) or not isfinite(z):
                    raise ValueError(f"Destino {dest}: dimensiones/z inválidos")
                volume += prod(dims)
                z_max = max(z_max, z + dims[2])
            records.append(PalletRecord(dest, index, count, volume, z_max,
                                        reason, reason != "END", group))
            offset += count
    return records


def pallet_utilization(rec: PalletRecord) -> float:
    return rec.box_volume_mm3 / prod(ENVELOPE_MM)


def run_kpi(records: Sequence[PalletRecord]) -> dict:
    """KPI/eff_real/h_fill sobre cerrados; listas y conteos sobre todos.

    Sin cerrados, los indicadores oficiales son None (no un cero inventado).
    """
    closed = [rec for rec in records if rec.closed]
    volume = sum(rec.box_volume_mm3 for rec in closed)
    n_closed = len(closed)
    return {
        "metric_version": METRIC_VERSION,
        "kpi": volume / (n_closed * prod(ENVELOPE_MM)) if n_closed else None,
        "kpi_nominal": volume / (n_closed * prod(NOMINAL_MM)) if n_closed else None,
        "n_pallets_total": len(records),
        "n_closed": n_closed,
        "boxes_placed": sum(rec.n_boxes for rec in records),
        "pallet_utils": [pallet_utilization(rec) for rec in records],
        "eff_real": mean(rec.box_volume_mm3 / (prod(ENVELOPE_MM[:2]) * rec.z_max_mm)
                         for rec in closed) if n_closed else None,
        "h_fill": mean(rec.z_max_mm / ENVELOPE_MM[2] for rec in closed) if n_closed else None,
        "kpi_all_pallets": mean(pallet_utilization(rec) for rec in records) if records else None,
    }


# t_{0.975,df}, df=1..120, redondeado a 9 decimales. Sin scipy en runtime.
# Tabla evaluada con la integral de cos(theta)^(df-1), theta=atan(t/sqrt(df)).
_T975 = (
    12.706204736, 4.302652730, 3.182446305, 2.776445105, 2.570581836,
    2.446911851, 2.364624252, 2.306004135, 2.262157163, 2.228138852,
    2.200985160, 2.178812830, 2.160368656, 2.144786688, 2.131449546,
    2.119905299, 2.109815578, 2.100922040, 2.093024054, 2.085963447,
    2.079613845, 2.073873068, 2.068657610, 2.063898562, 2.059538553,
    2.055529439, 2.051830516, 2.048407142, 2.045229642, 2.042272456,
    2.039513446, 2.036933343, 2.034515297, 2.032244509, 2.030107928,
    2.028094001, 2.026192463, 2.024394164, 2.022690920, 2.021075390,
    2.019540970, 2.018081703, 2.016692199, 2.015367574, 2.014103389,
    2.012895599, 2.011740514, 2.010634758, 2.009575237, 2.008559112,
    2.007583770, 2.006646805, 2.005745995, 2.004879288, 2.004044783,
    2.003240719, 2.002465459, 2.001717484, 2.000995378, 2.000297822,
    1.999623585, 1.998971517, 1.998340543, 1.997729654, 1.997137908,
    1.996564419, 1.996008354, 1.995468931, 1.994945415, 1.994437112,
    1.993943368, 1.993463567, 1.992997126, 1.992543495, 1.992102154,
    1.991672610, 1.991254395, 1.990847069, 1.990450210, 1.990063421,
    1.989686323, 1.989318557, 1.988959780, 1.988609667, 1.988267907,
    1.987934206, 1.987608282, 1.987289865, 1.986978700, 1.986674541,
    1.986377154, 1.986086317, 1.985801814, 1.985523442, 1.985251004,
    1.984984312, 1.984723186, 1.984467455, 1.984216952, 1.983971519,
    1.983731003, 1.983495259, 1.983264145, 1.983037526, 1.982815274,
    1.982597262, 1.982383370, 1.982173483, 1.981967490, 1.981765282,
    1.981566757, 1.981371815, 1.981180359, 1.980992298, 1.980807541,
    1.980626002, 1.980447599, 1.980272249, 1.980099876, 1.979930405,
)


def _seed_values(kpis: Sequence[float | Mapping]) -> list[float]:
    values = []
    for item in kpis:
        value = item["kpi"] if isinstance(item, Mapping) else item
        if value is None or not isfinite(float(value)):
            raise ValueError("Cada seed debe tener un KPI oficial finito y definido")
        values.append(float(value))
    return values


def _summary(values: Sequence[float]) -> dict:
    n = len(values)
    if not n:
        raise ValueError("Se necesita al menos una seed")
    avg = mean(values)
    sd = stdev(values) if n > 1 else None
    ci95 = None
    if sd is not None:
        quantile = _T975[n - 2] if n - 1 <= len(_T975) else 1.96
        margin = quantile * sd / sqrt(n)
        ci95 = [avg - margin, avg + margin]
    return {"mean": avg, "sd": sd, "n": n, "ci95": ci95,
            "min": min(values), "max": max(values)}


def aggregate_seeds(per_seed_kpis: Sequence[float | Mapping]) -> dict:
    """Media sin ponderar por palets; sd muestral e IC t bilateral del 95 %."""
    return _summary(_seed_values(per_seed_kpis))


def paired_comparison(kpis_a: Sequence[float | Mapping],
                      kpis_b: Sequence[float | Mapping]) -> dict:
    """B - A en pp. El llamante garantiza las mismas seeds y su mismo orden."""
    a, b = _seed_values(kpis_a), _seed_values(kpis_b)
    if len(a) != len(b):
        raise ValueError("Comparación pareada requiere las mismas seeds y orden")
    summary = _summary([100 * (vb - va) for va, vb in zip(a, b)])
    ci = summary["ci95"]
    return {"mean_diff_pp": summary["mean"], "sd_pp": summary["sd"],
            "n": summary["n"], "ci95_pp": ci,
            "significant": ci is not None and (ci[0] > 0 or ci[1] < 0)}
