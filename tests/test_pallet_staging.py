"""D-036 physical staging, visibility, release geometry and D-022 leakage."""
from collections import deque
from dataclasses import replace
from pathlib import Path
import random

import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.packer.pallet_model import PalletModel
from palca.scheduler.scheduler_v1 import (
    PickPlan, SchedulerConfig, SchedulerRampState, SchedulerSimState, SchedulerV1,
)
from palca.scheduler.staging import STAGING_RAMP_ID, narrow_candidates
from palca.scoring.free_sides import free_sides
from scripts.run_sweep import load_configs
from sim.des import Arrival, Box as SimBox, PalletStagingState, RampState, SimConfig, simulate
from sim.run import run_simulation


def box(i, l=100, w=100, h=100):
    return Box(i, l, w, h, 0, destination=1)


def sim_box(i, l=100, w=100, h=100):
    return SimBox(i, 0, 1, 1, l, w, h)


def policy(k=3, release="hole", **kwargs):
    return PolicyPackerScheduler.from_defaults(
        pallet_staging_slots=k, staging_release=release, lookahead_k=15,
        micro_plan_enabled=True, micro_plan_node_budget=57, micro_plan_hard_cap_ms=600000,
        micro_plan_value="rollout", micro_plan_rollout_leaves=2,
        rollout_future_boxes=6, rollout_prior="generic", heartbeat_sec=0, **kwargs)


@pytest.mark.parametrize("k", [1, 2, 3])
def test_des_floor_slots_fifo_timing_and_complete_drain(k):
    class Scripted:
        def __init__(self):
            self.calls, self.commits = [], []
        def choose_action(self, *, ramps, destinations, now, pallet_staging):
            queue = ramps[1].queue
            occupied = pallet_staging.occupied
            self.calls.append((now, [b.box_id for b in queue], [b.box_id for _, b in occupied]))
            assert all(not r.staging for r in ramps.values())  # M2 state remains untouched.
            assert len(occupied) <= k
            if pallet_staging.parks < k:
                b = queue[1]
                return PickPlan(1, 1, b.box_id, 1, None, 0, 0, "PARK", pallet_staging.parks)
            if queue:
                return PickPlan(1, 0, queue[0].box_id, 1, None, 0, 0)
            if occupied:
                slot, b = occupied[0]
                return PickPlan(1, 0, b.box_id, 1, None, 0, 0, "UNPARK", slot)
        def commit_plan(self, plan, now):
            self.commits.append((plan.kind, plan.box_id, now))

    controller = Scripted()
    result = simulate([Arrival(0, 1, i) for i in range(8)],
        SimConfig(ramp_capacity=3, n_per_pallet=100, pallet_staging_slots=k),
        decision_policy=controller, arrival_mode="immediate")
    assert result.processed_boxes == result.total_boxes == 8
    assert result.stop_reason is None
    assert result.makespan == (8-k)*14 + k*(6+10)
    assert result.robot_busy_time == result.makespan
    assert result.pallet_staging == dict(pallet_staging_slots=k, staging_parks=k,
        staging_unparks=k, staging_max_occupancy=k, staging_remaining=0, makespan=result.makespan)
    assert controller.calls[0] == (0, [1, 2, 3], [])
    assert controller.calls[1] == (6, [1, 3, 4], [2])
    assert {b for _, b, _ in controller.commits} == set(range(1, 9))
    unpark_times = [t for kind, _, t in controller.commits if kind == "UNPARK"]
    assert unpark_times[-1] == result.makespan
    assert all(b-a == 10 for a, b in zip(unpark_times, unpark_times[1:]))


def test_floor_slot_rejects_stacking():
    staging = PalletStagingState([None])
    staging.park(0, sim_box(1))
    with pytest.raises(ValueError, match="occupied"):
        staging.park(0, sim_box(2))
    assert staging.unpark(0).box_id == 1
    with pytest.raises(ValueError, match="empty"):
        staging.unpark(0)


