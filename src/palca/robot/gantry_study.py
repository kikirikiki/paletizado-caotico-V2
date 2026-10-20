"""Célula de un gantry sobre secuencias exportadas (sim3d): cinta, cola K, volteador y ciclo.

Disposición (todo dentro del pórtico):
  palet    envolvente x∈[-20,1220], y∈[-20,820] (igual que el brazo);
  cinta    paralela a X (lado largo del palet), a un lado (N: y>820 o S: y<-20), con la
           cola de cajas visibles con el largo L cruzado (igual que el visor del brazo) y
           15 mm entre cajas; la cabeza de la cola (n.º 1) junto al volteador;
  volteador cuna de 90° en línea con la cinta, aguas abajo de la cabeza.

Recogida: el gantry coge cualquier caja de la cola por arriba (cara L×W). Las de canto
(HWL/WHL) se dejan tumbadas en la cuna (L a lo largo de la cinta, cara −L contra el
tope); la cuna gira 90° y las deja de pie con la cara H×W arriba; el gantry las vuelve a
coger por arriba. Así la herramienta siempre está vertical (sin eje B).

Colocación: cell_study.evaluate_run con el GantryModel (entrada por el pasillo desde el
punto de recogida, bajada desplazada 20 mm al lateral libre, deslizamiento, colisiones de
garra, cabezal C, columna Z y caja transportada). Ciclo y tiempos: trayectorias de
seguridad (subir, mover en XY+C, bajar) con perfil trapezoidal por eje.
"""
from __future__ import annotations

from dataclasses import replace
from math import ceil

import numpy as np

from .cell_study import ENVELOPE, approach_dir, box_final_matrix, corridor_top, evaluate_run, placements_from_run
from .gantry import GantryCell, GantryFrame, GantryModel, c_of, nearest_c, tool_matrix
from .r2000 import FLOOR_Z_MM, Gripper, _ry

UP = np.array([0.0, 0.0, 1.0])
QUEUE_GAP_MM = 15.0
CONV_WIDTH_MM = 800.0
CONV_GAP_MM = 150.0            # borde de la envolvente - borde de la cinta
TIPPER = dict(plate_len_mm=750.0, plate_w_mm=560.0, backstop_mm=520.0, gap_mm=120.0, thickness_mm=25.0)
CLEAR_MM = 50.0


# --------------------------------------------------------------------------- geometría de la célula

def conveyor_layout(side: str, flow: str, height_mm: float, pivot_x: float | None = None) -> dict:
    """side N/S: lado del palet; flow E/W: sentido de avance (+x/−x); height_mm: superficie desde el suelo."""
    t = np.array([1.0, 0, 0]) if flow == "E" else np.array([-1.0, 0, 0])
    u = np.cross(t, UP)                       # L de la caja cruzada; (u, t, z) directo
    yc = ENVELOPE["y1"] + CONV_GAP_MM + CONV_WIDTH_MM / 2 if side == "N" else ENVELOPE["y0"] - CONV_GAP_MM - CONV_WIDTH_MM / 2
    zc = height_mm + FLOOR_Z_MM
    if pivot_x is None:
        pivot_x = 900.0 if flow == "E" else 300.0
    d = -t                                    # la cuna se extiende aguas arriba del pivote
    w = np.cross(UP, d)
    pivot = np.array([pivot_x, yc, zc])
    stop = pivot - t * (TIPPER["plate_len_mm"] + TIPPER["gap_mm"])
    return dict(side=side, flow=flow, height_mm=float(height_mm), z_conveyor=float(zc),
                point=stop.round(1).tolist(), direction=u.round(4).tolist(), tangent=t.round(4).tolist(),
                width_mm=CONV_WIDTH_MM, queue_head_offset_mm=0.0, queue_gap_mm=QUEUE_GAP_MM,
                tipper=dict(pivot=pivot.round(1).tolist(), d=d.round(4).tolist(), w=w.round(4).tolist(), **TIPPER))


def tipper_base(tip: dict) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = np.column_stack([tip["d"], tip["w"], UP])
    m[:3, 3] = tip["pivot"]
    return m


