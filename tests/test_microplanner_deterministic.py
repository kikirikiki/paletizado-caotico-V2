"""H3: strict attempted-node budget, legacy deadline and emergency watchdog."""
import json

import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1
from palca.integration.policy_packer_sched import PolicyPackerScheduler


def state():
    boxes = [Box(i, 200, 200, 100, timestamp=0, destination=1) for i in range(15)]
    return SchedulerSimState(0, {1: boxes}, {1: PalletModel(PalletSpec())}, set())


def scheduler(**kwargs):
    return SchedulerV1(SchedulerConfig(micro_plan_enabled=True, heartbeat_sec=0, **kwargs))


@pytest.mark.parametrize('budget', [1, 7, 15, 16, 100])
def test_budget_counts_attempts_exactly_and_finalizes_partial_depth(budget, monkeypatch):
    engine = scheduler(micro_plan_node_budget=budget, time_budget_ms=1)
    calls = []
    expand = engine._expand_beam_node

    def counted(**kw):
        calls.append(1)
        return expand(**kw)

    monkeypatch.setattr(engine, '_expand_beam_node', counted)
    assert engine.choose_action(state()) is not None
    assert len(calls) == budget
    assert engine.last_micro_plan_stats['nodes_expanded'] == budget
    assert engine.last_micro_plan_stats['cutoff_reason'] == 'node_budget'
    assert engine.hard_cap_hits == 0


def test_infeasible_attempts_count_and_fallback_has_only_hard_deadline(monkeypatch):
    engine = scheduler(micro_plan_node_budget=3, time_budget_ms=1)
    calls = []
    monkeypatch.setattr(engine, '_expand_beam_node', lambda **kw: calls.append(1))
    monkeypatch.setattr('palca.scheduler.scheduler_v1.time.perf_counter', lambda: 100.0)
    deadlines = []

    def greedy(sim_state, *, deadline, lookahead_k):
        deadlines.append(deadline)
        return None

    monkeypatch.setattr(engine, '_choose_action_greedy', greedy)
    engine.choose_action(state())
    assert len(calls) == 3
    assert deadlines == [110.0]  # 10 s watchdog, never the legacy 1 ms limit.
    assert engine.micro_plan_fallback_greedy == 1
    assert engine.last_micro_plan_stats['nodes_expanded'] == 3


