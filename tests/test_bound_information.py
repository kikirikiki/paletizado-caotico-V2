from dataclasses import replace
from inspect import signature
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.policy_open_pallets import OpenPalletScheduler
from palca.integration.policy_packer_sched import PolicyPackerScheduler, SUPPORTED_LOOKAHEAD_K
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1
from palca.study.metric import run_kpi, split_pallets
from palca.study.params import REFERENCE_SEEDS, preset_params
from palca.study.validate import validate_pallet
from scripts.run_sweep import load_configs
from sim.des import Arrival, SimConfig, simulate
from sim.run import run_simulation


ROOT = Path(__file__).resolve().parents[1]
BASELINE = json.loads((ROOT / "tests/fixtures/baseline_arm6_4orient_v1.json").read_text())


@pytest.mark.parametrize("seed", REFERENCE_SEEDS)
def test_n1_k15_exact_official_baseline(seed, tmp_path):
    """Valores congelados del baseline oficial: igualdad exacta, sin tolerancias."""
    params = preset_params("arm6_4orient", open_pallets=1, ramp_cap=15, lookahead_k=15,
                           episode_seed=seed, dump_placements_path=str(tmp_path / "dump.json"))
    payload = run_simulation(**params)
    records = split_pallets(payload, json.loads((tmp_path / "dump.json").read_text()))
    result = run_kpi(records)
    expected = BASELINE["seeds"][str(seed)]
    for key in ("kpi", "n_pallets_total", "n_closed", "boxes_placed", "pallet_utils"):
        assert result[key] == expected[key], (seed, key)
    actual = payload["metrics"]["pallet_kpis"]["continuous_pallet_sequence"]
    assert {str(k): v for k, v in actual.items()} == expected["sequence"]
    assert payload["metrics"]["pallet_kpis"]["deadline_cutoffs_count"] == 0


def test_configs_defaults_and_additive_k():
    assert signature(run_simulation).parameters["open_pallets"].default == 1
    assert set((1, 3, 5, 10, 15, 30, 75, 186)) <= set(SUPPORTED_LOOKAHEAD_K)
    buffers = load_configs(ROOT / "configs/sweeps/bound_buffer_k.yaml")
    assert [c["overrides"]["ramp_cap"] for c in buffers] == [15, 30, 75, 186]
    assert all(c["overrides"]["ramp_cap"] == c["overrides"]["lookahead_k"] for c in buffers)
    opened = load_configs(ROOT / "configs/sweeps/bound_open_pallets.yaml")
    assert [c["overrides"]["open_pallets"] for c in opened] == [1, 2, 3, 4]
    assert all(c["role"] == "bound" for c in buffers + opened)
    assert buffers[0] == opened[0]


class FakePallet:
    def __init__(self, entries):
        self.entries = entries
        self.placements = [object()]

    def preview_place(self, box):
        gain, height, reason = self.entries[box.box_id]
        placement = Placement(x_mm=0, y_mm=0, z_mm=0, rot90=False, layer_id=0,
                              length_mm=1, width_mm=1, height_mm=1, box_id=box.box_id)
        return PlacementPreview(feasible=reason is None, placement=placement if reason is None else None,
                                packing_gain=gain, fragmentation=0, height_after_mm=height,
                                infeasible_reason=reason)


def boxes():
    return [Box(i, 1, 1, 1, 0, destination=1) for i in (1, 2)]


def test_joint_box_pallet_selection_uses_global_production_height_slack():
    pallets = {"1:1": FakePallet({1: (1, 900, None), 2: (2, 900, None)}),
               "1:2": FakePallet({1: (0.1, 100, None), 2: (0.9, 100, None)})}
    state = SchedulerSimState(0, {1: boxes()}, pallets, set(), pallet_choices={1: list(pallets)})
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=3, score_mode="min_height_slack_then_gain",
                                           height_slack_mm=120))
    plan = scheduler.choose_action(state)
    # La mayor ganancia escalar (caja 2, palet 1) queda fuera del slack global.
    assert (plan.box_id, plan.pallet_id, plan.buffer_index) == (2, "1:2", 1)
    assert scheduler.last_eval_stats["evaluated_by_pallet"] == {"1:1": 2, "1:2": 2}
    assert scheduler.last_eval_stats["feasible_by_pallet"] == {"1:1": 2, "1:2": 2}
    blocked = scheduler.choose_action(replace(state, pallet_blocked={"1:2"}))
    assert blocked.pallet_id == "1:1"


def small_policy(n=2, changeover=60):
    base = PolicyPackerScheduler.from_defaults(lookahead_k=3, time_budget_ms=0)
    return OpenPalletScheduler(base.config, open_pallets=n, t_changeover=changeover)


