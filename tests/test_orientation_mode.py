from __future__ import annotations

from typing import Any

import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.integration.kpi_hooks import aggregate_pallet_kpis
from palca.packer.layer import LayerState
from palca.packer.maxrects2d import MaxRects2D
from palca.packer.pallet_model import (
    ORIENTATION_MODE_PLANAR,
    ORIENTATION_MODE_PLANAR_STAND_HW,
    ORIENTATION_MODE_PLANAR_STAND_HW_CANTO,
    PalletModel,
    _orientation_variants_for_mode,
    orientation_dims_for_mode,
)
from sim import run as sim_run


def test_orientation_dims_planar_vs_extended() -> None:
    dims = orientation_dims_for_mode(
        400,
        300,
        200,
        mode=ORIENTATION_MODE_PLANAR,
        allow_rotate=True,
    )
    assert dims == [(400, 300, 200), (300, 400, 200)]

    extended = orientation_dims_for_mode(
        400,
        300,
        200,
        mode=ORIENTATION_MODE_PLANAR_STAND_HW,
        allow_rotate=True,
    )
    assert extended == [(400, 300, 200), (300, 400, 200), (200, 300, 400), (300, 200, 400)]


@pytest.mark.parametrize("allow_rotate", [False, True])
def test_canto_exact_variants_and_old_modes(allow_rotate):
    expected = [
        ("LWH", (600, 400, 300), "planar", False),
        ("WLH", (400, 600, 300), "planar", True),
        ("HWL", (300, 400, 600), "stand_hw", False),
        ("WHL", (400, 300, 600), "stand_hw", True),
        ("LHW", (600, 300, 400), "canto", False),
        ("HLW", (300, 600, 400), "canto", True),
    ]
    for mode, count in [(ORIENTATION_MODE_PLANAR, 2),
                        (ORIENTATION_MODE_PLANAR_STAND_HW, 4),
                        (ORIENTATION_MODE_PLANAR_STAND_HW_CANTO, 6)]:
        variants = _orientation_variants_for_mode(
            600, 400, 300, mode=mode, allow_rotate=allow_rotate)
        assert [(v.name, (v.length_mm, v.width_mm, v.height_mm), v.family, v.rot90)
                for v in variants] == [v for v in expected[:count] if allow_rotate or not v[3]]


@pytest.mark.parametrize("dims,count", [((600, 400, 400), 3), ((400, 400, 400), 1)])
def test_canto_deduplicates_geometry(dims, count):
    variants = orientation_dims_for_mode(
        *dims, mode=ORIENTATION_MODE_PLANAR_STAND_HW_CANTO, allow_rotate=True)
    assert len(variants) == len(set(variants)) == count


@pytest.mark.parametrize("gate,expected", [(400, {"planar", "canto"}),
                                         (2400, {"planar", "stand_hw", "canto"})])
def test_canto_is_not_height_gated(gate, expected):
    model = PalletModel(orientation_mode=ORIENTATION_MODE_PLANAR_STAND_HW_CANTO,
                        stand_hw_height_margin_gate_mm=gate)
    assert {v.family for v in model._orientations(600, 400, 300)} == expected
    assert model.stats.stand_hw_gate_blocks_total == (gate == 400)
    assert model.stats.stand_hw_gate_allows_total == (gate == 2400)
    args = sim_run.build_parser().parse_args([
        "--excel", "dummy.xlsx", "--orientation-mode", ORIENTATION_MODE_PLANAR_STAND_HW_CANTO])
    assert args.orientation_mode == ORIENTATION_MODE_PLANAR_STAND_HW_CANTO


def test_canto_placement_and_telemetry():
    spec = PalletSpec(length_mm=1200, width_mm=800, max_height_mm=2400)
    box = Box(box_id=1, length_mm=1000, width_mm=1300, height_mm=700,
              timestamp=0.0, destination=1)
    model = PalletModel(spec=spec, orientation_mode=ORIENTATION_MODE_PLANAR_STAND_HW_CANTO)
    preview = model.preview_place(box)
    assert preview.feasible
    assert preview.placement.orientation_family == "canto"
    assert preview.placement.orientation_name == "LHW"
    model.commit_place(preview)
    kpis = aggregate_pallet_kpis({1: [model]})
    assert kpis["orientation_counts"] == {"planar": 0, "stand_hw": 0, "canto": 1}
    assert kpis["orientation_counts_by_dest"][1]["canto"] == 1
    assert not PalletModel(spec=spec, orientation_mode=ORIENTATION_MODE_PLANAR_STAND_HW).preview_place(box).feasible


