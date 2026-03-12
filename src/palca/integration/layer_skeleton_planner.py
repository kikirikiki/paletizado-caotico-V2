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
class LayerSkeletonPlan:
    pallet_id: int | str
    layer_id: int
    z_mm: int
    placements: tuple[PlannedLayerPlacement, ...]
    is_terminal: bool
    packed_area_same_layer: int
    largest_free_rect_area: int
    fragmentation_penalty: int
    min_support_ratio: float
    area_fill_ratio: float


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
    packed_area_same_layer: int
    future_same_layer_moves: int
    largest_free_rect_area: int
    fragmentation_penalty: int
    min_support_ratio: float
    layer_capacity_area: int


class LayerSkeletonPlanner:
    def __init__(
        self,
        *,
        candidate_cap: int = 8,
        skeleton_cap: int = 6,
        beam_width: int = 4,
    ) -> None:
        self.candidate_cap = max(1, int(candidate_cap))
        self.skeleton_cap = max(1, int(skeleton_cap))
        self.beam_width = max(1, int(beam_width))

    def plan_opening(
        self,
        *,
        pallet_id: int | str,
        pallet: PalletModel,
        ramp_queues: Mapping[int, Sequence[Box]],
        preview_place_fn: Callable[[PalletModel, Box], PlacementPreview],
    ) -> LayerSkeletonPlan | None:
        current_top_z = self._current_top_z_mm(pallet)
        all_candidates = self._collect_candidates(pallet_id=pallet_id, ramp_queues=ramp_queues)

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
    ) -> LayerSkeletonPlan | None:
        all_candidates = self._collect_candidates(pallet_id=pallet_id, ramp_queues=ramp_queues)
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

        best_plan: LayerSkeletonPlan | None = None
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
            if self._plan_sort_key(plan) > self._plan_sort_key(best_plan):
                best_plan = plan
                continue
            if self._plan_sort_key(plan) == self._plan_sort_key(best_plan) and int(plan.z_mm) < int(best_plan.z_mm):
                best_plan = plan

        return best_plan

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
    ) -> LayerSkeletonPlan | None:
        root = self._build_node(
            pallet=copy.deepcopy(pallet),
            remaining=tuple(all_candidates),
            placements=(),
            opening_layer_id=int(target_layer_id),
            opening_z_mm=int(target_z_mm),
            packed_area_same_layer=0,
            min_support_ratio=1.0,
            preview_place_fn=preview_place_fn,
        )
        beam: list[_Node] = [root]
        best: _Node | None = None

        for _depth in range(self.skeleton_cap):
            next_beam: list[_Node] = []
            for node in beam:
                next_beam.extend(
                    self._expand_node(
                        node=node,
                        opening_layer_id=int(target_layer_id),
                        opening_z_mm=int(target_z_mm),
                        preview_place_fn=preview_place_fn,
                        pallet_id=pallet_id,
                    )
                )
            if not next_beam:
                break

            next_beam.sort(key=self._rank_key, reverse=True)
            beam = next_beam[: self.beam_width]
            if best is None or self._final_rank_key(beam[0]) > self._final_rank_key(best):
                best = beam[0]

        if best is None or not best.placements:
            return None

        layer_capacity = max(1, int(best.layer_capacity_area))
        return LayerSkeletonPlan(
            pallet_id=pallet_id,
            layer_id=int(target_layer_id),
            z_mm=int(target_z_mm),
            placements=tuple(best.placements),
            is_terminal=bool(int(best.future_same_layer_moves) <= 0),
            packed_area_same_layer=int(best.packed_area_same_layer),
            largest_free_rect_area=int(best.largest_free_rect_area),
            fragmentation_penalty=int(best.fragmentation_penalty),
            min_support_ratio=float(best.min_support_ratio),
            area_fill_ratio=float(float(best.packed_area_same_layer) / float(layer_capacity)),
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
        feasible: list[tuple[tuple[float, float, float, int], PlacementPreview, _LayerCandidate]] = []
        for candidate in node.remaining:
            preview = preview_place_fn(node.pallet, candidate.box)
            if not bool(getattr(preview, "feasible", False)):
                continue
            layer_id = self._preview_layer_id(preview)
            z_mm = self._preview_z_mm(preview)
            if layer_id is None or z_mm is None:
                continue
            if int(layer_id) != int(opening_layer_id) or int(z_mm) < int(opening_z_mm):
                continue
            area = int(getattr(candidate.box, "length_mm", 0) or 0) * int(getattr(candidate.box, "width_mm", 0) or 0)
            local_key = (
                float(getattr(preview, "packing_gain", 0.0) or 0.0),
                -float(getattr(preview, "fragmentation", 0.0) or 0.0),
                float(self._support_ratio(preview)),
                int(area),
            )
            feasible.append((local_key, preview, candidate))

        if not feasible:
            return []

        feasible.sort(key=lambda item: item[0], reverse=True)
        expansions: list[_Node] = []
        for _local_key, preview, candidate in feasible[: self.candidate_cap]:
            try:
                pallet_clone = copy.deepcopy(node.pallet)
                preview_on_clone = preview_place_fn(pallet_clone, candidate.box)
                if not bool(getattr(preview_on_clone, "feasible", False)):
                    continue
                clone_layer_id = self._preview_layer_id(preview_on_clone)
                clone_z_mm = self._preview_z_mm(preview_on_clone)
                if clone_layer_id != int(opening_layer_id) or clone_z_mm is None or int(clone_z_mm) < int(opening_z_mm):
                    continue
                pallet_clone.commit_place(preview_on_clone)
            except Exception:
                continue

            area = int(getattr(candidate.box, "length_mm", 0) or 0) * int(getattr(candidate.box, "width_mm", 0) or 0)
            remaining = tuple(item for item in node.remaining if item is not candidate)
            planned = PlannedLayerPlacement(
                ramp_id=int(candidate.ramp_id),
                box_id=candidate.box.box_id,
                pallet_id=pallet_id,
                layer_id=int(opening_layer_id),
                z_mm=int(clone_z_mm),
            )
            child = self._build_node(
                pallet=pallet_clone,
                remaining=remaining,
                placements=tuple((*node.placements, planned)),
                opening_layer_id=int(opening_layer_id),
                opening_z_mm=int(opening_z_mm),
                packed_area_same_layer=int(node.packed_area_same_layer + area),
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
        packed_area_same_layer: int,
        min_support_ratio: float,
        preview_place_fn: Callable[[PalletModel, Box], PlacementPreview],
    ) -> _Node:
        future_same_layer_moves = 0
        for candidate in remaining:
            preview = preview_place_fn(pallet, candidate.box)
            if not bool(getattr(preview, "feasible", False)):
                continue
            if self._preview_layer_id(preview) != int(opening_layer_id):
                continue
            preview_z = self._preview_z_mm(preview)
            if preview_z is None or int(preview_z) < int(opening_z_mm):
                continue
            future_same_layer_moves += 1

        largest_free_rect_area = 0
        fragmentation_penalty = 0
        layers = list(getattr(pallet, "layers", []) or [])
        if 0 <= int(opening_layer_id) < len(layers):
            layer = layers[int(opening_layer_id)]
            free_rects = list(getattr(getattr(layer, "bin", None), "free_rects", []) or [])
            if free_rects:
                largest_free_rect_area = max(int(getattr(rect, "area", 0) or 0) for rect in free_rects)
                fragmentation_penalty = max(0, int(len(free_rects)) - 1)

        layer_capacity_area = self._layer_capacity_area_mm2(pallet=pallet, layer_id=int(opening_layer_id))
        return _Node(
            pallet=pallet,
            remaining=remaining,
            placements=placements,
            opening_layer_id=int(opening_layer_id),
            opening_z_mm=int(opening_z_mm),
            packed_area_same_layer=int(packed_area_same_layer),
            future_same_layer_moves=int(future_same_layer_moves),
            largest_free_rect_area=int(largest_free_rect_area),
            fragmentation_penalty=int(fragmentation_penalty),
            min_support_ratio=float(min_support_ratio),
            layer_capacity_area=int(layer_capacity_area),
        )

    @staticmethod
    def _rank_key(node: _Node) -> tuple[int, int, int, int, float]:
        return (
            int(node.packed_area_same_layer),
            int(len(node.placements)),
            int(node.largest_free_rect_area),
            -int(node.fragmentation_penalty),
            float(node.min_support_ratio),
        )

    @staticmethod
    def _final_rank_key(node: _Node) -> tuple[int, int, int, int, float]:
        return (
            int(node.packed_area_same_layer),
            int(len(node.placements)),
            int(node.largest_free_rect_area),
            -int(node.fragmentation_penalty),
            float(node.min_support_ratio),
        )

    @staticmethod
    def _plan_sort_key(plan: LayerSkeletonPlan) -> tuple[int, int, int, int, float]:
        return (
            int(plan.packed_area_same_layer),
            int(len(plan.placements)),
            int(plan.largest_free_rect_area),
            -int(plan.fragmentation_penalty),
            float(plan.min_support_ratio),
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

    @staticmethod
    def _layer_capacity_area_mm2(*, pallet: PalletModel, layer_id: int) -> int:
        layers = list(getattr(pallet, "layers", []) or [])
        if 0 <= int(layer_id) < len(layers):
            layer = layers[int(layer_id)]
            bin_obj = getattr(layer, "bin", None)
            if bin_obj is not None:
                length = getattr(bin_obj, "length_mm", None)
                width = getattr(bin_obj, "width_mm", None)
                try:
                    if length is not None and width is not None:
                        area = int(length) * int(width)
                        if area > 0:
                            return int(area)
                except Exception:
                    pass
        try:
            area = int(getattr(pallet, "bin_area_mm2", 0) or 0)
            if area > 0:
                return int(area)
        except Exception:
            pass
        return 1
