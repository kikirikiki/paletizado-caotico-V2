"""D-025: opt-in tall-box access, H1 exemption and H8 dead headroom."""
from copy import deepcopy
from dataclasses import asdict, replace
from inspect import signature
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.packer.controls import AccessibilityConfig, ControlConfig, RobotAccessibilityControl
from palca.packer.pallet_model import PalletModel
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1, _BeamNode, _BeamRampState, _BeamAction
from palca.scoring.access_debt import AccessDebt, access_debt, build_heightfield
from palca.scoring.dead_headroom import dead_headroom_volume
from palca.scoring.state_value import visible_completion_potential
from palca.study.accessibility_robustness import accessibility_gap_violations
from palca.study.validate import validate_accessibility, validate_pallet
from scripts.run_sweep import load_configs, make_task, run_task
from sim.run import build_parser, run_simulation


def placement(x=0, z=0, height=600, box_id=1):
    return Placement(x, 0, z, False, 0, 200, 200, height, box_id=box_id, orientation_name="LWH", orientation_family="planar")


@pytest.mark.parametrize('exempt,height,clearance,wall,expected', [
    (False, 300, 0, 600, False), (False, 750, 0, 600, False),
    (True, 300, 0, 600, False), (True, 750, 0, 600, True),
    (True, 600, 0, 600, True), (True, 599, 0, 600, False),
    (True, 500, 100, 600, True), (True, 499, 100, 600, False),
    (True, 300, 0, 400, True), (False, 750, 100, 600, False),
])
@pytest.mark.parametrize('base', [0, 100])
def test_control_and_independent_replay(exempt, height, clearance, wall, expected, base):
    # Support and wall make the same 600 mm well at either local base height.
    previous = ([placement(z=0, height=base)] if base else [])
    previous += [placement(x=200, height=base + wall, box_id=2)]
    candidate = placement(z=base, height=height, box_id=3)
    config = AccessibilityConfig(400, exempt, clearance)
    result = RobotAccessibilityControl(config).evaluate(
        pallet=SimpleNamespace(placements=previous), box=Box(3, 750, 200, 300, timestamp=0), placement=candidate)
    assert result.feasible is expected  # Uses oriented height, never Box.height_mm.
    params = asdict(config)
    items = [asdict(p) for p in [*previous, candidate]]
    assert (len(validate_accessibility(items, params)) == 0) is expected
    assert accessibility_gap_violations(items, 400, 0, exempt, clearance) == int(not expected)
    originals = {i['box_id']: (i['length_mm'], i['width_mm'], i['height_mm']) for i in items}
    assert ('ROBOT_ACCESS' not in {v.code for v in validate_pallet(items, originals, run_params=params)}) is expected


def test_zero_delta_stays_disabled_and_replay_is_chronological():
    wall, candidate = placement(x=200), placement(height=300)
    items = [asdict(p) for p in [wall, candidate]]
    assert not validate_accessibility(items, dict(accessibility_delta_mm=0, accessibility_tall_box_exemption=True))
    assert len(validate_accessibility(items, dict(accessibility_delta_mm=400))) == 1
    assert not validate_accessibility(list(reversed(items)), dict(accessibility_delta_mm=400))
    cfg = AccessibilityConfig(0, True, 100)
    assert RobotAccessibilityControl(cfg).evaluate(pallet=SimpleNamespace(placements=[wall]), box=None, placement=candidate).feasible


