from __future__ import annotations

from palca.integration.layer_monotonicity import compute_layer_monotonicity_metrics


def _placement(*, step: int, z: int, layer: int, h: int = 100) -> dict[str, int]:
    return {
        "step_index": step,
        "x_mm": 0,
        "y_mm": 0,
        "z_mm": z,
        "layer_id": layer,
        "length_mm": 200,
        "width_mm": 100,
        "height_mm": h,
    }


def test_layer_monotonicity_reentry_basic() -> None:
    seq = [
        _placement(step=0, z=0, layer=0),
        _placement(step=1, z=100, layer=1),
        _placement(step=2, z=0, layer=0),
        _placement(step=3, z=100, layer=1),
    ]

    m = compute_layer_monotonicity_metrics(seq, layer_band_mm=100)

    assert int(m["first_stack_step"]) == 1
    assert int(m["lower_layer_reentry_count"]) == 1
    assert int(m["lower_layer_reentry_max_drop_mm"]) == 100
    assert abs(float(m["lower_layer_reentry_mean_drop_mm"]) - 100.0) < 1e-9
    assert int(m["placements_below_current_top_band_after_opening_next_band"]) == 1
    assert abs(float(m["monotonic_stack_rate"]) - 0.75) < 1e-9
    assert abs(float(m["layer_closure_score"]) - 0.5) < 1e-9


def test_layer_monotonicity_monotonic_basic() -> None:
    seq = [
        _placement(step=0, z=0, layer=0),
        _placement(step=1, z=0, layer=0),
        _placement(step=2, z=100, layer=1),
        _placement(step=3, z=100, layer=1),
        _placement(step=4, z=200, layer=2),
    ]

    m = compute_layer_monotonicity_metrics(seq, layer_band_mm=100)

    assert int(m["lower_layer_reentry_count"]) == 0
    assert abs(float(m["monotonic_stack_rate"]) - 1.0) < 1e-9
    assert int(m["first_stack_step"]) == 2
    assert int(m["monotonic_violations_count"]) == 0
    assert int(m["reentries_total"]) == 0
    assert int(m["max_layer_drop"]) == 0
    assert int(m["reentries_drop_ge_2_count"]) == 0
    assert int(m["deep_drop_burden"]) == 0


def test_layer_drop_semantics_l_minus_1() -> None:
    seq = [
        _placement(step=0, z=0, layer=0),
        _placement(step=1, z=100, layer=1),
        _placement(step=2, z=200, layer=2),
        _placement(step=3, z=100, layer=1),
    ]
    m = compute_layer_monotonicity_metrics(seq, layer_band_mm=100, layer_drop_audit=True)

    assert int(m["reentries_total"]) == 1
    assert int(m["max_layer_drop"]) == 1
    assert int(m["reentries_drop_ge_2_count"]) == 0
    examples = m.get("layer_drop_examples", [])
    assert isinstance(examples, list) and examples
    assert int(examples[0]["layer_drop"]) == 1


def test_layer_drop_semantics_l_minus_2() -> None:
    seq = [
        _placement(step=0, z=0, layer=0),
        _placement(step=1, z=100, layer=1),
        _placement(step=2, z=200, layer=2),
        _placement(step=3, z=0, layer=0),
    ]
    m = compute_layer_monotonicity_metrics(seq, layer_band_mm=100, layer_drop_audit=True)

    assert int(m["reentries_total"]) == 1
    assert int(m["max_layer_drop"]) == 2
    assert int(m["reentries_drop_ge_2_count"]) == 1
    assert int(m["deep_drop_burden"]) == 2
    histogram = m.get("layer_drop_histogram", {})
    assert isinstance(histogram, dict)
    assert int(histogram.get("2", 0)) == 1


def test_layer_drop_audit_off_backward_compatibility() -> None:
    seq = [
        _placement(step=0, z=0, layer=0),
        _placement(step=1, z=100, layer=1),
        _placement(step=2, z=0, layer=0),
    ]
    m = compute_layer_monotonicity_metrics(seq, layer_band_mm=100, layer_drop_audit=False)

    assert int(m["lower_layer_reentry_count"]) == 1
    assert "layer_drop_step_trace" not in m
