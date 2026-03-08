from __future__ import annotations

from dataclasses import dataclass

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


@dataclass(frozen=True)
class PreviewSpec:
    z_mm: int
    x_mm: int
    y_mm: int
    length_mm: int
    width_mm: int
    height_mm: int
    packing_gain: float = 0.0
    fragmentation: float = 0.0
    orientation_family: str | None = None
    orientation_name: str | None = None


class FakePallet:
    def __init__(self, previews_by_box: dict[int, PreviewSpec], *, bin_area_mm2: int = 12_000) -> None:
        self._previews_by_box = dict(previews_by_box)
        self.placements: list[Placement] = []
        self.bin_area_mm2 = int(bin_area_mm2)

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
            x_mm=int(spec.x_mm),
            y_mm=int(spec.y_mm),
            z_mm=int(spec.z_mm),
            rot90=False,
            layer_id=0 if int(spec.z_mm) == 0 else 1,
            length_mm=int(spec.length_mm),
            width_mm=int(spec.width_mm),
            height_mm=int(spec.height_mm),
            box_id=box.box_id,
            orientation_family=spec.orientation_family,
            orientation_name=spec.orientation_name,
        )
        return PlacementPreview(
            feasible=True,
            placement=placement,
            packing_gain=float(spec.packing_gain),
            fragmentation=float(spec.fragmentation),
            height_after_mm=int(spec.z_mm) + int(spec.height_mm),
            infeasible_reason=None,
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
        length_mm=1,
        width_mm=1,
        height_mm=1,
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


def test_hard_floor_phase_never_chooses_stacking_while_floor_exists() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(z_mm=120, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=9.0),
            2: PreviewSpec(z_mm=0, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=0.1),
            3: PreviewSpec(z_mm=0, x_mm=40, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=0.1),
        }
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=3,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
        )
    )

    plan1 = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan1 is not None
    assert int(plan1.preview.placement.z_mm) == 0
    pallet.commit_place(plan1.preview)

    plan2 = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(3)], pallet=pallet))
    assert plan2 is not None
    assert int(plan2.preview.placement.z_mm) == 0


def test_hard_floor_phase_exits_when_no_floor_candidates_and_falls_back_to_normal() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(z_mm=120, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=8.0),
            2: PreviewSpec(z_mm=140, x_mm=40, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=6.0),
        }
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=5,
            hard_floor_phase_min_base_candidates=1,
        )
    )

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan is not None
    assert int(plan.preview.placement.z_mm) > 0
    assert int(scheduler.hard_floor_phase_exit_no_floor_total) == 1


def test_hard_floor_phase_can_choose_stand_hw_on_floor_when_base_improves() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(
                z_mm=0,
                x_mm=0,
                y_mm=0,
                length_mm=30,
                width_mm=30,
                height_mm=50,
                packing_gain=9.0,
                orientation_family="planar",
                orientation_name="planar_lw",
            ),
            2: PreviewSpec(
                z_mm=0,
                x_mm=0,
                y_mm=0,
                length_mm=80,
                width_mm=80,
                height_mm=30,
                packing_gain=0.1,
                orientation_family="stand_hw",
                orientation_name="stand_hw_lh",
            ),
        }
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=3,
            hard_floor_phase_min_base_candidates=1,
        )
    )

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan is not None
    assert str(plan.preview.placement.orientation_family) == "stand_hw"
    assert int(scheduler.hard_floor_phase_stand_hw_chosen_total) == 1


def test_hard_floor_phase_disabled_mode_keeps_existing_behavior() -> None:
    pallet_a = FakePallet(
        {
            1: PreviewSpec(z_mm=120, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=9.0),
            2: PreviewSpec(z_mm=0, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=0.1),
        }
    )
    pallet_b = FakePallet(
        {
            1: PreviewSpec(z_mm=120, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=9.0),
            2: PreviewSpec(z_mm=0, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=0.1),
        }
    )

    baseline = SchedulerV1(SchedulerConfig(lookahead_k=2))
    disabled = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=0,
            hard_floor_phase_min_base_candidates=1,
        )
    )

    baseline_plan = baseline.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet_a))
    disabled_plan = disabled.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet_b))

    assert baseline_plan is not None and disabled_plan is not None
    assert int(baseline_plan.box_id) == int(disabled_plan.box_id)
    assert int(baseline_plan.preview.placement.z_mm) == int(disabled_plan.preview.placement.z_mm)


def test_hard_floor_phase_applies_same_root_logic_with_micro_planner_depth0() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(z_mm=120, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=9.0),
            2: PreviewSpec(z_mm=0, x_mm=0, y_mm=0, length_mm=50, width_mm=40, height_mm=40, packing_gain=0.1),
        }
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            micro_plan_enabled=True,
            micro_plan_depth=3,
            micro_plan_width=6,
            micro_plan_topk_per_step=6,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
        )
    )

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan is not None
    assert int(plan.preview.placement.z_mm) == 0
    assert int(scheduler.hard_floor_phase_chosen_total) == 1


def test_hard_floor_phase_kpis_are_exposed() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        hard_floor_phase_end_step=8,
        hard_floor_phase_min_base_candidates=1,
        hard_floor_phase_lookahead_items=9,
    )
    policy._scheduler.hard_floor_phase_active_total = 3
    policy._scheduler.hard_floor_phase_floor_candidates_seen_total = 12
    policy._scheduler.hard_floor_phase_chosen_total = 2
    policy._scheduler.hard_floor_phase_stand_hw_chosen_total = 1
    policy._scheduler.hard_floor_phase_exit_no_floor_total = 1
    policy._scheduler.hard_floor_phase_exit_end_step_total = 1
    policy._scheduler.hard_floor_phase_score_sum = 5.0

    kpis = policy.collect_kpis()

    assert int(kpis["hard_floor_phase_active_total"]) == 3
    assert int(kpis["hard_floor_phase_floor_candidates_seen_total"]) == 12
    assert int(kpis["hard_floor_phase_chosen_total"]) == 2
    assert int(kpis["hard_floor_phase_stand_hw_chosen_total"]) == 1
    assert int(kpis["hard_floor_phase_exit_no_floor_total"]) == 1
    assert int(kpis["hard_floor_phase_exit_end_step_total"]) == 1
    assert float(kpis["hard_floor_phase_score_mean"]) == 2.5
