from __future__ import annotations

from palca.packer.maxrects2d import MaxRects2D


def test_maxrects_place_reduces_free_area() -> None:
    bin_2d = MaxRects2D(10, 10, heuristic="baf")
    cand = bin_2d.find_candidate(4, 6)
    assert cand is not None
    bin_2d.place(cand)
    free_area = sum(rect.w * rect.h for rect in bin_2d.free_rects)
    assert free_area == 100 - 24


def test_maxrects_rejects_oversize() -> None:
    bin_2d = MaxRects2D(5, 5, heuristic="bssf")
    cand = bin_2d.find_candidate(6, 4)
    assert cand is None
