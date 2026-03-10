from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..packer.maxrects2d import MaxRects2D, MaxRectsCandidate, Rect
from .layer_monotonicity import compute_layer_monotonicity_metrics

EVITABLE_BY_SELECTION = "EVITABLE por seleccion"
EVITABLE_BY_CANDIDATE_GENERATION = "EVITABLE potencialmente por candidate generation"
PROBABLY_UNAVOIDABLE = "PROBABLEMENTE INEVITABLE dado el estado geometrico"


@dataclass(frozen=True)
class _PlacementRow:
    step: int
    box_id: Any
    x_mm: int
    y_mm: int
    z_mm: int
    length_mm: int
    width_mm: int
    height_mm: int
    layer_id: int
    rot90: bool
    orientation_name: str | None
    orientation_family: str | None


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


def _to_placement_row(payload: Mapping[str, Any]) -> _PlacementRow:
    return _PlacementRow(
        step=_as_int(payload.get("step_index"), default=0),
        box_id=payload.get("box_id"),
        x_mm=_as_int(payload.get("x_mm"), default=0),
        y_mm=_as_int(payload.get("y_mm"), default=0),
        z_mm=_as_int(payload.get("z_mm"), default=0),
        length_mm=_as_int(payload.get("length_mm"), default=0),
        width_mm=_as_int(payload.get("width_mm"), default=0),
        height_mm=_as_int(payload.get("height_mm"), default=0),
        layer_id=_as_int(payload.get("layer_id"), default=0),
        rot90=bool(payload.get("rot90", False)),
        orientation_name=(None if payload.get("orientation_name") is None else str(payload.get("orientation_name"))),
        orientation_family=(
            None if payload.get("orientation_family") is None else str(payload.get("orientation_family"))
        ),
    )


def _default_bin_dims(params: Mapping[str, Any]) -> tuple[int, int, int]:
    overhang = max(0, _as_int(params.get("overhang_mm"), default=0))
    return 1200 + 2 * overhang, 800 + 2 * overhang, overhang


def _rect_union_boundary_metrics(
    rects: Sequence[Rect],
    *,
    bin_length_mm: int,
    bin_width_mm: int,
) -> dict[str, int]:
    if not rects:
        return {
            "largest_free_rect_area_mm2": 0,
            "boundary_connected_free_area_mm2": 0,
            "inaccessible_pocket_area_mm2": 0,
            "free_area_union_mm2": 0,
        }

    valid: list[Rect] = []
    for rect in rects:
        x0 = max(0, _as_int(getattr(rect, "x", 0), default=0))
        y0 = max(0, _as_int(getattr(rect, "y", 0), default=0))
        x1 = min(bin_length_mm, x0 + max(0, _as_int(getattr(rect, "w", 0), default=0)))
        y1 = min(bin_width_mm, y0 + max(0, _as_int(getattr(rect, "h", 0), default=0)))
        if x1 <= x0 or y1 <= y0:
            continue
        valid.append(Rect(x=x0, y=y0, w=(x1 - x0), h=(y1 - y0)))

    if not valid:
        return {
            "largest_free_rect_area_mm2": 0,
            "boundary_connected_free_area_mm2": 0,
            "inaccessible_pocket_area_mm2": 0,
            "free_area_union_mm2": 0,
        }

    x_coords = sorted({0, int(bin_length_mm), *[int(r.x) for r in valid], *[int(r.x + r.w) for r in valid]})
    y_coords = sorted({0, int(bin_width_mm), *[int(r.y) for r in valid], *[int(r.y + r.h) for r in valid]})

    x_index = {x: idx for idx, x in enumerate(x_coords)}
    y_index = {y: idx for idx, y in enumerate(y_coords)}
    nx = max(0, len(x_coords) - 1)
    ny = max(0, len(y_coords) - 1)

    if nx <= 0 or ny <= 0:
        return {
            "largest_free_rect_area_mm2": max((int(r.area) for r in valid), default=0),
            "boundary_connected_free_area_mm2": 0,
            "inaccessible_pocket_area_mm2": 0,
            "free_area_union_mm2": 0,
        }

    grid = [[False for _ in range(ny)] for _ in range(nx)]
    for rect in valid:
        ix0 = x_index[int(rect.x)]
        ix1 = x_index[int(rect.x + rect.w)]
        iy0 = y_index[int(rect.y)]
        iy1 = y_index[int(rect.y + rect.h)]
        for ix in range(ix0, ix1):
            for iy in range(iy0, iy1):
                grid[ix][iy] = True

    visited: set[tuple[int, int]] = set()
    total_area = 0
    boundary_area = 0

    for ix in range(nx):
        for iy in range(ny):
            if not grid[ix][iy] or (ix, iy) in visited:
                continue

            queue = [(ix, iy)]
            visited.add((ix, iy))
            component_area = 0
            touches_boundary = False

            while queue:
                cx, cy = queue.pop()
                cell_area = (x_coords[cx + 1] - x_coords[cx]) * (y_coords[cy + 1] - y_coords[cy])
                component_area += int(cell_area)
                if cx == 0 or cy == 0 or cx == nx - 1 or cy == ny - 1:
                    touches_boundary = True

                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx_i = cx + dx
                    ny_i = cy + dy
                    if nx_i < 0 or ny_i < 0 or nx_i >= nx or ny_i >= ny:
                        continue
                    if not grid[nx_i][ny_i] or (nx_i, ny_i) in visited:
                        continue
                    visited.add((nx_i, ny_i))
                    queue.append((nx_i, ny_i))

            total_area += int(component_area)
            if touches_boundary:
                boundary_area += int(component_area)

    inaccessible = max(0, int(total_area) - int(boundary_area))
    largest_rect = max((int(r.area) for r in valid), default=0)
    return {
        "largest_free_rect_area_mm2": int(largest_rect),
        "boundary_connected_free_area_mm2": int(boundary_area),
        "inaccessible_pocket_area_mm2": int(inaccessible),
        "free_area_union_mm2": int(total_area),
    }


