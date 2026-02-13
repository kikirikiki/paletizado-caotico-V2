import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


def _state_with_two_boxes():
    spec = PalletSpec(overhang_mm=0)
    model = PalletModel(spec=spec, heuristic="baf")

    # 1) Head imposible (oversize brutal)
    head = Box(
        box_id=1,
        length_mm=5000,
        width_mm=5000,
        height_mm=100,
        timestamp=0.0,
        destination=1,
    )

    # 2) Tail factible
    tail = Box(
        box_id=2,
        length_mm=100,
        width_mm=100,
        height_mm=100,
        timestamp=0.0,
        destination=1,
    )

    sim_state = SchedulerSimState(
        now=0.0,
        ramps={1: [head, tail]},
        pallets={1: model},
        pallet_blocked=set(),
        remaining_total=2,
    )
    return sim_state


def test_pick_window_1_cannot_take_tail():
    st = _state_with_two_boxes()
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=1, pick_window=1))
    plan = scheduler.choose_action(st)
    assert plan is None


def test_pick_window_2_can_take_tail():
    st = _state_with_two_boxes()
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=1, pick_window=2))
    plan = scheduler.choose_action(st)
    assert plan is not None
    assert plan.buffer_index == 1
    assert plan.box_id == 2


def test_pick_window_alias_sets_lookahead_k() -> None:
    cfg = SchedulerConfig(pick_window=15)
    assert cfg.lookahead_k == 15


def test_pick_window_conflict_raises_value_error() -> None:
    with pytest.raises(ValueError):
        SchedulerConfig(lookahead_k=3, pick_window=2)
