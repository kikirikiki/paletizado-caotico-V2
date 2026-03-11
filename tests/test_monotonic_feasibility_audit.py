from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import benchmark_one_pallet_canonical as bench
from palca.domain.box import Box
from palca.integration import monotonic_feasibility_audit as audit
from sim.des import Arrival


def _test_params() -> dict[str, object]:
    return {
        "arrival_mode": "immediate",
        "force_destination": 1,
        "shuffle_window": 0,
        "shuffle_strength": 0.0,
        "time_scale": 1.0,
        "priority_mode": "none",
        "weight_col": None,
        "overhang_mm": 0,
        "heuristic": "baf",
        "stacking_mode": "heightfield",
        "z_band_mm": 0,
        "stability_mode": "off",
        "min_support": 0.0,
        "stability_eps_mm": 1.0,
        "settle_snap_grid": False,
        "grid_mm": None,
        "settle_max_iter": 0,
        "settle_timeout_ms": 0,
        "heavy_bottom": False,
        "max_overweight_ratio": 1.5,
        "loadbear_penalty_weight": 0.0,
        "loadbear_factor": 1.0,
        "balance_weight": 0.0,
        "orientation_mode": "planar",
        "stand_hw_height_margin_gate_mm": 400,
        "coverage_grid_x": 0,
        "coverage_grid_y": 0,
        "coverage_weight": 0.0,
        "dominant_free_rect_weight": 0.0,
        "dominant_free_rect_ratio_gate": 0.35,
        "ramp_cap": 15,
        "lookahead_k": 15,
        "n_per_pallet": 24,
        "max_tries_per_item": 0,
        "max_candidates": 0,
        "max_seconds_per_item": 0.0,
    }


def test_episode_search_detects_monotonic_solution_in_small_synthetic_case() -> None:
    params = _test_params()
    pallet_factory = audit.build_pallet_factory_from_params(params)
    episode = [
        Box(box_id=1, length_mm=400, width_mm=400, height_mm=100, timestamp=0.0, destination=1),
        Box(box_id=2, length_mm=400, width_mm=400, height_mm=100, timestamp=0.0, destination=1),
        Box(box_id=3, length_mm=400, width_mm=400, height_mm=100, timestamp=0.0, destination=1),
    ]
    result = audit.run_episode_beam_search(
        episode_boxes=episode,
        pallet_factory=pallet_factory,
        ramp_capacity=2,
        lookahead_k=2,
        max_processed_target=3,
        preview_limits={"max_tries_per_item": 0, "max_candidates": 0, "max_seconds_per_item": 0.0},
        cfg=audit.MonotonicAuditSearchConfig(
            beam_width=8,
            expansion_topk=4,
            max_nodes=200,
            max_time_s=2.0,
            enforce_monotonic=True,
        ),
    )
    assert int(result.processed_boxes) == 3
    assert int(result.layers_used) >= 1


def test_episode_search_monotonic_can_be_worse_than_non_monotonic() -> None:
    params = _test_params()
    pallet_factory = audit.build_pallet_factory_from_params(params)
    episode = [
        Box(box_id=1, length_mm=900, width_mm=800, height_mm=100, timestamp=0.0, destination=1),
        Box(box_id=2, length_mm=800, width_mm=800, height_mm=100, timestamp=0.0, destination=1),
        Box(box_id=3, length_mm=300, width_mm=800, height_mm=100, timestamp=0.0, destination=1),
    ]
    non_monotonic = audit.run_episode_beam_search(
        episode_boxes=episode,
        pallet_factory=pallet_factory,
        ramp_capacity=1,
        lookahead_k=1,
        max_processed_target=3,
        cfg=audit.MonotonicAuditSearchConfig(
            beam_width=4,
            expansion_topk=1,
            max_nodes=50,
            max_time_s=2.0,
            enforce_monotonic=False,
        ),
    )
    monotonic = audit.run_episode_beam_search(
        episode_boxes=episode,
        pallet_factory=pallet_factory,
        ramp_capacity=1,
        lookahead_k=1,
        max_processed_target=3,
        cfg=audit.MonotonicAuditSearchConfig(
            beam_width=4,
            expansion_topk=1,
            max_nodes=50,
            max_time_s=2.0,
            enforce_monotonic=True,
        ),
    )
    assert int(non_monotonic.processed_boxes) == 3
    assert int(monotonic.processed_boxes) == 2
    assert int(non_monotonic.processed_boxes) > int(monotonic.processed_boxes)


