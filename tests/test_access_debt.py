from types import SimpleNamespace

import numpy as np
import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.domain.placement import Placement, PlacementPreview
from palca.scoring.access_debt import AccessDebt, access_debt, build_heightfield
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1
from palca.integration.policy_packer_sched import PolicyPackerScheduler


def placement(x=0, y=0, z=0, length=20, width=20, height=600):
    return Placement(x, y, z, False, 0, length, width, height)


def test_empty_floor_and_delta_zero():
    field = build_heightfield([], 60, 40)
    assert access_debt(field, (0, 0, 20, 20), 355, 400, 20) == AccessDebt()
    assert access_debt(field, (0, 0, 20, 20), 1600, 0, 20) == AccessDebt()
    assert access_debt(field, (0, 0, 20, 20), 1600, 400, 0) == AccessDebt()


def test_tower_next_to_hole_exact_and_no_mutation():
    tower = placement()
    field = build_heightfield([tower], 60, 20)
    before = field.heights_mm.copy()
    # Candidate on tower: top=800, threshold=400. Four neighbouring cells
    # (20 mm x 20 mm) in the band are at zero; footprint is excluded.
    debt = access_debt(field, (0, 0, 20, 20), 800, 400, 20)
    assert debt == AccessDebt(dead_volume_mm3=160000, dead_area_mm2=400)
    np.testing.assert_array_equal(field.heights_mm, before)


def test_euclidean_band_clipping_partial_cells_and_overhang():
    field = build_heightfield([], 35, 25, origin_mm=(-20, -20))
    assert field.cell_areas_mm2.sum() == 875
    assert access_debt(field, (-20, -20, 20, 20), 500, 400, 100) == AccessDebt(47500, 475)
    field = build_heightfield([], 40, 40)
    # Eleven of twelve outside cells are within the band. The far
    # corner (15,15) from the rectangle is outside the circular 20 mm band.
    assert access_debt(field, (0, 0, 20, 20), 500, 400, 20) == AccessDebt(110000, 1100)


def test_profile_takes_highest_top_and_strict_threshold():
    field = build_heightfield([placement(height=100), placement(z=100, height=300)], 40, 20)
    assert field.heights_mm[0, 0] == 400
    assert access_debt(field, (20, 0, 20, 20), 800, 400, 20) == AccessDebt()


class DebtPallet:
    spec = PalletSpec(length_mm=60, width_mm=20)
    accessibility_delta_mm = 400
    placements = [placement()]

    def preview_place(self, box):
        high = box.box_id == 1
        p = placement(z=600, height=200) if high else placement(x=20, height=200)
        # Keep both inside height slack so H1, not height, decides.
        return PlacementPreview(True, p, 1.0 if high else 0.9, 0.0, height_after_mm=800)


def decide(**kwargs):
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=2, time_budget_ms=0, **kwargs))
    boxes = [Box(i, 20, 20, 200, timestamp=0, destination=1) for i in (1, 2)]
    state = SchedulerSimState(0, {1: boxes}, {1: DebtPallet()}, set())
    return scheduler, scheduler.choose_action(state)


@pytest.mark.parametrize('mode', ['penalty', 'filter'])
def test_h1_changes_choice_and_reports_chosen_debt(mode):
    scheduler, chosen = decide(access_debt_weight=0.25, access_debt_band_mm=20, access_debt_mode=mode)
    assert chosen.box_id == 2
    metrics = scheduler.collect_access_debt_metrics()
    assert metrics['changed_choices'] == 1
    assert metrics['selected_count'] == 1
    assert metrics['selected_dead_volume_mm3_mean'] == 0
    assert metrics['measured_candidates'] == 2


def test_penalty_formula_disabled_path_and_counterfactual():
    scheduler, chosen = decide(access_debt_weight=0)
    assert chosen.box_id == 1
    assert scheduler.collect_access_debt_metrics()['measured_candidates'] == 0
    # 160000/80000 * 0.01 = 0.02. Same choice, smaller score, no change count.
    scheduler, chosen = decide(access_debt_weight=0.01, access_debt_band_mm=20)
    assert chosen.box_id == 1
    assert chosen.score == pytest.approx(0.98)
    assert scheduler.collect_access_debt_metrics()['changed_choices'] == 0
    assert scheduler.collect_access_debt_metrics()['selected_dead_volume_mm3_mean'] == 160000


