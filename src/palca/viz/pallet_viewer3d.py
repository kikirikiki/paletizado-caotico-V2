from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Tuple, Any

import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Line3DCollection


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
    filled_area_in_layer: float = 0.0
    total_placements: int = 0
    closed: bool = False
    close_reason: Optional[str] = None
    # Artists
    artists: list = field(default_factory=list)


class PalletViewer3D:
    """
    3D live viewer for 6 pallets (destinations 1..6) in a 2x3 grid.
    Draws boxes as wireframe edges using Line3DCollection.

    Expects on_place(..., meta={"z0": mm, "h": mm}) for proper height display.
    If meta is missing, falls back to z0=layer_idx, h=1.
    """

    def __init__(
        self,
        enabled: bool = True,
        pallet_size_mm: Tuple[int, int] = (1200, 800),
        pallet_max_height_mm: float = 2400.0,
        pallet_ids: Iterable[int] = (1, 2, 3, 4, 5, 6),
        update_every: int = 10,
        show_current_layer_only: bool = False,
        window_title: str = "Paletizado Caótico - Pallet Viewer 3D (6 destinos)",
    ) -> None:
        self.enabled = enabled
        self.pallet_w, self.pallet_h = pallet_size_mm
        self.pallet_max_z = float(pallet_max_height_mm)
        self.pallet_ids = list(pallet_ids)
        self.update_every = max(1, int(update_every))
        self.show_current_layer_only = show_current_layer_only

        self._event_count = 0

        self.states: Dict[int, _PalletState3D] = {pid: _PalletState3D() for pid in self.pallet_ids}

        self.fig = None
        self.axes: Dict[int, Any] = {}

        if not self.enabled:
            return

        plt.ion()
        self.fig = plt.figure(figsize=(14, 8))
        try:
            self.fig.canvas.manager.set_window_title(window_title)  # type: ignore[attr-defined]
        except Exception:
            pass

        gs = self.fig.add_gridspec(2, 3, wspace=0.15, hspace=0.15)
        positions = [(0,0),(0,1),(0,2),(1,0),(1,1),(1,2)]

        for pid, (r, c) in zip(self.pallet_ids, positions):
            ax = self.fig.add_subplot(gs[r, c], projection="3d")
            self.axes[pid] = ax
            self._setup_axis(ax, pid)

        self.fig.canvas.mpl_connect("key_press_event", self._on_key)
        self._redraw(force=True)

    # -------- Public API --------

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
        if pallet_id not in self.states:
            return

        st = self.states[pallet_id]
        ax = self.axes[pallet_id]

        if self.show_current_layer_only and layer_idx != st.current_layer:
            self._clear(ax, st)
            st.current_layer = layer_idx
            st.placements_in_layer = 0
            st.filled_area_in_layer = 0.0

        z0 = float(meta.get("z0", layer_idx)) if meta else float(layer_idx)
        hz = float(meta.get("h", 1.0)) if meta else 1.0

        artist = self._draw_box_edges(ax, rect.x, rect.y, z0, rect.w, rect.h, hz)
        st.artists.append(artist)

        st.placements_in_layer += 1
        st.total_placements += 1
        st.filled_area_in_layer += rect.w * rect.h

        self._update_title(pallet_id)

        self._event_count += 1
        if (self._event_count % self.update_every) == 0:
            self._redraw(force=False)

    def on_close(self, pallet_id: int, reason: Optional[str] = None) -> None:
        if not self.enabled:
            return
        if pallet_id not in self.states:
            return
        st = self.states[pallet_id]
        st.closed = True
        st.close_reason = reason
        self._update_title(pallet_id)
        self._redraw(force=True)

    def finalize(self, block: bool = True) -> None:
        if not self.enabled:
            return
        self._redraw(force=True)
        plt.ioff()
        if block:
            plt.show()

    # -------- Internal helpers --------

    def _setup_axis(self, ax, pallet_id: int) -> None:
        ax.set_xlim(0, self.pallet_w)
        ax.set_ylim(0, self.pallet_h)
        ax.set_zlim(0, self.pallet_max_z)
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("y (mm)")
        ax.set_zlabel("z (mm)")
        ax.set_title(f"Dest {pallet_id}")
        ax.view_init(elev=22, azim=-55)
        # Draw pallet base outline at z=0
        self._draw_base(ax)

    def _draw_base(self, ax) -> None:
        x0, y0, z0 = 0.0, 0.0, 0.0
        x1, y1 = float(self.pallet_w), float(self.pallet_h)
        segs = [
            [(x0,y0,z0),(x1,y0,z0)],
            [(x1,y0,z0),(x1,y1,z0)],
            [(x1,y1,z0),(x0,y1,z0)],
            [(x0,y1,z0),(x0,y0,z0)],
        ]
        lc = Line3DCollection(segs, linewidths=1.0)
        ax.add_collection3d(lc)

    def _draw_box_edges(self, ax, x, y, z, w, h, dz):
        x0, y0, z0 = float(x), float(y), float(z)
        x1, y1, z1 = x0 + float(w), y0 + float(h), z0 + float(dz)

        # 12 edges of a cuboid
        segs = [
            [(x0,y0,z0),(x1,y0,z0)], [(x1,y0,z0),(x1,y1,z0)], [(x1,y1,z0),(x0,y1,z0)], [(x0,y1,z0),(x0,y0,z0)],
            [(x0,y0,z1),(x1,y0,z1)], [(x1,y0,z1),(x1,y1,z1)], [(x1,y1,z1),(x0,y1,z1)], [(x0,y1,z1),(x0,y0,z1)],
            [(x0,y0,z0),(x0,y0,z1)], [(x1,y0,z0),(x1,y0,z1)], [(x1,y1,z0),(x1,y1,z1)], [(x0,y1,z0),(x0,y1,z1)],
        ]
        lc = Line3DCollection(segs, linewidths=0.6)
        ax.add_collection3d(lc)
        return lc

    def _clear(self, ax, st: _PalletState3D) -> None:
        for a in st.artists:
            try:
                a.remove()
            except Exception:
                pass
        st.artists.clear()
        self._draw_base(ax)

    def _update_title(self, pallet_id: int) -> None:
        st = self.states[pallet_id]
        ax = self.axes[pallet_id]
        fill = st.filled_area_in_layer / float(self.pallet_w * self.pallet_h)
        base = f"Dest {pallet_id} | L{st.current_layer} | nL={st.placements_in_layer} | fillL={fill:.2f}"
        if st.closed:
            base += f" | CLOSED ({st.close_reason})" if st.close_reason else " | CLOSED"
        ax.set_title(base)

    def _redraw(self, force: bool = False) -> None:
        if not self.enabled or self.fig is None:
            return
        try:
            self.fig.canvas.draw_idle()
            self.fig.canvas.flush_events()
        except Exception:
            pass
        plt.pause(0.001 if not force else 0.01)

    def _on_key(self, event) -> None:
        if not self.enabled:
            return
        if event.key in ("q", "escape"):
            try:
                plt.close(self.fig)
            except Exception:
                pass
