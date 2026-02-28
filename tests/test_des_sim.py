from __future__ import annotations

import pytest

from sim.des import Arrival, SimConfig, simulate


def make_arrivals(items: list[tuple[float, int]]) -> list[Arrival]:
    return [Arrival(time=t, destination=d, row_idx=i) for i, (t, d) in enumerate(items)]


def test_fifo_hol_m1() -> None:
    arrivals = make_arrivals([(0.0, 1), (0.0, 1), (0.0, 2)])
    config = SimConfig(model="M1", ramp_capacity=3, n_per_pallet=1, t_pick_place=10.0, t_changeover=60.0)
    result = simulate(arrivals, config)
    assert result.hol_blocked_time[1] == pytest.approx(60.0)


def test_staging_unblocks_m2() -> None:
    arrivals = make_arrivals([(0.0, 1), (0.0, 1), (0.0, 2)])
    config = SimConfig(
        model="M2",
        ramp_capacity=3,
        staging_capacity=1,
        n_per_pallet=1,
        t_pick_place=10.0,
        t_changeover=60.0,
        t_stage=6.0,
        t_unstage=10.0,
    )
    result = simulate(arrivals, config)
    assert result.hol_blocked_time[1] == pytest.approx(0.0)
    assert max(result.ramp_wait_times[1]) < 60.0


def test_upstream_hold_when_ramp_full() -> None:
    arrivals = make_arrivals([(0.0, 1), (0.0, 1), (11.0, 1)])
    config = SimConfig(model="M1", ramp_capacity=1, n_per_pallet=1, t_pick_place=10.0, t_changeover=60.0)
    result = simulate(arrivals, config)
    assert result.upstream_blocked_time[1] == pytest.approx(59.0)


def test_changeover_at_n_per_pallet() -> None:
    arrivals = make_arrivals([(0.0, 1), (1.0, 1)])
    config = SimConfig(model="M1", ramp_capacity=2, n_per_pallet=2, t_pick_place=5.0, t_changeover=60.0)
    result = simulate(arrivals, config)
    assert result.changeovers_by_destination[1] == 1
    assert result.changeover_time_by_destination[1] == pytest.approx(60.0)


def test_determinism_same_run() -> None:
    arrivals = make_arrivals([(0.0, 1), (0.0, 4), (2.0, 1), (3.0, 6)])
    config = SimConfig(model="M2", ramp_capacity=3, staging_capacity=2, n_per_pallet=2, t_pick_place=8.0)
    result_a = simulate(arrivals, config).to_dict()
    result_b = simulate(arrivals, config).to_dict()
    assert result_a == result_b


def test_stop_after_max_pallets_reached() -> None:
    arrivals = make_arrivals([(0.0, 1), (0.0, 1), (0.0, 1), (0.0, 1), (0.0, 1)])
    config = SimConfig(model="M1", ramp_capacity=5, n_per_pallet=2, t_pick_place=1.0, t_changeover=1.0)
    result = simulate(
        arrivals,
        config,
        max_pallets=2,
        max_pallets_destination=1,
    )
    seq = result.pallet_kpis["continuous_pallet_sequence"][1]
    assert result.stop_reason == "MAX_PALLETS_REACHED"
    assert seq == [2, 2]
    assert result.processed_boxes == 4
