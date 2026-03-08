from __future__ import annotations

from palca.integration.policy_packer_sched import PolicyPackerScheduler
from sim import run as sim_run


def test_build_parser_accepts_first_layer_planner_flags() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--first-layer-planner-end-step",
            "8",
            "--first-layer-planner-lookahead-items",
            "11",
        ]
    )
    assert int(args.first_layer_planner_end_step) == 8
    assert int(args.first_layer_planner_lookahead_items) == 11


def test_policy_from_defaults_propagates_first_layer_planner_config() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        first_layer_planner_end_step=7,
        first_layer_planner_lookahead_items=9,
    )
    cfg = policy.config.scheduler
    assert int(cfg.first_layer_planner_end_step) == 7
    assert int(cfg.first_layer_planner_lookahead_items) == 9