@pytest.mark.parametrize("cutoff,has_fit,expect_closed", [(False, False, True), (True, False, False),
                                                          (False, True, False)])
def test_close_only_nonempty_slot_after_complete_visible_window(monkeypatch, cutoff, has_fit, expect_closed):
    policy = small_policy()
    policy._collect_pallets({1: SimpleNamespace(queue=boxes())})
    slot = "1:1"
    policy._pallets[slot].placements.append(object())
    policy._committed_placements[slot] = [{"box_id": 99}]

    def choose(state):
        policy._scheduler.last_eval_stats = dict(cutoff=cutoff, evaluated_by_pallet={slot: 2},
                                                 feasible_by_pallet={slot: 1} if has_fit else {})
        # Incluye un fallo aunque otra caja quepa: no basta para cerrar.
        policy._scheduler.last_blocked_pallets = {slot: "NO_SPACE"}
        return None

    monkeypatch.setattr(policy._scheduler, "choose_action", choose)
    policy.choose_action(ramps={1: SimpleNamespace(queue=boxes())}, destinations={}, now=20)
    assert bool(policy._records) == expect_closed
    if expect_closed:
        assert policy.next_wakeup(20) == 80
        assert policy._records[1][0]["reason"] == "NO_VISIBLE_FIT"
        assert not policy._pallets[slot].placements


@pytest.mark.parametrize("n,changeover", [(2, 60), (3, 0), (4, 60)])
def test_des_physical_pallets_dump_and_validator(n, changeover):
    base = PolicyPackerScheduler.from_defaults(lookahead_k=15, time_budget_ms=0,
                                              stacking_mode="heightfield", min_support_ratio=0.85)
    policy = OpenPalletScheduler(base.config, open_pallets=n, t_changeover=changeover)
    arrivals = [Arrival(0, 1, i, 1200, 800, 1200) for i in range(9)]
    result = simulate(arrivals, SimConfig(ramp_capacity=15, n_per_pallet=999999, t_pick_place=1),
                      decision_policy=policy, continuous_pallets=True, arrival_mode="immediate")
    records = split_pallets({"metrics": {"pallet_kpis": result.pallet_kpis}},
                           {"pallets": policy.export_committed_placements()})
    assert result.processed_boxes == 9
    assert result.stop_reason is None
    assert sum(r.n_boxes for r in records) == 9
    assert len(records) == 5
    assert [r.closure_reason for r in records].count("END") == 1
    assert run_kpi(records)["n_closed"] == 4
    assert result.changeovers_by_destination[1] == policy._slot_closures
    assert result.changeover_time_by_destination[1] == policy._slot_closures * changeover
    original = {str(i): (1200, 800, 1200) for i in range(1, 10)}
    for rec in records:
        assert not validate_pallet(rec.items, original, min_support=0.85)
        assert len({(i["physical_slot"], i["slot_generation"]) for i in rec.items}) == 1


def test_terminal_tails_count_all_but_one_without_changing_metric():
    base = PolicyPackerScheduler.from_defaults(lookahead_k=3, time_budget_ms=0,
                                              score_mode="min_height_then_gain")
    policy = OpenPalletScheduler(base.config, open_pallets=2, t_changeover=60)
    result = simulate([Arrival(0, 1, i, 1200, 800, 100) for i in range(3)],
                      SimConfig(ramp_capacity=3, n_per_pallet=999999), decision_policy=policy,
                      continuous_pallets=True, arrival_mode="immediate")
    records = split_pallets({"metrics": {"pallet_kpis": result.pallet_kpis}},
                           {"pallets": policy.export_committed_placements()})
    assert [r.closure_reason for r in records] == ["DRAIN", "END"]
    assert [r.n_boxes for r in records] == [1, 2]
    assert run_kpi(records)["n_closed"] == 1


def test_unplaceable_box_stops_without_counting_or_recycling_empty_slots():
    policy = small_policy()
    result = simulate([Arrival(0, 1, 0, 3000, 3000, 3000)],
                      SimConfig(ramp_capacity=3, n_per_pallet=999999), decision_policy=policy,
                      continuous_pallets=True, arrival_mode="immediate")
    assert result.stop_reason == "DEADLOCK"
    assert result.processed_boxes == 0
    assert not policy.export_committed_placements()
    assert result.pallet_kpis["continuous_pallets_total"][1] == 0


@pytest.mark.parametrize("overrides", [{"open_pallets": 0}, {"open_pallets": 1.5},
    {"open_pallets": 2, "micro_plan": True}, {"open_pallets": 2, "continuous_pallets": False},
    {"open_pallets": 2, "arrival_mode": "excel"}])
def test_open_pallets_rejects_unsupported_modes(overrides):
    with pytest.raises(ValueError, match="open_pallets"):
        run_simulation(**preset_params("arm6_4orient", **overrides))
