from __future__ import annotations

from pathlib import Path

import pandas as pd

from sim.scenarios import run_grid


def _make_excel(path: Path) -> None:
    df = pd.DataFrame(
        {
            "timestamp": list(range(12)),
            "destino": [1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5, 6],
        }
    )
    df.to_excel(path, index=False)


def test_scenarios_smoke(tmp_path: Path) -> None:
    excel_path = tmp_path / "arrivals.xlsx"
    _make_excel(excel_path)

    out_csv = tmp_path / "grid_results.csv"
    out_json_dir = tmp_path / "grid_json"

    rows = run_grid(
        excel_path=str(excel_path),
        models=["M1", "M2"],
        n_per_pallet=[2],
        t_pick_place=[10.0],
        staging_caps=[0, 2],
        out_csv=str(out_csv),
        out_json_dir=str(out_json_dir),
    )

    assert out_csv.exists()
    assert len(rows) == 3
    assert not any(
        row["model"] == "M1" and int(row["staging_cap"]) > 0 for row in rows
    )

    csv_rows = pd.read_csv(out_csv)
    assert csv_rows.shape[0] == len(rows)
    assert csv_rows.shape[0] >= 1
