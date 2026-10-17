"""Deterministic future priors, independent of stream loaders and datasets.

Weights and inclusive integer millimetre ranges: docs/CONTEXT.md §3.
Published percentages sum to 101; sample integer weights / 101, not counts.
SHA256 counter sampling avoids global RNG state and Python hash randomization.
Generic ranges are an independent broad design assumption, not catalog data.
"""
from __future__ import annotations

import hashlib
import json

from ..domain.box import Box

# name, weight, width, length, height
CATALOG = (
    ("W450.L650", 77, (405, 465), (430, 645), (325, 415)),
    ("W400.L650", 10, (350, 400), (410, 635), (330, 435)),
    ("W400.L400", 7, (330, 335), (380, 390), (260, 295)),
    ("W450.L685", 3, (440, 450), (655, 660), (360, 400)),
    ("PICKING", 2, (445, 480), (605, 700), (360, 460)),
    ("W400.L685", 2, (355, 365), (655, 660), (370, 395)),
)

# A single broad type, defined without reference to any catalog constant.
GENERIC = (("GENERIC", 1, (300, 500), (350, 750), (250, 500)),)


def observed_seed(pallets, ramps) -> bytes:
    """Only placed geometry and visible boxes, never upstream/counts/episode seed."""
    state = (
        [(pid, [(p.x_mm, p.y_mm, p.z_mm, p.length_mm, p.width_mm, p.height_mm)
                 for p in pallet.placements]) for pid, pallet in pallets.items()],
        [(rid, [(b.length_mm, b.width_mm, b.height_mm, b.destination) for b in boxes])
         for rid, boxes in sorted(ramps.items())],
    )
    return hashlib.sha256(json.dumps(state, separators=(",", ":"), allow_nan=False).encode()).digest()


def sample_catalog(seed: bytes, sample: int, count: int, destinations,
                   prior: str = "catalog") -> list[Box]:
    """Independent uniform integer dimensions; destination from observed destinations.

    Rejection sampling gives exact discrete uniforms. Synthetic arrivals are
    revealed one at a time in this order and never use actual future metadata.
    The default preserves the original H6 catalog sample sequence exactly.
    """
    if prior == "generic":
        types = GENERIC
    elif prior == "uniform_types":
        types = tuple((name, 1, wr, lr, hr) for name, _, wr, lr, hr in CATALOG)
    elif prior == "catalog":
        types = CATALOG
    else:
        raise ValueError(f"Unknown rollout prior: {prior}")
    counter = 0

    def uniform(n):
        nonlocal counter
        limit = 2**64 - 2**64 % n
        while True:
            value = int.from_bytes(hashlib.sha256(
                seed + f":{sample}:{counter}".encode()).digest()[:8], "big")
            counter += 1
            if value < limit:
                return value % n

    boxes = []
    for index in range(count):
        ticket = uniform(sum(row[1] for row in types))
        for _, weight, wr, lr, hr in types:
            ticket -= weight
            if ticket < 0:
                break
        width, length, height = [lo + uniform(hi-lo+1) for lo, hi in (wr, lr, hr)]
        destination = destinations[uniform(len(destinations))]
        boxes.append(Box(f"h6:{sample}:{index}", length, width, height, 0,
                         destination=destination))
    return boxes
