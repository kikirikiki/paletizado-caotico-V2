from __future__ import annotations

from dataclasses import dataclass

from ..domain.box import Box
from ..domain.placement import Placement, PlacementPreview
from ..packer.scoring import packing_gain
from ..packer.zones import ZoneConfig, PALLET_ZONES

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..packer.pallet_model import PalletModel


def _height_under_footprint(
    placements: list[Placement],
    x_mm: int,
    y_mm: int,
    length_mm: int,
    width_mm: int,
) -> int:
    max_z = 0
    for p in placements:
        # Check XY overlap
        if (
            x_mm + length_mm > p.x_mm
            and p.x_mm + p.length_mm > x_mm
            and y_mm + width_mm > p.y_mm
            and p.y_mm + p.width_mm > y_mm
        ):
            top = p.z_mm + p.height_mm
            if top > max_z:
                max_z = top
    return max_z


@dataclass
class ZoneScheduler:
    zones: list[ZoneConfig]
    delta_max_mm: int = 400
    buffer_size: int = 15

    def compute_zone_heights(self, pallet: "PalletModel") -> dict[str, int]:
        heights: dict[str, int] = {zone.name: 0 for zone in self.zones}
        for p in pallet.placements:
            top = p.z_mm + p.height_mm
            for zone in self.zones:
                if (
                    p.x_mm < zone.x_max_mm
                    and p.x_mm + p.length_mm > zone.x_min_mm
                    and p.y_mm < zone.y_max_mm
                    and p.y_mm + p.width_mm > zone.y_min_mm
                ):
                    if top > heights[zone.name]:
                        heights[zone.name] = top
        return heights

    def select_target_zones(self, heights: dict[str, int]) -> list[ZoneConfig]:
        min_height = min(heights.values())
        threshold = min_height + self.delta_max_mm
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
        x_min = zone.x_min_mm
        x_max_box = zone.x_max_mm - length_mm
        y_min = zone.y_min_mm
        y_max_box = zone.y_max_mm - width_mm

        if x_max_box < x_min or y_max_box < y_min:
            return []

        seen: set[tuple[int, int]] = set()

        def _add(x: int, y: int) -> None:
            cx = min(max(x, x_min), x_max_box)
            cy = min(max(y, y_min), y_max_box)
            if x_min <= cx <= x_max_box and y_min <= cy <= y_max_box:
                seen.add((cx, cy))

        # Zone corners
        _add(zone.x_min_mm, zone.y_min_mm)
        _add(zone.x_max_mm - length_mm, zone.y_min_mm)
        _add(zone.x_min_mm, zone.y_max_mm - width_mm)
        _add(zone.x_max_mm - length_mm, zone.y_max_mm - width_mm)

        # Candidates from existing placements
        for p in pallet.placements:
            xs = [
                p.x_mm,
                p.x_mm + p.length_mm,
                p.x_mm - length_mm,
                p.x_mm + p.length_mm - length_mm,
            ]
            ys = [
                p.y_mm,
                p.y_mm + p.width_mm,
                p.y_mm - width_mm,
                p.y_mm + p.width_mm - width_mm,
            ]
            for x in xs:
                for y in ys:
                    _add(x, y)

        return sorted(seen)

    def try_place_in_zone(
        self,
        pallet: "PalletModel",
        box: Box,
        zone: ZoneConfig,
    ) -> PlacementPreview | None:
        feasible_candidates: list[tuple[int, int, int, Placement]] = []

        orientations: list[tuple[int, int, bool]] = [
            (int(box.length_mm), int(box.width_mm), False),
        ]
        if int(box.length_mm) != int(box.width_mm):
            orientations.append((int(box.width_mm), int(box.length_mm), True))

        for l_mm, w_mm, rot90 in orientations:
            if not zone.fits_box(l_mm, w_mm):
                continue

            candidates = self._zone_xy_candidates(pallet, l_mm, w_mm, zone)
            for x_mm, y_mm in candidates:
                z_mm = _height_under_footprint(
                    pallet.placements, x_mm, y_mm, l_mm, w_mm
                )
                if z_mm + box.height_mm > pallet.spec.max_height_mm:
                    continue

                placement = Placement(
                    x_mm=x_mm,
                    y_mm=y_mm,
                    z_mm=z_mm,
                    rot90=rot90,
                    layer_id=0,
                    length_mm=l_mm,
                    width_mm=w_mm,
                    height_mm=int(box.height_mm),
                    box_id=box.box_id,
                    weight_kg=box.effective_weight_kg(),
                    loadbear=box.loadbear,
                    priority=box.priority,
                )

                all_pass = True
                for control in pallet.controls.placement_controls:
                    result = control.evaluate(
                        pallet=pallet, box=box, placement=placement
                    )
                    if not result.feasible:
                        all_pass = False
                        break
                    placement = result.placement

                if all_pass:
                    feasible_candidates.append((z_mm, x_mm, y_mm, placement))

        if not feasible_candidates:
            return None

        winner = min(feasible_candidates, key=lambda t: (t[0], t[1], t[2]))
        w_placement = winner[3]
        height_after = winner[0] + int(box.height_mm)

        return PlacementPreview(
            feasible=True,
            placement=w_placement,
            packing_gain=packing_gain(
                w_placement.length_mm * w_placement.width_mm,
                pallet.spec.bin_area_mm2,
            ),
            fragmentation=0.0,
            infeasible_reason=None,
            debug={
                "zone": zone.name,
                "z_mm": winner[0],
                "x_mm": winner[1],
                "y_mm": winner[2],
                "height_after_mm": height_after,
            },
        )

    def step(
        self,
        pallet: "PalletModel",
        buffer: list,
    ) -> tuple | None:
        heights = self.compute_zone_heights(pallet)
        eligible = self.select_target_zones(heights)

        for zone in eligible:
            for box in buffer:
                if (
                    not zone.fits_box(int(box.length_mm), int(box.width_mm))
                    and not zone.fits_box(int(box.width_mm), int(box.length_mm))
                ):
                    continue
                preview = self.try_place_in_zone(pallet, box, zone)
                if preview is not None:
                    return (box, preview)

        return None
