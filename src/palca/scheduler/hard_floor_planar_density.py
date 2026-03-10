from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence


Rect = tuple[int, int, int, int]


@dataclass(frozen=True)
class PlanarBaseDensityMetrics:
    base_fill_ratio: float
    largest_free_rect_area_mm2: float
    boundary_connected_free_area_mm2: float
    inaccessible_pocket_area_mm2: float
    occupied_base_zones: int
    base_compactness: float
    floor_continuity_score: float
    early_l_penalty: float
    largest_free_rect_ratio: float
    boundary_connected_free_ratio: float
    zone_occupancy_ratio: float


def score_planar_base_density(
    *,
    floor_rects_after: Sequence[Rect],
    bin_area_mm2: int,
    floor_count_after: int,
    bin_length_mm: int | None = None,
    bin_width_mm: int | None = None,
    offset_mm: int = 0,
    bin_mm: int = 80,
    zone_x: int = 3,
    zone_y: int = 2,
) -> tuple[float, PlanarBaseDensityMetrics]:
    total_bin_area = max(1, int(bin_area_mm2))
    bx0, by0, bx1, by1 = _resolve_bin_bounds(
        floor_rects_after=floor_rects_after,
        bin_area_mm2=total_bin_area,
        bin_length_mm=bin_length_mm,
        bin_width_mm=bin_width_mm,
        offset_mm=int(offset_mm),
    )
    clipped = _clip_rects_to_bounds(
        floor_rects_after=floor_rects_after,
        bounds=(bx0, by0, bx1, by1),
    )

    bin_w = max(1, int(bx1 - bx0))
    bin_h = max(1, int(by1 - by0))
    cell_mm = max(20, int(bin_mm))
    cols = max(1, (bin_w + cell_mm - 1) // cell_mm)
    rows = max(1, (bin_h + cell_mm - 1) // cell_mm)
    cell_area = float(total_bin_area) / float(rows * cols)

    occupied = [[False for _ in range(cols)] for _ in range(rows)]
    for rect in clipped:
        _paint_rect_on_grid(
            occupied=occupied,
            rect=rect,
            bounds=(bx0, by0, bx1, by1),
            cell_mm=cell_mm,
        )

    occupied_count = sum(1 for row in occupied for cell in row if cell)
    total_cells = rows * cols
    free_count = max(0, total_cells - occupied_count)
    base_fill_ratio = float(occupied_count) / float(max(1, total_cells))

    free_mask = [[not cell for cell in row] for row in occupied]
    free_components, _largest_free_component, boundary_free_count = _component_stats(free_mask)
    del free_components  # free components count is intentionally unused in score formula.
    inaccessible_count = max(0, free_count - boundary_free_count)

    largest_free_rect_cells = _largest_true_rectangle_area(free_mask)
    largest_free_rect_area_mm2 = float(largest_free_rect_cells) * cell_area
    largest_free_rect_ratio = largest_free_rect_area_mm2 / float(max(1, total_bin_area))

    boundary_connected_free_area_mm2 = float(boundary_free_count) * cell_area
    free_area_mm2 = float(free_count) * cell_area
    boundary_connected_free_ratio = (
        boundary_connected_free_area_mm2 / float(max(1.0, free_area_mm2))
        if free_count > 0
        else 1.0
    )
    inaccessible_pocket_area_mm2 = float(inaccessible_count) * cell_area
    pocket_ratio = inaccessible_pocket_area_mm2 / float(max(1, total_bin_area))

    occupied_zones = _occupied_zones(
        occupied=occupied,
        zone_x=max(1, int(zone_x)),
        zone_y=max(1, int(zone_y)),
    )
    zone_occupancy_ratio = float(len(occupied_zones)) / float(max(1, int(zone_x) * int(zone_y)))

    occ_components, largest_occ_component, _boundary_occ_count = _component_stats(occupied)
    if occupied_count <= 0:
        continuity = 0.0
        compactness = 0.0
    else:
        largest_occ_ratio = float(largest_occ_component) / float(occupied_count)
        component_penalty = 0.12 * float(max(0, occ_components - 1))
        continuity = max(0.0, min(1.0, largest_occ_ratio - component_penalty))
        compactness = _occupied_bbox_compactness(occupied)

    early_l_penalty = _early_l_penalty(
        occupied=occupied,
        floor_count_after=max(0, int(floor_count_after)),
        compactness=float(compactness),
        pocket_ratio=float(pocket_ratio),
    )

    score = (
        1.9 * float(base_fill_ratio)
        + 1.4 * float(compactness)
        + 2.1 * float(largest_free_rect_ratio)
        + 1.3 * float(boundary_connected_free_ratio)
        + 0.8 * float(zone_occupancy_ratio)
        + 1.0 * float(continuity)
        - 2.4 * float(pocket_ratio)
        - 1.0 * float(early_l_penalty)
    )

    metrics = PlanarBaseDensityMetrics(
        base_fill_ratio=float(base_fill_ratio),
        largest_free_rect_area_mm2=float(largest_free_rect_area_mm2),
        boundary_connected_free_area_mm2=float(boundary_connected_free_area_mm2),
        inaccessible_pocket_area_mm2=float(inaccessible_pocket_area_mm2),
        occupied_base_zones=int(len(occupied_zones)),
        base_compactness=float(compactness),
        floor_continuity_score=float(continuity),
        early_l_penalty=float(early_l_penalty),
        largest_free_rect_ratio=float(largest_free_rect_ratio),
        boundary_connected_free_ratio=float(boundary_connected_free_ratio),
        zone_occupancy_ratio=float(zone_occupancy_ratio),
    )
    return float(score), metrics


def _resolve_bin_bounds(
    *,
    floor_rects_after: Sequence[Rect],
    bin_area_mm2: int,
    bin_length_mm: int | None,
    bin_width_mm: int | None,
    offset_mm: int,
) -> Rect:
    l_mm = int(bin_length_mm or 0)
    w_mm = int(bin_width_mm or 0)
    if l_mm > 0 and w_mm > 0:
        x0 = int(offset_mm)
        y0 = int(offset_mm)
        return x0, y0, x0 + l_mm, y0 + w_mm

    if floor_rects_after:
        min_x = min(int(r[0]) for r in floor_rects_after)
        min_y = min(int(r[1]) for r in floor_rects_after)
        max_x = max(int(r[2]) for r in floor_rects_after)
        max_y = max(int(r[3]) for r in floor_rects_after)
        width = max(1, max_x - min_x)
        height = max(1, max_y - min_y)
        rect_area = max(1, width * height)
        target_area = max(rect_area, int(bin_area_mm2))
        if target_area > rect_area:
            scale = math.sqrt(float(target_area) / float(rect_area))
            width = max(width, int(round(float(width) * scale)))
            height = max(height, int(round(float(height) * scale)))
        return int(min_x), int(min_y), int(min_x + width), int(min_y + height)

    side = max(1, int(round(math.sqrt(float(max(1, int(bin_area_mm2)))))))
    return 0, 0, side, side


def _clip_rects_to_bounds(*, floor_rects_after: Sequence[Rect], bounds: Rect) -> list[Rect]:
    bx0, by0, bx1, by1 = bounds
    clipped: list[Rect] = []
    for rect in floor_rects_after:
        x0, y0, x1, y1 = [int(v) for v in rect]
        rx0 = max(int(bx0), int(x0))
        ry0 = max(int(by0), int(y0))
        rx1 = min(int(bx1), int(x1))
        ry1 = min(int(by1), int(y1))
        if rx1 <= rx0 or ry1 <= ry0:
            continue
        clipped.append((int(rx0), int(ry0), int(rx1), int(ry1)))
    return clipped


def _paint_rect_on_grid(
    *,
    occupied: list[list[bool]],
    rect: Rect,
    bounds: Rect,
    cell_mm: int,
) -> None:
    bx0, by0, bx1, by1 = bounds
    rows = len(occupied)
    cols = len(occupied[0]) if rows > 0 else 0
    if rows <= 0 or cols <= 0:
        return

    x0, y0, x1, y1 = rect
    x0 = max(int(bx0), int(x0))
    y0 = max(int(by0), int(y0))
    x1 = min(int(bx1), int(x1))
    y1 = min(int(by1), int(y1))
    if x1 <= x0 or y1 <= y0:
        return

    c0 = max(0, (x0 - int(bx0)) // int(cell_mm))
    c1 = min(int(cols), (x1 - int(bx0) + int(cell_mm) - 1) // int(cell_mm))
    r0 = max(0, (y0 - int(by0)) // int(cell_mm))
    r1 = min(int(rows), (y1 - int(by0) + int(cell_mm) - 1) // int(cell_mm))
    for r in range(int(r0), int(r1)):
        row = occupied[r]
        for c in range(int(c0), int(c1)):
            row[c] = True


def _component_stats(mask: list[list[bool]]) -> tuple[int, int, int]:
    rows = len(mask)
    cols = len(mask[0]) if rows > 0 else 0
    if rows <= 0 or cols <= 0:
        return 0, 0, 0

    visited = [[False for _ in range(cols)] for _ in range(rows)]
    components = 0
    largest = 0
    boundary_total = 0
    for r in range(rows):
        for c in range(cols):
            if not mask[r][c] or visited[r][c]:
                continue
            components += 1
            stack = [(r, c)]
            visited[r][c] = True
            count = 0
            touches_boundary = False
            while stack:
                cr, cc = stack.pop()
                count += 1
                if cr == 0 or cc == 0 or cr == rows - 1 or cc == cols - 1:
                    touches_boundary = True
                if cr > 0 and mask[cr - 1][cc] and not visited[cr - 1][cc]:
                    visited[cr - 1][cc] = True
                    stack.append((cr - 1, cc))
                if cr + 1 < rows and mask[cr + 1][cc] and not visited[cr + 1][cc]:
                    visited[cr + 1][cc] = True
                    stack.append((cr + 1, cc))
                if cc > 0 and mask[cr][cc - 1] and not visited[cr][cc - 1]:
                    visited[cr][cc - 1] = True
                    stack.append((cr, cc - 1))
                if cc + 1 < cols and mask[cr][cc + 1] and not visited[cr][cc + 1]:
                    visited[cr][cc + 1] = True
                    stack.append((cr, cc + 1))
            largest = max(largest, count)
            if touches_boundary:
                boundary_total += count
    return int(components), int(largest), int(boundary_total)


def _largest_true_rectangle_area(mask: list[list[bool]]) -> int:
    rows = len(mask)
    cols = len(mask[0]) if rows > 0 else 0
    if rows <= 0 or cols <= 0:
        return 0

    heights = [0 for _ in range(cols)]
    best = 0
    for r in range(rows):
        for c in range(cols):
            if mask[r][c]:
                heights[c] += 1
            else:
                heights[c] = 0
        best = max(best, _largest_histogram_area(heights))
    return int(best)


def _largest_histogram_area(heights: list[int]) -> int:
    stack: list[tuple[int, int]] = []
    best = 0
    for idx in range(len(heights) + 1):
        cur_h = heights[idx] if idx < len(heights) else 0
        start = idx
        while stack and stack[-1][1] > cur_h:
            prev_start, prev_h = stack.pop()
            best = max(best, int(prev_h) * int(idx - prev_start))
            start = prev_start
        if not stack or stack[-1][1] < cur_h:
            stack.append((start, int(cur_h)))
    return int(best)


def _occupied_zones(
    *,
    occupied: list[list[bool]],
    zone_x: int,
    zone_y: int,
) -> set[tuple[int, int]]:
    rows = len(occupied)
    cols = len(occupied[0]) if rows > 0 else 0
    if rows <= 0 or cols <= 0:
        return set()

    zones: set[tuple[int, int]] = set()
    for r in range(rows):
        for c in range(cols):
            if not occupied[r][c]:
                continue
            zx = min(int(zone_x - 1), max(0, int((float(c) + 0.5) * float(zone_x) / float(cols))))
            zy = min(int(zone_y - 1), max(0, int((float(r) + 0.5) * float(zone_y) / float(rows))))
            zones.add((zx, zy))
    return zones


def _occupied_bbox_compactness(occupied: list[list[bool]]) -> float:
    rows = len(occupied)
    cols = len(occupied[0]) if rows > 0 else 0
    if rows <= 0 or cols <= 0:
        return 0.0

    min_r = rows
    max_r = -1
    min_c = cols
    max_c = -1
    count = 0
    for r in range(rows):
        for c in range(cols):
            if not occupied[r][c]:
                continue
            count += 1
            min_r = min(min_r, r)
            max_r = max(max_r, r)
            min_c = min(min_c, c)
            max_c = max(max_c, c)
    if count <= 0 or max_r < min_r or max_c < min_c:
        return 0.0
    bbox_area = max(1, (max_r - min_r + 1) * (max_c - min_c + 1))
    return float(count) / float(bbox_area)


def _early_l_penalty(
    *,
    occupied: list[list[bool]],
    floor_count_after: int,
    compactness: float,
    pocket_ratio: float,
) -> float:
    if floor_count_after <= 0:
        return 0.0
    if floor_count_after > 5:
        return 0.0
    if pocket_ratio >= 0.04:
        return 1.0

    rows = len(occupied)
    cols = len(occupied[0]) if rows > 0 else 0
    if rows <= 0 or cols <= 0:
        return 0.0

    touches_left = any(occupied[r][0] for r in range(rows))
    touches_right = any(occupied[r][cols - 1] for r in range(rows))
    touches_top = any(occupied[0][c] for c in range(cols))
    touches_bottom = any(occupied[rows - 1][c] for c in range(cols))
    touches = int(touches_left) + int(touches_right) + int(touches_top) + int(touches_bottom)

    if touches >= 3 and float(compactness) < 0.70:
        return 1.0
    return 0.0
