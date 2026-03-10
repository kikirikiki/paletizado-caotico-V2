from __future__ import annotations

from palca.integration.reentry_diagnostics import (
    EVITABLE_BY_CANDIDATE_GENERATION,
    EVITABLE_BY_SELECTION,
    PROBABLY_UNAVOIDABLE,
    build_consolidated_reentry_report,
    build_seed_reentry_report_from_dump,
)


def _dump_with_single_reentry(*, selection_pool: list[dict], evaluated_items: list[dict]) -> dict:
    return {
        "params": {
            "force_destination": 1,
            "overhang_mm": 20,
            "heuristic": "bssf",
            "min_support": 0.85,
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
    feasible_candidates_top: list[dict] | None = None,
) -> dict:
    debug = {}
    if feasible_candidates_top is not None:
        debug["feasible_candidates_top"] = feasible_candidates_top
    return {
        "ramp_id": 1,
        "buffer_index": 0,
        "box_id": box_id,
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
            },
            "debug": debug,
        },
        "terms": {"scalar_score": score},
        "score": score,
    }


def test_reentry_classified_as_avoidable_by_selection() -> None:
    dump_payload = _dump_with_single_reentry(
        selection_pool=[
            _candidate(box_id=12, z_mm=100, score=12.0),
            _candidate(box_id=13, z_mm=320, score=10.0),
        ],
        evaluated_items=[],
    )

    report = build_seed_reentry_report_from_dump(dump_payload=dump_payload, seed=50021)
    assert int(report["reentry_count"]) == 1
    entry = report["reentries"][0]
    assert int(entry["step"]) == 2
    assert int(entry["drop_mm"]) == 200
    assert bool(entry["had_alternative_without_reentry"]) is True
    assert str(entry["classification"]) == EVITABLE_BY_SELECTION
    assert any(
        str(row.get("candidate_origin")) == "selection_pool"
        and bool(row.get("non_reentry_candidate"))
        for row in entry.get("breakdown_rows", [])
    )


def test_reentry_classified_as_candidate_generation_when_hidden_alt_exists() -> None:
    dump_payload = _dump_with_single_reentry(
        selection_pool=[
            _candidate(
                box_id=12,
                z_mm=100,
                score=12.0,
                feasible_candidates_top=[
                    {
                        "x_mm": 0,
                        "y_mm": 0,
                        "z_mm": 320,
                        "length_mm": 400,
                        "width_mm": 300,
                        "height_mm": 200,
                        "layer_id": 1,
                        "objective": 9.5,
                    }
                ],
            ),
        ],
        evaluated_items=[],
    )

    report = build_seed_reentry_report_from_dump(dump_payload=dump_payload, seed=50022)
    entry = report["reentries"][0]
    assert bool(entry["had_alternative_without_reentry"]) is False
    assert str(entry["classification"]) == EVITABLE_BY_CANDIDATE_GENERATION
    assert any(
        str(row.get("rejected_reason_exact")) == "candidate_not_generated"
        for row in entry.get("breakdown_rows", [])
    )


def test_reentry_classified_as_probably_unavoidable_with_support_threshold() -> None:
    dump_payload = _dump_with_single_reentry(
        selection_pool=[
            _candidate(box_id=12, z_mm=100, score=12.0),
        ],
        evaluated_items=[
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
                                "support_ratio": 0.81,
                                "required_support_ratio": 0.85,
                            }
                        ]
                    },
                },
            }
        ],
    )

    report = build_seed_reentry_report_from_dump(dump_payload=dump_payload, seed=50023)
    entry = report["reentries"][0]
    assert str(entry["classification"]) == PROBABLY_UNAVOIDABLE
    assert str(entry["dominant_cause"]) == "support_surface_ratio_below_threshold"

    support_rows = [
        row
        for row in entry.get("breakdown_rows", [])
        if str(row.get("rejected_reason_exact")) == "support_surface_ratio_below_threshold"
    ]
    assert support_rows
    assert float(support_rows[0].get("threshold_value", 0.0)) == 0.85
    assert float(support_rows[0].get("observed_value", 1.0)) == 0.81

    consolidated = build_consolidated_reentry_report([report])
    assert len(consolidated["rows"]) == 1
    assert len(consolidated["breakdown_rows"]) >= 1
    summary = consolidated["summary"]
    assert int(summary["total_reentries"]) == 1
    assert int(summary["probably_unavoidable"]) == 1
    assert any(
        str(item.get("group")) == "support"
        for item in summary.get("group_pareto", [])
    )
