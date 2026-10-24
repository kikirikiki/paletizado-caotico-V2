"""G3 policy data checks.

Full production dump comparison (both D-041 members, seeds 50021/1):
    G3_FULL_EQUIVALENCE=1 pytest -q tests/test_policy_data_g3.py -k full_dump
Run explicitly: this exercises four full simulations twice, outside the smoke test.
"""
import builtins
import copy
from dataclasses import asdict, replace
import io
import json
import os
from pathlib import Path

import numpy as np
from numpy.testing import assert_equal
import pytest

from palca.learning import episode
from palca.scheduler.scheduler_v1 import SchedulerV1


def _normalize(value):
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()
                if k != 'git_head' and 'time' not in k and not k.endswith(('_ms', '_sec'))}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


@pytest.mark.parametrize('policy', ['official', 'official_b150'])
def test_short_dump_equivalence(monkeypatch, policy):
    original = SchedulerV1.choose_action
    dumps = []

    def capture(self, state):
        plan = original(self, state)
        if plan is not None:
            dumps.append(dict(action=(plan.buffer_index, *episode._placement(plan)),
                              preview=asdict(plan.preview), stats=copy.deepcopy(self.last_eval_stats)))
        return plan

    monkeypatch.setattr(SchedulerV1, 'choose_action', capture)
    result = episode.run_episode(1, 'mix', policy, count=30)
    assert 'cand_height_after' not in result
    enabled = _normalize(dumps)
    dumps.clear()
    monkeypatch.setattr(SchedulerV1, '_record_root_candidate', lambda *a, **k: None)
    # Bypass the data encoder while comparing actual search decisions.
    packing = episode.official_policy(policy)
    engine = packing._scheduler
    pallet = packing._new_pallet()
    stream = episode.synthetic_flow(1, 'mix', 30)
    queue, upstream = list(stream[:15]), iter(stream[15:])
    while queue:
        plan = engine.choose_action(episode.SchedulerSimState(0, {1: queue}, {1: pallet}, set()))
        if plan is None:
            assert pallet.placements
            pallet = packing._new_pallet()
            continue
        pallet.commit_place(plan.preview)
        queue.pop(plan.buffer_index)
        arrival = next(upstream, None)
        if arrival is not None:
            queue.append(arrival)
    assert len(enabled) == len(dumps) == 30
    assert enabled == _normalize(dumps)


def test_official_b150_data_no_io_determinism(monkeypatch):
    original = SchedulerV1.choose_action
    observed = []

    def capture(self, state):
        plan = original(self, state)
        if plan is not None:
            candidates = self.last_root_candidates
            assert candidates
            chosen = [c for c in candidates if c['chosen']]
            assert len(chosen) == 1
            assert chosen[0]['buffer_index'] == plan.buffer_index
            assert chosen[0]['placement'] == episode._placement(plan)
            values = [c['value'] for c in candidates if c['value'] is not None]
            assert values
            assert chosen[0]['value'] == max(values)
            clone = copy.deepcopy(state.pallets[plan.pallet_id])
            clone.commit_place(plan.preview)
            expected = episode.encode_state(clone, state.ramps[plan.ramp_id])["height"]
            # The chosen after-map must match a real commit, including offsets.
            placement = chosen[0]['placement']
            clone = copy.deepcopy(state.pallets[plan.pallet_id])
            x, y, z, l, w, h = placement
            clone.placements.append(replace(plan.preview.placement, x_mm=x, y_mm=y,
                z_mm=z, length_mm=l, width_mm=w, height_mm=h))
            assert_equal(episode.encode_state(clone, state.ramps[plan.ramp_id])["height"], expected)
            observed.append(tuple((c['buffer_index'], c['placement'], c['value'], c['chosen'])
                                  for c in candidates))
        return plan

    def forbidden(*args, **kwargs):
        raise AssertionError('D-032: episode attempted file I/O')

    monkeypatch.setattr(SchedulerV1, 'choose_action', capture)
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(io, 'open', forbidden)
    a = episode.run_episode(1, 'mix', 'official_b150', count=30, with_after=True)
    first = observed[:]
    observed.clear()
    b = episode.run_episode(1, 'mix', 'official_b150', count=30, with_after=True)
    assert first == observed
    assert len(first) == a['placed_boxes'] == 30
    for key in a:
        assert_equal(a[key], b[key])
    assert len(a['cand_count']) > 0, 'Must exercise persisted rows, not only censored states'
    for row, n in enumerate(a['cand_count']):
        i = a['cand_chosen'][row]
        assert 0 <= i < n <= 64
        assert_equal(a['cand_actions'][row, i],
                                [a['belt_index'][row], *a['placement'][row]])
        assert np.all(a['cand_actions'][row, n:] == -1)
        assert np.all(np.isnan(a['cand_values'][row, n:]))
        assert np.all(a['cand_height_after'][row, n:] == 0)
        assert np.all(a['cand_height_after'][row, i] >= a['height'][row])
    for key, dtype, shape in [
        ('cand_actions', np.int32, (64, 7)), ('cand_values', np.float32, (64,)),
        ('cand_chosen', np.int16, ()), ('cand_count', np.int16, ()),
        ('cand_height_after', np.uint16, (64, 62, 42))]:
        assert a[key].dtype == dtype
        assert a[key].shape[1:] == shape


