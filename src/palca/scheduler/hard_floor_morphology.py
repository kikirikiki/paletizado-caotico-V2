from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from ..packer.maxrects2d import Rect


@dataclass(frozen=True)
class HardFloorMorphologyMetrics:
    base_fill_ratio: float
    largest_free_rect_area_mm2: int
    occupied_base_zones: int
    bin_area_mm2: int
    boundary_connected_free_area_mm2: int
    inaccessible_pocket_area_mm2: int
    height_std_mm: float
    max_height_gap_mm: int
    isolated_high_spots_count: int
    stand_count_on_base: int


@dataclass(frozen=True)
class HardFloorCandidateDecision:
    accepted: bool
    metrics: HardFloorMorphologyMetrics
    projected_density_proxy: float
    legacy_score: float
    reject_reason: str | None
    rank_key: tuple[float, float, float, float, float, float, float]


@dataclass(frozen=True)
class HardFloorMorphologyConfig:
    mode: str = "off"
    max_height_gap_increase_mm: int = 60
    max_inaccessible_pocket_increase_mm2: int = 0
    max_boundary_connected_loss_ratio: float = 0.25
    max_isolated_high_spots_increase: int = 0
    isolated_high_spot_delta_mm: float = 50.0
    prefer_floor_while_open_base: bool = True
    min_base_fill_ratio_before_stack: float = 0.52
    min_base_zones_before_stack: int = 4
    max_largest_free_rect_ratio_before_stack: float = 0.30

    def normalized_mode(self) -> str:
        mode = str(self.mode or "off").strip().lower()
        if mode not in {"off", "on"}:
            return "off"
        return mode


@dataclass(frozen=True)
class HardFloorBaseClosureState:
    floor_candidate_count: int
    step_idx: int
    base_fill_ratio: float
    largest_free_rect_area_mm2: int
    occupied_base_zones: int
    bin_area_mm2: int


@dataclass(frozen=True)
class _FloorRect:
    rect: Rect
    top_height_mm: int
    is_stand_hw: bool


def compute_hard_floor_morphology_metrics(
    *,
    floor_rects: Sequence[tuple[int, int, int, int, int, bool]],
    bin_width_mm: int,
    bin_height_mm: int,
    bin_area_mm2: int,
    config: HardFloorMorphologyConfig,
) -> HardFloorMorphologyMetrics:
    bin_w = max(1, int(bin_width_mm))
    bin_h = max(1, int(bin_height_mm))
    area_hint = max(1, int(bin_area_mm2))

    normalized: list[_FloorRect] = []
    for x_mm, y_mm, length_mm, width_mm, top_height_mm, is_stand_hw in floor_rects:
        x0 = max(0, min(bin_w, int(x_mm)))
        y0 = max(0, min(bin_h, int(y_mm)))
        x1 = max(0, min(bin_w, int(x_mm) + max(0, int(length_mm))))
        y1 = max(0, min(bin_h, int(y_mm) + max(0, int(width_mm))))
        w = max(0, x1 - x0)
        h = max(0, y1 - y0)
        if w <= 0 or h <= 0:
            continue
        normalized.append(
            _FloorRect(
                rect=Rect(x=x0, y=y0, w=w, h=h),
                top_height_mm=max(0, int(top_height_mm)),
                is_stand_hw=bool(is_stand_hw),
            )
        )

    free_rects = _free_rects_from_occupied(bin_w=bin_w, bin_h=bin_h, occupied=[item.rect for item in normalized])
    total_free_area = sum(int(r.area) for r in free_rects)
    largest_free_rect = max((int(r.area) for r in free_rects), default=0)

    connected_free_area = _boundary_connected_area(bin_w=bin_w, bin_h=bin_h, free_rects=free_rects)
    inaccessible_pocket_area = max(0, int(total_free_area) - int(connected_free_area))

    occupied_area = sum(int(item.rect.area) for item in normalized)
    base_fill_ratio = float(occupied_area) / float(max(1, area_hint))
    occupied_base_zones = _occupied_base_zone_count(
        bin_w=bin_w,
        bin_h=bin_h,
        occupied_rects=[item.rect for item in normalized],
    )

    height_std_mm, max_height_gap_mm = _height_dispersion(
        filled=[(int(item.rect.area), int(item.top_height_mm)) for item in normalized],
        total_area=area_hint,
    )
    isolated_high_spots = _isolated_high_spots_count(
        rects=normalized,
        min_height_delta=float(config.isolated_high_spot_delta_mm),
    )
    stand_count_on_base = sum(1 for item in normalized if item.is_stand_hw)

    return HardFloorMorphologyMetrics(
        base_fill_ratio=float(base_fill_ratio),
        largest_free_rect_area_mm2=int(largest_free_rect),
        occupied_base_zones=int(occupied_base_zones),
        bin_area_mm2=int(area_hint),
        boundary_connected_free_area_mm2=int(connected_free_area),
        inaccessible_pocket_area_mm2=int(inaccessible_pocket_area),
        height_std_mm=float(height_std_mm),
        max_height_gap_mm=int(max_height_gap_mm),
        isolated_high_spots_count=int(isolated_high_spots),
        stand_count_on_base=int(stand_count_on_base),
    )


