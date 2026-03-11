from __future__ import annotations

from palca.integration.policy_packer_sched import PolicyPackerScheduler
from sim import run as sim_run


def test_build_parser_accepts_max_layer_backstep_flag() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--max-layer-backstep",
            "1",
        ]
    )
    assert int(args.max_layer_backstep) == 1


def test_policy_from_defaults_propagates_max_layer_backstep() -> None:
    policy = PolicyPackerScheduler.from_defaults(max_layer_backstep=1)
    assert int(policy.config.scheduler.max_layer_backstep or 0) == 1


def test_policy_collect_kpis_exposes_bounded_backstep_metrics() -> None:
    policy = PolicyPackerScheduler.from_defaults(max_layer_backstep=1)
    policy._scheduler.placements_rejected_bounded_backstep = 3
    policy._scheduler._bounded_backstep_rejection_samples = [  # noqa: SLF001
        {"candidate_layer_idx": 0, "highest_open_layer_idx": 2, "drop": 2, "limit": 1}
    ]

    kpis = policy.collect_kpis()
    assert int(kpis["max_layer_backstep"] or 0) == 1
    assert int(kpis["placements_rejected_bounded_backstep"]) == 3
    assert isinstance(kpis["bounded_backstep_rejection_samples"], list)
    assert len(kpis["bounded_backstep_rejection_samples"]) == 1
