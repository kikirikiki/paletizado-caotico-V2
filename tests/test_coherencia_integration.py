"""Integration tests for score_mode='coherencia_capa'."""
from __future__ import annotations

import pytest

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1
from palca.scoring.height_slack import ScoreMode


# ---------------------------------------------------------------------------
# Test 1: SchedulerConfig accepts score_mode="coherencia_capa" without error
# ---------------------------------------------------------------------------

def test_scheduler_config_coherencia_capa_no_valueerror() -> None:
    config = SchedulerConfig(score_mode="coherencia_capa")
    assert config.score_mode == "coherencia_capa"


def test_scoremode_enum_has_coherencia_capa() -> None:
    assert ScoreMode.COHERENCIA_CAPA.value == "coherencia_capa"


# ---------------------------------------------------------------------------
# Minimal mock pallet for integration
# ---------------------------------------------------------------------------

class _MockPallet:
    """Minimal pallet stub: returns a fixed PlacementPreview and exposes placements."""

    def __init__(
        self,
        gain: float = 1.0,
        height_after_mm: int = 100,
        x_mm: int = 0,
        y_mm: int = 0,
        z_mm: int = 0,
        existing_placements: list[Placement] | None = None,
    ) -> None:
        self._gain = gain
        self._height_after_mm = height_after_mm
        self._x_mm = x_mm
        self._y_mm = y_mm
        self._z_mm = z_mm
        self.placements: list[Placement] = existing_placements or []

    def preview_place(self, box: Box) -> PlacementPreview:
        placement = Placement(
            x_mm=self._x_mm,
            y_mm=self._y_mm,
            z_mm=self._z_mm,
            rot90=False,
            layer_id=0,
            length_mm=box.length_mm,
            width_mm=box.width_mm,
            height_mm=box.height_mm,
            box_id=box.box_id,
        )
        return PlacementPreview(
            feasible=True,
            placement=placement,
            packing_gain=self._gain,
            fragmentation=0.0,
            height_after_mm=self._height_after_mm,
        )


# ---------------------------------------------------------------------------
# Test 2: scheduler with score_mode="coherencia_capa" selects a candidate
# ---------------------------------------------------------------------------

def test_coherencia_capa_selects_candidate_without_crash() -> None:
    """Scheduler with coherencia_capa mode must return a plan from mock candidates."""
    box = Box(
        box_id=1,
        length_mm=300,
        width_mm=200,
        height_mm=150,
        timestamp=0.0,
        destination=1,
    )
    pallet = _MockPallet(gain=1.0, height_after_mm=150)
    sim_state = SchedulerSimState(
        now=0.0,
        ramps={1: [box]},
        pallets={1: pallet},
        pallet_blocked=set(),
    )
    scheduler = SchedulerV1(SchedulerConfig(score_mode="coherencia_capa"))
    plan = scheduler.choose_action(sim_state)
    assert plan is not None
    assert plan.pallet_id == 1


def test_coherencia_capa_selects_candidate_with_existing_placements() -> None:
    """Coherencia scorer should run without error when placements already exist."""
    existing = [
        Placement(
            x_mm=0,
            y_mm=0,
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=300,
            width_mm=200,
            height_mm=150,
            box_id=99,
        )
    ]
    box = Box(
        box_id=2,
        length_mm=300,
        width_mm=200,
        height_mm=150,
        timestamp=0.0,
        destination=1,
    )
    pallet = _MockPallet(
        gain=1.0,
        height_after_mm=300,
        x_mm=310,
        y_mm=0,
        z_mm=0,
        existing_placements=existing,
    )
    sim_state = SchedulerSimState(
        now=0.0,
        ramps={1: [box]},
        pallets={1: pallet},
        pallet_blocked=set(),
    )
    scheduler = SchedulerV1(SchedulerConfig(score_mode="coherencia_capa"))
    plan = scheduler.choose_action(sim_state)
    assert plan is not None
