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
from .layer_monotonicity import compute_layer_monotonicity_metrics


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


def _placement_to_box(row: dict[str, Any]) -> _AuditBox:
    token = _as_int(row.get("_audit_token"), 0)
    step = _as_int(row.get("_audit_step"), 0)
    box_id_raw = row.get("box_id")
    box_id = box_id_raw if box_id_raw is not None else f"oracle_box_{token}"
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
        }

    largest_fillable = 0
    unfillable_area = 0
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
        if fits:
            largest_fillable = max(largest_fillable, int(rect.area))
        else:
            unfillable_area += int(rect.area)

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
    }


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


def _collect_feasible_candidates(
    model: PalletModel,
    *,
    pending_boxes: list[_AuditBox],
    layer_band_mm: int,
    opening_layer: int,
    preview_max_candidates: int,
) -> list[dict[str, Any]]:
    projection_before = _projection_bin_snapshot(model)
    feasible: list[dict[str, Any]] = []

    for box_info in pending_boxes:
        preview = model.preview_place(
            box_info.box,
            max_candidates=int(max(1, preview_max_candidates)),
        )
        if not bool(getattr(preview, "feasible", False)) or preview.placement is None:
            continue

        remaining_after = _remove_box_once(pending_boxes, token=int(box_info.token))
        free_rects = _simulate_projection_free_rects(
            projection_before,
            spec=model.spec,
            placement=preview.placement,
        )
        metrics = _compute_residual_metrics(
            free_rects,
            candidate_boxes=remaining_after,
            orientation_mode=str(model.orientation_mode),
            allow_rotate=bool(model.spec.allow_rotate),
        )

        projected_layer = int(max(0, _as_int(preview.placement.z_mm, 0)) // max(1, int(layer_band_mm)))
        feasible.append(
            {
                "token": int(box_info.token),
                "box_id": str(box_info.box.box_id),
                "preview": preview,
                "projected_layer": int(projected_layer),
                "projected_layer_gap": int(abs(int(projected_layer) - int(opening_layer))),
                "fillability_score": float(metrics["active_layer_fillability_score"]),
                "poison_risk_score": float(metrics["poison_risk_score"]),
                "free_rect_count": int(metrics["free_rect_count"]),
                "unfillable_free_rect_area_mm2": int(metrics["unfillable_free_rect_area_mm2"]),
                "thin_strip_area_mm2": int(metrics["thin_strip_area_mm2"]),
                "dominant_bad_residual_shape": str(metrics["dominant_bad_residual_shape"]),
                "orientation_name": str(preview.placement.orientation_name or ""),
                "orientation_family": str(preview.placement.orientation_family or ""),
                "length_mm": int(preview.placement.length_mm),
                "width_mm": int(preview.placement.width_mm),
                "height_mm": int(preview.placement.height_mm),
            }
        )

    feasible.sort(
        key=lambda item: (
            int(item["projected_layer"] != int(opening_layer)),
            -float(item["fillability_score"]),
            float(item["poison_risk_score"]),
            int(item["projected_layer_gap"]),
            int(item["token"]),
        )
    )
    return feasible


def _pick_candidate(
    feasible: list[dict[str, Any]],
    *,
    opening_layer: int,
    prefer_opening_layer: bool,
) -> dict[str, Any] | None:
    if not feasible:
        return None

    if prefer_opening_layer:
        same_layer = [item for item in feasible if int(item.get("projected_layer", -1)) == int(opening_layer)]
        if same_layer:
            return same_layer[0]
    return feasible[0]


def _simulate_alternative(
    *,
    history_rows: list[dict[str, Any]],
    remaining_rows: list[dict[str, Any]],
    params: dict[str, Any],
    layer_band_mm: int,
    opening_layer: int,
    forced_first_token: int,
    prefix_len: int,
    oracle_rollout_depth: int,
    preview_max_candidates: int,
) -> dict[str, Any]:
    model = _build_model(params)
    for row in history_rows:
        _commit_actual_placement(model, row)

    pending_boxes = [_placement_to_box(row) for row in remaining_rows]
    committed_prefix: list[dict[str, Any]] = []
    committed_rollout: list[dict[str, Any]] = []

    target_total = int(max(1, prefix_len) + max(0, oracle_rollout_depth))
    failure_reason = "completed"

    for local_step in range(target_total):
        feasible = _collect_feasible_candidates(
            model,
            pending_boxes=pending_boxes,
            layer_band_mm=layer_band_mm,
            opening_layer=opening_layer,
            preview_max_candidates=preview_max_candidates,
        )
        if not feasible:
            failure_reason = "no_feasible_candidate"
            break

        chosen: dict[str, Any] | None = None
        if local_step == 0:
            chosen = next((item for item in feasible if int(item["token"]) == int(forced_first_token)), None)
            if chosen is None:
                failure_reason = "forced_first_not_feasible"
                break
        elif local_step < int(max(1, prefix_len)):
            chosen = _pick_candidate(feasible, opening_layer=opening_layer, prefer_opening_layer=True)
        else:
            chosen = _pick_candidate(feasible, opening_layer=opening_layer, prefer_opening_layer=False)

        if chosen is None:
            failure_reason = "selection_failed"
            break

        preview = chosen.get("preview")
        if not isinstance(preview, PlacementPreview) or preview.placement is None:
            failure_reason = "invalid_preview"
            break

        model.commit_place(preview)
        pending_boxes = _remove_box_once(pending_boxes, token=int(chosen["token"]))

        record = {
            "token": int(chosen["token"]),
            "box_id": str(chosen["box_id"]),
            "projected_layer": int(chosen["projected_layer"]),
            "fillability_score": float(chosen["fillability_score"]),
            "poison_risk_score": float(chosen["poison_risk_score"]),
            "free_rect_count": int(chosen["free_rect_count"]),
            "dominant_bad_residual_shape": str(chosen["dominant_bad_residual_shape"]),
            "orientation_name": str(chosen["orientation_name"]),
            "orientation_family": str(chosen["orientation_family"]),
            "length_mm": int(chosen["length_mm"]),
            "width_mm": int(chosen["width_mm"]),
            "height_mm": int(chosen["height_mm"]),
            "placement": preview.placement,
        }

        if local_step < int(max(1, prefix_len)):
            committed_prefix.append(record)
        else:
            committed_rollout.append(record)

    history_placements = [_placement_from_row(row) for row in history_rows]
    placed = [item["placement"] for item in committed_prefix] + [item["placement"] for item in committed_rollout]
    simulated_sequence = history_placements + placed
    mono = compute_layer_monotonicity_metrics(
        simulated_sequence,
        layer_band_mm=int(max(1, layer_band_mm)),
        layer_drop_audit=False,
    )

    prefix_fillability_values = [float(item["fillability_score"]) for item in committed_prefix]
    prefix_poison_values = [float(item["poison_risk_score"]) for item in committed_prefix]
    prefix_shapes = [str(item["dominant_bad_residual_shape"]) for item in committed_prefix]

    return {
        "processed_boxes": int(len(simulated_sequence)),
        "reentries_drop_ge_2_count": int(_as_int(mono.get("reentries_drop_ge_2_count"), 0)),
        "deep_drop_burden_sum": int(_as_int(mono.get("deep_drop_burden"), 0)),
        "max_layer_drop_max": int(_as_int(mono.get("max_layer_drop"), 0)),
        "reentries_total": int(_as_int(mono.get("reentries_total"), 0)),
        "prefix_tokens": [int(item["token"]) for item in committed_prefix],
        "prefix_projected_layers": [int(item["projected_layer"]) for item in committed_prefix],
        "prefix_len_committed": int(len(committed_prefix)),
        "rollout_len_committed": int(len(committed_rollout)),
        "prefix_fillability_mean": (
            float(sum(prefix_fillability_values) / max(1, len(prefix_fillability_values)))
            if prefix_fillability_values
            else 0.0
        ),
        "prefix_poison_risk_mean": (
            float(sum(prefix_poison_values) / max(1, len(prefix_poison_values)))
            if prefix_poison_values
            else 0.0
        ),
        "prefix_dominant_shape": Counter(prefix_shapes).most_common(1)[0][0] if prefix_shapes else "none",
        "failure_reason": str(failure_reason),
    }


def _rank_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -int(_as_int(candidate.get("processed_boxes"), 0)),
        int(_as_int(candidate.get("reentries_drop_ge_2_count"), 0)),
        int(_as_int(candidate.get("deep_drop_burden_sum"), 0)),
        int(_as_int(candidate.get("max_layer_drop_max"), 0)),
        -float(_as_float(candidate.get("prefix_fillability_mean"), 0.0)),
        str(candidate.get("candidate_id", "")),
    )


