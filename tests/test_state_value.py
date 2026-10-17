"""H5 visibility, fractional completion, bounded rollouts and legacy fixtures."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel
from palca.scheduler.scheduler_v1 import (SchedulerConfig, SchedulerSimState, SchedulerV1,
                                        SchedulerRampState, _BeamNode, _BeamRampState)
from palca.scoring.state_value import flat_zones, visible_completion_potential
from palca.integration.policy_packer_sched import PolicyPackerScheduler


def box(i=1, length=200, width=200, height=100):
    return Box(i, length, width, height, 0, destination=1)


def test_potential_accessible_dead_narrow_and_no_double_count():
    pallet = PalletModel(PalletSpec(length_mm=400, width_mm=200, max_height_mm=700))
    pallet.accessibility_delta_mm = 400
    b = box(height=200)
    assert visible_completion_potential(pallet, [b]) == b.volume_mm3
    assert visible_completion_potential(pallet, []) == 0
    assert visible_completion_potential(pallet, [box(length=500, width=500)]) == 0
    pallet.placements.append(SimpleNamespace(x_mm=0, y_mm=0, z_mm=0,
                                            length_mm=200, width_mm=200, height_mm=600))
    assert visible_completion_potential(pallet, [b]) == 0
    # Turning accessibility off makes the floor usable (same geometry).
    pallet.accessibility_delta_mm = 0
    assert visible_completion_potential(pallet, [b]) > 0
    zones = flat_zones(pallet)
    assert sum(l*w for _,_,l,w,_ in zones) == pallet.bin_area_mm2


def test_fractional_capacity_and_weight():
    pallet = PalletModel(PalletSpec(length_mm=200, width_mm=200, max_height_mm=150))
    boxes = [box(i) for i in range(2)]
    assert visible_completion_potential(pallet, boxes) == 1.5 * boxes[0].volume_mm3
    node = _BeamNode({1: _BeamRampState(boxes, [], 2)}, {1: pallet})
    engine = SchedulerV1(SchedulerConfig(micro_plan_value='potential', micro_plan_value_weight=.5))
    engine._set_state_value(node)
    assert node.state_value == .75 * boxes[0].volume_mm3


@pytest.mark.parametrize('mode', ['potential', 'rollout'])
def test_visible_only_determinism_budget_and_original_untouched(mode):
    results = []
    for future in ([], [box('secret', 1, 1, 1)]):
        boxes = [box(i, height=100 + (i%3)*20) for i in range(6)]
        pallet = PalletModel(PalletSpec(length_mm=400, width_mm=400, max_height_mm=400),
                             stacking_mode='heightfield')
        state = SchedulerSimState(0, {1: boxes}, {1: pallet}, set(),
                                  ramp_states={1: SchedulerRampState(tuple(boxes), tuple(future), 6)})
        engine = SchedulerV1(SchedulerConfig(micro_plan_enabled=True, micro_plan_value=mode,
                             micro_plan_node_budget=40, micro_plan_rollout_leaves=2,
                             micro_plan_window_total=6, heartbeat_sec=0, access_debt_weight=.25))
        choices = []
        counts = []
        while state.ramps[1]:
            plan = engine.choose_action(state)
            assert plan is not None and plan.preview.feasible
            choices.append(plan)
            stats = engine.last_micro_plan_stats
            assert stats['nodes_expanded'] <= 40
            if mode == 'rollout' and len(boxes) > 3:
                assert stats['rollout_nodes'] > 0
                assert stats['rollout_completed_leaves'] == 2
            counts.append(stats['nodes_expanded'])
            assert engine.hard_cap_hits == 0
            assert engine.micro_plan_fallback_greedy == 0
            # The search and rollout have committed nothing to the real pallet.
            assert len(pallet.placements) == len(choices)-1
            pallet.commit_place(plan.preview)
            boxes.pop(plan.buffer_index)
            state = replace(state, ramp_states={1: SchedulerRampState(tuple(boxes), tuple(future), 6)})
        results.append((choices, counts))
    assert results[0] == results[1]


def test_value_parameter_propagation_and_validation():
    policy = PolicyPackerScheduler.from_defaults(micro_plan_value='rollout',
              micro_plan_value_weight=.5, micro_plan_rollout_leaves=2)
    cfg = policy._scheduler.config
    assert (cfg.micro_plan_value, cfg.micro_plan_value_weight, cfg.micro_plan_rollout_leaves) == ('rollout', .5, 2)
    for kwargs in ({'micro_plan_value':'other'}, {'micro_plan_value_weight':-1},
                   {'micro_plan_value_weight':float('nan')}, {'micro_plan_value_weight':float('inf')},
                   {'micro_plan_rollout_leaves':0}, {'micro_plan_rollout_leaves':1.5}):
        with pytest.raises(ValueError):
            SchedulerConfig(**kwargs)


@pytest.mark.slow
@pytest.mark.parametrize('seed', [50021, 1])
def test_score_exactly_matches_pre_h5_canonical_fixture(seed, tmp_path):
    from palca.study.metric import run_kpi, split_pallets
    from palca.study.params import preset_params
    from scripts.run_sweep import sweep_lock
    from sim.run import run_simulation
    fixture = json.loads((Path(__file__).parent/'fixtures/h5_score_legacy.json').read_text())[str(seed)]
    dump_path = tmp_path/'dump.json'
    with sweep_lock(retry_seconds=60):
        payload = run_simulation(**preset_params('arm6_4orient', episode_seed=seed, multi_ramp_seed=seed,
            micro_plan=True, micro_plan_node_budget=100, micro_plan_value='score',
            access_debt_weight=.25, access_debt_band_mm=600, access_debt_mode='penalty',
            dump_placements_path=str(dump_path)))
    dump = json.loads(dump_path.read_text())
    assert hashlib.sha256(json.dumps(dump['pallets'], sort_keys=True).encode()).hexdigest() == fixture['pallets_sha256']
    kpi = run_kpi(split_pallets(payload, dump))
    assert kpi['kpi'] == fixture['kpi']
    assert kpi['pallet_utils'] == fixture['pallet_utils']
    assert payload['params']['micro_plan_value'] == 'score'
    assert payload['metrics']['pallet_kpis']['hard_cap_hits'] == 0


def test_d015_replay_uses_local_height_and_gap_only():
    from palca.study.accessibility_robustness import accessibility_gap_violations
    def p(x, z=0, h=600):
        return dict(x_mm=x,y_mm=0,z_mm=z,length_mm=200,width_mm=200,height_mm=h)
    assert accessibility_gap_violations([p(0),p(205,h=100)],400,0) == 0
    assert accessibility_gap_violations([p(0),p(205,h=100)],400,5) == 1
    assert accessibility_gap_violations([p(0),p(205,h=100)],0,50) == 0
    assert accessibility_gap_violations([p(0),p(0,z=600,h=100)],400,50) == 0


def test_rollout_scans_share_total_budget(monkeypatch):
    boxes = [box(i) for i in range(8)]
    state = SchedulerSimState(0, {1: boxes}, {1: PalletModel(PalletSpec())}, set())
    engine = SchedulerV1(SchedulerConfig(micro_plan_enabled=True, micro_plan_value='rollout',
                          micro_plan_node_budget=31, micro_plan_rollout_leaves=4, heartbeat_sec=0))
    attempts = []
    scans = []
    expand = engine._expand_beam_node
    greedy = SchedulerV1._choose_action_greedy
    def counted_expand(**kwargs):
        attempts.append(1)
        return expand(**kwargs)
    def counted_greedy(self, *args, **kwargs):
        scans.append(1)
        return greedy(self, *args, **kwargs)
    monkeypatch.setattr(engine, '_expand_beam_node', counted_expand)
    monkeypatch.setattr(SchedulerV1, '_choose_action_greedy', counted_greedy)
    assert engine.choose_action(state).preview.feasible
    stats = engine.last_micro_plan_stats
    assert 0 < len(scans) == stats['rollout_nodes']
    assert stats['nodes_expanded'] == len(attempts) + len(scans) <= 31


def test_rollout_batchfill_keeps_only_global_deadline(monkeypatch):
    engine = SchedulerV1(SchedulerConfig(micro_plan_enabled=True, micro_plan_value='rollout',
                          micro_plan_node_budget=50, batchfill_layer_starter=True))
    leaf = _BeamNode({1: _BeamRampState([box()], [], 1)}, {1: PalletModel(PalletSpec())})
    monkeypatch.setattr('palca.scheduler.scheduler_v1.time.perf_counter', lambda: 10.0)
    def greedy(self, *args, deadline, **kwargs):
        assert self._batchfill_deadline(deadline) == deadline
        return None
    monkeypatch.setattr(SchedulerV1, '_choose_action_greedy', greedy)
    assert engine._rollout_value(leaf, SchedulerSimState(0, {}, {}, set()), 1, 20.0)[1:] == (1, True)
