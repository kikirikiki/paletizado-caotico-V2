from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts import benchmark_one_pallet_canonical as bench


def test_load_profile_canonical_has_required_shape() -> None:
    profile = bench.load_profile("configs/benchmarks/one_pallet_canonical.json")

    assert profile["profile_name"] == "one_pallet_canonical"
    assert profile["excel"] == "data/Flujo rampas - Editado.xlsx"
    assert profile["seeds"] == [50021, 50022, 50023, 50024, 50025]
    assert set(profile["params"].keys()) == set(bench.REQUIRED_PARAM_KEYS)


def test_required_profile_keys_align_run_simulation_signature() -> None:
    expected = set(bench.RUN_SIMULATION_PARAM_KEYS - bench.RUN_SIM_EXCLUDED_PROFILE_KEYS)
    assert set(bench.REQUIRED_PARAM_KEYS) == expected


def test_load_profile_fails_when_required_param_missing(tmp_path: Path) -> None:
    src = Path("configs/benchmarks/one_pallet_canonical.json")
    payload = json.loads(src.read_text(encoding="utf-8"))
    payload["params"].pop("policy")

    broken_path = tmp_path / "broken_profile.json"
    broken_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="faltan parametros requeridos"):
        bench.load_profile(broken_path)


def test_load_profile_fails_when_unknown_param_present(tmp_path: Path) -> None:
    src = Path("configs/benchmarks/one_pallet_canonical.json")
    payload = json.loads(src.read_text(encoding="utf-8"))
    payload["params"]["orphan_param"] = 123

    broken_path = tmp_path / "broken_profile_unknown.json"
    broken_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="parametros desconocidos/no usados"):
        bench.load_profile(broken_path)


def test_parse_set_overrides_and_apply_aliases() -> None:
    base = {k: None for k in bench.REQUIRED_PARAM_KEYS}
    base["lookahead_k"] = 15
    base["micro_width"] = 40
    base["score_mode"] = "min_height_slack_then_gain"

    overrides = bench._parse_set_overrides([
        "k=10",
        "micro-width=60",
        "score_mode=\"gain_frag\"",
    ])

    merged = bench.apply_param_overrides(base, overrides)
    assert merged["lookahead_k"] == 10
    assert merged["micro_width"] == 60
    assert merged["score_mode"] == "gain_frag"


def test_build_run_simulation_kwargs_maps_profile_to_signature() -> None:
    profile = bench.load_profile("configs/benchmarks/one_pallet_canonical.json")
    kwargs = bench.build_run_simulation_kwargs(
        params=profile["params"],
        excel_path=profile["excel"],
        out_path=Path("/tmp/seed_50021.json"),
        seed=50021,
        dump_placements_path=Path("/tmp/seed_50021_placements.json"),
    )
    assert set(kwargs.keys()).issubset(set(bench.RUN_SIMULATION_PARAM_KEYS))
    assert kwargs["lookahead_k"] == profile["params"]["lookahead_k"]
    assert "k" not in kwargs
    assert kwargs["episode_seed"] == 50021
    assert kwargs["excel_path"] == profile["excel"]


def test_build_run_simulation_kwargs_fails_on_orphan_param() -> None:
    profile = bench.load_profile("configs/benchmarks/one_pallet_canonical.json")
    params = dict(profile["params"])
    params["orphan_param"] = "x"
    with pytest.raises(ValueError, match="run_simulation no acepta"):
        bench.build_run_simulation_kwargs(
            params=params,
            excel_path=profile["excel"],
            out_path=Path("/tmp/seed_50021.json"),
            seed=50021,
            dump_placements_path=Path("/tmp/seed_50021_placements.json"),
        )


