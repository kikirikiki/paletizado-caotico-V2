from __future__ import annotations

import csv
import inspect
import json
from pathlib import Path

import pytest

from scripts import benchmark_one_pallet_canonical as bench


def test_load_profile_canonical_has_required_shape() -> None:
    profile = bench.load_profile("configs/benchmarks/one_pallet_canonical.json")

    assert profile["profile_name"] == "one_pallet_canonical"
    assert profile["excel"] == "data/Flujo rampas - Editado.xlsx"
    assert profile["seeds"] == [50021, 50022, 50023, 50024, 50025]
    assert set(bench.REQUIRED_PARAM_KEYS).issubset(set(profile["params"].keys()))
    assert set(profile["params"].keys()).issubset(set(bench.PROFILE_ALLOWED_PARAM_KEYS))
    assert "human_like_layer_opener" not in profile["params"]


def test_required_profile_keys_align_run_simulation_signature() -> None:
    required_expected = {
        name
        for name, param in bench.RUN_SIMULATION_SIGNATURE.parameters.items()
        if name not in bench.RUN_SIM_EXCLUDED_PROFILE_KEYS and param.default is inspect._empty
    }
    allowed_expected = set(bench.RUN_SIMULATION_PARAM_KEYS - bench.RUN_SIM_EXCLUDED_PROFILE_KEYS)
    assert set(bench.REQUIRED_PARAM_KEYS) == required_expected
    assert set(bench.PROFILE_ALLOWED_PARAM_KEYS) == allowed_expected


def test_load_profile_fails_when_required_param_missing(tmp_path: Path) -> None:
    src = Path("configs/benchmarks/one_pallet_canonical.json")
    payload = json.loads(src.read_text(encoding="utf-8"))
    payload["params"].pop("model")

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


def test_apply_param_overrides_accepts_optional_human_like_layer_opener_knob() -> None:
    base = {"model": "M1", "n_per_pallet": 24, "t_pick_place": 14.0, "staging_cap": 0}
    merged = bench.apply_param_overrides(base, {"human_like_layer_opener": True})
    assert bool(merged["human_like_layer_opener"]) is True


def test_apply_param_overrides_accepts_optional_human_like_layer_opener_tail_risk_knobs() -> None:
    base = {"model": "M1", "n_per_pallet": 24, "t_pick_place": 14.0, "staging_cap": 0}
    merged = bench.apply_param_overrides(
        base,
        {
            "human_like_layer_opener_tail_risk": True,
            "human_like_layer_opener_tail_risk_weight": 0.8,
        },
    )
    assert bool(merged["human_like_layer_opener_tail_risk"]) is True
    assert float(merged["human_like_layer_opener_tail_risk_weight"]) == 0.8


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
                    {"step_index": 3, "z_mm": 100, "layer_id": 1, "orientation_family": "planar"},
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
    assert all(r["reentries_total"] == 1 for r in rows)
    assert all(r["max_layer_drop"] == 1 for r in rows)
    assert all(r["reentries_drop_ge_2_count"] == 0 for r in rows)
    assert all(r["deep_drop_burden"] == 0 for r in rows)
    assert all(r["deadlock_count"] == 0 for r in rows)
    assert all(r["layer_band_mm"] == 100 for r in rows)
    assert all("band_id" in r["layer_band_fill_progress_json"] for r in rows)

    assert summary["runs"]["variant"]["overrides"]["lookahead_k"] == 10
    assert summary["runs"]["baseline"]["effective_config_hash"] != summary["runs"]["variant"]["effective_config_hash"]
    assert summary["param_contract"]["missing_required_in_profile"] == []
    assert summary["param_contract"]["unknown_in_profile"] == []
    assert "lookahead_k" in summary["param_contract"]["run_simulation_param_keys"]
    assert "human_like_layer_opener" in summary["param_contract"]["profile_allowed_param_keys"]
    assert summary["discriminative"]["baseline"]["is_flat_processed_boxes"] is False
    assert summary["discriminative"]["variant"]["is_flat_processed_boxes"] is False
    assert summary["aggregates"]["baseline"]["deep_drop_burden_sum"] == 0
    assert summary["aggregates"]["variant"]["deep_drop_burden_sum"] == 0
    assert summary["aggregates"]["baseline"]["deadlock_count_sum"] == 0
    assert summary["aggregates"]["variant"]["deadlock_count_sum"] == 0
    assert summary["runs"]["baseline"]["effective_params"]["lookahead_k"] == 15
    assert summary["runs"]["variant"]["effective_params"]["lookahead_k"] == 10

    summary_csv = Path(summary["files"]["summary_csv"])
    summary_json = Path(summary["files"]["summary_json"])
    assert summary_csv.exists()
    assert summary_json.exists()

    with summary_csv.open("r", encoding="utf-8") as handle:
        csv_row = next(csv.DictReader(handle))
    assert "lower_layer_reentry_count" in csv_row
    assert "monotonic_stack_rate" in csv_row
    assert "step_trace_relevant_json" in csv_row
    assert "max_layer_drop" in csv_row
    assert "reentries_drop_ge_2_count" in csv_row
    assert "deep_drop_burden" in csv_row
    assert "deadlock_count" in csv_row


def test_run_benchmark_variant_can_enable_human_like_layer_opener(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_simulation(**kwargs):
        calls.append({"run_label_hint": str(kwargs["out_path"]), "kwargs": dict(kwargs)})
        out_path = Path(str(kwargs["out_path"]))
        dump_path = Path(str(kwargs["dump_placements_path"]))
        payload = {"metrics": {"processed_boxes": 24, "pallet_kpis": {}}}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload), encoding="utf-8")
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(json.dumps({"pallets": {"1": []}}), encoding="utf-8")
        return payload

    monkeypatch.setattr(bench, "run_simulation", fake_run_simulation)

    summary = bench.run_benchmark(
        profile_path="configs/benchmarks/one_pallet_canonical.json",
        outdir=tmp_path / "bench_out",
        set_overrides=["human_like_layer_opener=true"],
        seeds_override=[50021],
        variant_name="opener",
    )

    assert len(calls) == 2
    baseline_kwargs = calls[0]["kwargs"]
    variant_kwargs = calls[1]["kwargs"]
    assert bool(baseline_kwargs.get("human_like_layer_opener", False)) is False
    assert bool(variant_kwargs.get("human_like_layer_opener", False)) is True
    assert bool(summary["runs"]["opener"]["overrides"]["human_like_layer_opener"]) is True
