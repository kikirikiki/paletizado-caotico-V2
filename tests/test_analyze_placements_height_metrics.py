from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_analyze_placements_height_metrics(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    python_bin = repo_root / ".venv/bin/python"
    script_path = repo_root / "scripts/analyze_placements.py"

    placements_path = tmp_path / "placements.json"
    out_path = tmp_path / "analysis.json"

    placements = {
        "schema_version": 1,
        "pallets": {
            "1": [
                {"step_index": 0, "x_mm": 10, "y_mm": 10, "z_mm": 0, "height_mm": 10},
                {"step_index": 1, "x_mm": 120, "y_mm": 20, "z_mm": 0, "height_mm": 20},
                {"step_index": 2, "x_mm": 20, "y_mm": 30, "z_mm": 30, "height_mm": 40},
                {"step_index": 3, "x_mm": 130, "y_mm": 20, "z_mm": 20, "height_mm": 30},
                {"step_index": 4, "x_mm": 30, "y_mm": 20, "z_mm": 80, "height_mm": 20},
                {"step_index": 5, "x_mm": 140, "y_mm": 10, "z_mm": 60, "height_mm": 10},
                {"step_index": 6, "x_mm": 40, "y_mm": 40, "z_mm": 90, "height_mm": 30},
                {"step_index": 7, "x_mm": 150, "y_mm": 20, "z_mm": 80, "height_mm": 50},
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
            "--tower-slope-window",
            "3",
            "--tower-early-end-step",
            "14",
            "--out",
            str(out_path),
        ],
        cwd=repo_root,
        check=True,
    )

    out = json.loads(out_path.read_text(encoding="utf-8"))
    pallet = out["pallets"]["1"]

    assert float(pallet["xy_bins_top_z_max_mm"]) == 130.0
    assert abs(float(pallet["xy_bins_top_z_roughness_mm"]) - 8.0) < 1e-9
    assert int(pallet["xy_bins_top_z_max_reached_step"]) == 7

    timeline = pallet["xy_bins_top_z_max_by_step"]
    assert len(timeline) == 8
    assert timeline == [10.0, 20.0, 70.0, 70.0, 100.0, 100.0, 120.0, 130.0]

    assert abs(float(pallet["xy_bins_top_z_growth_slope_max_early"]) - 80.0) < 1e-9
