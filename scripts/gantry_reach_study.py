#!/usr/bin/env python3
"""Estudio de alcance y colisiones de un gantry XYZC: layout, carreras, estructura y ciclo.

Entrada: trazas de export_sim_trace.py (p. ej. las 10 seeds de h6_sin_gate_gantry y,
como control, las 10 de h6_sin_gate). Para cada layout candidato (lado de la cinta,
sentido de avance y altura de la cinta):
  1) se dimensionan las carreras X/Y/Z, el pórtico (patas, carriles, puente) y la
     columna Z a partir de todas las poses y recogidas de las trazas;
  2) se evalúa cada caja en el orden real (cell_study.evaluate_run con el GantryModel):
     entrada por el pasillo desde el punto de recogida, bajada desplazada 20 mm al lateral
     libre, deslizamiento, colisiones de garra/cabezal C/columna Z y caja transportada;
  3) se calcula el ciclo completo (recogida en la cola K, volteador para las de canto)
     con perfiles trapezoidales por eje.
Gana el layout con más colocaciones viables, luego menos tiempo de ciclo y menos planta.

  .venv/bin/python scripts/gantry_reach_study.py out/sim3d/seeds_gantry/*.json out/sim3d/seeds_arm/*.json \
      --out out/sim3d/gantry_study
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from palca.robot.gantry import GantryDynamics, GantryFrame  # noqa: E402
from palca.robot.gantry_study import (  # noqa: E402
    b_axis_cycle_s, conveyor_layout, cycle_json, evaluate_cell, queue_coverage, size_cell, structure_checks)
from palca.robot.r2000 import Gripper  # noqa: E402

MODEL = "Gantry XYZC (pórtico, sin eje B)"


def load_runs(paths):
    runs = []
    for p in paths:
        data = json.loads(Path(p).read_text())
        runs.extend(r for r in data["runs"] if r.get("config") not in ("synthetic", "synthetic-stress"))
    return runs


def summarize(cell, runs, ev) -> dict:
    """Viables, motivos y tiempos, en total y por config."""
    out = {}
    groups = {"todas": list(range(len(runs)))}
    for i, run in enumerate(runs):
        groups.setdefault(run["config"], []).append(i)
    for name, idx in groups.items():
        res = [r for i in idx for r in ev[i][0]]
        cyc = [c for i in idx for c in ev[i][1]]
        steps = [s for i in idx for s in runs[i]["steps"]]
        fails = [r for r in res if not r.ok]
        ct = np.array([c["cycle_s"] for c in cyc])
        canto = np.array([s.get("orientation") in ("HWL", "WHL") for s in steps])
        bt = np.array([b_axis_cycle_s(c) for c in cyc])
        out[name] = dict(
            n=len(res), n_ok=len(res) - len(fails), fail_kinds=dict(Counter(r.reason.split("(")[0] for r in fails)),
            gripper_fits=int(sum(r.gripper_fits for r in res)), canto_share=float(canto.mean()),
            cycle_mean_s=float(ct.mean()), cycle_p50_s=float(np.median(ct)), cycle_p95_s=float(np.percentile(ct, 95)),
            cycle_max_s=float(ct.max()), cycle_plana_s=float(ct[~canto].mean()),
            cycle_canto_s=float(ct[canto].mean()) if canto.any() else None,
            cycle_b_axis_mean_s=float(bt.mean()), cycle_b_axis_canto_s=float(bt[canto].mean()) if canto.any() else None,
            boxes_per_hour=float(3600.0 / ct.mean()),
            lift_mean_mm=float(np.mean([r.entry_lift_mm for r in res])),
            examples=[dict(config=runs[i]["config"], seed=runs[i]["seed"], step=r.step, pallet=r.pallet, reason=r.reason)
                      for i in idx for r in ev[i][0] if not r.ok][:12])
    return out


_RUNS = None


def _init(paths):
    global _RUNS
    _RUNS = load_runs(paths)


def _candidate(args):
    side, flow, height, frame_kw = args
    conv = conveyor_layout(side, flow, height)
    frame = GantryFrame(**frame_kw)
    cell, picks = size_cell(_RUNS, conv, frame=frame)
    ev = evaluate_cell(cell, _RUNS, picks, keep_poses=False)
    s = summarize(cell, _RUNS, ev)
    return dict(side=side, flow=flow, height_mm=height, frame=frame_kw, strokes=cell.strokes,
                footprint=cell.footprint(), heights=cell.heights(), summary=s,
                n=s["todas"]["n"], n_ok=s["todas"]["n_ok"], cycle_mean_s=s["todas"]["cycle_mean_s"])


def rank(c):
    """Más viables; luego ciclo (a 0,1 s), planta y, en empate, la cinta más cerca de 900 mm (altura estándar)."""
    return (-c["n_ok"], round(c["cycle_mean_s"], 1), round(c["footprint"]["area_m2"], 1), abs(c["height_mm"] - 900))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("traces", nargs="+")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "sim3d" / "gantry_study")
    ap.add_argument("--sides", default="N,S")
    ap.add_argument("--flows", default="E,W")
    ap.add_argument("--heights", default="700,800,900,1000,1100")
    ap.add_argument("--cell", help="Sin barrido: lado,sentido,altura (p. ej. N,E,900)")
    ap.add_argument("--head-d", type=float, default=250.0,
                    help="Diámetro del cabezal C (mm). Con 300 chocan algunas cajas de canto de cara 260 mm")
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args(argv)
    t0 = time.time()
    _init(args.traces)
    runs = _RUNS
    frame_kw = dict(head_d_mm=args.head_d)
    if args.cell:
        side, flow, h = args.cell.split(",")
        cands = [(side, flow, float(h), frame_kw)]
    else:
        cands = [(s, f, float(h), frame_kw) for s in args.sides.split(",") for f in args.flows.split(",")
                 for h in args.heights.split(",")]
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(args.traces,)) as pool:
        evaluated = list(pool.map(_candidate, cands))
        print(f"[gantry] {len(evaluated)} layouts evaluados ({time.time() - t0:.0f} s)", flush=True)
        for c in sorted(evaluated, key=rank)[:12]:
            print(f"[gantry] cinta {c['side']}/{c['flow']} a {c['height_mm']:.0f} mm: {c['n_ok']}/{c['n']} ok, "
                  f"ciclo {c['cycle_mean_s']:.2f} s, planta {c['footprint']['length']:.0f}×{c['footprint']['width']:.0f}, "
                  f"carreras {c['strokes']}", flush=True)
        best = min(evaluated, key=rank)
        # Sensibilidad del mejor layout: cabezal C y columna Z.
        sens = [(best["side"], best["flow"], best["height_mm"], dict(frame_kw, head_d_mm=d, ram_mm=min(250.0, d)))
                for d in (200.0, 250.0, 300.0, 350.0) if d != args.head_d]
        sensitivity = list(pool.map(_candidate, sens)) if not args.cell else []
    finalize(args, runs, best, evaluated, sensitivity, frame_kw, t0)


def finalize(args, runs, best, evaluated, sensitivity, frame_kw, t0):
    conv = conveyor_layout(best["side"], best["flow"], best["height_mm"])
    cell, picks = size_cell(runs, conv, frame=GantryFrame(**frame_kw))
    # Para dibujarla: la cinta termina en el tope de la cabeza y sale 1,5 m por fuera del pórtico.
    upstream_end = cell.rail_x[0] - 1500 if conv["tangent"][0] > 0 else cell.rail_x[1] + 1500
    conv.update(length_mm=round(abs(conv["point"][0] - upstream_end)), downstream_mm=0)
    ev = evaluate_cell(cell, runs, picks, keep_poses=True)
    per_run = []
    for run, (res, cyc), pk in zip(runs, ev, picks):
        rows = []
        for step, r, c in zip(run["steps"], res, cyc):
            d = asdict(r)
            d["cycle"] = cycle_json(pk[step["step"]], c)
            rows.append(d)
        per_run.append(dict(config=run["config"], seed=run["seed"], results=rows))
    summary = summarize(cell, runs, ev)
    span = cell.x_range[1] - cell.x_range[0]
    coverage = queue_coverage(runs, picks, cell, sorted({*np.arange(1500, span + 1, 500).tolist(), span}))
    tele = replace(cell, frame=replace(cell.frame, telescopic=True))
    pick_idx = [s.get("chosen_index") for run in runs for s in run["steps"] if s.get("chosen_index") is not None]
    gj = cell.to_json()
    out = dict(
        robot=MODEL, type="gantry", gripper=asdict(cell.gripper),
        cell=dict(base_x=cell.base_x, base_y=cell.base_y, side=best["side"], flow=best["flow"],
                  side_label={"N": "cinta detrás del lado largo (y > 820)", "S": "cinta delante del lado largo (y < −20)"}[best["side"]]),
        gantry=gj, conveyor=conv, best=best, candidates=sorted(evaluated, key=rank), sensitivity=sensitivity,
        summary=summary, structure_checks=structure_checks(cell), queue_coverage=coverage,
        heights_telescopic=tele.heights(), dynamics=asdict(cell.dyn),
        pick_index_hist={str(i): pick_idx.count(i) for i in sorted(set(pick_idx))},
        runs=per_run, seconds=time.time() - t0)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "study.json").write_text(json.dumps(out, default=_json_default), encoding="utf-8")
    s = summary["todas"]
    print(f"[gantry] mejor: cinta {best['side']}/{best['flow']} a {best['height_mm']:.0f} mm -> {s['n_ok']}/{s['n']} "
          f"viables, ciclo medio {s['cycle_mean_s']:.2f} s; carreras {cell.strokes}; planta "
          f"{gj['footprint']['length']:.0f}×{gj['footprint']['width']:.0f} mm; nave {gj['heights']['building_fixed']} mm "
          f"(telescópica {gj['heights']['building_telescopic']}); {args.out / 'study.json'} ({time.time() - t0:.0f} s)",
          flush=True)


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


if __name__ == "__main__":
    main()
