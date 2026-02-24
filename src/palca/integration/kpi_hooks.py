from __future__ import annotations

from statistics import mean
from typing import Iterable

from ..packer.pallet_model import PalletModel


def layer_utilization(pallet: PalletModel) -> dict[int, float]:
    used_area: dict[int, int] = {}
    for placement in pallet.placements:
        used_area[placement.layer_id] = used_area.get(placement.layer_id, 0) + (
            int(placement.length_mm) * int(placement.width_mm)
        )
    if pallet.bin_area_mm2 <= 0:
        return {layer_id: 0.0 for layer_id in used_area}
    return {
        layer_id: float(area) / float(pallet.bin_area_mm2)
        for layer_id, area in used_area.items()
    }


def volume_utilization(pallet: PalletModel) -> float:
    max_volume = float(pallet.bin_area_mm2) * float(pallet.spec.max_height_mm)
    if max_volume <= 0:
        return 0.0
    used = 0.0
    for placement in pallet.placements:
        used += float(placement.length_mm) * float(placement.width_mm) * float(placement.height_mm)
    return used / max_volume


def aggregate_pallet_kpis(pallets_by_dest: dict[int, Iterable[PalletModel]]) -> dict[str, object]:
    volume_by_dest: dict[int, float] = {}
    layer_util_by_dest: dict[int, dict[int, float]] = {}
    pallets_count: dict[int, int] = {}
    support_checks = 0
    support_rejects = 0
    corner_checks = 0
    corner_rejects = 0
    settle_count = 0
    settle_total = 0.0
    settle_max = 0.0
    floating_total = 0
    balance_quadrant_sum = [0.0, 0.0, 0.0, 0.0]
    balance_scores: list[float] = []
    com_dx: list[float] = []
    com_dy: list[float] = []
    orientation_counts = {"planar": 0, "stand_hw": 0}
    stand_hw_used_by_dest: dict[int, int] = {}
    orientation_counts_by_dest: dict[int, dict[str, int]] = {}
    stand_hw_gate_mm = 0
    stand_hw_gate_blocks_total = 0
    stand_hw_gate_allows_total = 0
    stand_hw_rejected_support_total = 0

    for dest, pallets in pallets_by_dest.items():
        pallet_list = list(pallets)
        pallets_count[dest] = len(pallet_list)
        if not pallet_list:
            volume_by_dest[dest] = 0.0
            layer_util_by_dest[dest] = {}
            continue

        vol_values = [volume_utilization(pallet) for pallet in pallet_list]
        volume_by_dest[dest] = mean(vol_values) if vol_values else 0.0

        layer_accum: dict[int, list[float]] = {}
        dest_orientation_counts = {"planar": 0, "stand_hw": 0}
        for pallet in pallet_list:
            for placement in pallet.placements:
                family = str(getattr(placement, "orientation_family", "planar") or "planar").strip().lower()
                if family not in ("planar", "stand_hw"):
                    family = "planar"
                orientation_counts[family] = int(orientation_counts.get(family, 0)) + 1
                dest_orientation_counts[family] = int(dest_orientation_counts.get(family, 0)) + 1
            for layer_id, util in layer_utilization(pallet).items():
                layer_accum.setdefault(layer_id, []).append(util)
            support_checks += int(pallet.stats.support_ratio_checks)
            support_rejects += int(pallet.stats.support_ratio_rejects)
            corner_checks += int(pallet.stats.corner_checks)
            corner_rejects += int(pallet.stats.corner_rejects)
            settle_count += int(pallet.stats.settle_adjustments_count)
            settle_total += float(pallet.stats.settle_total_mm)
            settle_max = max(settle_max, float(pallet.stats.settle_max_mm))
            floating_total += int(pallet.stats.floating_boxes_count)
            stand_hw_gate_mm = int(getattr(pallet, "stand_hw_height_margin_gate_mm", stand_hw_gate_mm))
            stand_hw_gate_blocks_total += int(getattr(pallet.stats, "stand_hw_gate_blocks_total", 0))
            stand_hw_gate_allows_total += int(getattr(pallet.stats, "stand_hw_gate_allows_total", 0))
            stand_hw_rejected_support_total += int(getattr(pallet.stats, "stand_hw_rejected_support_total", 0))

            metrics = pallet.balance_metrics()
            for i, val in enumerate(metrics.quadrant_weights):
                balance_quadrant_sum[i] += float(val)
            balance_scores.append(float(metrics.balance_score))
            com_dx.append(float(metrics.com_offset_mm[0]))
            com_dy.append(float(metrics.com_offset_mm[1]))
        layer_util_by_dest[dest] = {
            layer_id: mean(vals) if vals else 0.0 for layer_id, vals in layer_accum.items()
        }
        orientation_counts_by_dest[dest] = dict(dest_orientation_counts)
        stand_hw_used_by_dest[dest] = int(dest_orientation_counts.get("stand_hw", 0))

    support_pct = (100.0 * support_rejects / max(1, support_checks)) if support_checks else 0.0
    corner_pct = (100.0 * corner_rejects / max(1, corner_checks)) if corner_checks else 0.0
    avg_settle = (settle_total / settle_count) if settle_count else 0.0
    balance_score = mean(balance_scores) if balance_scores else 0.0
    com_offset = (
        float(mean(com_dx)) if com_dx else 0.0,
        float(mean(com_dy)) if com_dy else 0.0,
    )

    return {
        "pallets_count": pallets_count,
        "pallet_volume_utilization": volume_by_dest,
        "pallet_layer_utilization": layer_util_by_dest,
        "rejected_by_support_ratio_count": support_rejects,
        "rejected_by_support_ratio_pct": support_pct,
        "rejected_by_corner_support_count": corner_rejects,
        "rejected_by_corner_support_pct": corner_pct,
        "settle_adjustments_count": settle_count,
        "avg_settle_mm": avg_settle,
        "max_settle_mm": settle_max,
        "floating_boxes_count": floating_total,
        "balance_quadrant_weights": balance_quadrant_sum,
        "com_offset_mm": {"x": com_offset[0], "y": com_offset[1]},
        "balance_score": balance_score,
        "orientation_counts": {k: int(v) for k, v in orientation_counts.items()},
        "orientation_counts_by_dest": {
            int(dest): {k: int(v) for k, v in counts.items()}
            for dest, counts in orientation_counts_by_dest.items()
        },
        "stand_hw_used_total": int(orientation_counts.get("stand_hw", 0)),
        "stand_hw_used_by_dest": {int(dest): int(v) for dest, v in stand_hw_used_by_dest.items()},
        "stand_hw_gate_mm": int(stand_hw_gate_mm),
        "stand_hw_gate_blocks_total": int(stand_hw_gate_blocks_total),
        "stand_hw_gate_allows_total": int(stand_hw_gate_allows_total),
        "stand_hw_rejected_support_total": int(stand_hw_rejected_support_total),
    }
