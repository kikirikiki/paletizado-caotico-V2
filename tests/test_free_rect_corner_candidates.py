from __future__ import annotations

from palca.packer.maxrects2d import Rect
from palca.packer.pallet_model import _free_rect_corner_candidates


def test_free_rect_corner_candidates_returns_exact_four_corners() -> None:
    candidates, keys = _free_rect_corner_candidates([Rect(10, 20, 100, 80)], 40, 30)

    assert len(candidates) == 4
    assert {(cand.x, cand.y) for cand in candidates} == {(10, 20), (70, 20), (10, 70), (70, 70)}
    assert keys == {(10, 20, 40, 30), (70, 20, 40, 30), (10, 70, 40, 30), (70, 70, 40, 30)}


def test_free_rect_corner_candidates_top_n_and_fit_filter() -> None:
    free_rects = [
        Rect(0, 0, 300, 220),     # giant
        Rect(600, 100, 160, 100), # medium
        Rect(20, 20, 30, 20),     # too small for 40x30
    ]

    candidates, keys = _free_rect_corner_candidates(free_rects, 40, 30, top_n=1)

    assert len(candidates) == 4
    assert len(keys) == 4
    assert {(cand.x, cand.y) for cand in candidates} == {(0, 0), (260, 0), (0, 190), (260, 190)}
