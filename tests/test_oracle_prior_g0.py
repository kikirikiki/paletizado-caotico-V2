"""D-039 oracle bound: deliberate D-022 leakage, opt-in and default isolation."""
from dataclasses import replace
from pathlib import Path
import random
from types import SimpleNamespace

import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.packer.pallet_model import PalletModel
from palca.scheduler.scheduler_v1 import (
    SchedulerConfig, SchedulerRampState, SchedulerSimState, SchedulerV1, _BeamNode,
)


def oracle_config(**overrides):
    return replace(SchedulerConfig(micro_plan_enabled=True, micro_plan_value='rollout',
        micro_plan_node_budget=57, micro_plan_hard_cap_ms=600000,
        rollout_future_boxes=6, rollout_prior='oracle', allow_oracle_leak=True), **overrides)


@pytest.mark.parametrize('value', [False, None, 1, 'true'])
def test_oracle_requires_explicit_true(value):
    with pytest.raises(ValueError, match='allow_oracle_leak'):
        oracle_config(allow_oracle_leak=value)
    with pytest.raises(ValueError, match='allow_oracle_leak'):
        SchedulerConfig(rollout_prior='oracle')
    with pytest.raises(ValueError, match='allow_oracle_leak'):
        PolicyPackerScheduler.from_defaults(rollout_prior='oracle')
    assert SchedulerConfig().allow_oracle_leak is False


@pytest.mark.parametrize('count', [0, 2, 6, 9])
def test_real_arrivals_fifo_same_across_samples_and_leaves(count, monkeypatch):
    engine = SchedulerV1(oracle_config(rollout_future_samples=3))
    boxes = tuple(Box(i, 400+i, 300, 200, 0, destination=1) for i in range(count))
    state = SchedulerSimState(0, {}, {1: PalletModel()}, set(),
        ramp_states={1: SchedulerRampState((), boxes, 15)})
    seen = []
    def scan(self, state, **kwargs):
        seen.extend(state.ramps[0])
        return None  # unplaceable arrivals still consume a scan and preserve FIFO
    monkeypatch.setattr(SchedulerV1, '_choose_action_greedy', scan)
    monkeypatch.setattr('palca.scheduler.scheduler_v1.sample_catalog',
                        lambda *a, **kw: pytest.fail('oracle sampled synthetic arrivals'))
    for _ in range(2):
        leaf = _BeamNode({}, state.pallets)
        _, consumed, completed, nodes, samples = engine._stochastic_rollout_value(
            leaf, state, 100, None, b'unused')
        assert consumed == nodes == 3 * min(count, 6)
        assert completed and samples == 3
    assert seen == list(boxes[:6]) * 6
    assert state.ramp_states[1].upstream == boxes
    assert not state.pallets[1].placements


def test_oracle_respects_node_budget(monkeypatch):
    engine = SchedulerV1(oracle_config(rollout_future_samples=2))
    boxes = tuple(Box(i, 400, 300, 200, 0, destination=1) for i in range(6))
    state = SchedulerSimState(0, {}, {1: PalletModel()}, set(),
        ramp_states={1: SchedulerRampState((), boxes, 15)})
    monkeypatch.setattr(SchedulerV1, '_choose_action_greedy', lambda *a, **kw: None)
    _, consumed, completed, nodes, samples = engine._stochastic_rollout_value(
        _BeamNode({}, state.pallets), state, 5, None, b'unused')
    assert consumed == nodes == 4 and not completed and samples == 0


