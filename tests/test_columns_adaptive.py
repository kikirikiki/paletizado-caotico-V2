"""H10 geometry, frozen physical templates and D-022 information boundary."""
import copy
from dataclasses import replace
from itertools import islice
import random

import pytest

from palca.domain.box import Box
from palca.scheduler.columns import (
    ADAPTIVE_MAX_CANDIDATES, _assignment_score, _axis_partitions, adaptive_partition,
)
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerRampState, SchedulerSimState
from tests.test_columns_online import make_policy, pallet


def boxes(seed=17):
    rng = random.Random(seed)
    return [Box(i, rng.randrange(250, 751), rng.randrange(200, 501),
                rng.randrange(180, 451), 0, destination=1) for i in range(15)]


@pytest.mark.parametrize('slack', [0, 10, 20])
@pytest.mark.parametrize('seed', range(6))
def test_partition_deterministic_and_tiles_entire_envelope(seed, slack):
    p = pallet(make_policy(stand_hw_height_margin_gate_mm=2400))
    result = adaptive_partition(p, boxes(seed), slack)
    assert result == adaptive_partition(p, boxes(seed), slack)
    assert 0 < result['candidates_evaluated'] <= ADAPTIVE_MAX_CANDIDATES
    rects = result['rectangles']
    assert sum(l*w for x, y, l, w in rects) == 1240*840
    for i, (x, y, l, w) in enumerate(rects):
        assert -20 <= x < x+l <= 1220 and -20 <= y < y+w <= 820
        for a, b, c, d in rects[i+1:]:
            assert min(x+l, a+c) <= max(x, a) or min(y+w, b+d) <= max(y, b)
    assert sum(result['strips_mm']) == (840 if result['strip_axis'] == 'y' else 1240)
    assert p.placements == []


def test_residual_threshold_and_integer_slack_distribution():
    assert _axis_partitions(100, [40], 20)[0] == ((40, False), (40, False), (20, True))
    assert _axis_partitions(100, [41], 20)[0] == ((50, False), (50, False))
    assert _axis_partitions(101, [41], 20)[0] == ((51, False), (50, False))
    assert ((30, False), (50, False), (20, True)) in _axis_partitions(100, [30, 50], 20)


def test_greedy_score_one_box_per_column_and_residual_penalty():
    columns = ((0, 0, 40, 50), (40, 0, 40, 50), (80, 0, 20, 50))
    # Two orientations of one box cannot count as two boxes. The final strip
    # cannot fit either footprint, and is the only penalized residual.
    footprints = [(2000, 0, 40, 50), (2000, 0, 50, 40)]
    assert _assignment_score(columns, (1, 2), footprints) == (1000, 2000, 1000)
    assert _assignment_score(columns, (1, 2), footprints + [(2000, 1, 40, 50)]) == (3000, 4000, 1000)


def test_reads_exactly_fifteen_visible_boxes_and_uses_only_allowed_orientations(monkeypatch):
    p = pallet(make_policy(stand_hw_height_margin_gate_mm=2400))
    visible = boxes()
    expected = adaptive_partition(p, visible)
    def guarded_stream():
        yield from visible
        raise AssertionError('attempted to see a sixteenth box')
    assert adaptive_partition(p, guarded_stream()) == expected
    assert adaptive_partition(p, visible + boxes(99)) == expected
    calls = []
    original = p._orientations
    def orientations(l, w, h):
        calls.append((l, w, h))
        return original(l, w, h)
    monkeypatch.setattr(p, '_orientations', orientations)
    assert adaptive_partition(p, visible) == expected
    assert calls == [(b.length_mm, b.width_mm, b.height_mm) for b in visible]
    assert expected != adaptive_partition(p, boxes(99))


def test_no_hidden_family_constants(monkeypatch):
    import palca.scheduler.columns as module
    p = pallet(make_policy())
    def forbidden(*args, **kwargs):
        raise AssertionError('adaptive consulted a fixed family template')
    monkeypatch.setattr(module, 'dominant_width', forbidden)
    monkeypatch.setattr(module, 'column_rectangles', forbidden)
    # Noncanonical dimensions must still yield a complete partition.
    result = adaptive_partition(p, [Box(1, 317, 263, 197, 0, destination=1)])
    assert result['visible_count'] == 1
    assert sum(l*w for _, _, l, w in result['rectangles']) == 1240*840


