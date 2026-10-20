"""Spec 19: offline options use the production R7 rule throughout replay/search."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import runpy
import sys

import pytest

from palca.domain.box import Box
from palca.domain.placement import Placement
from palca.packer.controls import AccessibilityConfig, RobotAccessibilityControl
from palca.study.offline_search import (
    beam_search, checked_preview, column_search, decode_order, order_search,
    production_policy, summarize,
)
from palca.study.params import PRESETS

ROOT = Path(__file__).resolve().parents[1]
verify = runpy.run_path(str(ROOT / 'scripts/autopilot/verify_offline_witness.py'))
accessibility_violations = verify['accessibility_violations']


def touching_pair(candidate_height=600, neighbour_height=500):
    boxes = (Box(1, 200, 200, neighbour_height, 0, destination=1),
             Box(2, 200, 200, candidate_height, 0, destination=1))
    items = [asdict(Placement(x, -20, 0, False, 0, 200, 200, h, box_id=i,
                             orientation_name='LWH', orientation_family='planar'))
             for i, x, h in [(1, -20, neighbour_height), (2, 180, candidate_height)]]
    return boxes, items


def test_factory_uses_production_accessibility_config_without_mutation():
    before = deepcopy(PRESETS)
    old = production_policy()._new_pallet()
    new = production_policy(tall_box_exemption=True, top_clearance_mm=75)._new_pallet()
    old_control = next(c for c in old.controls.placement_controls if isinstance(c, RobotAccessibilityControl))
    new_control = next(c for c in new.controls.placement_controls if isinstance(c, RobotAccessibilityControl))
    assert old_control.config == AccessibilityConfig(400, False, 0)
    assert new_control.config == AccessibilityConfig(400, True, 75)
    assert PRESETS == before


@pytest.mark.parametrize('height,top,clearance,expected', [
    (600, 500, 0, 0), (600, 600, 0, 0), (600, 601, 0, 1),
    (300, 400, 0, 0), (300, 401, 0, 1),
    (450, 500, 50, 0), (450, 501, 50, 1),
    (450, 500, 0, 1), (600, 500, -101, 1),
])
def test_independent_verifier_agrees_with_production_threshold(height, top, clearance, expected):
    boxes, items = touching_pair(height, top)
    model = production_policy(tall_box_exemption=True, top_clearance_mm=clearance)._new_pallet()
    model.commit_place(checked_preview(model, boxes[0], Placement(**items[0])))
    if expected:
        with pytest.raises(ValueError, match='ROBOT_ACCESS'):
            checked_preview(model, boxes[1], Placement(**items[1]))
    else:
        assert checked_preview(model, boxes[1], Placement(**items[1])).feasible
    assert accessibility_violations(items, 400, tall_box_exemption=True,
                                    top_clearance_mm=clearance) == expected


def test_replay_old_forbids_new_allows_and_clearance_only_with_exemption():
    boxes, items = touching_pair()
    with pytest.raises(ValueError, match='ROBOT_ACCESS'):
        summarize([items], boxes)
    new = summarize([items], boxes, tall_box_exemption=True)
    assert new['n_violations'] == new['n_production_violations'] == 0
    assert new['boxes_placed'] == 2
    assert accessibility_violations(items, 400) == 1
    assert accessibility_violations(items, 400, top_clearance_mm=1000) == 1
    assert accessibility_violations(items, 0, tall_box_exemption=True) == 0


@pytest.mark.parametrize('search,kwargs', [
    (column_search, dict(width=2, templates=(1,))),
    (beam_search, dict(width=16, top_m=2)),
    (order_search, dict(evaluations=1)),
])
def test_all_searches_default_legacy_and_checkpoint_rule_identity(tmp_path, search, kwargs):
    boxes = tuple(Box(i, 600, 440, 350, 0, destination=1) for i in range(1, 4))
    default = search(boxes, **kwargs)
    explicit = search(boxes, tall_box_exemption=False, top_clearance_mm=0, **kwargs)
    for key in ('pallets', 'sequence', 'kpi', 'pallet_utils'):
        assert default[key] == explicit[key]
    checkpoint = tmp_path / 'checkpoint.json'
    new = search(boxes, checkpoint=checkpoint, tall_box_exemption=True, top_clearance_mm=50, **kwargs)
    resumed = search(boxes, checkpoint=checkpoint, resume=True,
                     tall_box_exemption=True, top_clearance_mm=50, **kwargs)
    assert new['pallets'] == resumed['pallets']
    assert new['config']['tall_box_exemption'] is True
    assert new['config']['top_clearance_mm'] == 50
    for access in (dict(tall_box_exemption=False, top_clearance_mm=50),
                   dict(tall_box_exemption=True, top_clearance_mm=0)):
        with pytest.raises(ValueError, match='does not match'):
            search(boxes, checkpoint=checkpoint, resume=True, **access, **kwargs)


def test_decoder_passes_rule_to_replay():
    boxes, _ = touching_pair()
    result = decode_order(boxes, [1, 2], tall_box_exemption=True, top_clearance_mm=30)
    assert result['boxes_placed'] == 2
    assert result['n_production_violations'] == 0


def test_mixed_columns_keep_actual_standing_gate_and_replay(tmp_path):
    boxes = tuple(Box(i, 600, 440, 350, 0, destination=1) for i in range(1, 22))
    checkpoint = tmp_path / 'mixed.json'
    result = column_search(boxes, width=2, templates=(1,), tall_box_exemption=True,
                           column_mixed_families=True, checkpoint=checkpoint)
    assert result['config']['column_mixed_families'] is True
    assert result['boxes_placed'] == 21
    summarize(result['pallets'], boxes, tall_box_exemption=True)
    for items in result['pallets']:
        peak = 0
        for item in items:
            if item['orientation_family'] == 'stand_hw':
                assert peak >= 2000
            peak = max(peak, item['z_mm'] + item['height_mm'])
    with pytest.raises(ValueError, match='does not match'):
        column_search(boxes, width=2, templates=(1,), tall_box_exemption=True,
                      checkpoint=checkpoint, resume=True)


def test_bound_cli_validate_uses_explicit_rule(tmp_path, monkeypatch, capsys):
    script = runpy.run_path(str(ROOT / 'scripts/bound_offline.py'))
    boxes, items = touching_pair(450)
    expected = summarize([items], boxes, tall_box_exemption=True, top_clearance_mm=50)
    path = tmp_path / 'witness.json'
    path.write_text(json.dumps(expected))
    monkeypatch.setitem(script['main'].__globals__, 'load_boxes', lambda _: boxes)
    args = ['bound_offline.py', '--method', 'columns', '--validate', str(path)]
    monkeypatch.setattr(sys, 'argv', args + ['--tall-box-exemption', '--top-clearance-mm', '50'])
    script['main']()
    assert json.loads(capsys.readouterr().out)['n_production_violations'] == 0
    monkeypatch.setattr(sys, 'argv', args)
    with pytest.raises(ValueError, match='ROBOT_ACCESS'):
        script['main']()


@pytest.mark.parametrize('method', ['columns', 'beam', 'order'])
def test_bound_cli_search_records_effective_production_rule(tmp_path, monkeypatch, method):
    script = runpy.run_path(str(ROOT / 'scripts/bound_offline.py'))
    namespace = script['main'].__globals__
    boxes, items = touching_pair(450)
    result = summarize([items], boxes, tall_box_exemption=True, top_clearance_mm=50)
    result.update(cpu_seconds=0.0, wall_seconds=0.0)
    captured = {}

    def search(actual_boxes, **kwargs):
        assert actual_boxes == boxes
        captured.update(kwargs)
        return result

    monkeypatch.setitem(namespace, 'load_boxes', lambda _: boxes)
    monkeypatch.setitem(namespace, 'comparison', lambda *args: dict(metrics=dict(
        kpi=dict(mean=0.6), kpi_all_pallets=dict(mean=0.5))))
    monkeypatch.setitem(namespace, f'{"column" if method == "columns" else method}_search', search)
    output = tmp_path / 'result.json'
    excel = tmp_path / 'input.xlsx'
    excel.write_bytes(b'input hash fixture')
    monkeypatch.setattr(sys, 'argv', ['bound_offline.py', '--method', method,
                                     '--excel', str(excel), '--out', str(output),
                                     '--tall-box-exemption', '--top-clearance-mm', '50',
                                     '--column-mixed-families'])
    script['main']()
    assert captured['tall_box_exemption'] is True
    assert captured['top_clearance_mm'] == 50
    if method == 'columns':
        assert captured['column_mixed_families'] is True
    effective = json.loads(output.read_text())['effective_policy']
    assert effective['accessibility_tall_box_exemption'] is True
    assert effective['accessibility_top_clearance_mm'] == 50


def test_verifier_cli_uses_explicit_rule(tmp_path, monkeypatch, capsys):
    boxes, items = touching_pair(450)
    path = tmp_path / 'witness.json'
    path.write_text(json.dumps(dict(pallets=[items])))
    monkeypatch.setitem(verify['main'].__globals__, 'load_arrivals', lambda _: boxes)
    args = ['verify_offline_witness.py', str(path)]
    monkeypatch.setattr(sys, 'argv', args)
    assert verify['main']() == 1
    monkeypatch.setattr(sys, 'argv', args + ['--tall-box-exemption', '--top-clearance-mm', '50'])
    assert verify['main']() == 0
    assert 'tall-box-exemption=True, top-clearance-mm=50' in capsys.readouterr().out
