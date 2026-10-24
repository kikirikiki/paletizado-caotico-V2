"""G1 search: observable inputs, batched search, isolated artifacts and R13 defaults."""
import builtins
import importlib.util
import io
import json
from pathlib import Path
import random
import subprocess
import sys
import types

import pytest

from palca.domain.box import Box
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerRampState, SchedulerSimState
from palca.learning.value_net import cached_value_net

ROOT = Path(__file__).resolve().parents[1]
BASELINE_REV = "ee1fa81fa0096a3c163b69d169e52dede35bbc3c"


@pytest.fixture(scope='module')
def checkpoint(tmp_path_factory):
    directory = tmp_path_factory.mktemp('g1_search')
    data = directory / 'data'
    subprocess.run([sys.executable, str(ROOT / 'scripts/gen_value_data.py'), '--policy', 'greedy',
                    '--prior', 'generic', '--episodes', '2', '--workers', '1', '--out', str(data)],
                   check=True, capture_output=True, timeout=240)
    spec = importlib.util.spec_from_file_location('search_train_g1', ROOT / 'scripts/train_value_g1.py')
    trainer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trainer)
    # Standalone training latency sweeps are outside this integration test.
    trainer.benchmark = lambda *args: {}
    trainer.main(['--data', str(data), '--out', str(directory / 'model'),
                  '--epochs', '1', '--batch', '64', '--device', 'cpu'])
    return directory / 'model/value_g1.pt'


def policy(checkpoint, mode='rollout+learned', **overrides):
    params = dict(lookahead_k=15, overhang_mm=20, stacking_mode="heightfield", time_budget_ms=0, stability_mode='off', micro_plan_enabled=True,
                  micro_plan_node_budget=150, micro_plan_hard_cap_ms=600000, micro_plan_depth=3,
                  micro_plan_value=mode, micro_plan_rollout_leaves=4,
                  rollout_future_boxes=6 if mode == 'rollout+learned' else 0,
                  rollout_prior='generic', learned_value_model='g1',
                  learned_value_model_path=str(checkpoint), learned_value_device='cpu')
    params.update(overrides)
    return PolicyPackerScheduler.from_defaults(**params)


def flow(seed=283, count=140):
    rng = random.Random(seed)
    return [Box(i, rng.choice((410, 470, 530)), rng.choice((270, 310, 370)),
                rng.choice((230, 290, 350)), 0, destination=1) for i in range(count)]


def replay(policies, flows, *, stop=30):
    queues = [list(f[:15]) for f in flows]
    upstreams = [list(f[15:]) for f in flows]
    pallets = [p._new_pallet() for p in policies]
    picks = closures = 0
    while queues[0] == queues[1] and picks < stop:
        plans = []
        for i, p in enumerate(policies):
            state = SchedulerSimState(picks, {1: queues[i]}, {1: pallets[i]}, set(),
                ramp_states={1: SchedulerRampState(tuple(queues[i]), tuple(upstreams[i]), 15)},
                remaining_total=len(queues[i])+len(upstreams[i]))
            plans.append(p._scheduler.choose_action(state))
        assert plans[0] == plans[1], f'decision {picks}'
        if plans[0] is None:
            assert pallets[0].placements
            pallets = [p._new_pallet() for p in policies]
            closures += 1
            assert closures < 20
            continue
        for i, plan in enumerate(plans):
            pallets[i].commit_place(plan.preview)
            queues[i].pop(plan.buffer_index)
            if upstreams[i]:
                queues[i].append(upstreams[i].pop(0))
        picks += 1
    return picks


@pytest.mark.parametrize('mode', ['learned', 'rollout+learned'])
def test_batch_matches_individual(checkpoint, mode, record_property, monkeypatch):
    import palca.learning.value_net as net
    calls = []
    original = net.remaining_value_batch
    def measured(model, encodings, device):
        calls.append(len(encodings))
        return original(model, encodings, device)
    monkeypatch.setattr(net, 'remaining_value_batch', measured)
    policies = [policy(checkpoint, mode) for _ in range(2)]
    policies[1]._scheduler._g1_batching = False
    assert replay(policies, [flow(), flow()]) == 30
    assert max(calls) > 1
    times = policies[0]._scheduler.g1_network_times_ms
    record_property('network_ms_per_decision', sum(times)/len(times))
    print(f'{mode}: network_ms_per_decision={sum(times)/len(times):.3f}')


