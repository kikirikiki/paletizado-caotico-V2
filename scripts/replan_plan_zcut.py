#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Rect:
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


def _foot(pl: Dict[str, Any]) -> Rect:
    return Rect(float(pl["x_mm"]), float(pl["y_mm"]), float(pl["w_mm"]), float(pl["h_mm"]))


def _z0(pl: Dict[str, Any]) -> float:
    return float(pl.get("z_mm", 0.0))


def _dz(pl: Dict[str, Any]) -> float:
    return float(pl.get("dz_mm", 0.0))


def _z1(pl: Dict[str, Any]) -> float:
    return float(pl.get("z_mm", 0.0)) + float(pl.get("dz_mm", 0.0))


def _intervals_overlap(a0: float, a1: float, b0: float, b1: float) -> bool:
    # strict overlap; touching faces is OK
    return min(a1, b1) > max(a0, b0)


def _collides(candidate: Rect, z0: float, z1: float, placed: List[Dict[str, Any]], ignore_row_idx: int) -> bool:
    for pl in placed:
        rid = int(pl.get("row_idx", -1))
        if rid == ignore_row_idx:
            continue
        if not _intervals_overlap(z0, z1, _z0(pl), _z1(pl)):
            continue
        if candidate.intersect(_foot(pl)) is not None:
            return True
    return False


def _compute_drop_z(candidate: Rect, placed: List[Dict[str, Any]], ignore_row_idx: int) -> float:
    """True gravity drop: z = max(top_z of any placed box whose footprint intersects candidate), else 0."""
    tops: List[float] = [0.0]
    for pl in placed:
        rid = int(pl.get("row_idx", -1))
        if rid == ignore_row_idx:
            continue
        if candidate.intersect(_foot(pl)) is None:
            continue
        tops.append(_z1(pl))
    return float(max(tops) if tops else 0.0)


def _support_rects_for(foot: Rect, z_mm: float, placed: List[Dict[str, Any]], eps_z_mm: float) -> Tuple[List[Rect], List[int]]:
    if z_mm <= 0.0:
        return [foot], []
    rects: List[Rect] = []
    rows: List[int] = []
    for pl in placed:
        top = _z1(pl)
        if abs(top - z_mm) > eps_z_mm + 1e-9:
            continue
        it = foot.intersect(_foot(pl))
        if it is None:
            continue
        rows.append(int(pl.get("row_idx", -1)))
        rects.append(it)
    rows_rects = sorted(zip(rows, rects), key=lambda t: t[0])
    rows = [r for r, _ in rows_rects]
    rects = [rc for _, rc in rows_rects]
    return rects, rows


def _support_area_ratio(foot: Rect, support_rects: List[Rect]) -> Tuple[int, float]:
    area = sum(r.area() for r in support_rects)
    farea = max(1e-9, foot.area())
    ratio = max(0.0, min(1.0, area / farea))
    return int(round(area)), float(ratio)


def _support_extents_overhang(foot: Rect, support_rects: List[Rect]) -> float:
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


def _com_margin(px: float, py: float, rects: List[Rect]) -> Tuple[bool, float]:
    containing = [r for r in rects if r.contains_point(px, py)]
    if containing:
        m = min(min(px - r.x1, r.x2 - px, py - r.y1, r.y2 - py) for r in containing)
        return True, float(m)
    if not rects:
        return False, float("-inf")
    d = min(r.dist_point(px, py) for r in rects)
    return False, -float(d)


def _required_com_margin(z_mm: float, args: argparse.Namespace) -> float:
    if args.min_com_margin is not None:
        return float(args.min_com_margin)
    if z_mm >= args.com_margin_switch_z:
        return float(args.min_com_margin_high)
    return float(args.min_com_margin_low)


def _severity(cls: str) -> int:
    return {"FAIL": 0, "CONDITIONAL": 1, "PASS": 2}.get(cls, 0)


