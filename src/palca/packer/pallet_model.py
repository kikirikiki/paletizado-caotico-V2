from __future__ import annotations

from dataclasses import dataclass, fields
import time
from typing import Any, Iterable

from ..domain.box import Box
from ..domain.pallet_spec import PalletSpec
from ..domain.placement import Placement, PlacementPreview
from .controls import ControlConfig, ControlStack, build_control_stack
from .layer import LayerState
from .maxrects2d import MaxRects2D, MaxRectsCandidate, Rect
from .scoring import ScoringWeights, packing_gain, fragmentation


@dataclass(frozen=True)
class _LayerCandidate:
    layer_id: int
    is_new_layer: bool
    candidate: MaxRectsCandidate
    rot90: bool
    length_mm: int
    width_mm: int
    z_mm: int
    next_height_mm: int
    packing_gain: float
    fragmentation: float
    score_delta: float
    placement: Placement
    debug: dict[str, Any]


@dataclass
class PalletStats:
    support_ratio_checks: int = 0
    support_ratio_rejects: int = 0
    corner_checks: int = 0
    corner_rejects: int = 0
    settle_checks: int = 0
    settle_adjustments_count: int = 0
    settle_total_mm: float = 0.0
    settle_max_mm: float = 0.0
    floating_boxes_count: int = 0

    def record_settle(self, settle_mm: float) -> None:
        self.settle_adjustments_count += 1
        self.settle_total_mm += float(settle_mm)
        self.settle_max_mm = max(self.settle_max_mm, float(settle_mm))


@dataclass(frozen=True)
class BalanceMetrics:
    quadrant_weights: list[float]
    com_offset_mm: tuple[float, float]
    com_offset_norm: float
    imbalance_ratio: float
    balance_score: float


@dataclass
class _PreviewBudget:
    max_candidates: int | None
    deadline: float | None
    candidates_checked: int = 0
    limit_hit: bool = False
    timeout_hit: bool = False

    def should_stop(self) -> bool:
        if self.max_candidates is not None and self.candidates_checked >= self.max_candidates:
            self.limit_hit = True
            return True
        if self.deadline is not None and time.perf_counter() >= self.deadline:
            self.timeout_hit = True
            return True
        return False


def _coerce_weight(value: Any, *, default: float) -> float:
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalize_scoring_weights(scoring_weights: Any | None) -> ScoringWeights:
    if scoring_weights is None:
        return ScoringWeights()
    if isinstance(scoring_weights, ScoringWeights):
        return scoring_weights

    return ScoringWeights(
        packing_gain_weight=_coerce_weight(
            getattr(scoring_weights, "packing_gain_weight", ScoringWeights.packing_gain_weight),
            default=ScoringWeights.packing_gain_weight,
        ),
        fragmentation_weight=_coerce_weight(
            getattr(scoring_weights, "fragmentation_weight", ScoringWeights.fragmentation_weight),
            default=ScoringWeights.fragmentation_weight,
        ),
        tower_penalty_ratio=_coerce_weight(
            getattr(scoring_weights, "tower_penalty_ratio", ScoringWeights.tower_penalty_ratio),
            default=ScoringWeights.tower_penalty_ratio,
        ),
        new_layer_penalty_ratio=_coerce_weight(
            getattr(
                scoring_weights,
                "new_layer_penalty_ratio",
                ScoringWeights.new_layer_penalty_ratio,
            ),
            default=ScoringWeights.new_layer_penalty_ratio,
        ),
    )


