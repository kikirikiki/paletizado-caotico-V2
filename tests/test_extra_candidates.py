"""H7 contact geometry, shared production paths and exact H6 compatibility."""
from copy import deepcopy
import hashlib
import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.extra_candidates import empty_maximal_rectangles, extra_xy_candidates
from palca.packer.maxrects2d import Rect
from palca.packer.pallet_model import PalletModel


def pallet(mode='ep', maximum=32, offset=0):
    p = PalletModel(PalletSpec(length_mm=600, width_mm=600, overhang_mm=offset),
                    stacking_mode='heightfield', extra_candidates=mode,
                    extra_candidates_max=maximum)
    p.placements = [SimpleNamespace(x_mm=100, y_mm=100, z_mm=0,
                                   length_mm=200, width_mm=200, height_mm=100)]
    return p


def test_known_extreme_point_projection_and_no_gap():
    p = pallet()
    legacy = set(pallet('off')._heightfield_xy_candidates(80, 70))
    points = extra_xy_candidates(p, 80, 70, exclude=legacy)
    assert (300, 100) in points  # right face, absent from legacy
    assert (300, 0) in points    # projected onto the front wall
    assert all(x != 301 for x, _ in points)
    assert p._height_under_footprint_mm(300, 100, 80, 70) == 0


def test_maximal_spaces_keep_overlapping_full_strips():
    spaces = empty_maximal_rectangles(600, 600, [Rect(100, 100, 200, 200)])
    assert Rect(300, 0, 300, 600) in spaces
    assert Rect(0, 300, 600, 300) in spaces
    assert all(not r.intersects(Rect(100, 100, 200, 200)) for r in spaces)
    assert not any(a != b and a.contains(b) for a in spaces for b in spaces)


@pytest.mark.parametrize('mode', ['ep', 'ems', 'ep+ems'])
@pytest.mark.parametrize('maximum', [0, 1, 3, 16, 32])
def test_dedup_cap_legacy_prefix_and_bounds(mode, maximum):
    p = pallet(mode, maximum, offset=20)
    old = pallet('off', offset=20)._heightfield_xy_candidates(80, 70, cap=3)
    points = p._heightfield_xy_candidates(80, 70, cap=3)
    assert points[:len(old)] == old
    assert len(points) == len(set(points))
    assert len(points) <= len(old)+maximum
    assert all(0 <= x <= 560 and 0 <= y <= 570 for x, y in points)
    assert points == p._heightfield_xy_candidates(80, 70, cap=3)


def test_ems_intermediate_level_and_cache_invalidation():
    p = pallet('ems')
    p.placements[0].x_mm = p.placements[0].y_mm = 0
    p.placements += [SimpleNamespace(x_mm=200, y_mm=0, z_mm=0,
                                    length_mm=100, width_mm=200, height_mm=300)]
    points = extra_xy_candidates(p, 80, 70, exclude=set())
    assert any(p._height_under_footprint_mm(x, y, 80, 70) == 100 for x, y in points)
    cache = p._extra_geometry_cache
    extra_xy_candidates(p, 80, 70, exclude=set())
    assert p._extra_geometry_cache is cache
    clone = deepcopy(p)
    assert clone._extra_geometry_cache.points is not cache.points
    clone.placements.pop()
    extra_xy_candidates(clone, 80, 70, exclude=set())
    assert clone._extra_geometry_cache.key != cache.key
    assert p._extra_geometry_cache is cache


def test_region_filters_extra_points():
    p = pallet('ep+ems')
    points = p._heightfield_xy_candidates(80, 70, x_min=350, y_min=350)
    assert points
    assert all(x+40 >= 350 and y+35 >= 350 for x, y in points)


