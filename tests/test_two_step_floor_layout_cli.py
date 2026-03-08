from __future__ import annotations

from palca.integration.policy_packer_sched import PolicyPackerScheduler
from sim import run as sim_run


def test_build_parser_accepts_two_step_floor_layout_flags() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--two-step-floor-layout-end-step",
            "8",
            "--two-step-floor-layout-lookahead-items",
            "7",
        ]
    )
    assert int(args.two_step_floor_layout_end_step) == 8
    assert int(args.two_step_floor_layout_lookahead_items) == 7


def test_policy_from_defaults_propagates_two_step_floor_layout_config() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        two_step_floor_layout_end_step=9,
        two_step_floor_layout_lookahead_items=5,
    )
    cfg = policy.config.scheduler
    assert int(cfg.two_step_floor_layout_end_step) == 9
    assert int(cfg.two_step_floor_layout_lookahead_items) == 5
