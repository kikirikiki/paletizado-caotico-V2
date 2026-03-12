from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


def _build_layer_opening_state() -> SchedulerSimState:
    spec = PalletSpec(length_mm=10, width_mm=10, max_height_mm=100, overhang_mm=0)
    pallet = PalletModel(spec=spec, heuristic="baf")

    base = Box(
        box_id=100,
        length_mm=10,
        width_mm=10,
        height_mm=10,
        timestamp=0.0,
        destination=1,
    )
    base_preview = pallet.preview_place(base)
    assert base_preview.feasible
    pallet.commit_place(base_preview)

    # En apertura de capa: 6x10 deja un hueco 4x10 (poison); 5x10 permite continuidad 5x10.
    box_6x10 = Box(box_id=1, length_mm=6, width_mm=10, height_mm=10, timestamp=0.0, destination=1)
    box_5x10_a = Box(box_id=2, length_mm=5, width_mm=10, height_mm=10, timestamp=0.0, destination=1)
    box_5x10_b = Box(box_id=3, length_mm=5, width_mm=10, height_mm=10, timestamp=0.0, destination=1)

    return SchedulerSimState(
        now=0.0,
        ramps={1: [box_6x10, box_5x10_a, box_5x10_b]},
        pallets={1: pallet},
        pallet_blocked=set(),
        remaining_total=3,
    )


def test_human_like_layer_opener_prefers_fillable_early_pattern() -> None:
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=3,
            micro_plan_enabled=False,
            human_like_layer_opener_enabled=True,
            human_like_layer_opener_prefix_len=2,
            human_like_layer_opener_candidate_cap=3,
            human_like_layer_opener_poison_penalty_weight=1.0,
            human_like_layer_opener_closure_weight=1.0,
            human_like_layer_opener_fragmentation_weight=1.0,
        )
    )
    plan = scheduler.choose_action(_build_layer_opening_state())

    assert plan is not None
    assert int(plan.box_id) in {2, 3}
    assert int(scheduler.human_like_layer_opener_applied) == 1
    assert int(scheduler.human_like_layer_opener_new_layer_applied) == 1
    assert float(scheduler.last_eval_stats.get("human_like_layer_opener_selected_layer_closure_score_mean", 0.0)) > 0.0


def test_human_like_layer_opener_flag_off_preserves_baseline_choice() -> None:
    baseline = SchedulerV1(
        SchedulerConfig(
            lookahead_k=3,
            micro_plan_enabled=False,
        )
    )
    with_flag_off = SchedulerV1(
        SchedulerConfig(
            lookahead_k=3,
            micro_plan_enabled=False,
            human_like_layer_opener_enabled=False,
        )
    )
    baseline_plan = baseline.choose_action(_build_layer_opening_state())
    off_plan = with_flag_off.choose_action(_build_layer_opening_state())

    assert baseline_plan is not None
    assert off_plan is not None
    assert int(baseline_plan.box_id) == 1
    assert int(off_plan.box_id) == int(baseline_plan.box_id)
    assert int(with_flag_off.human_like_layer_opener_applied) == 0
