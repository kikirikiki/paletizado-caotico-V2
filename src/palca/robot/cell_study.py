"""Alcance y colisiones del R-2000iC/165F sobre secuencias exportadas (sim3d).

Para cada caja, en el orden real y con las cajas ya colocadas de su palet:
  entrada  TCP sobre la posición desplazada, a la altura que libra el pasillo
           entre el robot y la caja (cajas que barre la garra con la caja colgando);
  bajada   vertical hasta la z final, desplazada 20 mm hacia el lateral libre con
           más holgura (o cenital si no hay ninguno);
  final    deslizamiento horizontal a la posición exportada.
En cada pose se exige IK dentro de límites y que ni el robot (nube de puntos de los
eslabones del STEP), ni la garra, ni la caja transportada invadan una caja colocada
o el palet. La herramienta va siempre vertical al colocar: las cajas de canto se
cogen por la cara H×W, que queda arriba (HWL/WHL).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import json
from math import atan2, degrees
from pathlib import Path

import numpy as np

from .r2000 import ArmGeometry, Cell, Gripper, IKResult, ik_world, link_frames, tool_frame

PALLET_WOOD = (0.0, 0.0, -144.0, 1200.0, 800.0, 0.0)
ENVELOPE = dict(x0=-20, y0=-20, x1=1220, y1=820, z0=0, z1=2400)
# Caja envolvente de cada pieza del STEP en pose cero (mm). Respaldo sin mallas.
STEP_AABB = {
    "base": ((-423, -307, 0), (348, 305, 336)), "j1": ((-355, -328, 215), (519, 419, 849)),
    "j2": ((41, -6, 483), (509, 418, 1909)), "j3": ((142, -214, 1563), (1361, 417, 2113)),
    "j4": ((1361, -93, 1877), (1685, 93, 2063)), "j5": ((1479, -150, 1857), (1796, 115, 2083)),
    "j6": ((1770, -80, 1892), (1807, 80, 2050)), "balancer_case": ((-510, 27, 465), (29, 351, 799)),
    "balancer_rod": ((-450, 160, 602), (147, 218, 701)),
}
JOINT_ORIGINS = {
    "base": (0, 0, 0), "j1": (0, 0, 0), "j2": (312, 0, 670), "j3": (312, 0, 1745),
    "j4": (312, 0, 1970), "j5": (1592, 0, 1970), "j6": (1592, 0, 1970),
    "balancer_case": (-470, 189, 621), "balancer_rod": (98, 189, 652),
}
# Marco de link_frames al que va unida cada pieza (el compensador se aproxima rígido).
FRAME_OF = {"base": 0, "j1": 1, "j2": 2, "j3": 3, "j4": 4, "j5": 5, "j6": 6,
            "balancer_case": 1, "balancer_rod": 2}
FRAME_ORIGIN = {0: (0, 0, 0), 1: (0, 0, 0), 2: (312, 0, 670), 3: (312, 0, 1745), 4: (312, 0, 1970),
                5: (1592, 0, 1970), 6: (1592, 0, 1970)}
TOOL_DOWN = np.array([0.0, 0.0, -1.0])


# --------------------------------------------------------------------------- geometría

def _surface_samples(v: np.ndarray, f: np.ndarray, spacing: float, rng) -> np.ndarray:
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    n = np.maximum(1, np.round(area / (spacing * spacing) * 2).astype(int))
    idx = np.repeat(np.arange(len(f)), n)
    r1, r2 = rng.random(len(idx)), rng.random(len(idx))
    s = np.sqrt(r1)
    pts = (1 - s)[:, None] * a[idx] + (s * (1 - r2))[:, None] * b[idx] + (s * r2)[:, None] * c[idx]
    pts = np.vstack([pts, v])
    key = np.floor(pts / spacing).astype(np.int64)
    _, keep = np.unique(key, axis=0, return_index=True)
    return pts[np.sort(keep)]


def _box_surface(lo, hi, spacing) -> np.ndarray:
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    axes = [np.linspace(lo[i], hi[i], max(2, int(np.ceil((hi[i] - lo[i]) / spacing)) + 1)) for i in range(3)]
    g = np.stack(np.meshgrid(*axes, indexing="ij"), -1).reshape(-1, 3)
    on = np.zeros(len(g), bool)
    for i in range(3):
        on |= np.isclose(g[:, i], lo[i]) | np.isclose(g[:, i], hi[i])
    return g[on]


def collision_points(mesh_json: Path | None = None, spacing: float = 40.0) -> dict[str, np.ndarray]:
    """Puntos de cada pieza en el marco de su articulación (link_frames)."""
    out = {}
    rng = np.random.default_rng(0)
    if mesh_json is not None and Path(mesh_json).exists():
        data = json.loads(Path(mesh_json).read_text())
        for name, link in data["links"].items():
            v = np.frombuffer(base64.b64decode(link["positions"]), "<f4").reshape(-1, 3).astype(float)
            f = np.frombuffer(base64.b64decode(link["indices"]), "<u4").reshape(-1, 3).astype(np.int64)
            pts = _surface_samples(v, f, spacing, rng) + np.array(JOINT_ORIGINS[name], float)
            out[name] = pts - np.array(FRAME_ORIGIN[FRAME_OF[name]], float)
    else:
        for name, (lo, hi) in STEP_AABB.items():
            out[name] = _box_surface(lo, hi, spacing) - np.array(FRAME_ORIGIN[FRAME_OF[name]], float)
    return out


def gripper_points(gripper: Gripper, spacing: float = 30.0) -> np.ndarray:
    return np.vstack([_box_surface(lo, hi, spacing) for lo, hi in gripper.boxes_tool()])


@dataclass
class RobotModel:
    cell: Cell
    points: dict[str, np.ndarray]
    grip_pts: np.ndarray = None

    def __post_init__(self):
        if self.grip_pts is None:
            self.grip_pts = gripper_points(self.cell.gripper)
        self._names = list(self.points)
        self._world = self.cell.world_matrix()

    def world_points(self, q) -> tuple[np.ndarray, np.ndarray]:
        frames = link_frames(self.cell.arm, q)
        chunks, labels = [], []
        for i, name in enumerate(self._names):
            m = self._world @ frames[FRAME_OF[name]]
            p = self.points[name]
            chunks.append(p @ m[:3, :3].T + m[:3, 3])
            labels.append(np.full(len(p), i))
        tool = self._world @ tool_frame(self.cell.arm, self.cell.gripper, q)
        chunks.append(self.grip_pts @ tool[:3, :3].T + tool[:3, 3])
        labels.append(np.full(len(self.grip_pts), len(self._names)))
        return np.vstack(chunks), np.concatenate(labels)

    def label(self, i: int) -> str:
        return "garra" if i == len(self._names) else self._names[i]


def first_hit(points, labels, boxes: np.ndarray, tol: float = 2.0):
    """Primer punto dentro de una caja (x0,y0,z0,x1,y1,z1), con tolerancia."""
    if not len(boxes):
        return None
    lo, hi = boxes[:, :3] + tol, boxes[:, 3:] - tol
    region = (points >= lo.min(0)).all(1) & (points <= hi.max(0)).all(1)
    if not region.any():
        return None
    p, lab = points[region], labels[region]
    inside = ((p[:, None, :] > lo[None]) & (p[:, None, :] < hi[None])).all(-1)
    hit = np.argwhere(inside)
    if not len(hit):
        return None
    return int(lab[hit[0, 0]]), int(hit[0, 1])


# --------------------------------------------------------------------------- poses

def side_clearances(b, prev):
    x, y, z, dx, dy, dz = b
    c = [np.inf] * 4
    for px, py, pz, pdx, pdy, pdz in prev:
        if pz >= z + dz or pz + pdz <= z:
            continue
        if py < y + dy and py + pdy > y:
            if px + pdx <= x:
                c[0] = min(c[0], x - (px + pdx))
            if px >= x + dx:
                c[1] = min(c[1], px - (x + dx))
        if px < x + dx and px + pdx > x:
            if py + pdy <= y:
                c[2] = min(c[2], y - (py + pdy))
            if py >= y + dy:
                c[3] = min(c[3], py - (y + dy))
    return c


DIRS = [(-1, 0), (1, 0), (0, -1), (0, 1)]


def approach_dir(b, prev, gap=20.0):
    """Mismo criterio que checks.js approachSide: lado libre con más holgura."""
    c = side_clearances(b, prev)
    x, y, _, dx, dy, _ = b
    edge = [x - ENVELOPE["x0"], ENVELOPE["x1"] - (x + dx), y - ENVELOPE["y0"], ENVELOPE["y1"] - (y + dy)]
    best = -1
    for i in range(4):
        if not c[i] >= gap or c[i] == 0:
            continue
        if best < 0 or c[i] > c[best] or (c[i] == c[best] and edge[i] < edge[best]):
            best = i
    return None if best < 0 else DIRS[best]


def corridor_top(center_xy, half, base_xy, prev, step=40.0) -> float:
    """Altura máxima de las cajas que barre la huella (half) yendo de la caja al robot."""
    c = np.asarray(center_xy, float)
    d = np.asarray(base_xy, float) - c
    length = np.linalg.norm(d)
    u = d / length if length else d
    top = 0.0
    margin = max(half) + 50
    for t in np.arange(0.0, length + step, step):
        p = c + u * t
        if (p[0] < ENVELOPE["x0"] - margin or p[0] > ENVELOPE["x1"] + margin
                or p[1] < ENVELOPE["y0"] - margin or p[1] > ENVELOPE["y1"] + margin):
            break
        for px, py, pz, pdx, pdy, pdz in prev:
            if (min(p[0] + half[0], px + pdx) - max(p[0] - half[0], px) > 1
                    and min(p[1] + half[1], py + pdy) - max(p[1] - half[1], py) > 1):
                top = max(top, pz + pdz)
    return top


def gripper_fits(face: tuple[float, float], gripper: Gripper) -> bool:
    fx, fy = gripper.footprint
    a, b = max(face), min(face)
    return a >= max(fx, fy) and b >= min(fx, fy)


@dataclass
class Placement:
    step: int
    pallet: int
    box: tuple
    orientation: str
    orig_lwh: tuple


@dataclass
class PoseCheck:
    kind: str
    tcp: tuple
    q: tuple | None
    reason: str | None = None
    margin: float | None = None
    other_step: int | None = None


@dataclass
class PlacementResult:
    step: int
    pallet: int
    ok: bool
    reason: str | None
    approach: tuple | None
    entry_lift_mm: float
    min_margin_deg: float | None
    poses: list[PoseCheck] = field(default_factory=list)
    gripper_fits: bool = True


def placements_from_run(run: dict) -> list[Placement]:
    return [Placement(s["step"], s["pallet"], (s["x"], s["y"], s["z"], s["dx"], s["dy"], s["dz"]),
                      s.get("orientation") or "LWH", tuple(s.get("orig_lwh") or (s["dx"], s["dy"], s["dz"])))
            for s in run["steps"]]


def _xaxis_for(box) -> list[np.ndarray]:
    # Paso largo de ventosas (210) a lo largo del lado largo de la cara superior.
    return [np.array([1.0, 0, 0]), np.array([-1.0, 0, 0])] if box[3] >= box[4] else \
        [np.array([0, 1.0, 0]), np.array([0, -1.0, 0])]


def solve(cell: Cell, tcp, box, prefer) -> IKResult:
    best = None
    for xa in _xaxis_for(box):
        r = ik_world(cell, tcp, TOOL_DOWN, xa, prefer)
        if r.q is None:
            return r
        key = (r.reason is not None, abs(r.q[5] - prefer[5]))
        if best is None or key < best[0]:
            best = (key, r)
    return best[1]


def evaluate_run(model: RobotModel, placements: list[Placement], *, lateral_mm=20.0, clearance_mm=50.0,
                 descent_step_mm=150.0, keep_poses=False) -> list[PlacementResult]:
    cell = model.cell
    base_xy = (cell.base_x, cell.base_y)
    results = []
    prev_by_pallet: dict[int, list] = {}
    q_prev = (0, 0, 0, 0, -90, 0)
    for pl in placements:
        prev = prev_by_pallet.setdefault(pl.pallet, [])
        x, y, z, dx, dy, dz = pl.box
        final = np.array([x + dx / 2, y + dy / 2, z + dz])
        d = approach_dir(pl.box, prev)
        off = np.array([d[0], d[1], 0.0]) * lateral_mm if d else np.zeros(3)
        pre = final + off
        gx, gy = cell.gripper.footprint
        long_x = dx >= dy
        half = (max(dx, gx if long_x else gy) / 2, max(dy, gy if long_x else gx) / 2)
        lift = max(0.0, corridor_top(pre[:2], half, base_xy, prev) + clearance_mm + dz - final[2])
        zs = [final[2] + lift]
        while zs[-1] - descent_step_mm > final[2]:
            zs.append(zs[-1] - descent_step_mm)
        poses = [("entrada" if i == 0 else "bajada", np.array([pre[0], pre[1], zz])) for i, zz in enumerate(zs)]
        poses += [("aproximacion", pre), ("final", final)]
        boxes = np.array([(px, py, pz, px + pdx, py + pdy, pz + pdz) for px, py, pz, pdx, pdy, pdz in prev]
                         + [PALLET_WOOD], float)
        checks, reason, other, margins = [], None, None, []
        q = q_prev
        for kind, tcp in poses:
            r = solve(cell, tcp, pl.box, q)
            pc = PoseCheck(kind, tuple(np.round(tcp, 1)), None if r.q is None else tuple(round(v, 3) for v in r.q))
            if r.q is None or r.reason:
                pc.reason = "alcance:" + (r.reason or "?")
            else:
                q = r.q
                margins.append(r.margin_deg)
                pts, labels = model.world_points(r.q)
                hit = first_hit(pts, labels, boxes)
                if hit is not None:
                    lab, bi = hit
                    pc.reason = f"colision:{model.label(lab)}"
                    pc.other_step = None if bi >= len(prev) else _step_of(prev, bi, placements, pl)
                elif kind != "final":
                    cb = np.array([tcp[0] - dx / 2, tcp[1] - dy / 2, tcp[2] - dz, tcp[0] + dx / 2, tcp[1] + dy / 2, tcp[2]])
                    for bi, ob in enumerate(boxes):
                        if (np.minimum(cb[3:], ob[3:]) - np.maximum(cb[:3], ob[:3]) > 1.0).all():
                            pc.reason = "colision:caja_transportada"
                            pc.other_step = None if bi >= len(prev) else _step_of(prev, bi, placements, pl)
                            break
            checks.append(pc)
            if pc.reason and reason is None:
                reason, other = f"{kind}:{pc.reason}", pc.other_step
        q_prev = q
        face = (pl.orig_lwh[0], pl.orig_lwh[1]) if pl.orientation in ("LWH", "WLH") else (pl.orig_lwh[2], pl.orig_lwh[1])
        res = PlacementResult(pl.step, pl.pallet, reason is None, reason, d, round(lift, 1),
                              min(margins) if margins else None, checks if keep_poses else [],
                              gripper_fits(face, cell.gripper))
        if other is not None:
            res.reason += f"(paso {other})"
        results.append(res)
        prev.append(pl.box)
    return results


def _step_of(prev, index, placements, current) -> int:
    same = [p for p in placements if p.pallet == current.pallet and p.step < current.step]
    return same[index].step if index < len(same) else None


# --------------------------------------------------------------------------- búsqueda rápida

def tool_down_reach(arm: ArmGeometry, gripper: Gripper, cell: Cell, tcps: np.ndarray):
    """IK vectorizada para herramienta vertical: (ok, margen articular mínimo en grados)."""
    w = tcps + np.array([0, 0, gripper.flange_to_tcp_mm + arm.d6])
    p = w - np.array([cell.base_x, cell.base_y, cell.base_z])
    c, s = np.cos(np.radians(cell.yaw_deg)), np.sin(np.radians(cell.yaw_deg))
    xr, yr = c * p[:, 0] + s * p[:, 1], -s * p[:, 0] + c * p[:, 1]
    q1 = np.degrees(np.arctan2(yr, xr))
    r = np.hypot(xr, yr) - arm.a1
    sz = p[:, 2] - arm.d1
    l3 = np.hypot(arm.d4, arm.a3)
    phi = np.arctan2(arm.a3, arm.d4)
    rho = np.hypot(r, sz)
    ok = (rho < arm.a2 + l3 - 1e-6) & (rho > abs(arm.a2 - l3) + 1e-6)
    cosa = np.clip((arm.a2 ** 2 + rho ** 2 - l3 ** 2) / (2 * arm.a2 * np.maximum(rho, 1e-9)), -1, 1)
    elev = np.arctan2(sz, r) + np.arccos(cosa)
    q2 = 90 - np.degrees(elev)
    q3 = np.degrees(np.arctan2(sz - arm.a2 * np.sin(elev), r - arm.a2 * np.cos(elev)) - phi)
    q5 = 90 + q3  # herramienta vertical
    lim = arm.limits
    m = np.minimum.reduce([q1 - lim[0][0], lim[0][1] - q1, q2 - lim[1][0], lim[1][1] - q2,
                           q3 - lim[2][0], lim[2][1] - q3, lim[4][1] - q5, q5 - lim[4][0]])
    ok &= m >= 0
    return ok, np.where(ok, m, -np.inf)


def yaw_towards(base_xy, target_xy=(600.0, 400.0)) -> float:
    return degrees(atan2(target_xy[1] - base_xy[1], target_xy[0] - base_xy[0]))


# --------------------------------------------------------------------------- ciclo para el visor

# Ejes originales (L, W, H) de la caja en el mundo según la orientación colocada.
# Rotaciones propias; la cara cogida es la que queda arriba (+z): +H plana, +L de canto.
_FINAL_AXES = {
    "LWH": ((1, 0, 0), (0, 1, 0), (0, 0, 1)), "WLH": ((0, 1, 0), (-1, 0, 0), (0, 0, 1)),
    "HWL": ((0, 0, 1), (0, 1, 0), (-1, 0, 0)), "WHL": ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
}


def box_final_matrix(step: dict) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = np.array(_FINAL_AXES[step.get("orientation") or "LWH"], float).T
    m[:3, 3] = [step["x"] + step["dx"] / 2, step["y"] + step["dy"] / 2, step["z"] + step["dz"] / 2]
    return m


def box_conveyor_matrix(step: dict, point, u) -> np.ndarray:
    """Caja tumbada en la cinta (H vertical, L cruzada). De canto: cara +L hacia el robot."""
    u = np.asarray(u, float)
    tang = np.array([-u[1], u[0], 0.0])
    z = np.array([0, 0, 1.0])
    canto = step.get("orientation") in ("HWL", "WHL")
    l_axis, w_axis = (-u, -tang) if canto else (u, tang)
    m = np.eye(4)
    m[:3, :3] = np.column_stack([l_axis, w_axis, z])
    m[:3, 3] = np.asarray(point, float) + [0, 0, step["orig_lwh"][2] / 2]
    return m


def _ik_matrix(cell: Cell, t: np.ndarray, prefer) -> IKResult:
    return ik_world(cell, t[:3, 3], -t[:3, 2], t[:3, 0], prefer)


def cycle(model: RobotModel, step: dict, result: PlacementResult, conveyor: dict | None, prev_q) -> dict:
    """Waypoints articulares de recogida + colocación + retirada para animar en el visor."""
    cell = model.cell
    poses = {p.kind: p for p in result.poses}
    final = poses.get("final")
    out = dict(waypoints=[], box_rel=None, box_conveyor=None, pick_ok=None)
    if final is None or final.q is None:
        return out
    world = cell.world_matrix()
    t_tool_final = world @ tool_frame(cell.arm, cell.gripper, final.q)
    rel = np.linalg.inv(t_tool_final) @ box_final_matrix(step)
    out["box_rel"] = np.round(rel, 6).tolist()
    wps = []
    q = tuple(prev_q)
    if conveyor:
        t_box = box_conveyor_matrix(step, conveyor["point"], conveyor["direction"])
        out["box_conveyor"] = np.round(t_box, 6).tolist()
        t_pick = t_box @ np.linalg.inv(rel)
        a = -t_pick[:3, 2]
        canto = step.get("orientation") in ("HWL", "WHL")
        back = (-a * 200.0) if canto else np.array([0, 0, 300.0])
        seq = [("sobre_recogida", back, False), ("recogida", np.zeros(3), True),
               ("levantar", (np.array([0, 0, 250.0]) + (-a * 60.0 if canto else 0)), True)]
        pick_ok = True
        for kind, off, attached in seq:
            t = t_pick.copy(); t[:3, 3] = t[:3, 3] + off
            r = _ik_matrix(cell, t, q)
            if r.q is None or r.reason:
                pick_ok = False
                continue
            q = r.q
            wps.append(dict(kind=kind, q=[round(v, 3) for v in q], attached=attached))
        if canto:  # reorientar sobre la cinta antes de ir al palet
            t = t_tool_final.copy()
            t[:3, 3] = [conveyor["point"][0], conveyor["point"][1], conveyor["z_conveyor"] + step["dz"] + 300.0]
            r = _ik_matrix(cell, t, q)
            if r.q is not None and not r.reason:
                q = r.q
                wps.append(dict(kind="reorientar", q=[round(v, 3) for v in q], attached=True))
            else:
                pick_ok = False
        out["pick_ok"] = pick_ok
    for p in result.poses:
        if p.q is not None:
            wps.append(dict(kind=p.kind, q=list(p.q), attached=True))
    # Suelta la caja y sube en vertical 150 mm.
    t = t_tool_final.copy()
    t[:3, 3] = t[:3, 3] + [0, 0, 150.0]
    r = _ik_matrix(cell, t, final.q)
    if r.q is not None and not r.reason:
        wps.append(dict(kind="retirada", q=[round(v, 3) for v in r.q], attached=False))
    out["waypoints"] = wps
    return out