def test_cache_separates_oriented_heights_and_config():
    from palca.packer.maxrects2d import MaxRects2D
    class UncachedProjection(MaxRects2D):
        pass
    cfg = ControlConfig(accessibility=AccessibilityConfig(400, True, 0))
    cached = PalletModel(PalletSpec(length_mm=400, width_mm=200, max_height_mm=850),
                         stacking_mode='heightfield', control_config=cfg)
    base = Box('wall', 200, 200, 600, timestamp=0)
    cached.commit_place(cached.preview_place(base))
    uncached = deepcopy(cached)
    uncached.layers[0].bin.__class__ = UncachedProjection
    for h, feasible in [(300, False), (750, True), (599, False), (600, True)]:
        box = Box(h, 200, 200, h, timestamp=0)
        preview = cached.preview_place(box)
        assert preview == uncached.preview_place(box)
        assert preview.feasible is feasible
    for pallet in (cached, uncached):
        controls = tuple(RobotAccessibilityControl(AccessibilityConfig(400, True, 100))
                         if type(c) is RobotAccessibilityControl else c for c in pallet.controls.placement_controls)
        pallet.controls = replace(pallet.controls, placement_controls=controls)
    assert cached.preview_place(Box('clearance', 200, 200, 500, timestamp=0)) == uncached.preview_place(Box('clearance', 200, 200, 500, timestamp=0))
    assert cached.preview_place(Box('clearance', 200, 200, 500, timestamp=0)).feasible


@pytest.mark.parametrize('exempt,exempt_delta,expected_volume', [
    (False, 750, 200 * 200 * 200), (True, 750, 0),
    (True, 600, 0), (True, 500, 200 * 200 * 100), (True, 100, 200 * 200 * 200),
])
def test_h1_effective_delta_in_function_and_scheduler(exempt, exempt_delta, expected_volume):
    pallet = PalletModel(PalletSpec(length_mm=400, width_mm=200),
                         control_config=ControlConfig(accessibility=AccessibilityConfig(400, exempt)))
    field = build_heightfield([], 400, 200)
    p = placement()
    debt = access_debt(field, (0, 0, 200, 200), 600, 400, 200,
                       accessibility_tall_box_exemption=exempt, access_debt_exempt_delta_mm=exempt_delta)
    assert debt.dead_volume_mm3 == expected_volume
    scheduler = SchedulerV1(SchedulerConfig(access_debt_weight=.25, access_debt_band_mm=200,
                                           access_debt_exempt_delta_mm=exempt_delta))
    terms = scheduler._score_candidate(now=0, box=Box(1, 200, 200, 600, timestamp=0), idx=0,
        preview=PlacementPreview(True, p, 1., 0.), max_priority=0, height_after_mm=600, pallet=pallet)
    assert terms.access_dead_volume_mm3 == expected_volume
    assert terms.scalar_score == pytest.approx(1 - .25 * expected_volume / (200 * 200 * 600))
    assert access_debt(field, (0, 0, 200, 200), 600, 0, 200,
                       accessibility_tall_box_exemption=True) == AccessDebt()


@pytest.mark.parametrize('headroom', [-1, 0, 1, 249, 250, 251, 349, 350])
@pytest.mark.parametrize('minimum', [250, 350])
def test_h8_open_interval_and_candidate_volume_normalization(headroom, minimum):
    p = placement(z=2400 - 750 - headroom, height=750)
    expected = 200 * 200 * headroom if 0 < headroom < minimum else 0
    assert dead_headroom_volume(p, 2400, minimum) == expected
    pallet = PalletModel()
    scheduler = SchedulerV1(SchedulerConfig(dead_headroom_weight=.25, dead_headroom_min_mm=minimum))
    terms = scheduler._score_candidate(now=0, box=Box(1, 200, 200, 750, timestamp=0), idx=0,
        preview=PlacementPreview(True, p, 1., 0.), max_priority=0, height_after_mm=2400-headroom, pallet=pallet)
    assert terms.dead_headroom_penalty == pytest.approx(.25 * expected / (200 * 200 * 750))
    assert terms.scalar_score == pytest.approx(1 - terms.dead_headroom_penalty)
    disabled = SchedulerV1(SchedulerConfig())._score_candidate(now=0, box=Box(1, 200, 200, 750, timestamp=0), idx=0,
        preview=PlacementPreview(True, p, 1., 0.), max_priority=0, height_after_mm=2400-headroom, pallet=pallet)
    assert disabled.scalar_score == 1.


