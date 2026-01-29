# src/palca/viz/pallet_viewer.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Tuple, Any

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


@dataclass(frozen=True)
class Rect2D:
    x: float
    y: float
    w: float
    h: float


@dataclass
class _PalletState:
    current_layer: int = 0
    placements_in_layer: int = 0
    filled_area_in_layer: float = 0.0
    total_placements: int = 0
    closed: bool = False
    close_reason: Optional[str] = None

    layer_patches: list = field(default_factory=list)
    layer_texts: list = field(default_factory=list)


class PalletViewer:
    """
    Live viewer for 6 pallets (destinations 1..6) in a 2x3 grid.

    API unchanged.
    Patch: force matplotlib refresh with show(block=False) + canvas.draw().
    """

    def __init__(
        self,
        enabled: bool = True,
        pallet_size_mm: Tuple[int, int] = (1200, 800),
        pallet_ids: Iterable[int] = (1, 2, 3, 4, 5, 6),
        update_every: int = 5,
        show_labels: bool = False,
        show_current_layer_only: bool = True,
        interactive_pause: bool = False,
        window_title: str = "Paletizado Caótico - Pallet Viewer (6 destinos)",
    ) -> None:
        self.enabled = enabled
        self.pallet_w, self.pallet_h = pallet_size_mm
        self.pallet_ids = list(pallet_ids)
        self.update_every = max(1, int(update_every))
        self.show_labels = show_labels
        self.show_current_layer_only = show_current_layer_only
        self.interactive_pause = interactive_pause

        self._event_count = 0
        self._paused = False
        self._step_once = False

        self.states: Dict[int, _PalletState] = {pid: _PalletState() for pid in self.pallet_ids}

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

        gs = self.fig.add_gridspec(2, 3, wspace=0.25, hspace=0.25)
        positions = [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)]

        for pid, (r, c) in zip(self.pallet_ids, positions):
            ax = self.fig.add_subplot(gs[r, c])
            self.axes[pid] = ax
            self._setup_axis(ax, pid)

        self.fig.canvas.mpl_connect("key_press_event", self._on_key)

        # IMPORTANT: some backends need an explicit non-blocking show to start event loop
        plt.show(block=False)
        self._redraw(force=True)

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
            self._clear_current_layer(st)
            st.current_layer = layer_idx
            st.placements_in_layer = 0
            st.filled_area_in_layer = 0.0

        patch = Rectangle((rect.x, rect.y), rect.w, rect.h, fill=False, linewidth=1.0)
        ax.add_patch(patch)
        st.layer_patches.append(patch)

        if self.show_labels:
            label = box_id if box_id is not None else ""
            if orientation is not None:
                label = f"{label}\n{orientation}°" if label else f"{orientation}°"
            if label:
                tx = ax.text(
                    rect.x + rect.w / 2.0,
                    rect.y + rect.h / 2.0,
                    label,
                    ha="center",
                    va="center",
                    fontsize=7,
                )
                st.layer_texts.append(tx)

        st.placements_in_layer += 1
        st.total_placements += 1
        st.filled_area_in_layer += rect.w * rect.h

        self._update_title(pallet_id)

        self._event_count += 1
        self._maybe_redraw()

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

    def _setup_axis(self, ax, pallet_id: int) -> None:
        ax.set_xlim(0, self.pallet_w)
        ax.set_ylim(0, self.pallet_h)
        ax.set_aspect("equal", adjustable="box")
        ax.invert_yaxis()
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("y (mm)")
        ax.grid(True, linewidth=0.3)
        ax.set_title(f"Dest {pallet_id}")

    def _clear_current_layer(self, st: _PalletState) -> None:
        for p in st.layer_patches:
            try:
                p.remove()
            except Exception:
                pass
        for t in st.layer_texts:
            try:
                t.remove()
            except Exception:
                pass
        st.layer_patches.clear()
        st.layer_texts.clear()

    def _update_title(self, pallet_id: int) -> None:
        st = self.states[pallet_id]
        ax = self.axes[pallet_id]
        fill = st.filled_area_in_layer / float(self.pallet_w * self.pallet_h)
        base = f"Dest {pallet_id} | L{st.current_layer} | nL={st.placements_in_layer} | fillL={fill:.2f}"
        if st.closed:
            base += f" | CLOSED ({st.close_reason})" if st.close_reason else " | CLOSED"
        ax.set_title(base)

    def _maybe_redraw(self) -> None:
        if not self.enabled:
            return

        if self.interactive_pause:
            if self._paused and not self._step_once:
                self._redraw(force=True)
                plt.pause(0.05)
                return
            if self._step_once:
                self._step_once = False

        if (self._event_count % self.update_every) == 0:
            self._redraw(force=False)

    def _redraw(self, force: bool = False) -> None:
        if not self.enabled or self.fig is None:
            return
        try:
            # draw() is stronger than draw_idle() for “static window” issues
            self.fig.canvas.draw()
            self.fig.canvas.flush_events()
        except Exception:
            pass
        plt.pause(0.001 if not force else 0.01)

    def _on_key(self, event) -> None:
        if not self.enabled:
            return
        if event.key == "p":
            self._paused = not self._paused
        elif event.key == "n":
            self._step_once = True
        elif event.key in ("q", "escape"):
            try:
                plt.close(self.fig)
            except Exception:
                pass
