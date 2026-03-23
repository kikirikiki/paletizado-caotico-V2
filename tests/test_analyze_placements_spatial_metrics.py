from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_analyze_placements_spatial_metrics(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    python_bin = repo_root / ".venv/bin/python"
    script_path = repo_root / "scripts/analyze_placements.py"

    placements_path = tmp_path / "placements.json"
    out_path = tmp_path / "analysis.json"

    placements = {
        "schema_version": 1,
        "pallets": {
            "1": [
                {"step_index": 0, "z_mm": 0, "x_mm": 10, "y_mm": 10},
                {"step_index": 1, "z_mm": 0, "x_mm": 20, "y_mm": 20},
                {"step_index": 2, "z_mm": 0, "x_mm": 30, "y_mm": 30},
                {"step_index": 3, "z_mm": 0, "x_mm": 40, "y_mm": 40},
                {"step_index": 4, "z_mm": 0, "x_mm": 50, "y_mm": 50},
                {"step_index": 5, "z_mm": 0, "x_mm": 60, "y_mm": 60},
                {"step_index": 6, "z_mm": 0, "x_mm": 110, "y_mm": 10},
                {"step_index": 7, "z_mm": 0, "x_mm": 120, "y_mm": 20},
            ]
        },
    }
    placements_path.write_text(json.dumps(placements), encoding="utf-8")

    subprocess.run(
        [
            str(python_bin),
            str(script_path),
            str(placements_path),
            "--xy-bin-mm",
            "100",
            "--out",
            str(out_path),
        ],
        cwd=repo_root,
        check=True,
    )

    out = json.loads(out_path.read_text(encoding="utf-8"))
    pallet = out["pallets"]["1"]
    assert pallet["xy_bin_mm"] == 100
    assert pallet["xy_bins_total"] == 2
    assert pallet["xy_bins_max_boxes"] == 6
    assert pallet["xy_bins_top3_boxes_percent"] == 1.0
    assert pallet["xy_bins_hist"].get("6") == 1
    assert pallet["xy_bins_hist"].get("2") == 1
    assert pallet["xy_bins_topk"][0]["count"] == 6
    assert pallet["xy_bins_gini"] > 0.0
