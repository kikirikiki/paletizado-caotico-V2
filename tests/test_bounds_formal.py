from fractions import Fraction
import json
from math import prod
from pathlib import Path

import numpy as np
import pytest

from palca.study.bounds_formal import (
    DFF, SCALE, build_dff_system, certify_dual, dff_grid, horizontal_bound,
    is_dominant, orientations, pallet_bound,
)
from palca.study.metric import ENVELOPE_MM, pallet_utilization, split_pallets
from palca.study.validate import validate_pallet
from scripts.bound_formal import moonshot, read_baseline


@pytest.mark.parametrize("function", dff_grid(), ids=lambda f: f.name)
def test_each_dff_is_dual_feasible_for_all_multisets_on_grid(function):
    # Unbounded knapsack DP checks EVERY multiset summing to <=1 on the grid,
    # not only pairs. Include all epsilon breaks and every u(k) equality.
    size = 4 * (int(function.parameter) + 1) if function.family == "u" else 120
    values = [function(Fraction(i, size)) for i in range(size + 1)]
    assert values[0] == 0 and values[-1] == 1
    assert all(0 <= v <= 1 for v in values)
    best = [Fraction(0)] * (size + 1)
    for capacity in range(1, size + 1):
        best[capacity] = max(best[capacity - amount] + values[amount]
                             for amount in range(1, capacity + 1))
        assert best[capacity] <= 1


def test_dff_discontinuities_are_exact():
    tiny = Fraction(1, 10**12)
    for k in range(1, 21):
        f = DFF("u", Fraction(k))
        for j in range(1, k + 1):
            x = Fraction(j, k + 1)
            assert f(x) == x
            assert f(x - tiny) == Fraction(j - 1, k)
            assert f(x + tiny) == Fraction(j, k)
    for j in range(1, 20):
        eps = Fraction(j, 40)
        f = DFF("U", eps)
        assert f(eps - tiny) == 0
        assert f(eps) == eps
        assert f(1 - eps) == 1 - eps
        assert f(1 - eps + tiny) == 1
        phi = DFF("phi", eps)
        assert phi(eps - tiny) == 0
        assert phi(eps) == Fraction(1, (1 / eps).__floor__())


def test_orientation_contract_and_original_family_axes():
    assert set(orientations((605, 445, 355))) == {
        (605, 445, 355), (445, 605, 355), (355, 445, 605), (445, 355, 605)}
    assert len(orientations((10, 10, 10), (10, 10, 10))) == 1
    assert orientations((100, 200, 300), (10, 10, 10)) == ()
    assert is_dominant((600, 440, 345))
    assert is_dominant((610, 450, 395))
    assert not is_dominant((445, 605, 355))


