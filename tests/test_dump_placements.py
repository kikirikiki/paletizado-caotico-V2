from __future__ import annotations

import json

import pandas as pd


def test_parser_includes_dump_placements_flag() -> None:
    from sim.run import build_parser

    parser = build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "data/Flujo rampas - Editado.xlsx",
            "--dump-placements",
            "/tmp/placements.json",
        ]
    )
    assert args.dump_placements == "/tmp/placements.json"


def test_dump_placements_creates_file(tmp_path) -> None:
    from sim.run import run_simulation

    df = pd.DataFrame(
        {
            "timestamp": [0, 1, 2, 3],
            "destino": [1, 1, 1, 1],
            "largo": [400, 400, 400, 400],
            "ancho": [300, 300, 300, 300],
            "alto": [200, 200, 200, 200],
        }
    )
    excel_path = tmp_path / "in.xlsx"
    df.to_excel(excel_path, index=False)

    out_path = tmp_path / "out.json"
    dump_path = tmp_path / "placements.json"

    run_simulation(
        excel_path=str(excel_path),
        model="M1",
        n_per_pallet=24,
        t_pick_place=1.0,
        staging_cap=0,
        out_path=str(out_path),
        policy="palca",
        lookahead_k=1,
        arrival_mode="excel",
        stacking_mode="layers",
        stability_mode="off",
        time_budget_ms=50,
        micro_plan=False,
        dump_placements_path=str(dump_path),
    )

    assert dump_path.exists()
    data = json.loads(dump_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert "pallets" in data
    pallets = data["pallets"]
    assert isinstance(pallets, dict)
    assert "1" in pallets
    assert isinstance(pallets["1"], list)
    assert len(pallets["1"]) > 0
    first = pallets["1"][0]
    assert first["step_index"] == 0
    assert first["layer_id"] >= 0
    assert "x_mm" in first and "y_mm" in first and "z_mm" in first
