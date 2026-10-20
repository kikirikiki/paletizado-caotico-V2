"""Cinemática del FANUC R-2000iC/165F con garra de ventosas y su célula.

Parámetros medidos en el STEP del fabricante (ejes de los cilindros de cada eslabón,
pose cero con el brazo vertical y el antebrazo horizontal hacia +x):
  J1 vertical por el origen; J2 (eje y) en x=312, z=670; J3 (eje y) en z=1745;
  J4 (eje x) en z=1970; J5 (eje y) en x=1592; brida de J6 en x=1807.
  => a1=312, d1=670, a2=1075, a3=225, d4=1280, d6=215 (coinciden con el catálogo).

Convenio de ángulos (grados, como el teach pendant de FANUC):
  J1 giro sobre +z; J2 inclinación del brazo hacia delante desde la vertical;
  J3 inclinación del antebrazo sobre la horizontal (interacción J2/J3 de FANUC,
  positivo hacia arriba); J4 giro sobre el eje del antebrazo; J5 flexión de muñeca;
  J6 giro de la brida.

Límites de catálogo (a confirmar con el manual del modelo concreto): J1 ±185,
J2 −60/+76, J3 −132/+180 (sobre la horizontal), J4 ±360, J5 ±125, J6 ±360.

Coordenadas de la célula = las del visor: mm, origen en la esquina del palet
nominal, z=0 cara superior del palet, suelo en z=−144.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, cos, degrees, hypot, radians, sin, sqrt, acos

import numpy as np

FLOOR_Z_MM = -144.0  # palet europeo en el suelo


@dataclass(frozen=True)
class ArmGeometry:
    name: str = "R-2000iC/165F"
    a1: float = 312.0
    d1: float = 670.0
    a2: float = 1075.0
    a3: float = 225.0
    d4: float = 1280.0
    d6: float = 215.0
    limits: tuple = ((-185, 185), (-60, 76), (-132, 180), (-360, 360), (-125, 125), (-360, 360))
    payload_kg: float = 165.0


@dataclass(frozen=True)
class Gripper:
    """4 ventosas 2x2 (centros 210 x 163 mm, Ø90) centradas en el eje de J6."""
    flange_to_tcp_mm: float = 200.0
    pitch_x_mm: float = 210.0
    pitch_y_mm: float = 163.0
    cup_diameter_mm: float = 90.0
    cup_height_mm: float = 60.0
    frame_height_mm: float = 60.0
    column_mm: float = 120.0

    @property
    def footprint(self) -> tuple[float, float]:
        return self.pitch_x_mm + self.cup_diameter_mm, self.pitch_y_mm + self.cup_diameter_mm

    def boxes_tool(self) -> list[tuple[np.ndarray, np.ndarray]]:
        """Cajas (min, max) en el marco herramienta: origen TCP, +z hacia la brida."""
        fx, fy = self.footprint
        h1 = self.cup_height_mm + self.frame_height_mm
        c = self.column_mm / 2
        return [(np.array([-fx / 2, -fy / 2, 0.0]), np.array([fx / 2, fy / 2, h1])),
                (np.array([-c, -c, h1]), np.array([c, c, self.flange_to_tcp_mm]))]


@dataclass
class Cell:
    """Posición del robot respecto al palet. yaw: dirección de +x del robot con J1=0."""
    base_x: float
    base_y: float
    pedestal_mm: float = 0.0
    yaw_deg: float = 0.0
    arm: ArmGeometry = field(default_factory=ArmGeometry)
    gripper: Gripper = field(default_factory=Gripper)

    @property
    def base_z(self) -> float:
        return FLOOR_Z_MM + self.pedestal_mm

    def to_robot(self, p) -> np.ndarray:
        p = np.asarray(p, dtype=float) - np.array([self.base_x, self.base_y, self.base_z])
        c, s = cos(radians(self.yaw_deg)), sin(radians(self.yaw_deg))
        return np.array([c * p[0] + s * p[1], -s * p[0] + c * p[1], p[2]])

    def dir_to_robot(self, d) -> np.ndarray:
        c, s = cos(radians(self.yaw_deg)), sin(radians(self.yaw_deg))
        return np.array([c * d[0] + s * d[1], -s * d[0] + c * d[1], d[2]])

    def world_matrix(self) -> np.ndarray:
        c, s = cos(radians(self.yaw_deg)), sin(radians(self.yaw_deg))
        m = np.eye(4)
        m[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
        m[:3, 3] = [self.base_x, self.base_y, self.base_z]
        return m


def _rx(a):
    c, s = cos(a), sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _ry(a):
    c, s = cos(a), sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rz(a):
    c, s = cos(a), sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def _tf(r, t):
    m = np.eye(4)
    m[:3, :3] = r
    m[:3, 3] = t
    return m


def link_frames(arm: ArmGeometry, q_deg) -> list[np.ndarray]:
    """Marcos (base del robot) de: base, J1, J2, J3, J4, J5, J6/brida.

    Cada marco tiene su origen en el punto del eje de la articulación usado en el
    STEP y los ejes alineados con el mundo en la pose cero, así que una malla del
    STEP se coloca restando ese origen (ver JOINT_ORIGINS)."""
    q1, q2, q3, q4, q5, q6 = (radians(v) for v in q_deg)
    t_base = np.eye(4)
    t1 = _tf(_rz(q1), [0, 0, 0])
    t2 = t1 @ _tf(_ry(q2), [arm.a1, 0, arm.d1])
    # J3 FANUC es absoluto sobre la horizontal: giro relativo = -(J3 + J2) sobre y.
    t3 = t2 @ _tf(_ry(-(q3 + q2)), [0, 0, arm.a2])
    t4 = t3 @ _tf(_rx(q4), [0, 0, arm.a3])
    t5 = t4 @ _tf(_ry(q5), [arm.d4, 0, 0])
    t6 = t5 @ _tf(_rx(q6), [0, 0, 0])
    flange = t6 @ _tf(np.eye(3), [arm.d6, 0, 0])
    return [t_base, t1, t2, t3, t4, t5, t6, flange]


# Origen de cada marco en coordenadas del STEP (pose cero), para colocar mallas.
JOINT_ORIGINS = {
    "base": (0, 0, 0), "j1": (0, 0, 0), "j2": (312, 0, 670), "j3": (312, 0, 1745),
    "j4": (312, 0, 1970), "j5": (1592, 0, 1970), "j6": (1592, 0, 1970),
}


def tool_frame(arm: ArmGeometry, gripper: Gripper, q_deg) -> np.ndarray:
    """TCP: eje de aproximación = +x de la brida; z herramienta apunta a la brida."""
    flange = link_frames(arm, q_deg)[-1]
    # Marco herramienta: z_tool = -x_brida (hacia la brida), x_tool = y_brida.
    # Columnas: x_tool = y_brida, y_tool = -z_brida, z_tool = -x_brida.
    r = flange[:3, :3] @ np.array([[0, 0, -1], [1, 0, 0], [0, -1, 0]])
    t = flange[:3, 3] + flange[:3, 0] * gripper.flange_to_tcp_mm
    return _tf(r, t)


@dataclass
class IKResult:
    q: tuple | None
    reason: str | None
    margin_deg: float | None = None  # distancia mínima a un límite articular


def ik(arm: ArmGeometry, gripper: Gripper, tcp_robot, approach_robot, xaxis_robot,
       prefer=(0, 0, 0, 0, 0, 0)) -> IKResult:
    """IK analítica, rama delante / codo arriba; elige la muñeca más cercana a prefer.

    tcp_robot: TCP en el marco del robot. approach_robot: dirección en la que apunta
    la herramienta (de la brida a la pieza). xaxis_robot: eje x de la herramienta
    (orientación de la garra alrededor de la aproximación)."""
    a = np.asarray(approach_robot, float); a = a / np.linalg.norm(a)
    x = np.asarray(xaxis_robot, float); x = x - a * x.dot(a); x = x / np.linalg.norm(x)
    w = np.asarray(tcp_robot, float) - a * (gripper.flange_to_tcp_mm + arm.d6)
    q1 = atan2(w[1], w[0])
    r = hypot(w[0], w[1]) - arm.a1
    s = w[2] - arm.d1
    l3 = hypot(arm.d4, arm.a3)
    phi = atan2(arm.a3, arm.d4)
    rho = hypot(r, s)
    if rho > arm.a2 + l3 - 1e-6:
        return IKResult(None, "fuera_de_alcance")
    if rho < abs(arm.a2 - l3) + 1e-6:
        return IKResult(None, "demasiado_cerca")
    gamma = atan2(s, r)
    alpha = acos(max(-1.0, min(1.0, (arm.a2 ** 2 + rho ** 2 - l3 ** 2) / (2 * arm.a2 * rho))))
    elev = gamma + alpha  # elevación del brazo J2->J3 (codo arriba)
    q2 = radians(90) - elev
    j3r, j3s = arm.a2 * cos(elev), arm.a2 * sin(elev)
    beta = atan2(s - j3s, r - j3r)
    q3 = beta - phi
    r03 = _rz(q1) @ _ry(-q3)  # J2 y J3 se compensan: antebrazo con cabeceo absoluto q3
    a3v = r03.T @ a  # dirección del eje J6 en el marco del antebrazo
    best = None
    for sign in (1, -1):
        c5 = max(-1.0, min(1.0, a3v[0]))
        q5 = sign * acos(c5)
        s5 = sin(q5)
        if abs(s5) < 1e-6:
            q4 = radians(prefer[3])
        else:
            q4 = atan2(a3v[1] / s5, -a3v[2] / s5)
        r36 = _rx(q4) @ _ry(q5)
        # x herramienta = y brida  ->  q6 alinea y_brida con x.
        y6 = (r03 @ r36).T @ x
        q6 = atan2(y6[2], y6[1])
        cand = [degrees(v) for v in (q1, q2, q3, q4, q5, q6)]
        # J4 y J6 giran ±360: representante dentro de límites más cercano a prefer
        # (evita que se "enrollen" de una caja a la siguiente).
        for j in (3, 5):
            lo, hi = arm.limits[j]
            reps = [cand[j] + 360 * k for k in range(-3, 4)]
            inside = [v for v in reps if lo <= v <= hi]
            cand[j] = min(inside or reps, key=lambda v: abs(v - prefer[j]))
        cost = sum(abs(c - p) for c, p in zip(cand[3:], prefer[3:]))
        if best is None or cost < best[0]:
            best = (cost, cand)
    q = tuple(best[1])
    margins = [min(v - lo, hi - v) for v, (lo, hi) in zip(q, arm.limits)]
    if min(margins) < 0:
        names = [f"J{i + 1}" for i, m in enumerate(margins) if m < 0]
        return IKResult(q, "limite_" + "_".join(names), min(margins))
    return IKResult(q, None, min(margins))


def ik_world(cell: Cell, tcp, approach, xaxis, prefer=(0, 0, 0, 0, 0, 0)) -> IKResult:
    return ik(cell.arm, cell.gripper, cell.to_robot(tcp), cell.dir_to_robot(approach),
              cell.dir_to_robot(xaxis), prefer)