def test_d022_prefix_100_permuted_suffix(checkpoint):
    original = flow(count=130)
    changed = original[:100] + list(reversed(original[100:]))
    policies = [policy(checkpoint, micro_plan_node_budget=57, micro_plan_depth=1,
                       micro_plan_rollout_leaves=2) for _ in range(2)]
    assert replay(policies, [original, changed], stop=100) == 86


def test_observable_rollout_encodings(checkpoint, monkeypatch):
    import palca.learning.state_encoding as encoding
    seen = []
    original = encoding.encode_state
    visible = flow(count=15)
    def capture(pallet, boxes):
        boxes = list(boxes)
        assert all(any(b is v for v in visible) for b in boxes)
        assert len({b.box_id for b in boxes}) == len(boxes)
        seen.append(len(boxes))
        return original(pallet, boxes)
    monkeypatch.setattr(encoding, 'encode_state', capture)
    p = policy(checkpoint)
    state = SchedulerSimState(0, {1: visible}, {1: p._new_pallet()}, set())
    assert p._scheduler.choose_action(state) is not None
    assert seen and max(seen) < 15  # rollout+learned evaluates only depleted leaf belts


def test_d032_only_checkpoint_and_json(checkpoint, monkeypatch):
    import palca.learning.value_net as net
    # Exercise a cold cache, and then reuse across an entire short replay.
    net._PATH_CACHE.clear()
    net._MODEL_CACHE.clear()
    allowed = {checkpoint.resolve(), checkpoint.with_suffix('.json').resolve()}
    opened = []
    def guard(original):
        def guarded(file, *args, **kwargs):
            if not isinstance(file, int):
                path = Path(file).resolve()
                assert path in allowed, f'D-032: unexpected inference read: {path}'
                opened.append(path)
            return original(file, *args, **kwargs)
        return guarded
    monkeypatch.setattr(builtins, 'open', guard(builtins.open))
    monkeypatch.setattr(io, 'open', guard(io.open))
    policies = [policy(checkpoint, micro_plan_node_budget=57) for _ in range(2)]
    assert policies[0]._scheduler._g1_model is policies[1]._scheduler._g1_model
    assert replay(policies, [flow(), flow()]) == 30
    assert set(opened) == allowed
    assert opened.count(checkpoint.with_suffix('.json').resolve()) == 1


def test_missing_bad_sha_and_changed_checkpoint(checkpoint, tmp_path):
    with pytest.raises(ValueError, match='G1 checkpoint'):
        cached_value_net(tmp_path / 'missing.pt')
    target = tmp_path / 'bad.pt'
    target.write_bytes(checkpoint.read_bytes())
    metadata = json.loads(checkpoint.with_suffix('.json').read_text())
    target.with_suffix('.json').write_text(json.dumps(metadata))
    assert cached_value_net(target) is cached_value_net(target)
    metadata['sha256'] = '0'*64
    target.with_suffix('.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='SHA256'):
        cached_value_net(target)


def test_validation_and_cli():
    from sim.run import build_parser
    with pytest.raises(ValueError, match='model_path'):
        SchedulerConfig(learned_value_model='g1')
    for option in ({'learned_value_model': 'x'}, {'learned_value_device': 'x'}):
        with pytest.raises(ValueError):
            SchedulerConfig(**option)
    args = build_parser().parse_args(['--excel', 'unused.xlsx', '--learned-value-model', 'g1',
        '--learned-value-model-path', '~/value.pt', '--learned-value-device', 'cpu'])
    assert args.learned_value_model == 'g1' and args.learned_value_device == 'cpu'


def test_simulation_dump(checkpoint, tmp_path):
    import pandas as pd
    from sim.run import run_simulation
    excel = tmp_path / 'flow.xlsx'
    pd.DataFrame({'timestamp': [0]*8, 'destino': [1]*8, 'largo': [400]*8,
                  'ancho': [300]*8, 'alto': [200]*8}).to_excel(excel, index=False)
    dump_path = tmp_path / 'dump.json'
    result = run_simulation(excel_path=str(excel), out_path=None, model='M1', n_per_pallet=24,
        t_pick_place=1, staging_cap=0, policy='palca', lookahead_k=15, arrival_mode='immediate',
        stacking_mode='heightfield', overhang_mm=20, stability_mode='off', time_budget_ms=0, micro_plan=True,
        micro_plan_node_budget=57, micro_plan_value='rollout+learned', rollout_future_boxes=6,
        rollout_prior='generic', learned_value_model='g1', learned_value_model_path=str(checkpoint),
        dump_placements_path=str(dump_path))
    dump = json.loads(dump_path.read_text())
    assert dump['params']['learned_value_sha256'] == cached_value_net(checkpoint).metadata['sha256']
    assert dump['params']['learned_value_device'] == 'cpu'
    assert result['metrics']['pallet_kpis']['micro_plan_learned_value_network_ms_mean'] > 0
    assert result['metrics']['pallet_kpis']['micro_plan_learned']['model'] == 'g1'


def normalized(value):
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in value.items()
                if k != 'git_head' and 'time' not in k and not k.endswith('_ms')}
    if isinstance(value, list):
        return [normalized(v) for v in value]
    return value


