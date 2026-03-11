#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from palca.integration.monotonic_feasibility_audit import (
    MonotonicAuditSearchConfig,
    run_monotonic_feasibility_audit,
)


DEFAULT_PROFILE = Path("configs/benchmarks/one_pallet_canonical.json")


def _print_table(rows: list[dict[str, Any]]) -> None:
    headers = [
        "seed",
        "baseline",
        "best_mono",
        "ge21",
        "ge22",
        "layers",
        "height_mm",
        "nodes",
        "time_s",
        "gap",
    ]
    print(" | ".join(headers))
    print("-" * 120)
    for row in sorted(rows, key=lambda item: int(item["seed"])):
        print(
            " | ".join(
                [
                    str(row.get("seed")),
                    str(row.get("baseline_processed_boxes")),
                    str(row.get("best_monotonic_processed_boxes_found")),
                    str(bool(row.get("monotonic_solution_exists_ge_21"))),
                    str(bool(row.get("monotonic_solution_exists_ge_22"))),
                    str(row.get("best_monotonic_layers_used")),
                    str(row.get("best_monotonic_height_mm")),
                    str(row.get("search_nodes")),
                    f"{float(row.get('search_time_s', 0.0)):.3f}",
                    str(row.get("best_gap_vs_baseline")),
                ]
            )
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Offline monotonic feasibility audit (upper-bound study) "
            "for one-pallet canonical benchmark episodes."
        )
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=str(DEFAULT_PROFILE),
        help=f"Path to benchmark profile (default: {DEFAULT_PROFILE}).",
    )
    parser.add_argument(
        "--outdir",
        type=str,
        default=None,
        help="Output directory (default: auto timestamp under out/benchmarks/<profile_name>/...).",
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=int,
        default=None,
        help="Optional list of seeds to audit. Defaults to profile seeds.",
    )
    parser.add_argument("--beam-width", type=int, default=128, help="Beam width.")
    parser.add_argument(
        "--expansion-topk",
        type=int,
        default=18,
        help="Max expansions kept per beam node.",
    )
    parser.add_argument("--max-nodes", type=int, default=20000, help="Max expanded nodes.")
    parser.add_argument("--max-time-s", type=float, default=45.0, help="Max search time per seed (s).")
    parser.add_argument(
        "--max-processed-target",
        type=int,
        default=None,
        help="Optional processed target override. Defaults to n_per_pallet from profile.",
    )
    parser.add_argument(
        "--no-monotonic-gate",
        action="store_true",
        help="Disable strict monotonic z gate (diagnostic only).",
    )
    parser.add_argument(
        "--export-best-plan",
        action="store_true",
        help="Export best plan JSON per seed.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    search_cfg = MonotonicAuditSearchConfig(
        beam_width=int(args.beam_width),
        expansion_topk=int(args.expansion_topk),
        max_nodes=int(args.max_nodes),
        max_time_s=float(args.max_time_s),
        export_best_plan=bool(args.export_best_plan),
        max_processed_target_override=(
            None if args.max_processed_target is None else int(args.max_processed_target)
        ),
        enforce_monotonic=not bool(args.no_monotonic_gate),
    )

    summary = run_monotonic_feasibility_audit(
        profile_path=str(args.profile),
        seeds_override=(list(args.seeds) if args.seeds else None),
        outdir=args.outdir,
        search_config=search_cfg,
    )

    rows = list(summary.get("rows", []))
    aggregate = dict(summary.get("aggregate", {}))
    files = dict(summary.get("files", {}))

    _print_table(rows)
    print(
        "[audit] seeds_ge_21=%s/%s seeds_ge_22=%s/%s best_monotonic_mean=%s gap_vs_baseline_mean=%s"
        % (
            aggregate.get("seeds_ge_21_count"),
            aggregate.get("seed_count"),
            aggregate.get("seeds_ge_22_count"),
            aggregate.get("seed_count"),
            aggregate.get("best_monotonic_mean"),
            aggregate.get("gap_vs_baseline_mean"),
        )
    )
    print(f"[audit] summary_csv={files.get('summary_csv')}")
    print(f"[audit] summary_json={files.get('summary_json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
