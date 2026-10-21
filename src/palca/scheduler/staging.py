"""D-036: visible-only reservation rule; no stream catalogue or search."""
from math import isfinite
from statistics import median
from typing import Mapping, Sequence

from ..domain.box import Box
from ..packer.pallet_model import orientation_dims_for_mode

STAGING_RAMP_ID = -1  # Virtual visible queue, never a physical conveyor.


def validate_staging(slots: int, policy: str, release: str, narrow_frac: float,
                     park_s: float = 6.0, unpark_s: float = 10.0) -> None:
    if type(slots) is not int or not 0 <= slots <= 3:
        raise ValueError("pallet_staging_slots must be an int in [0, 3]")
    if policy != "reserve_narrow" or release not in ("free", "hole"):
        raise ValueError("Invalid staging_policy or staging_release")
    if not isfinite(narrow_frac) or not 0 < narrow_frac <= 1:
        raise ValueError("staging_narrow_frac must be in (0, 1]")
    if any(not isfinite(t) or t < 0 for t in (park_s, unpark_s)):
        raise ValueError("Staging times must be finite and nonnegative")


def narrow_candidates(ramps: Mapping[int, Sequence[Box]], visible: Sequence[Box], *,
                      orientation_mode: str, fraction: float,
                      excluded_id: int | str | None = None) -> list[tuple[int, int, Box]]:
    """Return (ramp, index, box), narrowest first, using only visible geometry."""
    def side(box: Box) -> int:
        return min(min(l, w) for l, w, _ in orientation_dims_for_mode(
            box.length_mm, box.width_mm, box.height_mm,
            mode=orientation_mode, allow_rotate=True))

    sides = {b.box_id: side(b) for b in visible}
    if not sides:
        return []
    threshold = fraction * median(sides.values())
    candidates = [(rid, idx, box) for rid, queue in ramps.items()
                  for idx, box in enumerate(queue)
                  if box.box_id != excluded_id and sides[box.box_id] <= threshold]
    return sorted(candidates, key=lambda item: (
        sides[item[2].box_id], item[2].volume_mm3, item[1], item[0]))
