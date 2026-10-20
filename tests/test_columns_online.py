"""H4 templates, production feasibility, online decisions and disabled regression."""
from dataclasses import asdict
import hashlib
import json
from types import SimpleNamespace

import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.domain.placement import Placement
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.scheduler.columns import column_rectangles, dominant_width
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1
from scripts.run_sweep import accessibility_gap_violations


def make_policy(**kwargs):
    return PolicyPackerScheduler.from_defaults(
        overhang_mm=20, stacking_mode='heightfield', min_support_ratio=.85,
        stability_mode='ratio+corners+settle', orientation_mode='planar+stand_hw',
        accessibility_delta_mm=400, lookahead_k=15, time_budget_ms=0,
        access_debt_weight=.25, access_debt_band_mm=600, **kwargs)


def pallet(policy):
    # Same production factory used by the integration.
    return policy._new_pallet()


@pytest.mark.parametrize('width', [445, 450, 455, 440])
def test_templates_tile_envelope_without_any_artificial_gap(width):
    spec = PalletSpec(overhang_mm=20)
    columns = column_rectangles(spec, width)
    assert sum(l*w for x,y,l,w in columns) == 1240*840
    for x,y,l,w in columns:
        assert -20 <= x < x+l <= 1220 and -20 <= y < y+w <= 820
    for i,(x,y,l,w) in enumerate(columns):
        for a,b,c,d in columns[i+1:]:
            assert min(x+l,a+c) <= max(x,a) or min(y+w,b+d) <= max(y,b)
    a1,a2,b1,b2,b3 = columns
    assert a1[0]+a1[2] == a2[0]
    assert b1[0]+b1[2] == b2[0] and b2[0]+b2[2] == b3[0]
    assert all(a1[1]+a1[3] == b[1] for b in (b1,b2,b3))


@pytest.mark.parametrize('micro', [False, True])
def test_dominant_boxes_fill_both_a_columns_and_balance(micro):
    policy = make_policy(column_template='w445', micro_plan_enabled=micro, micro_plan_node_budget=100)
    p = pallet(policy)
    boxes = [Box(i,605,445,355,timestamp=0,destination=1) for i in range(8)]
    seen=[]
    while boxes:
        plan = policy._scheduler.choose_action(SchedulerSimState(0,{1:boxes},{1:p},set()))
        assert plan is not None
        seen.append(plan.preview.placement)
        p.commit_place(plan.preview)
        boxes.pop(plan.buffer_index)
    assert {(q.x_mm,q.y_mm) for q in seen[:2]} == {(-20,-20),(600,-20)}
    assert sorted(q.z_mm for q in seen) == [0,0,355,355,710,710,1065,1065]
    assert all(q.orientation_family == 'planar' for q in seen)
    if micro:
        assert policy._scheduler.last_micro_plan_stats['nodes_expanded'] <= 100
        assert policy._scheduler.hard_cap_hits == 0


def test_placements_of_exact_width_touch_and_accessibility_blocks_tall_neighbor():
    policy = make_policy(column_template='w445')
    p = pallet(policy)
    columns = column_rectangles(p.spec,445)
    box=Box(1,620,445,355,timestamp=0,destination=1)
    first=p.preview_column_candidates(box,columns,.85)[0]
    p.commit_place(first)
    second=next(v for v in p.preview_column_candidates(box,columns,.85) if v.placement.x_mm==600)
    assert first.placement.x_mm+first.placement.length_mm == second.placement.x_mm
    # Replace history with a valid 710 mm tower to test the production adjacency constraint.
    from dataclasses import replace
    p.placements[:] = [first.placement, replace(first.placement,z_mm=355)]
    candidates=p.preview_column_candidates(box,columns,.85)
    assert candidates
    assert all(v.placement.x_mm != 600 for v in candidates)


