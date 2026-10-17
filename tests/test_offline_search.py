from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from palca.domain.box import Box
from palca.domain.placement import Placement
from palca.scheduler.scheduler_v1 import SchedulerSimState
from palca.study.offline_search import (
    beam_search, checked_preview, column_search, decode_order, geometric_candidates, order_search,
    production_candidates, production_policy, profile_metrics, summarize,
)
from palca.study.params import PRESETS
from palca.study.validate import validate_pallet


def cubes():
    return tuple(Box(i, 620, 840, 400, 0, destination=1) for i in range(1, 14))


def test_factory_and_overrides_are_local():
    before = deepcopy(PRESETS)
    policy = production_policy()
    model = policy._new_pallet()
    assert (model.spec.bin_length_mm, model.spec.bin_width_mm, model.spec.max_height_mm) == (1240, 840, 2400)
    assert model.accessibility_delta_mm == model.stand_hw_height_margin_gate_mm == 400
    assert model.orientation_mode == "planar+stand_hw"
    assert policy.config.stability_mode == "ratio+corners+settle"
    assert policy.config.min_support_ratio == .85
    assert policy._scheduler.config.time_budget_ms == 0
    assert PRESETS == before


def test_gate_and_forbidden_face_rejected_even_when_geometry_valid():
    box = Box(1, 600, 440, 350, 0, destination=1)
    model = production_policy()._new_pallet()
    for dims, name, family in [((350, 440, 600), "HWL", "stand_hw"),
                               ((600, 350, 440), "LHW", "stand_hl")]:
        p = Placement(-20, -20, 0, False, 0, *dims, box_id=1,
                      orientation_name=name, orientation_family=family)
        with pytest.raises(ValueError, match="Orientation/gate"):
            checked_preview(model, box, p)


def test_accessibility_replay_checks_edge_contacts():
    model = production_policy()._new_pallet()
    tall = Box(1, 500, 500, 500, 0, destination=1)
    p = model.preview_place(tall)
    model.commit_place(p)
    small = Box(2, 200, 200, 200, 0, destination=1)
    adjacent = Placement(480, -20, 0, False, 0, 200, 200, 200, box_id=2,
                         orientation_name="LWH", orientation_family="planar")
    # A final-state geometric validator cannot see this chronological violation.
    assert validate_pallet([asdict(p.placement), asdict(adjacent)], {1: (500, 500, 500), 2: (200, 200, 200)}) == []
    with pytest.raises(ValueError, match="ROBOT_ACCESS"):
        checked_preview(model, small, adjacent)
    assert checked_preview(model, small, replace(adjacent, x_mm=481)).feasible


def test_replay_support_stricter_for_standing():
    model = production_policy()._new_pallet()
    # Reach the actual gate using one isolated pillar, then a low base elsewhere.
    pillar = Box(1, 200, 200, 2100, 0, destination=1)
    pp = Placement(-20, -20, 0, False, 0, 200, 200, 2100, box_id=1,
                   orientation_name="LWH", orientation_family="planar")
    model.commit_place(checked_preview(model, pillar, pp))
    base = Box(2, 180, 300, 200, 0, destination=1)
    bp = Placement(400, 400, 0, False, 0, 180, 300, 200, box_id=2,
                   orientation_name="LWH", orientation_family="planar")
    model.commit_place(checked_preview(model, base, bp))
    box = Box(3, 500, 300, 200, 0, destination=1)
    standing = Placement(400, 400, 200, False, 0, 200, 300, 500, box_id=3,
                          orientation_name="HWL", orientation_family="stand_hw")
    with pytest.raises(ValueError, match="SUPPORT_RATIO"):
        checked_preview(model, box, standing)  # 90%, production requires 92%.


def test_production_expansion_matches_actual_scheduler():
    policy = production_policy()
    model = policy._new_pallet()
    remaining = [Box(i, l, w, h, 0, destination=1) for i, (l, w, h) in enumerate(
        [(600, 440, 350), (500, 390, 200), (600, 440, 350), (400, 300, 280), (300, 250, 100)], 1)]
    while remaining:
        plan = policy._scheduler.choose_action(SchedulerSimState(0, {1: remaining}, {1: model}, set()))
        candidate = production_candidates(model, remaining, policy._scheduler)[0]
        assert candidate[1].box_id == plan.box_id
        assert candidate[2].placement == plan.preview.placement
        model.commit_place(plan.preview)
        remaining.pop(plan.buffer_index)


