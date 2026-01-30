from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Tuple
import math

import matplotlib.pyplot as plt

from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # type: ignore


@dataclass(frozen=True)
class Rect2D:
    x: float
    y: float
    w: float
    h: float


@dataclass
class _PalletState3D:
    current_layer: int = 0
    placements_in_layer: int = 0
    total_placements: int = 0
    closed: bool = False
    close_reason: Optional[str] = None

    solids: list[Any] = field(default_factory=list)   # Poly3DCollection
    texts: list[Any] = field(default_factory=list)


def _cuboid_faces(x: float, y: float, z: float, dx: float, dy: float, dz: float):
    # 8 vertices
    p0 = (x,      y,      z)
    p1 = (x + dx, y,      z)
    p2 = (x + dx, y + dy, z)
    p3 = (x,      y + dy, z)
    p4 = (x,      y,      z + dz)
    p5 = (x + dx, y,      z + dz)
    p6 = (x + dx, y + dy, z + dz)
    p7 = (x,      y + dy, z + dz)

    # 6 faces (each face is a list of 4 verts)
    return [
        [p0, p1, p2, p3],  # bottom
        [p4, p5, p6, p7],  # top
        [p0, p1, p5, p4],  # front
        [p1, p2, p6, p5],  # right
        [p2, p3, p7, p6],  # back
        [p3, p0, p4, p7],  # left
    ]


def _extract_z_h(meta: Optional[dict], layer_idx: int) -> tuple[float, float]:
    """
    Intenta sacar z_mm y height_mm de meta, pero no depende de un esquema único.
    Si no encuentra, devuelve (0, 1) como fallback para al menos visualizar algo.
    """
    z = None
    h = None
    if isinstance(meta, dict):
        for k in ("z_mm", "z", "placement_z_mm", "z0_mm", "base_z_mm"):
            if k in meta:
                try:
                    z = float(meta[k])
                    break
                except Exception:
                    pass

        for k in ("height_mm", "h_mm", "box_h_mm", "box_height_mm", "dz_mm"):
            if k in meta:
                try:
                    h = float(meta[k])
                    break
                except Exception:
                    pass

        # a veces viene un Placement dict/obj dentro de meta
        pl = meta.get("placement") if "placement" in meta else None
        if z is None and pl is not None:
            try:
                if isinstance(pl, dict) and "z_mm" in pl:
                    z = float(pl["z_mm"])
                else:
                    z = float(getattr(pl, "z_mm"))
            except Exception:
                pass

        if h is None and pl is not None:
            try:
                if isinstance(pl, dict) and "height_mm" in pl:
                    h = float(pl["height_mm"])
                else:
                    h = float(getattr(pl, "height_mm"))
            except Exception:
                pass

    if z is None:
        z = 0.0
    if h is None or h <= 0:
        h = 1.0

    return float(z), float(h)


