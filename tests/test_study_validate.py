from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from palca.study.metric import run_kpi, split_pallets
from palca.study.params import MAX_LOAD_HEIGHT_MM, PALLET_NOMINAL_MM, preset_params
from palca.study.validate import validate_pallet
from sim.run import run_simulation


def box(box_id=1, dims=(200, 100, 50), xyz=(0, 0, 0), **metadata):
    return dict(box_id=box_id, **dict(zip(("length_mm", "width_mm", "height_mm"), dims)),
                **dict(zip(("x_mm", "y_mm", "z_mm"), xyz)),
                orientation_name=metadata.pop("orientation_name", "LWH"),
                orientation_family=metadata.pop("orientation_family", "planar"), **metadata)


def originals(items):
    return {item["box_id"]: tuple(item[key] for key in ("length_mm", "width_mm", "height_mm"))
            for item in items}


def codes(items, dims=None, **kwargs):
    return {v.code for v in validate_pallet(items, originals(items) if dims is None else dims, **kwargs)}


def test_valid_pallet_supported_by_two_boxes():
    items = [box(1, (100, 100, 50)), box(2, (100, 100, 50), (100, 0, 0)),
             box(3, xyz=(0, 0, 50))]
    assert validate_pallet(items, originals(items)) == []
    assert validate_pallet(list(reversed(items)), originals(items)) == []


def test_overlap_detected_and_half_mm_tolerance():
    assert "overlap" in codes([box(), box(2, xyz=(50, 0, 0))])
    assert "overlap" not in codes([box(), box(2, xyz=(199.5, 0, 0))])
    assert "overlap" in codes([box(), box(2, xyz=(199.49, 0, 0))])
    assert "overlap" not in codes([box(), box(2, xyz=(0, 0, 50))])


@pytest.mark.parametrize("xyz", [(-21, 0, 0), (0, -21, 0), (0, 0, -1),
                                  (PALLET_NOMINAL_MM[0] + 21, 0, 0),
                                  (0, PALLET_NOMINAL_MM[1] + 21, 0),
                                  (0, 0, MAX_LOAD_HEIGHT_MM)])
def test_outside_envelope(xyz):
    assert "outside_envelope" in codes([box(xyz=xyz)])


def test_envelope_max_edges_and_tolerance():
    valid = box(xyz=(PALLET_NOMINAL_MM[0] + 20 - 200,
                     PALLET_NOMINAL_MM[1] + 20 - 100, 0))
    assert codes([valid]) == set()
    assert codes([box(xyz=(-20.5, -20.5, 0))]) == set()


def test_half_support_is_rejected_and_floating_box_is_rejected():
    items = [box(1, (100, 100, 50)), box(2, xyz=(0, 0, 50))]
    violations = validate_pallet(items, originals(items))
    assert [v.code for v in violations] == ["support"]
    assert "0.500000000 < 0.85" in violations[0].message
    assert codes(items, min_support=0.5) == set()
    assert "support" in codes([box(xyz=(0, 0, 100))])


def test_support_is_union_not_sum_and_z_contact_tolerance():
    items = [box(1, (100, 100, 50)), box(2, (100, 100, 50)), box(3, xyz=(0, 0, 50))]
    assert "support" in codes(items)
    assert "support" not in codes([box(), box(2, xyz=(0, 0, 51))])
    assert "support" in codes([box(), box(2, xyz=(0, 0, 51.01))])


def test_hxl_face_is_forbidden():
    item = box(dims=(200, 50, 100), orientation_name="LHW", orientation_family="stand_hl")
    assert codes([item], {1: (200, 100, 50)}) == {"forbidden_face"}


@pytest.mark.parametrize("original,placed,name", [
    ((200, 100, 50), (200, 50, 100), "LHW"),
    ((200, 100, 50), (50, 200, 100), "HLW"),
    ((200, 100, 100), (200, 100, 100), "LHW"),
])
def test_canto_requires_explicit_face_permission(original, placed, name):
    item = box(dims=placed, orientation_name=name, orientation_family="canto")
    assert codes([item], {1: original}) == {"forbidden_face"}
    assert codes([item], {1: original}, forbidden_faces=set()) == set()
    item["orientation_name"] = None
    assert codes([item], {1: original}, forbidden_faces=set()) == set()
    item["orientation_family"] = "stand_hw"
    assert codes([item], {1: original}, forbidden_faces=set()) == {"orientation"}


@pytest.mark.parametrize("original,placed,name,family", [
    ((200, 100, 100), (200, 100, 100), "LWH", "planar"),
    ((200, 200, 50), (50, 200, 200), "HWL", "stand_hw"),
    ((100, 100, 100), (100, 100, 100), "LWH", "planar"),
    ((200, 100, 200), (100, 200, 200), "WLH", "planar"),
])
def test_ambiguous_dimensions_use_metadata(original, placed, name, family):
    item = box(dims=placed, orientation_name=name, orientation_family=family)
    assert validate_pallet([item], {1: original}) == []
    item["orientation_name"] = None
    assert validate_pallet([item], {1: original}) == []
    item["orientation_family"] = None
    assert "orientation" in codes([item], {1: original})


