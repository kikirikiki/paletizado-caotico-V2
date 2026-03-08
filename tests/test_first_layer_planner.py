from __future__ import annotations

from dataclasses import replace

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


class ScriptedPallet:
    def __init__(
        self,
        previews_by_box_id: dict[int, PlacementPreview],
        *,
        placements: list[Placement] | None = None,
        bin_area_mm2: int = 10_000,
    ) -> None:
        self._previews_by_box_id = dict(previews_by_box_id)
        self.placements = list(placements or [])
        self.bin_area_mm2 = int(bin_area_mm2)

    def set_previews(self, previews_by_box_id: dict[int, PlacementPreview]) -> None:
        self._previews_by_box_id = dict(previews_by_box_id)

    def preview_place(self, box: Box) -> PlacementPreview:
        preview = self._previews_by_box_id[int(box.box_id)]
        return replace(preview)

    def commit_place(self, preview: PlacementPreview) -> Placement:
        placement = preview.placement
        assert placement is not None
        self.placements.append(placement)
        return placement

    def current_height_mm(self) -> int:
        if not self.placements:
            return 0
        return max(int(p.z_mm) + int(p.height_mm) for p in self.placements)


def _box(box_id: int) -> Box:
    return Box(
        box_id=int(box_id),
        length_mm=40,
        width_mm=20,
        height_mm=20,
        timestamp=0.0,
        destination=1,
    )


def _preview(
    *,
    box_id: int,
    x_mm: int,
    y_mm: int,
    z_mm: int,
    length_mm: int,
    width_mm: int,
    height_mm: int = 20,
    packing_gain: float = 0.0,
    orientation_family: str = "planar",
    orientation_name: str = "planar_lwh",
) -> PlacementPreview:
    return PlacementPreview(
        feasible=True,
        placement=Placement(
            x_mm=int(x_mm),
            y_mm=int(y_mm),
            z_mm=int(z_mm),
            rot90=False,
            layer_id=0 if int(z_mm) == 0 else 1,
            length_mm=int(length_mm),
            width_mm=int(width_mm),
            height_mm=int(height_mm),
            box_id=int(box_id),
            orientation_family=str(orientation_family),
            orientation_name=str(orientation_name),
        ),
        packing_gain=float(packing_gain),
        fragmentation=0.0,
        height_after_mm=int(z_mm) + int(height_mm),
    )


def test_first_layer_planner_enforces_floor_then_exits_when_floor_exhausted() -> None:
    pallet = ScriptedPallet(
        {
            1: _preview(box_id=1, x_mm=0, y_mm=0, z_mm=100, length_mm=40, width_mm=20, packing_gain=1.2),
            2: _preview(box_id=2, x_mm=50, y_mm=0, z_mm=0, length_mm=40, width_mm=20, packing_gain=0.2),
        }
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            first_layer_planner_end_step=4,
            first_layer_planner_lookahead_items=8,
        )
    )

    state1 = SchedulerSimState(now=0.0, ramps={1: [_box(1), _box(2)]}, pallets={1: pallet}, pallet_blocked=set())
    plan1 = scheduler.choose_action(state1)
    assert plan1 is not None
    assert int(plan1.box_id) == 2
    assert int(plan1.preview.placement.z_mm) == 0

    pallet.commit_place(plan1.preview)
    pallet.set_previews(
        {
            3: _preview(box_id=3, x_mm=0, y_mm=0, z_mm=140, length_mm=40, width_mm=20, packing_gain=0.9),
        }
    )
    state2 = SchedulerSimState(now=0.0, ramps={1: [_box(3)]}, pallets={1: pallet}, pallet_blocked=set())
    plan2 = scheduler.choose_action(state2)
    assert plan2 is not None
    assert int(plan2.box_id) == 3
    assert int(plan2.preview.placement.z_mm) > 0
    assert scheduler.first_layer_planner_exit_no_floor_total == 1


