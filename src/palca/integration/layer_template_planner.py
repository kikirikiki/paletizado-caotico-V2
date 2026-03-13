from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from ..domain.box import Box
from ..domain.placement import Placement, PlacementPreview
from ..packer.pallet_model import PalletModel

TEMPLATE_ROWS_X = "Rows-X"
TEMPLATE_ROWS_Y = "Rows-Y"
TEMPLATE_SPLIT_LR = "Split-Left-Right"
TEMPLATE_SPLIT_FB = "Split-Front-Back"
TEMPLATE_BRICK_2_LANES = "Brick-2-lanes"

ALLOWED_TEMPLATE_NAMES = (
    TEMPLATE_ROWS_X,
    TEMPLATE_ROWS_Y,
    TEMPLATE_SPLIT_LR,
    TEMPLATE_SPLIT_FB,
    TEMPLATE_BRICK_2_LANES,
)


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
    template_name: str = ""
    template_area_fill: float = 0.0
    terminal_case: bool = False


@dataclass(frozen=True)
class _LayerCandidate:
    ramp_id: int
    box: Box


@dataclass(frozen=True)
class _Slot:
    x_mm: int
    y_mm: int
    w_mm: int
    h_mm: int
    order: int


@dataclass(frozen=True)
class _Template:
    name: str
    slots: tuple[_Slot, ...]


@dataclass(frozen=True)
class _MaterializedTemplate:
    template: _Template
    placements: tuple[PlannedLayerPlacement, ...]
    packed_area_same_layer: int
    placements_count: int
    largest_free_rect_area_after: int
    fragmentation_penalty: int
    min_support_ratio: float
    area_fill_ratio: float


