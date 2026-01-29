from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Box:
    width_mm: int
    depth_mm: int
    height_mm: int
    box_id: str | None = None


@dataclass(frozen=True)
class Placement:
    x_mm: int
    y_mm: int
    z_mm: int
    rot_deg: int
    width_mm: int
    depth_mm: int
    height_mm: int
    layer_index: int
    box_id: str | None = None


@dataclass
class PackResult:
    placements: list[Placement]
    rejected: list[Box]
    all_fit: bool
    height_used_mm: int


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

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
    score: tuple[int, int, int, int, int]


class MaxRectsBin:
    def __init__(self, width: int, height: int, heuristic: str = "baf") -> None:
        if width <= 0 or height <= 0:
            raise ValueError("MaxRectsBin dimensions must be positive")
        self.width = int(width)
        self.height = int(height)
        self.heuristic = heuristic.lower()
        self.free_rects: list[Rect] = [Rect(0, 0, self.width, self.height)]

    def find_candidate(self, w: int, h: int) -> MaxRectsCandidate | None:
        best: MaxRectsCandidate | None = None
        for rect in self.free_rects:
            if w <= rect.w and h <= rect.h:
                score = self._score_rect(rect, w, h)
                cand = MaxRectsCandidate(rect.x, rect.y, w, h, score)
                if best is None or cand.score < best.score:
                    best = cand
        return best

    def place(self, cand: MaxRectsCandidate) -> None:
        used = Rect(cand.x, cand.y, cand.w, cand.h)
        new_free: list[Rect] = []
        for rect in self.free_rects:
            if not rect.intersects(used):
                new_free.append(rect)
                continue
            if used.x > rect.x:
                new_free.append(Rect(rect.x, rect.y, used.x - rect.x, rect.h))
            if used.x + used.w < rect.x + rect.w:
                new_free.append(
                    Rect(used.x + used.w, rect.y, rect.x + rect.w - (used.x + used.w), rect.h)
                )
            if used.y > rect.y:
                new_free.append(Rect(rect.x, rect.y, rect.w, used.y - rect.y))
            if used.y + used.h < rect.y + rect.h:
                new_free.append(
                    Rect(rect.x, used.y + used.h, rect.w, rect.y + rect.h - (used.y + used.h))
                )
        self.free_rects = [r for r in new_free if r.w > 0 and r.h > 0]
        self._prune_free_rects()

    def _score_rect(self, rect: Rect, w: int, h: int) -> tuple[int, int, int, int, int]:
        dw = rect.w - w
        dh = rect.h - h
        short_side = min(dw, dh)
        long_side = max(dw, dh)
        area_fit = rect.w * rect.h - w * h
        # Add stable tie-breaks (y, x) to reduce jitter.
        if self.heuristic in ("bssf", "ssf", "short_side"):
            return (short_side, long_side, area_fit, rect.y, rect.x)
        if self.heuristic in ("baf", "area", "best_area"):
            return (area_fit, short_side, long_side, rect.y, rect.x)
        raise ValueError(f"Unknown MaxRects heuristic: {self.heuristic}")

    def _prune_free_rects(self) -> None:
        i = 0
        while i < len(self.free_rects):
            rect_i = self.free_rects[i]
            removed = False
            j = i + 1
            while j < len(self.free_rects):
                rect_j = self.free_rects[j]
                if rect_i.contains(rect_j):
                    self.free_rects.pop(j)
                    continue
                if rect_j.contains(rect_i):
                    self.free_rects.pop(i)
                    removed = True
                    break
                j += 1
            if not removed:
                i += 1


@dataclass
class LayerState:
    index: int
    z_mm: int
    bin: MaxRectsBin
    height_mm: int = 0


