from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..packer.maxrects2d import MaxRects2D, MaxRectsCandidate, Rect
from .layer_monotonicity import compute_layer_monotonicity_metrics


EVITABLE_BY_SELECTION = "EVITABLE por selección"
EVITABLE_BY_CANDIDATE_GENERATION = "EVITABLE potencialmente por candidate generation"
PROBABLY_UNAVOIDABLE = "PROBABLEMENTE INEVITABLE dado el estado geométrico"


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
            # Observabilidad best-effort: si el replay no cuadra, continuamos.
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


def _candidate_rows(event: Mapping[str, Any], *, pallet_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in list(event.get("selection_pool", []) or []):
        if not isinstance(raw, Mapping):
            continue
        candidate_pid = _normalize_pid(raw.get("pallet_id"))
        if candidate_pid != pallet_id:
            continue
        preview = raw.get("preview", {}) if isinstance(raw.get("preview"), Mapping) else {}
        placement = preview.get("placement", {}) if isinstance(preview.get("placement"), Mapping) else {}
        terms = raw.get("terms", {}) if isinstance(raw.get("terms"), Mapping) else {}
        rows.append(
            {
                "ramp_id": _as_int(raw.get("ramp_id"), default=0),
                "buffer_index": _as_int(raw.get("buffer_index"), default=0),
                "box_id": raw.get("box_id"),
                "pallet_id": candidate_pid,
                "placement": {
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
                },
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
                "reason": str(preview.get("infeasible_reason") or ""),
                "debug": dict(preview.get("debug", {}) or {}),
            }
        )
    return rows


def _infer_dominant_cause(
    *,
    alternatives_without_reentry: list[dict[str, Any]],
    hidden_box_level_alternatives: list[dict[str, Any]],
    infeasible_rows: list[dict[str, Any]],
    feasible_rows: list[dict[str, Any]],
) -> str:
    if alternatives_without_reentry:
        return "score"
    if hidden_box_level_alternatives:
        return "free-rect / candidate generation"

    if not infeasible_rows:
        if len(feasible_rows) <= 1:
            return "disponibilidad"
        return "otra restricción"

    reasons = Counter(str(item.get("reason") or "").upper() for item in infeasible_rows)
    dominant_reason = reasons.most_common(1)[0][0] if reasons else ""

    rejection_reasons = Counter()
    settle_mm_positive = 0
    for item in infeasible_rows:
        debug = item.get("debug", {}) if isinstance(item.get("debug"), Mapping) else {}
        rr = str(debug.get("rejection_reason") or "").upper()
        if rr:
            rejection_reasons[rr] += 1
        if _as_float(debug.get("settle_mm"), default=0.0) > 0.0:
            settle_mm_positive += 1

    if "COLLISION" in rejection_reasons:
        return "colisión"
    if settle_mm_positive > 0 and dominant_reason == "STABILITY":
        return "settling"
    if dominant_reason == "STABILITY":
        return "soporte"
    if dominant_reason == "NO_SPACE":
        return "free-rect / candidate generation"
    if dominant_reason in {"HEIGHT_LIMIT", "TIMEOUT", "CANDIDATE_LIMIT"}:
        return "otra restricción"
    return "otra restricción"


def _alternative_loss_reason(
    *,
    selected_score: float | None,
    alternatives_without_reentry: list[dict[str, Any]],
) -> str:
    if not alternatives_without_reentry:
        return "no disponibilidad"
    alt_scores = [item.get("score") for item in alternatives_without_reentry]
    alt_scores = [float(v) for v in alt_scores if isinstance(v, (int, float))]
    if selected_score is None or not alt_scores:
        return "no equivalencia"
    best_alt = max(alt_scores)
    if abs(float(selected_score) - float(best_alt)) <= 1e-6:
        return "no equivalencia"
    return "score"


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
            alternatives_without_reentry = [
                candidate
                for candidate in feasible_candidates
                if _as_int(candidate["placement"].get("z_mm"), default=0) >= int(prev_max_z)
            ]
            hidden_box_level_alternatives = [
                candidate
                for candidate in feasible_candidates
                if _as_int(candidate["placement"].get("z_mm"), default=0) < int(prev_max_z)
                and _as_int(candidate.get("preview_debug", {}).get("feasible_candidates_max_z_mm"), default=-1)
                >= int(prev_max_z)
            ]
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

            if alternatives_without_reentry:
                classification = EVITABLE_BY_SELECTION
            elif hidden_box_level_alternatives:
                classification = EVITABLE_BY_CANDIDATE_GENERATION
            else:
                classification = PROBABLY_UNAVOIDABLE

            dominant_cause = _infer_dominant_cause(
                alternatives_without_reentry=alternatives_without_reentry,
                hidden_box_level_alternatives=hidden_box_level_alternatives,
                infeasible_rows=infeasible,
                feasible_rows=feasible_candidates,
            )
            loss_reason = _alternative_loss_reason(
                selected_score=selected_score,
                alternatives_without_reentry=alternatives_without_reentry,
            )

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
                    "alternatives_without_reentry": alternatives_without_reentry,
                    "hidden_box_level_alternatives": hidden_box_level_alternatives,
                    "had_alternative_without_reentry": bool(alternatives_without_reentry),
                    "alternative_loss_reason": str(loss_reason),
                    "infeasible_candidates_step": infeasible,
                    "dominant_cause": str(dominant_cause),
                    "classification": str(classification),
                }
            )

        max_z_seen = max(int(max_z_seen), int(z_mm))

    class_counts = Counter(str(item.get("classification")) for item in reentries)
    cause_counts = Counter(str(item.get("dominant_cause")) for item in reentries if item.get("dominant_cause"))

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
        },
    }


def build_consolidated_reentry_report(seed_reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    class_counts = Counter()
    cause_counts = Counter()
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
            rows.append(
                {
                    "seed": seed,
                    "step": _as_int(entry.get("step"), default=-1),
                    "drop_mm": _as_int(entry.get("drop_mm"), default=0),
                    "box_id": entry.get("box_id"),
                    "had_alternative_without_reentry": bool(entry.get("had_alternative_without_reentry", False)),
                    "dominant_cause": cause,
                    "classification": classification,
                }
            )
            if idx == 0 and len(reentries) > 1:
                step = _as_int(entry.get("step"), default=-1)
                if step >= 0:
                    slot = critical_by_step[step]
                    slot["seed_count"] = int(slot["seed_count"]) + 1
                    slot["downstream_reentries"] = int(slot["downstream_reentries"]) + int(len(reentries) - 1)
                    slot["seeds"].append(seed)

    rows.sort(key=lambda row: (_as_int(row.get("seed"), default=0), _as_int(row.get("step"), default=-1)))
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

    return {
        "rows": rows,
        "summary": {
            "total_reentries": int(len(rows)),
            "avoidable_by_selection": int(class_counts.get(EVITABLE_BY_SELECTION, 0)),
            "avoidable_potential_candidate_generation": int(
                class_counts.get(EVITABLE_BY_CANDIDATE_GENERATION, 0)
            ),
            "probably_unavoidable": int(class_counts.get(PROBABLY_UNAVOIDABLE, 0)),
            "dominant_cause_global": (cause_counts.most_common(1)[0][0] if cause_counts else None),
            "first_critical_reentry": first_critical_reentry,
        },
    }
