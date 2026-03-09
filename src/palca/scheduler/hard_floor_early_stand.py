from __future__ import annotations

import copy
from collections import deque
from dataclasses import dataclass
import math
from typing import Any, Callable, Sequence

from ..domain.box import Box
from ..domain.placement import PlacementPreview


@dataclass(frozen=True)
class HardFloorEarlyStandConfig:
    policy: str = "off"
    max_count: int = 1
    candidate_cap: int = 3
    max_placed_loss: int = 0
    max_largest_free_rect_loss_ratio: float = 0.08
    max_height_std_increase_mm: float = 40.0
    min_access_mouth_mm: int = 180


@dataclass(frozen=True)
class HardFloorFutureMetrics:
    placed_count: int
    largest_free_rect_area_mm2: float
    free_components: int
    inaccessible_pocket_area_mm2: float
    boundary_connected_free_area_mm2: float
    height_std_mm: float
    min_boundary_mouth_mm: float


@dataclass(frozen=True)
class EarlyStandDecision:
    candidate_index: int
    admitted: bool
    reject_reason: str
    projected_placed_loss: int = 0
    projected_lfr_loss_ratio: float = 0.0
    projected_height_std_increase_mm: float = 0.0
    debug_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class AccessGateEvaluation:
    passed: bool
    reject_checks: tuple[str, ...] = ()
    reject_detail: str = ""


def build_early_stand_config(
    *,
    policy: str = "off",
    max_count: int = 1,
    candidate_cap: int = 3,
    max_placed_loss: int = 0,
    max_largest_free_rect_loss_ratio: float = 0.08,
    max_height_std_increase_mm: float = 40.0,
    min_access_mouth_mm: int = 180,
) -> HardFloorEarlyStandConfig:
    normalized_policy = str(policy or "off").strip().lower()
    if normalized_policy not in {"off", "bonus", "regret_gated"}:
        normalized_policy = "off"
    return HardFloorEarlyStandConfig(
        policy=normalized_policy,
        max_count=max(0, int(max_count)),
        candidate_cap=max(1, int(candidate_cap)),
        max_placed_loss=max(0, int(max_placed_loss)),
        max_largest_free_rect_loss_ratio=max(0.0, float(max_largest_free_rect_loss_ratio)),
        max_height_std_increase_mm=max(0.0, float(max_height_std_increase_mm)),
        min_access_mouth_mm=max(0, int(min_access_mouth_mm)),
    )


def simulate_floor_only_future(
    *,
    pallet: object,
    baseline_preview: PlacementPreview,
    baseline_box: Box | None,
    future_boxes: Sequence[Box],
    preview_fn: Callable[[object, Box], PlacementPreview],
    lookahead_items: int,
) -> HardFloorFutureMetrics:
    return _simulate_future(
        pallet=pallet,
        first_preview=baseline_preview,
        first_box=baseline_box,
        future_boxes=future_boxes,
        preview_fn=preview_fn,
        lookahead_items=lookahead_items,
    )


def simulate_after_stand_then_floor_only(
    *,
    pallet: object,
    stand_preview: PlacementPreview,
    stand_box: Box | None,
    future_boxes: Sequence[Box],
    preview_fn: Callable[[object, Box], PlacementPreview],
    lookahead_items: int,
) -> HardFloorFutureMetrics:
    return _simulate_future(
        pallet=pallet,
        first_preview=stand_preview,
        first_box=stand_box,
        future_boxes=future_boxes,
        preview_fn=preview_fn,
        lookahead_items=lookahead_items,
    )


