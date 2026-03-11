from __future__ import annotations

import math
from typing import Any, Iterable

BLOCKED_REASONS = {
    "top_open_area_too_small",
    "top_bbox_too_narrow",
    "top_bbox_too_short",
    "overhead_blocked",
    "mixed",
}


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


def _blocked_reason(
    *,
    overhead_blocked_height_mm: int,
    narrow_fail: bool,
    short_fail: bool,
    area_fail: bool,
    top_open_bbox_l_mm: int,
    top_open_bbox_w_mm: int,
) -> str | None:
    if overhead_blocked_height_mm > 0 and top_open_bbox_l_mm <= 0 and top_open_bbox_w_mm <= 0:
        return "overhead_blocked"

    reasons: list[str] = []
    if overhead_blocked_height_mm > 0:
        reasons.append("overhead_blocked")
    if narrow_fail:
        reasons.append("top_bbox_too_narrow")
    if short_fail:
        reasons.append("top_bbox_too_short")
    if area_fail:
        reasons.append("top_open_area_too_small")

    if not reasons:
        return None
    if len(reasons) == 1:
        return reasons[0]
    return "mixed"


def compute_top_access_diagnostics(
    placements: Iterable[Any],
    *,
    bin_length_mm: int | None = None,
    bin_width_mm: int | None = None,
    insertion_margin_mm: int = 40,
    marginal_ratio: float = 0.90,
    critical_limit: int = 8,
) -> dict[str, Any]:
    seq = list(placements)
    n = len(seq)
    margin_mm = max(0, int(insertion_margin_mm))
    ratio = max(0.0, min(1.0, float(marginal_ratio)))

    if n <= 0:
        return {
            "placements_count": 0,
            "insertion_margin_mm": int(margin_mm),
            "marginal_ratio": float(ratio),
            "blocked_count": 0,
            "marginal_count": 0,
            "blocked_stand_hw": 0,
            "marginal_stand_hw": 0,
            "first_blocked_step": None,
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
        for prev in seq[:step]:
            px0 = _as_int(_placement_value(prev, "x_mm", 0))
            py0 = _as_int(_placement_value(prev, "y_mm", 0))
            pl = max(0, _as_int(_placement_value(prev, "length_mm", 0)))
            pw = max(0, _as_int(_placement_value(prev, "width_mm", 0)))
            pz = _as_int(_placement_value(prev, "z_mm", 0))
            ph = max(0, _as_int(_placement_value(prev, "height_mm", 0)))
            ptop = int(pz + ph)
            prev_rect = (int(px0), int(py0), int(px0 + pl), int(py0 + pw))

            if ptop > z_mm and _rect_overlap_area(target_rect, prev_rect) > 0:
                overhead_top_z_max = int(ptop if overhead_top_z_max is None else max(overhead_top_z_max, ptop))

            if ptop > z_mm:
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

        required_l_mm = int(l_mm + 2 * margin_mm)
        required_w_mm = int(w_mm + 2 * margin_mm)
        required_area_mm2 = int(required_l_mm * required_w_mm)
        required_l_marginal_mm = int(math.ceil(required_l_mm * ratio))
        required_w_marginal_mm = int(math.ceil(required_w_mm * ratio))
        required_area_marginal_mm2 = int(math.ceil(required_area_mm2 * ratio))

        has_hard_overhead = overhead_blocked_height_mm > 0
        meets_access = (
            (not has_hard_overhead)
            and top_open_bbox_l_mm >= required_l_mm
            and top_open_bbox_w_mm >= required_w_mm
            and top_open_area_mm2 >= required_area_mm2
        )
        meets_marginal = (
            (not has_hard_overhead)
            and top_open_bbox_l_mm >= required_l_marginal_mm
            and top_open_bbox_w_mm >= required_w_marginal_mm
            and top_open_area_mm2 >= required_area_marginal_mm2
        )

        if meets_access:
            accessibility_class = "accessible"
        elif meets_marginal:
            accessibility_class = "marginal"
        else:
            accessibility_class = "blocked"

        narrow_fail = top_open_bbox_w_mm < required_w_marginal_mm
        short_fail = top_open_bbox_l_mm < required_l_marginal_mm
        area_fail = (
            (top_open_area_mm2 < required_area_marginal_mm2)
            and (not narrow_fail)
            and (not short_fail)
        )

        blocked_reason_exact = _blocked_reason(
            overhead_blocked_height_mm=overhead_blocked_height_mm,
            narrow_fail=bool(narrow_fail),
            short_fail=bool(short_fail),
            area_fail=bool(area_fail),
            top_open_bbox_l_mm=int(top_open_bbox_l_mm),
            top_open_bbox_w_mm=int(top_open_bbox_w_mm),
        )
        if accessibility_class != "blocked":
            blocked_reason_exact = None
        elif blocked_reason_exact not in BLOCKED_REASONS:
            blocked_reason_exact = "mixed"

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
                "overhead_blocked_height_mm": int(overhead_blocked_height_mm),
                "vertical_access_margin_mm": int(vertical_margin_mm),
                "accessibility_class": str(accessibility_class),
                "blocked_reason_exact": blocked_reason_exact,
            }
        )

    blocked = [row for row in per_placement if str(row.get("accessibility_class")) == "blocked"]
    marginal = [row for row in per_placement if str(row.get("accessibility_class")) == "marginal"]
    issue_rows = [row for row in per_placement if str(row.get("accessibility_class")) in ("blocked", "marginal")]

    blocked_count = int(len(blocked))
    marginal_count = int(len(marginal))
    blocked_stand_hw = int(sum(1 for row in blocked if str(row.get("orientation_family")) == "stand_hw"))
    marginal_stand_hw = int(sum(1 for row in marginal if str(row.get("orientation_family")) == "stand_hw"))
    first_blocked_step = min((int(row["step_index"]) for row in blocked), default=None)

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

    def _criticality(row: dict[str, Any]) -> tuple[int, int, int, int]:
        cls = str(row.get("accessibility_class", "accessible"))
        cls_weight = 2 if cls == "blocked" else (1 if cls == "marginal" else 0)
        overhead = int(row.get("overhead_blocked_height_mm", 0) or 0)
        req_l = int(row.get("required_bbox_l_mm", 0) or 0)
        req_w = int(row.get("required_bbox_w_mm", 0) or 0)
        got_l = int(row.get("top_open_bbox_l_mm", 0) or 0)
        got_w = int(row.get("top_open_bbox_w_mm", 0) or 0)
        deficit = max(0, req_l - got_l) + max(0, req_w - got_w)
        step_idx = int(row.get("step_index", 0) or 0)
        return (cls_weight, overhead, deficit, step_idx)

    critical = sorted(issue_rows, key=_criticality, reverse=True)[: max(1, int(critical_limit))]
    critical_placements = [
        {
            "step_index": int(row.get("step_index", 0)),
            "box_id": row.get("box_id"),
            "orientation_family": str(row.get("orientation_family", "planar")),
            "accessibility_class": str(row.get("accessibility_class", "accessible")),
            "blocked_reason_exact": row.get("blocked_reason_exact"),
            "top_open_bbox_l_mm": int(row.get("top_open_bbox_l_mm", 0)),
            "top_open_bbox_w_mm": int(row.get("top_open_bbox_w_mm", 0)),
            "top_open_area_mm2": int(row.get("top_open_area_mm2", 0)),
            "overhead_blocked_height_mm": int(row.get("overhead_blocked_height_mm", 0)),
            "vertical_access_margin_mm": int(row.get("vertical_access_margin_mm", 0)),
        }
        for row in critical
    ]

    return {
        "placements_count": int(n),
        "insertion_margin_mm": int(margin_mm),
        "marginal_ratio": float(ratio),
        "blocked_count": int(blocked_count),
        "marginal_count": int(marginal_count),
        "blocked_stand_hw": int(blocked_stand_hw),
        "marginal_stand_hw": int(marginal_stand_hw),
        "first_blocked_step": (None if first_blocked_step is None else int(first_blocked_step)),
        "issues_concentrated_at_end": bool(issues_concentrated_at_end),
        "blocked_reason_counts": dict(reason_counts),
        "per_placement": per_placement,
        "critical_placements": critical_placements,
    }
