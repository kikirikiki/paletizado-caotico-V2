"""Unit tests for RobotAccessibilityControl (Issue #121)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from palca.domain.placement import Placement
from palca.packer.controls import AccessibilityConfig, RobotAccessibilityControl


def _make_placement(x_mm: int, y_mm: int, z_mm: int, length_mm: int, width_mm: int, height_mm: int) -> Placement:
    return Placement(
        x_mm=x_mm,
        y_mm=y_mm,
        z_mm=z_mm,
        rot90=False,
        layer_id=0,
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=height_mm,
    )


def _make_pallet(placements: list[Placement]) -> SimpleNamespace:
    return SimpleNamespace(placements=placements)


def _make_box() -> SimpleNamespace:
    return SimpleNamespace()


class TestRobotAccessibilityControl:
    def test_delta_zero_never_rejects(self) -> None:
        """Test 1: accessibility_delta_mm=0 desactiva el control — siempre feasible."""
        ctrl = RobotAccessibilityControl(config=AccessibilityConfig(accessibility_delta_mm=0))

        tall_tower = _make_placement(x_mm=0, y_mm=0, z_mm=0, length_mm=400, width_mm=300, height_mm=1500)
        candidate = _make_placement(x_mm=0, y_mm=0, z_mm=0, length_mm=400, width_mm=300, height_mm=200)
        pallet = _make_pallet([tall_tower])
        box = _make_box()

        result = ctrl.evaluate(pallet=pallet, box=box, placement=candidate)  # type: ignore[arg-type]
        assert result.feasible is True
        assert result.reason is None

    def test_blocked_by_tall_adjacent_tower(self) -> None:
        """Test 2: caja adyacente alta (no soporte) → feasible=False, reason=ROBOT_ACCESS.

        Candidata: x=400..800, y=0..300, z_base=0.
        Torre adyacente: x=0..400, y=0..300, z=0, height=600 → techo=600.
        local_z bajo la candidata = 0 (la torre no está debajo).
        gap_x = max(0,400) - min(400,800) = 400 - 400 = 0 → adyacente.
        gap_y = max(0,0) - min(300,300) = 0 - 300 = -300 ≤ 0 → adyacente.
        p_top=600 > local_z(0) + delta(400) → ROBOT_ACCESS.
        """
        ctrl = RobotAccessibilityControl(config=AccessibilityConfig(accessibility_delta_mm=400))

        tower = _make_placement(x_mm=0, y_mm=0, z_mm=0, length_mm=400, width_mm=300, height_mm=600)
        candidate = _make_placement(x_mm=400, y_mm=0, z_mm=0, length_mm=400, width_mm=300, height_mm=200)
        pallet = _make_pallet([tower])
        box = _make_box()

        result = ctrl.evaluate(pallet=pallet, box=box, placement=candidate)  # type: ignore[arg-type]
        assert result.feasible is False
        assert result.reason == "ROBOT_ACCESS"

    def test_support_box_below_does_not_block(self) -> None:
        """Test 3: caja debajo (soporte) alta → feasible=True.

        Candidata: x=0..400, y=0..300, z_base=600.
        Soporte: x=0..400, y=0..300, z=0, height=600 → techo=600 = local_z.
        gap_x = max(0,0) - min(400,400) = 0 - 400 = -400 ≤ 0 → "adyacente" (solapa).
        gap_y = -300 ≤ 0 → adyacente.
        p_top=600 > local_z(600) + delta(400)? → 600 > 1000? → False → feasible.
        """
        ctrl = RobotAccessibilityControl(config=AccessibilityConfig(accessibility_delta_mm=400))

        support = _make_placement(x_mm=0, y_mm=0, z_mm=0, length_mm=400, width_mm=300, height_mm=600)
        candidate = _make_placement(x_mm=0, y_mm=0, z_mm=600, length_mm=400, width_mm=300, height_mm=200)
        pallet = _make_pallet([support])
        box = _make_box()

        result = ctrl.evaluate(pallet=pallet, box=box, placement=candidate)  # type: ignore[arg-type]
        assert result.feasible is True
        assert result.reason is None

    def test_separated_tall_box_does_not_block(self) -> None:
        """Test 4: caja separada (gap > 0) alta → feasible=True (no adyacente).

        Candidata: x=500..900, y=0..300, z_base=0.
        Caja separada: x=0..400, y=0..300, z=0, height=900 → techo=900.
        gap_x = max(0,500) - min(400,900) = 500 - 400 = 100 > 0 → NO adyacente.
        """
        ctrl = RobotAccessibilityControl(config=AccessibilityConfig(accessibility_delta_mm=400))

        far_box = _make_placement(x_mm=0, y_mm=0, z_mm=0, length_mm=400, width_mm=300, height_mm=900)
        candidate = _make_placement(x_mm=500, y_mm=0, z_mm=0, length_mm=400, width_mm=300, height_mm=200)
        pallet = _make_pallet([far_box])
        box = _make_box()

        result = ctrl.evaluate(pallet=pallet, box=box, placement=candidate)  # type: ignore[arg-type]
        assert result.feasible is True
        assert result.reason is None

    def test_no_neighbors_is_feasible(self) -> None:
        """Test 5: sin vecinos → feasible=True."""
        ctrl = RobotAccessibilityControl(config=AccessibilityConfig(accessibility_delta_mm=400))

        candidate = _make_placement(x_mm=100, y_mm=100, z_mm=0, length_mm=400, width_mm=300, height_mm=200)
        pallet = _make_pallet([])
        box = _make_box()

        result = ctrl.evaluate(pallet=pallet, box=box, placement=candidate)  # type: ignore[arg-type]
        assert result.feasible is True
        assert result.reason is None
