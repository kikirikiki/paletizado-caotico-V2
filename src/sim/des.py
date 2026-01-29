from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
import heapq
import logging
from typing import Any, Deque, Iterable, Protocol

import numpy as np


EPS = 1e-9


@dataclass(frozen=True)
class Arrival:
    time: float
    destination: int
    row_idx: int
    length_mm: int | None = None
    width_mm: int | None = None
    height_mm: int | None = None
    weight_kg: float | None = None
    priority: float | None = None


@dataclass
class Box:
    box_id: int
    arrival_time: float
    destination: int
    ramp_id: int
    length_mm: int | None = None
    width_mm: int | None = None
    height_mm: int | None = None
    weight_kg: float | None = None
    priority: float | None = None
    ramp_enter_time: float | None = None
    staging_seq: int | None = None


@dataclass
class PackEval:
    fits: bool
    score: float


class Packer(Protocol):
    def evaluate(self, box: Box, destination: int) -> PackEval:
        ...


@dataclass(frozen=True)
class NullPacker:
    def evaluate(self, box: Box, destination: int) -> PackEval:
        return PackEval(fits=True, score=0.0)


@dataclass
class DestinationState:
    destination: int
    state: str = "ACTIVE"
    count: int = 0
    changeover_until: float | None = None
    changeovers: int = 0
    changeover_time: float = 0.0


@dataclass
class RampState:
    ramp_id: int
    capacity: int
    staging_capacity: int
    queue: Deque[Box] = field(default_factory=deque)
    upstream: Deque[Box] = field(default_factory=deque)
    staging: list[Box] = field(default_factory=list)
    staging_seq_counter: int = 0
    max_occupancy: int = 0
    max_staging: int = 0


@dataclass
class SimConfig:
    model: str = "M1"
    ramp_capacity: int = 15
    staging_capacity: int = 0
    n_per_pallet: int = 24
    t_changeover: float = 60.0
    t_pick_place: float = 14.0
    t_stage: float = 6.0
    t_unstage: float = 10.0
    logger: logging.Logger | None = None
    decision_policy: Any | None = None


@dataclass
class SimulationResult:
    total_boxes: int
    processed_boxes: int
    sim_start: float
    sim_end: float
    makespan: float
    throughput_per_hour: float
    robot_busy_time: float
    robot_utilization_percent: float
    upstream_blocked_time: dict[int, float]
    hol_blocked_time: dict[int, float]
    max_ramp_occupancy: dict[int, int]
    ramp_wait_times: dict[int, list[float]]
    ramp_wait_percentiles: dict[int, dict[str, float]]
    changeovers_by_destination: dict[int, int]
    changeover_time_by_destination: dict[int, float]
    staging_max_occupancy: dict[int, int]
    staging_full_percent: dict[int, float]
    pallet_kpis: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "total_boxes": self.total_boxes,
            "processed_boxes": self.processed_boxes,
            "sim_start": self.sim_start,
            "sim_end": self.sim_end,
            "makespan": self.makespan,
            "throughput_per_hour": self.throughput_per_hour,
            "robot_busy_time": self.robot_busy_time,
            "robot_utilization_percent": self.robot_utilization_percent,
            "upstream_blocked_time": dict(self.upstream_blocked_time),
            "hol_blocked_time": dict(self.hol_blocked_time),
            "max_ramp_occupancy": dict(self.max_ramp_occupancy),
            "ramp_wait_percentiles": {
                ramp_id: dict(vals) for ramp_id, vals in self.ramp_wait_percentiles.items()
            },
            "changeovers_by_destination": dict(self.changeovers_by_destination),
            "changeover_time_by_destination": dict(self.changeover_time_by_destination),
            "staging_max_occupancy": dict(self.staging_max_occupancy),
            "staging_full_percent": dict(self.staging_full_percent),
            "pallet_kpis": dict(self.pallet_kpis),
        }


