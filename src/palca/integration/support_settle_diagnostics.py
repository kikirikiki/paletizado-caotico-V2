from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from .reentry_diagnostics import (
    _infeasible_rows,
    _rows_from_infeasible_item,
    build_seed_reentry_report_from_dump,
)

FOCUS_REJECTION_REASONS = {
    "support_surface_ratio_below_threshold",
    "corners_unsupported",
    "com_margin_fail",
    "settle_failed",
}

CLOSE_CLASS_NEAR = "near_threshold"
CLOSE_CLASS_MODERATE = "moderately_close"
CLOSE_CLASS_FAR = "far_from_feasible"

RESCUE_YES = "yes"
RESCUE_PLAUSIBLE = "plausible"
RESCUE_NO = "no"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _as_float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _normalize_pid(value: Any) -> str:
    return str(value)


def _normalize_placement(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    placement = raw if isinstance(raw, Mapping) else {}
    return {
        "x_mm": _as_int(placement.get("x_mm"), default=0),
        "y_mm": _as_int(placement.get("y_mm"), default=0),
        "z_mm": _as_int(placement.get("z_mm"), default=0),
        "length_mm": _as_int(placement.get("length_mm"), default=0),
        "width_mm": _as_int(placement.get("width_mm"), default=0),
        "height_mm": _as_int(placement.get("height_mm"), default=0),
        "layer_id": _as_int(placement.get("layer_id"), default=0),
        "rot90": bool(placement.get("rot90", False)),
        "orientation_name": placement.get("orientation_name"),
        "orientation_family": placement.get("orientation_family"),
    }


def _first_pallet_id(dump_payload: Mapping[str, Any], *, params: Mapping[str, Any]) -> str | None:
    pallets = dump_payload.get("pallets", {}) if isinstance(dump_payload.get("pallets"), Mapping) else {}
    if not pallets:
        return None

    forced_dest = params.get("force_destination")
    if forced_dest is not None:
        key = str(_as_int(forced_dest, default=-1))
        if key in pallets:
            return key

    sortable: list[tuple[int, str]] = []
    for key in pallets.keys():
        try:
            sortable.append((int(key), str(key)))
        except Exception:
            sortable.append((1_000_000_000, str(key)))
    sortable.sort(key=lambda item: (item[0], item[1]))
    return sortable[0][1] if sortable else None


def _placement_by_step(placements: Sequence[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for item in placements:
        step = _as_int(item.get("step_index"), default=-1)
        if step < 0:
            continue
        out[int(step)] = _normalize_placement(item)
    return out


def _decision_by_step(trace: Sequence[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    out: dict[int, Mapping[str, Any]] = {}
    for item in trace:
        step = _as_int(item.get("step_index"), default=-1)
        if step < 0:
            continue
        out[int(step)] = item
    return out


def _max_selected_z_before_step(placements: Mapping[int, Mapping[str, Any]], *, step: int) -> int:
    max_z = 0
    for k, placement in placements.items():
        if int(k) >= int(step):
            continue
        max_z = max(max_z, _as_int(placement.get("z_mm"), default=0))
    return int(max_z)


def _fallback_rows_for_step(
    *,
    seed: int | None,
    step: int,
    pallet_id: str,
    params: Mapping[str, Any],
    decision_entry: Mapping[str, Any],
    selected_placement: Mapping[str, Any] | None,
    prev_max_z_mm: int,
) -> list[dict[str, Any]]:
    selected = selected_placement if isinstance(selected_placement, Mapping) else {}
    selected_z = _as_int(selected.get("z_mm"), default=0)
    reentry_box_id = selected.get("box_id")
    raw_rows: list[dict[str, Any]] = []
    for item in _infeasible_rows(decision_entry, pallet_id=pallet_id):
        raw_rows.extend(
            _rows_from_infeasible_item(
                seed=seed,
                step=int(step),
                prev_max_z_mm=int(prev_max_z_mm),
                reentry_z_mm=int(selected_z),
                drop_mm=max(0, int(prev_max_z_mm) - int(selected_z)),
                reentry_box_id=reentry_box_id,
                params=params,
                item=item,
            )
        )
    return raw_rows


def classify_rejection_closeness(
    *,
    reason: str,
    observed: float | None,
    threshold: float | None,
    height_also_blocked: bool,
    source_reason: str | None = None,
) -> dict[str, Any]:
    taxonomy = "other"
    closeness = CLOSE_CLASS_FAR
    rescue = RESCUE_NO
    gap = None if observed is None or threshold is None else float(observed) - float(threshold)
    tags: list[str] = []

    if reason == "support_surface_ratio_below_threshold":
        taxonomy = "support_far_below_threshold"
        if gap is not None and gap >= -0.03:
            closeness = CLOSE_CLASS_NEAR
            taxonomy = "support_near_threshold"
        elif gap is not None and gap >= -0.08:
            closeness = CLOSE_CLASS_MODERATE
            taxonomy = "support_near_threshold"
        else:
            closeness = CLOSE_CLASS_FAR
            taxonomy = "support_far_below_threshold"
    elif reason == "corners_unsupported":
        taxonomy = "corners_missing_severe"
        if gap is not None and gap >= -1.0:
            closeness = CLOSE_CLASS_NEAR
            taxonomy = "corners_missing_small"
        elif gap is not None and gap >= -2.0:
            closeness = CLOSE_CLASS_MODERATE
            taxonomy = "corners_missing_severe"
        else:
            closeness = CLOSE_CLASS_FAR
            taxonomy = "corners_missing_severe"
    elif reason == "com_margin_fail":
        taxonomy = "com_margin_large_fail"
        if gap is not None and gap >= -10.0:
            closeness = CLOSE_CLASS_NEAR
            taxonomy = "com_margin_small_fail"
        elif gap is not None and gap >= -25.0:
            closeness = CLOSE_CLASS_MODERATE
            taxonomy = "com_margin_small_fail"
        else:
            closeness = CLOSE_CLASS_FAR
            taxonomy = "com_margin_large_fail"
    elif reason == "settle_failed":
        settle_mm = abs(float(observed)) if observed is not None else None
        if settle_mm is not None:
            gap = -float(settle_mm)
            if settle_mm <= 12.0:
                closeness = CLOSE_CLASS_NEAR
                taxonomy = "settle_nearby_adjustment_possible"
            elif settle_mm <= 40.0:
                closeness = CLOSE_CLASS_MODERATE
                taxonomy = "settle_nearby_adjustment_possible"
            else:
                closeness = CLOSE_CLASS_FAR
                taxonomy = "settle_hard_fail"
        else:
            closeness = CLOSE_CLASS_FAR
            taxonomy = "settle_hard_fail"
        if source_reason and "collision" in str(source_reason).lower():
            tags.append("collision_after_settle")

    if height_also_blocked:
        tags.append("height_also_blocked")
        rescue = RESCUE_NO
    else:
        if closeness == CLOSE_CLASS_NEAR:
            rescue = RESCUE_YES
        elif closeness == CLOSE_CLASS_MODERATE:
            rescue = RESCUE_PLAUSIBLE
        else:
            rescue = RESCUE_NO

    return {
        "feasibility_gap": gap,
        "closeness_class": closeness,
        "taxonomy": taxonomy,
        "secondary_tags": tags,
        "could_be_rescued_by_support_settle_refinement": rescue,
    }


def _step_rows(
    *,
    seed: int | None,
    step: int,
    params: Mapping[str, Any],
    selected_placement: Mapping[str, Any] | None,
    raw_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    max_height_mm = _as_int(params.get("max_height_mm"), default=0)
    height_limited_candidates = {
        str(item.get("candidate_box_id"))
        for item in raw_rows
        if str(item.get("rejected_reason_exact") or "") == "height_limit_fail"
    }

    out: list[dict[str, Any]] = []
    for row in raw_rows:
        reason = str(row.get("rejected_reason_exact") or "")
        if reason not in FOCUS_REJECTION_REASONS:
            continue

        placement = _normalize_placement(row.get("placement") if isinstance(row.get("placement"), Mapping) else None)
        candidate_box_id = row.get("candidate_box_id")
        top_z_mm = _as_int(placement.get("z_mm"), default=0) + _as_int(placement.get("height_mm"), default=0)
        candidate_key = str(candidate_box_id)
        height_overflow_mm = max(0, int(top_z_mm) - int(max_height_mm)) if max_height_mm > 0 else 0
        height_also_blocked = (candidate_key in height_limited_candidates) or (height_overflow_mm > 0)

        observed_name = row.get("observed_name")
        threshold_name = row.get("threshold_name")
        observed_value = _as_float_or_none(row.get("observed_value"))
        threshold_value = _as_float_or_none(row.get("threshold_value"))

        if reason == "corners_unsupported":
            if threshold_value is None:
                threshold_value = 4.0
            if observed_value is None:
                observed_value = _as_float_or_none(placement.get("corners_supported_count"))

        classification = classify_rejection_closeness(
            reason=reason,
            observed=observed_value,
            threshold=threshold_value,
            height_also_blocked=height_also_blocked,
            source_reason=(None if row.get("source_reason") is None else str(row.get("source_reason"))),
        )

        chosen = _normalize_placement(selected_placement if isinstance(selected_placement, Mapping) else None)
        out.append(
            {
                "seed": (None if seed is None else int(seed)),
                "step": int(step),
                "candidate_box_id": candidate_box_id,
                "ramp_id": row.get("ramp_id"),
                "buffer_index": row.get("buffer_index"),
                "candidate_origin": row.get("candidate_origin"),
                "reason": reason,
                "source_reason": row.get("source_reason"),
                "observed_name": observed_name,
                "observed_value": observed_value,
                "threshold_name": threshold_name,
                "threshold_value": threshold_value,
                "feasibility_gap": classification["feasibility_gap"],
                "closeness_class": classification["closeness_class"],
                "feasibility_taxonomy": classification["taxonomy"],
                "secondary_tags": list(classification["secondary_tags"]),
                "could_be_rescued_by_support_settle_refinement": classification[
                    "could_be_rescued_by_support_settle_refinement"
                ],
                "height_overflow_mm": int(height_overflow_mm),
                "chosen_placement": chosen,
                "candidate_placement": placement,
                "detail": row.get("detail"),
            }
        )
    return out


def build_seed_support_settle_report_from_dump(
    *,
    dump_payload: Mapping[str, Any],
    seed: int | None = None,
    max_critical_steps: int = 3,
    critical_window_radius: int = 1,
) -> dict[str, Any]:
    params = dump_payload.get("params", {}) if isinstance(dump_payload.get("params"), Mapping) else {}
    pallet_id = _first_pallet_id(dump_payload, params=params)
    if pallet_id is None:
        return {
            "seed": (None if seed is None else int(seed)),
            "pallet_id": None,
            "critical_steps": [],
            "analysis_steps": [],
            "rows": [],
            "summary": {
                "rows_total": 0,
                "rows_close_total": 0,
                "rows_far_total": 0,
                "rows_close_pct": 0.0,
            },
        }

    pallets = dump_payload.get("pallets", {}) if isinstance(dump_payload.get("pallets"), Mapping) else {}
    trace = dump_payload.get("decision_trace", {}) if isinstance(dump_payload.get("decision_trace"), Mapping) else {}
    placements_raw = pallets.get(pallet_id, [])
    decision_raw = trace.get(pallet_id, [])
    placements = [item for item in placements_raw if isinstance(item, Mapping)]
    decisions = [item for item in decision_raw if isinstance(item, Mapping)]
    placements_by_step = _placement_by_step(placements)
    decisions_by_step = _decision_by_step(decisions)

    reentry_report = build_seed_reentry_report_from_dump(dump_payload=dump_payload, seed=seed)
    reentries = [item for item in list(reentry_report.get("reentries", []) or []) if isinstance(item, Mapping)]
    reentries.sort(key=lambda item: _as_int(item.get("step"), default=0))
    critical = reentries[: max(1, int(max_critical_steps))]
    critical_steps = [_as_int(item.get("step"), default=-1) for item in critical if _as_int(item.get("step"), -1) >= 0]

    all_steps = sorted(placements_by_step.keys())
    if critical_steps:
        radius = max(0, int(critical_window_radius))
        analysis_steps_set: set[int] = set()
        for step in critical_steps:
            for offset in range(-radius, radius + 1):
                s = int(step + offset)
                if s in placements_by_step:
                    analysis_steps_set.add(s)
        analysis_steps = sorted(analysis_steps_set)
    else:
        analysis_steps = all_steps[: max(1, min(3, len(all_steps)))]

    critical_rows_by_step: dict[int, list[Mapping[str, Any]]] = {}
    for entry in critical:
        step = _as_int(entry.get("step"), default=-1)
        if step < 0:
            continue
        rows = [item for item in list(entry.get("breakdown_rows", []) or []) if isinstance(item, Mapping)]
        critical_rows_by_step[step] = rows

    rows: list[dict[str, Any]] = []
    steps_payload: list[dict[str, Any]] = []
    normalized_pid = _normalize_pid(pallet_id)
    for step in analysis_steps:
        selected = placements_by_step.get(step)
        prev_max_z = _max_selected_z_before_step(placements_by_step, step=step)
        raw_rows = list(critical_rows_by_step.get(step, []))
        if not raw_rows:
            decision_entry = decisions_by_step.get(step, {})
            if isinstance(decision_entry, Mapping):
                raw_rows = _fallback_rows_for_step(
                    seed=seed,
                    step=step,
                    pallet_id=normalized_pid,
                    params=params,
                    decision_entry=decision_entry,
                    selected_placement=selected,
                    prev_max_z_mm=prev_max_z,
                )

        step_rows = _step_rows(
            seed=seed,
            step=step,
            params=params,
            selected_placement=selected,
            raw_rows=raw_rows,
        )
        rows.extend(step_rows)
        steps_payload.append(
            {
                "step": int(step),
                "is_critical_step": bool(step in critical_steps),
                "chosen_placement": _normalize_placement(selected if isinstance(selected, Mapping) else None),
                "relevant_rejections": step_rows,
            }
        )

    close_total = sum(1 for item in rows if item.get("closeness_class") in {CLOSE_CLASS_NEAR, CLOSE_CLASS_MODERATE})
    far_total = sum(1 for item in rows if item.get("closeness_class") == CLOSE_CLASS_FAR)

    return {
        "seed": (None if seed is None else int(seed)),
        "pallet_id": str(pallet_id),
        "critical_steps": [int(s) for s in critical_steps],
        "analysis_steps": steps_payload,
        "rows": rows,
        "summary": {
            "rows_total": int(len(rows)),
            "rows_close_total": int(close_total),
            "rows_far_total": int(far_total),
            "rows_close_pct": (float(close_total) / float(len(rows)) if rows else 0.0),
        },
    }


def build_consolidated_support_settle_report(seed_reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    per_seed: list[dict[str, Any]] = []
    for report in seed_reports:
        report_rows = [item for item in list(report.get("rows", []) or []) if isinstance(item, Mapping)]
        rows.extend(dict(item) for item in report_rows)
        summary = report.get("summary", {}) if isinstance(report.get("summary"), Mapping) else {}
        per_seed.append(
            {
                "seed": report.get("seed"),
                "rows_total": _as_int(summary.get("rows_total"), default=0),
                "rows_close_total": _as_int(summary.get("rows_close_total"), default=0),
                "rows_far_total": _as_int(summary.get("rows_far_total"), default=0),
                "rows_close_pct": _as_float(summary.get("rows_close_pct"), default=0.0),
                "critical_steps": list(report.get("critical_steps", []) or []),
            }
        )

    rows.sort(
        key=lambda item: (
            _as_int(item.get("seed"), default=0),
            _as_int(item.get("step"), default=-1),
            str(item.get("candidate_box_id")),
            str(item.get("reason")),
        )
    )

    reason_counter = Counter(str(item.get("reason") or "") for item in rows)
    closeness_counter = Counter(str(item.get("closeness_class") or "") for item in rows)
    taxonomy_counter = Counter(str(item.get("feasibility_taxonomy") or "") for item in rows)
    rescue_counter = Counter(str(item.get("could_be_rescued_by_support_settle_refinement") or "") for item in rows)

    by_reason_closeness: dict[tuple[str, str], int] = Counter(
        (str(item.get("reason") or ""), str(item.get("closeness_class") or ""))
        for item in rows
    )
    by_reason_closeness_rows = [
        {
            "reason": reason,
            "closeness_class": closeness,
            "count": int(count),
        }
        for (reason, closeness), count in sorted(
            by_reason_closeness.items(),
            key=lambda kv: (-int(kv[1]), kv[0][0], kv[0][1]),
        )
    ]

    total = len(rows)
    close_total = int(closeness_counter.get(CLOSE_CLASS_NEAR, 0) + closeness_counter.get(CLOSE_CLASS_MODERATE, 0))
    far_total = int(closeness_counter.get(CLOSE_CLASS_FAR, 0))

    return {
        "rows": rows,
        "seed_summaries": per_seed,
        "summary": {
            "rows_total": int(total),
            "rows_close_total": int(close_total),
            "rows_far_total": int(far_total),
            "rows_close_pct": (float(close_total) / float(total) if total > 0 else 0.0),
            "rows_far_pct": (float(far_total) / float(total) if total > 0 else 0.0),
            "reason_pareto": [
                {
                    "reason": key,
                    "count": int(val),
                    "pct": (float(val) / float(total) if total > 0 else 0.0),
                }
                for key, val in reason_counter.most_common()
                if key
            ],
            "closeness_pareto": [
                {
                    "closeness_class": key,
                    "count": int(val),
                    "pct": (float(val) / float(total) if total > 0 else 0.0),
                }
                for key, val in closeness_counter.most_common()
                if key
            ],
            "taxonomy_pareto": [
                {
                    "taxonomy": key,
                    "count": int(val),
                    "pct": (float(val) / float(total) if total > 0 else 0.0),
                }
                for key, val in taxonomy_counter.most_common()
                if key
            ],
            "rescue_pareto": [
                {
                    "rescue": key,
                    "count": int(val),
                    "pct": (float(val) / float(total) if total > 0 else 0.0),
                }
                for key, val in rescue_counter.most_common()
                if key
            ],
            "by_reason_closeness": by_reason_closeness_rows,
        },
    }
