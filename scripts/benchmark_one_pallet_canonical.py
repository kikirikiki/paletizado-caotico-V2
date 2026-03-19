#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
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

REQUIRED_PARAM_KEYS = {
    "arrival_mode",
    "balance_weight",
    "batchfill_budget_ms",
    "batchfill_greedy_topk",
    "batchfill_layer_starter",
    "batchfill_starters_max",
    "continuous_pallets",
    "controller_debug",
    "coverage_grid_x",
    "coverage_grid_y",
    "coverage_weight",
    "dominant_free_rect_ratio_gate",
    "dominant_free_rect_weight",
    "episode_id",
    "force_destination",
    "grid_mm",
    "hard_floor_phase_end_step",
    "hard_floor_phase_lookahead_items",
    "hard_floor_phase_min_base_candidates",
    "hard_floor_phase_stand_mix_bonus",
    "height_slack_mm",
    "heavy_bottom",
    "heuristic",
    "loadbear_factor",
    "loadbear_penalty_weight",
    "lookahead_k",
    "max_candidates",
    "max_overweight_ratio",
    "max_pallets",
    "max_seconds_per_item",
    "max_tries_per_item",
    "micro_depth",
    "micro_plan",
    "micro_topk",
    "micro_width",
    "min_support",
    "model",
    "n_per_pallet",
    "online_controller",
    "orientation_mode",
    "overhang_mm",
    "policy",
    "priority_mode",
    "priority_weight",
    "ramp_cap",
    "score_mode",
    "settle_max_iter",
    "settle_snap_grid",
    "settle_timeout_ms",
    "shuffle_strength",
    "shuffle_window",
    "spatial_tower_penalty_end_step",
    "spatial_tower_penalty_weight",
    "spatial_tower_target_base",
    "spatial_tower_target_step_div",
    "spatial_xy_bin_mm",
    "stability_eps_mm",
    "stability_mode",
    "stacking_mode",
    "staging_cap",
    "stand_hw_height_margin_gate_mm",
    "starvation_weight",
    "t_changeover",
    "t_pick_place",
    "t_select_base",
    "t_select_step",
    "t_stage",
    "t_unstage",
    "time_budget_ms",
    "time_penalty_weight",
    "time_scale",
    "tower_z_band_mm",
    "tower_z_penalty_weight",
    "watchdog_heartbeat_sec",
    "weight_col",
    "z_band_mm",
}

PARAM_ALIASES = {
    "k": "lookahead_k",
    "lookahead": "lookahead_k",
    "overhang": "overhang_mm",
    "time_budget": "time_budget_ms",
    "height_slack": "height_slack_mm",
}


@dataclass(frozen=True, slots=True)
class SeedSummary:
    run_label: str
    seed: int
    processed_boxes: int | None
    first_stack_step: int | None
    first_stand_hw_step: int | None
    stand_hw_used_total: int | None
    hard_floor_phase_stand_hw_chosen_total: int | None
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
        raise ValueError(f"Perfil incompleto: faltan parametros requeridos: {missing_params}")

    unknown_params = sorted(set(params.keys()) - REQUIRED_PARAM_KEYS)
    if unknown_params:
        raise ValueError(f"Perfil invalido: parametros desconocidos: {unknown_params}")

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
            raise ValueError(f"Override invalido: parametro desconocido '{key}'")
        out[key] = value
    return out


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

    kwargs = dict(params)
    kwargs.update(
        {
            "excel_path": excel_path,
            "out_path": str(out_json_path),
            "episode_seed": int(seed),
            "dump_placements_path": str(placements_path),
        }
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

    return SeedSummary(
        run_label=run_label,
        seed=int(seed),
        processed_boxes=processed_boxes,
        first_stack_step=first_stack_step,
        first_stand_hw_step=first_stand_hw_step,
        stand_hw_used_total=stand_hw_used_total,
        hard_floor_phase_stand_hw_chosen_total=hard_floor_stand_total,
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
            "processed_boxes_min": min(v.processed_boxes for v in values_sorted if v.processed_boxes is not None)
            if any(v.processed_boxes is not None for v in values_sorted)
            else None,
            "processed_boxes_max": max(v.processed_boxes for v in values_sorted if v.processed_boxes is not None)
            if any(v.processed_boxes is not None for v in values_sorted)
            else None,
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
            },
            str(variant_name): {
                "excel": variant_excel,
                "effective_config_hash": variant_hash,
                "overrides": merged_variant_overrides,
            }
            if variant_requested
            else None,
        },
        "aggregates": aggregates,
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