def _classify(
    support_ratio: float,
    com_inside: bool,
    com_margin_mm: float,
    req_margin_mm: float,
    max_overhang_mm: float,
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

    if "SUPPORT_RATIO_LT_HARD_FAIL" in reasons or "COM_OUTSIDE_SUPPORT" in reasons:
        return "FAIL", reasons

    if (
        support_ratio >= args.min_support
        and com_inside
        and com_margin_mm >= req_margin_mm
        and max_overhang_mm <= args.max_overhang
    ):
        return "PASS", []

    if (
        args.cond_support <= support_ratio < args.min_support
        and com_inside
        and com_margin_mm >= req_margin_mm
        and max_overhang_mm <= args.max_overhang
    ):
        return "CONDITIONAL", ["SUPPORT_RATIO_LT_PASS"]

    return "FAIL", reasons


def _eval_candidate(
    row_idx: int,
    cand_rect: Rect,
    z_mm: float,
    dz_mm: float,
    placed: List[Dict[str, Any]],
    eps_z_mm: float,
    args: argparse.Namespace,
) -> Dict[str, Any]:
    foot = cand_rect
    support_rects, sup_rows = _support_rects_for(foot, z_mm, placed, eps_z_mm=eps_z_mm)
    support_area, support_ratio = _support_area_ratio(foot, support_rects)

    com_x = foot.x + 0.5 * foot.w
    com_y = foot.y + 0.5 * foot.h
    com_inside, com_margin = _com_margin(com_x, com_y, support_rects)

    req_margin = _required_com_margin(z_mm, args)
    max_overhang = _support_extents_overhang(foot, support_rects)

    cls, reasons = _classify(
        support_ratio=support_ratio,
        com_inside=com_inside,
        com_margin_mm=float(com_margin),
        req_margin_mm=float(req_margin),
        max_overhang_mm=float(max_overhang),
        args=args,
    )

    return {
        "row_idx": int(row_idx),
        "x_mm": int(round(foot.x)),
        "y_mm": int(round(foot.y)),
        "z_mm": int(round(z_mm)),
        "dz_mm": int(round(dz_mm)),
        "w_mm": int(round(foot.w)),
        "h_mm": int(round(foot.h)),
        "support_area_mm2": int(support_area),
        "support_ratio": float(support_ratio),
        "support_by_rows": sup_rows,
        "support_by_n": int(len(sup_rows)),
        "com_x_mm": float(com_x),
        "com_y_mm": float(com_y),
        "com_inside_support": bool(com_inside),
        "com_margin_mm": float(com_margin),
        "req_com_margin_mm": float(req_margin),
        "max_overhang_edge_mm": float(max_overhang),
        "stability_class": cls,
        "reasons": reasons,
    }


def _counts_by_class(placements: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {"PASS": 0, "CONDITIONAL": 0, "FAIL": 0, "UNKNOWN": 0}
    for pl in placements:
        cls = str(pl.get("stability_class", "UNKNOWN"))
        if cls not in out:
            cls = "UNKNOWN"
        out[cls] = out.get(cls, 0) + 1
    return out


def _accept_level(args: argparse.Namespace) -> int:
    if args.accept == "pass":
        return _severity("PASS")
    if args.accept == "cond":
        return _severity("CONDITIONAL")
    return _severity("FAIL")


def _score(
    e: Dict[str, Any],
    z_mm: float,
    dz_mm: float,
    x0: int,
    y0: int,
    rot_xy: bool,
) -> Tuple[int, float, float, float, float, float, float, int, int, int]:
    """
    Higher is better.
    - Prefer higher stability class
    - Prefer lower TOP (z+dz) to avoid building mesas
    - Prefer lower z
    - Then maximize support / com_margin
    - Then minimize overhang and movement
    - Deterministic tie-break
    - Prefer NOT rotating if tie
    """
    man = abs(int(e["x_mm"]) - x0) + abs(int(e["y_mm"]) - y0)
    top = float(z_mm + dz_mm)
    return (
        _severity(e["stability_class"]),
        -top,
        -float(z_mm),
        float(e["support_ratio"]),
        float(e["com_margin_mm"]),
        -float(e["max_overhang_edge_mm"]),
        -float(man),
        -int(e["x_mm"]),
        -int(e["y_mm"]),
        -int(rot_xy),  # prefer non-rotated if everything else equal
    )


def _mov_key(pl: Dict[str, Any], order: str) -> Tuple[int, int, int]:
    dz = int(pl.get("dz_mm", 0) or 0)
    area = int(pl.get("w_mm", 0) or 0) * int(pl.get("h_mm", 0) or 0)
    rid = int(pl.get("row_idx", -1))
    if order == "area_height":
        return (-area, -dz, rid)
    return (-dz, -area, rid)  # height_area


def _place_sort_key(pl: Dict[str, Any]) -> Tuple[int, int]:
    rid = int(pl.get("row_idx", -1))
    item = int(pl.get("item_idx", -1))
    return (rid, item)


def _reindex_layers_by_z(placements: List[Dict[str, Any]]) -> None:
    zs = sorted({int(pl.get("z_mm", 0) or 0) for pl in placements})
    z2i = {z: i for i, z in enumerate(zs)}
    for pl in placements:
        pl["layer_idx"] = int(z2i[int(pl.get("z_mm", 0) or 0)])


def _best_try_key(meta: Dict[str, Any]) -> Tuple[int, int, int, int, int]:
    """
    Deterministic best-try priority:
      1) maximize placed_total
      2) minimize FAIL
      3) maximize PASS
      4) minimize max_top_mm
      5) minimize unplaced
    """
    counts = meta.get("counts") or {}
    return (
        int(meta.get("placed_total", 0)),
        -int(counts.get("FAIL", 0)),
        int(counts.get("PASS", 0)),
        -int(meta.get("max_top_mm", 0)),
        -int(meta.get("unplaced", 0)),
    )


def _ensure_rot_fields(pl: Dict[str, Any]) -> None:
    if "rot_xy" not in pl:
        pl["rot_xy"] = False
    if "yaw_deg" not in pl:
        pl["yaw_deg"] = 90 if bool(pl.get("rot_xy")) else 0


def _run_one(
    *,
    placements_in: List[Dict[str, Any]],
    fixed: List[Dict[str, Any]],
    movable: List[Dict[str, Any]],
    L: int,
    W: int,
    hmax: int,
    eps_z: float,
    plan_min_support: float,
    args: argparse.Namespace,
    order: str,
    scan: str,
    verbose_places: bool,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    movable_sorted = sorted(movable, key=lambda pl: (_mov_key(pl, order), _place_sort_key(pl)))
    placed: List[Dict[str, Any]] = [json.loads(json.dumps(pl)) for pl in sorted(fixed, key=_place_sort_key)]
    for pl in placed:
        _ensure_rot_fields(pl)

    accept_level = _accept_level(args)

    planned_rows: List[int] = []
    unplaced_rows: List[int] = []

    for pl in movable_sorted:
        rid = int(pl.get("row_idx", -1))
        w0 = int(pl.get("w_mm", 0) or 0)
        h0 = int(pl.get("h_mm", 0) or 0)
        dz = float(pl.get("dz_mm", 0.0) or 0.0)
        x0 = int(pl.get("x_mm", 0) or 0)
        y0 = int(pl.get("y_mm", 0) or 0)

        if w0 <= 0 or h0 <= 0 or dz <= 0:
            unplaced_rows.append(rid)
            print(
                f"[UNPLACED] row={rid} w={w0} h={h0} dz={int(dz)} reason=invalid_dims "
                "candidates_generated=0 candidates_evaluated=0 rej_hmax=0 rej_coll=0 rej_stab=0"
            )
            continue

        # footprint variants: (w,h,rot_xy)
        variants: List[Tuple[int, int, bool]] = [(w0, h0, False)]
        if args.allow_rotate_xy and w0 != h0:
            variants.append((h0, w0, True))

        best_accept: Optional[Tuple[Tuple[int, float, float, float, float, float, float, int, int, int], Dict[str, Any], bool]] = None

        candidates_generated = 0
        candidates_evaluated = 0
        rej_hmax = 0
        rej_coll = 0
        rej_stab = 0

        for (w, h, rot_xy) in variants:
            if w > L or h > W:
                continue

            xs = range(0, L - w + 1, args.grid)
            ys = range(0, W - h + 1, args.grid)

            if scan == "xy":
                outer, inner, outer_is_x = xs, ys, True
            else:
                outer, inner, outer_is_x = ys, xs, False

            for o in outer:
                for i in inner:
                    x = int(o) if outer_is_x else int(i)
                    y = int(i) if outer_is_x else int(o)
                    candidates_generated += 1
                    candidates_evaluated += 1

                    cand_rect = Rect(float(x), float(y), float(w), float(h))
                    znew = _compute_drop_z(cand_rect, placed, ignore_row_idx=rid)
                    top = znew + dz

                    if top > (hmax + 1e-9):
                        rej_hmax += 1
                        continue

                    if _collides(cand_rect, znew, znew + dz, placed, ignore_row_idx=rid):
                        rej_coll += 1
                        continue

                    e = _eval_candidate(
                        row_idx=rid,
                        cand_rect=cand_rect,
                        z_mm=znew,
                        dz_mm=dz,
                        placed=placed,
                        eps_z_mm=eps_z,
                        args=args,
                    )

                    if _severity(e["stability_class"]) < accept_level:
                        rej_stab += 1
                        continue

                    sc = _score(e, z_mm=znew, dz_mm=dz, x0=x0, y0=y0, rot_xy=rot_xy)
                    if best_accept is None or sc > best_accept[0]:
                        best_accept = (sc, e, rot_xy)

                    if args.max_per_item > 0 and candidates_evaluated >= args.max_per_item:
                        break
                if args.max_per_item > 0 and candidates_evaluated >= args.max_per_item:
                    break

        if best_accept is None:
            unplaced_rows.append(rid)
            print(
                f"[UNPLACED] row={rid} w={w0} h={h0} dz={int(dz)} "
                f"candidates_generated={candidates_generated} candidates_evaluated={candidates_evaluated} "
                f"rej_hmax={rej_hmax} rej_coll={rej_coll} rej_stab={rej_stab}"
            )
            continue

        chosen = best_accept[1]
        rot_xy = bool(best_accept[2])

        new_pl = json.loads(json.dumps(pl))
        new_pl["x_mm"] = int(chosen["x_mm"])
        new_pl["y_mm"] = int(chosen["y_mm"])
        new_pl["z_mm"] = int(chosen["z_mm"])

        # update footprint if rotated
        new_pl["w_mm"] = int(chosen["w_mm"])
        new_pl["h_mm"] = int(chosen["h_mm"])
        new_pl["rot_xy"] = rot_xy
        new_pl["yaw_deg"] = 90 if rot_xy else 0

        new_pl["support_area_mm2"] = int(chosen["support_area_mm2"])
        new_pl["support_ratio"] = float(chosen["support_ratio"])
        new_pl["support_by_rows"] = list(chosen["support_by_rows"])
        new_pl["support_by_n"] = int(chosen["support_by_n"])

        new_pl["stability_class"] = str(chosen["stability_class"])
        new_pl["stability_reasons"] = list(chosen["reasons"])
        new_pl["stability_com_margin_mm"] = float(chosen["com_margin_mm"])
        new_pl["stability_req_com_margin_mm"] = float(chosen["req_com_margin_mm"])
        new_pl["stability_max_overhang_edge_mm"] = float(chosen["max_overhang_edge_mm"])

        # Legacy: is_supported uses plan_min_support
        new_pl["is_supported"] = bool(float(chosen["support_ratio"]) >= plan_min_support)

        placed.append(new_pl)
        planned_rows.append(rid)

        if verbose_places:
            top_i = int(new_pl["z_mm"] + int(round(dz)))
            tag = f"{order}/{scan}"
            print(
                f"[place:{tag}] row={rid} rot_xy={int(rot_xy)} -> (x,y,z)=({new_pl['x_mm']},{new_pl['y_mm']},{new_pl['z_mm']}) "
                f"w,h=({new_pl['w_mm']},{new_pl['h_mm']}) dz={int(dz)} top={top_i} "
                f"class={new_pl['stability_class']} ratio={chosen['support_ratio']:.3f} "
                f"com_margin={chosen['com_margin_mm']:.1f}/{chosen['req_com_margin_mm']:.1f} "
                f"ov={chosen['max_overhang_edge_mm']:.1f} sup_n={chosen['support_by_n']}"
            )

    # rebuild output in deterministic row order
    by_row: Dict[int, Dict[str, Any]] = {int(pl.get("row_idx", -1)): pl for pl in placed}
    placements_out: List[Dict[str, Any]] = []
    missing: List[int] = []
    for pl in sorted(placements_in, key=_place_sort_key):
        rid = int(pl.get("row_idx", -1))
        if rid in by_row:
            out_pl = by_row[rid]
            _ensure_rot_fields(out_pl)
            placements_out.append(out_pl)
        else:
            missing.append(rid)

    max_top = 0
    for pl in placements_out:
        max_top = max(max_top, int(round(_z1(pl))))

    supported_count = int(sum(1 for pl in placements_out if bool(pl.get("is_supported", False))))
    unsupported_count = int(len(placements_out) - supported_count)

    meta = {
        "order": order,
        "scan": scan,
        "placed_total": int(len(placements_out)),
        "placed_movable": int(len(planned_rows)),
        "unplaced": int(len(unplaced_rows)),
        "unplaced_rows": sorted([int(r) for r in unplaced_rows]),
        "missing_from_output": sorted([int(r) for r in missing]),
        "max_top_mm": int(max_top),
        "supported_count": int(supported_count),
        "unsupported_count": int(unsupported_count),
    }
    return meta, placements_out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Replan placements above z_cut using drop-z + industrial stability gating (greedy, deterministic)."
    )
    ap.add_argument("plan_json", help="Path to *.plan.json (schema palca/enforce_plan, z_mode=drop)")
    ap.add_argument("--out", default=None, help="Output path (default: out/plan_replanned/<stem>.zcut<Z>.plan.json)")
    ap.add_argument(
        "--z-cut-mm",
        type=float,
        default=0.0,
        help=(
            "Keep boxes fixed if bottom z_mm < z_cut_mm + eps_z_mm (volume crosses or stays below the cut); "
            "replan only boxes fully above the cut."
        ),
    )
    ap.add_argument("--grid", type=int, default=10, help="XY grid step in mm (default: 10)")
    ap.add_argument("--max-per-item", type=int, default=0, help="Optional cap of evaluated candidates per item (0 = no cap)")
    ap.add_argument("--accept", choices=["pass", "cond", "any"], default="cond", help="Minimum stability class to accept (default: cond)")
    ap.add_argument("--order", choices=["auto", "area_height", "height_area"], default="auto", help="Movable order (default: auto)")
    ap.add_argument("--scan", choices=["auto", "xy", "yx"], default="auto", help="XY scan order (default: auto)")
    ap.add_argument("--allow-rotate-xy", action="store_true", help="Also evaluate (h,w) footprint (yaw 90°) when possible.")
    ap.add_argument("--allow-partial", action="store_true", help="Return exit code 0 even when unplaced rows remain.")
    ap.add_argument("--reindex-layers", action="store_true", help="Rewrite layer_idx based on sorted unique z_mm in output plan.")
    ap.add_argument("--quiet-places", action="store_true", help="Do not print per-placement lines (only summaries).")
    ap.add_argument("--strict", action="store_true", help="Exit non-zero if unplaced rows remain or any output placement is FAIL.")

    ap.add_argument("--min-support", type=float, default=0.90)
    ap.add_argument("--cond-support", type=float, default=0.85)
    ap.add_argument("--hard-fail-support", type=float, default=0.60)

    ap.add_argument("--min-com-margin", type=float, default=None, help="If set, overrides low/high margin logic (recommended: 30)")
    ap.add_argument("--min-com-margin-low", type=float, default=20.0)
    ap.add_argument("--min-com-margin-high", type=float, default=30.0)
    ap.add_argument("--com-margin-switch-z", type=float, default=1200.0)

    ap.add_argument("--max-overhang", type=float, default=40.0)

    args = ap.parse_args()

    if args.grid <= 0:
        print("[ERROR] --grid must be > 0", file=sys.stderr)
        return 2
    if not (0.0 <= args.hard_fail_support <= args.cond_support <= args.min_support <= 1.0):
        print("[ERROR] expected 0<=hard_fail<=cond<=min_support<=1", file=sys.stderr)
        return 2

    plan_path = Path(args.plan_json)
    d = json.load(plan_path.open("r", encoding="utf-8"))

    schema = d.get("schema")
    schema_version = d.get("schema_version")
    z_mode = d.get("z_mode")
    if schema != "palca/enforce_plan":
        print(f"[ERROR] expected schema='palca/enforce_plan', got {schema!r}", file=sys.stderr)
        return 2
    if z_mode != "drop":
        print(f"[ERROR] expected z_mode='drop', got {z_mode!r}", file=sys.stderr)
        return 2

    base_eff = d.get("base_effective") or {}
    L = int(base_eff.get("length_mm", 0) or 0)
    W = int(base_eff.get("width_mm", 0) or 0)
    if L <= 0 or W <= 0:
        print(f"[ERROR] invalid base_effective: {base_eff}", file=sys.stderr)
        return 2

    hmax = int(d.get("hmax_mm", 2400) or 2400)
    support_block = d.get("support") or {}
    eps_z = float(support_block.get("eps_z_mm", 1.0))
    plan_min_support = float(support_block.get("min_support", args.min_support))

    placements_in: List[Dict[str, Any]] = list(d.get("placements") or [])
    if not placements_in:
        print("[ERROR] no placements", file=sys.stderr)
        return 2

    z_cut = float(args.z_cut_mm)
    fixed: List[Dict[str, Any]] = []
    movable: List[Dict[str, Any]] = []
    # Fixed rule (conservative around the cut plane):
    # keep fixed any placement whose bottom is below z_cut (+eps), because it intersects
    # or lies below the cut volume. Only boxes fully above the cut are movable.
    for pl in sorted(placements_in, key=_place_sort_key):
        z0 = _z0(pl)
        if z0 < z_cut + eps_z + 1e-9:
            fixed.append(pl)
        else:
            movable.append(pl)

    orders = ["area_height", "height_area"] if args.order == "auto" else [args.order]
    scans = ["xy", "yx"] if args.scan == "auto" else [args.scan]

    print(f"PLAN: {plan_path}")
    print(f"schema={schema!r} schema_version={schema_version} z_mode={z_mode!r} hmax={hmax} eps_z_mm={eps_z}")
    print(f"base_effective: L={L} W={W} overhang={base_eff.get('overhang_mm')}")
    print(
        f"z_cut_mm={z_cut} fixed={len(fixed)} movable={len(movable)} grid={args.grid} "
        f"accept={args.accept} order={args.order} scan={args.scan} rotate_xy={args.allow_rotate_xy}"
    )
    print(
        f"thresholds: pass_support>={args.min_support:.2f} cond_support>={args.cond_support:.2f} hard_fail<{args.hard_fail_support:.2f} "
        f"com_margin(min={args.min_com_margin if args.min_com_margin is not None else 'auto'}) max_overhang<={args.max_overhang:.0f}"
    )

    best_meta: Optional[Dict[str, Any]] = None
    best_placements: Optional[List[Dict[str, Any]]] = None
    candidates: List[Dict[str, Any]] = []

    verbose_places = not args.quiet_places

    best_key: Optional[Tuple[int, int, int, int, int]] = None

    for order in orders:
        for scan in scans:
            if not args.quiet_places:
                print(f"\n=== TRY order={order} scan={scan} ===")
            meta, placements_out = _run_one(
                placements_in=placements_in,
                fixed=fixed,
                movable=movable,
                L=L,
                W=W,
                hmax=hmax,
                eps_z=eps_z,
                plan_min_support=plan_min_support,
                args=args,
                order=order,
                scan=scan,
                verbose_places=verbose_places,
            )

            counts = _counts_by_class(placements_out)
            meta2 = dict(meta)
            meta2["counts"] = counts
            candidates.append(meta2)

            key = _best_try_key(meta2)

            if best_meta is None or best_key is None or key > best_key:
                best_meta = meta2
                best_placements = placements_out
                best_key = key

            if args.quiet_places:
                print(
                    f"[TRY] order={order} scan={scan} placed={meta2['placed_total']} unplaced={meta2['unplaced']} "
                    f"PASS={counts.get('PASS',0)} COND={counts.get('CONDITIONAL',0)} FAIL={counts.get('FAIL',0)} "
                    f"max_top={meta2['max_top_mm']}"
                )

    assert best_meta is not None and best_placements is not None

    d["placements"] = best_placements

    supported_count = int(sum(1 for pl in best_placements if bool(pl.get("is_supported", False))))
    unsupported_count = int(len(best_placements) - supported_count)
    if isinstance(d.get("support"), dict):
        d["support"]["supported_count"] = supported_count
        d["support"]["unsupported_count"] = unsupported_count

    if args.reindex_layers:
        _reindex_layers_by_z(best_placements)

    max_top = 0
    for pl in best_placements:
        max_top = max(max_top, int(round(_z1(pl))))

    d.pop("replan_zcut", None)
    chosen_order_scan = None
    if args.order == "auto" or args.scan == "auto":
        chosen_order_scan = {"order": best_meta["order"], "scan": best_meta["scan"]}
    d["replan"] = {
        "tool": "scripts/replan_plan_zcut.py",
        "input_path": str(plan_path),
        "z_cut_mm": float(z_cut),
        "grid_mm": int(args.grid),
        "accept_policy": args.accept,
        "order": args.order,
        "scan": args.scan,
        "placed_total": int(best_meta["placed_total"]),
        "unplaced_total": int(best_meta["unplaced"]),
        "unplaced_rows": list(best_meta["unplaced_rows"]),
        "rotate_xy": bool(args.allow_rotate_xy),
        "chosen_order_scan": chosen_order_scan,
        "fixed_count": int(len(fixed)),
        "movable_count": int(len(movable)),
        "max_top_mm": int(max_top),
        "hmax_mm": int(hmax),
        "thresholds": {
            "min_support": float(args.min_support),
            "cond_support": float(args.cond_support),
            "hard_fail_support": float(args.hard_fail_support),
            "min_com_margin": args.min_com_margin,
            "min_com_margin_low": float(args.min_com_margin_low),
            "min_com_margin_high": float(args.min_com_margin_high),
            "com_margin_switch_z": float(args.com_margin_switch_z),
            "max_overhang": float(args.max_overhang),
            "plan_min_support": float(plan_min_support),
        },
        "best_selection_policy": "max placed_total, min FAIL, max PASS, min max_top_mm, min unplaced",
        "chosen": best_meta,
        "candidates": candidates,
    }

    out = args.out
    if out is None:
        out_dir = Path("out/plan_replanned")
        out_dir.mkdir(parents=True, exist_ok=True)
        ztag = int(round(z_cut))
        out = str(out_dir / f"{plan_path.stem}.zcut{ztag}.plan.json")

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    warnings = list(d.get("warnings") or [])
    warnings.append("Greedy heuristic; prefer placements[].z_mm as source of truth.")
    warnings.append("is_supported uses plan_min_support (legacy); for industrial gating use stability_class.")
    if int(best_meta["unplaced"]) > 0:
        warnings.append(
            f"PARTIAL_REPLAN: unplaced_total={int(best_meta['unplaced'])} "
            f"unplaced_rows={list(best_meta['unplaced_rows'])}"
        )
    if args.strict:
        warnings.append("STRICT_MODE enabled: non-zero exit if unplaced rows remain or any FAIL appears.")
    d["warnings"] = warnings

    out_path.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")

    print("\n=== BEST CHOSEN ===")
    c = best_meta.get("counts", {})
    print(
        f"order={best_meta['order']} scan={best_meta['scan']} placed={best_meta['placed_total']} unplaced={best_meta['unplaced']} "
        f"PASS={c.get('PASS',0)} COND={c.get('CONDITIONAL',0)} FAIL={c.get('FAIL',0)} max_top={best_meta['max_top_mm']}"
    )
    print(f"unplaced_rows={best_meta.get('unplaced_rows', [])}")
    print(f"WROTE: {out_path}")

    exit_code = 0
    unplaced_total = int(best_meta["unplaced"])
    fail_total = int(c.get("FAIL", 0))
    if unplaced_total > 0 and not args.allow_partial:
        exit_code = 2
    if args.strict and (unplaced_total > 0 or fail_total > 0):
        exit_code = 2
    if exit_code != 0:
        print(
            f"[EXIT {exit_code}] partial_or_strict_guard triggered: "
            f"unplaced_total={unplaced_total} fail_total={fail_total} allow_partial={args.allow_partial} strict={args.strict}"
        )
        return exit_code
    if args.allow_partial and unplaced_total > 0:
        print("[WARN] allow_partial enabled: returning 0 with unplaced rows present.")
    if args.strict and fail_total == 0 and unplaced_total == 0:
        print("[OK] strict mode checks passed.")
    if args.allow_partial and unplaced_total == 0:
        print("[OK] allow_partial set, but plan is complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
