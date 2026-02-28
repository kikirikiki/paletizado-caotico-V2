#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from scripts.ablation_runner import RunKpis, extract_kpis, print_table, write_csv
except ModuleNotFoundError:
    from ablation_runner import RunKpis, extract_kpis, print_table, write_csv  # type: ignore


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Summarize one sim.run JSON into compact KPIs.")
    p.add_argument("--in", dest="in_json", required=True, help="Path to one sim.run JSON output")
    p.add_argument("--dest", default="1", help="Destination id key to read KPIs from (default: 1)")
    p.add_argument("--variant", default=None, help="Variant label for output row (default: input filename stem)")
    p.add_argument("--csv", default=None, help="Optional CSV output path for one-row summary")
    args = p.parse_args(argv)

    in_path = Path(args.in_json).expanduser()
    if not in_path.exists():
        print(f"[summarize][ERROR] file not found: {in_path}", file=sys.stderr)
        return 2

    try:
        payload = json.loads(in_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[summarize][ERROR] invalid JSON in {in_path}: {exc}", file=sys.stderr)
        return 3

    if not isinstance(payload, dict):
        print(f"[summarize][ERROR] expected JSON object at root: {in_path}", file=sys.stderr)
        return 4

    k = extract_kpis(payload, dest=str(args.dest))
    variant = str(args.variant) if args.variant else in_path.stem
    row = RunKpis(
        variant=variant,
        returncode=0,
        pallets=k["pallets"],
        seq_len=k["seq_len"],
        seq_sum=k["seq_sum"],
        seq_avg=k["seq_avg"],
        seq_min=k["seq_min"],
        vol_util=k["vol_util"],
        deadlock_stability=k["deadlock_stability"],
        close_height_full=k["close_height_full"],
        planar_count=k["planar_count"],
        stand_hw_count=k["stand_hw_count"],
        stand_hw_used_total=k["stand_hw_used_total"],
        rejected_support_pct=k["rejected_support_pct"],
        rejected_corner_pct=k["rejected_corner_pct"],
        micro_plan_time_ms_mean=k["micro_plan_time_ms_mean"],
        out_json=str(in_path.resolve()),
        out_log="-",
    )
    print_table([row])

    if args.csv:
        out_csv = Path(args.csv).expanduser().resolve()
        write_csv([row], out_csv)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
