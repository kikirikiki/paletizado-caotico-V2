#!/usr/bin/env python3
"""Benchmark ZoneScheduler on real Excel data across multiple seeds."""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import Sequence, TypeVar

# Allow running as a standalone script
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sim.io import load_arrivals
from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel
from palca.packer.controls import ControlConfig, StabilityConfig, build_control_stack
from palca.packer.zones import PALLET_ZONES
from palca.scheduler.zone_scheduler import ZoneScheduler


T = TypeVar("T")

BUFFER_SIZE = 15


def _apply_shuffle(
    order: Sequence[T],
    *,
    seed: int,
    window: int,
    strength: float,
) -> list[T]:
    items = list(order)
    if window <= 1 or strength <= 0.0:
        return items
    rng = random.Random(seed)
    for start in range(0, len(items), window):
        stop = min(start + window, len(items))
        block = items[start:stop]
        if len(block) <= 1:
            continue
        if strength >= 1.0:
            rng.shuffle(block)
        else:
            n_swaps = max(1, int(round(strength * window)))
            for _ in range(n_swaps):
                i = rng.randrange(len(block))
                j = rng.randrange(len(block))
                block[i], block[j] = block[j], block[i]
        items[start:stop] = block
    return items


def _run_seed(excel_path: str, seed: int) -> dict:
    arrivals = load_arrivals(excel_path)

    shuffled = _apply_shuffle(arrivals, seed=seed, window=BUFFER_SIZE, strength=0.5)

    # Force all destinations to 1
    boxes: list[Box] = []
    for idx, a in enumerate(shuffled):
        lmm = int(a.length_mm) if a.length_mm is not None else 400
        wmm = int(a.width_mm) if a.width_mm is not None else 300
        hmm = int(a.height_mm) if a.height_mm is not None else 200
        boxes.append(
            Box(
                box_id=idx,
                length_mm=lmm,
                width_mm=wmm,
                height_mm=hmm,
                timestamp=float(a.time),
                destination=1,
                weight_kg=a.weight_kg,
                priority=a.priority,
            )
        )

    spec = PalletSpec(
        length_mm=1200,
        width_mm=800,
        max_height_mm=2400,
        overhang_mm=20,
        allow_rotate=True,
    )
    control_config = ControlConfig(
        stability=StabilityConfig(
            mode="ratio+corners+settle",
            min_support_ratio=0.85,
        )
    )
    pallet = PalletModel(
        spec=spec,
        heuristic="bssf",
        controls=build_control_stack(control_config),
    )
    scheduler = ZoneScheduler(
        zones=PALLET_ZONES,
        delta_max_mm=400,
        buffer_size=BUFFER_SIZE,
    )

    flow = list(boxes)
    buffer: list[Box] = flow[:BUFFER_SIZE]
    flow_idx = BUFFER_SIZE

    step_num = 0
    zone_placed: list[str] = []
    max_delta_observed = 0

    while True:
        result = scheduler.step(pallet, buffer)
        if result is None:
            break
        box, preview = result

        pallet.commit_place(preview)
        buffer.remove(box)
        if flow_idx < len(flow):
            buffer.append(flow[flow_idx])
            flow_idx += 1

        step_num += 1
        zone_name = (preview.debug or {}).get("zone", "?")
        zone_placed.append(zone_name)

        heights = scheduler.compute_zone_heights(pallet)
        delta = max(heights.values()) - min(heights.values())
        if delta > max_delta_observed:
            max_delta_observed = delta

    return {
        "seed": seed,
        "boxes_placed": step_num,
        "max_delta_mm": max_delta_observed,
        "zone_counts": {z: zone_placed.count(z) for z in "ABCD"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark ZoneScheduler")
    parser.add_argument("--excel", required=True, help="Path to arrivals Excel file")
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=int,
        default=[50021, 50022, 50023, 50024, 50025],
        help="Random seeds to run",
    )
    args = parser.parse_args()

    results = []
    for seed in args.seeds:
        r = _run_seed(args.excel, seed)
        results.append(r)
        print(
            f"seed={seed:6d}  placed={r['boxes_placed']:3d}  "
            f"max_delta={r['max_delta_mm']:4d}mm  "
            f"zones={r['zone_counts']}"
        )

    mean_placed = sum(r["boxes_placed"] for r in results) / len(results)
    print(f"\nSummary: mean boxes placed = {mean_placed:.1f} across {len(results)} seeds")


if __name__ == "__main__":
    main()
