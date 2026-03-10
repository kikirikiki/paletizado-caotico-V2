from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from .reentry_diagnostics import build_seed_reentry_report_from_dump

VIABLE_LATE_STAND_EXISTS = "viable_late_stand_exists"
LATE_STAND_GENERATED_BUT_REJECTED = "late_stand_generated_but_rejected"
LATE_STAND_NOT_GENERATED = "late_stand_not_generated"
NO_PLAUSIBLE_LATE_STAND = "no_plausible_late_stand"

ALT_STATUS_FEASIBLE = "factible"
ALT_STATUS_NOT_FEASIBLE = "no_factible"
ALT_STATUS_NOT_GENERATED = "no_generada"

_REJECTION_TOKENS = {
    "stand_gate_blocked",
    "support_surface_ratio_below_threshold",
    "corners_unsupported",
    "com_margin_fail",
    "settle_failed",
    "collision_fail",
    "free_rect_unavailable",
    "candidate_not_generated",
    "height_limit_fail",
    "loadbear_fail",
    "grid_snap_fail",
}


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


def _normalize_pid(value: Any) -> str:
    return str(value)


def _normalize_reason_exact(raw_reason: Any) -> str:
    reason = str(raw_reason or "").strip()
    if not reason:
        return "other_exact_unknown"
    if reason in _REJECTION_TOKENS:
        return reason
    if reason.startswith("other_exact_"):
        return reason
    if reason.startswith("candidate_not_generated"):
        return "candidate_not_generated"
    if reason == "grid_or_footprint_fail":
        return "grid_snap_fail"
    return f"other_exact_{reason}"


def _is_stand_placement(raw_placement: Mapping[str, Any] | None) -> bool:
    placement = raw_placement if isinstance(raw_placement, Mapping) else {}
    family = str(placement.get("orientation_family") or "").strip().lower()
    if family == "stand_hw":
        return True
    name = str(placement.get("orientation_name") or "").strip().lower()
    if name in {"hwl", "whl"}:
        return True
    return "stand" in name


def _placement_payload(raw: Mapping[str, Any] | None) -> dict[str, Any]:
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
        "orientation_name": (
            None if placement.get("orientation_name") is None else str(placement.get("orientation_name"))
        ),
        "orientation_family": (
            None if placement.get("orientation_family") is None else str(placement.get("orientation_family"))
        ),
    }


