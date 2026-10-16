from copy import deepcopy
from math import prod, sqrt

import pytest

from palca.study.metric import (
    ENVELOPE_MM, METRIC_VERSION, NOMINAL_MM, aggregate_seeds,
    paired_comparison, pallet_utilization, run_kpi, split_pallets,
)
from palca.study.params import MAX_LOAD_HEIGHT_MM, PALLET_NOMINAL_MM, pallet_envelope_mm
from sim.des import Arrival, SimConfig, simulate


def synthetic_run():
    length, width, height = ENVELOPE_MM
    items = [dict(box_id=i, length_mm=length / 2, width_mm=width / 2,
                  height_mm=height / (4 if i == 3 else 2), x_mm=0, y_mm=0, z_mm=0)
             for i in range(1, 5)]
    payload = {"metrics": {"pallet_kpis": {
        "continuous_pallet_sequence": {"1": [1, 2, 1]},
        "continuous_closure_sequence": {"1": ["COUNT", "EARLY_NO_SPACE", "END"]},
    }}}
    return payload, {"pallets": {"1": items}}


def test_split_and_exact_kpi():
    payload, dump = synthetic_run()
    before = deepcopy((payload, dump))
    records = split_pallets(payload, dump)
    assert [(r.dest, r.index, r.n_boxes, r.closure_reason, r.closed) for r in records] == [
        ("1", 1, 1, "COUNT", True), ("1", 2, 2, "EARLY_NO_SPACE", True),
        ("1", 3, 1, "END", False),
    ]
    assert [r.box_volume_mm3 for r in records] == [prod(ENVELOPE_MM) * f for f in (1/8, 3/16, 1/8)]
    assert pallet_utilization(records[1]) == 3/16
    result = run_kpi(records)
    # Cerrados: V/8 + (V/8 + V/16) = 5V/16, entre dos envolventes.
    assert result == {
        "metric_version": "1.0.0", "kpi": 5/32,
        "kpi_nominal": (5/32) * prod(ENVELOPE_MM) / prod(NOMINAL_MM),
        "n_pallets_total": 3, "n_closed": 2, "boxes_placed": 4,
        "pallet_utils": [1/8, 3/16, 1/8], "eff_real": 5/16, "h_fill": 0.5,
        "kpi_all_pallets": 7/48,
    }
    assert (payload, dump) == before
    records[0].items[0]["x_mm"] = 99
    assert dump["pallets"]["1"][0]["x_mm"] == 0


def test_fixed_envelope_ignores_experiment_overhang():
    payload, dump = synthetic_run()
    payload["params"] = {"overhang_mm": 0}
    assert ENVELOPE_MM == pallet_envelope_mm(20)
    assert NOMINAL_MM == (*PALLET_NOMINAL_MM, MAX_LOAD_HEIGHT_MM)
    assert METRIC_VERSION == "1.0.0"
    assert run_kpi(split_pallets(payload, dump))["kpi"] == 5/32


def test_split_multiple_destinations_and_integer_keys():
    payload, dump = synthetic_run()
    kpis = payload["metrics"]["pallet_kpis"]
    kpis["continuous_pallet_sequence"][2] = [1]
    kpis["continuous_closure_sequence"][2] = ["END"]
    dump["pallets"][2] = [dict(dump["pallets"]["1"][0], box_id=5)]
    records = split_pallets(payload, dump)
    assert records[-1].dest == "2" and records[-1].index == 1
    assert run_kpi(records)["n_closed"] == 2
    assert run_kpi(records)["boxes_placed"] == 5


@pytest.mark.parametrize("damage,match", [
    ("count", "suma secuencia"), ("missing_reasons", "Falta telemetría"),
    ("short_reasons", "faltan motivos"), ("missing_dest_reasons", "faltan motivos"),
    ("orphan_dump", "Destinos"), ("early_end", "END debe ser"),
    ("empty_reason", "motivo de cierre"), ("negative_count", "enteros positivos"),
])
def test_split_rejects_incoherent_input(damage, match):
    payload, dump = synthetic_run()
    kpis = payload["metrics"]["pallet_kpis"]
    if damage == "count":
        kpis["continuous_pallet_sequence"]["1"] = [1, 2, 2]
    elif damage == "missing_reasons":
        del kpis["continuous_closure_sequence"]
    elif damage == "short_reasons":
        kpis["continuous_closure_sequence"]["1"].pop()
    elif damage == "missing_dest_reasons":
        del kpis["continuous_closure_sequence"]["1"]
    elif damage == "orphan_dump":
        dump["pallets"]["2"] = dump["pallets"]["1"]
    elif damage == "early_end":
        kpis["continuous_closure_sequence"]["1"][0] = "END"
    elif damage == "empty_reason":
        kpis["continuous_closure_sequence"]["1"][0] = ""
    elif damage == "negative_count":
        kpis["continuous_pallet_sequence"]["1"][0] = -1
    with pytest.raises(ValueError, match=match):
        split_pallets(payload, dump)


