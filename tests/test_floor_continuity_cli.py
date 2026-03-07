from __future__ import annotations

from palca.integration.policy_packer_sched import PolicyPackerScheduler
from sim import run as sim_run


def test_build_parser_accepts_floor_continuity_flags() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--floor-continuity-end-step",
            "8",
            "--floor-continuity-lookahead-items",
            "6",
        ]
    )
    assert int(args.floor_continuity_end_step) == 8
    assert int(args.floor_continuity_lookahead_items) == 6


def test_policy_from_defaults_propagates_floor_continuity_config() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        floor_continuity_end_step=9,
        floor_continuity_lookahead_items=7,
    )
    cfg = policy.config.scheduler
    assert int(cfg.floor_continuity_end_step) == 9
    assert int(cfg.floor_continuity_lookahead_items) == 7
