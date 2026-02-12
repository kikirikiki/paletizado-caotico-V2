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
        verify_2d=True,
        verify_time_limit_s=10.0,
    )

    summary = report["summary"]
    target_mode = report["target_mode"]

    status = summary["status"]
    assert status in {"SAT", "UNSAT"}

    if status == "UNSAT":
        assert target_mode["status"] == "UNSAT", (
            "El modo target reportó UNSAT para N=21 bajo el bound optimista por capas; "
            "esto es evidencia fuerte de imposibilidad."
        )
        assert "verify_2d_result" in target_mode
        assert target_mode["verify_2d"] is False
        assert target_mode["verify_2d_status"] in {"SKIPPED_TARGET_UNSAT", "UNSAT", "UNKNOWN"}
        return

    assert target_mode["status"] == "SAT"
    assert int(target_mode["selected_count"]) >= 21
    assert int(target_mode["min_height_mm"]) <= 2400

    layers = target_mode["layers"]
    packed_count = sum(int(layer["count"]) for layer in layers)
    assert packed_count == int(target_mode["selected_count"])

    verify_result = target_mode["verify_2d_result"]
    assert "verify_2d" in verify_result
    assert "status" in verify_result
    assert "per_layer" in verify_result

    verify_status = verify_result["status"]
    assert verify_status in {"SAT", "UNSAT", "UNKNOWN"}
    assert verify_result["verify_2d"] == (verify_status == "SAT")
    if verify_status == "SAT":
        assert all(layer["status"] == "SAT" for layer in verify_result["per_layer"])
