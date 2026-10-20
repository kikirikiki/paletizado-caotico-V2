"""R13 synthetic provenance, geometry value, integration and D-022 boundary."""
import builtins
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from palca.scheduler.scheduler_v1 import (
    SchedulerConfig, SchedulerRampState, SchedulerSimState, SchedulerV1,
    _BeamNode, _BeamRampState,
)
from palca.scoring import learned_value as value
from palca.scoring import synthetic_value_data as data
from scripts.run_sweep import load_configs


def policy():
    base = data.training_policy()
    base._scheduler = SchedulerV1(replace(base._scheduler.config,
        micro_plan_enabled=True, micro_plan_node_budget=57, micro_plan_hard_cap_ms=600000,
        micro_plan_value="rollout+learned", micro_plan_rollout_leaves=2,
        rollout_future_boxes=6, rollout_prior="generic"))
    return base


def test_generic_stream_reproducible_independent_of_catalog(monkeypatch):
    from palca.scoring import catalog_prior
    monkeypatch.setattr(catalog_prior, "CATALOG", ())
    first = data.synthetic_stream(1300001)
    assert len(first) == 186
    assert first == data.synthetic_stream(1300001)
    assert first != data.synthetic_stream(1300002)
    assert all(350 <= b.length_mm <= 750 and 300 <= b.width_mm <= 500
               and 250 <= b.height_mm <= 500 for b in first)


def test_synthetic_imports_cannot_access_evaluated_data():
    code = '''
import builtins, sys
def audit(event, args):
    if event == "open":
        path = str(args[0]).lower()
        assert not (path.endswith((".xlsx", ".csv")) or "/out/" in path or "/data/" in path), path
sys.addaudithook(audit)
original = builtins.__import__
def guarded(name, *args, **kwargs):
    assert not name.startswith(("sim", "pandas", "openpyxl", "palca.study")), name
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from palca.scoring.synthetic_value_data import synthetic_stream, training_policy
assert len(synthetic_stream(1380000)) == 186
assert training_policy()._new_pallet().placements == []
'''
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


@pytest.mark.slow
def test_d032_entire_episode_never_opens_any_file(monkeypatch):
    """Fail on *any* file read, including Excel/CSV, out/, configs and dumps."""
    def forbidden(*args, **kwargs):
        raise AssertionError(f"Synthetic generator attempted file I/O: {args[:1]}")
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(io, "open", forbidden)  # includes Path.read_text/bytes
    monkeypatch.setattr("os.open", forbidden)
    episode = data.generate_episode(1380000)
    assert episode["placed_boxes"] == 186
    assert episode["closed_pallets"] > 1
    assert episode["censored_states"] > 0
    assert episode["x"].shape == (len(episode["y"]), len(value.FEATURE_NAMES))
    for pid in np.unique(episode["pallet_ids"]):
        mask = episode["pallet_ids"] == pid
        x, y = episode["x"][mask], episode["y"][mask]
        assert np.all(y == y[0])
        assert x[0, value.FEATURE_NAMES.index("box_count_32")] == 0
        assert x[-1, value.FEATURE_NAMES.index("placed_volume")] == pytest.approx(y[0])


def test_geometry_features_wells_and_bounds():
    p = data.training_policy()._new_pallet()
    empty = value.state_features(p)
    assert empty.shape == (len(value.FEATURE_NAMES),)
    assert not empty.any()
    p.placements.append(SimpleNamespace(x_mm=-20, y_mm=-20, z_mm=0,
        length_mm=620, width_mm=420, height_mm=1200))
    p.placements.append(SimpleNamespace(x_mm=600, y_mm=-20, z_mm=0,
        length_mm=620, width_mm=420, height_mm=300))
    f = dict(zip(value.FEATURE_NAMES, value.state_features(p)))
    assert f["floor_coverage"] == .5
    assert f["max_height"] == .5
    assert f["debt_volume"] > 0 and f["mid_well_volume"] > 0
    before = list(p.placements)
    prediction = value.predict_utilization(p)
    assert value.pallet_volume(p) / value.envelope_volume(p) <= prediction <= 1
    assert prediction == value.predict_utilization(p)
    assert p.placements == before


def test_model_disjoint_flow_splits_and_schema():
    model = json.loads(value.MODEL_PATH.read_text())
    assert model["feature_names"] == list(value.FEATURE_NAMES)
    assert all(not array.flags.writeable for array in value.load_model()[:3])
    training = model["training"]
    assert training["source"] == "synthetic_generic_only"
    seeds = [set(training["splits"][name]["seeds"]) for name in ("train", "validation", "test")]
    assert all(not seeds[i] & seeds[j] for i in range(3) for j in range(i))
    assert training["provenance"]["boxes"] == 186
    assert training["workers"] <= 14
    for name in ("validation", "test"):
        assert np.isfinite(training["splits"][name]["r2"])
        assert 0 <= training["splits"][name]["mae"] <= 1


