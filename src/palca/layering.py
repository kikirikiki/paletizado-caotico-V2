from __future__ import annotations

DEFAULT_LAYER_BAND_MM = 100


def layer_idx_from_base_z_mm(base_z_mm: int | float | None, *, layer_band_mm: int = DEFAULT_LAYER_BAND_MM) -> int:
    band_mm = max(1, int(layer_band_mm))
    try:
        z_mm = int(base_z_mm) if base_z_mm is not None else 0
    except Exception:
        z_mm = 0
    if z_mm < 0:
        z_mm = 0
    return int(z_mm // band_mm)
