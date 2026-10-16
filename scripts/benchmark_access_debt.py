#!/usr/bin/env python3
"""Deterministic H1 kernel benchmark, including amortized grid construction."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from palca.domain.placement import Placement
from palca.scoring.access_debt import access_debt, build_heightfield


def benchmark(iterations=5000):
    rng = np.random.default_rng(1001)
    placements = [Placement(int(rng.integers(-20, 850)), int(rng.integers(-20, 450)),
                            int(rng.integers(0, 1800)), False, 0, 355, 355, 355)
                  for _ in range(40)]
    field = build_heightfield(placements, 1240, 840, origin_mm=(-20, -20))
    candidates = [(int(rng.integers(-20, 620)), int(rng.integers(-20, 395)), 605, 445)
                  for _ in range(100)]
    results = {}
    for band in (200, 445):
        for footprint in candidates:  # Warmup
            access_debt(field, footprint, 1800, 400, band)
        times = []
        for i in range(iterations):
            started = perf_counter()
            access_debt(field, candidates[i % len(candidates)], 1800, 400, band)
            times.append((perf_counter() - started) * 1000)
        results[str(band)] = dict(mean_ms=float(np.mean(times)), p95_ms=float(np.percentile(times, 95)))
    times = []
    for _ in range(200):
        started = perf_counter()
        build_heightfield(placements, 1240, 840, origin_mm=(-20, -20))
        times.append((perf_counter() - started) * 1000)
    results['grid_build'] = dict(mean_ms=float(np.mean(times)), p95_ms=float(np.percentile(times, 95)))
    results['assumptions'] = dict(grid_mm=10, envelope_mm=[1240, 840], placements=40,
                                  candidates_per_decision=15, iterations=iterations,
                                  amortized_grid_ms_per_candidate=float(np.mean(times)) / 15)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('out/h1_benchmark.json'))
    args = parser.parse_args()
    result = benchmark()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
