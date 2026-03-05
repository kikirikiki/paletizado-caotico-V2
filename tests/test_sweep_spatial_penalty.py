from __future__ import annotations

import json

from scripts import sweep_spatial_penalty as sweep


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
