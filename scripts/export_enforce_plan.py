#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"No existe el archivo: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _as_int_mm(v: Any, *, field: str) -> int:
    if v is None:
        raise KeyError(field)
    try:
        return int(round(float(v)))
    except Exception as e:
        raise ValueError(f"Campo {field} no convertible a mm (value={v!r})") from e


def _iter_layer_coords(layer_obj: dict[str, Any]) -> list[dict[str, Any]]:
    coords = layer_obj.get("coords")
    if coords is None:
        coords = layer_obj.get("items")
    if coords is None:
        coords = []
    if not isinstance(coords, list):
        raise TypeError("enforce_2d_result.per_layer[].coords/items debe ser una lista")
    return coords


def _layer_height_mm(layer_obj: dict[str, Any], coords: list[dict[str, Any]]) -> int:
    # Preferimos height_mm del layer; si falta/<=0, usamos max dz (coord["z_mm"] = dz)
    h = layer_obj.get("height_mm")
    if h is not None:
        try:
            h_int = int(round(float(h)))
        except Exception:
            h_int = 0
        if h_int > 0:
            return h_int

    if coords:
        dzs: list[float] = []
        for c in coords:
            if "z_mm" in c and c["z_mm"] is not None:
                dzs.append(float(c["z_mm"]))
        if dzs:
            return int(round(max(dzs)))

    return 0


def _check_bounds(
    *,
    x_mm: int,
    y_mm: int,
    w_mm: int,
    h_mm: int,
    base_l_mm: int,
    base_w_mm: int,
    ident: str,
    warnings: list[str],
) -> None:
    if x_mm < 0 or y_mm < 0:
        warnings.append(f"{ident}: coord negativa (x={x_mm}, y={y_mm})")
    if x_mm + w_mm > base_l_mm or y_mm + h_mm > base_w_mm:
        warnings.append(
            f"{ident}: fuera de base efectiva {base_l_mm}x{base_w_mm} "
            f"(x+w={x_mm + w_mm}, y+h={y_mm + h_mm})"
        )


def _overlap_area_xy(
    *,
    ax_mm: int,
    ay_mm: int,
    aw_mm: int,
    ah_mm: int,
    bx_mm: int,
    by_mm: int,
    bw_mm: int,
    bh_mm: int,
) -> int:
    ax1 = ax_mm + aw_mm
    ay1 = ay_mm + ah_mm
    bx1 = bx_mm + bw_mm
    by1 = by_mm + bh_mm
    x0 = max(ax_mm, bx_mm)
    y0 = max(ay_mm, by_mm)
    x1 = min(ax1, bx1)
    y1 = min(ay1, by1)
    dx = x1 - x0
    dy = y1 - y0
    if dx <= 0 or dy <= 0:
        return 0
    return int(dx * dy)


def _compute_drop_z(current: dict[str, Any], placed: list[dict[str, Any]]) -> int:
    z_drop = 0
    for prev in placed:
        overlap_area = _overlap_area_xy(
            ax_mm=int(current["x_mm"]),
            ay_mm=int(current["y_mm"]),
            aw_mm=int(current["w_mm"]),
            ah_mm=int(current["h_mm"]),
            bx_mm=int(prev["x_mm"]),
            by_mm=int(prev["y_mm"]),
            bw_mm=int(prev["w_mm"]),
            bh_mm=int(prev["h_mm"]),
        )
        if overlap_area <= 0:
            continue
        top_z = int(prev["z_mm"]) + int(prev["dz_mm"])
        if top_z > z_drop:
            z_drop = top_z
    return int(z_drop)


