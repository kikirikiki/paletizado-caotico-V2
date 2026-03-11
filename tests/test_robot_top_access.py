from __future__ import annotations

from copy import deepcopy

from palca.domain.placement import Placement
from palca.integration.kpi_hooks import aggregate_pallet_kpis
from palca.integration.robot_top_access import compute_top_access_diagnostics
from palca.packer.pallet_model import PalletModel


def _placement(
    *,
    x_mm: int,
    y_mm: int,
    z_mm: int,
    length_mm: int,
    width_mm: int,
    height_mm: int,
    orientation_family: str = "planar",
) -> dict[str, object]:
    return {
        "x_mm": int(x_mm),
        "y_mm": int(y_mm),
        "z_mm": int(z_mm),
        "length_mm": int(length_mm),
        "width_mm": int(width_mm),
        "height_mm": int(height_mm),
        "orientation_family": str(orientation_family),
    }


def test_top_access_clean_case_accessible() -> None:
    seq = [
        _placement(x_mm=100, y_mm=100, z_mm=100, length_mm=200, width_mm=200, height_mm=100),
    ]
    metrics = compute_top_access_diagnostics(seq, bin_length_mm=800, bin_width_mm=800, insertion_margin_mm=40)
    per = metrics["per_placement"]
    assert len(per) == 1
    assert per[0]["accessibility_class"] == "accessible"
    assert per[0]["top_access_clear"] is True
    assert per[0]["blocked_reason_exact"] is None
    assert int(per[0]["entry_throat_bbox_l_mm"]) >= int(per[0]["length_mm"])
    assert int(per[0]["entry_throat_bbox_w_mm"]) >= int(per[0]["width_mm"])
    assert int(per[0]["entry_clearance_margin_mm"]) >= 0
    assert float(per[0]["marginal_severity_score"]) >= 0.0


def test_top_access_overhead_blocked_case() -> None:
    seq = [
        _placement(x_mm=100, y_mm=100, z_mm=80, length_mm=200, width_mm=200, height_mm=80),
        _placement(x_mm=100, y_mm=100, z_mm=50, length_mm=200, width_mm=200, height_mm=80),
    ]
    metrics = compute_top_access_diagnostics(seq, bin_length_mm=800, bin_width_mm=800, insertion_margin_mm=40)
    row = metrics["per_placement"][1]
    assert row["accessibility_class"] == "blocked"
    assert row["blocked_reason_exact"] == "overhead_blocked"
    assert int(row["overhead_blocked_height_mm"]) > 0
    assert int(row["vertical_access_margin_mm"]) < 0


def test_top_access_bbox_too_narrow_case_is_marginal_without_overhead() -> None:
    seq = [
        _placement(x_mm=100, y_mm=300, z_mm=150, length_mm=200, width_mm=40, height_mm=100),
        _placement(x_mm=100, y_mm=100, z_mm=100, length_mm=200, width_mm=200, height_mm=100),
    ]
    metrics = compute_top_access_diagnostics(seq, bin_length_mm=500, bin_width_mm=500, insertion_margin_mm=40)
    row = metrics["per_placement"][1]
    assert row["accessibility_class"] in ("marginal", "accessible")
    assert row["blocked_reason_exact"] is None
    assert int(row["overhead_blocked_height_mm"]) == 0


def test_top_access_bbox_too_short_case_is_marginal_without_overhead() -> None:
    seq = [
        _placement(x_mm=300, y_mm=100, z_mm=150, length_mm=40, width_mm=200, height_mm=100),
        _placement(x_mm=100, y_mm=100, z_mm=100, length_mm=200, width_mm=200, height_mm=100),
    ]
    metrics = compute_top_access_diagnostics(seq, bin_length_mm=500, bin_width_mm=500, insertion_margin_mm=40)
    row = metrics["per_placement"][1]
    assert row["accessibility_class"] in ("marginal", "accessible")
    assert row["blocked_reason_exact"] is None
    assert int(row["overhead_blocked_height_mm"]) == 0


def test_top_access_lateral_neighbors_without_vertical_invasion_not_blocked() -> None:
    seq = [
        _placement(x_mm=120, y_mm=160, z_mm=180, length_mm=80, width_mm=280, height_mm=120),
        _placement(x_mm=400, y_mm=160, z_mm=180, length_mm=80, width_mm=280, height_mm=120),
        _placement(x_mm=200, y_mm=200, z_mm=120, length_mm=200, width_mm=200, height_mm=100),
    ]
    metrics = compute_top_access_diagnostics(seq, bin_length_mm=700, bin_width_mm=700, insertion_margin_mm=40)
    row = metrics["per_placement"][2]
    assert row["accessibility_class"] in ("marginal", "accessible")
    assert row["blocked_reason_exact"] is None
    assert int(row["overhead_blocked_height_mm"]) == 0