def tipper_rotation(tip: dict, frac: float = 1.0) -> np.ndarray:
    """Giro de la cuna (fracción 0..1 de 90°) alrededor de su pivote, en el mundo."""
    b = tipper_base(tip)
    r = np.eye(4)
    r[:3, :3] = _ry(np.radians(-90.0 * frac))
    return b @ r @ np.linalg.inv(b)


def queue_offsets(queue: list[dict], conv: dict) -> list[float]:
    """Distancia aguas arriba (mm) del centro de cada caja de la cola al tope de la cabeza."""
    s, out = conv.get("queue_head_offset_mm", 0.0), []
    for b in queue:
        s += b["lwh"][1] / 2 + conv.get("queue_gap_mm", QUEUE_GAP_MM)
        out.append(s)
        s += b["lwh"][1] / 2
    return out


def box_on_conveyor(lwh, conv: dict, offset: float) -> np.ndarray:
    """Caja tumbada en la cola: L según u (cruzada), W según t, H vertical."""
    u, t = np.array(conv["direction"]), np.array(conv["tangent"])
    m = np.eye(4)
    m[:3, :3] = np.column_stack([u, t, UP])
    m[:3, 3] = np.array(conv["point"]) - t * offset + UP * (conv["z_conveyor"] - conv["point"][2] + lwh[2] / 2)
    return m


def box_on_tipper(lwh, tip: dict) -> np.ndarray:
    """Caja tumbada en la cuna: L según d (cara −L contra el tope), H vertical."""
    l, w, h = lwh
    b = tipper_base(tip)
    m = np.eye(4)
    m[:3, :3] = b[:3, :3]
    m[:3, 3] = np.array(tip["pivot"]) + np.array(tip["d"]) * (l / 2) + UP * (h / 2)
    return m


def tool_on_top(box: np.ndarray, half_up: float, long_axis: np.ndarray, prefer_c: float) -> np.ndarray:
    """Herramienta vertical sobre la cara superior; paso largo según long_axis (±, el más cercano a prefer_c)."""
    t = np.eye(4)
    x = long_axis / np.linalg.norm(long_axis)
    t[:3, :3] = np.column_stack([x, np.cross(UP, x), UP])
    t[:3, 3] = box[:3, 3] + UP * half_up
    c = c_of(t)
    c2 = nearest_c(c, prefer_c, period=180.0)
    return tool_matrix((*t[:3, 3], c2))


def _flips(b: np.ndarray) -> list[np.ndarray]:
    """La misma caja girada 180° sobre la vertical (es simétrica): dos marcos válidos."""
    out = [b]
    f = b.copy()
    rz = np.diag([-1.0, -1.0, 1.0])
    f[:3, :3] = rz @ b[:3, :3]
    out.append(f)
    return out


# --------------------------------------------------------------------------- recogidas por paso

def is_canto(step: dict) -> bool:
    return step.get("orientation") in ("HWL", "WHL")


def pick_plan(step: dict, conv: dict, prefer_c: float = 0.0, canto_strategy: str = "volteador") -> dict:
    """Marcos de caja y herramienta de la recogida de un paso (antes de ir al palet)."""
    lwh = tuple(step["orig_lwh"])
    queue = step.get("conveyor") or [dict(box_id=step.get("box_id"), lwh=list(lwh))]
    idx = step.get("chosen_index") or 0
    idx = min(idx, len(queue) - 1)
    offs = queue_offsets(queue, conv)
    b_conv = box_on_conveyor(lwh, conv, offs[idx])
    l, w, h = lwh
    long1 = b_conv[:3, 0] if l >= w else b_conv[:3, 1]
    t1 = tool_on_top(b_conv, h / 2, long1, prefer_c)
    rel1 = np.linalg.inv(t1) @ b_conv
    hq = max(b["lwh"][2] for b in queue)
    zc = conv["z_conveyor"]
    out = dict(queue_index=idx, queue_offset=offs[idx], queue_len=offs[-1] + queue[-1]["lwh"][1] / 2,
               b_conv=b_conv, t_pick=t1, rel_pick=rel1, canto=is_canto(step), xy=t1[:2, 3].tolist(),
               floor_z=zc + hq + CLEAR_MM + h, hq=hq)
    if out["canto"] and canto_strategy == "volteador":
        tip = conv["tipper"]
        b_tf = box_on_tipper(lwh, tip)
        t_place = b_tf @ np.linalg.inv(rel1)
        b_up = tipper_rotation(tip) @ b_tf
        long2 = b_up[:3, 2] if h >= w else b_up[:3, 1]     # H del marco de pie según −d, W según w
        t2 = tool_on_top(b_up, l / 2, long2, c_of(t_place))
        out.update(b_tipper=b_tf, t_tipper_place=t_place, b_up=b_up, t_pick2=t2,
                   rel=np.linalg.inv(t2) @ b_up, xy=t2[:2, 3].tolist(),
                   floor_z=zc + max(TIPPER["plate_len_mm"], hq) + CLEAR_MM + l)
    else:
        out.update(rel=rel1)
    return out


