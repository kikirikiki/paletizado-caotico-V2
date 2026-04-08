from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..domain.box import Box
from ..domain.placement import Placement, PlacementPreview
from ..packer.zones import ZoneConfig, PALLET_ZONES
from ..packer.scoring import packing_gain

if TYPE_CHECKING:
    from ..packer.pallet_model import PalletModel


@dataclass
class GuidedHeightfieldScheduler:
    """Zone-balanced scheduler using full MaxRects 2D placement with half-height guidance.

    The pallet is notionally split into left (L) and right (R) halves along X.
    No spatial restriction is applied to placement search — the full pallet is
    always searched.  When one half is significantly taller than the other, a
    score bonus is added to candidates whose centre-x falls in the shorter half,
    nudging the packer toward balance without hard-blocking cross-boundary boxes.
    """

    split_x_mm: int = 600
    delta_max_mm: int = 400
    balance_bonus: float = 0.15
    buffer_size: int = 15

    def compute_half_heights(self, pallet: "PalletModel") -> dict[str, int]:
        """Return the current max height for each half ("L" and "R").

        Height of a half = max(p.z_mm + p.height_mm) for all placements whose
        centre-x falls in that half.  Empty halves return 0.
        """
        heights: dict[str, int] = {"L": 0, "R": 0}
        for p in pallet.placements:
            cx = p.x_mm + p.length_mm / 2
            key = "L" if cx < self.split_x_mm else "R"
            top = p.z_mm + p.height_mm
            if top > heights[key]:
                heights[key] = top
        return heights

    def placement_half(self, placement: Placement) -> str:
        """Return "L" or "R" based on where the placement centre-x falls."""
        cx = placement.x_mm + placement.length_mm / 2
        return "L" if cx < self.split_x_mm else "R"

    def step(
        self,
        pallet: "PalletModel",
        buffer: list[Box],
    ) -> tuple[Box, PlacementPreview] | None:
        """Select one box from *buffer* and return (box, preview).

        Full-pallet MaxRects search — no region restriction.
        A balance_bonus is added to the score when the placement falls in the
        shorter half and the height delta exceeds delta_max_mm / 2.
        Returns None when no feasible placement exists.
        """
        heights = self.compute_half_heights(pallet)
        h_L, h_R = heights["L"], heights["R"]
        delta = h_L - h_R  # positive = L taller, negative = R taller

        # Preferred half: the LOWER one when significantly unbalanced
        if abs(delta) > self.delta_max_mm / 2:
            preferred: str | None = "L" if delta < 0 else "R"
        else:
            preferred = None

        best_score: float | None = None
        best_box: Box | None = None
        best_preview: PlacementPreview | None = None

        for box in buffer:
            preview = pallet.preview_place(box)
            if not preview.feasible or preview.placement is None:
                continue

            base_score = (
                preview.packing_gain
                - preview.fragmentation
                + preview.score_adjustment
            )

            # Add bonus if placement falls in the preferred (lower) half
            half = self.placement_half(preview.placement)
            bonus = self.balance_bonus if (preferred is not None and half == preferred) else 0.0

            total_score = base_score + bonus

            if best_score is None or total_score > best_score:
                best_score = total_score
                best_box = box
                best_preview = preview

        if best_box is None:
            return None
        return (best_box, best_preview)  # type: ignore[return-value]


def _height_under_footprint(
    placements: list[Placement],
    x_mm: int,
    y_mm: int,
    length_mm: int,
    width_mm: int,
) -> int:
    """Return max top-z of any placement whose footprint overlaps the given rectangle."""
    x1 = x_mm + length_mm
    y1 = y_mm + width_mm
    max_top = 0
    for p in placements:
        px1 = p.x_mm + p.length_mm
        py1 = p.y_mm + p.width_mm
        if x1 > p.x_mm and px1 > x_mm and y1 > p.y_mm and py1 > y_mm:
            top = p.z_mm + p.height_mm
            if top > max_top:
                max_top = top
    return max_top


