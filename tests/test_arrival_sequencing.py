"""Secuenciación aguas arriba; política y restricciones físicas intactas."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from palca.study.metric import run_kpi, split_pallets
from palca.study.params import preset_params
from palca.tuning.episodes import apply_shuffle
from scripts.kpi_from_run import original_dims_for_run
from scripts.run_sweep import load_configs
from sim import run
from sim.arrival_sequence import sort_arrival_windows
from sim.des import Arrival


def arrivals():
    # Dominante en ambos extremos inclusivos, empate estable, resto más alto.
    dims = [(610, 450, 395), (700, 500, 500), (600, 440, 345),
            (605, 445, 395), (599, 445, 400), (605, 445, 396), (610, 451, 390)]
    return [Arrival(i * 3, i % 6 + 1, i, *d, weight_kg=i + 1, priority=i / 10)
            for i, d in enumerate(dims)]


@pytest.mark.parametrize("key,order", [
    ("family_height", [0, 3, 2, 1, 4, 5, 6]),
    ("height_desc", [1, 4, 5, 0, 3, 6, 2]),
    ("volume_desc", [1, 0, 6, 4, 5, 3, 2]),
])
@pytest.mark.parametrize("window", [7, 186])
def test_whole_stream_stable_sort(key, order, window):
    source = arrivals()
    before = list(source)
    result = sort_arrival_windows(source, window=window, key=key)
    assert [a.weight_kg - 1 for a in result] == order
    assert [a.time for a in result] == [a.time for a in source]
    assert [a.row_idx for a in result] == list(range(1, 8))
    assert result == [replace(source[j], row_idx=i + 1, time=source[i].time)
                      for i, j in enumerate(order)]
    assert source == before


@pytest.mark.parametrize("key", ["family_height", "height_desc", "volume_desc"])
def test_blocks_do_not_cross_and_partial_tail(key):
    source = arrivals()
    result = sort_arrival_windows(source, window=3, key=key)
    for start in range(0, len(source), 3):
        expected = sort_arrival_windows(source[start:start + 3], window=186, key=key)
        assert [a.weight_kg for a in result[start:start + 3]] == [a.weight_kg for a in expected]


@pytest.mark.parametrize("window", [0, 1])
def test_disabled_is_exact_identity_even_without_dimensions(window):
    source = [Arrival(30, 1, 9), Arrival(10, 2, 4)]
    assert sort_arrival_windows(source, window=window) is source


@pytest.mark.parametrize("kwargs", [dict(window=-1), dict(window=2.5), dict(window=True),
                                    dict(key="typo")])
def test_invalid_sort_parameters(kwargs):
    with pytest.raises(ValueError):
        sort_arrival_windows(arrivals(), **kwargs)


def test_enabled_requires_original_dimensions():
    with pytest.raises(ValueError, match="dimensiones originales"):
        sort_arrival_windows([Arrival(0, 1, 0)], window=15)


@pytest.mark.parametrize("mode", ["immediate", "excel"])
@pytest.mark.parametrize("window", [0, 3, 186])
@pytest.mark.parametrize("shuffle_window", [0, 4])
def test_simulation_sorts_after_shuffle_and_reconstructs_ids(monkeypatch, mode, window, shuffle_window):
    source = list(reversed(arrivals()))  # loader returns timestamp order, not necessarily row order
    source = [replace(a, time=18 - a.time) for a in source]
    monkeypatch.setattr(run, "load_arrivals", lambda *a, **kw: source)
    monkeypatch.setattr("scripts.kpi_from_run.load_arrivals", lambda *a, **kw: source)
    captured = []

    def simulate(items, config, **kwargs):
        captured.extend(items)
        return SimpleNamespace(to_dict=lambda: {})

    monkeypatch.setattr(run, "simulate", simulate)
    payload = run.run_simulation(
        excel_path="unused.xlsx", model="M1", n_per_pallet=24,
        t_pick_place=14, staging_cap=0, out_path=None, arrival_mode=mode,
        episode_seed=42, shuffle_window=shuffle_window, shuffle_strength=1,
        arrival_sort_window=window, arrival_sort_key="height_desc")
    # Reconstruct the legacy shuffle independently (including its renumbering).
    shuffled = source
    if shuffle_window:
        shuffled = [replace(a, row_idx=i) for i, a in enumerate(
            apply_shuffle(sorted(source, key=lambda a: a.row_idx), seed=42,
                          window=shuffle_window, strength=1), start=1)]
    if window:
        slots = sorted(shuffled, key=(lambda a: a.row_idx) if mode == "immediate"
                       else (lambda a: (a.time, a.row_idx)))
        boxes = [a for start in range(0, len(slots), window)
                 for a in sorted(slots[start:start + window], key=lambda a: -a.height_mm)]
        expected = [replace(a, row_idx=i + 1, time=slots[i].time) for i, a in enumerate(boxes)]
    else:
        expected = shuffled
    assert captured == expected
    actual_order = sorted(captured, key=(lambda a: a.row_idx) if mode == "immediate"
                          else (lambda a: (a.time, a.row_idx)))
    assert original_dims_for_run(payload, "unused.xlsx") == {
        i: (a.length_mm, a.width_mm, a.height_mm) for i, a in enumerate(actual_order, start=1)}
    assert payload["params"]["arrival_sort_window"] == window
    assert payload["params"]["arrival_sort_key"] == "height_desc"


@pytest.mark.parametrize("explicit_zero", [False, True])
def test_zero_preserves_pre_change_placements_and_kpi(tmp_path, explicit_zero):
    # Golden captured before this change: real 60-box fixture, seed=42, arm6.
    path = tmp_path / "dump.json"
    params = preset_params("arm6_4orient", excel_path="data/Flujo_smoke_60.xlsx",
                           episode_seed=42, dump_placements_path=str(path))
    if explicit_zero:
        params["arrival_sort_window"] = 0
    payload = run.run_simulation(**params)
    dump = json.loads(path.read_text())
    assert run_kpi(split_pallets(payload, dump))["kpi"] == 0.6427499526636371
    assert hashlib.sha256(json.dumps(dump["pallets"], sort_keys=True).encode()).hexdigest() == (
        "1bb8c4b7ac062d03b11214b57637ff21462338d4b09cb26f2d1ffdaae015fede")


def test_sweep_matrix_matches_h6_and_keeps_online_buffer():
    root = Path(__file__).resolve().parents[1]
    configs = load_configs(root / "configs/sweeps/lever_sequencing.yaml")
    reference = next(c["overrides"] for c in load_configs(root / "configs/sweeps/h6_prior_eval.yaml")
                     if c["name"] == "h6_generic")
    assert len(configs) == 12
    for c in configs[:9]:
        overrides = {k: v for k, v in c["overrides"].items() if not k.startswith("arrival_sort_")}
        assert overrides == reference
        params = preset_params(c["preset"], **c["overrides"])
        assert params["ramp_cap"] == params["lookahead_k"] == 15
    assert {(c["overrides"]["arrival_sort_window"], c["overrides"]["arrival_sort_key"])
            for c in configs[1:9]} == {(w, k) for w in (15, 30, 60, 186)
                                     for k in ("family_height", "height_desc")}
    assert [c["overrides"]["arrival_sort_window"] for c in configs[9:]] == [0, 30, 186]
    args = run.build_parser().parse_args(["--excel", "unused.xlsx"])
    assert (args.arrival_sort_window, args.arrival_sort_key) == (0, "family_height")
    args = run.build_parser().parse_args(["--excel", "unused.xlsx", "--arrival-sort-window", "30",
                                         "--arrival-sort-key", "volume_desc"])
    assert (args.arrival_sort_window, args.arrival_sort_key) == (30, "volume_desc")
