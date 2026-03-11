from __future__ import annotations

from palca.domain.pallet_spec import PalletSpec
from palca.integration.layer_pattern_audit import audit_layer_pattern_poison


def _base_params() -> dict[str, object]:
    return {
        "overhang_mm": 0,
        "stacking_mode": "heightfield",
        "orientation_mode": "planar",
        "stability_mode": "off",
    }


def test_audit_detects_better_counterfactual_in_synthetic_case() -> None:
    placements = [
        {
            "step_index": 0,
            "box_id": "A",
            "length_mm": 70,
            "width_mm": 70,
            "height_mm": 20,
            "x_mm": 0,
            "y_mm": 0,
            "z_mm": 0,
            "layer_id": 0,
            "orientation_name": "LWH",
            "orientation_family": "planar",
        },
        {
            "step_index": 1,
            "box_id": "B",
            "length_mm": 50,
            "width_mm": 50,
            "height_mm": 20,
            "x_mm": 0,
            "y_mm": 0,
            "z_mm": 0,
            "layer_id": 0,
            "orientation_name": "LWH",
            "orientation_family": "planar",
        },
        {
            "step_index": 2,
            "box_id": "C",
            "length_mm": 50,
            "width_mm": 50,
            "height_mm": 20,
            "x_mm": 50,
            "y_mm": 0,
            "z_mm": 0,
            "layer_id": 0,
            "orientation_name": "LWH",
            "orientation_family": "planar",
        },
    ]

    audit = audit_layer_pattern_poison(
        seed=123,
        placements=placements,
        params=_base_params(),
        counterfactual_top_k=3,
        audit_horizon_step=0,
        pallet_spec_override=PalletSpec(length_mm=100, width_mm=100, max_height_mm=200, overhang_mm=0),
    )
    step0 = next(row for row in audit["step_rows"] if int(row["step"]) == 0)

    assert bool(step0["counterfactual_better_same_step_exists"]) is True
    assert float(step0["counterfactual_best_fillability_delta"]) > 0.0
    assert "50x50x20" in str(step0["counterfactual_best_dims_orientation"])


def test_audit_reports_no_better_counterfactual_when_equivalent() -> None:
    placements = [
        {
            "step_index": 0,
            "box_id": "A",
            "length_mm": 50,
            "width_mm": 50,
            "height_mm": 20,
            "x_mm": 0,
            "y_mm": 0,
            "z_mm": 0,
            "layer_id": 0,
            "orientation_name": "LWH",
            "orientation_family": "planar",
        },
        {
            "step_index": 1,
            "box_id": "B",
            "length_mm": 50,
            "width_mm": 50,
            "height_mm": 20,
            "x_mm": 50,
            "y_mm": 0,
            "z_mm": 0,
            "layer_id": 0,
            "orientation_name": "LWH",
            "orientation_family": "planar",
        },
    ]

    audit = audit_layer_pattern_poison(
        seed=456,
        placements=placements,
        params=_base_params(),
        counterfactual_top_k=3,
        audit_horizon_step=0,
        pallet_spec_override=PalletSpec(length_mm=100, width_mm=100, max_height_mm=200, overhang_mm=0),
    )
    step0 = next(row for row in audit["step_rows"] if int(row["step"]) == 0)

    assert bool(step0["counterfactual_better_same_step_exists"]) is False
    assert abs(float(step0["counterfactual_best_fillability_delta"])) <= 1e-9
