from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import time
from typing import Any, Sequence

from ..domain.box import Box
from ..domain.placement import PlacementPreview
from ..packer.pallet_model import PalletModel

OBJECTIVE_MAX_PLACED_THEN_MIN_HEIGHT_GAIN = "max_placed_then_min_height_gain"
OBJECTIVE_MAX_PLACED_THEN_MIN_HEIGHT_WASTE = "max_placed_then_min_height_waste"
SUPPORTED_BEAM_OBJECTIVES = frozenset(
    {
        OBJECTIVE_MAX_PLACED_THEN_MIN_HEIGHT_GAIN,
        OBJECTIVE_MAX_PLACED_THEN_MIN_HEIGHT_WASTE,
    }
)


@dataclass(frozen=True, slots=True)
class BeamPickConfig:
    beam_width: int = 12
    beam_depth: int = 6
    beam_max_expansions: int = 2500
    beam_time_budget_ms: int = 200
    beam_objective: str = OBJECTIVE_MAX_PLACED_THEN_MIN_HEIGHT_GAIN
    beam_debug: bool = False


@dataclass(frozen=True, slots=True)
class BeamPickResult:
    buffer_index: int | None
    box_id: int | str | None
    preview: PlacementPreview | None
    best_sequence: tuple[tuple[int, int | str], ...] = field(default_factory=tuple)
    objective_key: tuple[Any, ...] = field(default_factory=tuple)
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _BeamNode:
    pallet: PalletModel
    window: tuple[Box, ...]
    upstream: tuple[Box, ...]
    placed_count: int
    height_used_mm: int
    current_layer_height_mm: int
    n_items_in_current_layer: int
    cumulative_height_gain_mm: int
    cumulative_height_waste_mm: int
    layer_opened_count: int
    cumulative_preview_objective: float
    cumulative_score_adjustment: float
    tower_penalty_total: float
    fragmentation_total: float
    first_pick_index: int | None
    first_pick_box_id: int | str | None
    first_pick_preview: PlacementPreview | None
    first_pick_height_gain_mm: int
    first_pick_height_waste_mm: int
    first_pick_opened_layer: int
    first_pick_score_adjustment: float
    sequence: tuple[tuple[int, int | str], ...]


