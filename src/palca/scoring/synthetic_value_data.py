"""Synthetic-only R13 data generation. No loaders, paths or dataset inputs.

Each 186-box episode is a separate GENERIC draw. Use production physical
controls and H1 greedy over the 15 visible boxes, without clock cutoffs.
Labels belong to physically closed pallets only; END pallets are censored.
"""
from __future__ import annotations

import hashlib

import numpy as np

from ..integration.policy_packer_sched import PolicyPackerScheduler
from ..scheduler.scheduler_v1 import SchedulerSimState
from .catalog_prior import sample_catalog
from .learned_value import envelope_volume, pallet_volume, state_features

EPISODE_BOXES = 186
POLICY = "H1-greedy-visible15-production-controls-v1"


def synthetic_stream(seed: int, count: int = EPISODE_BOXES):
    key = hashlib.sha256(f"palca-r13-synthetic-v1:{seed}".encode()).digest()
    return sample_catalog(key, 0, count, (1,), prior="generic")


def training_policy():
    return PolicyPackerScheduler.from_defaults(
        lookahead_k=15, overhang_mm=20, heuristic="bssf", stacking_mode="heightfield",
        stability_mode="ratio+corners+settle", min_support_ratio=.85,
        orientation_mode="planar+stand_hw", stand_hw_height_margin_gate_mm=2400,
        accessibility_delta_mm=400, score_mode="min_height_slack_then_gain", height_slack_mm=120,
        access_debt_weight=.25, access_debt_band_mm=600, heartbeat_sec=0,
    )


def generate_episode(seed: int) -> dict:
    """No file I/O, including stream reads; no actual future is passed to H1."""
    stream = synthetic_stream(seed)
    policy = training_policy()
    engine = policy._scheduler
    pallet = policy._new_pallet()
    queue, upstream = list(stream[:15]), iter(stream[15:])
    pending = [state_features(pallet)]
    features, labels, pallet_ids = [], [], []
    closed = 0
    placed = 0
    while queue:
        state = SchedulerSimState(0, {1: queue}, {1: pallet}, set())
        plan = engine._choose_action_greedy(state, deadline=None, lookahead_k=15)
        if plan is None:
            if not pallet.placements:
                raise RuntimeError("GENERIC box cannot fit an empty production pallet")
            label = pallet_volume(pallet) / envelope_volume(pallet)
            features.extend(pending)
            labels.extend([label] * len(pending))
            pallet_ids.extend([closed] * len(pending))
            closed += 1
            pallet = policy._new_pallet()
            pending = [state_features(pallet)]
            continue
        pallet.commit_place(plan.preview)
        placed += 1
        queue.pop(plan.buffer_index)
        arrival = next(upstream, None)
        if arrival is not None:
            queue.append(arrival)
        pending.append(state_features(pallet))
    return dict(seed=seed, x=np.asarray(features), y=np.asarray(labels),
                pallet_ids=np.asarray(pallet_ids), closed_pallets=closed,
                placed_boxes=placed, censored_states=len(pending))