def test_config_pipeline_dump_and_plan_mark_leak():
    from scripts.run_sweep import load_configs
    from sim.run import _build_placements_dump, build_parser
    configs = load_configs(Path('configs/sweeps/g0_margen.yaml'))
    official = next(c for c in configs if c['name'] == 'h6_sin_gate')
    reference = next(c for c in load_configs(Path('configs/sweeps/pulmon_tune.yaml'))
                     if c['name'] == 'h6_sin_gate')
    assert official['overrides'] == reference['overrides']
    for name, n, budget, leaves in [('oraculo_f6', 6, 57, 2),
                                   ('oraculo_f30_b400_l4', 30, 400, 4)]:
        config = next(c for c in configs if c['name'] == name)
        assert config['role'] == 'bound'
        expected = dict(official['overrides'], rollout_prior='oracle', allow_oracle_leak=True,
            rollout_future_boxes=n, micro_plan_node_budget=budget, micro_plan_rollout_leaves=leaves)
        assert config['overrides'] == expected
    args = build_parser().parse_args(['--excel', 'unused.xlsx', '--rollout-prior', 'oracle',
                                      '--allow-oracle-leak'])
    assert args.allow_oracle_leak and args.rollout_prior == 'oracle'
    policy = PolicyPackerScheduler.from_defaults(micro_plan_enabled=True,
        micro_plan_value='rollout', micro_plan_node_budget=57, rollout_future_boxes=6,
        rollout_prior='oracle', allow_oracle_leak=True)
    assert policy.config.allow_oracle_leak and policy._scheduler.config.allow_oracle_leak
    boxes = [Box(1, 400, 300, 200, 0, destination=1)]
    state = SchedulerSimState(0, {1: boxes}, {1: policy._new_pallet()}, set(),
        ramp_states={1: SchedulerRampState(tuple(boxes), (), 15)})
    assert policy._scheduler.choose_action(state) is not None
    assert policy._scheduler.last_micro_plan_stats['oracle_leak'] is True
    assert policy.collect_kpis()['oracle_leak'] is True
    assert _build_placements_dump(decision_policy=policy,
        params={'rollout_prior': 'oracle'})['oracle_leak'] is True
    assert 'oracle_leak' not in _build_placements_dump(decision_policy=None,
        params={'rollout_prior': 'generic'})


def test_oracle_upstream_snapshot_with_staging():
    policy = PolicyPackerScheduler.from_defaults(pallet_staging_slots=1,
        rollout_prior='oracle', allow_oracle_leak=True)
    box = Box(2, 400, 300, 200, 0, destination=1)
    ramp = SimpleNamespace(queue=[], upstream=[box], capacity=15)
    assert policy._collect_ramp_states({1: ramp})[1].upstream == (box,)