def evaluate_hard_floor_candidate(
    *,
    baseline: HardFloorMorphologyMetrics,
    candidate: HardFloorMorphologyMetrics,
    projected_density_proxy: float,
    legacy_score: float,
    config: HardFloorMorphologyConfig,
    prefer_base_closure: bool = False,
) -> HardFloorCandidateDecision:
    reject_reason: str | None = None

    max_pocket_increase = max(0, int(config.max_inaccessible_pocket_increase_mm2))
    if float(baseline.base_fill_ratio) < 0.20:
        # Early-phase geometry is volatile; keep pocket gating softer.
        max_pocket_increase = max(max_pocket_increase, 120_000)
    max_pockets = int(baseline.inaccessible_pocket_area_mm2) + int(max_pocket_increase)
    if int(candidate.inaccessible_pocket_area_mm2) > int(max_pockets):
        reject_reason = "inaccessible_pockets_worse"

    if reject_reason is None:
        early_base_extra_gap_mm = 0
        if float(baseline.base_fill_ratio) < 0.20:
            # In very early base construction, large absolute gaps are unavoidable.
            early_base_extra_gap_mm = 400
        max_gap_allowed = (
            int(baseline.max_height_gap_mm)
            + max(0, int(config.max_height_gap_increase_mm))
            + int(early_base_extra_gap_mm)
        )
        if int(candidate.max_height_gap_mm) > int(max_gap_allowed):
            reject_reason = "height_gap_too_large"

    if reject_reason is None:
        max_isolated_increase = max(0, int(config.max_isolated_high_spots_increase))
        if float(baseline.base_fill_ratio) < 0.20:
            max_isolated_increase = max(max_isolated_increase, 1)
        max_isolated = int(baseline.isolated_high_spots_count) + int(max_isolated_increase)
        if int(candidate.isolated_high_spots_count) > int(max_isolated):
            reject_reason = "isolated_high_spot"

    if reject_reason is None:
        boundary_floor = max(1.0, float(baseline.boundary_connected_free_area_mm2))
        allowed_ratio = max(0.0, min(1.0, float(config.max_boundary_connected_loss_ratio)))
        if float(baseline.base_fill_ratio) < 0.20:
            allowed_ratio = max(float(allowed_ratio), 0.45)
        min_boundary = boundary_floor * (1.0 - allowed_ratio)
        if float(candidate.boundary_connected_free_area_mm2) + 1e-6 < float(min_boundary):
            reject_reason = "boundary_continuity_broken"

    if bool(prefer_base_closure) and reject_reason in {"inaccessible_pockets_worse", "boundary_continuity_broken"}:
        # While the base is still open, allow softer continuity/pocket tradeoffs.
        reject_reason = None

    accepted = reject_reason is None
    if bool(prefer_base_closure):
        largest_free_rect_ratio = float(candidate.largest_free_rect_area_mm2) / float(max(1, int(candidate.bin_area_mm2)))
        rank_key = (
            float(candidate.occupied_base_zones),
            -float(largest_free_rect_ratio),
            float(candidate.base_fill_ratio),
            float(projected_density_proxy),
            -float(candidate.max_height_gap_mm),
            -float(candidate.height_std_mm),
            float(legacy_score),
        )
    else:
        rank_key = (
            float(projected_density_proxy),
            -float(candidate.max_height_gap_mm),
            -float(candidate.height_std_mm),
            float(candidate.boundary_connected_free_area_mm2),
            -float(candidate.inaccessible_pocket_area_mm2),
            float(candidate.largest_free_rect_area_mm2),
            float(legacy_score),
        )

    return HardFloorCandidateDecision(
        accepted=bool(accepted),
        metrics=candidate,
        projected_density_proxy=float(projected_density_proxy),
        legacy_score=float(legacy_score),
        reject_reason=reject_reason,
        rank_key=rank_key,
    )


