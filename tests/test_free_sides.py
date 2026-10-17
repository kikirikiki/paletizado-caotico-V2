"""D-024 geometry, compactness priority, propagation and observation time."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from palca.domain.box import Box
from palca.domain.placement import Placement
from palca.packer.pallet_model import PalletModel
from palca.packer.scoring import ScoringWeights
from palca.scoring.free_sides import free_sides, free_sides_distribution, rank_free_sides
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1, _BeamNode
from palca.integration.policy_packer_sched import PolicyPackerScheduler


def p(x=100, y=100, z=100, l=100, w=100, h=100):
    return Placement(x, y, z, False, 0, l, w, h)


@pytest.mark.parametrize('axis', ['-x', '+x', '-y', '+y'])
@pytest.mark.parametrize('gap,expected', [(0, 3), (19, 3), (19.999, 3), (20, 4), (21, 4)])
def test_all_faces_and_strict_gap(axis, gap, expected):
    xy = {'-x': (-gap, 100), '+x': (200+gap, 100),
          '-y': (100, -gap), '+y': (100, 200+gap)}[axis]
    assert free_sides(p(), [p(*xy)]) == expected


@pytest.mark.parametrize('neighbor,expected', [
    (p(0, 100, 0), 4),       # support / roof exactly at candidate base
    (p(0, 100, -1), 4),      # roof below base
    (p(0, 100, 1), 3),       # positive vertical overlap by one mm
    (p(0, 100, 199), 3),
    (p(0, 100, 200), 4),     # base exactly at candidate roof
    (p(0, 100, 201), 4),
    (p(0, 0), 4),            # corner only
    (p(0, 1), 3),            # tangential overlap by one mm
    (p(0, 200), 4),
    (p(0, 199), 3),
    (p(100, 100, 0), 4),     # actual box directly supporting candidate
    (p(0, 100, 90, h=120), 3),  # spans the whole candidate height
])
def test_overlap_intervals(neighbor, expected):
    assert free_sides(p(), [neighbor]) == expected


def test_counts_unique_faces_edges_and_oriented_dimensions():
    neighbors = [p(0, 100), p(200, 100), p(100, 0), p(100, 200)]
    for count in range(5):
        assert free_sides(p(), neighbors[:count] * 2) == 4-count
    assert free_sides(p(0, 0, 0), []) == 4  # pallet edge has no geometry
    assert free_sides(replace(p(l=200, w=50), rot90=True), [p(300, 100)]) == 3
    assert free_sides(p(), [p(0, 100)], gap_mm=0) == 3
    assert free_sides(p(), [p(-1, 100)], gap_mm=0) == 4
    before = deepcopy(neighbors)
    free_sides(p(), iter(neighbors))
    assert neighbors == before


@pytest.mark.parametrize('invalid', [-1, float('nan'), float('inf')])
def test_invalid_tolerance_and_gap(invalid):
    with pytest.raises(ValueError):
        free_sides(p(), [], invalid)
    with pytest.raises(ValueError):
        SchedulerConfig(free_sides_tolerance=invalid)


def ranked(rows, tolerance):
    return rank_free_sides(rows, value=lambda r: r[0], sides=lambda r: r[1],
                           legacy_key=lambda r: (r[0], r[2]), tolerance=tolerance)


def test_relative_ties_compactness_first_zero_negative_and_no_chaining():
    assert ranked([(1., 1, 0), (1., 4, 0)], 0)[0][1] == 4
    assert ranked([(1., 1, 0), (.999, 4, 0)], 0)[0][1] == 1
    assert ranked([(1., 1, 0), (.9995, 4, 0)], .001)[0][1] == 4
    assert ranked([(1., 1, 0), (.99, 4, 0)], .001)[0][1] == 1
    assert ranked([(0., 1, 0), (-1e-12, 4, 0)], .01)[0][1] == 1
    assert ranked([(-100., 1, 0), (-100.05, 4, 0)], .001)[0][1] == 4
    assert ranked([(100., 1, 0), (99.5, 2, 0), (99., 4, 0)], .006)[0][1] == 2
    assert ranked([(1., 3, 1), (1., 3, 2)], 0)[0][2] == 2


def test_packer_tied_positions_and_runtime_isolation(monkeypatch):
    pallet = PalletModel(stacking_mode='heightfield',
                         scoring_weights=ScoringWeights(fragmentation_weight=0))
    pallet.placements.append(p(100, 0, 0))
    monkeypatch.setattr(pallet, '_heightfield_xy_candidates', lambda *a, **k: [(0, 0), (500, 0)])
    box = Box(1, 100, 100, 100, 0, destination=1)
    old = pallet.preview_place(box)
    new = pallet.preview_place(box, free_sides_tiebreak=True)
    assert old.placement.x_mm == 0
    assert new.placement.x_mm == 500
    assert old.packing_gain - old.fragmentation + old.score_adjustment == (
        new.packing_gain - new.fragmentation + new.score_adjustment)
    assert pallet.preview_place(box) == old


class SlatePallet:
    def __init__(self):
        self.placements = [p(100, 0, 0)]
        self.received = []

    def preview_place(self, box, **kwargs):
        from palca.domain.placement import PlacementPreview
        self.received.append(kwargs)
        placement = p(0 if box.box_id == 1 else 500, 0, 0)
        return PlacementPreview(True, placement, 1., 0., height_after_mm=100)

    def current_height_mm(self):
        return 100

    def commit_place(self, preview):
        self.placements.append(preview.placement)
        return preview.placement


@pytest.mark.parametrize('mode,future', [('greedy', 0), ('score', 0), ('potential', 0), ('rollout', 0), ('rollout', 1)])
def test_tied_actions_greedy_beam_h3_h5_h6_and_rollouts(mode, future, monkeypatch):
    monkeypatch.setattr("palca.scheduler.scheduler_v1.visible_completion_potential", lambda *a: 0.0)
    boxes = [Box(i, 100, 100, 100, 0, destination=1) for i in (1, 2)]
    state = SchedulerSimState(0, {1: boxes}, {1: SlatePallet()}, set())
    cfg = SchedulerConfig(lookahead_k=2, time_budget_ms=0, heartbeat_sec=0,
                          micro_plan_enabled=mode != 'greedy', micro_plan_depth=1,
                          micro_plan_width=2, micro_plan_node_budget=10,
                          micro_plan_window_total=2, micro_plan_value='score' if mode == 'greedy' else mode,
                          rollout_future_boxes=future)
    assert SchedulerV1(cfg).choose_action(state).box_id == 1
    engine = SchedulerV1(replace(cfg, free_sides_tiebreak=True))
    assert engine.choose_action(state).box_id == 2
    # Visible and stochastic continuations both use the enabled greedy selector.
    leaf = _BeamNode({1: SimpleNamespace(queue=boxes)}, state.pallets)
    _, consumed, completed = engine._rollout_value(leaf, state, 1, None)
    assert consumed == 1
    assert not completed
    assert state.pallets[1].placements == [p(100, 0, 0)]
    assert state.pallets[1].received[-1]['free_sides_tiebreak'] is True


def test_beam_first_action_uses_original_neighbors():
    engine = SchedulerV1(SchedulerConfig(free_sides_tiebreak=True, micro_plan_value='potential'))
    low = _BeamNode({}, {}, state_value=100, first_free_sides=1)
    high = _BeamNode({}, {}, state_value=100, first_free_sides=4)
    assert engine._rank_free_beam([low, high])[0] is high
    low.state_value = 101
    assert engine._rank_free_beam([low, high])[0] is low
    engine.config = replace(engine.config, free_sides_tolerance=.01)
    assert engine._rank_free_beam([low, high])[0] is high


def test_distribution_before_insertion_and_reset():
    a, b = p(0, 0, 0), p(100, 0, 0)
    result = free_sides_distribution([[a, b], [a]])
    assert result['count'] == 3
    assert result['counts'] == {'4': 2, '3': 1, '2': 0, '1': 0, '0': 0}
    assert result['proportions']['4'] == 2/3
    assert sum(result['proportions'].values()) == 1
    assert free_sides_distribution([])['count'] == 0


def test_config_and_cli_propagation():
    from sim.run import build_parser
    policy = PolicyPackerScheduler.from_defaults(free_sides_tiebreak=True, free_sides_tolerance=.01)
    assert policy.config.free_sides_tiebreak is True
    assert policy._scheduler.config.free_sides_tolerance == .01
    assert SchedulerConfig().free_sides_tiebreak is False
    assert SchedulerConfig().free_sides_tolerance == 0
    args = build_parser().parse_args(['--excel', 'unused.xlsx', '--free-sides-tiebreak', '--free-sides-tolerance', '.001'])
    assert args.free_sides_tiebreak and args.free_sides_tolerance == .001


@pytest.mark.parametrize('seed', [1, 42])
def test_disabled_identical_decisions_for_two_seeds(seed):
    import random
    randomizer = random.Random(seed)
    boxes = [Box(i, randomizer.choice([100, 200]), 100, 100, 0, destination=1) for i in range(12)]
    def episode(**kw):
        engine = SchedulerV1(SchedulerConfig(lookahead_k=4, time_budget_ms=0, heartbeat_sec=0,
                                             micro_plan_enabled=True, micro_plan_node_budget=12, **kw))
        pallet = PalletModel(stacking_mode='heightfield')
        queue = list(boxes)
        plans = []
        while queue:
            state = SchedulerSimState(0, {1: queue}, {1: pallet}, set())
            plan = engine.choose_action(state)
            assert plan is not None
            plans.append(plan)
            pallet.commit_place(plan.preview)
            queue.pop(plan.buffer_index)
        return plans, pallet.placements, engine.micro_plan_nodes_expanded
    assert episode() == episode(free_sides_tiebreak=False, free_sides_tolerance=.01)


def test_recommendation_requires_both_cohorts_and_no_mean_loss():
    from scripts.report_free_sides import BASELINE, VARIANTS, choose_configuration
    def summary():
        return dict(seeds=list(range(10)),
                    configs={name: dict(violations_total=0, hard_cap_hits_total=0,
                                        deadline_cutoffs_count={'max': 0}) for name in (BASELINE, *VARIANTS)},
                    comparisons={name: dict(result=dict(n=10, mean_diff_pp=.2, ci95_pp=[-.4,.8]))
                                 for name in VARIANTS})
    a, b = summary(), summary()
    assert choose_configuration([a, b]) == 'free_sides_01'
    b['comparisons']['free_sides_01']['result']['mean_diff_pp'] = -.01
    assert choose_configuration([a, b]) == 'free_sides_001'
    b['comparisons']['free_sides_001']['result']['ci95_pp'][0] = -.5
    assert choose_configuration([a, b]) == 'free_sides_exact'
    b['configs']['free_sides_exact']['hard_cap_hits_total'] = 1
    assert choose_configuration([a, b]) == BASELINE