def test_complete_inventory_metric_and_replay():
    result = decode_order(cubes(), list(range(1, 14)))
    assert result["n_pallets_total"] == 2
    assert result["n_closed"] == 1
    # Production's location choice builds a tower here: 7 closed, 6 on END.
    assert result["kpi"] == pytest.approx(7 / 12)
    assert result["kpi_all_pallets"] == pytest.approx(13 / 24)
    assert result["n_violations"] == result["n_production_violations"] == 0
    with pytest.raises(ValueError):
        summarize(result["pallets"][:-1], cubes())
    with pytest.raises(ValueError):
        decode_order(cubes(), [1, 2, 3, 4, 4])


def test_beam_resume_and_config_mismatch(tmp_path):
    checkpoint = tmp_path / "beam.json"
    first = beam_search(cubes(), top_m=3, checkpoint=checkpoint)
    resumed = beam_search(cubes(), top_m=3, checkpoint=checkpoint, resume=True)
    for key in ("kpi", "sequence", "pallets"):
        assert first[key] == resumed[key]
    with pytest.raises(ValueError, match="does not match"):
        beam_search(cubes(), top_m=4, checkpoint=checkpoint, resume=True)
    with pytest.raises(ValueError, match="width"):
        beam_search(cubes(), width=8)


def test_order_resume_preserves_rng_and_best(tmp_path):
    checkpoint = tmp_path / "order.json"
    order_search(cubes(), evaluations=2, seed=42, checkpoint=checkpoint)
    resumed = order_search(cubes(), evaluations=5, seed=42, checkpoint=checkpoint, resume=True)
    uninterrupted = order_search(cubes(), evaluations=5, seed=42)
    assert resumed["input_order"] == uninterrupted["input_order"]
    assert resumed["sequence"] == uninterrupted["sequence"]
    assert [h["sequence_sha256"] for h in resumed["history"]] == [h["sequence_sha256"] for h in uninterrupted["history"]]
    assert json.loads(checkpoint.read_text())["cpu_seconds"] > 0


def test_resume_interrupted_beam_frontier(tmp_path):
    checkpoint = tmp_path / "frontier.json"

    def interrupt(_):
        raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated"):
        beam_search(cubes(), checkpoint=checkpoint, progress=interrupt)
    assert json.loads(checkpoint.read_text())["beam"]
    resumed = beam_search(cubes(), checkpoint=checkpoint, resume=True)
    uninterrupted = beam_search(cubes())
    assert resumed["pallets"] == uninterrupted["pallets"]


def test_budget_completion_keeps_all_boxes_and_no_empty_pallet():
    result = beam_search(cubes(), cpu_seconds=0.000001)
    assert result["budget_hit"]
    assert result["boxes_placed"] == len(cubes())
    assert all(result["pallets"])
    assert result["n_violations"] == result["n_production_violations"] == 0


def test_debt_detects_low_neighbours_and_does_not_validate_geometry():
    low = Placement(-20, -20, 0, False, 0, 600, 400, 350)
    tall = replace(low, height_mm=1400)
    assert profile_metrics([low])["debt_mm3"] == 0
    assert profile_metrics([tall])["debt_mm3"] > 0


def test_alternative_locations_pass_same_control_replay():
    policy = production_policy()
    model = policy._new_pallet()
    remaining = cubes()
    for _ in range(4):
        actions = list(geometric_candidates(model, remaining))
        assert actions
        for _, box, preview in actions:
            assert checked_preview(model, box, preview.placement).feasible
        idx, _, preview = actions[0]
        model.commit_place(preview)
        remaining = remaining[:idx] + remaining[idx + 1:]


def test_columns_known_pattern_with_actual_gate_and_resume(tmp_path):
    boxes = tuple(Box(i, 600, 440, 350, 0, destination=1) for i in range(21))
    checkpoint = tmp_path / "columns.json"

    def interrupt(_):
        raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated"):
        column_search(boxes, width=4, templates=(1,), checkpoint=checkpoint, progress=interrupt)
    result = column_search(boxes, width=4, templates=(1,), checkpoint=checkpoint, resume=True)
    assert [len(p) for p in result["pallets"]] == [20, 1]
    assert result["n_violations"] == result["n_production_violations"] == 0
    peak = 0
    standing = 0
    for item in result["pallets"][0]:
        if item["orientation_family"] == "stand_hw":
            assert peak >= 2000
            standing += 1
        peak = max(peak, item["z_mm"] + item["height_mm"])
    assert standing == 8
    uninterrupted = column_search(boxes, width=4, templates=(1,))
    assert result["pallets"] == uninterrupted["pallets"]


def test_columns_timeout_finishes_and_empty_template_fails():
    result = column_search(cubes(), cpu_seconds=.000001)
    assert result["budget_hit"]
    assert result["boxes_placed"] == len(cubes())
    assert result["n_production_violations"] == 0
    with pytest.raises(ValueError, match="templates"):
        column_search(cubes(), templates=())