class LayerPacker:
    def __init__(
        self,
        pallet_w_mm: int = 1200,
        pallet_d_mm: int = 800,
        max_height_mm: int = 2400,
        overhang_mm: int = 0,
        heuristic: str = "baf",
        allow_rotate: bool = True,
    ) -> None:
        self.pallet_w_mm = int(pallet_w_mm)
        self.pallet_d_mm = int(pallet_d_mm)
        self.max_height_mm = int(max_height_mm)
        self.overhang_mm = max(0, int(overhang_mm))
        self.heuristic = heuristic
        self.allow_rotate = bool(allow_rotate)
        self._offset_mm = self.overhang_mm
        self._bin_w = self.pallet_w_mm + 2 * self.overhang_mm
        self._bin_d = self.pallet_d_mm + 2 * self.overhang_mm

    def pack(self, boxes: Sequence[Box]) -> PackResult:
        placements: list[Placement] = []
        if not boxes:
            return PackResult(placements, [], True, 0)

        z_base = 0
        layer_index = 0
        layer = self._new_layer(layer_index, z_base)

        for idx, box in enumerate(boxes):
            placement = self._place_in_layer(layer, box)
            if placement is None:
                if layer.height_mm == 0:
                    rejected = list(boxes[idx:])
                    return PackResult(placements, rejected, False, z_base)

                z_base += layer.height_mm
                if z_base >= self.max_height_mm:
                    rejected = list(boxes[idx:])
                    return PackResult(placements, rejected, False, z_base)

                layer_index += 1
                layer = self._new_layer(layer_index, z_base)
                placement = self._place_in_layer(layer, box)
                if placement is None:
                    rejected = list(boxes[idx:])
                    return PackResult(placements, rejected, False, z_base)

            placements.append(placement)

        height_used = z_base + layer.height_mm if placements else 0
        return PackResult(placements, [], True, height_used)

    def _new_layer(self, index: int, z_mm: int) -> LayerState:
        bin_2d = MaxRectsBin(self._bin_w, self._bin_d, self.heuristic)
        return LayerState(index=index, z_mm=z_mm, bin=bin_2d)

    def _place_in_layer(self, layer: LayerState, box: Box) -> Placement | None:
        next_height = max(layer.height_mm, box.height_mm)
        if layer.z_mm + next_height > self.max_height_mm:
            return None

        candidates: list[tuple[MaxRectsCandidate, int, int, int]] = []
        cand = layer.bin.find_candidate(box.width_mm, box.depth_mm)
        if cand is not None:
            candidates.append((cand, 0, box.width_mm, box.depth_mm))

        if self.allow_rotate and box.width_mm != box.depth_mm:
            cand_rot = layer.bin.find_candidate(box.depth_mm, box.width_mm)
            if cand_rot is not None:
                candidates.append((cand_rot, 90, box.depth_mm, box.width_mm))

        if not candidates:
            return None

        best = min(candidates, key=lambda c: (c[0].score, c[1]))
        cand_best, rot_deg, fw, fd = best
        layer.bin.place(cand_best)
        layer.height_mm = max(layer.height_mm, box.height_mm)

        x_mm = cand_best.x - self._offset_mm
        y_mm = cand_best.y - self._offset_mm
        return Placement(
            x_mm=x_mm,
            y_mm=y_mm,
            z_mm=layer.z_mm,
            rot_deg=rot_deg,
            width_mm=fw,
            depth_mm=fd,
            height_mm=box.height_mm,
            layer_index=layer.index,
            box_id=box.box_id,
        )


def pack_boxes(
    boxes: Iterable[Box],
    pallet_w_mm: int = 1200,
    pallet_d_mm: int = 800,
    max_height_mm: int = 2400,
    overhang_mm: int = 0,
    heuristic: str = "baf",
    allow_rotate: bool = True,
) -> PackResult:
    packer = LayerPacker(
        pallet_w_mm=pallet_w_mm,
        pallet_d_mm=pallet_d_mm,
        max_height_mm=max_height_mm,
        overhang_mm=overhang_mm,
        heuristic=heuristic,
        allow_rotate=allow_rotate,
    )
    return packer.pack(list(boxes))