@pytest.mark.parametrize('visible', [[], [Box(1, 5000, 5000, 5000, 0, destination=1)],
                                   [Box(1, 1, 1, 1, 0, destination=1)]])
def test_degenerate_inputs_still_cover_envelope(visible):
    result = adaptive_partition(pallet(make_policy()), visible)
    assert sum(l*w for _, _, l, w in result['rectangles']) == 1240*840
    assert result['candidates_evaluated'] <= ADAPTIVE_MAX_CANDIDATES


@pytest.mark.parametrize('micro', [False, True])
def test_partition_pinned_for_physical_pallet_beam_and_future_rollouts(micro, monkeypatch):
    policy = make_policy(column_template='adaptive', column_min_fill=.75,
        stand_hw_height_margin_gate_mm=2400, micro_plan_enabled=micro,
        micro_plan_node_budget=57, micro_plan_hard_cap_ms=600000,
        micro_plan_value='rollout', micro_plan_rollout_leaves=2,
        rollout_future_boxes=6, rollout_future_samples=1, rollout_prior='generic')
    engine, p = policy._scheduler, pallet(policy)
    visible = boxes()
    state = SchedulerSimState(0, {1: visible}, {1: p}, set())
    first = engine.choose_action(state)
    assert first is not None and p.placements == []
    rects = p._column_rectangles
    assert copy.deepcopy(p)._column_rectangles == rects
    assert engine.choose_action(state) == first
    if micro:
        assert engine.last_micro_plan_stats['rollout_future_nodes'] > 0
    def forbidden(*args):
        raise AssertionError('recomputed a physical template with later/synthetic arrivals')
    monkeypatch.setattr('palca.scheduler.scheduler_v1.adaptive_partition', forbidden)
    engine.choose_action(replace(state, ramps={1: boxes(99)}))
    assert p._column_rectangles == rects
    assert len(engine.collect_column_metrics()['open_partitions']) == 1
    assert engine.hard_cap_hits == 0


def test_defaults_cli_yaml_and_slack_validation():
    from inspect import signature
    from pathlib import Path
    from sim.run import build_parser, run_simulation
    from scripts.run_sweep import load_configs
    assert SchedulerConfig().column_template == 'off'
    assert signature(run_simulation).parameters['column_slack_mm'].default == 10
    args = build_parser().parse_args(['--excel', 'unused.xlsx', '--column-template', 'adaptive',
                                    '--column-slack-mm', '20'])
    assert args.column_template == 'adaptive' and args.column_slack_mm == 20
    policy = make_policy(column_template='adaptive', column_slack_mm=20)
    assert policy._scheduler.config.column_slack_mm == 20
    for slack in (-1, 1.5, True, float('nan')):
        with pytest.raises(ValueError):
            SchedulerConfig(column_slack_mm=slack)
    configs = load_configs(Path('configs/sweeps/h10_tune.yaml'))
    assert configs[0]['name'] == 'h6_sin_gate' and configs[0]['role'] == 'official'
    assert len(configs) == 7
    for config in configs:
        assert config['overrides']['micro_plan_hard_cap_ms'] == 600000
        assert config['overrides']['stand_hw_height_margin_gate_mm'] == 2400
    assert {(c['overrides']['column_slack_mm'], c['overrides']['column_min_fill'])
            for c in configs[1:]} == {(s, f) for s in (0, 10, 20) for f in (.75, .85)}


@pytest.mark.parametrize('value', ['score', 'potential', 'rollout'])
def test_adaptive_beam_never_even_reads_upstream_in_any_value_mode(value):
    class ForbiddenUpstream:
        def __iter__(self):
            raise AssertionError('H10 read the unknown real upstream')
    policy = make_policy(column_template='adaptive', column_min_fill=.75,
        stand_hw_height_margin_gate_mm=2400, micro_plan_enabled=True,
        micro_plan_node_budget=25, micro_plan_hard_cap_ms=600000,
        micro_plan_value=value, micro_plan_rollout_leaves=2)
    visible = boxes()[:4]
    state = SchedulerSimState(0, {1: visible}, {1: pallet(policy)}, set(),
        ramp_states={1: SchedulerRampState(tuple(visible), ForbiddenUpstream(), 15)})
    assert policy._scheduler.choose_action(state) is not None


