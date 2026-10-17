from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import run_sweep as sweep
from palca.study.params import REFERENCE_SEEDS


ROOT = Path(__file__).resolve().parents[1]


def config_file(tmp_path, overrides="{}"):
    path = tmp_path / "configs.yaml"
    path.write_text(f"- name: trial\n  preset: arm6_4orient\n  role: official\n  overrides: {overrides}\n")
    return path


def test_yaml_parsing_and_presets(tmp_path):
    configs = sweep.load_configs(ROOT / "configs/sweeps/baseline.yaml")
    assert [c["name"] for c in configs] == ["arm6_4orient", "gantry_4orient", "arm6_planar"]
    assert [c["role"] for c in configs] == ["official", "contrast", "control"]
    assert sweep.load_configs(config_file(tmp_path, "{min_support: 0.9}"))[0]["overrides"] == {"min_support": 0.9}


def test_lever_canto_configs_match_current_h5_reference():
    configs = sweep.load_configs(ROOT / "configs/sweeps/lever_canto.yaml")
    reference = next(c["overrides"] for c in sweep.load_configs(ROOT / "configs/sweeps/h5_eval.yaml")
                     if c["name"] == "h5_rollout_l4")
    assert [c["preset"] for c in configs] == ["arm6_4orient", "lever_arm_canto", "gantry_6orient"] * 2
    assert [c["role"] for c in configs] == ["control", "lever", "contrast", "official", "lever", "contrast"]
    assert all(c["overrides"] == {} for c in configs[:3])
    assert all(c["overrides"] == reference for c in configs[3:])


@pytest.mark.parametrize("preset,forbidden,violations", [
    ("arm6_4orient", ["HxL"], 1),
    ("lever_arm_canto", [], 0),
    ("gantry_6orient", [], 0),
])
def test_worker_validates_canto_with_preset_faces(tmp_path, monkeypatch, preset, forbidden, violations):
    config = dict(name=preset, preset=preset, overrides={}, role="control")
    task = sweep.make_task(config, 1, tmp_path, "head", "source")
    assert task["forbidden_faces"] == forbidden
    item = dict(box_id=1, length_mm=600, width_mm=300, height_mm=400,
                x_mm=0, y_mm=0, z_mm=0, orientation_name="LHW", orientation_family="canto")

    def simulate(**params):
        Path(params["dump_placements_path"]).write_text("{}")
        return dict(metrics=dict(decision_timing={}, pallet_kpis=dict(deadline_cutoffs_count=0)))

    monkeypatch.setattr(sweep, "run_simulation", simulate)
    monkeypatch.setattr(sweep, "split_pallets", lambda *args: [SimpleNamespace(dest=1, index=0, items=[item])])
    monkeypatch.setattr(sweep, "original_dims_for_run", lambda *args: {1: (600, 400, 300)})
    monkeypatch.setattr(sweep, "run_kpi", lambda *args: dict(boxes_placed=1))
    monkeypatch.setattr(sweep, "accessibility_gap_violations", lambda *args: 0)
    result = sweep.run_task(task)
    assert result["forbidden_faces"] == forbidden
    assert result["n_violations"] == violations
    assert all(v["code"] == "forbidden_face" for v in result["violations"])


@pytest.mark.parametrize("overrides", ["{typo_param: 2}", "{episode_seed: 1}", "{out_path: out.json}", "[]",
                                       "{orientation_mode: all}", "{multi_ramp: true}"])
def test_invalid_override_is_error(tmp_path, overrides):
    with pytest.raises(ValueError):
        sweep.load_configs(config_file(tmp_path, overrides))


@pytest.mark.parametrize("content", ["{}", "[]", "- name: ../escape\n  preset: arm6_planar\n  role: control",
                                    "- name: test\n  preset: arm6_planar\n  role: typo"])
def test_invalid_yaml_schema(tmp_path, content):
    path = tmp_path / "bad.yaml"
    path.write_text(content)
    with pytest.raises(ValueError):
        sweep.load_configs(path)


def test_seeds_and_mutually_exclusive_args():
    assert sweep.select_seeds(3) == REFERENCE_SEEDS[:3]
    extended = sweep.select_seeds(25)
    assert extended[:10] == REFERENCE_SEEDS
    assert extended == sweep.select_seeds(25)
    assert len(set(extended)) == 25
    assert sweep.select_seeds(explicit="42, 7,1") == [42, 7, 1]
    for value in ("1,1", "", "-1", str(2**32), "1,"):
        with pytest.raises(ValueError):
            sweep.select_seeds(explicit=value)
    with pytest.raises(ValueError):
        sweep.select_seeds(0)
    with pytest.raises(SystemExit):
        sweep.build_parser().parse_args(["--configs", "x", "--seeds", "2", "--seed-list", "1"])


