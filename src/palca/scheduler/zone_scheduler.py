from __future__ import annotations

from dataclasses import dataclass, replace

from ..domain.box import Box
from ..domain.placement import PlacementPreview
from ..packer.controls import ControlStack
from ..packer.zones import ZoneConfig, ZonePointControl, PALLET_ZONES

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..packer.pallet_model import PalletModel


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

    def try_place_in_zone(
        self,
        pallet: "PalletModel",
        box: Box,
        zone: ZoneConfig,
        original_controls: ControlStack,
    ) -> PlacementPreview | None:
        zone_controls = replace(original_controls, point=ZonePointControl(zone))
        pallet.controls = zone_controls
        try:
            preview = pallet.preview_place(box)
        finally:
            pallet.controls = original_controls

        if not preview.feasible or preview.placement is None:
            return None

        p = preview.placement
        if not zone.contains_placement(p.x_mm, p.y_mm, p.length_mm, p.width_mm):
            return None

        return preview

    def step(
        self,
        pallet: "PalletModel",
        buffer: list[Box],
    ) -> tuple[Box, PlacementPreview] | None:
        original_controls = pallet.controls
        heights = self.compute_zone_heights(pallet)
        eligible_zones = self.select_target_zones(heights)

        for zone in eligible_zones:
            for box in buffer[: self.buffer_size]:
                if not zone.fits_box(box.length_mm, box.width_mm):
                    continue
                result = self.try_place_in_zone(pallet, box, zone, original_controls)
                if result is not None:
                    return (box, result)

        return None
