from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Protocol, Sequence, TYPE_CHECKING

from ..domain.box import Box
from ..domain.placement import Placement
from .layer import LayerState
from .maxrects2d import MaxRectsCandidate

DEFAULT_K = 25

if TYPE_CHECKING:
    from .pallet_model import PalletModel


STAND_HW_MIN_SUPPORT_RATIO = 0.92


def is_stand_hw_orientation(placement: Placement) -> bool:
    family = str(getattr(placement, "orientation_family", "") or "").strip().lower()
    return family == "stand_hw"


def required_support_for_orientation(*, is_stand_hw: bool, min_support_ratio: float) -> float:
    required = float(min_support_ratio)
    if is_stand_hw:
        # Standing on the long axis is more sensitive to marginal supports.
        required = max(required, STAND_HW_MIN_SUPPORT_RATIO)
    return required


class ManifestControl(Protocol):
    def is_eligible(self, box: Box, pallet: "PalletModel") -> bool:
        ...


class PointControl(Protocol):
    def candidates(
        self,
        *,
        layer: LayerState,
        length_mm: int,
        width_mm: int,
        height_mm: int,
        is_new_layer: bool,
    ) -> Iterable[MaxRectsCandidate]:
        ...


@dataclass(frozen=True)
class PlacementControlResult:
    feasible: bool
    placement: Placement
    score_delta: float = 0.0
    reason: str | None = None
    debug: dict[str, Any] = field(default_factory=dict)


class PlacementControl(Protocol):
    def evaluate(
        self,
        *,
        pallet: "PalletModel",
        box: Box,
        placement: Placement,
    ) -> PlacementControlResult:
        ...


@dataclass(frozen=True)
class StabilityConfig:
    mode: str = "ratio+corners"
    min_support_ratio: float = 0.75
    eps_mm: float = 1.0
    settle_snap_grid: bool = False
    grid_mm: int | None = None
    settle_max_iter: int = 0
    settle_timeout_ms: int = 0
    support_frontier_refine_enabled: bool = True
    support_frontier_ratio_margin: float = 0.02
    support_frontier_xy_step_mm: int = 1
    support_frontier_max_xy_attempts: int = 4
    support_frontier_max_refines_per_pallet: int = 48

    def enable_ratio(self) -> bool:
        return self.mode in ("ratio", "ratio+corners", "ratio+corners+settle")

    def enable_corners(self) -> bool:
        return self.mode in ("ratio+corners", "ratio+corners+settle")

    def enable_settle(self) -> bool:
        return self.mode in ("ratio+corners+settle",)


@dataclass(frozen=True)
class LoadBearConfig:
    heavy_bottom: bool = False
    max_overweight_ratio: float = 1.5
    penalty_weight: float = 1.0
    loadbear_factor: float = 1.0


@dataclass(frozen=True)
class BalanceConfig:
    balance_weight: float = 0.0


@dataclass(frozen=True)
class ControlConfig:
    stability: StabilityConfig = StabilityConfig()
    loadbear: LoadBearConfig = LoadBearConfig()
    balance: BalanceConfig = BalanceConfig()


@dataclass(frozen=True)
class ControlStack:
    manifest: ManifestControl
    point: PointControl
    placement_controls: Sequence[PlacementControl]


@dataclass(frozen=True)
class DefaultManifestControl:
    def is_eligible(self, box: Box, pallet: "PalletModel") -> bool:
        return True


@dataclass(frozen=True)
class DefaultPointControl:
    def candidates(
        self,
        *,
        layer: LayerState,
        length_mm: int,
        width_mm: int,
        height_mm: int,
        is_new_layer: bool,
    ) -> Iterable[MaxRectsCandidate]:
        return layer.bin.find_candidates(length_mm, width_mm, k=DEFAULT_K)


