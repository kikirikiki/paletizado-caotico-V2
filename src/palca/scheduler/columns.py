"""Contiguous online templates; all coordinates use the production envelope origin."""
from collections import Counter
from itertools import islice


# Geometric search budgets, independent of the evaluated stream. No wall clock
# cutoff: a slow machine must choose exactly the same partition.
ADAPTIVE_MAX_CANDIDATES = 256
_AXIS_PLANS = 64
_AXIS_SEGMENTS = 16


def _axis_partitions(total, dimensions, minimum):
    """Bounded guillotine cuts: repeated dimensions and two-run mixtures.

    Each dimension already includes slack. A small tail is shared in integer
    millimetres; a usable tail remains a separate, marked residual segment.
    """
    dimensions = tuple(dict.fromkeys(d for d in dimensions if 0 < d <= total))
    plans = []
    seen = set()

    def add(sizes):
        if not sizes or len(sizes) > _AXIS_SEGMENTS:
            return
        tail = total - sum(sizes)
        parts = [(d, False) for d in sizes]
        if tail >= minimum:
            parts.append((tail, True))
        elif tail:
            share, extra = divmod(tail, len(parts))
            parts = [(d + share + (i < extra), False) for i, (d, _) in enumerate(parts)]
        parts = tuple(parts)
        if parts not in seen:
            seen.add(parts)
            plans.append(parts)

    for d in dimensions:
        add([d] * min(total // d, _AXIS_SEGMENTS + 1))
    # Keep every homogeneous plan before enumerating mixtures, so the search
    # budget does not privilege a small prefix of the visible dimensions.
    for count in range(1, _AXIS_SEGMENTS):
        for first in dimensions:
            for second in dimensions:
                if len(plans) >= _AXIS_PLANS:
                    return tuple(plans)
                remaining = total - first * count
                if first != second and remaining >= second:
                    n = min(remaining // second, _AXIS_SEGMENTS + 1)
                    add([first] * count + [second] * n)
    return tuple(plans) or (((total, True),),)


def _assignment_score(columns, residuals, footprints):
    """Greedy descending footprint area; at most one box per column.

    Equal areas retain box/orientation/column enumeration order. Residuals are
    penalized only when no visible permitted footprint fits (even if its box
    was already assigned elsewhere).
    """
    used_boxes, used_columns = set(), set()
    covered = 0
    fillable = set()
    for area, box_index, length, width in footprints:
        for index, (_, _, cl, cw) in enumerate(columns):
            if length <= cl and width <= cw:
                fillable.add(index)
                if box_index not in used_boxes and index not in used_columns:
                    covered += area
                    used_boxes.add(box_index)
                    used_columns.add(index)
    penalty = sum(columns[i][2] * columns[i][3] for i in residuals if i not in fillable)
    return covered - penalty, covered, penalty


def adaptive_partition(pallet, boxes, slack_mm=10):
    """Choose a two-level floor partition from at most 15 visible boxes.

    Enumerate both strip axes, homogeneous/two-run strip plans, and four
    deterministic combinations of inner plans, capped at 256 floor plans.
    Inner plans are ranked by the same visible assignment score within a strip.
    All geometry comes from _orientations, the envelope, or the explicit slack.
    """
    if type(slack_mm) is not int or slack_mm < 0:
        raise ValueError("column_slack_mm must be a nonnegative int")
    spec = pallet.spec
    if (spec.bin_length_mm, spec.bin_width_mm) != (1240, 840):
        raise ValueError("Column templates require a 1240 x 840 mm envelope")
    visible = tuple(islice(boxes, 15))
    footprints = []
    for index, box in enumerate(visible):
        for orientation in pallet._orientations(box.length_mm, box.width_mm, box.height_mm):
            l, w = int(orientation.length_mm), int(orientation.width_mm)
            footprints.append((l * w, index, l, w))
    footprints.sort(key=lambda f: -f[0])
    minimum = min((min(l, w) for _, _, l, w in footprints), default=max(
        spec.bin_length_mm, spec.bin_width_mm) + 1)
    dimensions = tuple(dict.fromkeys(d + slack_mm for _, _, l, w in footprints for d in (l, w)))
    best_score = None
    best = None
    evaluated = 0
    seen = set()
    inner_cache = {}
    axis_cache = {}

    def axis_plans(total, sizes):
        # Many strip widths admit precisely the same visible footprints. Their
        # raw cuts are identical; only their local scores depend on strip width.
        # This cache lives for this opening only, never across physical pallets.
        key = total, tuple(dict.fromkeys(sizes))
        if key not in axis_cache:
            axis_cache[key] = _axis_partitions(total, key[1], minimum)
        return axis_cache[key]

    # Outer plans are interleaved by axis; ties always keep the first plan.
    outer = [(axis, plan) for axis, total in ((1, spec.bin_width_mm), (0, spec.bin_length_mm))
             for plan in axis_plans(total, dimensions)]
    by_axis = [tuple(p for p in outer if p[0] == axis) for axis in (1, 0)]
    outer = [plans[i] for i in range(max(map(len, by_axis))) for plans in by_axis if i < len(plans)]
    for variant in range(4):
        for axis, strips in outer:
            columns, residuals = [], []
            strip_start = spec.offset_mm
            for strip_index, (width, strip_residual) in enumerate(strips):
                key = axis, width
                if key not in inner_cache:
                    # Perpendicular size must fit this strip. The ordering is
                    # derived only from visible footprint areas, never a family.
                    cuts = [((l if axis == 1 else w) + slack_mm)
                            for _, _, l, w in footprints if (w if axis == 1 else l) <= width]
                    total = spec.bin_length_mm if axis == 1 else spec.bin_width_mm
                    plans = axis_plans(total, cuts)
                    # Sample across the entire enumeration (including mixtures),
                    # rather than scoring only a prefix of homogeneous plans.
                    count = min(16, len(plans))
                    plans = tuple(plans[i * (len(plans)-1) // max(1, count-1)] for i in range(count))
                    def inner_score(plan):
                        rects = [(0, 0, size, width) if axis == 1 else (0, 0, width, size)
                                 for size, _ in plan]
                        residual = [i for i, (_, flag) in enumerate(plan) if flag]
                        return _assignment_score(rects, residual, footprints)[0]
                    inner_cache[key] = tuple(sorted(plans, key=inner_score, reverse=True))
                plans = inner_cache[key]
                inner = plans[(variant // 2 + strip_index * (variant % 2)) % len(plans)]
                cut_start = spec.offset_mm
                for size, cut_residual in inner:
                    rect = ((cut_start, strip_start, size, width) if axis == 1 else
                            (strip_start, cut_start, width, size))
                    if strip_residual or cut_residual:
                        residuals.append(len(columns))
                    columns.append(rect)
                    cut_start += size
                strip_start += width
            signature = tuple(columns), tuple(residuals)
            if signature in seen:
                continue
            seen.add(signature)
            score, covered, penalty = _assignment_score(columns, residuals, footprints)
            evaluated += 1
            if best_score is None or score > best_score:
                best_score = score
                best = dict(rectangles=tuple(columns), residual_indices=tuple(residuals),
                            strip_axis="y" if axis == 1 else "x",
                            strips_mm=tuple(w for w, _ in strips),
                            covered_area_mm2=covered, residual_penalty_mm2=penalty,
                            score_mm2=score, visible_count=len(visible))
            if evaluated >= ADAPTIVE_MAX_CANDIDATES:
                break
        if evaluated >= ADAPTIVE_MAX_CANDIDATES:
            break
    best["candidates_evaluated"] = evaluated
    return best


def dominant_width(boxes):
    counts = Counter(int(b.width_mm) for b in boxes
                     if 400 <= b.width_mm <= 500 and 550 <= b.length_mm <= 620)
    return min(counts, key=lambda w: (-counts[w], w)) if counts else 445


def column_rectangles(spec, width):
    length, breadth, offset = spec.bin_length_mm, spec.bin_width_mm, spec.offset_mm
    if length != 1240 or breadth != 840:
        raise ValueError("Column templates require a 1240 x 840 mm envelope")
    if not 0 < width < breadth:
        raise ValueError("Invalid column width")
    return ((offset, offset, 620, width), (offset + 620, offset, 620, width),
            (offset, offset + width, 445, breadth - width),
            (offset + 445, offset + width, 445, breadth - width),
            (offset + 890, offset + width, 350, breadth - width))
