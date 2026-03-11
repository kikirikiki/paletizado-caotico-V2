from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any, Iterable

DEFAULT_FUTURE_HORIZONS: tuple[int, int, int] = (5, 10, 15)
_TYPE_KEYS: tuple[str, ...] = ("sku", "sku_id", "item_type", "box_type", "type", "product_code")


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _placement_value(placement: Any, key: str, default: Any = None) -> Any:
    if isinstance(placement, dict):
        return placement.get(key, default)
    return getattr(placement, key, default)


def normalize_horizons(horizons: Iterable[int] | None) -> list[int]:
    raw = list(horizons) if horizons is not None else list(DEFAULT_FUTURE_HORIZONS)
    cleaned = sorted({_as_int(v, 0) for v in raw if _as_int(v, 0) > 0})
    if not cleaned:
        raise ValueError("future horizons invalidos: se esperaba al menos un entero > 0")
    return cleaned


def _placement_type_label(placement: Any, *, dims_label: str) -> str:
    for key in _TYPE_KEYS:
        raw = _placement_value(placement, key, None)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return dims_label


def _normalize_placements(placements: Iterable[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, placement in enumerate(placements):
        length_mm = max(0, _as_int(_placement_value(placement, "length_mm", 0)))
        width_mm = max(0, _as_int(_placement_value(placement, "width_mm", 0)))
        height_mm = max(0, _as_int(_placement_value(placement, "height_mm", 0)))
        dims_label = f"{length_mm}x{width_mm}x{height_mm}"
        normalized.append(
            {
                "index": int(idx),
                "step": _as_int(_placement_value(placement, "step_index", idx), idx),
                "layer_id": _as_int(_placement_value(placement, "layer_id", 0), 0),
                "box_id": _placement_value(placement, "box_id", None),
                "z_mm": _as_int(_placement_value(placement, "z_mm", 0), 0),
                "length_mm": int(length_mm),
                "width_mm": int(width_mm),
                "height_mm": int(height_mm),
                "dims_mm": dims_label,
                "type_label": _placement_type_label(placement, dims_label=dims_label),
            }
        )
    return normalized


def _future_candidates(
    seq: list[dict[str, Any]],
    *,
    start_index: int,
    active_band_idx: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for idx in range(start_index + 1, len(seq)):
        if int(seq[idx]["band_id"]) != int(active_band_idx):
            continue
        candidate = dict(seq[idx])
        candidate["offset"] = int(idx - start_index)
        candidates.append(candidate)
    return candidates


def _build_event_rows(seq: list[dict[str, Any]], horizons: list[int], *, layer_band_mm: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    max_band_seen = -1
    max_h = max(horizons)
    max_h_key = f"future_same_layer_candidate_exists_at_h{max_h}"
    band_mm = max(1, int(layer_band_mm))
    for row in seq:
        row["band_id"] = int(int(row["z_mm"]) // band_mm)

    for idx, row in enumerate(seq):
        layer_id = int(row["layer_id"])
        band_id = int(row["band_id"])
        if idx == 0:
            max_band_seen = max(max_band_seen, band_id)
            continue

        event_type: str | None = None
        active_layer_idx: int | None = None
        if band_id > max_band_seen and max_band_seen >= 0:
            event_type = "upper_open"
            active_layer_idx = int(max_band_seen)
        elif band_id < max_band_seen and max_band_seen >= 0:
            event_type = "reentry"
            active_layer_idx = int(max_band_seen)

        if event_type and active_layer_idx is not None:
            candidates = _future_candidates(seq, start_index=idx, active_band_idx=active_layer_idx)
            first_candidate = candidates[0] if candidates else None
            min_offset = first_candidate["offset"] if first_candidate else None
            offsets = [int(c["offset"]) for c in candidates]
            row_out: dict[str, Any] = {
                "event_idx": int(len(events)),
                "event_type": str(event_type),
                "event_step": int(row["step"]),
                "event_sequence_index": int(idx),
                "event_layer_idx": int(layer_id),
                "event_band_idx": int(band_id),
                "active_layer_idx": int(active_layer_idx),
                "max_band_before_event": int(max_band_seen),
                "min_future_offset_to_same_layer_candidate": (
                    None if min_offset is None else int(min_offset)
                ),
                "future_continuation_sequence_exists_len2_at_h10": bool(
                    sum(1 for off in offsets if int(off) <= 10) >= 2
                ),
                "first_future_candidate_offset": (None if first_candidate is None else int(first_candidate["offset"])),
                "first_future_candidate_step": (None if first_candidate is None else int(first_candidate["step"])),
                "first_future_candidate_box_id": (
                    None if first_candidate is None else first_candidate.get("box_id")
                ),
                "first_future_candidate_dims_mm": (
                    None if first_candidate is None else str(first_candidate.get("dims_mm", ""))
                ),
                "first_future_candidate_type": (
                    None if first_candidate is None else str(first_candidate.get("type_label", ""))
                ),
                "first_future_candidate_length_mm": (
                    None if first_candidate is None else int(first_candidate.get("length_mm", 0))
                ),
                "first_future_candidate_width_mm": (
                    None if first_candidate is None else int(first_candidate.get("width_mm", 0))
                ),
                "first_future_candidate_height_mm": (
                    None if first_candidate is None else int(first_candidate.get("height_mm", 0))
                ),
                "next_reentry_step": None,
                "next_reentry_offset": None,
            }
            for h in horizons:
                exists = any(int(off) <= int(h) for off in offsets)
                row_out[f"future_same_layer_candidate_exists_at_h{int(h)}"] = bool(exists)
                row_out[f"next_reentry_explained_at_h{int(h)}"] = None
            row_out[f"limitation_class_h{max_h}"] = (
                "sequencing-limited" if bool(row_out[max_h_key]) else "pattern-limited"
            )
            events.append(row_out)

        max_band_seen = max(max_band_seen, band_id)

    reentries = [e for e in events if str(e.get("event_type")) == "reentry"]
    for event in events:
        if str(event.get("event_type")) != "upper_open":
            continue
        seq_index = int(event["event_sequence_index"])
        next_reentry = next((e for e in reentries if int(e["event_sequence_index"]) > seq_index), None)
        if next_reentry is None:
            continue
        next_reentry_idx = int(next_reentry["event_sequence_index"])
        next_reentry_step = int(next_reentry["event_step"])
        event["next_reentry_step"] = int(next_reentry_step)
        event["next_reentry_offset"] = int(next_reentry_idx - seq_index)
        first_candidate_offset = event.get("first_future_candidate_offset")
        first_candidate_step = event.get("first_future_candidate_step")
        for h in horizons:
            explained = (
                first_candidate_offset is not None
                and first_candidate_step is not None
                and int(first_candidate_offset) <= int(h)
                and int(first_candidate_step) <= int(next_reentry_step)
            )
            event[f"next_reentry_explained_at_h{int(h)}"] = bool(explained)

    return events


def _critical_steps_for_seed(
    reentry_events: list[dict[str, Any]],
    *,
    max_h: int,
) -> list[int]:
    class_key = f"limitation_class_h{max_h}"
    pattern_steps = sorted(
        {int(e["event_step"]) for e in reentry_events if str(e.get(class_key)) == "pattern-limited"}
    )
    if pattern_steps:
        return pattern_steps[:10]

    sortable: list[tuple[int, int]] = []
    for e in reentry_events:
        offset = e.get("min_future_offset_to_same_layer_candidate")
        if offset is None:
            continue
        sortable.append((int(offset), int(e["event_step"])))
    sortable.sort(key=lambda item: (-item[0], item[1]))
    return [int(step) for _, step in sortable[:10]]


def _seed_summary(events: list[dict[str, Any]], horizons: list[int]) -> dict[str, Any]:
    reentries = [row for row in events if str(row.get("event_type")) == "reentry"]
    upper_open = [row for row in events if str(row.get("event_type")) == "upper_open"]
    max_h = max(horizons)
    class_key = f"limitation_class_h{max_h}"

    explainable_by_h: dict[int, int] = {}
    for h in horizons:
        key = f"future_same_layer_candidate_exists_at_h{int(h)}"
        explainable_by_h[int(h)] = int(sum(1 for row in reentries if bool(row.get(key))))

    offsets = [
        int(v)
        for v in (row.get("min_future_offset_to_same_layer_candidate") for row in reentries)
        if v is not None
    ]

    recurrent_counter: Counter[str] = Counter()
    for row in reentries:
        label = str(row.get("first_future_candidate_type") or "").strip()
        if not label:
            label = str(row.get("first_future_candidate_dims_mm") or "").strip()
        if label:
            recurrent_counter[label] += 1

    recurrent = [{"label": label, "count": int(count)} for label, count in recurrent_counter.most_common(5)]

    summary: dict[str, Any] = {
        "upper_open_events_total": int(len(upper_open)),
        "reentry_events_total": int(len(reentries)),
        "mean_min_future_offset": (float(mean(offsets)) if offsets else None),
        "recurrent_future_box_dims_or_types": recurrent,
        "critical_steps": _critical_steps_for_seed(reentries, max_h=max_h),
        f"sequencing_limited_reentries_h{max_h}": int(
            sum(1 for row in reentries if str(row.get(class_key)) == "sequencing-limited")
        ),
        f"pattern_limited_reentries_h{max_h}": int(
            sum(1 for row in reentries if str(row.get(class_key)) == "pattern-limited")
        ),
    }
    for h, value in explainable_by_h.items():
        summary[f"explainable_reentries_h{int(h)}"] = int(value)
    return summary


def audit_future_window_continuation(
    placements: Iterable[Any],
    *,
    horizons: Iterable[int] | None = None,
    layer_band_mm: int = 100,
) -> dict[str, Any]:
    normalized_h = normalize_horizons(horizons)
    seq = _normalize_placements(placements)
    events = _build_event_rows(seq, normalized_h, layer_band_mm=int(layer_band_mm))
    summary = _seed_summary(events, normalized_h)
    return {
        "schema_version": 1,
        "horizons": [int(v) for v in normalized_h],
        "layer_band_mm": int(max(1, int(layer_band_mm))),
        "events": events,
        "summary": summary,
    }