def test_orientation_mode_parser_and_policy_wiring(monkeypatch: Any) -> None:
    parser = sim_run.build_parser()
    default_args = parser.parse_args(["--excel", "dummy.xlsx"])
    assert str(default_args.orientation_mode) == ORIENTATION_MODE_PLANAR
    assert int(default_args.max_pallets) == 0

    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--policy",
            "palca",
            "--orientation-mode",
            ORIENTATION_MODE_PLANAR_STAND_HW,
            "--max-pallets",
            "1",
        ]
    )

    captured: dict[str, Any] = {}

    class DummyPolicyPackerScheduler:
        @classmethod
        def from_defaults(cls, **kwargs: Any) -> object:
            captured.update(kwargs)
            return object()

    class DummyResult:
        def to_dict(self) -> dict[str, object]:
            return {"processed_boxes": 0, "total_boxes": 0, "pallet_kpis": {}}

    monkeypatch.setattr(sim_run, "load_arrivals", lambda *args, **kwargs: [])
    monkeypatch.setattr(sim_run, "simulate", lambda *args, **kwargs: DummyResult())

    import palca.integration.policy_packer_sched as integration_mod

    monkeypatch.setattr(integration_mod, "PolicyPackerScheduler", DummyPolicyPackerScheduler)

    payload = sim_run.run_simulation(
        excel_path=str(args.excel),
        model="M1",
        n_per_pallet=24,
        t_pick_place=14.0,
        staging_cap=0,
        out_path=None,
        policy=str(args.policy),
        lookahead_k=1,
        orientation_mode=str(args.orientation_mode),
    )

    assert captured.get("orientation_mode") == ORIENTATION_MODE_PLANAR_STAND_HW
    assert payload["params"]["orientation_mode"] == ORIENTATION_MODE_PLANAR_STAND_HW


def test_max_pallets_requires_force_destination() -> None:
    with pytest.raises(ValueError, match="max_pallets requiere force_destination"):
        sim_run.run_simulation(
            excel_path="dummy.xlsx",
            model="M1",
            n_per_pallet=24,
            t_pick_place=14.0,
            staging_cap=0,
            out_path=None,
            max_pallets=1,
        )


def test_orientation_mode_stand_hw_metrics() -> None:
    spec = PalletSpec(length_mm=1200, width_mm=800, max_height_mm=2400)
    box = Box(
        box_id=1,
        length_mm=1300,
        width_mm=700,
        height_mm=1000,
        timestamp=0.0,
        destination=1,
    )

    planar_model = PalletModel(spec=spec, orientation_mode=ORIENTATION_MODE_PLANAR)
    planar_preview = planar_model.preview_place(box)
    assert not planar_preview.feasible
    assert planar_preview.infeasible_reason == "OVERSIZE"

    extended_model = PalletModel(
        spec=spec,
        orientation_mode=ORIENTATION_MODE_PLANAR_STAND_HW,
        stand_hw_height_margin_gate_mm=3000,
    )
    preview = extended_model.preview_place(box)
    assert preview.feasible
    assert preview.placement is not None
    assert str(preview.placement.orientation_family) == "stand_hw"

    extended_model.commit_place(preview)
    kpis = aggregate_pallet_kpis({1: [extended_model]})

    orientation_counts = kpis["orientation_counts"]
    stand_hw_used_by_dest = kpis["stand_hw_used_by_dest"]
    assert isinstance(orientation_counts, dict)
    assert isinstance(stand_hw_used_by_dest, dict)
    assert int(orientation_counts.get("stand_hw", 0)) == 1
    assert int(orientation_counts.get("planar", 0)) == 0
    assert int(kpis.get("stand_hw_used_total", 0)) == 1
    assert int(stand_hw_used_by_dest.get(1, 0)) == 1
    assert int(kpis.get("stand_hw_gate_mm", 0)) == 3000
    assert int(kpis.get("stand_hw_gate_allows_total", 0)) == 1
    assert int(kpis.get("stand_hw_gate_blocks_total", 0)) == 0


def test_stand_hw_height_margin_gating() -> None:
    spec = PalletSpec(length_mm=1200, width_mm=800, max_height_mm=1200)
    gate_mm = 400

    high_margin_model = PalletModel(
        spec=spec,
        orientation_mode=ORIENTATION_MODE_PLANAR_STAND_HW,
        stand_hw_height_margin_gate_mm=gate_mm,
    )
    high_margin_families = {
        variant.family
        for variant in high_margin_model._orientations(400, 300, 200)  # noqa: SLF001
    }
    assert high_margin_families == {"planar"}
    assert high_margin_model.stats.stand_hw_gate_blocks_total == 1
    assert high_margin_model.stats.stand_hw_gate_allows_total == 0

    low_margin_model = PalletModel(
        spec=spec,
        orientation_mode=ORIENTATION_MODE_PLANAR_STAND_HW,
        stand_hw_height_margin_gate_mm=gate_mm,
    )
    low_margin_model.layers = [
        LayerState(
            layer_id=0,
            z_mm=0,
            bin=MaxRects2D(spec.bin_length_mm, spec.bin_width_mm, heuristic="baf"),
            height_mm=1000,
        )
    ]
    low_margin_families = {
        variant.family
        for variant in low_margin_model._orientations(400, 300, 200)  # noqa: SLF001
    }
    assert "stand_hw" in low_margin_families
    assert low_margin_model.stats.stand_hw_gate_blocks_total == 0
    assert low_margin_model.stats.stand_hw_gate_allows_total == 1
