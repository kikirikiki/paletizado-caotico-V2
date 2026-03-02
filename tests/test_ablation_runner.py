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
        stacking_mode="layers",
        z_band_mm=None,
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


def test_build_base_cmd_propagates_stacking_mode_when_non_default() -> None:
    args = _make_args(max_pallets=0)
    args.stacking_mode = "heightfield"
    cmd = ablation_runner.build_base_cmd(args)
    assert "--stacking-mode" in cmd
    idx = cmd.index("--stacking-mode")
    assert cmd[idx + 1] == "heightfield"


def test_build_base_cmd_propagates_z_band_when_set() -> None:
    args = _make_args(max_pallets=0)
    args.z_band_mm = 0
    cmd = ablation_runner.build_base_cmd(args)
    assert "--z-band-mm" in cmd
    idx = cmd.index("--z-band-mm")
    assert cmd[idx + 1] == "0"


def test_extract_kpis_uses_closed_sequence_as_primary_pallet_count() -> None:
    payload = {
        "metrics": {
            "processed_boxes": 14,
            "stop_reason": "MAX_PALLETS_REACHED",
            "pallet_kpis": {
                "continuous_pallet_sequence": {"1": [24, 21, 23]},
                "pallets_count": {"1": 99},
            }
        }
    }
    k = ablation_runner.extract_kpis(payload, dest="1")
    assert k["pallets_closed"] == 3
    assert k["pallets_created"] == 99
    assert k["seq_len"] == 3
    assert k["first_pallet_boxes"] == 24
    assert k["processed_boxes"] == 14
    assert k["stop_reason"] == "MAX_PALLETS_REACHED"

    empty = ablation_runner.extract_kpis({"metrics": {"pallet_kpis": {}}}, dest="1")
    assert empty["pallets_closed"] == 0
    assert empty["pallets_created"] is None
    assert empty["first_pallet_boxes"] is None
    assert empty["processed_boxes"] is None
    assert empty["stop_reason"] is None


def test_extract_kpis_tolerates_missing_metrics_keys() -> None:
    missing = ablation_runner.extract_kpis({}, dest="1")
    assert missing["pallets_closed"] == 0
    assert missing["pallets_created"] is None
    assert missing["seq_len"] == 0
    assert missing["processed_boxes"] is None
    assert missing["stop_reason"] is None
