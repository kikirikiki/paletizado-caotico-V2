from __future__ import annotations

from dataclasses import dataclass, field
from collections import Counter, deque
import math
from typing import Iterable

import numpy as np


EPS = 1e-9


@dataclass(slots=True)
class Box:
    box_id: int
    arrival_time: float
    destination: int
    height: float
    ramp_id: int
    enter_time: float | None = None
    pick_time: float | None = None


@dataclass(slots=True)
class Ramp:
    ramp_id: int
    capacity: int
    queue: deque[Box] = field(default_factory=deque)
    upstream: deque[Box] = field(default_factory=deque)
    blocked_time: float = 0.0
    blocked_since: float | None = None
    upstream_blocked_time: float = 0.0
    upstream_blocked_count: int = 0
    upstream_wait_times: list[float] = field(default_factory=list)


@dataclass(slots=True)
class Pallet:
    destination: int
    max_height: float
    height: float = 0.0
    blocked_until: float = 0.0
    pallets_completed: int = 0


@dataclass(slots=True)
class SimConfig:
    ramp_capacity: int = 15
    pallet_max_height: float = 2400.0
    t_pick: float = 1.0
    t_place: float = 1.0
    t_travel_ramp_pallet: float = 1.0
    t_travel_pallet_home: float = 1.0
    t_change_pallet: float = 60.0
    t_z: float = 0.0
    extra_height_source: str = "pallet"
    default_box_height: float = 200.0
    policy: str = "oldest"


@dataclass(slots=True)
class SimulationResult:
    total_boxes: int
    processed_boxes: int
    sim_start: float
    sim_end: float
    sim_time: float
    throughput: float
    robot_busy_time: float
    robot_utilization: float
    queue_wait_times: list[float]
    queue_wait_mean: float
    queue_wait_percentiles: dict[str, float]
    ramp_blocked_time: dict[int, float]
    ramp_blocked_percent: dict[int, float]
    upstream_blocked_time: dict[int, float]
    upstream_blocked_count: dict[int, int]
    boxes_by_destination: dict[int, int]
    pallets_completed_by_destination: dict[int, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "total_boxes": self.total_boxes,
            "processed_boxes": self.processed_boxes,
            "sim_start": self.sim_start,
            "sim_end": self.sim_end,
            "sim_time": self.sim_time,
            "throughput": self.throughput,
            "robot_busy_time": self.robot_busy_time,
            "robot_utilization": self.robot_utilization,
            "queue_wait_mean": self.queue_wait_mean,
            "queue_wait_percentiles": dict(self.queue_wait_percentiles),
            "ramp_blocked_time": dict(self.ramp_blocked_time),
            "ramp_blocked_percent": dict(self.ramp_blocked_percent),
            "upstream_blocked_time": dict(self.upstream_blocked_time),
            "upstream_blocked_count": dict(self.upstream_blocked_count),
            "boxes_by_destination": dict(self.boxes_by_destination),
            "pallets_completed_by_destination": dict(self.pallets_completed_by_destination),
        }


def assign_ramp(destination: int) -> int:
    if 1 <= destination <= 3:
        return 1
    if 4 <= destination <= 6:
        return 2
    raise ValueError(f"Destino fuera de rango (1-6): {destination}")


def _eligible_ramps(
    ramps: dict[int, Ramp],
    pallets: dict[int, Pallet],
    time: float,
) -> list[int]:
    eligible: list[int] = []
    for ramp_id, ramp in ramps.items():
        if not ramp.queue:
            continue
        dest = ramp.queue[0].destination
        if pallets[dest].blocked_until - time > EPS:
            continue
        eligible.append(ramp_id)
    return eligible