def task_for(tmp_path, seed=1, head="head", code_hash="code"):
    config = sweep.load_configs(config_file(tmp_path))[0]
    return sweep.make_task(config, seed, tmp_path / "out", head, code_hash)


def test_hash_and_resume_without_executing(tmp_path, monkeypatch):
    task = task_for(tmp_path)
    directory = Path(task["directory"])
    sweep.write_json(directory / "kpi.json", {"task_hash": task["task_hash"]})
    assert not sweep.cached_task(task)  # Incomplete artifacts are never reused.
    for name in ("run.json", "dump.json"):
        (directory / name).write_text("{}")
    assert sweep.cached_task(task)
    monkeypatch.setattr(sweep, "ProcessPoolExecutor", lambda **kw: pytest.fail("Must not run again"))
    assert sweep.execute_tasks([task], 2) == {"executed": 0, "skipped": 1}
    assert all(os.environ[key] == "1" for key in sweep.THREAD_ENV)
    assert not sweep.cached_task(task_for(tmp_path, head="other-head"))
    assert not sweep.cached_task(task_for(tmp_path, code_hash="edited-code"))
    changed = deepcopy(task)
    changed["params"]["min_support"] = 0.9
    assert sweep.digest(changed["params"]) != sweep.digest(task["params"])
    (directory / "kpi.json").write_text("truncated")
    assert not sweep.cached_task(task)


def test_flock_across_processes_and_release(tmp_path):
    lock = tmp_path / "lock"
    code = ("from pathlib import Path; from scripts.run_sweep import sweep_lock; "
            f"\nwith sweep_lock(Path({str(lock)!r})): pass")
    with sweep.sweep_lock(lock):
        process = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        assert process.returncode != 0
        assert "Ya hay un sweep activo" in process.stderr
    process = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    with pytest.raises(RuntimeError, match="failure"):
        with sweep.sweep_lock(lock):
            raise RuntimeError("failure")
    with sweep.sweep_lock(lock):
        pass


def synthetic_sweep(tmp_path):
    configs = [dict(name="base", role="official"), dict(name="better", role="contrast")]
    sweep.write_json(tmp_path / "manifest.json", dict(
        command="python scripts/run_sweep.py --configs synthetic.yaml", git_head="abc", source_hash="dirty",
        date="2026-10-16T00:00:00+00:00", seeds=[1, 2, 3], seed_rule=sweep.SEED_RULE,
        baseline="base", configs=configs,
        task_hashes={c["name"]: {str(s): f"{c['name']}-{s}" for s in (1, 2, 3)} for c in configs}))
    for config in configs:
        for seed in (1, 2, 3):
            value = 0.4 + 0.1 * seed + (0.05 if config["name"] == "better" else 0)
            sweep.write_json(tmp_path / config["name"] / str(seed) / "kpi.json", dict(
                task_hash=f"{config['name']}-{seed}", seed=seed, kpi=value, kpi_nominal=value * 1.1,
                n_pallets_total=4, n_closed=3, n_unplaced=0, n_violations=1,
                violations=[dict(code="support")], wall_time_s=seed * 10,
                deadline_cutoffs_count=0, decision_timing=dict(mean_ms=100, p95_ms=200)))


def test_summary_synthetic_kpi_and_paired_ci(tmp_path):
    synthetic_sweep(tmp_path)
    summary = sweep.generate_summary(tmp_path)
    base = summary["configs"]["base"]
    assert base["kpi"]["n"] == 3
    assert base["kpi"]["mean"] == pytest.approx(0.6)
    assert base["kpi"]["sd"] == pytest.approx(0.1)
    assert base["kpi"]["ci95"] == pytest.approx([0.35158623, 0.84841377])
    assert base["violations_total"] == 3
    assert base["violation_codes"] == {"support": 3}
    comparison = summary["comparisons"]["better"]
    assert comparison["paired_seeds"] == [1, 2, 3]
    assert comparison["result"]["mean_diff_pp"] == pytest.approx(5)
    assert comparison["result"]["ci95_pp"] == pytest.approx([5, 5])
    assert comparison["result"]["significant"]
    assert "2026-10-16" in (tmp_path / "summary.md").read_text()
    assert json.loads((tmp_path / "summary.json").read_text())["git_head"] == "abc"
    before = (tmp_path / "base/1/kpi.json").stat().st_mtime_ns
    subprocess.run([sys.executable, "scripts/summarize_sweep.py", str(tmp_path), "--lock-retry-seconds", "60"], cwd=ROOT, check=True)
    assert (tmp_path / "base/1/kpi.json").stat().st_mtime_ns == before


