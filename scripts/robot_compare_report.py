#!/usr/bin/env python3
"""Informe comparativo brazo FANUC R-2000iC/165F frente a gantry XYZC (Markdown + plano SVG).

Entradas: study.json de la celda del brazo (robot_reach_study.py --cell), study.json del
gantry (gantry_reach_study.py), las trazas usadas y, opcional, las mallas del brazo para
medir su altura y su barrido en planta reales (sin ellas se usan las cajas del STEP).

  .venv/bin/python scripts/robot_compare_report.py --arm out/sim3d/arm/robot_cell_study.json \
      --arm-mesh out/sim3d/arm/robot_r2000ic165f.json --gantry out/sim3d/gantry_study/study.json \
      --traces out/sim3d/seeds_gantry/*.json out/sim3d/seeds_arm/*.json --out docs/COMPARATIVA_BRAZO_GANTRY.md
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from palca.robot.cell_study import RobotModel, collision_points  # noqa: E402
from palca.robot.r2000 import FLOOR_Z_MM, Cell, Gripper, link_frames  # noqa: E402

POSE = {"entrada": "entrada sobre el pasillo", "bajada": "bajada", "aproximacion": "aproximación lateral",
        "final": "posición final"}
WHAT = {"cabezal_c": "cabezal del eje C", "columna_z": "columna Z", "garra": "garra",
        "caja_transportada": "caja transportada"}


def classify(reason: str) -> str:
    pose, kind, what = (reason.split("(")[0].split(":") + ["", ""])[:3]
    if kind == "alcance":
        return f"{POSE.get(pose, pose)}: fuera de alcance / límite ({what.replace('_', ' ')})"
    name = what.upper() if what.startswith("j") else WHAT.get(what, what.replace("_", " "))
    return f"{POSE.get(pose, pose)}: colisión de {name}"


def pct(a, b, d=1):
    return f"{100 * a / b:.{d}f} %" if b else "—"


def fmt(v, d=0):
    return f"{v:,.{d}f}".replace(",", " ")


# --------------------------------------------------------------------------- brazo: barrido real

def arm_envelope(arm: dict, mesh: Path | None, spacing: float = 80.0) -> dict:
    """Altura máxima y extensión en planta del brazo en todas las poses del estudio."""
    c = arm["cell"]
    cell = Cell(base_x=c["base_x"], base_y=c["base_y"], pedestal_mm=c["pedestal_mm"], yaw_deg=c["yaw_deg"],
                gripper=Gripper(**arm["gripper"]))
    model = RobotModel(cell, collision_points(mesh if mesh and Path(mesh).exists() else None, spacing))
    qs = {tuple(w["q"]) for run in arm["runs"] for r in run["results"] for w in r["cycle"]["waypoints"]}
    qs |= {tuple(p["q"]) for run in arm["runs"] for r in run["results"] for p in r["poses"] if p["q"]}
    lo, hi, top_q, top_z = np.full(3, np.inf), np.full(3, -np.inf), None, -np.inf
    for q in qs:
        pts, _ = model.world_points(q)
        lo, hi = np.minimum(lo, pts.min(0)), np.maximum(hi, pts.max(0))
        if pts[:, 2].max() > top_z:
            top_z, top_q = pts[:, 2].max(), q
    return dict(lo=lo.tolist(), hi=hi.tolist(), top_q=list(top_q), max_height_floor=float(top_z - FLOOR_Z_MM),
                n_poses=len(qs), cell=cell)


def arm_footprint(arm: dict, env: dict) -> dict:
    c, conv = arm["cell"], arm["conveyor"]
    xs = [-20, 1220, c["base_x"] - 450, c["base_x"] + 450, env["lo"][0], env["hi"][0]]
    ys = [-20, 820, c["base_y"] - 450, c["base_y"] + 450, env["lo"][1], env["hi"][1]]
    if conv:
        p, u, t = np.array(conv["point"][:2]), np.array(conv["direction"][:2]), np.array(conv["tangent"][:2])
        for a, b in ((600, 400), (600, -400), (-600, 400), (-600, -400)):
            q = p + t * a + u * b
            xs.append(q[0]); ys.append(q[1])
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    return dict(x0=x0, x1=x1, y0=y0, y1=y1, length=x1 - x0, width=y1 - y0, area_m2=(x1 - x0) * (y1 - y0) / 1e6)


def arm_queue_out(arm: dict, steps: list[dict]) -> tuple[int, int, int]:
    """Como robot_study_report: cola fija con la cabeza en la estación; alcance aguas arriba."""
    conv = arm["conveyor"]
    mean_w = float(np.mean([s["orig_lwh"][1] for s in steps]))
    slots = 1 + int(conv["reach_along_mm"]["upstream"] // mean_w) if conv else 1
    hist = arm.get("pick_index_hist", {})
    total = sum(hist.values())
    beyond = sum(v for k, v in hist.items() if int(k) >= slots)
    return slots, beyond, total


# --------------------------------------------------------------------------- plano SVG

class Svg:
    def __init__(self):
        self.out = []

    def add(self, s):
        self.out.append(s)

    def rect(self, x, y, w, h, **kw):
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" {_attrs(kw)}/>')

    def line(self, x1, y1, x2, y2, **kw):
        self.add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" {_attrs(kw)}/>')

    def text(self, x, y, s, **kw):
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" {_attrs(kw)}>{s}</text>')

    def poly(self, pts, **kw):
        self.add('<polygon points="' + " ".join(f"{a:.1f},{b:.1f}" for a, b in pts) + f'" {_attrs(kw)}/>')

    def circle(self, x, y, r, **kw):
        self.add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" {_attrs(kw)}/>')


def _attrs(kw):
    return " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in kw.items())


def plan_view(svg: Svg, ox, oy, s, box, draw):
    """Planta: x a la derecha, y hacia arriba. box = (x0, y0, x1, y1) en mm; (ox, oy) esquina sup. izq."""
    x0, y0, x1, y1 = box
    X = lambda x: ox + (x - x0) * s
    Y = lambda y: oy + (y1 - y) * s
    draw(X, Y)
    return (x1 - x0) * s, (y1 - y0) * s


def svg_plan(arm, arm_env, arm_fp, gst, gfp, out_path: Path):
    g, conv = gst["gantry"], gst["conveyor"]
    pad = 500
    abox = (arm_fp["x0"] - pad, arm_fp["y0"] - pad, arm_fp["x1"] + pad, arm_fp["y1"] + pad)
    gbox = (gfp["x0"] - pad, gfp["y0"] - pad, gfp["x1"] + pad, gfp["y1"] + pad)
    s = 1240 / ((abox[2] - abox[0]) + (gbox[2] - gbox[0]) + 1500)
    svg = Svg()
    title_h = 40
    # ----- plantas
    ax, ay = 20, title_h + 30
    aw = (abox[2] - abox[0]) * s
    gx = ax + aw + 1500 * s * 0.6
    ah, gh = (abox[3] - abox[1]) * s, (gbox[3] - gbox[1]) * s
    plan_h = max(ah, gh)

    def common(X, Y):
        svg.rect(X(-20), Y(820), 1240 * s, 840 * s, fill="#d8b27a", stroke="#7a5a2a")
        svg.text(X(600), Y(400) + 4, "palet", text_anchor="middle", font_size="11")

    def dim_box(X, Y, fp, color="#d0202e"):
        svg.rect(X(fp["x0"]), Y(fp["y1"]), fp["length"] * s, fp["width"] * s, fill="none", stroke=color,
                 stroke_dasharray="6 4", stroke_width="1.5")
        svg.text(X((fp["x0"] + fp["x1"]) / 2), Y(fp["y1"]) - 6, f'{fmt(fp["length"])} mm', text_anchor="middle",
                 fill=color, font_size="12")
        svg.text(X(fp["x1"]) + 6, Y((fp["y0"] + fp["y1"]) / 2), f'{fmt(fp["width"])} mm', fill=color, font_size="12")

    def draw_arm(X, Y):
        c, cv = arm["cell"], arm["conveyor"]
        if cv:
            p, u, t = np.array(cv["point"][:2]), np.array(cv["direction"][:2]), np.array(cv["tangent"][:2])
            pts = [p + t * a + u * b for a, b in ((600, -400), (600, 400), (-3000, 400), (-3000, -400))]
            svg.poly([(X(q[0]), Y(q[1])) for q in pts], fill="#4a556030", stroke="#4a5560")
            svg.circle(X(p[0]), Y(p[1]), 5, fill="#00c8ff", stroke="#222")
        lo, hi = arm_env["lo"], arm_env["hi"]
        svg.rect(X(lo[0]), Y(hi[1]), (hi[0] - lo[0]) * s, (hi[1] - lo[1]) * s, fill="#f2c50018", stroke="#c9a400",
                 stroke_dasharray="3 3")
        common(X, Y)
        bx, by = c["base_x"], c["base_y"]
        svg.rect(X(bx - 450), Y(by + 450), 900 * s, 900 * s, fill="#59606a", opacity="0.6")
        svg.circle(X(bx), Y(by), 300 * s, fill="#f2c500", stroke="#222")
        svg.text(X(bx), Y(by) + 4, "J1", text_anchor="middle", font_size="10", font_weight="bold")
        dim_box(X, Y, arm_fp)

    def draw_gantry(X, Y):
        f = g["frame"]
        t = np.array(conv["tangent"][:2]); u = np.array(conv["direction"][:2])
        p = np.array(conv["point"][:2])
        L = conv.get("length_mm", 9000)
        pts = [p + u * b + t * a for a, b in ((0, -400), (0, 400), (-L, 400), (-L, -400))]
        svg.poly([(X(q[0]), Y(q[1])) for q in pts], fill="#4a556030", stroke="#4a5560")
        tp = conv["tipper"]
        pv, d = np.array(tp["pivot"][:2]), np.array(tp["d"][:2])
        a0, a1 = pv + d * tp["plate_len_mm"], pv - d * tp["backstop_mm"]
        w = tp["plate_w_mm"] / 2
        svg.poly([(X(q[0]), Y(q[1])) for q in (a0 + u * w, a0 - u * w, a1 - u * w, a1 + u * w)],
                 fill="#1f8a7055", stroke="#1f8a70")
        svg.text(X(pv[0]), Y(pv[1] + w) - 4, "volteador", text_anchor="middle", font_size="10", fill="#1f8a70")
        xr, yr = g["x_range"], g["y_range"]
        svg.rect(X(xr[0]), Y(yr[1]), (xr[1] - xr[0]) * s, (yr[1] - yr[0]) * s, fill="none", stroke="#0a6cff",
                 stroke_dasharray="4 3")
        svg.text(X(xr[0]) + 4, Y(yr[1]) + 12, "carreras del TCP", fill="#0a6cff", font_size="10")
        common(X, Y)
        for yy in g["rails_y"]:
            svg.rect(X(g["rail_x"][0]), Y(yy + f["rail_w_mm"] / 2), (g["rail_x"][1] - g["rail_x"][0]) * s,
                     f["rail_w_mm"] * s, fill="#3f6fb540", stroke="#3f6fb5")
        for px, py in g["posts"]:
            svg.rect(X(px - f["post_mm"] / 2), Y(py + f["post_mm"] / 2), f["post_mm"] * s, f["post_mm"] * s,
                     fill="#3f6fb5", stroke="#1d3a66")
        bxm = (xr[0] + xr[1]) / 2
        svg.rect(X(bxm - f["bridge_w_mm"] / 2), Y(g["rails_y"][1]), f["bridge_w_mm"] * s,
                 (g["rails_y"][1] - g["rails_y"][0]) * s, fill="#3f6fb520", stroke="#3f6fb5", stroke_dasharray="5 3")
        svg.text(X(bxm), Y(g["rails_y"][0]) + 14, "puente (móvil en X)", text_anchor="middle", font_size="10",
                 fill="#1d3a66")
        dim_box(X, Y, gfp)

    svg.text(ax, title_h, "Brazo FANUC R-2000iC/165F — planta", font_size="15", font_weight="bold")
    plan_view(svg, ax, ay, s, abox, draw_arm)
    svg.text(gx, title_h, "Gantry XYZC — planta (misma escala)", font_size="15", font_weight="bold")
    plan_view(svg, gx, ay, s, gbox, draw_gantry)
    # ----- alzados (misma escala), vistos desde −Y
    hz = max(arm_env["max_height_floor"], g["heights"]["building_fixed"]) + 600
    ey = ay + plan_h + 70
    ground = ey + hz * s

    def zline(x0, x1, zf, label, color, dash="6 4"):
        svg.line(x0, ground - zf * s, x1, ground - zf * s, stroke=color, stroke_dasharray=dash)
        svg.text(x1 + 4, ground - zf * s + 4, label, fill=color, font_size="11")

    def stack(X):
        svg.rect(X(-20), ground - (144 + 2400) * s, 1240 * s, 2400 * s, fill="#d8b27a40", stroke="#7a5a2a",
                 stroke_dasharray="3 3")
        svg.rect(X(0), ground - 144 * s, 1200 * s, 144 * s, fill="#b98d55")

    svg.text(ax, ey - 10, "Brazo — alzado (vista desde −Y)", font_size="15", font_weight="bold")
    AX = lambda x: ax + (x - abox[0]) * s
    svg.line(ax, ground, ax + aw, ground, stroke="#333")
    stack(AX)
    c = arm["cell"]
    svg.rect(AX(c["base_x"] - 450), ground - c["pedestal_mm"] * s, 900 * s, c["pedestal_mm"] * s, fill="#59606a")
    cell = arm_env["cell"]
    fr = link_frames(cell.arm, arm_env["top_q"])
    wm = cell.world_matrix()
    chain = [(wm @ m)[:3, 3] for m in fr]
    svg.add('<polyline points="' + " ".join(f"{AX(p[0]):.1f},{ground - (p[2] - FLOOR_Z_MM) * s:.1f}" for p in chain)
            + '" fill="none" stroke="#f2c500" stroke-width="7" stroke-linejoin="round"/>')
    hmax = arm_env["max_height_floor"]
    zline(ax, ax + aw, hmax, f"brazo máx. {fmt(hmax)} mm", "#c77700")
    zline(ax, ax + aw, hmax + 300, f"nave ≥ {fmt(hmax + 300)} mm", "#d0202e")
    zline(ax, ax + aw, 2544, "pilas 2400 + palet", "#7a5a2a", "2 3")
    svg.text(gx, ey - 10, "Gantry — alzado (vista desde −Y)", font_size="15", font_weight="bold")
    GX = lambda x: gx + (x - gbox[0]) * s
    gw = (gbox[2] - gbox[0]) * s
    svg.line(gx, ground, gx + gw, ground, stroke="#333")
    stack(GX)
    f, hs = g["frame"], g["heights"]
    for px in sorted({p[0] for p in g["posts"]}):
        svg.rect(GX(px - f["post_mm"] / 2), ground - hs["post"] * s, f["post_mm"] * s, hs["post"] * s, fill="#3f6fb5")
    svg.rect(GX(g["rail_x"][0]), ground - hs["rail_top"] * s, (g["rail_x"][1] - g["rail_x"][0]) * s,
             f["rail_h_mm"] * s, fill="#3f6fb5aa")
    bxm = (g["x_range"][0] + g["x_range"][1]) / 2
    svg.rect(GX(bxm - f["bridge_w_mm"] / 2), ground - hs["bridge_top"] * s, f["bridge_w_mm"] * s, f["bridge_h_mm"] * s,
             fill="#1d3a66")
    zt = g["z_range"][1] - FLOOR_Z_MM
    ram_bot = zt + Gripper(**gst["gripper"]).flange_to_tcp_mm + f["head_h_mm"]
    svg.rect(GX(bxm - f["ram_mm"] / 2), ground - hs["ram_top_fixed"] * s, f["ram_mm"] * s,
             (hs["ram_top_fixed"] - ram_bot) * s, fill="#f2c500", stroke="#9a7c00")
    svg.rect(GX(bxm - f["head_d_mm"] / 2), ground - ram_bot * s, f["head_d_mm"] * s, f["head_h_mm"] * s, fill="#2e2e2e")
    zc = conv["height_mm"]
    svg.rect(GX(conv["point"][0] - conv.get("length_mm", 9000) if conv["tangent"][0] > 0 else conv["point"][0]),
             ground - zc * s, conv.get("length_mm", 9000) * s, 60 * s, fill="#4a5560")
    zline(gx, gx + gw, hs["bridge_underside"], f"bajo puente {fmt(hs['bridge_underside'])} mm", "#1d3a66")
    zline(gx, gx + gw, hs["building_telescopic"], f"nave ≥ {fmt(hs['building_telescopic'])} mm (Z telescópica)",
          "#c77700")
    zline(gx, gx + gw, hs["building_fixed"], f"nave ≥ {fmt(hs['building_fixed'])} mm (columna Z fija, arriba)",
          "#d0202e")
    zline(gx, gx + gw, 2544, "pilas 2400 + palet", "#7a5a2a", "2 3")
    H = ground + 40
    svg.text(20, H - 12, "mm; origen en la esquina del palet nominal. Rojo discontinuo: huella en planta (envolvente "
             "de todo lo fijo y móvil). Brazo: amarillo punteado = barrido real del brazo en el estudio.",
             fill="#666", font_size="11")
    W = int(gx + (gbox[2] - gbox[0]) * s + 300)
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H:.0f}" font-family="sans-serif" '
            f'font-size="12"><rect width="100%" height="100%" fill="#ffffff"/>')
    out_path.write_text(head + "\n" + "\n".join(svg.out) + "\n</svg>\n", encoding="utf-8")


# --------------------------------------------------------------------------- informe

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", type=Path, required=True, help="study.json de la celda del brazo")
    ap.add_argument("--arm-mesh", type=Path, help="Mallas del brazo (step_to_robot_mesh.py)")
    ap.add_argument("--gantry", type=Path, required=True, help="study.json de gantry_reach_study.py")
    ap.add_argument("--traces", nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    arm = json.loads(args.arm.read_text())
    gst = json.loads(args.gantry.read_text())
    traces = {}
    for p in args.traces:
        for r in json.loads(Path(p).read_text())["runs"]:
            traces.setdefault(r["config"], []).append(r)
    arm_steps = [s for r in traces.get("h6_sin_gate", []) for s in r["steps"]]
    env = arm_envelope(arm, args.arm_mesh)
    afp = arm_footprint(arm, env)
    g, gs, conv = gst["gantry"], gst["summary"], gst["conveyor"]
    gfp, hs = g["footprint"], g["heights"]
    svg_path = args.out.with_suffix(".svg")
    svg_plan(arm, env, afp, gst, gfp, svg_path)

    a_res = [r for run in arm["runs"] for r in run["results"]]
    a_fail = [r for r in a_res if not r["ok"]]
    a_kinds = Counter(classify(r["reason"]) for r in a_fail)
    slots, beyond, total = arm_queue_out(arm, arm_steps)
    kpi = {cfg: [r["kpi"]["kpi"] for r in runs] for cfg, runs in traces.items()}
    gg = gs.get("h6_sin_gate_gantry") or gs["todas"]
    ga = gs.get("h6_sin_gate")
    g_runs = [r for run in gst["runs"] for r in run["results"]]
    g_kinds = {cfg: Counter(classify(r["reason"]) for run in gst["runs"] if run["config"] == cfg
                            for r in run["results"] if not r["ok"]) for cfg in {run["config"] for run in gst["runs"]}}
    cov = gst["queue_coverage"]
    full = next(c for c in cov if c["picks_ok"] >= 1.0)
    canto = gg["canto_share"]
    best = gst["best"]
    hmax = env["max_height_floor"]
    mean = lambda v: 100 * float(np.mean(v)) if v else float("nan")
    side_txt = {"N": "detrás del lado largo (y > 820)", "S": "delante del lado largo (y < −20)"}[best["side"]]
    head = g["frame"]["head_d_mm"]
    worse = [c for c in gst.get("sensitivity", []) if c["n_ok"] < c["n"]]
    if gs["todas"]["n_ok"] == gs["todas"]["n"]:
        g_limit = (f"nada con cabezal C ≤ Ø{head:.0f}: garra, cabezal y columna bajan dentro de la huella de la caja"
                   + ("; con " + ", ".join(f"Ø{c['frame']['head_d_mm']:.0f} fallan {c['n'] - c['n_ok']}" for c in worse)
                      + " (cabezal contra vecinas más altas en cajas de canto de cara 260 mm)" if worse else ""))
    else:
        g_limit = (f"cabezal C Ø{head:.0f} contra vecinas más altas en cajas de canto de cara estrecha "
                   f"({gs['todas']['n'] - gs['todas']['n_ok']} cajas); nada de alcance")
    g_limit += ". Fuera de la colocación: altura de nave, longitud del pórtico para la cola y doble toma de las de canto"

    L = []
    L += ["# Brazo FANUC R-2000iC/165F frente a gantry XYZC · alcance, colisiones y layout", "",
          "Mismas comprobaciones por caja en el orden real del algoritmo (`palca.robot.cell_study.evaluate_run`): "
          "entrada por el pasillo, bajada desplazada 20 mm al lateral libre, deslizamiento, límites y colisiones con "
          "nube de puntos contra las cajas ya colocadas, y caja transportada. Generado con "
          "`scripts/gantry_reach_study.py` y `scripts/robot_compare_report.py`.", "",
          "## Qué configuración de palet", "",
          f"- **Brazo**: `h6_sin_gate` (preset `arm6_4orient`, accesibilidad 400 mm), 10 seeds de referencia, "
          f"KPI medio **{mean(kpi.get('h6_sin_gate', [])):.2f} %**.",
          f"- **Gantry**: `h6_sin_gate_gantry` = mismas técnicas H1+H3+H5+H6 sin gate pero con el preset "
          f"`gantry_4orient` (`accessibility_delta_mm = 0`, `configs/sweeps/h6_sin_gate_gantry.yaml`). Es la "
          f"configuración que tendría un pórtico, que baja en vertical y no necesita la restricción de 400 mm "
          f"(con delta 0 la deuda de accesibilidad H1 queda inactiva por construcción). KPI medio "
          f"**{mean(kpi.get('h6_sin_gate_gantry', [])):.2f} %** en las mismas 10 seeds (exportación verificada "
          f"bit-idéntica en la 50021).",
          "- Como control, el gantry también se evalúa sobre los palets de `h6_sin_gate` (los del brazo).", "",
          "## Resumen", "",
          "| | Brazo R-2000iC/165F | Gantry XYZC |", "|---|---|---|",
          f"| Colocaciones viables | **{len(a_res) - len(a_fail)} / {len(a_res)} ({pct(len(a_res) - len(a_fail), len(a_res))})** "
          f"en `h6_sin_gate` | **{gg['n_ok']} / {gg['n']} ({pct(gg['n_ok'], gg['n'])})** en `h6_sin_gate_gantry`"
          + (f"; {ga['n_ok']} / {ga['n']} ({pct(ga['n_ok'], ga['n'])}) en `h6_sin_gate`" if ga else "") + " |",
          f"| Qué limita la colocación | antebrazo J3 / muñeca J5 contra columnas altas al bajar a huecos; esquina "
          f"lejana con pilas delante (alcance) | {g_limit} |",
          f"| Recogida en la cola K = 15 | estación fija: alcanza {slots} posiciones; **{beyond} de {total} recogidas "
          f"({pct(beyond, total, 0)}) fuera** | **todas** con carrera X de {fmt(g['strokes']['X'])} mm "
          f"(cola en línea bajo el pórtico) |",
          f"| Cajas de canto ({100 * canto:.0f} %) | se cogen por H×W en horizontal y se reorientan con J5/J6 | "
          f"volteador de 90° y segunda toma (sin eje B) |",
          f"| Tiempo de ciclo | no modelado en el estudio del brazo | {gg['cycle_mean_s']:.1f} s medio "
          f"({gg['boxes_per_hour']:.0f} cajas/h), p95 {gg['cycle_p95_s']:.1f} s; con eje B ≈ "
          f"{gg['cycle_b_axis_mean_s']:.1f} s |",
          f"| Huella en planta | {fmt(afp['length'])} × {fmt(afp['width'])} mm = **{afp['area_m2']:.1f} m²** "
          f"(palet, pedestal, estación y barrido del brazo) | {fmt(gfp['length'])} × {fmt(gfp['width'])} mm = "
          f"**{gfp['area_m2']:.1f} m²** (exterior de patas; incluye cola de 15 cajas y volteador) |",
          f"| Altura de nave necesaria | **{fmt(hmax + 300)} mm** (brazo a {fmt(hmax)} mm en su pose más alta + 300) | "
          f"**{fmt(hs['building_fixed'])} mm** con columna Z fija (sale por encima del puente) o "
          f"**{fmt(hs['building_telescopic'])} mm** con Z telescópica; bajo puente {fmt(hs['bridge_underside'])} mm |",
          "", f"![Plantas y alzados a la misma escala]({svg_path.name})", "",
          "## Layout recomendado del gantry", "",
          f"- **Pórtico** de {fmt(gfp['length'])} × {fmt(gfp['width'])} mm (exterior), {len(g['posts'])} patas de "
          f"{g['frame']['post_mm']:.0f} × {g['frame']['post_mm']:.0f} mm en x = "
          f"{', '.join(fmt(x) for x in sorted({p[0] for p in g['posts']}))} e y = "
          f"{', '.join(fmt(y) for y in g['rails_y'])} (ejes de los carriles X, de "
          f"{g['frame']['rail_w_mm']:.0f} × {g['frame']['rail_h_mm']:.0f} mm, a {fmt(hs['rail_top'])} mm del suelo). "
          f"Puente de {g['frame']['bridge_w_mm']:.0f} × {g['frame']['bridge_h_mm']:.0f} mm; columna Z de "
          f"{g['frame']['ram_mm']:.0f} × {g['frame']['ram_mm']:.0f} mm; cabezal C Ø{g['frame']['head_d_mm']:.0f} × "
          f"{g['frame']['head_h_mm']:.0f} mm; garra de 4 ventosas con la cara a 200 mm de la brida C.",
          f"- **Carreras** (TCP): X {fmt(g['x_range'][0])} … {fmt(g['x_range'][1])} = **{fmt(g['strokes']['X'])} mm**; "
          f"Y {fmt(g['y_range'][0])} … {fmt(g['y_range'][1])} = **{fmt(g['strokes']['Y'])} mm**; Z "
          f"{fmt(g['z_range'][0])} … {fmt(g['z_range'][1])} (sobre la cara del palet) = **{fmt(g['strokes']['Z'])} mm**; "
          "C ±180°.",
          f"- **Palet** en el mismo sitio (origen en su esquina), un solo palet bajo el pórtico; se saca por el lado "
          f"sin cinta, entre patas.",
          f"- **Cinta** {side_txt}, paralela a X, a {conv['height_mm']:.0f} mm del suelo, {conv['width_mm']:.0f} mm de "
          f"ancho y a 150 mm de la envolvente; avance hacia {'+X' if conv['tangent'][0] > 0 else '−X'}. Cola con el "
          f"largo L cruzado y 15 mm entre cajas, con la cabeza en x = {fmt(conv['point'][0])}.",
          f"- **Volteador** de 90° en línea, tras la cabeza (pivote en x = {fmt(conv['tipper']['pivot'][0])}): la caja "
          "de canto se deja tumbada con la cara −L contra el tope, la cuna la pone de pie y el gantry la coge por H×W.",
          "", "### Comparación de layouts (mejores)", "",
          "| Cinta | Avance | Altura cinta | Viables | Ciclo medio | Carrera X | Planta |", "|---|---|---:|---:|---:|---:|---:|"]
    for c in gst["candidates"][:10]:
        L.append(f"| {c['side']} | {c['flow']} | {c['height_mm']:.0f} | {c['n_ok']} / {c['n']} | "
                 f"{c['cycle_mean_s']:.2f} s | {fmt(c['strokes']['X'])} | {c['footprint']['area_m2']:.1f} m² |")
    L += ["", "La viabilidad no depende del layout (el gantry baja siempre en vertical y las carreras se dimensionan "
          "para cubrir todo): el layout decide el tiempo de ciclo, la planta y la altura.", "",
          "## Cola K = 15: carrera X frente a recogidas alcanzables", "",
          "El algoritmo elige cualquiera de las 15 cajas visibles y suele coger las del fondo (la n.º 15 es la más "
          "frecuente). Con la cola en línea bajo el pórtico, cada metro de carrera X añade unas dos posiciones:", "",
          "| Carrera X | Recogidas alcanzables | Posiciones siempre alcanzables |", "|---:|---:|---:|"]
    for c in cov:
        L.append(f"| {fmt(c['stroke_x_mm'])} mm | {100 * c['picks_ok']:.1f} % | {c['positions_always']} |")
    L += ["", f"Con {fmt(full['stroke_x_mm'])} mm se alcanzan todas: el algoritmo no cambia (K = 15). Con un pórtico más "
          "corto habría que limitar K (re-simular con `lookahead_k` menor, a costa de compactación) o una mesa de "
          "acumulación de 2–3 carriles (la misma cola en la mitad de longitud).", "",
          "## Tiempo de ciclo del gantry", "",
          f"Perfil trapezoidal por eje (X/Y {gst['dynamics']['x'][0] / 1000:.1f} m/s y "
          f"{gst['dynamics']['x'][1] / 1000:.1f} m/s², Z {gst['dynamics']['z'][0] / 1000:.1f} m/s y "
          f"{gst['dynamics']['z'][1] / 1000:.1f} m/s², C {gst['dynamics']['c'][0]:.0f}°/s), ejes sincronizados, "
          f"movimientos en secuencia segura (subir, trasladar, bajar; sin solapes), últimos 50 mm de cada bajada a "
          f"0,3 m/s, vacío {gst['dynamics']['grip_s']} s / {gst['dynamics']['release_s']} s, volteador "
          f"{gst['dynamics']['tip_s']} s.", "",
          "| Palets | Ciclo medio | p50 | p95 | Máx | Plana | De canto (volteador) | Con eje B (estim.) |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name in ("h6_sin_gate_gantry", "h6_sin_gate"):
        sm = gs.get(name)
        if sm:
            L.append(f"| `{name}` | {sm['cycle_mean_s']:.2f} s | {sm['cycle_p50_s']:.2f} | {sm['cycle_p95_s']:.2f} | "
                     f"{sm['cycle_max_s']:.1f} | {sm['cycle_plana_s']:.2f} | {sm['cycle_canto_s']:.2f} | "
                     f"{sm['cycle_b_axis_mean_s']:.2f} |")
    L += ["", f"El volteador cuesta ~{gg['cycle_canto_s'] - gg['cycle_plana_s']:.1f} s por caja de canto "
          f"({100 * canto:.0f} % de las cajas). Un eje B (±90°) lo evitaría (≈ {gg['cycle_b_axis_mean_s']:.1f} s de "
          "media, estimación: sin el volteador y +1 s de aproximación horizontal y giro), a cambio de un cabezal "
          "mayor, el par de la caja en voladizo y acceso lateral a la cara H×W en la cola. El modelo DES del algoritmo "
          "usa 14 s por caja.", "",
          "## Colocaciones no viables", "",
          "| Robot y palets | Motivo | Cajas |", "|---|---|---:|"]
    for k, v in a_kinds.most_common():
        L.append(f"| Brazo · `h6_sin_gate` | {k} | {v} |")
    for cfg in ("h6_sin_gate_gantry", "h6_sin_gate"):
        for k, v in g_kinds.get(cfg, Counter()).most_common():
            L.append(f"| Gantry · `{cfg}` | {k} | {v} |")
        if cfg in g_kinds and not g_kinds[cfg]:
            L.append(f"| Gantry · `{cfg}` | ninguna (cabezal C Ø{head:.0f}) | 0 |")
    L += ["", "En el gantry la garra (300 × 253 mm) siempre cabe dentro de la cara de agarre (la menor es 330 × 260), "
          "así que la garra y la columna bajan dentro de la huella de la caja: solo el cabezal C, más ancho que 260 mm, "
          "puede tocar una vecina más alta pegada (< 20 mm). Es el equivalente gantry del choque del antebrazo.", "",
          "Sensibilidad al cabezal C (mismo layout):", "", "| Cabezal C | Columna Z | Viables |", "|---:|---:|---:|"]
    for c in sorted(gst.get("sensitivity", []) + [best], key=lambda c: c["frame"].get("head_d_mm", 300)):
        L.append(f"| Ø{c['frame'].get('head_d_mm', 300):.0f} | {c['frame'].get('ram_mm', 250):.0f} | "
                 f"{c['n_ok']} / {c['n']} ({pct(c['n_ok'], c['n'], 2)}) |")
    L += ["", "## Estructura: interferencias", "", "| Comprobación | Holgura mínima | OK |", "|---|---:|:-:|"]
    for c in gst["structure_checks"]:
        L.append(f"| {c['what']} | {fmt(c['min_gap_mm'])} mm | {'sí' if c['ok'] else '**NO**'} |")
    L += ["", f"El puente pasa a {fmt(hs['bridge_underside'] - 2544)} mm por encima de las pilas de 2400 mm: lo "
          "fija la caja más alta transportada por encima de las pilas, no las pilas. La columna Z fija mide "
          f"{fmt(g['ram_length_mm'])} mm (carrera + {g['frame']['ram_guide_mm']:.0f} mm de guiado) y arriba del todo "
          f"asoma hasta {fmt(hs['ram_top_fixed'])} mm del suelo: es lo que marca la altura de nave.", "",
          "## Supuestos y límites", "",
          "- Gantry: datos propuestos, no de un fabricante. Ejes XYZC sin B; patas 300 × 300 a ≥ 300 mm de cualquier "
          "parte móvil, la cinta o el palet; luz máxima de carril 6 m (patas intermedias fuera del palet). Velocidades y "
          "aceleraciones típicas de pórticos de paletizado: **confirmar con el fabricante**.",
          "- Colisiones con nube de puntos (30 mm) de la garra, el cabezal C (cilindro) y la columna Z (prisma hasta "
          "encima de las pilas) contra las cajas colocadas y el palet, en entrada, bajada cada 150 mm, aproximación y "
          "final; la caja transportada contra las colocadas. Igual que el brazo: no se comprueba el trayecto entre "
          "poses fuera de los puntos muestreados (en el gantry los traslados van por encima de todo por construcción).",
          "- Recogida: cola con la geometría del visor (L cruzada, 15 mm entre cajas, la cinta compacta la cola). "
          "Volteador: cuna de 750 mm, tope de 520 mm, giro de 90° en 1,8 s; el gantry espera encima a "
          "hypot(L, H) + 50 mm mientras gira.",
          "- Altura del brazo medida con sus mallas en todas las poses del estudio (+300 mm); la del gantry, con la "
          "columna arriba del todo (+300 mm). Huella del brazo: envolvente de palet, pedestal 900 × 900, estación de "
          "recogida y barrido real del brazo; sin vallado en ninguno de los dos.", ""]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(L), encoding="utf-8")
    print(f"[informe] {args.out} y {svg_path}; brazo {env['n_poses']} poses, máx {hmax:.0f} mm; "
          f"planta brazo {afp['area_m2']:.1f} m², gantry {gfp['area_m2']:.1f} m²")


if __name__ == "__main__":
    main()