@dataclass(frozen=True)
class StabilityPlacementControl:
    config: StabilityConfig = StabilityConfig()

    @staticmethod
    def _in_bin_bounds(*, pallet: "PalletModel", placement: Placement) -> bool:
        min_x = int(pallet.spec.offset_mm)
        min_y = int(pallet.spec.offset_mm)
        max_x = min_x + int(pallet.spec.bin_length_mm) - int(placement.length_mm)
        max_y = min_y + int(pallet.spec.bin_width_mm) - int(placement.width_mm)
        return (
            int(placement.x_mm) >= min_x
            and int(placement.y_mm) >= min_y
            and int(placement.x_mm) <= max_x
            and int(placement.y_mm) <= max_y
        )

    def try_small_xy_support_adjustments(
        self,
        *,
        pallet: "PalletModel",
        placement: Placement,
        eps_mm: float,
    ) -> list[Placement]:
        cfg = self.config
        step_mm = max(1, int(cfg.support_frontier_xy_step_mm))
        max_attempts = max(0, int(cfg.support_frontier_max_xy_attempts))
        if max_attempts <= 0:
            return []

        offsets: list[tuple[int, int]] = [
            (-step_mm, 0),
            (step_mm, 0),
            (0, -step_mm),
            (0, step_mm),
            (-step_mm, -step_mm),
            (-step_mm, step_mm),
            (step_mm, -step_mm),
            (step_mm, step_mm),
        ]

        out: list[Placement] = []
        seen: set[tuple[int, int]] = set()
        for dx, dy in offsets:
            if len(out) >= max_attempts:
                break
            candidate = replace(
                placement,
                x_mm=int(placement.x_mm) + int(dx),
                y_mm=int(placement.y_mm) + int(dy),
            )
            key = (int(candidate.x_mm), int(candidate.y_mm))
            if key in seen:
                continue
            seen.add(key)
            if not self._in_bin_bounds(pallet=pallet, placement=candidate):
                continue
            if pallet._collides(candidate, eps_mm=eps_mm):  # noqa: SLF001
                continue
            out.append(candidate)
        return out

    def local_support_frontier_search(
        self,
        *,
        pallet: "PalletModel",
        placement: Placement,
        eps_mm: float,
        required_support: float,
    ) -> tuple[Placement | None, dict[str, Any]]:
        cfg = self.config
        attempts = 0

        for tentative in self.try_small_xy_support_adjustments(
            pallet=pallet,
            placement=placement,
            eps_mm=eps_mm,
        ):
            attempts += 1
            probe = tentative
            refine_settle_mm = 0.0
            if cfg.enable_settle():
                probe, refine_settle_mm = pallet.settle_placement(
                    probe,
                    eps_mm=eps_mm,
                    snap_grid=bool(cfg.settle_snap_grid),
                    grid_mm=cfg.grid_mm,
                    max_iter=cfg.settle_max_iter,
                    timeout_ms=cfg.settle_timeout_ms,
                )
            if not self._in_bin_bounds(pallet=pallet, placement=probe):
                continue
            if pallet._collides(probe, eps_mm=eps_mm):  # noqa: SLF001
                continue

            ratio = 1.0
            support_area = float(probe.length_mm * probe.width_mm)
            if cfg.enable_ratio():
                ratio, support_area = pallet.support_surface_ratio(probe, eps_mm=eps_mm)
                if ratio + 1e-9 < float(required_support):
                    continue

            com_supported = True
            overlaps = 0
            if cfg.enable_corners():
                com_supported, overlaps = pallet.com_support_info(probe, eps_mm=eps_mm)
                if not com_supported:
                    continue

            return probe, {
                "attempts": int(attempts),
                "support_ratio": float(ratio),
                "support_area_mm2": float(support_area),
                "com_supported": bool(com_supported),
                "supported_overlaps_count": int(overlaps),
                "refine_settle_mm": float(refine_settle_mm),
            }

        return None, {"attempts": int(attempts)}

    def refine_candidate_for_support_frontier(
        self,
        *,
        pallet: "PalletModel",
        placement: Placement,
        eps_mm: float,
        required_support: float,
        ratio_failed: bool,
        corners_failed: bool,
        support_ratio: float,
        com_supported: bool,
        overlaps: int,
        debug: dict[str, Any],
    ) -> PlacementControlResult | None:
        cfg = self.config
        if not bool(cfg.support_frontier_refine_enabled):
            return None
        if int(placement.z_mm) <= float(eps_mm):
            return None
        if not (bool(ratio_failed) or bool(corners_failed)):
            return None
        max_refines = max(0, int(cfg.support_frontier_max_refines_per_pallet))
        if max_refines > 0 and int(pallet.stats.support_frontier_refine_attempts) >= max_refines:
            debug["support_frontier_refine_skipped_budget"] = True
            return None

        margin = max(0.0, float(cfg.support_frontier_ratio_margin))
        if float(support_ratio) + margin + 1e-9 < float(required_support):
            return None
        if bool(corners_failed) and int(overlaps) <= 0:
            return None

        debug["support_frontier_refine_attempted"] = True
        debug["support_frontier_initial_support_ratio"] = float(support_ratio)
        debug["support_frontier_initial_com_supported"] = bool(com_supported)
        debug["support_frontier_initial_overlaps"] = int(overlaps)
        debug["support_frontier_rescue_failed_reasons"] = {
            "support_ratio": bool(ratio_failed),
            "corners": bool(corners_failed),
        }
        pallet.stats.support_frontier_refine_attempts += 1

        rescued_placement, rescue_debug = self.local_support_frontier_search(
            pallet=pallet,
            placement=placement,
            eps_mm=eps_mm,
            required_support=float(required_support),
        )
        debug["support_frontier_attempts"] = int(rescue_debug.get("attempts", 0) or 0)
        if rescued_placement is None:
            debug["support_frontier_rescued"] = False
            return None

        debug["support_frontier_rescued"] = True
        debug["support_frontier_refine_settle_mm"] = float(rescue_debug.get("refine_settle_mm", 0.0) or 0.0)
        debug["support_ratio"] = float(rescue_debug.get("support_ratio", support_ratio))
        debug["support_area_mm2"] = float(rescue_debug.get("support_area_mm2", 0.0))
        debug["com_supported"] = bool(rescue_debug.get("com_supported", True))
        debug["supported_overlaps_count"] = int(rescue_debug.get("supported_overlaps_count", 0))

        rescue_by_support = bool(ratio_failed)
        rescue_by_corners = bool(corners_failed)
        debug["support_frontier_rescue_reason_support"] = bool(rescue_by_support)
        debug["support_frontier_rescue_reason_corners"] = bool(rescue_by_corners)

        pallet.stats.support_frontier_candidates_rescued += 1
        if rescue_by_support:
            pallet.stats.support_frontier_rescued_support_ratio += 1
        if rescue_by_corners:
            pallet.stats.support_frontier_rescued_corner_support += 1

        return PlacementControlResult(
            feasible=True,
            placement=rescued_placement,
            score_delta=0.0,
            reason=None,
            debug=debug,
        )

    def evaluate(
        self,
        *,
        pallet: "PalletModel",
        box: Box,
        placement: Placement,
    ) -> PlacementControlResult:
        cfg = self.config
        eps = float(cfg.eps_mm)
        adjusted: Placement = placement
        debug: dict[str, Any] = {}

        # 0) SETTLE (si está habilitado)
        # settle_placement devuelve (Placement, settle_mm)
        if cfg.enable_settle():
            z_before = float(getattr(adjusted, "z_mm", 0.0) or 0.0)
            settled, settle_mm = pallet.settle_placement(
                adjusted,
                eps_mm=eps,
                snap_grid=bool(cfg.settle_snap_grid),
                grid_mm=cfg.grid_mm,
                max_iter=cfg.settle_max_iter,
                timeout_ms=cfg.settle_timeout_ms,
            )
            adjusted = settled
            z_after = float(getattr(adjusted, "z_mm", 0.0) or 0.0)

            debug["settle_mm"] = float(settle_mm)
            debug["settled_z_before_mm"] = float(z_before)
            debug["settled_z_after_mm"] = float(z_after)

            # Stats: usa lo que existe en PalletStats
            if float(settle_mm) > 0.0:
                pallet.stats.record_settle(float(settle_mm))

        ratio_failed = False
        corners_failed = False
        support_ratio = 1.0
        support_area = float(adjusted.length_mm * adjusted.width_mm)
        required_support = required_support_for_orientation(
            is_stand_hw=is_stand_hw_orientation(adjusted),
            min_support_ratio=float(cfg.min_support_ratio),
        )
        com_supported = True
        overlaps = 0

        # 1) SUPPORT RATIO (primero)
        if cfg.enable_ratio():
            pallet.stats.support_ratio_checks += 1
            support_ratio, support_area = pallet.support_surface_ratio(adjusted, eps_mm=eps)
            stand_hw_orientation = is_stand_hw_orientation(adjusted)
            required_support = required_support_for_orientation(
                is_stand_hw=stand_hw_orientation,
                min_support_ratio=float(cfg.min_support_ratio),
            )
            debug["support_ratio"] = float(support_ratio)
            debug["support_area_mm2"] = float(support_area)
            debug["required_support_ratio"] = float(required_support)
            if support_ratio + 1e-9 < float(required_support):
                pallet.stats.support_ratio_rejects += 1
                if stand_hw_orientation:
                    pallet.stats.stand_hw_rejected_support_total += 1
                ratio_failed = True

        # 2) CORNERS SUPPORT (después del ratio)
        if cfg.enable_corners():
            pallet.stats.corner_checks += 1
            com_supported, overlaps = pallet.com_support_info(adjusted, eps_mm=eps)
            corners_ok = pallet.corners_supported(adjusted, eps_mm=eps)
            debug["com_supported"] = bool(com_supported)
            debug["supported_overlaps_count"] = int(overlaps)
            debug["corners_supported"] = bool(corners_ok)
            if not com_supported:
                pallet.stats.corner_rejects += 1
                corners_failed = True

        rescue = self.refine_candidate_for_support_frontier(
            pallet=pallet,
            placement=adjusted,
            eps_mm=eps,
            required_support=float(required_support),
            ratio_failed=bool(ratio_failed),
            corners_failed=bool(corners_failed),
            support_ratio=float(support_ratio),
            com_supported=bool(com_supported),
            overlaps=int(overlaps),
            debug=debug,
        )
        if rescue is not None:
            return rescue

        if corners_failed:
            return PlacementControlResult(
                feasible=False,
                placement=adjusted,
                score_delta=0.0,
                reason="CORNER_SUPPORT",
                debug=debug,
            )

        if ratio_failed:
            return PlacementControlResult(
                feasible=False,
                placement=adjusted,
                score_delta=0.0,
                reason="SUPPORT_RATIO",
                debug=debug,
            )

        return PlacementControlResult(
            feasible=True,
            placement=adjusted,
            score_delta=0.0,
            reason=None,
            debug=debug,
        )


