from __future__ import annotations

import math
from typing import Any, Mapping


def _as_dict(value: Any) -> dict[Any, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return float(default)
        return parsed
    except Exception:
        return float(default)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _lookup_with_int_or_str(source: Mapping[Any, Any], key: int, default: Any) -> Any:
    if key in source:
        return source[key]
    key_str = str(key)
    if key_str in source:
        return source[key_str]
    return default


def _percentile(sorted_values: list[int], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    qv = min(1.0, max(0.0, float(q)))
    pos = (len(sorted_values) - 1) * qv
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_values) - 1)
    if lo == hi:
        return float(sorted_values[lo])
    frac = pos - lo
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac)


def _stats_from_boxes(boxes_per_pallet: list[int]) -> dict[str, float | int]:
    if not boxes_per_pallet:
        return {
            "pallets": 0,
            "avg_boxes": 0.0,
            "p50_boxes_per_pallet": 0.0,
            "p95_boxes_per_pallet": 0.0,
            "max_boxes": 0,
            "pct_ge_21": 0.0,
        }

    values = sorted(int(v) for v in boxes_per_pallet)
    pallets = len(values)
    ge21 = sum(1 for v in values if v >= 21)
    return {
        "pallets": pallets,
        "avg_boxes": float(sum(values) / float(pallets)),
        "p50_boxes_per_pallet": float(_percentile(values, 0.50)),
        "p95_boxes_per_pallet": float(_percentile(values, 0.95)),
        "max_boxes": int(values[-1]),
        "pct_ge_21": float(ge21 / float(pallets)),
    }


def _normalize_pct(value: Any) -> float:
    pct = _to_float(value, default=0.0)
    if pct > 1.0:
        return float(pct / 100.0)
    if pct < 0.0:
        return 0.0
    return pct


def _read_stats_dict(stats: Mapping[Any, Any]) -> dict[str, float | int]:
    pallets = _to_int(stats.get("pallets", stats.get("count", stats.get("n", 0))), default=0)
    avg_boxes = _to_float(stats.get("avg_boxes", stats.get("avg", stats.get("mean", 0.0))), default=0.0)
    p50_boxes = _to_float(stats.get("p50_boxes_per_pallet", stats.get("p50", stats.get("q50", 0.0))), default=0.0)
    p95_boxes = _to_float(stats.get("p95_boxes_per_pallet", stats.get("p95", stats.get("q95", 0.0))), default=0.0)
    max_boxes = _to_int(stats.get("max_boxes", stats.get("max", stats.get("maximum", 0))), default=0)
    pct_ge_21 = _normalize_pct(
        stats.get(
            "pct_ge_21",
            stats.get(
                "pct_boxes_ge_21",
                stats.get("pct_ge21", stats.get("ge_21_ratio", stats.get("ge_21_pct", 0.0))),
            ),
        )
    )
    return {
        "pallets": int(max(0, pallets)),
        "avg_boxes": float(avg_boxes),
        "p50_boxes_per_pallet": float(p50_boxes),
        "p95_boxes_per_pallet": float(p95_boxes),
        "max_boxes": int(max(0, max_boxes)),
        "pct_ge_21": float(pct_ge_21),
    }


def _extract_boxes_stats(payload: Mapping[str, Any], destination: int) -> tuple[dict[str, float | int], list[int], bool]:
    metrics = _as_dict(payload.get("metrics"))
    pallet_kpis = _as_dict(metrics.get("pallet_kpis"))

    seq_by_dest = _as_dict(pallet_kpis.get("continuous_pallet_sequence"))
    seq_raw = _lookup_with_int_or_str(seq_by_dest, destination, None)
    if isinstance(seq_raw, list):
        boxes = [_to_int(v, default=0) for v in seq_raw]
        boxes = [v for v in boxes if v >= 0]
        return _stats_from_boxes(boxes), boxes, True

    candidates: list[Any] = [
        _lookup_with_int_or_str(_as_dict(pallet_kpis.get("continuous_pallet_sequence_stats")), destination, None),
        _lookup_with_int_or_str(_as_dict(pallet_kpis.get("boxes_per_pallet_stats")), destination, None),
        pallet_kpis.get("continuous_pallet_sequence_stats"),
        pallet_kpis.get("boxes_per_pallet_stats"),
    ]
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            stats = _read_stats_dict(candidate)
            return stats, [], True

    return _stats_from_boxes([]), [], False


