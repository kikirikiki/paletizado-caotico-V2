from __future__ import annotations

from pathlib import Path

from sim.run import run_simulation


def test_single_pallet_mode_max_pallets_continuous() -> None:
    excel_path = Path("data") / "Flujo_smoke_60.xlsx"
    assert excel_path.exists(), "Excel smoke de 60 filas no encontrado en data/"

    payload = run_simulation(
        excel_path=str(excel_path),
        model="M1",
        n_per_pallet=24,
        t_pick_place=14.0,
        staging_cap=0,
        out_path=None,
        policy="palca",
        lookahead_k=1,
        arrival_mode="immediate",
        force_destination=1,
        continuous_pallets=True,
        max_pallets=1,
    )

    params = payload["params"]
    metrics = payload["metrics"]
    pallet_kpis = metrics.get("pallet_kpis", {})

    seq_by_dest = pallet_kpis.get("continuous_pallet_sequence", {})
    totals_by_dest = pallet_kpis.get("continuous_pallets_total", {})
    seq_dest1 = seq_by_dest.get(1, seq_by_dest.get("1", [])) if isinstance(seq_by_dest, dict) else []
    pallets_total = (
        totals_by_dest.get(1, totals_by_dest.get("1", len(seq_dest1))) if isinstance(totals_by_dest, dict) else len(seq_dest1)
    )

    assert int(params.get("max_pallets", 0)) == 1
    assert metrics.get("stop_reason") == "MAX_PALLETS"
    assert len(seq_dest1) == 1 or int(pallets_total) == 1
    assert int(pallet_kpis.get("pallets_closed", 0)) == 1
    assert int(pallet_kpis.get("first_pallet_boxes", 0)) >= 1
