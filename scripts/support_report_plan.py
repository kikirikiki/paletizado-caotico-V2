#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle in mm (x,y = lower-left), with width/height in mm."""
    x: float
    y: float
    w: float
    h: float

    @property
    def x1(self) -> float:
        return self.x

    @property
    def y1(self) -> float:
        return self.y

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    def intersect(self, other: "Rect") -> Optional["Rect"]:
        ix1 = max(self.x1, other.x1)
        iy1 = max(self.y1, other.y1)
        ix2 = min(self.x2, other.x2)
        iy2 = min(self.y2, other.y2)
        iw = ix2 - ix1
        ih = iy2 - iy1
        if iw <= 0.0 or ih <= 0.0:
            return None
        return Rect(ix1, iy1, iw, ih)

    def contains_point(self, px: float, py: float, eps: float = 1e-9) -> bool:
        return (self.x1 - eps) <= px <= (self.x2 + eps) and (self.y1 - eps) <= py <= (self.y2 + eps)

    def dist_point(self, px: float, py: float) -> float:
        """Euclidean distance from point to rectangle (0 if inside)."""
        dx = 0.0
        if px < self.x1:
            dx = self.x1 - px
        elif px > self.x2:
            dx = px - self.x2

        dy = 0.0
        if py < self.y1:
            dy = self.y1 - py
        elif py > self.y2:
            dy = py - self.y2

        return math.hypot(dx, dy)


def _make_centers(length_mm: float, grid_mm: float) -> List[float]:
    """Centers in [0, length] spaced by grid, guaranteed inside footprint."""
    if grid_mm <= 0:
        raise ValueError("grid_mm must be > 0")
    if length_mm <= 0:
        return [0.0]
    max_center = max(0.0, length_mm - 0.5 * grid_mm)
    n = int(math.floor((max_center - 0.5 * grid_mm) / grid_mm)) + 1
    n = max(1, n)
    centers = [(i + 0.5) * grid_mm for i in range(n)]
    centers = [c for c in centers if 0.0 <= c <= length_mm]
    if not centers:
        centers = [0.5 * length_mm]
    return centers


def _point_supported(px: float, py: float, rects: List[Rect]) -> bool:
    for r in rects:
        if r.contains_point(px, py):
            return True
    return False


def _com_margin(px: float, py: float, rects: List[Rect]) -> Tuple[bool, float]:
    """
    Returns (inside_union, signed_margin_mm).
    - If inside: conservative margin = min distance to edge of any rectangle that contains the point.
    - If outside: margin = -distance to nearest rectangle (Euclidean).
    """
    containing: List[Rect] = [r for r in rects if r.contains_point(px, py)]
    if containing:
        m = min(min(px - r.x1, r.x2 - px, py - r.y1, r.y2 - py) for r in containing)
        return True, float(m)
    if not rects:
        return False, float("-inf")
    d = min(r.dist_point(px, py) for r in rects)
    return False, -float(d)


def _support_extents_overhang(foot: Rect, support_rects: List[Rect]) -> float:
    """
    Overhang metric = maximum inset between footprint edges and the EXTENTS of support union.
    This matches "how far does the footprint protrude beyond supported extents" (mm),
    and does NOT explode for thin strips (those are handled as overhang, not as an interior "gap").
    """
    if not support_rects:
        return float(max(foot.w, foot.h))
    xmin = min(r.x1 for r in support_rects)
    xmax = max(r.x2 for r in support_rects)
    ymin = min(r.y1 for r in support_rects)
    ymax = max(r.y2 for r in support_rects)
    dx_left = max(0.0, xmin - foot.x1)
    dx_right = max(0.0, foot.x2 - xmax)
    dy_bottom = max(0.0, ymin - foot.y1)
    dy_top = max(0.0, foot.y2 - ymax)
    return float(max(dx_left, dx_right, dy_bottom, dy_top))


def _supported_mask(foot: Rect, support_rects: List[Rect], grid_mm: float) -> Tuple[List[float], List[float], List[List[bool]]]:
    xs_local = _make_centers(foot.w, grid_mm)
    ys_local = _make_centers(foot.h, grid_mm)
    mask: List[List[bool]] = []
    for y0 in ys_local:
        py = foot.y + y0
        row: List[bool] = []
        for x0 in xs_local:
            px = foot.x + x0
            row.append(_point_supported(px, py, support_rects))
        mask.append(row)
    return xs_local, ys_local, mask


