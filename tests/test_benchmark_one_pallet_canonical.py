from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from scripts import benchmark_one_pallet_canonical as bench


def test_benchmark_summary_includes_layer_monotonicity_fields(tmp_path: Path, monkeypatch) -> None:
    profile_path = tmp_path / "profile.json"
    outdir = tmp_path / "bench_out"

    profile = {
        "schema_version": 1,
        "profile_name": "test_profile",
        "description": "test",
        "excel": "data/Flujo_smoke_60.xlsx",
        "seeds": [123],
        "params": {
            "model": "M1",
            "n_per_pallet": 24,
            "t_pick_place": 14.0,
            "staging_cap": 0,
            "policy": "palca",
            "lookahead_k": 1,
            "arrival_mode": "immediate",
            "force_destination": 1,
            "continuous_pallets": True,
            "max_pallets": 1,
        },
    }
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    def _fake_run_simulation(**kwargs: Any) -> dict[str, Any]:
        dump_path = Path(str(kwargs["dump_placements_path"]))
        out_path = Path(str(kwargs["out_path"]))
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        dump_payload = {
            "schema_version": 1,
            "pallets": {
                "1": [
                    {"step_index": 0, "z_mm": 0, "layer_id": 0, "orientation_family": "planar"},
                    {"step_index": 1, "z_mm": 120, "layer_id": 1, "orientation_family": "planar"},
                    {"step_index": 2, "z_mm": 20, "layer_id": 0, "orientation_family": "planar"},
                ]
            },
        }
        dump_path.write_text(json.dumps(dump_payload), encoding="utf-8")
        out_path.write_text(json.dumps({"ok": True}), encoding="utf-8")

        monotonic = {
            "first_stack_step": 1,
            "max_z_seen_so_far_by_step": [0, 120, 120],
            "lower_layer_reentry_count": 1,
            "lower_layer_reentry_total_drop_mm": 100,
            "lower_layer_reentry_max_drop_mm": 100,
            "lower_layer_reentry_mean_drop_mm": 100.0,
            "monotonic_stack_rate": 2.0 / 3.0,
            "placements_below_current_top_band_after_opening_next_band": 1,
            "layer_closure_score": 0.5,
            "layer_fill_homogeneity_score": 0.8,
            "z_band_fill_homogeneity_score": 0.7,
            "layer_band_mm": 100,
            "active_layers_over_time": [1, 2, 2],
            "layer_band_fill_progress": [{"band_id": 0, "opened_step": 0}],
            "z_band_fill_share": {"0": 0.6, "1": 0.4},
            "layer_fill_share": {"0": 0.55, "1": 0.45},
            "step_trace_relevant": [
                {
                    "step": 2,
                    "z_mm": 20,
                    "max_z_seen_so_far": 120,
                    "opened_new_band": False,
                    "is_reentry": True,
                    "reentry_drop_mm": 100,
                    "active_layers": 2,
                    "below_current_top_band_after_opening_next_band": True,
                }
            ],
        }

        return {
            "metrics": {
                "processed_boxes": 12,
                "pallet_kpis": {
                    "stand_hw_used_total": 0,
                    "hard_floor_phase_stand_hw_chosen_total": 0,
                    "layer_monotonicity_first_pallet_by_dest": {"1": monotonic},
                },
            }
        }

    monkeypatch.setattr(bench, "run_simulation", _fake_run_simulation)

    summary = bench.run_benchmark(
        profile_path=profile_path,
        outdir=outdir,
        seeds_override=[123],
        variant_name="variant",
    )

    rows = summary["rows"]
    assert len(rows) == 1
    row = rows[0]

    assert row["processed_boxes"] == 12
    assert row["first_stack_step"] == 1
    assert row["lower_layer_reentry_count"] == 1
    assert row["lower_layer_reentry_max_drop_mm"] == 100
    assert abs(float(row["monotonic_stack_rate"]) - (2.0 / 3.0)) < 1e-9
    assert row["layer_closure_score"] == 0.5

    csv_path = Path(summary["files"]["summary_csv"])
    with csv_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        first = next(reader)
    assert "lower_layer_reentry_count" in first
    assert "monotonic_stack_rate" in first
    assert "layer_band_fill_progress_json" in first

    trace_path = Path(row["layer_trace_report"])
    assert trace_path.exists()
    assert "is_reentry" in trace_path.read_text(encoding="utf-8")