@pytest.mark.parametrize("mode,expected", [("planar", 2), ("planar+stand_hw", 1)])
def test_narrow_orientation_relative_median_ties_and_exclusion(mode, expected):
    boxes = [box(1, 200, 200, 20), box(2, 200, 30, 100), box(3, 200, 30, 80),
             *[box(i, 200, 200, 200) for i in range(4, 9)]]
    # Volume breaks the equal side tie between 2 and 3.
    candidates = narrow_candidates({1: boxes}, boxes, orientation_mode=mode, fraction=.75,
                                   excluded_id=3)
    assert candidates[0][2].box_id == expected
    scaled = [replace(b, length_mm=b.length_mm*3, width_mm=b.width_mm*3,
                      height_mm=b.height_mm*3) for b in boxes]
    assert [c[2].box_id for c in candidates] == [c[2].box_id for c in narrow_candidates(
        {1: scaled}, scaled, orientation_mode=mode, fraction=.75, excluded_id=3)]


def test_reserve_requires_full_belt_upstream_and_excludes_selected_box():
    p = policy()
    boxes = [box(1, 20), box(2, 30), box(3), box(4), box(5)]
    physical = RampState(1, 5, 0, deque(sim_box(b.box_id, b.length_mm) for b in boxes), deque([sim_box(6)]))
    chosen = PickPlan(1, 0, 1, 1, None, 1, 0)
    staging = PalletStagingState([None]*3)
    def choose():
        return p._staging_action(chosen, {1: physical}, {1: boxes}, boxes, staging, [])
    assert choose().kind == "PARK"
    assert choose().box_id == 2
    physical.upstream.clear()
    assert choose() == chosen
    physical.upstream.append(sim_box(6))
    physical.capacity += 1
    assert choose() == chosen


def test_free_sides_with_envelope_walls_keeps_legacy_default():
    p = Placement(-20, -20, 0, False, 0, 100, 100, 100)
    neighbor = replace(p, x_mm=80)
    assert free_sides(p, [neighbor]) == 3
    assert free_sides(p, [neighbor], bounds=(-20, -20, 1220, 820)) == 1
    assert free_sides(replace(p, x_mm=0, y_mm=0), [], bounds=(-20, -20, 1220, 820)) == 4
    assert free_sides(p, [replace(neighbor, z_mm=100)], bounds=(-20, -20, 1220, 820)) == 2


@pytest.mark.parametrize("stacking", ["heightfield", "layers"])
def test_hole_filters_positions_before_selection(stacking):
    pallet = PalletModel(PalletSpec(300, 300, 100, overhang_mm=20), stacking_mode=stacking)
    neighbor = Placement(80, -20, 0, False, 0, 240, 100, 100, box_id=99)
    pallet.commit_place(PlacementPreview(True, neighbor, 0, 0))
    preview = SchedulerV1()._preview_place(pallet, box(1), hole_only=True)
    assert preview.feasible
    assert free_sides(preview.placement, pallet.placements, bounds=(-20, -20, 320, 320)) <= 1
    assert not PalletModel(PalletSpec(), stacking_mode=stacking).preview_place(
        box(1), placement_filter=lambda _: False).feasible


@pytest.mark.parametrize("micro", [False, True])
def test_hole_release_and_no_other_feasible_escape(micro):
    staged, belt = box(10, 250, 250, 100), box(1, 50, 50, 100)
    pallet = PalletModel(PalletSpec(300, 300, 100), stacking_mode="heightfield")
    engine = SchedulerV1(SchedulerConfig(lookahead_k=15, micro_plan_enabled=micro,
        micro_plan_node_budget=57, micro_plan_value="rollout", micro_plan_rollout_leaves=2))
    state = SchedulerSimState(0, {1: [belt], STAGING_RAMP_ID: [staged]}, {1: pallet}, set(),
        ramp_states={1: SchedulerRampState((belt,), (), 15),
                     STAGING_RAMP_ID: SchedulerRampState((staged,), (), 1, True)}, staging_hole=True)
    assert engine.choose_action(state).box_id == belt.box_id  # No hole on empty pallet.
    blocked = replace(belt, length_mm=301, width_mm=301)
    state = replace(state, ramps={1: [blocked], STAGING_RAMP_ID: [staged]},
        ramp_states={1: SchedulerRampState((blocked,), (), 15),
                     STAGING_RAMP_ID: state.ramp_states[STAGING_RAMP_ID]})
    assert engine.choose_action(state).box_id == staged.box_id


