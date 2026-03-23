from __future__ import annotations

import csv
import json
from concurrent.futures import ThreadPoolExecutor

from scripts import sweep_spatial_penalty as sweep


def test_parse_jobs_and_fail_fast_flags() -> None:
    parser = sweep.build_parser()
    args = parser.parse_args(["--jobs", "8", "--fail-fast"])
    assert args.jobs == 8
    assert args.fail_fast is True

    defaults = parser.parse_args([])
    assert defaults.jobs == 1
    assert defaults.fail_fast is False


def test_parse_csv() -> None:
    vals = sweep.parse_csv_list("0.2, 0.4, , ,", float)
    assert vals == [0.2, 0.4]


def test_rank_key() -> None:
    a = {"status": "ok", "processed_boxes": 10, "reached_step": 999, "top3_pct": 0.9}
    b = {"status": "ok", "processed_boxes": 11, "reached_step": 1, "top3_pct": 0.9}
    assert sweep.rank_key(b) > sweep.rank_key(a)


def test_extract_metrics_from_files(tmp_path) -> None:
    out_json = tmp_path / "out.json"
    analysis_json = tmp_path / "analysis.json"

    out_json.write_text(
        json.dumps(
            {
                "metrics": {
                    "processed_boxes": 42,
                    "pallet_kpis": {
                        "spatial_tower_penalty_applied_count": 7,
                        "spatial_tower_selected_penalty_count": 3,
                        "spatial_tower_selected_penalty_mean": 1.25,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    analysis_json.write_text(
        json.dumps(
            {
                "pallets": {
                    "2": {
                        "xy_bins_max_boxes": 999,
                        "xy_bins_max_boxes_reached_step": 999,
                        "xy_bins_top3_boxes_percent": 0.99,
                        "xy_bins_gini": 0.99,
                    },
                    "1": {
                        "xy_bins_max_boxes": 5,
                        "xy_bins_max_boxes_reached_step": 13,
                        "xy_bins_top3_boxes_percent": 0.4,
                        "xy_bins_gini": 0.12,
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    m = sweep.extract_metrics_from_files(out_json, analysis_json)
    assert m["processed_boxes"] == 42
    assert m["spatial_applied"] == 7
    assert m["spatial_selected"] == 3
    assert m["spatial_selected_mean"] == 1.25
    assert m["max_boxes"] == 5
    assert m["reached_step"] == 13
    assert m["top3_pct"] == 0.4
    assert m["gini"] == 0.12


def test_parallel_main_writes_runs_and_top_configs(tmp_path, monkeypatch) -> None:
    out_root = tmp_path / "sweep_out"

    monkeypatch.setattr(sweep, "ProcessPoolExecutor", ThreadPoolExecutor)
    monkeypatch.setattr(sweep, "detect_spatial_flags", lambda *_: (True, [], ""))

    def fake_worker(payload: dict[str, object]) -> dict[str, object]:
        cfg = payload["cfg"]
        rep_id = int(payload["rep_id"])
        out_root_local = payload["out_root"]
        run_dir = out_root_local / "runs" / cfg.config_id / f"rep_{rep_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return {
            "status": "ok",
            "error_msg": "",
            "config_id": cfg.config_id,
            "rep_id": rep_id,
            "bin_mm": cfg.bin_mm,
            "w": cfg.weight,
            "end_step": cfg.end_step,
            "base": cfg.target_base,
            "div": cfg.target_div,
            "processed_boxes": 100.0 + float(cfg.weight),
            "reached_step": 10.0 + float(cfg.weight),
            "top3_pct": 0.2 + (float(cfg.weight) / 10.0),
            "max_boxes": 5.0,
            "gini": 0.1,
            "spatial_applied": 2.0,
            "spatial_selected": 1.0,
            "spatial_selected_mean": 0.5,
            "run_dir": str(run_dir),
        }

    monkeypatch.setattr(sweep, "_run_single_worker", fake_worker)

    rc = sweep.main(
        [
            "--phase",
            "explore",
            "--out-root",
            str(out_root),
            "--jobs",
            "2",
            "--xy-bin-mm-list",
            "150",
            "--weight-list",
            "0.2,0.4,0.8",
            "--end-step-list",
            "10",
            "--target-base-list",
            "1",
            "--target-div-list",
            "4",
        ]
    )
    assert rc == 0

    runs_csv = out_root / "runs.csv"
    assert runs_csv.exists()
    with runs_csv.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3

    top_json = out_root / "top_configs.json"
    assert top_json.exists()
    payload = json.loads(top_json.read_text(encoding="utf-8"))
    assert "top_configs" in payload
    assert len(payload["top_configs"]) == 3