def final_tool(step: dict, rel: np.ndarray, prefer_c: float) -> tuple[np.ndarray, np.ndarray]:
    """Marco de la herramienta en la posición final (de las dos variantes simétricas, la de menos giro de C)."""
    best = None
    for b in _flips(box_final_matrix(step)):
        t = b @ np.linalg.inv(rel)
        cost = abs(nearest_c(c_of(t), prefer_c) - prefer_c)
        if best is None or cost < best[0]:
            best = (cost, t, b)
    return best[1], best[2]


# --------------------------------------------------------------------------- ciclo

def _q(t: np.ndarray, prefer_c: float) -> list[float]:
    return [float(t[0, 3]), float(t[1, 3]), float(t[2, 3]), nearest_c(c_of(t), prefer_c)]


def plan_cycles(model: GantryModel, run: dict, results: list, picks: dict, canto_strategy: str = "volteador") -> list[dict]:
    """Waypoints (q, tiempos, estado de la caja) de cada caja, encadenados desde la anterior."""
    cell = model.cell
    dyn = cell.dyn
    conv = cell.conveyor
    zc = conv["z_conveyor"]
    grip_half = tuple(v / 2 for v in cell.gripper.footprint)
    q = list(model.home_q)
    prev_by_pallet: dict[int, list] = {}
    out = []
    for step, res in zip(run["steps"], results):
        prev = prev_by_pallet.setdefault(step["pallet"], [])
        box = (step["x"], step["y"], step["z"], step["dx"], step["dy"], step["dz"])
        pk = picks[step["step"]]
        wps = []

        def go(kind, qn, box_state, fine=False, dwell=0.0, min_t=0.0, tip=None):
            nonlocal q
            qn = [float(v) for v in qn]
            qn[3] = nearest_c(qn[3], q[3])
            t_move = max(dyn.move_time(q, qn, fine=fine), min_t)
            wps.append(dict(kind=kind, q=[round(v, 1) for v in qn], t_move=round(t_move, 3), t_dwell=dwell,
                            box=box_state, attached=box_state in ("tool", "tool_pick"),
                            **({} if tip is None else dict(tip=tip))))
            q = qn

        # 1) Vuelta en vacío a la recogida, por encima de pilas y cola.
        t1 = pk["t_pick"]
        z_ret = max(q[2], corridor_top(q[:2], grip_half, t1[:2, 3], prev) + CLEAR_MM, zc + pk["hq"] + CLEAR_MM)
        if z_ret > q[2] + 1:
            go("subir", [q[0], q[1], z_ret, q[3]], "conveyor")
        go("sobre_recogida", [t1[0, 3], t1[1, 3], max(z_ret, t1[2, 3] + 100), c_of(t1)], "conveyor")
        canto_tip = pk["canto"] and canto_strategy == "volteador"
        go("recogida", _q(t1, q[3]), "tool_pick" if canto_tip else "tool", fine=True, dwell=dyn.grip_s)
        if canto_tip:
            tp, t2 = pk["t_tipper_place"], pk["t_pick2"]
            l = step["orig_lwh"][0]
            z1 = zc + pk["hq"] + CLEAR_MM + step["orig_lwh"][2]
            go("levantar_volteador", [q[0], q[1], max(z1, q[2]), q[3]], "tool_pick")
            go("sobre_volteador", [tp[0, 3], tp[1, 3], max(z1, tp[2, 3] + 100), c_of(tp)], "tool_pick")
            go("deja_volteador", _q(tp, q[3]), "tipper", fine=True, dwell=dyn.release_s, tip=0)
            # Mientras gira, la esquina de la caja llega a hypot(L, H) sobre el pivote.
            z_up = zc + float(np.hypot(l, step["orig_lwh"][2])) + CLEAR_MM
            go("sube_volteador", [q[0], q[1], max(q[2] + 100, z_up), q[3]], "tipper", tip=0)
            go("volteo", [t2[0, 3], t2[1, 3], max(q[2], t2[2, 3] + 100), c_of(t2)], "tipper",
               min_t=dyn.tip_s, tip=1)
            go("coge_volteador", _q(t2, q[3]), "tool", fine=True, dwell=dyn.grip_s, tip=1)
        # 2) Transporte por encima del pasillo hasta la entrada, bajada y deslizamiento.
        poses = {p.kind: p for p in res.poses}
        entry = poses.get("entrada")
        t_fin, b_fin = final_tool(step, pk["rel"], q[3])
        z_entry = entry.tcp[2] if entry else max(q[2], t_fin[2, 3])
        go("levantar", [q[0], q[1], max(q[2], z_entry), q[3]], "tool", tip=0 if canto_tip else None)
        appr = poses.get("aproximacion")
        pre = appr.tcp if appr else t_fin[:3, 3]
        go("entrada", [pre[0], pre[1], q[2], c_of(t_fin)], "tool")
        go("aproximacion", [pre[0], pre[1], t_fin[2, 3], q[3]], "tool", fine=True)
        go("final", _q(t_fin, q[3]), "placed", dwell=dyn.release_s)
        go("retirada", [q[0], q[1], q[2] + 150.0, q[3]], "placed")
        total = sum(w["t_move"] + w["t_dwell"] for w in wps)
        out.append(dict(waypoints=wps, cycle_s=round(total, 3), b_final=b_fin, t_final=t_fin))
        prev.append(box)
    return out