@pytest.mark.parametrize('seed', [50021, 1])
def test_default_complete_dump_matches_head(seed, tmp_path, monkeypatch):
    """Compare full dumps to pre-change scheduler, with canonical H6 overrides."""
    import palca.integration.policy_packer_sched as integration
    from sim.run import run_simulation
    from scripts.run_sweep import load_configs
    # Pin the pre-G1 scheduler so later commits cannot turn this into a self-comparison.
    source = subprocess.check_output(['git', '--no-pager', 'show',
        f'{BASELINE_REV}:src/palca/scheduler/scheduler_v1.py'], text=True)
    baseline = types.ModuleType('palca.scheduler._g1_reference')
    baseline.__package__ = 'palca.scheduler'
    monkeypatch.setitem(sys.modules, baseline.__name__, baseline)
    exec(compile(source, '<HEAD scheduler_v1>', 'exec'), baseline.__dict__)
    from palca.study.params import preset_params
    config = next(c for c in load_configs(Path('configs/sweeps/g0_margen.yaml'))
                  if c['name'] == 'h6_sin_gate')
    params = preset_params(config['preset'], **config['overrides'])
    params['excel_path'] = str(ROOT / params['excel_path'])
    dumps = []
    for legacy in (False, True):
        if legacy:
            monkeypatch.setattr(integration, 'SchedulerV1', baseline.SchedulerV1)
        dump = tmp_path / 'dump.json'
        run_simulation(**dict(params, out_path=None, episode_seed=seed,
                              dump_placements_path=str(dump)))
        dumps.append(normalized(json.loads(dump.read_text())))
    assert dumps[0] == dumps[1]


def test_sweep_configuration():
    from scripts.run_sweep import load_configs
    configs = load_configs(Path('configs/sweeps/g1_tune.yaml'))
    base = next(c['overrides'] for c in load_configs(Path('configs/sweeps/g0_margen.yaml'))
                if c['name'] == 'h6_sin_gate')
    assert configs[0]['overrides'] == base
    assert len(configs) == 6
    for config in configs[1:]:
        params = config['overrides']
        assert params['learned_value_model'] == 'g1'
        assert params['micro_plan_hard_cap_ms'] == 600000
        assert params['learned_value_device'] == 'cpu'


@pytest.mark.parametrize('mode', ['learned', 'rollout+learned'])
def test_one_network_call_per_layer_or_rollout_set(checkpoint, mode, monkeypatch):
    import palca.learning.value_net as net
    calls = []
    original = net.remaining_value_batch
    def measured(model, encodings, device):
        calls.append(len(encodings))
        return original(model, encodings, device)
    monkeypatch.setattr(net, 'remaining_value_batch', measured)
    p = policy(checkpoint, mode, micro_plan_depth=1, rollout_future_samples=3)
    visible = flow(count=15)
    state = SchedulerSimState(0, {1: visible}, {1: p._new_pallet()}, set())
    assert p._scheduler.choose_action(state) is not None
    if mode == 'learned':
        assert len(calls) == 2 and calls[0] == 1 and calls[1] > 1
    else:
        assert len(calls) == 1 and calls[0] > 1