@dataclass(frozen=True)
class Action:
    kind: str
    ramp_id: int


@dataclass(frozen=True)
class Event:
    time: float
    kind: str
    seq: int
    box: Box | None = None
    destination: int | None = None
    ramp_id: int | None = None
    action: str | None = None
    plan: object | None = None


class Scheduler:
    def __init__(self) -> None:
        self._last_served: dict[int, float] = {1: 0.0, 2: 0.0}

    def decide(self, ramps: dict[int, RampState], active_heads: dict[int, bool], stageable: dict[int, bool],
               allow_unstage: bool, staging_ready: dict[int, bool], now: float) -> Action | None:
        candidates: list[tuple[float, float, int, Action]] = []
        for ramp_id, ramp in ramps.items():
            action = self._candidate_for_ramp(
                ramp_id,
                ramp,
                active_heads,
                stageable,
                allow_unstage,
                staging_ready,
            )
            if action is None:
                continue
            occupancy = len(ramp.queue) / float(ramp.capacity) if ramp.capacity > 0 else 0.0
            idle_time = now - self._last_served.get(ramp_id, 0.0)
            candidates.append((occupancy, idle_time, -ramp_id, action))

        if not candidates:
            return None

        candidates.sort(reverse=True)
        chosen = candidates[0][3]
        self._last_served[chosen.ramp_id] = now
        return chosen

    def _candidate_for_ramp(
        self,
        ramp_id: int,
        ramp: RampState,
        active_heads: dict[int, bool],
        stageable: dict[int, bool],
        allow_unstage: bool,
        staging_ready: dict[int, bool],
    ) -> Action | None:
        if ramp.queue and active_heads.get(ramp_id, False):
            return Action(kind="PICK", ramp_id=ramp_id)
        if ramp.queue and stageable.get(ramp_id, False):
            return Action(kind="STAGE", ramp_id=ramp_id)
        if allow_unstage and staging_ready.get(ramp_id, False):
            return Action(kind="UNSTAGE", ramp_id=ramp_id)
        return None


def assign_ramp(destination: int) -> int:
    if 1 <= destination <= 3:
        return 1
    if 4 <= destination <= 6:
        return 2
    raise ValueError(f"Destino fuera de rango (1-6): {destination}")


