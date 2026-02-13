from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.maxrects2d import MaxRects2D
from palca.packer.pallet_model import PalletModel


def test_seed_anchored_points_include_edges() -> None:
    spec = PalletSpec(length_mm=1000, width_mm=800, max_height_mm=2000, allow_rotate=True)
    model = PalletModel(spec=spec, heuristic="baf")

    base_box = Box(box_id=1, length_mm=500, width_mm=300, height_mm=200, timestamp=0.0)
    preview = model.preview_place(base_box)
    assert preview.feasible
    assert preview.placement is not None
    model.commit_place(preview)

    placement = preview.placement
    assert placement is not None

    top_z = placement.z_mm + placement.height_mm
    candidate_l = 400
    candidate_w = 200

    free_rects = MaxRects2D(spec.bin_length_mm, spec.bin_width_mm).free_rects
    seeds = model._seed_new_layer_points(top_z, candidate_l, candidate_w, free_rects=free_rects)

    sx = placement.x_mm - spec.offset_mm
    sy = placement.y_mm - spec.offset_mm
    sw = placement.length_mm
    sh = placement.width_mm

    assert (sx + sw - candidate_l, sy) in seeds
    assert (sx, sy + sh - candidate_w) in seeds