def _event_by_step(trace: Sequence[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    out: dict[int, Mapping[str, Any]] = {}
    for entry in trace:
        step = _as_int(entry.get("step_index"), default=-1)
        if step < 0:
            continue
        out[int(step)] = entry
    return out


def _max_top_before_step(placements: Sequence[Mapping[str, Any]], *, step: int) -> int:
    max_top = 0
    for item in placements:
        item_step = _as_int(item.get("step_index"), default=-1)
        if item_step < 0 or item_step >= int(step):
            continue
        z_mm = _as_int(item.get("z_mm"), default=0)
        h_mm = _as_int(item.get("height_mm"), default=0)
        max_top = max(max_top, int(z_mm + h_mm))
    return int(max_top)


def _score_from_candidate(row: Mapping[str, Any]) -> float:
    score = row.get("score")
    if isinstance(score, (int, float)):
        return float(score)
    terms = row.get("terms", {})
    if isinstance(terms, Mapping):
        scalar = terms.get("scalar_score")
        if isinstance(scalar, (int, float)):
            return float(scalar)
    return 0.0


def _top_feasible_candidates(entry: Mapping[str, Any], *, top_k: int = 5) -> list[dict[str, Any]]:
    candidates = [item for item in list(entry.get("feasible_candidates_step", []) or []) if isinstance(item, Mapping)]
    ranked = sorted(
        candidates,
        key=lambda row: (
            _score_from_candidate(row),
            _as_int(row.get("box_id"), default=-1),
        ),
        reverse=True,
    )[: max(1, int(top_k))]
    out: list[dict[str, Any]] = []
    for row in ranked:
        out.append(
            {
                "box_id": row.get("box_id"),
                "score": float(_score_from_candidate(row)),
                "ramp_id": _as_int(row.get("ramp_id"), default=0),
                "buffer_index": _as_int(row.get("buffer_index"), default=0),
                "placement": _placement_payload(
                    row.get("placement") if isinstance(row.get("placement"), Mapping) else None
                ),
            }
        )
    return out


def _coerce_bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    return bool(value)


def _stand_breakdown_rows(
    *,
    seed: int | None,
    step: int,
    reentry_placement: Mapping[str, Any],
    breakdown_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    dedupe: set[tuple[Any, ...]] = set()
    for row in breakdown_rows:
        placement = _placement_payload(row.get("placement") if isinstance(row.get("placement"), Mapping) else None)
        if not _is_stand_placement(placement):
            continue
        normalized_reason = _normalize_reason_exact(row.get("rejected_reason_exact"))
        payload = {
            "seed": (None if seed is None else int(seed)),
            "step": int(step),
            "reentry_placement": _placement_payload(
                reentry_placement if isinstance(reentry_placement, Mapping) else None
            ),
            "stand_candidate": placement,
            "candidate_box_id": row.get("candidate_box_id"),
            "candidate_origin": str(row.get("candidate_origin") or ""),
            "generated": _coerce_bool_or_none(row.get("generated")),
            "feasible": _coerce_bool_or_none(row.get("feasible")),
            "non_reentry_candidate": _coerce_bool_or_none(row.get("non_reentry_candidate")),
            "rejected_reason_exact": normalized_reason,
            "threshold_name": row.get("threshold_name"),
            "threshold_value": row.get("threshold_value"),
            "observed_name": row.get("observed_name"),
            "observed_value": row.get("observed_value"),
            "detail": row.get("detail"),
            "source_reason": row.get("source_reason"),
            "ramp_id": row.get("ramp_id"),
            "buffer_index": row.get("buffer_index"),
        }
        key = (
            payload.get("candidate_box_id"),
            payload.get("candidate_origin"),
            payload.get("generated"),
            payload.get("feasible"),
            payload.get("non_reentry_candidate"),
            payload.get("rejected_reason_exact"),
            placement.get("x_mm"),
            placement.get("y_mm"),
            placement.get("z_mm"),
            placement.get("length_mm"),
            placement.get("width_mm"),
            placement.get("height_mm"),
            placement.get("orientation_name"),
            placement.get("orientation_family"),
        )
        if key in dedupe:
            continue
        dedupe.add(key)
        out.append(payload)
    return out


def _infer_box_dims_from_item(item: Mapping[str, Any]) -> tuple[int, int, int] | None:
    raw_l = item.get("box_length_mm")
    raw_w = item.get("box_width_mm")
    raw_h = item.get("box_height_mm")
    if isinstance(raw_l, (int, float)) and isinstance(raw_w, (int, float)) and isinstance(raw_h, (int, float)):
        l_mm = _as_int(raw_l, default=0)
        w_mm = _as_int(raw_w, default=0)
        h_mm = _as_int(raw_h, default=0)
        if l_mm > 0 and w_mm > 0 and h_mm > 0:
            return (l_mm, w_mm, h_mm)

    preview = item.get("preview", {})
    if not isinstance(preview, Mapping):
        return None
    placement = preview.get("placement", {})
    if not isinstance(placement, Mapping):
        return None

    l_p = _as_int(placement.get("length_mm"), default=0)
    w_p = _as_int(placement.get("width_mm"), default=0)
    h_p = _as_int(placement.get("height_mm"), default=0)
    if l_p <= 0 or w_p <= 0 or h_p <= 0:
        return None

    orientation = str(placement.get("orientation_name") or "").strip().upper()
    if orientation == "LWH":
        return (l_p, w_p, h_p)
    if orientation == "WLH":
        return (w_p, l_p, h_p)
    if orientation == "HWL":
        return (h_p, w_p, l_p)
    if orientation == "WHL":
        return (h_p, l_p, w_p)
    return (l_p, w_p, h_p)


def _synthetic_gate_blocked_row(
    *,
    seed: int | None,
    step: int,
    reentry_placement: Mapping[str, Any],
    prev_max_z_mm: int,
    params: Mapping[str, Any],
    event: Mapping[str, Any],
    has_any_non_reentry: bool,
) -> dict[str, Any] | None:
    mode = str(params.get("orientation_mode") or "").strip().lower()
    if mode != "planar+stand_hw":
        return None

    gate_mm = max(0, _as_int(params.get("stand_hw_height_margin_gate_mm"), default=0))
    max_height_mm = max(0, _as_int(params.get("max_height_mm"), default=0))

    placements = (
        list(event.get("placements_before_step", []) or [])
        if isinstance(event.get("placements_before_step"), list)
        else []
    )
    if placements:
        current_top = _max_top_before_step(placements, step=10**9)
    else:
        current_top = _as_int(event.get("current_top_mm"), default=0)

    height_margin_mm = max(0, int(max_height_mm) - int(current_top))
    if int(height_margin_mm) <= int(gate_mm):
        return None

    if not has_any_non_reentry:
        return None

    pooled_items: list[Mapping[str, Any]] = []
    for raw in list(event.get("selection_pool", []) or []):
        if isinstance(raw, Mapping):
            pooled_items.append(raw)
    for raw in list(event.get("evaluated_items", []) or []):
        if isinstance(raw, Mapping):
            pooled_items.append(raw)

    candidate_box_id: Any | None = None
    for item in pooled_items:
        dims = _infer_box_dims_from_item(item)
        if dims is None:
            continue
        l_mm, w_mm, _ = dims
        stand_h_mm = max(0, int(l_mm))
        if int(prev_max_z_mm) + int(stand_h_mm) <= int(max_height_mm):
            candidate_box_id = item.get("box_id")
            break

    if candidate_box_id is None:
        return None

    return {
        "seed": (None if seed is None else int(seed)),
        "step": int(step),
        "reentry_placement": _placement_payload(reentry_placement if isinstance(reentry_placement, Mapping) else None),
        "stand_candidate": _placement_payload(None),
        "candidate_box_id": candidate_box_id,
        "candidate_origin": "synthetic_gate_blocked",
        "generated": False,
        "feasible": False,
        "non_reentry_candidate": True,
        "rejected_reason_exact": "stand_gate_blocked",
        "threshold_name": "stand_hw_height_margin_gate_mm",
        "threshold_value": int(gate_mm),
        "observed_name": "height_margin_mm",
        "observed_value": int(height_margin_mm),
        "detail": "stand gate blocked while non-reentry alternatives existed in this decision step",
        "source_reason": "SYNTHETIC_STAND_GATE",
        "ramp_id": None,
        "buffer_index": None,
    }


def _classify_late_stand_step(rows: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    non_reentry_rows = [
        row for row in rows if _coerce_bool_or_none(row.get("non_reentry_candidate")) is True
    ]
    feasible_rows = [
        row
        for row in non_reentry_rows
        if _coerce_bool_or_none(row.get("generated")) is True and _coerce_bool_or_none(row.get("feasible")) is True
    ]
    rejected_rows = [
        row
        for row in non_reentry_rows
        if _coerce_bool_or_none(row.get("generated")) is True and _coerce_bool_or_none(row.get("feasible")) is False
    ]
    missing_rows = [
        row
        for row in non_reentry_rows
        if _coerce_bool_or_none(row.get("generated")) is False
    ]

    if feasible_rows:
        return VIABLE_LATE_STAND_EXISTS, ALT_STATUS_FEASIBLE
    if rejected_rows:
        return LATE_STAND_GENERATED_BUT_REJECTED, ALT_STATUS_NOT_FEASIBLE
    if missing_rows:
        return LATE_STAND_NOT_GENERATED, ALT_STATUS_NOT_GENERATED
    return NO_PLAUSIBLE_LATE_STAND, ALT_STATUS_NOT_FEASIBLE


def _reason_pareto(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    reason_counts: Counter[str] = Counter()
    for row in rows:
        generated = _coerce_bool_or_none(row.get("generated"))
        feasible = _coerce_bool_or_none(row.get("feasible"))
        if generated is True and feasible is True:
            continue
        reason = _normalize_reason_exact(row.get("rejected_reason_exact"))
        if not reason:
            continue
        reason_counts[str(reason)] += 1

    total = int(sum(reason_counts.values()))
    out: list[dict[str, Any]] = []
    for reason, count in reason_counts.most_common():
        out.append(
            {
                "reason": str(reason),
                "count": int(count),
                "pct": (float(count) / float(total) if total > 0 else 0.0),
            }
        )
    return out


def build_seed_late_stand_report_from_dump(
    *,
    dump_payload: Mapping[str, Any],
    seed: int | None = None,
    critical_start_step: int = 15,
    max_critical_steps: int = 3,
) -> dict[str, Any]:
    reentry_report = build_seed_reentry_report_from_dump(dump_payload=dump_payload, seed=seed)
    reentries = [item for item in list(reentry_report.get("reentries", []) or []) if isinstance(item, Mapping)]
    filtered_reentries = [
        item for item in reentries if _as_int(item.get("step"), default=-1) >= int(critical_start_step)
    ][: max(1, int(max_critical_steps))]

    params = dump_payload.get("params", {}) if isinstance(dump_payload.get("params"), Mapping) else {}
    pallets = dump_payload.get("pallets", {}) if isinstance(dump_payload.get("pallets"), Mapping) else {}
    decision_trace_all = (
        dump_payload.get("decision_trace", {}) if isinstance(dump_payload.get("decision_trace"), Mapping) else {}
    )
    chosen_pid = _normalize_pid(reentry_report.get("pallet_id"))
    placements = list(pallets.get(chosen_pid, []) or []) if chosen_pid in pallets else []
    trace = list(decision_trace_all.get(chosen_pid, []) or []) if chosen_pid in decision_trace_all else []
    events = _event_by_step([item for item in trace if isinstance(item, Mapping)])

    rows: list[dict[str, Any]] = []
    critical_steps: list[dict[str, Any]] = []

    for entry in filtered_reentries:
        step = _as_int(entry.get("step"), default=-1)
        if step < 0:
            continue
        chosen_placement = entry.get("chosen_placement", {}) if isinstance(entry.get("chosen_placement"), Mapping) else {}
        prev_max_z_mm = _as_int(entry.get("z_prev_max_mm"), default=0)
        step_breakdown = [
            item for item in list(entry.get("breakdown_rows", []) or []) if isinstance(item, Mapping)
        ]
        stand_rows = _stand_breakdown_rows(
            seed=seed,
            step=step,
            reentry_placement=chosen_placement,
            breakdown_rows=step_breakdown,
        )

        event = dict(events.get(int(step), {}))
        event["placements_before_step"] = [item for item in placements if _as_int(item.get("step_index"), default=-1) < step]
        event["current_top_mm"] = _max_top_before_step(placements, step=step)

        has_any_non_reentry = any(
            _coerce_bool_or_none(item.get("non_reentry_candidate")) is True for item in step_breakdown
        )
        synthetic_row = _synthetic_gate_blocked_row(
            seed=seed,
            step=step,
            reentry_placement=chosen_placement,
            prev_max_z_mm=prev_max_z_mm,
            params=params,
            event=event,
            has_any_non_reentry=has_any_non_reentry,
        )
        if synthetic_row is not None:
            stand_rows.append(synthetic_row)

        classification, alt_status = _classify_late_stand_step(stand_rows)
        for row in stand_rows:
            row["classification"] = str(classification)

        top_candidates = _top_feasible_candidates(entry, top_k=5)
        generated_non_reentry = [
            row
            for row in stand_rows
            if _coerce_bool_or_none(row.get("generated")) is True
            and _coerce_bool_or_none(row.get("non_reentry_candidate")) is True
        ]
        not_generated_non_reentry = [
            row
            for row in stand_rows
            if _coerce_bool_or_none(row.get("generated")) is False
            and _coerce_bool_or_none(row.get("non_reentry_candidate")) is True
        ]

        critical_steps.append(
            {
                "seed": (None if seed is None else int(seed)),
                "step": int(step),
                "drop_mm": _as_int(entry.get("drop_mm"), default=0),
                "z_prev_max_mm": int(prev_max_z_mm),
                "z_reentry_mm": _as_int(entry.get("z_reentry_mm"), default=0),
                "reentry_box_id": entry.get("box_id"),
                "reentry_placement": _placement_payload(chosen_placement),
                "top_feasible_candidates_current": top_candidates,
                "stand_candidates_generated_non_reentry": generated_non_reentry,
                "stand_candidates_not_generated_non_reentry": not_generated_non_reentry,
                "stand_candidates_all": stand_rows,
                "classification": str(classification),
                "late_stand_alternative_without_reentry": str(alt_status),
                "reason_pareto": _reason_pareto(stand_rows),
            }
        )
        rows.extend(stand_rows)

    class_counts = Counter(str(item.get("classification") or "") for item in critical_steps)
    reason_pareto = _reason_pareto(rows)
    dominant_reason = (reason_pareto[0]["reason"] if reason_pareto else None)

    return {
        "seed": (None if seed is None else int(seed)),
        "pallet_id": chosen_pid,
        "critical_start_step": int(critical_start_step),
        "max_critical_steps": int(max_critical_steps),
        "critical_reentry_steps": critical_steps,
        "breakdown_rows": rows,
        "summary": {
            "critical_steps_total": int(len(critical_steps)),
            "viable_late_stand_exists": int(class_counts.get(VIABLE_LATE_STAND_EXISTS, 0)),
            "late_stand_generated_but_rejected": int(class_counts.get(LATE_STAND_GENERATED_BUT_REJECTED, 0)),
            "late_stand_not_generated": int(class_counts.get(LATE_STAND_NOT_GENERATED, 0)),
            "no_plausible_late_stand": int(class_counts.get(NO_PLAUSIBLE_LATE_STAND, 0)),
            "dominant_rejection_reason": dominant_reason,
            "reason_pareto": reason_pareto,
        },
    }


def build_consolidated_late_stand_report(seed_reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    breakdown_rows: list[dict[str, Any]] = []
    class_counts: Counter[str] = Counter()

    for report in seed_reports:
        seed = report.get("seed")
        steps = [
            item
            for item in list(report.get("critical_reentry_steps", []) or [])
            if isinstance(item, Mapping)
        ]
        for entry in steps:
            classification = str(entry.get("classification") or "")
            class_counts[classification] += 1
            rows.append(
                {
                    "seed": seed,
                    "step": _as_int(entry.get("step"), default=-1),
                    "drop_mm": _as_int(entry.get("drop_mm"), default=0),
                    "classification": classification,
                    "late_stand_alternative_without_reentry": str(
                        entry.get("late_stand_alternative_without_reentry") or ALT_STATUS_NOT_FEASIBLE
                    ),
                    "reentry_placement": _placement_payload(
                        entry.get("reentry_placement") if isinstance(entry.get("reentry_placement"), Mapping) else None
                    ),
                    "top_feasible_candidates_current": list(entry.get("top_feasible_candidates_current", []) or []),
                }
            )

        for row in list(report.get("breakdown_rows", []) or []):
            if isinstance(row, Mapping):
                breakdown_rows.append(dict(row))

    rows.sort(key=lambda item: (_as_int(item.get("seed"), default=0), _as_int(item.get("step"), default=-1)))
    breakdown_rows.sort(
        key=lambda item: (
            _as_int(item.get("seed"), default=0),
            _as_int(item.get("step"), default=-1),
            _as_int(item.get("candidate_box_id"), default=-1),
            str(item.get("candidate_origin") or ""),
        )
    )

    reason_pareto = _reason_pareto(breakdown_rows)
    dominant_reason = reason_pareto[0]["reason"] if reason_pareto else None
    total_steps = int(len(rows))
    feasible_steps = int(class_counts.get(VIABLE_LATE_STAND_EXISTS, 0))

    return {
        "rows": rows,
        "breakdown_rows": breakdown_rows,
        "summary": {
            "critical_steps_total": total_steps,
            "viable_late_stand_exists": feasible_steps,
            "late_stand_generated_but_rejected": int(class_counts.get(LATE_STAND_GENERATED_BUT_REJECTED, 0)),
            "late_stand_not_generated": int(class_counts.get(LATE_STAND_NOT_GENERATED, 0)),
            "no_plausible_late_stand": int(class_counts.get(NO_PLAUSIBLE_LATE_STAND, 0)),
            "critical_steps_with_feasible_non_reentry_late_stand": feasible_steps,
            "critical_steps_without_feasible_non_reentry_late_stand": max(0, int(total_steps - feasible_steps)),
            "dominant_rejection_reason": dominant_reason,
            "reason_pareto": reason_pareto,
        },
    }
