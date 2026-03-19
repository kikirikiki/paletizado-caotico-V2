from __future__ import annotations

import pytest

from palca.domain.box import Box
from palca.domain.placement import Placement
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel
from palca.packer.controls import ControlConfig, StabilityConfig, build_control_stack
from palca.packer.zones import ZoneConfig, PALLET_ZONES
from palca.scheduler.zone_scheduler import ZoneScheduler


ZONE_A = PALLET_ZONES[0]  # x=-20..585, y=-20..430
ZONE_B = PALLET_ZONES[1]  # x=585..1215, y=-20..335
ZONE_C = PALLET_ZONES[2]  # x=-20..615, y=430..780
ZONE_D = PALLET_ZONES[3]  # x=615..1220, y=335..785


def _make_box(
    box_id: int = 1,
    length_mm: int = 400,
    width_mm: int = 300,
    height_mm: int = 200,
) -> Box:
    return Box(
        box_id=box_id,
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        timestamp=float(box_id),
    )


# ---------------------------------------------------------------------------
# ZoneConfig.fits_box
# ---------------------------------------------------------------------------


def test_zone_fits_box() -> None:
    """Box 605×445 fits in zone A (usable 605×450) straight."""
    assert ZoneConfig("A", -20, 585, -20, 430).fits_box(605, 445) is True


def test_zone_fits_box_too_large() -> None:
    """Box 605×445 does NOT fit in zone B (usable 630×355) — 445 > 355 in both orientations."""
    assert ZoneConfig("B", 585, 1215, -20, 335).fits_box(605, 445) is False


# ---------------------------------------------------------------------------
# ZoneConfig.contains_placement
# ---------------------------------------------------------------------------


def test_contains_placement_inside() -> None:
    """Placement (-20,-20,605,450) spans exactly zone A bounds → inside."""
    assert ZONE_A.contains_placement(x_mm=-20, y_mm=-20, length_mm=605, width_mm=450)


def test_contains_placement_outside() -> None:
    """Placement at x=600 with length=100 ends at 700 > zone A x_max=585."""
    assert not ZONE_A.contains_placement(x_mm=600, y_mm=0, length_mm=100, width_mm=100)


# ---------------------------------------------------------------------------
# ZoneScheduler.compute_zone_heights
# ---------------------------------------------------------------------------


def test_compute_zone_heights_empty() -> None:
    """Empty pallet returns zero for all zones."""
    scheduler = ZoneScheduler(zones=PALLET_ZONES)
    pallet = PalletModel()
    heights = scheduler.compute_zone_heights(pallet)
    assert heights == {"A": 0, "B": 0, "C": 0, "D": 0}


def test_compute_zone_heights_one_box() -> None:
    """Placement at zone A coords updates only zone A height."""
    scheduler = ZoneScheduler(zones=PALLET_ZONES)
    pallet = PalletModel()
    # Place inside zone A: x=-20..585, y=-20..430
    p = Placement(
        x_mm=0, y_mm=0, z_mm=0,
        rot90=False, layer_id=0,
        length_mm=200, width_mm=200, height_mm=300,
    )
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
    """All zones at height 0 → all 4 returned sorted."""
    scheduler = ZoneScheduler(zones=PALLET_ZONES, delta_max_mm=400)
    heights = {z.name: 0 for z in PALLET_ZONES}
    eligible = scheduler.select_target_zones(heights)
    assert len(eligible) == 4
    assert [z.name for z in eligible] == ["A", "B", "C", "D"]


def test_select_target_zones_excludes_tall() -> None:
    """Zone D at 450mm with delta_max=400 and others at 0 → D excluded."""
    scheduler = ZoneScheduler(zones=PALLET_ZONES, delta_max_mm=400)
    heights = {"A": 0, "B": 0, "C": 0, "D": 450}
    eligible = scheduler.select_target_zones(heights)
    names = [z.name for z in eligible]
    assert "D" not in names
    assert set(names) == {"A", "B", "C"}


# ---------------------------------------------------------------------------
# Integration: step() places boxes and keeps heights balanced
# ---------------------------------------------------------------------------


def test_step_places_boxes() -> None:
    """
    Run 6 steps with ZoneScheduler on a real PalletModel with heightfield
    and assert all placements are feasible and zone heights never differ > 400mm.
    """
    spec = PalletSpec(
        length_mm=1220,
        width_mm=800,
        max_height_mm=3000,
        overhang_mm=20,
        allow_rotate=True,
    )
    control_config = ControlConfig(
        stability=StabilityConfig(
            mode="ratio+corners+settle",
            min_support_ratio=0.85,
        )
    )
    pallet = PalletModel(
        spec=spec,
        heuristic="bssf",
        controls=build_control_stack(control_config),
    )

    # Use test zones aligned to the pallet
    test_zones = [
        ZoneConfig("TL", x_min_mm=-20, x_max_mm=620,  y_min_mm=-20, y_max_mm=420),
        ZoneConfig("TR", x_min_mm=600, x_max_mm=1240, y_min_mm=-20, y_max_mm=420),
        ZoneConfig("BL", x_min_mm=-20, x_max_mm=620,  y_min_mm=400, y_max_mm=840),
        ZoneConfig("BR", x_min_mm=600, x_max_mm=1240, y_min_mm=400, y_max_mm=840),
    ]
    scheduler = ZoneScheduler(zones=test_zones, delta_max_mm=400, buffer_size=15)

    buffer = [_make_box(box_id=i, length_mm=400, width_mm=300, height_mm=200) for i in range(1, 7)]

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
        min_h = min(v for v in heights.values() if v > 0) if any(heights.values()) else 0
        assert max_h - min(heights.values()) <= 400, (
            f"Zone height imbalance after {placed} placements: {heights}"
        )

    assert placed == 6, f"Expected 6 placements, got {placed}"
