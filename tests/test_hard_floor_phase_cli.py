from __future__ import annotations

from palca.integration.policy_packer_sched import PolicyPackerScheduler
from sim import run as sim_run


def test_build_parser_accepts_hard_floor_phase_flags() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--hard-floor-phase-end-step",
            "8",
            "--hard-floor-phase-min-base-candidates",
            "2",
            "--hard-floor-phase-lookahead-items",
            "11",
            "--hard-floor-phase-stand-mix-bonus",
            "0.35",
        ]
    )
    assert int(args.hard_floor_phase_end_step) == 8
    assert int(args.hard_floor_phase_min_base_candidates) == 2
    assert int(args.hard_floor_phase_lookahead_items) == 11
    assert float(args.hard_floor_phase_stand_mix_bonus) == 0.35


def test_policy_from_defaults_propagates_hard_floor_phase_config() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        hard_floor_phase_end_step=9,
        hard_floor_phase_min_base_candidates=3,
        hard_floor_phase_lookahead_items=10,
        hard_floor_phase_stand_mix_bonus=0.4,
    )
    cfg = policy.config.scheduler
    assert int(cfg.hard_floor_phase_end_step) == 9
    assert int(cfg.hard_floor_phase_min_base_candidates) == 3
    assert int(cfg.hard_floor_phase_lookahead_items) == 10
    assert float(cfg.hard_floor_phase_stand_mix_bonus) == 0.4


def test_build_parser_accepts_active_layer_closure_reservation_flag() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--enforce-active-layer-closure-reservation",
        ]
    )
    assert bool(args.enforce_active_layer_closure_reservation) is True


def test_policy_from_defaults_propagates_active_layer_closure_reservation() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        enforce_active_layer_closure_reservation=True,
    )
    cfg = policy.config.scheduler
    assert bool(cfg.enforce_active_layer_closure_reservation) is True
