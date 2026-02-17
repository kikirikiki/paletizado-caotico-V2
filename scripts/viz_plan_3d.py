#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from palca.viz.pallet_viewer3d import PalletViewer3D, Rect2D


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"No existe el archivo: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _as_support_info(it: dict[str, Any], *, min_support: float) -> tuple[bool, float] | None:
    has_ratio = "support_ratio" in it
    has_supported = "is_supported" in it
    if not has_ratio and not has_supported:
        return None

    ratio = 0.0
    if has_ratio:
        try:
            ratio = float(it.get("support_ratio", 0.0))
        except Exception:
            ratio = 0.0

    if has_supported:
        raw = it.get("is_supported")
        if isinstance(raw, bool):
            is_supported = raw
        elif isinstance(raw, (int, float)):
            is_supported = bool(raw)
        elif isinstance(raw, str):
            is_supported = raw.strip().lower() in {"1", "true", "yes", "y", "si", "sí"}
        else:
            is_supported = False
    else:
        is_supported = bool(ratio + 1e-9 >= float(min_support))

    return bool(is_supported), float(ratio)


def main() -> int:
    ap = argparse.ArgumentParser(description="Visualiza un plan exportado (palca/enforce_plan) en 3D.")
    ap.add_argument("plan_json", help="Ruta a *.plan.json generado por export_enforce_plan.py")
    ap.add_argument("--pallet-id", type=int, default=1)
    ap.add_argument("--sleep", type=float, default=0.0, help="Sleep entre placements (animación). Default 0.")
    ap.add_argument("--update-every", type=int, default=1)
    ap.add_argument(
        "--show-inner-pallet",
        action="store_true",
        help="Dibuja el palet real (base efectiva - 2*overhang) dentro de base efectiva.",
    )
    ap.add_argument(
        "--layer",
        type=int,
        default=None,
        help="Si se indica, solo muestra esa capa (layer_idx).",
    )
    ap.add_argument(
        "--min-support",
        type=float,
        default=0.90,
        help="Umbral de soporte para interpretar support_ratio si is_supported no existe. Default: 0.90.",
    )
    ap.add_argument(
        "--label-unsupported",
        action="store_true",
        help="Etiqueta cajas no soportadas como !ratio encima de cada caja (si el plan trae support_*).",
    )
    args = ap.parse_args()
    if float(args.min_support) < 0:
        raise SystemExit("--min-support debe ser >= 0")

    p = Path(args.plan_json)
    d = _load_json(p)

    if d.get("schema") != "palca/enforce_plan":
        raise SystemExit(f"schema inesperado: {d.get('schema')!r} (esperado 'palca/enforce_plan')")

    base = d.get("base_effective", {}) or {}
    base_l = int(round(float(base.get("length_mm", 1240))))
    base_w = int(round(float(base.get("width_mm", 840))))
    overhang = int(round(float(base.get("overhang_mm", 0))))
    hmax = int(round(float(d.get("hmax_mm", 2400))))

    placements = d.get("placements") or []
    if not isinstance(placements, list) or not placements:
        raise SystemExit("No hay placements en el plan (placements vacío).")

    viewer = PalletViewer3D(
        enabled=True,
        pallet_size_mm=(base_l, base_w),
        pallet_max_h_mm=hmax,
        pallet_ids=(int(args.pallet_id),),
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

    # Orden de pintado: por z_mm y layer_idx para consistencia visual
    def _k(it: dict[str, Any]) -> tuple[int, int, int]:
        try:
            z = int(round(float(it.get("z_mm", 0))))
        except Exception:
            z = 0
        try:
            layer = int(it.get("layer_idx", 0))
        except Exception:
            layer = 0
        try:
            row = int(it.get("row_idx", -1))
        except Exception:
            row = -1
        return (z, layer, row)

    placements_sorted = sorted(placements, key=_k)
    unsupported_rows: list[str] = []
    support_checks = 0

    for it in placements_sorted:
        layer_idx = int(it.get("layer_idx", 0))
        if args.layer is not None and layer_idx != int(args.layer):
            continue

        x = float(it["x_mm"])
        y = float(it["y_mm"])
        w = float(it["w_mm"])
        h = float(it["h_mm"])
        z = float(it.get("z_mm", 0.0))
        dz = float(it.get("dz_mm", it.get("height_mm", 0.0)))

        row = it.get("row_idx")
        orient = it.get("orient_id")

        viewer.on_place(
            pallet_id=int(args.pallet_id),
            layer_idx=layer_idx,
            rect=Rect2D(x=x, y=y, w=w, h=h),
            box_id=str(row) if row is not None else None,
            orientation=int(orient) if orient is not None else None,
            meta={"z_mm": z, "height_mm": dz},
        )
        support_info = _as_support_info(it, min_support=float(args.min_support))
        if support_info is not None:
            support_checks += 1
            is_supported, ratio = support_info
            if not is_supported:
                rid = it.get("row_idx")
                unsupported_rows.append(f"row={rid} layer={layer_idx} support_ratio={ratio:.2f}")
                if args.label_unsupported:
                    ax = viewer.axes[int(args.pallet_id)]
                    st = viewer.states[int(args.pallet_id)]
                    t = ax.text(
                        x + (w / 2.0),
                        y + (h / 2.0),
                        z + dz + 20.0,
                        f"!{ratio:.2f}",
                        color="crimson",
                        fontsize=8,
                        ha="center",
                        va="bottom",
                    )
                    st.texts.append(t)

        if args.sleep and args.sleep > 0:
            time.sleep(float(args.sleep))

    if support_checks > 0:
        print(
            f"[viz] support_fields={support_checks} "
            f"unsupported={len(unsupported_rows)} min_support={float(args.min_support):.2f}"
        )
        for line in unsupported_rows:
            print(f"[viz][UNSUPPORTED] {line}")
    else:
        print("[viz] el plan no trae support_ratio/is_supported; solo visualización geométrica.")

    viewer.finalize(block=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
