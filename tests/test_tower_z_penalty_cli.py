from __future__ import annotations

from palca.integration.policy_packer_sched import PolicyPackerScheduler
from sim import run as sim_run


def test_build_parser_accepts_tower_z_flags() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--tower-z-band-mm",
            "120",
            "--tower-z-penalty-weight",
            "0.75",
        ]
    )
    assert int(args.tower_z_band_mm) == 120
    assert float(args.tower_z_penalty_weight) == 0.75


def test_policy_from_defaults_propagates_tower_z_config() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        tower_z_band_mm=80,
        tower_z_penalty_weight=0.5,
    )
    assert int(policy.config.scheduler.tower_z_band_mm) == 80
    assert float(policy.config.scheduler.tower_z_penalty_weight) == 0.5
