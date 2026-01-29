from __future__ import annotations

from pathlib import Path

from sim.run import run_simulation


def test_real_excel_smoke() -> None:
    excel_path = Path("data") / "Flujo rampas - Editado.xlsx"
    assert excel_path.exists(), "Excel real no encontrado en data/"

    payload = run_simulation(
        excel_path=str(excel_path),
        model="M1",
        n_per_pallet=24,
        t_pick_place=14.0,
        staging_cap=0,
        out_path=None,
        policy="palca",
        lookahead_k=1,
        time_scale=1.0,
        stability_mode="ratio+corners+settle",
        min_support=0.75,
        balance_weight=0.0,
        heavy_bottom=False,
    )

    kpis = payload["metrics"]["pallet_kpis"]
    assert "balance_quadrant_weights" in kpis
    assert "com_offset_mm" in kpis
    assert "balance_score" in kpis
    assert kpis.get("floating_boxes_count", 0) == 0
