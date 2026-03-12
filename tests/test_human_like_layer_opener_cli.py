from __future__ import annotations

from palca.integration.policy_packer_sched import PolicyPackerScheduler
from sim import run as sim_run


def test_build_parser_accepts_human_like_layer_opener_flags() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--human-like-layer-opener",
            "--human-like-layer-opener-prefix-len",
            "4",
            "--human-like-layer-opener-candidate-cap",
            "8",
            "--human-like-layer-opener-poison-penalty-weight",
            "1.7",
            "--human-like-layer-opener-closure-weight",
            "0.9",
            "--human-like-layer-opener-fragmentation-weight",
            "1.2",
        ]
    )
    assert bool(args.human_like_layer_opener) is True
    assert int(args.human_like_layer_opener_prefix_len) == 4
    assert int(args.human_like_layer_opener_candidate_cap) == 8
    assert float(args.human_like_layer_opener_poison_penalty_weight) == 1.7
    assert float(args.human_like_layer_opener_closure_weight) == 0.9
    assert float(args.human_like_layer_opener_fragmentation_weight) == 1.2


def test_policy_from_defaults_propagates_human_like_layer_opener_config() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        human_like_layer_opener=True,
        human_like_layer_opener_prefix_len=4,
        human_like_layer_opener_candidate_cap=8,
        human_like_layer_opener_poison_penalty_weight=1.7,
        human_like_layer_opener_closure_weight=0.9,
        human_like_layer_opener_fragmentation_weight=1.2,
    )
    cfg = policy.config.scheduler
    assert bool(cfg.human_like_layer_opener_enabled) is True
    assert int(cfg.human_like_layer_opener_prefix_len) == 4
    assert int(cfg.human_like_layer_opener_candidate_cap) == 8
    assert float(cfg.human_like_layer_opener_poison_penalty_weight) == 1.7
    assert float(cfg.human_like_layer_opener_closure_weight) == 0.9
    assert float(cfg.human_like_layer_opener_fragmentation_weight) == 1.2
