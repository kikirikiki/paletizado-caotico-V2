from __future__ import annotations

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.packer.scoring import ScoringWeights
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerV1


class _DummyPallet:
    def __init__(self, largest_free_rect_weight: float) -> None:
        self.scoring_weights = ScoringWeights(largest_free_rect_weight=largest_free_rect_weight)


def _preview_with_largest_rect(largest_area_after_mm2: int) -> PlacementPreview:
    return PlacementPreview(
        feasible=True,
        placement=Placement(
            x_mm=0,
            y_mm=0,
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=100,
            width_mm=100,
            height_mm=100,
            box_id=1,
        ),
        packing_gain=1.0,
        fragmentation=0.0,
        free_area_after_mm2=1000,
        largest_free_rect_area_after_mm2=int(largest_area_after_mm2),
    )


def test_largest_free_rect_weight_prefers_higher_ratio() -> None:
    scheduler = SchedulerV1(SchedulerConfig())
    pallet = _DummyPallet(largest_free_rect_weight=1.0)
    box = Box(box_id=1, length_mm=100, width_mm=100, height_mm=100, timestamp=0.0, destination=1)

    terms_a = scheduler._score_candidate(
        now=0.0,
        box=box,
        idx=0,
        preview=_preview_with_largest_rect(800),
        pallet=pallet,
        max_priority=0.0,
        height_after_mm=100,
    )
    terms_b = scheduler._score_candidate(
        now=0.0,
        box=box,
        idx=0,
        preview=_preview_with_largest_rect(200),
        pallet=pallet,
        max_priority=0.0,
        height_after_mm=100,
    )

    assert terms_a.largest_free_rect_ratio_after == 0.8
    assert terms_b.largest_free_rect_ratio_after == 0.2
    assert terms_a.scalar_score > terms_b.scalar_score


def test_largest_free_rect_weight_zero_preserves_baseline_tie() -> None:
    scheduler = SchedulerV1(SchedulerConfig())
    pallet = _DummyPallet(largest_free_rect_weight=0.0)
    box = Box(box_id=1, length_mm=100, width_mm=100, height_mm=100, timestamp=0.0, destination=1)

    terms_a = scheduler._score_candidate(
        now=0.0,
        box=box,
        idx=0,
        preview=_preview_with_largest_rect(800),
        pallet=pallet,
        max_priority=0.0,
        height_after_mm=100,
    )
    terms_b = scheduler._score_candidate(
        now=0.0,
        box=box,
        idx=0,
        preview=_preview_with_largest_rect(200),
        pallet=pallet,
        max_priority=0.0,
        height_after_mm=100,
    )

    assert terms_a.largest_free_rect_penalty == 0.0
    assert terms_b.largest_free_rect_penalty == 0.0
    assert terms_a.scalar_score == terms_b.scalar_score
