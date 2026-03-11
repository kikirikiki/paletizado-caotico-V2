from __future__ import annotations

from palca.integration.policy_packer_sched import PolicyPackerScheduler
from sim import run as sim_run


def test_build_parser_accepts_no_layer_reentry_flag() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--enforce-no-layer-reentry",
        ]
    )
    assert bool(args.enforce_no_layer_reentry) is True


def test_policy_from_defaults_propagates_no_layer_reentry_config() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        enforce_no_layer_reentry=True,
    )
    assert bool(policy.config.scheduler.enforce_no_layer_reentry) is True