def passes_local_geom_gate(
    *,
    pallet: object,
    stand_preview: PlacementPreview,
    floor_candidate_previews: Sequence[PlacementPreview],
    config: HardFloorEarlyStandConfig,
) -> bool:
    rect = _preview_rect(stand_preview)
    if rect is None:
        return False

    bounds = _pallet_bounds(pallet)
    boundary_touch = _rect_touches_boundary(rect, bounds)
    floor_rects = _floor_rects_from_pallet(pallet)
    frontier_touch = boundary_touch or any(_rectangles_touch(rect, other) for other in floor_rects)
    if not frontier_touch:
        return False

    before_topology = _topology_metrics(pallet=pallet, extra_rects=[])
    after_topology = _topology_metrics(pallet=pallet, extra_rects=[rect])

    if after_topology["free_components"] > before_topology["free_components"]:
        return False
    if after_topology["inaccessible_area_mm2"] > before_topology["inaccessible_area_mm2"]:
        return False

    if not boundary_touch:
        has_boundary_planar = any(
            _rect_touches_boundary(_preview_rect(preview), bounds)
            for preview in floor_candidate_previews
            if _preview_rect(preview) is not None and not _is_stand_hw_preview(preview)
        )
        if has_boundary_planar:
            return False

    return True


def passes_regret_gate(
    *,
    baseline: HardFloorFutureMetrics,
    candidate: HardFloorFutureMetrics,
    config: HardFloorEarlyStandConfig,
) -> tuple[bool, int, float, float]:
    placed_loss = max(0, int(baseline.placed_count) - int(candidate.placed_count))
    if baseline.largest_free_rect_area_mm2 <= 0.0:
        lfr_loss_ratio = 0.0
    else:
        lfr_loss = max(0.0, float(baseline.largest_free_rect_area_mm2) - float(candidate.largest_free_rect_area_mm2))
        lfr_loss_ratio = float(lfr_loss / max(1.0, float(baseline.largest_free_rect_area_mm2)))
    height_std_increase = max(0.0, float(candidate.height_std_mm) - float(baseline.height_std_mm))

    accepted = (
        placed_loss <= int(config.max_placed_loss)
        and lfr_loss_ratio <= float(config.max_largest_free_rect_loss_ratio)
        and height_std_increase <= float(config.max_height_std_increase_mm)
    )
    return accepted, int(placed_loss), float(lfr_loss_ratio), float(height_std_increase)


def passes_access_gate(
    *,
    baseline: HardFloorFutureMetrics,
    candidate: HardFloorFutureMetrics,
    config: HardFloorEarlyStandConfig,
) -> bool:
    return _evaluate_access_gate(baseline=baseline, candidate=candidate, config=config).passed


