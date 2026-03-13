from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from statistics import mean
from typing import Any, Callable, Literal, Sequence, TypeVar


LayerPhase = Literal["opening", "filling", "repair", "closed"]
CandidateT = TypeVar("CandidateT")
ViolationCause = Literal["closed_reopen", "width_overflow", "below_frontier"]
BlockedCause = Literal["state", "closure", "frontier"]


@dataclass
class FrontierLayerState:
    layer: int
    phase: LayerPhase
    moves: int = 0
    repair_moves: int = 0
    opened_at_event: int = 0
    closed_at_event: int | None = None


@dataclass
class FrontierPalletState:
    active_layer: int | None = None
    repair_layer: int | None = None
    highest_opened_layer: int | None = None
    opening_moves_remaining: int = 0
    repair_burst_used: int = 0
    layers: dict[int, FrontierLayerState] = field(default_factory=dict)
    last_available_layers: set[int] = field(default_factory=set)


def resolve_frontier_layer(
    *,
    layer_id: Any,
    z_mm: Any,
    stacking_mode: str,
    z_band_mm: int | None = None,
) -> int | None:
    try:
        z_value = int(z_mm)
    except Exception:
        z_value = None
    try:
        layer_value = int(layer_id)
    except Exception:
        layer_value = None

    mode = str(stacking_mode or "layers").strip().lower()
    band_mm = max(0, int(z_band_mm or 0))
    if mode == "heightfield":
        if z_value is None:
            return None
        if band_mm > 0:
            return int(z_value // band_mm)
        return int(z_value)

    if layer_value is not None:
        return int(layer_value)
    if z_value is not None and band_mm > 0:
        return int(z_value // band_mm)
    return z_value


class TwoLayerFrontierController:
    def __init__(
        self,
        *,
        enabled: bool = False,
        opening_span_moves: int = 2,
        repair_burst_max: int = 2,
        max_backstep_depth: int = 1,
    ) -> None:
        self.enabled = bool(enabled)
        self.opening_span_moves = max(1, int(opening_span_moves))
        self.repair_burst_max = max(1, int(repair_burst_max))
        self.allowed_backstep_depth = max(0, int(max_backstep_depth))
        self._event_index = 0
        self._pallets: dict[int | str, FrontierPalletState] = {}
        self.reentries_total = 0
        self.max_backstep_depth = 0
        self.frontier_width_max = 0
        self.two_layer_frontier_violations = 0
        self.repair_moves_total = 0
        self.layer_reopen_events_total = 0
        self.repair_candidates_available_total = 0
        self.repair_candidates_selected_total = 0
        self.repair_candidates_blocked_total = 0
        self.repair_candidates_blocked_by_state_total = 0
        self.repair_candidates_blocked_by_closure_total = 0
        self.repair_candidates_blocked_by_frontier_total = 0
        self.frontier_violation_closed_reopen_total = 0
        self.frontier_violation_width_overflow_total = 0
        self.frontier_violation_below_frontier_total = 0
        self._decision_index = 0
        self._decision_trace_limit = 256
        self._decision_trace: list[dict[str, object]] = []
        self._layer_closure_scores: list[float] = []

    def reset_pallet(self, pallet_id: int | str) -> None:
        self._pallets.pop(pallet_id, None)

    def snapshot(self) -> dict[int | str, FrontierPalletState]:
        return deepcopy(self._pallets)

    def clone_snapshot(self, snapshot: dict[int | str, FrontierPalletState] | None) -> dict[int | str, FrontierPalletState]:
        if snapshot is None:
            return {}
        return deepcopy(snapshot)

    def frontier_kpis(self) -> dict[str, object]:
        return {
            "reentries_total": int(self.reentries_total),
            "max_backstep_depth": int(self.max_backstep_depth),
            "frontier_width_max": int(self.frontier_width_max),
            "two_layer_frontier_violations": int(self.two_layer_frontier_violations),
            "repair_moves_total": int(self.repair_moves_total),
            "layer_reopen_events_total": int(self.layer_reopen_events_total),
            "repair_candidates_available_total": int(self.repair_candidates_available_total),
            "repair_candidates_selected_total": int(self.repair_candidates_selected_total),
            "repair_candidates_blocked_total": int(self.repair_candidates_blocked_total),
            "repair_candidates_blocked_by_state_total": int(self.repair_candidates_blocked_by_state_total),
            "repair_candidates_blocked_by_closure_total": int(self.repair_candidates_blocked_by_closure_total),
            "repair_candidates_blocked_by_frontier_total": int(self.repair_candidates_blocked_by_frontier_total),
            "frontier_violation_closed_reopen_total": int(self.frontier_violation_closed_reopen_total),
            "frontier_violation_width_overflow_total": int(self.frontier_violation_width_overflow_total),
            "frontier_violation_below_frontier_total": int(self.frontier_violation_below_frontier_total),
            "frontier_decision_trace": list(self._decision_trace),
            "layer_closure_score": (
                float(mean(self._layer_closure_scores)) if self._layer_closure_scores else 1.0
            ),
        }

    def legal_frontier(self, *, pallet_id: int | str, snapshot: dict[int | str, FrontierPalletState] | None = None) -> tuple[int, ...]:
        state = self._get_state(pallet_id=pallet_id, snapshot=snapshot, create=False)
        if state is None:
            return ()
        frontier: list[int] = []
        if state.active_layer is not None and not self._is_closed(state, state.active_layer):
            frontier.append(int(state.active_layer))
        if (
            state.repair_layer is not None
            and state.repair_layer != state.active_layer
            and not self._is_closed(state, state.repair_layer)
        ):
            frontier.append(int(state.repair_layer))
        return tuple(frontier)

    def is_candidate_legal(
        self,
        *,
        pallet_id: int | str,
        layer: int,
        snapshot: dict[int | str, FrontierPalletState] | None = None,
    ) -> bool:
        state = self._get_state(pallet_id=pallet_id, snapshot=snapshot, create=False)
        if state is None:
            return True
        frontier = self.legal_frontier(pallet_id=pallet_id, snapshot=snapshot)
        if not frontier:
            return True
        return int(layer) in frontier

    def select_candidates(
        self,
        *,
        pallet_id: int | str,
        candidates: Sequence[CandidateT],
        layer_fn: Callable[[CandidateT], int | None],
        best_candidate_fn: Callable[[Sequence[CandidateT]], CandidateT],
        snapshot: dict[int | str, FrontierPalletState] | None = None,
        mutate_metrics: bool = True,
    ) -> list[CandidateT]:
        if not self.enabled or not candidates:
            return list(candidates)

        state = self._get_state(pallet_id=pallet_id, snapshot=snapshot)
        by_layer: dict[int, list[CandidateT]] = {}
        for candidate in candidates:
            layer = layer_fn(candidate)
            if layer is None:
                continue
            by_layer.setdefault(int(layer), []).append(candidate)

        available_layers = sorted(by_layer)
        state.last_available_layers = set(int(layer) for layer in available_layers)
        if not available_layers:
            return []

        active_layer = int(state.active_layer) if state.active_layer is not None else None
        repair_layer = int(state.repair_layer) if state.repair_layer is not None else None
        active_candidates = by_layer.get(active_layer, []) if active_layer is not None else []
        repair_candidates = by_layer.get(repair_layer, []) if repair_layer is not None else []
        closed_lower_layers = self._closed_lower_layers(
            state=state,
            available_layers=available_layers,
        )
        below_frontier_layers = self._below_frontier_layers(
            state=state,
            available_layers=available_layers,
        )
        repair_available = bool(repair_layer is not None and repair_candidates)

        if state.active_layer is None:
            chosen_layer = int(min(available_layers))
            self._update_frontier_width(1, mutate_metrics=mutate_metrics)
            return self._finalize_selection(
                pallet_id=pallet_id,
                state=state,
                chosen_layer=chosen_layer,
                selected_candidates=by_layer[chosen_layer],
                active_layer=None,
                repair_layer=None,
                num_candidates_active_layer=0,
                num_candidates_repair_layer=0,
                reason_selected_layer="initial_active_layer",
                repair_not_selected_reason=None,
                repair_available=False,
                blocked_cause=None,
                mutate_metrics=mutate_metrics,
            )

        legal_layers = self._candidate_legal_layers(
            state=state,
            available_layers=available_layers,
            mutate_metrics=mutate_metrics,
        )
        if not legal_layers:
            return self._finalize_selection(
                pallet_id=pallet_id,
                state=state,
                chosen_layer=None,
                selected_candidates=[],
                active_layer=active_layer,
                repair_layer=repair_layer,
                num_candidates_active_layer=len(active_candidates),
                num_candidates_repair_layer=len(repair_candidates),
                reason_selected_layer="no_legal_layer",
                repair_not_selected_reason=(
                    "repair_layer_closed"
                    if closed_lower_layers
                    else "below_frontier_candidate_only"
                    if below_frontier_layers
                    else None
                ),
                repair_available=repair_available,
                blocked_cause=(
                    "state"
                    if repair_available
                    else "closure"
                    if closed_lower_layers
                    else "frontier"
                    if below_frontier_layers
                    else None
                ),
                mutate_metrics=mutate_metrics,
            )

        if len(legal_layers) == 1:
            chosen_layer = int(legal_layers[0])
            return self._finalize_selection(
                pallet_id=pallet_id,
                state=state,
                chosen_layer=chosen_layer,
                selected_candidates=by_layer[chosen_layer],
                active_layer=active_layer,
                repair_layer=repair_layer,
                num_candidates_active_layer=len(active_candidates),
                num_candidates_repair_layer=len(repair_candidates),
                reason_selected_layer=(
                    "repair_only"
                    if repair_layer is not None and chosen_layer == repair_layer
                    else "active_only"
                    if active_layer is not None and chosen_layer == active_layer
                    else "next_active_layer"
                ),
                repair_not_selected_reason=(
                    "repair_layer_closed"
                    if chosen_layer != repair_layer and closed_lower_layers
                    else "below_frontier_candidate_only"
                    if chosen_layer != repair_layer and below_frontier_layers
                    else None
                ),
                repair_available=repair_available,
                blocked_cause=(
                    "state"
                    if repair_available and chosen_layer != repair_layer
                    else "closure"
                    if chosen_layer != repair_layer and closed_lower_layers
                    else "frontier"
                    if chosen_layer != repair_layer and below_frontier_layers
                    else None
                ),
                mutate_metrics=mutate_metrics,
            )

        if not active_candidates:
            return self._finalize_selection(
                pallet_id=pallet_id,
                state=state,
                chosen_layer=repair_layer,
                selected_candidates=repair_candidates,
                active_layer=active_layer,
                repair_layer=repair_layer,
                num_candidates_active_layer=0,
                num_candidates_repair_layer=len(repair_candidates),
                reason_selected_layer="repair_only",
                repair_not_selected_reason=None,
                repair_available=repair_available,
                blocked_cause=None,
                mutate_metrics=mutate_metrics,
            )
        if not repair_candidates:
            return self._finalize_selection(
                pallet_id=pallet_id,
                state=state,
                chosen_layer=active_layer,
                selected_candidates=active_candidates,
                active_layer=active_layer,
                repair_layer=repair_layer,
                num_candidates_active_layer=len(active_candidates),
                num_candidates_repair_layer=0,
                reason_selected_layer="active_only",
                repair_not_selected_reason=(
                    "repair_layer_closed"
                    if closed_lower_layers
                    else "below_frontier_candidate_only"
                    if below_frontier_layers
                    else None
                ),
                repair_available=False,
                blocked_cause=(
                    "closure" if closed_lower_layers else "frontier" if below_frontier_layers else None
                ),
                mutate_metrics=mutate_metrics,
            )

        if int(state.opening_moves_remaining) > 0:
            return self._finalize_selection(
                pallet_id=pallet_id,
                state=state,
                chosen_layer=active_layer,
                selected_candidates=active_candidates,
                active_layer=active_layer,
                repair_layer=repair_layer,
                num_candidates_active_layer=len(active_candidates),
                num_candidates_repair_layer=len(repair_candidates),
                reason_selected_layer="opening_window_active",
                repair_not_selected_reason="opening_window_active",
                repair_available=repair_available,
                blocked_cause="state",
                mutate_metrics=mutate_metrics,
            )
        if int(state.repair_burst_used) >= int(self.repair_burst_max):
            return self._finalize_selection(
                pallet_id=pallet_id,
                state=state,
                chosen_layer=active_layer,
                selected_candidates=active_candidates,
                active_layer=active_layer,
                repair_layer=repair_layer,
                num_candidates_active_layer=len(active_candidates),
                num_candidates_repair_layer=len(repair_candidates),
                reason_selected_layer="repair_burst_exhausted_active",
                repair_not_selected_reason="repair_burst_exhausted",
                repair_available=repair_available,
                blocked_cause="state",
                mutate_metrics=mutate_metrics,
            )

        best_active = best_candidate_fn(active_candidates)
        best_repair = best_candidate_fn(repair_candidates)
        chosen = repair_candidates if best_candidate_fn([best_active, best_repair]) is best_repair else active_candidates
        chosen_layer = repair_layer if chosen is repair_candidates else active_layer
        return self._finalize_selection(
            pallet_id=pallet_id,
            state=state,
            chosen_layer=chosen_layer,
            selected_candidates=chosen,
            active_layer=active_layer,
            repair_layer=repair_layer,
            num_candidates_active_layer=len(active_candidates),
            num_candidates_repair_layer=len(repair_candidates),
            reason_selected_layer="best_score_repair" if chosen is repair_candidates else "best_score_active",
            repair_not_selected_reason=None if chosen is repair_candidates else "best_score_active",
            repair_available=repair_available,
            blocked_cause=None if chosen is repair_candidates else "state",
            mutate_metrics=mutate_metrics,
        )

    def register_selection(
        self,
        *,
        pallet_id: int | str,
        layer: int | None,
        snapshot: dict[int | str, FrontierPalletState] | None = None,
        mutate_metrics: bool = True,
    ) -> None:
        if not self.enabled or layer is None:
            return

        state = self._get_state(pallet_id=pallet_id, snapshot=snapshot)
        chosen_layer = int(layer)
        self._event_index += 1

        if state.active_layer is None:
            self._open_initial_layer(state=state, layer=chosen_layer)
            self._update_backstep_depth(state=state, layer=chosen_layer, mutate_metrics=mutate_metrics)
            self._validate_invariants(state=state, mutate_metrics=mutate_metrics)
            return

        active_layer = state.active_layer
        repair_layer = state.repair_layer

        if self._is_closed(state, chosen_layer):
            if mutate_metrics:
                self._record_violation("closed_reopen")
                self.layer_reopen_events_total += 1
            return

        if active_layer is not None and chosen_layer == int(active_layer):
            layer_state = self._ensure_layer_state(state=state, layer=chosen_layer, phase=self._phase_for_active(state))
            layer_state.moves += 1
            layer_state.phase = self._phase_for_active(state)
            state.repair_burst_used = 0
            if state.opening_moves_remaining > 0:
                state.opening_moves_remaining -= 1
                if state.opening_moves_remaining <= 0:
                    layer_state.phase = "filling"
            self._update_backstep_depth(state=state, layer=chosen_layer, mutate_metrics=mutate_metrics)
            self._validate_invariants(state=state, mutate_metrics=mutate_metrics)
            return

        if repair_layer is not None and chosen_layer == int(repair_layer):
            layer_state = self._ensure_layer_state(state=state, layer=chosen_layer, phase="repair")
            layer_state.phase = "repair"
            layer_state.moves += 1
            layer_state.repair_moves += 1
            state.repair_burst_used += 1
            if mutate_metrics:
                self.reentries_total += 1
                self.repair_moves_total += 1
            self._update_backstep_depth(state=state, layer=chosen_layer, mutate_metrics=mutate_metrics)
            self._validate_invariants(state=state, mutate_metrics=mutate_metrics)
            return

        if active_layer is not None and chosen_layer > int(active_layer):
            self._shift_frontier_up(state=state, new_active_layer=chosen_layer, mutate_metrics=mutate_metrics)
            self._update_backstep_depth(state=state, layer=chosen_layer, mutate_metrics=mutate_metrics)
            self._validate_invariants(state=state, mutate_metrics=mutate_metrics)
            return

        if mutate_metrics:
            self._record_violation("below_frontier")
        self._validate_invariants(state=state, mutate_metrics=mutate_metrics)

    def _open_initial_layer(self, *, state: FrontierPalletState, layer: int) -> None:
        state.active_layer = int(layer)
        state.repair_layer = None
        state.highest_opened_layer = int(layer)
        state.opening_moves_remaining = max(0, int(self.opening_span_moves) - 1)
        state.repair_burst_used = 0
        layer_state = self._ensure_layer_state(state=state, layer=int(layer), phase="opening")
        layer_state.moves += 1
        layer_state.phase = "opening" if state.opening_moves_remaining > 0 else "filling"
        self._update_frontier_width(1, mutate_metrics=True)

    def _shift_frontier_up(self, *, state: FrontierPalletState, new_active_layer: int, mutate_metrics: bool) -> None:
        prior_active = state.active_layer
        prior_repair = state.repair_layer

        if prior_repair is not None:
            self._close_layer(
                state=state,
                layer=int(prior_repair),
                mutate_metrics=mutate_metrics,
            )

        if prior_active is not None:
            active_state = self._ensure_layer_state(state=state, layer=int(prior_active), phase="repair")
            if active_state.phase != "closed":
                active_state.phase = "repair"
            state.repair_layer = int(prior_active)
        else:
            state.repair_layer = None

        state.active_layer = int(new_active_layer)
        state.highest_opened_layer = (
            int(new_active_layer)
            if state.highest_opened_layer is None
            else max(int(state.highest_opened_layer), int(new_active_layer))
        )
        state.opening_moves_remaining = max(0, int(self.opening_span_moves) - 1)
        state.repair_burst_used = 0

        active_state = self._ensure_layer_state(state=state, layer=int(new_active_layer), phase="opening")
        if active_state.phase == "closed":
            if mutate_metrics:
                self._record_violation("closed_reopen")
                self.layer_reopen_events_total += 1
            return
        active_state.moves += 1
        active_state.phase = "opening" if state.opening_moves_remaining > 0 else "filling"
        self._update_frontier_width(2 if state.repair_layer is not None else 1, mutate_metrics=mutate_metrics)

    def _close_layer(self, *, state: FrontierPalletState, layer: int, mutate_metrics: bool) -> None:
        layer_state = self._ensure_layer_state(state=state, layer=int(layer), phase="closed")
        if layer_state.phase == "closed":
            return
        layer_state.phase = "closed"
        layer_state.closed_at_event = int(self._event_index)
        viable_left = int(layer) in set(state.last_available_layers)
        self._layer_closure_scores.append(0.0 if viable_left else 1.0)
        if state.repair_layer == int(layer):
            state.repair_layer = None
        if state.active_layer == int(layer):
            state.active_layer = None
        if mutate_metrics:
            self._validate_invariants(state=state, mutate_metrics=True)

    def _candidate_legal_layers(
        self,
        *,
        state: FrontierPalletState,
        available_layers: list[int],
        mutate_metrics: bool,
    ) -> tuple[int, ...]:
        active_layer = state.active_layer
        repair_layer = state.repair_layer
        legal: list[int] = []

        if active_layer is not None and int(active_layer) in available_layers and not self._is_closed(state, int(active_layer)):
            legal.append(int(active_layer))
        if (
            repair_layer is not None
            and int(repair_layer) in available_layers
            and repair_layer != active_layer
            and not self._is_closed(state, int(repair_layer))
        ):
            legal.append(int(repair_layer))

        if legal:
            self._update_frontier_width(len(legal), mutate_metrics=mutate_metrics)
            illegal_lower = [
                layer
                for layer in available_layers
                if layer < int(active_layer if active_layer is not None else layer + 1)
                and int(layer) not in set(legal)
            ]
            if illegal_lower and mutate_metrics:
                self._record_violation("below_frontier")
            return tuple(legal)

        if active_layer is not None:
            higher_layers = [layer for layer in available_layers if int(layer) > int(active_layer)]
            if higher_layers:
                self._update_frontier_width(1, mutate_metrics=mutate_metrics)
                return (int(min(higher_layers)),)

        illegal_lower = [
            layer
            for layer in available_layers
            if repair_layer is None or int(layer) < int(repair_layer)
        ]
        if illegal_lower and mutate_metrics:
            self._record_violation("below_frontier")
        return ()

    def _validate_invariants(self, *, state: FrontierPalletState, mutate_metrics: bool) -> bool:
        active_count = 1 if state.active_layer is not None and not self._is_closed(state, int(state.active_layer)) else 0
        repair_count = (
            1
            if state.repair_layer is not None
            and state.repair_layer != state.active_layer
            and not self._is_closed(state, int(state.repair_layer))
            else 0
        )
        ok = True
        if active_count > 1 or repair_count > 1:
            ok = False
        if active_count + repair_count > 2:
            ok = False

        repair_layer = state.repair_layer
        if repair_layer is not None:
            for layer, layer_state in state.layers.items():
                if int(layer) < int(repair_layer) and layer_state.phase != "closed":
                    ok = False
                    layer_state.phase = "closed"

        if not ok and mutate_metrics:
            if active_count + repair_count > 2:
                self._record_violation("width_overflow")
            else:
                self._record_violation("below_frontier")
        return ok

    def _update_backstep_depth(self, *, state: FrontierPalletState, layer: int, mutate_metrics: bool) -> None:
        highest = state.highest_opened_layer
        if highest is None:
            state.highest_opened_layer = int(layer)
            highest = int(layer)
        depth = max(0, int(highest) - int(layer))
        if mutate_metrics:
            self.max_backstep_depth = max(int(self.max_backstep_depth), int(depth))
            if int(depth) > int(self.allowed_backstep_depth):
                self._record_violation("below_frontier")

    def _update_frontier_width(self, width: int, *, mutate_metrics: bool) -> None:
        if not mutate_metrics:
            return
        self.frontier_width_max = max(int(self.frontier_width_max), int(width))
        if int(width) > 2:
            self._record_violation("width_overflow")

    @staticmethod
    def _phase_for_active(state: FrontierPalletState) -> LayerPhase:
        return "opening" if int(state.opening_moves_remaining) > 0 else "filling"

    @staticmethod
    def _is_closed(state: FrontierPalletState, layer: int) -> bool:
        layer_state = state.layers.get(int(layer))
        return bool(layer_state is not None and layer_state.phase == "closed")

    def _get_state(
        self,
        *,
        pallet_id: int | str,
        snapshot: dict[int | str, FrontierPalletState] | None,
        create: bool = True,
    ) -> FrontierPalletState | None:
        store = self._pallets if snapshot is None else snapshot
        if pallet_id in store:
            return store[pallet_id]
        if not create:
            return None
        state = FrontierPalletState()
        store[pallet_id] = state
        return state

    def _ensure_layer_state(
        self,
        *,
        state: FrontierPalletState,
        layer: int,
        phase: LayerPhase,
    ) -> FrontierLayerState:
        current = state.layers.get(int(layer))
        if current is not None:
            return current
        created = FrontierLayerState(
            layer=int(layer),
            phase=phase,
            opened_at_event=int(self._event_index),
        )
        state.layers[int(layer)] = created
        return created

    def _record_violation(self, cause: ViolationCause) -> None:
        self.two_layer_frontier_violations += 1
        if cause == "closed_reopen":
            self.frontier_violation_closed_reopen_total += 1
            return
        if cause == "width_overflow":
            self.frontier_violation_width_overflow_total += 1
            return
        self.frontier_violation_below_frontier_total += 1

    def _record_blocked_repair(self, cause: BlockedCause) -> None:
        self.repair_candidates_blocked_total += 1
        if cause == "state":
            self.repair_candidates_blocked_by_state_total += 1
            return
        if cause == "closure":
            self.repair_candidates_blocked_by_closure_total += 1
            return
        self.repair_candidates_blocked_by_frontier_total += 1

    def _finalize_selection(
        self,
        *,
        pallet_id: int | str,
        state: FrontierPalletState,
        chosen_layer: int | None,
        selected_candidates: Sequence[CandidateT],
        active_layer: int | None,
        repair_layer: int | None,
        num_candidates_active_layer: int,
        num_candidates_repair_layer: int,
        reason_selected_layer: str,
        repair_not_selected_reason: str | None,
        repair_available: bool,
        blocked_cause: BlockedCause | None,
        mutate_metrics: bool,
    ) -> list[CandidateT]:
        if mutate_metrics:
            if repair_available:
                self.repair_candidates_available_total += 1
            if repair_available and repair_layer is not None and chosen_layer == int(repair_layer):
                self.repair_candidates_selected_total += 1
            elif blocked_cause is not None:
                self._record_blocked_repair(blocked_cause)
            self._record_decision_trace(
                pallet_id=pallet_id,
                state=state,
                chosen_layer=chosen_layer,
                active_layer=active_layer,
                repair_layer=repair_layer,
                num_candidates_active_layer=num_candidates_active_layer,
                num_candidates_repair_layer=num_candidates_repair_layer,
                reason_selected_layer=reason_selected_layer,
                repair_not_selected_reason=repair_not_selected_reason,
            )
        return list(selected_candidates)

    def _record_decision_trace(
        self,
        *,
        pallet_id: int | str,
        state: FrontierPalletState,
        chosen_layer: int | None,
        active_layer: int | None,
        repair_layer: int | None,
        num_candidates_active_layer: int,
        num_candidates_repair_layer: int,
        reason_selected_layer: str,
        repair_not_selected_reason: str | None,
    ) -> None:
        self._decision_index += 1
        if len(self._decision_trace) >= int(self._decision_trace_limit):
            return
        self._decision_trace.append(
            {
                "decision_index": int(self._decision_index),
                "pallet_id": pallet_id,
                "active_layer": active_layer,
                "repair_layer": repair_layer,
                "selected_layer": chosen_layer,
                "num_candidates_active_layer": int(num_candidates_active_layer),
                "num_candidates_repair_layer": int(num_candidates_repair_layer),
                "reason_selected_layer": str(reason_selected_layer),
                "repair_not_selected_reason": repair_not_selected_reason,
                "opening_moves_remaining": int(state.opening_moves_remaining),
                "repair_burst_used": int(state.repair_burst_used),
            }
        )

    def _closed_lower_layers(
        self,
        *,
        state: FrontierPalletState,
        available_layers: Sequence[int],
    ) -> tuple[int, ...]:
        if state.active_layer is None:
            return ()
        return tuple(
            int(layer)
            for layer in available_layers
            if int(layer) < int(state.active_layer) and self._is_closed(state, int(layer))
        )

    def _below_frontier_layers(
        self,
        *,
        state: FrontierPalletState,
        available_layers: Sequence[int],
    ) -> tuple[int, ...]:
        if state.active_layer is None:
            return ()
        repair_layer = state.repair_layer
        closed_layers = set(self._closed_lower_layers(state=state, available_layers=available_layers))
        return tuple(
            int(layer)
            for layer in available_layers
            if int(layer) < int(state.active_layer)
            and int(layer) not in closed_layers
            and (repair_layer is None or int(layer) < int(repair_layer))
        )