def test_extras_pass_production_controls_and_scoring():
    from dataclasses import replace
    from palca.domain.placement import Placement
    from palca.packer.controls import PlacementControlResult

    p = PalletModel(PalletSpec(length_mm=600, width_mm=600, allow_rotate=False),
                    stacking_mode='heightfield', extra_candidates='ep')
    p.placements = [Placement(x_mm=100, y_mm=100, z_mm=0, rot90=False,
        layer_id=0, length_mm=200, width_mm=200, height_mm=100, box_id=0)]
    target = (300, 100)
    seen = []
    class OnlyTarget:
        def evaluate(self, *, pallet, box, placement):
            xy = (placement.x_mm, placement.y_mm)
            seen.append(xy)
            return PlacementControlResult(xy == target, placement, score_delta=7.0)
    p.controls = replace(p.controls, placement_controls=[OnlyTarget()])
    preview = p.preview_place(Box(1, 80, 70, 100, 0))
    assert preview.feasible
    assert (preview.placement.x_mm, preview.placement.y_mm) == target
    assert seen.count(target) == 1
    # Turning extras off removes the only position accepted by this control.
    p.extra_candidates = 'off'
    assert not p.preview_place(Box(1, 80, 70, 100, 0)).feasible


def test_rear_autopsy_exact_union_clipping_and_bridging():
    from scripts.analyze_h7 import rear_strip
    def item(x, y, z=0):
        return dict(x_mm=x, y_mm=y, z_mm=z, length_mm=100, width_mm=100, height_mm=100)
    stats = rear_strip([item(0, 0), item(50, 0), item(150, 0, 100)],
                       x_bounds=(0, 200), y_bounds=(0, 100))
    assert stats['empty_fraction'] == 0
    assert stats['no_base_fraction'] == .25
    assert stats['mean_profile_mm'] == 125
    assert rear_strip([], x_bounds=(0, 200), y_bounds=(0, 100))['empty_fraction'] == 1


@pytest.mark.parametrize('kwargs', [{'extra_candidates': 'random'},
    {'extra_candidates_max': -1}, {'extra_candidates_max': True}, {'extra_candidates_max': 1.5}])
def test_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        PalletModel(**kwargs)


def test_cli_policy_and_search_copies_share_extra_generator(monkeypatch):
    from sim.run import build_parser
    from palca.integration.policy_packer_sched import PolicyPackerScheduler
    from palca.scheduler.scheduler_v1 import SchedulerSimState
    args = build_parser().parse_args(['--excel', 'unused', '--extra-candidates', 'ep+ems',
                                     '--extra-candidates-max', '16'])
    policy = PolicyPackerScheduler.from_defaults(stacking_mode='heightfield',
        extra_candidates=args.extra_candidates, extra_candidates_max=args.extra_candidates_max,
        micro_plan_enabled=True, micro_plan_node_budget=57, micro_plan_value='rollout',
        micro_plan_rollout_leaves=2, rollout_future_boxes=6, rollout_prior='generic', heartbeat_sec=0)
    p = policy._new_pallet()
    assert (p.extra_candidates, p.extra_candidates_max) == ('ep+ems', 16)
    calls = []
    original = PalletModel._heightfield_xy_candidates
    def counted(self, *args, **kwargs):
        assert (self.extra_candidates, self.extra_candidates_max) == ('ep+ems', 16)
        calls.append(id(self))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(PalletModel, '_heightfield_xy_candidates', counted)
    boxes = [Box(i, 200, 200, 100, 0, destination=1) for i in range(6)]
    engine = policy._scheduler
    plan = engine.choose_action(SchedulerSimState(0, {1: boxes}, {1: p}, set()))
    assert plan.preview.feasible
    assert engine.last_micro_plan_stats['rollout_nodes'] > 0
    assert len(set(calls)) > 1
    assert not p.placements
    assert engine.hard_cap_hits == 0


@pytest.mark.slow
@pytest.mark.parametrize('seed', [1, 50021])
def test_off_exact_h6_two_seeds(seed, tmp_path):
    import yaml
    from palca.study.metric import run_kpi, split_pallets
    from palca.study.params import preset_params
    from scripts.run_sweep import sweep_lock
    from sim.run import run_simulation
    fixture = json.loads((Path(__file__).parent/'fixtures/h7_h6_legacy.json').read_text())[str(seed)]
    base = next(c for c in yaml.safe_load(Path('configs/sweeps/h6_prior_eval.yaml').read_text())
                if c['name'] == 'h6_generic')
    with sweep_lock(retry_seconds=60):
        payload = run_simulation(**preset_params(base['preset'], **base['overrides'],
            episode_seed=seed, multi_ramp_seed=seed, extra_candidates='off',
            dump_placements_path=str(tmp_path/'dump.json')))
    dump = json.loads((tmp_path/'dump.json').read_text())
    assert hashlib.sha256(json.dumps(dump['pallets'], sort_keys=True).encode()).hexdigest() == fixture['pallets_sha256']
    kpi = run_kpi(split_pallets(payload, dump))
    assert kpi['kpi'] == fixture['kpi']
    assert kpi['pallet_utils'] == fixture['pallet_utils']
    assert payload['metrics']['pallet_kpis']['micro_plan_nodes_expanded'] == fixture['nodes']
    assert payload['metrics']['pallet_kpis']['hard_cap_hits'] == 0


