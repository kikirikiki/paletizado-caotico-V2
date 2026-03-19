from __future__ import annotations

import pytest

from palca.domain.box import Box
from palca.domain.placement import Placement
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel
from palca.packer.zones import ZoneConfig, ZonePointControl, PALLET_ZONES
from palca.scheduler.zone_scheduler import ZoneScheduler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ZONE_A = PALLET_ZONES[0]  # x=-20..585, y=-20..430  (605×450)
ZONE_B = PALLET_ZONES[1]  # x=585..1215, y=-20..335 (630×355)
ZONE_C = PALLET_ZONES[2]  # x=-20..615, y=430..780  (635×350)
ZONE_D = PALLET_ZONES[3]  # x=615..1220, y=335..785 (605×450)


def _make_box(box_id: int = 1, length_mm: int = 445, width_mm: int = 605, height_mm: int = 355) -> Box:
    return Box(box_id=box_id, length_mm=length_mm, width_mm=width_mm, height_mm=height_mm, timestamp=float(box_id))


def _make_placement(
    x_mm: int = 100,
    y_mm: int = 100,
    z_mm: int = 0,
    length_mm: int = 200,
    width_mm: int = 200,
    height_mm: int = 300,
    layer_id: int = 0,
) -> Placement:
    return Placement(
        x_mm=x_mm,
        y_mm=y_mm,
        z_mm=z_mm,
        rot90=False,
        layer_id=layer_id,
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=height_mm,
    )


# ---------------------------------------------------------------------------
# ZoneConfig.fits_box
# ---------------------------------------------------------------------------


def test_zone_fits_box_nominal() -> None:
    """445×605 fits in zone A (605×450) via rotation."""
    assert ZONE_A.fits_box(445, 605)


def test_zone_fits_box_rotation() -> None:
    """A 500×300 box fits in zone A only when rotated (300 width)."""
    # Zone A: usable width=605, depth=450
    # Original (500×300): 500<=605 ✓ and 300<=450 ✓ — fits already; use zone C for strict rotation test
    # Zone C: usable width=635, depth=350
    zone_c = ZONE_C  # x=-20..615, y=430..780 → width=635, depth=350
    # Box 400×400: neither orientation exclusively requires rotation; use 400×300
    # 400×300: 400<=635 ✓ and 300<=350 ✓ → fits in both, not ideal
    # Use 640×300 — original fails (640>635), rotated (300<=635 ✓ and 640>350 ✗) — neither fits
    # Use 300×360: original (300<=635 ✓ and 360>350 ✗), rotated (360<=635 ✓ and 300<=350 ✓)
    assert not zone_c.fits_box(300, 360) or zone_c.fits_box(360, 300)  # at least one orientation fits
    # Specifically: fits_box must return True because rotation saves it
    assert zone_c.fits_box(300, 360)


def test_zone_fits_box_too_large() -> None:
    """Box larger than zone in both orientations returns False."""
    # Zone A: 605×450. Box 700×700 → neither orientation fits.
    assert not ZONE_A.fits_box(700, 700)


# ---------------------------------------------------------------------------
# ZoneConfig.contains_placement
# ---------------------------------------------------------------------------


def test_contains_placement_inside() -> None:
    """Placement fully inside zone A returns True."""
    # Zone A: x=-20..585, y=-20..430
    assert ZONE_A.contains_placement(x_mm=0, y_mm=0, length_mm=300, width_mm=200)


def test_contains_placement_outside() -> None:
    """Placement that extends beyond zone boundary returns False."""
    # Zone A x_max=585; a 300mm box at x=400 ends at 700 > 585
    assert not ZONE_A.contains_placement(x_mm=400, y_mm=0, length_mm=300, width_mm=200)


# ---------------------------------------------------------------------------
# ZoneScheduler.compute_zone_heights
# ---------------------------------------------------------------------------


def test_compute_zone_heights_empty() -> None:
    """Empty pallet returns zero for all zones."""
    scheduler = ZoneScheduler(zones=PALLET_ZONES)
    pallet = PalletModel()
    heights = scheduler.compute_zone_heights(pallet)
    assert heights == {"A": 0, "B": 0, "C": 0, "D": 0}


