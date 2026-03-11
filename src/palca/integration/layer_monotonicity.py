from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any, Iterable


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _gini(values: Iterable[float]) -> float:
    vals = [max(0.0, float(v)) for v in values]
    n = len(vals)
    if n <= 1:
        return 0.0
    total = float(sum(vals))
    if total <= 0.0:
        return 0.0
    diff_sum = 0.0
    for x in vals:
        for y in vals:
            diff_sum += abs(x - y)
    return float(diff_sum / (2.0 * float(n) * total))


def _placement_value(placement: Any, key: str, default: Any = 0) -> Any:
    if isinstance(placement, dict):
        return placement.get(key, default)
    return getattr(placement, key, default)


def _sorted_numeric_dict(d: dict[int, Any]) -> dict[str, Any]:
    return {str(k): d[k] for k in sorted(d)}


def compute_layer_monotonicity_metrics(
    placements: Iterable[Any],
    *,
    layer_band_mm: int = 100,
    bin_area_mm2: int | None = None,
    relevant_steps_limit: int = 40,
) -> dict[str, Any]:
    seq = list(placements)
    n = len(seq)
    band_mm = max(1, int(layer_band_mm))

    if n <= 0:
        return {
            "layer_band_mm": int(band_mm),
            "placements_count": 0,
            "first_stack_step": -1,
            "first_upper_layer_open_step": -1,
            "first_reentry_step": -1,
            "reentries_total": 0,
            "max_z_seen_so_far_by_step": [],
            "max_top_z_seen_so_far_by_step": [],
            "lower_layer_reentry_count": 0,
            "lower_layer_reentry_total_drop_mm": 0,
            "lower_layer_reentry_max_drop_mm": 0,
            "lower_layer_reentry_mean_drop_mm": 0.0,
            "monotonic_stack_rate": 1.0,
            "monotonic_stack_rate_pct": 100.0,
            "monotonic_placements_count": 0,
            "monotonic_violations_count": 0,
            "active_layers_over_time": [],
            "placements_below_current_top_band_after_opening_next_band": 0,
            "layer_closure_score": 1.0,
            "layer_fill_homogeneity_score": 1.0,
            "z_band_fill_homogeneity_score": 1.0,
            "layer_fill_area_mm2": {},
            "layer_fill_share": {},
            "z_band_fill_area_mm2": {},
            "z_band_fill_share": {},
            "layer_fill_ratio_of_bin": {},
            "z_band_fill_ratio_of_bin": {},
            "layer_placement_counts": {},
            "z_band_placement_counts": {},
            "layer_band_fill_progress": [],
            "step_trace_relevant": [],
            "step_trace_head": [],
        }

    first_stack_step = -1
    first_upper_layer_open_step = -1
    first_reentry_step = -1
    max_z_seen = 0
    max_top_seen = 0
    max_band_seen = 0
    monotonic_count = 0
    reentry_drops: list[int] = []
    below_top_after_opening_count = 0

    max_z_seen_so_far_by_step: list[int] = []
    max_top_z_seen_so_far_by_step: list[int] = []
    active_layers_over_time: list[int] = []

    layer_counts: dict[int, int] = defaultdict(int)
    band_counts: dict[int, int] = defaultdict(int)
    layer_area_mm2: dict[int, int] = defaultdict(int)
    band_area_mm2: dict[int, int] = defaultdict(int)
    band_open_step: dict[int, int] = {}
    band_steps: dict[int, list[int]] = defaultdict(list)

    step_trace: list[dict[str, Any]] = []

    for step, placement in enumerate(seq):
        z_mm = _as_int(_placement_value(placement, "z_mm", 0))
        h_mm = max(0, _as_int(_placement_value(placement, "height_mm", 0)))
        l_mm = max(0, _as_int(_placement_value(placement, "length_mm", 0)))
        w_mm = max(0, _as_int(_placement_value(placement, "width_mm", 0)))
        layer_id = _as_int(_placement_value(placement, "layer_id", 0))

        band_id = int(z_mm // band_mm)
        area_mm2 = int(l_mm * w_mm)

        prev_max_z = int(max_z_seen)
        prev_max_band = int(max_band_seen)

        if step == 0:
            is_reentry = False
            reentry_drop_mm = 0
            respects_monotonic_growth = True
        else:
            is_reentry = int(z_mm) < int(prev_max_z)
            reentry_drop_mm = int(prev_max_z - z_mm) if is_reentry else 0
            respects_monotonic_growth = int(z_mm) >= int(prev_max_z)

        if respects_monotonic_growth:
            monotonic_count += 1
        if is_reentry:
            reentry_drops.append(int(reentry_drop_mm))
            if int(first_reentry_step) < 0:
                first_reentry_step = int(step)

        opened_new_band = int(band_id) > int(prev_max_band)
        if opened_new_band and int(first_upper_layer_open_step) < 0:
            first_upper_layer_open_step = int(step)
        below_current_top_after_opening = int(prev_max_band) >= 1 and int(band_id) < int(prev_max_band)
        if below_current_top_after_opening:
            below_top_after_opening_count += 1

        max_z_seen = max(int(max_z_seen), int(z_mm))
        max_top_seen = max(int(max_top_seen), int(z_mm + h_mm))
        max_band_seen = max(int(max_band_seen), int(band_id))

        if first_stack_step < 0 and int(z_mm) > 0:
            first_stack_step = int(step)

        max_z_seen_so_far_by_step.append(int(max_z_seen))
        max_top_z_seen_so_far_by_step.append(int(max_top_seen))
        active_layers_over_time.append(int(max_band_seen + 1))

        layer_counts[int(layer_id)] += 1
        band_counts[int(band_id)] += 1
        layer_area_mm2[int(layer_id)] += int(area_mm2)
        band_area_mm2[int(band_id)] += int(area_mm2)
        band_steps[int(band_id)].append(int(step))
        if int(band_id) not in band_open_step:
            band_open_step[int(band_id)] = int(step)

        step_trace.append(
            {
                "step": int(step),
                "z_mm": int(z_mm),
                "height_mm": int(h_mm),
                "top_z_mm": int(z_mm + h_mm),
                "layer_id": int(layer_id),
                "band_id": int(band_id),
                "max_z_seen_so_far": int(max_z_seen),
                "max_top_z_seen_so_far": int(max_top_seen),
                "active_layers": int(max_band_seen + 1),
                "opened_new_band": bool(opened_new_band),
                "is_reentry": bool(is_reentry),
                "reentry_drop_mm": int(reentry_drop_mm),
                "respects_monotonic_growth": bool(respects_monotonic_growth),
                "below_current_top_band_after_opening_next_band": bool(below_current_top_after_opening),
            }
        )

    total_area_mm2 = int(sum(layer_area_mm2.values()))
    total_band_area_mm2 = int(sum(band_area_mm2.values()))
    ratio_den = max(1, _as_int(bin_area_mm2 or 0, default=0))

    layer_share = {
        int(layer_id): float(area) / float(max(1, total_area_mm2))
        for layer_id, area in layer_area_mm2.items()
    }
    band_share = {
        int(band_id): float(area) / float(max(1, total_band_area_mm2))
        for band_id, area in band_area_mm2.items()
    }

    layer_fill_ratio_of_bin = {
        int(layer_id): float(area) / float(ratio_den)
        for layer_id, area in layer_area_mm2.items()
    }
    z_band_fill_ratio_of_bin = {
        int(band_id): float(area) / float(ratio_den)
        for band_id, area in band_area_mm2.items()
    }

    band_ids_sorted = sorted(band_counts.keys())
    layer_band_fill_progress: list[dict[str, Any]] = []
    weighted_closure_num = 0.0
    weighted_closure_den = 0
    for band_id in band_ids_sorted:
        higher_open_steps = [band_open_step[b] for b in band_ids_sorted if b > band_id and b in band_open_step]
        next_band_open_step = min(higher_open_steps) if higher_open_steps else None
        steps_for_band = band_steps.get(band_id, [])
        placements_total = len(steps_for_band)
        if next_band_open_step is None:
            placements_before_next = placements_total
        else:
            placements_before_next = sum(1 for s in steps_for_band if s < int(next_band_open_step))
        reentries_after_next = int(placements_total - placements_before_next)
        closure_ratio = float(placements_before_next / max(1, placements_total))

        if next_band_open_step is not None:
            weighted_closure_num += float(closure_ratio * placements_total)
            weighted_closure_den += int(placements_total)

        layer_band_fill_progress.append(
            {
                "band_id": int(band_id),
                "opened_step": int(band_open_step.get(band_id, 0)),
                "next_higher_band_opened_step": (
                    None if next_band_open_step is None else int(next_band_open_step)
                ),
                "placements_total": int(placements_total),
                "placements_before_next_higher_band": int(placements_before_next),
                "reentries_after_next_higher_band": int(reentries_after_next),
                "closure_ratio": float(closure_ratio),
            }
        )

    layer_closure_score = (
        float(weighted_closure_num / max(1, weighted_closure_den)) if weighted_closure_den > 0 else 1.0
    )

    monotonic_stack_rate = float(monotonic_count / max(1, n))
    relevant_steps: list[dict[str, Any]] = []
    for row in step_trace:
        if (
            row["is_reentry"]
            or row["opened_new_band"]
            or row["below_current_top_band_after_opening_next_band"]
            or row["step"] < 10
        ):
            relevant_steps.append(row)
            if len(relevant_steps) >= max(1, int(relevant_steps_limit)):
                break

    return {
        "layer_band_mm": int(band_mm),
        "placements_count": int(n),
        "first_stack_step": int(first_stack_step),
        "first_upper_layer_open_step": int(first_upper_layer_open_step),
        "first_reentry_step": int(first_reentry_step),
        "reentries_total": int(len(reentry_drops)),
        "max_z_seen_so_far_by_step": [int(v) for v in max_z_seen_so_far_by_step],
        "max_top_z_seen_so_far_by_step": [int(v) for v in max_top_z_seen_so_far_by_step],
        "lower_layer_reentry_count": int(len(reentry_drops)),
        "lower_layer_reentry_total_drop_mm": int(sum(reentry_drops)),
        "lower_layer_reentry_max_drop_mm": int(max(reentry_drops)) if reentry_drops else 0,
        "lower_layer_reentry_mean_drop_mm": float(mean(reentry_drops)) if reentry_drops else 0.0,
        "monotonic_stack_rate": float(monotonic_stack_rate),
        "monotonic_stack_rate_pct": float(100.0 * monotonic_stack_rate),
        "monotonic_placements_count": int(monotonic_count),
        "monotonic_violations_count": int(n - monotonic_count),
        "active_layers_over_time": [int(v) for v in active_layers_over_time],
        "placements_below_current_top_band_after_opening_next_band": int(
            below_top_after_opening_count
        ),
        "layer_closure_score": float(layer_closure_score),
        "layer_fill_homogeneity_score": float(1.0 - _gini(layer_share.values())),
        "z_band_fill_homogeneity_score": float(1.0 - _gini(band_share.values())),
        "layer_fill_area_mm2": _sorted_numeric_dict({int(k): int(v) for k, v in layer_area_mm2.items()}),
        "layer_fill_share": _sorted_numeric_dict({int(k): float(v) for k, v in layer_share.items()}),
        "z_band_fill_area_mm2": _sorted_numeric_dict({int(k): int(v) for k, v in band_area_mm2.items()}),
        "z_band_fill_share": _sorted_numeric_dict({int(k): float(v) for k, v in band_share.items()}),
        "layer_fill_ratio_of_bin": _sorted_numeric_dict(
            {int(k): float(v) for k, v in layer_fill_ratio_of_bin.items()}
        ),
        "z_band_fill_ratio_of_bin": _sorted_numeric_dict(
            {int(k): float(v) for k, v in z_band_fill_ratio_of_bin.items()}
        ),
        "layer_placement_counts": _sorted_numeric_dict({int(k): int(v) for k, v in layer_counts.items()}),
        "z_band_placement_counts": _sorted_numeric_dict({int(k): int(v) for k, v in band_counts.items()}),
        "layer_band_fill_progress": layer_band_fill_progress,
        "step_trace_relevant": relevant_steps,
        "step_trace_head": step_trace[: max(1, min(60, int(relevant_steps_limit) * 2))],
    }