def test_zero_budget_preserves_legacy_deadline_and_feasible_count(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('palca.scheduler.scheduler_v1.time.perf_counter', lambda: clock[0])
    engine = scheduler(micro_plan_node_budget=0, time_budget_ms=100)
    calls = []

    def expand(**kw):
        calls.append(1)
        clock[0] += .06
        return None

    monkeypatch.setattr(engine, '_expand_beam_node', expand)
    assert engine.choose_action(state()) is None
    assert len(calls) == 2
    assert engine.last_micro_plan_stats['cutoff_reason'] == 'time_budget'
    assert engine.last_micro_plan_stats['nodes_expanded'] == 0
    assert engine.hard_cap_hits == 0


def test_hard_cap_interrupts_and_marks_nondeterministic(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('palca.scheduler.scheduler_v1.time.perf_counter', lambda: clock[0])
    engine = scheduler(micro_plan_node_budget=100, micro_plan_hard_cap_ms=10)
    calls = []

    def expand(**kw):
        calls.append(1)
        clock[0] += .02
        return None

    monkeypatch.setattr(engine, '_expand_beam_node', expand)
    monkeypatch.setattr(engine, '_choose_action_greedy', lambda *a, **kw: pytest.fail('No fallback after cap'))
    engine.choose_action(state())
    assert len(calls) == 1
    assert engine.hard_cap_hits == 1
    assert engine.last_micro_plan_stats['hard_cap_hits'] == 1
    assert engine.last_eval_stats['cutoff_reason'] == 'hard_cap'
    policy = PolicyPackerScheduler.from_defaults(micro_plan_enabled=True, micro_plan_node_budget=100)
    policy._scheduler = engine
    metrics = policy.collect_kpis()
    assert metrics['hard_cap_hits'] == 1
    assert metrics['micro_plan_deterministic'] is False


def test_defaults_propagation_and_validation():
    policy = PolicyPackerScheduler.from_defaults(micro_plan_enabled=True, micro_plan_node_budget=7,
                                                 micro_plan_hard_cap_ms=1234)
    assert policy._scheduler.config.micro_plan_node_budget == 7
    assert policy._scheduler.config.micro_plan_hard_cap_ms == 1234
    assert SchedulerConfig().micro_plan_node_budget == 0
    for kwargs in ({'micro_plan_node_budget': -1}, {'micro_plan_node_budget': 1.5},
                   {'micro_plan_hard_cap_ms': 0}, {'micro_plan_hard_cap_ms': -1}):
        with pytest.raises(ValueError):
            SchedulerConfig(**kwargs)
    with pytest.raises(ValueError):
        scheduler(micro_plan_node_budget=3, max_seconds_per_item=1)
    with pytest.raises(ValueError):
        PolicyPackerScheduler.from_defaults(micro_plan_enabled=True, micro_plan_node_budget=3, settle_timeout_ms=1)


@pytest.mark.slow
def test_canonical_excel_bit_exact_placements_and_kpi(tmp_path):
    from palca.study.metric import run_kpi, split_pallets
    from palca.study.params import preset_params
    from scripts.run_sweep import sweep_lock
    from sim.run import run_simulation

    results = []
    dumps = []
    node_counts = []
    with sweep_lock(retry_seconds=60):
        for repeat in range(2):
            dump = tmp_path / f'dump{repeat}.json'
            payload = run_simulation(**preset_params(
                'arm6_4orient', episode_seed=50021, multi_ramp_seed=50021,
                micro_plan=True, micro_plan_node_budget=7, time_budget_ms=1,
                access_debt_mode='penalty', access_debt_weight=.25, access_debt_band_mm=600,
                dump_placements_path=str(dump)))
            data = json.loads(dump.read_text())
            results.append(run_kpi(split_pallets(payload, data)))
            dumps.append(data)
            kpis = payload['metrics']['pallet_kpis']
            node_counts.append(kpis['micro_plan_nodes_expanded'])
            assert kpis['hard_cap_hits'] == 0
            assert kpis['micro_plan_deterministic'] is True
            assert kpis['micro_plan_nodes_expanded_max'] <= 7
            assert payload['params']['micro_plan_node_budget'] == 7
    assert dumps[0] == dumps[1]
    assert results[0] == results[1]
    assert results[0]['boxes_placed'] == 186
    assert node_counts[0] == node_counts[1]


def test_clock_speed_does_not_change_choice_or_nodes_below_hard_cap(monkeypatch):
    results = []
    for tick in (0.0, .001):
        clock = [0.0]

        def now():
            clock[0] += tick
            return clock[0]

        monkeypatch.setattr('palca.scheduler.scheduler_v1.time.perf_counter', now)
        engine = scheduler(micro_plan_node_budget=16, time_budget_ms=1)
        plan = engine.choose_action(state())
        results.append((plan, engine.micro_plan_nodes_expanded))
        assert engine.hard_cap_hits == 0
    assert results[0] == results[1]


def test_last_expansion_overrun_is_counted_without_next_attempt(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('palca.scheduler.scheduler_v1.time.perf_counter', lambda: clock[0])
    engine = scheduler(micro_plan_node_budget=15, micro_plan_depth=1, micro_plan_hard_cap_ms=10)
    expand = engine._expand_beam_node
    calls = []

    def slow_last(**kw):
        calls.append(1)
        result = expand(**kw)
        if len(calls) == 15:
            clock[0] = .02
        return result

    monkeypatch.setattr(engine, '_expand_beam_node', slow_last)
    assert engine.choose_action(state()) is not None
    assert len(calls) == 15
    assert engine.hard_cap_hits == 1
    assert engine.last_micro_plan_stats['cutoff_reason'] == 'hard_cap'


def test_batchfill_cannot_introduce_a_local_time_budget_in_h3(monkeypatch):
    engine = scheduler(micro_plan_node_budget=3, batchfill_layer_starter=True)
    monkeypatch.setattr('palca.scheduler.scheduler_v1.time.perf_counter',
                        lambda: pytest.fail('No local clock deadline in H3 batchfill'))
    assert engine._batchfill_deadline(1234.5) == 1234.5
    assert engine._batchfill_deadline(None) is None
