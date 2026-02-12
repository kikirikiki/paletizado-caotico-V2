from __future__ import annotations

from pathlib import Path

import pytest

from palca.analysis.feasibility_bound import run_feasibility_bound


@pytest.mark.slow
def test_feasibility_bound_dest1_overhang20_target21() -> None:
    excel_path = Path("data") / "Flujo_dest1.xlsx"
    assert excel_path.exists(), "Dataset fijo no encontrado en data/Flujo_dest1.xlsx"

    report = run_feasibility_bound(
        excel_path=excel_path,
        dest=1,
        overhang_mm=20,
        hmax_mm=2400,
        target=21,
        max_layers=10,
        time_limit_s=10.0,
        random_seed=123,
        enforce_2d=True,
        enforce_time_limit_s=30.0,
        verify_2d=True,
        verify_time_limit_s=10.0,
    )

    summary = report["summary"]
    target_mode = report["target_mode"]
    enforce_result = target_mode["enforce_2d_result"]

    assert target_mode["enforce_2d"] is True
    enforce_status = target_mode["enforce_2d_status"]
    assert enforce_status in {"SAT", "UNSAT", "UNKNOWN"}
    assert summary["enforce_2d_status"] == enforce_status

    if enforce_status == "SAT":
        assert int(enforce_result["selected_count"]) >= 21
        assert int(enforce_result["height_mm"]) <= 2400
        per_layer = enforce_result["per_layer"]
        assert isinstance(per_layer, list)
        assert per_layer
        packed_count = sum(int(layer["count"]) for layer in per_layer)
        assert packed_count >= 21
        assert all(layer["coords"] for layer in per_layer)
    elif enforce_status == "UNSAT":
        assert bool(enforce_result["solver_status_proven"]) is True
        assert enforce_result["solver_status"] == "INFEASIBLE"
    else:
        assert enforce_result["solver_status"] in {"UNKNOWN", "FEASIBLE", "OPTIMAL"}
        assert float(enforce_result["time_limit_s"]) == pytest.approx(30.0)
        assert "per_layer" in enforce_result

    verify_result = target_mode["verify_2d_result"]
    assert verify_result["status"] in {"SAT", "UNSAT", "UNKNOWN", "SKIPPED", "SKIPPED_TARGET_UNSAT"}
    assert target_mode["verify_2d_note"].startswith("post_check_only")
