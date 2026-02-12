from __future__ import annotations

import importlib

import palca.analysis.feasibility_bound as fb


FORBIDDEN_ORIENTS = {"LHW", "HLW"}


def _reload_feasibility_bound(monkeypatch, allow_lh_value: str | None) -> None:
    if allow_lh_value is None:
        monkeypatch.delenv("PALCA_ALLOW_LH_BASE", raising=False)
    else:
        monkeypatch.setenv("PALCA_ALLOW_LH_BASE", allow_lh_value)
    importlib.reload(fb)


def test_enforce_2d_synthetic_two_rects_fit_single_layer(monkeypatch) -> None:
    _reload_feasibility_bound(monkeypatch, "0")
    items = [
        fb.BoundItem(
            item_idx=0,
            row_idx=100,
            length_mm=5,
            width_mm=5,
            height_mm=4,
            orientations=fb._deduplicated_orientations(5, 5, 4),
        ),
        fb.BoundItem(
            item_idx=1,
            row_idx=101,
            length_mm=5,
            width_mm=5,
            height_mm=4,
            orientations=fb._deduplicated_orientations(5, 5, 4),
        ),
    ]

    result = fb._solve_target_enforce_2d(
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
        assert str(c["orient_name"]) not in FORBIDDEN_ORIENTS


def test_enforce_2d_sat_mode_returns_non_empty_coords(monkeypatch) -> None:
    _reload_feasibility_bound(monkeypatch, "0")
    items = [
        fb.BoundItem(
            item_idx=0,
            row_idx=200,
            length_mm=6,
            width_mm=4,
            height_mm=3,
            orientations=fb._deduplicated_orientations(6, 4, 3),
        ),
        fb.BoundItem(
            item_idx=1,
            row_idx=201,
            length_mm=4,
            width_mm=4,
            height_mm=3,
            orientations=fb._deduplicated_orientations(4, 4, 3),
        ),
        fb.BoundItem(
            item_idx=2,
            row_idx=202,
            length_mm=5,
            width_mm=3,
            height_mm=3,
            orientations=fb._deduplicated_orientations(5, 3, 3),
        ),
    ]

    result = fb._solve_target_enforce_2d(
        items=items,
        base_length_mm=10,
        base_width_mm=10,
        base_area_mm2=100,
        hmax_mm=20,
        max_layers=2,
        target=2,
        time_limit_s=2.0,
        random_seed=123,
        enforce_mode="sat",
    )

    assert result["status"] == "SAT"
    assert int(result["selected_count"]) == 2
    per_layer = result["per_layer"]
    assert per_layer
    assert any(layer["coords"] for layer in per_layer)
    for layer in per_layer:
        for coord in layer["coords"]:
            assert str(coord["orient_name"]) not in FORBIDDEN_ORIENTS


def test_override_env_reenables_lh_base_orientations(monkeypatch) -> None:
    _reload_feasibility_bound(monkeypatch, "1")
    names_with_override = {o.orient_name for o in fb._deduplicated_orientations(10, 8, 6)}
    assert "LHW" in names_with_override
    assert "HLW" in names_with_override

    _reload_feasibility_bound(monkeypatch, "0")
