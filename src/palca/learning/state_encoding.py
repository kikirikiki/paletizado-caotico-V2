"""Observed geometry only; axes of height are [x, y], cells overlap footprints."""
from __future__ import annotations

import numpy as np

from ..scoring.learned_value import envelope_volume, pallet_volume

ENCODING_VERSION = "g1-v1"


def encode_state(pallet, visible_boxes) -> dict[str, np.ndarray]:
    spec = pallet.spec
    if (spec.bin_length_mm, spec.bin_width_mm, spec.max_height_mm) != (1240, 840, 2400):
        raise ValueError("g1-v1 requires the production 1240 x 840 x 2400 envelope")
    height = np.zeros((62, 42), dtype=np.uint16)
    origin = spec.offset_mm
    for p in pallet.placements:
        a = max(0, (p.x_mm - origin) // 20)
        b = min(62, (p.x_mm + p.length_mm - origin + 19) // 20)
        c = max(0, (p.y_mm - origin) // 20)
        d = min(42, (p.y_mm + p.width_mm - origin + 19) // 20)
        np.maximum(height[a:b, c:d], p.z_mm + p.height_mm, out=height[a:b, c:d])
    visible = list(visible_boxes)
    if len(visible) > 15:
        raise ValueError("g1-v1 accepts at most 15 visible boxes")
    boxes = np.zeros((15, 4), dtype=np.float32)
    for i, box in enumerate(visible):
        boxes[i, :3] = np.asarray(sorted(
            (box.length_mm, box.width_mm, box.height_mm), reverse=True)) / 1000
        boxes[i, 3] = 1
    top = max((p.z_mm + p.height_mm for p in pallet.placements), default=0)
    scalars = np.asarray([pallet_volume(pallet) / envelope_volume(pallet),
                          len(pallet.placements) / 100, top / 2400, len(visible) / 15],
                         dtype=np.float32)
    return dict(height=height, boxes=boxes, scalars=scalars)
