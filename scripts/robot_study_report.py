#!/usr/bin/env python3
"""Informe del estudio de alcance (Markdown + plano SVG acotado) a partir de robot_reach_study.py.

  .venv/bin/python scripts/robot_study_report.py out/sim3d/robot_study/study.json \
      out/sim3d/robot_cell/study.json --out docs/ESTUDIO_ROBOT_R2000.md
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from math import cos, radians, sin
from pathlib import Path

SIDE = {"N": "detrás del lado largo (y > 820)", "S": "delante del lado largo (y < −20)",
        "W": "en el lado corto izquierdo (x < −20)", "E": "en el lado corto derecho (x > 1220)"}
POSE = {"entrada": "entrada sobre el pasillo", "bajada": "bajada", "aproximacion": "aproximación lateral",
        "final": "posición final"}


def classify(reason: str) -> str:
    pose, kind, what = (reason.split("(")[0].split(":") + ["", ""])[:3]
    if kind == "alcance":
        return f"{POSE.get(pose, pose)}: fuera de alcance / límite ({what.replace('_', ' ')})"
    return f"{POSE.get(pose, pose)}: colisión de {what.upper() if what.startswith('j') else what.replace('_', ' ')}"


def svg_plan(cell: dict, conv: dict | None) -> str:
    """Planta: x a la derecha, y hacia arriba (se invierte para SVG)."""
    pts = [(-20, -20), (1220, 820), (cell["base_x"], cell["base_y"])]
    if conv:
        pts.append(tuple(conv["point"][:2]))
    pad = 700
    x0 = min(p[0] for p in pts) - pad; x1 = max(p[0] for p in pts) + pad
    y0 = min(p[1] for p in pts) - pad; y1 = max(p[1] for p in pts) + pad
    s = 900 / (x1 - x0)
    X = lambda x: (x - x0) * s
    Y = lambda y: (y1 - y) * s
    w, h = 900, (y1 - y0) * s
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" font-family="sans-serif" font-size="13">',
           '<rect width="100%" height="100%" fill="#ffffff"/>']
    reach = 2655 + 0  # alcance de catálogo del centro de muñeca, referencia visual
    out.append(f'<circle cx="{X(cell["base_x"]):.1f}" cy="{Y(cell["base_y"]):.1f}" r="{reach * s:.1f}" fill="#f2c50010" stroke="#c9a400" stroke-dasharray="6 4"/>')
    out.append(f'<rect x="{X(0):.1f}" y="{Y(800):.1f}" width="{1200 * s:.1f}" height="{800 * s:.1f}" fill="#d8b27a" stroke="#7a5a2a"/>')
    out.append(f'<rect x="{X(-20):.1f}" y="{Y(820):.1f}" width="{1240 * s:.1f}" height="{840 * s:.1f}" fill="none" stroke="#2b7bff" stroke-dasharray="4 3"/>')
    out.append(f'<text x="{X(600):.1f}" y="{Y(400):.1f}" text-anchor="middle">palet 1200×800</text>')
    bx, by = cell["base_x"], cell["base_y"]
    out.append(f'<rect x="{X(bx - 450):.1f}" y="{Y(by + 450):.1f}" width="{900 * s:.1f}" height="{900 * s:.1f}" fill="#59606a" opacity="0.5"/>')
    out.append(f'<circle cx="{X(bx):.1f}" cy="{Y(by):.1f}" r="{300 * s:.1f}" fill="#f2c500" stroke="#222"/>')
    out.append(f'<text x="{X(bx):.1f}" y="{Y(by) + 4:.1f}" text-anchor="middle" font-weight="bold">J1</text>')
    # Cotas: distancia J1 - borde de la envolvente y x de J1.
    edge_y = 820 if cell["side"] == "N" else -20
    out.append(f'<line x1="{X(bx):.1f}" y1="{Y(edge_y):.1f}" x2="{X(bx):.1f}" y2="{Y(by):.1f}" stroke="#d0202e"/>')
    out.append(f'<text x="{X(bx) + 6:.1f}" y="{(Y(edge_y) + Y(by)) / 2:.1f}" fill="#d0202e">{abs(by - edge_y):.0f} mm</text>')
    out.append(f'<line x1="{X(0):.1f}" y1="{Y(by):.1f}" x2="{X(bx):.1f}" y2="{Y(by):.1f}" stroke="#d0202e" stroke-dasharray="3 3"/>')
    out.append(f'<text x="{(X(0) + X(bx)) / 2:.1f}" y="{Y(by) - 6:.1f}" fill="#d0202e" text-anchor="middle">x = {bx:.0f}</text>')
    if conv:
        px, py = conv["point"][:2]
        ux, uy = conv["direction"][:2]
        tx, ty = conv["tangent"][:2]
        corners = []
        for a, b in ((600, -400), (600, 400), (-7900, 400), (-7900, -400)):
            corners.append((px + tx * a + ux * b, py + ty * a + uy * b))
        out.append('<polygon points="' + " ".join(f"{X(cx):.1f},{Y(cy):.1f}" for cx, cy in corners)
                   + '" fill="#4a556030" stroke="#4a5560"/>')
        out.append(f'<circle cx="{X(px):.1f}" cy="{Y(py):.1f}" r="7" fill="#00c8ff" stroke="#222"/>')
        out.append(f'<text x="{X(px) + 10:.1f}" y="{Y(py) - 8:.1f}">recogida ({px:.0f}, {py:.0f}), cinta a {conv.get("height_mm", conv["z_conveyor"] + 144):.0f} mm</text>')
        out.append(f'<line x1="{X(bx):.1f}" y1="{Y(by):.1f}" x2="{X(px):.1f}" y2="{Y(py):.1f}" stroke="#00a0cc" stroke-dasharray="5 4"/>')
        out.append(f'<text x="{(X(bx) + X(px)) / 2 + 6:.1f}" y="{(Y(by) + Y(py)) / 2:.1f}" fill="#007799">{conv["radius"]:.0f} mm</text>')
    # Ejes.
    out.append(f'<line x1="{X(0):.1f}" y1="{Y(0):.1f}" x2="{X(300):.1f}" y2="{Y(0):.1f}" stroke="#d0202e" stroke-width="2"/>')
    out.append(f'<line x1="{X(0):.1f}" y1="{Y(0):.1f}" x2="{X(0):.1f}" y2="{Y(300):.1f}" stroke="#1a8f4c" stroke-width="2"/>')
    out.append(f'<text x="{X(320):.1f}" y="{Y(0) + 4:.1f}" fill="#d0202e">x</text><text x="{X(0) - 4:.1f}" y="{Y(330):.1f}" fill="#1a8f4c">y</text>')
    out.append(f'<text x="10" y="{h - 10:.0f}" fill="#666">Planta en mm; origen en la esquina del palet nominal. Círculo discontinuo: 2655 mm desde J1.</text>')
    out.append("</svg>")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("search", type=Path, help="study.json del barrido")
    ap.add_argument("cell", type=Path, help="study.json de la celda elegida (--cell)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--traces", nargs="+", required=True, help="Trazas de export_sim_trace.py usadas en el estudio")
    args = ap.parse_args(argv)
    search = json.loads(args.search.read_text())
    celld = json.loads(args.cell.read_text())
    cell, conv, best = celld["cell"], celld["conveyor"], celld["best"]
    import sys
    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from palca.robot.cell_study import tool_down_reach
    from palca.robot.r2000 import ArmGeometry, Cell, Gripper
    steps = [s for p in args.traces for s in json.loads(Path(p).read_text())["runs"][0]["steps"]]
    finals = np.array([[s["x"] + s["dx"] / 2, s["y"] + s["dy"] / 2, s["z"] + s["dz"]] for s in steps])
    c_obj = Cell(base_x=cell["base_x"], base_y=cell["base_y"], pedestal_mm=cell["pedestal_mm"], yaw_deg=cell["yaw_deg"],
                 gripper=Gripper(**celld["gripper"]))
    final_ok, final_margin = tool_down_reach(ArmGeometry(), c_obj.gripper, c_obj, finals)
    mean_w = float(np.mean([s["orig_lwh"][1] for s in steps]))
    results = [r for run in celld["runs"] for r in run["results"]]
    fails = [r for r in results if not r["ok"]]
    kinds = Counter(classify(r["reason"]) for r in fails)
    per_side = {}
    for c in search["candidates"]:
        if c["side"] not in per_side or c["n_ok"] > per_side[c["side"]]["n_ok"]:
            per_side[c["side"]] = c
    top = sorted(search["candidates"], key=lambda e: (-e["n_ok"], -(e["min_margin"] or -1e9)))[:8]
    hist = celld.get("pick_index_hist", {})
    total_picks = sum(hist.values())
    # Cola fija con la cabeza en la estación: solo cuenta el alcance aguas arriba.
    slots = 1 + int(conv["reach_along_mm"]["upstream"] // mean_w) if conv else 1
    beyond = sum(v for k, v in hist.items() if int(k) >= slots)
    svg_path = args.out.with_suffix(".svg")
    svg_path.write_text(svg_plan(cell, conv), encoding="utf-8")
    n, n_ok = len(results), len(results) - len(fails)
    seeds = [run["seed"] for run in celld["runs"]]
    lines = [
        "# Estudio de alcance · FANUC R-2000iC/165F con garra de 4 ventosas", "",
        f"Configuración `h6_sin_gate`, {len(seeds)} seeds de referencia ({', '.join(map(str, seeds))}), "
        f"{n} colocaciones en el orden real del algoritmo. Generado con `scripts/robot_reach_study.py` y "
        "`scripts/robot_study_report.py` (cinemática medida en el STEP del robot).", "",
        "## Recomendación", "",
        f"- **Robot {SIDE[cell['side']]}**: eje J1 en **x = {cell['base_x']:.0f}, y = {cell['base_y']:.0f} mm** "
        f"(a {cell['dist_from_envelope_mm']:.0f} mm del borde de la envolvente), mirando al centro del palet.",
        f"- **Pedestal de {cell['pedestal_mm']:.0f} mm** (J2 a {cell['pedestal_mm'] + 670:.0f} mm del suelo; palet en el suelo).",
        (f"- **Estación de recogida** a {conv['radius']:.0f} mm de J1, girada {conv['angle']:.0f}° respecto a la dirección "
         f"del palet, en ({conv['point'][0]:.0f}, {conv['point'][1]:.0f}); superficie de cinta a "
         f"{conv.get('height_mm', conv['z_conveyor'] + 144):.0f} mm. Cajas con el largo L cruzado a la cinta, "
         "así la cara H×W de las de canto mira al robot.") if conv else "- Sin estación de recogida viable.",
        f"- Resultado: **{n_ok} / {n} colocaciones viables ({100 * n_ok / n:.1f} %)**; todas las recogidas en la cinta "
        f"son alcanzables y las 4 ventosas (300 × 253 mm) caben en la cara de agarre de todas las cajas.", "",
        f"![Planta de la célula]({svg_path.name})", "",
        "## Qué se comprueba en cada caja", "",
        "Con las cajas ya colocadas de su palet (estado real del robot al colocarla):",
        "1. **Entrada**: la garra con la caja colgando sube hasta librar todas las cajas del pasillo entre la caja y el robot (+50 mm).",
        "2. **Bajada** vertical desplazada 20 mm hacia el lateral libre con más holgura (cenital si no hay ninguno) y "
        "**deslizamiento** a la posición final.",
        "3. En cada pose (cada 150 mm de bajada): IK dentro de límites articulares y sin invadir ninguna caja colocada ni "
        "el palet con el robot (malla de los eslabones del STEP), la garra o la caja transportada.",
        "4. Recogida: planas por la cara superior; de canto por la cara H×W en horizontal, levantar y reorientar "
        "(J5/J6) para dejarla con H×W arriba.", "",
        "## Comparación de posiciones (mejor de cada lado)", "",
        "| Lado | J1 a lo largo | Distancia | Pedestal | Viables |", "|---|---:|---:|---:|---:|"]
    for side in "NESW":
        c = per_side.get(side)
        if c:
            lines.append(f"| {SIDE[side]} | {c['along']:.0f} | {c['dist']:.0f} | {c['ped']:.0f} | "
                         f"{c['n_ok']} / {c['n']} ({100 * c['n_ok'] / c['n']:.1f} %) |")
    lines += ["", "El algoritmo empieza a llenar por la esquina (−20, −20), lejos del lado N: con el robot detrás, "
              "las primeras columnas quedan al fondo y no estorban.", "",
              "Sensibilidad alrededor del óptimo (la meseta es ancha: ±100 mm en x o distancia apenas cambia):", "",
              "| Lado | x / y J1 | Distancia | Pedestal | Viables |", "|---|---:|---:|---:|---:|"]
    for c in top:
        lines.append(f"| {c['side']} | {c['along']:.0f} | {c['dist']:.0f} | {c['ped']:.0f} | {c['n_ok']} / {c['n']} |")
    lines += ["", f"## Las {len(fails)} colocaciones no viables", "",
              "| Motivo | Cajas |", "|---|---:|"]
    for k, v in kinds.most_common():
        lines.append(f"| {k} | {v} |")
    lines += ["",
              f"Las {int(final_ok.sum())} de {len(final_ok)} posiciones finales se alcanzan (margen mínimo a los límites "
              f"{final_margin.min():.1f}°): los fallos no son de alcance de la posición, sino de **la secuencia**: "
              "cajas bajas metidas en un hueco entre columnas altas (el antebrazo J3/la muñeca J5 tocan la columna al "
              "bajar) y cajas en la esquina lejana cuando ya hay pilas altas delante (no se puede pasar la caja por encima). "
              "El algoritmo no conoce el robot; usar este modelo como restricción de factibilidad al elegir hueco "
              "debería evitar la mayoría sin cambiar de robot (a cambio de algo de compactación, por medir).", "",
              "## Cinta y lookahead K = 15", "",
              f"El algoritmo elige cualquiera de las 15 cajas visibles; en estas seeds la posición elegida en la cola se "
              f"reparte casi uniforme (n.º 15 es la más frecuente). Con la estación propuesta el robot alcanza "
              f"±{conv['reach_along_mm']['upstream']:.0f} mm a lo largo de la cinta. Con una cola fija y la primera caja "
              f"en la estación, eso son las {slots} primeras posiciones (ancho medio de caja a lo largo de la cinta "
              f"{mean_w:.0f} mm): **{beyond} de {total_picks} recogidas ({100 * beyond / total_picks:.0f} %) quedarían "
              f"fuera**. Hace falta que la cinta lleve la caja elegida a la estación (buffer/desviador), un séptimo eje "
              f"o limitar K.", "",
              "## Supuestos y límites", "",
              "- Cinemática del STEP (a1=312, d1=670, a2=1075, a3=225, d4=1280, d6=215 mm), igual que el catálogo. "
              "Límites de catálogo: J1 ±185°, J2 −60/+76°, J3 −132/+180° (sobre la horizontal), J4 ±360°, J5 ±125°, "
              "J6 ±360°: **confirmar con el manual del modelo comprado**.",
              "- Garra centrada en J6, cara de ventosas a 200 mm de la brida; 4 ventosas Ø90 a 210 × 163 mm; marco y "
              "columna modelados como cajas. Con la herramienta vertical J5 = 90° + J3: el antebrazo no puede subir "
              "más de 35°.",
              "- Colisiones con nube de puntos de las mallas (40 mm) contra las cajas colocadas y el palet; el "
              "compensador se aproxima rígido. No se comprueban la cinta, el pedestal ni el trayecto entre poses "
              "fuera de los puntos muestreados, ni dinámica, cargas o tiempo de ciclo.",
              "- El STEP es un modelo de terceros (CGTrader): sirve para el estudio, pero la geometría exacta debe "
              "confirmarse con el CAD oficial de FANUC antes de fijar el layout.", ""]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[informe] {args.out} y {svg_path}")


if __name__ == "__main__":
    main()