def test_downward_quantization_never_strengthens_dff():
    dims = (605, 445, 355)
    system = build_dff_system([dims])
    # Every generated row must be <= its unrounded conservative scale.
    axes = []
    for axis, cap in enumerate(ENVELOPE_MM):
        axes.append({tuple((f(Fraction(o[axis], cap)) * SCALE).__floor__()
                           for o in system.oriented) for f in dff_grid()})
    expected = {tuple(x * y * z // SCALE**2 for x, y, z in zip(a, b, c))
                for a in axes[0] for b in axes[1] for c in axes[2]}
    assert all(tuple(row) in expected for row in system.rows)
    assert all(0 <= n <= SCALE for row in system.rows for n in row)


def test_exact_dual_certificate_repairs_underestimated_and_negative_duals():
    rows = np.array([[SCALE, SCALE]])
    objective = [Fraction(1, 2), Fraction(3, 4)]
    for multipliers in ([0.5], [-0.1], [0.749999999], [2.0]):
        bound, certificate = certify_dual(rows, [1], objective, [1, 1], multipliers)
        assert bound >= Fraction(3, 4)
        assert certificate["positive_residual_correction"]["numerator"] >= 0
    assert certify_dual(rows, [1], objective, [1, 1], [0.75])[0] == Fraction(3, 4)


def assert_all_bounds_above(result, feasible):
    for method, value in result["bounds"].items():
        r = value["upper_bound"]
        assert Fraction(r["numerator"], r["denominator"]) >= feasible, method
    assert Fraction(result["upper_bound"]["numerator"], result["upper_bound"]["denominator"]) >= feasible


@pytest.mark.parametrize("dims,envelope,n,known", [
    ((4, 3, 2), (8, 6, 4), 16, Fraction(1)),
    ((4, 4, 4), (10, 10, 10), 20, Fraction(8 * 64, 1000)),
])
def test_known_small_optima(dims, envelope, n, known):
    result = pallet_bound([dims] * n, envelope, seconds=2)
    assert_all_bounds_above(result, known)
    assert result["upper_bound"]["value"] == pytest.approx(float(known))


@pytest.fixture(scope="module")
def identical_bound():
    return pallet_bound([(605, 445, 355)] * 40, seconds=3)


@pytest.mark.parametrize("standing", [False, True], ids=["12_flat", "18_flat_and_standing"])
def test_research_patterns_are_feasible_and_below_every_bound(identical_bound, standing):
    placements = []
    for level in range(6):
        for column in range(2):
            placements.append(((605 * column, 0, 355 * level), (605, 445, 355), "LWH", "planar"))
    if standing:
        for level in range(3):
            for column in range(2):
                placements.append(((445 * column, 445, 605 * level), (445, 355, 605), "WHL", "stand_hw"))
    items = [dict(box_id=i, **dict(zip(("x_mm", "y_mm", "z_mm"), xyz)),
                  **dict(zip(("length_mm", "width_mm", "height_mm"), dims)),
                  orientation_name=name, orientation_family=family)
             for i, (xyz, dims, name, family) in enumerate(placements)]
    originals = {i: (605, 445, 355) for i in range(len(items))}
    assert len(items) == (18 if standing else 12)
    assert validate_pallet(items, originals) == []
    assert_all_bounds_above(identical_bound, Fraction(len(items) * prod((605, 445, 355)), prod(ENVELOPE_MM)))


def test_real_baseline_pallet_read_from_dump():
    from scripts.kpi_from_run import original_dims_for_run

    fixture = json.loads((Path(__file__).parent / "fixtures/bounds_baseline_pallet.json").read_text())
    record, = split_pallets(fixture["run"], fixture["dump"])
    originals = fixture["original_dimensions_by_box_id"]
    assert record.closed and record.n_boxes == 19
    assert fixture["provenance"]["preset"] == "arm6_4orient"
    canonical = original_dims_for_run({"params": fixture["provenance"]["params"]},
                                     fixture["provenance"]["excel"])
    assert all(tuple(dims) == canonical[int(box_id)] for box_id, dims in originals.items())
    assert validate_pallet(record.items, originals) == []
    assert pallet_utilization(record) > 0.72
    result = pallet_bound(list(originals.values()), seconds=0.1)
    feasible = Fraction(sum(prod(d) for d in originals.values()), prod(ENVELOPE_MM))
    assert_all_bounds_above(result, feasible)


def test_horizontal_exact_catalogue_and_timeout_are_upper_bounds():
    exact = horizontal_bound([(4, 3, 2)], (8, 6, 4), seconds=2)
    assert exact["status"] == "OPTIMAL"
    assert exact["area_upper_mm2"] == exact["area_incumbent_mm2"] == 48
    timeout = horizontal_bound([(4, 3, 2)], (8, 6, 4), seconds=1e-9)
    assert timeout["status"] == "UNKNOWN"
    assert timeout["upper_bound"]["value"] == 1
    assert timeout["area_incumbent_mm2"] is None


def test_horizontal_cuts_respect_inventory_and_witness_geometry():
    boxes = [(4, 3, 2)]
    finite = horizontal_bound(boxes, (8, 6, 4), seconds=2, finite_inventory=True)
    unlimited = horizontal_bound(boxes, (8, 6, 4), seconds=2)
    assert finite["optimal"]
    assert finite["area_upper_mm2"] == finite["area_incumbent_mm2"] == 12
    assert unlimited["area_upper_mm2"] == 48
    for result in (finite, unlimited):
        witness = result["slice_witness"]
        assert sum(r["length_mm"] * r["width_mm"] for r in witness) == result["area_incumbent_mm2"]
        for i, a in enumerate(witness):
            assert 0 <= a["x_mm"] <= 8 - a["length_mm"]
            assert 0 <= a["y_mm"] <= 6 - a["width_mm"]
            for b in witness[:i]:
                assert (a["x_mm"] + a["length_mm"] <= b["x_mm"]
                        or b["x_mm"] + b["length_mm"] <= a["x_mm"]
                        or a["y_mm"] + a["width_mm"] <= b["y_mm"]
                        or b["y_mm"] + b["width_mm"] <= a["y_mm"])


def test_summary_kpi_overrides_provisional_and_selects_arm(tmp_path):
    path = tmp_path / "summary.json"
    assert read_baseline(path)["kpi"] == 0.619
    assert read_baseline(path)["provisional"]
    path.write_text(json.dumps({"configs": {
        "gantry_4orient": {"kpi": {"mean": 0.68}},
        "arm6_4orient": {"kpi": {"mean": 0.64, "ci95": [0.62, 0.66]}}}}))
    result = read_baseline(path)
    assert result["kpi"] == 0.64 and not result["provisional"]
    assert result["ci95"] == [0.62, 0.66]
    path.write_text('{"kpi": {"mean": 61.9}}')
    with pytest.raises(ValueError, match="ratio"):
        read_baseline(path)
    path.write_text('{"configs": {"gantry_4orient": {"kpi": {"mean": 0.68}}}}')
    with pytest.raises(ValueError, match="unambiguous"):
        read_baseline(path)
    path.write_text('{"preset": "gantry_4orient", "kpi": 0.68}')
    with pytest.raises(ValueError, match="unambiguous"):
        read_baseline(path)


def test_moonshot_requires_strict_upper_bound_and_does_not_claim_feasibility():
    bound = {"numerator": 9285, "denominator": 10000}
    assert not moonshot(bound, 0.619)["excluded"]
    assert moonshot(bound, 0.62)["excluded"]
    assert not moonshot({"numerator": 1, "denominator": 1}, 0.619)["excluded"]


@pytest.mark.parametrize("dims", [(0, 1, 2), (1.5, 2, 3), (1, 2)])
def test_invalid_dimensions_rejected(dims):
    with pytest.raises(ValueError, match="positive integer"):
        build_dff_system([dims])