def admit_early_stands(
    *,
    config: HardFloorEarlyStandConfig,
    pallet: object,
    floor_candidate_previews: Sequence[PlacementPreview],
    stand_candidates: Sequence[tuple[int, PlacementPreview, Box]],
    future_boxes: Sequence[Box],
    baseline_preview: PlacementPreview,
    baseline_box: Box | None,
    preview_fn: Callable[[object, Box], PlacementPreview],
    lookahead_items: int,
    remaining_count: int,
    step_idx: int,
    pallet_id: int | str,
) -> tuple[list[int], list[EarlyStandDecision], HardFloorFutureMetrics]:
    baseline_metrics = simulate_floor_only_future(
        pallet=pallet,
        baseline_preview=baseline_preview,
        baseline_box=baseline_box,
        future_boxes=future_boxes,
        preview_fn=preview_fn,
        lookahead_items=lookahead_items,
    )

    if remaining_count <= 0:
        return [], [], baseline_metrics

    admitted: list[int] = []
    decisions: list[EarlyStandDecision] = []
    cap = min(int(config.candidate_cap), len(stand_candidates))

    for idx in range(cap):
        candidate_index, stand_preview, stand_box = stand_candidates[idx]
        if len(admitted) >= int(remaining_count):
            break

        if not passes_local_geom_gate(
            pallet=pallet,
            stand_preview=stand_preview,
            floor_candidate_previews=floor_candidate_previews,
            config=config,
        ):
            decisions.append(
                EarlyStandDecision(
                    candidate_index=int(candidate_index),
                    admitted=False,
                    reject_reason="geom",
                    debug_payload=_build_reject_debug_payload(
                        step_idx=int(step_idx),
                        pallet_id=pallet_id,
                        stand_preview=stand_preview,
                        baseline_metrics=baseline_metrics,
                        stand_metrics=None,
                        reject_reason="geom",
                        access_eval=None,
                        config=config,
                    ),
                )
            )
            continue

        stand_metrics = simulate_after_stand_then_floor_only(
            pallet=pallet,
            stand_preview=stand_preview,
            stand_box=stand_box,
            future_boxes=future_boxes,
            preview_fn=preview_fn,
            lookahead_items=lookahead_items,
        )
        ok_regret, placed_loss, lfr_loss_ratio, height_std_increase = passes_regret_gate(
            baseline=baseline_metrics,
            candidate=stand_metrics,
            config=config,
        )
        if not ok_regret:
            decisions.append(
                EarlyStandDecision(
                    candidate_index=int(candidate_index),
                    admitted=False,
                    reject_reason="regret",
                    projected_placed_loss=int(placed_loss),
                    projected_lfr_loss_ratio=float(lfr_loss_ratio),
                    projected_height_std_increase_mm=float(height_std_increase),
                    debug_payload=_build_reject_debug_payload(
                        step_idx=int(step_idx),
                        pallet_id=pallet_id,
                        stand_preview=stand_preview,
                        baseline_metrics=baseline_metrics,
                        stand_metrics=stand_metrics,
                        reject_reason="regret",
                        access_eval=None,
                        config=config,
                    ),
                )
            )
            continue

        access_eval = _evaluate_access_gate(
            baseline=baseline_metrics,
            candidate=stand_metrics,
            config=config,
        )
        if not access_eval.passed:
            decisions.append(
                EarlyStandDecision(
                    candidate_index=int(candidate_index),
                    admitted=False,
                    reject_reason="access",
                    projected_placed_loss=int(placed_loss),
                    projected_lfr_loss_ratio=float(lfr_loss_ratio),
                    projected_height_std_increase_mm=float(height_std_increase),
                    debug_payload=_build_reject_debug_payload(
                        step_idx=int(step_idx),
                        pallet_id=pallet_id,
                        stand_preview=stand_preview,
                        baseline_metrics=baseline_metrics,
                        stand_metrics=stand_metrics,
                        reject_reason="access",
                        access_eval=access_eval,
                        config=config,
                    ),
                )
            )
            continue

        admitted.append(int(candidate_index))
        decisions.append(
            EarlyStandDecision(
                candidate_index=int(candidate_index),
                admitted=True,
                reject_reason="",
                projected_placed_loss=int(placed_loss),
                projected_lfr_loss_ratio=float(lfr_loss_ratio),
                projected_height_std_increase_mm=float(height_std_increase),
            )
        )

    return admitted, decisions, baseline_metrics


def _evaluate_access_gate(
    *,
    baseline: HardFloorFutureMetrics,
    candidate: HardFloorFutureMetrics,
    config: HardFloorEarlyStandConfig,
) -> AccessGateEvaluation:
    checks: list[str] = []
    details: list[str] = []

    baseline_pocket = float(baseline.inaccessible_pocket_area_mm2)
    candidate_pocket = float(candidate.inaccessible_pocket_area_mm2)
    if candidate_pocket > baseline_pocket:
        checks.append("inaccessible_pocket_area_mm2_increase")
        details.append(f"inaccessible_pocket_area_mm2({candidate_pocket:.1f}>{baseline_pocket:.1f})")

    baseline_connected = float(baseline.boundary_connected_free_area_mm2)
    candidate_connected = float(candidate.boundary_connected_free_area_mm2)
    if candidate_connected < baseline_connected:
        checks.append("boundary_connected_free_area_mm2_drop")
        details.append(f"boundary_connected_free_area_mm2({candidate_connected:.1f}<{baseline_connected:.1f})")

    candidate_mouth = float(candidate.min_boundary_mouth_mm)
    min_mouth = float(config.min_access_mouth_mm)
    if candidate_mouth < min_mouth:
        checks.append("min_boundary_mouth_mm_below_threshold")
        details.append(f"min_boundary_mouth_mm({candidate_mouth:.1f}<{min_mouth:.1f})")

    return AccessGateEvaluation(
        passed=len(checks) == 0,
        reject_checks=tuple(checks),
        reject_detail="; ".join(details),
    )