def test_first_layer_planner_can_choose_stand_hw_for_better_base_layout() -> None:
    existing_floor = [
        Placement(
            x_mm=0,
            y_mm=0,
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=20,
            width_mm=10,
            height_mm=20,
            box_id=901,
            orientation_family="planar",
        ),
        Placement(
            x_mm=0,
            y_mm=10,
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=20,
            width_mm=10,
            height_mm=20,
            box_id=902,
            orientation_family="planar",
        ),
        Placement(
            x_mm=0,
            y_mm=20,
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=20,
            width_mm=10,
            height_mm=20,
            box_id=903,
            orientation_family="planar",
        ),
        Placement(
            x_mm=0,
            y_mm=30,
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=20,
            width_mm=10,
            height_mm=20,
            box_id=904,
            orientation_family="planar",
        ),
    ]
    pallet = ScriptedPallet(
        {
            10: _preview(
                box_id=10,
                x_mm=0,
                y_mm=40,
                z_mm=0,
                length_mm=20,
                width_mm=10,
                packing_gain=0.9,
                orientation_family="planar",
            ),
            11: _preview(
                box_id=11,
                x_mm=50,
                y_mm=0,
                z_mm=0,
                length_mm=30,
                width_mm=20,
                packing_gain=0.2,
                orientation_family="stand_hw",
                orientation_name="stand_hw_lwh",
            ),
        },
        placements=existing_floor,
    )
    baseline_scheduler = SchedulerV1(SchedulerConfig(lookahead_k=2, spatial_xy_bin_mm=50))
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            spatial_xy_bin_mm=50,
            first_layer_planner_end_step=6,
            first_layer_planner_lookahead_items=8,
        )
    )
    state = SchedulerSimState(now=0.0, ramps={1: [_box(10), _box(11)]}, pallets={1: pallet}, pallet_blocked=set())
    baseline_plan = baseline_scheduler.choose_action(state)
    assert baseline_plan is not None
    assert int(baseline_plan.box_id) == 10
    plan = scheduler.choose_action(state)
    assert plan is not None
    assert int(plan.box_id) == 11
    assert str(plan.preview.placement.orientation_family) == "stand_hw"
    assert scheduler.first_layer_planner_stand_hw_chosen_total == 1


def test_first_layer_planner_disabled_keeps_current_behavior() -> None:
    pallet_a = ScriptedPallet(
        {
            1: _preview(box_id=1, x_mm=0, y_mm=0, z_mm=120, length_mm=40, width_mm=20, packing_gain=1.0),
            2: _preview(box_id=2, x_mm=50, y_mm=0, z_mm=0, length_mm=40, width_mm=20, packing_gain=0.1),
        }
    )
    pallet_b = ScriptedPallet(
        {
            1: _preview(box_id=1, x_mm=0, y_mm=0, z_mm=120, length_mm=40, width_mm=20, packing_gain=1.0),
            2: _preview(box_id=2, x_mm=50, y_mm=0, z_mm=0, length_mm=40, width_mm=20, packing_gain=0.1),
        }
    )
    base = SchedulerV1(SchedulerConfig(lookahead_k=2))
    disabled = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            first_layer_planner_end_step=0,
            first_layer_planner_lookahead_items=8,
        )
    )

    state_a = SchedulerSimState(now=0.0, ramps={1: [_box(1), _box(2)]}, pallets={1: pallet_a}, pallet_blocked=set())
    state_b = SchedulerSimState(now=0.0, ramps={1: [_box(1), _box(2)]}, pallets={1: pallet_b}, pallet_blocked=set())
    base_plan = base.choose_action(state_a)
    disabled_plan = disabled.choose_action(state_b)
    assert base_plan is not None and disabled_plan is not None
    assert int(base_plan.box_id) == int(disabled_plan.box_id) == 1
    assert disabled.first_layer_planner_active_total == 0
    assert disabled.first_layer_planner_chosen_total == 0


def test_first_layer_planner_applies_in_micro_root_depth0() -> None:
    pallet = ScriptedPallet(
        {
            1: _preview(box_id=1, x_mm=0, y_mm=0, z_mm=200, length_mm=40, width_mm=20, packing_gain=1.1),
            2: _preview(box_id=2, x_mm=50, y_mm=0, z_mm=0, length_mm=40, width_mm=20, packing_gain=0.1),
        }
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=True,
            micro_plan_depth=1,
            micro_plan_width=8,
            micro_plan_topk_per_step=8,
            first_layer_planner_end_step=5,
            first_layer_planner_lookahead_items=8,
        )
    )
    state = SchedulerSimState(now=0.0, ramps={1: [_box(1), _box(2)]}, pallets={1: pallet}, pallet_blocked=set())
    plan = scheduler.choose_action(state)
    assert plan is not None
    assert int(plan.box_id) == 2
    assert int(plan.preview.placement.z_mm) == 0


def test_first_layer_planner_kpis_are_exposed() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        first_layer_planner_end_step=8,
        first_layer_planner_lookahead_items=12,
    )
    scheduler = policy._scheduler  # noqa: SLF001
    scheduler.first_layer_planner_active_total = 4
    scheduler.first_layer_planner_floor_candidates_seen_total = 23
    scheduler.first_layer_planner_chosen_total = 3
    scheduler.first_layer_planner_stand_hw_chosen_total = 1
    scheduler.first_layer_planner_exit_no_floor_total = 2
    scheduler.first_layer_planner_score_sum = 7.5
    scheduler.first_layer_planner_score_count = 3

    kpis = policy.collect_kpis()
    assert int(kpis.get("first_layer_planner_active_total", 0)) == 4
    assert int(kpis.get("first_layer_planner_floor_candidates_seen_total", 0)) == 23
    assert int(kpis.get("first_layer_planner_chosen_total", 0)) == 3
    assert int(kpis.get("first_layer_planner_stand_hw_chosen_total", 0)) == 1
    assert int(kpis.get("first_layer_planner_exit_no_floor_total", 0)) == 2
    assert float(kpis.get("first_layer_planner_score_mean", 0.0)) == 2.5