def _extract_closures(payload: Mapping[str, Any], destination: int) -> tuple[dict[str, int], bool]:
    metrics = _as_dict(payload.get("metrics"))
    pallet_kpis = _as_dict(metrics.get("pallet_kpis"))

    by_dest = _as_dict(pallet_kpis.get("continuous_closures_by_reason"))
    per_dest = _lookup_with_int_or_str(by_dest, destination, None)
    if isinstance(per_dest, Mapping):
        closures = {str(k): _to_int(v, default=0) for k, v in per_dest.items()}
        return closures, True

    global_closures = pallet_kpis.get("closures_by_reason")
    if isinstance(global_closures, Mapping):
        closures = {str(k): _to_int(v, default=0) for k, v in global_closures.items()}
        return closures, True

    return {}, False


def parse_metrics(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {
            "status": "fail",
            "error": "invalid_payload",
            "boxes_per_pallet": [],
            "pallets": 0,
            "avg_boxes": 0.0,
            "p50_boxes_per_pallet": 0.0,
            "p95_boxes_per_pallet": 0.0,
            "max_boxes": 0,
            "pct_ge_21": 0.0,
            "deadlocks_stability": 0,
            "close_height_full": 0,
            "closures_total": 0,
            "time_penalty": 0.0,
        }

    params = _as_dict(payload.get("params"))
    destination = _to_int(params.get("force_destination", 1), default=1)
    if destination <= 0:
        destination = 1

    stats, boxes_per_pallet, has_boxes_source = _extract_boxes_stats(payload, destination)
    closures_map, has_closures_source = _extract_closures(payload, destination)

    deadlocks_stability = _to_int(closures_map.get("DEADLOCK_STABILITY"), default=0)
    close_height_full = _to_int(closures_map.get("CLOSE_HEIGHT_FULL"), default=0)
    closures_total = sum(max(0, _to_int(v, default=0)) for v in closures_map.values())

    metrics = _as_dict(payload.get("metrics"))
    pallet_kpis = _as_dict(metrics.get("pallet_kpis"))
    scheduler_kpis = _as_dict(pallet_kpis.get("scheduler_kpis"))

    time_penalty = _to_float(
        scheduler_kpis.get(
            "time_penalty_total",
            scheduler_kpis.get(
                "dt_extra_total",
                pallet_kpis.get("time_penalty", metrics.get("time_penalty", 0.0)),
            ),
        ),
        default=0.0,
    )

    errors: list[str] = []
    if not has_boxes_source:
        errors.append("missing_boxes_per_pallet")
    if not has_closures_source:
        errors.append("missing_closures_by_reason")

    parsed = {
        "status": "ok" if not errors else "fail",
        "error": ";".join(errors) if errors else None,
        "boxes_per_pallet": list(boxes_per_pallet),
        "pallets": _to_int(stats.get("pallets"), default=0),
        "avg_boxes": _to_float(stats.get("avg_boxes"), default=0.0),
        "p50_boxes_per_pallet": _to_float(stats.get("p50_boxes_per_pallet"), default=0.0),
        "p95_boxes_per_pallet": _to_float(stats.get("p95_boxes_per_pallet"), default=0.0),
        "max_boxes": _to_int(stats.get("max_boxes"), default=0),
        "pct_ge_21": _normalize_pct(stats.get("pct_ge_21")),
        "deadlocks_stability": int(max(0, deadlocks_stability)),
        "close_height_full": int(max(0, close_height_full)),
        "closures_total": int(max(0, closures_total)),
        "time_penalty": float(max(0.0, time_penalty)),
    }
    return parsed


def merge_metrics(metrics_list: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not metrics_list:
        return parse_metrics({})

    success: list[Mapping[str, Any]] = []
    errors: list[str] = []
    for metrics in metrics_list:
        status = str(metrics.get("status", "fail")).lower()
        if status == "ok":
            success.append(metrics)
        else:
            error = metrics.get("error")
            if error:
                errors.append(str(error))

    if not success:
        merged = parse_metrics({})
        merged["error"] = ";".join(errors[:3]) if errors else "all_runs_failed"
        merged["fail_count"] = len(metrics_list)
        merged["ok_count"] = 0
        return merged

    all_boxes: list[int] = []
    pallets_total = 0
    weighted_avg = 0.0
    weighted_p50 = 0.0
    weighted_p95 = 0.0
    weighted_pct_ge_21 = 0.0
    max_boxes = 0

    deadlocks_stability = 0
    close_height_full = 0
    closures_total = 0
    time_penalty = 0.0

    for metrics in success:
        boxes = metrics.get("boxes_per_pallet")
        if isinstance(boxes, list):
            all_boxes.extend(_to_int(v, default=0) for v in boxes if _to_int(v, default=0) >= 0)
        pallets = max(0, _to_int(metrics.get("pallets"), default=0))
        pallets_total += pallets
        weighted_avg += _to_float(metrics.get("avg_boxes"), default=0.0) * pallets
        weighted_p50 += _to_float(metrics.get("p50_boxes_per_pallet"), default=0.0) * pallets
        weighted_p95 += _to_float(metrics.get("p95_boxes_per_pallet"), default=0.0) * pallets
        weighted_pct_ge_21 += _normalize_pct(metrics.get("pct_ge_21")) * pallets
        max_boxes = max(max_boxes, _to_int(metrics.get("max_boxes"), default=0))
        deadlocks_stability += max(0, _to_int(metrics.get("deadlocks_stability"), default=0))
        close_height_full += max(0, _to_int(metrics.get("close_height_full"), default=0))
        closures_total += max(0, _to_int(metrics.get("closures_total"), default=0))
        time_penalty += max(0.0, _to_float(metrics.get("time_penalty"), default=0.0))

    merged_stats = _stats_from_boxes(all_boxes)
    if not all_boxes and pallets_total > 0:
        merged_stats["pallets"] = int(pallets_total)
        merged_stats["avg_boxes"] = float(weighted_avg / float(pallets_total))
        merged_stats["p50_boxes_per_pallet"] = float(weighted_p50 / float(pallets_total))
        merged_stats["p95_boxes_per_pallet"] = float(weighted_p95 / float(pallets_total))
        merged_stats["pct_ge_21"] = float(weighted_pct_ge_21 / float(pallets_total))
        merged_stats["max_boxes"] = int(max_boxes)

    merged = {
        "status": "ok" if not errors else "fail",
        "error": ";".join(errors[:3]) if errors else None,
        "boxes_per_pallet": list(all_boxes),
        "pallets": _to_int(merged_stats.get("pallets"), default=0),
        "avg_boxes": _to_float(merged_stats.get("avg_boxes"), default=0.0),
        "p50_boxes_per_pallet": _to_float(merged_stats.get("p50_boxes_per_pallet"), default=0.0),
        "p95_boxes_per_pallet": _to_float(merged_stats.get("p95_boxes_per_pallet"), default=0.0),
        "max_boxes": _to_int(merged_stats.get("max_boxes"), default=0),
        "pct_ge_21": _normalize_pct(merged_stats.get("pct_ge_21")),
        "deadlocks_stability": int(deadlocks_stability),
        "close_height_full": int(close_height_full),
        "closures_total": int(closures_total),
        "time_penalty": float(time_penalty),
        "ok_count": int(len(success)),
        "fail_count": int(len(metrics_list) - len(success)),
    }
    return merged