def test_run_benchmark_generates_summary_with_expected_structure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_simulation(**kwargs):
        seed = int(kwargs["episode_seed"])
        lookahead_k = int(kwargs["lookahead_k"])
        out_path = Path(str(kwargs["out_path"]))
        dump_path = Path(str(kwargs["dump_placements_path"]))

        payload = {
            "metrics": {
                "processed_boxes": seed + lookahead_k,
                "pallet_kpis": {
                    "stand_hw_used_total": lookahead_k,
                    "hard_floor_phase_stand_hw_chosen_total": 1,
                    "layer_monotonicity_first_pallet_by_dest": {
                        "1": {
                            "first_stack_step": 3,
                            "max_z_seen_so_far_by_step": [0, 0, 100, 200],
                            "lower_layer_reentry_count": 1,
                            "lower_layer_reentry_total_drop_mm": 100,
                            "lower_layer_reentry_max_drop_mm": 100,
                            "lower_layer_reentry_mean_drop_mm": 100.0,
                            "monotonic_stack_rate": 0.75,
                            "placements_below_current_top_band_after_opening_next_band": 1,
                            "layer_closure_score": 0.5,
                            "layer_fill_homogeneity_score": 0.8,
                            "z_band_fill_homogeneity_score": 0.7,
                            "layer_band_mm": 100,
                            "layer_band_fill_progress": [{"band_id": 0, "opened_step": 0}],
                            "active_layers_over_time": [1, 1, 2, 2],
                            "z_band_fill_share": {"0": 0.6, "1": 0.4},
                            "layer_fill_share": {"0": 0.55, "1": 0.45},
                            "step_trace_relevant": [
                                {
                                    "step": 3,
                                    "z_mm": 20,
                                    "max_z_seen_so_far": 120,
                                    "opened_new_band": False,
                                    "is_reentry": True,
                                    "reentry_drop_mm": 100,
                                }
                            ],
                        }
                    },
                    "top_access_first_pallet_by_dest": {
                        "1": {
                            "blocked_count": 2,
                            "marginal_count": 1,
                            "severe_marginal_count": 1,
                            "blocked_stand_hw": 1,
                            "marginal_stand_hw": 0,
                            "severe_marginal_stand_hw": 1,
                            "first_blocked_step": 7,
                            "first_severe_marginal_step": 8,
                            "issues_concentrated_at_end": True,
                            "critical_placements": [
                                {
                                    "step_index": 7,
                                    "accessibility_class": "blocked",
                                    "blocked_reason_exact": "mixed",
                                    "marginal_severity_score": 12.5,
                                }
                            ],
                        }
                    },
                },
            }
        }

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload), encoding="utf-8")

        dump_payload = {
            "pallets": {
                "1": [
                    {"step_index": 0, "z_mm": 0, "layer_id": 0, "orientation_family": "planar"},
                    {"step_index": 1, "z_mm": 0, "layer_id": 0, "orientation_family": "stand_hw"},
                    {"step_index": 2, "z_mm": 200, "layer_id": 1, "orientation_family": "planar"},
                ]
            }
        }
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(json.dumps(dump_payload), encoding="utf-8")

        calls.append({"seed": seed, "lookahead_k": lookahead_k})
        return payload

    monkeypatch.setattr(bench, "run_simulation", fake_run_simulation)

    summary = bench.run_benchmark(
        profile_path="configs/benchmarks/one_pallet_canonical.json",
        outdir=tmp_path / "bench_out",
        set_overrides=["lookahead_k=10"],
        seeds_override=[50021, 50022],
        variant_name="variant",
    )

    assert len(calls) == 4
    assert len(summary["rows"]) == 4
    assert summary["fingerprint"]["seeds"] == [50021, 50022]

    rows = summary["rows"]
    baseline_rows = [r for r in rows if r["run_label"] == "baseline"]
    variant_rows = [r for r in rows if r["run_label"] == "variant"]
    assert len(baseline_rows) == 2
    assert len(variant_rows) == 2

    assert all(r["first_stack_step"] == 2 for r in rows)
    assert all(r["first_stand_hw_step"] == 1 for r in rows)
    assert all(r["lower_layer_reentry_count"] == 1 for r in rows)
    assert all(abs(float(r["monotonic_stack_rate"]) - 0.75) < 1e-9 for r in rows)
    assert all(r["blocked_count"] == 2 for r in rows)
    assert all(r["marginal_count"] == 1 for r in rows)
    assert all(r["severe_marginal_count"] == 1 for r in rows)
    assert all(r["blocked_stand_hw"] == 1 for r in rows)
    assert all(r["severe_marginal_stand_hw"] == 1 for r in rows)
    assert all(r["first_blocked_step"] == 7 for r in rows)
    assert all(r["first_severe_marginal_step"] == 8 for r in rows)
    assert all(r["issues_concentrated_at_end"] is True for r in rows)
    assert all("blocked_reason_exact" in r["critical_placements_json"] for r in rows)
    assert all("marginal_severity_score" in r["critical_placements_json"] for r in rows)
    assert all(r["layer_band_mm"] == 100 for r in rows)
    assert all("band_id" in r["layer_band_fill_progress_json"] for r in rows)

    assert summary["runs"]["variant"]["overrides"]["lookahead_k"] == 10
    assert summary["runs"]["baseline"]["effective_config_hash"] != summary["runs"]["variant"]["effective_config_hash"]
    assert summary["param_contract"]["missing_required_in_profile"] == []
    assert summary["param_contract"]["unknown_in_profile"] == []
    assert "lookahead_k" in summary["param_contract"]["run_simulation_param_keys"]
    assert summary["discriminative"]["baseline"]["is_flat_processed_boxes"] is False
    assert summary["discriminative"]["variant"]["is_flat_processed_boxes"] is False
    assert summary["runs"]["baseline"]["effective_params"]["lookahead_k"] == 15
    assert summary["runs"]["variant"]["effective_params"]["lookahead_k"] == 10
    assert summary["top_access_explainability"]["enabled"] is False
    assert int(summary["top_access_explainability"]["top_k"]) == bench.DEFAULT_EXPLAINABILITY_TOP_K

    summary_csv = Path(summary["files"]["summary_csv"])
    summary_json = Path(summary["files"]["summary_json"])
    assert summary_csv.exists()
    assert summary_json.exists()

    with summary_csv.open("r", encoding="utf-8") as handle:
        csv_row = next(csv.DictReader(handle))
    assert "lower_layer_reentry_count" in csv_row
    assert "monotonic_stack_rate" in csv_row
    assert "step_trace_relevant_json" in csv_row
    assert "top_critical_steps_json" in csv_row
    assert "top_critical_orientations_json" in csv_row
    assert "top_critical_reasons_json" in csv_row
    assert "recurrent_blockers_json" in csv_row
    assert "explainability_exports_json" in csv_row
    parsed_steps = json.loads(csv_row["top_critical_steps_json"])
    assert parsed_steps == [7]


