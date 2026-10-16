#!/usr/bin/env python3
"""Part A only: formal single-pallet utilization bounds for arm6_4orient."""

from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
from math import ceil, isfinite, prod
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ortools

from palca.study.bounds_formal import is_dominant, pallet_bound
from palca.study.metric import ENVELOPE_MM, METRIC_VERSION
from palca.study.params import REFERENCE_EXCEL
from sim.io import load_arrivals


def read_baseline(path: Path) -> dict:
    """Read the arm6 KPI in a sweep summary; never substitute another preset.

    Accept a single-config {kpi: {mean, ci95}} summary or a configs/results
    mapping/list identified by arm6_4orient. Ambiguous/invalid files fail loudly.
    """
    if not path.exists():
        return {"kpi": 0.619, "ci95": None, "source": "provisional", "provisional": True}
    payload = json.loads(path.read_text())
    candidates = []

    def visit(node, route=(), selected=False):
        if isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, (*route, str(index)), selected)
        elif isinstance(node, dict):
            selected = selected or any(node.get(key) == "arm6_4orient"
                                       for key in ("name", "config", "preset", "config_name", "id"))
            if "kpi" in node or "kpi_mean" in node:
                raw = node.get("kpi", node.get("kpi_mean"))
                if isinstance(raw, dict):
                    value, ci95 = raw.get("mean"), raw.get("ci95")
                else:
                    value, ci95 = raw, node.get("ci95", node.get("kpi_ci95"))
                if isinstance(value, (int, float)):
                    candidates.append((selected, route, value, ci95))
            for key, child in node.items():
                visit(child, (*route, key), selected or key == "arm6_4orient")

    visit(payload)
    selected = [c for c in candidates if c[0]]
    if not selected:
        # A root KPI is unambiguous only in a single-config summary.
        named = isinstance(payload, dict) and any(key in payload for key in
                                                 ("name", "config", "preset", "config_name", "id"))
        selected = [c for c in candidates if not c[1]] if len(candidates) == 1 and not named else []
    if len(selected) != 1:
        raise ValueError(f"{path}: expected one unambiguous arm6_4orient KPI mean")
    _, route, kpi, ci95 = selected[0]
    if not isfinite(kpi) or not 0 < kpi <= 1:
        raise ValueError(f"{path}: KPI must be a finite ratio in (0, 1]")
    return {"kpi": kpi, "ci95": ci95, "source": str(path), "json_path": list(route),
            "provisional": False, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def moonshot(bound: dict, baseline: float) -> dict:
    upper = Fraction(bound["numerator"], bound["denominator"])
    target = Fraction(3, 2) * Fraction(str(baseline))
    excluded = upper < target
    return {"target": float(target), "excluded": excluded,
            "verdict": "ruled_out" if excluded else "not_ruled_out",
            "meaning": "An upper bound above target does not prove attainability."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--excel", type=Path, default=Path(REFERENCE_EXCEL))
    parser.add_argument("--baseline-summary", type=Path,
                        default=Path("out/sweeps/baseline_v1/summary.json"))
    parser.add_argument("--out", type=Path, default=Path("out/bounds/formal.json"))
    parser.add_argument("--seconds", type=float, default=30,
                        help="Time limit per CP-SAT solve, one worker (default 30)")
    args = parser.parse_args()
    if not isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be positive and finite")
    baseline = read_baseline(args.baseline_summary)
    boxes = [(a.length_mm, a.width_mm, a.height_mm) for a in load_arrivals(args.excel)]
    dominant = [d for d in boxes if is_dominant(d)]
    volume = sum(prod(d) for d in boxes)
    result = {"schema_version": 1, "metric_version": METRIC_VERSION,
              "ortools_version": ortools.__version__, "preset": "arm6_4orient",
              "envelope_mm": ENVELOPE_MM, "envelope_volume_mm3": prod(ENVELOPE_MM),
              "input": {"path": str(args.excel), "sha256": hashlib.sha256(args.excel.read_bytes()).hexdigest(),
                        "n_boxes": len(boxes), "volume_mm3": volume},
              "baseline": baseline,
              "trivial": {"fractional_pallets": volume / prod(ENVELOPE_MM),
                          "minimum_pallets": ceil(Fraction(volume, prod(ENVELOPE_MM))),
                          "one_pallet_upper_bound": 1.0},
              "dominant_definition": "Original L=600..610, W=440..450, H=345..395 mm (inclusive)",
              "assumptions": ["Exact orthogonal geometry, integer mm dimensions, no overlap or overhang beyond ENVELOPE_MM",
                              "LxW and HxW resting faces, both yaw rotations; HxL forbidden",
                              "Support, accessibility, stand-height gate, weight and arrival order relaxed",
                              "Single pallet can select any subset of the finite input inventory",
                              "Horizontal cuts respect the finite inventory and allow arbitrary integer coordinates",
                              "Validator tolerances are measurement allowances, not extra physical capacity",
                              "U* denotes a proved upper bound, not the achievable optimum"],
              "solver": {"seconds_per_cp_model": args.seconds, "workers": 1, "seed": 0}}
    for name, population in (("full_flow", boxes), ("dominant_family", dominant)):
        print(f"Computing {name}: {len(population)} boxes", flush=True)
        if not population:
            result[name] = None
            continue
        result[name] = pallet_bound(population, seconds=args.seconds)
        result[name]["moonshot"] = moonshot(result[name]["upper_bound"], baseline["kpi"])
        print(f"{name}: U*={result[name]['upper_bound']['value']:.9%} "
              f"({result[name]['best_method']}), {result[name]['moonshot']['verdict']}", flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(args.out)


if __name__ == "__main__":
    main()
