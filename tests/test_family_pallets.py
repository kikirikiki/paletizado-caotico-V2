"""D-019 R1: routing constrains scoring, never physical closure feasibility."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.policy_open_pallets import OpenPalletScheduler
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1
from palca.study.metric import run_kpi, split_pallets
from palca.study.params import preset_params
from scripts.run_sweep import load_configs
from sim.run import build_parser, run_simulation


ROOT = Path(__file__).resolve().parents[1]
FROZEN = json.loads((ROOT / "tests/fixtures/open_pallets_score_v1.json").read_text())


class RoutingPallet:
    spec = PalletSpec()
    accessibility_delta_mm = 400

    def __init__(self, height, reason=None):
        self.height = height
        self.reason = reason
        self.placements = []

    def preview_place(self, box):
        placement = Placement(0, 0, 0, False, 0, box.length_mm, box.width_mm, box.height_mm,
                              box_id=box.box_id)
        return PlacementPreview(self.reason is None, placement if self.reason is None else None,
                                packing_gain=1.0, fragmentation=0, height_after_mm=self.height,
                                infeasible_reason=self.reason)


def routing_state(dims=(605, 445, 355), preferred_reason=None):
    box = Box(1, *dims, timestamp=0, destination=1)
    # B has a much better production height score; assignment must still prefer A.
    pallets = {"1:1": RoutingPallet(1000, preferred_reason), "1:2": RoutingPallet(100)}
    return SchedulerSimState(0, {1: [box]}, pallets, set(), pallet_choices={1: list(pallets)},
                             open_pallets_assignment="family")


@pytest.mark.parametrize("mode", ["penalty", "filter"])
def test_dominant_prefers_a_even_when_b_has_better_production_score_with_h1(mode):
    state = routing_state()
    config = SchedulerConfig(score_mode="min_height_slack_then_gain", height_slack_mm=120,
                             access_debt_weight=0.25, access_debt_band_mm=600, access_debt_mode=mode)
    scheduler = SchedulerV1(config)
    assert scheduler.choose_action(state).pallet_id == "1:1"
    assert scheduler.collect_access_debt_metrics()["measured_candidates"] == 1
    # Geometric fits on B remain visible to the existing closure rule.
    assert scheduler.last_eval_stats["feasible_by_pallet"] == {"1:1": 1, "1:2": 1}
    assert SchedulerV1(config).choose_action(replace(state, open_pallets_assignment="score")).pallet_id == "1:2"


@pytest.mark.parametrize("dims,preferred", [
    ((600, 440, 345), "1:1"), ((610, 450, 395), "1:1"),
    ((599, 445, 355), "1:2"), ((611, 445, 355), "1:2"),
    ((605, 439, 355), "1:2"), ((605, 451, 355), "1:2"),
    ((605, 445, 344), "1:2"), ((605, 445, 396), "1:2"),
    ((445, 605, 355), "1:2"),
])
def test_family_bounds_use_original_dimensions(dims, preferred):
    scheduler = SchedulerV1(SchedulerConfig())
    assert scheduler.choose_action(routing_state(dims)).pallet_id == preferred


@pytest.mark.parametrize("reason", ["NO_SPACE", "HEIGHT_LIMIT", "ACCESSIBILITY"])
@pytest.mark.parametrize("dominant", [True, False])
def test_overflow_only_if_preferred_has_no_feasible_placement(reason, dominant):
    state = routing_state((605, 445, 355) if dominant else (400, 300, 200))
    preferred, other = state.choices_for_box(state.ramps[1][0])
    state.pallets[preferred].reason = reason
    scheduler = SchedulerV1(SchedulerConfig(access_debt_weight=0.25, access_debt_band_mm=600))
    assert scheduler.choose_action(state).pallet_id == other
    state.pallets[other].reason = "NO_SPACE"
    assert scheduler.choose_action(state) is None


def test_unavailable_preferred_slot_allows_overflow():
    state = replace(routing_state(), pallet_blocked={"1:1"})
    assert SchedulerV1(SchedulerConfig()).choose_action(state).pallet_id == "1:2"


def test_assignment_does_not_close_slot_that_can_fit_other_family():
    base = PolicyPackerScheduler.from_defaults(lookahead_k=3, time_budget_ms=0)
    policy = OpenPalletScheduler(base.config, open_pallets=2, t_changeover=60,
                                 open_pallets_assignment="family")
    ramps = {1: SimpleNamespace(queue=[Box(1, 605, 445, 355, 0, destination=1)])}
    policy._collect_pallets(ramps)
    plan = policy.choose_action(ramps=ramps, destinations={}, now=0)
    policy.commit_plan(plan, time=0)
    # Only the other family is now visible, but it fits both slots.
    ramps[1].queue = [Box(2, 400, 300, 200, 0, destination=1)]
    plan = policy.choose_action(ramps=ramps, destinations={}, now=14)
    assert plan.pallet_id == "1:2"
    assert not policy._records
    assert policy._scheduler.last_eval_stats["feasible_by_pallet"] == {"1:2": 1, "1:1": 1}


@pytest.mark.parametrize("h1", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_score_exact_prechange_dump_and_kpi(tmp_path, h1, explicit):
    """Golden generated using the original HEAD source, before this change."""
    dump_path = tmp_path / "dump.json"
    options = dict(access_debt_weight=0.25, access_debt_band_mm=600, access_debt_mode="penalty") if h1 else {}
    if explicit:
        options["open_pallets_assignment"] = "score"
    payload = run_simulation(**preset_params(
        "arm6_4orient", excel_path=FROZEN["excel"], open_pallets=2,
        episode_seed=FROZEN["seed"], dump_placements_path=str(dump_path), **options))
    dump = json.loads(dump_path.read_text())
    expected = FROZEN["cases"]["h1" if h1 else "no_h1"]
    assert hashlib.sha256(json.dumps(dump["pallets"], sort_keys=True).encode()).hexdigest() == expected["placements_sha256"]
    assert run_kpi(split_pallets(payload, dump)) == expected["kpi"]
    sequence = payload["metrics"]["pallet_kpis"]["continuous_pallet_sequence"]
    assert {str(k): v for k, v in sequence.items()} == expected["sequence"]
    assert payload["metrics"]["pallet_kpis"]["deadline_cutoffs_count"] == 0


def test_n1_family_is_inert(tmp_path):
    dumps = []
    for assignment in ("score", "family"):
        path = tmp_path / f"{assignment}.json"
        run_simulation(**preset_params("arm6_4orient", excel_path=FROZEN["excel"], open_pallets=1,
                                      open_pallets_assignment=assignment, dump_placements_path=str(path),
                                      episode_seed=FROZEN["seed"],
                                      access_debt_weight=0.25, access_debt_band_mm=600))
        dumps.append(json.loads(path.read_text())["pallets"])
    assert dumps[0] == dumps[1]


@pytest.mark.parametrize("options", [{"open_pallets_assignment": "typo"},
    {"open_pallets": 3, "open_pallets_assignment": "family"},
    {"open_pallets": 2, "open_pallets_assignment": "family", "micro_plan": True}])
def test_invalid_assignment_modes(options):
    with pytest.raises(ValueError, match="open_pallets"):
        run_simulation(**preset_params("arm6_4orient", **options))


def test_cli_and_sweep_configuration():
    args = build_parser().parse_args(["--excel", FROZEN["excel"], "--open-pallets-assignment", "family"])
    assert args.open_pallets_assignment == "family"
    configs = load_configs(ROOT / "configs/sweeps/lever_family_pallets.yaml")
    assert len(configs) == 6
    assert all(not preset_params(c["preset"], **c["overrides"])["micro_plan"] for c in configs)
