from __future__ import annotations

import random
from typing import Sequence, TypeVar


T = TypeVar("T")


def apply_shuffle(
    order: Sequence[T],
    *,
    seed: int | None,
    window: int,
    strength: float,
) -> list[T]:
    items = list(order)
    if int(window) <= 1 or float(strength) <= 0.0:
        return items

    rng = random.Random(0 if seed is None else int(seed))
    window_size = int(window)
    strength_value = float(strength)

    for start in range(0, len(items), window_size):
        stop = min(start + window_size, len(items))
        block = items[start:stop]
        if len(block) <= 1:
            continue

        if strength_value >= 1.0:
            rng.shuffle(block)
        else:
            n_swaps = max(1, int(round(strength_value * window_size)))
            for _ in range(n_swaps):
                i = rng.randrange(len(block))
                j = rng.randrange(len(block))
                block[i], block[j] = block[j], block[i]

        items[start:stop] = block

    return items
