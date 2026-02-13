from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import palca.analysis.feasibility_bound as fb

FORBIDDEN_ORIENTS = {"LHW", "HLW"}


@pytest.mark.slow
def test_feasibility_bound_dest1_overhang20_target21(monkeypatch) -> None:
    monkeypatch.setenv("PALCA_ALLOW_LH_BASE", "0")
    importlib.reload(fb)

    excel_path = Path("data") / "Flujo_dest1.xlsx"
    assert excel_path.exists(), "Dataset fijo no encontrado en data/Flujo_dest1.xlsx"

    report = fb.run_feasibility_bound(
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
        enforce_mode="max",
        enforce_min_target=0,
        verify_2d=True,
        verify_time_limit_s=10.0,
    )

    summary = report["summary"]
    report_input = report["input"]
    target_mode = report["target_mode"]
    maximize_mode = report["maximize_mode"]
    enforce_result = target_mode["enforce_2d_result"]

    assert bool(report_input["forbid_lh_base"]) is True
    assert bool(report_input["allow_lh_base_override_env"]) is False

    assert target_mode["enforce_2d"] is True
    enforce_status = target_mode["enforce_2d_status"]
    assert enforce_status == "SAT"
    assert summary["enforce_2d_status"] == enforce_status

    assert int(enforce_result["selected_count"]) >= 1
    assert int(enforce_result["height_mm"]) <= 2400
    assert int(enforce_result["volume_mm3_total"]) > 0
    assert float(enforce_result["fill_ratio"]) > 0.0
    per_layer = enforce_result["per_layer"]
    assert isinstance(per_layer, list)
    assert per_layer
    assert any(layer["coords"] for layer in per_layer)
    for layer in per_layer:
        for coord in layer["coords"]:
            assert str(coord["orient_name"]) not in FORBIDDEN_ORIENTS

    verify_result = target_mode["verify_2d_result"]
    assert verify_result["status"] in {"SAT", "UNSAT", "UNKNOWN", "SKIPPED", "SKIPPED_TARGET_UNSAT"}
    assert target_mode["verify_2d_note"].startswith("post_check_only")

    for layer in maximize_mode["layers"]:
        for item in layer["items"]:
            assert str(item["orient_name"]) not in FORBIDDEN_ORIENTS
