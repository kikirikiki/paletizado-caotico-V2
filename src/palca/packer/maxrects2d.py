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

    def _score_rect(self, rect, w: int, h: int):

        """Score placing (w,h) inside a free rect; lower is better."""

        dw = rect.w - w

        dh = rect.h - h

        area_left = rect.w * rect.h - w * h

        heur = str(getattr(self, 'heuristic', 'baf')).lower()

        if heur in ('baf', 'best_area_fit', 'area'):

            return (area_left, min(dw, dh), max(dw, dh))

        if heur in ('bssf', 'best_short_side_fit', 'short'):

            return (min(dw, dh), max(dw, dh), area_left)

        if heur in ('blsf', 'best_long_side_fit', 'long'):

            return (max(dw, dh), min(dw, dh), area_left)

        return (area_left, min(dw, dh), max(dw, dh))


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

    def simulate_place(self, cand):

        """Return the free_rects list after committing `cand` WITHOUT mutating state.

    

        This implementation keeps free rectangles DISJOINT so that

        sum(w*h) represents true free area (as expected by tests).

        """

        if cand is None:

            return None

    

        px, py, pw, ph = int(cand.x), int(cand.y), int(cand.w), int(cand.h)

        if pw <= 0 or ph <= 0:

            return None

        if not self.free_rects:

            return None

    

        RectCls = self.free_rects[0].__class__

    

        target_idx = None

        target = None

        for i, r in enumerate(self.free_rects):

            if (px >= r.x and py >= r.y and (px + pw) <= (r.x + r.w) and (py + ph) <= (r.y + r.h)):

                target_idx = i

                target = r

                break

        if target is None:

            return None

    

        out = [r for i, r in enumerate(self.free_rects) if i != target_idx]

    

        # Disjoint split of `target` around placed rect (left/right full height + top/bottom in the center column).

        # left strip

        if px > target.x:

            out.append(RectCls(target.x, target.y, px - target.x, target.h))

        # right strip

        rx = px + pw

        if rx < target.x + target.w:

            out.append(RectCls(rx, target.y, (target.x + target.w) - rx, target.h))

        # bottom (center column)

        if py > target.y:

            out.append(RectCls(px, target.y, pw, py - target.y))

        # top (center column)

        ty = py + ph

        if ty < target.y + target.h:

            out.append(RectCls(px, ty, pw, (target.y + target.h) - ty))

    

        # remove any zero/negative rectangles

        out = [r for r in out if getattr(r, 'w', 0) > 0 and getattr(r, 'h', 0) > 0]

        return out


    def place(self, cand) -> bool:
        """Commit a candidate into the bin.

        Must update `free_rects`, otherwise free area never decreases.
        We reuse `simulate_place()` and then commit the result.
        """
        if cand is None:
            return False
        free_after = self.simulate_place(cand)
        if free_after is None:
            return False
        self.free_rects = list(free_after)
        return True

