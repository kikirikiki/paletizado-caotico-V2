from __future__ import annotations

from palca.integration.policy_packer_sched import PolicyPackerScheduler
from sim import run as sim_run


def test_build_parser_accepts_floor_first_flag() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--floor-first-end-step",
            "8",
        ]
    )
    assert int(args.floor_first_end_step) == 8


def test_policy_from_defaults_propagates_floor_first_config() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        floor_first_end_step=8,
    )
    assert int(policy.config.scheduler.floor_first_end_step) == 8