def test_potential_uses_oriented_height_and_exemption():
    pallet = PalletModel(PalletSpec(length_mm=400, width_mm=200, max_height_mm=850),
        control_config=ControlConfig(accessibility=AccessibilityConfig(400, True)))
    pallet.placements.append(placement(x=200))
    assert visible_completion_potential(pallet, [Box(1, 200, 200, 750, timestamp=0)]) == 200*200*750
    assert visible_completion_potential(pallet, [Box(1, 200, 200, 300, timestamp=0)]) == 0
    pallet.accessibility_tall_box_exemption = False
    assert visible_completion_potential(pallet, [Box(1, 200, 200, 750, timestamp=0)]) == 0


def test_h8_applies_to_beam_and_rollout():
    # Only one feasible candidate; score should include H8 in beam and rollout.
    pallet = PalletModel(PalletSpec(length_mm=200, width_mm=200, max_height_mm=900), stacking_mode='heightfield')
    box = Box(1, 200, 200, 750, destination=1, timestamp=0)
    scheduler = SchedulerV1(SchedulerConfig(dead_headroom_weight=.5, time_budget_ms=0))
    state = SchedulerSimState(0, {1: [box]}, {1: pallet}, set())
    expected_score = 1 - .5 * 150 / 750
    plan = scheduler._choose_action_greedy(state, deadline=None, lookahead_k=1)
    assert plan.score == pytest.approx(expected_score)
    node = _BeamNode({1: _BeamRampState([box], [], 1)}, {1: pallet})
    action = _BeamAction(ramp_id=1, buffer_index=0, max_priority=0)
    expansion = scheduler._expand_beam_node(node=node, action=action, sim_state=state)
    assert expansion.terms.dead_headroom_penalty == pytest.approx(.1)
    # _rollout_value creates a fresh greedy scheduler with the same config.
    calls = []
    original = SchedulerV1._score_candidate
    def record(self, **kwargs):
        terms = original(self, **kwargs)
        calls.append(terms.dead_headroom_penalty)
        return terms
    from unittest.mock import patch
    with patch.object(SchedulerV1, '_score_candidate', record):
        scheduler._rollout_value(node, state, quota=1, deadline=None)
    assert calls and calls[0] == pytest.approx(.1)
    micro = SchedulerV1(SchedulerConfig(dead_headroom_weight=.5, micro_plan_enabled=True,
                                        micro_plan_node_budget=4, micro_plan_value='rollout'))
    assert micro.choose_action(state) is not None
    metrics = micro.collect_dead_headroom_metrics()
    assert metrics['selected_count'] == 1
    assert metrics['selected_penalty_mean'] == pytest.approx(.1)


def test_defaults_propagation_cli_and_run_json(tmp_path):
    defaults = dict(accessibility_tall_box_exemption=False, accessibility_top_clearance_mm=0,
                    access_debt_exempt_delta_mm=750, dead_headroom_weight=0., dead_headroom_min_mm=250)
    assert {k:signature(run_simulation).parameters[k].default for k in defaults} == defaults
    overrides = dict(accessibility_tall_box_exemption=True, accessibility_top_clearance_mm=100,
                     access_debt_exempt_delta_mm=500, dead_headroom_weight=.25, dead_headroom_min_mm=350)
    policy = PolicyPackerScheduler.from_defaults(accessibility_delta_mm=400, **overrides)
    pallet = policy._new_pallet()
    assert pallet.accessibility_tall_box_exemption and pallet.accessibility_top_clearance_mm == 100
    assert policy._scheduler.config.access_debt_exempt_delta_mm == 500
    args = build_parser().parse_args(['--excel','data/Flujo_smoke_60.xlsx','--accessibility-tall-box-exemption',
        '--accessibility-top-clearance-mm','100','--access-debt-exempt-delta-mm','500',
        '--dead-headroom-weight','.25','--dead-headroom-min-mm','350'])
    assert {k:getattr(args,k) for k in overrides} == overrides
    path = tmp_path/'run.json'
    payload = run_simulation(excel_path='data/Flujo_smoke_60.xlsx', model='M1', n_per_pallet=999999,
        t_pick_place=14., staging_cap=0, out_path=str(path), policy='palca', lookahead_k=1,
        max_pallets=1, continuous_pallets=True, force_destination=1, **overrides)
    assert {k:payload['params'][k] for k in overrides} == overrides
    assert json.loads(path.read_text())['metrics']['pallet_kpis']['dead_headroom']['enabled']