def _rebuild_layer_bins(
    placements_before: Sequence[_PlacementRow],
    *,
    bin_length_mm: int,
    bin_width_mm: int,
    overhang_mm: int,
    heuristic: str,
) -> dict[int, MaxRects2D]:
    by_layer: dict[int, MaxRects2D] = {}
    for row in placements_before:
        layer = int(row.layer_id)
        current = by_layer.get(layer)
        if current is None:
            current = MaxRects2D(bin_length_mm, bin_width_mm, heuristic=heuristic)
            by_layer[layer] = current

        cand = MaxRectsCandidate(
            x=int(row.x_mm) + int(overhang_mm),
            y=int(row.y_mm) + int(overhang_mm),
            w=max(0, int(row.length_mm)),
            h=max(0, int(row.width_mm)),
            score=(0,),
        )
        if cand.w <= 0 or cand.h <= 0:
            continue
        try:
            current.place(cand)
        except Exception:
            continue
    return by_layer


def _used_area_union_mm2(
    rows: Sequence[_PlacementRow],
    *,
    bin_length_mm: int,
    bin_width_mm: int,
    overhang_mm: int,
    heuristic: str,
) -> int:
    bin_model = MaxRects2D(bin_length_mm, bin_width_mm, heuristic=heuristic)
    for row in rows:
        cand = MaxRectsCandidate(
            x=int(row.x_mm) + int(overhang_mm),
            y=int(row.y_mm) + int(overhang_mm),
            w=max(0, int(row.length_mm)),
            h=max(0, int(row.width_mm)),
            score=(0,),
        )
        if cand.w <= 0 or cand.h <= 0:
            continue
        try:
            bin_model.place(cand)
        except Exception:
            continue
    return int(getattr(bin_model, "used_area", 0) or 0)


