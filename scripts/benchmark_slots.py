#!/usr/bin/env python3
"""H2: deterministic geometry benchmark (kernel and unshared grid cost)."""
import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from palca.domain.placement import Placement
from palca.scoring.access_debt import build_heightfield
from palca.scoring.slots import narrow_slot_penalty


def benchmark(iterations=5000):
    rng = np.random.default_rng(1001)
    placements = [Placement(int(rng.integers(-20, 850)), int(rng.integers(-20, 450)),
                            int(rng.integers(0, 1800)), False, 0, 355, 355, 355)
                  for _ in range(40)]
    field = build_heightfield(placements, 1240, 840, origin_mm=(-20, -20))
    candidates = [(int(rng.integers(-20, 620)), int(rng.integers(-20, 395)), 605, 445)
                  for _ in range(100)]
    def measure(operation, n):
        for i in range(100):
            operation(i)
        times = []
        for i in range(n):
            started = perf_counter()
            operation(i)
            times.append((perf_counter() - started) * 1000)
        return dict(mean_ms=float(np.mean(times)), p95_ms=float(np.percentile(times, 95)))
    result = dict(kernel=measure(lambda i: narrow_slot_penalty(field, candidates[i % 100], 900, 355), iterations),
                  grid_build=measure(lambda i: build_heightfield(placements, 1240, 840, origin_mm=(-20, -20)), 500),
                  iterations=iterations, grid_mm=10, placements=40, envelope_mm=[1240, 840])
    result['unshared_total_mean_ms'] = result['kernel']['mean_ms'] + result['grid_build']['mean_ms']
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('out/h2_benchmark.json'))
    args = parser.parse_args()
    result = benchmark()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
