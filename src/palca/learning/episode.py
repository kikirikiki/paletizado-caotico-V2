"""Complete synthetic FIFO episodes; all configuration I/O happens at import.

Official microsearch exposes only the winner and aggregate statistics, not root
plans/values. Consequently official epsilon is rejected and G3 roots are omitted.
Greedy exposes scored roots through its selection callback: retain its top three,
ranked by repeated application of the unchanged production selector. Their values
are immediate scalar scores, NOT microsearch rollout values.

Each row is before a successful placement. cajas_hasta_cierre includes that
placement. The final open pallet's decision rows are censored, even if full.
"""
from __future__ import annotations

import hashlib
from inspect import Parameter, signature
from pathlib import Path

import numpy as np
import yaml

from ..integration.policy_packer_sched import PolicyPackerScheduler
from ..scheduler.scheduler_v1 import SchedulerSimState, SchedulerV1
from ..scoring.learned_value import envelope_volume, pallet_volume
from ..scoring.synthetic_value_data import training_policy
from ..study.params import preset_params
from sim.run import run_simulation
from .state_encoding import encode_state
from .synthetic_flows import synthetic_flow


def _official_kwargs():
    """Same preset/default merge and argument renaming as run_sweep -> sim.run."""
    path = Path(__file__).resolve().parents[3] / "configs/sweeps/pulmon_tune.yaml"
    config = next(c for c in yaml.safe_load(path.read_text()) if c["name"] == "h6_sin_gate")
    params = {k: p.default for k, p in signature(run_simulation).parameters.items()
              if p.default is not Parameter.empty}
    overrides = dict(config["overrides"], micro_plan_hard_cap_ms=600000)
    params.update(preset_params(config["preset"], **overrides))
    aliases = dict(min_support_ratio="min_support", heartbeat_sec="watchdog_heartbeat_sec",
                   micro_plan_enabled="micro_plan", micro_plan_depth="micro_depth",
                   micro_plan_width="micro_width", micro_plan_topk_per_step="micro_topk")
    return {k: params[aliases.get(k, k)] for k in signature(
        PolicyPackerScheduler.from_defaults).parameters if aliases.get(k, k) in params}


OFFICIAL_KWARGS = _official_kwargs()


def official_policy():
    return PolicyPackerScheduler.from_defaults(**OFFICIAL_KWARGS)


class _RootGreedy(SchedulerV1):
    """Capture the existing greedy selection boundary without changing ranking."""

    def _choose_scored_candidate(self, candidates, sim_state, *, undo_debt=False):
        selected, stats = super()._choose_scored_candidate(
            candidates, sim_state, undo_debt=undo_debt)
        roots, remaining = [], list(candidates)
        while remaining and len(roots) < 3:
            candidate, _ = super()._choose_scored_candidate(
                remaining, sim_state, undo_debt=undo_debt)
            roots.append(candidate)
            remaining = [c for c in remaining if c.plan is not candidate.plan]
        self.roots = roots
        return selected, stats


def _placement(plan):
    p = plan.preview.placement
    return (p.x_mm, p.y_mm, p.z_mm, p.length_mm, p.width_mm, p.height_mm)


def run_episode(seed, prior, policy, epsilon=0.0, *, count=186) -> dict:
    """Pure in-memory episode; no upstream is provided to either scheduler."""
    if policy not in {"official", "greedy"}:
        raise ValueError("policy must be official or greedy")
    if not 0 <= epsilon <= 1:
        raise ValueError("epsilon must be in [0, 1]")
    if policy == "official" and epsilon:
        raise ValueError("official search does not expose root candidates; epsilon is greedy-only")
    stream = synthetic_flow(seed, prior, count)
    packing = official_policy() if policy == "official" else training_policy()
    engine = packing._scheduler if policy == "official" else _RootGreedy(packing._scheduler.config)
    key = hashlib.sha256(f"palca-g1-exploration-v1:{seed}".encode()).digest()
    rng = np.random.default_rng(int.from_bytes(key, "big"))
    pallet = packing._new_pallet()
    queue, upstream = list(stream[:15]), iter(stream[15:])
    pending, rows = [], []
    closed = placed = 0
    while queue:
        state = SchedulerSimState(0, {1: queue}, {1: pallet}, set())
        if policy == "official":
            plan = engine.choose_action(state)
            if engine.last_micro_plan_stats.get("hard_cap_hits"):
                raise RuntimeError("official hard cap hit; refusing time-dependent training data")
        else:
            engine.roots = []
            plan = engine._choose_action_greedy(state, deadline=None, lookahead_k=15)
        if plan is None:
            if not pallet.placements:
                raise RuntimeError("Synthetic box cannot fit an empty production pallet")
            util = pallet_volume(pallet) / envelope_volume(pallet)
            for i, row in enumerate(pending):
                row.update(util_final=util, cajas_hasta_cierre=len(pending) - i, pallet_id=closed)
            rows.extend(pending)
            pending = []
            closed += 1
            pallet = packing._new_pallet()
            continue
        roots = engine.roots if policy == "greedy" else []
        if len(roots) > 1 and epsilon and rng.random() < epsilon:
            alternatives = [c for c in roots if c.plan is not plan]
            plan = alternatives[int(rng.integers(len(alternatives)))].plan
        row = encode_state(pallet, queue)
        row.update(belt_index=plan.buffer_index, placement=_placement(plan))
        if policy == "greedy":
            root_actions = np.full((3, 7), -1, dtype=np.int32)
            root_values = np.zeros(3, dtype=np.float64)
            for i, c in enumerate(roots):
                root_actions[i] = (c.plan.buffer_index, *_placement(c.plan))
                root_values[i] = c.terms.scalar_score
            row.update(root_actions=root_actions, root_values=root_values, root_count=len(roots))
        pending.append(row)
        pallet.commit_place(plan.preview)
        placed += 1
        queue.pop(plan.buffer_index)
        arrival = next(upstream, None)
        if arrival is not None:
            queue.append(arrival)
    schema = dict(height=((62, 42), np.uint16), boxes=((15, 4), np.float32),
                  scalars=((4,), np.float32), belt_index=((), np.int16),
                  placement=((6,), np.int32), util_final=((), np.float64),
                  cajas_hasta_cierre=((), np.int32), pallet_id=((), np.int32))
    if policy == "greedy":
        schema.update(root_actions=((3, 7), np.int32), root_values=((3,), np.float64),
                      root_count=((), np.int8))
    result = {k: np.asarray([row[k] for row in rows], dtype=dtype).reshape((-1, *shape))
              for k, (shape, dtype) in schema.items()}
    result.update(seed=seed, closed_pallets=closed, placed_boxes=placed,
                  censored_states=len(pending))
    return result