def is_base_sufficiently_closed(
    *,
    state: HardFloorBaseClosureState,
    config: HardFloorMorphologyConfig,
) -> bool:
    fill_ratio = float(state.base_fill_ratio)
    occupied_zones = max(0, int(state.occupied_base_zones))
    largest_free_rect = max(0, int(state.largest_free_rect_area_mm2))
    bin_area = max(1, int(state.bin_area_mm2))
    step_idx = max(0, int(state.step_idx))

    min_fill = max(0.0, min(0.98, float(config.min_base_fill_ratio_before_stack)))
    min_zones = max(1, int(config.min_base_zones_before_stack))
    max_free_rect_ratio = max(0.0, min(1.0, float(config.max_largest_free_rect_ratio_before_stack)))

    # Keep the closure gate stricter in very early steps.
    if step_idx <= 1:
        min_fill = min(0.98, float(min_fill) + 0.08)
        min_zones += 1
    elif step_idx == 2:
        min_fill = min(0.98, float(min_fill) + 0.04)

    fill_ok = float(fill_ratio) >= float(min_fill)
    zones_ok = int(occupied_zones) >= int(min_zones)
    free_rect_ratio = float(largest_free_rect) / float(max(1, bin_area))
    free_rect_ok = float(free_rect_ratio) <= float(max_free_rect_ratio)
    return bool((fill_ok and zones_ok) or (fill_ok and free_rect_ok))


def should_prefer_floor_over_stack(
    *,
    state: HardFloorBaseClosureState,
    config: HardFloorMorphologyConfig,
) -> bool:
    if not bool(config.prefer_floor_while_open_base):
        return False
    if int(state.floor_candidate_count) <= 0:
        return False
    return not is_base_sufficiently_closed(state=state, config=config)


def _free_rects_from_occupied(*, bin_w: int, bin_h: int, occupied: Sequence[Rect]) -> list[Rect]:
    free_rects: list[Rect] = [Rect(0, 0, int(bin_w), int(bin_h))]
    for occ in occupied:
        clipped = _clip_rect(occ, bin_w=bin_w, bin_h=bin_h)
        if clipped is None:
            continue
        split: list[Rect] = []
        for free in free_rects:
            if not free.intersects(clipped):
                split.append(free)
                continue
            split.extend(MaxRectsSplit.split(free=free, placed=clipped))
        free_rects = _prune_rects(split)
    return free_rects


def _boundary_connected_area(*, bin_w: int, bin_h: int, free_rects: Sequence[Rect]) -> int:
    if not free_rects:
        return 0

    starts = {
        idx
        for idx, rect in enumerate(free_rects)
        if rect.x == 0 or rect.y == 0 or (rect.x + rect.w) == int(bin_w) or (rect.y + rect.h) == int(bin_h)
    }
    if not starts:
        return 0

    stack = list(starts)
    visited: set[int] = set()
    connected_area = 0
    while stack:
        idx = stack.pop()
        if idx in visited:
            continue
        visited.add(idx)
        rect = free_rects[idx]
        connected_area += int(rect.area)
        for j, other in enumerate(free_rects):
            if j in visited:
                continue
            if _rects_connected(rect, other):
                stack.append(j)
    return int(connected_area)


def _occupied_base_zone_count(*, bin_w: int, bin_h: int, occupied_rects: Sequence[Rect]) -> int:
    if not occupied_rects:
        return 0
    zones_x = 3
    zones_y = 2
    touched = 0
    for ix in range(zones_x):
        zx0 = (int(ix) * int(bin_w)) // int(zones_x)
        zx1 = ((int(ix) + 1) * int(bin_w)) // int(zones_x)
        for iy in range(zones_y):
            zy0 = (int(iy) * int(bin_h)) // int(zones_y)
            zy1 = ((int(iy) + 1) * int(bin_h)) // int(zones_y)
            zone = Rect(x=zx0, y=zy0, w=max(0, zx1 - zx0), h=max(0, zy1 - zy0))
            if int(zone.w) <= 0 or int(zone.h) <= 0:
                continue
            if any(_rect_intersection_area(zone, rect) > 0 for rect in occupied_rects):
                touched += 1
    return int(touched)


def _rect_intersection_area(a: Rect, b: Rect) -> int:
    ix0 = max(int(a.x), int(b.x))
    iy0 = max(int(a.y), int(b.y))
    ix1 = min(int(a.x + a.w), int(b.x + b.w))
    iy1 = min(int(a.y + a.h), int(b.y + b.h))
    w = max(0, int(ix1) - int(ix0))
    h = max(0, int(iy1) - int(iy0))
    return int(w * h)


def _height_dispersion(*, filled: Sequence[tuple[int, int]], total_area: int) -> tuple[float, int]:
    if total_area <= 0:
        return 0.0, 0

    weighted_sum = 0.0
    weighted_sq_sum = 0.0
    min_h = 0
    max_h = 0
    seen = False
    filled_area = 0

    for area, h_mm in filled:
        a = max(0, int(area))
        h = max(0, int(h_mm))
        if a <= 0:
            continue
        filled_area += a
        weighted_sum += float(a) * float(h)
        weighted_sq_sum += float(a) * float(h * h)
        if not seen:
            min_h = h
            max_h = h
            seen = True
        else:
            min_h = min(min_h, h)
            max_h = max(max_h, h)

    free_area = max(0, int(total_area) - int(filled_area))
    if free_area > 0:
        # Free floor at z=0 contributes to base leveling signal.
        pass

    mean = weighted_sum / float(max(1, total_area))
    variance = (weighted_sq_sum / float(max(1, total_area))) - (mean * mean)
    std_mm = math.sqrt(max(0.0, variance))

    if free_area > 0:
        min_h = 0 if seen else 0
    if not seen:
        min_h = 0
        max_h = 0

    return float(std_mm), int(max_h - min_h)


