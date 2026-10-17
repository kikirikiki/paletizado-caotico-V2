from inspect import signature

import pytest

from palca.domain.pallet_spec import PalletSpec
from palca.study.params import (
    BASE_PARAMS, MAX_LOAD_HEIGHT_MM, PALLET_NOMINAL_MM, PRESETS,
    REFERENCE_EXCEL, REFERENCE_SEEDS, pallet_envelope_mm, preset_params,
)
from sim.run import run_simulation


def test_reference_params_and_complete_simulation_call():
    assert BASE_PARAMS == dict(
        model="M1", n_per_pallet=999999, t_pick_place=14.0, staging_cap=0,
        out_path=None, ramp_cap=15, policy="palca", lookahead_k=15,
        arrival_mode="immediate", shuffle_window=15, shuffle_strength=1.0,
        overhang_mm=20, heuristic="bssf", stacking_mode="heightfield",
        stability_mode="ratio+corners+settle", min_support=0.85,
        score_mode="min_height_slack_then_gain", height_slack_mm=120,
        stand_hw_height_margin_gate_mm=400, time_budget_ms=900, micro_plan=False,
        force_destination=1, continuous_pallets=True, max_pallets=0,
        excel_path="data/Flujo rampas - Editado.xlsx",
    )
    assert REFERENCE_EXCEL == BASE_PARAMS["excel_path"]
    assert REFERENCE_SEEDS == [50021, 1, 42, 100, 777, 5678, 9999, 12345, 99999, 314159]
    for name in PRESETS:
        signature(run_simulation).bind(**preset_params(name))


@pytest.mark.parametrize("name,mode,delta", [
    ("arm6_4orient", "planar+stand_hw", 400),
    ("gantry_4orient", "planar+stand_hw", 0),
    ("arm6_planar", "planar", 400),
    ("lever_arm_canto", "planar+stand_hw+canto", 400),
    ("gantry_6orient", "planar+stand_hw+canto", 0),
])
def test_presets(name, mode, delta):
    params = preset_params(name)
    assert params["orientation_mode"] == mode
    assert params["accessibility_delta_mm"] == delta


def test_copies_and_overrides_are_independent(monkeypatch):
    # Nested test data makes this fail if deepcopy is replaced with dict.copy.
    monkeypatch.setitem(PRESETS["arm6_4orient"], "episode_id", {"tags": ["reference"]})
    override = {"tags": ["override"]}
    first = preset_params("arm6_4orient")
    second = preset_params("arm6_4orient", episode_id=override, min_support=0.9)
    first["episode_id"]["tags"].append("changed")
    second["episode_id"]["tags"].append("changed")
    first["ramp_cap"] = 1
    assert preset_params("arm6_4orient")["episode_id"] == {"tags": ["reference"]}
    assert override == {"tags": ["override"]}
    assert preset_params("gantry_4orient")["ramp_cap"] == BASE_PARAMS["ramp_cap"] == 15
    assert preset_params("arm6_4orient")["min_support"] == 0.85


@pytest.mark.parametrize("new,base", [("lever_arm_canto", "arm6_4orient"),
                                    ("gantry_6orient", "gantry_4orient")])
def test_canto_presets_only_change_orientation(new, base):
    params = preset_params(new)
    params["orientation_mode"] = "planar+stand_hw"
    assert params == preset_params(base)


def test_invalid_override_and_unknown_preset():
    with pytest.raises(ValueError, match="min_suport"):
        preset_params("arm6_4orient", min_suport=0.9)
    with pytest.raises(ValueError, match="desconocido"):
        preset_params("missing")
    with pytest.raises(ValueError, match="orientation_mode"):
        preset_params("arm6_4orient", orientation_mode="missing")


def test_envelope_uses_pallet_spec():
    spec = PalletSpec()
    assert PALLET_NOMINAL_MM == (spec.length_mm, spec.width_mm) == (1200, 800)
    assert MAX_LOAD_HEIGHT_MM == spec.max_height_mm == 2400
    assert pallet_envelope_mm(0) == (1200, 800, 2400)
    # Historical 1240x820 is inconsistent with 20 mm on EACH side.
    assert pallet_envelope_mm(20) == (1240, 840, 2400)