def test_b150_configuration():
    config = episode.official_policy('official_b150')._scheduler.config
    assert config.micro_plan_node_budget == 150
    assert config.micro_plan_rollout_leaves == 4
    assert config.micro_plan_hard_cap_ms == 600000
    assert config.rollout_prior != 'oracle'


@pytest.mark.skipif(os.environ.get('G3_FULL_EQUIVALENCE') != '1', reason='explicit full dump check')
def test_full_dump_equivalence(monkeypatch, tmp_path):
    # Reuse the production runner's exact parameter construction, with no sweep.
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('g3_sweep', root / 'scripts/run_sweep.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    from sim.run import run_simulation
    import yaml
    configs = yaml.safe_load((root / 'configs/sweeps/computo_eval.yaml').read_text())
    recorder = SchedulerV1._record_root_candidate
    for config in configs:
        for seed in (50021, 1):
            task = runner.make_task(config, seed, tmp_path, 'normalized', 'normalized')
            params = task['params']
            params['micro_plan_hard_cap_ms'] = 600000
            Path(task['directory']).mkdir(parents=True, exist_ok=True)
            snapshots = []
            for enabled in (True, False):
                monkeypatch.setattr(SchedulerV1, '_record_root_candidate',
                                    recorder if enabled else lambda *a, **k: None)
                import random
                random.seed(seed)
                np.random.seed(seed)
                run_simulation(**params)
                snapshots.append(_normalize(json.loads(Path(params['dump_placements_path']).read_text())))
            assert snapshots[0] == snapshots[1], (config['name'], seed)


def test_root_aggregation_and_missing_values():
    from types import SimpleNamespace
    from dataclasses import dataclass

    @dataclass
    class Placement:
        x_mm: int = 0
        y_mm: int = 0
        z_mm: int = 0
        length_mm: int = 300
        width_mm: int = 200
        height_mm: int = 100

    engine = episode.official_policy('official_b150')._scheduler
    plan = SimpleNamespace(ramp_id=1, buffer_index=2,
                           preview=SimpleNamespace(placement=Placement()))
    engine._record_root_candidate(plan)
    assert engine.last_root_candidates[0]['value'] is None
    engine._record_root_candidate(plan, 3.0)
    engine._record_root_candidate(plan, 2.0)
    engine._record_root_candidate(plan, 4.0)
    engine._record_root_candidate(plan, chosen=True)
    assert len(engine.last_root_candidates) == 1
    assert engine.last_root_candidates[0]['value'] == 4.0
    assert engine.last_root_candidates[0]['chosen']


def test_manifest_after_identity():
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('g3_generator', root / 'scripts/gen_value_data.py')
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    previous = dict(policy='official_b150', with_after=False)
    assert generator._compatible(previous, previous.copy())
    assert not generator._compatible(previous, dict(previous, with_after=True))
    assert generator._compatible(dict(policy='greedy'), dict(policy='greedy', with_after=False))


@pytest.mark.skipif(os.environ.get('G3_BENCHMARK') != '1', reason='explicit throughput measurement')
def test_benchmark(tmp_path):
    """G3_BENCHMARK=1 pytest -q -s tests/test_policy_data_g3.py -k benchmark.

    Two 30-box episodes per mode; set G3_BENCHMARK_COUNT=186 for full episodes.
    Throughput counts persisted states, including compression time, one worker.
    """
    import time
    count = int(os.environ.get('G3_BENCHMARK_COUNT', '30'))
    results = {}
    for with_after in (False, True):
        started = time.perf_counter()
        states, sizes = 0, []
        for seed in (200000, 200001):
            result = episode.run_episode(seed, 'mix', 'official_b150',
                                         count=count, with_after=with_after)
            path = tmp_path / f'ep_{seed}_{with_after}.npz'
            np.savez_compressed(path, **result)
            states += len(result['util_final'])
            sizes.append(path.stat().st_size)
        seconds = time.perf_counter() - started
        results[str(with_after)] = dict(episodes=2, count=count, states=states,
            seconds=seconds, states_per_hour_per_worker=states * 3600 / seconds,
            mean_episode_bytes=sum(sizes) / 2)
    print(json.dumps(results, sort_keys=True))