def geometry_cases(generator):
    rng=random.Random(7107)
    results=[]
    for n in (0,1,3,8,16,24):
        for overhang in (0,20):
            p=PalletModel(PalletSpec(length_mm=1200,width_mm=800,overhang_mm=overhang),stacking_mode='heightfield')
            p.placements=[]
            for _ in range(n):
                l,w=rng.randrange(100,601,25),rng.randrange(75,401,25)
                x,y=rng.randrange(0,1200-l+1,25),rng.randrange(0,800-w+1,25)
                z,h=rng.choice((0,100,400,700)),rng.choice((100,200,400))
                p.placements.append(SimpleNamespace(x_mm=x-overhang,y_mm=y-overhang,z_mm=z,
                    length_mm=l,width_mm=w,height_mm=h))
            for mode in ('ep','ems','ep+ems'):
                p.extra_candidates=mode
                for maximum in (1,16,32):
                    p.extra_candidates_max=maximum
                    for l,w in ((175,125),(575,350),(350,575)):
                        p.extra_candidates='off'
                        exclude=set(p._heightfield_xy_candidates(l,w,cap=17))
                        p.extra_candidates=mode
                        results.append(generator(p,l,w,exclude=exclude))
    return results


def test_optimized_geometry_exact_v0_fixture():
    fixture = json.loads((Path(__file__).parent/"fixtures/h7_candidates_v0.json").read_text())
    results = geometry_cases(extra_xy_candidates)
    assert len(results) == fixture["n_cases"]
    assert hashlib.sha256(json.dumps(results, sort_keys=True).encode()).hexdigest() == fixture["sha256"]


def selection_files(tmp_path, monkeypatch, baseline_hits=0, all_invalid=False):
    from scripts import analyze_h7
    monkeypatch.setattr(analyze_h7, 'ROOT', tmp_path)
    folder = tmp_path/'out/sweeps/h7_tune'
    folder.mkdir(parents=True)
    (tmp_path/'configs/sweeps').mkdir(parents=True)
    configs, metrics = [], {}
    for name, score, cap, hits in [('h6_generic', .70, 16, baseline_hits),
                                  ('bad', .99, 32, 1), ('small', .80, 16, int(all_invalid)),
                                  ('large', .80, 32, int(all_invalid))]:
        configs.append(dict(name=name, preset='arm6_4orient', role='control',
                            overrides=dict(extra_candidates='ep', extra_candidates_max=cap)))
        metrics[name] = dict(kpi=dict(mean=score), violations_total=0,
                            hard_cap_hits_total=hits, n_unplaced=dict(max=0),
                            deadline_cutoffs_count=dict(max=0))
    (folder/'manifest.json').write_text(json.dumps(dict(configs=configs)))
    summary = json.dumps(dict(configs=metrics, source_hash='measured-source'))
    (folder/'summary.json').write_text(summary)
    return analyze_h7, folder, summary


def test_selection_excludes_invalid_variant_and_breaks_ties_by_cap(tmp_path, monkeypatch):
    module, folder, original = selection_files(tmp_path, monkeypatch)
    result = module.select()
    assert result['winner'] == 'small'
    assert [r['name'] for r in result['ranking']] == ['small', 'large']
    assert set(result['rejected']) == {'bad'}
    assert result['source_hash'] == 'measured-source'
    assert (folder/'summary.json').read_text() == original


def test_selection_rejects_invalid_baseline(tmp_path, monkeypatch):
    module, _, _ = selection_files(tmp_path, monkeypatch, baseline_hits=1)
    with pytest.raises(ValueError, match='h6_generic'):
        module.select()


def test_selection_requires_an_admissible_variant(tmp_path, monkeypatch):
    module, _, _ = selection_files(tmp_path, monkeypatch, all_invalid=True)
    with pytest.raises(ValueError, match='No admissible'):
        module.select()
