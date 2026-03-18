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

        # Torre alta al lado de la candidata — con delta=0 debe ser ignored
        tall_tower = _make_placement(x_mm=0, y_mm=0, z_mm=0, length_mm=400, width_mm=300, height_mm=1500)
        # Candidata justo encima con z_base=0 — inaccesible si delta>0
        candidate = _make_placement(x_mm=0, y_mm=0, z_mm=0, length_mm=400, width_mm=300, height_mm=200)
        pallet = _make_pallet([tall_tower])
        box = _make_box()

        result = ctrl.evaluate(pallet=pallet, box=box, placement=candidate)  # type: ignore[arg-type]
        assert result.feasible is True
        assert result.reason is None

    def test_blocked_by_tall_adjacent_tower(self) -> None:
        """Test 2: caja bloqueada por torre adyacente con delta=400 → feasible=False, reason=ROBOT_ACCESS."""
        ctrl = RobotAccessibilityControl(config=AccessibilityConfig(accessibility_delta_mm=400))

        # Torre ya colocada: x=0, y=0, z=0, size=400x300, height=600
        # Su techo está en z=600. La candidata tiene z_base=0. Delta=400.
        # Condición: placed.z_mm + placed.height_mm > placement.z_mm + delta
        #   => 0 + 600 > 0 + 400 => 600 > 400 => BLOCKED
        tower = _make_placement(x_mm=0, y_mm=0, z_mm=0, length_mm=400, width_mm=300, height_mm=600)
        candidate = _make_placement(x_mm=0, y_mm=0, z_mm=0, length_mm=400, width_mm=300, height_mm=200)
        pallet = _make_pallet([tower])
        box = _make_box()

        result = ctrl.evaluate(pallet=pallet, box=box, placement=candidate)  # type: ignore[arg-type]
        assert result.feasible is False
        assert result.reason == "ROBOT_ACCESS"

    def test_adjacent_tower_within_delta_is_feasible(self) -> None:
        """Test 3: torre adyacente dentro del delta → feasible=True."""
        ctrl = RobotAccessibilityControl(config=AccessibilityConfig(accessibility_delta_mm=400))

        # Torre: height=300, z=0 → techo en z=300. Candidata z_base=0, delta=400.
        # 300 > 0 + 400 => False => no blocked
        tower = _make_placement(x_mm=0, y_mm=0, z_mm=0, length_mm=400, width_mm=300, height_mm=300)
        candidate = _make_placement(x_mm=0, y_mm=0, z_mm=0, length_mm=400, width_mm=300, height_mm=200)
        pallet = _make_pallet([tower])
        box = _make_box()

        result = ctrl.evaluate(pallet=pallet, box=box, placement=candidate)  # type: ignore[arg-type]
        assert result.feasible is True
        assert result.reason is None

    def test_no_neighbors_is_feasible(self) -> None:
        """Test 4: sin vecinos → feasible=True."""
        ctrl = RobotAccessibilityControl(config=AccessibilityConfig(accessibility_delta_mm=400))

        candidate = _make_placement(x_mm=100, y_mm=100, z_mm=0, length_mm=400, width_mm=300, height_mm=200)
        pallet = _make_pallet([])
        box = _make_box()

        result = ctrl.evaluate(pallet=pallet, box=box, placement=candidate)  # type: ignore[arg-type]
        assert result.feasible is True
        assert result.reason is None