def _isolated_high_spots_count(*, rects: Sequence[_FloorRect], min_height_delta: float) -> int:
    if not rects:
        return 0

    weighted_area = sum(int(item.rect.area) for item in rects)
    if weighted_area <= 0:
        return 0

    weighted_height_sum = sum(float(int(item.rect.area)) * float(int(item.top_height_mm)) for item in rects)
    mean_height = weighted_height_sum / float(max(1, weighted_area))
    threshold = float(mean_height) + max(0.0, float(min_height_delta))

    high_indices = [idx for idx, item in enumerate(rects) if float(item.top_height_mm) >= threshold]
    if not high_indices:
        return 0

    isolated = 0
    for idx in high_indices:
        current = rects[idx].rect
        has_neighbor = False
        for other_idx in high_indices:
            if other_idx == idx:
                continue
            if _rects_connected(current, rects[other_idx].rect):
                has_neighbor = True
                break
        if not has_neighbor:
            isolated += 1
    return int(isolated)


def _rects_connected(a: Rect, b: Rect) -> bool:
    overlap_x = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
    overlap_y = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
    if overlap_x > 0 and overlap_y > 0:
        return True
    touch_x = overlap_x > 0 and ((a.y + a.h) == b.y or (b.y + b.h) == a.y)
    touch_y = overlap_y > 0 and ((a.x + a.w) == b.x or (b.x + b.w) == a.x)
    return bool(touch_x or touch_y)


def _clip_rect(rect: Rect, *, bin_w: int, bin_h: int) -> Rect | None:
    x0 = max(0, min(int(bin_w), int(rect.x)))
    y0 = max(0, min(int(bin_h), int(rect.y)))
    x1 = max(0, min(int(bin_w), int(rect.x + rect.w)))
    y1 = max(0, min(int(bin_h), int(rect.y + rect.h)))
    w = max(0, x1 - x0)
    h = max(0, y1 - y0)
    if w <= 0 or h <= 0:
        return None
    return Rect(x=x0, y=y0, w=w, h=h)


def _prune_rects(rects: Sequence[Rect]) -> list[Rect]:
    filtered = [r for r in rects if int(r.w) > 0 and int(r.h) > 0]
    if not filtered:
        return []

    dedup: list[Rect] = []
    seen: set[tuple[int, int, int, int]] = set()
    for rect in filtered:
        key = (int(rect.x), int(rect.y), int(rect.w), int(rect.h))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(rect)

    dedup.sort(key=lambda r: int(r.area), reverse=True)
    pruned: list[Rect] = []
    for rect in dedup:
        if any(_contains(other, rect) for other in pruned):
            continue
        pruned.append(rect)
    return pruned


def _contains(a: Rect, b: Rect) -> bool:
    return (
        int(a.x) <= int(b.x)
        and int(a.y) <= int(b.y)
        and int(a.x + a.w) >= int(b.x + b.w)
        and int(a.y + a.h) >= int(b.y + b.h)
    )


class MaxRectsSplit:
    @staticmethod
    def split(*, free: Rect, placed: Rect) -> list[Rect]:
        fx0, fy0 = int(free.x), int(free.y)
        fx1, fy1 = int(free.x + free.w), int(free.y + free.h)
        px0, py0 = int(placed.x), int(placed.y)
        px1, py1 = int(placed.x + placed.w), int(placed.y + placed.h)

        ix0 = max(fx0, px0)
        iy0 = max(fy0, py0)
        ix1 = min(fx1, px1)
        iy1 = min(fy1, py1)

        if ix0 >= ix1 or iy0 >= iy1:
            return [free]

        out: list[Rect] = []
        if iy0 > fy0:
            out.append(Rect(fx0, fy0, free.w, iy0 - fy0))
        if iy1 < fy1:
            out.append(Rect(fx0, iy1, free.w, fy1 - iy1))
        if ix0 > fx0:
            out.append(Rect(fx0, iy0, ix0 - fx0, iy1 - iy0))
        if ix1 < fx1:
            out.append(Rect(ix1, iy0, fx1 - ix1, iy1 - iy0))
        return [r for r in out if int(r.w) > 0 and int(r.h) > 0]