def _select_ramp(
    ramps: dict[int, Ramp],
    pallets: dict[int, Pallet],
    time: float,
    policy: str,
) -> int | None:
    eligible = _eligible_ramps(ramps, pallets, time)
    if not eligible:
        return None

    policy = policy.lower()
    if policy == "ramp1_first":
        return 1 if 1 in eligible else eligible[0]
    if policy == "ramp2_first":
        return 2 if 2 in eligible else eligible[0]
    if policy == "longest":
        return max(eligible, key=lambda rid: (len(ramps[rid].queue), -rid))
    if policy == "oldest":
        return min(
            eligible,
            key=lambda rid: (
                (
                    ramps[rid].queue[0].enter_time
                    if ramps[rid].queue[0].enter_time is not None
                    else ramps[rid].queue[0].arrival_time
                ),
                rid,
            ),
        )

    raise ValueError(f"Politica no reconocida: {policy}")


def _update_ramp_blocked_state(
    ramp: Ramp,
    pallets: dict[int, Pallet],
    time: float,
) -> None:
    blocked_now = False
    if ramp.queue:
        dest = ramp.queue[0].destination
        blocked_now = pallets[dest].blocked_until - time > EPS

    if blocked_now:
        if ramp.blocked_since is None:
            ramp.blocked_since = time
    elif ramp.blocked_since is not None:
        ramp.blocked_time += time - ramp.blocked_since
        ramp.blocked_since = None


def _fill_from_upstream(ramp: Ramp, time: float) -> None:
    while len(ramp.queue) < ramp.capacity and ramp.upstream:
        box = ramp.upstream.popleft()
        box.enter_time = time
        wait = time - box.arrival_time
        ramp.upstream_blocked_time += wait
        if wait > EPS:
            ramp.upstream_blocked_count += 1
        ramp.upstream_wait_times.append(wait)
        ramp.queue.append(box)


