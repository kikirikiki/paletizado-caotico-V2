from __future__ import annotations

import pytest

from palca.packer.maxrects2d import MaxRects2D


@pytest.mark.parametrize("heuristic", ["baf", "bssf"])
def test_maxrects_future_capacity_tie_breaker(heuristic: str) -> None:
    bin_2d = MaxRects2D(1200, 800, heuristic=heuristic)

    cand1 = bin_2d.find_candidate(575, 430)
    assert cand1 is not None

    cand1_rot = bin_2d.find_candidate(430, 575)
    assert cand1_rot is not None

    cand1 = min((cand1, cand1_rot), key=lambda c: c.score)
    assert (cand1.w, cand1.h) == (430, 575)

    bin_2d.place(cand1)
    cand2 = bin_2d.find_candidate(605, 450)
    assert cand2 is not None