def _max_interior_gap(mask: List[List[bool]], grid_mm: float) -> float:
    """
    max_gap_mm = maximum span of an UNSUPPORTED connected component that does NOT touch the footprint border.
    This captures "bridge/gap" inside the footprint while ignoring edge overhang strips
    (those are handled by the overhang metric).
    Approximation: component span = max(bbox_dx, bbox_dy) * grid_mm.
    """
    if not mask:
        return 0.0
    ny = len(mask)
    nx = len(mask[0]) if ny else 0
    if nx == 0:
        return 0.0

    visited = [[False] * nx for _ in range(ny)]
    neigh = ((1, 0), (-1, 0), (0, 1), (0, -1))

    def touches_border(i: int, j: int) -> bool:
        return i == 0 or j == 0 or i == nx - 1 or j == ny - 1

    best = 0.0

    for j in range(ny):
        for i in range(nx):
            if visited[j][i]:
                continue
            if mask[j][i]:
                visited[j][i] = True
                continue

            # BFS on unsupported component
            q = [(i, j)]
            visited[j][i] = True
            tb = touches_border(i, j)
            min_i = max_i = i
            min_j = max_j = j

            while q:
                ci, cj = q.pop()
                for di, dj in neigh:
                    ni, nj = ci + di, cj + dj
                    if ni < 0 or nj < 0 or ni >= nx or nj >= ny:
                        continue
                    if visited[nj][ni]:
                        continue
                    visited[nj][ni] = True
                    if mask[nj][ni]:
                        continue
                    if touches_border(ni, nj):
                        tb = True
                    min_i = min(min_i, ni)
                    max_i = max(max_i, ni)
                    min_j = min(min_j, nj)
                    max_j = max(max_j, nj)
                    q.append((ni, nj))

            if tb:
                continue  # ignore overhang-connected components

            span_x = float((max_i - min_i + 1)) * float(grid_mm)
            span_y = float((max_j - min_j + 1)) * float(grid_mm)
            best = max(best, span_x, span_y)

    return float(best)


def _grid_metrics(foot: Rect, support_rects: List[Rect], grid_mm: float) -> Tuple[float, float]:
    """
    Returns:
      - max_overhang_edge_mm: inset of support extents from footprint edges (mm)
      - max_gap_mm: maximum INTERIOR unsupported span (mm), ignoring components touching border
    """
    if grid_mm <= 0:
        raise ValueError("grid must be > 0")

    # Floor / fully supported shortcut
    if len(support_rects) == 1:
        r = support_rects[0]
        if abs(r.x - foot.x) < 1e-6 and abs(r.y - foot.y) < 1e-6 and abs(r.w - foot.w) < 1e-6 and abs(r.h - foot.h) < 1e-6:
            return 0.0, 0.0

    max_overhang = _support_extents_overhang(foot, support_rects)
    _, _, mask = _supported_mask(foot, support_rects, grid_mm)
    max_gap = _max_interior_gap(mask, grid_mm)
    return float(max_overhang), float(max_gap)


def _required_com_margin(z_mm: float, args: argparse.Namespace) -> float:
    if args.min_com_margin is not None:
        return float(args.min_com_margin)
    if z_mm >= args.com_margin_switch_z:
        return float(args.min_com_margin_high)
    return float(args.min_com_margin_low)


def _severity(cls: str) -> int:
    # smaller is worse
    return {"FAIL": 0, "CONDITIONAL": 1, "PASS": 2}.get(cls, 9)


