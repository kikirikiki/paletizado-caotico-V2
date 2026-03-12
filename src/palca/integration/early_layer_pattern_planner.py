from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from ..domain.box import Box
from ..domain.placement import PlacementPreview
from ..packer.pallet_model import PalletModel


@dataclass(frozen=True)
class PlannedLayerPlacement:
    ramp_id: int
    box_id: int | str
    pallet_id: int | str
    layer_id: int
    z_mm: int


@dataclass(frozen=True)
class LayerOpeningPlan:
    pallet_id: int | str
    layer_id: int
    z_mm: int
    placements: tuple[PlannedLayerPlacement, ...]


@dataclass(frozen=True)
class _LayerCandidate:
    ramp_id: int
    box: Box


@dataclass(frozen=True)
class _Node:
    pallet: PalletModel
    remaining: tuple[_LayerCandidate, ...]
    placements: tuple[PlannedLayerPlacement, ...]
    opening_layer_id: int
    opening_z_mm: int
    future_same_layer_moves: int
    residual_fill_area: int
    largest_free_rect_area: int
    fragmentation_penalty: int
    min_support_ratio: float


class EarlyLayerPatternPlanner:
    def __init__(
        self,
        *,
        prefix_depth: int = 3,
        beam_width: int = 4,
        candidate_cap: int = 8,
    ) -> None:
        self.prefix_depth = max(1, int(prefix_depth))
        self.beam_width = max(1, int(beam_width))
        self.candidate_cap = max(1, int(candidate_cap))

    def plan_opening(
        self,
        *,
        pallet_id: int | str,
        pallet: PalletModel,
        ramp_queues: Mapping[int, Sequence[Box]],
        preview_place_fn: Callable[[PalletModel, Box], PlacementPreview],
    ) -> LayerOpeningPlan | None:
        current_top_z = self._current_top_z_mm(pallet)

        all_candidates = self._collect_candidates(
            pallet_id=pallet_id,
            ramp_queues=ramp_queues,
        )
        starters_raw: list[tuple[_LayerCandidate, PlacementPreview]] = []
        has_active_layer_candidate = False
        for candidate in all_candidates:
            preview = preview_place_fn(pallet, candidate.box)
            if not bool(getattr(preview, "feasible", False)):
                continue
            preview_z_mm = self._preview_z_mm(preview)
            if preview_z_mm is None:
                continue
            if int(preview_z_mm) == int(current_top_z):
                has_active_layer_candidate = True
            if int(preview_z_mm) > int(current_top_z):
                starters_raw.append((candidate, preview))

        if has_active_layer_candidate or not starters_raw:
            return None

        opening_z_mm = min(int(self._preview_z_mm(preview) or 0) for _, preview in starters_raw)
        starters = [cand for cand, preview in starters_raw if int(self._preview_z_mm(preview) or 0) == int(opening_z_mm)]
        if not starters:
            return None
        opening_layer_id = self._preview_layer_id(starters_raw[0][1])
        if opening_layer_id is None:
            opening_layer_id = len(list(getattr(pallet, "layers", []) or []))

        return self._plan_for_target_layer_at_z(
            pallet_id=pallet_id,
            pallet=pallet,
            all_candidates=all_candidates,
            target_layer_id=int(opening_layer_id),
            target_z_mm=int(opening_z_mm),
            preview_place_fn=preview_place_fn,
        )

    def plan_for_layer(
        self,
        *,
        pallet_id: int | str,
        pallet: PalletModel,
        ramp_queues: Mapping[int, Sequence[Box]],
        target_layer_id: int,
        target_z_mm: int | None = None,
        preview_place_fn: Callable[[PalletModel, Box], PlacementPreview],
    ) -> LayerOpeningPlan | None:
        all_candidates = self._collect_candidates(
            pallet_id=pallet_id,
            ramp_queues=ramp_queues,
        )
        if target_z_mm is not None:
            return self._plan_for_target_layer_at_z(
                pallet_id=pallet_id,
                pallet=pallet,
                all_candidates=all_candidates,
                target_layer_id=int(target_layer_id),
                target_z_mm=int(target_z_mm),
                preview_place_fn=preview_place_fn,
            )

        candidate_z_values: set[int] = set()
        for candidate in all_candidates:
            preview = preview_place_fn(pallet, candidate.box)
            if not bool(getattr(preview, "feasible", False)):
                continue
            preview_layer_id = self._preview_layer_id(preview)
            preview_z_mm = self._preview_z_mm(preview)
            if preview_layer_id is None or preview_z_mm is None:
                continue
            if int(preview_layer_id) != int(target_layer_id):
                continue
            candidate_z_values.add(int(preview_z_mm))

        best_plan: LayerOpeningPlan | None = None
        for candidate_z in sorted(candidate_z_values):
            plan = self._plan_for_target_layer_at_z(
                pallet_id=pallet_id,
                pallet=pallet,
                all_candidates=all_candidates,
                target_layer_id=int(target_layer_id),
                target_z_mm=int(candidate_z),
                preview_place_fn=preview_place_fn,
            )
            if plan is None:
                continue
            if best_plan is None:
                best_plan = plan
                continue
            if len(plan.placements) > len(best_plan.placements):
                best_plan = plan
                continue
            if len(plan.placements) == len(best_plan.placements) and int(plan.z_mm) < int(best_plan.z_mm):
                best_plan = plan
        if best_plan is None:
            return None
        return best_plan

    def _plan_for_target_layer(
        self,
        *,
        pallet_id: int | str,
        pallet: PalletModel,
        all_candidates: Sequence[_LayerCandidate],
        target_layer_id: int,
        target_z_mm: int,
        preview_place_fn: Callable[[PalletModel, Box], PlacementPreview],
    ) -> LayerOpeningPlan | None:
        return self._plan_for_target_layer_at_z(
            pallet_id=pallet_id,
            pallet=pallet,
            all_candidates=all_candidates,
            target_layer_id=int(target_layer_id),
            target_z_mm=int(target_z_mm),
            preview_place_fn=preview_place_fn,
        )

    @staticmethod
    def _collect_candidates(
        *,
        pallet_id: int | str,
        ramp_queues: Mapping[int, Sequence[Box]],
    ) -> list[_LayerCandidate]:
        out: list[_LayerCandidate] = []
        for ramp_id in sorted(ramp_queues):
            queue = list(ramp_queues.get(ramp_id, []) or [])
            for box in queue:
                if getattr(box, "destination", None) != pallet_id:
                    continue
                out.append(_LayerCandidate(ramp_id=int(ramp_id), box=box))
        return out

    def _plan_for_target_layer_at_z(
        self,
        *,
        pallet_id: int | str,
        pallet: PalletModel,
        all_candidates: Sequence[_LayerCandidate],
        target_layer_id: int,
        target_z_mm: int,
        preview_place_fn: Callable[[PalletModel, Box], PlacementPreview],
    ) -> LayerOpeningPlan | None:
        root = self._build_node(
            pallet=copy.deepcopy(pallet),
            remaining=tuple(all_candidates),
            placements=(),
            opening_layer_id=int(target_layer_id),
            opening_z_mm=int(target_z_mm),
            min_support_ratio=1.0,
            preview_place_fn=preview_place_fn,
        )
        beam: list[_Node] = [root]
        best: _Node | None = None

        for _depth in range(self.prefix_depth):
            next_beam: list[_Node] = []
            for node in beam:
                expansions = self._expand_node(
                    node=node,
                    opening_layer_id=int(target_layer_id),
                    opening_z_mm=int(target_z_mm),
                    preview_place_fn=preview_place_fn,
                    pallet_id=pallet_id,
                )
                next_beam.extend(expansions)
            if not next_beam:
                break

            next_beam.sort(key=self._rank_key, reverse=True)
            beam = next_beam[: self.beam_width]
            if best is None or self._final_rank_key(beam[0]) > self._final_rank_key(best):
                best = beam[0]

        if best is None or not best.placements:
            return None
        return LayerOpeningPlan(
            pallet_id=pallet_id,
            layer_id=int(target_layer_id),
            z_mm=int(target_z_mm),
            placements=tuple(best.placements),
        )

    def _expand_node(
        self,
        *,
        node: _Node,
        opening_layer_id: int,
        opening_z_mm: int,
        preview_place_fn: Callable[[PalletModel, Box], PlacementPreview],
        pallet_id: int | str,
    ) -> list[_Node]:
        feasible: list[tuple[tuple[float, float, float, float], int, PlacementPreview, _LayerCandidate]] = []
        for idx, candidate in enumerate(node.remaining):
            preview = preview_place_fn(node.pallet, candidate.box)
            if not bool(getattr(preview, "feasible", False)):
                continue
            layer_id = self._preview_layer_id(preview)
            z_mm = self._preview_z_mm(preview)
            if layer_id is None or z_mm is None:
                continue
            if int(layer_id) != int(opening_layer_id) or int(z_mm) != int(opening_z_mm):
                continue
            support_ratio = self._support_ratio(preview)
            area = int(getattr(candidate.box, "length_mm", 0) or 0) * int(getattr(candidate.box, "width_mm", 0) or 0)
            local_key = (
                float(getattr(preview, "packing_gain", 0.0) or 0.0),
                -float(getattr(preview, "fragmentation", 0.0) or 0.0),
                float(getattr(preview, "score_adjustment", 0.0) or 0.0),
                float(support_ratio),
            )
            feasible.append((local_key, int(area), preview, candidate))

        if not feasible:
            return []

        feasible.sort(key=lambda item: (item[0], item[1]), reverse=True)
        expansions: list[_Node] = []
        for _local_key, _area, preview, candidate in feasible[: self.candidate_cap]:
            try:
                pallet_clone = copy.deepcopy(node.pallet)
                preview_on_clone = preview_place_fn(pallet_clone, candidate.box)
                if not bool(getattr(preview_on_clone, "feasible", False)):
                    continue
                clone_layer_id = self._preview_layer_id(preview_on_clone)
                clone_z_mm = self._preview_z_mm(preview_on_clone)
                if clone_layer_id != int(opening_layer_id) or clone_z_mm != int(opening_z_mm):
                    continue
                pallet_clone.commit_place(preview_on_clone)
            except Exception:
                continue

            remaining = tuple(item for item in node.remaining if item is not candidate)
            planned = PlannedLayerPlacement(
                ramp_id=int(candidate.ramp_id),
                box_id=candidate.box.box_id,
                pallet_id=pallet_id,
                layer_id=int(opening_layer_id),
                z_mm=int(opening_z_mm),
            )
            child = self._build_node(
                pallet=pallet_clone,
                remaining=remaining,
                placements=tuple((*node.placements, planned)),
                opening_layer_id=int(opening_layer_id),
                opening_z_mm=int(opening_z_mm),
                min_support_ratio=min(float(node.min_support_ratio), float(self._support_ratio(preview))),
                preview_place_fn=preview_place_fn,
            )
            expansions.append(child)

        return expansions

    def _build_node(
        self,
        *,
        pallet: PalletModel,
        remaining: tuple[_LayerCandidate, ...],
        placements: tuple[PlannedLayerPlacement, ...],
        opening_layer_id: int,
        opening_z_mm: int,
        min_support_ratio: float,
        preview_place_fn: Callable[[PalletModel, Box], PlacementPreview],
    ) -> _Node:
        future_same_layer_moves = 0
        residual_fill_area = 0
        for candidate in remaining:
            preview = preview_place_fn(pallet, candidate.box)
            if not bool(getattr(preview, "feasible", False)):
                continue
            if self._preview_layer_id(preview) != int(opening_layer_id):
                continue
            if self._preview_z_mm(preview) != int(opening_z_mm):
                continue
            future_same_layer_moves += 1
            residual_fill_area += int(getattr(candidate.box, "length_mm", 0) or 0) * int(
                getattr(candidate.box, "width_mm", 0) or 0
            )

        largest_free_rect_area = 0
        fragmentation_penalty = 0
        layers = list(getattr(pallet, "layers", []) or [])
        if 0 <= int(opening_layer_id) < len(layers):
            layer = layers[int(opening_layer_id)]
            free_rects = list(getattr(getattr(layer, "bin", None), "free_rects", []) or [])
            if free_rects:
                largest_free_rect_area = max(int(getattr(rect, "area", 0) or 0) for rect in free_rects)
                fragmentation_penalty = max(0, int(len(free_rects)) - 1)

        return _Node(
            pallet=pallet,
            remaining=remaining,
            placements=placements,
            opening_layer_id=int(opening_layer_id),
            opening_z_mm=int(opening_z_mm),
            future_same_layer_moves=int(future_same_layer_moves),
            residual_fill_area=int(residual_fill_area),
            largest_free_rect_area=int(largest_free_rect_area),
            fragmentation_penalty=int(fragmentation_penalty),
            min_support_ratio=float(min_support_ratio),
        )

    @staticmethod
    def _rank_key(node: _Node) -> tuple[int, int, int, int, float, int]:
        return (
            int(node.future_same_layer_moves),
            int(node.residual_fill_area),
            int(node.largest_free_rect_area),
            -int(node.fragmentation_penalty),
            float(node.min_support_ratio),
            int(len(node.placements)),
        )

    @staticmethod
    def _final_rank_key(node: _Node) -> tuple[int, int, int, int, int, float]:
        return (
            int(len(node.placements)),
            int(node.future_same_layer_moves),
            int(node.residual_fill_area),
            int(node.largest_free_rect_area),
            -int(node.fragmentation_penalty),
            float(node.min_support_ratio),
        )

    @staticmethod
    def _preview_layer_id(preview: PlacementPreview | None) -> int | None:
        if preview is None:
            return None
        placement = getattr(preview, "placement", None)
        if placement is None:
            return None
        try:
            return int(getattr(placement, "layer_id"))
        except Exception:
            return None

    @staticmethod
    def _preview_z_mm(preview: PlacementPreview | None) -> int | None:
        if preview is None:
            return None
        placement = getattr(preview, "placement", None)
        if placement is None:
            return None
        try:
            return int(getattr(placement, "z_mm"))
        except Exception:
            return None

    @staticmethod
    def _current_top_z_mm(pallet: PalletModel) -> int:
        top = 0
        for placement in list(getattr(pallet, "placements", []) or []):
            try:
                top = max(int(top), int(getattr(placement, "z_mm", 0) or 0))
            except Exception:
                continue
        return int(top)

    @staticmethod
    def _support_ratio(preview: PlacementPreview | None) -> float:
        if preview is None:
            return 1.0
        debug = getattr(preview, "debug", None)
        if not isinstance(debug, dict):
            return 1.0
        value = debug.get("support_ratio")
        try:
            return float(value) if value is not None else 1.0
        except Exception:
            return 1.0
