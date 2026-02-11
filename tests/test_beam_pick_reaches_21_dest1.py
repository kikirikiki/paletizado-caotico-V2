from __future__ import annotations

from pathlib import Path
import time

import pytest

from sim.run import run_simulation


@pytest.mark.slow
def test_beam_pick_reaches_21_dest1() -> None:
    excel_path = Path("data") / "Flujo_dest1.xlsx"
    assert excel_path.exists(), "Dataset requerido no encontrado: data/Flujo_dest1.xlsx"

    started = time.perf_counter()
    payload = run_simulation(
        excel_path=str(excel_path),
        model="M1",
        n_per_pallet=24,
        t_pick_place=14.0,
        staging_cap=0,
        out_path=None,
        policy="palca",
        planner="beam_pick",
        lookahead_k=15,
        pick_window=15,
        overhang_mm=20,
        stop_after_first_pallet=True,
        stability_mode="off",
        beam_width=12,
        beam_depth=6,
        beam_max_expansions=2500,
        beam_time_budget_ms=200,
    )
    elapsed = time.perf_counter() - started

    metrics = payload.get("metrics", {}) or {}
    kpis = metrics.get("pallet_kpis", {}) or {}
    processed = int(metrics.get("processed_boxes", 0) or 0)
    stop_reason = str(metrics.get("stop_reason", "") or "")
    layers = kpis.get("current_layers_by_dest", {})
    heights = kpis.get("current_height_mm_by_dest", {})
    beam_debug = kpis.get("beam_pick_last_debug", {})
    summary = (
        f"processed={processed}, stop_reason={stop_reason}, elapsed={elapsed:.3f}s, "
        f"layers={layers}, heights={heights}, beam_debug={beam_debug}"
    )

    assert elapsed < 180.0, summary
    assert processed >= 21, summary
    assert stop_reason.startswith("PALLET_DONE dest=1"), summary