def test_compute_zone_heights_with_placement() -> None:
    """A placement in zone A updates only zone A's height."""
    scheduler = ZoneScheduler(zones=PALLET_ZONES)
    pallet = PalletModel()
    # Placement at (100, 100) — inside zone A (x=-20..585, y=-20..430)
    p = _make_placement(x_mm=100, y_mm=100, z_mm=0, length_mm=200, width_mm=200, height_mm=300)
    pallet.placements.append(p)
    heights = scheduler.compute_zone_heights(pallet)
    assert heights["A"] == 300
    assert heights["B"] == 0
    assert heights["C"] == 0
    assert heights["D"] == 0


# ---------------------------------------------------------------------------
# ZoneScheduler.select_target_zones
# ---------------------------------------------------------------------------


def test_select_target_zones_all_zero() -> None:
    """All zones at height 0 — all four are eligible."""
    scheduler = ZoneScheduler(zones=PALLET_ZONES, delta_max_mm=400)
    heights = {z.name: 0 for z in PALLET_ZONES}
    eligible = scheduler.select_target_zones(heights)
    assert len(eligible) == 4
    assert [z.name for z in eligible] == ["A", "B", "C", "D"]


def test_select_target_zones_delta() -> None:
    """Zone whose height exceeds min + delta is excluded."""
    scheduler = ZoneScheduler(zones=PALLET_ZONES, delta_max_mm=400)
    heights = {"A": 0, "B": 0, "C": 0, "D": 500}
    eligible = scheduler.select_target_zones(heights)
    names = [z.name for z in eligible]
    assert "D" not in names
    assert set(names) == {"A", "B", "C"}


# ---------------------------------------------------------------------------
# Integration: step() balances zone heights
# ---------------------------------------------------------------------------

# We use a larger pallet and custom zones aligned to where MaxRects naturally
# places 605×445 boxes (rotated from 445×605 input).
#
# Box dims after rotation: length=605, width=445.
# Two boxes side-by-side in x: positions 0 and 605 → pallet length >= 1210.
# Two boxes stacked in y: positions 0 and 445 → pallet width >= 890.
#
# Zones are split at x=600 and y=440, each with a -20 lower guard:
#   TL: x=-20..610, y=-20..450  → accepts box at (0, 0) in bin coords
#   TR: x=600..1220, y=-20..450 → accepts box at (605, 0)
#   BL: x=-20..610, y=440..900  → accepts box at (0, 445)
#   BR: x=600..1220, y=440..900 → accepts box at (605, 445)
_INTEGRATION_SPEC = PalletSpec(
    length_mm=1220,
    width_mm=900,
    max_height_mm=3000,
    overhang_mm=0,
    allow_rotate=True,
)

_TEST_ZONES = [
    ZoneConfig(name="TL", x_min_mm=-20, x_max_mm=610, y_min_mm=-20, y_max_mm=450),
    ZoneConfig(name="TR", x_min_mm=600, x_max_mm=1220, y_min_mm=-20, y_max_mm=450),
    ZoneConfig(name="BL", x_min_mm=-20, x_max_mm=610, y_min_mm=440, y_max_mm=900),
    ZoneConfig(name="BR", x_min_mm=600, x_max_mm=1220, y_min_mm=440, y_max_mm=900),
]


def test_step_places_in_lowest_zone() -> None:
    """
    Place 6 boxes (445×605×355mm) via ZoneScheduler and verify that zone
    heights never diverge by more than delta_max_mm=400mm.
    """
    scheduler = ZoneScheduler(zones=_TEST_ZONES, delta_max_mm=400, buffer_size=15)
    pallet = PalletModel(spec=_INTEGRATION_SPEC)

    buffer = [_make_box(box_id=i) for i in range(1, 7)]

    placed = 0
    while buffer:
        result = scheduler.step(pallet, buffer)
        if result is None:
            break
        box, preview = result
        assert preview.feasible
        assert preview.placement is not None
        pallet.commit_place(preview)
        buffer.remove(box)
        placed += 1

        heights = scheduler.compute_zone_heights(pallet)
        max_h = max(heights.values())
        min_h = min(heights.values())
        assert max_h - min_h <= 400, (
            f"Zone height imbalance after {placed} placements: {heights}"
        )

    assert placed == 6, f"Expected 6 placements, got {placed}"
