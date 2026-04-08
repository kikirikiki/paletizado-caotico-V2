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
            "--hard-floor-phase-early-stand-policy",
            "regret_gated",
            "--hard-floor-phase-early-stand-max-count",
            "1",
            "--hard-floor-phase-early-stand-candidate-cap",
            "4",
            "--hard-floor-phase-early-stand-max-placed-loss",
            "0",
            "--hard-floor-phase-early-stand-max-largest-free-rect-loss-ratio",
            "0.12",
            "--hard-floor-phase-early-stand-max-height-std-increase-mm",
            "25.0",
            "--hard-floor-phase-early-stand-min-access-mouth-mm",
            "200",
        ]
    )
    assert int(args.hard_floor_phase_end_step) == 8
    assert int(args.hard_floor_phase_min_base_candidates) == 2
    assert int(args.hard_floor_phase_lookahead_items) == 11
    assert float(args.hard_floor_phase_stand_mix_bonus) == 0.35
    assert str(args.hard_floor_phase_early_stand_policy) == "regret_gated"
    assert int(args.hard_floor_phase_early_stand_max_count) == 1
    assert int(args.hard_floor_phase_early_stand_candidate_cap) == 4
    assert int(args.hard_floor_phase_early_stand_max_placed_loss) == 0
    assert float(args.hard_floor_phase_early_stand_max_largest_free_rect_loss_ratio) == 0.12
    assert float(args.hard_floor_phase_early_stand_max_height_std_increase_mm) == 25.0
    assert int(args.hard_floor_phase_early_stand_min_access_mouth_mm) == 200


def test_policy_from_defaults_propagates_hard_floor_phase_config() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        hard_floor_phase_end_step=9,
        hard_floor_phase_min_base_candidates=3,
        hard_floor_phase_lookahead_items=10,
        hard_floor_phase_stand_mix_bonus=0.4,
        hard_floor_phase_early_stand_policy="regret_gated",
        hard_floor_phase_early_stand_max_count=1,
        hard_floor_phase_early_stand_candidate_cap=5,
        hard_floor_phase_early_stand_max_placed_loss=0,
        hard_floor_phase_early_stand_max_largest_free_rect_loss_ratio=0.06,
        hard_floor_phase_early_stand_max_height_std_increase_mm=30.0,
        hard_floor_phase_early_stand_min_access_mouth_mm=210,
    )
    cfg = policy.config.scheduler
    assert int(cfg.hard_floor_phase_end_step) == 9
    assert int(cfg.hard_floor_phase_min_base_candidates) == 3
    assert int(cfg.hard_floor_phase_lookahead_items) == 10
    assert float(cfg.hard_floor_phase_stand_mix_bonus) == 0.4
    assert str(cfg.hard_floor_phase_early_stand_policy) == "regret_gated"
    assert int(cfg.hard_floor_phase_early_stand_max_count) == 1
    assert int(cfg.hard_floor_phase_early_stand_candidate_cap) == 5
    assert int(cfg.hard_floor_phase_early_stand_max_placed_loss) == 0
    assert float(cfg.hard_floor_phase_early_stand_max_largest_free_rect_loss_ratio) == 0.06
    assert float(cfg.hard_floor_phase_early_stand_max_height_std_increase_mm) == 30.0
    assert int(cfg.hard_floor_phase_early_stand_min_access_mouth_mm) == 210