@pytest.mark.parametrize('kwargs', [dict(dead_headroom_weight=-1), dict(dead_headroom_weight=float('nan')),
    dict(dead_headroom_weight=float('inf')), dict(dead_headroom_min_mm=-1), dict(access_debt_exempt_delta_mm=-1)])
def test_invalid_score_config(kwargs):
    with pytest.raises(ValueError):
        SchedulerConfig(**kwargs)


def test_sweep_grids_preserve_official_baseline():
    baseline = next(c for c in load_configs(Path('configs/sweeps/h6_sin_gate.yaml')) if c['name']=='h6_sin_gate')
    lever = load_configs(Path('configs/sweeps/lever_tall_box.yaml'))
    h8 = load_configs(Path('configs/sweeps/h8_tune.yaml'))
    assert lever[0] == h8[0] == baseline
    assert [c['name'] for c in lever[1:]] == ['tall_c0','tall_c100','tall_c0_d500','tall_c0_d400']
    assert all(c['role']=='lever' for c in lever[1:])
    assert [c['overrides']['dead_headroom_weight'] for c in h8[1:]] == [.1,.25,.5,1.,.25]
    assert all(c['role']=='official' for c in h8)


def test_worker_reads_run_rule_and_counts_base_violations(tmp_path, monkeypatch):
    import scripts.run_sweep as sweep
    config = next(c for c in load_configs(Path('configs/sweeps/lever_tall_box.yaml')) if c['name']=='tall_c0')
    task = make_task(config, 1, tmp_path, 'head', 'source')
    items = [asdict(placement(x=200, box_id=1)), asdict(placement(height=750, box_id=2))]
    run_params = dict(task['params'])
    def simulate(**params):
        Path(params['dump_placements_path']).write_text('{}')
        return dict(params=run_params, metrics=dict(decision_timing={}, pallet_kpis=dict(deadline_cutoffs_count=0)))
    monkeypatch.setattr(sweep, 'run_simulation', simulate)
    monkeypatch.setattr(sweep, 'split_pallets', lambda *a:[SimpleNamespace(dest=1,index=0,items=items)])
    monkeypatch.setattr(sweep, 'original_dims_for_run', lambda *a:{1:(200,200,600),2:(200,200,750)})
    monkeypatch.setattr(sweep, 'run_kpi', lambda *a:dict(boxes_placed=2))
    result = run_task(task)
    assert result['n_violations'] == result['acc_violations'] == 0
    assert result['acc_violations_base_rule'] == 1
    assert all(result[f'acc_violations_gap{g}']==0 for g in (5,20,50))
    # The actual run is authoritative, even if the task metadata disagrees.
    run_params['accessibility_tall_box_exemption'] = False
    result = run_task(task)
    assert result['n_violations'] == result['acc_violations'] == result['acc_violations_base_rule'] == 1


def test_summary_reports_both_accessibility_rules(tmp_path):
    from scripts.run_sweep import generate_summary, write_json
    write_json(tmp_path/'manifest.json', dict(configs=[dict(name='tall',role='lever')],
        seeds=[1], task_hashes=dict(tall={'1':'hash'}), command='test',git_head='head',
        source_hash='hash',date='today',seed_rule='test',baseline='tall'))
    write_json(tmp_path/'tall/1/kpi.json', dict(task_hash='hash',seed=1,kpi=.7,kpi_nominal=.7,
        n_pallets_total=1,n_closed=1,n_unplaced=0,n_violations=0,wall_time_s=1,
        deadline_cutoffs_count=0,decision_timing=dict(mean_ms=1,p95_ms=1),violations=[],
        acc_violations=0,acc_violations_base_rule=3))
    summary=generate_summary(tmp_path)
    assert summary['configs']['tall']['acc_violations']['mean'] == 0
    assert summary['configs']['tall']['acc_violations_base_rule']['mean'] == 3
    assert '| tall | 0.000 | 3.000 |' in (tmp_path/'summary.md').read_text()