def test_full_without_narrow_escapes_hole_and_staging_survives_closure():
    p = policy(k=1)
    staged = sim_box(2, 250, 250, 100)
    staging = PalletStagingState([staged])
    ramp = RampState(1, 15, 0, deque([sim_box(1, 200, 200, 100)]))
    plan = p.choose_action(ramps={1: ramp}, destinations={}, now=0, pallet_staging=staging)
    assert plan.kind == "UNPARK"  # Full, no narrow belt box relative to visible median.
    p.on_changeover_start(1, "HEIGHT_LIMIT")
    assert staging.slots == [staged]
    ramp.queue.clear()
    plan = p.choose_action(ramps={1: ramp}, destinations={}, now=60, pallet_staging=staging)
    assert plan.kind == "UNPARK"
    p.commit_plan(plan, 70)
    assert p.export_committed_placements()["1"][-1]["box_id"] == 2


def test_root_and_rollouts_always_include_staging_despite_small_windows(monkeypatch):
    engine = SchedulerV1(SchedulerConfig(micro_plan_enabled=True, micro_plan_node_budget=57,
        micro_plan_value="rollout", micro_plan_window_total=1, micro_plan_topk_per_step=1,
        micro_plan_rollout_leaves=1, micro_plan_depth=1,
        rollout_future_boxes=1, rollout_prior="generic"))
    belt, staged = [box(1), box(2)], [box(i, 50, 50, 50) for i in (3, 4, 5)]
    state = SchedulerSimState(0, {1: belt, -1: staged}, {1: PalletModel()}, set(),
        ramp_states={1: SchedulerRampState(tuple(belt), (), 15),
                     -1: SchedulerRampState(tuple(staged), (), 3, True)})
    root = engine._build_beam_ramps(state)
    assert [(a.ramp_id, a.buffer_index) for a in engine._enumerate_beam_actions(root)] == [
        (1, 0), (-1, 0), (-1, 1), (-1, 2)]
    seen = []
    original = SchedulerV1._choose_action_greedy
    def record(self, sim_state, **kw):
        seen.extend(b.box_id for b in sim_state.ramps.get(-1, []))
        return original(self, sim_state, **kw)
    monkeypatch.setattr(SchedulerV1, "_choose_action_greedy", record)
    assert engine.choose_action(state) is not None
    assert set(seen) & {3, 4, 5}


def test_zero_is_inert_including_dumps_and_m2(tmp_path):
    args = dict(excel_path="data/Flujo_smoke_60.xlsx", model="M1", n_per_pallet=999999,
        t_pick_place=14, staging_cap=0, out_path=None, policy="palca", lookahead_k=15,
        arrival_mode="immediate", force_destination=1, max_pallets=1,
        continuous_pallets=True, time_budget_ms=0)
    import json
    a = run_simulation(**args, dump_placements_path=str(tmp_path/"absent.json"))
    b = run_simulation(**args, pallet_staging_slots=0, dump_placements_path=str(tmp_path/"zero.json"))
    assert json.loads((tmp_path/"absent.json").read_text()) == json.loads((tmp_path/"zero.json").read_text())
    assert a["metrics"]["makespan"] == b["metrics"]["makespan"]
    assert "pallet_staging" not in b["metrics"] and "pallet_staging_slots" not in b["params"]
    arrivals = [Arrival(0, 1, i) for i in range(4)]
    m2 = SimConfig(model="M2", ramp_capacity=2, staging_capacity=1, n_per_pallet=1)
    assert simulate(arrivals, m2).to_dict() == simulate(arrivals, replace(m2, pallet_staging_slots=0)).to_dict()
    with pytest.raises(ValueError, match="M1"):
        simulate(arrivals, replace(m2, pallet_staging_slots=1))