class PalletModel:
    def __init__(
        self,
        spec: PalletSpec | None = None,
        *,
        heuristic: str = "baf",
        scoring_weights: ScoringWeights | None = None,
        controls: ControlStack | None = None,
        control_config: ControlConfig | None = None,
    ) -> None:
        self.spec = spec or PalletSpec()
        self.heuristic = heuristic
        self.scoring_weights = _normalize_scoring_weights(scoring_weights)
        self.layers: list[LayerState] = []
        self.placements: list[Placement] = []
        self.stats = PalletStats()

        if controls is None:
            controls = build_control_stack(control_config)
        self.controls = controls

    @property
    def bin_area_mm2(self) -> int:
        return self.spec.bin_area_mm2

    def current_height_mm(self) -> int:
        if not self.layers:
            return 0
        last = self.layers[-1]
        return last.z_mm + last.height_mm

    def fork(self) -> "PalletModel":
        clone = PalletModel(
            spec=self.spec,
            heuristic=self.heuristic,
            scoring_weights=self.scoring_weights,
            controls=self.controls,
        )
        clone.layers = [
            LayerState(
                layer_id=int(layer.layer_id),
                z_mm=int(layer.z_mm),
                bin=layer.bin.clone(),
                height_mm=int(layer.height_mm),
            )
            for layer in self.layers
        ]
        clone.placements = list(self.placements)
        clone.stats = PalletStats(
            support_ratio_checks=int(self.stats.support_ratio_checks),
            support_ratio_rejects=int(self.stats.support_ratio_rejects),
            corner_checks=int(self.stats.corner_checks),
            corner_rejects=int(self.stats.corner_rejects),
            settle_checks=int(self.stats.settle_checks),
            settle_adjustments_count=int(self.stats.settle_adjustments_count),
            settle_total_mm=float(self.stats.settle_total_mm),
            settle_max_mm=float(self.stats.settle_max_mm),
            floating_boxes_count=int(self.stats.floating_boxes_count),
        )
        return clone

    def preview_place(
        self,
        box: Box,
        *,
        max_tries_per_item: int | None = None,
        max_candidates: int | None = None,
        max_seconds_per_item: float | None = None,
    ) -> PlacementPreview:
        length_mm = int(box.length_mm)
        width_mm = int(box.width_mm)
        height_mm = int(box.height_mm)

        candidates_limit = None
        for val in (max_tries_per_item, max_candidates):
            if val is None:
                continue
            if int(val) <= 0:
                continue
            if candidates_limit is None:
                candidates_limit = int(val)
            else:
                candidates_limit = min(candidates_limit, int(val))

        deadline = None
        if max_seconds_per_item is not None and float(max_seconds_per_item) > 0:
            deadline = time.perf_counter() + float(max_seconds_per_item)

        budget = None
        if candidates_limit is not None or deadline is not None:
            budget = _PreviewBudget(max_candidates=candidates_limit, deadline=deadline)

        if length_mm <= 0 or width_mm <= 0 or height_mm <= 0:
            return PlacementPreview(
                feasible=False,
                placement=None,
                packing_gain=0.0,
                fragmentation=0.0,
                infeasible_reason="OVERSIZE",
                debug={"reason": "non_positive_dims", "candidates_evaluated": 0},
            )

        bin_l = self.spec.bin_length_mm
        bin_w = self.spec.bin_width_mm

        if not self._fits_in_bin(length_mm, width_mm, bin_l, bin_w):
            if not self.spec.allow_rotate or not self._fits_in_bin(width_mm, length_mm, bin_l, bin_w):
                return PlacementPreview(
                    feasible=False,
                    placement=None,
                    packing_gain=0.0,
                    fragmentation=0.0,
                    infeasible_reason="OVERSIZE",
                    debug={
                        "bin": (bin_l, bin_w),
                        "box": (length_mm, width_mm),
                        "candidates_evaluated": 0,
                    },
                )

        if not self.controls.manifest.is_eligible(box, self):
            return PlacementPreview(
                feasible=False,
                placement=None,
                packing_gain=0.0,
                fragmentation=0.0,
                infeasible_reason="MANIFEST_BLOCKED",
                debug={"reason": "manifest_control", "candidates_evaluated": 0},
            )

        rejected_by_controls_active = 0
        rejected_by_controls_new = 0
        rejected_by_controls = 0
        evaluated_candidates: list[tuple[float, dict[str, Any]]] = []
        def _inv_maxrects_score(cand: _LayerCandidate, objective: float) -> tuple[int, ...]:
            score = getattr(cand.candidate, "score", None)
            if score is None:
                score_tuple = (
                    -(objective),
                    cand.candidate.x,
                    cand.candidate.y,
                    cand.candidate.w,
                    cand.candidate.h,
                )
            else:
                score_tuple = tuple(score)
            return tuple(-int(v) for v in score_tuple)

        objective_eps = 2e-3

        def _objective(cand: _LayerCandidate) -> float:
            return cand.packing_gain - cand.fragmentation + cand.score_delta

        def _tie_key(cand: _LayerCandidate, objective: float) -> tuple[tuple[int, ...], int, int, int, int, int]:
            inv_score = _inv_maxrects_score(cand, objective)
            return (
                inv_score,
                -cand.layer_id,
                -cand.candidate.x,
                -cand.candidate.y,
                -cand.candidate.w,
                -cand.candidate.h,
            )

        def _select_best(candidates: list[_LayerCandidate]) -> _LayerCandidate:
            best = candidates[0]
            best_objective = _objective(best)
            best_tie = _tie_key(best, best_objective)
            for cand in candidates[1:]:
                obj = _objective(cand)
                if obj > best_objective + objective_eps:
                    best = cand
                    best_objective = obj
                    best_tie = _tie_key(cand, obj)
                    continue
                if best_objective > obj + objective_eps:
                    continue
                tie = _tie_key(cand, obj)
                if tie > best_tie:
                    best = cand
                    best_objective = obj
                    best_tie = tie
            return best

        def _build_preview(best: _LayerCandidate) -> PlacementPreview:
            placement = best.placement
            debug = dict(best.debug)
            debug["rejected_by_controls"] = rejected_by_controls
            if budget is not None:
                debug["candidates_evaluated"] = int(budget.candidates_checked)
                if budget.limit_hit:
                    debug["candidate_limit_hit"] = True
                if budget.timeout_hit:
                    debug["timeout_hit"] = True
            return PlacementPreview(
                feasible=True,
                placement=placement,
                packing_gain=best.packing_gain,
                fragmentation=best.fragmentation,
                score_adjustment=best.score_delta,
                infeasible_reason=None,
                debug=debug,
            )

        active_layer = self.layers[-1] if self.layers else None
        if active_layer is not None:
            layer_candidates, layer_rejected, layer_evaluated, _ = self._preview_in_layer(
                active_layer,
                box,
                length_mm,
                width_mm,
                height_mm,
                is_new_layer=False,
                budget=budget,
            )
            rejected_by_controls_active += layer_rejected
            rejected_by_controls += layer_rejected
            evaluated_candidates.extend(layer_evaluated)
            all_candidates: list[_LayerCandidate] = []

            # ... cuando evalúas capa activa:
            if layer_candidates:
                all_candidates.extend(layer_candidates)

            # ... cuando evalúas capa nueva:
            if layer_candidates:
                all_candidates.extend(layer_candidates)

            if all_candidates:
                best = _select_best(all_candidates)
                return _build_preview(best)


        if self._can_open_new_layer(height_mm):
            new_layer_id = len(self.layers)
            z_mm = self.current_height_mm()
            new_layer = LayerState(
                layer_id=new_layer_id,
                z_mm=z_mm,
                bin=MaxRects2D(bin_l, bin_w, heuristic=self.heuristic),
            )
            layer_candidates, layer_rejected, layer_evaluated, _ = self._preview_in_layer(
                new_layer,
                box,
                length_mm,
                width_mm,
                height_mm,
                is_new_layer=True,
                budget=budget,
            )
            rejected_by_controls_new += layer_rejected
            rejected_by_controls += layer_rejected
            evaluated_candidates.extend(layer_evaluated)
            if layer_candidates:
                best = _select_best(layer_candidates)
                return _build_preview(best)

        can_open_new_layer = self._can_open_new_layer(height_mm)
        reason = "NO_SPACE"
        if not can_open_new_layer:
            reason = "HEIGHT_LIMIT"
        elif rejected_by_controls > 0:
            # Si SOLO falla estabilidad al intentar abrir capa nueva,
            # tratamos como NO_SPACE (pallet no admite seguir creciendo establemente ahora).
            if rejected_by_controls_active == 0 and rejected_by_controls_new > 0:
                reason = "NO_SPACE"
            else:
                reason = "STABILITY"
        if budget is not None:
            if budget.timeout_hit:
                reason = "TIMEOUT"
            elif budget.limit_hit:
                reason = "CANDIDATE_LIMIT"
        debug = {
            "height_used": self.current_height_mm(),
            "rejected_by_controls": rejected_by_controls,
            "rejected_by_controls_active": rejected_by_controls_active,
            "rejected_by_controls_new": rejected_by_controls_new,
        }
        if can_open_new_layer and rejected_by_controls_active == 0 and rejected_by_controls_new > 0:
            debug["new_layer_blocked_by_stability"] = True
        if budget is not None:
            debug["candidates_evaluated"] = int(budget.candidates_checked)
            if budget.limit_hit:
                debug["candidate_limit_hit"] = True
            if budget.timeout_hit:
                debug["timeout_hit"] = True
        if rejected_by_controls > 0 and evaluated_candidates:
            evaluated_candidates.sort(
                key=lambda item: (
                    -float(item[0]),
                    int(item[1].get("x", 0)),
                    int(item[1].get("y", 0)),
                    int(item[1].get("w", 0)),
                    int(item[1].get("h", 0)),
                )
            )
            debug["stability_candidates"] = [entry for _, entry in evaluated_candidates[:10]]
        return PlacementPreview(
            feasible=False,
            placement=None,
            packing_gain=0.0,
            fragmentation=0.0,
            infeasible_reason=reason,
            debug=debug,
        )

    def commit_place(self, preview: PlacementPreview) -> Placement:
        if not preview.feasible or preview.placement is None:
            raise ValueError("Cannot commit infeasible placement")

        placement = preview.placement
        if preview.debug and "settle_mm" in preview.debug:
            settle_mm = preview.debug.get("settle_mm")
            if isinstance(settle_mm, (int, float)) and settle_mm > 0:
                self.stats.record_settle(float(settle_mm))
        if placement.layer_id == len(self.layers):
            layer = LayerState(
                layer_id=placement.layer_id,
                z_mm=self.current_height_mm(),
                bin=MaxRects2D(self.spec.bin_length_mm, self.spec.bin_width_mm, heuristic=self.heuristic),
            )
            self.layers.append(layer)
        elif 0 <= placement.layer_id < len(self.layers):
            layer = self.layers[placement.layer_id]
        else:
            raise ValueError("Invalid layer id")

        cand = MaxRectsCandidate(
            x=placement.x_mm - self.spec.offset_mm,
            y=placement.y_mm - self.spec.offset_mm,
            w=placement.length_mm,
            h=placement.width_mm,
            score=(0,),
        )
        layer.bin.place(cand)
        layer.height_mm = max(layer.height_mm, placement.height_mm)
        self.placements.append(placement)
        com_supported, _ = self.com_support_info(placement, eps_mm=1e-6)
        if not com_supported:
            self.stats.floating_boxes_count += 1
        return placement

    def _can_open_new_layer(self, next_height_mm: int) -> bool:
        return self.current_height_mm() + int(next_height_mm) <= self.spec.max_height_mm

    def _fits_in_bin(self, length_mm: int, width_mm: int, bin_l: int, bin_w: int) -> bool:
        return length_mm <= bin_l and width_mm <= bin_w

    def _tower_penalty(self, placement: Placement, packing_gain: float) -> float:
        if packing_gain <= 0:
            return 0.0
        for prev in self.placements:
            if prev.z_mm == placement.z_mm:
                continue
            if (
                prev.x_mm == placement.x_mm
                and prev.y_mm == placement.y_mm
                and prev.length_mm == placement.length_mm
                and prev.width_mm == placement.width_mm
            ):
                return -float(self.scoring_weights.tower_penalty_ratio) * float(packing_gain)
        return 0.0

    def _preview_in_layer(
        self,
        layer: LayerState,
        box: Box,
        length_mm: int,
        width_mm: int,
        height_mm: int,
        *,
        is_new_layer: bool,
        budget: _PreviewBudget | None = None,
    ) -> tuple[list[_LayerCandidate], int, list[tuple[float, dict[str, Any]]], bool]:
        candidates: list[_LayerCandidate] = []
        rejected_by_controls = 0
        evaluated_candidates: list[tuple[float, dict[str, Any]]] = []
        if budget is not None and budget.should_stop():
            return candidates, rejected_by_controls, evaluated_candidates, True
        for rot90, (l_mm, w_mm) in self._orientations(length_mm, width_mm):
            next_height = max(layer.height_mm, height_mm)
            if layer.z_mm + next_height > self.spec.max_height_mm:
                continue
            base_candidates = list(
                self.controls.point.candidates(
                    layer=layer,
                    length_mm=l_mm,
                    width_mm=w_mm,
                    height_mm=height_mm,
                    is_new_layer=is_new_layer,
                )
            )
            if is_new_layer:
                seeded_points = self._seed_new_layer_points(
                    layer.z_mm,
                    l_mm,
                    w_mm,
                    free_rects=layer.bin.free_rects,
                )
                if seeded_points:
                    seeded = [
                        MaxRectsCandidate(x, y, int(l_mm), int(w_mm), score=(0,))
                        for x, y in seeded_points
                    ]
                    seen: set[tuple[int, int, int, int]] = set()
                    merged: list[MaxRectsCandidate] = []
                    for cand in base_candidates + seeded:
                        key = (int(cand.x), int(cand.y), int(cand.w), int(cand.h))
                        if key in seen:
                            continue
                        seen.add(key)
                        merged.append(cand)
                    base_candidates = merged
            for cand in base_candidates:
                if budget is not None and budget.should_stop():
                    return candidates, rejected_by_controls, evaluated_candidates, True
                if budget is not None:
                    budget.candidates_checked += 1
                free_after = layer.bin.simulate_place(cand)
                gain = packing_gain(l_mm * w_mm, self.bin_area_mm2)
                frag = fragmentation(free_after)
                weighted_gain = self.scoring_weights.packing_gain_weight * gain
                weighted_frag = self.scoring_weights.fragmentation_weight * frag
                debug = {
                    "layer_id": layer.layer_id,
                    "is_new_layer": is_new_layer,
                    "free_rects": len(layer.bin.free_rects),
                    "free_rects_after": len(free_after),
                }

                base_placement = Placement(
                    x_mm=cand.x + self.spec.offset_mm,
                    y_mm=cand.y + self.spec.offset_mm,
                    z_mm=layer.z_mm,
                    rot90=rot90,
                    layer_id=layer.layer_id,
                    length_mm=l_mm,
                    width_mm=w_mm,
                    height_mm=height_mm,
                    box_id=box.box_id,
                    weight_kg=box.effective_weight_kg(),
                    loadbear=box.loadbear,
                    priority=box.priority,
                )
                adjusted = base_placement
                score_delta = 0.0
                feasible = True
                reject_reason: str | None = None
                for control in self.controls.placement_controls:
                    result = control.evaluate(
                        pallet=self,
                        box=box,
                        placement=adjusted,
                    )
                    debug.update(result.debug)
                    score_delta += float(result.score_delta)
                    adjusted = result.placement
                    if not result.feasible:
                        rejected_by_controls += 1
                        feasible = False
                        reject_reason = result.reason
                        break

                objective = weighted_gain - weighted_frag + score_delta
                if feasible:
                    tower_penalty = self._tower_penalty(adjusted, weighted_gain)
                    if tower_penalty:
                        score_delta += tower_penalty
                        objective += tower_penalty
                        debug["tower_penalty"] = float(tower_penalty)
                        # Penaliza abrir capa nueva
                    if is_new_layer:
                        p = -float(self.scoring_weights.new_layer_penalty_ratio) * float(weighted_gain)
                        score_delta += p
                        objective += p
                        debug["new_layer_penalty"] = float(p)

                    # Penaliza aumentar la altura de la capa activa (mezclar alturas)
                    if (not is_new_layer) and next_height > layer.height_mm:
                        dh = int(next_height - layer.height_mm)
                        p = (
                            -float(self.scoring_weights.height_increase_penalty_ratio)
                            * float(weighted_gain)
                            * (float(dh) / float(self.spec.max_height_mm))
                        )
                        score_delta += p
                        objective += p
                        debug["height_increase_mm"] = dh
                        debug["height_increase_penalty"] = float(p)


                candidate_info: dict[str, Any] = {
                    "x": int(adjusted.x_mm),
                    "y": int(adjusted.y_mm),
                    "w": int(adjusted.length_mm),
                    "h": int(adjusted.width_mm),
                    "layer_id": int(layer.layer_id),
                    "is_new_layer": bool(is_new_layer),
                    "reject_reason": reject_reason,
                }
                if "support_ratio" in debug:
                    candidate_info["support_ratio"] = float(debug["support_ratio"])
                if "com_supported" in debug:
                    candidate_info["com_supported"] = bool(debug["com_supported"])
                if "corners_supported" in debug:
                    candidate_info["corners_supported"] = bool(debug["corners_supported"])
                if "supported_overlaps_count" in debug:
                    candidate_info["supported_overlaps_count"] = int(
                        debug["supported_overlaps_count"]
                    )
                if "support_area_mm2" in debug:
                    candidate_info["support_area_mm2"] = float(debug["support_area_mm2"])
                evaluated_candidates.append((float(objective), candidate_info))

                if not feasible:
                    continue

                candidates.append(
                    _LayerCandidate(
                        layer_id=layer.layer_id,
                        is_new_layer=is_new_layer,
                        candidate=cand,
                        rot90=rot90,
                        length_mm=l_mm,
                        width_mm=w_mm,
                        z_mm=adjusted.z_mm,
                        next_height_mm=next_height,
                        packing_gain=weighted_gain,
                        fragmentation=weighted_frag,
                        score_delta=score_delta,
                        placement=adjusted,
                        debug=debug,
                    )
                )
        return candidates, rejected_by_controls, evaluated_candidates, False

    def _seed_new_layer_points(
        self,
        layer_z_mm: int,
        length_mm: int,
        width_mm: int,
        *,
        free_rects: Iterable[Rect] | None = None,
    ) -> list[tuple[int, int]]:
        if not self.placements:
            return []

        l_mm = int(length_mm)
        w_mm = int(width_mm)
        if l_mm <= 0 or w_mm <= 0:
            return []

        bin_l = int(self.spec.bin_length_mm)
        bin_w = int(self.spec.bin_width_mm)
        max_x = bin_l - l_mm
        max_y = bin_w - w_mm
        if max_x < 0 or max_y < 0:
            return []

        free_rects_list = list(free_rects) if free_rects is not None else None
        offset = int(self.spec.offset_mm)
        layer_z = int(layer_z_mm)
        points: set[tuple[int, int]] = set()
        for placement in self.placements:
            top_z = int(placement.z_mm) + int(placement.height_mm)
            if top_z != layer_z:
                continue
            x0 = int(placement.x_mm) - offset
            y0 = int(placement.y_mm) - offset
            x1 = x0 + int(placement.length_mm)
            y1 = y0 + int(placement.width_mm)
            for x, y in (
                (x0, y0),
                (x1 - l_mm, y0),
                (x0, y1 - w_mm),
                (x1 - l_mm, y1 - w_mm),
            ):
                clamped_x = min(max(int(x), 0), max_x)
                clamped_y = min(max(int(y), 0), max_y)
                if not (0 <= clamped_x <= max_x and 0 <= clamped_y <= max_y):
                    continue
                if free_rects_list is not None:
                    candidate_rect = Rect(clamped_x, clamped_y, l_mm, w_mm)
                    if not any(rect.contains(candidate_rect) for rect in free_rects_list):
                        continue
                points.add((clamped_x, clamped_y))

        return sorted(points, key=lambda pt: (pt[0], pt[1]))

    def _orientations(self, length_mm: int, width_mm: int) -> list[tuple[bool, tuple[int, int]]]:
        orientations = [(False, (length_mm, width_mm))]
        if self.spec.allow_rotate and length_mm != width_mm:
            orientations.append((True, (width_mm, length_mm)))
        return orientations

    def _best_by_maxrects_score(self, candidates: list[_LayerCandidate]) -> list[_LayerCandidate]:
        def _layer_key(cand: _LayerCandidate) -> tuple[float, ...]:
            score = getattr(cand.candidate, "score", None)
            if score is None:
                objective = cand.packing_gain - cand.fragmentation + cand.score_delta
                return (
                    -(objective),
                    cand.candidate.x,
                    cand.candidate.y,
                    cand.candidate.w,
                    cand.candidate.h,
                )
            return tuple(score) + (
                cand.candidate.x,
                cand.candidate.y,
                cand.candidate.w,
                cand.candidate.h,
            )

        best_by_layer: dict[int, _LayerCandidate] = {}
        for cand in candidates:
            prev = best_by_layer.get(cand.layer_id)
            if prev is None or _layer_key(cand) < _layer_key(prev):
                best_by_layer[cand.layer_id] = cand
        return list(best_by_layer.values())

    def support_surface_ratio(self, placement: Placement, *, eps_mm: float) -> tuple[float, float]:
        if placement.z_mm <= eps_mm:
            return 1.0, float(placement.length_mm * placement.width_mm)

        base_area = float(placement.length_mm * placement.width_mm)
        if base_area <= 0:
            return 0.0, 0.0

        support_area = 0.0
        for below in self._supporting_placements(placement, eps_mm=eps_mm):
            overlap = _overlap_area(placement, below)
            support_area += overlap

        ratio = support_area / base_area if base_area > 0 else 0.0
        return ratio, support_area

    def corners_supported(self, placement: Placement, *, eps_mm: float) -> bool:
        if placement.z_mm <= eps_mm:
            return True

        corners = [
            (placement.x_mm, placement.y_mm),
            (placement.x_mm + placement.length_mm, placement.y_mm),
            (placement.x_mm, placement.y_mm + placement.width_mm),
            (placement.x_mm + placement.length_mm, placement.y_mm + placement.width_mm),
        ]

        supports = list(self._supporting_placements(placement, eps_mm=eps_mm))
        for cx, cy in corners:
            if not _corner_supported(cx, cy, supports, eps_mm=eps_mm):
                return False
        return True

    def com_support_info(self, placement: Placement, *, eps_mm: float) -> tuple[bool, int]:
        if placement.z_mm <= eps_mm:
            return True, 0

        cx = float(placement.x_mm) + float(placement.length_mm) / 2.0
        cy = float(placement.y_mm) + float(placement.width_mm) / 2.0

        ax0 = float(placement.x_mm)
        ay0 = float(placement.y_mm)
        ax1 = float(placement.x_mm + placement.length_mm)
        ay1 = float(placement.y_mm + placement.width_mm)

        eps = float(eps_mm)
        com_supported = False
        overlaps_count = 0
        for below in self._supporting_placements(placement, eps_mm=eps_mm):
            bx0 = float(below.x_mm)
            by0 = float(below.y_mm)
            bx1 = float(below.x_mm + below.length_mm)
            by1 = float(below.y_mm + below.width_mm)

            x0 = max(ax0, bx0)
            y0 = max(ay0, by0)
            x1 = min(ax1, bx1)
            y1 = min(ay1, by1)
            if x1 <= x0 or y1 <= y0:
                continue

            overlaps_count += 1
            if (x0 - eps) <= cx <= (x1 + eps) and (y0 - eps) <= cy <= (y1 + eps):
                com_supported = True

        return com_supported, int(overlaps_count)

    def com_supported(self, placement: Placement, *, eps_mm: float) -> bool:
        return self.com_support_info(placement, eps_mm=eps_mm)[0]

    def settle_placement(
        self,
        placement: Placement,
        *,
        eps_mm: float,
        snap_grid: bool,
        grid_mm: int | None,
        max_iter: int | None = None,
        timeout_ms: int | None = None,
    ) -> tuple[Placement, float]:
        self.stats.settle_checks += 1
        if placement.z_mm <= eps_mm:
            return placement, 0.0

        base_x0 = placement.x_mm
        base_y0 = placement.y_mm
        base_x1 = placement.x_mm + placement.length_mm
        base_y1 = placement.y_mm + placement.width_mm

        candidates: list[int] = [0]
        deadline = None
        if timeout_ms is not None and int(timeout_ms) > 0:
            deadline = time.perf_counter() + (float(timeout_ms) / 1000.0)
        max_iter = int(max_iter) if max_iter is not None else None
        for idx, other in enumerate(self.placements):
            if max_iter is not None and max_iter > 0 and idx >= max_iter:
                break
            if deadline is not None and time.perf_counter() >= deadline:
                break
            if not _overlaps_xy_bounds(
                base_x0,
                base_y0,
                base_x1,
                base_y1,
                other.x_mm,
                other.y_mm,
                other.x_mm + other.length_mm,
                other.y_mm + other.width_mm,
            ):
                continue
            candidates.append(int(other.z_mm + other.height_mm))

        target_z = placement.z_mm
        chosen_z = placement.z_mm
        for z in sorted(set(candidates), reverse=True):
            if z > target_z + eps_mm:
                continue
            tentative = Placement(
                x_mm=placement.x_mm,
                y_mm=placement.y_mm,
                z_mm=int(z),
                rot90=placement.rot90,
                layer_id=placement.layer_id,
                length_mm=placement.length_mm,
                width_mm=placement.width_mm,
                height_mm=placement.height_mm,
                box_id=placement.box_id,
                weight_kg=placement.weight_kg,
                loadbear=placement.loadbear,
                priority=placement.priority,
            )
            if not self._collides(tentative, eps_mm=eps_mm):
                chosen_z = int(z)
                break

        if snap_grid and grid_mm is not None and grid_mm > 0:
            snapped = round(chosen_z / float(grid_mm)) * float(grid_mm)
            if abs(snapped - chosen_z) <= eps_mm:
                chosen_z = int(round(snapped))

        if chosen_z == placement.z_mm:
            return placement, 0.0

        adjusted = Placement(
            x_mm=placement.x_mm,
            y_mm=placement.y_mm,
            z_mm=chosen_z,
            rot90=placement.rot90,
            layer_id=placement.layer_id,
            length_mm=placement.length_mm,
            width_mm=placement.width_mm,
            height_mm=placement.height_mm,
            box_id=placement.box_id,
            weight_kg=placement.weight_kg,
            loadbear=placement.loadbear,
            priority=placement.priority,
        )
        return adjusted, float(placement.z_mm - chosen_z)

    def loadbear_ratio(self, placement: Placement, *, loadbear_factor: float) -> tuple[float, float]:
        weight = _placement_weight(placement)
        if weight <= 0:
            return 0.0, 0.0
        if placement.z_mm <= 0:
            return 0.0, float("inf")

        base_area = float(placement.length_mm * placement.width_mm)
        if base_area <= 0:
            return 0.0, 0.0

        capacity = 0.0
        for below in self._supporting_placements(placement, eps_mm=1e-6):
            overlap = _overlap_area(placement, below)
            if overlap <= 0:
                continue
            support_fraction = overlap / base_area
            capacity += support_fraction * _placement_loadbear(below, loadbear_factor)

        if capacity <= 0:
            return float("inf"), 0.0
        return float(weight / capacity), float(capacity)

    def balance_metrics(self, extra_placements: Iterable[Placement] | None = None) -> BalanceMetrics:
        placements = list(self.placements)
        if extra_placements:
            placements.extend(extra_placements)

        if not placements:
            return BalanceMetrics(
                quadrant_weights=[0.0, 0.0, 0.0, 0.0],
                com_offset_mm=(0.0, 0.0),
                com_offset_norm=0.0,
                imbalance_ratio=0.0,
                balance_score=1.0,
            )

        bin_l = float(self.spec.bin_length_mm)
        bin_w = float(self.spec.bin_width_mm)
        center_x = float(self.spec.offset_mm) + bin_l / 2.0
        center_y = float(self.spec.offset_mm) + bin_w / 2.0

        quadrant_weights = [0.0, 0.0, 0.0, 0.0]
        total_weight = 0.0
        sum_x = 0.0
        sum_y = 0.0

        for placement in placements:
            weight = _placement_weight(placement)
            if weight <= 0:
                continue
            cx = float(placement.x_mm) + float(placement.length_mm) / 2.0
            cy = float(placement.y_mm) + float(placement.width_mm) / 2.0
            total_weight += weight
            sum_x += weight * cx
            sum_y += weight * cy

            idx = _quadrant_index(cx, cy, center_x, center_y)
            quadrant_weights[idx] += weight

        if total_weight <= 0:
            return BalanceMetrics(
                quadrant_weights=quadrant_weights,
                com_offset_mm=(0.0, 0.0),
                com_offset_norm=0.0,
                imbalance_ratio=0.0,
                balance_score=1.0,
            )

        com_x = sum_x / total_weight
        com_y = sum_y / total_weight
        dx = com_x - center_x
        dy = com_y - center_y
        max_radius = (bin_l**2 + bin_w**2) ** 0.5 / 2.0
        com_offset_norm = ((dx**2 + dy**2) ** 0.5) / max_radius if max_radius > 0 else 0.0
        imbalance_ratio = (max(quadrant_weights) - min(quadrant_weights)) / total_weight
        balance_score = max(0.0, 1.0 - 0.5 * (imbalance_ratio + com_offset_norm))

        return BalanceMetrics(
            quadrant_weights=quadrant_weights,
            com_offset_mm=(float(dx), float(dy)),
            com_offset_norm=float(com_offset_norm),
            imbalance_ratio=float(imbalance_ratio),
            balance_score=float(balance_score),
        )

    def _supporting_placements(self, placement: Placement, *, eps_mm: float) -> Iterable[Placement]:
        target_z = placement.z_mm
        if target_z <= eps_mm:
            return []
        for other in self.placements:
            top_z = other.z_mm + other.height_mm
            if abs(float(top_z) - float(target_z)) > eps_mm:
                continue
            if _overlaps_xy(placement, other):
                yield other

    def _collides(self, placement: Placement, *, eps_mm: float) -> bool:
        for other in self.placements:
            if not _overlaps_xy(placement, other):
                continue
            if _overlaps_z(placement, other, eps_mm=eps_mm):
                return True
        return False


