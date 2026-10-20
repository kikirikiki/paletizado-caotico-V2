"""Verificación independiente de un testigo de empaquetado offline (herramienta del director, no código de producción).

Comprueba, sin usar el packer:
  1. que el multiconjunto de dimensiones colocadas coincide exactamente con el del Excel canónico (cada caja una vez);
  2. el validador congelado `palca.study.validate` (envolvente, solapes, cara apoyada, soporte >= 0,85);
  3. la regla de accesibilidad del brazo (delta 400 mm) re-implementada en el orden de colocación del testigo
     (umbral base + delta; con exención, base + max(delta, altura + holgura superior));
  4. el KPI oficial (`palca.study.metric`, palets cerrados = todos salvo el último) y Σvol / (n_palets · V).

Uso:  PYTHONPATH=src .venv/bin/python scripts/autopilot/verify_offline_witness.py <testigo.json> [--delta 400] [--adjacency-gap 0]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from math import prod
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from palca.study.metric import ENVELOPE_MM  # noqa: E402
from palca.study.params import REFERENCE_EXCEL  # noqa: E402
from palca.study.validate import validate_pallet  # noqa: E402
from sim.io import load_arrivals  # noqa: E402


def pallets_of(witness: dict) -> list[list[dict]]:
    raw = witness["pallets"]
    raw = raw if isinstance(raw, list) else [raw[k] for k in sorted(raw, key=int)]
    return [p["items"] if isinstance(p, dict) and "items" in p else p for p in raw]


def accessibility_violations(items: list[dict], delta: int, adjacency_gap: int = 0, *,
                             tall_box_exemption: bool = False, top_clearance_mm: int = 0) -> int:
    """Count blocked descents; G expands adjacency, never the supporting footprint."""
    if adjacency_gap < 0:
        raise ValueError("adjacency_gap must be nonnegative")
    if delta == 0:  # Production disables the accessibility control.
        return 0
    placed, bad = [], 0
    for it in items:
        effective_delta = max(delta, it["height_mm"] + top_clearance_mm) if tall_box_exemption else delta
        x0, x1 = it["x_mm"], it["x_mm"] + it["length_mm"]
        y0, y1 = it["y_mm"], it["y_mm"] + it["width_mm"]
        local = max((q[4] for q in placed if min(q[1], x1) - max(q[0], x0) > 0 and min(q[3], y1) - max(q[2], y0) > 0),
                    default=0)
        if any(max(q[0], x0) - min(q[1], x1) <= adjacency_gap and max(q[2], y0) - min(q[3], y1) <= adjacency_gap and q[4] > local + effective_delta
               for q in placed):
            bad += 1
        placed.append((x0, x1, y0, y1, it["z_mm"] + it["height_mm"]))
    return bad


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("witness", type=Path)
    parser.add_argument("--delta", type=int, default=400)
    parser.add_argument("--adjacency-gap", type=int, default=0,
                        help="Consider adjacent gaps <= G mm on both axes (default: production rule, 0)")
    parser.add_argument("--tall-box-exemption", action="store_true")
    parser.add_argument("--top-clearance-mm", type=int, default=0)
    args = parser.parse_args()
    if args.adjacency_gap < 0:
        parser.error("--adjacency-gap must be nonnegative")
    witness = json.loads(args.witness.read_text())
    pallets = pallets_of(witness)
    arrivals = load_arrivals(str(ROOT / REFERENCE_EXCEL))
    excel = collections.Counter(tuple(sorted((a.length_mm, a.width_mm, a.height_mm))) for a in arrivals)
    pool = collections.defaultdict(list)
    for a in arrivals:
        pool[tuple(sorted((a.length_mm, a.width_mm, a.height_mm)))].append((a.length_mm, a.width_mm, a.height_mm))
    placed = collections.Counter(tuple(sorted((it["length_mm"], it["width_mm"], it["height_mm"])))
                                 for items in pallets for it in items)
    ok_multiset = placed == excel
    volume = prod(ENVELOPE_MM)
    utils, n_viol, n_acc = [], 0, 0
    for i, items in enumerate(pallets, start=1):
        original = {it["box_id"]: pool[tuple(sorted((it["length_mm"], it["width_mm"], it["height_mm"])))][0] for it in items}
        violations = validate_pallet(items, original, min_support=0.85)
        acc = accessibility_violations(items, args.delta, args.adjacency_gap,
                                       tall_box_exemption=args.tall_box_exemption,
                                       top_clearance_mm=args.top_clearance_mm)
        util = sum(it["length_mm"] * it["width_mm"] * it["height_mm"] for it in items) / volume
        utils.append(util)
        n_viol += len(violations)
        n_acc += acc
        print(f"palet {i}: {len(items)} cajas, utilización {100 * util:.2f} %, violaciones {len(violations)}, accesibilidad {acc}")
    closed = utils[:-1]
    kpi = sum(closed) / len(closed) if closed else float("nan")
    print(f"multiconjunto de dimensiones == Excel: {ok_multiset}")
    print(f"KPI oficial (palets cerrados): {100 * kpi:.3f} %  |  Σvol/(n·V): {100 * sum(utils) / len(utils):.3f} %  |  palets: {len(utils)}")
    print(f"violaciones validador: {n_viol}  |  violaciones accesibilidad (delta {args.delta}, adjacency-gap {args.adjacency_gap}): {n_acc}")
    print(f"regla: tall-box-exemption={args.tall_box_exemption}, top-clearance-mm={args.top_clearance_mm}")
    return 0 if ok_multiset and n_viol == 0 and n_acc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