def audit_prefix_oracle_for_seed(
    *,
    seed: int,
    run_label: str,
    placements: Iterable[dict[str, Any]],
    params: dict[str, Any],
    prefix_len: int = 3,
    candidate_cap: int = 8,
    oracle_rollout_depth: int = 8,
    max_openings_per_seed: int = 3,
    layer_band_mm: int = 100,
    preview_max_candidates: int = 240,
) -> dict[str, Any]:
    sequence = _normalize_placements(placements)
    if not sequence:
        seed_summary = {
            "seed": int(seed),
            "run_label": str(run_label),
            "openings_detected": 0,
            "openings_audited": 0,
            "baseline_matches_best_count": 0,
            "baseline_mismatch_count": 0,
            "mean_gap_deep_drop_burden": 0.0,
            "mean_gap_reentries_drop_ge_2": 0.0,
        }
        return {"seed_summary": seed_summary, "opening_rows": [], "alternative_rows": []}

    mono_full = compute_layer_monotonicity_metrics(
        sequence,
        layer_band_mm=int(max(1, layer_band_mm)),
        layer_drop_audit=True,
    )
    step_trace = mono_full.get("layer_drop_step_trace", [])
    openings = [
        row
        for row in step_trace
        if isinstance(row, dict) and bool(row.get("opened_new_band", False))
    ]

    opening_rows: list[dict[str, Any]] = []
    alternative_rows: list[dict[str, Any]] = []
    max_openings = int(max(0, max_openings_per_seed))

    for opening_index, opening in enumerate(openings[:max_openings], start=1):
        opening_step = int(_as_int(opening.get("step"), -1))
        if opening_step < 0:
            continue

        history_rows = [row for row in sequence if int(row["_audit_step"]) < int(opening_step)]
        remaining_rows = [row for row in sequence if int(row["_audit_step"]) >= int(opening_step)]
        if not remaining_rows:
            continue

        opening_layer = int(_as_int(opening.get("chosen_layer"), _as_int(opening.get("band_id"), 0)))
        baseline_first_token = int(_as_int(remaining_rows[0].get("_audit_token"), 0))

        opening_state_model = _build_model(params)
        for row in history_rows:
            _commit_actual_placement(opening_state_model, row)

        opening_pending_boxes = [_placement_to_box(row) for row in remaining_rows]
        opening_feasible = _collect_feasible_candidates(
            opening_state_model,
            pending_boxes=opening_pending_boxes,
            layer_band_mm=int(max(1, layer_band_mm)),
            opening_layer=int(opening_layer),
            preview_max_candidates=int(max(1, preview_max_candidates)),
        )

        candidate_tokens: list[int] = [int(baseline_first_token)]
        for candidate in opening_feasible:
            token = int(candidate["token"])
            if token not in candidate_tokens:
                candidate_tokens.append(token)
            if len(candidate_tokens) >= int(max(1, candidate_cap)):
                break
        candidate_tokens = candidate_tokens[: int(max(1, candidate_cap))]

        opening_alternatives: list[dict[str, Any]] = []
        for idx, token in enumerate(candidate_tokens, start=1):
            evaluated = _simulate_alternative(
                history_rows=history_rows,
                remaining_rows=remaining_rows,
                params=params,
                layer_band_mm=int(max(1, layer_band_mm)),
                opening_layer=int(opening_layer),
                forced_first_token=int(token),
                prefix_len=int(max(1, prefix_len)),
                oracle_rollout_depth=int(max(0, oracle_rollout_depth)),
                preview_max_candidates=int(max(1, preview_max_candidates)),
            )

            first_meta = next((item for item in opening_feasible if int(item["token"]) == int(token)), None)
            opening_alternatives.append(
                {
                    "candidate_id": f"cand_{opening_index:03d}_{idx:03d}",
                    "seed": int(seed),
                    "run_label": str(run_label),
                    "opening_index": int(opening_index),
                    "opening_step": int(opening_step),
                    "opening_layer": int(opening_layer),
                    "first_token": int(token),
                    "is_baseline": bool(int(token) == int(baseline_first_token)),
                    "first_fillability_score": float(_as_float(first_meta.get("fillability_score"), 0.0)) if first_meta else 0.0,
                    "first_poison_risk_score": float(_as_float(first_meta.get("poison_risk_score"), 0.0)) if first_meta else 0.0,
                    "first_dominant_bad_residual_shape": str(first_meta.get("dominant_bad_residual_shape", "none")) if first_meta else "none",
                    "first_projected_layer": int(_as_int(first_meta.get("projected_layer"), opening_layer)) if first_meta else int(opening_layer),
                    "first_orientation_family": str(first_meta.get("orientation_family", "")) if first_meta else "",
                    "first_dims": (
                        f"{int(first_meta['length_mm'])}x{int(first_meta['width_mm'])}x{int(first_meta['height_mm'])}"
                        if first_meta
                        else ""
                    ),
                    **evaluated,
                }
            )

        if not opening_alternatives:
            continue

        opening_alternatives.sort(key=_rank_key)
        for rank, item in enumerate(opening_alternatives, start=1):
            item["rank"] = int(rank)
            alternative_rows.append(dict(item))

        best = opening_alternatives[0]
        baseline = next((item for item in opening_alternatives if bool(item.get("is_baseline", False))), None)
        if baseline is None:
            baseline = best

        opening_rows.append(
            {
                "seed": int(seed),
                "run_label": str(run_label),
                "opening_index": int(opening_index),
                "opening_step": int(opening_step),
                "opening_layer": int(opening_layer),
                "opening_feasible_candidate_count": int(len(opening_feasible)),
                "baseline_first_token": int(baseline_first_token),
                "baseline_rank": int(_as_int(baseline.get("rank"), 1)),
                "baseline_matches_best": bool(int(_as_int(baseline.get("rank"), 1)) == 1),
                "baseline_processed_boxes": int(_as_int(baseline.get("processed_boxes"), 0)),
                "baseline_reentries_drop_ge_2_count": int(_as_int(baseline.get("reentries_drop_ge_2_count"), 0)),
                "baseline_deep_drop_burden_sum": int(_as_int(baseline.get("deep_drop_burden_sum"), 0)),
                "baseline_max_layer_drop_max": int(_as_int(baseline.get("max_layer_drop_max"), 0)),
                "best_candidate_id": str(best.get("candidate_id", "")),
                "best_first_token": int(_as_int(best.get("first_token"), 0)),
                "best_processed_boxes": int(_as_int(best.get("processed_boxes"), 0)),
                "best_reentries_drop_ge_2_count": int(_as_int(best.get("reentries_drop_ge_2_count"), 0)),
                "best_deep_drop_burden_sum": int(_as_int(best.get("deep_drop_burden_sum"), 0)),
                "best_max_layer_drop_max": int(_as_int(best.get("max_layer_drop_max"), 0)),
                "gap_processed_boxes_best_minus_baseline": int(
                    _as_int(best.get("processed_boxes"), 0)
                    - _as_int(baseline.get("processed_boxes"), 0)
                ),
                "gap_reentries_drop_ge_2_baseline_minus_best": int(
                    _as_int(baseline.get("reentries_drop_ge_2_count"), 0)
                    - _as_int(best.get("reentries_drop_ge_2_count"), 0)
                ),
                "gap_deep_drop_burden_baseline_minus_best": int(
                    _as_int(baseline.get("deep_drop_burden_sum"), 0)
                    - _as_int(best.get("deep_drop_burden_sum"), 0)
                ),
                "gap_max_layer_drop_baseline_minus_best": int(
                    _as_int(baseline.get("max_layer_drop_max"), 0)
                    - _as_int(best.get("max_layer_drop_max"), 0)
                ),
                "opening_baseline_fillability_score": float(_as_float(baseline.get("first_fillability_score"), 0.0)),
                "opening_baseline_poison_risk_score": float(_as_float(baseline.get("first_poison_risk_score"), 0.0)),
                "opening_baseline_dominant_bad_residual_shape": str(
                    baseline.get("first_dominant_bad_residual_shape", "none")
                ),
                "opening_baseline_first_dims": str(baseline.get("first_dims", "")),
                "opening_best_first_dims": str(best.get("first_dims", "")),
                "opening_best_prefix_tokens": [int(x) for x in best.get("prefix_tokens", [])],
                "opening_baseline_prefix_tokens": [int(x) for x in baseline.get("prefix_tokens", [])],
            }
        )

    mismatch_rows = [row for row in opening_rows if not bool(row.get("baseline_matches_best", False))]
    seed_summary = {
        "seed": int(seed),
        "run_label": str(run_label),
        "openings_detected": int(len(openings)),
        "openings_audited": int(len(opening_rows)),
        "baseline_matches_best_count": int(sum(1 for row in opening_rows if bool(row.get("baseline_matches_best", False)))),
        "baseline_mismatch_count": int(len(mismatch_rows)),
        "mean_gap_deep_drop_burden": float(
            sum(int(row.get("gap_deep_drop_burden_baseline_minus_best", 0)) for row in opening_rows)
            / max(1, len(opening_rows))
        ),
        "mean_gap_reentries_drop_ge_2": float(
            sum(int(row.get("gap_reentries_drop_ge_2_baseline_minus_best", 0)) for row in opening_rows)
            / max(1, len(opening_rows))
        ),
        "bad_opening_dominant_shapes": Counter(
            str(row.get("opening_baseline_dominant_bad_residual_shape", "none"))
            for row in mismatch_rows
        ).most_common(3),
    }

    return {
        "seed_summary": seed_summary,
        "opening_rows": opening_rows,
        "alternative_rows": alternative_rows,
    }