def _compute_support(
    current: dict[str, Any],
    placed: list[dict[str, Any]],
    *,
    eps_z_mm: float,
    min_support: float,
) -> dict[str, Any]:
    z_mm = int(current["z_mm"])
    area_footprint = int(max(0, int(current["w_mm"])) * max(0, int(current["h_mm"])))

    if z_mm <= float(eps_z_mm):
        return {
            "support_area_mm2": float(area_footprint),
            "support_ratio": 1.0,
            "support_by_rows": [],
            "support_by_n": 0,
            "is_supported": True,
        }

    support_area = 0.0
    support_rows: list[int] = []
    seen_rows: set[int] = set()
    for prev in placed:
        top_z = int(prev["z_mm"]) + int(prev["dz_mm"])
        if abs(float(top_z) - float(z_mm)) > float(eps_z_mm):
            continue

        overlap_area = _overlap_area_xy(
            ax_mm=int(current["x_mm"]),
            ay_mm=int(current["y_mm"]),
            aw_mm=int(current["w_mm"]),
            ah_mm=int(current["h_mm"]),
            bx_mm=int(prev["x_mm"]),
            by_mm=int(prev["y_mm"]),
            bw_mm=int(prev["w_mm"]),
            bh_mm=int(prev["h_mm"]),
        )
        if overlap_area <= 0:
            continue

        support_area += float(overlap_area)
        row_idx = int(prev.get("row_idx", -1))
        if row_idx not in seen_rows:
            seen_rows.add(row_idx)
            support_rows.append(row_idx)

    support_ratio = (support_area / float(area_footprint)) if area_footprint > 0 else 0.0
    is_supported = bool(support_ratio + 1e-9 >= float(min_support))
    return {
        "support_area_mm2": float(support_area),
        "support_ratio": float(support_ratio),
        "support_by_rows": support_rows,
        "support_by_n": len(support_rows),
        "is_supported": is_supported,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Exporta placements limpios desde enforce_2d_result (feasibility_bound).")
    ap.add_argument(
        "input_json",
        help="*.FULL.clean.json con target_mode.enforce_2d_result.per_layer[].coords (coord.z_mm = dz)",
    )
    ap.add_argument(
        "-o",
        "--output",
        help="Ruta de salida. Default: out/plan_enforce/<stem>.plan.json",
    )
    ap.add_argument(
        "--include-real-xy",
        action="store_true",
        help="Incluye x_real_mm,y_real_mm (= x_mm - overhang, y_mm - overhang) si overhang>0.",
    )
    ap.add_argument("--pretty", action="store_true", help="JSON formateado con indent=2 (más legible).")
    ap.add_argument("--strict", action="store_true", help="Falla (exit!=0) si hay warnings de integridad.")
    ap.add_argument("--print-summary", action="store_true", help="Imprime resumen (capas/alturas y primeras colocaciones).")
    ap.add_argument("--head", type=int, default=20, help="N items a mostrar en --print-summary (default: 20).")
    ap.add_argument(
        "--z-mode",
        choices=("layer", "drop"),
        default="drop",
        help="Modo de Z exportada: layer (plana) o drop (gravedad). Default: drop.",
    )
    ap.add_argument(
        "--min-support",
        type=float,
        default=0.90,
        help="Soporte mínimo para marcar is_supported=True cuando z_mm>0. Default: 0.90.",
    )
    ap.add_argument(
        "--support-eps-z-mm",
        type=float,
        default=1.0,
        help="Tolerancia en Z para considerar cajas soporte (|top_z-z_mm|<=eps). Default: 1.0.",
    )
    args = ap.parse_args()
    if float(args.support_eps_z_mm) < 0:
        raise SystemExit("--support-eps-z-mm debe ser >= 0")
    if float(args.min_support) < 0:
        raise SystemExit("--min-support debe ser >= 0")

    in_path = Path(args.input_json)
    d = _load_json(in_path)

    inp = d.get("input", {}) or {}
    base_l = int(round(float(inp.get("base_length_mm", 1240))))
    base_w = int(round(float(inp.get("base_width_mm", 840))))
    hmax = int(round(float(inp.get("hmax_mm", 2400))))
    overhang = int(round(float(inp.get("overhang_mm", 0))))

    if base_l <= 0 or base_w <= 0:
        raise SystemExit(f"Base inválida en input: base_length_mm={base_l}, base_width_mm={base_w}")

    pallet_real_l = max(0, base_l - 2 * overhang)
    pallet_real_w = max(0, base_w - 2 * overhang)

    tm = d.get("target_mode", {}) or {}
    er = tm.get("enforce_2d_result") or {}
    per_layer = er.get("per_layer") or []
    if not per_layer:
        raise SystemExit("No encuentro target_mode.enforce_2d_result.per_layer en el JSON")

    # Orden robusto por layer_idx (si falta, usamos list_idx). Mantiene estable por list_idx.
    layer_entries: list[tuple[int, int, dict[str, Any]]] = []
    for list_idx, L in enumerate(per_layer):
        try:
            layer_idx = int(L.get("layer_idx", list_idx))
        except Exception:
            layer_idx = list_idx
        layer_entries.append((layer_idx, list_idx, L))
    layer_entries.sort(key=lambda t: (t[0], t[1]))

    warnings: list[str] = []
    layers_out: list[dict[str, Any]] = []
    placements: list[dict[str, Any]] = []

    layer_base_z = 0
    seen_rows: set[int] = set()
    seen_layer_idxs: set[int] = set()

    for layer_idx, list_idx, L in layer_entries:
        if layer_idx in seen_layer_idxs:
            warnings.append(f"layer_idx duplicado: {layer_idx} (list_idx={list_idx})")
        seen_layer_idxs.add(layer_idx)

        coords = _iter_layer_coords(L)
        layer_h = _layer_height_mm(L, coords)

        layers_out.append(
            {
                "layer_idx": layer_idx,
                "base_z_mm": int(layer_base_z),
                "height_mm": int(layer_h),
                "count": len(coords),
            }
        )

        for c_i, c in enumerate(coords):
            x = _as_int_mm(c.get("x_mm"), field="x_mm")
            y = _as_int_mm(c.get("y_mm"), field="y_mm")
            w = _as_int_mm(c.get("w_mm"), field="w_mm")
            h = _as_int_mm(c.get("h_mm"), field="h_mm")

            # IMPORTANTE: coord["z_mm"] aquí es dz (altura orientada), NO es base_z
            dz = _as_int_mm(c.get("z_mm", 0), field="z_mm")

            row_idx = c.get("row_idx")
            if row_idx is None:
                warnings.append(f"layer={layer_idx} item#{c_i}: falta row_idx")
                row_int = -1
            else:
                row_int = int(row_idx)
                if row_int in seen_rows:
                    warnings.append(f"row_idx duplicado: {row_int} (layer={layer_idx} item#{c_i})")
                seen_rows.add(row_int)

            orient_id = c.get("orient_id")
            orient_int = int(orient_id) if orient_id is not None else None
            orient_name = c.get("orient_name")

            ident = f"row={row_int} layer={layer_idx}"
            _check_bounds(
                x_mm=x,
                y_mm=y,
                w_mm=w,
                h_mm=h,
                base_l_mm=base_l,
                base_w_mm=base_w,
                ident=ident,
                warnings=warnings,
            )

            out: dict[str, Any] = {
                "row_idx": row_int,
                "item_idx": int(c.get("item_idx", -1)),
                "orient_id": orient_int,
                "orient_name": orient_name,
                "layer_idx": layer_idx,
                "x_mm": x,
                "y_mm": y,
                "z_mm": int(layer_base_z),
                "z_layer_mm": int(layer_base_z),
                "w_mm": w,
                "h_mm": h,
                "dz_mm": dz,
            }

            if args.include_real_xy and overhang > 0:
                out["x_real_mm"] = x - overhang
                out["y_real_mm"] = y - overhang

            placements.append(out)

        layer_base_z += int(layer_h)

    placed: list[dict[str, Any]] = []
    unsupported_count = 0
    for it in placements:
        if args.z_mode == "drop":
            it["z_mm"] = int(_compute_drop_z(it, placed))
        else:
            it["z_mm"] = int(it["z_layer_mm"])

        support_info = _compute_support(
            it,
            placed,
            eps_z_mm=float(args.support_eps_z_mm),
            min_support=float(args.min_support),
        )
        it.update(support_info)
        if not bool(it["is_supported"]):
            unsupported_count += 1
        placed.append(it)

    # Coherencia suave con height_mm global si existe
    height_global = er.get("height_mm")
    if height_global is not None:
        try:
            hg = int(round(float(height_global)))
        except Exception:
            hg = None  # type: ignore[assignment]
        if isinstance(hg, int) and hg > 0:
            if abs(hg - int(layer_base_z)) > 2:
                warnings.append(f"height_mm global ({hg}) != suma alturas capas ({int(layer_base_z)})")

    out_path = Path(args.output) if args.output else Path("out") / "plan_enforce" / f"{in_path.stem}.plan.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "schema": "palca/enforce_plan",
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "z_mode": str(args.z_mode),
        "source": {
            "input_json": str(in_path),
            "target_mode_status": tm.get("status"),
            "solver_status": er.get("solver_status"),
            "solver_status_proven": er.get("solver_status_proven"),
            "selected_count": er.get("selected_count"),
            "used_layers": er.get("used_layers"),
            "height_mm": er.get("height_mm"),
        },
        "base_effective": {"length_mm": base_l, "width_mm": base_w, "overhang_mm": overhang},
        "pallet_real": {"length_mm": pallet_real_l, "width_mm": pallet_real_w},
        "hmax_mm": hmax,
        "support": {
            "min_support": float(args.min_support),
            "eps_z_mm": float(args.support_eps_z_mm),
            "supported_count": int(len(placements) - unsupported_count),
            "unsupported_count": int(unsupported_count),
        },
        "layers": layers_out,
        "placements": placements,
        "warnings": warnings,
    }

    txt = json.dumps(payload, indent=2, ensure_ascii=False) if args.pretty else json.dumps(payload, ensure_ascii=False)
    out_path.write_text(txt + "\n", encoding="utf-8")

    print(f"[export] wrote: {out_path}  placements={len(placements)} layers={len(layers_out)} warnings={len(warnings)}")
    if warnings:
        for w in warnings[:20]:
            print(f"[WARN] {w}")
        if len(warnings) > 20:
            print(f"[WARN] ... ({len(warnings) - 20} warnings más)")
        if args.strict:
            return 2

    if args.print_summary:
        print("\n=== LAYERS ===")
        for L in layers_out:
            print(f"L{L['layer_idx']} base_z={L['base_z_mm']} height={L['height_mm']} count={L['count']}")
        print(
            "\n=== SUPPORT ===\n"
            f"z_mode={args.z_mode} min_support={float(args.min_support):.2f} "
            f"unsupported={unsupported_count}/{len(placements)}"
        )
        print("\n=== HEAD placements ===")
        for it in placements[: max(0, int(args.head))]:
            print(
                f"row={it['row_idx']} layer={it['layer_idx']} "
                f"xy=({it['x_mm']},{it['y_mm']}) wh=({it['w_mm']},{it['h_mm']}) "
                f"z={it['z_mm']} z_layer={it['z_layer_mm']} dz={it['dz_mm']} "
                f"sup={it.get('support_ratio', 0.0):.2f} ok={it.get('is_supported')} orient={it.get('orient_id')}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
