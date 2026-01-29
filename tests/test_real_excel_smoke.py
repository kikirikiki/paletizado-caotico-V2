from __future__ import annotations

from pathlib import Path

import pandas as pd

from sim.run import run_simulation


def test_real_excel_smoke(tmp_path) -> None:
    src = Path("data") / "Flujo rampas - Editado.xlsx"
    assert src.exists(), "Excel real no encontrado en data/"

    # Smoke = recorta el excel para que no tarde minutos
    df = pd.read_excel(src, engine="openpyxl").head(20).copy()
    mini = tmp_path / "smoke_real.xlsx"
    df.to_excel(mini, index=False, engine="openpyxl")

    payload = run_simulation(
        excel_path=str(mini),
        model="M1",
        n_per_pallet=24,
        t_pick_place=14.0,
        staging_cap=0,
        out_path=None,
        policy="palca",
        lookahead_k=1,
        time_scale=1.0,
        stability_mode="ratio",
        min_support=0.75,
        balance_weight=0.0,
        heavy_bottom=False,
    )

    kpis = payload["metrics"]["pallet_kpis"]
    assert "balance_quadrant_weights" in kpis
    assert "com_offset_mm" in kpis
    assert "balance_score" in kpis
    assert kpis.get("floating_boxes_count", 0) >= 0
