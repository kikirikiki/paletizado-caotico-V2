from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel
from palca.planner.beam_pick import BeamPickConfig, BeamPickPlanner


def _box(box_id: int, length_mm: int, width_mm: int, height_mm: int) -> Box:
    return Box(
        box_id=box_id,
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        timestamp=0.0,
        destination=1,
    )


def test_beam_pick_prefers_first_choice_with_more_future_placements() -> None:
    pallet = PalletModel(
        spec=PalletSpec(length_mm=4, width_mm=4, max_height_mm=4, overhang_mm=0),
        heuristic="baf",
    )
    # idx=0 ocupa todo el layer y bloquea; idx=1 permite colocar otra caja.
    window = [
        _box(100, 4, 4, 3),
        _box(200, 2, 4, 2),
    ]
    upstream = [_box(300, 2, 4, 2)]

    result = BeamPickPlanner.plan(
        pallet=pallet,
        window=window,
        pick_window=2,
        upstream=upstream,
        cfg=BeamPickConfig(beam_width=8, beam_depth=3, beam_max_expansions=200, beam_time_budget_ms=200),
    )

    assert result.buffer_index == 1
    assert result.box_id == 200
    assert result.preview is not None
    assert len(result.best_sequence) >= 2


def test_beam_pick_uses_height_tie_breaker() -> None:
    pallet = PalletModel(
        spec=PalletSpec(length_mm=4, width_mm=4, max_height_mm=4, overhang_mm=0),
        heuristic="baf",
    )
    window = [
        _box(10, 4, 4, 3),
        _box(20, 4, 4, 2),
    ]

    result = BeamPickPlanner.plan(
        pallet=pallet,
        window=window,
        pick_window=2,
        cfg=BeamPickConfig(beam_width=6, beam_depth=1, beam_max_expansions=50, beam_time_budget_ms=200),
    )

    assert result.buffer_index == 1
    assert result.box_id == 20


def test_beam_pick_returns_best_so_far_when_budget_hits() -> None:
    pallet = PalletModel(
        spec=PalletSpec(length_mm=4, width_mm=4, max_height_mm=4, overhang_mm=0),
        heuristic="baf",
    )
    window = [
        _box(10, 4, 4, 3),
        _box(20, 4, 4, 2),
    ]

    result = BeamPickPlanner.plan(
        pallet=pallet,
        window=window,
        pick_window=2,
        cfg=BeamPickConfig(beam_width=6, beam_depth=4, beam_max_expansions=1, beam_time_budget_ms=200),
    )

    assert result.buffer_index is not None
    assert result.preview is not None
    assert int(result.debug.get("expansions_used", 0)) >= 1
