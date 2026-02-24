from __future__ import annotations

import json
import math
from typing import Any, Mapping


FAIL_DEADLOCK_PENALTY = 1_000_000


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return float(default)
        return parsed
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def make_rank_key(metrics: Mapping[str, Any]) -> tuple[float, float, float, float, float]:
    deadlocks_stability = max(0, _safe_int(metrics.get("deadlocks_stability"), default=0))
    p95_boxes_per_pallet = _safe_float(metrics.get("p95_boxes_per_pallet"), default=0.0)
    pct_ge_21 = _safe_float(metrics.get("pct_ge_21"), default=0.0)
    avg_boxes = _safe_float(metrics.get("avg_boxes"), default=0.0)
    time_penalty = _safe_float(metrics.get("time_penalty"), default=0.0)

    status = str(metrics.get("status", "ok")).lower()
    fail_count = max(0, _safe_int(metrics.get("fail_count"), default=0))
    if status != "ok" or fail_count > 0:
        deadlocks_stability += FAIL_DEADLOCK_PENALTY + fail_count

    return (
        float(deadlocks_stability),
        float(-p95_boxes_per_pallet),
        float(-pct_ge_21),
        float(-avg_boxes),
        float(time_penalty),
    )


def serialize_rank_key(rank_key: tuple[float, float, float, float, float]) -> str:
    return json.dumps([float(v) for v in rank_key], ensure_ascii=True, separators=(",", ":"))
