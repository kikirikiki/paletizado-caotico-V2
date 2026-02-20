from __future__ import annotations

from pathlib import Path

from sim.run import run_simulation


def test_microplanner_immediate_smoke_60() -> None:
    excel_path = Path("data") / "Flujo_smoke_60.xlsx"
    assert excel_path.exists(), "Excel smoke de 60 filas no encontrado en data/"

    payload = run_simulation(
        excel_path=str(excel_path),
        model="M1",
        n_per_pallet=999999,
        t_pick_place=14.0,
        staging_cap=0,
        out_path=None,
        policy="palca",
        lookahead_k=15,
        stability_mode="ratio+corners+settle",
        min_support=0.90,
        overhang_mm=20,
        force_destination=1,
        continuous_pallets=True,
        arrival_mode="immediate",
        time_budget_ms=900,
        micro_plan=True,
        micro_depth=3,
        micro_width=8,
        micro_topk=15,
    )

    params = payload["params"]
    metrics = payload["metrics"]
    kpis = metrics["pallet_kpis"]
    window_stats = kpis.get("accessible_window_stats", {})

    assert int(metrics["processed_boxes"]) == 60
    assert int(metrics["total_boxes"]) == 60
    assert bool(params.get("micro_plan")) is True
    assert int(params.get("micro_depth", 0)) == 3
    assert int(params.get("micro_width", 0)) == 8
    assert int(params.get("micro_topk", 0)) == 15

    for key in (
        "micro_plan_calls",
        "micro_plan_fallback_greedy",
        "micro_plan_time_ms_min",
        "micro_plan_time_ms_mean",
        "micro_plan_time_ms_max",
        "micro_plan_nodes_expanded_total",
        "micro_plan_depth_effective_mean",
    ):
        assert key in kpis

    assert float(window_stats.get("mean", 0.0)) >= 12.0
    assert float(window_stats.get("mean", 0.0)) <= 15.1
