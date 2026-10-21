"""G1 synthetic-only episodes, observation encoding and frozen-policy checks."""
import builtins
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from palca.domain.box import Box
from palca.domain.placement import Placement
from palca.learning import synthetic_flows
from palca.learning.episode import OFFICIAL_KWARGS, official_policy, run_episode
from palca.learning.state_encoding import ENCODING_VERSION, encode_state
from palca.scoring.catalog_prior import sample_catalog
from palca.scoring.synthetic_value_data import training_policy


@pytest.mark.parametrize("policy,count", [("greedy", 186), ("official", 30)])
def test_determinism_no_io_and_labels(monkeypatch, policy, count):
    first = run_episode(0, "mix", policy, count=count)

    def forbidden(*args, **kwargs):
        raise AssertionError("Episode attempted file I/O")

    with monkeypatch.context() as blocked:
        for module, name in [(builtins, "open"), (io, "open"), (os, "open")]:
            blocked.setattr(module, name, forbidden)
        second = run_episode(0, "mix", policy, count=count)
    for key in first:
        np.testing.assert_array_equal(first[key], second[key], err_msg=key)
    n = len(first["util_final"])
    assert first["closed_pallets"] > 0
    assert first["placed_boxes"] == count
    assert n + first["censored_states"] == count
    assert first["censored_states"] > 0
    assert first["height"].shape == (n, 62, 42)
    assert first["boxes"].dtype == np.float32
    assert first["height"].dtype == np.uint16
    assert np.all((first["util_final"] >= 0) & (first["util_final"] <= 1))
    assert set(first["pallet_id"]) == set(range(first["closed_pallets"]))
    for pid in range(first["closed_pallets"]):
        mask = first["pallet_id"] == pid
        actions = first["placement"][mask]
        expected = np.prod(actions[:, 3:].astype(np.float64), axis=1).sum() / (1240*840*2400)
        np.testing.assert_allclose(first["util_final"][mask], expected)
        np.testing.assert_array_equal(first["cajas_hasta_cierre"][mask], np.arange(mask.sum(), 0, -1))
        np.testing.assert_array_equal(first["scalars"][mask][0, :3], 0)
    if policy == "official":
        assert "root_actions" not in first
    else:
        assert np.all((first["root_count"] >= 1) & (first["root_count"] <= 3))


def test_encoding_origin_overlap_visible_only():
    pallet = training_policy()._new_pallet()
    empty = encode_state(pallet, [])
    assert ENCODING_VERSION == "g1-v1"
    assert all(not a.any() for a in empty.values())
    origin = pallet.spec.offset_mm
    pallet.placements.append(Placement(origin, origin, 0, False, 0, 40, 60, 300))
    encoded = encode_state(pallet, [Box("x", 400, 500, 300, 0)])
    expected = np.zeros((62, 42), dtype=np.uint16)
    expected[:2, :3] = 300
    np.testing.assert_array_equal(encoded["height"], expected)
    np.testing.assert_allclose(encoded["boxes"][0], [.5, .4, .3, 1])
    assert not encoded["boxes"][1:].any()
    np.testing.assert_allclose(encoded["scalars"], [40*60*300/(1240*840*2400), .01, .125, 1/15])
    # An overlapping upper footprint raises, rather than adds to, the map.
    pallet.placements.append(Placement(origin+20, origin+20, 300, False, 0, 21, 20, 100))
    encoded = encode_state(pallet, [])
    expected[1:3, 1:2] = 400
    np.testing.assert_array_equal(encoded["height"], expected)


def test_prior_ranges_one_family_and_generic_equivalence():
    one_family = None
    for seed in range(50):
        key = hashlib.sha256(f"palca-g1-synthetic-v1:families:{seed}".encode()).digest()
        k = np.random.default_rng(int.from_bytes(key, "big")).integers(1, 13)
        boxes = synthetic_flows.synthetic_flow(seed, "families")
        sizes = {(b.length_mm, b.width_mm, b.height_mm) for b in boxes}
        for l, w, h in sizes:
            assert 350 <= l <= 750 and 300 <= w <= 500 and 250 <= h <= 500
        if k == 1:
            one_family = seed
            assert len(sizes) == 1
            break
    assert one_family is not None
    key = hashlib.sha256(b"palca-g1-synthetic-v1:generic:42").digest()
    assert synthetic_flows.synthetic_flow(42, "generic") == sample_catalog(key, 0, 186, (1,), prior="generic")
    mix_key = hashlib.sha256(b"palca-g1-synthetic-v1:mix:0").digest()
    assert synthetic_flows.synthetic_flow(0, "mix") == sample_catalog(mix_key, 0, 186, (1,), prior="generic")
    repeated = synthetic_flows.synthetic_flow(1, "mix")
    assert len({(b.length_mm, b.width_mm, b.height_mm) for b in repeated}) <= 12
    assert all(b.destination == 1 for b in boxes)