@pytest.mark.parametrize('prior', ['generic', 'oracle'])
def test_d022_identical_prefix_100_suffix_permuted(prior, record_property):
    """Use D-022's paired FIFO replay until the first changed box is visible.

    Generic must match every decision; the deliberate oracle must differ while
    both the visible state and all prior physical decisions still match.
    """
    rng = random.Random(283)
    flow = [Box(i, rng.choice((410, 470, 530)), rng.choice((270, 310, 370)),
                rng.choice((230, 290, 350)), 0, destination=1) for i in range(100)]
    # A permuted mix of gap fillers and full-pallet boxes makes the hidden
    # continuation change leaf values before those boxes reach the belt.
    flow += [Box(i, 100, 100, 350, 0, destination=1) if i < 115 else
             Box(i, 1200, 800, 350, 0, destination=1) for i in range(100, 130)]
    changed = flow[:100] + list(reversed(flow[100:]))
    policies = [PolicyPackerScheduler.from_defaults(lookahead_k=15, time_budget_ms=0,
        stability_mode='off', micro_plan_enabled=True,
        micro_plan_node_budget=400, micro_plan_hard_cap_ms=600000, micro_plan_depth=1,
        micro_plan_value='rollout', micro_plan_rollout_leaves=8,
        rollout_future_boxes=30, rollout_future_samples=1, rollout_prior=prior,
        allow_oracle_leak=prior == 'oracle') for _ in range(2)]
    queues = [list(flow[:15]), list(changed[:15])]
    upstreams = [list(flow[15:]), list(changed[15:])]
    # A single physical layer keeps the paired replay inexpensive while retaining
    # real packing, pallet replacement and future-dependent choice of first pick.
    def new_pallets():
        return [PalletModel(PalletSpec(1200, 800, 350), stacking_mode='heightfield')
                for _ in policies]
    pallets = new_pallets()
    picks = closures = future_nodes = 0
    while queues[0] == queues[1]:
        plans = []
        for i, policy in enumerate(policies):
            state = SchedulerSimState(picks, {1: queues[i]}, {1: pallets[i]}, set(),
                ramp_states={1: SchedulerRampState(tuple(queues[i]), tuple(upstreams[i]), 15)},
                remaining_total=len(queues[i])+len(upstreams[i]))
            plans.append(policy._scheduler.choose_action(state))
            stats = policy._scheduler.last_micro_plan_stats
            future_nodes += stats.get('rollout_future_nodes', 0)
            assert stats['nodes_expanded'] <= 400
            assert policy._scheduler.hard_cap_hits == 0
            assert stats.get('oracle_leak', False) == (prior == 'oracle')
        if prior == 'oracle' and plans[0] != plans[1]:
            assert picks < 86 and future_nodes > 0
            record_property('divergence_before_pick', picks + 1)
            return
        assert plans[0] == plans[1]
        if plans[0] is None:
            assert pallets[0].placements
            pallets = new_pallets()
            closures += 1
            continue
        for i, plan in enumerate(plans):
            pallets[i].commit_place(plan.preview)
            queues[i].pop(plan.buffer_index)
            queues[i].append(upstreams[i].pop(0))
        picks += 1
    assert prior == 'generic', 'D-022 failed to detect the deliberate oracle leak'
    record_property('identical_picks', picks)
    assert picks == 86 and closures >= 1 and future_nodes > 0
    assert queues[0][-1].box_id == 100 and queues[1][-1].box_id == 129


@pytest.mark.parametrize('prior', ['generic', 'oracle'])
def test_simulation_dump_and_kpis_opt_in(prior, tmp_path, monkeypatch):
    import json
    import pandas as pd
    from sim.run import run_simulation

    snapshots = []
    original = SchedulerV1._stochastic_rollout_value
    def rollout(self, leaf, state, *args):
        snapshots.append(tuple(box for r in state.ramp_states.values() for box in r.upstream))
        return original(self, leaf, state, *args)
    monkeypatch.setattr(SchedulerV1, '_stochastic_rollout_value', rollout)
    excel = tmp_path / 'flow.xlsx'
    pd.DataFrame({'timestamp': [0] * 20, 'destino': [1] * 20,
                  'largo': [100] * 20, 'ancho': [100] * 20,
                  'alto': [100] * 20}).to_excel(excel, index=False)
    dump_path = tmp_path / 'dump.json'
    payload = run_simulation(excel_path=str(excel), out_path=None, model='M1', n_per_pallet=24,
        t_pick_place=1, staging_cap=0, policy='palca', lookahead_k=15,
        arrival_mode='immediate', stacking_mode='heightfield', stability_mode='off',
        time_budget_ms=0, micro_plan=True, micro_plan_node_budget=57,
        micro_plan_value='rollout', micro_plan_rollout_leaves=2,
        rollout_future_boxes=6, rollout_prior=prior, allow_oracle_leak=prior == 'oracle',
        dump_placements_path=str(dump_path))
    dump = json.loads(dump_path.read_text())
    assert dump['pallets']
    assert any(snapshots), 'integration did not pass the real upstream to H6'
    if prior == 'oracle':
        assert dump['oracle_leak'] is True
        assert dump['params']['allow_oracle_leak'] is True
        assert dump['params']['oracle_leak'] is True
        assert payload['metrics']['pallet_kpis']['oracle_leak'] is True
    else:
        assert 'oracle_leak' not in dump
        assert 'oracle_leak' not in dump['params']
        assert 'allow_oracle_leak' not in dump['params']
        assert 'oracle_leak' not in payload['metrics']['pallet_kpis']
