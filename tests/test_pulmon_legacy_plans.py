"""Legacy policy plans remain PICK actions without staging attributes."""
from collections import deque
from types import SimpleNamespace

import pytest

from palca.integration.policy_packer_sched import PolicyPackerScheduler
from sim.des import Arrival, Box, PalletStagingState, RampState, SimConfig, simulate


@pytest.mark.parametrize("slots", [0, 1])
def test_policy_counts_legacy_plan_as_pick(monkeypatch, slots):
    policy = PolicyPackerScheduler.from_defaults(pallet_staging_slots=slots)
    plan = SimpleNamespace(ramp_id=1, buffer_index=0, box_id=1, pallet_id=1, dt_extra=2.0)
    monkeypatch.setattr(policy, "_run_scheduler_attempt", lambda **kw: (plan, None, {}, None, {}))
    ramp = RampState(1, 1, 0, deque([Box(1, 0, 1, 1, 100, 100, 100)]))
    kwargs = {"pallet_staging": PalletStagingState([None])} if slots else {}

    assert policy.choose_action(ramps={1: ramp}, destinations={}, now=0, **kwargs) is plan
    assert policy.total_picks == 1
    assert policy.dt_extra_total == 2.0


@pytest.mark.parametrize("slots", [0, 1])
@pytest.mark.parametrize("explicit_kind", [False, True])
def test_des_pick_needs_no_staging_attributes(slots, explicit_kind):
    class LegacyPolicy:
        def __init__(self):
            self.commits = []

        def choose_action(self, *, ramps, destinations, now, **kwargs):
            if not ramps[1].queue:
                return None
            box = ramps[1].queue[0]
            plan = SimpleNamespace(ramp_id=1, buffer_index=0, box_id=box.box_id, dt_extra=2.0)
            if explicit_kind:
                plan.kind = "PICK"
            return plan

        def commit_plan(self, plan, time):
            self.commits.append((plan.box_id, time))

    policy = LegacyPolicy()
    result = simulate([Arrival(0, 1, 0), Arrival(0, 1, 1)],
                      SimConfig(pallet_staging_slots=slots),
                      decision_policy=policy, arrival_mode="immediate")

    assert result.processed_boxes == 2
    assert result.makespan == 32.0
    assert policy.commits == [(1, 16.0), (2, 32.0)]
    assert result.stop_reason is None
    if slots:
        assert result.pallet_staging["staging_parks"] == 0
        assert result.pallet_staging["staging_unparks"] == 0