def test_top_access_prism_free_but_throat_narrow_is_marginal() -> None:
    seq = [
        _placement(x_mm=100, y_mm=180, z_mm=260, length_mm=100, width_mm=160, height_mm=90),
        _placement(x_mm=300, y_mm=180, z_mm=260, length_mm=100, width_mm=160, height_mm=90),
        _placement(x_mm=200, y_mm=200, z_mm=200, length_mm=100, width_mm=100, height_mm=80),
    ]
    metrics = compute_top_access_diagnostics(seq, bin_length_mm=700, bin_width_mm=700, insertion_margin_mm=40)
    row = metrics["per_placement"][2]
    assert row["blocked_reason_exact"] is None
    assert row["accessibility_class"] == "marginal"
    assert int(row["entry_throat_min_clearance_mm"]) == 0
    assert int(row["entry_clearance_margin_mm"]) < 0


def test_top_access_near_pallet_edge_not_blocked_by_aux_area() -> None:
    seq = [
        _placement(x_mm=0, y_mm=0, z_mm=120, length_mm=200, width_mm=200, height_mm=100),
    ]
    metrics = compute_top_access_diagnostics(seq, bin_length_mm=500, bin_width_mm=500, insertion_margin_mm=40)
    row = metrics["per_placement"][0]
    assert row["accessibility_class"] in ("marginal", "accessible")
    assert row["blocked_reason_exact"] is None
    assert int(row["overhead_blocked_height_mm"]) == 0
    assert int(row["entry_throat_min_clearance_mm"]) >= 0


def test_top_access_real_vertical_prism_invasion_is_blocked() -> None:
    seq = [
        _placement(x_mm=240, y_mm=180, z_mm=280, length_mm=120, width_mm=120, height_mm=100),
        _placement(x_mm=200, y_mm=200, z_mm=220, length_mm=200, width_mm=200, height_mm=100),
    ]
    metrics = compute_top_access_diagnostics(seq, bin_length_mm=700, bin_width_mm=700, insertion_margin_mm=40)
    row = metrics["per_placement"][1]
    assert row["accessibility_class"] == "blocked"
    assert row["blocked_reason_exact"] == "overhead_blocked"
    assert int(row["overhead_blocked_height_mm"]) > 0


def test_top_access_late_stand_hw_case() -> None:
    seq = [
        _placement(x_mm=100, y_mm=100, z_mm=100, length_mm=200, width_mm=200, height_mm=100),
        _placement(x_mm=360, y_mm=100, z_mm=100, length_mm=120, width_mm=120, height_mm=100),
        _placement(x_mm=240, y_mm=120, z_mm=260, length_mm=100, width_mm=160, height_mm=80),
        _placement(
            x_mm=100,
            y_mm=100,
            z_mm=220,
            length_mm=200,
            width_mm=200,
            height_mm=100,
            orientation_family="stand_hw",
        ),
    ]
    metrics = compute_top_access_diagnostics(seq, bin_length_mm=800, bin_width_mm=800, insertion_margin_mm=40)
    assert int(metrics["blocked_count"]) == 1
    assert int(metrics["marginal_count"]) >= 0
    assert int(metrics["blocked_stand_hw"]) == 1
    assert int(metrics["first_blocked_step"]) == 3
    assert bool(metrics["issues_concentrated_at_end"]) is True
    late = metrics["per_placement"][3]
    assert late["accessibility_class"] == "blocked"
    assert late["blocked_reason_exact"] == "overhead_blocked"


def test_top_access_late_stand_hw_tight_throat_is_severe_marginal() -> None:
    seq = [
        _placement(x_mm=200, y_mm=100, z_mm=480, length_mm=75, width_mm=260, height_mm=120),
        _placement(x_mm=325, y_mm=100, z_mm=480, length_mm=75, width_mm=260, height_mm=120),
        _placement(x_mm=100, y_mm=100, z_mm=120, length_mm=180, width_mm=180, height_mm=120),
        _placement(
            x_mm=275,
            y_mm=140,
            z_mm=420,
            length_mm=50,
            width_mm=180,
            height_mm=140,
            orientation_family="stand_hw",
        ),
    ]
    metrics = compute_top_access_diagnostics(seq, bin_length_mm=700, bin_width_mm=700, insertion_margin_mm=40)
    row = metrics["per_placement"][3]
    assert row["blocked_reason_exact"] is None
    assert row["accessibility_class"] == "marginal"
    assert row["orientation_family"] == "stand_hw"
    assert bool(row["is_severe_marginal"]) is True
    assert float(row["marginal_severity_score"]) >= float(metrics["severe_marginal_threshold"])
    assert int(metrics["severe_marginal_count"]) >= 1
    assert int(metrics["severe_marginal_stand_hw"]) >= 1
    assert int(metrics["first_severe_marginal_step"]) == 3