def _classify(
    support_ratio: float,
    com_inside: bool,
    com_margin_mm: float,
    req_margin_mm: float,
    max_overhang_mm: float,
    max_gap_mm: float,
    args: argparse.Namespace,
) -> Tuple[str, List[str]]:
    reasons: List[str] = []

    if support_ratio < args.hard_fail_support:
        reasons.append("SUPPORT_RATIO_LT_HARD_FAIL")
    if support_ratio < args.cond_support:
        reasons.append("SUPPORT_RATIO_LT_COND")
    elif support_ratio < args.min_support:
        reasons.append("SUPPORT_RATIO_LT_PASS")

    if not com_inside:
        reasons.append("COM_OUTSIDE_SUPPORT")
    elif com_margin_mm < req_margin_mm:
        reasons.append("COM_MARGIN_LOW")

    if max_overhang_mm > args.max_overhang:
        reasons.append("OVERHANG_TOO_LARGE")
    if max_gap_mm > args.max_gap:
        reasons.append("GAP_TOO_LARGE")

    # Determine class
    if "SUPPORT_RATIO_LT_HARD_FAIL" in reasons or "COM_OUTSIDE_SUPPORT" in reasons:
        return "FAIL", reasons

    # PASS
    if (
        support_ratio >= args.min_support
        and com_inside
        and com_margin_mm >= req_margin_mm
        and max_overhang_mm <= args.max_overhang
        and max_gap_mm <= args.max_gap
    ):
        return "PASS", []

    # CONDITIONAL
    if (
        args.cond_support <= support_ratio < args.min_support
        and com_inside
        and com_margin_mm >= req_margin_mm
        and max_overhang_mm <= args.max_overhang
        and max_gap_mm <= args.max_gap
    ):
        return "CONDITIONAL", ["SUPPORT_RATIO_LT_PASS"]

    return "FAIL", reasons