def _event_by_step(trace: Sequence[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    out: dict[int, Mapping[str, Any]] = {}
    for entry in trace:
        step = _as_int(entry.get("step_index"), default=-1)
        if step < 0:
            continue
        out[step] = entry
    return out


def _placement_from_mapping(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = raw if isinstance(raw, Mapping) else {}
    return {
        "x_mm": _as_int(payload.get("x_mm"), default=0),
        "y_mm": _as_int(payload.get("y_mm"), default=0),
        "z_mm": _as_int(payload.get("z_mm"), default=0),
        "length_mm": _as_int(payload.get("length_mm"), default=0),
        "width_mm": _as_int(payload.get("width_mm"), default=0),
        "height_mm": _as_int(payload.get("height_mm"), default=0),
        "layer_id": _as_int(payload.get("layer_id"), default=0),
        "top_z_mm": _as_int(payload.get("top_z_mm"), default=0),
        "rot90": bool(payload.get("rot90", False)),
        "orientation_name": payload.get("orientation_name"),
        "orientation_family": payload.get("orientation_family"),
    }


def _candidate_rows(event: Mapping[str, Any], *, pallet_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in list(event.get("selection_pool", []) or []):
        if not isinstance(raw, Mapping):
            continue
        candidate_pid = _normalize_pid(raw.get("pallet_id"))
        if candidate_pid != pallet_id:
            continue
        preview = raw.get("preview", {}) if isinstance(raw.get("preview"), Mapping) else {}
        placement = _placement_from_mapping(preview.get("placement") if isinstance(preview, Mapping) else None)
        terms = raw.get("terms", {}) if isinstance(raw.get("terms"), Mapping) else {}
        rows.append(
            {
                "ramp_id": _as_int(raw.get("ramp_id"), default=0),
                "buffer_index": _as_int(raw.get("buffer_index"), default=0),
                "box_id": raw.get("box_id"),
                "pallet_id": candidate_pid,
                "placement": placement,
                "score": _as_float(terms.get("scalar_score"), default=_as_float(raw.get("score"), default=0.0)),
                "terms": dict(terms),
                "preview_debug": dict(preview.get("debug", {}) or {}),
            }
        )
    return rows


def _infeasible_rows(event: Mapping[str, Any], *, pallet_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in list(event.get("evaluated_items", []) or []):
        if not isinstance(raw, Mapping):
            continue
        candidate_pid = _normalize_pid(raw.get("pallet_id"))
        if candidate_pid != pallet_id:
            continue
        if bool(raw.get("feasible", False)):
            continue
        preview = raw.get("preview", {}) if isinstance(raw.get("preview"), Mapping) else {}
        rows.append(
            {
                "box_id": raw.get("box_id"),
                "ramp_id": _as_int(raw.get("ramp_id"), default=0),
                "buffer_index": _as_int(raw.get("buffer_index"), default=0),
                "reason": str(preview.get("infeasible_reason") or ""),
                "debug": dict(preview.get("debug", {}) or {}),
            }
        )
    return rows


def _reason_group(reason_exact: str) -> str:
    key = str(reason_exact or "")
    if key.startswith("support_"):
        return "support"
    if key.startswith("corners_") or key.startswith("com_"):
        return "corners"
    if key.startswith("settle_"):
        return "settle"
    if key.startswith("collision_"):
        return "collision"
    if key.startswith("free_rect_"):
        return "free_rect_unavailable"
    if key.startswith("candidate_not_generated"):
        return "candidate_not_generated"
    if key.startswith("loadbear_"):
        return "loadbear"
    if key.startswith("height_limit"):
        return "height_limit"
    if key.startswith("stand_gate_"):
        return "stand_gate"
    return "other"


def _is_rejection_row(row: Mapping[str, Any]) -> bool:
    reason = str(row.get("rejected_reason_exact") or "")
    if not reason:
        return False
    if reason in {"candidate_feasible_generated", "selection_score_lower_priority"}:
        return False
    if bool(row.get("feasible", False)) and bool(row.get("generated", False)):
        return False
    return True


def _normalize_reason_token(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "unknown"
    chars: list[str] = []
    for ch in raw:
        chars.append(ch if (ch.isalnum() or ch == "_") else "_")
    norm = "".join(chars).strip("_")
    while "__" in norm:
        norm = norm.replace("__", "_")
    return norm or "unknown"


def _make_breakdown_row(
    *,
    seed: int | None,
    step: int,
    prev_max_z_mm: int,
    reentry_z_mm: int,
    drop_mm: int,
    box_id: Any,
    candidate_origin: str,
    generated: bool | None,
    feasible: bool | None,
    non_reentry_candidate: bool | None,
    rejected_reason_exact: str,
    threshold_name: str | None = None,
    threshold_value: float | int | None = None,
    observed_name: str | None = None,
    observed_value: float | int | None = None,
    detail: str | None = None,
    candidate_box_id: Any | None = None,
    ramp_id: int | None = None,
    buffer_index: int | None = None,
    placement: Mapping[str, Any] | None = None,
    source_reason: str | None = None,
) -> dict[str, Any]:
    payload = {
        "seed": (None if seed is None else int(seed)),
        "step": int(step),
        "z_prev_max_mm": int(prev_max_z_mm),
        "z_reentry_mm": int(reentry_z_mm),
        "drop_mm": int(drop_mm),
        "reentry_box_id": box_id,
        "candidate_origin": str(candidate_origin),
        "generated": generated,
        "feasible": feasible,
        "non_reentry_candidate": non_reentry_candidate,
        "rejected_reason_exact": str(rejected_reason_exact),
        "threshold_name": threshold_name,
        "threshold_value": threshold_value,
        "observed_name": observed_name,
        "observed_value": observed_value,
        "detail": detail,
        "candidate_box_id": candidate_box_id,
        "ramp_id": ramp_id,
        "buffer_index": buffer_index,
        "placement": _placement_from_mapping(placement if isinstance(placement, Mapping) else None),
        "source_reason": source_reason,
    }
    return payload


def _map_rejection_reason(
    *,
    raw_reason: Any,
    debug: Mapping[str, Any],
    params: Mapping[str, Any],
) -> tuple[str, str | None, float | int | None, str | None, float | int | None, str | None]:
    reason = str(raw_reason or "").strip().upper()
    if reason == "SUPPORT_RATIO":
        required = _as_float(debug.get("required_support_ratio"), default=_as_float(params.get("min_support"), default=0.0))
        observed = _as_float(debug.get("support_ratio"), default=0.0)
        return (
            "support_surface_ratio_below_threshold",
            "required_support_ratio",
            float(required),
            "support_ratio",
            float(observed),
            None,
        )
    if reason == "CORNER_SUPPORT":
        com_supported = bool(debug.get("com_supported", False))
        corners_supported = bool(debug.get("corners_supported", False))
        corners_supported_count = _as_int(
            debug.get("corners_supported_count"),
            default=(4 if corners_supported else 0),
        )
        com_margin_mm = _as_float(
            debug.get("com_margin_mm"),
            default=(0.0 if com_supported else -1.0),
        )
        if not corners_supported:
            return (
                "corners_unsupported",
                "corners_required_count",
                4,
                "corners_supported_count",
                int(corners_supported_count),
                f"corners support check failed ({int(corners_supported_count)}/4)",
            )
        if not com_supported:
            return (
                "com_margin_fail",
                "com_margin_min_mm",
                0.0,
                "com_margin_mm",
                float(com_margin_mm),
                "center of mass not supported",
            )
        return (
            "corners_unsupported",
            "corners_required_count",
            4,
            "corners_supported_count",
            int(corners_supported_count),
            "corners support check failed",
        )
    if reason == "LOADBEAR":
        max_over = _as_float(params.get("max_overweight_ratio"), default=1.5)
        observed = _as_float(debug.get("loadbear_ratio"), default=0.0)
        return (
            "loadbear_fail",
            "max_overweight_ratio",
            float(max_over),
            "loadbear_ratio",
            float(observed),
            None,
        )
    if reason == "COLLISION":
        return (
            "collision_fail",
            None,
            None,
            None,
            None,
            None,
        )
    if reason == "SETTLE":
        settle_max_iter = _as_int(params.get("settle_max_iter"), default=0)
        observed = _as_float(debug.get("settle_mm"), default=0.0)
        return (
            "settle_failed",
            "settle_max_iter",
            int(settle_max_iter),
            "settle_mm",
            float(observed),
            None,
        )
    token = _normalize_reason_token(reason)
    return (f"other_exact_{token}", None, None, None, None, None)


def _hidden_non_reentry_candidates(
    *,
    candidate: Mapping[str, Any],
    prev_max_z_mm: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    debug = candidate.get("preview_debug", {}) if isinstance(candidate.get("preview_debug"), Mapping) else {}
    top = debug.get("feasible_candidates_top", []) if isinstance(debug.get("feasible_candidates_top"), list) else []
    for item in top:
        if not isinstance(item, Mapping):
            continue
        placement = _placement_from_mapping(item)
        placement["top_z_mm"] = int(placement.get("z_mm", 0)) + int(placement.get("height_mm", 0))
        if int(placement.get("z_mm", 0)) < int(prev_max_z_mm):
            continue
        out.append(
            {
                "placement": placement,
                "objective": _as_float(item.get("objective"), default=0.0),
            }
        )
    return out


def _rows_from_selection_pool(
    *,
    seed: int | None,
    step: int,
    prev_max_z_mm: int,
    reentry_z_mm: int,
    drop_mm: int,
    reentry_box_id: Any,
    candidates: list[dict[str, Any]],
    selected_score: float | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    alternatives_without_reentry: list[dict[str, Any]] = []
    hidden_candidates: list[dict[str, Any]] = []

    hidden_seen: set[tuple[int, int, int, int, int, int]] = set()
    for candidate in candidates:
        placement = _placement_from_mapping(candidate.get("placement") if isinstance(candidate, Mapping) else None)
        non_reentry = bool(int(placement.get("z_mm", 0)) >= int(prev_max_z_mm))
        if non_reentry:
            alternatives_without_reentry.append(candidate)

        reason = "candidate_feasible_generated"
        threshold_name = None
        threshold_value = None
        observed_name = None
        observed_value = None
        if non_reentry and selected_score is not None and isinstance(candidate.get("score"), (int, float)):
            reason = "selection_score_lower_priority"
            threshold_name = "selected_scalar_score"
            threshold_value = float(selected_score)
            observed_name = "candidate_scalar_score"
            observed_value = float(candidate.get("score", 0.0))

        rows.append(
            _make_breakdown_row(
                seed=seed,
                step=step,
                prev_max_z_mm=prev_max_z_mm,
                reentry_z_mm=reentry_z_mm,
                drop_mm=drop_mm,
                box_id=reentry_box_id,
                candidate_origin="selection_pool",
                generated=True,
                feasible=True,
                non_reentry_candidate=non_reentry,
                rejected_reason_exact=reason,
                threshold_name=threshold_name,
                threshold_value=threshold_value,
                observed_name=observed_name,
                observed_value=observed_value,
                candidate_box_id=candidate.get("box_id"),
                ramp_id=_as_int(candidate.get("ramp_id"), default=0),
                buffer_index=_as_int(candidate.get("buffer_index"), default=0),
                placement=placement,
                source_reason=None,
            )
        )

        for hidden in _hidden_non_reentry_candidates(candidate=candidate, prev_max_z_mm=prev_max_z_mm):
            hidden_placement = _placement_from_mapping(hidden.get("placement") if isinstance(hidden, Mapping) else None)
            key = (
                _as_int(candidate.get("box_id"), default=-1),
                int(hidden_placement.get("x_mm", 0)),
                int(hidden_placement.get("y_mm", 0)),
                int(hidden_placement.get("z_mm", 0)),
                int(hidden_placement.get("length_mm", 0)),
                int(hidden_placement.get("width_mm", 0)),
            )
            if key in hidden_seen:
                continue
            hidden_seen.add(key)
            hidden_candidates.append(
                {
                    "box_id": candidate.get("box_id"),
                    "ramp_id": _as_int(candidate.get("ramp_id"), default=0),
                    "buffer_index": _as_int(candidate.get("buffer_index"), default=0),
                    "placement": hidden_placement,
                    "objective": _as_float(hidden.get("objective"), default=0.0),
                    "selected_z_mm": int(placement.get("z_mm", 0)),
                }
            )

    for hidden in hidden_candidates:
        rows.append(
            _make_breakdown_row(
                seed=seed,
                step=step,
                prev_max_z_mm=prev_max_z_mm,
                reentry_z_mm=reentry_z_mm,
                drop_mm=drop_mm,
                box_id=reentry_box_id,
                candidate_origin="selection_pool_hidden",
                generated=False,
                feasible=None,
                non_reentry_candidate=True,
                rejected_reason_exact="candidate_not_generated",
                threshold_name="reentry_floor_z_mm",
                threshold_value=int(prev_max_z_mm),
                observed_name="hidden_candidate_z_mm",
                observed_value=int(hidden.get("placement", {}).get("z_mm", 0)),
                detail="non-reentry candidate present in packer preview top but not surfaced to scheduler pool",
                candidate_box_id=hidden.get("box_id"),
                ramp_id=_as_int(hidden.get("ramp_id"), default=0),
                buffer_index=_as_int(hidden.get("buffer_index"), default=0),
                placement=hidden.get("placement") if isinstance(hidden.get("placement"), Mapping) else None,
                source_reason="HIDDEN_FROM_PREVIEW_TOP",
            )
        )

    return rows, alternatives_without_reentry, hidden_candidates


def _rows_from_infeasible_item(
    *,
    seed: int | None,
    step: int,
    prev_max_z_mm: int,
    reentry_z_mm: int,
    drop_mm: int,
    reentry_box_id: Any,
    params: Mapping[str, Any],
    item: Mapping[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    source_reason = str(item.get("reason") or "").upper()
    debug = item.get("debug", {}) if isinstance(item.get("debug"), Mapping) else {}

    if source_reason == "STABILITY":
        stability_candidates = debug.get("stability_candidates", [])
        if isinstance(stability_candidates, list) and stability_candidates:
            for raw in stability_candidates:
                if not isinstance(raw, Mapping):
                    continue
                placement = _placement_from_mapping(raw)
                raw_reason = raw.get("rejection_reason") or debug.get("rejection_reason")
                reason_exact, thr_name, thr_value, obs_name, obs_value, detail = _map_rejection_reason(
                    raw_reason=raw_reason,
                    debug=raw,
                    params=params,
                )
                non_reentry_candidate: bool | None
                if "z_mm" in raw:
                    non_reentry_candidate = bool(int(placement.get("z_mm", 0)) >= int(prev_max_z_mm))
                else:
                    non_reentry_candidate = None
                out.append(
                    _make_breakdown_row(
                        seed=seed,
                        step=step,
                        prev_max_z_mm=prev_max_z_mm,
                        reentry_z_mm=reentry_z_mm,
                        drop_mm=drop_mm,
                        box_id=reentry_box_id,
                        candidate_origin="stability_candidates",
                        generated=True,
                        feasible=False,
                        non_reentry_candidate=non_reentry_candidate,
                        rejected_reason_exact=reason_exact,
                        threshold_name=thr_name,
                        threshold_value=thr_value,
                        observed_name=obs_name,
                        observed_value=obs_value,
                        detail=detail,
                        candidate_box_id=item.get("box_id"),
                        ramp_id=_as_int(item.get("ramp_id"), default=0),
                        buffer_index=_as_int(item.get("buffer_index"), default=0),
                        placement=placement,
                        source_reason=source_reason,
                    )
                )
            return out

        out.append(
            _make_breakdown_row(
                seed=seed,
                step=step,
                prev_max_z_mm=prev_max_z_mm,
                reentry_z_mm=reentry_z_mm,
                drop_mm=drop_mm,
                box_id=reentry_box_id,
                candidate_origin="infeasible_item",
                generated=False,
                feasible=False,
                non_reentry_candidate=None,
                rejected_reason_exact="stability_reject_unknown",
                detail="preview infeasible by stability without candidate details",
                candidate_box_id=item.get("box_id"),
                ramp_id=_as_int(item.get("ramp_id"), default=0),
                buffer_index=_as_int(item.get("buffer_index"), default=0),
                placement=None,
                source_reason=source_reason,
            )
        )
        return out

    if source_reason == "HEIGHT_LIMIT":
        max_height = _as_int(params.get("max_height_mm"), default=0)
        height_candidates = debug.get("height_limit_candidates", [])
        if isinstance(height_candidates, list) and height_candidates:
            for raw in height_candidates:
                if not isinstance(raw, Mapping):
                    continue
                placement = _placement_from_mapping(raw)
                top_z = _as_int(raw.get("top_z_mm"), default=0)
                max_h = _as_int(raw.get("max_height_mm"), default=max_height)
                out.append(
                    _make_breakdown_row(
                        seed=seed,
                        step=step,
                        prev_max_z_mm=prev_max_z_mm,
                        reentry_z_mm=reentry_z_mm,
                        drop_mm=drop_mm,
                        box_id=reentry_box_id,
                        candidate_origin="height_limit_candidates",
                        generated=True,
                        feasible=False,
                        non_reentry_candidate=bool(int(placement.get("z_mm", 0)) >= int(prev_max_z_mm)),
                        rejected_reason_exact="height_limit_fail",
                        threshold_name="max_height_mm",
                        threshold_value=int(max_h),
                        observed_name="candidate_top_z_mm",
                        observed_value=int(top_z),
                        detail=None,
                        candidate_box_id=item.get("box_id"),
                        ramp_id=_as_int(item.get("ramp_id"), default=0),
                        buffer_index=_as_int(item.get("buffer_index"), default=0),
                        placement=placement,
                        source_reason=source_reason,
                    )
                )
            return out

        out.append(
            _make_breakdown_row(
                seed=seed,
                step=step,
                prev_max_z_mm=prev_max_z_mm,
                reentry_z_mm=reentry_z_mm,
                drop_mm=drop_mm,
                box_id=reentry_box_id,
                candidate_origin="infeasible_item",
                generated=False,
                feasible=False,
                non_reentry_candidate=None,
                rejected_reason_exact="height_limit_fail",
                threshold_name="max_height_mm",
                threshold_value=int(max_height),
                observed_name="height_used_mm",
                observed_value=_as_int(debug.get("height_used"), default=0),
                candidate_box_id=item.get("box_id"),
                ramp_id=_as_int(item.get("ramp_id"), default=0),
                buffer_index=_as_int(item.get("buffer_index"), default=0),
                placement=None,
                source_reason=source_reason,
            )
        )
        return out

    reason_map = {
        "NO_SPACE": "free_rect_unavailable",
        "CANDIDATE_LIMIT": "candidate_not_generated_candidate_limit",
        "TIMEOUT": "candidate_not_generated_timeout",
        "MANIFEST_BLOCKED": "stand_gate_blocked",
        "OVERSIZE": "grid_or_footprint_fail",
    }
    reason_exact = reason_map.get(source_reason)
    if reason_exact is None:
        reason_exact = f"other_exact_{_normalize_reason_token(source_reason)}"

    out.append(
        _make_breakdown_row(
            seed=seed,
            step=step,
            prev_max_z_mm=prev_max_z_mm,
            reentry_z_mm=reentry_z_mm,
            drop_mm=drop_mm,
            box_id=reentry_box_id,
            candidate_origin="infeasible_item",
            generated=False,
            feasible=False,
            non_reentry_candidate=None,
            rejected_reason_exact=reason_exact,
            candidate_box_id=item.get("box_id"),
            ramp_id=_as_int(item.get("ramp_id"), default=0),
            buffer_index=_as_int(item.get("buffer_index"), default=0),
            placement=None,
            source_reason=source_reason,
        )
    )
    return out


def _infer_dominant_cause(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return "other_exact_no_evidence"

    focus: list[str] = []
    fallback: list[str] = []
    for row in rows:
        if not _is_rejection_row(row):
            continue
        reason = str(row.get("rejected_reason_exact") or "other_exact_unknown")
        fallback.append(reason)
        non_reentry = row.get("non_reentry_candidate")
        if non_reentry is True:
            focus.append(reason)

    if focus:
        return Counter(focus).most_common(1)[0][0]
    if not fallback:
        return "other_exact_no_evidence"
    return Counter(fallback).most_common(1)[0][0]


def _alternative_loss_reason(
    *,
    selected_score: float | None,
    alternatives_without_reentry: list[dict[str, Any]],
) -> str:
    if not alternatives_without_reentry:
        return "no_non_reentry_alternative"
    alt_scores = [item.get("score") for item in alternatives_without_reentry]
    alt_scores = [float(v) for v in alt_scores if isinstance(v, (int, float))]
    if selected_score is None or not alt_scores:
        return "non_reentry_available_score_unknown"
    best_alt = max(alt_scores)
    if abs(float(selected_score) - float(best_alt)) <= 1e-6:
        return "score_tie"
    if float(best_alt) > float(selected_score):
        return "selection_score_regression"
    return "selection_score_tradeoff"


def build_seed_reentry_report_from_dump(
    *,
    dump_payload: Mapping[str, Any],
    seed: int | None = None,
    layer_band_mm: int = 100,
) -> dict[str, Any]:
    params = dump_payload.get("params", {}) if isinstance(dump_payload.get("params"), Mapping) else {}
    forced_dest = params.get("force_destination")
    pallets = dump_payload.get("pallets", {}) if isinstance(dump_payload.get("pallets"), Mapping) else {}
    decision_trace_all = (
        dump_payload.get("decision_trace", {}) if isinstance(dump_payload.get("decision_trace"), Mapping) else {}
    )

    chosen_pid: str | None = None
    if forced_dest is not None:
        forced_key = str(_as_int(forced_dest, default=-1))
        if forced_key in pallets:
            chosen_pid = forced_key
    if chosen_pid is None:
        keys = sorted((str(k) for k in pallets.keys()))
        chosen_pid = keys[0] if keys else None

    if chosen_pid is None:
        return {
            "seed": (None if seed is None else int(seed)),
            "pallet_id": None,
            "reentries": [],
            "reentry_count": 0,
            "summary": {
                "avoidable_by_selection": 0,
                "avoidable_potential_candidate_generation": 0,
                "probably_unavoidable": 0,
                "dominant_cause": None,
                "reason_pareto": [],
            },
        }

    placements_raw = pallets.get(chosen_pid, [])
    decision_trace_raw = decision_trace_all.get(chosen_pid, [])
    placements = [_to_placement_row(item) for item in placements_raw if isinstance(item, Mapping)]
    placements.sort(key=lambda row: int(row.step))
    decision_by_step = _event_by_step([item for item in decision_trace_raw if isinstance(item, Mapping)])

    bin_length_mm, bin_width_mm, overhang_mm = _default_bin_dims(params)
    bin_area_mm2 = int(bin_length_mm) * int(bin_width_mm)
    heuristic = str(params.get("heuristic", "baf") or "baf")

    reentries: list[dict[str, Any]] = []
    max_z_seen = 0
    for idx, row in enumerate(placements):
        z_mm = int(row.z_mm)
        prev_max_z = int(max_z_seen)
        is_reentry = idx > 0 and int(z_mm) < int(prev_max_z)
        if is_reentry:
            prefix = placements[:idx]
            layer_bins = _rebuild_layer_bins(
                prefix,
                bin_length_mm=bin_length_mm,
                bin_width_mm=bin_width_mm,
                overhang_mm=overhang_mm,
                heuristic=heuristic,
            )
            target_bin = layer_bins.get(int(row.layer_id))
            if target_bin is None:
                free_rects = [Rect(0, 0, int(bin_length_mm), int(bin_width_mm))]
            else:
                free_rects = list(target_bin.free_rects)
            free_metrics = _rect_union_boundary_metrics(
                free_rects,
                bin_length_mm=int(bin_length_mm),
                bin_width_mm=int(bin_width_mm),
            )

            mono_before = compute_layer_monotonicity_metrics(
                [
                    {
                        "z_mm": int(item.z_mm),
                        "layer_id": int(item.layer_id),
                        "length_mm": int(item.length_mm),
                        "width_mm": int(item.width_mm),
                        "height_mm": int(item.height_mm),
                    }
                    for item in prefix
                ],
                layer_band_mm=int(layer_band_mm),
                bin_area_mm2=int(bin_area_mm2),
            )
            base_floor_rows = [item for item in prefix if int(item.z_mm) <= 0]
            target_layer_rows = [item for item in prefix if int(item.layer_id) == int(row.layer_id)]
            base_used = _used_area_union_mm2(
                base_floor_rows,
                bin_length_mm=bin_length_mm,
                bin_width_mm=bin_width_mm,
                overhang_mm=overhang_mm,
                heuristic=heuristic,
            )
            layer_used = _used_area_union_mm2(
                target_layer_rows,
                bin_length_mm=bin_length_mm,
                bin_width_mm=bin_width_mm,
                overhang_mm=overhang_mm,
                heuristic=heuristic,
            )

            event = decision_by_step.get(int(row.step), {})
            feasible_candidates = _candidate_rows(event, pallet_id=_normalize_pid(chosen_pid))
            infeasible = _infeasible_rows(event, pallet_id=_normalize_pid(chosen_pid))

            selected = event.get("selected", {}) if isinstance(event.get("selected"), Mapping) else {}
            selected_terms = selected.get("terms", {}) if isinstance(selected.get("terms"), Mapping) else {}
            selected_score = (
                _as_float(selected_terms.get("scalar_score"))
                if selected_terms
                else (
                    _as_float(selected.get("score"))
                    if isinstance(selected.get("score"), (int, float))
                    else None
                )
            )

            breakdown_rows, alternatives_without_reentry, hidden_candidates = _rows_from_selection_pool(
                seed=seed,
                step=int(row.step),
                prev_max_z_mm=int(prev_max_z),
                reentry_z_mm=int(z_mm),
                drop_mm=int(prev_max_z - z_mm),
                reentry_box_id=row.box_id,
                candidates=feasible_candidates,
                selected_score=selected_score,
            )

            for item in infeasible:
                breakdown_rows.extend(
                    _rows_from_infeasible_item(
                        seed=seed,
                        step=int(row.step),
                        prev_max_z_mm=int(prev_max_z),
                        reentry_z_mm=int(z_mm),
                        drop_mm=int(prev_max_z - z_mm),
                        reentry_box_id=row.box_id,
                        params=params,
                        item=item,
                    )
                )

            if alternatives_without_reentry:
                classification = EVITABLE_BY_SELECTION
            elif hidden_candidates:
                classification = EVITABLE_BY_CANDIDATE_GENERATION
            else:
                classification = PROBABLY_UNAVOIDABLE

            dominant_cause = _infer_dominant_cause(breakdown_rows)
            loss_reason = _alternative_loss_reason(
                selected_score=selected_score,
                alternatives_without_reentry=alternatives_without_reentry,
            )

            reason_counts = Counter(
                str(item.get("rejected_reason_exact") or "")
                for item in breakdown_rows
                if _is_rejection_row(item)
            )
            total_breakdown = int(sum(reason_counts.values()))
            reason_pareto = [
                {
                    "reason": reason,
                    "count": int(count),
                    "pct": (float(count) / float(total_breakdown) if total_breakdown > 0 else 0.0),
                }
                for reason, count in reason_counts.most_common()
                if reason
            ]

            reentries.append(
                {
                    "step": int(row.step),
                    "z_prev_max_mm": int(prev_max_z),
                    "z_reentry_mm": int(z_mm),
                    "drop_mm": int(prev_max_z - z_mm),
                    "box_id": row.box_id,
                    "chosen_placement": {
                        "x_mm": int(row.x_mm),
                        "y_mm": int(row.y_mm),
                        "z_mm": int(row.z_mm),
                        "length_mm": int(row.length_mm),
                        "width_mm": int(row.width_mm),
                        "height_mm": int(row.height_mm),
                        "orientation_name": row.orientation_name,
                        "orientation_family": row.orientation_family,
                        "rot90": bool(row.rot90),
                        "layer_id": int(row.layer_id),
                    },
                    "geometry_state_before_reentry": {
                        "largest_free_rect_area_mm2": int(free_metrics["largest_free_rect_area_mm2"]),
                        "boundary_connected_free_area_mm2": int(free_metrics["boundary_connected_free_area_mm2"]),
                        "inaccessible_pocket_area_mm2": int(free_metrics["inaccessible_pocket_area_mm2"]),
                        "base_layer_closure": {
                            "base_fill_ratio": float(base_used / max(1, int(bin_area_mm2))),
                            "layer_fill_ratio": float(layer_used / max(1, int(bin_area_mm2))),
                            "layer_closure_score": float(_as_float(mono_before.get("layer_closure_score"), default=1.0)),
                        },
                        "max_z_seen_so_far": int(prev_max_z),
                        "active_layers_over_time": list(mono_before.get("active_layers_over_time", []) or []),
                    },
                    "feasible_candidates_step": feasible_candidates,
                    "infeasible_candidates_step": infeasible,
                    "alternatives_without_reentry": alternatives_without_reentry,
                    "hidden_box_level_alternatives": hidden_candidates,
                    "had_alternative_without_reentry": bool(alternatives_without_reentry),
                    "alternative_loss_reason": str(loss_reason),
                    "breakdown_rows": breakdown_rows,
                    "reason_pareto": reason_pareto,
                    "dominant_cause": str(dominant_cause),
                    "classification": str(classification),
                }
            )

        max_z_seen = max(int(max_z_seen), int(z_mm))

    class_counts = Counter(str(item.get("classification")) for item in reentries)
    cause_counts = Counter(str(item.get("dominant_cause")) for item in reentries if item.get("dominant_cause"))
    reason_counter = Counter()
    group_counter = Counter()
    total_rejection_rows = 0
    for entry in reentries:
        rows = [item for item in list(entry.get("breakdown_rows", []) or []) if isinstance(item, Mapping)]
        for row_item in rows:
            if not _is_rejection_row(row_item):
                continue
            total_rejection_rows += 1
            reason = str(row_item.get("rejected_reason_exact") or "")
            if not reason:
                continue
            reason_counter[reason] += 1
            group_counter[_reason_group(reason)] += 1

    reason_pareto = [
        {
            "reason": reason,
            "count": int(count),
            "pct": (float(count) / float(total_rejection_rows) if total_rejection_rows > 0 else 0.0),
            "group": _reason_group(reason),
        }
        for reason, count in reason_counter.most_common()
    ]
    group_pareto = [
        {
            "group": group,
            "count": int(count),
            "pct": (float(count) / float(total_rejection_rows) if total_rejection_rows > 0 else 0.0),
        }
        for group, count in group_counter.most_common()
    ]

    return {
        "seed": (None if seed is None else int(seed)),
        "pallet_id": str(chosen_pid),
        "reentries": reentries,
        "reentry_count": int(len(reentries)),
        "summary": {
            "avoidable_by_selection": int(class_counts.get(EVITABLE_BY_SELECTION, 0)),
            "avoidable_potential_candidate_generation": int(class_counts.get(EVITABLE_BY_CANDIDATE_GENERATION, 0)),
            "probably_unavoidable": int(class_counts.get(PROBABLY_UNAVOIDABLE, 0)),
            "dominant_cause": (cause_counts.most_common(1)[0][0] if cause_counts else None),
            "reason_pareto": reason_pareto,
            "group_pareto": group_pareto,
        },
    }


def build_consolidated_reentry_report(seed_reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    breakdown_rows: list[dict[str, Any]] = []
    class_counts = Counter()
    cause_counts = Counter()
    reason_counts = Counter()
    group_counts = Counter()
    critical_by_step: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"seed_count": 0, "downstream_reentries": 0, "seeds": []}
    )

    for report in seed_reports:
        seed = report.get("seed")
        reentries = [item for item in list(report.get("reentries", []) or []) if isinstance(item, Mapping)]
        for idx, entry in enumerate(reentries):
            classification = str(entry.get("classification"))
            cause = str(entry.get("dominant_cause"))
            class_counts[classification] += 1
            if cause:
                cause_counts[cause] += 1

            step = _as_int(entry.get("step"), default=-1)
            rows.append(
                {
                    "seed": seed,
                    "step": step,
                    "drop_mm": _as_int(entry.get("drop_mm"), default=0),
                    "box_id": entry.get("box_id"),
                    "had_alternative_without_reentry": bool(entry.get("had_alternative_without_reentry", False)),
                    "dominant_cause": cause,
                    "classification": classification,
                }
            )

            step_breakdown = [item for item in list(entry.get("breakdown_rows", []) or []) if isinstance(item, Mapping)]
            for row in step_breakdown:
                if not _is_rejection_row(row):
                    breakdown_rows.append(dict(row))
                    continue
                reason = str(row.get("rejected_reason_exact") or "")
                if reason:
                    reason_counts[reason] += 1
                    group_counts[_reason_group(reason)] += 1
                breakdown_rows.append(dict(row))

            if idx == 0 and len(reentries) > 1:
                if step >= 0:
                    slot = critical_by_step[step]
                    slot["seed_count"] = int(slot["seed_count"]) + 1
                    slot["downstream_reentries"] = int(slot["downstream_reentries"]) + int(len(reentries) - 1)
                    slot["seeds"].append(seed)

    rows.sort(key=lambda row: (_as_int(row.get("seed"), default=0), _as_int(row.get("step"), default=-1)))
    breakdown_rows.sort(
        key=lambda row: (
            _as_int(row.get("seed"), default=0),
            _as_int(row.get("step"), default=-1),
            str(row.get("candidate_origin") or ""),
            _as_int(row.get("candidate_box_id"), default=-1),
        )
    )

    first_critical_reentry = None
    if critical_by_step:
        step, info = max(
            critical_by_step.items(),
            key=lambda item: (int(item[1]["downstream_reentries"]), int(item[1]["seed_count"]), -int(item[0])),
        )
        first_critical_reentry = {
            "step": int(step),
            "seed_count": int(info["seed_count"]),
            "downstream_reentries": int(info["downstream_reentries"]),
            "seeds": list(info["seeds"]),
        }

    total_rejection_rows = int(sum(reason_counts.values()))
    reason_pareto = [
        {
            "reason": reason,
            "count": int(count),
            "pct": (float(count) / float(total_rejection_rows) if total_rejection_rows > 0 else 0.0),
            "group": _reason_group(reason),
        }
        for reason, count in reason_counts.most_common()
    ]
    group_pareto = [
        {
            "group": group,
            "count": int(count),
            "pct": (float(count) / float(total_rejection_rows) if total_rejection_rows > 0 else 0.0),
        }
        for group, count in group_counts.most_common()
    ]

    return {
        "rows": rows,
        "breakdown_rows": breakdown_rows,
        "summary": {
            "total_reentries": int(len(rows)),
            "avoidable_by_selection": int(class_counts.get(EVITABLE_BY_SELECTION, 0)),
            "avoidable_potential_candidate_generation": int(
                class_counts.get(EVITABLE_BY_CANDIDATE_GENERATION, 0)
            ),
            "probably_unavoidable": int(class_counts.get(PROBABLY_UNAVOIDABLE, 0)),
            "dominant_cause_global": (cause_counts.most_common(1)[0][0] if cause_counts else None),
            "first_critical_reentry": first_critical_reentry,
            "reason_pareto": reason_pareto,
            "group_pareto": group_pareto,
        },
    }
