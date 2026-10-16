"""Contiguous online templates; all coordinates use the production envelope origin."""
from collections import Counter


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
