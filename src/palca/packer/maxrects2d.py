from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def area(self) -> int:
        return int(self.w) * int(self.h)

    def contains(self, other: "Rect") -> bool:
        return (
            self.x <= other.x
            and self.y <= other.y
            and self.x + self.w >= other.x + other.w
            and self.y + self.h >= other.y + other.h
        )

    def intersects(self, other: "Rect") -> bool:
        return not (
            other.x >= self.x + self.w
            or other.x + other.w <= self.x
            or other.y >= self.y + self.h
            or other.y + other.h <= self.y
        )


@dataclass(frozen=True)
class MaxRectsCandidate:
    x: int
    y: int
    w: int
    h: int
    score: tuple[int, ...]


class MaxRects2D:
    def __init__(self, width: int, height: int, heuristic: str = "baf") -> None:
        if width <= 0 or height <= 0:
            raise ValueError("MaxRects2D dimensions must be positive")
        self.width = int(width)
        self.height = int(height)
        self.heuristic = heuristic.lower()
        self.free_rects: list[Rect] = [Rect(0, 0, self.width, self.height)]

    def copy(self) -> "MaxRects2D":
        clone = MaxRects2D(self.width, self.height, self.heuristic)
        clone.free_rects = list(self.free_rects)
        return clone

    @property
    def area(self) -> int:
        return int(self.width) * int(self.height)

    def free_area(self) -> int:
        return sum(rect.area for rect in self.free_rects)

    def find_candidate(self, w: int, h: int) -> MaxRectsCandidate | None:
        best: MaxRectsCandidate | None = None
        w = int(w)
        h = int(h)
        for rect in self.free_rects:
            if w <= rect.w and h <= rect.h:
                score = self._score_rect(rect, w, h)
                cand = MaxRectsCandidate(rect.x, rect.y, w, h, score)
                if best is None or cand.score < best.score:
                    best = cand
        return best

    def place(self, cand: MaxRectsCandidate) -> None:
        used = Rect(cand.x, cand.y, cand.w, cand.h)
        self.free_rects = self._place_rect(self.free_rects, used)

    def simulate_place(self, cand: MaxRectsCandidate) -> list[Rect]:
        used = Rect(cand.x, cand.y, cand.w, cand.h)
        return self._place_rect(self.free_rects, used)

    def _score_rect(self, rect: Rect, w: int, h: int) -> tuple[int, ...]:
        dw = rect.w - w
        dh = rect.h - h
        short_side = min(dw, dh)
        long_side = max(dw, dh)
        area_fit = rect.w * rect.h - w * h

        splits = self._split_rect(rect, Rect(rect.x, rect.y, w, h))
        delta_free = max(0, len(splits) - 1)
        other_max = 0
        for other in self.free_rects:
            if other is rect:
                continue
            other_max = max(other_max, other.area)
        largest_after = max([other_max] + [r.area for r in splits])

        if self.heuristic in ("bssf", "ssf", "short_side"):
            return (
                short_side,
                long_side,
                area_fit,
                delta_free,
                -largest_after,
                rect.y,
                rect.x,
            )
        if self.heuristic in ("baf", "area", "best_area"):
            return (
                area_fit,
                short_side,
                long_side,
                delta_free,
                -largest_after,
                rect.y,
                rect.x,
            )
        raise ValueError(f"Unknown MaxRects heuristic: {self.heuristic}")

    def _place_rect(self, free_rects: Iterable[Rect], used: Rect) -> list[Rect]:
        new_free: list[Rect] = []
        for rect in free_rects:
            if not rect.intersects(used):
                new_free.append(rect)
                continue
            new_free.extend(self._split_rect(rect, used))
        new_free = [rect for rect in new_free if rect.w > 0 and rect.h > 0]
        self._prune_free_rects(new_free)
        return new_free

    def _split_rect(self, rect: Rect, used: Rect) -> list[Rect]:
        if not rect.intersects(used):
            return [rect]
        pieces: list[Rect] = []
        if used.x > rect.x:
            pieces.append(Rect(rect.x, rect.y, used.x - rect.x, rect.h))
        if used.x + used.w < rect.x + rect.w:
            pieces.append(
                Rect(
                    used.x + used.w,
                    rect.y,
                    rect.x + rect.w - (used.x + used.w),
                    rect.h,
                )
            )
        if used.y > rect.y:
            pieces.append(Rect(rect.x, rect.y, rect.w, used.y - rect.y))
        if used.y + used.h < rect.y + rect.h:
            pieces.append(
                Rect(
                    rect.x,
                    used.y + used.h,
                    rect.w,
                    rect.y + rect.h - (used.y + used.h),
                )
            )
        return [p for p in pieces if p.w > 0 and p.h > 0]

    def _prune_free_rects(self, rects: list[Rect]) -> None:
        i = 0
        while i < len(rects):
            rect_i = rects[i]
            removed = False
            j = i + 1
            while j < len(rects):
                rect_j = rects[j]
                if rect_i.contains(rect_j):
                    rects.pop(j)
                    continue
                if rect_j.contains(rect_i):
                    rects.pop(i)
                    removed = True
                    break
                j += 1
            if not removed:
                i += 1