def test_sweep_cache_changes_when_model_changes(tmp_path, monkeypatch):
    from scripts import run_sweep
    monkeypatch.setattr(run_sweep, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "requirements.txt").write_text("")
    models = tmp_path / "src/palca/scoring/models"
    models.mkdir(parents=True)
    artifact = models / "r13_generic_v1.json"
    artifact.write_text('{"coef": [1]}')
    before = run_sweep.source_hash()
    artifact.write_text('{"coef": [2]}')
    assert before != run_sweep.source_hash()


@pytest.mark.parametrize("mode", ["learned", "rollout+learned"])
def test_clock_and_hidden_metadata_cannot_affect_learned_choice(mode, monkeypatch):
    results = []
    class ForbiddenUpstream:
        def __iter__(self):
            pytest.fail("read hidden real upstream")
    for step in (0, .001):
        clock = [0.]
        def now():
            clock[0] += step
            return clock[0]
        monkeypatch.setattr("palca.scheduler.scheduler_v1.time.perf_counter", now)
        p = policy()
        p._scheduler.config = replace(p._scheduler.config, micro_plan_value=mode,
                                     rollout_future_boxes=6 if mode == "rollout+learned" else 0)
        boxes = data.synthetic_stream(1300200, 5)
        pallet = p._new_pallet()
        state = SchedulerSimState(0, {1: boxes}, {1: pallet}, set(),
            ramp_states={1: SchedulerRampState(tuple(boxes), ForbiddenUpstream(), 15)})
        plan = p._scheduler.choose_action(state)
        stats = p._scheduler.last_micro_plan_stats
        assert plan is not None and plan.preview.feasible
        assert pallet.placements == []
        assert stats["nodes_expanded"] <= 57 and stats["hard_cap_hits"] == 0
        assert stats["learned_model"] == "r13_generic_v1"
        results.append((plan, stats["nodes_expanded"], stats["state_value"]))
    assert results[0] == results[1]


def test_value_units_do_not_double_count_and_zero_mix_is_exact(monkeypatch):
    p = data.training_policy()._new_pallet()
    p.placements.append(SimpleNamespace(x_mm=-20, y_mm=-20, z_mm=0,
        length_mm=620, width_mm=420, height_mm=300))
    monkeypatch.setattr(value, "predict_utilization", lambda p: .7)
    engine = SchedulerV1(SchedulerConfig(micro_plan_value="learned", learned_value_weight=1))
    node = _BeamNode({1: _BeamRampState([], [], 15)}, {1: p})
    engine._set_state_value(node)
    assert node.state_value == pytest.approx(.7 * value.envelope_volume(p))
    engine.config = replace(engine.config, learned_value_weight=.5)
    engine._set_state_value(node)
    assert node.state_value == pytest.approx((value.pallet_volume(p) + .7 * value.envelope_volume(p))/2)
    # Both rollout implementations ignore learned inference at zero mix weight.
    monkeypatch.setattr(value, "remaining_value", lambda p: pytest.fail("zero weight loads model"))
    original = SchedulerV1(SchedulerConfig(micro_plan_value="rollout"))
    mixed = SchedulerV1(SchedulerConfig(micro_plan_value="rollout+learned", learned_value_weight=0))
    state = SchedulerSimState(0, {}, {1:p}, set())
    assert original._rollout_value(node, state, 2, None) == mixed._rollout_value(node, state, 2, None)
    for e in (original, mixed):
        e.config = replace(e.config, micro_plan_node_budget=57, rollout_future_boxes=1, rollout_prior="generic")
    assert original._stochastic_rollout_value(node, state, 2, None, b"test") == mixed._stochastic_rollout_value(node, state, 2, None, b"test")


@pytest.mark.parametrize("weight", [-1, float("inf"), float("nan")])
def test_bad_weights_rejected(weight):
    with pytest.raises(ValueError):
        SchedulerConfig(learned_value_weight=weight)