def _coerce_weight(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalize_scoring_weights(scoring_weights: Any) -> ScoringWeights:
    if scoring_weights is None:
        return ScoringWeights()
    if isinstance(scoring_weights, ScoringWeights):
        return scoring_weights

    kwargs: dict[str, Any] = {}
    for f in fields(ScoringWeights):
        default = getattr(ScoringWeights, f.name)
        value = getattr(scoring_weights, f.name, default)
        if isinstance(default, (int, float)):
            kwargs[f.name] = _coerce_weight(value, default=float(default))
        else:
            kwargs[f.name] = value
    return ScoringWeights(**kwargs)


def _overlaps_xy(a: Placement, b: Placement) -> bool:
    return _overlaps_xy_bounds(
        a.x_mm,
        a.y_mm,
        a.x_mm + a.length_mm,
        a.y_mm + a.width_mm,
        b.x_mm,
        b.y_mm,
        b.x_mm + b.length_mm,
        b.y_mm + b.width_mm,
    )


def _overlaps_xy_bounds(
    ax0: int,
    ay0: int,
    ax1: int,
    ay1: int,
    bx0: int,
    by0: int,
    bx1: int,
    by1: int,
) -> bool:
    return not (ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0)


def _overlaps_z(a: Placement, b: Placement, *, eps_mm: float) -> bool:
    a0 = float(a.z_mm)
    a1 = float(a.z_mm + a.height_mm)
    b0 = float(b.z_mm)
    b1 = float(b.z_mm + b.height_mm)
    return not (a1 <= b0 + eps_mm or b1 <= a0 + eps_mm)


def _overlap_area(a: Placement, b: Placement) -> float:
    x0 = max(a.x_mm, b.x_mm)
    y0 = max(a.y_mm, b.y_mm)
    x1 = min(a.x_mm + a.length_mm, b.x_mm + b.length_mm)
    y1 = min(a.y_mm + a.width_mm, b.y_mm + b.width_mm)
    dx = max(0, x1 - x0)
    dy = max(0, y1 - y0)
    return float(dx * dy)


def _corner_supported(x: int, y: int, supports: Iterable[Placement], *, eps_mm: float) -> bool:
    for sup in supports:
        if (
            x >= sup.x_mm - eps_mm
            and x <= sup.x_mm + sup.length_mm + eps_mm
            and y >= sup.y_mm - eps_mm
            and y <= sup.y_mm + sup.width_mm + eps_mm
        ):
            return True
    return False


def _placement_weight(placement: Placement) -> float:
    if placement.weight_kg is not None:
        return float(placement.weight_kg)
    volume = float(placement.length_mm * placement.width_mm * placement.height_mm)
    return volume / 1_000_000.0


def _placement_loadbear(placement: Placement, factor: float) -> float:
    if placement.loadbear is not None:
        return float(placement.loadbear)
    return _placement_weight(placement) * float(factor)


def _quadrant_index(x: float, y: float, cx: float, cy: float) -> int:
    right = x >= cx
    top = y >= cy
    if right and top:
        return 0
    if (not right) and top:
        return 1
    if (not right) and (not top):
        return 2
    return 3
