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
    throughput_final: int | None
    first_stack_step: int | None
    first_upper_layer_open_step: int | None
    first_stand_hw_step: int | None
    first_reentry_step: int | None
    stand_hw_used_total: int | None
    hard_floor_phase_stand_hw_chosen_total: int | None
    max_z_seen_last_mm: int | None
    reentries_total: int | None
    lower_layer_reentry_count: int | None
    lower_layer_reentry_total_drop_mm: int | None
    lower_layer_reentry_max_drop_mm: int | None
    lower_layer_reentry_mean_drop_mm: float | None
    blocked_upper_layer_open_attempts: int | None
    active_layer_exhaustion_events: int | None
    monotonic_stack_rate: float | None
    placements_below_current_top_band_after_opening_next_band: int | None
    layer_closure_score: float | None
    layer_fill_homogeneity_score: float | None
    z_band_fill_homogeneity_score: float | None
    active_layers_peak: int | None
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


def run_seed(
    *,
    run_label: str,
    seed: int,
    excel_path: str,
    params: dict[str, Any],
    effective_config_hash: str,
    run_dir: Path,
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
    throughput_final = processed_boxes
    stand_hw_used_total = _safe_int(pallet_kpis.get("stand_hw_used_total")) if isinstance(pallet_kpis, dict) else None
    blocked_upper_layer_open_attempts = (
        _safe_int(pallet_kpis.get("blocked_upper_layer_open_attempts"))
        if isinstance(pallet_kpis, dict)
        else None
    )
    active_layer_exhaustion_events = (
        _safe_int(pallet_kpis.get("active_layer_exhaustion_events"))
        if isinstance(pallet_kpis, dict)
        else None
    )
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

    return SeedSummary(
        run_label=run_label,
        seed=int(seed),
        processed_boxes=processed_boxes,
        throughput_final=throughput_final,
        first_stack_step=first_stack_step,
        first_upper_layer_open_step=_safe_int(mono.get("first_upper_layer_open_step")),
        first_stand_hw_step=first_stand_hw_step,
        first_reentry_step=_safe_int(mono.get("first_reentry_step")),
        stand_hw_used_total=stand_hw_used_total,
        hard_floor_phase_stand_hw_chosen_total=hard_floor_stand_total,
        max_z_seen_last_mm=max_z_seen_last_mm,
        reentries_total=_safe_int(mono.get("reentries_total")),
        lower_layer_reentry_count=_safe_int(mono.get("lower_layer_reentry_count")),
        lower_layer_reentry_total_drop_mm=_safe_int(mono.get("lower_layer_reentry_total_drop_mm")),
        lower_layer_reentry_max_drop_mm=_safe_int(mono.get("lower_layer_reentry_max_drop_mm")),
        lower_layer_reentry_mean_drop_mm=_safe_float(mono.get("lower_layer_reentry_mean_drop_mm")),
        blocked_upper_layer_open_attempts=blocked_upper_layer_open_attempts,
        active_layer_exhaustion_events=active_layer_exhaustion_events,
        monotonic_stack_rate=_safe_float(mono.get("monotonic_stack_rate")),
        placements_below_current_top_band_after_opening_next_band=_safe_int(
            mono.get("placements_below_current_top_band_after_opening_next_band")
        ),
        layer_closure_score=_safe_float(mono.get("layer_closure_score")),
        layer_fill_homogeneity_score=_safe_float(mono.get("layer_fill_homogeneity_score")),
        z_band_fill_homogeneity_score=_safe_float(mono.get("z_band_fill_homogeneity_score")),
        active_layers_peak=active_layers_peak,
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
            "throughput_final_mean": _mean([v.throughput_final for v in values_sorted]),
            "first_stack_step_mean": _mean([v.first_stack_step for v in values_sorted]),
            "first_upper_layer_open_step_mean": _mean([v.first_upper_layer_open_step for v in values_sorted]),
            "first_stand_hw_step_mean": _mean([v.first_stand_hw_step for v in values_sorted]),
            "first_reentry_step_mean": _mean([v.first_reentry_step for v in values_sorted]),
            "stand_hw_used_total_mean": _mean([v.stand_hw_used_total for v in values_sorted]),
            "hard_floor_phase_stand_hw_chosen_total_mean": _mean(
                [v.hard_floor_phase_stand_hw_chosen_total for v in values_sorted]
            ),
            "reentries_total_mean": _mean([v.reentries_total for v in values_sorted]),
            "lower_layer_reentry_count_mean": _mean([v.lower_layer_reentry_count for v in values_sorted]),
            "lower_layer_reentry_max_drop_mm_mean": _mean([v.lower_layer_reentry_max_drop_mm for v in values_sorted]),
            "lower_layer_reentry_mean_drop_mm_mean": _mean([v.lower_layer_reentry_mean_drop_mm for v in values_sorted]),
            "blocked_upper_layer_open_attempts_mean": _mean(
                [v.blocked_upper_layer_open_attempts for v in values_sorted]
            ),
            "active_layer_exhaustion_events_mean": _mean(
                [v.active_layer_exhaustion_events for v in values_sorted]
            ),
            "monotonic_stack_rate_mean": _mean([v.monotonic_stack_rate for v in values_sorted]),
            "placements_below_current_top_band_after_opening_next_band_mean": _mean(
                [v.placements_below_current_top_band_after_opening_next_band for v in values_sorted]
            ),
            "layer_closure_score_mean": _mean([v.layer_closure_score for v in values_sorted]),
            "layer_fill_homogeneity_score_mean": _mean([v.layer_fill_homogeneity_score for v in values_sorted]),
            "z_band_fill_homogeneity_score_mean": _mean([v.z_band_fill_homogeneity_score for v in values_sorted]),
            "active_layers_peak_mean": _mean([v.active_layers_peak for v in values_sorted]),
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
    ]
    print(" | ".join(headers))
    print("-" * 110)
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
                ]
            )
        )


def run_benchmark(
    *,
    profile_path: str | Path,
    outdir: str | Path | None = None,
    variant_config_path: str | Path | None = None,
    set_overrides: list[str] | None = None,
    seeds_override: list[int] | None = None,
    variant_name: str = "variant",
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
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