@dataclass(frozen=True)
class LoadBearPlacementControl:
    config: LoadBearConfig = LoadBearConfig()

    def evaluate(
        self,
        *,
        pallet: "PalletModel",
        box: Box,
        placement: Placement,
    ) -> PlacementControlResult:
        cfg = self.config
        if not cfg.heavy_bottom:
            return PlacementControlResult(feasible=True, placement=placement)

        ratio, capacity = pallet.loadbear_ratio(
            placement,
            loadbear_factor=cfg.loadbear_factor,
        )
        debug = {"loadbear_ratio": float(ratio), "support_capacity": float(capacity)}

        if ratio > cfg.max_overweight_ratio:
            return PlacementControlResult(
                feasible=False,
                placement=placement,
                score_delta=0.0,
                reason="LOADBEAR",
                debug=debug,
            )

        penalty = cfg.penalty_weight * max(0.0, ratio - 1.0)
        return PlacementControlResult(
            feasible=True,
            placement=placement,
            score_delta=-float(penalty),
            reason=None,
            debug=debug,
        )


@dataclass(frozen=True)
class BalancePlacementControl:
    config: BalanceConfig = BalanceConfig()

    def evaluate(
        self,
        *,
        pallet: "PalletModel",
        box: Box,
        placement: Placement,
    ) -> PlacementControlResult:
        weight = float(self.config.balance_weight)
        if weight <= 0:
            return PlacementControlResult(feasible=True, placement=placement)

        metrics = pallet.balance_metrics(extra_placements=[placement])
        penalty = (metrics.imbalance_ratio + metrics.com_offset_norm) * weight
        debug = {
            "balance_imbalance_ratio": float(metrics.imbalance_ratio),
            "balance_com_offset_norm": float(metrics.com_offset_norm),
        }
        return PlacementControlResult(
            feasible=True,
            placement=placement,
            score_delta=-float(penalty),
            reason=None,
            debug=debug,
        )


def build_control_stack(config: ControlConfig | None = None) -> ControlStack:
    cfg = config or ControlConfig()
    placement_controls: list[PlacementControl] = []

    if cfg.stability.mode != "off":
        placement_controls.append(StabilityPlacementControl(cfg.stability))

    if cfg.loadbear.heavy_bottom:
        placement_controls.append(LoadBearPlacementControl(cfg.loadbear))

    if cfg.balance.balance_weight != 0.0:
        placement_controls.append(BalancePlacementControl(cfg.balance))

    return ControlStack(
        manifest=DefaultManifestControl(),
        point=DefaultPointControl(),
        placement_controls=placement_controls,
    )
