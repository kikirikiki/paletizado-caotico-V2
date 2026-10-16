import importlib.util
from pathlib import Path

import pandas as pd
import pytest


spec = importlib.util.spec_from_file_location(
    "profile_flow", Path(__file__).resolve().parents[1] / "scripts/profile_flow.py"
)
profile_flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile_flow)


def test_geometric_volume_families_and_missing_weight():
    frame = pd.DataFrame({
        "Largo": [600, 600, 612.5], "Ancho": [400, 400, 400], "Alto": [300, 300, 300],
        "Palet destino": [1, 2, 1], "Volumen": [1, 1, 1],
    })
    result = profile_flow.profile_frame(frame)
    assert result["total_volume_m3"] == pytest.approx(0.2175)
    assert result["distinct_references"] == 2
    assert result["top10"][0] == {"lwh_mm": [600, 400, 300], "boxes": 2}
    assert result["families_round25mm"][1]["lwh_mm"] == [625, 400, 300]
    assert result["weight"] == {"present": False}
    assert result["destination_distribution"] == {"1": 2, "2": 1}
    assert result["pallet_volume_lower_bounds"]["historical_1240x820x2400"]["ratio"] == pytest.approx(
        217500000 / (1240 * 820 * 2400)
    )


def test_real_pallets_read_all_flow_sheets_and_skip_catalog(monkeypatch, tmp_path):
    path = tmp_path / "real.xlsx"
    assert profile_flow.real_pallets_profile(path)["available"] is False
    path.touch()
    flow = pd.DataFrame({
        "Largo": [600], "Ancho": [400], "Alto": [300], "Etiqueta pallet destino": ["PA1"],
    })
    monkeypatch.setattr(profile_flow.pd, "read_excel", lambda *a, **kw: {
        "Rampa1": flow, "Rampa2": flow, "Catálogo": pd.DataFrame({"ID": [1]}),
    })
    result = profile_flow.real_pallets_profile(path)
    assert result["boxes"] == 2
    assert result["pallet_count"] == 1
    assert result["ignored_sheets"] == ["Catálogo"]
    assert result["pallets"][0]["volume_m3"] == pytest.approx(0.144)
    assert "ESTIMACIÓN" in result["utilization_label"]


def test_weight_statistics_preserve_original_units():
    result = profile_flow.profile_frame(pd.DataFrame({
        "Largo": [600, 600], "Ancho": [400, 400], "Alto": [300, 300],
        "Palet destino": [1, 1], "Peso Contenedor": [1000, None],
    }))
    assert result["weight"]["count"] == 1
    assert result["weight"]["missing"] == 1
    assert result["weight"]["mean"] == 1000
    assert "sin conversión" in result["weight"]["unit"]


@pytest.mark.parametrize("destination", [
    profile_flow.REPO_ROOT / "data/profile_should_not_be_written.json",
    profile_flow.REAL_EXCEL.parent / "profile_should_not_be_written.json",
    profile_flow.REPO_ROOT / "out/profile_should_not_be_written.xlsx",
])
def test_output_guard_rejects_data_directories_and_non_json(monkeypatch, destination):
    monkeypatch.setattr(profile_flow.sys, "argv", ["profile_flow.py", "--out", str(destination)])
    with pytest.raises(SystemExit) as exc:
        profile_flow.main()
    assert exc.value.code == 2
    assert not destination.exists()
