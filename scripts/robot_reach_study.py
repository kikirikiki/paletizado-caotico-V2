#!/usr/bin/env python3
"""Estudio de alcance del R-2000iC/165F: mejor posición del robot, pedestal y cinta.

Entrada: trazas de export_sim_trace.py (p. ej. las 10 seeds de referencia).
1) Barrido rápido (IK vectorizada, herramienta vertical) de lado, posición, distancia
   y pedestal sobre las poses final, aproximación y entrada mínima de todas las cajas,
   más una rejilla de toda la envolvente 1240x840x2400.
2) Evaluación completa de los mejores candidatos de cada lado: pasillo de entrada,
   bajada desplazada, límites articulares y colisiones del robot (mallas del STEP),
   la garra y la caja transportada contra las cajas ya colocadas.
3) Propuesta de la estación de recogida en la cinta (planas por arriba, canto por H×W).

  .venv/bin/python scripts/robot_reach_study.py out/sim3d/seeds/*.json \
      --mesh out/sim3d/robot_r2000ic165f.json --out out/sim3d/robot_study
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from palca.robot.cell_study import (  # noqa: E402
    RobotModel, approach_dir, collision_points, cycle, evaluate_run, placements_from_run, tool_down_reach, yaw_towards)
from palca.robot.r2000 import ArmGeometry, Cell, Gripper, ik_world  # noqa: E402

SIDES = {"S": "frente largo (y<0)", "N": "frente largo (y>800)", "W": "lado corto (x<0)", "E": "lado corto (x>1200)"}


def base_xy(side: str, along: float, dist: float) -> tuple[float, float]:
    return {"S": (along, -20 - dist), "N": (along, 820 + dist),
            "W": (-20 - dist, along), "E": (1220 + dist, along)}[side]


def make_cell(side, along, dist, ped, gripper) -> Cell:
    bx, by = base_xy(side, along, dist)
    return Cell(base_x=bx, base_y=by, pedestal_mm=ped, yaw_deg=yaw_towards((bx, by)), gripper=gripper)


def load_runs(paths):
    runs = []
    for p in paths:
        data = json.loads(Path(p).read_text())
        runs.extend(r for r in data["runs"] if r.get("config") not in ("synthetic", "synthetic-stress"))
    return runs


def trace_targets(runs, lateral=20.0):
    finals, pres, entries = [], [], []
    for run in runs:
        prev_by = {}
        for s in run["steps"]:
            prev = prev_by.setdefault(s["pallet"], [])
            b = (s["x"], s["y"], s["z"], s["dx"], s["dy"], s["dz"])
            f = np.array([s["x"] + s["dx"] / 2, s["y"] + s["dy"] / 2, s["z"] + s["dz"]])
            d = approach_dir(b, prev)
            pre = f + (np.array([d[0], d[1], 0]) * lateral if d else 0)
            finals.append(f); pres.append(pre); entries.append(pre + [0, 0, 100.0])
            prev.append(b)
    return np.array(finals), np.array(pres), np.array(entries)


def envelope_grid():
    xs, ys, zs = np.linspace(130, 1070, 9), np.linspace(130, 670, 6), np.arange(150, 2401, 250)
    return np.stack(np.meshgrid(xs, ys, zs, indexing="ij"), -1).reshape(-1, 3)


def phase1(runs, gripper, sides, alongs_long, alongs_short, dists, peds):
    arm = ArmGeometry()
    finals, pres, entries = trace_targets(runs)
    tr = np.vstack([finals, pres, entries])
    grid = envelope_grid()
    rows = []
    for side in sides:
        alongs = alongs_long if side in "SN" else alongs_short
        for along in alongs:
            for dist in dists:
                for ped in peds:
                    cell = make_cell(side, along, dist, ped, gripper)
                    ok, m = tool_down_reach(arm, gripper, cell, tr)
                    gok, _ = tool_down_reach(arm, gripper, cell, grid)
                    rows.append(dict(side=side, along=float(along), dist=float(dist), ped=float(ped),
                                     trace_ok=float(ok.mean()), env_ok=float(gok.mean()),
                                     margin=float(m.min()) if ok.all() else float("-inf")))
    return rows


_MODEL = None


def _init(mesh, gripper_kw):
    global _MODEL
    _MODEL = (collision_points(Path(mesh) if mesh else None), Gripper(**gripper_kw))


def _evaluate(args):
    cand, runs = args
    pts, gripper = _MODEL
    cell = make_cell(cand["side"], cand["along"], cand["dist"], cand["ped"], gripper)
    model = RobotModel(cell, pts)
    res = []
    for run in runs:
        res.extend(evaluate_run(model, placements_from_run(run)))
    fails = [r for r in res if not r.ok]
    margins = [r.min_margin_deg for r in res if r.min_margin_deg is not None]
    kinds = {}
    for r in fails:
        k = r.reason.split("(")[0]
        kinds[k] = kinds.get(k, 0) + 1
    return dict(cand, n=len(res), n_ok=len(res) - len(fails), fail_kinds=kinds,
                min_margin=min(margins) if margins else None,
                p5_margin=float(np.percentile(margins, 5)) if margins else None,
                lift_mean=float(np.mean([r.entry_lift_mm for r in res])),
                examples=[dict(step=r.step, pallet=r.pallet, reason=r.reason) for r in fails[:8]])


def conveyor_study(cell: Cell, runs, heights_mm=(700.0, 800.0, 900.0, 1000.0, 1100.0)):
    """Estación de recogida: ángulo respecto a J1 (palet = 0°), radio y altura; cajas con L cruzada."""
    dims = {}
    for run in runs:
        for s in run["steps"]:
            canto = s.get("orientation") in ("HWL", "WHL")
            dims[(tuple(s["orig_lwh"]), canto)] = True
    best = None
    for height in heights_mm:
        zc = height - 144.0
        for ang in (90.0, -90.0, 180.0, 135.0, -135.0):
            for radius in np.arange(1100, 2501, 100):
                res = conveyor_eval(cell, dims, ang, radius, 0.0, zc)
                if res["ok"] and (best is None or res["min_margin"] > best["min_margin"]):
                    best = dict(res, angle=ang, radius=float(radius), height_mm=float(height))
    if best is None:
        return None
    zc = best["z_conveyor"]
    # Longitud de cinta alcanzable alrededor del punto de recogida (cajas en fila).
    reach = {}
    for sgn in (1, -1):
        t = 0.0
        while conveyor_eval(cell, dims, best["angle"], best["radius"], sgn * (t + 50), zc)["ok"] and t < 4000:
            t += 50
        reach[sgn] = t
    best["reach_along_mm"] = dict(upstream=reach[-1], downstream=reach[1])
    return best


def conveyor_eval(cell, dims, ang, radius, shift, zc):
    yaw = np.radians(cell.yaw_deg + ang)
    u = np.array([np.cos(yaw), np.sin(yaw), 0.0])
    tang = np.array([-u[1], u[0], 0.0])
    p = np.array([cell.base_x, cell.base_y, zc]) + u * radius + tang * shift
    margins, ok = [], True
    down = np.array([0, 0, -1.0])
    for (lwh, canto) in dims:
        l, w, h = lwh
        if canto:  # ventosas sobre la cara H×W que mira al robot; L cruzada a la cinta
            tcp = p - u * (l / 2) + np.array([0, 0, h / 2])
            poses = [(tcp - u * 200, u, np.array([0, 0, 1.0])), (tcp, u, np.array([0, 0, 1.0])),
                     (tcp + np.array([0, 0, 250.0]) - u * 60, u, np.array([0, 0, 1.0])),
                     (np.array([p[0], p[1], zc + l + 300.0]), down, tang)]  # reorientada, L vertical
        else:
            tcp = p + np.array([0, 0, h])
            poses = [(tcp + np.array([0, 0, 300.0]), down, tang), (tcp, down, tang),
                     (tcp + np.array([0, 0, 250.0]), down, tang)]
        for pos, approach, xa in poses:
            r = ik_world(cell, pos, approach, xa)
            if r.q is None or r.reason:
                ok = False
                break
            margins.append(r.margin_deg)
        if not ok:
            break
    return dict(ok=ok, min_margin=min(margins) if ok and margins else -1e9, point=p.round(1).tolist(),
                direction=u.round(4).tolist(), tangent=tang.round(4).tolist(), z_conveyor=zc)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("traces", nargs="+")
    ap.add_argument("--mesh", help="JSON de mallas (step_to_robot_mesh.py); sin él, cajas del STEP")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "sim3d" / "robot_study")
    ap.add_argument("--flange-to-tcp", type=float, default=200.0)
    ap.add_argument("--top-per-side", type=int, default=6)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--peds", default="0,200,400,600,800,1000,1200")
    ap.add_argument("--cell", help="Sin búsqueda: evalúa lado,along,dist,pedestal (p. ej. N,450,1000,800)")
    args = ap.parse_args(argv)
    t0 = time.time()
    runs = load_runs(args.traces)
    gripper_kw = dict(flange_to_tcp_mm=args.flange_to_tcp)
    gripper = Gripper(**gripper_kw)
    if args.cell:
        side, along, dist, ped = args.cell.split(",")
        cand = dict(side=side, along=float(along), dist=float(dist), ped=float(ped), trace_ok=None, env_ok=None, margin=None)
        _init(args.mesh, gripper_kw)
        best = _evaluate((cand, runs))
        finalize(args, runs, gripper, gripper_kw, best, [best], {}, t0)
        return
    peds = [float(v) for v in args.peds.split(",")]
    rows = phase1(runs, gripper, "SNWE", np.arange(-300, 1501, 150), np.arange(-300, 1101, 100),
                  np.arange(500, 1801, 100), peds)
    print(f"[estudio] fase 1: {len(rows)} candidatos, {sum(r['trace_ok'] == 1 for r in rows)} con todas las "
          f"poses de traza alcanzables ({time.time() - t0:.0f} s)", flush=True)
    key = lambda r: (r["trace_ok"], r["env_ok"], r["margin"], -r["ped"])
    chosen = []
    for side in "SNWE":
        side_rows = sorted((r for r in rows if r["side"] == side), key=key, reverse=True)
        chosen.extend(side_rows[:args.top_per_side])
    rank = lambda e: (e["n_ok"], e["env_ok"], e["min_margin"] or -1e9, -e["ped"])
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(args.mesh, gripper_kw)) as pool:
        evaluated = list(pool.map(_evaluate, [(c, runs) for c in chosen]))
        print(f"[estudio] fase 2: {len(evaluated)} candidatos ({time.time() - t0:.0f} s)", flush=True)
        # Refinado local alrededor del mejor (paso fino en posición y pedestal).
        top = max(evaluated, key=rank)
        seen = {(e["side"], e["along"], e["dist"], e["ped"]) for e in evaluated}
        local = []
        for da in (-100, -50, 0, 50, 100):
            for dd in (-100, -50, 0, 50, 100):
                for dp in (-100, 0, 100):
                    c = dict(top, along=top["along"] + da, dist=top["dist"] + dd, ped=max(0.0, top["ped"] + dp))
                    k = (c["side"], c["along"], c["dist"], c["ped"])
                    if k not in seen and c["dist"] >= 450:
                        seen.add(k)
                        local.append({key: c[key] for key in ("side", "along", "dist", "ped", "trace_ok", "env_ok", "margin")})
        evaluated += list(pool.map(_evaluate, [(c, runs) for c in local]))
    for e in sorted(evaluated, key=rank, reverse=True)[:15]:
        print(f"[estudio] {e['side']} along={e['along']:.0f} dist={e['dist']:.0f} ped={e['ped']:.0f}: "
              f"{e['n_ok']}/{e['n']} ok, margen min {e['min_margin']}, fallos {e['fail_kinds']}", flush=True)
    best = max(evaluated, key=rank)
    phase1_top = {s: sorted((r for r in rows if r["side"] == s), key=key, reverse=True)[:5] for s in "SNWE"}
    finalize(args, runs, gripper, gripper_kw, best, evaluated, phase1_top, t0)


def finalize(args, runs, gripper, gripper_kw, best, evaluated, phase1_top, t0):
    cell = make_cell(best["side"], best["along"], best["dist"], best["ped"], gripper)
    conv = conveyor_study(cell, runs)
    # Resultado completo de la celda elegida, con poses y ciclo, para el visor.
    _init(args.mesh, gripper_kw)
    model = RobotModel(cell, _MODEL[0])
    per_run = []
    for run in runs:
        res = evaluate_run(model, placements_from_run(run), keep_poses=True)
        rows_out, q = [], (0, 0, 0, 0, -90, 0)
        for step, r in zip(run["steps"], res):
            cyc = cycle(model, step, r, conv, q)
            if cyc["waypoints"]:
                q = tuple(cyc["waypoints"][-1]["q"])
            rows_out.append(dict(asdict(r), cycle=cyc))
        per_run.append(dict(config=run["config"], seed=run["seed"], results=rows_out))
    pick_idx = [s.get("chosen_index") for run in runs for s in run["steps"] if s.get("chosen_index") is not None]
    out = dict(
        robot="FANUC R-2000iC/165F", gripper=asdict(gripper),
        cell=dict(base_x=cell.base_x, base_y=cell.base_y, base_z=cell.base_z, pedestal_mm=cell.pedestal_mm,
                  yaw_deg=cell.yaw_deg, side=best["side"], side_label=SIDES[best["side"]], along=best["along"],
                  dist_from_envelope_mm=best["dist"], limits=list(cell.arm.limits)),
        conveyor=conv, best=best, candidates=sorted(evaluated, key=lambda e: -e["n_ok"]),
        phase1_top=phase1_top,
        pick_index_hist={str(i): pick_idx.count(i) for i in sorted(set(pick_idx))},
        runs=per_run, seconds=time.time() - t0)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "study.json").write_text(json.dumps(out, default=float), encoding="utf-8")
    print(f"[estudio] mejor: {best['side']} along={best['along']} dist={best['dist']} ped={best['ped']} "
          f"-> {best['n_ok']}/{best['n']}; cinta {conv and conv.get('angle')} r={conv and conv.get('radius')}; "
          f"{args.out / 'study.json'} ({time.time() - t0:.0f} s)", flush=True)


if __name__ == "__main__":
    main()
