#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import inspect
import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sim.run import run_simulation

PROFILE_SCHEMA_VERSION = 1
DEFAULT_PROFILE_PATH = Path("configs/benchmarks/one_pallet_canonical.json")
DEFAULT_EXPLAINABILITY_TOP_K = 5
DEFAULT_EXPLAINABILITY_CANVAS_COLS = 64
DEFAULT_EXPLAINABILITY_CANVAS_ROWS = 24

RUN_SIM_EXCLUDED_PROFILE_KEYS = {
    "excel_path",
    "out_path",
    "out_json",
    "episode_seed",
    "dump_placements_path",
    "viz",
    "viz_mode",
    "viz_every",
    "viz_labels",
    "viz_block",
    "viz_debug",
    "viz_dest",
}

PARAM_ALIASES = {
    "k": "lookahead_k",
    "lookahead": "lookahead_k",
    "overhang": "overhang_mm",
    "time_budget": "time_budget_ms",
    "height_slack": "height_slack_mm",
}

RUN_SIMULATION_SIGNATURE = inspect.signature(run_simulation)
RUN_SIMULATION_PARAM_KEYS = set(RUN_SIMULATION_SIGNATURE.parameters.keys())
REQUIRED_PARAM_KEYS = set(RUN_SIMULATION_PARAM_KEYS - RUN_SIM_EXCLUDED_PROFILE_KEYS)


def _validate_harness_contract() -> None:
    missing_excluded = sorted(RUN_SIM_EXCLUDED_PROFILE_KEYS - RUN_SIMULATION_PARAM_KEYS)
    if missing_excluded:
        raise RuntimeError(
            "Harness desalineado con run_simulation: claves runtime no encontradas: "
            f"{missing_excluded}"
        )

    invalid_alias_targets = sorted({dst for dst in PARAM_ALIASES.values() if dst not in REQUIRED_PARAM_KEYS})
    if invalid_alias_targets:
        raise RuntimeError(
            "PARAM_ALIASES desalineado con run_simulation; destino(s) inexistente(s): "
            f"{invalid_alias_targets}"
        )


_validate_harness_contract()