def test_no_closed_pallets_is_undefined():
    payload, dump = synthetic_run()
    records = split_pallets(payload, dump)[-1:]
    result = run_kpi(records)
    assert result["kpi"] is result["eff_real"] is result["h_fill"] is None
    assert result["n_closed"] == 0 and result["kpi_all_pallets"] == 1/8
    assert run_kpi([])["kpi_all_pallets"] is None


def test_seed_confidence_interval_known_values():
    result = aggregate_seeds([{"kpi": value} for value in (0.4, 0.5, 0.6)])
    assert result["mean"] == 0.5
    assert result["sd"] == pytest.approx(0.1)
    assert result["n"] == 3 and result["min"] == 0.4 and result["max"] == 0.6
    margin = 4.302652730 * 0.1 / sqrt(3)
    assert result["ci95"] == pytest.approx([0.5 - margin, 0.5 + margin])
    assert aggregate_seeds([0.2, 0.4])["ci95"] == pytest.approx([
        0.3 - 12.706204736 * 0.1, 0.3 + 12.706204736 * 0.1])


@pytest.mark.parametrize("n,quantile", [(2, 12.706204736), (10, 2.262157163),
                                        (121, 1.979930405), (122, 1.96)])
def test_student_table_boundaries(n, quantile):
    result = aggregate_seeds(list(range(n)))
    margin = quantile * result["sd"] / sqrt(n)
    assert result["ci95"] == pytest.approx([result["mean"] - margin, result["mean"] + margin])


def test_single_empty_and_invalid_seeds():
    assert aggregate_seeds([0.5]) == dict(mean=0.5, sd=None, n=1, ci95=None, min=0.5, max=0.5)
    for values in ([], [None], [float("nan")], [{"kpi": None}]):
        with pytest.raises(ValueError):
            aggregate_seeds(values)


def test_paired_comparison_known_values():
    result = paired_comparison([0.1, 0.2, 0.3], [0.11, 0.22, 0.33])
    assert result["mean_diff_pp"] == pytest.approx(2)
    assert result["sd_pp"] == pytest.approx(1)
    margin = 4.302652730 / sqrt(3)
    assert result["ci95_pp"] == pytest.approx([2 - margin, 2 + margin])
    assert not result["significant"]
    positive = paired_comparison([0.1, 0.2, 0.3], [0.2, 0.3, 0.4])
    assert positive["mean_diff_pp"] == pytest.approx(10) and positive["significant"]
    assert paired_comparison([0.2] * 3, [0.1] * 3)["significant"]
    assert not paired_comparison([0.1] * 3, [0.1] * 3)["significant"]
    assert not paired_comparison([0.1], [0.9])["significant"]
    with pytest.raises(ValueError, match="mismas seeds"):
        paired_comparison([0.1], [0.1, 0.2])


def test_des_ordered_telemetry_empty_count_and_end():
    config = SimConfig(model="M1", n_per_pallet=2, t_pick_place=1, t_changeover=1)
    empty = simulate([], config).pallet_kpis
    assert empty["continuous_closure_sequence"] == {dest: [] for dest in range(1, 7)}
    arrivals = [Arrival(time=0, destination=1, row_idx=i) for i in range(5)]
    kpis = simulate(arrivals, config).pallet_kpis
    assert kpis["continuous_pallet_sequence"][1] == [2, 2, 1]
    assert kpis["continuous_closure_sequence"][1] == ["COUNT", "COUNT", "END"]
    limited = simulate(arrivals, config, max_pallets=1, max_pallets_destination=1).pallet_kpis
    assert limited["continuous_closure_sequence"][1] == ["COUNT"]