def test_tuning_configs_and_window18_root():
    configs = load_configs(Path("configs/sweeps/pulmon_tune.yaml"))
    assert len(configs) == 14
    assert all(c["overrides"]["micro_plan_hard_cap_ms"] == 600000 for c in configs)
    assert all(c["overrides"]["stand_hw_height_margin_gate_mm"] == 2400 for c in configs)
    p = PolicyPackerScheduler.from_defaults(lookahead_k=18)
    ramp = RampState(1, 18, 0, deque(sim_box(i) for i in range(18)))
    boxes = p._collect_ramp_boxes({1: ramp})
    state = SchedulerSimState(0, boxes, {}, set(), ramp_states=p._collect_ramp_states({1: ramp}))
    assert len(p._scheduler._enumerate_beam_actions(p._scheduler._build_beam_ramps(state))) == 18


def test_staging_shares_exact_root_visibility_and_never_reads_upstream_geometry(monkeypatch):
    class Hidden:
        def __len__(self):
            return 1000
        def __iter__(self):
            raise AssertionError("Read hidden upstream geometry")
    p = policy()
    ramps = {rid: RampState(rid, 15, 0, deque(sim_box(100*rid+i) for i in range(15)), Hidden())
             for rid in (1, 2)}
    seen = []
    def capture(**kwargs):
        seen.append(kwargs["sim_state"])
        return None, None, {}, None, {}
    monkeypatch.setattr(p, "_run_scheduler_attempt", capture)
    staging = PalletStagingState([sim_box(999), None, None])
    assert p.choose_action(ramps=ramps, destinations={}, now=0, pallet_staging=staging) is None
    assert sum(len(q) for rid, q in seen[0].ramps.items() if rid != -1) == 15
    assert len(seen[0].ramps[-1]) == 1


def test_d022_tail_permutation_including_park_unpark():
    class ChangedVisible(Exception):
        pass
    dimensions = [(100, 100, 100) if i % 5 else (30, 100, 100) for i in range(125)]
    permuted = dimensions[:100] + random.Random(42).sample(dimensions[100:], len(dimensions)-100)
    first_changed = next(i+1 for i, (a, b) in enumerate(zip(dimensions, permuted)) if a != b)
    traces = []
    for stream in (dimensions, permuted):
        p = policy(stacking_mode="heightfield", orientation_mode="planar+stand_hw",
                   stand_hw_height_margin_gate_mm=2400, accessibility_delta_mm=400,
                   score_mode="min_height_slack_then_gain", height_slack_mm=120,
                   access_debt_mode="penalty", access_debt_weight=.25, access_debt_band_mm=600)
        p.config = replace(p.config, pallet_spec=PalletSpec(300, 200, 300, overhang_mm=20))
        trace = []
        choose = p.choose_action
        def record(*, ramps, destinations, now, pallet_staging):
            if any(b.box_id >= first_changed for r in ramps.values() for b in r.queue):
                raise ChangedVisible
            plan = choose(ramps=ramps, destinations=destinations, now=now, pallet_staging=pallet_staging)
            trace.append((now, None if plan is None else (plan.kind, plan.box_id,
                plan.staging_slot, plan.preview.placement if plan.preview else None)))
            return plan
        p.choose_action = record
        arrivals = [Arrival(0, 1, i, *dims) for i, dims in enumerate(stream)]
        with pytest.raises(ChangedVisible):
            simulate(arrivals, SimConfig(pallet_staging_slots=3), decision_policy=p,
                     arrival_mode="immediate", continuous_pallets=True)
        traces.append(trace)
    assert traces[0] == traces[1]
    actions = [step[1][0] for step in traces[0] if step[1]]
    assert "PARK" in actions and "UNPARK" in actions
    assert len(actions) >= 86
