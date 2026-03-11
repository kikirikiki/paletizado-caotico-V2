from __future__ import annotations

import math
from typing import Any, Iterable

BLOCKED_REASONS = {
    "overhead_blocked",
    "mixed",
}
SEVERE_MARGINAL_SCORE_THRESHOLD = 1.75
DEFAULT_LOCAL_BLOCKERS_LIMIT = 5


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _placement_value(placement: Any, key: str, default: Any = 0) -> Any:
    if isinstance(placement, dict):
        return placement.get(key, default)
    return getattr(placement, key, default)


def _rect_overlap_area(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    x0 = max(int(a[0]), int(b[0]))
    y0 = max(int(a[1]), int(b[1]))
    x1 = min(int(a[2]), int(b[2]))
    y1 = min(int(a[3]), int(b[3]))
    if x1 <= x0 or y1 <= y0:
        return 0
    return int((x1 - x0) * (y1 - y0))


def _rect_intersection(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    x0 = max(int(a[0]), int(b[0]))
    y0 = max(int(a[1]), int(b[1]))
    x1 = min(int(a[2]), int(b[2]))
    y1 = min(int(a[3]), int(b[3]))
    if x1 <= x0 or y1 <= y0:
        return None
    return (int(x0), int(y0), int(x1), int(y1))


def _segment_overlap_len(a0: int, a1: int, b0: int, b1: int) -> int:
    lo = max(int(a0), int(b0))
    hi = min(int(a1), int(b1))
    return int(max(0, hi - lo))


def _bbox_payload(rect: tuple[int, int, int, int]) -> dict[str, int]:
    return {
        "x0_mm": int(rect[0]),
        "y0_mm": int(rect[1]),
        "x1_mm": int(rect[2]),
        "y1_mm": int(rect[3]),
    }


def _rect_axis_gaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, int]:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b

    if ax1 <= bx0:
        gap_x = int(bx0 - ax1)
    elif bx1 <= ax0:
        gap_x = int(ax0 - bx1)
    else:
        gap_x = 0

    if ay1 <= by0:
        gap_y = int(by0 - ay1)
    elif by1 <= ay0:
        gap_y = int(ay0 - by1)
    else:
        gap_y = 0

    return int(gap_x), int(gap_y)


def _rect_clearance_mm(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    gap_x, gap_y = _rect_axis_gaps(a, b)
    return int(max(gap_x, gap_y))


def _neighbor_rect(blocker: Any) -> tuple[int, int, int, int]:
    if isinstance(blocker, dict):
        raw = blocker.get("bbox_mm")
        if isinstance(raw, (list, tuple)) and len(raw) == 4:
            return (int(raw[0]), int(raw[1]), int(raw[2]), int(raw[3]))
    if isinstance(blocker, (list, tuple)) and len(blocker) == 4:
        return (int(blocker[0]), int(blocker[1]), int(blocker[2]), int(blocker[3]))
    return (0, 0, 0, 0)


def _neighbor_step_index(blocker: Any) -> int | None:
    if not isinstance(blocker, dict):
        return None
    raw = blocker.get("step_index")
    try:
        if raw is None:
            return None
        return int(raw)
    except Exception:
        return None


def _neighbor_orientation(blocker: Any) -> str:
    if isinstance(blocker, dict):
        raw = str(blocker.get("orientation_family", "planar") or "planar").strip().lower()
    else:
        raw = "planar"
    if raw not in ("planar", "stand_hw"):
        return "planar"
    return str(raw)


def _neighbor_overhead_height_mm(blocker: Any) -> int:
    if not isinstance(blocker, dict):
        return 0
    return max(0, _as_int(blocker.get("overhead_height_mm", 0), default=0))


def _blocker_axis_clearances(
    *,
    target: tuple[int, int, int, int],
    blocker_rect: tuple[int, int, int, int],
    axis_expansion_mm: int,
) -> dict[str, dict[str, int]]:
    x0, y0, x1, y1 = target
    nx0, ny0, nx1, ny1 = blocker_rect
    y_axis_min = int(y0 - axis_expansion_mm)
    y_axis_max = int(y1 + axis_expansion_mm)
    x_axis_min = int(x0 - axis_expansion_mm)
    x_axis_max = int(x1 + axis_expansion_mm)

    out: dict[str, dict[str, int]] = {}
    y_overlap = _segment_overlap_len(ny0, ny1, y_axis_min, y_axis_max)
    x_overlap = _segment_overlap_len(nx0, nx1, x_axis_min, x_axis_max)

    if y_overlap > 0:
        if nx1 <= x0:
            out["left"] = {
                "clearance_mm": int(max(0, x0 - nx1)),
                "axis_overlap_mm": int(y_overlap),
            }
        if nx0 >= x1:
            out["right"] = {
                "clearance_mm": int(max(0, nx0 - x1)),
                "axis_overlap_mm": int(y_overlap),
            }

    if x_overlap > 0:
        if ny1 <= y0:
            out["bottom"] = {
                "clearance_mm": int(max(0, y0 - ny1)),
                "axis_overlap_mm": int(x_overlap),
            }
        if ny0 >= y1:
            out["top"] = {
                "clearance_mm": int(max(0, ny0 - y1)),
                "axis_overlap_mm": int(x_overlap),
            }

    return out


def _prefix_sum_2d(blocked: list[list[int]]) -> list[list[int]]:
    nx = len(blocked)
    ny = len(blocked[0]) if nx > 0 else 0
    pref = [[0] * (ny + 1) for _ in range(nx + 1)]
    for x in range(nx):
        row_acc = 0
        for y in range(ny):
            row_acc += int(blocked[x][y])
            pref[x + 1][y + 1] = pref[x][y + 1] + row_acc
    return pref


def _sum_rect(pref: list[list[int]], x0: int, x1: int, y0: int, y1: int) -> int:
    return int(pref[x1][y1] - pref[x0][y1] - pref[x1][y0] + pref[x0][y0])


def _largest_clear_rect_containing_target(
    *,
    zone: tuple[int, int, int, int],
    target: tuple[int, int, int, int],
    blockers: list[tuple[int, int, int, int]],
) -> tuple[int, int, int]:
    zx0, zy0, zx1, zy1 = zone
    tx0, ty0, tx1, ty1 = target
    if zx1 <= zx0 or zy1 <= zy0:
        return 0, 0, 0

    x_coords = {int(zx0), int(zx1), int(tx0), int(tx1)}
    y_coords = {int(zy0), int(zy1), int(ty0), int(ty1)}
    for bx0, by0, bx1, by1 in blockers:
        x_coords.add(int(bx0))
        x_coords.add(int(bx1))
        y_coords.add(int(by0))
        y_coords.add(int(by1))

    xs = sorted(x_coords)
    ys = sorted(y_coords)
    if len(xs) < 2 or len(ys) < 2:
        return 0, 0, 0

    tx0_idx = xs.index(int(tx0))
    tx1_idx = xs.index(int(tx1))
    ty0_idx = ys.index(int(ty0))
    ty1_idx = ys.index(int(ty1))

    x_cells = len(xs) - 1
    y_cells = len(ys) - 1
    if tx1_idx <= tx0_idx or ty1_idx <= ty0_idx:
        return 0, 0, 0

    blocked = [[0 for _ in range(y_cells)] for _ in range(x_cells)]
    for xi in range(x_cells):
        cx0 = int(xs[xi])
        cx1 = int(xs[xi + 1])
        for yi in range(y_cells):
            cy0 = int(ys[yi])
            cy1 = int(ys[yi + 1])
            cell = (cx0, cy0, cx1, cy1)
            is_blocked = False
            for blocker in blockers:
                if _rect_overlap_area(cell, blocker) > 0:
                    is_blocked = True
                    break
            blocked[xi][yi] = 1 if is_blocked else 0

    pref = _prefix_sum_2d(blocked)
    best_l = 0
    best_w = 0
    best_area = 0

    for x0_idx in range(0, tx0_idx + 1):
        for x1_idx in range(max(tx1_idx, x0_idx + 1), len(xs)):
            blocked_on_target = _sum_rect(
                pref,
                x0_idx,
                x1_idx,
                ty0_idx,
                ty1_idx,
            )
            if blocked_on_target > 0:
                continue

            y_low_cell = int(ty0_idx)
            y_high_cell = int(ty1_idx - 1)

            while y_low_cell > 0:
                if _sum_rect(pref, x0_idx, x1_idx, y_low_cell - 1, y_low_cell) > 0:
                    break
                y_low_cell -= 1

            while y_high_cell + 1 < y_cells:
                if _sum_rect(pref, x0_idx, x1_idx, y_high_cell + 1, y_high_cell + 2) > 0:
                    break
                y_high_cell += 1

            l_mm = int(xs[x1_idx] - xs[x0_idx])
            w_mm = int(ys[y_high_cell + 1] - ys[y_low_cell])
            area_mm2 = int(max(0, l_mm) * max(0, w_mm))

            if area_mm2 > best_area:
                best_l = int(l_mm)
                best_w = int(w_mm)
                best_area = int(area_mm2)
            elif area_mm2 == best_area and area_mm2 > 0:
                if l_mm > best_l or (l_mm == best_l and w_mm > best_w):
                    best_l = int(l_mm)
                    best_w = int(w_mm)
                    best_area = int(area_mm2)

    return int(best_l), int(best_w), int(best_area)


def _entry_throat_metrics(
    *,
    target: tuple[int, int, int, int],
    overhead_neighbors: list[Any],
    insertion_margin_mm: int,
    tool_margin_mm: int,
) -> dict[str, Any]:
    x0, y0, x1, y1 = target
    l_mm = max(0, int(x1 - x0))
    w_mm = max(0, int(y1 - y0))
    probe_mm = max(10, int(insertion_margin_mm))
    axis_expansion_mm = max(0, int(tool_margin_mm), int(math.ceil(insertion_margin_mm * 0.25)))

    left_clearance_mm = int(probe_mm)
    right_clearance_mm = int(probe_mm)
    bottom_clearance_mm = int(probe_mm)
    top_clearance_mm = int(probe_mm)

    y_axis_min = int(y0 - axis_expansion_mm)
    y_axis_max = int(y1 + axis_expansion_mm)
    x_axis_min = int(x0 - axis_expansion_mm)
    x_axis_max = int(x1 + axis_expansion_mm)

    source_by_axis: dict[str, dict[str, Any] | None] = {
        "left": None,
        "right": None,
        "bottom": None,
        "top": None,
    }

    for blocker in overhead_neighbors:
        nx0, ny0, nx1, ny1 = _neighbor_rect(blocker)
        y_overlap = _segment_overlap_len(ny0, ny1, y_axis_min, y_axis_max)
        x_overlap = _segment_overlap_len(nx0, nx1, x_axis_min, x_axis_max)
        step_index = _neighbor_step_index(blocker)
        orientation = _neighbor_orientation(blocker)

        if y_overlap > 0:
            if nx1 <= x0:
                candidate = int(max(0, x0 - nx1))
                if candidate < left_clearance_mm:
                    left_clearance_mm = int(candidate)
                    source_by_axis["left"] = {
                        "step_index": step_index,
                        "orientation_family": orientation,
                        "bbox_mm": (int(nx0), int(ny0), int(nx1), int(ny1)),
                    }
            if nx0 >= x1:
                candidate = int(max(0, nx0 - x1))
                if candidate < right_clearance_mm:
                    right_clearance_mm = int(candidate)
                    source_by_axis["right"] = {
                        "step_index": step_index,
                        "orientation_family": orientation,
                        "bbox_mm": (int(nx0), int(ny0), int(nx1), int(ny1)),
                    }

        if x_overlap > 0:
            if ny1 <= y0:
                candidate = int(max(0, y0 - ny1))
                if candidate < bottom_clearance_mm:
                    bottom_clearance_mm = int(candidate)
                    source_by_axis["bottom"] = {
                        "step_index": step_index,
                        "orientation_family": orientation,
                        "bbox_mm": (int(nx0), int(ny0), int(nx1), int(ny1)),
                    }
            if ny0 >= y1:
                candidate = int(max(0, ny0 - y1))
                if candidate < top_clearance_mm:
                    top_clearance_mm = int(candidate)
                    source_by_axis["top"] = {
                        "step_index": step_index,
                        "orientation_family": orientation,
                        "bbox_mm": (int(nx0), int(ny0), int(nx1), int(ny1)),
                    }

    entry_throat_bbox_l_mm = int(l_mm + left_clearance_mm + right_clearance_mm)
    entry_throat_bbox_w_mm = int(w_mm + bottom_clearance_mm + top_clearance_mm)
    axis_clearances = {
        "left": int(left_clearance_mm),
        "right": int(right_clearance_mm),
        "bottom": int(bottom_clearance_mm),
        "top": int(top_clearance_mm),
    }
    limiting_axis = min(axis_clearances.keys(), key=lambda axis: (int(axis_clearances[axis]), str(axis)))
    entry_throat_min_clearance_mm = int(axis_clearances[limiting_axis])

    required_clearance_mm = int(max(tool_margin_mm, math.ceil(insertion_margin_mm * 0.5)))
    entry_clearance_margin_mm = int(entry_throat_min_clearance_mm - required_clearance_mm)
    source = source_by_axis.get(limiting_axis)
    throat_source_reason = (
        f"entry_throat_{limiting_axis}_limited"
        if source is not None
        else "entry_throat_open"
    )
    nearest_blocker_step = source.get("step_index") if isinstance(source, dict) else None
    nearest_blocker_orientation = source.get("orientation_family") if isinstance(source, dict) else None

    return {
        "entry_throat_bbox_l_mm": int(entry_throat_bbox_l_mm),
        "entry_throat_bbox_w_mm": int(entry_throat_bbox_w_mm),
        "entry_throat_min_clearance_mm": int(entry_throat_min_clearance_mm),
        "entry_clearance_margin_mm": int(entry_clearance_margin_mm),
        "left_clearance_mm": int(left_clearance_mm),
        "right_clearance_mm": int(right_clearance_mm),
        "bottom_clearance_mm": int(bottom_clearance_mm),
        "top_clearance_mm": int(top_clearance_mm),
        "probe_mm": int(probe_mm),
        "axis_expansion_mm": int(axis_expansion_mm),
        "required_clearance_mm": int(required_clearance_mm),
        "limiting_axis": str(limiting_axis),
        "limiting_clearance_mm": int(entry_throat_min_clearance_mm),
        "throat_source_reason": str(throat_source_reason),
        "nearest_blocker_step": (
            None if nearest_blocker_step is None else int(nearest_blocker_step)
        ),
        "nearest_blocker_orientation": (
            None if nearest_blocker_orientation is None else str(nearest_blocker_orientation)
        ),
    }


def _build_local_blockers(
    *,
    target: tuple[int, int, int, int],
    hard_target: tuple[int, int, int, int],
    zone: tuple[int, int, int, int],
    overhead_neighbors: list[Any],
    insertion_margin_mm: int,
    tool_margin_mm: int,
    local_blockers_limit: int,
    preferred_step: int | None,
) -> list[dict[str, Any]]:
    axis_expansion_mm = max(0, int(tool_margin_mm), int(math.ceil(insertion_margin_mm * 0.25)))
    top_n = max(1, int(local_blockers_limit))
    ranked: list[tuple[int, int, int, int, int, int, dict[str, Any]]] = []
    for blocker in overhead_neighbors:
        blocker_rect = _neighbor_rect(blocker)
        if blocker_rect[2] <= blocker_rect[0] or blocker_rect[3] <= blocker_rect[1]:
            continue

        local_footprint = _rect_intersection(blocker_rect, zone)
        hard_overlap_area = _rect_overlap_area(hard_target, blocker_rect)
        target_overlap_area = _rect_overlap_area(target, blocker_rect)
        axis_clearances = _blocker_axis_clearances(
            target=target,
            blocker_rect=blocker_rect,
            axis_expansion_mm=axis_expansion_mm,
        )

        if local_footprint is None and hard_overlap_area <= 0 and target_overlap_area <= 0 and not axis_clearances:
            continue

        step_index = _neighbor_step_index(blocker)
        orientation_family = _neighbor_orientation(blocker)
        overhead_height_mm = _neighbor_overhead_height_mm(blocker)
        blocker_type = "stand_hw" if orientation_family == "stand_hw" else "planar"

        clearance_to_target_mm = int(_rect_clearance_mm(target, blocker_rect))
        clearance_to_hard_prism_mm = int(_rect_clearance_mm(hard_target, blocker_rect))

        if hard_overlap_area > 0:
            limiting_axis = "z_overhead"
            limiting_clearance_mm = int(-max(1, overhead_height_mm))
            rank_bucket = 0
        elif target_overlap_area > 0:
            limiting_axis = "xy_overlap"
            limiting_clearance_mm = 0
            rank_bucket = 1
        elif axis_clearances:
            axis, axis_payload = min(
                axis_clearances.items(),
                key=lambda item: (int(item[1]["clearance_mm"]), str(item[0])),
            )
            limiting_axis = str(axis)
            limiting_clearance_mm = int(axis_payload["clearance_mm"])
            rank_bucket = 2
        else:
            limiting_axis = None
            limiting_clearance_mm = int(clearance_to_target_mm)
            rank_bucket = 3

        preferred_penalty = (
            0
            if (
                preferred_step is not None
                and step_index is not None
                and int(step_index) == int(preferred_step)
            )
            else 1
        )

        item = {
            "step_index": (None if step_index is None else int(step_index)),
            "orientation_family": str(orientation_family),
            "blocker_type": str(blocker_type),
            "blocker_bbox_mm": _bbox_payload(blocker_rect),
            "blocker_local_footprint_mm": _bbox_payload(local_footprint or blocker_rect),
            "clearance_to_target_mm": int(clearance_to_target_mm),
            "clearance_to_hard_prism_mm": int(clearance_to_hard_prism_mm),
            "limiting_axis": (None if limiting_axis is None else str(limiting_axis)),
            "limiting_clearance_mm": int(limiting_clearance_mm),
            "is_hard_prism_overlap": bool(hard_overlap_area > 0),
            "overlaps_target": bool(target_overlap_area > 0),
            "throat_axis_clearances_mm": {
                str(axis): int(payload["clearance_mm"]) for axis, payload in axis_clearances.items()
            },
            "throat_axis_overlap_mm": {
                str(axis): int(payload["axis_overlap_mm"]) for axis, payload in axis_clearances.items()
            },
            "overhead_height_mm": int(overhead_height_mm),
        }

        sortable_step = int(step_index) if step_index is not None else -1
        ranked.append(
            (
                int(rank_bucket),
                int(preferred_penalty),
                int(limiting_clearance_mm),
                int(clearance_to_hard_prism_mm),
                int(clearance_to_target_mm),
                int(-sortable_step),
                item,
            )
        )

    ranked_sorted = sorted(
        ranked,
        key=lambda row: (
            int(row[0]),
            int(row[1]),
            int(row[2]),
            int(row[3]),
            int(row[4]),
            int(row[5]),
        ),
    )
    return [entry[-1] for entry in ranked_sorted[:top_n]]


def compute_top_access_diagnostics(
    placements: Iterable[Any],
    *,
    bin_length_mm: int | None = None,
    bin_width_mm: int | None = None,
    insertion_margin_mm: int = 40,
    tool_margin_mm: int = 0,
    marginal_ratio: float = 0.90,
    critical_limit: int = 8,
    local_blockers_limit: int = DEFAULT_LOCAL_BLOCKERS_LIMIT,
) -> dict[str, Any]:
    seq = list(placements)
    n = len(seq)
    margin_mm = max(0, int(insertion_margin_mm))
    tool_margin = max(0, int(tool_margin_mm))
    ratio = max(0.0, min(1.0, float(marginal_ratio)))
    blockers_top_n = max(1, int(local_blockers_limit))

    if n <= 0:
        return {
            "placements_count": 0,
            "insertion_margin_mm": int(margin_mm),
            "tool_margin_mm": int(tool_margin),
            "marginal_ratio": float(ratio),
            "local_blockers_limit": int(blockers_top_n),
            "blocked_count": 0,
            "marginal_count": 0,
            "severe_marginal_threshold": float(SEVERE_MARGINAL_SCORE_THRESHOLD),
            "severe_marginal_count": 0,
            "blocked_stand_hw": 0,
            "marginal_stand_hw": 0,
            "severe_marginal_stand_hw": 0,
            "first_blocked_step": None,
            "first_severe_marginal_step": None,
            "issues_concentrated_at_end": False,
            "blocked_reason_counts": {},
            "per_placement": [],
            "critical_placements": [],
        }

    per_placement: list[dict[str, Any]] = []

    for step, placement in enumerate(seq):
        x0 = _as_int(_placement_value(placement, "x_mm", 0))
        y0 = _as_int(_placement_value(placement, "y_mm", 0))
        l_mm = max(0, _as_int(_placement_value(placement, "length_mm", 0)))
        w_mm = max(0, _as_int(_placement_value(placement, "width_mm", 0)))
        h_mm = max(0, _as_int(_placement_value(placement, "height_mm", 0)))
        z_mm = _as_int(_placement_value(placement, "z_mm", 0))
        layer_id = _as_int(_placement_value(placement, "layer_id", 0))
        box_id = _placement_value(placement, "box_id", None)
        orientation_family = str(_placement_value(placement, "orientation_family", "planar") or "planar")

        x1 = int(x0 + l_mm)
        y1 = int(y0 + w_mm)
        target_rect = (int(x0), int(y0), int(x1), int(y1))
        hard_target_rect = (
            int(x0 - tool_margin),
            int(y0 - tool_margin),
            int(x1 + tool_margin),
            int(y1 + tool_margin),
        )
        if bin_length_mm is not None:
            hard_target_rect = (
                max(0, int(hard_target_rect[0])),
                int(hard_target_rect[1]),
                min(int(bin_length_mm), int(hard_target_rect[2])),
                int(hard_target_rect[3]),
            )
        if bin_width_mm is not None:
            hard_target_rect = (
                int(hard_target_rect[0]),
                max(0, int(hard_target_rect[1])),
                int(hard_target_rect[2]),
                min(int(bin_width_mm), int(hard_target_rect[3])),
            )
        if hard_target_rect[2] <= hard_target_rect[0] or hard_target_rect[3] <= hard_target_rect[1]:
            hard_target_rect = target_rect

        zone_x0 = int(x0 - margin_mm)
        zone_y0 = int(y0 - margin_mm)
        zone_x1 = int(x1 + margin_mm)
        zone_y1 = int(y1 + margin_mm)

        if bin_length_mm is not None:
            zone_x0 = max(0, int(zone_x0))
            zone_x1 = min(int(bin_length_mm), int(zone_x1))
        if bin_width_mm is not None:
            zone_y0 = max(0, int(zone_y0))
            zone_y1 = min(int(bin_width_mm), int(zone_y1))

        if zone_x1 <= zone_x0 or zone_y1 <= zone_y0:
            zone_x0, zone_y0, zone_x1, zone_y1 = int(x0), int(y0), int(x1), int(y1)

        zone_rect = (int(zone_x0), int(zone_y0), int(zone_x1), int(zone_y1))

        overhead_top_z_max: int | None = None
        blockers: list[tuple[int, int, int, int]] = []
        overhead_neighbors: list[dict[str, Any]] = []
        for prev_step, prev in enumerate(seq[:step]):
            px0 = _as_int(_placement_value(prev, "x_mm", 0))
            py0 = _as_int(_placement_value(prev, "y_mm", 0))
            pl = max(0, _as_int(_placement_value(prev, "length_mm", 0)))
            pw = max(0, _as_int(_placement_value(prev, "width_mm", 0)))
            pz = _as_int(_placement_value(prev, "z_mm", 0))
            ph = max(0, _as_int(_placement_value(prev, "height_mm", 0)))
            ptop = int(pz + ph)
            prev_rect = (int(px0), int(py0), int(px0 + pl), int(py0 + pw))
            prev_orientation = str(_placement_value(prev, "orientation_family", "planar") or "planar").strip().lower()
            if prev_orientation not in ("planar", "stand_hw"):
                prev_orientation = "planar"

            if ptop > z_mm and _rect_overlap_area(hard_target_rect, prev_rect) > 0:
                overhead_top_z_max = int(ptop if overhead_top_z_max is None else max(overhead_top_z_max, ptop))

            if ptop > z_mm:
                overhead_neighbors.append(
                    {
                        "step_index": int(prev_step),
                        "orientation_family": str(prev_orientation),
                        "blocker_type": str(prev_orientation),
                        "bbox_mm": (int(prev_rect[0]), int(prev_rect[1]), int(prev_rect[2]), int(prev_rect[3])),
                        "overhead_height_mm": int(max(0, ptop - z_mm)),
                    }
                )
                inter = _rect_intersection(prev_rect, zone_rect)
                if inter is not None:
                    blockers.append(inter)

        if overhead_top_z_max is None:
            vertical_margin_mm = int(z_mm)
            overhead_blocked_height_mm = 0
        else:
            vertical_margin_mm = int(z_mm - overhead_top_z_max)
            overhead_blocked_height_mm = int(max(0, overhead_top_z_max - z_mm))

        top_open_bbox_l_mm, top_open_bbox_w_mm, top_open_area_mm2 = _largest_clear_rect_containing_target(
            zone=zone_rect,
            target=target_rect,
            blockers=blockers,
        )

        required_l_mm = int(l_mm + margin_mm)
        required_w_mm = int(w_mm + margin_mm)
        required_area_mm2 = int(required_l_mm * required_w_mm)
        required_l_marginal_mm = int(math.ceil(required_l_mm * ratio))
        required_w_marginal_mm = int(math.ceil(required_w_mm * ratio))
        required_area_marginal_mm2 = int(math.ceil(required_area_mm2 * ratio))
        throat = _entry_throat_metrics(
            target=target_rect,
            overhead_neighbors=overhead_neighbors,
            insertion_margin_mm=margin_mm,
            tool_margin_mm=tool_margin,
        )
        entry_throat_bbox_l_mm = int(throat["entry_throat_bbox_l_mm"])
        entry_throat_bbox_w_mm = int(throat["entry_throat_bbox_w_mm"])
        entry_throat_min_clearance_mm = int(throat["entry_throat_min_clearance_mm"])
        entry_clearance_margin_mm = int(throat["entry_clearance_margin_mm"])

        has_hard_overhead = overhead_blocked_height_mm > 0
        meets_access_aux = (
            top_open_bbox_l_mm >= required_l_mm
            and top_open_bbox_w_mm >= required_w_mm
            and top_open_area_mm2 >= required_area_mm2
        )
        meets_marginal_aux = (
            top_open_bbox_l_mm >= required_l_marginal_mm
            and top_open_bbox_w_mm >= required_w_marginal_mm
            and top_open_area_mm2 >= required_area_marginal_mm2
        )
        min_access_clearance_mm = int(max(1, tool_margin))
        throat_tight_for_access = (
            orientation_family == "stand_hw" and entry_throat_min_clearance_mm < min_access_clearance_mm
        )

        if has_hard_overhead:
            accessibility_class = "blocked"
        elif meets_access_aux and (not throat_tight_for_access):
            accessibility_class = "accessible"
        elif meets_marginal_aux or throat_tight_for_access:
            accessibility_class = "marginal"
        else:
            accessibility_class = "marginal"

        narrow_fail = top_open_bbox_w_mm < required_w_marginal_mm
        short_fail = top_open_bbox_l_mm < required_l_marginal_mm
        area_fail = (
            (top_open_area_mm2 < required_area_marginal_mm2)
            and (not narrow_fail)
            and (not short_fail)
        )
        blocked_reason_exact: str | None
        if accessibility_class == "blocked":
            blocked_reason_exact = "overhead_blocked"
        else:
            blocked_reason_exact = None

        local_blockers = _build_local_blockers(
            target=target_rect,
            hard_target=hard_target_rect,
            zone=zone_rect,
            overhead_neighbors=overhead_neighbors,
            insertion_margin_mm=margin_mm,
            tool_margin_mm=tool_margin,
            local_blockers_limit=blockers_top_n,
            preferred_step=(
                None
                if throat.get("nearest_blocker_step") is None
                else int(throat.get("nearest_blocker_step"))
            ),
        )

        nearest_blocker_step = (
            None
            if throat.get("nearest_blocker_step") is None
            else int(throat.get("nearest_blocker_step"))
        )
        nearest_blocker_orientation = (
            None
            if throat.get("nearest_blocker_orientation") is None
            else str(throat.get("nearest_blocker_orientation"))
        )
        if nearest_blocker_step is None and local_blockers:
            nearest_blocker_step = (
                None
                if local_blockers[0].get("step_index") is None
                else int(local_blockers[0]["step_index"])
            )
            nearest_blocker_orientation = str(local_blockers[0].get("orientation_family", "planar"))

        limiting_axis = str(throat.get("limiting_axis", "left"))
        limiting_clearance_mm = int(throat.get("limiting_clearance_mm", entry_throat_min_clearance_mm))
        throat_source_reason = str(throat.get("throat_source_reason", "entry_throat_open"))

        if has_hard_overhead:
            limiting_axis = "z_overhead"
            limiting_clearance_mm = int(-overhead_blocked_height_mm)
            throat_source_reason = "overhead_prism_overlap"
            overlap_blockers = [item for item in local_blockers if bool(item.get("is_hard_prism_overlap", False))]
            if overlap_blockers:
                nearest_blocker_step = (
                    None
                    if overlap_blockers[0].get("step_index") is None
                    else int(overlap_blockers[0]["step_index"])
                )
                nearest_blocker_orientation = str(overlap_blockers[0].get("orientation_family", "planar"))
        elif throat_tight_for_access and throat_source_reason == "entry_throat_open":
            throat_source_reason = "stand_hw_throat_tight"

        entry_throat_bbox_rect = (
            int(x0 - int(throat.get("left_clearance_mm", 0))),
            int(y0 - int(throat.get("bottom_clearance_mm", 0))),
            int(x1 + int(throat.get("right_clearance_mm", 0))),
            int(y1 + int(throat.get("top_clearance_mm", 0))),
        )

        late_ratio = float(step) / float(max(1, n - 1))
        stand_hw_bonus = 0.40 if orientation_family == "stand_hw" else 0.0
        clearance_deficit_ratio = float(max(0, -entry_clearance_margin_mm)) / float(max(1, margin_mm))
        l_deficit_ratio = float(max(0, required_l_marginal_mm - top_open_bbox_l_mm)) / float(max(1, required_l_marginal_mm))
        w_deficit_ratio = float(max(0, required_w_marginal_mm - top_open_bbox_w_mm)) / float(max(1, required_w_marginal_mm))
        area_deficit_ratio = (
            float(max(0, required_area_marginal_mm2 - top_open_area_mm2)) / float(max(1, required_area_marginal_mm2))
        )
        if accessibility_class == "blocked":
            marginal_severity_score = (
                10.0
                + float(overhead_blocked_height_mm) / 100.0
                + 3.0 * clearance_deficit_ratio
                + 1.0 * l_deficit_ratio
                + 1.0 * w_deficit_ratio
            )
        else:
            marginal_severity_score = (
                2.3 * clearance_deficit_ratio
                + 1.0 * l_deficit_ratio
                + 1.0 * w_deficit_ratio
                + 0.8 * area_deficit_ratio
                + stand_hw_bonus
                + 0.35 * late_ratio
            )
            if accessibility_class == "accessible":
                marginal_severity_score *= 0.35

        is_severe_marginal = (
            accessibility_class == "marginal"
            and float(marginal_severity_score) >= float(SEVERE_MARGINAL_SCORE_THRESHOLD)
        )

        per_placement.append(
            {
                "step_index": int(step),
                "box_id": box_id,
                "layer_id": int(layer_id),
                "orientation_family": str(orientation_family),
                "x_mm": int(x0),
                "y_mm": int(y0),
                "z_mm": int(z_mm),
                "length_mm": int(l_mm),
                "width_mm": int(w_mm),
                "height_mm": int(h_mm),
                "target_bbox_l_mm": int(l_mm),
                "target_bbox_w_mm": int(w_mm),
                "required_bbox_l_mm": int(required_l_mm),
                "required_bbox_w_mm": int(required_w_mm),
                "required_area_mm2": int(required_area_mm2),
                "required_bbox_l_marginal_mm": int(required_l_marginal_mm),
                "required_bbox_w_marginal_mm": int(required_w_marginal_mm),
                "required_area_marginal_mm2": int(required_area_marginal_mm2),
                "top_access_clear": bool(accessibility_class in ("accessible", "marginal")),
                "top_open_area_mm2": int(top_open_area_mm2),
                "top_open_bbox_l_mm": int(top_open_bbox_l_mm),
                "top_open_bbox_w_mm": int(top_open_bbox_w_mm),
                "aux_opening_meets_access": bool(meets_access_aux),
                "aux_opening_meets_marginal": bool(meets_marginal_aux),
                "aux_opening_fail_narrow": bool(narrow_fail),
                "aux_opening_fail_short": bool(short_fail),
                "aux_opening_fail_area": bool(area_fail),
                "entry_throat_bbox_l_mm": int(entry_throat_bbox_l_mm),
                "entry_throat_bbox_w_mm": int(entry_throat_bbox_w_mm),
                "entry_throat_min_clearance_mm": int(entry_throat_min_clearance_mm),
                "entry_clearance_margin_mm": int(entry_clearance_margin_mm),
                "entry_throat_bbox_mm": _bbox_payload(entry_throat_bbox_rect),
                "entry_throat_clearances_mm": {
                    "left_mm": int(throat.get("left_clearance_mm", 0)),
                    "right_mm": int(throat.get("right_clearance_mm", 0)),
                    "bottom_mm": int(throat.get("bottom_clearance_mm", 0)),
                    "top_mm": int(throat.get("top_clearance_mm", 0)),
                },
                "entry_throat_probe_mm": int(throat.get("probe_mm", 0)),
                "entry_throat_axis_expansion_mm": int(throat.get("axis_expansion_mm", 0)),
                "entry_throat_required_clearance_mm": int(throat.get("required_clearance_mm", 0)),
                "marginal_severity_score": float(round(float(marginal_severity_score), 6)),
                "is_severe_marginal": bool(is_severe_marginal),
                "overhead_blocked_height_mm": int(overhead_blocked_height_mm),
                "vertical_access_margin_mm": int(vertical_margin_mm),
                "limiting_axis": str(limiting_axis),
                "limiting_clearance_mm": int(limiting_clearance_mm),
                "throat_source_reason": str(throat_source_reason),
                "nearest_blocker_step": (
                    None if nearest_blocker_step is None else int(nearest_blocker_step)
                ),
                "nearest_blocker_orientation": (
                    None
                    if nearest_blocker_orientation is None
                    else str(nearest_blocker_orientation)
                ),
                "local_blockers": local_blockers,
                "local_blockers_count": int(len(local_blockers)),
                "pallet_bounds_mm": {
                    "x0_mm": 0,
                    "y0_mm": 0,
                    "x1_mm": (
                        None if bin_length_mm is None else int(bin_length_mm)
                    ),
                    "y1_mm": (
                        None if bin_width_mm is None else int(bin_width_mm)
                    ),
                },
                "target_footprint_mm": _bbox_payload(target_rect),
                "target_hard_prism_mm": _bbox_payload(hard_target_rect),
                "analysis_zone_mm": _bbox_payload(zone_rect),
                "accessibility_class": str(accessibility_class),
                "blocked_reason_exact": blocked_reason_exact,
            }
        )

    blocked = [row for row in per_placement if str(row.get("accessibility_class")) == "blocked"]
    marginal = [row for row in per_placement if str(row.get("accessibility_class")) == "marginal"]
    issue_rows = [row for row in per_placement if str(row.get("accessibility_class")) in ("blocked", "marginal")]

    blocked_count = int(len(blocked))
    marginal_count = int(len(marginal))
    severe_marginal = [row for row in marginal if bool(row.get("is_severe_marginal"))]
    severe_marginal_count = int(len(severe_marginal))
    blocked_stand_hw = int(sum(1 for row in blocked if str(row.get("orientation_family")) == "stand_hw"))
    marginal_stand_hw = int(sum(1 for row in marginal if str(row.get("orientation_family")) == "stand_hw"))
    severe_marginal_stand_hw = int(sum(1 for row in severe_marginal if str(row.get("orientation_family")) == "stand_hw"))
    first_blocked_step = min((int(row["step_index"]) for row in blocked), default=None)
    first_severe_marginal_step = min((int(row["step_index"]) for row in severe_marginal), default=None)

    reason_counts: dict[str, int] = {}
    for row in blocked:
        reason = row.get("blocked_reason_exact")
        if reason is None:
            continue
        reason_s = str(reason)
        reason_counts[reason_s] = int(reason_counts.get(reason_s, 0) + 1)

    tail_start = int(math.floor(0.75 * max(1, n)))
    issues_tail = int(sum(1 for row in issue_rows if int(row.get("step_index", -1)) >= tail_start))
    issues_concentrated_at_end = bool(issue_rows and (issues_tail / float(len(issue_rows)) >= 0.60))

    def _criticality(row: dict[str, Any]) -> tuple[float, int, int]:
        cls = str(row.get("accessibility_class", "accessible"))
        cls_weight = 1 if cls == "blocked" else 0
        severity = float(row.get("marginal_severity_score", 0.0) or 0.0)
        step_idx = int(row.get("step_index", 0) or 0)
        return (severity, cls_weight, step_idx)

    critical = sorted(issue_rows, key=_criticality, reverse=True)[: max(1, int(critical_limit))]
    critical_placements = [
        {
            "step_index": int(row.get("step_index", 0)),
            "box_id": row.get("box_id"),
            "orientation_family": str(row.get("orientation_family", "planar")),
            "accessibility_class": str(row.get("accessibility_class", "accessible")),
            "blocked_reason_exact": row.get("blocked_reason_exact"),
            "marginal_severity_score": float(round(float(row.get("marginal_severity_score", 0.0) or 0.0), 6)),
            "is_severe_marginal": bool(row.get("is_severe_marginal", False)),
            "top_open_bbox_l_mm": int(row.get("top_open_bbox_l_mm", 0)),
            "top_open_bbox_w_mm": int(row.get("top_open_bbox_w_mm", 0)),
            "top_open_area_mm2": int(row.get("top_open_area_mm2", 0)),
            "entry_throat_bbox_l_mm": int(row.get("entry_throat_bbox_l_mm", 0)),
            "entry_throat_bbox_w_mm": int(row.get("entry_throat_bbox_w_mm", 0)),
            "entry_throat_min_clearance_mm": int(row.get("entry_throat_min_clearance_mm", 0)),
            "entry_clearance_margin_mm": int(row.get("entry_clearance_margin_mm", 0)),
            "limiting_axis": str(row.get("limiting_axis", "left")),
            "limiting_clearance_mm": int(row.get("limiting_clearance_mm", 0)),
            "throat_source_reason": str(row.get("throat_source_reason", "entry_throat_open")),
            "nearest_blocker_step": (
                None
                if row.get("nearest_blocker_step") is None
                else int(row.get("nearest_blocker_step", 0))
            ),
            "nearest_blocker_orientation": (
                None
                if row.get("nearest_blocker_orientation") is None
                else str(row.get("nearest_blocker_orientation"))
            ),
            "local_blockers": [
                item for item in row.get("local_blockers", []) if isinstance(item, dict)
            ],
            "entry_throat_bbox_mm": (
                dict(row.get("entry_throat_bbox_mm"))
                if isinstance(row.get("entry_throat_bbox_mm"), dict)
                else None
            ),
            "entry_throat_clearances_mm": (
                dict(row.get("entry_throat_clearances_mm"))
                if isinstance(row.get("entry_throat_clearances_mm"), dict)
                else {}
            ),
            "pallet_bounds_mm": (
                dict(row.get("pallet_bounds_mm"))
                if isinstance(row.get("pallet_bounds_mm"), dict)
                else {}
            ),
            "target_footprint_mm": (
                dict(row.get("target_footprint_mm"))
                if isinstance(row.get("target_footprint_mm"), dict)
                else {}
            ),
            "target_hard_prism_mm": (
                dict(row.get("target_hard_prism_mm"))
                if isinstance(row.get("target_hard_prism_mm"), dict)
                else {}
            ),
            "analysis_zone_mm": (
                dict(row.get("analysis_zone_mm"))
                if isinstance(row.get("analysis_zone_mm"), dict)
                else {}
            ),
            "overhead_blocked_height_mm": int(row.get("overhead_blocked_height_mm", 0)),
            "vertical_access_margin_mm": int(row.get("vertical_access_margin_mm", 0)),
        }
        for row in critical
    ]

    return {
        "placements_count": int(n),
        "insertion_margin_mm": int(margin_mm),
        "tool_margin_mm": int(tool_margin),
        "marginal_ratio": float(ratio),
        "local_blockers_limit": int(blockers_top_n),
        "severe_marginal_threshold": float(SEVERE_MARGINAL_SCORE_THRESHOLD),
        "blocked_count": int(blocked_count),
        "marginal_count": int(marginal_count),
        "severe_marginal_count": int(severe_marginal_count),
        "blocked_stand_hw": int(blocked_stand_hw),
        "marginal_stand_hw": int(marginal_stand_hw),
        "severe_marginal_stand_hw": int(severe_marginal_stand_hw),
        "first_blocked_step": (None if first_blocked_step is None else int(first_blocked_step)),
        "first_severe_marginal_step": (
            None if first_severe_marginal_step is None else int(first_severe_marginal_step)
        ),
        "issues_concentrated_at_end": bool(issues_concentrated_at_end),
        "blocked_reason_counts": dict(reason_counts),
        "per_placement": per_placement,
        "critical_placements": critical_placements,
    }
