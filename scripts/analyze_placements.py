#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SystemExit("placements.json must be a JSON object")
    return data


def _bucket(v: float, bin_mm: int) -> int:
    b = max(1, int(bin_mm))
    return int((float(v) // float(b)) * b)


def _percentile(xs: List[float], p: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    if p <= 0:
        return float(ys[0])
    if p >= 100:
        return float(ys[-1])
    k = (len(ys) - 1) * (p / 100.0)
    f = int(k)
    c = min(len(ys) - 1, f + 1)
    if f == c:
        return float(ys[f])
    d0 = ys[f] * (c - k)
    d1 = ys[c] * (k - f)
    return float(d0 + d1)


@dataclass
class SegmentStats:
    step_from: int
    step_to: int
    n: int
    z_mean: float
    z_max: int
    by_family: Dict[str, int]


def _segment_steps(n_steps: int) -> List[Tuple[int, int]]:
    """Quartiles by step index, inclusive ranges [a,b]."""
    if n_steps <= 0:
        return []
    cuts = [0, int(0.25 * n_steps), int(0.50 * n_steps), int(0.75 * n_steps), n_steps]
    # ensure monotonic and unique-ish
    cuts = [max(0, min(n_steps, c)) for c in cuts]
    out: List[Tuple[int, int]] = []
    for i in range(4):
        a = cuts[i]
        b = cuts[i + 1] - 1
        if a <= b:
            out.append((a, b))
    if not out:
        out = [(0, n_steps - 1)]
    return out


def analyze_pallet(
    placements: List[dict[str, Any]],
    *,
    band_mm: int,
    hist_bin_mm: int,
) -> dict[str, Any]:
    # sort by step_index if present
    def key(p: dict[str, Any]) -> int:
        try:
            return int(p.get("step_index", 0) or 0)
        except Exception:
            return 0

    seq = sorted(placements, key=key)
    zs = []
    families = []
    for p in seq:
        try:
            z = int(p.get("z_mm", 0) or 0)
        except Exception:
            z = 0
        zs.append(z)
        fam = p.get("orientation_family") or "unknown"
        families.append(str(fam))

    n = len(seq)
    if n == 0:
        return {"n": 0}

    z_min = int(min(zs))
    z_max = int(max(zs))
    z_mean = float(sum(zs) / float(n)) if n else 0.0

    # running max_z per step
    max_z_by_step: List[int] = []
    cur_max = -10**9
    for z in zs:
        cur_max = max(cur_max, int(z))
        max_z_by_step.append(int(cur_max))

    # histogram of z (bucketed)
    hist: Dict[str, int] = {}
    for z in zs:
        b = _bucket(float(z), hist_bin_mm)
        key_s = f"{b}"
        hist[key_s] = int(hist.get(key_s, 0) + 1)

    band = max(0, int(band_mm))

    # tower_jumps: placements above global min + band
    tower_jumps = sum(1 for z in zs if int(z) > int(z_min + band))

    # tower_jump_events: transitions that jump above previous by more than band
    tower_jump_events = 0
    for i in range(1, n):
        if int(zs[i]) > int(zs[i - 1] + band):
            tower_jump_events += 1

    # longest run of strictly increasing z (tower-y behavior)
    max_inc_run = 1
    cur_run = 1
    for i in range(1, n):
        if zs[i] > zs[i - 1]:
            cur_run += 1
            max_inc_run = max(max_inc_run, cur_run)
        else:
            cur_run = 1

    # layerliness_score: fraction near the pallet's minimum z (floor/lowest layer)
    layerliness_score = float(sum(1 for z in zs if int(z) <= int(z_min + band))) / float(n)

    # breakdown planar vs stand_hw (and others) by step segments
    segments: List[dict[str, Any]] = []
    for a, b in _segment_steps(n):
        seg_zs = zs[a : b + 1]
        seg_fams = families[a : b + 1]
        by_family: Dict[str, int] = {}
        for f in seg_fams:
            by_family[f] = int(by_family.get(f, 0) + 1)
        seg = SegmentStats(
            step_from=a,
            step_to=b,
            n=len(seg_zs),
            z_mean=float(sum(seg_zs) / float(len(seg_zs))) if seg_zs else 0.0,
            z_max=int(max(seg_zs)) if seg_zs else 0,
            by_family=by_family,
        )
        segments.append(
            {
                "step_from": seg.step_from,
                "step_to": seg.step_to,
                "n": seg.n,
                "z_mean": seg.z_mean,
                "z_max": seg.z_max,
                "by_family": dict(sorted(seg.by_family.items(), key=lambda kv: (-kv[1], kv[0]))),
            }
        )

    # breakdown by z-buckets too (useful to spot stand_hw dominance at height)
    by_zbin: Dict[str, Dict[str, int]] = {}
    for z, fam in zip(zs, families):
        zb = _bucket(float(z), hist_bin_mm)
        zkey = f"{zb}"
        fams = by_zbin.setdefault(zkey, {})
        fams[fam] = int(fams.get(fam, 0) + 1)
    by_zbin_sorted: Dict[str, Any] = {}
    for zkey in sorted(by_zbin.keys(), key=lambda s: int(s)):
        fams = by_zbin[zkey]
        by_zbin_sorted[zkey] = dict(sorted(fams.items(), key=lambda kv: (-kv[1], kv[0])))

    return {
        "n": n,
        "z_min": z_min,
        "z_mean": z_mean,
        "z_p50": _percentile([float(z) for z in zs], 50),
        "z_p90": _percentile([float(z) for z in zs], 90),
        "z_max": z_max,
        "max_z_by_step": max_z_by_step,
        "hist_z": dict(sorted(hist.items(), key=lambda kv: int(kv[0]))),
        "tower_jumps": int(tower_jumps),
        "tower_jump_events": int(tower_jump_events),
        "max_consecutive_increasing_z": int(max_inc_run),
        "layerliness_score": float(layerliness_score),
        "family_by_step_segments": segments,
        "family_by_zbin": by_zbin_sorted,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze committed placement sequences (torres vs capas).")
    ap.add_argument("placements_json", type=str, help="Path to placements.json (from --dump-placements).")
    ap.add_argument("--band-mm", type=int, default=20, help="Band (mm) for 'near-min-z' and jump detection.")
    ap.add_argument("--hist-bin-mm", type=int, default=100, help="Histogram bucket size for z (mm).")
    ap.add_argument("--pallet-id", type=str, default=None, help="Analyze only this pallet_id (optional).")
    ap.add_argument("--out", type=str, default=None, help="Write summary JSON to this path (optional).")
    args = ap.parse_args()

    src = Path(args.placements_json)
    data = _load_json(src)

    pallets = data.get("pallets", {})
    if not isinstance(pallets, dict):
        raise SystemExit("placements.json: 'pallets' must be an object")

    out: dict[str, Any] = {
        "schema_version": int(data.get("schema_version", 1) or 1),
        "git_head": data.get("git_head", None),
        "params": data.get("params", {}),
        "analysis": {
            "band_mm": int(args.band_mm),
            "hist_bin_mm": int(args.hist_bin_mm),
        },
        "pallets": {},
    }

    selected = args.pallet_id
    for pid, seq in pallets.items():
        if selected is not None and str(pid) != str(selected):
            continue
        if not isinstance(seq, list):
            continue
        seq2 = [p for p in seq if isinstance(p, dict)]
        out["pallets"][str(pid)] = analyze_pallet(
            seq2, band_mm=int(args.band_mm), hist_bin_mm=int(args.hist_bin_mm)
        )

    text = json.dumps(out, indent=2, ensure_ascii=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
