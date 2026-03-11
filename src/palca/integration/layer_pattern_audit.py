from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from ..domain.box import Box
from ..domain.pallet_spec import PalletSpec
from ..domain.placement import Placement, PlacementPreview
from ..packer.controls import BalanceConfig, ControlConfig, LoadBearConfig, StabilityConfig
from ..packer.maxrects2d import MaxRects2D, MaxRectsCandidate, Rect
from ..packer.pallet_model import PalletModel, orientation_dims_for_mode


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _clamp01(value: float) -> float:
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return float(value)


@dataclass(frozen=True, slots=True)
class _AuditBox:
    token: int
    box: Box
    step: int
    orientation_name: str
    orientation_family: str


def _normalize_placements(placements: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, raw in enumerate(placements):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        step = _as_int(row.get("step_index"), idx)
        row["_audit_step"] = int(step)
        row["_audit_token"] = int(idx)
        out.append(row)
    out.sort(key=lambda item: (_as_int(item.get("_audit_step"), 0), _as_int(item.get("_audit_token"), 0)))
    return out


def _first_reentry_step(placements: list[dict[str, Any]]) -> int | None:
    max_z = -1_000_000_000
    for idx, row in enumerate(placements):
        step = _as_int(row.get("_audit_step"), idx)
        z_mm = _as_int(row.get("z_mm"), 0)
        if idx > 0 and z_mm < max_z:
            return int(step)
        max_z = max(max_z, int(z_mm))
    return None


def _first_upper_layer_open_step(placements: list[dict[str, Any]]) -> int | None:
    for idx, row in enumerate(placements):
        step = _as_int(row.get("_audit_step"), idx)
        if _as_int(row.get("z_mm"), 0) > 0:
            return int(step)
    return None


def _placement_to_box(row: dict[str, Any]) -> _AuditBox:
    token = _as_int(row.get("_audit_token"), 0)
    step = _as_int(row.get("_audit_step"), 0)
    box_id_raw = row.get("box_id")
    box_id = box_id_raw if box_id_raw is not None else f"audit_box_{token}"
    weight = row.get("weight_kg")
    weight_kg = _as_float(weight, 0.0) if weight is not None else None
    box = Box(
        box_id=box_id,
        length_mm=_as_int(row.get("length_mm"), 0),
        width_mm=_as_int(row.get("width_mm"), 0),
        height_mm=_as_int(row.get("height_mm"), 0),
        timestamp=_as_float(row.get("timestamp"), float(step)),
        destination=row.get("pallet_id"),
        weight_kg=weight_kg,
        loadbear=None,
        priority=None,
    )
    return _AuditBox(
        token=int(token),
        box=box,
        step=int(step),
        orientation_name=str(row.get("orientation_name", "") or ""),
        orientation_family=str(row.get("orientation_family", "") or ""),
    )


def _placement_from_row(row: dict[str, Any]) -> Placement:
    return Placement(
        x_mm=_as_int(row.get("x_mm"), 0),
        y_mm=_as_int(row.get("y_mm"), 0),
        z_mm=_as_int(row.get("z_mm"), 0),
        rot90=bool(row.get("rot90", False)),
        layer_id=_as_int(row.get("layer_id"), 0),
        length_mm=_as_int(row.get("length_mm"), 0),
        width_mm=_as_int(row.get("width_mm"), 0),
        height_mm=_as_int(row.get("height_mm"), 0),
        box_id=row.get("box_id"),
        weight_kg=_as_float(row.get("weight_kg"), 0.0) if row.get("weight_kg") is not None else None,
        loadbear=None,
        priority=None,
        orientation_name=str(row.get("orientation_name", "") or ""),
        orientation_family=str(row.get("orientation_family", "") or ""),
    )


def _build_model(params: dict[str, Any], *, pallet_spec_override: PalletSpec | None = None) -> PalletModel:
    spec = pallet_spec_override
    if spec is None:
        spec = PalletSpec(
            overhang_mm=_as_int(params.get("overhang_mm"), 0),
            allow_rotate=True,
        )

    stability = StabilityConfig(
        mode=str(params.get("stability_mode", "ratio+corners") or "ratio+corners"),
        min_support_ratio=_as_float(params.get("min_support"), 0.75),
        eps_mm=_as_float(params.get("stability_eps_mm"), 1.0),
        settle_snap_grid=bool(params.get("settle_snap_grid", False)),
        grid_mm=(
            _as_int(params.get("grid_mm"), 0)
            if params.get("grid_mm", None) is not None
            else None
        ),
        settle_max_iter=_as_int(params.get("settle_max_iter"), 0),
        settle_timeout_ms=_as_int(params.get("settle_timeout_ms"), 0),
    )
    loadbear = LoadBearConfig(
        heavy_bottom=bool(params.get("heavy_bottom", False)),
        max_overweight_ratio=_as_float(params.get("max_overweight_ratio"), 1.5),
        penalty_weight=_as_float(params.get("loadbear_penalty_weight"), 1.0),
        loadbear_factor=_as_float(params.get("loadbear_factor"), 1.0),
    )
    balance = BalanceConfig(
        balance_weight=_as_float(params.get("balance_weight"), 0.0),
    )

    return PalletModel(
        spec=spec,
        heuristic=str(params.get("heuristic", "baf") or "baf"),
        control_config=ControlConfig(
            stability=stability,
            loadbear=loadbear,
            balance=balance,
        ),
        stacking_mode=str(params.get("stacking_mode", "heightfield") or "heightfield"),
        z_band_mm=(
            _as_int(params.get("z_band_mm"), 0)
            if params.get("z_band_mm", None) is not None
            else None
        ),
        orientation_mode=str(params.get("orientation_mode", "planar") or "planar"),
        stand_hw_height_margin_gate_mm=_as_int(params.get("stand_hw_height_margin_gate_mm"), 400),
        coverage_grid_x=_as_int(params.get("coverage_grid_x"), 0),
        coverage_grid_y=_as_int(params.get("coverage_grid_y"), 0),
        coverage_weight=_as_float(params.get("coverage_weight"), 0.0),
        dominant_free_rect_weight=_as_float(params.get("dominant_free_rect_weight"), 0.0),
        dominant_free_rect_ratio_gate=_as_float(params.get("dominant_free_rect_ratio_gate"), 0.35),
    )


def _projection_bin_snapshot(model: PalletModel) -> MaxRects2D:
    base = next((layer for layer in model.layers if _as_int(getattr(layer, "layer_id", 0), 0) == 0), None)
    if base is not None and getattr(base, "bin", None) is not None:
        return base.bin.copy()
    return MaxRects2D(
        int(model.spec.bin_length_mm),
        int(model.spec.bin_width_mm),
        heuristic=str(model.heuristic),
    )


def _simulate_projection_free_rects(
    projection_bin: MaxRects2D,
    *,
    spec: PalletSpec,
    placement: Placement,
) -> list[Rect]:
    if _as_int(getattr(placement, "z_mm", 0), 0) > 0:
        return list(projection_bin.free_rects)
    cand = MaxRectsCandidate(
        x=_as_int(getattr(placement, "x_mm", 0), 0) - int(spec.offset_mm),
        y=_as_int(getattr(placement, "y_mm", 0), 0) - int(spec.offset_mm),
        w=_as_int(getattr(placement, "length_mm", 0), 0),
        h=_as_int(getattr(placement, "width_mm", 0), 0),
        score=(0,),
    )
    free_after = projection_bin.simulate_place(cand)
    if isinstance(free_after, list):
        return list(free_after)
    return list(projection_bin.free_rects)


def _footprint_options(box: Box, *, orientation_mode: str, allow_rotate: bool) -> list[tuple[int, int]]:
    dims = orientation_dims_for_mode(
        int(box.length_mm),
        int(box.width_mm),
        int(box.height_mm),
        mode=str(orientation_mode),
        allow_rotate=bool(allow_rotate),
    )
    out: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for l_mm, w_mm, _ in dims:
        key = (int(l_mm), int(w_mm))
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _box_fits_rect(box: Box, rect: Rect, *, orientation_mode: str, allow_rotate: bool) -> bool:
    for l_mm, w_mm in _footprint_options(box, orientation_mode=orientation_mode, allow_rotate=allow_rotate):
        if int(l_mm) <= int(rect.w) and int(w_mm) <= int(rect.h):
            return True
    return False


def _dominant_shape(*, free_rect_count: int, largest_ratio: float, unfillable_ratio: float, thin_ratio: float) -> str:
    if free_rect_count <= 0:
        return "none"
    if unfillable_ratio >= 0.45 and thin_ratio >= 0.20:
        return "thin_unfillable_mix"
    if thin_ratio >= 0.30:
        return "thin_strips"
    if unfillable_ratio >= 0.45:
        return "unfillable_blocks"
    if free_rect_count >= 6 and largest_ratio <= 0.25:
        return "fragmented_mosaic"
    if free_rect_count >= 4:
        return "fragmented"
    return "compact"


def _compute_residual_metrics(
    free_rects: list[Rect],
    *,
    candidate_boxes: list[_AuditBox],
    orientation_mode: str,
    allow_rotate: bool,
) -> dict[str, Any]:
    rects = [r for r in free_rects if int(r.w) > 0 and int(r.h) > 0]
    total_free_area = int(sum(int(r.area) for r in rects))
    free_rect_count = int(len(rects))
    if total_free_area <= 0:
        return {
            "free_rect_count": 0,
            "largest_fillable_free_rect_area_mm2": 0,
            "unfillable_free_rect_area_mm2": 0,
            "thin_strip_area_mm2": 0,
            "box_compatibility_count": 0,
            "active_layer_fillability_score": 1.0,
            "poison_risk_score": 0.0,
            "dominant_bad_residual_shape": "none",
            "residual_free_rect_decomposition": [],
        }

    rect_fillable: list[bool] = []
    largest_fillable = 0
    unfillable_area = 0
    decomposition: list[dict[str, Any]] = []

    for rect in rects:
        fits = any(
            _box_fits_rect(
                box_info.box,
                rect,
                orientation_mode=orientation_mode,
                allow_rotate=allow_rotate,
            )
            for box_info in candidate_boxes
        )
        rect_fillable.append(bool(fits))
        if fits:
            largest_fillable = max(largest_fillable, int(rect.area))
        else:
            unfillable_area += int(rect.area)
        aspect = float(max(rect.w, rect.h)) / float(max(1, min(rect.w, rect.h)))
        decomposition.append(
            {
                "x_mm": int(rect.x),
                "y_mm": int(rect.y),
                "w_mm": int(rect.w),
                "h_mm": int(rect.h),
                "area_mm2": int(rect.area),
                "aspect_ratio": float(aspect),
                "fillable": bool(fits),
            }
        )

    decomposition.sort(key=lambda row: (-int(row["area_mm2"]), int(row["x_mm"]), int(row["y_mm"])))

    compatibility_count = 0
    for box_info in candidate_boxes:
        if any(
            _box_fits_rect(
                box_info.box,
                rect,
                orientation_mode=orientation_mode,
                allow_rotate=allow_rotate,
            )
            for rect in rects
        ):
            compatibility_count += 1

    min_short_side = None
    for box_info in candidate_boxes:
        for l_mm, w_mm in _footprint_options(
            box_info.box,
            orientation_mode=orientation_mode,
            allow_rotate=allow_rotate,
        ):
            short_side = min(int(l_mm), int(w_mm))
            if short_side <= 0:
                continue
            if min_short_side is None:
                min_short_side = int(short_side)
            else:
                min_short_side = min(min_short_side, int(short_side))

    thin_strip_area = 0
    if min_short_side is not None and min_short_side > 0:
        for rect in rects:
            if min(int(rect.w), int(rect.h)) < int(min_short_side):
                thin_strip_area += int(rect.area)

    largest_ratio = float(largest_fillable) / float(max(1, total_free_area))
    unfillable_ratio = float(unfillable_area) / float(max(1, total_free_area))
    thin_ratio = float(thin_strip_area) / float(max(1, total_free_area))
    compatibility_ratio = float(compatibility_count) / float(max(1, len(candidate_boxes)))
    fragmentation_ratio = _clamp01(float(max(0, free_rect_count - 1)) / 10.0)

    fillability = _clamp01(
        (0.45 * (1.0 - unfillable_ratio))
        + (0.25 * compatibility_ratio)
        + (0.20 * largest_ratio)
        + (0.10 * (1.0 - fragmentation_ratio))
        - (0.10 * thin_ratio)
    )
    poison_risk = _clamp01(
        (0.50 * unfillable_ratio)
        + (0.20 * thin_ratio)
        + (0.15 * (1.0 - largest_ratio))
        + (0.15 * fragmentation_ratio)
    )

    return {
        "free_rect_count": int(free_rect_count),
        "largest_fillable_free_rect_area_mm2": int(largest_fillable),
        "unfillable_free_rect_area_mm2": int(unfillable_area),
        "thin_strip_area_mm2": int(thin_strip_area),
        "box_compatibility_count": int(compatibility_count),
        "active_layer_fillability_score": float(fillability),
        "poison_risk_score": float(poison_risk),
        "dominant_bad_residual_shape": _dominant_shape(
            free_rect_count=free_rect_count,
            largest_ratio=largest_ratio,
            unfillable_ratio=unfillable_ratio,
            thin_ratio=thin_ratio,
        ),
        "residual_free_rect_decomposition": decomposition,
    }


def _format_dims_orientation(
    *,
    length_mm: int,
    width_mm: int,
    height_mm: int,
    orientation_name: str,
    orientation_family: str,
) -> str:
    family = str(orientation_family or "unknown")
    name = str(orientation_name or "NA")
    return f"{int(length_mm)}x{int(width_mm)}x{int(height_mm)}|{family}|{name}"


def _remove_box_once(boxes: list[_AuditBox], *, token: int) -> list[_AuditBox]:
    out: list[_AuditBox] = []
    removed = False
    for box_info in boxes:
        if not removed and int(box_info.token) == int(token):
            removed = True
            continue
        out.append(box_info)
    return out


def _commit_actual_placement(model: PalletModel, row: dict[str, Any]) -> None:
    placement = _placement_from_row(row)
    preview = PlacementPreview(
        feasible=True,
        placement=placement,
        packing_gain=0.0,
        fragmentation=0.0,
        height_after_mm=max(
            int(model.current_height_mm()),
            int(placement.z_mm) + int(placement.height_mm),
        ),
        infeasible_reason=None,
        score_adjustment=0.0,
        debug={},
    )
    model.commit_place(preview)


def _select_root_cause(step_rows: list[dict[str, Any]]) -> str:
    if not step_rows:
        return "inconclusive_pattern_signal"
    better_steps = [row for row in step_rows if bool(row.get("counterfactual_better_same_step_exists", False))]
    max_risk = max(_as_float(row.get("poison_risk_score"), 0.0) for row in step_rows)
    if len(better_steps) >= 2:
        return "pattern_limited_local_poison_steps"
    if max_risk >= 0.60:
        return "pattern_limited_structural_residuals"
    return "inconclusive_pattern_signal"


def _build_seed_summary(
    *,
    seed: int,
    first_reentry_step: int | None,
    first_upper_layer_open_step: int | None,
    step_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    sorted_by_risk = sorted(
        step_rows,
        key=lambda row: (-_as_float(row.get("poison_risk_score"), 0.0), _as_int(row.get("step"), 0)),
    )
    poison_steps_topk = [_as_int(row.get("step"), 0) for row in sorted_by_risk[:5]]
    better_rows = [row for row in step_rows if bool(row.get("counterfactual_better_same_step_exists", False))]
    candidate_poison_rows = [
        row
        for row in step_rows
        if bool(row.get("counterfactual_better_same_step_exists", False))
        and _as_float(row.get("poison_risk_score"), 0.0) >= 0.45
    ]

    if candidate_poison_rows:
        first_poison_step = min(_as_int(row.get("step"), 0) for row in candidate_poison_rows)
    elif better_rows:
        first_poison_step = min(_as_int(row.get("step"), 0) for row in better_rows)
    elif sorted_by_risk:
        first_poison_step = _as_int(sorted_by_risk[0].get("step"), 0)
    else:
        first_poison_step = None

    shape_counter = Counter(
        str(row.get("dominant_bad_residual_shape", "none") or "none")
        for row in step_rows
        if str(row.get("dominant_bad_residual_shape", "none") or "none") != "none"
    )
    dominant_shapes = [shape for shape, _ in shape_counter.most_common(5)]

    recurrent_rows = [
        row
        for row in step_rows
        if _as_float(row.get("poison_risk_score"), 0.0) >= 0.55
        or bool(row.get("counterfactual_better_same_step_exists", False))
    ]
    dims_counter = Counter(str(row.get("placed_dims_orientation", "") or "") for row in recurrent_rows)
    recurrent_dims_orientations = [item for item, _ in dims_counter.most_common(5) if item]

    return {
        "seed": int(seed),
        "first_reentry_step": first_reentry_step,
        "first_upper_layer_open_step": first_upper_layer_open_step,
        "first_poison_step": first_poison_step,
        "poison_steps_topk": poison_steps_topk,
        "dominant_bad_residual_shapes": dominant_shapes,
        "recurrent_poison_dims_orientations": recurrent_dims_orientations,
        "count_steps_with_better_counterfactual": int(len(better_rows)),
        "pattern_limited_root_cause_class": _select_root_cause(step_rows),
    }


def audit_layer_pattern_poison(
    *,
    seed: int,
    placements: Iterable[dict[str, Any]],
    params: dict[str, Any],
    counterfactual_top_k: int = 5,
    audit_horizon_step: int = 15,
    counterfactual_preview_max_candidates: int = 240,
    pallet_spec_override: PalletSpec | None = None,
) -> dict[str, Any]:
    sequence = _normalize_placements(placements)
    if not sequence:
        empty_summary = {
            "seed": int(seed),
            "first_reentry_step": None,
            "first_upper_layer_open_step": None,
            "first_poison_step": None,
            "poison_steps_topk": [],
            "dominant_bad_residual_shapes": [],
            "recurrent_poison_dims_orientations": [],
            "count_steps_with_better_counterfactual": 0,
            "pattern_limited_root_cause_class": "inconclusive_pattern_signal",
        }
        return {"seed_summary": empty_summary, "step_rows": [], "counterfactual_rows": []}

    first_reentry_step = _first_reentry_step(sequence)
    first_upper_step = _first_upper_layer_open_step(sequence)

    max_known_step = max(_as_int(row.get("_audit_step"), idx) for idx, row in enumerate(sequence))
    end_candidates = [int(audit_horizon_step), int(max_known_step)]
    if first_reentry_step is not None:
        end_candidates.append(int(first_reentry_step))
    if first_upper_step is not None:
        end_candidates.append(int(first_upper_step))
    audit_end_step = int(min(max_known_step, max(end_candidates)))
    min_known_step = min(_as_int(row.get("_audit_step"), idx) for idx, row in enumerate(sequence))

    model = _build_model(params, pallet_spec_override=pallet_spec_override)
    rows_by_step = {int(_as_int(row.get("_audit_step"), idx)): row for idx, row in enumerate(sequence)}
    sorted_steps = sorted(rows_by_step.keys())

    step_rows: list[dict[str, Any]] = []
    counter_rows: list[dict[str, Any]] = []

    sequence_boxes = [_placement_to_box(row) for row in sequence]
    step_to_token = {_as_int(row.get("_audit_step"), idx): _as_int(row.get("_audit_token"), idx) for idx, row in enumerate(sequence)}

    for step in sorted_steps:
        row = rows_by_step.get(step)
        if row is None:
            continue
        if int(step) < int(min_known_step) or int(step) > int(audit_end_step):
            _commit_actual_placement(model, row)
            continue

        projection_before = _projection_bin_snapshot(model)
        actual_placement = _placement_from_row(row)
        remaining_boxes = [box_info for box_info in sequence_boxes if int(box_info.step) >= int(step)]
        selected_token = int(step_to_token.get(int(step), _as_int(row.get("_audit_token"), 0)))
        remaining_after_actual = _remove_box_once(remaining_boxes, token=selected_token)

        actual_free_rects = _simulate_projection_free_rects(
            projection_before,
            spec=model.spec,
            placement=actual_placement,
        )
        actual_metrics = _compute_residual_metrics(
            actual_free_rects,
            candidate_boxes=remaining_after_actual,
            orientation_mode=str(model.orientation_mode),
            allow_rotate=bool(model.spec.allow_rotate),
        )

        viable_alternatives: list[dict[str, Any]] = []
        for candidate in remaining_boxes:
            preview = model.preview_place(
                candidate.box,
                max_candidates=int(max(1, counterfactual_preview_max_candidates)),
            )
            if not bool(getattr(preview, "feasible", False)) or preview.placement is None:
                continue
            simulated_free_rects = _simulate_projection_free_rects(
                projection_before,
                spec=model.spec,
                placement=preview.placement,
            )
            remaining_after_candidate = _remove_box_once(remaining_boxes, token=int(candidate.token))
            metrics = _compute_residual_metrics(
                simulated_free_rects,
                candidate_boxes=remaining_after_candidate,
                orientation_mode=str(model.orientation_mode),
                allow_rotate=bool(model.spec.allow_rotate),
            )
            viable_alternatives.append(
                {
                    "seed": int(seed),
                    "step": int(step),
                    "candidate_token": int(candidate.token),
                    "candidate_box_id": str(candidate.box.box_id),
                    "candidate_placement_z_mm": int(preview.placement.z_mm),
                    "candidate_dims_orientation": _format_dims_orientation(
                        length_mm=int(preview.placement.length_mm),
                        width_mm=int(preview.placement.width_mm),
                        height_mm=int(preview.placement.height_mm),
                        orientation_name=str(preview.placement.orientation_name or ""),
                        orientation_family=str(preview.placement.orientation_family or ""),
                    ),
                    "candidate_orientation_name": str(preview.placement.orientation_name or ""),
                    "candidate_orientation_family": str(preview.placement.orientation_family or ""),
                    "candidate_length_mm": int(preview.placement.length_mm),
                    "candidate_width_mm": int(preview.placement.width_mm),
                    "candidate_height_mm": int(preview.placement.height_mm),
                    "candidate_fillability_score": float(metrics["active_layer_fillability_score"]),
                    "candidate_poison_risk_score": float(metrics["poison_risk_score"]),
                    "candidate_free_rect_count": int(metrics["free_rect_count"]),
                    "candidate_unfillable_free_rect_area_mm2": int(metrics["unfillable_free_rect_area_mm2"]),
                    "candidate_thin_strip_area_mm2": int(metrics["thin_strip_area_mm2"]),
                    "candidate_box_compatibility_count": int(metrics["box_compatibility_count"]),
                    "candidate_dominant_bad_residual_shape": str(metrics["dominant_bad_residual_shape"]),
                }
            )

        viable_alternatives.sort(
            key=lambda item: (
                -_as_float(item.get("candidate_fillability_score"), 0.0),
                _as_float(item.get("candidate_poison_risk_score"), 0.0),
                _as_int(item.get("candidate_unfillable_free_rect_area_mm2"), 0),
                str(item.get("candidate_dims_orientation", "")),
            )
        )

        chosen_dims_orientation = _format_dims_orientation(
            length_mm=int(actual_placement.length_mm),
            width_mm=int(actual_placement.width_mm),
            height_mm=int(actual_placement.height_mm),
            orientation_name=str(actual_placement.orientation_name or ""),
            orientation_family=str(actual_placement.orientation_family or ""),
        )

        alternatives_only = [
            item
            for item in viable_alternatives
            if not (
                int(item["candidate_token"]) == int(selected_token)
                and str(item["candidate_dims_orientation"]) == str(chosen_dims_orientation)
            )
        ]

        best_alt = alternatives_only[0] if alternatives_only else None
        best_delta = (
            _as_float(best_alt.get("candidate_fillability_score"), 0.0)
            - _as_float(actual_metrics.get("active_layer_fillability_score"), 0.0)
            if best_alt is not None
            else 0.0
        )
        better_exists = bool(best_alt is not None and best_delta > 1e-9)

        rank_pool: list[tuple[str, float, float]] = [
            (
                "chosen",
                _as_float(actual_metrics.get("active_layer_fillability_score"), 0.0),
                _as_float(actual_metrics.get("poison_risk_score"), 0.0),
            )
        ]
        for item in viable_alternatives:
            rank_pool.append(
                (
                    f"candidate:{item['candidate_token']}",
                    _as_float(item.get("candidate_fillability_score"), 0.0),
                    _as_float(item.get("candidate_poison_risk_score"), 0.0),
                )
            )
        rank_pool.sort(key=lambda item: (-item[1], item[2], item[0]))
        chosen_rank = None
        for idx, entry in enumerate(rank_pool, start=1):
            if entry[0] == "chosen":
                chosen_rank = int(idx)
                break

        decomposition = actual_metrics.get("residual_free_rect_decomposition", [])
        step_rows.append(
            {
                "seed": int(seed),
                "step": int(step),
                "active_layer_idx": int(actual_placement.layer_id),
                "placed_dims": f"{int(actual_placement.length_mm)}x{int(actual_placement.width_mm)}x{int(actual_placement.height_mm)}",
                "placed_orientation": str(actual_placement.orientation_name or ""),
                "placed_orientation_family": str(actual_placement.orientation_family or ""),
                "placed_dims_orientation": chosen_dims_orientation,
                "placed_z_mm": int(actual_placement.z_mm),
                "first_reentry_step": first_reentry_step,
                "first_upper_layer_open_step": first_upper_step,
                "in_window_first_reentry": (
                    bool(first_reentry_step is not None and int(step) <= int(first_reentry_step))
                ),
                "in_window_first_upper_layer_open": (
                    bool(first_upper_step is not None and int(step) <= int(first_upper_step))
                ),
                "in_window_h15": bool(int(step) <= int(audit_horizon_step)),
                "residual_free_rect_decomposition": decomposition,
                "active_layer_fillability_score": float(actual_metrics["active_layer_fillability_score"]),
                "largest_fillable_free_rect_area_mm2": int(actual_metrics["largest_fillable_free_rect_area_mm2"]),
                "unfillable_free_rect_area_mm2": int(actual_metrics["unfillable_free_rect_area_mm2"]),
                "thin_strip_area_mm2": int(actual_metrics["thin_strip_area_mm2"]),
                "free_rect_count": int(actual_metrics["free_rect_count"]),
                "box_compatibility_count": int(actual_metrics["box_compatibility_count"]),
                "dominant_bad_residual_shape": str(actual_metrics["dominant_bad_residual_shape"]),
                "poison_risk_score": float(actual_metrics["poison_risk_score"]),
                "counterfactual_better_same_step_exists": bool(better_exists),
                "counterfactual_best_fillability_delta": float(best_delta),
                "counterfactual_best_dims_orientation": (
                    str(best_alt["candidate_dims_orientation"]) if best_alt is not None else ""
                ),
                "counterfactual_best_dims": (
                    f"{best_alt['candidate_length_mm']}x{best_alt['candidate_width_mm']}x{best_alt['candidate_height_mm']}"
                    if best_alt is not None
                    else ""
                ),
                "counterfactual_best_orientation": (
                    str(best_alt["candidate_orientation_name"]) if best_alt is not None else ""
                ),
                "poison_step_candidate_rank": chosen_rank,
            }
        )

        for rank, item in enumerate(viable_alternatives[: max(1, int(counterfactual_top_k))], start=1):
            counter_row = dict(item)
            counter_row["candidate_rank_by_fillability"] = int(rank)
            counter_row["chosen_fillability_score"] = float(actual_metrics["active_layer_fillability_score"])
            counter_row["fillability_delta_vs_chosen"] = (
                _as_float(item.get("candidate_fillability_score"), 0.0)
                - _as_float(actual_metrics.get("active_layer_fillability_score"), 0.0)
            )
            counter_rows.append(counter_row)

        _commit_actual_placement(model, row)

    seed_summary = _build_seed_summary(
        seed=int(seed),
        first_reentry_step=first_reentry_step,
        first_upper_layer_open_step=first_upper_step,
        step_rows=step_rows,
    )
    return {
        "seed_summary": seed_summary,
        "step_rows": step_rows,
        "counterfactual_rows": counter_rows,
    }