def test_gate_400_blocks_b_standing_at_floor_but_open_gate_allows_it():
    box=Box(1,605,445,355,timestamp=0,destination=1)
    for gate,expected in [(400,False),(2400,True)]:
        p=pallet(make_policy(stand_hw_height_margin_gate_mm=gate))
        previews=p.preview_column_candidates(box,column_rectangles(p.spec,445),.85)
        assert any(v.placement.orientation_family=='stand_hw' for v in previews) == expected
        assert all(v.placement.y_mm in (-20,425) for v in previews)


def test_auto_width_is_pinned_from_visible_boxes_only():
    policy=make_policy(column_template='auto')
    p=pallet(policy)
    boxes=[Box(i,605,450,355,timestamp=0,destination=1) for i in range(16)]
    policy._scheduler.choose_action(SchedulerSimState(0,{1:boxes},{1:p},set()))
    assert p._column_width_mm==450
    boxes=[Box(i,605,455,355,timestamp=0,destination=1) for i in range(20)]
    policy._scheduler.choose_action(SchedulerSimState(0,{1:boxes},{1:p},set()))
    assert p._column_width_mm==450
    assert dominant_width(boxes)==455


@pytest.mark.parametrize('fallback', ['free','none'])
@pytest.mark.parametrize('micro', [False, True])
def test_fallback_only_when_no_column_candidate(fallback,micro):
    policy=make_policy(column_template='w445', column_fallback=fallback,
                       micro_plan_enabled=micro,micro_plan_node_budget=100)
    p=pallet(policy)
    small=Box(1,100,100,100,timestamp=0,destination=1)
    result=policy._scheduler.choose_action(SchedulerSimState(0,{1:[small]},{1:p},set()))
    assert (result is not None)==(fallback=='free')


def test_fill_threshold_and_propagation():
    from inspect import signature
    from sim.run import run_simulation
    defaults=signature(run_simulation).parameters
    assert defaults['column_template'].default=='off'
    assert defaults['column_min_fill'].default==.85
    assert defaults['column_fallback'].default=='free'
    policy=make_policy(column_template='auto',column_min_fill=.75,column_fallback='none')
    assert policy._scheduler.config.column_min_fill==.75
    p=pallet(policy)
    box=Box(1,605,445,355,timestamp=0,destination=1)
    assert p.preview_column_candidates(box,column_rectangles(p.spec,445),.85)
    assert not p.preview_column_candidates(box,column_rectangles(p.spec,445),1.0)
    for kw in [{'column_template':'bad'},{'column_fallback':'bad'}, {'column_min_fill':0},
               {'column_min_fill':1.01},{'column_min_fill':float('nan')}]:
        with pytest.raises(ValueError):
            SchedulerConfig(**kw)


def item(x=0,y=0,z=0,l=100,w=100,h=600):
    return dict(x_mm=x,y_mm=y,z_mm=z,length_mm=l,width_mm=w,height_mm=h)


def test_robustness_gap_threshold_order_local_height_and_one_count_per_placement():
    tower=item()
    target=item(x=105,h=100,z=999)  # local_z is footprint support, never candidate z.
    assert accessibility_gap_violations([tower,target],400,5)==1
    assert accessibility_gap_violations([tower,target],400,4)==0
    assert accessibility_gap_violations([target,tower],400,5)==1
    assert accessibility_gap_violations([tower,target],0,50)==0
    assert accessibility_gap_violations([item(h=400),target],400,50)==0
    assert accessibility_gap_violations([tower,item(x=200),item(x=100)],400,0)==1
    # True overlap provides local support and removes the apparent blocker.
    assert accessibility_gap_violations([tower,item(x=99,z=600)],400,50)==0


def test_robustness_zero_gap_matches_production_replay():
    from palca.domain.placement import Placement
    from palca.packer.controls import RobotAccessibilityControl, AccessibilityConfig
    ctrl=RobotAccessibilityControl(AccessibilityConfig(400))
    placed=[]
    items=[item(),item(x=100),item(x=200),item(x=100,z=600),item(x=301,y=150)]
    rejected=0
    for q in items:
        placement=Placement(**q,rot90=False,layer_id=0)
        rejected+=not ctrl.evaluate(pallet=SimpleNamespace(placements=placed),box=None,placement=placement).feasible
        placed.append(placement)
    assert accessibility_gap_violations(items,400,0)==rejected


