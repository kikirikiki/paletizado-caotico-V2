from __future__ import annotations

from types import MethodType

import pytest

from palca.domain.box import Box
from palca.domain.placement import Placement
from palca.packer.maxrects2d import MaxRectsCandidate
from palca.packer.pallet_model import PalletModel, _LayerCandidate
from palca.packer.scoring import ScoringWeights


def _build_candidate(model: PalletModel, *, is_new_layer: bool, box_id: int) -> _LayerCandidate:
    new_layer_penalty = float(model.scoring_weights.new_layer_penalty_ratio) if is_new_layer else 0.0
    placement = Placement(
        x_mm=0,
        y_mm=0,
        z_mm=0,
        rot90=False,
        layer_id=0,
        length_mm=10,
        width_mm=10,
        height_mm=5,
        box_id=box_id,
    )
    return _LayerCandidate(
        layer_id=0,
        is_new_layer=is_new_layer,
        candidate=MaxRectsCandidate(x=0, y=0, w=10, h=10, score=(0,)),
        rot90=False,
        length_mm=10,
        width_mm=10,
        z_mm=0,
        next_height_mm=5,
        height_after_mm=5,
        packing_gain=1.0,
        fragmentation=0.2,
        score_delta=0.0,
        spread_reward=0.0,
        new_layer_penalty=new_layer_penalty,
        height_increase_penalty=0.0,
        placement=placement,
        debug={"is_new_layer": is_new_layer},
    )


@pytest.mark.parametrize(
    ("penalty_ratio", "expected_box_id", "expected_is_new_layer"),
    [
        (0.2, 1, False),
        (0.0, 2, True),
    ],
)
def test_preview_selection_applies_new_layer_penalty_and_keeps_tie_order(
    penalty_ratio: float,
    expected_box_id: int,
    expected_is_new_layer: bool,
) -> None:
    model = PalletModel(scoring_weights=ScoringWeights(new_layer_penalty_ratio=penalty_ratio))

    def _preview_in_layer_stub(
        self: PalletModel,
        layer,
        box,
        orientations,
        *,
        is_new_layer: bool,
        budget=None,
    ):
        cand_new_layer = _build_candidate(self, is_new_layer=True, box_id=2)
        cand_existing_layer = _build_candidate(self, is_new_layer=False, box_id=1)
        # Base order: new-layer candidate first.
        return [cand_new_layer, cand_existing_layer], 0, [], False

    setattr(model, "_preview_in_layer", MethodType(_preview_in_layer_stub, model))

    preview = model.preview_place(Box(box_id=99, length_mm=10, width_mm=10, height_mm=5, timestamp=0.0))

    assert preview.feasible
    assert preview.placement is not None
    assert int(preview.placement.box_id) == expected_box_id
    assert bool(preview.debug["is_new_layer"]) is expected_is_new_layer