def test_d022_suffix_permutation_cannot_change_any_decision_before_visibility():
    """Identical rows 1..100, permuted later rows; first change enters at pick 86.

    Give the scheduler the real upstream snapshots too: H6 must freeze visible
    queues and use only its generic synthetic future, including new pallets.
    """
    rng = random.Random(283)
    flow = [Box(i, rng.choice((410, 470, 530)), rng.choice((270, 310, 370)),
                rng.choice((230, 290, 350)), 0, destination=1) for i in range(130)]
    permuted = flow[:100] + list(reversed(flow[100:]))
    policies = [make_policy(column_template='adaptive', column_min_fill=.75,
        stand_hw_height_margin_gate_mm=2400, micro_plan_enabled=True,
        micro_plan_node_budget=57, micro_plan_hard_cap_ms=600000,
        micro_plan_value='rollout', micro_plan_rollout_leaves=2,
        rollout_future_boxes=6, rollout_future_samples=1, rollout_prior='generic') for _ in range(2)]
    streams = [iter(flow), iter(permuted)]
    queues = [list(islice(stream, 15)) for stream in streams]
    upstreams = [flow[15:], permuted[15:]]
    pallets = [pallet(policy) for policy in policies]
    decisions = 0
    physical_pallets = 1
    future_nodes = 0
    while queues[0] == queues[1]:
        plans = []
        for i, policy in enumerate(policies):
            state = SchedulerSimState(decisions, {1: queues[i]}, {1: pallets[i]}, set(),
                ramp_states={1: SchedulerRampState(tuple(queues[i]), tuple(upstreams[i]), 15)},
                remaining_total=len(queues[i]) + len(upstreams[i]))
            plan = policy._scheduler.choose_action(state)
            future_nodes += policy._scheduler.last_micro_plan_stats.get('rollout_future_nodes', 0)
            plans.append(plan)
        assert plans[0] == plans[1]
        assert pallets[0]._column_rectangles == pallets[1]._column_rectangles
        if plans[0] is None:
            assert pallets[0].placements  # no endless replacement of an empty pallet
            pallets = [pallet(policy) for policy in policies]
            physical_pallets += 1
            continue
        for i, plan in enumerate(plans):
            pallets[i].commit_place(plan.preview)
            queues[i].pop(plan.buffer_index)
            queues[i].append(upstreams[i].pop(0))
        decisions += 1
    assert decisions == 86
    assert physical_pallets >= 2 and future_nodes > 0
    assert queues[0][-1].box_id == 100 and queues[1][-1].box_id == 129


@pytest.mark.slow
@pytest.mark.parametrize('seed', [50021, 1])
def test_disabled_defaults_reproduce_official_h6_pallets(seed, tmp_path):
    import hashlib
    import json
    from pathlib import Path
    from palca.study.params import preset_params
    from scripts.run_sweep import load_configs, sweep_lock
    from sim.run import run_simulation
    fixture = json.loads((Path(__file__).parent / 'fixtures/h10_official.json').read_text())
    config = load_configs(Path('configs/sweeps/h10_tune.yaml'))[0]
    dump_path = tmp_path / 'dump.json'
    params = preset_params(config['preset'], **config['overrides'], episode_seed=seed,
                           multi_ramp_seed=seed, dump_placements_path=str(dump_path))
    # Do not supply column settings: this verifies the real disabled defaults.
    assert not any(key.startswith('column_') for key in params)
    with sweep_lock():
        payload = run_simulation(**params)
    dump = json.loads(dump_path.read_text())
    fingerprint = hashlib.sha256(json.dumps(dump['pallets'], sort_keys=True).encode()).hexdigest()
    assert fingerprint == fixture['seeds'][str(seed)]['pallets_sha256']
    assert payload['metrics']['pallet_kpis']['hard_cap_hits'] == 0