def test_top_access_explainability_lists_relevant_lateral_blockers() -> None:
    seq = [
        _placement(x_mm=100, y_mm=180, z_mm=260, length_mm=100, width_mm=160, height_mm=90),
        _placement(x_mm=300, y_mm=180, z_mm=260, length_mm=100, width_mm=160, height_mm=90),
        _placement(
            x_mm=200,
            y_mm=200,
            z_mm=200,
            length_mm=100,
            width_mm=100,
            height_mm=80,
            orientation_family="stand_hw",
        ),
    ]
    metrics = compute_top_access_diagnostics(
        seq,
        bin_length_mm=700,
        bin_width_mm=700,
        insertion_margin_mm=40,
        local_blockers_limit=4,
    )
    row = metrics["per_placement"][2]
    assert row["accessibility_class"] == "marginal"
    assert row["limiting_axis"] == "left"
    assert int(row["limiting_clearance_mm"]) == 0
    assert row["throat_source_reason"] == "entry_throat_left_limited"
    assert int(row["nearest_blocker_step"]) == 0
    assert row["nearest_blocker_orientation"] == "planar"
    blockers = row["local_blockers"]
    assert isinstance(blockers, list)
    assert len(blockers) >= 2
    assert {int(item["step_index"]) for item in blockers if item.get("step_index") is not None} >= {0, 1}
    axes = {str(item.get("limiting_axis")) for item in blockers}
    assert "left" in axes or "right" in axes


def test_top_access_explainability_nearest_blocker_bottom_axis_reproducible() -> None:
    seq = [
        _placement(x_mm=210, y_mm=120, z_mm=180, length_mm=80, width_mm=70, height_mm=80),
        _placement(
            x_mm=200,
            y_mm=200,
            z_mm=100,
            length_mm=100,
            width_mm=100,
            height_mm=80,
            orientation_family="stand_hw",
        ),
    ]
    metrics = compute_top_access_diagnostics(
        seq,
        bin_length_mm=700,
        bin_width_mm=700,
        insertion_margin_mm=40,
        local_blockers_limit=3,
    )
    row = metrics["per_placement"][1]
    assert row["limiting_axis"] == "bottom"
    assert int(row["limiting_clearance_mm"]) == 10
    assert int(row["nearest_blocker_step"]) == 0
    assert row["nearest_blocker_orientation"] == "planar"
    assert row["throat_source_reason"] == "entry_throat_bottom_limited"
    blockers = row["local_blockers"]
    assert len(blockers) >= 1
    first = blockers[0]
    assert int(first["step_index"]) == 0
    assert first["limiting_axis"] == "bottom"
    assert int(first["clearance_to_target_mm"]) == 10


def test_top_access_helper_does_not_mutate_input() -> None:
    seq = [
        _placement(x_mm=100, y_mm=100, z_mm=100, length_mm=200, width_mm=200, height_mm=100),
        _placement(x_mm=300, y_mm=100, z_mm=150, length_mm=40, width_mm=200, height_mm=100),
    ]
    original = deepcopy(seq)
    _ = compute_top_access_diagnostics(seq, bin_length_mm=500, bin_width_mm=500, insertion_margin_mm=40)
    assert seq == original


def test_aggregate_pallet_kpis_exposes_top_access_payload() -> None:
    pallet = PalletModel()
    pallet.placements = [
        Placement(x_mm=100, y_mm=100, z_mm=100, rot90=False, layer_id=0, length_mm=200, width_mm=200, height_mm=100),
        Placement(x_mm=240, y_mm=120, z_mm=250, rot90=False, layer_id=1, length_mm=120, width_mm=160, height_mm=80),
        Placement(
            x_mm=100,
            y_mm=100,
            z_mm=220,
            rot90=False,
            layer_id=1,
            length_mm=200,
            width_mm=200,
            height_mm=100,
            orientation_family="stand_hw",
        ),
    ]

    kpis = aggregate_pallet_kpis({1: [pallet]})
    by_dest = kpis.get("top_access_first_pallet_by_dest")
    assert isinstance(by_dest, dict)
    first = by_dest.get(1)
    assert isinstance(first, dict)
    assert "per_placement" in first
    assert int(first.get("blocked_count", 0)) >= 1
    critical = first.get("critical_placements", [])
    assert isinstance(critical, list)
    if critical:
        row = critical[0]
        assert "limiting_axis" in row
        assert "nearest_blocker_step" in row
        assert "local_blockers" in row
    assert "top_access_first_pallet" in kpis