def test_flow_import_has_no_loaders():
    code = ('import sys; import palca.learning.synthetic_flows; '
            'assert not any(n in sys.modules for n in '
            '["sim.io", "pandas", "openpyxl", "palca.tuning.episodes"])')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


def test_official_configuration_matches_sim_constructor():
    # Inspect the actual call in sim.run, including its argument transforms.
    import ast
    import sim.run
    from palca.study.params import preset_params
    from inspect import signature, Parameter
    import yaml

    root = Path(__file__).resolve().parents[1]
    config = next(c for c in yaml.safe_load((root / "configs/sweeps/pulmon_tune.yaml").read_text())
                  if c["name"] == "h6_sin_gate")
    namespace = {k: p.default for k, p in signature(sim.run.run_simulation).parameters.items()
                 if p.default is not Parameter.empty}
    namespace.update(preset_params(config["preset"], **config["overrides"]))
    tree = ast.parse(Path(sim.run.__file__).read_text())
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "from_defaults")
    expected = {kw.arg: eval(compile(ast.Expression(kw.value), "sim_constructor", "eval"), namespace)
                for kw in call.keywords}
    assert OFFICIAL_KWARGS == expected
    assert official_policy().config.scheduler.micro_plan_node_budget == 57


def test_epsilon_validation():
    with pytest.raises(ValueError, match="greedy-only"):
        run_episode(1, "mix", "official", epsilon=.1, count=0)
    with pytest.raises(ValueError):
        run_episode(1, "mix", "greedy", epsilon=2, count=0)


def test_censored_empty_shapes():
    result = run_episode(2, "generic", "greedy", count=1)
    assert result["height"].shape == (0, 62, 42)
    assert result["closed_pallets"] == 0
    assert result["censored_states"] == 1


def test_cli_resume_manifest_and_output_guard(tmp_path, monkeypatch, capsys):
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("gen_value_data", root / "scripts/gen_value_data.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    class InlinePool:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def map(self, worker, tasks):
            return map(worker, tasks)

    calls = []

    def small_episode(seed, prior, policy, epsilon):
        calls.append(seed)
        return run_episode(seed, prior, policy, epsilon, count=1)

    import palca.learning.episode
    monkeypatch.setattr(palca.learning.episode, "run_episode", small_episode)
    monkeypatch.setattr(cli, "ProcessPoolExecutor", InlinePool)
    args = ["--policy", "greedy", "--prior", "mix", "--episodes", "1", "--workers", "1"]
    with pytest.raises(SystemExit):
        cli.main(args + ["--out", str(root / "outputs/g1")])
    out = tmp_path / "external"
    cli.main(args + ["--out", str(out)])
    assert calls == [0]
    before = (out / "ep_mix_0.npz").read_bytes()
    cli.main(args + ["--out", str(out)])
    assert calls == [0]
    assert (out / "ep_mix_0.npz").read_bytes() == before
    import json
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["encoding_version"] == ENCODING_VERSION
    assert manifest["totals"]["episodes"] == 1
    assert manifest["totals"]["censored_states"] == 1
    assert manifest["runs"][-1]["skipped"] == 1
    assert manifest["source_sha256"]
    assert os.environ["OMP_NUM_THREADS"] == "1"
    with pytest.raises(SystemExit):
        cli.main(args + ["--out", str(out), "--epsilon", "0.1"])


def test_greedy_exploration_is_seeded_and_uses_alternative_roots():
    first = run_episode(0, "generic", "greedy", epsilon=1, count=30)
    second = run_episode(0, "generic", "greedy", epsilon=1, count=30)
    assert len(first["util_final"]) > 0
    for key in first:
        np.testing.assert_array_equal(first[key], second[key], err_msg=key)
    mask = first["root_count"] > 1
    assert mask.any()
    # Exploration always chooses a different plan among the retained top three.
    chosen = np.column_stack((first["belt_index"], first["placement"]))
    assert np.all(np.any(chosen[mask] != first["root_actions"][mask, 0], axis=1))
    assert np.all(np.any(np.all(chosen[:, None] == first["root_actions"], axis=2), axis=1))
