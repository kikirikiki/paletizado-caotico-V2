from __future__ import annotations

from palca.analysis.feasibility_bound import BoundItem, _deduplicated_orientations, _solve_target_enforce_2d


def test_enforce_2d_synthetic_two_rects_fit_single_layer() -> None:
    items = [
        BoundItem(
            item_idx=0,
            row_idx=100,
            length_mm=5,
            width_mm=5,
            height_mm=4,
            orientations=_deduplicated_orientations(5, 5, 4),
        ),
        BoundItem(
            item_idx=1,
            row_idx=101,
            length_mm=5,
            width_mm=5,
            height_mm=4,
            orientations=_deduplicated_orientations(5, 5, 4),
        ),
    ]

    result = _solve_target_enforce_2d(
        items=items,
        base_length_mm=10,
        base_width_mm=10,
        base_area_mm2=100,
        hmax_mm=20,
        max_layers=1,
        target=2,
        time_limit_s=2.0,
        random_seed=7,
    )

    assert result["status"] == "SAT"
    assert int(result["selected_count"]) >= 2
    assert int(result["height_mm"]) <= 20
    per_layer = result["per_layer"]
    assert len(per_layer) == 1
    coords = per_layer[0]["coords"]
    assert len(coords) == 2
    for c in coords:
        assert int(c["x_mm"]) >= 0
        assert int(c["y_mm"]) >= 0
        assert int(c["x_mm"]) + int(c["w_mm"]) <= 10
        assert int(c["y_mm"]) + int(c["h_mm"]) <= 10
