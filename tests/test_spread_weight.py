from __future__ import annotations

from types import MethodType

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.domain.placement import Placement
from palca.packer.layer import LayerState
from palca.packer.maxrects2d import MaxRects2D, MaxRectsCandidate
from palca.packer.pallet_model import PalletModel, _LayerCandidate
from palca.packer.scoring import ScoringWeights


def _make_candidate(
    *,
    layer: LayerState,
    box: Box,
    x: int,
    y: int,
    score: tuple[int, ...],
    spread_dist: float,
    spread_weight: float,
) -> _LayerCandidate:
    w_mm = 20
    h_mm = 20
    z_mm = int(layer.z_mm)
    reward = float(spread_weight) * float(spread_dist)
    placement = Placement(
        x_mm=int(x),
        y_mm=int(y),
        z_mm=z_mm,
        rot90=False,
        layer_id=int(layer.layer_id),
        length_mm=w_mm,
        width_mm=h_mm,
        height_mm=10,
        box_id=box.box_id,
    )
    return _LayerCandidate(
        layer_id=int(layer.layer_id),
        is_new_layer=False,
        candidate=MaxRectsCandidate(x=int(x), y=int(y), w=w_mm, h=h_mm, score=score),
        rot90=False,
        length_mm=w_mm,
        width_mm=h_mm,
        z_mm=z_mm,
        next_height_mm=10,
        height_after_mm=10,
        packing_gain=0.5,
        fragmentation=0.2,
        score_delta=0.0,
        spread_reward=reward,
        placement=placement,
        debug={"spread_dist": float(spread_dist), "spread_reward": float(reward)},
    )


def _install_preview_stub(model: PalletModel) -> None:
    def _preview_in_layer_stub(
        self: PalletModel,
        layer: LayerState,
        box: Box,
        orientations: list[object],
        *,
        is_new_layer: bool,
        budget: object | None = None,
    ) -> tuple[list[_LayerCandidate], int, list[tuple[float, dict[str, object]]], bool]:
        _ = (orientations, is_new_layer, budget)
        spread_weight = float(self.scoring_weights.spread_weight)
        center = _make_candidate(
            layer=layer,
            box=box,
            x=40,
            y=40,
            score=(0, 0, 0),
            spread_dist=0.0,
            spread_weight=spread_weight,
        )
        edge = _make_candidate(
            layer=layer,
            box=box,
            x=80,
            y=80,
            score=(10, 0, 0),
            spread_dist=1.6,
            spread_weight=spread_weight,
        )
        return [center, edge], 0, [], False

    setattr(model, "_preview_in_layer", MethodType(_preview_in_layer_stub, model))


def _build_model(spread_weight: float) -> PalletModel:
    spec = PalletSpec(length_mm=100, width_mm=100, max_height_mm=200, overhang_mm=0)
    model = PalletModel(spec=spec, scoring_weights=ScoringWeights(spread_weight=float(spread_weight)))
    model.layers = [
        LayerState(
            layer_id=0,
            z_mm=0,
            bin=MaxRects2D(spec.bin_length_mm, spec.bin_width_mm, heuristic="baf"),
            height_mm=0,
        )
    ]
    _install_preview_stub(model)
    return model


def test_spread_weight_positive_prefers_far_candidate() -> None:
    model = _build_model(spread_weight=0.6)
    box = Box(box_id=1, length_mm=20, width_mm=20, height_mm=10, timestamp=0.0)

    preview = model.preview_place(box)

    assert preview.feasible
    assert preview.placement is not None
    assert preview.placement.x_mm == 80
    assert preview.placement.y_mm == 80
    assert preview.debug is not None
    assert float(preview.debug.get("spread_dist", 0.0)) > 0.0
    assert float(preview.debug.get("spread_reward", 0.0)) > 0.0


def test_spread_weight_zero_keeps_maxrects_tie_break() -> None:
    model = _build_model(spread_weight=0.0)
    box = Box(box_id=2, length_mm=20, width_mm=20, height_mm=10, timestamp=0.0)

    preview = model.preview_place(box)

    assert preview.feasible
    assert preview.placement is not None
    assert preview.placement.x_mm == 40
    assert preview.placement.y_mm == 40
    assert preview.debug is not None
    assert float(preview.debug.get("spread_reward", 1.0)) == 0.0
