from __future__ import annotations

from pathlib import Path
import time

import pytest

from sim.run import run_simulation


def _to_int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _count_dest1_in_records(node: object) -> int:
    def _iter_records(value: object):
        if isinstance(value, list):
            for item in value:
                for nested in _iter_records(item):
                    yield nested
        elif isinstance(value, dict):
            if any(key in value for key in ("destination", "dest", "pallet_id", "destination_id")):
                yield value
            for key in ("placements", "plan", "items", "actions", "steps"):
                nested = value.get(key)
                if nested is not None:
                    for rec in _iter_records(nested):
                        yield rec

    count = 0
    for record in _iter_records(node):
        candidate = None
        for key in ("destination", "dest", "pallet_id", "destination_id"):
            if key in record:
                candidate = record.get(key)
                break
        if _to_int_or_none(candidate) == 1:
            count += 1
    return count


def _extract_placed_dest1(payload: dict) -> tuple[int, str]:
    metrics = payload.get("metrics", {}) or {}
    if isinstance(metrics.get("placed_by_dest"), dict):
        placed_map = metrics["placed_by_dest"]
        return int(placed_map.get("1") or placed_map.get(1) or 0), "metrics.placed_by_dest"

    pallet_kpis = metrics.get("pallet_kpis", {}) or {}
    if isinstance(pallet_kpis.get("placed_by_dest"), dict):
        placed_map = pallet_kpis["placed_by_dest"]
        return int(placed_map.get("1") or placed_map.get(1) or 0), "metrics.pallet_kpis.placed_by_dest"

    candidates = [
        ("payload.placements", payload.get("placements")),
        ("payload.plan", payload.get("plan")),
        ("metrics.placements", metrics.get("placements")),
        ("metrics.plan", metrics.get("plan")),
        ("metrics.pallet_kpis.placements", pallet_kpis.get("placements")),
        ("metrics.pallet_kpis.plan", pallet_kpis.get("plan")),
    ]
    for source, candidate in candidates:
        if candidate is None:
            continue
        count = _count_dest1_in_records(candidate)
        if count > 0:
            return count, source

    pytest.fail(
        "No se pudo extraer placed_dest1 del payload. "
        f"metrics.keys={sorted(metrics.keys())}, "
        f"pallet_kpis.keys={sorted(pallet_kpis.keys())}"
    )


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
    placed_dest1, placed_source = _extract_placed_dest1(payload)
    stop_reason = str(metrics.get("stop_reason", "") or "")
    layers = kpis.get("current_layers_by_dest", {})
    heights = kpis.get("current_height_mm_by_dest", {})
    beam_debug = kpis.get("beam_pick_last_debug", {})
    summary = (
        f"placed_dest1={placed_dest1}({placed_source}), stop_reason={stop_reason}, elapsed={elapsed:.3f}s, "
        f"layers={layers}, heights={heights}, beam_debug={beam_debug}"
    )

    assert elapsed < 180.0, summary
    assert placed_dest1 >= 21, summary
    assert stop_reason.startswith("PALLET_DONE dest=1"), summary