def test_config_propagation_and_official_defaults(monkeypatch):
    from palca.integration.policy_packer_sched import PolicyPackerScheduler
    configs = load_configs(Path("configs/sweeps/r13_tune.yaml"))
    assert len(configs) == 5 and configs[0]["role"] == "official"
    assert configs[0]["overrides"] == dict(
        load_configs(Path("configs/sweeps/h6_sin_gate.yaml"))[1]["overrides"],
        micro_plan_hard_cap_ms=600000)
    assert [c["overrides"].get("learned_value_weight") for c in configs[1:]] == [1., .5, 1., 2.]
    assert [c["overrides"]["micro_plan_node_budget"] for c in configs] == [57, 57, 27, 27, 27]
    for c in configs:
        assert c["overrides"]["stand_hw_height_margin_gate_mm"] == 2400
        assert c["overrides"]["micro_plan_hard_cap_ms"] == 600000
    p = PolicyPackerScheduler.from_defaults(micro_plan_value="learned", learned_value_weight=.5)
    assert p.config.learned_value_weight == p._scheduler.config.learned_value_weight == .5
    monkeypatch.setattr(value, "remaining_value", lambda p: pytest.fail("official reads model"))
    assert SchedulerV1(SchedulerConfig(micro_plan_value="rollout"))._learned_tail_value({}) == 0


@pytest.mark.slow
@pytest.mark.parametrize("mode,node_budget", [
    pytest.param("learned", 57, id="learned"),
    pytest.param("rollout+learned", 57, id="mix_full"),
    pytest.param("rollout+learned", 27, id="mix_bounded"),
])
def test_d022_identical_prefix_100_suffix_permuted(mode, node_budget, tmp_path):
    """Full 186-box GENERIC flow; compare every decision until first changed arrival."""
    flow = data.synthetic_stream(1380100)
    changed = flow[:100] + list(reversed(flow[100:]))
    policies = [policy() for _ in range(2)]
    for p in policies:
        p._scheduler.config = replace(p._scheduler.config, micro_plan_value=mode,
            micro_plan_node_budget=node_budget,
            rollout_future_boxes=6 if mode == "rollout+learned" else 0)
    queues = [list(flow[:15]), list(changed[:15])]
    upstreams = [list(flow[15:]), list(changed[15:])]
    pallets = [p._new_pallet() for p in policies]
    picks, closures, future_nodes = 0, 0, 0
    while queues[0] == queues[1]:
        plans = []
        for i, p in enumerate(policies):
            state = SchedulerSimState(picks, {1: queues[i]}, {1: pallets[i]}, set(),
                ramp_states={1: SchedulerRampState(tuple(queues[i]), tuple(upstreams[i]), 15)},
                remaining_total=len(queues[i])+len(upstreams[i]))
            plans.append(p._scheduler.choose_action(state))
            assert p._scheduler.hard_cap_hits == 0
            assert p._scheduler.last_micro_plan_stats["nodes_expanded"] <= node_budget
            future_nodes += p._scheduler.last_micro_plan_stats.get("rollout_future_nodes", 0)
        assert plans[0] == plans[1]
        if plans[0] is None:
            assert pallets[0].placements
            pallets = [p._new_pallet() for p in policies]
            closures += 1
            continue
        for i, plan in enumerate(plans):
            pallets[i].commit_place(plan.preview)
            queues[i].pop(plan.buffer_index)
            queues[i].append(upstreams[i].pop(0))
        picks += 1
    assert picks == 86 and closures >= 1
    assert queues[0][-1] == flow[100] and queues[1][-1] == flow[-1]
    if mode == "rollout+learned":
        assert future_nodes > 0
    (tmp_path / f"d022_{mode}.json").write_text(json.dumps(dict(
        mode=mode, node_budget=node_budget, identical_picks=picks, closures=closures,
        future_nodes=future_nodes)))


@pytest.mark.slow
@pytest.mark.parametrize("seed", [50021, 1])
def test_disabled_model_matches_official_pallets(seed, tmp_path, monkeypatch):
    from palca.study.params import preset_params
    from scripts.run_sweep import sweep_lock
    from sim.run import run_simulation
    monkeypatch.setattr(value, "load_model", lambda: pytest.fail("official loads R13 model"))
    config = load_configs(Path("configs/sweeps/r13_tune.yaml"))[0]
    dump_path = tmp_path / "dump.json"
    with sweep_lock():
        payload = run_simulation(**preset_params(config["preset"], **config["overrides"],
            episode_seed=seed, multi_ramp_seed=seed, dump_placements_path=str(dump_path)))
    dump = json.loads(dump_path.read_text())
    reference = Path("/home/ingen/code/Paletizador/09_Software/paletizado-autopilot/out/sweeps/h6_sin_gate_ref/h6_sin_gate") / str(seed) / "dump.json"
    if reference.exists():
        assert dump["pallets"] == json.loads(reference.read_text())["pallets"]
    else:
        fixture = json.loads(Path("tests/fixtures/h10_official.json").read_text())
        assert hashlib.sha256(json.dumps(dump["pallets"], sort_keys=True).encode()).hexdigest() == fixture["seeds"][str(seed)]["pallets_sha256"]
    assert payload["metrics"]["pallet_kpis"]["hard_cap_hits"] == 0