def test_run_benchmark_exports_top_access_explainability(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_simulation(**kwargs):
        out_path = Path(str(kwargs["out_path"]))
        dump_path = Path(str(kwargs["dump_placements_path"]))
        payload = {
            "metrics": {
                "processed_boxes": 21,
                "pallet_kpis": {
                    "layer_monotonicity_first_pallet_by_dest": {"1": {"active_layers_over_time": [1, 1, 2]}},
                    "top_access_first_pallet_by_dest": {
                        "1": {
                            "blocked_count": 1,
                            "marginal_count": 1,
                            "severe_marginal_count": 1,
                            "blocked_stand_hw": 1,
                            "marginal_stand_hw": 0,
                            "severe_marginal_stand_hw": 1,
                            "first_blocked_step": 7,
                            "first_severe_marginal_step": 8,
                            "issues_concentrated_at_end": True,
                            "per_placement": [
                                {
                                    "step_index": 7,
                                    "orientation_family": "stand_hw",
                                    "accessibility_class": "blocked",
                                    "blocked_reason_exact": "overhead_blocked",
                                    "marginal_severity_score": 12.5,
                                    "throat_source_reason": "overhead_prism_overlap",
                                    "limiting_axis": "z_overhead",
                                    "limiting_clearance_mm": -50,
                                    "nearest_blocker_step": 4,
                                    "nearest_blocker_orientation": "stand_hw",
                                    "pallet_bounds_mm": {"x0_mm": 0, "y0_mm": 0, "x1_mm": 700, "y1_mm": 700},
                                    "analysis_zone_mm": {"x0_mm": 120, "y0_mm": 120, "x1_mm": 380, "y1_mm": 380},
                                    "target_footprint_mm": {"x0_mm": 200, "y0_mm": 200, "x1_mm": 300, "y1_mm": 300},
                                    "target_hard_prism_mm": {"x0_mm": 195, "y0_mm": 195, "x1_mm": 305, "y1_mm": 305},
                                    "entry_throat_bbox_mm": {"x0_mm": 180, "y0_mm": 180, "x1_mm": 320, "y1_mm": 320},
                                    "entry_throat_clearances_mm": {
                                        "left_mm": 20,
                                        "right_mm": 20,
                                        "bottom_mm": 20,
                                        "top_mm": 20,
                                    },
                                    "local_blockers": [
                                        {
                                            "step_index": 4,
                                            "orientation_family": "stand_hw",
                                            "blocker_bbox_mm": {"x0_mm": 240, "y0_mm": 180, "x1_mm": 330, "y1_mm": 330},
                                            "blocker_local_footprint_mm": {
                                                "x0_mm": 240,
                                                "y0_mm": 180,
                                                "x1_mm": 330,
                                                "y1_mm": 330,
                                            },
                                        }
                                    ],
                                },
                                {
                                    "step_index": 8,
                                    "orientation_family": "planar",
                                    "accessibility_class": "marginal",
                                    "blocked_reason_exact": None,
                                    "marginal_severity_score": 2.8,
                                    "throat_source_reason": "entry_throat_left_limited",
                                    "limiting_axis": "left",
                                    "limiting_clearance_mm": 5,
                                    "nearest_blocker_step": 6,
                                    "nearest_blocker_orientation": "planar",
                                    "pallet_bounds_mm": {"x0_mm": 0, "y0_mm": 0, "x1_mm": 700, "y1_mm": 700},
                                    "analysis_zone_mm": {"x0_mm": 100, "y0_mm": 100, "x1_mm": 360, "y1_mm": 360},
                                    "target_footprint_mm": {"x0_mm": 180, "y0_mm": 180, "x1_mm": 280, "y1_mm": 280},
                                    "target_hard_prism_mm": {"x0_mm": 175, "y0_mm": 175, "x1_mm": 285, "y1_mm": 285},
                                    "entry_throat_bbox_mm": {"x0_mm": 160, "y0_mm": 170, "x1_mm": 295, "y1_mm": 295},
                                    "entry_throat_clearances_mm": {
                                        "left_mm": 20,
                                        "right_mm": 15,
                                        "bottom_mm": 10,
                                        "top_mm": 15,
                                    },
                                    "local_blockers": [
                                        {
                                            "step_index": 6,
                                            "orientation_family": "planar",
                                            "blocker_bbox_mm": {"x0_mm": 130, "y0_mm": 170, "x1_mm": 175, "y1_mm": 285},
                                            "blocker_local_footprint_mm": {
                                                "x0_mm": 130,
                                                "y0_mm": 170,
                                                "x1_mm": 175,
                                                "y1_mm": 285,
                                            },
                                        }
                                    ],
                                },
                            ],
                            "critical_placements": [
                                {
                                    "step_index": 7,
                                    "orientation_family": "stand_hw",
                                    "accessibility_class": "blocked",
                                    "blocked_reason_exact": "overhead_blocked",
                                    "marginal_severity_score": 12.5,
                                }
                            ],
                        }
                    },
                },
            }
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload), encoding="utf-8")
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(json.dumps({"pallets": {"1": [{"step_index": 0, "z_mm": 0, "layer_id": 0}]}}), encoding="utf-8")
        return payload

    monkeypatch.setattr(bench, "run_simulation", fake_run_simulation)

    summary = bench.run_benchmark(
        profile_path="configs/benchmarks/one_pallet_canonical.json",
        outdir=tmp_path / "bench_export",
        seeds_override=[50021],
        export_top_access_explainability=True,
        explainability_top_k=2,
    )

    assert summary["top_access_explainability"]["enabled"] is True
    assert int(summary["top_access_explainability"]["top_k"]) == 2
    rows = summary["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert json.loads(row["top_critical_steps_json"]) == [7, 8]
    blockers = json.loads(row["recurrent_blockers_json"])
    assert blockers
    assert int(blockers[0]["hits"]) >= 1
    exports = json.loads(row["explainability_exports_json"])
    assert exports["enabled"] is True
    assert int(exports["exports_count"]) == 2
    export_dir = Path(str(exports["export_dir"]))
    assert export_dir.exists()
    for item in exports["exports"]:
        assert Path(item["json"]).exists()
        assert Path(item["ascii"]).exists()


@pytest.mark.slow
def test_canonical_baseline_processed_boxes_are_stable(tmp_path: Path) -> None:
    summary = bench.run_benchmark(
        profile_path="configs/benchmarks/one_pallet_canonical.json",
        outdir=tmp_path / "canonical_stability",
        seeds_override=[50021, 50022, 50023, 50024, 50025],
        variant_name="variant",
        export_top_access_explainability=True,
        explainability_top_k=3,
    )

    rows = [r for r in summary["rows"] if r["run_label"] == "baseline"]
    got = {int(r["seed"]): int(r["processed_boxes"]) for r in rows}
    assert got == {
        50021: 21,
        50022: 22,
        50023: 22,
        50024: 21,
        50025: 21,
    }
    blocked = {int(r["seed"]): int(r["blocked_count"] or 0) for r in rows}
    assert blocked == {
        50021: 0,
        50022: 0,
        50023: 0,
        50024: 0,
        50025: 0,
    }
    for row in rows:
        exports = json.loads(row["explainability_exports_json"])
        assert exports["enabled"] is True