class BeamPickPlanner:
    @classmethod
    def plan(
        cls,
        *,
        pallet: PalletModel,
        window: Sequence[Box],
        pick_window: int,
        cfg: BeamPickConfig | None = None,
        upstream: Sequence[Box] | None = None,
        rng: Any | None = None,
    ) -> BeamPickResult:
        del rng  # beam search is deterministic today.
        config = cfg or BeamPickConfig()
        objective = cls._normalize_objective(config.beam_objective)
        k = max(1, int(pick_window))
        root_window = tuple(window[:k])
        root_upstream = tuple(upstream or ())

        if not root_window:
            return BeamPickResult(buffer_index=None, box_id=None, preview=None, debug={"reason": "empty_window"})
        root_layer_height_mm, root_layer_items = cls._active_layer_state(pallet)

        started = time.perf_counter()
        beam_width = max(1, int(config.beam_width))
        beam_depth = max(1, int(config.beam_depth))
        max_expansions = max(1, int(config.beam_max_expansions))
        deadline = None
        if int(config.beam_time_budget_ms) > 0:
            deadline = time.perf_counter() + (float(config.beam_time_budget_ms) / 1000.0)

        infeasible_reasons: Counter[str] = Counter()
        frontier_sizes: dict[int, int] = {}
        best_key_by_depth: dict[int, tuple[Any, ...]] = {}
        expansions = 0
        exhausted = False

        root = _BeamNode(
            pallet=pallet,
            window=root_window,
            upstream=root_upstream,
            placed_count=0,
            height_used_mm=int(pallet.current_height_mm()),
            current_layer_height_mm=int(root_layer_height_mm),
            n_items_in_current_layer=int(root_layer_items),
            cumulative_height_gain_mm=0,
            cumulative_height_waste_mm=0,
            layer_opened_count=0,
            cumulative_preview_objective=0.0,
            cumulative_score_adjustment=0.0,
            tower_penalty_total=0.0,
            fragmentation_total=0.0,
            first_pick_index=None,
            first_pick_box_id=None,
            first_pick_preview=None,
            first_pick_height_gain_mm=0,
            first_pick_height_waste_mm=0,
            first_pick_opened_layer=0,
            first_pick_score_adjustment=0.0,
            sequence=tuple(),
        )
        frontier: list[_BeamNode] = [root]
        best = root

        for depth in range(1, beam_depth + 1):
            if deadline is not None and time.perf_counter() >= deadline:
                exhausted = True
                break
            if expansions >= max_expansions:
                exhausted = True
                break

            next_frontier: list[_BeamNode] = []
            for node in frontier:
                node_children: list[tuple[tuple[Any, ...], _BeamNode]] = []
                node_pick_window = min(k, len(node.window))
                if node_pick_window <= 0:
                    continue
                for idx in range(node_pick_window):
                    if deadline is not None and time.perf_counter() >= deadline:
                        exhausted = True
                        break
                    if expansions >= max_expansions:
                        exhausted = True
                        break
                    expansions += 1

                    box = node.window[idx]
                    preview = node.pallet.preview_place(box)
                    if not preview.feasible or preview.placement is None:
                        reason = str(preview.infeasible_reason or "UNKNOWN")
                        infeasible_reasons[reason] += 1
                        continue

                    child_pallet = node.pallet.fork()
                    try:
                        child_pallet.commit_place(preview)
                    except Exception:
                        infeasible_reasons["COMMIT_ERROR"] += 1
                        continue

                    child_height = int(child_pallet.current_height_mm())
                    height_gain = max(0, child_height - int(node.height_used_mm))
                    preview_debug = preview.debug or {}
                    opens_new_layer = bool(preview_debug.get("is_new_layer"))
                    if not opens_new_layer:
                        opens_new_layer = int(preview.placement.layer_id) >= len(node.pallet.layers)
                    height_waste_mm, next_layer_height_mm, next_layer_items = cls._height_waste_step(
                        is_new_layer=opens_new_layer,
                        item_height_mm=int(preview.placement.height_mm),
                        current_layer_height_mm=int(node.current_layer_height_mm),
                        n_items_in_current_layer=int(node.n_items_in_current_layer),
                    )

                    next_window, next_upstream = cls._advance_window(
                        node.window,
                        node.upstream,
                        picked_index=idx,
                        pick_window=k,
                    )

                    first_pick_index = node.first_pick_index if node.first_pick_index is not None else int(idx)
                    first_pick_box_id = node.first_pick_box_id if node.first_pick_box_id is not None else box.box_id
                    first_pick_preview = (
                        node.first_pick_preview if node.first_pick_preview is not None else preview
                    )
                    first_pick_height_gain = int(node.first_pick_height_gain_mm)
                    first_pick_height_waste = int(node.first_pick_height_waste_mm)
                    first_pick_opened_layer = int(node.first_pick_opened_layer)
                    first_pick_score_adjustment = float(node.first_pick_score_adjustment)
                    if node.first_pick_index is None:
                        first_pick_height_gain = int(height_gain)
                        first_pick_height_waste = int(height_waste_mm)
                        first_pick_opened_layer = 1 if opens_new_layer else 0
                        first_pick_score_adjustment = float(preview.score_adjustment or 0.0)
                    next_sequence = node.sequence + ((int(idx), box.box_id),)

                    tower_penalty = max(0.0, -float(preview_debug.get("tower_penalty", 0.0) or 0.0))
                    fragmentation = max(0.0, float(preview.fragmentation))
                    score_adjustment = float(preview.score_adjustment or 0.0)
                    step_objective = cls._preview_objective(preview)

                    child = _BeamNode(
                        pallet=child_pallet,
                        window=next_window,
                        upstream=next_upstream,
                        placed_count=int(node.placed_count) + 1,
                        height_used_mm=child_height,
                        current_layer_height_mm=int(next_layer_height_mm),
                        n_items_in_current_layer=int(next_layer_items),
                        cumulative_height_gain_mm=int(node.cumulative_height_gain_mm) + int(height_gain),
                        cumulative_height_waste_mm=int(node.cumulative_height_waste_mm) + int(height_waste_mm),
                        layer_opened_count=int(node.layer_opened_count) + (1 if opens_new_layer else 0),
                        cumulative_preview_objective=float(node.cumulative_preview_objective) + float(step_objective),
                        cumulative_score_adjustment=float(node.cumulative_score_adjustment) + float(score_adjustment),
                        tower_penalty_total=float(node.tower_penalty_total) + tower_penalty,
                        fragmentation_total=float(node.fragmentation_total) + fragmentation,
                        first_pick_index=first_pick_index,
                        first_pick_box_id=first_pick_box_id,
                        first_pick_preview=first_pick_preview,
                        first_pick_height_gain_mm=first_pick_height_gain,
                        first_pick_height_waste_mm=first_pick_height_waste,
                        first_pick_opened_layer=first_pick_opened_layer,
                        first_pick_score_adjustment=first_pick_score_adjustment,
                        sequence=next_sequence,
                    )
                    local_key = cls._local_child_key(
                        child=child,
                        opens_new_layer=opens_new_layer,
                        height_gain=height_gain,
                        height_waste_mm=height_waste_mm,
                        score_adjustment=score_adjustment,
                        step_objective=step_objective,
                        objective=objective,
                    )
                    node_children.append((local_key, child))

                if node_children:
                    node_children.sort(key=lambda item: item[0])
                    next_frontier.extend(child for _, child in node_children)
                if exhausted:
                    break

            if next_frontier:
                next_frontier.sort(key=lambda node: cls._objective_key(node, objective))
                pruned = next_frontier[:beam_width]
                frontier_sizes[depth] = len(pruned)
                best_key_by_depth[depth] = cls._objective_key(pruned[0], objective)
                if cls._objective_key(pruned[0], objective) < cls._objective_key(best, objective):
                    best = pruned[0]
                frontier = pruned
            else:
                frontier = []
                break

            if exhausted:
                break

        projected_best = int(best.placed_count)
        if deadline is not None and time.perf_counter() < deadline:
            candidates = [node for node in frontier if node.placed_count > 0] if frontier else []
            if best.placed_count > 0 and best not in candidates:
                candidates.append(best)
            selected = best
            selected_key = (
                -int(best.placed_count),
                cls._objective_key(best, objective),
            )
            for node in candidates:
                if deadline is not None and time.perf_counter() >= deadline:
                    exhausted = True
                    break
                extra = cls._rollout_count(
                    node=node,
                    pick_window=k,
                    max_steps=max(
                        beam_depth,
                        min(48, int(k) + len(node.window) + len(node.upstream)),
                    ),
                    deadline=deadline,
                    objective=objective,
                )
                projected = int(node.placed_count) + int(extra)
                candidate_key = (
                    -projected,
                    cls._objective_key(node, objective),
                )
                if candidate_key < selected_key:
                    selected = node
                    selected_key = candidate_key
                    projected_best = projected
            best = selected

        if best.placed_count <= 0 or best.first_pick_index is None:
            debug = cls._build_debug(
                config=config,
                expansions=expansions,
                exhausted=exhausted,
                infeasible_reasons=infeasible_reasons,
                best=best,
                frontier_sizes=frontier_sizes,
                best_key_by_depth=best_key_by_depth,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                projected_best=projected_best,
                objective=objective,
            )
            return BeamPickResult(
                buffer_index=None,
                box_id=None,
                preview=None,
                objective_key=cls._objective_key(best, objective),
                debug=debug,
            )

        debug = cls._build_debug(
            config=config,
            expansions=expansions,
            exhausted=exhausted,
            infeasible_reasons=infeasible_reasons,
            best=best,
            frontier_sizes=frontier_sizes,
            best_key_by_depth=best_key_by_depth,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            projected_best=projected_best,
            objective=objective,
        )
        return BeamPickResult(
            buffer_index=int(best.first_pick_index),
            box_id=best.first_pick_box_id,
            preview=best.first_pick_preview,
            best_sequence=best.sequence,
            objective_key=cls._objective_key(best, objective),
            debug=debug,
        )

    @staticmethod
    def _advance_window(
        window: tuple[Box, ...],
        upstream: tuple[Box, ...],
        *,
        picked_index: int,
        pick_window: int,
    ) -> tuple[tuple[Box, ...], tuple[Box, ...]]:
        next_window = list(window)
        next_window.pop(int(picked_index))
        next_upstream = list(upstream)
        while len(next_window) < int(pick_window) and next_upstream:
            next_window.append(next_upstream.pop(0))
        return tuple(next_window), tuple(next_upstream)

    @staticmethod
    def _active_layer_state(pallet: PalletModel) -> tuple[int, int]:
        if not pallet.layers:
            return 0, 0
        active_layer = pallet.layers[-1]
        active_layer_id = int(active_layer.layer_id)
        active_items = sum(1 for placement in pallet.placements if int(placement.layer_id) == active_layer_id)
        return int(active_layer.height_mm), int(active_items)

    @staticmethod
    def _height_waste_step(
        *,
        is_new_layer: bool,
        item_height_mm: int,
        current_layer_height_mm: int,
        n_items_in_current_layer: int,
    ) -> tuple[int, int, int]:
        item_height = max(0, int(item_height_mm))
        if is_new_layer:
            return 0, int(item_height), 1

        layer_height = max(0, int(current_layer_height_mm))
        n_items = max(0, int(n_items_in_current_layer))
        if n_items <= 0:
            next_height = max(int(layer_height), int(item_height))
            waste_delta = max(0, int(layer_height - item_height))
            return int(waste_delta), int(next_height), 1

        if item_height <= layer_height:
            waste_delta = int(layer_height - item_height)
            return int(waste_delta), int(layer_height), int(n_items + 1)

        dh = int(item_height - layer_height)
        waste_delta = int(n_items * dh)
        return int(waste_delta), int(item_height), int(n_items + 1)

    @staticmethod
    def _objective_key(node: _BeamNode, objective: str) -> tuple[Any, ...]:
        if objective == OBJECTIVE_MAX_PLACED_THEN_MIN_HEIGHT_WASTE:
            return BeamPickPlanner._height_waste_objective_key(node)
        return BeamPickPlanner._legacy_objective_key(node)

    @staticmethod
    def _legacy_objective_key(node: _BeamNode) -> tuple[Any, ...]:
        seq_key = tuple((int(idx), str(box_id)) for idx, box_id in node.sequence[:10])
        first_idx = int(node.first_pick_index) if node.first_pick_index is not None else 10**9
        return (
            -int(node.placed_count),
            int(node.height_used_mm),
            int(node.first_pick_height_gain_mm),
            int(node.first_pick_opened_layer),
            int(node.cumulative_height_gain_mm),
            int(node.layer_opened_count),
            int(node.first_pick_height_waste_mm),
            int(node.cumulative_height_waste_mm),
            -int(round(float(node.first_pick_score_adjustment) * 1_000_000.0)),
            -int(round(float(node.cumulative_score_adjustment) * 1_000_000.0)),
            -int(round(float(node.cumulative_preview_objective) * 1_000_000.0)),
            float(node.tower_penalty_total),
            float(node.fragmentation_total),
            first_idx,
            seq_key,
        )

    @staticmethod
    def _height_waste_objective_key(node: _BeamNode) -> tuple[Any, ...]:
        seq_key = tuple((int(idx), str(box_id)) for idx, box_id in node.sequence[:10])
        first_idx = int(node.first_pick_index) if node.first_pick_index is not None else 10**9
        return (
            -int(node.placed_count),
            int(node.height_used_mm),
            int(node.cumulative_height_waste_mm),
            int(node.layer_opened_count),
            int(node.first_pick_height_gain_mm),
            int(node.cumulative_height_gain_mm),
            int(node.first_pick_opened_layer),
            int(node.first_pick_height_waste_mm),
            float(node.tower_penalty_total),
            float(node.fragmentation_total),
            first_idx,
            seq_key,
        )

    @staticmethod
    def _normalize_objective(objective: str | None) -> str:
        if objective in SUPPORTED_BEAM_OBJECTIVES:
            return str(objective)
        return OBJECTIVE_MAX_PLACED_THEN_MIN_HEIGHT_GAIN

    @classmethod
    def _local_child_key(
        cls,
        *,
        child: _BeamNode,
        opens_new_layer: bool,
        height_gain: int,
        height_waste_mm: int,
        score_adjustment: float,
        step_objective: float,
        objective: str,
    ) -> tuple[Any, ...]:
        if objective == OBJECTIVE_MAX_PLACED_THEN_MIN_HEIGHT_WASTE:
            return cls._objective_key(child, objective)
        return (
            1 if opens_new_layer else 0,
            int(height_gain),
            int(height_waste_mm),
            -int(round(score_adjustment * 1_000_000.0)),
            -int(round(step_objective * 1_000_000.0)),
            cls._objective_key(child, objective),
        )

    @classmethod
    def _rollout_local_key(
        cls,
        *,
        idx: int,
        is_new_layer: bool,
        gain: int,
        height_waste_mm: int,
        score_adjustment: float,
        preview_objective: float,
        placement_height_mm: int,
        current_height_mm: int,
        objective: str,
    ) -> tuple[Any, ...]:
        if objective == OBJECTIVE_MAX_PLACED_THEN_MIN_HEIGHT_WASTE:
            return (
                1 if is_new_layer else 0,
                int(gain),
                int(height_waste_mm),
                int(placement_height_mm),
                int(current_height_mm + gain),
                int(idx),
            )
        return (
            1 if is_new_layer else 0,
            int(gain),
            int(height_waste_mm),
            -int(round(score_adjustment * 1_000_000.0)),
            -int(round(preview_objective * 1_000_000.0)),
            int(placement_height_mm),
            int(current_height_mm + gain),
            int(idx),
        )

    @staticmethod
    def _preview_objective(preview: PlacementPreview) -> float:
        return (
            float(preview.packing_gain)
            - float(preview.fragmentation)
            + float(preview.score_adjustment or 0.0)
        )

    @classmethod
    def _rollout_count(
        cls,
        *,
        node: _BeamNode,
        pick_window: int,
        max_steps: int,
        deadline: float | None,
        objective: str,
    ) -> int:
        pallet = node.pallet.fork()
        window = list(node.window)
        upstream = list(node.upstream)
        layer_height_mm = int(node.current_layer_height_mm)
        n_items_in_layer = int(node.n_items_in_current_layer)
        steps = 0
        placed = 0
        while window and steps < max(1, int(max_steps)):
            if deadline is not None and time.perf_counter() >= deadline:
                break
            best_idx: int | None = None
            best_preview: PlacementPreview | None = None
            best_local_key: tuple[Any, ...] | None = None
            best_next_layer_height_mm: int | None = None
            best_next_layer_items: int | None = None
            limit = min(len(window), max(1, int(pick_window)))
            current_height = int(pallet.current_height_mm())
            current_layers = len(pallet.layers)
            for idx in range(limit):
                preview = pallet.preview_place(window[idx])
                if not preview.feasible or preview.placement is None:
                    continue
                dbg = preview.debug or {}
                is_new_layer = bool(dbg.get("is_new_layer")) or (int(preview.placement.layer_id) >= current_layers)
                gain = int(dbg.get("height_increase_mm") or 0)
                if is_new_layer:
                    gain = int(preview.placement.height_mm)
                height_waste_mm, next_layer_height_mm, next_layer_items = cls._height_waste_step(
                    is_new_layer=is_new_layer,
                    item_height_mm=int(preview.placement.height_mm),
                    current_layer_height_mm=int(layer_height_mm),
                    n_items_in_current_layer=int(n_items_in_layer),
                )
                score_adjustment = float(preview.score_adjustment or 0.0)
                preview_objective = cls._preview_objective(preview)
                local_key = cls._rollout_local_key(
                    idx=int(idx),
                    is_new_layer=is_new_layer,
                    gain=int(gain),
                    height_waste_mm=int(height_waste_mm),
                    score_adjustment=float(score_adjustment),
                    preview_objective=float(preview_objective),
                    placement_height_mm=int(preview.placement.height_mm),
                    current_height_mm=int(current_height),
                    objective=objective,
                )
                if best_local_key is None or local_key < best_local_key:
                    best_local_key = local_key
                    best_idx = int(idx)
                    best_preview = preview
                    best_next_layer_height_mm = int(next_layer_height_mm)
                    best_next_layer_items = int(next_layer_items)
            if best_idx is None or best_preview is None:
                break
            try:
                pallet.commit_place(best_preview)
            except Exception:
                break
            if best_next_layer_height_mm is not None and best_next_layer_items is not None:
                layer_height_mm = int(best_next_layer_height_mm)
                n_items_in_layer = int(best_next_layer_items)
            window.pop(best_idx)
            while len(window) < int(pick_window) and upstream:
                window.append(upstream.pop(0))
            placed += 1
            steps += 1
        return int(placed)

    @classmethod
    def _build_debug(
        cls,
        *,
        config: BeamPickConfig,
        expansions: int,
        exhausted: bool,
        infeasible_reasons: Counter[str],
        best: _BeamNode,
        frontier_sizes: dict[int, int],
        best_key_by_depth: dict[int, tuple[Any, ...]],
        elapsed_ms: float,
        projected_best: int,
        objective: str,
    ) -> dict[str, Any]:
        top_reasons = sorted(infeasible_reasons.items(), key=lambda item: (-item[1], item[0]))[:10]
        debug = {
            "beam_objective": str(objective),
            "beam_objective_requested": str(config.beam_objective),
            "beam_width": int(config.beam_width),
            "beam_depth": int(config.beam_depth),
            "beam_max_expansions": int(config.beam_max_expansions),
            "beam_time_budget_ms": int(config.beam_time_budget_ms),
            "expansions_used": int(expansions),
            "budget_exhausted": bool(exhausted),
            "elapsed_ms": float(elapsed_ms),
            "frontier_size_by_depth": {int(k): int(v) for k, v in frontier_sizes.items()},
            "best_score_by_depth": {int(k): list(v) for k, v in best_key_by_depth.items()},
            "best_sequence_first10": list(best.sequence[:10]),
            "best_placed_count": int(best.placed_count),
            "best_height_used_mm": int(best.height_used_mm),
            "best_cumulative_preview_objective": float(best.cumulative_preview_objective),
            "best_cumulative_score_adjustment": float(best.cumulative_score_adjustment),
            "best_cumulative_height_waste_mm": int(best.cumulative_height_waste_mm),
            "best_current_layer_height_mm": int(best.current_layer_height_mm),
            "best_n_items_in_current_layer": int(best.n_items_in_current_layer),
            "best_layers": len(best.pallet.layers),
            "projected_best_placed_count": int(projected_best),
            "top_infeasible_reasons": [[str(reason), int(count)] for reason, count in top_reasons],
            "best_objective_key": list(cls._objective_key(best, objective)),
        }
        return debug
