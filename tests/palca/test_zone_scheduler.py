from __future__ import annotations

import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.controls import (
    AccessibilityConfig,
    ControlConfig,
    StabilityConfig,
)
from palca.packer.pallet_model import PalletModel
from palca.packer.zones import ZoneConfig, PALLET_ZONES
from palca.scheduler.zone_scheduler import ZoneScheduler, _height_under_footprint


# ─── Fixtures ────────────────────────────────────────────────────────────────

ZONE_A = ZoneConfig("A", x_min_mm=-20, x_max_mm=585, y_min_mm=-20, y_max_mm=430)
ZONE_B = ZoneConfig("B", x_min_mm=585, x_max_mm=1215, y_min_mm=-20, y_max_mm=335)
ZONE_C = ZoneConfig("C", x_min_mm=-20, x_max_mm=615, y_min_mm=430, y_max_mm=780)
ZONE_D = ZoneConfig("D", x_min_mm=615, x_max_mm=1220, y_min_mm=335, y_max_mm=785)


def _make_pallet() -> PalletModel:
    return PalletModel(
        spec=PalletSpec(overhang_mm=20),
        heuristic="bssf",
        stacking_mode="heightfield",
        control_config=ControlConfig(
            stability=StabilityConfig(mode="ratio+corners", min_support_ratio=0.75),
            accessibility=AccessibilityConfig(accessibility_delta_mm=400),
        ),
    )


def _box(box_id: int, l: int, w: int, h: int) -> Box:
    return Box(box_id=box_id, length_mm=l, width_mm=w, height_mm=h, timestamp=0.0)


def _scheduler() -> ZoneScheduler:
    return ZoneScheduler(zones=list(PALLET_ZONES), delta_max_mm=400, buffer_size=15)


# ─── ZoneConfig tests ────────────────────────────────────────────────────────

def test_zone_fits_box_nominal():
    # 605x445 fits in A (usable 605x450)
    assert ZONE_A.fits_box(605, 445) is True


def test_zone_fits_box_too_large_for_B():
    # 605x445 does not fit in B (usable 630x355) — width 445 > 355
    assert ZONE_B.fits_box(605, 445) is False


def test_zone_fits_box_rotation():
    # 355x630 doesn't fit in B as-is (630 fits, 355 fits) — actually fits
    # 445x630: width 445 > 355, rotated: 630 > 630? No. 630<=630 and 445>355 → False
    assert ZONE_B.fits_box(445, 630) is False
    # 300x355 fits in B
    assert ZONE_B.fits_box(300, 355) is True


def test_contains_placement_inside():
    assert ZONE_A.contains_placement(0, 0, 500, 400) is True


def test_contains_placement_outside():
    # x+length overflows zone A x_max=585
    assert ZONE_A.contains_placement(0, 0, 610, 400) is False


def test_contains_placement_at_boundary():
    # Exactly at boundary — should fit
    assert ZONE_A.contains_placement(-20, -20, 605, 450) is True


# ─── ZoneScheduler.compute_zone_heights ──────────────────────────────────────

def test_compute_zone_heights_empty():
    pallet = _make_pallet()
    scheduler = _scheduler()
    heights = scheduler.compute_zone_heights(pallet)
    assert heights == {"A": 0, "B": 0, "C": 0, "D": 0}


def test_compute_zone_heights_one_box():
    """Place one box inside zone A — only A should have non-zero height."""
    pallet = _make_pallet()
    scheduler = _scheduler()

    # Place a box manually in zone A
    box = _box(1, 500, 400, 350)
    buffer = [box]
    result = scheduler.step(pallet, buffer)
    assert result is not None, "Should place box in zone A"
    _, preview = result
    pallet.commit_place(preview)

    heights = scheduler.compute_zone_heights(pallet)
    # Only the zone where box was placed should be non-zero
    non_zero = [name for name, h in heights.items() if h > 0]
    assert len(non_zero) == 1
    assert heights[non_zero[0]] == 350


# ─── ZoneScheduler.select_target_zones ───────────────────────────────────────

def test_select_target_zones_all_zero():
    scheduler = _scheduler()
    heights = {"A": 0, "B": 0, "C": 0, "D": 0}
    eligible = scheduler.select_target_zones(heights)
    assert len(eligible) == 4
    assert [z.name for z in eligible] == ["A", "B", "C", "D"]


def test_select_target_zones_excludes_tall():
    """Zone D at 450mm, others at 0 → D excluded with delta_max=400."""
    scheduler = ZoneScheduler(zones=list(PALLET_ZONES), delta_max_mm=400)
    heights = {"A": 0, "B": 0, "C": 0, "D": 450}
    eligible = scheduler.select_target_zones(heights)
    names = [z.name for z in eligible]
    assert "D" not in names
    assert "A" in names
    assert "B" in names
    assert "C" in names


def test_select_target_zones_sorted_by_height():
    scheduler = _scheduler()
    heights = {"A": 350, "B": 100, "C": 200, "D": 300}
    eligible = scheduler.select_target_zones(heights)
    h_vals = [heights[z.name] for z in eligible]
    assert h_vals == sorted(h_vals)


# ─── Integration: step places boxes with height sync ─────────────────────────

def test_step_places_boxes_with_height_sync():
    """Place 6 boxes. Assert all placements feasible and zone delta never > 400mm."""
    pallet = _make_pallet()
    scheduler = _scheduler()

    # Use the dominant box from the real flow
    boxes = [_box(i, 605, 445, 355) for i in range(20)]
    buffer = list(boxes[:15])
    remaining = list(boxes[15:])

    placed = 0
    for _ in range(6):
        result = scheduler.step(pallet, buffer)
        assert result is not None, f"Expected placement at step {placed + 1}"
        box, preview = result
        assert preview.feasible
        pallet.commit_place(preview)
        placed += 1

        buffer.remove(box)
        if remaining:
            buffer.append(remaining.pop(0))

        heights = scheduler.compute_zone_heights(pallet)
        h_vals = list(heights.values())
        delta = max(h_vals) - min(h_vals)
        assert delta <= 400, (
            f"Zone height delta {delta}mm exceeds 400mm at step {placed}: {heights}"
        )

    assert placed == 6


def test_step_returns_none_when_buffer_empty():
    pallet = _make_pallet()
    scheduler = _scheduler()
    result = scheduler.step(pallet, [])
    assert result is None