def cycle_json(pk: dict, cyc: dict, canto_strategy: str = "volteador") -> dict:
    """Lo que necesita el visor: waypoints y marcos de la caja (4×4, filas)."""
    r = lambda m: np.round(m, 6).tolist()
    out = dict(waypoints=cyc["waypoints"], cycle_s=cyc["cycle_s"], box_rel=r(np.linalg.inv(cyc["t_final"]) @ cyc["b_final"]),
               box_conveyor=r(pk["b_conv"]), box_rel_pick=r(pk["rel_pick"]), queue_index=pk["queue_index"],
               queue_offset_mm=round(pk["queue_offset"], 1), pick_ok=pk.get("pick_ok", True),
               pick_reason=pk.get("pick_reason"))
    if pk["canto"] and canto_strategy == "volteador":
        out["box_tipper_local"] = r(np.linalg.inv(tipper_base(pk["conv_tipper"])) @ pk["b_tipper"])
    return out


# --------------------------------------------------------------------------- célula a partir de los datos

def collect_picks(runs: list[dict], conv: dict, canto_strategy: str = "volteador") -> list[dict]:
    """Recogida de cada paso de cada run (el C preferido sigue al anterior)."""
    out = []
    for run in runs:
        picks, c = {}, 0.0
        for s in run["steps"]:
            pk = pick_plan(s, conv, c, canto_strategy)
            pk["conv_tipper"] = conv["tipper"]
            picks[s["step"]] = pk
            c = c_of(pk.get("t_pick2", pk["t_pick"]))
        out.append(picks)
    return out


def _span(values, margin=50.0, step=10.0):
    lo, hi = min(values) - margin, max(values) + margin
    return (float(np.floor(lo / step) * step), float(np.ceil(hi / step) * step))


def evaluate_cell(cell: GantryCell, runs: list[dict], picks_by_run: list[dict], canto_strategy="volteador",
                  keep_poses=True):
    """evaluate_run + ciclos para cada run. Devuelve [(results, cycles)]."""
    out = []
    for run, picks in zip(runs, picks_by_run):
        model = GantryModel(cell, picks=picks)
        res = evaluate_run(model, placements_from_run(run), keep_poses=True)
        cyc = plan_cycles(model, run, res, picks, canto_strategy)
        if not keep_poses:
            for r in res:
                r.poses = []
        out.append((res, cyc))
    return out