@pytest.mark.parametrize('seed,kpi,fingerprint',[
    (50021,.6529122663850486,'96cc9f679dff88f5c798aa1ca10c380687d350fe3cf83cdfe0d956558628b6f4'),
    (1,.6271623183883769,'9bb4de60f4fe0e154c61dddbf815295d667ccf8de994154ff24c3e6e1e1bb677')])
@pytest.mark.slow
def test_off_reproduces_frozen_h1_exact_dump_and_kpi(tmp_path,seed,kpi,fingerprint):
    """Frozen H1 artifacts from h3_eval, generated before H4; 186 canonical boxes."""
    from palca.study.params import preset_params
    from palca.study.metric import run_kpi,split_pallets
    from sim.run import run_simulation
    from scripts.run_sweep import sweep_lock
    path=tmp_path/'dump.json'
    params=preset_params('arm6_4orient', access_debt_weight=.25,access_debt_band_mm=600,
                         column_template='off',episode_seed=seed,multi_ramp_seed=seed,
                         dump_placements_path=str(path))
    with sweep_lock(retry_seconds=60):
        result=run_simulation(**params)
    dump=json.loads(path.read_text())
    assert hashlib.sha256(json.dumps(dump['pallets'],sort_keys=True).encode()).hexdigest()==fingerprint
    assert run_kpi(split_pallets(result,dump))['kpi']==kpi


def test_beam_expands_distinct_column_actions_for_same_box(monkeypatch):
    policy=make_policy(column_template='w445',micro_plan_enabled=True,micro_plan_node_budget=100)
    engine=policy._scheduler
    p=pallet(policy)
    box=Box(1,605,445,355,timestamp=0,destination=1)
    actions=[]
    original=engine._expand_beam_node
    def expand(**kwargs):
        action=kwargs['action']
        if action.preview is not None:
            actions.append(action.preview.placement.x_mm)
        return original(**kwargs)
    monkeypatch.setattr(engine,'_expand_beam_node',expand)
    assert engine.choose_action(SchedulerSimState(0,{1:[box]},{1:p},set())) is not None
    assert set(actions)=={-20,600}
    assert engine.column_selected==1 and engine.column_free_selected==0


def test_production_support_rejection_is_preserved():
    policy=make_policy(column_template='w445')
    p=pallet(policy)
    p.placements.append(Placement(-20,-20,0,False,0,500,445,355))
    box=Box(1,605,445,355,timestamp=0,destination=1)
    candidates=p.preview_column_candidates(box,column_rectangles(p.spec,445),.85)
    assert all(v.placement.x_mm!= -20 for v in candidates)


def test_robustness_gaps_are_rectangular_inclusive_and_history_resets():
    # Both gaps <= G includes diagonal neighbours (same definition as production).
    boxes=[item(),item(x=105,y=105)]
    assert accessibility_gap_violations(boxes,400,5)==1
    assert accessibility_gap_violations(boxes,400,4)==0
    assert accessibility_gap_violations(boxes[1:],400,50)==0


@pytest.mark.parametrize('dims,expected', [
    ([(620,445,355),(605,445,345)],2),
    ([(605,445,355),(620,445,355)],2),
])
def test_greedy_prioritizes_column_roof_then_fill(dims,expected):
    policy=make_policy(column_template='w445')
    p=pallet(policy)
    boxes=[Box(i+1,*d,timestamp=0,destination=1) for i,d in enumerate(dims)]
    chosen=policy._scheduler.choose_action(SchedulerSimState(0,{1:boxes},{1:p},set()))
    assert chosen.box_id==expected


