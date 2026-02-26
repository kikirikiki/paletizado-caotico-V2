from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sim import run as sim_run


@pytest.mark.parametrize(
    ("score_mode", "height_slack_mm", "height_bucket_mm"),
    [
        ("gain_frag", 0, 80),
        ("min_height_then_gain", 0, 80),
        ("min_height_slack_then_gain", 80, 80),
    ],
)
def test_microplanner_immediate_smoke_60(
    score_mode: str,
    height_slack_mm: int,
    height_bucket_mm: int,
) -> None:
    excel_path = Path("data") / "Flujo_smoke_60.xlsx"
    assert excel_path.exists(), "Excel smoke de 60 filas no encontrado en data/"

    payload = sim_run.run_simulation(
        excel_path=str(excel_path),
        model="M1",
        n_per_pallet=999999,
        t_pick_place=14.0,
        staging_cap=0,
        out_path=None,
        policy="palca",
        lookahead_k=15,
        stability_mode="ratio+corners+settle",
        min_support=0.90,
        overhang_mm=20,
        force_destination=1,
        continuous_pallets=True,
        arrival_mode="immediate",
        time_budget_ms=900,
        score_mode=score_mode,
        height_slack_mm=height_slack_mm,
        height_bucket_mm=height_bucket_mm,
        micro_plan=True,
        micro_depth=3,
        micro_width=8,
        micro_topk=15,
    )

    params = payload["params"]
    metrics = payload["metrics"]
    kpis = metrics["pallet_kpis"]
    window_stats = kpis.get("accessible_window_stats", {})

    assert int(metrics["processed_boxes"]) == 60
    assert int(metrics["total_boxes"]) == 60
    assert metrics.get("stop_reason") is None
    assert bool(params.get("micro_plan")) is True
    assert str(params.get("score_mode")) == score_mode
    assert int(params.get("height_slack_mm", 0)) == int(height_slack_mm)
    assert int(params.get("height_bucket_mm", 0)) == int(height_bucket_mm)
    assert int(params.get("micro_depth", 0)) == 3
    assert int(params.get("micro_width", 0)) == 8
    assert int(params.get("micro_topk", 0)) == 15

    for key in (
        "micro_plan_calls",
        "micro_plan_fallback_greedy",
        "micro_plan_time_ms_min",
        "micro_plan_time_ms_mean",
        "micro_plan_time_ms_max",
        "micro_plan_nodes_expanded_total",
        "micro_plan_depth_effective_mean",
    ):
        assert key in kpis

    print(f"[micro-smoke] score_mode={score_mode} closures_by_reason={kpis.get('closures_by_reason', {})}")
    assert float(window_stats.get("mean", 0.0)) >= 0.0


def test_microplanner_cli_height_bucket_param_in_payload(monkeypatch: Any) -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--policy",
            "palca",
            "--score-mode",
            "min_height_slack_then_gain",
            "--height-slack-mm",
            "80",
            "--height-bucket-mm",
            "80",
            "--micro-plan",
            "--micro-depth",
            "3",
            "--micro-width",
            "8",
            "--micro-topk",
            "15",
        ]
    )

    captured: dict[str, Any] = {}

    class DummyPolicyPackerScheduler:
        @classmethod
        def from_defaults(cls, **kwargs: Any) -> object:
            captured.update(kwargs)
            return object()

    class DummyResult:
        def to_dict(self) -> dict[str, object]:
            return {"processed_boxes": 0, "total_boxes": 0, "pallet_kpis": {}}

    monkeypatch.setattr(sim_run, "load_arrivals", lambda *args, **kwargs: [])
    monkeypatch.setattr(sim_run, "simulate", lambda *args, **kwargs: DummyResult())

    import palca.integration.policy_packer_sched as integration_mod

    monkeypatch.setattr(integration_mod, "PolicyPackerScheduler", DummyPolicyPackerScheduler)

    payload = sim_run.run_simulation(
        excel_path=str(args.excel),
        model="M1",
        n_per_pallet=24,
        t_pick_place=14.0,
        staging_cap=0,
        out_path=None,
        policy=str(args.policy),
        lookahead_k=1,
        score_mode=str(args.score_mode),
        height_slack_mm=int(args.height_slack_mm),
        height_bucket_mm=int(args.height_bucket_mm),
        micro_plan=bool(args.micro_plan),
        micro_depth=int(args.micro_depth),
        micro_width=int(args.micro_width),
        micro_topk=int(args.micro_topk),
    )

    assert int(args.height_bucket_mm) == 80
    assert str(args.score_mode) == "min_height_slack_then_gain"
    assert int(captured.get("height_bucket_mm", 0)) == 80
    assert str(payload["params"]["score_mode"]) == "min_height_slack_then_gain"
    assert int(payload["params"]["height_bucket_mm"]) == 80
