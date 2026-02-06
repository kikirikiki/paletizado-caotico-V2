from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.domain.placement import Placement
from palca.packer.controls import ControlConfig, StabilityConfig
from palca.packer.layer import LayerState
from palca.packer.maxrects2d import MaxRects2D, MaxRectsCandidate
from palca.packer.pallet_model import PalletModel


def _register_placement(pallet: PalletModel, layer: LayerState, placement: Placement) -> None:
    cand = MaxRectsCandidate(
        x=placement.x_mm - pallet.spec.offset_mm,
        y=placement.y_mm - pallet.spec.offset_mm,
        w=placement.length_mm,
        h=placement.width_mm,
        score=(0,),
    )
    assert layer.bin.place(cand)
    layer.height_mm = max(layer.height_mm, placement.height_mm)
    pallet.placements.append(placement)


def test_stability_candidates_include_reject_reason() -> None:
    spec = PalletSpec(length_mm=700, width_mm=500, max_height_mm=1000)
    control_cfg = ControlConfig(
        stability=StabilityConfig(mode="ratio+corners", min_support_ratio=0.75, eps_mm=0.01)
    )
    pallet = PalletModel(spec=spec, control_config=control_cfg)

    layer = LayerState(
        layer_id=0,
        z_mm=0,
        bin=MaxRects2D(spec.bin_length_mm, spec.bin_width_mm, heuristic="baf"),
    )
    pallet.layers = [layer]

    placements = [
        Placement(
            x_mm=0,
            y_mm=0,
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=200,
            width_mm=200,
            height_mm=100,
            box_id=1,
            weight_kg=1.0,
        ),
        Placement(
            x_mm=200,
            y_mm=0,
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=500,
            width_mm=200,
            height_mm=50,
            box_id=2,
            weight_kg=1.0,
        ),
        Placement(
            x_mm=0,
            y_mm=200,
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=700,
            width_mm=300,
            height_mm=50,
            box_id=3,
            weight_kg=1.0,
        ),
    ]
    for placement in placements:
        _register_placement(pallet, layer, placement)

    box = Box(box_id=99, length_mm=655, width_mm=440, height_mm=360, timestamp=0.0)
    preview = pallet.preview_place(box)

    assert not preview.feasible
    assert preview.infeasible_reason == "STABILITY"
    assert preview.debug is not None
    stability_candidates = preview.debug.get("stability_candidates")
    assert isinstance(stability_candidates, list)
    assert stability_candidates
    assert any(
        cand.get("reject_reason") in {"SUPPORT_RATIO", "CORNER_SUPPORT"}
        for cand in stability_candidates
        if isinstance(cand, dict)
    )
