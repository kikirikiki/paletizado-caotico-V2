from __future__ import annotations

from palca.integration.future_window_audit import audit_future_window_continuation


def _p(
    step: int,
    layer: int,
    box_id: int,
    *,
    dims: tuple[int, int, int] = (600, 400, 300),
    z_mm: int | None = None,
) -> dict[str, int]:
    length_mm, width_mm, height_mm = dims
    z = int(layer * 300 if z_mm is None else z_mm)
    return {
        "step_index": int(step),
        "layer_id": int(layer),
        "box_id": int(box_id),
        "z_mm": int(z),
        "length_mm": int(length_mm),
        "width_mm": int(width_mm),
        "height_mm": int(height_mm),
    }


def test_future_window_audit_detects_future_candidate_after_upper_open() -> None:
    placements = [
        _p(0, 0, 1),
        _p(1, 1, 2),
        _p(2, 1, 3),
        _p(3, 1, 4),
        _p(4, 0, 5),
        _p(5, 1, 6),
    ]

    payload = audit_future_window_continuation(placements, horizons=[5, 10, 15])
    events = payload["events"]
    upper_open = next(e for e in events if e["event_type"] == "upper_open")
    reentry = next(e for e in events if e["event_type"] == "reentry")

    assert upper_open["event_step"] == 1
    assert upper_open["active_layer_idx"] == 0
    assert upper_open["future_same_layer_candidate_exists_at_h5"] is True
    assert upper_open["min_future_offset_to_same_layer_candidate"] == 3
    assert upper_open["first_future_candidate_dims_mm"] == "600x400x300"
    assert upper_open["next_reentry_step"] == 4
    assert upper_open["next_reentry_explained_at_h5"] is True

    assert reentry["event_step"] == 4
    assert reentry["active_layer_idx"] == 3
    assert reentry["future_same_layer_candidate_exists_at_h5"] is True
    assert reentry["limitation_class_h15"] == "sequencing-limited"

    summary = payload["summary"]
    assert summary["upper_open_events_total"] == 1
    assert summary["reentry_events_total"] == 1
    assert summary["explainable_reentries_h5"] == 1


def test_future_window_audit_marks_pattern_limited_when_no_future_candidate() -> None:
    placements = [
        _p(0, 0, 1),
        _p(1, 1, 2),
        _p(2, 0, 3),
        _p(3, 0, 4),
        _p(4, 0, 5),
    ]

    payload = audit_future_window_continuation(placements, horizons=[5, 10, 15])
    reentry = next(e for e in payload["events"] if e["event_type"] == "reentry")

    assert reentry["event_step"] == 2
    assert reentry["active_layer_idx"] == 3
    assert reentry["future_same_layer_candidate_exists_at_h15"] is False
    assert reentry["min_future_offset_to_same_layer_candidate"] is None
    assert reentry["limitation_class_h15"] == "pattern-limited"

    summary = payload["summary"]
    assert summary["explainable_reentries_h5"] == 0
    assert summary["explainable_reentries_h10"] == 0
    assert summary["explainable_reentries_h15"] == 0
    assert summary["pattern_limited_reentries_h15"] == 3
    assert 2 in summary["critical_steps"]
