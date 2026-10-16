#!/usr/bin/env python3
"""Regenera summary.json y summary.md sin ejecutar simulaciones."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_sweep import generate_summary, sweep_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--lock-retry-seconds", type=float, default=0)
    args = parser.parse_args()
    try:
        with sweep_lock(retry_seconds=args.lock_retry_seconds):
            generate_summary(args.directory)
        print(args.directory / "summary.md")
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
