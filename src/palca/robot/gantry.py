"""Pórtico cartesiano (gantry) XYZC con la garra de 4 ventosas y su estructura.

Ejes: X (puente sobre dos carriles paralelos al lado de 1200 del palet), Y (carro sobre
el puente), Z (columna vertical que baja desde el carro) y C (giro de la garra sobre la
vertical). Sin eje B: la herramienta siempre está vertical.

Coordenadas = las del visor y de r2000.py: mm, origen en la esquina del palet nominal,
z=0 cara superior del palet, suelo en z=-144. Las «articulaciones» del gantry son la
posición del TCP en el mundo y el ángulo C: q = (x, y, z, c_grados). El marco
herramienta es el mismo que el del brazo: origen en el TCP (cara de las ventosas),
+z hacia la brida (arriba), +x a lo largo del paso largo (210 mm) de las ventosas.

Modelo de colisión (nube de puntos, como cell_study): garra (Gripper.boxes_tool), cabezal
del eje C (cilindro) y columna Z (prisma cuadrado que sube desde el cabezal). La columna
se muestrea solo hasta encima de la envolvente: más arriba no hay nada con lo que chocar.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import atan2, ceil, cos, degrees, radians, sin, sqrt

import numpy as np

from .cell_study import _box_surface, gripper_points
from .r2000 import FLOOR_Z_MM, Gripper, IKResult

AXES = ("X", "Y", "Z", "C")


# --------------------------------------------------------------------------- dinámica

def trapezoid_time(distance: float, v_max: float, a_max: float) -> float:
    """Tiempo de un movimiento punto a punto con perfil trapezoidal (o triangular)."""
    d = abs(float(distance))
    if d <= 1e-9:
        return 0.0
    if d <= v_max * v_max / a_max:
        return 2.0 * sqrt(d / a_max)
    return d / v_max + v_max / a_max


@dataclass(frozen=True)
class GantryDynamics:
    """Velocidad (mm/s o °/s) y aceleración (mm/s² o °/s²) por eje; tiempos fijos en s."""
    x: tuple = (3000.0, 3000.0)
    y: tuple = (3000.0, 3000.0)
    z: tuple = (2000.0, 3000.0)
    c: tuple = (180.0, 360.0)
    grip_s: float = 0.25          # hacer vacío
    release_s: float = 0.15       # soltar
    fine_mm: float = 50.0         # últimos mm de cada bajada a velocidad lenta
    fine_speed_mm_s: float = 300.0
    tip_s: float = 1.8            # volteador: giro de 90° + asentamiento

    def move_time(self, q0, q1, fine: bool = False) -> float:
        """Ejes sincronizados: manda el más lento. fine: bajada con aproximación lenta."""
        dz = q1[2] - q0[2]
        t_fine = 0.0
        if fine and dz < 0:
            f = min(self.fine_mm, -dz)
            t_fine = f / self.fine_speed_mm_s
            dz += f
        dc = (q1[3] - q0[3]) if len(q0) > 3 else 0.0
        t = max(trapezoid_time(q1[0] - q0[0], *self.x), trapezoid_time(q1[1] - q0[1], *self.y),
                trapezoid_time(dz, *self.z), trapezoid_time(dc, *self.c))
        return t + t_fine


# --------------------------------------------------------------------------- estructura

@dataclass(frozen=True)
class GantryFrame:
    """Secciones (mm). Las patas van a clearance_mm de cualquier parte móvil, la cinta o el palet."""
    post_mm: float = 300.0
    rail_w_mm: float = 300.0
    rail_h_mm: float = 500.0
    bridge_w_mm: float = 400.0
    bridge_h_mm: float = 600.0
    carriage_mm: float = 500.0          # carro Z sobre el puente (planta)
    ram_mm: float = 250.0               # columna Z cuadrada
    ram_guide_mm: float = 800.0         # longitud de guiado de la columna en el carro
    head_d_mm: float = 250.0            # cabezal del eje C (cilindro); con 300 choca en caras de 260 mm
    head_h_mm: float = 250.0
    clearance_mm: float = 300.0
    max_post_span_mm: float = 6000.0    # luz máxima del carril entre patas
    telescopic: bool = False


@dataclass
class GantryCell:
    """Pórtico concreto: carreras (TCP en el mundo), estructura, cinta y volteador."""
    x_range: tuple
    y_range: tuple
    z_range: tuple
    c_range: tuple = (-180.0, 180.0)
    frame: GantryFrame = field(default_factory=GantryFrame)
    gripper: Gripper = field(default_factory=Gripper)
    dyn: GantryDynamics = field(default_factory=GantryDynamics)
    rails_y: tuple = (0.0, 0.0)          # eje de cada carril
    rail_x: tuple = (0.0, 0.0)           # extremos de los carriles
    posts: list = field(default_factory=list)
    conveyor: dict | None = None
    tipper: dict | None = None
    ram_top_clear_z: float = 2600.0      # la columna se muestrea hasta aquí (encima de las pilas)

    @property
    def limits(self) -> list[tuple]:
        return [tuple(self.x_range), tuple(self.y_range), tuple(self.z_range), tuple(self.c_range)]

    @property
    def strokes(self) -> dict:
        return {a: float(hi - lo) for a, (lo, hi) in zip(AXES[:3], self.limits[:3])}

    @property
    def base_x(self) -> float:  # centro de la planta (para encuadrar el visor)
        return (self.rail_x[0] + self.rail_x[1]) / 2

    @property
    def base_y(self) -> float:
        return (self.rails_y[0] + self.rails_y[1]) / 2

    # Alturas en coordenadas de la célula (z=0 cara del palet).
    @property
    def bridge_underside_z(self) -> float:
        f = self.frame
        return self.z_range[1] + self.gripper.flange_to_tcp_mm + f.head_h_mm + 50.0

    @property
    def ram_length_mm(self) -> float:
        f = self.frame
        stroke = self.z_range[1] - self.z_range[0]
        return (stroke / 2 if f.telescopic else stroke) + f.ram_guide_mm

    def heights(self) -> dict:
        """Alturas desde el suelo (mm) de la estructura y de la columna arriba del todo."""
        f = self.frame
        under = self.bridge_underside_z
        ram_bottom = self.z_range[1] + self.gripper.flange_to_tcp_mm + f.head_h_mm
        fixed = ram_bottom + (self.z_range[1] - self.z_range[0]) + f.ram_guide_mm
        tele = ram_bottom + (self.z_range[1] - self.z_range[0]) / 2 + f.ram_guide_mm
        top = under + f.bridge_h_mm
        h = lambda z: round(z - FLOOR_Z_MM)
        return dict(rail_top=h(under), post=h(under - f.rail_h_mm), bridge_underside=h(under), bridge_top=h(top),
                    ram_top_fixed=h(fixed), ram_top_telescopic=h(tele),
                    building_fixed=h(max(top, fixed) + 300.0), building_telescopic=h(max(top, tele) + 300.0))

    def footprint(self) -> dict:
        p = self.frame.post_mm / 2
        x0, x1 = self.rail_x[0] - p, self.rail_x[1] + p
        y0, y1 = self.rails_y[0] - p, self.rails_y[1] + p
        return dict(x0=x0, x1=x1, y0=y0, y1=y1, length=x1 - x0, width=y1 - y0, area_m2=(x1 - x0) * (y1 - y0) / 1e6)

    def to_json(self) -> dict:
        return dict(x_range=list(self.x_range), y_range=list(self.y_range), z_range=list(self.z_range),
                    c_range=list(self.c_range), strokes=self.strokes, frame=asdict(self.frame), dyn=asdict(self.dyn),
                    rails_y=list(self.rails_y), rail_x=list(self.rail_x), posts=[list(p) for p in self.posts],
                    bridge_underside_z=self.bridge_underside_z, ram_length_mm=self.ram_length_mm,
                    heights=self.heights(), footprint=self.footprint(), floor_z=FLOOR_Z_MM)


# --------------------------------------------------------------------------- cinemática

def tool_matrix(q) -> np.ndarray:
    """Marco herramienta en el mundo para q = (x, y, z, c)."""
    c, s = cos(radians(q[3])), sin(radians(q[3]))
    m = np.eye(4)
    m[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    m[:3, 3] = q[:3]
    return m


def c_of(matrix: np.ndarray) -> float:
    """Ángulo C (grados) de un marco herramienta vertical."""
    return degrees(atan2(matrix[1, 0], matrix[0, 0]))


def nearest_c(c: float, prefer: float, lo: float = -180.0, hi: float = 180.0, period: float = 360.0) -> float:
    """Representante de c (módulo period) dentro de [lo, hi] más cercano a prefer."""
    cands = [c + period * k for k in range(-3, 4)]
    inside = [v for v in cands if lo - 1e-6 <= v <= hi + 1e-6]
    return min(inside or cands, key=lambda v: abs(v - prefer))


def check_limits(cell: GantryCell, q) -> IKResult:
    margins = [min(v - lo, hi - v) for v, (lo, hi) in zip(q, cell.limits)]
    bad = [AXES[i] for i, m in enumerate(margins) if m < -1e-6]
    if bad:
        return IKResult(tuple(q), "limite_" + "_".join(bad), min(margins[:3]))
    return IKResult(tuple(q), None, min(margins[:3]))


def _cylinder_points(radius, z0, z1, spacing) -> np.ndarray:
    n = max(8, int(ceil(2 * np.pi * radius / spacing)))
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    zs = np.linspace(z0, z1, max(2, int(ceil((z1 - z0) / spacing)) + 1))
    ring = np.stack([radius * np.cos(a), radius * np.sin(a)], 1)
    side = np.vstack([np.column_stack([ring, np.full(n, z)]) for z in zs])
    caps = [np.column_stack([ring * f, np.full(n, z)]) for z in (z0, z1) for f in (0.5,)]
    return np.vstack([side, *caps, [[0, 0, z0], [0, 0, z1]]])


@dataclass
class GantryModel:
    """Misma interfaz que cell_study.RobotModel para cell_study.evaluate_run."""
    cell: GantryCell
    spacing: float = 30.0
    picks: dict = field(default_factory=dict)    # step -> dict(xy, floor_z) del estudio de célula

    LABELS = ("garra", "cabezal_c", "columna_z")

    def __post_init__(self):
        g, f = self.cell.gripper, self.cell.frame
        self.grip_pts = gripper_points(g, self.spacing)
        self.head_pts = _cylinder_points(f.head_d_mm / 2, g.flange_to_tcp_mm, g.flange_to_tcp_mm + f.head_h_mm, self.spacing)
        h = f.ram_mm / 2
        ring = _box_surface((-h, -h, 0), (h, h, 1), self.spacing)
        self.ram_ring = np.unique(ring[:, :2].round(3), axis=0)
        self.ram_z0 = g.flange_to_tcp_mm + f.head_h_mm

    @property
    def home_q(self) -> tuple:
        """Reposo: sobre la cabeza de la cola, 1200 mm por encima de la cinta."""
        cv = self.cell.conveyor
        if cv:
            return (float(cv["point"][0]), float(cv["point"][1]), float(cv["z_conveyor"]) + 1200.0, 0.0)
        c = self.cell
        return ((c.x_range[0] + c.x_range[1]) / 2, (c.y_range[0] + c.y_range[1]) / 2, c.z_range[1], 0.0)

    def solve(self, tcp, box, prefer) -> IKResult:
        """Sin IK: el TCP es la posición de los ejes. C alinea el paso largo con el lado largo de la cara."""
        base = 0.0 if box[3] >= box[4] else 90.0
        lo, hi = self.cell.c_range
        c = nearest_c(base, prefer[3] if len(prefer) > 3 else 0.0, lo, hi, period=180.0)
        return check_limits(self.cell, (float(tcp[0]), float(tcp[1]), float(tcp[2]), c))

    def world_points(self, q) -> tuple[np.ndarray, np.ndarray]:
        t = tool_matrix(q)
        p = np.asarray(q[:3], float)
        grip = self.grip_pts @ t[:3, :3].T + p
        head = self.head_pts @ t[:3, :3].T + p
        z0, z1 = p[2] + self.ram_z0, max(p[2] + self.ram_z0, self.cell.ram_top_clear_z)
        zs = np.arange(z0, z1 + self.spacing, self.spacing)
        ram = np.column_stack([np.tile(self.ram_ring, (len(zs), 1)) + p[:2], np.repeat(zs, len(self.ram_ring))])
        labels = np.concatenate([np.zeros(len(grip), int), np.ones(len(head), int), np.full(len(ram), 2)])
        return np.vstack([grip, head, ram]), labels

    def label(self, i: int) -> str:
        return self.LABELS[i]

    def entry_xy(self, placement) -> tuple[float, float]:
        """El pasillo de entrada parte del punto de recogida (cola de la cinta o volteador)."""
        pick = self.picks.get(placement.step)
        return tuple(pick["xy"]) if pick else (self.cell.base_x, self.cell.base_y)

    def entry_floor_z(self, placement) -> float:
        pick = self.picks.get(placement.step)
        return pick["floor_z"] if pick else -np.inf