def size_cell(runs: list[dict], conv: dict, frame: GantryFrame | None = None, gripper: Gripper | None = None,
              dyn=None, canto_strategy: str = "volteador", queue_cover: float | None = None) -> tuple[GantryCell, list]:
    """Carreras y estructura mínimas que cubren todas las poses y recogidas de las trazas.

    queue_cover: carrera X máxima permitida hacia aguas arriba de la cola (None = toda la cola)."""
    frame = frame or GantryFrame()
    gripper = gripper or Gripper()
    big = (-1e5, 1e5)
    kw = dict(frame=frame, gripper=gripper, conveyor=conv, tipper=conv["tipper"])
    if dyn is not None:
        kw["dyn"] = dyn
    cell = GantryCell(x_range=big, y_range=big, z_range=(-1e4, 1e4), **kw)
    picks = collect_picks(runs, conv, canto_strategy)
    ev = evaluate_cell(cell, runs, picks, canto_strategy)
    qs = [w["q"] for _, cyc in ev for c in cyc for w in c["waypoints"]]
    qs += [p.q for res, _ in ev for r in res for p in r.poses if p.q is not None]
    qs = np.array([q[:3] for q in qs], float)
    t = np.array(conv["tangent"])
    xr = _span(qs[:, 0])
    if queue_cover is not None:  # se recorta solo por el lado de la cola (aguas arriba)
        xr = (max(xr[0], xr[1] - queue_cover), xr[1]) if t[0] > 0 else (xr[0], min(xr[1], xr[0] + queue_cover))
    yr, zr = _span(qs[:, 1]), _span(qs[:, 2])
    # Planta ocupada: envolvente, cinta y volteador dentro del pórtico, TCP ± caja más grande girando.
    r_move = max(np.hypot(*sorted(s["orig_lwh"])[1:]) / 2 for run in runs for s in run["steps"]) + CLEAR_MM
    yc, hw = conv["point"][1], conv["width_mm"] / 2
    occ_x = (min(xr[0] - r_move, ENVELOPE["x0"]), max(xr[1] + r_move, ENVELOPE["x1"],
                                                        conv["tipper"]["pivot"][0] + TIPPER["backstop_mm"] + 50))
    occ_y = (min(yr[0] - r_move, ENVELOPE["y0"], yc - hw), max(yr[1] + r_move, ENVELOPE["y1"], yc + hw))
    off = frame.clearance_mm + frame.post_mm / 2
    rail_x = (occ_x[0] - off, occ_x[1] + off)
    rails_y = (occ_y[0] - off, occ_y[1] + off)
    posts = _posts(rail_x, rails_y, frame)
    top_clear = ENVELOPE["z1"] + 200.0
    cell = replace(cell, x_range=xr, y_range=yr, z_range=zr, rail_x=rail_x, rails_y=rails_y, posts=posts,
                   ram_top_clear_z=top_clear)
    return cell, picks


def _posts(rail_x, rails_y, frame: GantryFrame) -> list[tuple]:
    """Patas en los extremos de los carriles y, si la luz supera el máximo, intermedias fuera del palet."""
    length = rail_x[1] - rail_x[0]
    n = max(1, int(ceil(length / frame.max_post_span_mm)))
    xs = [rail_x[0] + length * i / n for i in range(n + 1)]
    keep_out = (ENVELOPE["x0"] - frame.clearance_mm - frame.post_mm / 2, ENVELOPE["x1"] + frame.clearance_mm + frame.post_mm / 2)
    fixed = []
    for i, x in enumerate(xs):
        if 0 < i < n and keep_out[0] < x < keep_out[1]:  # no tapar el acceso al palet
            x = keep_out[0] if x - keep_out[0] < keep_out[1] - x else keep_out[1]
        fixed.append(x)
    return [(round(x, 1), round(y, 1)) for x in fixed for y in rails_y]


