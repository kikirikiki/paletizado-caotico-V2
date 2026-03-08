from __future__ import annotations

from palca.integration.policy_packer_sched import PolicyPackerScheduler
from sim import run as sim_run


def test_build_parser_accepts_early_stand_floor_priority_flags() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--early-stand-floor-priority-end-step",
            "8",
            "--early-stand-floor-priority-bonus",
            "0.6",
        ]
    )
    assert int(args.early_stand_floor_priority_end_step) == 8
    assert float(args.early_stand_floor_priority_bonus) == 0.6


def test_policy_from_defaults_propagates_early_stand_floor_priority_config() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        early_stand_floor_priority_end_step=10,
        early_stand_floor_priority_bonus=0.75,
    )
    cfg = policy.config.scheduler
    assert int(cfg.early_stand_floor_priority_end_step) == 10
    assert float(cfg.early_stand_floor_priority_bonus) == 0.75
