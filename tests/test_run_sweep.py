from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_sweep


def test_build_sim_run_cmd_contains_sim_module_seed_and_out() -> None:
    out_path = Path("/tmp/palca-sweeps/test_seed314.json")
    cmd = run_sweep.build_sim_run_cmd(
        python_exe="./.venv/bin/python",
        run_args="--excel data/Flujo_smoke_60.xlsx --model M1",
        seed=314,
        out_json=out_path,
        seed_flag="--seed",
    )

    assert cmd[1:3] == ["-m", "sim.run"]
    assert "--seed" in cmd
    seed_idx = cmd.index("--seed")
    assert cmd[seed_idx + 1] == "314"
    out_idx = cmd.index("--out")
    assert cmd[out_idx + 1] == str(out_path)


def test_make_out_path_changes_when_seed_changes() -> None:
    path_a = run_sweep.make_out_path(
        base_out_dir="/tmp/palca-sweeps",
        out_prefix="sweep",
        seed=314,
        run_args="--excel data/Flujo_smoke_60.xlsx --policy palca",
    )
    path_b = run_sweep.make_out_path(
        base_out_dir="/tmp/palca-sweeps",
        out_prefix="sweep",
        seed=315,
        run_args="--excel data/Flujo_smoke_60.xlsx --policy palca",
    )

    assert path_a != path_b


def test_make_out_path_stable_for_same_seed_and_run_args() -> None:
    path_a = run_sweep.make_out_path(
        base_out_dir="/tmp/palca-sweeps",
        out_prefix="sweep",
        seed=314,
        run_args="--excel data/Flujo_smoke_60.xlsx --policy palca",
    )
    path_b = run_sweep.make_out_path(
        base_out_dir="/tmp/palca-sweeps",
        out_prefix="sweep",
        seed=314,
        run_args="--excel data/Flujo_smoke_60.xlsx --policy palca",
    )

    assert path_a == path_b
