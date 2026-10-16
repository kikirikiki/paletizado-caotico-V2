"""H2 kernel, shared greedy/beam scoring and frozen H1+H3 regression."""
import hashlib
import json
from inspect import signature

import numpy as np
import pytest

from palca.domain.placement import Placement
from palca.scoring.access_debt import build_heightfield
from palca.scoring.slots import narrow_slot_penalty
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerV1
from palca.integration.policy_packer_sched import PolicyPackerScheduler


def test_contact_and_exact_wall_strip():
    field = build_heightfield([], 950, 400)
    before = field.heights_mm.copy()
    assert narrow_slot_penalty(field, (0, 0, 950, 400), 0, 355) == 0
    assert narrow_slot_penalty(field, (0, 0, 600, 400), 0, 355) == 350 / 600
    assert narrow_slot_penalty(build_heightfield([], 955, 400), (0, 0, 600, 400), 0, 355) == 0
    np.testing.assert_array_equal(field.heights_mm, before)
    field = build_heightfield([], 1000, 400)
    assert narrow_slot_penalty(field, (0, 0, 600, 400), 0, 355) == 0


@pytest.mark.parametrize('gap', [0, 350, 360, 400])
@pytest.mark.parametrize('rotate', [False, True])
def test_obstacle_and_strict_threshold(gap, rotate):
    obstacle = Placement(600 + gap, 0, 0, False, 0, 200, 400, 100)
    footprint = (0, 0, 600, 400)
    dims = (1300, 400)
    if rotate:
        obstacle = Placement(0, 600 + gap, 0, False, 0, 400, 200, 100)
        footprint, dims = (0, 0, 400, 600), (400, 1300)
    field = build_heightfield([obstacle], *dims)
    before = field.heights_mm.copy()
    assert narrow_slot_penalty(field, footprint, 0, 355) == (gap / 600 if 0 < gap < 355 else 0)
    # Height == base is traversable, exposing a wide region all the way to wall.
    assert narrow_slot_penalty(field, footprint, 100, 355) == 0
    np.testing.assert_array_equal(field.heights_mm, before)


def test_four_faces_partial_cells_translation_and_no_corners():
    field = build_heightfield([], 95, 85, origin_mm=(-20, -20))
    # Gaps 20, 35 along x and 20, 25 along y; four disjoint strips.
    assert narrow_slot_penalty(field, (0, 0, 40, 40), 0, 40) == (55 * 40 + 45 * 40) / 1600
    tiny = build_heightfield([], 7, 8, origin_mm=(-3, -4))
    assert narrow_slot_penalty(tiny, (-3, -4, 7, 8), 0, 355) == 0


def test_propagation_validation_and_disabled_path(monkeypatch):
    from sim.run import run_simulation
    assert signature(run_simulation).parameters['slot_weight'].default == 0
    assert signature(run_simulation).parameters['slot_min_mm'].default == 355
    policy = PolicyPackerScheduler.from_defaults(slot_weight=.5, slot_min_mm=400)
    assert policy._scheduler.config.slot_weight == .5
    assert policy._scheduler.config.slot_min_mm == 400
    for kw in ({'slot_weight': -1}, {'slot_weight': float('nan')}, {'slot_weight': float('inf')},
               {'slot_min_mm': 0}, {'slot_min_mm': 3.5}):
        with pytest.raises(ValueError):
            SchedulerConfig(**kw)
    from test_access_debt import decide
    monkeypatch.setattr('palca.scheduler.scheduler_v1.narrow_slot_penalty', lambda *a: pytest.fail('disabled'))
    engine, _ = decide(access_debt_weight=.25)
    assert engine.collect_slot_metrics()['measured_candidates'] == 0


def test_greedy_changes_choice_and_reports_penalty():
    from test_access_debt import decide
    engine, plan = decide(slot_weight=1, slot_min_mm=41)
    assert plan.box_id == 2  # Contact with obstacle; candidate 1 leaves a 40 mm gap.
    stats = engine.collect_slot_metrics()
    assert stats['changed_choices'] == 1
    assert stats['selected_count'] == 1
    assert stats['selected_penalty_mean'] == 1  # 20 mm gap to right wall / length 20.
    assert stats['measured_candidates'] == 2


def test_beam_scores_every_expansion_and_records_first_choice(monkeypatch):
    from test_microplanner_deterministic import state
    engine = SchedulerV1(SchedulerConfig(micro_plan_enabled=True, micro_plan_node_budget=30,
                                         slot_weight=.5, access_debt_weight=.25))
    penalties = []
    real = narrow_slot_penalty
    def measured(*args):
        result = real(*args)
        penalties.append(result)
        return result
    monkeypatch.setattr('palca.scheduler.scheduler_v1.narrow_slot_penalty', measured)
    chosen = engine.choose_action(state())
    assert chosen is not None
    stats = engine.collect_slot_metrics()
    assert len(penalties) == 30
    assert stats['measured_candidates'] == 30
    assert stats['selected_count'] == 1
    expected = real(build_heightfield([], 1200, 800),
                    (chosen.preview.placement.x_mm, chosen.preview.placement.y_mm, 200, 200), 0, 355)
    assert stats['selected_penalty_mean'] == expected


@pytest.mark.slow
@pytest.mark.parametrize('seed, expected_hash, expected_kpi', [
    (50021, '93487df849fe503649fb900c08e42fa89ccc0f7c67e2f8ed0e55bf2f94276b11', .6998966822765545),
    (1, 'a537143db424829114516831964446057d05f920130ec317a9a3c02fd0bb9295', .693043454781106),
])
def test_zero_weight_exact_h1_h3_frozen_reference(tmp_path, seed, expected_hash, expected_kpi):
    from palca.study.params import preset_params
    from palca.study.metric import run_kpi, split_pallets
    from scripts.run_sweep import sweep_lock
    from sim.run import run_simulation
    dump = tmp_path / 'dump.json'
    with sweep_lock(retry_seconds=60):
        payload = run_simulation(**preset_params(
            'arm6_4orient', episode_seed=seed, multi_ramp_seed=seed, slot_weight=0,
            access_debt_mode='penalty', access_debt_weight=.25, access_debt_band_mm=600,
            micro_plan=True, micro_plan_node_budget=100, micro_depth=3, micro_width=8, micro_topk=15,
            dump_placements_path=str(dump)))
    data = json.loads(dump.read_text())
    assert hashlib.sha256(json.dumps(data['pallets'], sort_keys=True).encode()).hexdigest() == expected_hash
    assert run_kpi(split_pallets(payload, data))['kpi'] == expected_kpi
    assert payload['metrics']['pallet_kpis']['hard_cap_hits'] == 0
    assert payload['metrics']['pallet_kpis']['deadline_cutoffs_count'] == 0