def structure_checks(cell: GantryCell) -> list[dict]:
    """La estructura no invade el palet, la cinta, el volteador ni las pilas de 2400 mm."""
    f, conv = cell.frame, cell.conveyor
    half = f.post_mm / 2
    yc, hw = conv["point"][1], conv["width_mm"] / 2
    rects = {"envolvente del palet": (ENVELOPE["x0"], ENVELOPE["y0"], ENVELOPE["x1"], ENVELOPE["y1"]),
             "cinta": (-1e5, yc - hw, 1e5, yc + hw)}
    tip = conv["tipper"]
    px, py = tip["pivot"][:2]
    rects["volteador"] = (min(px - TIPPER["plate_len_mm"], px - TIPPER["backstop_mm"]), py - TIPPER["plate_w_mm"] / 2,
                          max(px + TIPPER["plate_len_mm"], px + TIPPER["backstop_mm"]), py + TIPPER["plate_w_mm"] / 2)
    out = []
    for name, (x0, y0, x1, y1) in rects.items():
        gap = min(max(x0 - (x + half), (x - half) - x1, y0 - (y + half), (y - half) - y1) for x, y in cell.posts)
        out.append(dict(what=f"patas frente a {name}", min_gap_mm=round(gap, 1), ok=gap >= f.clearance_mm - 1))
    under = cell.bridge_underside_z
    out.append(dict(what="bajo del puente frente a pilas de 2400 mm", min_gap_mm=round(under - ENVELOPE["z1"], 1),
                    ok=under - ENVELOPE["z1"] >= 300))
    out.append(dict(what="bajo del puente frente a la cabeza C con el TCP arriba del todo",
                    min_gap_mm=round(under - (cell.z_range[1] + cell.gripper.flange_to_tcp_mm + f.head_h_mm), 1),
                    ok=under >= cell.z_range[1] + cell.gripper.flange_to_tcp_mm + f.head_h_mm))
    sides = sorted({x for x, _ in cell.posts})
    access = 0.0
    for a, b in zip(sides, sides[1:]):
        lo, hi = a + half, b - half
        if lo <= ENVELOPE["x0"] and hi >= ENVELOPE["x1"]:
            access = hi - lo
    out.append(dict(what="hueco libre entre patas para sacar el palet (lado sin cinta)", min_gap_mm=round(access, 1),
                    ok=access >= ENVELOPE["x1"] - ENVELOPE["x0"] + 2 * 200))
    return out


TIPPER_KINDS = ("levantar_volteador", "sobre_volteador", "deja_volteador", "sube_volteador", "volteo", "coge_volteador")


def b_axis_cycle_s(cycle: dict, extra_s: float = 1.0) -> float:
    """Estimación con eje B en lugar de volteador: sin los movimientos del volteador y con
    extra_s por la aproximación horizontal a la cara H×W y el giro de B (solapado con la subida)."""
    wps = cycle["waypoints"]
    if not any(w["kind"] == "volteo" for w in wps):
        return cycle["cycle_s"]
    saved = sum(w["t_move"] + w["t_dwell"] for w in wps if w["kind"] in TIPPER_KINDS)
    return cycle["cycle_s"] - saved + extra_s


def queue_coverage(runs: list[dict], picks_by_run: list[dict], cell: GantryCell, strokes_mm) -> list[dict]:
    """% de recogidas de la cola alcanzables según la carrera X (anclada en el extremo aguas abajo)."""
    t = np.array(cell.conveyor["tangent"])
    xs = np.array([pk["t_pick"][0, 3] for picks in picks_by_run for pk in picks.values()])
    idx = np.array([pk["queue_index"] for picks in picks_by_run for pk in picks.values()])
    out = []
    for stroke in strokes_mm:
        ok = xs >= cell.x_range[1] - stroke if t[0] > 0 else xs <= cell.x_range[0] + stroke
        per_index = {int(i): float(ok[idx == i].mean()) for i in sorted(set(idx.tolist()))}
        always = 0
        for i in sorted(per_index):
            if per_index[i] < 1.0:
                break
            always = i + 1
        out.append(dict(stroke_x_mm=float(stroke), picks_ok=float(ok.mean()), per_index=per_index,
                        positions_always=always))
    return out
