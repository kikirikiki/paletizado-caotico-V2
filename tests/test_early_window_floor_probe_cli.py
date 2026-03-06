from __future__ import annotations

from palca.integration.policy_packer_sched import PolicyPackerScheduler
from sim import run as sim_run


def test_build_parser_accepts_early_window_floor_probe_flag() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--early-window-floor-probe-end-step",
            "8",
        ]
    )
    assert int(args.early_window_floor_probe_end_step) == 8


def test_policy_from_defaults_propagates_early_window_floor_probe_config() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        early_window_floor_probe_end_step=9,
    )
    assert int(policy.config.scheduler.early_window_floor_probe_end_step) == 9

