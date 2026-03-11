from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.packer.pallet_model import PalletModel
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerRampState, SchedulerSimState, SchedulerV1


def _box(*, box_id: int, l: int, w: int, h: int = 10) -> Box:
    return Box(
        box_id=int(box_id),
        length_mm=int(l),
        width_mm=int(w),
        height_mm=int(h),
        timestamp=0.0,
        destination=1,
    )


def _seed_partial_active_layer() -> PalletModel:
    spec = PalletSpec(length_mm=10, width_mm=10, max_height_mm=100, overhang_mm=0)
    pallet = PalletModel(spec=spec, heuristic="baf")
    seed = _box(box_id=100, l=6, w=10, h=10)
    seed_preview = pallet.preview_place(seed)
    assert seed_preview.feasible
    pallet.commit_place(seed_preview)
    return pallet


def test_closure_reservation_blocks_upper_open_when_visible_buffer_shows_active_layer_potential() -> None:
    pallet = _seed_partial_active_layer()
    upper_now = _box(box_id=1, l=5, w=10, h=10)
    visible_fit = _box(box_id=2, l=4, w=10, h=10)

    sim_state = SchedulerSimState(
        now=0.0,
        ramps={1: [upper_now]},
        ramp_states={
            1: SchedulerRampState(
                queue=(upper_now, visible_fit),
                staging=(),
                upstream=(),
                capacity=15,
            )
        },
        pallets={1: pallet},
        pallet_blocked=set(),
        remaining_total=2,
    )

    baseline = SchedulerV1(SchedulerConfig(lookahead_k=1))
    baseline_plan = baseline.choose_action(sim_state)
    assert baseline_plan is not None
    assert int(baseline_plan.box_id) == 1

    guarded = SchedulerV1(
        SchedulerConfig(
            lookahead_k=1,
            enforce_active_layer_closure_reservation=True,
        )
    )
    guarded_plan = guarded.choose_action(sim_state)
    assert guarded_plan is None
    assert int(guarded.upper_layer_open_attempts_total) == 1
    assert int(guarded.blocked_upper_layer_open_attempts) == 1
    assert int(guarded.closure_reservation_hits) == 1
    assert int(guarded.closure_reservation_misses) == 0
    assert guarded.upper_layer_open_attempts_trace
    first_event = guarded.upper_layer_open_attempts_trace[0]
    match = first_event.get("closure_potential_match")
    assert isinstance(match, dict)
    assert int(match.get("box", {}).get("box_id")) == 2


def test_closure_reservation_allows_upper_open_when_no_visible_active_layer_potential() -> None:
    pallet = _seed_partial_active_layer()
    upper_now = _box(box_id=1, l=5, w=10, h=10)
    visible_no_fit = _box(box_id=3, l=5, w=5, h=10)

    sim_state = SchedulerSimState(
        now=0.0,
        ramps={1: [upper_now]},
        ramp_states={
            1: SchedulerRampState(
                queue=(upper_now, visible_no_fit),
                staging=(),
                upstream=(),
                capacity=15,
            )
        },
        pallets={1: pallet},
        pallet_blocked=set(),
        remaining_total=2,
    )

    guarded = SchedulerV1(
        SchedulerConfig(
            lookahead_k=1,
            enforce_active_layer_closure_reservation=True,
        )
    )
    guarded_plan = guarded.choose_action(sim_state)
    assert guarded_plan is not None
    assert int(guarded.upper_layer_open_attempts_total) == 1
    assert int(guarded.blocked_upper_layer_open_attempts) == 0
    assert int(guarded.closure_reservation_hits) == 0
    assert int(guarded.closure_reservation_misses) == 1


def test_closure_reservation_kpis_are_exposed() -> None:
    policy = PolicyPackerScheduler.from_defaults(enforce_active_layer_closure_reservation=True)
    policy._scheduler.upper_layer_open_attempts_total = 7
    policy._scheduler.blocked_upper_layer_open_attempts = 3
    policy._scheduler.closure_reservation_hits = 3
    policy._scheduler.closure_reservation_misses = 4
    policy._scheduler.upper_layer_open_attempts_trace = [{"step_index": 1, "reason": "closure_potential_visible"}]
    kpis = policy.collect_kpis()
    assert bool(kpis["enforce_active_layer_closure_reservation"]) is True
    assert int(kpis["upper_layer_open_attempts_total"]) == 7
    assert int(kpis["blocked_upper_layer_open_attempts"]) == 3
    assert int(kpis["closure_reservation_hits"]) == 3
    assert int(kpis["closure_reservation_misses"]) == 4
    trace = kpis["upper_layer_open_attempts_trace"]
    assert isinstance(trace, list) and trace