def _build_reject_debug_payload(
    *,
    step_idx: int,
    pallet_id: int | str,
    stand_preview: PlacementPreview,
    baseline_metrics: HardFloorFutureMetrics,
    stand_metrics: HardFloorFutureMetrics | None,
    reject_reason: str,
    access_eval: AccessGateEvaluation | None,
    config: HardFloorEarlyStandConfig,
) -> dict[str, Any]:
    placement = getattr(stand_preview, "placement", None)
    orientation_family = str(getattr(placement, "orientation_family", "") or "")
    orientation_name = str(getattr(placement, "orientation_name", "") or "")
    orientation = orientation_name or orientation_family
    x_mm = int(getattr(placement, "x_mm", 0) or 0)
    y_mm = int(getattr(placement, "y_mm", 0) or 0)
    l_mm = int(getattr(placement, "length_mm", 0) or 0)
    w_mm = int(getattr(placement, "width_mm", 0) or 0)
    if isinstance(pallet_id, int):
        pallet_value: int | str = int(pallet_id)
    else:
        pallet_value = str(pallet_id)

    return {
        "step": int(step_idx),
        "pallet_id": pallet_value,
        "orientation": orientation,
        "orientation_family": orientation_family,
        "orientation_name": orientation_name,
        "x": x_mm,
        "y": y_mm,
        "l": l_mm,
        "w": w_mm,
        "reject_reason": str(reject_reason or ""),
        "access_reject_checks": list(access_eval.reject_checks) if access_eval is not None else [],
        "access_reject_reason": str(access_eval.reject_detail or "") if access_eval is not None else "",
        "access_min_mouth_threshold_mm": float(config.min_access_mouth_mm),
        "boundary_connected_free_area_mm2": {
            "baseline": float(baseline_metrics.boundary_connected_free_area_mm2),
            "stand": float(stand_metrics.boundary_connected_free_area_mm2) if stand_metrics is not None else None,
        },
        "inaccessible_pocket_area_mm2": {
            "baseline": float(baseline_metrics.inaccessible_pocket_area_mm2),
            "stand": float(stand_metrics.inaccessible_pocket_area_mm2) if stand_metrics is not None else None,
        },
        "min_boundary_mouth_mm": {
            "baseline": float(baseline_metrics.min_boundary_mouth_mm),
            "stand": float(stand_metrics.min_boundary_mouth_mm) if stand_metrics is not None else None,
        },
        "largest_free_rect_area_mm2": {
            "baseline": float(baseline_metrics.largest_free_rect_area_mm2),
            "stand": float(stand_metrics.largest_free_rect_area_mm2) if stand_metrics is not None else None,
        },
        "placed_count": {
            "baseline": int(baseline_metrics.placed_count),
            "stand": int(stand_metrics.placed_count) if stand_metrics is not None else None,
        },
        "height_std_mm": {
            "baseline": float(baseline_metrics.height_std_mm),
            "stand": float(stand_metrics.height_std_mm) if stand_metrics is not None else None,
        },
    }