def simulate(
    arrivals: Iterable[Arrival],
    config: SimConfig,
    packer: Packer | None = None,
    decision_policy: Any | None = None,
) -> SimulationResult:
    arrival_list = sorted(list(arrivals), key=lambda a: (a.time, a.row_idx))
    if not arrival_list:
        return SimulationResult(
            total_boxes=0,
            processed_boxes=0,
            sim_start=0.0,
            sim_end=0.0,
            makespan=0.0,
            throughput_per_hour=0.0,
            robot_busy_time=0.0,
            robot_utilization_percent=0.0,
            upstream_blocked_time={1: 0.0, 2: 0.0},
            hol_blocked_time={1: 0.0, 2: 0.0},
            max_ramp_occupancy={1: 0, 2: 0},
            ramp_wait_times={1: [], 2: []},
            ramp_wait_percentiles={1: {}, 2: {}},
            changeovers_by_destination={dest: 0 for dest in range(1, 7)},
            changeover_time_by_destination={dest: 0.0 for dest in range(1, 7)},
            staging_max_occupancy={1: 0, 2: 0},
            staging_full_percent={1: 0.0, 2: 0.0},
            pallet_kpis={},
        )

    logger = config.logger or logging.getLogger(__name__)
    packer = packer or NullPacker()
    policy = decision_policy or config.decision_policy

    model = config.model.upper()
    if model not in ("M1", "M2"):
        raise ValueError(f"Modelo no soportado: {config.model}")

    staging_cap = config.staging_capacity if model == "M2" else 0

    ramps = {
        1: RampState(ramp_id=1, capacity=config.ramp_capacity, staging_capacity=staging_cap),
        2: RampState(ramp_id=2, capacity=config.ramp_capacity, staging_capacity=staging_cap),
    }
    destinations = {dest: DestinationState(destination=dest) for dest in range(1, 7)}

    scheduler = Scheduler()
    events: list[tuple[float, int, Event]] = []
    seq = 0
    for box_id, arrival in enumerate(arrival_list, start=1):
        ramp_id = assign_ramp(arrival.destination)
        box = Box(
            box_id=box_id,
            arrival_time=float(arrival.time),
            destination=arrival.destination,
            ramp_id=ramp_id,
            length_mm=arrival.length_mm,
            width_mm=arrival.width_mm,
            height_mm=arrival.height_mm,
            weight_kg=arrival.weight_kg,
            priority=arrival.priority,
        )
        event = Event(time=box.arrival_time, kind="ARRIVAL", seq=seq, box=box, ramp_id=ramp_id)
        heapq.heappush(events, (event.time, event.seq, event))
        seq += 1

    sim_start = arrival_list[0].time
    current_time = sim_start
    robot_busy = False
    robot_busy_time = 0.0
    processed_boxes = 0

    ramp_wait_times: dict[int, list[float]] = {1: [], 2: []}
    upstream_blocked_time: dict[int, float] = {1: 0.0, 2: 0.0}
    hol_blocked_time: dict[int, float] = {1: 0.0, 2: 0.0}
    staging_full_time: dict[int, float] = {1: 0.0, 2: 0.0}

    upstream_blocked: dict[int, bool] = {1: False, 2: False}
    hol_blocked: dict[int, bool] = {1: False, 2: False}
    staging_full: dict[int, bool] = {1: False, 2: False}

    def advance_time(delta: float) -> None:
        nonlocal robot_busy_time
        if delta <= EPS:
            return
        if robot_busy:
            robot_busy_time += delta
        for rid in (1, 2):
            if upstream_blocked[rid]:
                upstream_blocked_time[rid] += delta
            if hol_blocked[rid]:
                hol_blocked_time[rid] += delta
            if staging_full[rid]:
                staging_full_time[rid] += delta

    def update_flags() -> None:
        for rid, ramp in ramps.items():
            upstream_blocked[rid] = bool(
                len(ramp.queue) >= ramp.capacity and len(ramp.upstream) > 0
            )
            hol_blocked[rid] = False
            if ramp.queue:
                dest_state = destinations[ramp.queue[0].destination]
                if dest_state.state == "CHANGEOVER":
                    if model == "M1":
                        hol_blocked[rid] = True
                    else:
                        hol_blocked[rid] = len(ramp.staging) >= ramp.staging_capacity
            if model == "M2" and ramp.staging_capacity > 0:
                staging_full[rid] = len(ramp.staging) >= ramp.staging_capacity
            else:
                staging_full[rid] = False

            ramp.max_occupancy = max(ramp.max_occupancy, len(ramp.queue))
            ramp.max_staging = max(ramp.max_staging, len(ramp.staging))

    def fill_ramp_from_upstream(ramp: RampState, time: float) -> None:
        while len(ramp.queue) < ramp.capacity and ramp.upstream:
            box = ramp.upstream.popleft()
            box.ramp_enter_time = time
            ramp.queue.append(box)

    def fill_ramp_from_arrivals(ramp: RampState, arrivals_q: Deque[Box], time: float) -> None:
        while len(ramp.queue) < ramp.capacity and arrivals_q:
            box = arrivals_q.popleft()
            box.ramp_enter_time = time
            ramp.queue.append(box)
        while arrivals_q:
            ramp.upstream.append(arrivals_q.popleft())

    def schedule_event(event: Event) -> None:
        heapq.heappush(events, (event.time, event.seq, event))

    def start_changeover(destination: int, time: float, reason: str = "COUNT") -> None:
        dest_state = destinations[destination]
        if dest_state.state == "CHANGEOVER":
            return
        dest_state.state = "CHANGEOVER"
        dest_state.changeovers += 1
        dest_state.changeover_time += config.t_changeover
        dest_state.changeover_until = time + config.t_changeover
        if policy is not None and hasattr(policy, "on_changeover_start"):
            try:
                policy.on_changeover_start(destination, reason)
            except Exception:
                logger.exception("policy on_changeover_start failed dest=%s", destination)
        schedule_event(
            Event(
                time=dest_state.changeover_until,
                kind="CHANGEOVER_DONE",
                seq=next_seq(),
                destination=destination,
            )
        )

    def next_seq() -> int:
        nonlocal seq
        seq += 1
        return seq

    def place_box(box: Box, time: float) -> None:
        nonlocal processed_boxes
        processed_boxes += 1
        dest_state = destinations[box.destination]
        dest_state.count += 1
        if dest_state.count >= config.n_per_pallet:
            start_changeover(box.destination, time, reason="COUNT")

    def handle_robot_done(event: Event) -> None:
        nonlocal robot_busy
        robot_busy = False
        if event.action == "PICK":
            if event.box is None:
                return
            if event.plan is not None and policy is not None and hasattr(policy, "commit_plan"):
                try:
                    policy.commit_plan(event.plan, event.time)
                except Exception:
                    logger.exception("policy commit_plan failed")
            place_box(event.box, event.time)
        elif event.action == "STAGE":
            if event.box is None or event.ramp_id is None:
                return
            ramp = ramps[event.ramp_id]
            ramp.staging.append(event.box)
        elif event.action == "UNSTAGE":
            if event.box is None:
                return
            place_box(event.box, event.time)

    def handle_changeover_done(event: Event) -> None:
        if event.destination is None:
            return
        dest_state = destinations[event.destination]
        dest_state.state = "ACTIVE"
        dest_state.count = 0
        dest_state.changeover_until = None

    def pick_head(ramp: RampState, time: float, kind: str) -> Box:
        box = ramp.queue.popleft()
        wait = time - (box.ramp_enter_time if box.ramp_enter_time is not None else box.arrival_time)
        ramp_wait_times[ramp.ramp_id].append(wait)
        if kind == "STAGE":
            ramp.staging_seq_counter += 1
            box.staging_seq = ramp.staging_seq_counter
        return box

    def pick_from_queue(ramp: RampState, index: int, time: float, kind: str) -> Box:
        if index <= 0:
            return pick_head(ramp, time, kind)
        items = list(ramp.queue)
        if index >= len(items):
            return pick_head(ramp, time, kind)
        box = items.pop(index)
        ramp.queue = deque(items)
        wait = time - (box.ramp_enter_time if box.ramp_enter_time is not None else box.arrival_time)
        ramp_wait_times[ramp.ramp_id].append(wait)
        return box

    def pick_from_staging(ramp: RampState) -> Box | None:
        for idx, box in enumerate(ramp.staging):
            dest_state = destinations[box.destination]
            if dest_state.state != "ACTIVE":
                continue
            if not packer.evaluate(box, box.destination).fits:
                continue
            return ramp.staging.pop(idx)
        return None

    def system_has_boxes() -> bool:
        if any(ramp.queue or ramp.upstream or ramp.staging for ramp in ramps.values()):
            return True
        return False

    def map_policy_reason(reason: str) -> str:
        """
        Convención:
        - EARLY_* : cierres tempranos por huecos/fragmentación (lo que queremos medir como 'early')
        - CLOSE_* : cierres normales (altura llena, fin por conteo, etc.)
        """
        r = str(reason).upper().strip()

        # Esto NO es "early": es pallet lleno por altura (normal)
        if r in ("HEIGHT_LIMIT", "HEIGHT_FULL", "MAX_HEIGHT"):
            return "CLOSE_HEIGHT_FULL"

        # Esto SÍ lo consideramos "early" (si lo implementas en el packer)
        if r in ("NO_SPACE", "FRAGMENTATION", "PACK_NO_SPACE"):
            return f"EARLY_{r}"

        # Por defecto: cierre normal
        return f"CLOSE_{r}"

    update_flags()

    while events or robot_busy or system_has_boxes():
        next_time = events[0][0] if events else current_time
        if next_time - current_time > EPS:
            advance_time(next_time - current_time)
            current_time = next_time

        pending_arrivals: dict[int, Deque[Box]] = {1: deque(), 2: deque()}
        batch: list[Event] = []
        while events and abs(events[0][0] - current_time) <= EPS:
            _, _, event = heapq.heappop(events)
            batch.append(event)

        batch.sort(key=lambda e: (_event_priority(e.kind), e.seq))
        for event in batch:
            if event.kind == "ARRIVAL" and event.box is not None and event.ramp_id is not None:
                pending_arrivals[event.ramp_id].append(event.box)
            elif event.kind == "ROBOT_DONE":
                handle_robot_done(event)
            elif event.kind == "CHANGEOVER_DONE":
                handle_changeover_done(event)

        for rid, ramp in ramps.items():
            fill_ramp_from_upstream(ramp, current_time)
            fill_ramp_from_arrivals(ramp, pending_arrivals[rid], current_time)

        if not robot_busy:
            if policy is None:
                action = decide_action(ramps, destinations, packer, scheduler, model, current_time)
                if action is not None:
                    ramp = ramps[action.ramp_id]
                    if action.kind == "PICK":
                        box = pick_head(ramp, current_time, "PICK")
                        schedule_event(
                            Event(
                                time=current_time + config.t_pick_place,
                                kind="ROBOT_DONE",
                                seq=next_seq(),
                                box=box,
                                action="PICK",
                            )
                        )
                        robot_busy = True
                        fill_ramp_from_upstream(ramp, current_time)
                    elif action.kind == "STAGE":
                        box = pick_head(ramp, current_time, "STAGE")
                        schedule_event(
                            Event(
                                time=current_time + config.t_stage,
                                kind="ROBOT_DONE",
                                seq=next_seq(),
                                box=box,
                                action="STAGE",
                                ramp_id=action.ramp_id,
                            )
                        )
                        robot_busy = True
                        fill_ramp_from_upstream(ramp, current_time)
                    elif action.kind == "UNSTAGE":
                        box = pick_from_staging(ramp)
                        if box is not None:
                            schedule_event(
                                Event(
                                    time=current_time + config.t_unstage,
                                    kind="ROBOT_DONE",
                                    seq=next_seq(),
                                    box=box,
                                    action="UNSTAGE",
                                )
                            )
                            robot_busy = True
            else:
                plan = policy.choose_action(ramps=ramps, destinations=destinations, now=current_time)
                if plan is None:
                    pending = getattr(policy, "drain_pending_closures", None)
                    if callable(pending):
                        for dest_id, reason in pending().items():
                            dest_key = int(dest_id) if str(dest_id).isdigit() else dest_id
                            if dest_key in destinations and destinations[dest_key].state == "ACTIVE":
                                start_changeover(dest_key, current_time, reason=map_policy_reason(str(reason)))
                else:
                    ramp = ramps[plan.ramp_id]
                    box = pick_from_queue(ramp, int(plan.buffer_index), current_time, "PICK")
                    dt_extra = float(getattr(plan, "dt_extra", 0.0) or 0.0)
                    schedule_event(
                        Event(
                            time=current_time + config.t_pick_place + dt_extra,
                            kind="ROBOT_DONE",
                            seq=next_seq(),
                            box=box,
                            action="PICK",
                            plan=plan,
                        )
                    )
                    robot_busy = True
                    fill_ramp_from_upstream(ramp, current_time)

        update_flags()

        if not events and not robot_busy and not system_has_boxes():
            break

    sim_end = current_time
    makespan = max(sim_end - sim_start, 0.0)
    throughput = processed_boxes / (makespan / 3600.0) if makespan > EPS else 0.0
    robot_util = (robot_busy_time / makespan) * 100.0 if makespan > EPS else 0.0

    ramp_wait_percentiles = {
        rid: _percentiles(values) for rid, values in ramp_wait_times.items()
    }

    changeovers_by_destination = {dest: st.changeovers for dest, st in destinations.items()}
    changeover_time_by_destination = {
        dest: st.changeover_time for dest, st in destinations.items()
    }

    staging_full_percent = {
        rid: (staging_full_time[rid] / makespan) * 100.0 if makespan > EPS else 0.0
        for rid in (1, 2)
    }

    pallet_kpis: dict[str, object] = {}
    if policy is not None and hasattr(policy, "collect_kpis"):
        try:
            pallet_kpis = policy.collect_kpis()
        except Exception:
            logger.exception("policy collect_kpis failed")
            pallet_kpis = {}

    return SimulationResult(
        total_boxes=len(arrival_list),
        processed_boxes=processed_boxes,
        sim_start=float(sim_start),
        sim_end=float(sim_end),
        makespan=float(makespan),
        throughput_per_hour=float(throughput),
        robot_busy_time=float(robot_busy_time),
        robot_utilization_percent=float(robot_util),
        upstream_blocked_time=upstream_blocked_time,
        hol_blocked_time=hol_blocked_time,
        max_ramp_occupancy={rid: ramp.max_occupancy for rid, ramp in ramps.items()},
        ramp_wait_times=ramp_wait_times,
        ramp_wait_percentiles=ramp_wait_percentiles,
        changeovers_by_destination=changeovers_by_destination,
        changeover_time_by_destination=changeover_time_by_destination,
        staging_max_occupancy={rid: ramp.max_staging for rid, ramp in ramps.items()},
        staging_full_percent=staging_full_percent,
        pallet_kpis=pallet_kpis,
    )


