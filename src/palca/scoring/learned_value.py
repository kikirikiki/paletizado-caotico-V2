"""R13: small, deterministic state value trained exclusively on GENERIC episodes.

Inference uses geometry only, with no stream metadata, clock, RNG or sklearn.
The JSON ridge model is loaded only when a learned value is explicitly selected.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path

import numpy as np

GRID_X, GRID_Y = 8, 6  # 155 x 140 mm on the production envelope
FEATURE_VERSION = "r13-geometry-v1"
MODEL_PATH = Path(__file__).with_name("models") / "r13_generic_v1.json"
SUMMARY_NAMES = (
    "max_height", "mean_height", "std_height", "floor_coverage", "placed_volume",
    "box_count_32", "profile_volume", "internal_void", "debt_volume", "debt_area",
    "well_volume", "floor_well_volume", "mid_well_volume", "upper_well_volume",
    "front_height", "rear_height", "left_height", "right_height",
    "roughness_x", "roughness_y",
)
FEATURE_NAMES = tuple(f"height_{y}_{x}" for y in range(GRID_Y) for x in range(GRID_X)) + SUMMARY_NAMES + tuple(
    f"{name}_squared" for name in SUMMARY_NAMES)


def envelope_volume(pallet) -> float:
    return float(pallet.spec.bin_area_mm2 * pallet.spec.max_height_mm)


def pallet_volume(pallet) -> float:
    return float(sum(p.length_mm * p.width_mm * p.height_mm for p in pallet.placements))


def state_features(pallet) -> np.ndarray:
    """Centre-sampled coarse profile + normalized geometry and H1-like wells.

    Wells have a neighbour within 300 mm more than the accessibility delta above
    them. This is a cheap descriptor, not a replacement for physical controls.
    All dimensions below are operational constants, independent of any catalog.
    """
    spec = pallet.spec
    x = spec.offset_mm + (np.arange(GRID_X) + .5) * spec.bin_length_mm / GRID_X
    y = spec.offset_mm + (np.arange(GRID_Y) + .5) * spec.bin_width_mm / GRID_Y
    heights = np.zeros((GRID_Y, GRID_X), dtype=float)
    for p in pallet.placements:
        a, b = np.searchsorted(x, [p.x_mm, p.x_mm + p.length_mm])
        c, d = np.searchsorted(y, [p.y_mm, p.y_mm + p.width_mm])
        np.maximum(heights[c:d, a:b], p.z_mm + p.height_mm, out=heights[c:d, a:b])
    xx, yy = np.meshgrid(x, y)
    near = ((xx.ravel()[:, None] - xx.ravel()) ** 2
            + (yy.ravel()[:, None] - yy.ravel()) ** 2 <= 300 ** 2)
    neighbour = np.max(np.where(near, heights.ravel()[None, :], 0), axis=1).reshape(heights.shape)
    delta = pallet.accessibility_delta_mm
    deficit = np.maximum(0, neighbour - delta - heights) if delta > 0 else np.zeros_like(heights)
    h = heights / spec.max_height_mm
    well = deficit > 0
    headroom = (1 - h) * well
    volume = pallet_volume(pallet) / envelope_volume(pallet)
    profile = float(h.mean())
    summary = np.array([
        pallet.current_height_mm() / spec.max_height_mm, profile, h.std(),
        (heights > 0).mean(), volume, len(pallet.placements) / 32,
        profile, max(0, profile - volume), (deficit / spec.max_height_mm).mean(), well.mean(),
        headroom.mean(), (headroom * (h == 0)).mean(),
        (headroom * ((h > 0) & (h <= 1/3))).mean(), (headroom * (h > 1/3)).mean(),
        h[:GRID_Y//2].mean(), h[GRID_Y//2:].mean(),
        h[:, :GRID_X//2].mean(), h[:, GRID_X//2:].mean(),
        np.abs(np.diff(h, axis=1)).mean(), np.abs(np.diff(h, axis=0)).mean(),
    ], dtype=float)
    return np.concatenate((h.ravel(), summary, summary ** 2))


@lru_cache(maxsize=1)
def load_model():
    model = json.loads(MODEL_PATH.read_text())
    if model["feature_version"] != FEATURE_VERSION or model["feature_names"] != list(FEATURE_NAMES):
        raise ValueError("Incompatible R13 model feature schema")
    arrays = [np.asarray(model[key], dtype=float) for key in ("mean", "scale", "coef")]
    if any(a.shape != (len(FEATURE_NAMES),) or not np.isfinite(a).all() for a in arrays):
        raise ValueError("Invalid R13 model coefficients")
    if np.any(arrays[1] <= 0) or not np.isfinite(model["intercept"]):
        raise ValueError("Invalid R13 model scaling/intercept")
    for a in arrays:
        a.setflags(write=False)
    return (*arrays, float(model["intercept"]))


def predict_utilization(pallet) -> float:
    mean, scale, coef, intercept = load_model()
    prediction = intercept + float(np.dot((state_features(pallet) - mean) / scale, coef))
    current = pallet_volume(pallet) / envelope_volume(pallet)
    return float(np.clip(prediction, current, 1.0))


def remaining_value(pallets) -> float:
    """Expected additional volume; callers already count placed volume once."""
    return sum(max(0.0, predict_utilization(p) * envelope_volume(p) - pallet_volume(p))
               for p in pallets.values())