def _simulate_future(
    *,
    pallet: object,
    first_preview: PlacementPreview,
    first_box: Box | None,
    future_boxes: Sequence[Box],
    preview_fn: Callable[[object, Box], PlacementPreview],
    lookahead_items: int,
) -> HardFloorFutureMetrics:
    try:
        pallet_clone = copy.deepcopy(pallet)
    except Exception:
        pallet_clone = pallet

    placed_count = 0
    selected_id = getattr(first_box, "box_id", None) if first_box is not None else None
    selected_consumed = False

    if first_preview.feasible and _preview_is_floor(first_preview):
        try:
            commit_fn = getattr(pallet_clone, "commit_place")
            commit_fn(first_preview)
            placed_count += 1
        except Exception:
            pass

    cap = max(1, int(lookahead_items))
    for box in list(future_boxes)[:cap]:
        if first_box is not None and not selected_consumed:
            if box is first_box or getattr(box, "box_id", None) == selected_id:
                selected_consumed = True
                continue
        try:
            preview = preview_fn(pallet_clone, box)
        except Exception:
            continue
        if not preview.feasible or not _preview_is_floor(preview):
            continue
        try:
            commit_fn = getattr(pallet_clone, "commit_place")
            commit_fn(preview)
            placed_count += 1
        except Exception:
            continue

    topo = _topology_metrics(pallet=pallet_clone, extra_rects=[])
    heights = _placement_heights_mm(pallet_clone)
    return HardFloorFutureMetrics(
        placed_count=int(placed_count),
        largest_free_rect_area_mm2=float(topo["largest_free_rect_area_mm2"]),
        free_components=int(topo["free_components"]),
        inaccessible_pocket_area_mm2=float(topo["inaccessible_area_mm2"]),
        boundary_connected_free_area_mm2=float(topo["boundary_connected_area_mm2"]),
        height_std_mm=float(_stddev(heights)),
        min_boundary_mouth_mm=float(topo["min_boundary_mouth_mm"]),
    )


def _preview_is_floor(preview: PlacementPreview | None) -> bool:
    if preview is None:
        return False
    placement = getattr(preview, "placement", None)
    if placement is None:
        return False
    try:
        return int(getattr(placement, "z_mm", 0) or 0) == 0
    except Exception:
        return False


def _is_stand_hw_preview(preview: PlacementPreview | None) -> bool:
    if preview is None:
        return False
    placement = getattr(preview, "placement", None)
    family = str(getattr(placement, "orientation_family", "") or "").lower()
    name = str(getattr(placement, "orientation_name", "") or "").lower()
    return family == "stand_hw" or "stand_hw" in name


def _preview_rect(preview: PlacementPreview | None) -> tuple[int, int, int, int] | None:
    if preview is None:
        return None
    placement = getattr(preview, "placement", None)
    if placement is None:
        return None
    try:
        x0 = int(getattr(placement, "x_mm", 0) or 0)
        y0 = int(getattr(placement, "y_mm", 0) or 0)
        x1 = x0 + int(getattr(placement, "length_mm", 0) or 0)
        y1 = y0 + int(getattr(placement, "width_mm", 0) or 0)
        return (x0, y0, x1, y1)
    except Exception:
        return None


def _pallet_bounds(pallet: object) -> tuple[int, int, int, int]:
    spec = getattr(pallet, "spec", None)
    if spec is None:
        return (0, 0, 1200, 800)

    try:
        x0 = int(getattr(spec, "offset_mm", 0) or 0)
    except Exception:
        x0 = 0
    try:
        y0 = int(getattr(spec, "offset_mm", 0) or 0)
    except Exception:
        y0 = 0

    try:
        length = int(getattr(spec, "bin_length_mm", 1200) or 1200)
    except Exception:
        length = 1200
    try:
        width = int(getattr(spec, "bin_width_mm", 800) or 800)
    except Exception:
        width = 800
    return (x0, y0, x0 + max(1, length), y0 + max(1, width))


def _floor_rects_from_pallet(pallet: object) -> list[tuple[int, int, int, int]]:
    rects: list[tuple[int, int, int, int]] = []
    for placement in list(getattr(pallet, "placements", []) or []):
        try:
            if int(getattr(placement, "z_mm", 0) or 0) != 0:
                continue
            x0 = int(getattr(placement, "x_mm", 0) or 0)
            y0 = int(getattr(placement, "y_mm", 0) or 0)
            x1 = x0 + int(getattr(placement, "length_mm", 0) or 0)
            y1 = y0 + int(getattr(placement, "width_mm", 0) or 0)
        except Exception:
            continue
        rects.append((x0, y0, x1, y1))
    return rects