class LayerTemplatePlanner:
    def __init__(
        self,
        *,
        candidate_cap: int = 8,
        plan_cap: int = 6,
    ) -> None:
        self.candidate_cap = max(1, int(candidate_cap))
        self.plan_cap = max(1, int(plan_cap))

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

        opening_candidate = min(
            starters_raw,
            key=lambda item: (
                int(self._preview_z_mm(item[1]) or 0),
                int(self._preview_layer_id(item[1]) or 0),
            ),
        )
        opening_z_mm = int(self._preview_z_mm(opening_candidate[1]) or 0)
        opening_layer_id = self._preview_layer_id(opening_candidate[1])
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

        z_values: set[int] = set()
        for candidate in all_candidates:
            preview = preview_place_fn(pallet, candidate.box)
            if not bool(getattr(preview, "feasible", False)):
                continue
            layer_id = self._preview_layer_id(preview)
            z_mm = self._preview_z_mm(preview)
            if layer_id is None or z_mm is None:
                continue
            if int(layer_id) != int(target_layer_id):
                continue
            z_values.add(int(z_mm))

        best: LayerOpeningPlan | None = None
        for z_mm in sorted(z_values):
            candidate_plan = self._plan_for_target_layer_at_z(
                pallet_id=pallet_id,
                pallet=pallet,
                all_candidates=all_candidates,
                target_layer_id=int(target_layer_id),
                target_z_mm=int(z_mm),
                preview_place_fn=preview_place_fn,
            )
            if candidate_plan is None:
                continue
            if best is None:
                best = candidate_plan
                continue
            if len(candidate_plan.placements) > len(best.placements):
                best = candidate_plan
                continue
            if len(candidate_plan.placements) == len(best.placements):
                if float(candidate_plan.template_area_fill) > float(best.template_area_fill):
                    best = candidate_plan
                    continue
                if int(candidate_plan.z_mm) < int(best.z_mm):
                    best = candidate_plan
        return best

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
        if not all_candidates:
            return None

        target_free_rects = self._layer_free_rects(
            pallet=pallet,
            layer_id=int(target_layer_id),
            z_mm=int(target_z_mm),
        )
        if not target_free_rects:
            return None

        footprint = self._typical_footprint(
            pallet=pallet,
            candidates=all_candidates,
            target_layer_id=int(target_layer_id),
            target_z_mm=int(target_z_mm),
            preview_place_fn=preview_place_fn,
        )
        if footprint is None:
            return None
        unit_l_mm, unit_w_mm = footprint

        templates = self._build_template_candidates(
            free_rects=target_free_rects,
            unit_l_mm=int(unit_l_mm),
            unit_w_mm=int(unit_w_mm),
        )
        if not templates:
            layers_count = len(list(getattr(pallet, "layers", []) or []))
            fallback_rects = self._layer_free_rects(
                pallet=pallet,
                layer_id=int(layers_count),
                z_mm=int(target_z_mm),
            )
            templates = self._build_template_candidates(
                free_rects=fallback_rects,
                unit_l_mm=int(unit_l_mm),
                unit_w_mm=int(unit_w_mm),
            )
        if not templates:
            return None

        terminal_case = self._is_terminal_case(
            pallet=pallet,
            candidates=all_candidates,
            target_layer_id=int(target_layer_id),
            target_z_mm=int(target_z_mm),
            preview_place_fn=preview_place_fn,
        )

        best: _MaterializedTemplate | None = None
        for template in templates[: self.candidate_cap]:
            materialized = self._materialize_template(
                pallet_id=pallet_id,
                pallet=pallet,
                candidates=all_candidates,
                target_layer_id=int(target_layer_id),
                target_z_mm=int(target_z_mm),
                template=template,
                preview_place_fn=preview_place_fn,
            )
            if materialized is None:
                continue
            if int(materialized.placements_count) <= 0:
                continue
            if int(materialized.placements_count) < 2 and not terminal_case:
                continue
            if best is None:
                best = materialized
                continue
            if self._template_score(materialized) > self._template_score(best):
                best = materialized

        if best is None:
            return None
        return LayerOpeningPlan(
            pallet_id=pallet_id,
            layer_id=int(target_layer_id),
            z_mm=int(target_z_mm),
            placements=tuple(best.placements),
            template_name=str(best.template.name),
            template_area_fill=float(best.area_fill_ratio),
            terminal_case=bool(terminal_case),
        )

    def _materialize_template(
        self,
        *,
        pallet_id: int | str,
        pallet: PalletModel,
        candidates: Sequence[_LayerCandidate],
        target_layer_id: int,
        target_z_mm: int,
        template: _Template,
        preview_place_fn: Callable[[PalletModel, Box], PlacementPreview],
    ) -> _MaterializedTemplate | None:
        pallet_clone = copy.deepcopy(pallet)
        remaining = list(candidates)
        chosen: list[PlannedLayerPlacement] = []
        packed_area = 0
        min_support_ratio = 1.0
        current_z_floor = int(target_z_mm)

        for slot in sorted(template.slots, key=lambda s: int(s.order)):
            if len(chosen) >= self.plan_cap:
                break
            best_idx = -1
            best_preview: PlacementPreview | None = None
            best_key: tuple[int, int, int, float, float, float, int] | None = None
            for idx, candidate in enumerate(remaining):
                preview = preview_place_fn(pallet_clone, candidate.box)
                if not bool(getattr(preview, "feasible", False)):
                    continue
                layer_id = self._preview_layer_id(preview)
                z_mm = self._preview_z_mm(preview)
                if layer_id is None or z_mm is None:
                    continue
                if int(layer_id) != int(target_layer_id) or int(z_mm) < int(current_z_floor):
                    continue
                placement = getattr(preview, "placement", None)
                slot_fit = self._placement_in_slot(placement=placement, slot=slot)
                slot_distance = self._slot_distance_to_placement(slot=slot, placement=placement)
                support_ratio = self._support_ratio(preview)
                area = int(getattr(candidate.box, "length_mm", 0) or 0) * int(
                    getattr(candidate.box, "width_mm", 0) or 0
                )
                key = (
                    1 if slot_fit else 0,
                    -int(z_mm),
                    -int(slot_distance),
                    float(getattr(preview, "packing_gain", 0.0) or 0.0),
                    -float(getattr(preview, "fragmentation", 0.0) or 0.0),
                    float(support_ratio),
                    int(area),
                )
                if best_key is None or key > best_key:
                    best_key = key
                    best_idx = int(idx)
                    best_preview = preview

            if best_idx < 0 or best_preview is None:
                continue

            selected = remaining.pop(best_idx)
            try:
                preview_on_clone = preview_place_fn(pallet_clone, selected.box)
                if not bool(getattr(preview_on_clone, "feasible", False)):
                    continue
                if self._preview_layer_id(preview_on_clone) != int(target_layer_id):
                    continue
                selected_z_mm = self._preview_z_mm(preview_on_clone)
                if selected_z_mm is None or int(selected_z_mm) < int(current_z_floor):
                    continue
                pallet_clone.commit_place(preview_on_clone)
            except Exception:
                continue

            packed_area += int(getattr(selected.box, "length_mm", 0) or 0) * int(
                getattr(selected.box, "width_mm", 0) or 0
            )
            min_support_ratio = min(float(min_support_ratio), float(self._support_ratio(best_preview)))
            chosen.append(
                PlannedLayerPlacement(
                    ramp_id=int(selected.ramp_id),
                    box_id=selected.box.box_id,
                    pallet_id=pallet_id,
                    layer_id=int(target_layer_id),
                    z_mm=int(selected_z_mm),
                )
            )
            current_z_floor = int(selected_z_mm)

        if not chosen:
            return None

        largest_free_rect_area_after, fragmentation_penalty = self._layer_rect_metrics(
            pallet=pallet_clone,
            layer_id=int(target_layer_id),
            z_mm=int(target_z_mm),
        )
        layer_free_area_before = self._layer_free_area(
            pallet=pallet,
            layer_id=int(target_layer_id),
            z_mm=int(target_z_mm),
        )
        layer_free_area_after = self._layer_free_area(
            pallet=pallet_clone,
            layer_id=int(target_layer_id),
            z_mm=int(target_z_mm),
        )
        area_fill_ratio = 0.0
        if int(layer_free_area_before) > 0:
            used = max(0, int(layer_free_area_before) - int(layer_free_area_after))
            area_fill_ratio = float(used) / float(max(1, int(layer_free_area_before)))

        return _MaterializedTemplate(
            template=template,
            placements=tuple(chosen),
            packed_area_same_layer=int(packed_area),
            placements_count=int(len(chosen)),
            largest_free_rect_area_after=int(largest_free_rect_area_after),
            fragmentation_penalty=int(fragmentation_penalty),
            min_support_ratio=float(min_support_ratio),
            area_fill_ratio=float(area_fill_ratio),
        )

    def _build_template_candidates(
        self,
        *,
        free_rects: Sequence[tuple[int, int, int, int]],
        unit_l_mm: int,
        unit_w_mm: int,
    ) -> list[_Template]:
        templates: list[_Template] = []
        order = 0

        sorted_rects = sorted(
            [rect for rect in free_rects if int(rect[2]) > 0 and int(rect[3]) > 0],
            key=lambda item: int(item[2]) * int(item[3]),
            reverse=True,
        )
        for x_mm, y_mm, w_mm, h_mm in sorted_rects:
            rows_x = self._template_rows(
                x_mm=int(x_mm),
                y_mm=int(y_mm),
                w_mm=int(w_mm),
                h_mm=int(h_mm),
                unit_l_mm=int(unit_l_mm),
                unit_w_mm=int(unit_w_mm),
                row_major=True,
                start_order=int(order),
            )
            if rows_x:
                templates.append(_Template(name=TEMPLATE_ROWS_X, slots=tuple(rows_x)))
                if len(rows_x) > 1:
                    templates.append(
                        _Template(
                            name=TEMPLATE_ROWS_X,
                            slots=self._reindex_slots(reversed(rows_x)),
                        )
                    )
                order += len(rows_x)

            rows_y = self._template_rows(
                x_mm=int(x_mm),
                y_mm=int(y_mm),
                w_mm=int(w_mm),
                h_mm=int(h_mm),
                unit_l_mm=int(unit_l_mm),
                unit_w_mm=int(unit_w_mm),
                row_major=False,
                start_order=int(order),
            )
            if rows_y:
                templates.append(_Template(name=TEMPLATE_ROWS_Y, slots=tuple(rows_y)))
                if len(rows_y) > 1:
                    templates.append(
                        _Template(
                            name=TEMPLATE_ROWS_Y,
                            slots=self._reindex_slots(reversed(rows_y)),
                        )
                    )
                order += len(rows_y)

            split_lr = self._template_split_lr(
                x_mm=int(x_mm),
                y_mm=int(y_mm),
                w_mm=int(w_mm),
                h_mm=int(h_mm),
                unit_l_mm=int(unit_l_mm),
                unit_w_mm=int(unit_w_mm),
                start_order=int(order),
            )
            if split_lr:
                templates.append(_Template(name=TEMPLATE_SPLIT_LR, slots=tuple(split_lr)))
                if len(split_lr) > 1:
                    templates.append(
                        _Template(
                            name=TEMPLATE_SPLIT_LR,
                            slots=self._reindex_slots(reversed(split_lr)),
                        )
                    )
                order += len(split_lr)

            split_fb = self._template_split_fb(
                x_mm=int(x_mm),
                y_mm=int(y_mm),
                w_mm=int(w_mm),
                h_mm=int(h_mm),
                unit_l_mm=int(unit_l_mm),
                unit_w_mm=int(unit_w_mm),
                start_order=int(order),
            )
            if split_fb:
                templates.append(_Template(name=TEMPLATE_SPLIT_FB, slots=tuple(split_fb)))
                if len(split_fb) > 1:
                    templates.append(
                        _Template(
                            name=TEMPLATE_SPLIT_FB,
                            slots=self._reindex_slots(reversed(split_fb)),
                        )
                    )
                order += len(split_fb)

            brick = self._template_brick_two_lanes(
                x_mm=int(x_mm),
                y_mm=int(y_mm),
                w_mm=int(w_mm),
                h_mm=int(h_mm),
                unit_l_mm=int(unit_l_mm),
                unit_w_mm=int(unit_w_mm),
                start_order=int(order),
            )
            if brick:
                templates.append(_Template(name=TEMPLATE_BRICK_2_LANES, slots=tuple(brick)))
                if len(brick) > 1:
                    templates.append(
                        _Template(
                            name=TEMPLATE_BRICK_2_LANES,
                            slots=self._reindex_slots(reversed(brick)),
                        )
                    )
                order += len(brick)

        return templates

    @staticmethod
    def _reindex_slots(slots: Sequence[_Slot]) -> tuple[_Slot, ...]:
        out: list[_Slot] = []
        for idx, slot in enumerate(slots):
            out.append(
                _Slot(
                    x_mm=int(slot.x_mm),
                    y_mm=int(slot.y_mm),
                    w_mm=int(slot.w_mm),
                    h_mm=int(slot.h_mm),
                    order=int(idx),
                )
            )
        return tuple(out)

    def _template_rows(
        self,
        *,
        x_mm: int,
        y_mm: int,
        w_mm: int,
        h_mm: int,
        unit_l_mm: int,
        unit_w_mm: int,
        row_major: bool,
        start_order: int,
    ) -> list[_Slot]:
        max_template_slots = max(4, int(self.plan_cap) * 3)
        if int(unit_l_mm) <= 0 or int(unit_w_mm) <= 0:
            return []
        nx = int(w_mm) // int(unit_l_mm)
        ny = int(h_mm) // int(unit_w_mm)
        if nx <= 0 or ny <= 0:
            return []
        slots: list[_Slot] = []
        order = int(start_order)
        if row_major:
            for row in range(ny):
                for col in range(nx):
                    slots.append(
                        _Slot(
                            x_mm=int(x_mm + col * unit_l_mm),
                            y_mm=int(y_mm + row * unit_w_mm),
                            w_mm=int(unit_l_mm),
                            h_mm=int(unit_w_mm),
                            order=int(order),
                        )
                    )
                    order += 1
                    if len(slots) >= max_template_slots:
                        return slots
            return slots

        for col in range(nx):
            for row in range(ny):
                slots.append(
                    _Slot(
                        x_mm=int(x_mm + col * unit_l_mm),
                        y_mm=int(y_mm + row * unit_w_mm),
                        w_mm=int(unit_l_mm),
                        h_mm=int(unit_w_mm),
                        order=int(order),
                    )
                )
                order += 1
                if len(slots) >= max_template_slots:
                    return slots
        return slots

    def _template_split_lr(
        self,
        *,
        x_mm: int,
        y_mm: int,
        w_mm: int,
        h_mm: int,
        unit_l_mm: int,
        unit_w_mm: int,
        start_order: int,
    ) -> list[_Slot]:
        max_template_slots = max(4, int(self.plan_cap) * 3)
        if int(w_mm) < 2 * int(unit_l_mm):
            return []
        left_w = int(w_mm) // 2
        right_w = int(w_mm) - int(left_w)
        left_slots = self._template_rows(
            x_mm=int(x_mm),
            y_mm=int(y_mm),
            w_mm=int(left_w),
            h_mm=int(h_mm),
            unit_l_mm=int(unit_l_mm),
            unit_w_mm=int(unit_w_mm),
            row_major=True,
            start_order=int(start_order),
        )
        if not left_slots:
            return []
        right_slots = self._template_rows(
            x_mm=int(x_mm + left_w),
            y_mm=int(y_mm),
            w_mm=int(right_w),
            h_mm=int(h_mm),
            unit_l_mm=int(unit_l_mm),
            unit_w_mm=int(unit_w_mm),
            row_major=True,
            start_order=int(start_order + len(left_slots)),
        )
        return (left_slots + right_slots)[:max_template_slots]

    def _template_split_fb(
        self,
        *,
        x_mm: int,
        y_mm: int,
        w_mm: int,
        h_mm: int,
        unit_l_mm: int,
        unit_w_mm: int,
        start_order: int,
    ) -> list[_Slot]:
        max_template_slots = max(4, int(self.plan_cap) * 3)
        if int(h_mm) < 2 * int(unit_w_mm):
            return []
        front_h = int(h_mm) // 2
        back_h = int(h_mm) - int(front_h)
        front_slots = self._template_rows(
            x_mm=int(x_mm),
            y_mm=int(y_mm),
            w_mm=int(w_mm),
            h_mm=int(front_h),
            unit_l_mm=int(unit_l_mm),
            unit_w_mm=int(unit_w_mm),
            row_major=False,
            start_order=int(start_order),
        )
        if not front_slots:
            return []
        back_slots = self._template_rows(
            x_mm=int(x_mm),
            y_mm=int(y_mm + front_h),
            w_mm=int(w_mm),
            h_mm=int(back_h),
            unit_l_mm=int(unit_l_mm),
            unit_w_mm=int(unit_w_mm),
            row_major=False,
            start_order=int(start_order + len(front_slots)),
        )
        return (front_slots + back_slots)[:max_template_slots]

    def _template_brick_two_lanes(
        self,
        *,
        x_mm: int,
        y_mm: int,
        w_mm: int,
        h_mm: int,
        unit_l_mm: int,
        unit_w_mm: int,
        start_order: int,
    ) -> list[_Slot]:
        max_template_slots = max(4, int(self.plan_cap) * 3)
        if int(unit_l_mm) <= 1 or int(unit_w_mm) <= 1:
            return []
        if int(h_mm) < 2 * int(unit_w_mm):
            return []
        lane_w = int(unit_w_mm)
        if int(w_mm) < 2 * int(unit_l_mm):
            return []
        lane1_y = int(y_mm)
        lane2_y = int(y_mm + lane_w)
        x_start_1 = int(x_mm)
        x_start_2 = int(x_mm + max(1, unit_l_mm // 2))
        slots: list[_Slot] = []
        order = int(start_order)
        x_cursor_1 = int(x_start_1)
        x_cursor_2 = int(x_start_2)
        while len(slots) < max_template_slots:
            placed_any = False
            if x_cursor_1 + int(unit_l_mm) <= int(x_mm + w_mm):
                slots.append(
                    _Slot(
                        x_mm=int(x_cursor_1),
                        y_mm=int(lane1_y),
                        w_mm=int(unit_l_mm),
                        h_mm=int(lane_w),
                        order=int(order),
                    )
                )
                order += 1
                x_cursor_1 += int(unit_l_mm)
                placed_any = True
            if len(slots) >= max_template_slots:
                break
            if x_cursor_2 + int(unit_l_mm) <= int(x_mm + w_mm):
                slots.append(
                    _Slot(
                        x_mm=int(x_cursor_2),
                        y_mm=int(lane2_y),
                        w_mm=int(unit_l_mm),
                        h_mm=int(lane_w),
                        order=int(order),
                    )
                )
                order += 1
                x_cursor_2 += int(unit_l_mm)
                placed_any = True
            if not placed_any:
                break
        if len(slots) < 2:
            return []
        return slots

    @staticmethod
    def _template_score(item: _MaterializedTemplate) -> tuple[int, int, int, int, float]:
        return (
            int(item.packed_area_same_layer),
            int(item.placements_count),
            int(item.largest_free_rect_area_after),
            -int(item.fragmentation_penalty),
            float(item.min_support_ratio),
        )

    def _is_terminal_case(
        self,
        *,
        pallet: PalletModel,
        candidates: Sequence[_LayerCandidate],
        target_layer_id: int,
        target_z_mm: int,
        preview_place_fn: Callable[[PalletModel, Box], PlacementPreview],
    ) -> bool:
        pallet_clone = copy.deepcopy(pallet)
        remaining = list(candidates)
        committed = 0
        current_z_floor = int(target_z_mm)
        while committed < 2:
            chosen_idx = -1
            chosen_preview: PlacementPreview | None = None
            chosen_gain = float("-inf")
            for idx, candidate in enumerate(remaining):
                preview = preview_place_fn(pallet_clone, candidate.box)
                if not bool(getattr(preview, "feasible", False)):
                    continue
                if self._preview_layer_id(preview) != int(target_layer_id):
                    continue
                preview_z_mm = self._preview_z_mm(preview)
                if preview_z_mm is None or int(preview_z_mm) < int(current_z_floor):
                    continue
                gain = float(getattr(preview, "packing_gain", 0.0) or 0.0)
                if chosen_preview is None or gain > chosen_gain:
                    chosen_idx = int(idx)
                    chosen_preview = preview
                    chosen_gain = gain
            if chosen_idx < 0 or chosen_preview is None:
                break
            selected = remaining.pop(chosen_idx)
            try:
                preview_on_clone = preview_place_fn(pallet_clone, selected.box)
                if not bool(getattr(preview_on_clone, "feasible", False)):
                    break
                if self._preview_layer_id(preview_on_clone) != int(target_layer_id):
                    break
                selected_z_mm = self._preview_z_mm(preview_on_clone)
                if selected_z_mm is None or int(selected_z_mm) < int(current_z_floor):
                    break
                pallet_clone.commit_place(preview_on_clone)
                committed += 1
                current_z_floor = int(selected_z_mm)
            except Exception:
                break
        return int(committed) < 2

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

    def _typical_footprint(
        self,
        *,
        pallet: PalletModel,
        candidates: Sequence[_LayerCandidate],
        target_layer_id: int,
        target_z_mm: int,
        preview_place_fn: Callable[[PalletModel, Box], PlacementPreview],
    ) -> tuple[int, int] | None:
        histogram: dict[tuple[int, int], int] = {}
        for candidate in candidates:
            preview = preview_place_fn(pallet, candidate.box)
            if not bool(getattr(preview, "feasible", False)):
                continue
            if self._preview_layer_id(preview) != int(target_layer_id):
                continue
            preview_z_mm = self._preview_z_mm(preview)
            if preview_z_mm is None or int(preview_z_mm) < int(target_z_mm):
                continue
            placement = getattr(preview, "placement", None)
            if placement is None:
                continue
            l_mm = int(getattr(placement, "length_mm", 0) or 0)
            w_mm = int(getattr(placement, "width_mm", 0) or 0)
            if l_mm <= 0 or w_mm <= 0:
                continue
            key = (int(l_mm), int(w_mm))
            histogram[key] = int(histogram.get(key, 0)) + 1
        if not histogram:
            return None
        ranked = sorted(
            histogram.items(),
            key=lambda item: (int(item[1]), int(item[0][0] * item[0][1]), int(item[0][0]), int(item[0][1])),
            reverse=True,
        )
        best = ranked[0][0]
        return int(best[0]), int(best[1])

    @staticmethod
    def _layer_free_rects(
        *,
        pallet: PalletModel,
        layer_id: int,
        z_mm: int,
    ) -> list[tuple[int, int, int, int]]:
        layers = list(getattr(pallet, "layers", []) or [])
        if 0 <= int(layer_id) < len(layers):
            layer = layers[int(layer_id)]
            free_rects = list(getattr(getattr(layer, "bin", None), "free_rects", []) or [])
            out: list[tuple[int, int, int, int]] = []
            for rect in free_rects:
                try:
                    out.append(
                        (
                            int(getattr(rect, "x", 0) or 0),
                            int(getattr(rect, "y", 0) or 0),
                            int(getattr(rect, "w", 0) or 0),
                            int(getattr(rect, "h", 0) or 0),
                        )
                    )
                except Exception:
                    continue
            return out

        # Si la capa objetivo todavia no existe, modelamos el snapshot de apertura
        # como bin vacio completo a la cota actual.
        if int(layer_id) >= int(len(layers)):
            try:
                if layers:
                    ref_bin = getattr(layers[-1], "bin", None)
                    if ref_bin is not None:
                        width = int(getattr(ref_bin, "width", 0) or 0)
                        height = int(getattr(ref_bin, "height", 0) or 0)
                        if width > 0 and height > 0:
                            return [(0, 0, width, height)]
                overhang = int(getattr(pallet.spec, "overhang_mm", 0) or 0)
                length = int(getattr(pallet.spec, "length_mm", 0) or 0) + 2 * int(overhang)
                width = int(getattr(pallet.spec, "width_mm", 0) or 0) + 2 * int(overhang)
                if length > 0 and width > 0:
                    return [(0, 0, int(length), int(width))]
            except Exception:
                return []

        return []

    @staticmethod
    def _layer_free_area(
        *,
        pallet: PalletModel,
        layer_id: int,
        z_mm: int,
    ) -> int:
        free_rects = LayerTemplatePlanner._layer_free_rects(pallet=pallet, layer_id=int(layer_id), z_mm=int(z_mm))
        return int(sum(int(w_mm) * int(h_mm) for _x, _y, w_mm, h_mm in free_rects))

    @staticmethod
    def _layer_rect_metrics(
        *,
        pallet: PalletModel,
        layer_id: int,
        z_mm: int,
    ) -> tuple[int, int]:
        free_rects = LayerTemplatePlanner._layer_free_rects(pallet=pallet, layer_id=int(layer_id), z_mm=int(z_mm))
        if not free_rects:
            return 0, 0
        largest = max(int(w_mm) * int(h_mm) for _x, _y, w_mm, h_mm in free_rects)
        fragmentation = max(0, int(len(free_rects)) - 1)
        return int(largest), int(fragmentation)

    @staticmethod
    def _placement_in_slot(
        *,
        placement: Placement | None,
        slot: _Slot,
    ) -> bool:
        if placement is None:
            return False
        try:
            x_mm = int(getattr(placement, "x_mm"))
            y_mm = int(getattr(placement, "y_mm"))
            w_mm = int(getattr(placement, "length_mm"))
            h_mm = int(getattr(placement, "width_mm"))
        except Exception:
            return False
        x1 = int(x_mm + w_mm)
        y1 = int(y_mm + h_mm)
        sx1 = int(slot.x_mm + slot.w_mm)
        sy1 = int(slot.y_mm + slot.h_mm)
        return int(x_mm) >= int(slot.x_mm) and int(y_mm) >= int(slot.y_mm) and int(x1) <= int(sx1) and int(y1) <= int(
            sy1
        )

    @staticmethod
    def _slot_distance_to_placement(
        *,
        slot: _Slot,
        placement: Placement | None,
    ) -> int:
        if placement is None:
            return 1_000_000_000
        try:
            px = int(getattr(placement, "x_mm"))
            py = int(getattr(placement, "y_mm"))
            pw = int(getattr(placement, "length_mm"))
            ph = int(getattr(placement, "width_mm"))
        except Exception:
            return 1_000_000_000
        slot_cx = int(slot.x_mm + (slot.w_mm // 2))
        slot_cy = int(slot.y_mm + (slot.h_mm // 2))
        placement_cx = int(px + (pw // 2))
        placement_cy = int(py + (ph // 2))
        return abs(int(slot_cx) - int(placement_cx)) + abs(int(slot_cy) - int(placement_cy))

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
