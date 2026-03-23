#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from palca.viz.pallet_viewer3d import PalletViewer3D, Rect2D


def _load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"No existe el archivo: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path", nargs="?", help="*.FULL.clean.json de feasibility_bound (con enforce_2d_result)")
    ap.add_argument("--json", help="(legacy) *.FULL.clean.json de feasibility_bound (con enforce_2d_result)")
    ap.add_argument("--sleep", type=float, default=0.12)
    ap.add_argument("--update-every", type=int, default=1)
    ap.add_argument("--pallet-id", type=int, default=1)
    ap.add_argument(
        "--show-inner-pallet",
        action="store_true",
        help="Dibuja el palet real (1200x800) dentro de base con overhang",
    )
    args = ap.parse_args()

    json_path = args.json or args.json_path
    if not json_path:
        raise SystemExit("Uso: viz_enforce_3d_live.py <file.FULL.clean.json>  (o --json <file>)")

    d = _load_json(json_path)
    inp = d.get("input", {}) or {}
    base_l = int(inp.get("base_length_mm", 1240))
    base_w = int(inp.get("base_width_mm", 840))
    hmax = int(inp.get("hmax_mm", 2400))
    overhang = int(inp.get("overhang_mm", 0))

    tm = d.get("target_mode", {}) or {}
    er = tm.get("enforce_2d_result") or {}
    per_layer = er.get("per_layer") or []

    if not per_layer:
        raise SystemExit("No encuentro enforce_2d_result.per_layer en el JSON")

    viewer = PalletViewer3D(
        enabled=True,
        pallet_size_mm=(base_l, base_w),
        pallet_max_h_mm=hmax,
        pallet_ids=(args.pallet_id,),
        update_every=max(1, int(args.update_every)),
        show_current_layer_only=False,
        debug=False,
    )

    # Dibuja el palet real (sin overhang) como rectángulo interior
    if args.show_inner_pallet and overhang > 0:
        ax = viewer.axes[int(args.pallet_id)]
        x0 = overhang
        y0 = overhang
        x1 = base_l - overhang
        y1 = base_w - overhang
        xs = [x0, x1, x1, x0, x0]
        ys = [y0, y0, y1, y1, y0]
        zs = [0, 0, 0, 0, 0]
        ax.plot(xs, ys, zs, linewidth=1.8)

    # Pintar capa a capa, con Z acumulado correcto
    layer_base_z = 0.0
    for list_idx, L in enumerate(per_layer):
        layer_idx = int(L.get("layer_idx", list_idx))
        coords = L.get("coords") or L.get("items") or []

        # Altura de la capa para apilar (preferimos height_mm del layer; fallback: max dz de sus items)
        layer_h = float(L.get("height_mm") or 0.0)
        if layer_h <= 0.0 and coords:
            layer_h = float(max(float(c.get("z_mm", 0.0)) for c in coords))
        if layer_h <= 0.0:
            layer_h = 0.0

        for c in coords:
            x = float(c["x_mm"])
            y = float(c["y_mm"])
            w = float(c["w_mm"])
            h = float(c["h_mm"])

            # IMPORTANTE:
            # - c["z_mm"] NO es base_z; en este JSON es dz (altura orientada)
            dz = float(c.get("z_mm", 0.0))

            row = c.get("row_idx")
            orient = c.get("orient_id")

            viewer.on_place(
                pallet_id=int(args.pallet_id),
                layer_idx=layer_idx,
                rect=Rect2D(x=x, y=y, w=w, h=h),
                box_id=str(row) if row is not None else None,
                orientation=int(orient) if orient is not None else None,
                meta={"z_mm": layer_base_z, "height_mm": dz},
            )
            time.sleep(max(0.0, float(args.sleep)))

        layer_base_z += layer_h

    viewer.finalize(block=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
