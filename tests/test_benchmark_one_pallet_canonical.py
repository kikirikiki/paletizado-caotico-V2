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
    assert bool(profile["params"]["use_early_layer_pattern_planner"]) is False
    assert int(profile["params"]["layer_pattern_prefix_depth"]) == 3
    assert int(profile["params"]["layer_pattern_beam_width"]) == 4
    assert int(profile["params"]["layer_pattern_candidate_cap"]) == 8
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
        "use_active_layer_commit=true",
        "use_layer_skeleton_planner=true",
        "layer_skeleton_cap=6",
        "layer_skeleton_beam_width=4",
        "layer_skeleton_candidate_cap=8",
    ])

    merged = bench.apply_param_overrides(base, overrides)
    assert merged["lookahead_k"] == 10
    assert merged["micro_width"] == 60
    assert merged["score_mode"] == "gain_frag"
    assert bool(merged["use_early_layer_pattern_planner"]) is True
    assert int(merged["layer_pattern_prefix_depth"]) == 6
    assert int(merged["layer_pattern_beam_width"]) == 4
    assert int(merged["layer_pattern_candidate_cap"]) == 8


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
                    "planner_invocations": 2,
                    "planner_abstains": 1,
                    "planned_prefix_len_mean": 3.0,
                    "committed_layer_plan_len_mean": 3.0,
                    "planned_prefix_executed_mean": 2.0,
                    "active_layer_commit_replans_total": 4,
                    "active_layer_commit_fallback_same_layer_total": 2,
                    "active_layer_commit_closures_total": 1,
                    "skeleton_breaks_total": 1,
                    "skeleton_rebuilds_total": 2,
                    "skeleton_area_fill_mean": 0.75,
                    "deadlock_count": 0,
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
                            "reentries_total": 1,
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
    assert all(r["planner_invocations"] == 2 for r in rows)
    assert all(r["planner_abstains"] == 1 for r in rows)
    assert all(abs(float(r["planned_prefix_len_mean"]) - 3.0) < 1e-9 for r in rows)
    assert all(abs(float(r["committed_layer_plan_len_mean"]) - 3.0) < 1e-9 for r in rows)
    assert all(abs(float(r["planned_prefix_executed_mean"]) - 2.0) < 1e-9 for r in rows)
    assert all(r["active_layer_commit_replans_total"] == 4 for r in rows)
    assert all(r["active_layer_commit_fallback_same_layer_total"] == 2 for r in rows)
    assert all(r["active_layer_commit_closures_total"] == 1 for r in rows)
    assert all(r["skeleton_breaks_total"] == 1 for r in rows)
    assert all(r["skeleton_rebuilds_total"] == 2 for r in rows)
    assert all(abs(float(r["skeleton_area_fill_mean"]) - 0.75) < 1e-9 for r in rows)
    assert all(r["deadlock_count"] == 0 for r in rows)
    assert all(r["lower_layer_reentry_count"] == 1 for r in rows)
    assert all(r["reentries_total"] == 1 for r in rows)
    assert all(abs(float(r["monotonic_stack_rate"]) - 0.75) < 1e-9 for r in rows)
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

    summary_csv = Path(summary["files"]["summary_csv"])
    summary_json = Path(summary["files"]["summary_json"])
    assert summary_csv.exists()
    assert summary_json.exists()

    with summary_csv.open("r", encoding="utf-8") as handle:
        csv_row = next(csv.DictReader(handle))
    assert "lower_layer_reentry_count" in csv_row
    assert "planner_invocations" in csv_row
    assert "planned_prefix_executed_mean" in csv_row
    assert "committed_layer_plan_len_mean" in csv_row
    assert "active_layer_commit_replans_total" in csv_row
    assert "active_layer_commit_fallback_same_layer_total" in csv_row
    assert "active_layer_commit_closures_total" in csv_row
    assert "skeleton_breaks_total" in csv_row
    assert "skeleton_rebuilds_total" in csv_row
    assert "skeleton_area_fill_mean" in csv_row
    assert "reentries_total" in csv_row
    assert "deadlock_count" in csv_row
    assert "monotonic_stack_rate" in csv_row
    assert "step_trace_relevant_json" in csv_row


def test_run_benchmark_feature_on_keeps_strict_monotonicity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_simulation(**kwargs):
        payload = {
            "metrics": {
                "processed_boxes": 18,
                "stop_reason": None,
                "pallet_kpis": {
                    "planner_invocations": 3,
                    "planner_abstains": 0,
                    "planned_prefix_len_mean": 3.0,
                    "committed_layer_plan_len_mean": 3.0,
                    "planned_prefix_executed_mean": 3.0,
                    "active_layer_commit_replans_total": 6,
                    "active_layer_commit_fallback_same_layer_total": 2,
                    "active_layer_commit_closures_total": 3,
                    "skeleton_breaks_total": 0,
                    "skeleton_rebuilds_total": 4,
                    "skeleton_area_fill_mean": 0.8,
                    "deadlock_count": 0,
                    "layer_monotonicity_first_pallet_by_dest": {
                        "1": {
                            "lower_layer_reentry_count": 0,
                            "lower_layer_reentry_total_drop_mm": 0,
                            "lower_layer_reentry_max_drop_mm": 0,
                            "lower_layer_reentry_mean_drop_mm": 0.0,
                            "monotonic_stack_rate": 1.0,
                            "placements_below_current_top_band_after_opening_next_band": 0,
                            "layer_closure_score": 1.0,
                            "layer_fill_homogeneity_score": 1.0,
                            "z_band_fill_homogeneity_score": 1.0,
                            "layer_band_mm": 100,
                            "layer_band_fill_progress": [],
                            "active_layers_over_time": [1, 1, 2, 2],
                            "z_band_fill_share": {},
                            "layer_fill_share": {},
                            "step_trace_relevant": [],
                            "max_z_seen_so_far_by_step": [0, 100],
                        }
                    },
                },
            }
        }
        out_path = Path(str(kwargs["out_path"]))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload), encoding="utf-8")
        dump_path = Path(str(kwargs["dump_placements_path"]))
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(json.dumps({"pallets": {"1": []}}), encoding="utf-8")
        return payload

    monkeypatch.setattr(bench, "run_simulation", fake_run_simulation)
    summary = bench.run_benchmark(
        profile_path="configs/benchmarks/one_pallet_canonical.json",
        outdir=tmp_path / "bench_out_feature_on",
        set_overrides=[
            "use_layer_skeleton_planner=true",
            "use_active_layer_commit=true",
            "layer_skeleton_cap=6",
            "layer_skeleton_beam_width=4",
            "layer_skeleton_candidate_cap=8",
        ],
        seeds_override=[50021],
        variant_name="feature_on",
    )

    rows = [row for row in summary["rows"] if row["run_label"] == "feature_on"]
    assert len(rows) == 1
    row = rows[0]
    assert row["planner_invocations"] == 3
    assert row["planner_abstains"] == 0
    assert row["active_layer_commit_replans_total"] == 6
    assert row["active_layer_commit_fallback_same_layer_total"] == 2
    assert row["active_layer_commit_closures_total"] == 3
    assert row["skeleton_breaks_total"] == 0
    assert row["skeleton_rebuilds_total"] == 4
    assert abs(float(row["skeleton_area_fill_mean"]) - 0.8) < 1e-9
    assert abs(float(row["committed_layer_plan_len_mean"]) - 3.0) < 1e-9
    assert abs(float(row["monotonic_stack_rate"]) - 1.0) < 1e-9
    assert int(row["reentries_total"]) == 0
    assert int(row["deadlock_count"]) == 0
