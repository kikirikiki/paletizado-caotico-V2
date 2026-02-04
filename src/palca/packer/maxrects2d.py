from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ---------------------------
# Data structures
# ---------------------------

@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
class MaxRectsCandidate:
    x: int
    y: int
    w: int
    h: int
    score: tuple[int, ...]


# ---------------------------
# MaxRects bin packer (2D)
# ---------------------------

class MaxRects2D:
    """
    Canonical-ish MaxRects free-rect handling:
      - free_rects are kept non-overlapping to keep total free area meaningful.
      - we aggressively prune (dedupe + remove contained) and cap list size
        to prevent pathological blow-ups and "stuck at 95% CPU" situations.

    Notes:
      - free_area() returns TRUE free area (= bin_area - used_area).
    """

    # Hard caps to avoid combinatorial explosions
    _MAX_FREE_RECTS: int = 2048
    _PRUNE_INPUT_CAP: int = 4096  # cap before O(n^2) containment pruning

    def __init__(self, width: int, height: int, heuristic: str = "baf") -> None:
        if width <= 0 or height <= 0:
            raise ValueError("MaxRects2D dimensions must be positive")
        self.width = int(width)
        self.height = int(height)
        self.heuristic = str(heuristic).lower().strip() or "baf"
        self.free_rects: list[Rect] = [Rect(0, 0, self.width, self.height)]
        self.used_area: int = 0

    def copy(self) -> "MaxRects2D":
        clone = MaxRects2D(self.width, self.height, self.heuristic)
        clone.free_rects = list(self.free_rects)
        clone.used_area = int(self.used_area)
        return clone

    @property
    def area(self) -> int:
        return int(self.width) * int(self.height)

    def free_area(self) -> int:
        # true free area
        return max(0, self.area - int(self.used_area))

    # ---------------------------
    # Scoring / candidate search
    # ---------------------------

    def _score_rect(self, rect: Rect, w: int, h: int) -> tuple[int, ...]:
        """Score placing (w,h) inside a free rect; lower is better."""
        dw = rect.w - w
        dh = rect.h - h
        area_left = rect.w * rect.h - w * h

        placed = Rect(rect.x, rect.y, w, h)
        splits = self._split_free_rect(rect, placed)
        best_free_area = 0
        best_free_side = 0
        for piece in splits:
            area = piece.area
            if area > best_free_area:
                best_free_area = area
            side = piece.w if piece.w > piece.h else piece.h
            if side > best_free_side:
                best_free_side = side

        tie_break = (-best_free_area, -best_free_side, rect.x, rect.y, w, h)

        heur = self.heuristic
        if heur in ("baf", "best_area_fit", "area"):
            return (area_left, min(dw, dh), max(dw, dh), *tie_break)
        if heur in ("bssf", "best_short_side_fit", "short"):
            return (min(dw, dh), max(dw, dh), area_left, *tie_break)
        if heur in ("blsf", "best_long_side_fit", "long"):
            return (max(dw, dh), min(dw, dh), area_left, *tie_break)

        # default = BAF
        return (area_left, min(dw, dh), max(dw, dh), *tie_break)

    def find_candidates(self, w: int, h: int, *, k: int = 25) -> list[MaxRectsCandidate]:
        w = int(w)
        h = int(h)
        k = int(k)
        if w <= 0 or h <= 0 or k <= 0:
            return []

        candidates: list[MaxRectsCandidate] = []
        seen: set[tuple[int, int, int, int]] = set()

        # localize for speed
        score_fn = self._score_rect
        for r in self.free_rects:
            if w <= r.w and h <= r.h:
                sc = score_fn(r, w, h)
                cand = MaxRectsCandidate(r.x, r.y, w, h, sc)
                key = (int(cand.x), int(cand.y), int(cand.w), int(cand.h))
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(cand)

        if not candidates:
            return []

        candidates.sort(key=lambda c: c.score)
        return candidates[:k]

    def find_candidate(self, w: int, h: int) -> Optional[MaxRectsCandidate]:
        candidates = self.find_candidates(w, h, k=1)
        if not candidates:
            return None
        return candidates[0]

    # ---------------------------
    # Free-rect maintenance
    # ---------------------------

    @staticmethod
    def _split_free_rect(free: Rect, placed: Rect) -> list[Rect]:
        """
        Split a free rect against a placed rect into non-overlapping pieces.
        Returns up to 4 rectangles that partition the remaining area.
        """
        if not free.intersects(placed):
            return [free]

        fx0, fy0 = free.x, free.y
        fx1, fy1 = free.x + free.w, free.y + free.h

        px0, py0 = placed.x, placed.y
        px1, py1 = placed.x + placed.w, placed.y + placed.h

        ix0 = max(fx0, px0)
        iy0 = max(fy0, py0)
        ix1 = min(fx1, px1)
        iy1 = min(fy1, py1)

        # No overlap after intersection check
        if ix0 >= ix1 or iy0 >= iy1:
            return [free]

        out: list[Rect] = []

        # Bottom strip
        if iy0 > fy0:
            out.append(Rect(fx0, fy0, free.w, iy0 - fy0))
        # Top strip
        if iy1 < fy1:
            out.append(Rect(fx0, iy1, free.w, fy1 - iy1))
        # Left middle strip
        if ix0 > fx0:
            out.append(Rect(fx0, iy0, ix0 - fx0, iy1 - iy0))
        # Right middle strip
        if ix1 < fx1:
            out.append(Rect(ix1, iy0, fx1 - ix1, iy1 - iy0))

        return [r for r in out if r.w > 0 and r.h > 0]

    def _prune_free_rects(self, rects: list[Rect]) -> list[Rect]:
        """
        Prune:
          1) remove non-positive
          2) dedupe
          3) remove contained rectangles
          4) hard cap by area (keeps biggest rectangles)
        """
        # 1) non-positive
        rects = [r for r in rects if r.w > 0 and r.h > 0]

        if not rects:
            return []

        # 2) dedupe
        seen: set[tuple[int, int, int, int]] = set()
        uniq: list[Rect] = []
        for r in rects:
            key = (r.x, r.y, r.w, r.h)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(r)
        rects = uniq

        if not rects:
            return []

        # Cap input to keep containment pruning sane
        if len(rects) > self._PRUNE_INPUT_CAP:
            rects.sort(key=lambda r: r.area, reverse=True)
            rects = rects[: self._PRUNE_INPUT_CAP]

        # 3) remove contained
        # Sort by area desc; keep list only with "maximal" rects
        rects.sort(key=lambda r: (r.area, r.w, r.h), reverse=True)
        kept: list[Rect] = []
        for r in rects:
            contained = False
            for k in kept:
                if k.contains(r):
                    contained = True
                    break
            if not contained:
                kept.append(r)

        # 4) hard cap (avoid blow-ups)
        if len(kept) > self._MAX_FREE_RECTS:
            kept.sort(key=lambda r: r.area, reverse=True)
            kept = kept[: self._MAX_FREE_RECTS]

        return kept

    # ---------------------------
    # Public place/simulate
    # ---------------------------

    def simulate_place(self, cand: Optional[MaxRectsCandidate]) -> Optional[list[Rect]]:
        """
        Return the free_rects list after committing `cand` WITHOUT mutating state.
        """
        if cand is None:
            return None

        pw, ph = int(cand.w), int(cand.h)
        if pw <= 0 or ph <= 0:
            return None

        placed = Rect(int(cand.x), int(cand.y), pw, ph)

        # must be inside the bin
        if placed.x < 0 or placed.y < 0 or placed.x + placed.w > self.width or placed.y + placed.h > self.height:
            return None

        new_rects: list[Rect] = []
        touched = False
        split_fn = self._split_free_rect
        for r in self.free_rects:
            if not r.intersects(placed):
                new_rects.append(r)
            else:
                touched = True
                new_rects.extend(split_fn(r, placed))

        if not touched:
            return None

        return self._prune_free_rects(new_rects)

    def place(self, cand: Optional[MaxRectsCandidate]) -> bool:
        """
        Commit a candidate into the bin.
        Updates free_rects and used_area.
        """
        if cand is None:
            return False

        free_after = self.simulate_place(cand)
        if free_after is None:
            return False

        self.free_rects = list(free_after)
        self.used_area += int(cand.w) * int(cand.h)
        return True