def test_filter_retains_all_if_every_candidate_has_debt():
    candidates = [SimpleNamespace(terms=SimpleNamespace(access_dead_volume_mm3=v)) for v in (1, 2)]
    assert SchedulerV1._filter_access_debt(candidates) is candidates
    zero = SimpleNamespace(terms=SimpleNamespace(access_dead_volume_mm3=0))
    assert SchedulerV1._filter_access_debt([*candidates, zero]) == [zero]


def test_propagation_and_invalid_config():
    from inspect import signature
    from sim.run import run_simulation
    defaults = signature(run_simulation).parameters
    assert defaults['access_debt_weight'].default == 0.0
    assert defaults['access_debt_band_mm'].default == 300
    assert defaults['access_debt_mode'].default == 'penalty'
    policy = PolicyPackerScheduler.from_defaults(access_debt_weight=2, access_debt_band_mm=445, access_debt_mode='filter')
    assert policy._scheduler.config.access_debt_weight == 2
    assert policy._scheduler.config.access_debt_band_mm == 445
    assert policy._scheduler.config.access_debt_mode == 'filter'
    for kwargs in ({'access_debt_mode': 'bad'}, {'access_debt_weight': -1}, {'access_debt_weight': float('nan')}, {'access_debt_band_mm': -1}):
        with pytest.raises(ValueError):
            SchedulerConfig(**kwargs)


@pytest.mark.parametrize('seed, expected_kpi', [(50021, 0.6409566412250384), (1, 0.6244391341045827)])
@pytest.mark.slow
def test_weight_zero_canonical_excel_matches_frozen_baseline(tmp_path, seed, expected_kpi):
    """Frozen baseline_v1, arm6_4orient, canonical Excel (186 boxes), KPI v1.

    Reference: official 59f1ef59 / baseline_v1, not an H1 execution compared
    against itself. Final evaluation remeasures all ten baseline seeds too.
    """
    from scripts.run_sweep import sweep_lock
    from palca.study.metric import run_kpi, split_pallets
    from palca.study.params import preset_params
    from sim.run import run_simulation
    import json

    dump = tmp_path / 'dump.json'
    params = preset_params('arm6_4orient', episode_seed=seed, multi_ramp_seed=seed,
                           access_debt_weight=0.0, dump_placements_path=str(dump))
    with sweep_lock(retry_seconds=60):
        payload = run_simulation(**params)
    result = run_kpi(split_pallets(payload, json.loads(dump.read_text())))
    assert result['kpi'] == expected_kpi
    assert result['boxes_placed'] == 186
    assert result['n_closed'] == 10
    assert not payload['metrics']['pallet_kpis']['access_debt']['enabled']


def test_tune_grid_is_complete_and_disjoint_from_reference():
    from pathlib import Path
    from scripts.run_sweep import load_configs
    from palca.study.params import REFERENCE_SEEDS
    configs = load_configs(Path('configs/sweeps/h1_tune.yaml'))
    assert len(configs) == 13
    assert not set(range(1001, 1011)) & set(REFERENCE_SEEDS)
    assert {(c['overrides']['access_debt_weight'], c['overrides']['access_debt_band_mm'])
            for c in configs if c['overrides'].get('access_debt_mode') == 'penalty'} == {
                (weight, band) for weight in (.25, .5, 1, 2, 4) for band in (200, 445)}


def test_delta_zero_and_heightfield_reused_within_decision(monkeypatch):
    from palca.scheduler import scheduler_v1 as module
    calls = []
    original = module.build_heightfield

    def build(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, 'build_heightfield', build)
    scheduler, chosen = decide(access_debt_weight=.25)
    assert len(calls) == 1  # Two candidates, one pre-placement field.
    calls.clear()
    monkeypatch.setattr(DebtPallet, 'accessibility_delta_mm', 0)
    scheduler, chosen = decide(access_debt_mode='filter')
    assert chosen.box_id == 1
    assert not calls
    assert scheduler.collect_access_debt_metrics()['selected_dead_volume_mm3_mean'] == 0
