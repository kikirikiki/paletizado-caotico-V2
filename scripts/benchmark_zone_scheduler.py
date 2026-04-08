#!/usr/bin/env python3
"""
Standalone benchmark for ZoneScheduler.

Usage:
  python scripts/benchmark_zone_scheduler.py \
    --excel "data/Flujo rampas - Editado.xlsx" \
    --seeds 50021 50022 50023 50024 50025
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.controls import (
    AccessibilityConfig,
    ControlConfig,
    StabilityConfig,
)
from palca.packer.pallet_model import PalletModel
from palca.scheduler.zone_scheduler import ZoneScheduler
from palca.packer.zones import PALLET_ZONES
from palca.tuning.episodes import apply_shuffle
from sim.io import load_arrivals
from sim.des import Arrival


def _make_box(arrival: Arrival, idx: int) -> Box:
    return Box(
        box_id=idx,
        length_mm=int(arrival.length_mm or 600),
        width_mm=int(arrival.width_mm or 400),
        height_mm=int(arrival.height_mm or 350),
        timestamp=float(arrival.time),
        destination=arrival.destination,
        weight_kg=arrival.weight_kg,
    )


def _make_pallet() -> PalletModel:
    spec = PalletSpec(overhang_mm=20)
    control_config = ControlConfig(
        stability=StabilityConfig(
            mode="ratio+corners+settle",
            min_support_ratio=0.85,
        ),
        accessibility=AccessibilityConfig(accessibility_delta_mm=400),
    )
    return PalletModel(
        spec=spec,
        heuristic="bssf",
        stacking_mode="heightfield",
        control_config=control_config,
    )


def run_seed(
    arrivals: list[Arrival],
    seed: int,
    scheduler: ZoneScheduler,
    verbose: bool = False,
) -> dict:
    # Shuffle and force destination=1
    ordered = sorted(arrivals, key=lambda a: int(a.row_idx))
    shuffled = apply_shuffle(ordered, seed=seed, window=15, strength=0.5)
    flow = [
        Arrival(
            time=a.time,
            destination=1,
            row_idx=idx,
            length_mm=a.length_mm,
            width_mm=a.width_mm,
            height_mm=a.height_mm,
            weight_kg=a.weight_kg,
            priority=a.priority,
        )
        for idx, a in enumerate(shuffled, start=1)
    ]

    # Build box list
    boxes = [_make_box(a, i) for i, a in enumerate(flow)]

    pallet = _make_pallet()

    # Initialize buffer
    buffer_size = scheduler.buffer_size
    buffer: list[Box] = list(boxes[:buffer_size])
    remaining: list[Box] = list(boxes[buffer_size:])

    boxes_placed = 0
    max_zone_delta = 0
    step_log = []

    while True:
        result = scheduler.step(pallet, buffer)
        if result is None:
            break

        box, preview = result
        pallet.commit_place(preview)
        boxes_placed += 1

        # Replace used box in buffer
        buffer.remove(box)
        if remaining:
            buffer.append(remaining.pop(0))

        # Record zone heights
        heights = scheduler.compute_zone_heights(pallet)
        h_vals = list(heights.values())
        delta = max(h_vals) - min(h_vals)
        if delta > max_zone_delta:
            max_zone_delta = delta

        zone_name = preview.debug.get("zone", "?") if preview.debug else "?"
        step_log.append({
            "step": boxes_placed,
            "zone": zone_name,
            "heights": dict(heights),
            "delta_mm": delta,
        })

        if verbose:
            print(
                f"  step {boxes_placed:3d} | zone={zone_name} | "
                f"A={heights['A']:4d} B={heights['B']:4d} "
                f"C={heights['C']:4d} D={heights['D']:4d} | "
                f"delta={delta:4d}mm"
            )

    # Final heights
    final_heights = scheduler.compute_zone_heights(pallet)

    return {
        "seed": seed,
        "boxes_placed": boxes_placed,
        "max_zone_delta_mm": max_zone_delta,
        "final_heights": final_heights,
        "steps": step_log,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="ZoneScheduler benchmark")
    parser.add_argument("--excel", required=True, help="Path to Excel file")
    parser.add_argument("--seeds", type=int, nargs="+",
                        default=[50021, 50022, 50023, 50024, 50025])
    parser.add_argument("--verbose", action="store_true",
                        help="Print step-by-step zone heights")
    args = parser.parse_args()

    print(f"Loading arrivals from {args.excel} ...")
    arrivals = load_arrivals(args.excel)
    print(f"Loaded {len(arrivals)} arrivals")

    scheduler = ZoneScheduler(
        zones=list(PALLET_ZONES),
        delta_max_mm=400,
        buffer_size=15,
    )

    results = []
    for seed in args.seeds:
        print(f"\n--- Seed {seed} ---")
        result = run_seed(arrivals, seed, scheduler, verbose=args.verbose)
        results.append(result)
        fh = result["final_heights"]
        print(
            f"  boxes_placed={result['boxes_placed']} | "
            f"max_delta={result['max_zone_delta_mm']}mm | "
            f"final: A={fh['A']} B={fh['B']} C={fh['C']} D={fh['D']}"
        )

    # Summary
    placed = [r["boxes_placed"] for r in results]
    deltas = [r["max_zone_delta_mm"] for r in results]
    print(f"\n{'='*60}")
    print(f"SUMMARY over {len(results)} seeds:")
    print(f"  boxes_placed: {placed}  mean={sum(placed)/len(placed):.1f}")
    print(f"  max_zone_delta_mm: {deltas}  max={max(deltas)}")
    print(f"  target: >= 22 boxes/pallet, max_delta <= 400mm")
    target_ok = [p >= 22 for p in placed]
    delta_ok = [d <= 400 for d in deltas]
    print(f"  target_ok: {target_ok} ({sum(target_ok)}/{len(target_ok)} seeds)")
    print(f"  delta_ok:  {delta_ok} ({sum(delta_ok)}/{len(delta_ok)} seeds)")


if __name__ == "__main__":
    main()
