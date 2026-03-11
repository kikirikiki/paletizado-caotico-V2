from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


def _box(box_id: int, length_mm: int, width_mm: int, height_mm: int = 10) -> Box:
    return Box(
        box_id=int(box_id),
        length_mm=int(length_mm),
        width_mm=int(width_mm),
        height_mm=int(height_mm),
        timestamp=0.0,
        destination=1,
    )


def _sim_state(*, pallet: PalletModel, boxes: list[Box]) -> SchedulerSimState:
    return SchedulerSimState(
        now=0.0,
        ramps={1: list(boxes)},
        pallets={1: pallet},
        pallet_blocked=set(),
        remaining_total=len(boxes),
    )


def test_early_layer_fillability_rerank_changes_choice_when_residual_is_better() -> None:
    spec = PalletSpec(length_mm=100, width_mm=100, max_height_mm=500, overhang_mm=0)
    pallet = PalletModel(spec=spec, heuristic="baf", stacking_mode="layers")
    boxes = [_box(1, 70, 70), _box(2, 50, 50)]

    baseline = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=False,
            enable_early_layer_fillability_rerank=False,
        )
    )
    baseline_plan = baseline.choose_action(_sim_state(pallet=pallet, boxes=boxes))
    assert baseline_plan is not None
    assert int(baseline_plan.box_id) == 1

    rerank = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=False,
            enable_early_layer_fillability_rerank=True,
            early_layer_fillability_steps=4,
            early_layer_fillability_topk=2,
        )
    )
    rerank_plan = rerank.choose_action(_sim_state(pallet=pallet, boxes=boxes))
    assert rerank_plan is not None
    assert int(rerank_plan.box_id) == 2
    assert int(rerank.early_layer_rerank_invocations) == 1
    assert int(rerank.early_layer_rerank_changed_choice_count) == 1


def test_early_layer_fillability_rerank_keeps_choice_when_no_better_option() -> None:
    spec = PalletSpec(length_mm=100, width_mm=100, max_height_mm=500, overhang_mm=0)
    pallet = PalletModel(spec=spec, heuristic="baf", stacking_mode="layers")
    boxes = [_box(1, 70, 70), _box(2, 70, 70)]

    rerank = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=False,
            enable_early_layer_fillability_rerank=True,
            early_layer_fillability_steps=4,
            early_layer_fillability_topk=2,
        )
    )
    plan = rerank.choose_action(_sim_state(pallet=pallet, boxes=boxes))
    assert plan is not None
    assert int(plan.box_id) == 1
    assert int(rerank.early_layer_rerank_invocations) == 1
    assert int(rerank.early_layer_rerank_changed_choice_count) == 0


def test_early_layer_fillability_rerank_not_applied_outside_early_window() -> None:
    spec = PalletSpec(length_mm=100, width_mm=100, max_height_mm=500, overhang_mm=0)
    pallet = PalletModel(spec=spec, heuristic="baf", stacking_mode="layers")
    for idx in range(4):
        seed_box = _box(100 + idx, 10, 10)
        preview = pallet.preview_place(seed_box)
        assert preview.feasible
        pallet.commit_place(preview)

    rerank = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=False,
            enable_early_layer_fillability_rerank=True,
            early_layer_fillability_steps=4,
            early_layer_fillability_topk=2,
        )
    )
    plan = rerank.choose_action(_sim_state(pallet=pallet, boxes=[_box(1, 70, 70), _box(2, 50, 50)]))
    assert plan is not None
    assert int(rerank.early_layer_rerank_invocations) == 0
    assert int(rerank.early_layer_rerank_changed_choice_count) == 0