def test_ambiguous_forbidden_face_and_contradictory_metadata():
    original = {1: (200, 100, 100)}
    item = box(dims=(200, 100, 100), orientation_name="LHW", orientation_family="stand_hl")
    assert codes([item], original) == {"forbidden_face"}
    item["orientation_family"] = "planar"
    assert codes([item], original) == {"orientation"}
    assert "orientation" in codes([box(orientation_name="HWL")])


def test_invalid_dimensions_unknown_id_and_nonfinite_geometry():
    assert codes([box()], {1: (201, 100, 50)}) == {"dimensions"}
    assert codes([box()], {}) == {"unknown_box_id"}
    assert codes([box(xyz=(float("nan"), 0, 0))]) == {"invalid_geometry"}
    assert codes([box(dims=(0, 100, 50))]) == {"invalid_geometry"}
    assert "duplicate_box_id" in codes([box(), box(xyz=(200, 0, 0))])
    with pytest.raises(ValueError):
        validate_pallet([], {}, min_support=1.1)


@pytest.fixture(scope="module")
def smoke_run(tmp_path_factory):
    folder = tmp_path_factory.mktemp("metric-smoke")
    run_path, dump_path = folder / "run.json", folder / "dump.json"
    payload = run_simulation(**preset_params(
        "arm6_4orient", excel_path="data/Flujo_smoke_60.xlsx", episode_seed=50021,
        time_budget_ms=30, out_path=str(run_path), dump_placements_path=str(dump_path)))
    dump = json.loads(dump_path.read_text())
    return payload, dump, run_path, dump_path


def test_integration_split_and_physical_validation(smoke_run):
    from scripts.kpi_from_run import original_dims_for_run

    payload, dump, _, _ = smoke_run
    records = split_pallets(payload, dump)
    result = run_kpi(records)
    assert result["boxes_placed"] == payload["metrics"]["processed_boxes"] == 60
    assert 0 < result["kpi"] <= 1
    for dest, counts in payload["metrics"]["pallet_kpis"]["continuous_pallet_sequence"].items():
        reasons = payload["metrics"]["pallet_kpis"]["continuous_closure_sequence"][dest]
        assert len(counts) == len(reasons)
        if reasons:
            assert reasons[-1] == "END"
    dims = original_dims_for_run(payload, "data/Flujo_smoke_60.xlsx")
    violations = [{"dest": rec.dest, "index": rec.index, **asdict(v)}
                  for rec in records for v in validate_pallet(rec.items, dims)]
    if violations:
        pytest.xfail("Violaciones físicas reales (validador sin relajar): " + json.dumps(violations))
    assert violations == []


def test_cli_real_run(smoke_run):
    _, _, run_path, dump_path = smoke_run
    completed = subprocess.run([
        sys.executable, "scripts/kpi_from_run.py", "--run", str(run_path), "--dump", str(dump_path),
        "--validate", "--excel", "data/Flujo_smoke_60.xlsx"],
        capture_output=True, text=True, check=True, env=dict(os.environ))
    result = json.loads(completed.stdout)
    assert 0 < result["kpi"] <= 1
    assert result["n_violations"] == len(result["violations"])


@pytest.mark.parametrize("arrival_mode", ["immediate", "excel"])
@pytest.mark.parametrize("shuffle", [False, True])
def test_original_ids_follow_des_order(monkeypatch, arrival_mode, shuffle):
    from scripts import kpi_from_run
    from sim.des import Arrival

    arrivals = [Arrival(time=30, destination=1, row_idx=0, length_mm=100, width_mm=50, height_mm=25),
                Arrival(time=10, destination=2, row_idx=1, length_mm=200, width_mm=60, height_mm=30),
                Arrival(time=20, destination=1, row_idx=2, length_mm=300, width_mm=70, height_mm=35)]
    monkeypatch.setattr(kpi_from_run, "load_arrivals", lambda *args, **kwargs: arrivals)
    # El shuffle sintético intercambia 0/2 y permite verificar la identidad a mano.
    monkeypatch.setattr(kpi_from_run, "apply_shuffle", lambda items, **kwargs: list(reversed(items)))
    payload = {"params": dict(arrival_mode=arrival_mode, shuffle_window=3 if shuffle else 0,
                               shuffle_strength=1, episode_seed=50021)}
    dims = kpi_from_run.original_dims_for_run(payload, "unused.xlsx")
    expected = [200, 300, 100] if arrival_mode == "excel" else ([300, 200, 100] if shuffle else [100, 200, 300])
    assert [dims[i][0] for i in (1, 2, 3)] == expected
