from __future__ import annotations

from palca.integration.policy_packer_sched import PolicyPackerScheduler
from sim import run as sim_run


def test_build_parser_accepts_spatial_tower_flags() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--spatial-xy-bin-mm",
            "120",
            "--spatial-tower-penalty-weight",
            "0.4",
            "--spatial-tower-penalty-end-step",
            "14",
            "--spatial-tower-target-base",
            "3",
            "--spatial-tower-target-step-div",
            "7",
        ]
    )
    assert int(args.spatial_xy_bin_mm) == 120
    assert float(args.spatial_tower_penalty_weight) == 0.4
    assert int(args.spatial_tower_penalty_end_step) == 14
    assert int(args.spatial_tower_target_base) == 3
    assert int(args.spatial_tower_target_step_div) == 7


def test_policy_from_defaults_propagates_spatial_tower_config() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        spatial_xy_bin_mm=110,
        spatial_tower_penalty_weight=0.25,
        spatial_tower_penalty_end_step=12,
        spatial_tower_target_base=2,
        spatial_tower_target_step_div=5,
    )
    cfg = policy.config.scheduler
    assert int(cfg.spatial_xy_bin_mm) == 110
    assert float(cfg.spatial_tower_penalty_weight) == 0.25
    assert int(cfg.spatial_tower_penalty_end_step) == 12
    assert int(cfg.spatial_tower_target_base) == 2
    assert int(cfg.spatial_tower_target_step_div) == 5
