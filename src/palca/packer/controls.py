from __future__ import annotations

from dataclasses import dataclass, field
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
class AccessibilityConfig:
    accessibility_delta_mm: int = 400


@dataclass(frozen=True)
class ControlConfig:
    stability: StabilityConfig = StabilityConfig()
    loadbear: LoadBearConfig = LoadBearConfig()
    balance: BalanceConfig = BalanceConfig()
    accessibility: AccessibilityConfig = field(default_factory=lambda: AccessibilityConfig(accessibility_delta_mm=0))


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

        # 1) SUPPORT RATIO (primero)
        if cfg.enable_ratio():
            pallet.stats.support_ratio_checks += 1
            ratio, support_area = pallet.support_surface_ratio(adjusted, eps_mm=eps)
            stand_hw_orientation = is_stand_hw_orientation(adjusted)
            required_support = required_support_for_orientation(
                is_stand_hw=stand_hw_orientation,
                min_support_ratio=float(cfg.min_support_ratio),
            )
            debug["support_ratio"] = float(ratio)
            debug["support_area_mm2"] = float(support_area)
            debug["required_support_ratio"] = float(required_support)
            if ratio + 1e-9 < float(required_support):
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


@dataclass(frozen=True)
class RobotAccessibilityControl:
    config: AccessibilityConfig = AccessibilityConfig()

    def evaluate(
        self,
        *,
        pallet: "PalletModel",
        box: Box,
        placement: Placement,
    ) -> PlacementControlResult:
        if self.config.accessibility_delta_mm == 0:
            return PlacementControlResult(feasible=True, placement=placement)
        for placed in (getattr(pallet, 'placements', None) or []):
            overlap_x = max(0, min(placed.x_mm + placed.length_mm, placement.x_mm + placement.length_mm) - max(placed.x_mm, placement.x_mm))
            overlap_y = max(0, min(placed.y_mm + placed.width_mm, placement.y_mm + placement.width_mm) - max(placed.y_mm, placement.y_mm))
            if overlap_x > 0 and overlap_y > 0:
                if placed.z_mm + placed.height_mm > placement.z_mm + self.config.accessibility_delta_mm:
                    return PlacementControlResult(feasible=False, placement=placement, reason="ROBOT_ACCESS")
        return PlacementControlResult(feasible=True, placement=placement)


def build_control_stack(config: ControlConfig | None = None) -> ControlStack:
    cfg = config or ControlConfig()
    placement_controls: list[PlacementControl] = []

    if cfg.stability.mode != "off":
        placement_controls.append(StabilityPlacementControl(cfg.stability))

    if cfg.loadbear.heavy_bottom:
        placement_controls.append(LoadBearPlacementControl(cfg.loadbear))

    if cfg.balance.balance_weight != 0.0:
        placement_controls.append(BalancePlacementControl(cfg.balance))

    if cfg.accessibility.accessibility_delta_mm > 0:
        placement_controls.append(RobotAccessibilityControl(cfg.accessibility))

    return ControlStack(
        manifest=DefaultManifestControl(),
        point=DefaultPointControl(),
        placement_controls=placement_controls,
    )
