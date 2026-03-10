from __future__ import annotations

from palca.integration.late_stand_diagnostics import (
    LATE_STAND_GENERATED_BUT_REJECTED,
    LATE_STAND_NOT_GENERATED,
    VIABLE_LATE_STAND_EXISTS,
    build_consolidated_late_stand_report,
    build_seed_late_stand_report_from_dump,
)


def _base_dump(*, selection_pool: list[dict], evaluated_items: list[dict]) -> dict:
    return {
        "params": {
            "force_destination": 1,
            "overhang_mm": 20,
            "heuristic": "bssf",
            "min_support": 0.85,
            "max_height_mm": 2600,
            "max_overweight_ratio": 1.5,
            "orientation_mode": "planar+stand_hw",
            "stand_hw_height_margin_gate_mm": 200,
        },
        "pallets": {
            "1": [
                {
                    "step_index": 14,
                    "box_id": 100,
                    "x_mm": 0,
                    "y_mm": 0,
                    "z_mm": 400,
                    "length_mm": 400,
                    "width_mm": 300,
                    "height_mm": 200,
                    "layer_id": 1,
                },
                {
                    "step_index": 15,
                    "box_id": 101,
                    "x_mm": 0,
                    "y_mm": 300,
                    "z_mm": 100,
                    "length_mm": 400,
                    "width_mm": 300,
                    "height_mm": 200,
                    "layer_id": 1,
                    "orientation_family": "planar",
                    "orientation_name": "LWH",
                },
            ]
        },
        "decision_trace": {
            "1": [
                {"step_index": 14, "selection_pool": [], "evaluated_items": []},
                {
                    "step_index": 15,
                    "selection_pool": selection_pool,
                    "evaluated_items": evaluated_items,
                    "selected": selection_pool[0] if selection_pool else None,
                },
            ]
        },
    }


def _candidate(
    *,
    box_id: int,
    z_mm: int,
    score: float,
    orientation_family: str,
    orientation_name: str,
    feasible_candidates_top: list[dict] | None = None,
) -> dict:
    debug = {}
    if feasible_candidates_top is not None:
        debug["feasible_candidates_top"] = feasible_candidates_top
    return {
        "ramp_id": 1,
        "buffer_index": 0,
        "box_id": box_id,
        "box_length_mm": 400,
        "box_width_mm": 300,
        "box_height_mm": 200,
        "pallet_id": 1,
        "preview": {
            "feasible": True,
            "placement": {
                "x_mm": 0,
                "y_mm": 0,
                "z_mm": z_mm,
                "length_mm": 400,
                "width_mm": 300,
                "height_mm": 200,
                "layer_id": 1,
                "rot90": False,
                "orientation_family": orientation_family,
                "orientation_name": orientation_name,
            },
            "debug": debug,
        },
        "terms": {"scalar_score": score},
        "score": score,
    }


def test_late_stand_classification_viable_generated_rejected_not_generated() -> None:
    viable_dump = _base_dump(
        selection_pool=[
            _candidate(
                box_id=101,
                z_mm=100,
                score=12.0,
                orientation_family="planar",
                orientation_name="LWH",
            ),
            _candidate(
                box_id=202,
                z_mm=500,
                score=10.0,
                orientation_family="stand_hw",
                orientation_name="HWL",
            ),
        ],
        evaluated_items=[],
    )
    viable = build_seed_late_stand_report_from_dump(dump_payload=viable_dump, seed=50021)
    assert viable["critical_reentry_steps"][0]["classification"] == VIABLE_LATE_STAND_EXISTS

    rejected_dump = _base_dump(
        selection_pool=[
            _candidate(
                box_id=101,
                z_mm=100,
                score=12.0,
                orientation_family="planar",
                orientation_name="LWH",
            ),
        ],
        evaluated_items=[
            {
                "box_id": 303,
                "box_length_mm": 400,
                "box_width_mm": 300,
                "box_height_mm": 200,
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
                                "z_mm": 500,
                                "length_mm": 200,
                                "width_mm": 300,
                                "height_mm": 400,
                                "orientation_family": "stand_hw",
                                "orientation_name": "HWL",
                                "rejection_reason": "SUPPORT_RATIO",
                                "support_ratio": 0.81,
                                "required_support_ratio": 0.92,
                            }
                        ]
                    },
                },
            }
        ],
    )
    rejected = build_seed_late_stand_report_from_dump(dump_payload=rejected_dump, seed=50022)
    assert rejected["critical_reentry_steps"][0]["classification"] == LATE_STAND_GENERATED_BUT_REJECTED

    hidden_dump = _base_dump(
        selection_pool=[
            _candidate(
                box_id=101,
                z_mm=100,
                score=12.0,
                orientation_family="planar",
                orientation_name="LWH",
                feasible_candidates_top=[
                    {
                        "x_mm": 0,
                        "y_mm": 0,
                        "z_mm": 500,
                        "length_mm": 200,
                        "width_mm": 300,
                        "height_mm": 400,
                        "layer_id": 1,
                        "rot90": False,
                        "orientation_family": "stand_hw",
                        "orientation_name": "HWL",
                        "objective": 9.5,
                    }
                ],
            ),
        ],
        evaluated_items=[],
    )
    hidden = build_seed_late_stand_report_from_dump(dump_payload=hidden_dump, seed=50023)
    assert hidden["critical_reentry_steps"][0]["classification"] == LATE_STAND_NOT_GENERATED


def test_late_stand_consolidated_report_serializable_shape() -> None:
    dump_payload = _base_dump(
        selection_pool=[
            _candidate(
                box_id=101,
                z_mm=100,
                score=12.0,
                orientation_family="planar",
                orientation_name="LWH",
            ),
            _candidate(
                box_id=202,
                z_mm=500,
                score=10.0,
                orientation_family="stand_hw",
                orientation_name="HWL",
            ),
        ],
        evaluated_items=[],
    )
    report = build_seed_late_stand_report_from_dump(dump_payload=dump_payload, seed=50021)
    consolidated = build_consolidated_late_stand_report([report])

    assert len(consolidated["rows"]) == 1
    assert len(consolidated["breakdown_rows"]) >= 1
    summary = consolidated["summary"]
    assert int(summary["critical_steps_total"]) == 1
    assert int(summary["viable_late_stand_exists"]) == 1
    assert "reason_pareto" in summary