def simulate(boxes: Iterable[Box], config: SimConfig) -> SimulationResult:
    boxes = list(boxes)
    if not boxes:
        return SimulationResult(
            total_boxes=0,
            processed_boxes=0,
            sim_start=0.0,
            sim_end=0.0,
            sim_time=0.0,
            throughput=0.0,
            robot_busy_time=0.0,
            robot_utilization=0.0,
            queue_wait_times=[],
            queue_wait_mean=0.0,
            queue_wait_percentiles={},
            ramp_blocked_time={1: 0.0, 2: 0.0},
            ramp_blocked_percent={1: 0.0, 2: 0.0},
            upstream_blocked_time={1: 0.0, 2: 0.0},
            upstream_blocked_count={1: 0, 2: 0},
            boxes_by_destination={},
            pallets_completed_by_destination={},
        )

    boxes.sort(key=lambda b: b.arrival_time)
    sim_start = boxes[0].arrival_time
    time = sim_start

    ramps = {
        1: Ramp(ramp_id=1, capacity=config.ramp_capacity),
        2: Ramp(ramp_id=2, capacity=config.ramp_capacity),
    }
    pallets = {
        dest: Pallet(destination=dest, max_height=config.pallet_max_height)
        for dest in range(1, 7)
    }

    robot_busy_until = 0.0
    robot_busy = False
    robot_box: Box | None = None
    robot_busy_time = 0.0

    queue_wait_times: list[float] = []
    boxes_by_destination: Counter[int] = Counter()
    pallets_completed: Counter[int] = Counter()

    idx = 0
    total_boxes = len(boxes)

    while (
        idx < total_boxes
        or robot_busy
        or ramps[1].queue
        or ramps[2].queue
        or ramps[1].upstream
        or ramps[2].upstream
    ):
        if robot_busy and robot_busy_until - time <= EPS:
            robot_busy = False
            if robot_box is not None:
                pallet = pallets[robot_box.destination]
                pallet.height += robot_box.height
                boxes_by_destination[robot_box.destination] += 1
                if pallet.height >= pallet.max_height - EPS:
                    pallet.pallets_completed += 1
                    pallets_completed[robot_box.destination] += 1
                    pallet.height = 0.0
                    pallet.blocked_until = time + config.t_change_pallet
                robot_box = None

        for pallet in pallets.values():
            if pallet.blocked_until > 0.0 and pallet.blocked_until - time <= EPS:
                pallet.blocked_until = 0.0

        while idx < total_boxes and boxes[idx].arrival_time - time <= EPS:
            box = boxes[idx]
            idx += 1
            ramps[box.ramp_id].upstream.append(box)

        for ramp in ramps.values():
            _fill_from_upstream(ramp, time)

        for ramp in ramps.values():
            _update_ramp_blocked_state(ramp, pallets, time)

        if not robot_busy:
            chosen = _select_ramp(ramps, pallets, time, config.policy)
            if chosen is not None:
                ramp = ramps[chosen]
                box = ramp.queue.popleft()
                box.pick_time = time
                wait_time = time - (
                    box.enter_time if box.enter_time is not None else box.arrival_time
                )
                queue_wait_times.append(wait_time)

                pallet = pallets[box.destination]
                if config.extra_height_source == "pallet":
                    extra_height = pallet.height
                elif config.extra_height_source == "box":
                    extra_height = box.height
                else:
                    raise ValueError(
                        f"extra_height_source invalido: {config.extra_height_source}"
                    )

                cycle_time = (
                    config.t_pick
                    + config.t_travel_ramp_pallet
                    + config.t_place
                    + config.t_travel_pallet_home
                    + config.t_z * extra_height
                )

                robot_busy = True
                robot_busy_until = time + cycle_time
                robot_busy_time += cycle_time
                robot_box = box

                _fill_from_upstream(ramp, time)
                for ramp_item in ramps.values():
                    _update_ramp_blocked_state(ramp_item, pallets, time)
                continue

        next_times: list[float] = []
        if idx < total_boxes:
            next_times.append(boxes[idx].arrival_time)
        if robot_busy:
            next_times.append(robot_busy_until)
        for pallet in pallets.values():
            if pallet.blocked_until - time > EPS:
                next_times.append(pallet.blocked_until)

        if not next_times:
            break

        next_time = min(next_times)
        if next_time - time <= EPS:
            time += EPS
        else:
            time = next_time

    sim_end = time
    sim_time = max(sim_end - sim_start, 0.0)
    if sim_time < EPS:
        sim_time = 0.0

    for ramp in ramps.values():
        if ramp.blocked_since is not None:
            ramp.blocked_time += sim_end - ramp.blocked_since
            ramp.blocked_since = None

    if queue_wait_times:
        queue_wait_mean = float(np.mean(queue_wait_times))
        percentiles = np.percentile(queue_wait_times, [50, 90, 95, 99])
        queue_wait_percentiles = {
            "p50": float(percentiles[0]),
            "p90": float(percentiles[1]),
            "p95": float(percentiles[2]),
            "p99": float(percentiles[3]),
        }
    else:
        queue_wait_mean = 0.0
        queue_wait_percentiles = {}

    throughput = boxes_by_destination.total() / sim_time if sim_time > EPS else 0.0
    robot_utilization = robot_busy_time / sim_time if sim_time > EPS else 0.0

    ramp_blocked_time = {rid: ramp.blocked_time for rid, ramp in ramps.items()}
    if sim_time > EPS:
        ramp_blocked_percent = {
            rid: (blocked / sim_time) * 100.0 for rid, blocked in ramp_blocked_time.items()
        }
    else:
        ramp_blocked_percent = {rid: 0.0 for rid in ramp_blocked_time}

    upstream_blocked_time = {
        rid: ramp.upstream_blocked_time for rid, ramp in ramps.items()
    }
    upstream_blocked_count = {
        rid: ramp.upstream_blocked_count for rid, ramp in ramps.items()
    }

    return SimulationResult(
        total_boxes=total_boxes,
        processed_boxes=boxes_by_destination.total(),
        sim_start=sim_start,
        sim_end=sim_end,
        sim_time=sim_time,
        throughput=throughput,
        robot_busy_time=robot_busy_time,
        robot_utilization=robot_utilization,
        queue_wait_times=queue_wait_times,
        queue_wait_mean=queue_wait_mean,
        queue_wait_percentiles=queue_wait_percentiles,
        ramp_blocked_time=ramp_blocked_time,
        ramp_blocked_percent=ramp_blocked_percent,
        upstream_blocked_time=upstream_blocked_time,
        upstream_blocked_count=upstream_blocked_count,
        boxes_by_destination=dict(boxes_by_destination),
        pallets_completed_by_destination=dict(pallets_completed),
    )