def test_beam_free_fallback_preserves_h3_height_slack_filter(monkeypatch):
    from palca.scheduler import scheduler_v1 as module
    policy=make_policy(column_template='w445',micro_plan_enabled=True,micro_plan_node_budget=100,
                       score_mode='min_height_slack_then_gain',height_slack_mm=120)
    calls=[]
    original=module.rank_for_expansion_with_height_slack
    def ranked(**kwargs):
        calls.append(kwargs['score_mode'])
        return original(**kwargs)
    monkeypatch.setattr(module,'rank_for_expansion_with_height_slack',ranked)
    p=pallet(policy)
    small=Box(1,100,100,100,timestamp=0,destination=1)
    chosen=policy._scheduler.choose_action(SchedulerSimState(0,{1:[small]},{1:p},set()))
    assert chosen is not None and not (chosen.preview.debug or {}).get('column')
    assert calls==['min_height_slack_then_gain']


def test_free_only_beam_decisions_match_h3_exactly():
    results=[]
    for template in ('off','w445'):
        policy=make_policy(column_template=template,micro_plan_enabled=True,micro_plan_node_budget=100,
                           score_mode='min_height_slack_then_gain',height_slack_mm=120)
        p=pallet(policy)
        boxes=[Box(i,100,100,h,timestamp=0,destination=1) for i,h in [(1,350),(2,100),(3,200)]]
        picks=[]
        while boxes:
            plan=policy._scheduler.choose_action(SchedulerSimState(0,{1:boxes},{1:p},set()))
            assert plan is not None
            picks.append(plan.box_id)
            p.commit_place(plan.preview)
            boxes.pop(plan.buffer_index)
        assert picks[0] != 1  # 350 mm is outside the initial [100,220] slack.
        results.append((picks,list(p.placements)))
    assert results[0]==results[1]


def test_column_previews_do_not_contaminate_free_geometry_cache():
    import copy

    p = pallet(make_policy(stand_hw_height_margin_gate_mm=2400))
    box = Box(1, 605, 445, 355, timestamp=0, destination=1)
    free_preview = p.preview_place(box)
    cache = p._heightfield_preview_cache
    fields = ('xy', 'heights', 'projections', 'robot')
    before = {key: copy.deepcopy(getattr(cache, key)) for key in fields}
    columns = column_rectangles(p.spec, 450)
    previews = p.preview_column_candidates(box, columns, .85)
    assert previews
    assert p._heightfield_preview_cache is cache
    assert {key: getattr(cache, key) for key in fields} == before
    assert p.preview_place(box) == free_preview
    # Preview order and the previously cached geometry cannot affect H4.
    fresh = pallet(make_policy(stand_hw_height_margin_gate_mm=2400))
    assert fresh.preview_column_candidates(box, columns, .85) == previews
    assert fresh._heightfield_preview_cache is None


@pytest.mark.parametrize('template', ['w450', 'auto'])
def test_columns_combine_with_h6_future_rollouts_without_mutating_real_pallet(template):
    import copy

    policy = make_policy(
        column_template=template, stand_hw_height_margin_gate_mm=2400,
        micro_plan_enabled=True, micro_plan_node_budget=57,
        micro_plan_hard_cap_ms=600000, micro_plan_value='rollout',
        micro_plan_rollout_leaves=2, rollout_future_boxes=6,
        rollout_future_samples=1, rollout_prior='generic')
    p = pallet(policy)
    boxes = [Box(i, 605, 450, 355, timestamp=0, destination=1) for i in range(8)]
    state = SchedulerSimState(0, {1: boxes}, {1: p}, set())
    engine = policy._scheduler
    first = engine.choose_action(state)
    stats = dict(engine.last_micro_plan_stats)
    assert first is not None and 'column' in first.preview.debug
    assert p.placements == [] and len(boxes) == 8
    assert p._column_width_mm == copy.deepcopy(p)._column_width_mm == 450
    assert 0 < stats['rollout_future_nodes'] <= stats['nodes_expanded'] <= 57
    assert stats['rollout_future_samples_completed'] > 0
    assert engine.hard_cap_hits == 0
    assert engine.column_selected == 1 and engine.column_open_widths == [450]
    assert engine.choose_action(state) == first
    assert engine.last_micro_plan_stats['nodes_expanded'] == stats['nodes_expanded']
    assert engine.column_open_widths == [450]