def _load_plan(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _build_row_index(placements: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    idx: Dict[int, Dict[str, Any]] = {}
    dups: List[int] = []
    for pl in placements:
        rid = pl.get("row_idx")
        if rid is None:
            continue
        rid_i = int(rid)
        if rid_i in idx:
            dups.append(rid_i)
        idx[rid_i] = pl
    if dups:
        uniq = sorted(set(dups))
        print(f"[WARN] duplicate row_idx detected (using last): {uniq[:10]}{'...' if len(uniq)>10 else ''}", file=sys.stderr)
    return idx


def _footprint(pl: Dict[str, Any]) -> Rect:
    return Rect(float(pl["x_mm"]), float(pl["y_mm"]), float(pl["w_mm"]), float(pl["h_mm"]))


def _support_rects(
    pl: Dict[str, Any],
    row_index: Dict[int, Dict[str, Any]],
    eps_z_mm: float,
) -> Tuple[List[Rect], List[int], int]:
    """
    Returns (support_rects, missing_support_rows, z_mismatch_count).
    For floor items: returns full footprint as support.
    """
    foot = _footprint(pl)
    z_mm = float(pl.get("z_mm", 0.0))
    if z_mm <= 0.0 or int(pl.get("support_by_n", 0) or 0) <= 0:
        return [foot], [], 0

    rects: List[Rect] = []
    missing: List[int] = []
    z_mismatch = 0

    for rid in (pl.get("support_by_rows") or []):
        rid_i = int(rid)
        sup = row_index.get(rid_i)
        if sup is None:
            missing.append(rid_i)
            continue

        top_z = float(sup.get("z_mm", 0.0)) + float(sup.get("dz_mm", 0.0))
        if abs(top_z - z_mm) > float(eps_z_mm) + 1e-6:
            z_mismatch += 1  # still accept, but record

        inter = foot.intersect(_footprint(sup))
        if inter is not None:
            rects.append(inter)

    return rects, missing, z_mismatch


def _write_csv(path: Path, records: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = list(records[0].keys()) if records else []
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in records:
            w.writerow(r)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Industrial stability KPI report for palca/enforce_plan (z drop).")
    ap.add_argument("plan_json", help="Path to *.plan.json (schema palca/enforce_plan)")
    ap.add_argument("--min-support", type=float, default=0.90, help="PASS threshold for support_ratio (default: 0.90)")
    ap.add_argument("--cond-support", type=float, default=0.85, help="CONDITIONAL lower bound (default: 0.85)")
    ap.add_argument("--hard-fail-support", type=float, default=0.60, help="Hard FAIL if support_ratio below (default: 0.60)")

    ap.add_argument("--min-com-margin", type=float, default=None, help="If set, overrides low/high COM margin.")
    ap.add_argument("--min-com-margin-low", type=float, default=20.0, help="Required COM margin below switch height (default: 20)")
    ap.add_argument("--min-com-margin-high", type=float, default=30.0, help="Required COM margin above switch height (default: 30)")
    ap.add_argument("--com-margin-switch-z", type=float, default=1200.0, help="Switch height in mm for COM margin (default: 1200)")

    ap.add_argument("--max-overhang", type=float, default=40.0, help="Max allowed overhang (support extents inset) in mm (default: 40)")
    ap.add_argument("--max-gap", type=float, default=80.0, help="Max allowed INTERIOR gap span in mm (default: 80)")
    ap.add_argument("--grid", type=float, default=10.0, help="Grid in mm for gap metric (default: 10)")

    ap.add_argument("--head", type=int, default=50, help="Show top N worst rows (default: 50)")
    ap.add_argument("--out-json", type=str, default=None, help="Optional output JSON path (do NOT commit outputs).")
    ap.add_argument("--out-csv", type=str, default=None, help="Optional output CSV path (do NOT commit outputs).")
    ap.add_argument("--strict", action="store_true", help="Exit non-zero if any FAIL.")

    args = ap.parse_args()

    # Defensive validations
    if not (0.0 <= args.cond_support <= 1.0 and 0.0 <= args.min_support <= 1.0 and 0.0 <= args.hard_fail_support <= 1.0):
        print("[ERROR] support thresholds must be within [0,1].", file=sys.stderr)
        return 2
    if not (args.hard_fail_support <= args.cond_support <= args.min_support):
        print("[ERROR] expected hard_fail_support <= cond_support <= min_support.", file=sys.stderr)
        return 2
    if args.grid <= 0:
        print("[ERROR] grid must be > 0.", file=sys.stderr)
        return 2

    plan_path = Path(args.plan_json)
    d = _load_plan(plan_path)

    schema = d.get("schema")
    schema_version = d.get("schema_version")
    z_mode = d.get("z_mode")
    if schema != "palca/enforce_plan":
        print(f"[WARN] unexpected schema={schema!r} (expected 'palca/enforce_plan')", file=sys.stderr)
    if schema_version is None or int(schema_version) < 2:
        print(f"[WARN] unexpected schema_version={schema_version!r} (expected >=2)", file=sys.stderr)
    if z_mode != "drop":
        print(f"[WARN] z_mode={z_mode!r} (expected 'drop')", file=sys.stderr)

    placements: List[Dict[str, Any]] = list(d.get("placements") or [])
    row_index = _build_row_index(placements)

    # eps for top_z matching
    eps_z = float((d.get("support") or {}).get("eps_z_mm", 1.0))

    records: List[Dict[str, Any]] = []
    n_missing_rows = 0
    n_z_mismatch = 0

    # Stable order for determinism
    def _sort_key(pl: Dict[str, Any]) -> Tuple[float, int, int]:
        return (
            float(pl.get("z_mm", 0.0)),
            int(pl.get("layer_idx", 0) or 0),
            int(pl.get("row_idx", pl.get("item_idx", 0)) or 0),
        )

    for pl in sorted(placements, key=_sort_key):
        foot = _footprint(pl)
        support_rects, missing_rows, z_mismatch = _support_rects(pl, row_index, eps_z_mm=eps_z)
        n_missing_rows += len(missing_rows)
        n_z_mismatch += z_mismatch

        # COM (approx): center of footprint
        com_x = foot.x + 0.5 * foot.w
        com_y = foot.y + 0.5 * foot.h

        com_inside, com_margin = _com_margin(com_x, com_y, support_rects)

        max_overhang, max_gap = _grid_metrics(foot, support_rects, float(args.grid))
        req_margin = _required_com_margin(float(pl.get("z_mm", 0.0)), args)

        support_ratio = float(pl.get("support_ratio", 0.0))
        support_area = int(pl.get("support_area_mm2", 0))

        stability_class, reasons = _classify(
            support_ratio=support_ratio,
            com_inside=com_inside,
            com_margin_mm=float(com_margin),
            req_margin_mm=float(req_margin),
            max_overhang_mm=float(max_overhang),
            max_gap_mm=float(max_gap),
            args=args,
        )

        if missing_rows:
            reasons = reasons + ["SUPPORT_ROWS_MISSING"]

        rec: Dict[str, Any] = {
            "row_idx": int(pl.get("row_idx", -1)),
            "item_idx": int(pl.get("item_idx", -1)),
            "layer_idx": int(pl.get("layer_idx", -1)),
            "x_mm": int(pl.get("x_mm", 0)),
            "y_mm": int(pl.get("y_mm", 0)),
            "z_mm": int(pl.get("z_mm", 0)),
            "w_mm": int(pl.get("w_mm", 0)),
            "h_mm": int(pl.get("h_mm", 0)),
            "dz_mm": int(pl.get("dz_mm", 0)),
            "support_ratio": round(support_ratio, 4),
            "support_area_mm2": support_area,
            "is_supported_area": bool(support_ratio >= float(args.min_support)),
            "com_x_mm": round(com_x, 2),
            "com_y_mm": round(com_y, 2),
            "com_inside_support": bool(com_inside),
            "com_margin_mm": round(float(com_margin), 2),
            "req_com_margin_mm": round(float(req_margin), 2),
            "max_overhang_edge_mm": round(float(max_overhang), 2),
            "max_gap_mm": round(float(max_gap), 2),
            "stability_class": stability_class,
            "reasons": reasons,
            "support_by_n": int(pl.get("support_by_n", 0) or 0),
        }
        records.append(rec)

    # Summary
    counts = {"PASS": 0, "CONDITIONAL": 0, "FAIL": 0}
    for r in records:
        counts[r["stability_class"]] = counts.get(r["stability_class"], 0) + 1

    print(f"PLAN: {plan_path}")
    print(f"schema={schema!r} schema_version={schema_version!r} z_mode={z_mode!r} eps_z_mm={eps_z}")
    print(
        "thresholds:"
        f" pass_support>={args.min_support:.2f}"
        f" cond_support>={args.cond_support:.2f}"
        f" hard_fail<{args.hard_fail_support:.2f}"
        f" com_margin(low/high)={_required_com_margin(0.0,args):.0f}/{_required_com_margin(args.com_margin_switch_z,args):.0f}"
        f" switch_z={args.com_margin_switch_z:.0f}"
        f" max_overhang<={args.max_overhang:.0f}"
        f" max_gap<={args.max_gap:.0f}"
        f" grid={args.grid:.0f}"
    )
    print(f"counts: PASS={counts.get('PASS',0)}  CONDITIONAL={counts.get('CONDITIONAL',0)}  FAIL={counts.get('FAIL',0)}")
    if n_missing_rows:
        print(f"[WARN] missing supporter rows total: {n_missing_rows}", file=sys.stderr)
    if n_z_mismatch:
        print(f"[WARN] supporter top_z mismatch count: {n_z_mismatch} (accepted but suspicious)", file=sys.stderr)

    # Worst-first table
    worst = sorted(
        records,
        key=lambda r: (
            _severity(r["stability_class"]),
            float(r["support_ratio"]),
            float(r["com_margin_mm"]),
            -float(r["max_overhang_edge_mm"]),
            -float(r["max_gap_mm"]),
            int(r["z_mm"]),
            int(r["row_idx"]),
        ),
    )[: max(0, int(args.head))]

    cols = [
        "stability_class",
        "row_idx",
        "z_mm",
        "layer_idx",
        "support_ratio",
        "com_margin_mm",
        "req_com_margin_mm",
        "max_overhang_edge_mm",
        "max_gap_mm",
        "reasons",
    ]
    print("\nTOP WORST (sorted):")
    print(" | ".join(cols))
    for r in worst:
        print(
            f"{r['stability_class']:>11} | "
            f"{r['row_idx']:>6} | "
            f"{r['z_mm']:>4} | "
            f"{r['layer_idx']:>7} | "
            f"{r['support_ratio']:>11} | "
            f"{r['com_margin_mm']:>12} | "
            f"{r['req_com_margin_mm']:>15} | "
            f"{r['max_overhang_edge_mm']:>18} | "
            f"{r['max_gap_mm']:>9} | "
            f"{','.join(r['reasons'])}"
        )

    if args.out_csv:
        _write_csv(Path(args.out_csv), records)
        print(f"\nWROTE CSV: {args.out_csv}")
    if args.out_json:
        payload = {
            "schema": "palca/support_report",
            "schema_version": 1,
            "plan": {
                "path": str(plan_path),
                "schema": schema,
                "schema_version": schema_version,
                "z_mode": z_mode,
            },
            "thresholds": {
                "min_support": args.min_support,
                "cond_support": args.cond_support,
                "hard_fail_support": args.hard_fail_support,
                "min_com_margin": args.min_com_margin,
                "min_com_margin_low": args.min_com_margin_low,
                "min_com_margin_high": args.min_com_margin_high,
                "com_margin_switch_z": args.com_margin_switch_z,
                "max_overhang": args.max_overhang,
                "max_gap": args.max_gap,
                "grid": args.grid,
            },
            "counts": counts,
            "records": records,
        }
        _write_json(Path(args.out_json), payload)
        print(f"WROTE JSON: {args.out_json}")

    if args.strict and counts.get("FAIL", 0) > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