def test_run_monotonic_feasibility_audit_writes_report_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_payload = {
        "profile_name": "synthetic_profile",
        "excel": "dummy.xlsx",
        "seeds": [9001],
        "params": _test_params(),
    }
    profile_path = tmp_path / "synthetic_profile.json"
    profile_path.write_text(json.dumps(profile_payload), encoding="utf-8")

    arrivals = [
        Arrival(time=0.0, destination=1, row_idx=1, length_mm=400, width_mm=400, height_mm=100),
        Arrival(time=0.0, destination=1, row_idx=2, length_mm=400, width_mm=400, height_mm=100),
        Arrival(time=0.0, destination=1, row_idx=3, length_mm=400, width_mm=400, height_mm=100),
    ]
    monkeypatch.setattr(audit, "load_arrivals", lambda *args, **kwargs: list(arrivals))

    summary = audit.run_monotonic_feasibility_audit(
        profile_path=profile_path,
        outdir=tmp_path / "audit_out",
        search_config=audit.MonotonicAuditSearchConfig(
            beam_width=8,
            expansion_topk=4,
            max_nodes=200,
            max_time_s=2.0,
            export_best_plan=True,
            enforce_monotonic=True,
        ),
        baseline_by_seed={9001: 2},
    )

    rows = list(summary["rows"])
    assert len(rows) == 1
    row = rows[0]
    assert row["seed"] == 9001
    assert "best_monotonic_processed_boxes_found" in row
    assert "monotonic_solution_exists_ge_21" in row
    assert row["best_plan_json"] is not None
    assert Path(row["best_plan_json"]).exists()

    summary_csv = Path(summary["files"]["summary_csv"])
    summary_json = Path(summary["files"]["summary_json"])
    assert summary_csv.exists()
    assert summary_json.exists()


def test_benchmark_baseline_regression_unchanged_after_audit_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_simulation(**kwargs):
        out_path = Path(str(kwargs["out_path"]))
        dump_path = Path(str(kwargs["dump_placements_path"]))
        payload = {
            "metrics": {
                "processed_boxes": int(kwargs["episode_seed"]) % 2 + 21,
                "pallet_kpis": {
                    "stand_hw_used_total": 0,
                    "hard_floor_phase_stand_hw_chosen_total": 0,
                    "layer_monotonicity_first_pallet_by_dest": {"1": {"max_z_seen_so_far_by_step": [0]}},
                },
            }
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload), encoding="utf-8")
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(json.dumps({"pallets": {"1": []}}), encoding="utf-8")
        return payload

    monkeypatch.setattr(bench, "run_simulation", fake_run_simulation)
    before = bench.run_benchmark(
        profile_path="configs/benchmarks/one_pallet_canonical.json",
        outdir=tmp_path / "before",
        seeds_override=[50021],
    )

    profile_payload = {
        "profile_name": "synthetic_profile",
        "excel": "dummy.xlsx",
        "seeds": [9001],
        "params": _test_params(),
    }
    profile_path = tmp_path / "synthetic_profile.json"
    profile_path.write_text(json.dumps(profile_payload), encoding="utf-8")
    monkeypatch.setattr(
        audit,
        "load_arrivals",
        lambda *args, **kwargs: [Arrival(time=0.0, destination=1, row_idx=1, length_mm=400, width_mm=400, height_mm=100)],
    )
    _ = audit.run_monotonic_feasibility_audit(
        profile_path=profile_path,
        outdir=tmp_path / "audit",
        search_config=audit.MonotonicAuditSearchConfig(max_time_s=1.0, max_nodes=20),
    )

    after = bench.run_benchmark(
        profile_path="configs/benchmarks/one_pallet_canonical.json",
        outdir=tmp_path / "after",
        seeds_override=[50021],
    )

    before_rows = list(before["rows"])
    after_rows = list(after["rows"])
    assert len(before_rows) == len(after_rows) == 1
    assert before_rows[0]["processed_boxes"] == after_rows[0]["processed_boxes"]
