from __future__ import annotations

from palca.integration.policy_packer_sched import PolicyPackerScheduler
from sim import run as sim_run


def test_build_parser_accepts_early_floor_reorder_flag() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--early-floor-reorder-end-step",
            "8",
        ]
    )
    assert int(args.early_floor_reorder_end_step) == 8


def test_build_parser_early_floor_reorder_default_zero() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(["--excel", "dummy.xlsx"])
    assert int(args.early_floor_reorder_end_step) == 0


def test_policy_from_defaults_propagates_early_floor_reorder_config() -> None:
    policy = PolicyPackerScheduler.from_defaults(early_floor_reorder_end_step=12)
    assert int(policy.config.scheduler.early_floor_reorder_end_step) == 12
