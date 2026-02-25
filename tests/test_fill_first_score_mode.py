from __future__ import annotations

from types import SimpleNamespace

import pytest

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerV1


def _preview_for_layer(layer_id: int) -> PlacementPreview:
    return PlacementPreview(
        feasible=True,
        placement=Placement(
            x_mm=0,
            y_mm=0,
            z_mm=0,
            rot90=False,
            layer_id=int(layer_id),
            length_mm=200,
            width_mm=200,
            height_mm=200,
            box_id=1,
        ),
        packing_gain=1.0,
        fragmentation=0.1,
        height_after_mm=200,
        infeasible_reason=None,
        score_adjustment=0.0,
        debug=None,
    )


def _pallet_with_active_fill(*, used_area: int, area: int) -> object:
    layer_bin = SimpleNamespace(used_area=int(used_area), area=int(area))
    layer = SimpleNamespace(bin=layer_bin)
    return SimpleNamespace(layers=[layer])


def test_scheduler_fill_first_then_height_penalizes_opening_new_layer() -> None:
    scheduler = SchedulerV1(
        SchedulerConfig(
            score_mode="fill_first_then_height",
            fill_gate=0.60,
            open_layer_penalty=5.0,
        )
    )
    box = Box(box_id=1, length_mm=200, width_mm=200, height_mm=200, timestamp=0.0, destination=1)
    pallet = _pallet_with_active_fill(used_area=2500, area=10000)  # active_fill=0.25

    same_layer_terms = scheduler._score_candidate(
        now=0.0,
        box=box,
        idx=0,
        preview=_preview_for_layer(layer_id=0),
        pallet=pallet,  # type: ignore[arg-type]
        max_priority=0.0,
        height_after_mm=200,
    )
    open_new_layer_terms = scheduler._score_candidate(
        now=0.0,
        box=box,
        idx=0,
        preview=_preview_for_layer(layer_id=1),
        pallet=pallet,  # type: ignore[arg-type]
        max_priority=0.0,
        height_after_mm=200,
    )

    expected_penalty = 5.0 * (0.60 - 0.25)
    assert same_layer_terms.scalar_score > open_new_layer_terms.scalar_score
    assert (same_layer_terms.scalar_score - open_new_layer_terms.scalar_score) == pytest.approx(expected_penalty)


def test_fill_first_config_clamps_values() -> None:
    config = SchedulerConfig(score_mode="fill_first_then_height", fill_gate=2.5, open_layer_penalty=-3.0)
    assert config.fill_gate == 1.0
    assert config.open_layer_penalty == 0.0