class PalletViewer3D:
    """
    Live 3D viewer for N pallets.

    Fixes:
      - Auto layout (1 pallet -> 1 axis that fills window; 6 -> 2x3)
      - Equal unit scaling on x/y/z via set_box_aspect
      - Solid boxes using Poly3DCollection (not only edges)
    """

    def __init__(
        self,
        enabled: bool = True,
        pallet_size_mm: Tuple[int, int] = (1200, 800),
        pallet_max_h_mm: int = 2400,
        pallet_ids: Iterable[int] = (1, 2, 3, 4, 5, 6),
        update_every: int = 5,
        show_current_layer_only: bool = True,
        window_title: str = "Paletizado Caótico - Pallet Viewer 3D",
        debug: bool = False,
    ) -> None:
        self.enabled = bool(enabled)
        self.pallet_w = int(pallet_size_mm[0])
        self.pallet_h = int(pallet_size_mm[1])
        self.pallet_zmax = int(pallet_max_h_mm)

        ids: list[int] = []
        seen: set[int] = set()
        for pid in pallet_ids:
            ip = int(pid)
            if ip not in seen:
                ids.append(ip)
                seen.add(ip)
        self.pallet_ids = ids if ids else [1]

        self.update_every = max(1, int(update_every))
        self.show_current_layer_only = bool(show_current_layer_only)
        self.debug = bool(debug)

        self._event_count = 0
        self.states: Dict[int, _PalletState3D] = {pid: _PalletState3D() for pid in self.pallet_ids}

        self.fig = None
        self.axes: Dict[int, Any] = {}

        if not self.enabled:
            return

        plt.ion()

        n = len(self.pallet_ids)
        figsize = (9, 6) if n == 1 else (14, 8)
        # constrained_layout suele reducir bastante el blanco
        self.fig = plt.figure(figsize=figsize, constrained_layout=True)
        try:
            self.fig.canvas.manager.set_window_title(window_title)  # type: ignore[attr-defined]
        except Exception:
            pass

        if n == 1:
            nrows, ncols = 1, 1
        else:
            ncols = min(3, n)
            nrows = int(math.ceil(n / ncols))

        gs = self.fig.add_gridspec(nrows, ncols)

        for idx, pid in enumerate(self.pallet_ids):
            r = idx // ncols
            c = idx % ncols
            ax = self.fig.add_subplot(gs[r, c], projection="3d")
            self.axes[pid] = ax
            self._setup_axis(ax, pid)

        plt.show(block=False)
        self._redraw(force=True)

        if self.debug:
            print(f"[VIZ3D] init pallets={self.pallet_ids} update_every={self.update_every}", flush=True)

    def on_place(
        self,
        pallet_id: int,
        layer_idx: int,
        rect: Rect2D,
        box_id: Optional[str] = None,
        orientation: Optional[int] = None,
        meta: Optional[dict] = None,
    ) -> None:
        if not self.enabled:
            return
        pid = int(pallet_id)
        if pid not in self.states:
            return

        st = self.states[pid]
        ax = self.axes[pid]

        if self.show_current_layer_only and int(layer_idx) != st.current_layer:
            self._clear_current_layer(st)
            st.current_layer = int(layer_idx)
            st.placements_in_layer = 0

        if rect.w <= 0 or rect.h <= 0:
            return

        z, h = _extract_z_h(meta, int(layer_idx))

        faces = _cuboid_faces(float(rect.x), float(rect.y), float(z), float(rect.w), float(rect.h), float(h))

        poly = Poly3DCollection(
            faces,
            linewidths=0.3,
            edgecolors="k",
            alpha=0.35,     # “sólido” pero se ve dentro
        )
        ax.add_collection3d(poly)
        st.solids.append(poly)

        st.placements_in_layer += 1
        st.total_placements += 1

        ax.set_title(f"Dest {pid} | L{st.current_layer} | nL={st.placements_in_layer}")

        self._event_count += 1
        if self.debug and (st.total_placements == 1 or (st.total_placements % 10) == 0):
            print(f"[VIZ3D] dest={pid} placed={st.total_placements} z={z:.1f} h={h:.1f}", flush=True)

        self._maybe_redraw()

    def on_close(self, pallet_id: int, reason: Optional[str] = None) -> None:
        if not self.enabled:
            return
        pid = int(pallet_id)
        if pid not in self.states:
            return
        st = self.states[pid]
        st.closed = True
        st.close_reason = reason
        ax = self.axes[pid]
        base = ax.get_title()
        ax.set_title(base + (f" | CLOSED({reason})" if reason else " | CLOSED"))
        self._redraw(force=True)

    def finalize(self, block: bool = True) -> None:
        if not self.enabled:
            return
        self._redraw(force=True)
        plt.ioff()
        if block:
            plt.show()

    def _setup_axis(self, ax, pallet_id: int) -> None:
        ax.set_xlim(0, self.pallet_w)
        ax.set_ylim(0, self.pallet_h)
        ax.set_zlim(0, self.pallet_zmax)

        ax.set_xlabel("x (mm)")
        ax.set_ylabel("y (mm)")
        ax.set_zlabel("z (mm)")
        ax.set_title(f"Dest {pallet_id}")

        # vista inicial
        ax.view_init(elev=18, azim=-55)

        # *** CLAVE: escala real (1mm en x=y=z) ***
        try:
            ax.set_box_aspect((self.pallet_w, self.pallet_h, self.pallet_zmax))
        except Exception:
            # fallback: si el backend es viejo, al menos evita deformaciones fuertes
            pass

        # dibujar el contorno del pallet (suelo)
        xs = [0, self.pallet_w, self.pallet_w, 0, 0]
        ys = [0, 0, self.pallet_h, self.pallet_h, 0]
        zs = [0, 0, 0, 0, 0]
        ax.plot(xs, ys, zs, linewidth=1.2)

    def _clear_current_layer(self, st: _PalletState3D) -> None:
        for obj in st.solids:
            try:
                obj.remove()
            except Exception:
                pass
        for t in st.texts:
            try:
                t.remove()
            except Exception:
                pass
        st.solids.clear()
        st.texts.clear()

    def _maybe_redraw(self) -> None:
        if (self._event_count % self.update_every) == 0:
            self._redraw(force=False)

    def _redraw(self, force: bool = False) -> None:
        if not self.enabled or self.fig is None:
            return
        try:
            if force:
                self.fig.canvas.draw()
            else:
                self.fig.canvas.draw_idle()
            self.fig.canvas.flush_events()
        except Exception:
            pass
        plt.pause(0.001 if not force else 0.01)