def test_undefined_kpi_and_stale_result(tmp_path):
    synthetic_sweep(tmp_path)
    path = tmp_path / "base/1/kpi.json"
    row = json.loads(path.read_text())
    row["kpi"] = row["kpi_nominal"] = None
    sweep.write_json(path, row)
    summary = sweep.generate_summary(tmp_path)
    assert summary["configs"]["base"]["kpi"]["n"] == 2
    assert summary["configs"]["base"]["undefined_kpi_seeds"] == [1]
    assert summary["comparisons"]["better"]["paired_seeds"] == [2, 3]
    row["task_hash"] = "stale"
    sweep.write_json(path, row)
    with pytest.raises(ValueError, match="obsoleto"):
        sweep.generate_summary(tmp_path)


def test_timing_is_additive_and_preserves_return_and_exception(monkeypatch):
    from palca.integration import policy_packer_sched as policy

    scheduler = policy.PolicyPackerScheduler()
    assert scheduler.collect_decision_timing()["n"] == 0
    clock = iter([1.0, 1.01, 2.0, 2.03])
    monkeypatch.setattr(policy, "perf_counter", lambda: next(clock))
    result = object()
    monkeypatch.setattr(scheduler, "_choose_action", lambda **kw: result)
    assert scheduler.choose_action(ramps={}, destinations={}, now=0) is result

    def fail(**kwargs):
        raise ValueError("same exception")

    monkeypatch.setattr(scheduler, "_choose_action", fail)
    with pytest.raises(ValueError, match="same exception"):
        scheduler.choose_action(ramps={}, destinations={}, now=0)
    timing = scheduler.collect_decision_timing()
    assert timing["durations_ms"] == pytest.approx([10, 30])
    assert timing["n"] == 2
    assert timing["mean_ms"] == pytest.approx(20)
    assert timing["p50_ms"] == pytest.approx(20)
    assert timing["p95_ms"] == pytest.approx(29)
    assert timing["max_ms"] == pytest.approx(30)


def test_integration_smoke_two_seeds_two_workers(tmp_path):
    path = config_file(tmp_path, "{excel_path: data/Flujo_smoke_60.xlsx, time_budget_ms: 30}")
    config = sweep.load_configs(path)[0]
    tasks = [sweep.make_task(config, s, tmp_path / "out", "test-head", "test-source") for s in REFERENCE_SEEDS[:2]]
    with sweep.sweep_lock(retry_seconds=60):
        assert sweep.execute_tasks(tasks, 2) == {"executed": 2, "skipped": 0}
        mtimes = []
        for task in tasks:
            directory = Path(task["directory"])
            run = json.loads((directory / "run.json").read_text())
            kpi = json.loads((directory / "kpi.json").read_text())
            timing = kpi["decision_timing"]
            assert timing == run["metrics"]["decision_timing"]
            assert timing["n"] == len(timing["durations_ms"]) >= kpi["boxes_placed"]
            assert timing["p95_ms"] >= timing["p50_ms"] >= 0
            assert kpi["n_boxes_input"] == 60
            assert kpi["n_unplaced"] == 60 - kpi["boxes_placed"]
            assert kpi["n_violations"] == len(kpi["violations"])
            assert kpi["kpi"] is not None and 0 < kpi["kpi"] <= 1
            assert kpi["worker_pid"] != os.getpid()
            assert kpi["thread_env"] == {key: "1" for key in sweep.THREAD_ENV}
            mtimes.append((directory / "kpi.json").stat().st_mtime_ns)
        assert len({json.loads((Path(t["directory"]) / "kpi.json").read_text())["worker_pid"] for t in tasks}) == 2
        assert sweep.execute_tasks(tasks, 2) == {"executed": 0, "skipped": 2}
        assert mtimes == [(Path(t["directory"]) / "kpi.json").stat().st_mtime_ns for t in tasks]


def test_lock_retry_preserves_inode_and_waits(tmp_path, monkeypatch):
    lock = tmp_path / 'retry.lock'
    calls = []
    original_flock = sweep.fcntl.flock

    def occupied_once(handle, operation):
        if operation != sweep.fcntl.LOCK_UN and not calls:
            calls.append('occupied')
            raise BlockingIOError()
        return original_flock(handle, operation)

    monkeypatch.setattr(sweep.fcntl, 'flock', occupied_once)
    monkeypatch.setattr(sweep, 'sleep', lambda seconds: calls.append(seconds))
    with sweep.sweep_lock(lock, retry_seconds=60):
        inode = lock.stat().st_ino
    assert calls == ['occupied', 60]
    assert lock.stat().st_ino == inode
