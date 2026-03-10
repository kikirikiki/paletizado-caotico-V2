from __future__ import annotations

from palca.integration.support_settle_diagnostics import (
    CLOSE_CLASS_FAR,
    CLOSE_CLASS_MODERATE,
    CLOSE_CLASS_NEAR,
    RESCUE_NO,
    RESCUE_PLAUSIBLE,
    RESCUE_YES,
    build_consolidated_support_settle_report,
    build_seed_support_settle_report_from_dump,
    classify_rejection_closeness,
)


def _dump_with_support_and_com_fail() -> dict:
    return {
        "params": {
            "force_destination": 1,
            "overhang_mm": 20,
            "heuristic": "bssf",
            "min_support": 0.92,
            "max_height_mm": 2600,
            "max_overweight_ratio": 1.5,
        },
        "pallets": {
            "1": [
                {
                    "step_index": 0,
                    "box_id": 10,
                    "x_mm": -20,
                    "y_mm": -20,
                    "z_mm": 0,
                    "length_mm": 400,
                    "width_mm": 300,
                    "height_mm": 200,
                    "layer_id": 0,
                },
                {
                    "step_index": 1,
                    "box_id": 11,
                    "x_mm": 380,
                    "y_mm": -20,
                    "z_mm": 300,
                    "length_mm": 400,
                    "width_mm": 300,
                    "height_mm": 200,
                    "layer_id": 1,
                },
                {
                    "step_index": 2,
                    "box_id": 12,
                    "x_mm": -20,
                    "y_mm": 280,
                    "z_mm": 100,
                    "length_mm": 400,
                    "width_mm": 300,
                    "height_mm": 200,
                    "layer_id": 1,
                },
            ]
        },
        "decision_trace": {
            "1": [
                {"step_index": 0, "selection_pool": [], "evaluated_items": []},
                {"step_index": 1, "selection_pool": [], "evaluated_items": []},
                {
                    "step_index": 2,
                    "selection_pool": [],
                    "evaluated_items": [
                        {
                            "box_id": 99,
                            "ramp_id": 2,
                            "buffer_index": 1,
                            "pallet_id": 1,
                            "feasible": False,
                            "preview": {
                                "infeasible_reason": "STABILITY",
                                "debug": {
                                    "stability_candidates": [
                                        {
                                            "x_mm": 0,
                                            "y_mm": 0,
                                            "z_mm": 320,
                                            "length_mm": 400,
                                            "width_mm": 300,
                                            "height_mm": 200,
                                            "rejection_reason": "SUPPORT_RATIO",
                                            "support_ratio": 0.90,
                                            "required_support_ratio": 0.92,
                                        },
                                        {
                                            "x_mm": 0,
                                            "y_mm": 0,
                                            "z_mm": 320,
                                            "length_mm": 400,
                                            "width_mm": 300,
                                            "height_mm": 200,
                                            "rejection_reason": "CORNER_SUPPORT",
                                            "corners_supported": True,
                                            "corners_supported_count": 4,
                                            "com_supported": False,
                                            "com_margin_mm": -8.0,
                                        },
                                    ]
                                },
                            },
                        }
                    ],
                    "selected": None,
                },
            ]
        },
    }


def test_classify_rejection_closeness_support_com_and_settle() -> None:
    support = classify_rejection_closeness(
        reason="support_surface_ratio_below_threshold",
        observed=0.90,
        threshold=0.92,
        height_also_blocked=False,
    )
    assert support["closeness_class"] == CLOSE_CLASS_NEAR
    assert support["could_be_rescued_by_support_settle_refinement"] == RESCUE_YES

    com = classify_rejection_closeness(
        reason="com_margin_fail",
        observed=-18.0,
        threshold=0.0,
        height_also_blocked=False,
    )
    assert com["closeness_class"] == CLOSE_CLASS_MODERATE
    assert com["could_be_rescued_by_support_settle_refinement"] == RESCUE_PLAUSIBLE

    settle = classify_rejection_closeness(
        reason="settle_failed",
        observed=85.0,
        threshold=0.0,
        height_also_blocked=True,
    )
    assert settle["closeness_class"] == CLOSE_CLASS_FAR
    assert settle["could_be_rescued_by_support_settle_refinement"] == RESCUE_NO


def test_support_settle_report_and_consolidation_shape() -> None:
    seed_report = build_seed_support_settle_report_from_dump(
        dump_payload=_dump_with_support_and_com_fail(),
        seed=50021,
        max_critical_steps=3,
        critical_window_radius=1,
    )
    assert seed_report["seed"] == 50021
    assert seed_report["pallet_id"] == "1"
    assert seed_report["critical_steps"] == [2]
    assert len(seed_report["rows"]) >= 2

    reasons = {str(item.get("reason")) for item in seed_report["rows"]}
    assert "support_surface_ratio_below_threshold" in reasons
    assert "com_margin_fail" in reasons

    consolidated = build_consolidated_support_settle_report([seed_report])
    assert len(consolidated["rows"]) >= 2
    assert int(consolidated["summary"]["rows_total"]) >= 2
    assert "reason_pareto" in consolidated["summary"]
    assert "closeness_pareto" in consolidated["summary"]
