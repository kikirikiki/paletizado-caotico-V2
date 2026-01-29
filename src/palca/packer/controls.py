from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, Sequence, TYPE_CHECKING

from ..domain.box import Box
from ..domain.placement import Placement
from .maxrects2d import MaxRectsCandidate
from .layer import LayerState

if TYPE_CHECKING:
    from .pallet_model import PalletModel


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
        cand = layer.bin.find_candidate(length_mm, width_mm)
        if cand is None:
            return []
        return [cand]


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
        adjusted = placement
        debug: dict[str, Any] = {}

        if cfg.enable_settle():
            adjusted, settle_mm = pallet.settle_placement(
                placement,
                eps_mm=eps,
                snap_grid=cfg.settle_snap_grid,
                grid_mm=cfg.grid_mm,
            )
            debug["settle_mm"] = float(settle_mm)

        if cfg.enable_corners():
            pallet.stats.corner_checks += 1
            corners_ok = pallet.corners_supported(adjusted, eps_mm=eps)
            debug["corners_supported"] = bool(corners_ok)
            if not corners_ok:
                pallet.stats.corner_rejects += 1
                return PlacementControlResult(
                    feasible=False,
                    placement=adjusted,
                    score_delta=0.0,
                    reason="CORNER_SUPPORT",
                    debug=debug,
                )

        if cfg.enable_ratio():
            pallet.stats.support_ratio_checks += 1
            ratio, support_area = pallet.support_surface_ratio(adjusted, eps_mm=eps)
            debug["support_ratio"] = float(ratio)
            debug["support_area_mm2"] = float(support_area)
            if ratio + 1e-9 < float(cfg.min_support_ratio):
                pallet.stats.support_ratio_rejects += 1
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
