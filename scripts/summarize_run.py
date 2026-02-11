#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _latest_json() -> Path | None:
    candidates: list[Path] = []
    for base in (Path("out"), Path("outputs")):
        if not base.exists():
            continue
        candidates.extend([p for p in base.rglob("*.json") if p.is_file()])
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _read_payload(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description="Resumen corto de output JSON de sim.run")
    parser.add_argument("path", nargs="?", help="Ruta al JSON. Si se omite, usa el mas reciente en out/ u outputs/")
    args = parser.parse_args()

    json_path = Path(args.path) if args.path else _latest_json()
    if json_path is None:
        print("No se encontro JSON en out/ ni outputs/.")
        return 1
    if not json_path.exists():
        print(f"No existe: {json_path}")
        return 1

    payload = _read_payload(json_path)
    metrics = payload.get("metrics", {}) or {}
    params = payload.get("params", {}) or {}
    kpis = metrics.get("pallet_kpis", {}) or {}

    processed = int(metrics.get("processed_boxes", 0) or 0)
    total = int(metrics.get("total_boxes", 0) or 0)
    stop_reason = metrics.get("stop_reason")
    policy_stop = metrics.get("policy_stop_reason")
    heights = kpis.get("current_height_mm_by_dest", {})
    layers = kpis.get("current_layers_by_dest", {})
    util = kpis.get("pallet_volume_utilization", {})

    print(f"path: {json_path}")
    print(f"policy/planner: {params.get('policy')} / {params.get('planner')}")
    print(f"processed/total: {processed}/{total}")
    print(f"stop_reason: {stop_reason}")
    if policy_stop is not None:
        print(f"policy_stop_reason: {policy_stop}")
    print(f"height_mm_by_dest: {heights}")
    print(f"layers_by_dest: {layers}")
    print(f"volume_util_by_dest: {util}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
