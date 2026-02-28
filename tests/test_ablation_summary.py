from __future__ import annotations

import csv
import json

import pytest

from scripts import summarize_run
from scripts.ablation_runner import RunKpis, extract_kpis, variant_flags, write_csv


def test_extract_kpis_full_payload() -> None:
    payload = {
        "metrics": {
            "pallet_kpis": {
                "pallets_count": {"1": 3},
                "continuous_pallet_sequence": {"1": [12, 18, 21]},
                "pallet_volume_utilization": {"1": 0.92},
                "closures_by_reason": {"DEADLOCK_STABILITY": 2, "CLOSE_HEIGHT_FULL": 1},
                "orientation_counts": {"planar": 5, "stand_hw": 2},
                "stand_hw_used_total": 2,
                "rejected_by_support_ratio_pct": 0.11,
                "rejected_by_corner_support_pct": 0.07,
                "micro_plan_time_ms_mean": 6.5,
            }
        }
    }

    k = extract_kpis(payload, dest="1")

    assert k["pallets"] == 3
    assert k["seq_len"] == 3
    assert k["seq_sum"] == 51
    assert k["seq_avg"] == pytest.approx(17.0)
    assert k["seq_min"] == 12
    assert k["vol_util"] == pytest.approx(0.92)
    assert k["deadlock_stability"] == 2
    assert k["close_height_full"] == 1
    assert k["planar_count"] == 5
    assert k["stand_hw_count"] == 2
    assert k["stand_hw_used_total"] == 2
    assert k["rejected_support_pct"] == pytest.approx(0.11)
    assert k["rejected_corner_pct"] == pytest.approx(0.07)
    assert k["micro_plan_time_ms_mean"] == pytest.approx(6.5)


def test_extract_kpis_missing_keys_no_crash() -> None:
    payload = {"metrics": {"pallet_kpis": {}}}

    k = extract_kpis(payload, dest="1")

    assert k["pallets"] is None
    assert k["seq_len"] == 0
    assert k["seq_sum"] == 0
    assert k["seq_avg"] is None
    assert k["seq_min"] is None
    assert k["vol_util"] is None
    assert k["deadlock_stability"] == 0
    assert k["close_height_full"] == 0
    assert k["planar_count"] is None
    assert k["stand_hw_count"] is None
    assert k["stand_hw_used_total"] is None
    assert k["rejected_support_pct"] is None
    assert k["rejected_corner_pct"] is None
    assert k["micro_plan_time_ms_mean"] is None


def test_extract_kpis_non_list_sequence() -> None:
    payload = {
        "metrics": {
            "pallet_kpis": {
                "continuous_pallet_sequence": {"1": "not-a-list"},
            }
        }
    }

    k = extract_kpis(payload, dest="1")

    assert k["seq_len"] == 0
    assert k["seq_sum"] == 0
    assert k["seq_avg"] is None
    assert k["seq_min"] is None


def test_variant_flags_known_variants() -> None:
    assert variant_flags("planar") == ["--orientation-mode", "planar"]
    assert variant_flags("gate0") == [
        "--orientation-mode",
        "planar+stand_hw",
        "--stand-hw-height-margin-gate-mm",
        "0",
    ]
    assert variant_flags("gate400") == [
        "--orientation-mode",
        "planar+stand_hw",
        "--stand-hw-height-margin-gate-mm",
        "400",
    ]
    assert variant_flags("gate2400") == [
        "--orientation-mode",
        "planar+stand_hw",
        "--stand-hw-height-margin-gate-mm",
        "2400",
    ]


def test_variant_flags_unknown_raises() -> None:
    with pytest.raises(ValueError):
        variant_flags("bad-variant")


def test_write_csv_and_header(tmp_path) -> None:
    row = RunKpis(
        variant="planar",
        returncode=0,
        pallets=3,
        seq_len=3,
        seq_sum=51,
        seq_avg=17.0,
        seq_min=12,
        vol_util=0.92,
        deadlock_stability=2,
        close_height_full=1,
        planar_count=5,
        stand_hw_count=2,
        stand_hw_used_total=2,
        rejected_support_pct=0.11,
        rejected_corner_pct=0.07,
        micro_plan_time_ms_mean=6.5,
        out_json="/tmp/planar.json",
        out_log="/tmp/planar.log",
    )
    out_csv = tmp_path / "summary.csv"

    write_csv([row], out_csv)

    assert out_csv.exists()
    with out_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        values = next(reader)
    assert "variant" in header
    assert "micro_plan_time_ms_mean" in header
    assert "out_log" in header
    assert values[header.index("variant")] == "planar"


def test_summarize_run_single_json_to_csv(tmp_path, capsys) -> None:
    in_json = tmp_path / "gate400.json"
    payload = {"metrics": {"pallet_kpis": {"continuous_pallet_sequence": {"1": [4, 6]}}}}
    in_json.write_text(json.dumps(payload), encoding="utf-8")
    out_csv = tmp_path / "one.csv"

    rc = summarize_run.main(["--in", str(in_json), "--dest", "1", "--csv", str(out_csv)])

    assert rc == 0
    assert out_csv.exists()
    out = capsys.readouterr().out
    assert "variant" in out
    assert "gate400" in out