def decide_action(
    ramps: dict[int, RampState],
    destinations: dict[int, DestinationState],
    packer: Packer,
    scheduler: Scheduler,
    model: str,
    now: float,
) -> Action | None:
    active_heads: dict[int, bool] = {}
    stageable: dict[int, bool] = {}
    staging_ready: dict[int, bool] = {}
    for rid, ramp in ramps.items():
        head_ok = False
        if ramp.queue:
            head = ramp.queue[0]
            dest_state = destinations[head.destination]
            if dest_state.state == "ACTIVE" and packer.evaluate(head, head.destination).fits:
                head_ok = True
        active_heads[rid] = head_ok

        stageable[rid] = False
        if model == "M2" and ramp.queue:
            head = ramp.queue[0]
            dest_state = destinations[head.destination]
            if dest_state.state == "CHANGEOVER" and len(ramp.staging) < ramp.staging_capacity:
                stageable[rid] = True

        staging_ready[rid] = False
        if model == "M2" and ramp.staging:
            for box in ramp.staging:
                dest_state = destinations[box.destination]
                if dest_state.state == "ACTIVE" and packer.evaluate(box, box.destination).fits:
                    staging_ready[rid] = True
                    break

    allow_unstage = False
    if model == "M2":
        allow_unstage = all(
            (len(ramp.queue) / float(ramp.capacity) if ramp.capacity > 0 else 0.0) < 0.8
            for ramp in ramps.values()
        )

    return scheduler.decide(
        ramps=ramps,
        active_heads=active_heads,
        stageable=stageable,
        allow_unstage=allow_unstage,
        staging_ready=staging_ready,
        now=now,
    )


def _event_priority(kind: str) -> int:
    if kind == "ROBOT_DONE":
        return 0
    if kind == "CHANGEOVER_DONE":
        return 1
    return 2


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    percentiles = np.percentile(values, [50, 90, 99])
    return {
        "p50": float(percentiles[0]),
        "p90": float(percentiles[1]),
        "p99": float(percentiles[2]),
    }