@dataclass
class ZoneScheduler:
    zones: list[ZoneConfig] = field(default_factory=lambda: list(PALLET_ZONES))
    delta_max_mm: int = 400
    buffer_size: int = 15

    def compute_zone_heights(self, pallet: "PalletModel") -> dict[str, int]:
        """For each zone, return max top-z of placements intersecting the zone footprint."""
        heights: dict[str, int] = {z.name: 0 for z in self.zones}
        for p in pallet.placements:
            px1 = p.x_mm + p.length_mm
            py1 = p.y_mm + p.width_mm
            for z in self.zones:
                # Strict intersection (not just touching)
                if p.x_mm < z.x_max_mm and px1 > z.x_min_mm and p.y_mm < z.y_max_mm and py1 > z.y_min_mm:
                    top = p.z_mm + p.height_mm
                    if top > heights[z.name]:
                        heights[z.name] = top
        return heights

    def select_target_zones(self, heights: dict[str, int]) -> list[ZoneConfig]:
        """Return zones eligible for next placement, sorted by height ascending.

        Eligible = height <= min_height + delta_max_mm.
        """
        min_h = min(heights.values())
        threshold = min_h + self.delta_max_mm
        eligible = [z for z in self.zones if heights[z.name] <= threshold]
        eligible.sort(key=lambda z: heights[z.name])
        return eligible

    def _zone_xy_candidates(
        self,
        pallet: "PalletModel",
        length_mm: int,
        width_mm: int,
        zone: ZoneConfig,
    ) -> list[tuple[int, int]]:
        """Generate candidate (x_mm, y_mm) positions for a box inside zone bounds."""
        x_min = zone.x_min_mm
        x_max = zone.x_max_mm - length_mm
        y_min = zone.y_min_mm
        y_max = zone.y_max_mm - width_mm

        if x_max < x_min or y_max < y_min:
            return []

        points: set[tuple[int, int]] = set()

        def _add(x: int, y: int) -> None:
            cx = min(max(x, x_min), x_max)
            cy = min(max(y, y_min), y_max)
            if x_min <= cx <= x_max and y_min <= cy <= y_max:
                points.add((cx, cy))

        # Zone corners
        _add(x_min, y_min)
        _add(x_max, y_min)
        _add(x_min, y_max)
        _add(x_max, y_max)

        # Adjacency candidates from existing placements
        for p in pallet.placements:
            for px in (p.x_mm, p.x_mm + p.length_mm,
                       p.x_mm - length_mm, p.x_mm + p.length_mm - length_mm):
                for py in (p.y_mm, p.y_mm + p.width_mm,
                           p.y_mm - width_mm, p.y_mm + p.width_mm - width_mm):
                    _add(px, py)

        return sorted(points)

    def try_place_in_zone(
        self,
        pallet: "PalletModel",
        box: Box,
        zone: ZoneConfig,
    ) -> PlacementPreview | None:
        """Try to place box in zone. Returns feasible PlacementPreview or None."""
        l = int(box.length_mm)
        w = int(box.width_mm)
        h = int(box.height_mm)
        max_height = int(pallet.spec.max_height_mm)
        bin_area = int(pallet.spec.bin_area_mm2)

        orientations = [(l, w, False)]
        if l != w:
            orientations.append((w, l, True))

        feasible_candidates: list[tuple[int, int, int, Placement]] = []

        for l_mm, w_mm, rot90 in orientations:
            if not zone.fits_box(l_mm, w_mm):
                continue

            candidates = self._zone_xy_candidates(pallet, l_mm, w_mm, zone)

            for x_mm, y_mm in candidates:
                z_mm = _height_under_footprint(
                    pallet.placements, x_mm, y_mm, l_mm, w_mm
                )
                if z_mm + h > max_height:
                    continue

                placement = Placement(
                    x_mm=x_mm,
                    y_mm=y_mm,
                    z_mm=z_mm,
                    rot90=rot90,
                    layer_id=0,
                    length_mm=l_mm,
                    width_mm=w_mm,
                    height_mm=h,
                    box_id=box.box_id,
                    weight_kg=box.effective_weight_kg(),
                    loadbear=box.loadbear,
                    priority=box.priority,
                    orientation_name="WLH" if rot90 else "LWH",
                    orientation_family="planar",
                )

                feasible = True
                for control in pallet.controls.placement_controls:
                    result = control.evaluate(
                        pallet=pallet,
                        box=box,
                        placement=placement,
                    )
                    if not result.feasible:
                        feasible = False
                        break
                    placement = result.placement

                if feasible:
                    feasible_candidates.append((z_mm, x_mm, y_mm, placement))

        if not feasible_candidates:
            return None

        # Pick lowest z, then leftmost x, then bottom-most y
        winner = min(feasible_candidates, key=lambda c: (c[0], c[1], c[2]))
        z_mm, x_mm, y_mm, best_placement = winner

        return PlacementPreview(
            feasible=True,
            placement=best_placement,
            packing_gain=packing_gain(
                best_placement.length_mm * best_placement.width_mm,
                bin_area,
            ),
            fragmentation=0.0,
            height_after_mm=z_mm + h,
            infeasible_reason=None,
            debug={
                "zone": zone.name,
                "z_mm": z_mm,
                "x_mm": x_mm,
                "y_mm": y_mm,
            },
        )

    def step(
        self,
        pallet: "PalletModel",
        buffer: list[Box],
    ) -> tuple[Box, PlacementPreview] | None:
        """One scheduling step.

        Returns (box, preview) to commit, or None to signal pallet closure.
        """
        heights = self.compute_zone_heights(pallet)
        eligible_zones = self.select_target_zones(heights)

        for zone in eligible_zones:
            for box in buffer:
                l, w = int(box.length_mm), int(box.width_mm)
                if not zone.fits_box(l, w) and not zone.fits_box(w, l):
                    continue
                preview = self.try_place_in_zone(pallet, box, zone)
                if preview is not None:
                    return (box, preview)

        return None
