from __future__ import annotations

from dataclasses import dataclass

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


@dataclass(frozen=True)
class PreviewSpec:
    z_mm: int
    height_mm: int
    packing_gain: float = 0.0
    orientation_family: str | None = None
    orientation_name: str | None = None


class FakePallet:
    def __init__(self, previews_by_box: dict[int, PreviewSpec]) -> None:
        self._previews_by_box = dict(previews_by_box)
        self.placements: list[Placement] = []
        self.bin_area_mm2 = 10_000

    def preview_place(self, box: Box) -> PlacementPreview:
        spec = self._previews_by_box.get(int(box.box_id))
        if spec is None:
            return PlacementPreview(
                feasible=False,
                placement=None,
                packing_gain=0.0,
                fragmentation=0.0,
                infeasible_reason="NO_SPACE",
            )
        placement = Placement(
            x_mm=0,
            y_mm=0,
            z_mm=int(spec.z_mm),
            rot90=False,
            layer_id=max(0, int(spec.z_mm // 100)),
            length_mm=40,
            width_mm=40,
            height_mm=int(spec.height_mm),
            box_id=box.box_id,
            orientation_family=spec.orientation_family,
            orientation_name=spec.orientation_name,
        )
        return PlacementPreview(
            feasible=True,
            placement=placement,
            packing_gain=float(spec.packing_gain),
            fragmentation=0.0,
            height_after_mm=int(spec.z_mm) + int(spec.height_mm),
        )

    def commit_place(self, preview: PlacementPreview) -> Placement:
        assert preview.placement is not None
        self.placements.append(preview.placement)
        return preview.placement

    def current_height_mm(self) -> int:
        if not self.placements:
            return 0
        return max(int(p.z_mm) + int(p.height_mm) for p in self.placements)


def _box(box_id: int) -> Box:
    return Box(
        box_id=int(box_id),
        length_mm=40,
        width_mm=40,
        height_mm=40,
        timestamp=0.0,
        destination=1,
    )


def _sim_state(*, boxes: list[Box], pallet: FakePallet) -> SchedulerSimState:
    return SchedulerSimState(
        now=0.0,
        ramps={1: list(boxes)},
        pallets={1: pallet},
        pallet_blocked=set(),
        remaining_total=len(boxes),
    )


def test_no_layer_reentry_rejects_going_back_to_lower_layer() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(z_mm=200, height_mm=40, packing_gain=1.0),
            2: PreviewSpec(z_mm=0, height_mm=40, packing_gain=1.0),
        }
    )
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=1, enforce_no_layer_reentry=True))

    first = scheduler.choose_action(_sim_state(boxes=[_box(1)], pallet=pallet))
    assert first is not None
    pallet.commit_place(first.preview)

    second = scheduler.choose_action(_sim_state(boxes=[_box(2)], pallet=pallet))
    assert second is None
    assert int(scheduler.no_layer_reentry_rejections_total) == 1


def test_no_layer_reentry_allows_placements_in_current_highest_layer() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(z_mm=200, height_mm=40, packing_gain=1.0),
            2: PreviewSpec(z_mm=0, height_mm=40, packing_gain=10.0),
            3: PreviewSpec(z_mm=200, height_mm=40, packing_gain=1.0),
        }
    )
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=2, enforce_no_layer_reentry=True))

    first = scheduler.choose_action(_sim_state(boxes=[_box(1)], pallet=pallet))
    assert first is not None
    pallet.commit_place(first.preview)

    second = scheduler.choose_action(_sim_state(boxes=[_box(2), _box(3)], pallet=pallet))
    assert second is not None
    assert int(second.preview.placement.z_mm) == 200
    assert int(scheduler.no_layer_reentry_rejections_total) == 1


def test_no_layer_reentry_uses_base_z_not_top_height_for_layer() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(z_mm=200, height_mm=40, packing_gain=1.0, orientation_family="planar"),
            2: PreviewSpec(
                z_mm=0,
                height_mm=1500,
                packing_gain=10.0,
                orientation_family="stand_hw",
                orientation_name="stand_hw_lh",
            ),
        }
    )
    scheduler = SchedulerV1(SchedulerConfig(lookahead_k=1, enforce_no_layer_reentry=True))

    first = scheduler.choose_action(_sim_state(boxes=[_box(1)], pallet=pallet))
    assert first is not None
    pallet.commit_place(first.preview)

    second = scheduler.choose_action(_sim_state(boxes=[_box(2)], pallet=pallet))
    assert second is None
    assert int(scheduler.no_layer_reentry_rejections_total) == 1
