"""Contact-only extreme points and maximal empty rectangles of the heightfield.

Coordinates are bin-relative. No grid rounding, clearance offsets or relaxed
feasibility: the caller evaluates these through the production preview path.
"""
from __future__ import annotations

from .maxrects2d import Rect


def empty_maximal_rectangles(length, width, obstacles):
    """Overlapping maximal free rectangles, unlike the disjoint 2D partition."""
    free = [Rect(0, 0, length, width)]
    for obstacle in obstacles:
        pieces = set()
        for r in free:
            if not r.intersects(obstacle):
                pieces.add(r)
                continue
            # Full strips intentionally overlap: preserve every maximal space.
            if r.x < obstacle.x:
                pieces.add(Rect(r.x, r.y, obstacle.x-r.x, r.h))
            if obstacle.x+obstacle.w < r.x+r.w:
                pieces.add(Rect(obstacle.x+obstacle.w, r.y,
                                r.x+r.w-obstacle.x-obstacle.w, r.h))
            if r.y < obstacle.y:
                pieces.add(Rect(r.x, r.y, r.w, obstacle.y-r.y))
            if obstacle.y+obstacle.h < r.y+r.h:
                pieces.add(Rect(r.x, obstacle.y+obstacle.h, r.w,
                                r.y+r.h-obstacle.y-obstacle.h))
        free = []
        for r in sorted(pieces, key=lambda r: (-r.area, r.x, r.y, r.w, r.h)):
            if not any(k.contains(r) for k in free):
                free.append(r)
    return free


class _GeometryCache:
    """Per-pallet memoization; immutable geometry/results, independent dicts."""
    def __init__(self, key, spaces=None, points=None):
        self.key = key
        self.spaces = spaces
        self.points = {} if points is None else points

    def __deepcopy__(self, memo):
        # Rects and nested coordinate tuples are immutable. Do not recursively
        # recopy them at every beam expansion, or share a mutable query map.
        return _GeometryCache(self.key, self.spaces, self.points.copy())


def extra_xy_candidates(pallet, length, width, *, exclude):
    spec = pallet.spec
    offset = spec.offset_mm
    bin_l, bin_w = spec.bin_length_mm, spec.bin_width_mm
    max_x, max_y = bin_l-length, bin_w-width
    boxes = tuple((p.x_mm-offset, p.y_mm-offset,
                   p.x_mm-offset+p.length_mm, p.y_mm-offset+p.width_mm,
                   p.z_mm, p.z_mm+p.height_mm) for p in pallet.placements)
    key = (bin_l, bin_w, spec.max_height_mm, boxes)
    cache = getattr(pallet, '_extra_geometry_cache', None)
    if cache is None or cache.key != key:
        cache = _GeometryCache(key)
        pallet._extra_geometry_cache = cache
    query = (length, width, pallet.extra_candidates)
    ordered = cache.points.get(query)
    if ordered is None:
        ordered = _generate_points(pallet.extra_candidates, length, width,
                                   bin_l, bin_w, spec.max_height_mm, boxes, cache)
        # Bound memoization independently of the evaluation cap. Eviction has
        # no effect on the points, their order or the production result.
        if len(cache.points) >= 32:
            cache.points.clear()
        cache.points[query] = ordered
    result = []
    for _, x, y in ordered:
        if (x, y) not in exclude:
            result.append((x, y))
            if len(result) >= pallet.extra_candidates_max:
                break
    return result if pallet.extra_candidates_max > 0 else []


def _generate_points(mode, length, width, bin_l, bin_w, max_height, boxes, cache):
    max_x, max_y = bin_l-length, bin_w-width
    points = set()
    heights = {}
    contacts = {}
    by_top = sorted(boxes, key=lambda b: -b[5])

    def height(x, y):
        xy = (x, y)
        if xy not in heights:
            # The first intersecting footprint in descending top order is
            # exactly the upper envelope used by the original generator.
            heights[xy] = next((top for a,b,c,d,_,top in by_top
                                if x < c and a < x+length and y < d and b < y+width), 0)
        return heights[xy]

    def add(x, y, level=None):
        if not (0 <= x <= max_x and 0 <= y <= max_y):
            return
        xy = (x, y)
        z = height(x, y)
        if level is not None and z != level:
            return
        if xy not in contacts:
            contact = x in (0, max_x) or y in (0, max_y)
            if not contact:
                for a,b,c,d,bottom,top in boxes:
                    overlap_x = x < c and a < x+length
                    overlap_y = y < d and b < y+width
                    edge_x = x+length == a or x == c
                    edge_y = y+width == b or y == d
                    lateral = bottom <= z < top and (
                        (edge_x and overlap_y) or (edge_y and overlap_x))
                    supported_edge = top == z and overlap_x and overlap_y and (
                        x in (a, c-length) or y in (b, d-width))
                    if lateral or supported_edge:
                        contact = True
                        break
            contacts[xy] = contact
        if contacts[xy]:
            points.add((z, x, y))

    if 'ep' in mode:
        for a,b,c,d,_,_ in boxes:
            seeds = [(x, y) for x in (a-length, c) for y in (b, d-width)]
            seeds += [(x, y) for x in (a, c-length) for y in (b-width, d)]
            for x, y in seeds:
                if not (0 <= x <= max_x and 0 <= y <= max_y):
                    continue
                add(x, y)
                z = height(x, y)
                px = max((c for a,b,c,d,_,top in boxes if top > z
                          and c <= x and b < y+width and y < d), default=0)
                py = max((d for a,b,c,d,_,top in boxes if top > z
                          and d <= y and a < x+length and x < c), default=0)
                add(px, y)
                add(x, py)

    if 'ems' in mode:
        if cache.spaces is None:
            spaces = []
            for z in sorted({0, *(b[5] for b in boxes)}):
                if z >= max_height:
                    continue
                spaces.extend((z, r) for r in empty_maximal_rectangles(
                    bin_l, bin_w, [Rect(a,b,c-a,d-b) for a,b,c,d,_,top in boxes if top > z]))
            cache.spaces = tuple(spaces)
        for z, r in cache.spaces:
            if r.w >= length and r.h >= width:
                for x in (r.x, r.x+r.w-length):
                    for y in (r.y, r.y+r.h-width):
                        add(x, y, z)
    return tuple(sorted(points))
