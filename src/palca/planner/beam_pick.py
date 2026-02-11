from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import time
from typing import Any, Sequence

from ..domain.box import Box
from ..domain.placement import PlacementPreview
from ..packer.pallet_model import PalletModel


@dataclass(frozen=True, slots=True)
class BeamPickConfig:
    beam_width: int = 12
    beam_depth: int = 6
    beam_max_expansions: int = 2500
    beam_time_budget_ms: int = 200
    beam_objective: str = "max_placed_then_min_height_gain"
    beam_debug: bool = False


@dataclass(frozen=True, slots=True)
class BeamPickResult:
    buffer_index: int | None
    box_id: int | str | None
    preview: PlacementPreview | None
    best_sequence: tuple[tuple[int, int | str], ...] = field(default_factory=tuple)
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _BeamNode:
    pallet: PalletModel
    window: tuple[Box, ...]
    upstream: tuple[Box, ...]
    placed_count: int
    height_used_mm: int
    cumulative_height_gain_mm: int
    layer_opened_count: int
    tower_penalty_total: float
    fragmentation_total: float
    first_pick_index: int | None
    first_pick_box_id: int | str | None
    first_pick_preview: PlacementPreview | None
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
        k = max(1, int(pick_window))
        root_window = tuple(window[:k])
        root_upstream = tuple(upstream or ())

        if not root_window:
            return BeamPickResult(buffer_index=None, box_id=None, preview=None, debug={"reason": "empty_window"})

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
            cumulative_height_gain_mm=0,
            layer_opened_count=0,
            tower_penalty_total=0.0,
            fragmentation_total=0.0,
            first_pick_index=None,
            first_pick_box_id=None,
            first_pick_preview=None,
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
                    next_sequence = node.sequence + ((int(idx), box.box_id),)

                    tower_penalty = max(0.0, -float(preview_debug.get("tower_penalty", 0.0) or 0.0))
                    fragmentation = max(0.0, float(preview.fragmentation))

                    child = _BeamNode(
                        pallet=child_pallet,
                        window=next_window,
                        upstream=next_upstream,
                        placed_count=int(node.placed_count) + 1,
                        height_used_mm=child_height,
                        cumulative_height_gain_mm=int(node.cumulative_height_gain_mm) + int(height_gain),
                        layer_opened_count=int(node.layer_opened_count) + (1 if opens_new_layer else 0),
                        tower_penalty_total=float(node.tower_penalty_total) + tower_penalty,
                        fragmentation_total=float(node.fragmentation_total) + fragmentation,
                        first_pick_index=first_pick_index,
                        first_pick_box_id=first_pick_box_id,
                        first_pick_preview=first_pick_preview,
                        sequence=next_sequence,
                    )
                    local_key = (
                        1 if opens_new_layer else 0,
                        int(height_gain),
                        cls._objective_key(child),
                    )
                    node_children.append((local_key, child))

                if node_children:
                    node_children.sort(key=lambda item: item[0])
                    next_frontier.extend(child for _, child in node_children)
                if exhausted:
                    break

            if next_frontier:
                next_frontier.sort(key=cls._objective_key)
                pruned = next_frontier[:beam_width]
                frontier_sizes[depth] = len(pruned)
                best_key_by_depth[depth] = cls._objective_key(pruned[0])
                if cls._objective_key(pruned[0]) < cls._objective_key(best):
                    best = pruned[0]
                frontier = pruned
            else:
                frontier = []
                break

            if exhausted:
                break

        if best.placed_count <= 0 or best.first_pick_index is None:
            debug = cls._build_debug(
                config=config,
                expansions=expansions,
                exhausted=exhausted,
                infeasible_reasons=infeasible_reasons,
                best=best,
                frontier_sizes=frontier_sizes,
                best_key_by_depth=best_key_by_depth,
            )
            return BeamPickResult(buffer_index=None, box_id=None, preview=None, debug=debug)

        debug = cls._build_debug(
            config=config,
            expansions=expansions,
            exhausted=exhausted,
            infeasible_reasons=infeasible_reasons,
            best=best,
            frontier_sizes=frontier_sizes,
            best_key_by_depth=best_key_by_depth,
        )
        return BeamPickResult(
            buffer_index=int(best.first_pick_index),
            box_id=best.first_pick_box_id,
            preview=best.first_pick_preview,
            best_sequence=best.sequence,
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
    def _objective_key(node: _BeamNode) -> tuple[Any, ...]:
        seq_key = tuple((int(idx), str(box_id)) for idx, box_id in node.sequence[:10])
        first_idx = int(node.first_pick_index) if node.first_pick_index is not None else 10**9
        return (
            -int(node.placed_count),
            int(node.height_used_mm),
            int(node.cumulative_height_gain_mm),
            int(node.layer_opened_count),
            float(node.tower_penalty_total),
            float(node.fragmentation_total),
            first_idx,
            seq_key,
        )

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
    ) -> dict[str, Any]:
        top_reasons = sorted(infeasible_reasons.items(), key=lambda item: (-item[1], item[0]))[:10]
        debug = {
            "beam_objective": str(config.beam_objective),
            "beam_width": int(config.beam_width),
            "beam_depth": int(config.beam_depth),
            "beam_max_expansions": int(config.beam_max_expansions),
            "beam_time_budget_ms": int(config.beam_time_budget_ms),
            "expansions_used": int(expansions),
            "budget_exhausted": bool(exhausted),
            "frontier_size_by_depth": {int(k): int(v) for k, v in frontier_sizes.items()},
            "best_score_by_depth": {int(k): list(v) for k, v in best_key_by_depth.items()},
            "best_sequence_first10": list(best.sequence[:10]),
            "best_placed_count": int(best.placed_count),
            "best_height_used_mm": int(best.height_used_mm),
            "best_layers": len(best.pallet.layers),
            "top_infeasible_reasons": [[str(reason), int(count)] for reason, count in top_reasons],
            "best_objective_key": list(cls._objective_key(best)),
        }
        return debug
