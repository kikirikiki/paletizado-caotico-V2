from __future__ import annotations

from argparse import Namespace

from scripts import ablation_runner


def _make_args(*, max_pallets: int) -> Namespace:
    return Namespace(
        python="./.venv/bin/python",
        excel="data/Flujo rampas - Editado.xlsx",
        force_destination=1,
        max_pallets=max_pallets,
        k=15,
        heuristic="bssf",
        overhang_mm=20,
        stability_mode="ratio+corners+settle",
        min_support=0.85,
        time_budget_ms=900,
        micro_depth=4,
        micro_width=40,
        micro_topk=15,
        score_mode="min_height_slack_then_gain",
        height_slack_mm=120,
    )


def test_build_base_cmd_propagates_max_pallets_when_enabled() -> None:
    cmd = ablation_runner.build_base_cmd(_make_args(max_pallets=1))
    assert "--max-pallets" in cmd
    idx = cmd.index("--max-pallets")
    assert cmd[idx + 1] == "1"
    assert "--print" not in cmd


def test_build_base_cmd_skips_max_pallets_when_zero() -> None:
    cmd = ablation_runner.build_base_cmd(_make_args(max_pallets=0))
    assert "--max-pallets" not in cmd


def test_extract_kpis_first_pallet_boxes() -> None:
    payload = {
        "metrics": {
            "pallet_kpis": {
                "continuous_pallet_sequence": {"1": [24, 21, 23]},
                "pallets_count": {"1": 3},
            }
        }
    }
    k = ablation_runner.extract_kpis(payload, dest="1")
    assert k["seq_len"] == 3
    assert k["first_pallet_boxes"] == 24

    empty = ablation_runner.extract_kpis({"metrics": {"pallet_kpis": {}}}, dest="1")
    assert empty["first_pallet_boxes"] is None