def _placement_heights_mm(pallet: object) -> list[float]:
    heights: list[float] = []
    for placement in list(getattr(pallet, "placements", []) or []):
        try:
            top = int(getattr(placement, "z_mm", 0) or 0) + int(getattr(placement, "height_mm", 0) or 0)
        except Exception:
            continue
        heights.append(float(top))
    return heights


def _rectangles_touch(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    overlap_x = min(ax1, bx1) - max(ax0, bx0)
    overlap_y = min(ay1, by1) - max(ay0, by0)
    if overlap_x > 0 and overlap_y > 0:
        return True
    touch_x = overlap_x > 0 and (ay1 == by0 or by1 == ay0)
    touch_y = overlap_y > 0 and (ax1 == bx0 or bx1 == ax0)
    return bool(touch_x or touch_y)


def _rect_touches_boundary(rect: tuple[int, int, int, int] | None, bounds: tuple[int, int, int, int]) -> bool:
    if rect is None:
        return False
    x0, y0, x1, y1 = rect
    bx0, by0, bx1, by1 = bounds
    return x0 <= bx0 or y0 <= by0 or x1 >= bx1 or y1 >= by1


def _topology_metrics(
    *,
    pallet: object,
    extra_rects: Sequence[tuple[int, int, int, int]],
) -> dict[str, float | int]:
    bounds = _pallet_bounds(pallet)
    bx0, by0, bx1, by1 = bounds
    length = max(1, bx1 - bx0)
    width = max(1, by1 - by0)

    # Discretización barata y suficientemente fina para mouth/proxy de pockets.
    cell_mm = max(20, min(80, int(min(length, width) / 20)))
    nx = max(1, math.ceil(length / cell_mm))
    ny = max(1, math.ceil(width / cell_mm))
    free_grid = [[True for _ in range(nx)] for _ in range(ny)]

    rects = _floor_rects_from_pallet(pallet) + [tuple(r) for r in extra_rects]
    for rect in rects:
        _mark_occupied(
            free_grid=free_grid,
            rect=rect,
            bounds=bounds,
            cell_mm=cell_mm,
        )

    components = _free_components(free_grid)
    cell_area = float(cell_mm * cell_mm)
    boundary_connected_cells = 0
    total_free_cells = 0
    for comp_cells, touches_boundary in components:
        comp_size = len(comp_cells)
        total_free_cells += comp_size
        if touches_boundary:
            boundary_connected_cells += comp_size

    min_boundary_mouth_mm = _min_boundary_mouth_mm(free_grid=free_grid, cell_mm=cell_mm)

    return {
        "largest_free_rect_area_mm2": float(_largest_free_rect_area_mm2(free_grid=free_grid, cell_mm=cell_mm)),
        "free_components": int(len(components)),
        "inaccessible_area_mm2": float((total_free_cells - boundary_connected_cells) * cell_area),
        "boundary_connected_area_mm2": float(boundary_connected_cells * cell_area),
        "min_boundary_mouth_mm": float(min_boundary_mouth_mm),
    }


def _mark_occupied(
    *,
    free_grid: list[list[bool]],
    rect: tuple[int, int, int, int],
    bounds: tuple[int, int, int, int],
    cell_mm: int,
) -> None:
    nx = len(free_grid[0]) if free_grid else 0
    ny = len(free_grid)
    if nx <= 0 or ny <= 0:
        return

    bx0, by0, bx1, by1 = bounds
    x0, y0, x1, y1 = rect
    x0 = max(x0, bx0)
    y0 = max(y0, by0)
    x1 = min(x1, bx1)
    y1 = min(y1, by1)
    if x0 >= x1 or y0 >= y1:
        return

    gx0 = max(0, int((x0 - bx0) // cell_mm))
    gy0 = max(0, int((y0 - by0) // cell_mm))
    gx1 = min(nx, int(math.ceil((x1 - bx0) / float(cell_mm))))
    gy1 = min(ny, int(math.ceil((y1 - by0) / float(cell_mm))))

    for gy in range(gy0, gy1):
        for gx in range(gx0, gx1):
            free_grid[gy][gx] = False


def _free_components(free_grid: list[list[bool]]) -> list[tuple[list[tuple[int, int]], bool]]:
    ny = len(free_grid)
    nx = len(free_grid[0]) if ny else 0
    visited = [[False for _ in range(nx)] for _ in range(ny)]
    out: list[tuple[list[tuple[int, int]], bool]] = []

    for y in range(ny):
        for x in range(nx):
            if not free_grid[y][x] or visited[y][x]:
                continue
            queue: deque[tuple[int, int]] = deque([(x, y)])
            visited[y][x] = True
            cells: list[tuple[int, int]] = []
            touches_boundary = False
            while queue:
                cx, cy = queue.popleft()
                cells.append((cx, cy))
                if cx == 0 or cy == 0 or cx == (nx - 1) or cy == (ny - 1):
                    touches_boundary = True
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx0 = cx + dx
                    ny0 = cy + dy
                    if nx0 < 0 or ny0 < 0 or nx0 >= nx or ny0 >= ny:
                        continue
                    if visited[ny0][nx0] or not free_grid[ny0][nx0]:
                        continue
                    visited[ny0][nx0] = True
                    queue.append((nx0, ny0))
            out.append((cells, touches_boundary))
    return out


def _min_boundary_mouth_mm(*, free_grid: list[list[bool]], cell_mm: int) -> float:
    if not free_grid:
        return 0.0
    ny = len(free_grid)
    nx = len(free_grid[0]) if ny else 0
    if nx <= 0:
        return 0.0

    runs: list[int] = []

    for row in (free_grid[0], free_grid[ny - 1]):
        runs.extend(_free_runs_1d(row))

    left_col = [free_grid[y][0] for y in range(ny)]
    right_col = [free_grid[y][nx - 1] for y in range(ny)]
    runs.extend(_free_runs_1d(left_col))
    runs.extend(_free_runs_1d(right_col))

    runs = [r for r in runs if r > 0]
    if not runs:
        return 0.0
    return float(min(runs) * cell_mm)


def _free_runs_1d(bits: Sequence[bool]) -> list[int]:
    runs: list[int] = []
    current = 0
    for bit in bits:
        if bool(bit):
            current += 1
            continue
        if current > 0:
            runs.append(current)
            current = 0
    if current > 0:
        runs.append(current)
    return runs


def _largest_free_rect_area_mm2(*, free_grid: list[list[bool]], cell_mm: int) -> float:
    ny = len(free_grid)
    nx = len(free_grid[0]) if ny else 0
    if nx <= 0 or ny <= 0:
        return 0.0

    heights = [0 for _ in range(nx)]
    max_cells = 0
    for y in range(ny):
        for x in range(nx):
            heights[x] = heights[x] + 1 if free_grid[y][x] else 0
        max_cells = max(max_cells, _largest_histogram_area_cells(heights))
    return float(max_cells * cell_mm * cell_mm)


def _largest_histogram_area_cells(heights: Sequence[int]) -> int:
    stack: list[int] = []
    best = 0
    ext = list(heights) + [0]
    for i, h in enumerate(ext):
        while stack and ext[stack[-1]] > h:
            top = stack.pop()
            height = ext[top]
            left = stack[-1] if stack else -1
            width = i - left - 1
            best = max(best, int(height) * int(width))
        stack.append(i)
    return int(best)


def _stddev(values: Sequence[float]) -> float:
    if len(values) <= 1:
        return 0.0
    mean = float(sum(values) / len(values))
    var = sum((float(v) - mean) ** 2 for v in values) / float(len(values))
    return float(math.sqrt(max(0.0, var)))