@dataclass(frozen=True, slots=True)
class SeedSummary:
    run_label: str
    seed: int
    processed_boxes: int | None
    first_stack_step: int | None
    first_stand_hw_step: int | None
    stand_hw_used_total: int | None
    hard_floor_phase_stand_hw_chosen_total: int | None
    max_z_seen_last_mm: int | None
    lower_layer_reentry_count: int | None
    lower_layer_reentry_total_drop_mm: int | None
    lower_layer_reentry_max_drop_mm: int | None
    lower_layer_reentry_mean_drop_mm: float | None
    monotonic_stack_rate: float | None
    placements_below_current_top_band_after_opening_next_band: int | None
    layer_closure_score: float | None
    layer_fill_homogeneity_score: float | None
    z_band_fill_homogeneity_score: float | None
    active_layers_peak: int | None
    blocked_count: int | None
    marginal_count: int | None
    severe_marginal_count: int | None
    blocked_stand_hw: int | None
    marginal_stand_hw: int | None
    severe_marginal_stand_hw: int | None
    first_blocked_step: int | None
    first_severe_marginal_step: int | None
    issues_concentrated_at_end: bool | None
    critical_placements_json: str
    top_critical_steps_json: str
    top_critical_orientations_json: str
    top_critical_reasons_json: str
    recurrent_blockers_json: str
    explainability_exports_json: str
    layer_band_mm: int | None
    layer_band_fill_progress_json: str
    active_layers_over_time_json: str
    z_band_fill_share_json: str
    layer_fill_share_json: str
    step_trace_relevant_json: str
    output_json: str
    placements_json: str
    effective_config_hash: str


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _safe_get(d: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    cur: Any = d
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def _normalize_param_key(raw_key: str) -> str:
    key = str(raw_key).strip()
    if key.startswith("params."):
        key = key[len("params.") :]
    key = key.replace("-", "_")
    key = PARAM_ALIASES.get(key, key)
    return key


def _coerce_override_value(raw: str) -> Any:
    text = str(raw).strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except Exception:
        return text


def _parse_set_overrides(items: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Override invalido (esperado key=value): {item!r}")
        raw_key, raw_value = item.split("=", 1)
        key = _normalize_param_key(raw_key)
        out[key] = _coerce_override_value(raw_value)
    return out


def _stable_hash(payload: dict[str, Any]) -> str:
    packed = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def _git(cmd: list[str]) -> str | None:
    try:
        out = subprocess.check_output(cmd, text=True).strip()
    except Exception:
        return None
    return out or None


def load_profile(path: str | Path) -> dict[str, Any]:
    profile_path = Path(path).expanduser().resolve()
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Perfil invalido: se esperaba un objeto JSON")

    schema_version = int(payload.get("schema_version", 0) or 0)
    if schema_version != PROFILE_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version invalida: {schema_version}; esperada: {PROFILE_SCHEMA_VERSION}"
        )

    missing_top = [k for k in ("profile_name", "excel", "seeds", "params") if k not in payload]
    if missing_top:
        raise ValueError(f"Perfil incompleto: faltan claves top-level: {missing_top}")

    seeds = payload.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        raise ValueError("Perfil invalido: 'seeds' debe ser una lista no vacia")
    norm_seeds = [int(s) for s in seeds]

    params = payload.get("params")
    if not isinstance(params, dict):
        raise ValueError("Perfil invalido: 'params' debe ser un objeto JSON")

    missing_params = sorted(REQUIRED_PARAM_KEYS - set(params.keys()))
    if missing_params:
        raise ValueError(
            "Perfil incompleto: faltan parametros requeridos para run_simulation: "
            f"{missing_params}"
        )

    unknown_params = sorted(set(params.keys()) - REQUIRED_PARAM_KEYS)
    if unknown_params:
        raise ValueError(
            "Perfil invalido: parametros desconocidos/no usados por run_simulation: "
            f"{unknown_params}"
        )

    return {
        "schema_version": schema_version,
        "profile_name": str(payload["profile_name"]),
        "description": str(payload.get("description", "")),
        "excel": str(payload["excel"]),
        "seeds": norm_seeds,
        "params": deepcopy(params),
        "profile_path": str(profile_path),
    }


def load_variant_overrides(path: str | Path | None) -> tuple[str | None, dict[str, Any]]:
    if not path:
        return None, {}
    variant_path = Path(path).expanduser().resolve()
    payload = json.loads(variant_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Variant config invalida: se esperaba un objeto JSON")

    excel_override = payload.get("excel")
    if excel_override is not None:
        excel_override = str(excel_override)

    raw_params: dict[str, Any]
    if isinstance(payload.get("params"), dict):
        raw_params = dict(payload["params"])
    else:
        raw_params = dict(payload)

    raw_params.pop("excel", None)
    raw_params.pop("schema_version", None)
    raw_params.pop("profile_name", None)
    raw_params.pop("description", None)
    raw_params.pop("seeds", None)

    overrides: dict[str, Any] = {}
    for key, value in raw_params.items():
        normalized = _normalize_param_key(str(key))
        overrides[normalized] = value

    return excel_override, overrides


def apply_param_overrides(base_params: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base_params)
    for key, value in overrides.items():
        if key not in REQUIRED_PARAM_KEYS:
            raise ValueError(
                "Override invalido: parametro desconocido/no usado por run_simulation "
                f"'{key}'"
            )
        out[key] = value
    return out


def build_run_simulation_kwargs(
    *,
    params: dict[str, Any],
    excel_path: str,
    out_path: Path,
    seed: int,
    dump_placements_path: Path,
) -> dict[str, Any]:
    kwargs = dict(params)
    kwargs.update(
        {
            "excel_path": str(excel_path),
            "out_path": str(out_path),
            "episode_seed": int(seed),
            "dump_placements_path": str(dump_placements_path),
        }
    )

    unknown_kwargs = sorted(set(kwargs.keys()) - RUN_SIMULATION_PARAM_KEYS)
    if unknown_kwargs:
        raise ValueError(
            "Harness invalido: se intentaron pasar parametros que run_simulation no acepta: "
            f"{unknown_kwargs}"
        )

    missing_required_args = sorted(
        name
        for name, param in RUN_SIMULATION_SIGNATURE.parameters.items()
        if param.default is inspect._empty and name not in kwargs
    )
    if missing_required_args:
        raise ValueError(
            "Harness invalido: faltan argumentos requeridos por run_simulation: "
            f"{missing_required_args}"
        )

    return kwargs


def _extract_placement_sequence(dump_path: Path, *, forced_destination: int | None) -> list[dict[str, Any]]:
    if not dump_path.exists():
        return []

    payload = json.loads(dump_path.read_text(encoding="utf-8"))
    pallets = payload.get("pallets", {})
    if not isinstance(pallets, dict):
        return []

    if forced_destination is not None:
        seq = pallets.get(str(int(forced_destination)))
        if isinstance(seq, list):
            return [x for x in seq if isinstance(x, dict)]

    sortable_keys: list[tuple[int, str]] = []
    for key in pallets.keys():
        try:
            sortable_keys.append((int(key), str(key)))
        except Exception:
            sortable_keys.append((1_000_000_000, str(key)))

    for _, key in sorted(sortable_keys):
        seq = pallets.get(key)
        if isinstance(seq, list) and seq:
            return [x for x in seq if isinstance(x, dict)]

    return []


def _first_steps_from_placements(
    dump_path: Path,
    *,
    forced_destination: int | None,
) -> tuple[int | None, int | None]:
    seq = _extract_placement_sequence(dump_path, forced_destination=forced_destination)

    first_stack_step: int | None = None
    first_stand_hw_step: int | None = None

    for item in seq:
        step_index = _safe_int(item.get("step_index"))
        if step_index is None:
            continue

        z_mm = _safe_int(item.get("z_mm"))
        layer_id = _safe_int(item.get("layer_id"))
        if first_stack_step is None and ((z_mm is not None and z_mm > 0) or (layer_id is not None and layer_id > 0)):
            first_stack_step = step_index

        family = str(item.get("orientation_family", "")).strip().lower()
        if first_stand_hw_step is None and family == "stand_hw":
            first_stand_hw_step = step_index

        if first_stack_step is not None and first_stand_hw_step is not None:
            break

    return first_stack_step, first_stand_hw_step


def _select_first_pallet_monotonicity(
    pallet_kpis: dict[str, Any],
    *,
    forced_destination: int | None,
) -> dict[str, Any]:
    by_dest = pallet_kpis.get("layer_monotonicity_first_pallet_by_dest", {})
    if isinstance(by_dest, dict):
        if forced_destination is not None:
            if forced_destination in by_dest and isinstance(by_dest[forced_destination], dict):
                return dict(by_dest[forced_destination])
            key = str(int(forced_destination))
            if key in by_dest and isinstance(by_dest[key], dict):
                return dict(by_dest[key])

        sortable_keys: list[tuple[int, str]] = []
        for key in by_dest.keys():
            try:
                sortable_keys.append((int(key), str(key)))
            except Exception:
                sortable_keys.append((1_000_000_000, str(key)))
        for _, key in sorted(sortable_keys):
            raw = by_dest.get(key)
            if isinstance(raw, dict):
                return dict(raw)

    top_level = pallet_kpis.get("layer_monotonicity_first_pallet")
    if isinstance(top_level, dict):
        return dict(top_level)

    return {}


def _select_first_pallet_top_access(
    pallet_kpis: dict[str, Any],
    *,
    forced_destination: int | None,
) -> dict[str, Any]:
    by_dest = pallet_kpis.get("top_access_first_pallet_by_dest", {})
    if isinstance(by_dest, dict):
        if forced_destination is not None:
            if forced_destination in by_dest and isinstance(by_dest[forced_destination], dict):
                return dict(by_dest[forced_destination])
            key = str(int(forced_destination))
            if key in by_dest and isinstance(by_dest[key], dict):
                return dict(by_dest[key])

        sortable_keys: list[tuple[int, str]] = []
        for key in by_dest.keys():
            try:
                sortable_keys.append((int(key), str(key)))
            except Exception:
                sortable_keys.append((1_000_000_000, str(key)))
        for _, key in sorted(sortable_keys):
            raw = by_dest.get(key)
            if isinstance(raw, dict):
                return dict(raw)

    top_level = pallet_kpis.get("top_access_first_pallet")
    if isinstance(top_level, dict):
        return dict(top_level)

    return {}


def _coerce_bbox(value: Any) -> tuple[int, int, int, int] | None:
    if isinstance(value, dict):
        keys = ("x0_mm", "y0_mm", "x1_mm", "y1_mm")
        if all(k in value for k in keys):
            vals = [_safe_int(value.get(k)) for k in keys]
            if all(v is not None for v in vals):
                x0, y0, x1, y1 = (int(vals[0]), int(vals[1]), int(vals[2]), int(vals[3]))
                if x1 > x0 and y1 > y0:
                    return (x0, y0, x1, y1)
    if isinstance(value, (list, tuple)) and len(value) == 4:
        vals = [_safe_int(v) for v in value]
        if all(v is not None for v in vals):
            x0, y0, x1, y1 = (int(vals[0]), int(vals[1]), int(vals[2]), int(vals[3]))
            if x1 > x0 and y1 > y0:
                return (x0, y0, x1, y1)
    return None


def _bbox_union(a: tuple[int, int, int, int] | None, b: tuple[int, int, int, int] | None) -> tuple[int, int, int, int] | None:
    if a is None:
        return b
    if b is None:
        return a
    return (
        int(min(a[0], b[0])),
        int(min(a[1], b[1])),
        int(max(a[2], b[2])),
        int(max(a[3], b[3])),
    )


def _criticality_key(item: dict[str, Any]) -> tuple[float, int, int]:
    severity = float(item.get("marginal_severity_score", 0.0) or 0.0)
    cls = str(item.get("accessibility_class", "accessible"))
    cls_weight = 1 if cls == "blocked" else 0
    step_idx = _safe_int(item.get("step_index")) or 0
    return (severity, cls_weight, step_idx)


def _select_top_critical_placements(top_access: dict[str, Any], *, top_k: int) -> list[dict[str, Any]]:
    limit = max(1, int(top_k))
    per_placement = top_access.get("per_placement")
    if isinstance(per_placement, list):
        issue_rows = [
            item
            for item in per_placement
            if isinstance(item, dict)
            and str(item.get("accessibility_class", "accessible")) in ("blocked", "marginal")
        ]
        ranked = sorted(issue_rows, key=_criticality_key, reverse=True)
        return ranked[:limit]

    critical = top_access.get("critical_placements")
    if isinstance(critical, list):
        ranked = sorted([item for item in critical if isinstance(item, dict)], key=_criticality_key, reverse=True)
        return ranked[:limit]
    return []


def _summarize_top_critical_placements(top_critical: list[dict[str, Any]]) -> dict[str, Any]:
    steps = [_safe_int(item.get("step_index")) for item in top_critical]
    top_critical_steps = [int(v) for v in steps if v is not None]
    top_critical_orientations = [
        str(item.get("orientation_family", "planar"))
        for item in top_critical
    ]
    top_critical_reasons = []
    for item in top_critical:
        reason = item.get("throat_source_reason")
        if reason in (None, ""):
            reason = item.get("blocked_reason_exact")
        if reason in (None, ""):
            reason = "unspecified"
        top_critical_reasons.append(str(reason))

    blocker_counts: dict[tuple[int | None, str], int] = {}
    for item in top_critical:
        local_blockers = item.get("local_blockers", [])
        if not isinstance(local_blockers, list):
            continue
        for blocker in local_blockers:
            if not isinstance(blocker, dict):
                continue
            blocker_step = _safe_int(blocker.get("step_index"))
            blocker_orientation = str(blocker.get("orientation_family", "planar"))
            key = (blocker_step, blocker_orientation)
            blocker_counts[key] = int(blocker_counts.get(key, 0) + 1)

    recurrent_blockers = [
        {
            "blocker_step": (None if step is None else int(step)),
            "blocker_orientation": str(orientation),
            "hits": int(hits),
        }
        for (step, orientation), hits in sorted(
            blocker_counts.items(),
            key=lambda item: (
                -int(item[1]),
                -(int(item[0][0]) if item[0][0] is not None else -1),
                str(item[0][1]),
            ),
        )
    ]

    return {
        "top_critical_steps": top_critical_steps,
        "top_critical_orientations": top_critical_orientations,
        "top_critical_reasons": top_critical_reasons,
        "recurrent_blockers": recurrent_blockers,
    }


def _sanitize_name_token(value: Any) -> str:
    raw = str(value if value is not None else "na").strip().lower()
    out = []
    for ch in raw:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9"):
            out.append(ch)
        else:
            out.append("_")
    clean = "".join(out).strip("_")
    return clean or "na"


def _draw_rect_ascii(
    canvas: list[list[str]],
    *,
    rect: tuple[int, int, int, int] | None,
    view: tuple[int, int, int, int],
    char: str,
    fill: bool,
) -> None:
    if rect is None:
        return
    vx0, vy0, vx1, vy1 = view
    width = len(canvas[0]) if canvas else 0
    height = len(canvas)
    if width <= 0 or height <= 0 or vx1 <= vx0 or vy1 <= vy0:
        return

    def _x_to_col(x_mm: int) -> int:
        ratio = (float(x_mm) - float(vx0)) / float(max(1, vx1 - vx0))
        ratio = max(0.0, min(1.0, ratio))
        return int(round(ratio * float(width - 1)))

    def _y_to_row(y_mm: int) -> int:
        ratio = (float(y_mm) - float(vy0)) / float(max(1, vy1 - vy0))
        ratio = max(0.0, min(1.0, ratio))
        return int(round(ratio * float(height - 1)))

    x0, y0, x1, y1 = rect
    c0 = _x_to_col(int(x0))
    c1 = _x_to_col(int(x1))
    r0 = _y_to_row(int(y0))
    r1 = _y_to_row(int(y1))
    lo_c, hi_c = sorted((c0, c1))
    lo_r, hi_r = sorted((r0, r1))

    if fill:
        for row in range(lo_r, hi_r + 1):
            for col in range(lo_c, hi_c + 1):
                canvas[row][col] = char
        return

    for col in range(lo_c, hi_c + 1):
        canvas[lo_r][col] = char
        canvas[hi_r][col] = char
    for row in range(lo_r, hi_r + 1):
        canvas[row][lo_c] = char
        canvas[row][hi_c] = char


def _render_local_top_view_ascii(placement: dict[str, Any]) -> str:
    target = _coerce_bbox(placement.get("target_footprint_mm"))
    hard = _coerce_bbox(placement.get("target_hard_prism_mm"))
    throat = _coerce_bbox(placement.get("entry_throat_bbox_mm"))
    zone = _coerce_bbox(placement.get("analysis_zone_mm"))
    pallet = _coerce_bbox(placement.get("pallet_bounds_mm"))

    blockers_raw = placement.get("local_blockers", [])
    blockers: list[tuple[int, int, int, int]] = []
    if isinstance(blockers_raw, list):
        for item in blockers_raw:
            if not isinstance(item, dict):
                continue
            bbox = _coerce_bbox(item.get("blocker_local_footprint_mm")) or _coerce_bbox(item.get("blocker_bbox_mm"))
            if bbox is not None:
                blockers.append(bbox)

    view = zone
    if view is None:
        for bbox in [pallet, throat, hard, target, *blockers]:
            view = _bbox_union(view, bbox)
    if view is None:
        return "No local geometry available"

    pad_mm = 10
    view = (
        int(view[0] - pad_mm),
        int(view[1] - pad_mm),
        int(view[2] + pad_mm),
        int(view[3] + pad_mm),
    )

    canvas = [
        [" " for _ in range(DEFAULT_EXPLAINABILITY_CANVAS_COLS)]
        for _ in range(DEFAULT_EXPLAINABILITY_CANVAS_ROWS)
    ]
    _draw_rect_ascii(canvas, rect=pallet, view=view, char="P", fill=False)
    _draw_rect_ascii(canvas, rect=zone, view=view, char=".", fill=False)
    _draw_rect_ascii(canvas, rect=hard, view=view, char="H", fill=False)
    _draw_rect_ascii(canvas, rect=throat, view=view, char="G", fill=False)
    _draw_rect_ascii(canvas, rect=target, view=view, char="T", fill=True)

    blocker_chars = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for idx, blocker in enumerate(blockers):
        marker = blocker_chars[idx % len(blocker_chars)]
        _draw_rect_ascii(canvas, rect=blocker, view=view, char=marker, fill=True)

    lines = ["".join(row) for row in canvas]
    legend = [
        "",
        f"view_bbox_mm={view}",
        f"pallet_bounds_mm={pallet}",
        f"analysis_zone_mm={zone}",
        f"target_footprint_mm={target}",
        f"hard_prism_mm={hard}",
        f"entry_throat_bbox_mm={throat}",
        "legend: P=pallet border, .=analysis zone, H=hard prism, G=throat bbox, T=target, 1..=blockers",
    ]
    return "\n".join(lines + legend)


def _export_top_access_explainability(
    *,
    run_label: str,
    seed: int,
    run_dir: Path,
    top_access: dict[str, Any],
    top_k: int,
) -> dict[str, Any]:
    top_critical = _select_top_critical_placements(top_access, top_k=top_k)
    export_dir = run_dir / f"seed_{int(seed)}_top_access_explainability"
    export_dir.mkdir(parents=True, exist_ok=True)
    exports: list[dict[str, Any]] = []
    for rank, item in enumerate(top_critical, start=1):
        step_idx = _safe_int(item.get("step_index"))
        orientation = str(item.get("orientation_family", "planar"))
        step_token = "na" if step_idx is None else str(int(step_idx))
        orientation_token = _sanitize_name_token(orientation)
        stem = f"critical_{rank:02d}_step_{step_token}_{orientation_token}"

        export_payload = {
            "run_label": str(run_label),
            "seed": int(seed),
            "rank": int(rank),
            "step_index": (None if step_idx is None else int(step_idx)),
            "orientation_family": str(orientation),
            "accessibility_class": str(item.get("accessibility_class", "accessible")),
            "marginal_severity_score": float(item.get("marginal_severity_score", 0.0) or 0.0),
            "blocked_reason_exact": item.get("blocked_reason_exact"),
            "throat_source_reason": item.get("throat_source_reason"),
            "limiting_axis": item.get("limiting_axis"),
            "limiting_clearance_mm": _safe_int(item.get("limiting_clearance_mm")),
            "nearest_blocker_step": _safe_int(item.get("nearest_blocker_step")),
            "nearest_blocker_orientation": item.get("nearest_blocker_orientation"),
            "pallet_bounds_mm": item.get("pallet_bounds_mm"),
            "analysis_zone_mm": item.get("analysis_zone_mm"),
            "target_footprint_mm": item.get("target_footprint_mm"),
            "target_hard_prism_mm": item.get("target_hard_prism_mm"),
            "entry_throat_bbox_mm": item.get("entry_throat_bbox_mm"),
            "entry_throat_clearances_mm": item.get("entry_throat_clearances_mm"),
            "local_blockers": [
                blocker
                for blocker in item.get("local_blockers", [])
                if isinstance(blocker, dict)
            ]
            if isinstance(item.get("local_blockers", []), list)
            else [],
        }

        json_path = export_dir / f"{stem}.json"
        json_path.write_text(json.dumps(export_payload, indent=2, ensure_ascii=True), encoding="utf-8")

        ascii_path = export_dir / f"{stem}.txt"
        ascii_path.write_text(_render_local_top_view_ascii(item), encoding="utf-8")

        exports.append(
            {
                "rank": int(rank),
                "step_index": (None if step_idx is None else int(step_idx)),
                "orientation_family": str(orientation),
                "json": str(json_path),
                "ascii": str(ascii_path),
            }
        )

    return {
        "enabled": True,
        "top_k": int(max(1, int(top_k))),
        "export_dir": str(export_dir),
        "exports_count": int(len(exports)),
        "exports": exports,
    }


def run_seed(
    *,
    run_label: str,
    seed: int,
    excel_path: str,
    params: dict[str, Any],
    effective_config_hash: str,
    run_dir: Path,
    export_top_access_explainability: bool = False,
    explainability_top_k: int = DEFAULT_EXPLAINABILITY_TOP_K,
) -> SeedSummary:
    run_dir.mkdir(parents=True, exist_ok=True)
    out_json_path = run_dir / f"seed_{int(seed)}.json"
    placements_path = run_dir / f"seed_{int(seed)}_placements.json"

    kwargs = build_run_simulation_kwargs(
        params=params,
        excel_path=excel_path,
        out_path=out_json_path,
        seed=int(seed),
        dump_placements_path=placements_path,
    )

    payload = run_simulation(**kwargs)

    metrics = payload.get("metrics", {}) if isinstance(payload, dict) else {}
    pallet_kpis = metrics.get("pallet_kpis", {}) if isinstance(metrics, dict) else {}

    processed_boxes = _safe_int(metrics.get("processed_boxes")) if isinstance(metrics, dict) else None
    stand_hw_used_total = _safe_int(pallet_kpis.get("stand_hw_used_total")) if isinstance(pallet_kpis, dict) else None
    hard_floor_stand_total = (
        _safe_int(pallet_kpis.get("hard_floor_phase_stand_hw_chosen_total"))
        if isinstance(pallet_kpis, dict)
        else None
    )

    forced_destination = _safe_int(params.get("force_destination"))
    first_stack_step, first_stand_hw_step = _first_steps_from_placements(
        placements_path,
        forced_destination=forced_destination,
    )

    mono = _select_first_pallet_monotonicity(
        pallet_kpis if isinstance(pallet_kpis, dict) else {},
        forced_destination=forced_destination,
    )
    top_access = _select_first_pallet_top_access(
        pallet_kpis if isinstance(pallet_kpis, dict) else {},
        forced_destination=forced_destination,
    )
    top_access = top_access if isinstance(top_access, dict) else {}

    max_z_series = mono.get("max_z_seen_so_far_by_step", [])
    max_z_seen_last_mm = None
    if isinstance(max_z_series, list) and max_z_series:
        max_z_seen_last_mm = _safe_int(max_z_series[-1])

    active_layers_series = mono.get("active_layers_over_time", [])
    active_layers_peak = None
    if isinstance(active_layers_series, list) and active_layers_series:
        active_layers_peak = max((_safe_int(v) or 0) for v in active_layers_series)

    step_trace_relevant = mono.get("step_trace_relevant", [])
    clean_trace = (
        [item for item in step_trace_relevant if isinstance(item, dict)]
        if isinstance(step_trace_relevant, list)
        else []
    )
    critical_placements = top_access.get("critical_placements", [])
    clean_critical_placements = (
        [item for item in critical_placements if isinstance(item, dict)]
        if isinstance(critical_placements, list)
        else []
    )
    top_critical = _select_top_critical_placements(
        top_access,
        top_k=max(1, int(explainability_top_k)),
    )
    top_critical_summary = _summarize_top_critical_placements(top_critical)
    explainability_exports = {
        "enabled": False,
        "top_k": int(max(1, int(explainability_top_k))),
        "export_dir": None,
        "exports_count": 0,
        "exports": [],
    }
    if export_top_access_explainability:
        explainability_exports = _export_top_access_explainability(
            run_label=run_label,
            seed=int(seed),
            run_dir=run_dir,
            top_access=top_access,
            top_k=max(1, int(explainability_top_k)),
        )

    return SeedSummary(
        run_label=run_label,
        seed=int(seed),
        processed_boxes=processed_boxes,
        first_stack_step=first_stack_step,
        first_stand_hw_step=first_stand_hw_step,
        stand_hw_used_total=stand_hw_used_total,
        hard_floor_phase_stand_hw_chosen_total=hard_floor_stand_total,
        max_z_seen_last_mm=max_z_seen_last_mm,
        lower_layer_reentry_count=_safe_int(mono.get("lower_layer_reentry_count")),
        lower_layer_reentry_total_drop_mm=_safe_int(mono.get("lower_layer_reentry_total_drop_mm")),
        lower_layer_reentry_max_drop_mm=_safe_int(mono.get("lower_layer_reentry_max_drop_mm")),
        lower_layer_reentry_mean_drop_mm=_safe_float(mono.get("lower_layer_reentry_mean_drop_mm")),
        monotonic_stack_rate=_safe_float(mono.get("monotonic_stack_rate")),
        placements_below_current_top_band_after_opening_next_band=_safe_int(
            mono.get("placements_below_current_top_band_after_opening_next_band")
        ),
        layer_closure_score=_safe_float(mono.get("layer_closure_score")),
        layer_fill_homogeneity_score=_safe_float(mono.get("layer_fill_homogeneity_score")),
        z_band_fill_homogeneity_score=_safe_float(mono.get("z_band_fill_homogeneity_score")),
        active_layers_peak=active_layers_peak,
        blocked_count=_safe_int(top_access.get("blocked_count")),
        marginal_count=_safe_int(top_access.get("marginal_count")),
        severe_marginal_count=_safe_int(top_access.get("severe_marginal_count")),
        blocked_stand_hw=_safe_int(top_access.get("blocked_stand_hw")),
        marginal_stand_hw=_safe_int(top_access.get("marginal_stand_hw")),
        severe_marginal_stand_hw=_safe_int(top_access.get("severe_marginal_stand_hw")),
        first_blocked_step=_safe_int(top_access.get("first_blocked_step")),
        first_severe_marginal_step=_safe_int(top_access.get("first_severe_marginal_step")),
        issues_concentrated_at_end=(
            bool(top_access.get("issues_concentrated_at_end"))
            if top_access.get("issues_concentrated_at_end") is not None
            else None
        ),
        critical_placements_json=json.dumps(clean_critical_placements, ensure_ascii=True),
        top_critical_steps_json=json.dumps(top_critical_summary.get("top_critical_steps", []), ensure_ascii=True),
        top_critical_orientations_json=json.dumps(
            top_critical_summary.get("top_critical_orientations", []),
            ensure_ascii=True,
        ),
        top_critical_reasons_json=json.dumps(top_critical_summary.get("top_critical_reasons", []), ensure_ascii=True),
        recurrent_blockers_json=json.dumps(top_critical_summary.get("recurrent_blockers", []), ensure_ascii=True),
        explainability_exports_json=json.dumps(explainability_exports, ensure_ascii=True),
        layer_band_mm=_safe_int(mono.get("layer_band_mm")),
        layer_band_fill_progress_json=json.dumps(mono.get("layer_band_fill_progress", []), ensure_ascii=True),
        active_layers_over_time_json=json.dumps(
            active_layers_series if isinstance(active_layers_series, list) else [],
            ensure_ascii=True,
        ),
        z_band_fill_share_json=json.dumps(mono.get("z_band_fill_share", {}), ensure_ascii=True),
        layer_fill_share_json=json.dumps(mono.get("layer_fill_share", {}), ensure_ascii=True),
        step_trace_relevant_json=json.dumps(clean_trace, ensure_ascii=True),
        output_json=str(out_json_path),
        placements_json=str(placements_path),
        effective_config_hash=effective_config_hash,
    )


def _mean(values: list[int | None]) -> float | None:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / float(len(nums))


def _aggregate_rows(rows: list[SeedSummary]) -> dict[str, dict[str, Any]]:
    by_label: dict[str, list[SeedSummary]] = {}
    for row in rows:
        by_label.setdefault(row.run_label, []).append(row)

    out: dict[str, dict[str, Any]] = {}
    for label, values in by_label.items():
        values_sorted = sorted(values, key=lambda r: r.seed)
        out[label] = {
            "seed_count": len(values_sorted),
            "seeds": [int(v.seed) for v in values_sorted],
            "processed_boxes_mean": _mean([v.processed_boxes for v in values_sorted]),
            "first_stack_step_mean": _mean([v.first_stack_step for v in values_sorted]),
            "first_stand_hw_step_mean": _mean([v.first_stand_hw_step for v in values_sorted]),
            "stand_hw_used_total_mean": _mean([v.stand_hw_used_total for v in values_sorted]),
            "hard_floor_phase_stand_hw_chosen_total_mean": _mean(
                [v.hard_floor_phase_stand_hw_chosen_total for v in values_sorted]
            ),
            "lower_layer_reentry_count_mean": _mean([v.lower_layer_reentry_count for v in values_sorted]),
            "lower_layer_reentry_max_drop_mm_mean": _mean([v.lower_layer_reentry_max_drop_mm for v in values_sorted]),
            "lower_layer_reentry_mean_drop_mm_mean": _mean([v.lower_layer_reentry_mean_drop_mm for v in values_sorted]),
            "monotonic_stack_rate_mean": _mean([v.monotonic_stack_rate for v in values_sorted]),
            "placements_below_current_top_band_after_opening_next_band_mean": _mean(
                [v.placements_below_current_top_band_after_opening_next_band for v in values_sorted]
            ),
            "layer_closure_score_mean": _mean([v.layer_closure_score for v in values_sorted]),
            "layer_fill_homogeneity_score_mean": _mean([v.layer_fill_homogeneity_score for v in values_sorted]),
            "z_band_fill_homogeneity_score_mean": _mean([v.z_band_fill_homogeneity_score for v in values_sorted]),
            "active_layers_peak_mean": _mean([v.active_layers_peak for v in values_sorted]),
            "blocked_count_mean": _mean([v.blocked_count for v in values_sorted]),
            "marginal_count_mean": _mean([v.marginal_count for v in values_sorted]),
            "severe_marginal_count_mean": _mean([v.severe_marginal_count for v in values_sorted]),
            "blocked_stand_hw_mean": _mean([v.blocked_stand_hw for v in values_sorted]),
            "marginal_stand_hw_mean": _mean([v.marginal_stand_hw for v in values_sorted]),
            "severe_marginal_stand_hw_mean": _mean([v.severe_marginal_stand_hw for v in values_sorted]),
            "first_blocked_step_mean": _mean([v.first_blocked_step for v in values_sorted]),
            "first_severe_marginal_step_mean": _mean([v.first_severe_marginal_step for v in values_sorted]),
            "issues_concentrated_at_end_share": _mean(
                [
                    (
                        None
                        if v.issues_concentrated_at_end is None
                        else int(bool(v.issues_concentrated_at_end))
                    )
                    for v in values_sorted
                ]
            ),
            "processed_boxes_min": min(v.processed_boxes for v in values_sorted if v.processed_boxes is not None)
            if any(v.processed_boxes is not None for v in values_sorted)
            else None,
            "processed_boxes_max": max(v.processed_boxes for v in values_sorted if v.processed_boxes is not None)
            if any(v.processed_boxes is not None for v in values_sorted)
            else None,
        }

    return out


def _discriminative_status(rows: list[SeedSummary]) -> dict[str, dict[str, Any]]:
    by_label: dict[str, list[SeedSummary]] = {}
    for row in rows:
        by_label.setdefault(row.run_label, []).append(row)

    out: dict[str, dict[str, Any]] = {}
    for label, values in by_label.items():
        values_sorted = sorted(values, key=lambda r: r.seed)
        processed = [int(v.processed_boxes) for v in values_sorted if v.processed_boxes is not None]
        unique_processed = sorted(set(processed))
        is_flat = len(processed) >= 2 and len(unique_processed) <= 1
        out[label] = {
            "seed_count": len(values_sorted),
            "processed_boxes_observed": processed,
            "processed_boxes_unique": unique_processed,
            "processed_boxes_unique_count": len(unique_processed),
            "is_flat_processed_boxes": bool(is_flat),
            "is_discriminative_processed_boxes": bool(not is_flat and len(unique_processed) >= 2),
        }
    return out


def _default_outdir(
    *,
    profile_name: str,
    baseline_hash: str,
    variant_hash: str | None,
) -> Path:
    root = Path("out") / "benchmarks" / profile_name
    if variant_hash:
        run_id = f"cmp_{baseline_hash[:8]}_vs_{variant_hash[:8]}"
    else:
        run_id = f"baseline_{baseline_hash[:8]}"
    return (root / run_id).resolve()


def _print_summary_table(rows: list[SeedSummary]) -> None:
    headers = [
        "run",
        "seed",
        "processed_boxes",
        "first_stack_step",
        "first_stand_hw_step",
        "stand_hw_used_total",
        "hard_floor_phase_stand_hw_chosen_total",
        "blocked_count",
        "marginal_count",
        "severe_marginal_count",
        "blocked_stand_hw",
        "marginal_stand_hw",
        "severe_marginal_stand_hw",
        "first_blocked_step",
        "first_severe_marginal_step",
        "issues_concentrated_at_end",
    ]
    print(" | ".join(headers))
    print("-" * 170)
    for row in sorted(rows, key=lambda r: (r.run_label, r.seed)):
        print(
            " | ".join(
                [
                    str(row.run_label),
                    str(row.seed),
                    str(row.processed_boxes),
                    str(row.first_stack_step),
                    str(row.first_stand_hw_step),
                    str(row.stand_hw_used_total),
                    str(row.hard_floor_phase_stand_hw_chosen_total),
                    str(row.blocked_count),
                    str(row.marginal_count),
                    str(row.severe_marginal_count),
                    str(row.blocked_stand_hw),
                    str(row.marginal_stand_hw),
                    str(row.severe_marginal_stand_hw),
                    str(row.first_blocked_step),
                    str(row.first_severe_marginal_step),
                    str(row.issues_concentrated_at_end),
                ]
            )
        )

    print("\nTop critical placements by marginal_severity_score")
    for row in sorted(rows, key=lambda r: (r.run_label, r.seed)):
        try:
            critical = json.loads(row.critical_placements_json)
        except Exception:
            critical = []
        if not isinstance(critical, list):
            critical = []
        ranked = sorted(
            [item for item in critical if isinstance(item, dict)],
            key=lambda item: float(item.get("marginal_severity_score", 0.0) or 0.0),
            reverse=True,
        )[:3]
        summary = [
            {
                "step_index": _safe_int(item.get("step_index")),
                "orientation_family": str(item.get("orientation_family", "planar")),
                "accessibility_class": str(item.get("accessibility_class", "accessible")),
                "marginal_severity_score": round(float(item.get("marginal_severity_score", 0.0) or 0.0), 3),
            }
            for item in ranked
        ]
        print(f"{row.run_label} seed={row.seed}: {json.dumps(summary, ensure_ascii=True)}")
        try:
            top_steps = json.loads(row.top_critical_steps_json)
        except Exception:
            top_steps = []
        try:
            top_orientations = json.loads(row.top_critical_orientations_json)
        except Exception:
            top_orientations = []
        try:
            top_reasons = json.loads(row.top_critical_reasons_json)
        except Exception:
            top_reasons = []
        try:
            recurrent_blockers = json.loads(row.recurrent_blockers_json)
        except Exception:
            recurrent_blockers = []
        print(
            f"{row.run_label} seed={row.seed} explainability: "
            f"top_critical_steps={json.dumps(top_steps, ensure_ascii=True)} "
            f"top_critical_orientations={json.dumps(top_orientations, ensure_ascii=True)} "
            f"top_critical_reasons={json.dumps(top_reasons, ensure_ascii=True)} "
            f"recurrent_blockers={json.dumps(recurrent_blockers[:5], ensure_ascii=True)}"
        )


def run_benchmark(
    *,
    profile_path: str | Path,
    outdir: str | Path | None = None,
    variant_config_path: str | Path | None = None,
    set_overrides: list[str] | None = None,
    seeds_override: list[int] | None = None,
    variant_name: str = "variant",
    export_top_access_explainability: bool = False,
    explainability_top_k: int = DEFAULT_EXPLAINABILITY_TOP_K,
) -> dict[str, Any]:
    profile = load_profile(profile_path)

    baseline_excel = str(profile["excel"])
    baseline_params = deepcopy(profile["params"])
    seeds = [int(s) for s in (seeds_override if seeds_override else profile["seeds"])]

    variant_requested = bool(variant_config_path or (set_overrides and len(set_overrides) > 0))

    variant_excel_override, variant_file_overrides = load_variant_overrides(variant_config_path)
    cli_overrides = _parse_set_overrides(set_overrides or [])

    merged_variant_overrides = dict(variant_file_overrides)
    merged_variant_overrides.update(cli_overrides)

    variant_excel = str(variant_excel_override) if variant_excel_override else baseline_excel
    variant_params = apply_param_overrides(baseline_params, merged_variant_overrides)

    baseline_missing_params = sorted(REQUIRED_PARAM_KEYS - set(baseline_params.keys()))
    baseline_unknown_params = sorted(set(baseline_params.keys()) - REQUIRED_PARAM_KEYS)
    if baseline_missing_params:
        raise ValueError(
            "Perfil baseline invalido: faltan parametros requeridos para run_simulation: "
            f"{baseline_missing_params}"
        )
    if baseline_unknown_params:
        raise ValueError(
            "Perfil baseline invalido: contiene parametros no usados por run_simulation: "
            f"{baseline_unknown_params}"
        )

    baseline_hash = _stable_hash(
        {
            "excel": baseline_excel,
            "params": baseline_params,
            "seeds": seeds,
        }
    )

    variant_hash: str | None = None
    if variant_requested:
        variant_hash = _stable_hash(
            {
                "excel": variant_excel,
                "params": variant_params,
                "seeds": seeds,
            }
        )

    if outdir:
        run_output_dir = Path(outdir).expanduser().resolve()
    else:
        run_output_dir = _default_outdir(
            profile_name=str(profile["profile_name"]),
            baseline_hash=baseline_hash,
            variant_hash=variant_hash,
        )
    run_output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[SeedSummary] = []
    runs: list[tuple[str, str, dict[str, Any], str]] = [
        ("baseline", baseline_excel, baseline_params, baseline_hash),
    ]
    if variant_requested:
        runs.append((str(variant_name), variant_excel, variant_params, str(variant_hash)))

    for run_label, excel_path, params, effective_hash in runs:
        for seed in seeds:
            row = run_seed(
                run_label=run_label,
                seed=int(seed),
                excel_path=excel_path,
                params=params,
                effective_config_hash=str(effective_hash),
                run_dir=run_output_dir / run_label,
                export_top_access_explainability=bool(export_top_access_explainability),
                explainability_top_k=max(1, int(explainability_top_k)),
            )
            rows.append(row)

    rows_sorted = sorted(rows, key=lambda r: (r.run_label, r.seed))

    csv_path = run_output_dir / "summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(rows_sorted[0]).keys()))
        writer.writeheader()
        for row in rows_sorted:
            writer.writerow(asdict(row))

    aggregates = _aggregate_rows(rows_sorted)
    discriminative = _discriminative_status(rows_sorted)
    baseline_flat = bool(discriminative.get("baseline", {}).get("is_flat_processed_boxes"))

    timestamp_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    fingerprint = {
        "git_branch": _git(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "git_commit_sha": _git(["git", "rev-parse", "HEAD"]),
        "profile_path": str(Path(profile["profile_path"])),
        "seeds": [int(s) for s in seeds],
        "timestamp_utc": timestamp_utc,
        "python_version": sys.version.split()[0],
        "effective_config_hashes": {
            "baseline": baseline_hash,
            str(variant_name): variant_hash,
        },
    }

    summary_payload = {
        "schema_version": 1,
        "profile_name": profile["profile_name"],
        "profile_description": profile.get("description", ""),
        "fingerprint": fingerprint,
        "runs": {
            "baseline": {
                "excel": baseline_excel,
                "effective_config_hash": baseline_hash,
                "overrides": {},
                "effective_params": baseline_params,
            },
            str(variant_name): {
                "excel": variant_excel,
                "effective_config_hash": variant_hash,
                "overrides": merged_variant_overrides,
                "effective_params": variant_params,
            }
            if variant_requested
            else None,
        },
        "param_contract": {
            "run_simulation_param_keys": sorted(RUN_SIMULATION_PARAM_KEYS),
            "profile_required_param_keys": sorted(REQUIRED_PARAM_KEYS),
            "profile_param_keys": sorted(profile["params"].keys()),
            "missing_required_in_profile": baseline_missing_params,
            "unknown_in_profile": baseline_unknown_params,
            "variant_override_keys": sorted(merged_variant_overrides.keys()),
        },
        "top_access_explainability": {
            "enabled": bool(export_top_access_explainability),
            "top_k": int(max(1, int(explainability_top_k))),
        },
        "aggregates": aggregates,
        "discriminative": discriminative,
        "rows": [asdict(r) for r in rows_sorted],
        "files": {
            "summary_csv": str(csv_path),
            "summary_json": str(run_output_dir / "summary.json"),
        },
    }

    summary_json_path = run_output_dir / "summary.json"
    summary_json_path.write_text(
        json.dumps(summary_payload, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )

    _print_summary_table(rows_sorted)
    print(f"[benchmark] profile={profile['profile_name']} seeds={seeds}")
    print(f"[benchmark] outdir={run_output_dir}")
    print(f"[benchmark] summary_csv={csv_path}")
    print(f"[benchmark] summary_json={summary_json_path}")
    if baseline_flat:
        print(
            "[benchmark][warning] baseline flat across seeds in processed_boxes; "
            "benchmark can be non-discriminative."
        )

    return summary_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run canonical frozen 1-pallet benchmark profile.")
    parser.add_argument(
        "--profile",
        default=str(DEFAULT_PROFILE_PATH),
        help=f"Path to canonical benchmark profile (default: {DEFAULT_PROFILE_PATH}).",
    )
    parser.add_argument(
        "--outdir",
        default=None,
        help="Output directory. If omitted, a deterministic folder is created under out/benchmarks/.",
    )
    parser.add_argument(
        "--variant-config",
        default=None,
        help="Optional JSON file with variant overrides.",
    )
    parser.add_argument(
        "--set",
        nargs="*",
        default=[],
        help="Variant overrides as key=value pairs (e.g. --set lookahead_k=10 micro_width=60).",
    )
    parser.add_argument(
        "--variant-name",
        default="variant",
        help="Label used in summary for the variant run.",
    )
    parser.add_argument(
        "--seeds",
        nargs="*",
        type=int,
        default=None,
        help="Optional seed override list. Defaults to profile seeds.",
    )
    parser.add_argument(
        "--export-top-access-explainability",
        action="store_true",
        help="Export JSON+ASCII local explainability for top critical placements per seed.",
    )
    parser.add_argument(
        "--explainability-top-k",
        type=int,
        default=DEFAULT_EXPLAINABILITY_TOP_K,
        help=f"Top-K critical placements exported per seed (default: {DEFAULT_EXPLAINABILITY_TOP_K}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_benchmark(
        profile_path=args.profile,
        outdir=args.outdir,
        variant_config_path=args.variant_config,
        set_overrides=list(args.set or []),
        seeds_override=(list(args.seeds) if args.seeds else None),
        variant_name=str(args.variant_name),
        export_top_access_explainability=bool(args.export_top_access_explainability),
        explainability_top_k=max(1, int(args.explainability_top_k)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
