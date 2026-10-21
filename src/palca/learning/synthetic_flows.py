"""Synthetic arrivals only: no file, Excel or dump loaders."""
from __future__ import annotations

import hashlib

import numpy as np

from ..domain.box import Box
from ..scoring.catalog_prior import sample_catalog


def synthetic_flow(seed: int, prior: str, count: int = 186) -> list[Box]:
    if prior not in {"generic", "families", "mix"}:
        raise ValueError(f"Unknown synthetic prior: {prior}")
    if count < 0:
        raise ValueError("count must be nonnegative")
    key = hashlib.sha256(f"palca-g1-synthetic-v1:{prior}:{seed}".encode()).digest()
    effective = ("generic" if seed % 2 == 0 else "families") if prior == "mix" else prior
    if effective == "generic":
        return sample_catalog(key, 0, count, (1,), prior="generic")
    rng = np.random.default_rng(int.from_bytes(key, "big"))
    k = int(rng.integers(1, 13))
    # Match H6's width, length, height ranges, including both endpoints.
    sizes = rng.integers([300, 350, 250], [501, 751, 501], size=(k, 3))
    weights = rng.dirichlet(np.full(k, 0.5))
    families = rng.choice(k, size=count, p=weights)
    return [Box(f"g1:{index}", int(sizes[f, 1]), int(sizes[f, 0]),
                int(sizes[f, 2]), 0, destination=1)
            for index, f in enumerate(families)]
