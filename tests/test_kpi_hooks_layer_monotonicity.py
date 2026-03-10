from __future__ import annotations

from palca.domain.placement import Placement
from palca.integration.kpi_hooks import aggregate_pallet_kpis
from palca.packer.pallet_model import PalletModel


def test_aggregate_pallet_kpis_exposes_first_pallet_layer_monotonicity() -> None:
    pallet = PalletModel()
    pallet.placements = [
        Placement(x_mm=0, y_mm=0, z_mm=0, rot90=False, layer_id=0, length_mm=200, width_mm=100, height_mm=100),
        Placement(x_mm=0, y_mm=0, z_mm=100, rot90=False, layer_id=1, length_mm=200, width_mm=100, height_mm=100),
        Placement(x_mm=0, y_mm=0, z_mm=0, rot90=False, layer_id=0, length_mm=200, width_mm=100, height_mm=100),
    ]

    kpis = aggregate_pallet_kpis({1: [pallet]})

    by_dest = kpis.get("layer_monotonicity_first_pallet_by_dest")
    assert isinstance(by_dest, dict)
    mono = by_dest.get(1)
    assert isinstance(mono, dict)

    assert int(mono.get("lower_layer_reentry_count", 0)) == 1
    assert "layer_band_fill_progress" in mono
    assert "active_layers_over_time" in mono
    assert "layer_monotonicity_first_pallet" in kpis


def test_aggregate_pallet_kpis_exposes_support_frontier_refinement_counters() -> None:
    pallet = PalletModel()
    pallet.stats.support_frontier_refine_attempts = 4
    pallet.stats.support_frontier_candidates_rescued = 2
    pallet.stats.support_frontier_rescued_support_ratio = 1
    pallet.stats.support_frontier_rescued_corner_support = 1
    pallet.stats.support_frontier_selected_placements = 1
    pallet.stats.support_frontier_selected_support_ratio = 1
    pallet.stats.support_frontier_selected_corner_support = 0

    kpis = aggregate_pallet_kpis({1: [pallet]})
    assert int(kpis.get("support_frontier_refine_attempts_count", -1)) == 4
    assert int(kpis.get("support_frontier_rescued_candidates_count", -1)) == 2
    assert int(kpis.get("support_frontier_rescued_by_support_ratio_count", -1)) == 1
    assert int(kpis.get("support_frontier_rescued_by_corner_support_count", -1)) == 1
    assert int(kpis.get("support_frontier_selected_placements_count", -1)) == 1
