from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_analyze_placements_spatial_timeline(tmp_path: Path) -> None:
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
                {"step_index": 1, "z_mm": 0, "x_mm": 110, "y_mm": 10},
                {"step_index": 2, "z_mm": 0, "x_mm": 10, "y_mm": 110},
                {"step_index": 3, "z_mm": 0, "x_mm": 110, "y_mm": 110},
                {"step_index": 4, "z_mm": 0, "x_mm": 20, "y_mm": 20},
                {"step_index": 5, "z_mm": 0, "x_mm": 30, "y_mm": 30},
                {"step_index": 6, "z_mm": 0, "x_mm": 40, "y_mm": 40},
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

    assert pallet["xy_bins_top1_count_by_step"] == [1, 1, 1, 1, 2, 3, 4]
    assert pallet["xy_bins_max_boxes_reached_step"] == 6
    assert pallet["xy_bins_top1_bin_by_step"] == [{"bx": 0, "by": 0}] * 7

    top3_pct = pallet["xy_bins_top3_boxes_percent_by_step"]
    assert len(top3_pct) == 7
    assert abs(float(top3_pct[3]) - 0.75) < 1e-9
    assert abs(float(top3_pct[6]) - (6.0 / 7.0)) < 1e-9

    gini_by_step = pallet["xy_bins_gini_by_step"]
    assert len(gini_by_step) == 7
    assert float(gini_by_step[4]) > float(gini_by_step[3])
    assert float(gini_by_step[6]) > float(gini_by_step[4])
