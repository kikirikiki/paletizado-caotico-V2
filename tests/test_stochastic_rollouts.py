"""H6 operational prior, isolation, budget, averaging and H5 compatibility."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel
from palca.scoring.catalog_prior import CATALOG, observed_seed, sample_catalog
from palca.scheduler.scheduler_v1 import (
    SchedulerConfig, SchedulerRampState, SchedulerSimState, SchedulerV1,
    _BeamNode, _BeamRampState,
)
from palca.integration.policy_packer_sched import PolicyPackerScheduler


def config(**kwargs):
    return SchedulerConfig(**dict(dict(micro_plan_enabled=True, micro_plan_value='rollout',
        micro_plan_node_budget=100, rollout_future_boxes=3, rollout_future_samples=3,
        micro_plan_rollout_leaves=2, micro_plan_window_total=3, heartbeat_sec=0,
        access_debt_weight=.25), **kwargs))


def box(i, height=100):
    return Box(i, 200, 200, height, 0, destination=1)


def test_prior_exact_ranges_and_independent_samples():
    draws = sample_catalog(b'known-observed-state', 0, 2000, (1,))
    assert draws == sample_catalog(b'known-observed-state', 0, 2000, (1,))
    assert draws != sample_catalog(b'known-observed-state', 1, 2000, (1,))
    for b in draws:
        assert any(w[0] <= b.width_mm <= w[1] and l[0] <= b.length_mm <= l[1]
                   and h[0] <= b.height_mm <= h[1] for _, _, w, l, h in CATALOG)
    assert sum(row[1] for row in CATALOG) == 101
    # Golden prefix also detects accidental changes to the portable sampler.
    assert hashlib.sha256(json.dumps([(b.length_mm,b.width_mm,b.height_mm)
                         for b in draws[:10]]).encode()).hexdigest() == '928fdeced1b1bead48fc9c731afbb48fc122914377af42ca954d1cb5577c723f'


def test_generic_never_reads_any_catalog_constant(monkeypatch):
    import palca.scoring.catalog_prior as priors
    expected = sample_catalog(b'generic-state', 0, 2000, (1,), prior='generic')
    class ForbiddenCatalog:
        def __iter__(self):
            raise AssertionError('generic read catalog types, weights or ranges')
        def __getitem__(self, key):
            raise AssertionError('generic indexed catalog')
        def __len__(self):
            raise AssertionError('generic read catalog length')
        def __bool__(self):
            raise AssertionError('generic inspected catalog')
    monkeypatch.setattr(priors, 'CATALOG', ForbiddenCatalog())
    actual = sample_catalog(b'generic-state', 0, 2000, (1,), prior='generic')
    assert actual == expected
    assert actual != sample_catalog(b'generic-state', 1, 2000, (1,), prior='generic')
    assert all(300 <= b.width_mm <= 500 and 350 <= b.length_mm <= 750
               and 250 <= b.height_mm <= 500 for b in actual)


def test_uniform_types_ignores_catalog_weights_and_is_equiprobable(monkeypatch):
    from collections import Counter
    import palca.scoring.catalog_prior as priors
    # Distinct point dimensions identify every selected type unambiguously.
    types = tuple((str(i), 10**i, (i+1, i+1), (10,10), (20,20)) for i in range(6))
    monkeypatch.setattr(priors, 'CATALOG', types)
    draws = sample_catalog(b'uniform-types', 0, 6000, (1,), prior='uniform_types')
    monkeypatch.setattr(priors, 'CATALOG', tuple((n, 0, w, l, h) for n, _, w, l, h in types))
    assert draws == sample_catalog(b'uniform-types', 0, 6000, (1,), prior='uniform_types')
    counts = Counter(b.width_mm for b in draws)
    assert set(counts) == set(range(1,7))
    assert all(900 < n < 1100 for n in counts.values())
    with pytest.raises(ValueError):
        sample_catalog(b'state', 0, 1, (1,), prior='unknown')


def test_prior_fails_on_stream_import_or_file_read():
    # A fresh interpreter catches new imports (including transitive loaders),
    # unlike a guard installed after modules are cached in this pytest process.
    code = '''
import builtins, sys
def no_dataset_io(event, args):
    if event == 'open':
        path = str(args[0]).lower()
        assert not (path.endswith('.xlsx') or '/data/' in path or path.startswith('data/')), path
sys.addaudithook(no_dataset_io)
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    assert not (name.startswith(('sim', 'scripts', 'pandas', 'openpyxl'))
                or 'loader' in name or name.startswith('palca.study')), name
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
from palca.scoring.catalog_prior import sample_catalog, observed_seed
def no_io(*args, **kwargs):
    raise AssertionError('prior attempted file I/O')
builtins.open = no_io
from pathlib import Path
Path.open = no_io
for prior in ('catalog', 'uniform_types', 'generic'):
    assert len(sample_catalog(observed_seed({}, {}), 0, 10, (1,), prior=prior)) == 10
'''
    subprocess.run([sys.executable, '-c', code], check=True)


@pytest.mark.parametrize('prior', ['catalog', 'uniform_types', 'generic'])
def test_future_independent_of_hidden_stream_and_deterministic(prior):
    class Secret:
        def __iter__(self):
            raise AssertionError('H6 touched real future')
    results = []
    for hidden in ((), Secret()):
        boxes = [box(i, 100+20*i) for i in range(3)]
        pallet = PalletModel(PalletSpec(), stacking_mode='heightfield')
        state = SchedulerSimState(0, {1: boxes}, {1: pallet}, set(),
            ramp_states={1: SchedulerRampState(tuple(boxes), hidden, 3)}, remaining_total=987)
        engine = SchedulerV1(config(rollout_prior=prior))
        plan = engine.choose_action(state)
        stats = engine.last_micro_plan_stats
        assert plan.preview.feasible
        assert stats['rollout_future_nodes'] == 18
        assert stats['rollout_future_samples_completed'] == 6
        assert stats['nodes_expanded'] <= 100
        assert engine.hard_cap_hits == 0
        assert not pallet.placements and len(boxes) == 3
        results.append((plan, stats))
    assert results[0] == results[1]


def test_visible_prefix_then_ordered_future_and_mean(monkeypatch):
    engine = SchedulerV1(config(rollout_future_boxes=2, rollout_future_samples=2))
    pallet = PalletModel(PalletSpec(), stacking_mode='heightfield')
    leaf = _BeamNode({1: _BeamRampState([box('visible')], [], 1)}, {1: pallet})
    calls = []
    real_greedy = SchedulerV1._choose_action_greedy
    def greedy(self, state, **kwargs):
        assert self.config.access_debt_weight == .25
        calls.append([b.box_id for queue in state.ramps.values() for b in queue])
        return real_greedy(self, state, **kwargs)
    monkeypatch.setattr(SchedulerV1, '_choose_action_greedy', greedy)
    monkeypatch.setattr('palca.scheduler.scheduler_v1.sample_catalog',
        lambda seed, sample, count, destinations, **kwargs: [box(f'{sample}:{i}', 100*(sample+1)) for i in range(count)])
    value, nodes, complete, future_nodes, samples = engine._stochastic_rollout_value(
        leaf, SchedulerSimState(0, {}, {}, set()), 5, None, b'state')
    assert calls == [['visible'], ['0:0'], ['0:1'], ['1:0'], ['1:1']]
    assert value == box(0).volume_mm3 * 4  # visible + mean(2*1, 2*2)
    assert (nodes, complete, future_nodes, samples) == (5, True, 4, 2)
    assert not pallet.placements
    # Budget exhaustion cannot pretend to have simulated future boxes.
    _, nodes, complete, future_nodes, samples = engine._stochastic_rollout_value(
        leaf, SchedulerSimState(0, {}, {}, set()), 2, None, b'state')
    assert nodes <= 2 and not complete and future_nodes == samples == 0


def test_all_scans_consume_shared_node_budget(monkeypatch):
    boxes = [box(i) for i in range(3)]
    engine = SchedulerV1(config(micro_plan_node_budget=27))
    counts = {'beam': 0, 'greedy': 0}
    expand = engine._expand_beam_node
    greedy = SchedulerV1._choose_action_greedy
    def counted_expand(**kwargs):
        counts['beam'] += 1
        return expand(**kwargs)
    def counted_greedy(self, *args, **kwargs):
        counts['greedy'] += 1
        return greedy(self, *args, **kwargs)
    monkeypatch.setattr(engine, '_expand_beam_node', counted_expand)
    monkeypatch.setattr(SchedulerV1, '_choose_action_greedy', counted_greedy)
    engine.choose_action(SchedulerSimState(0, {1: boxes}, {1: PalletModel(PalletSpec())}, set()))
    stats = engine.last_micro_plan_stats
    assert stats['nodes_expanded'] == sum(counts.values()) <= 27
    assert stats['rollout_nodes'] == counts['greedy']


@pytest.mark.parametrize('prior', ['catalog', 'uniform_types', 'generic'])
def test_parameter_propagation_and_validation(prior):
    policy = PolicyPackerScheduler.from_defaults(micro_plan_value='rollout',
        micro_plan_node_budget=100, rollout_future_boxes=6, rollout_future_samples=3, rollout_prior=prior)
    for cfg in (policy.config, policy._scheduler.config):
        assert (cfg.rollout_future_boxes, cfg.rollout_future_samples, cfg.rollout_prior) == (6,3,prior)
    for kwargs in ({'rollout_future_boxes': -1}, {'rollout_future_boxes': 1.5},
                   {'rollout_future_samples': 0}, {'rollout_future_samples': True},
                   {'rollout_prior': 'excel'}, {'micro_plan_node_budget': 0},
                   {'micro_plan_value': 'potential'}):
        with pytest.raises(ValueError):
            config(**kwargs)
    assert SchedulerConfig().rollout_future_boxes == 0


@pytest.mark.parametrize('prior', ['catalog', 'uniform_types', 'generic'])
def test_cli_and_sweep_parameter_propagation(prior):
    from sim.run import build_parser
    from scripts.run_sweep import load_configs
    args = build_parser().parse_args(['--excel', 'unused.xlsx', '--rollout-future-boxes', '6',
        '--rollout-future-samples', '3', '--rollout-prior', prior])
    assert (args.rollout_future_boxes, args.rollout_future_samples, args.rollout_prior) == (6,3,prior)
    configs = load_configs(Path('configs/sweeps/h6_tune.yaml'))
    assert {(c['overrides']['rollout_future_boxes'], c['overrides']['rollout_future_samples'])
            for c in configs[1:]} == {(n,s) for n in (3,6,10) for s in (1,3)}


def test_unplaceable_arrival_consumes_node_and_next_arrival_is_tried(monkeypatch):
    engine = SchedulerV1(config(rollout_future_boxes=2, rollout_future_samples=1))
    pallet = PalletModel(PalletSpec(), stacking_mode='heightfield')
    leaf = _BeamNode({}, {1: pallet})
    monkeypatch.setattr('palca.scheduler.scheduler_v1.sample_catalog',
        lambda *args, **kwargs: [Box('too-big', 10000,10000,10000,0,destination=1), box('fits')])
    value, nodes, complete, nf, ns = engine._stochastic_rollout_value(
        leaf, SchedulerSimState(0, {}, {}, set()), 2, None, b'state')
    assert value == box(0).volume_mm3
    assert (nodes, complete, nf, ns) == (2, True, 2, 1)


@pytest.mark.slow
@pytest.mark.parametrize('seed', [50021, 1])
def test_zero_future_exact_h5_two_seeds(seed, tmp_path):
    from palca.study.params import preset_params
    from palca.study.metric import run_kpi, split_pallets
    from scripts.run_sweep import sweep_lock
    from sim.run import run_simulation
    fixture = json.loads((Path(__file__).parent/'fixtures/h6_h5_legacy.json').read_text())[str(seed)]
    dump_path = tmp_path/'dump.json'
    with sweep_lock(retry_seconds=60):
        payload = run_simulation(**preset_params('arm6_4orient', episode_seed=seed, multi_ramp_seed=seed,
            micro_plan=True, micro_plan_node_budget=25, micro_plan_value='rollout',
            micro_plan_rollout_leaves=4, rollout_future_boxes=0,
            access_debt_weight=.25, access_debt_band_mm=600, access_debt_mode='penalty',
            dump_placements_path=str(dump_path)))
    dump = json.loads(dump_path.read_text())
    assert hashlib.sha256(json.dumps(dump['pallets'], sort_keys=True).encode()).hexdigest() == fixture['pallets_sha256']
    kpi = run_kpi(split_pallets(payload, dump))
    assert (kpi['kpi'], kpi['pallet_utils']) == (fixture['kpi'], fixture['pallet_utils'])
    assert payload['metrics']['pallet_kpis']['micro_plan_nodes_expanded'] == fixture['nodes']
    assert payload['params']['rollout_future_boxes'] == 0
    assert payload['metrics']['pallet_kpis']['hard_cap_hits'] == 0
