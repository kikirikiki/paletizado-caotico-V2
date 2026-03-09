from __future__ import annotations

from dataclasses import dataclass
from types import MethodType

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


def _placement(
    *,
    box_id: int,
    z_mm: int,
    x_mm: int,
    y_mm: int,
    length_mm: int,
    width_mm: int,
    height_mm: int,
    orientation_family: str | None = None,
    orientation_name: str | None = None,
) -> Placement:
    return Placement(
        x_mm=int(x_mm),
        y_mm=int(y_mm),
        z_mm=int(z_mm),
        rot90=False,
        layer_id=0 if int(z_mm) == 0 else 1,
        length_mm=int(length_mm),
        width_mm=int(width_mm),
        height_mm=int(height_mm),
        box_id=int(box_id),
        orientation_family=orientation_family,
        orientation_name=orientation_name,
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


def test_hard_floor_phase_stand_mix_bonus_can_choose_stand_hw_on_floor() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(
                z_mm=0,
                x_mm=60,
                y_mm=0,
                length_mm=60,
                width_mm=40,
                height_mm=50,
                packing_gain=3.0,
                orientation_family="planar",
                orientation_name="planar_lw",
            ),
            2: PreviewSpec(
                z_mm=0,
                x_mm=0,
                y_mm=40,
                length_mm=40,
                width_mm=60,
                height_mm=30,
                packing_gain=3.0,
                orientation_family="stand_hw",
                orientation_name="stand_hw_lh",
            ),
        }
    )
    pallet.placements.append(
        _placement(
            box_id=100,
            z_mm=0,
            x_mm=0,
            y_mm=0,
            length_mm=60,
            width_mm=40,
            height_mm=30,
            orientation_family="stand_hw",
            orientation_name="stand_hw_seed",
        )
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=3,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_stand_mix_bonus=1.0,
        )
    )

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan is not None
    assert str(plan.preview.placement.orientation_family) == "stand_hw"
    assert int(scheduler.hard_floor_phase_stand_mix_candidates_total) == 1
    assert int(scheduler.hard_floor_phase_stand_mix_bonus_applied_total) == 1
    assert int(scheduler.hard_floor_phase_stand_mix_chosen_total) == 1
    assert int(scheduler.hard_floor_phase_stand_hw_chosen_total) == 1


def test_hard_floor_phase_stand_mix_bonus_zero_keeps_current_behavior() -> None:
    previews = {
        1: PreviewSpec(
            z_mm=0,
            x_mm=60,
            y_mm=0,
            length_mm=60,
            width_mm=40,
            height_mm=50,
            packing_gain=3.0,
            orientation_family="planar",
            orientation_name="planar_lw",
        ),
        2: PreviewSpec(
            z_mm=0,
            x_mm=0,
            y_mm=40,
            length_mm=40,
            width_mm=60,
            height_mm=30,
            packing_gain=3.0,
            orientation_family="stand_hw",
            orientation_name="stand_hw_lh",
        ),
    }
    pallet_a = FakePallet(previews)
    pallet_b = FakePallet(previews)
    seed = _placement(
        box_id=100,
        z_mm=0,
        x_mm=0,
        y_mm=0,
        length_mm=60,
        width_mm=40,
        height_mm=30,
        orientation_family="stand_hw",
        orientation_name="stand_hw_seed",
    )
    pallet_a.placements.append(seed)
    pallet_b.placements.append(seed)
    baseline = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=3,
            hard_floor_phase_min_base_candidates=1,
        )
    )
    with_zero_bonus = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=3,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_stand_mix_bonus=0.0,
        )
    )

    baseline_plan = baseline.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet_a))
    zero_bonus_plan = with_zero_bonus.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet_b))

    assert baseline_plan is not None and zero_bonus_plan is not None
    assert str(baseline_plan.preview.placement.orientation_family) == "planar"
    assert str(zero_bonus_plan.preview.placement.orientation_family) == str(
        baseline_plan.preview.placement.orientation_family
    )
    assert int(with_zero_bonus.hard_floor_phase_stand_mix_bonus_applied_total) == 0
    assert int(with_zero_bonus.hard_floor_phase_stand_mix_chosen_total) == 0


def test_hard_floor_phase_morphology_can_win_with_worse_legacy_local_score() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(
                z_mm=0,
                x_mm=40,
                y_mm=0,
                length_mm=20,
                width_mm=20,
                height_mm=60,
                packing_gain=9.0,
                orientation_family="planar",
                orientation_name="planar_small",
            ),
            2: PreviewSpec(
                z_mm=0,
                x_mm=40,
                y_mm=20,
                length_mm=40,
                width_mm=40,
                height_mm=60,
                packing_gain=1.0,
                orientation_family="planar",
                orientation_name="planar_dense",
            ),
        }
    )
    pallet.placements.append(
        _placement(
            box_id=100,
            z_mm=0,
            x_mm=0,
            y_mm=0,
            length_mm=40,
            width_mm=40,
            height_mm=60,
            orientation_family="planar",
            orientation_name="seed",
        )
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_morphology_mode="on",
        )
    )

    def prefer_box_1(self: SchedulerV1, *, preview: PlacementPreview, **_: object) -> float:
        placement = getattr(preview, "placement", None)
        box_id = int(getattr(placement, "box_id", 0) or 0)
        return 10.0 if box_id == 1 else 1.0

    scheduler._hard_floor_phase_score_preview = MethodType(prefer_box_1, scheduler)  # type: ignore[method-assign]

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan is not None
    assert int(plan.box_id) == 2


def test_hard_floor_phase_morphology_rejects_isolated_high_spot() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(
                z_mm=0,
                x_mm=80,
                y_mm=0,
                length_mm=20,
                width_mm=20,
                height_mm=120,
                packing_gain=6.0,
                orientation_family="stand_hw",
                orientation_name="stand_hw_isolated",
            ),
            2: PreviewSpec(
                z_mm=0,
                x_mm=40,
                y_mm=0,
                length_mm=40,
                width_mm=40,
                height_mm=60,
                packing_gain=3.0,
                orientation_family="planar",
                orientation_name="planar_balanced",
            ),
        }
    )
    pallet.placements.extend(
        [
            _placement(
                box_id=101,
                z_mm=0,
                x_mm=0,
                y_mm=0,
                length_mm=40,
                width_mm=40,
                height_mm=60,
                orientation_family="planar",
                orientation_name="seed_a",
            ),
            _placement(
                box_id=102,
                z_mm=0,
                x_mm=0,
                y_mm=40,
                length_mm=40,
                width_mm=40,
                height_mm=60,
                orientation_family="planar",
                orientation_name="seed_b",
            ),
        ]
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_morphology_mode="on",
        )
    )

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan is not None
    assert int(plan.box_id) == 2


def test_hard_floor_phase_morphology_rejects_strong_height_gap_increase() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(
                z_mm=0,
                x_mm=40,
                y_mm=0,
                length_mm=40,
                width_mm=40,
                height_mm=200,
                packing_gain=8.0,
                orientation_family="stand_hw",
                orientation_name="stand_hw_tall",
            ),
            2: PreviewSpec(
                z_mm=0,
                x_mm=40,
                y_mm=40,
                length_mm=40,
                width_mm=40,
                height_mm=60,
                packing_gain=2.0,
                orientation_family="planar",
                orientation_name="planar_low",
            ),
        }
    )
    pallet.placements.extend(
        [
            _placement(
                box_id=201,
                z_mm=0,
                x_mm=0,
                y_mm=0,
                length_mm=40,
                width_mm=40,
                height_mm=60,
                orientation_family="planar",
                orientation_name="seed_a",
            ),
            _placement(
                box_id=202,
                z_mm=0,
                x_mm=0,
                y_mm=40,
                length_mm=40,
                width_mm=40,
                height_mm=60,
                orientation_family="planar",
                orientation_name="seed_b",
            ),
        ]
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_morphology_mode="on",
        )
    )

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan is not None
    assert int(plan.box_id) == 2


def test_hard_floor_phase_morphology_prefers_floor_when_base_open_even_with_low_floor_count() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(z_mm=120, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=9.0),
            2: PreviewSpec(z_mm=0, x_mm=40, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=0.1),
        },
        bin_area_mm2=12_000,
    )
    pallet.placements.append(
        _placement(
            box_id=401,
            z_mm=0,
            x_mm=0,
            y_mm=0,
            length_mm=40,
            width_mm=40,
            height_mm=60,
            orientation_family="planar",
            orientation_name="seed_open_base",
        )
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=5,
            hard_floor_phase_min_base_candidates=2,
            hard_floor_phase_morphology_mode="on",
        )
    )

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan is not None
    assert int(plan.preview.placement.z_mm) == 0


def test_hard_floor_phase_morphology_allows_stacking_when_base_is_closed() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(z_mm=160, x_mm=0, y_mm=0, length_mm=40, width_mm=30, height_mm=40, packing_gain=9.0),
            2: PreviewSpec(z_mm=0, x_mm=40, y_mm=30, length_mm=40, width_mm=30, height_mm=40, packing_gain=0.1),
        },
        bin_area_mm2=4_800,
    )
    pallet.placements.extend(
        [
            _placement(
                box_id=501,
                z_mm=0,
                x_mm=0,
                y_mm=0,
                length_mm=40,
                width_mm=30,
                height_mm=60,
                orientation_family="planar",
                orientation_name="seed_a",
            ),
            _placement(
                box_id=502,
                z_mm=0,
                x_mm=40,
                y_mm=0,
                length_mm=40,
                width_mm=30,
                height_mm=60,
                orientation_family="planar",
                orientation_name="seed_b",
            ),
            _placement(
                box_id=503,
                z_mm=0,
                x_mm=0,
                y_mm=30,
                length_mm=40,
                width_mm=30,
                height_mm=60,
                orientation_family="planar",
                orientation_name="seed_c",
            ),
        ]
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=6,
            hard_floor_phase_min_base_candidates=2,
            hard_floor_phase_morphology_mode="on",
        )
    )

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan is not None
    assert int(plan.preview.placement.z_mm) > 0


def test_hard_floor_phase_morphology_floor_delay_policy_off_mode_keeps_legacy() -> None:
    pallet = FakePallet(
        {
            1: PreviewSpec(z_mm=120, x_mm=0, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=9.0),
            2: PreviewSpec(z_mm=0, x_mm=40, y_mm=0, length_mm=40, width_mm=40, height_mm=40, packing_gain=0.1),
        },
        bin_area_mm2=12_000,
    )
    pallet.placements.append(
        _placement(
            box_id=601,
            z_mm=0,
            x_mm=0,
            y_mm=0,
            length_mm=40,
            width_mm=40,
            height_mm=60,
            orientation_family="planar",
            orientation_name="seed_open_base",
        )
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=5,
            hard_floor_phase_min_base_candidates=2,
            hard_floor_phase_morphology_mode="off",
        )
    )

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=pallet))
    assert plan is not None
    assert int(plan.preview.placement.z_mm) > 0


def test_hard_floor_phase_morphology_mode_off_keeps_legacy_behavior() -> None:
    previews = {
        1: PreviewSpec(
            z_mm=0,
            x_mm=60,
            y_mm=0,
            length_mm=60,
            width_mm=40,
            height_mm=50,
            packing_gain=3.0,
            orientation_family="planar",
            orientation_name="planar_lw",
        ),
        2: PreviewSpec(
            z_mm=0,
            x_mm=0,
            y_mm=40,
            length_mm=40,
            width_mm=60,
            height_mm=30,
            packing_gain=3.0,
            orientation_family="stand_hw",
            orientation_name="stand_hw_lh",
        ),
    }
    baseline_pallet = FakePallet(previews)
    off_mode_pallet = FakePallet(previews)
    seed = _placement(
        box_id=300,
        z_mm=0,
        x_mm=0,
        y_mm=0,
        length_mm=60,
        width_mm=40,
        height_mm=30,
        orientation_family="stand_hw",
        orientation_name="stand_hw_seed",
    )
    baseline_pallet.placements.append(seed)
    off_mode_pallet.placements.append(seed)

    legacy = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=3,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_stand_mix_bonus=1.0,
        )
    )
    explicit_off = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=3,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_stand_mix_bonus=1.0,
            hard_floor_phase_morphology_mode="off",
        )
    )

    legacy_plan = legacy.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=baseline_pallet))
    off_plan = explicit_off.choose_action(_sim_state(boxes=[_box(1), _box(2)], pallet=off_mode_pallet))

    assert legacy_plan is not None and off_plan is not None
    assert int(legacy_plan.box_id) == int(off_plan.box_id)
    assert str(legacy_plan.preview.placement.orientation_family) == str(off_plan.preview.placement.orientation_family)


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
            hard_floor_phase_stand_mix_bonus=0.7,
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
        hard_floor_phase_stand_mix_bonus=0.5,
        hard_floor_phase_morphology_mode="on",
    )
    policy._scheduler.hard_floor_phase_active_total = 3
    policy._scheduler.hard_floor_phase_floor_candidates_seen_total = 12
    policy._scheduler.hard_floor_phase_chosen_total = 2
    policy._scheduler.hard_floor_phase_stand_hw_chosen_total = 1
    policy._scheduler.hard_floor_phase_exit_no_floor_total = 1
    policy._scheduler.hard_floor_phase_exit_end_step_total = 1
    policy._scheduler.hard_floor_phase_score_sum = 5.0
    policy._scheduler.hard_floor_phase_stand_mix_bonus_applied_total = 4
    policy._scheduler.hard_floor_phase_stand_mix_candidates_total = 6
    policy._scheduler.hard_floor_phase_stand_mix_chosen_total = 1
    policy._scheduler.hard_floor_base_metrics_samples_total = 2
    policy._scheduler.hard_floor_base_height_gap_mm_sum = 150.0
    policy._scheduler.hard_floor_base_height_gap_mm_max = 90
    policy._scheduler.hard_floor_base_height_std_mm_sum = 45.0
    policy._scheduler.hard_floor_inaccessible_pocket_area_mm2_max = 1800
    policy._scheduler.hard_floor_boundary_connected_free_area_mm2_sum = 6400.0
    policy._scheduler.hard_floor_isolated_high_spots_total = 3

    kpis = policy.collect_kpis()

    assert int(kpis["hard_floor_phase_active_total"]) == 3
    assert int(kpis["hard_floor_phase_floor_candidates_seen_total"]) == 12
    assert int(kpis["hard_floor_phase_chosen_total"]) == 2
    assert int(kpis["hard_floor_phase_stand_hw_chosen_total"]) == 1
    assert int(kpis["hard_floor_phase_exit_no_floor_total"]) == 1
    assert int(kpis["hard_floor_phase_exit_end_step_total"]) == 1
    assert float(kpis["hard_floor_phase_stand_mix_bonus"]) == 0.5
    assert str(kpis["hard_floor_phase_morphology_mode"]) == "on"
    assert int(kpis["hard_floor_phase_stand_mix_bonus_applied_total"]) == 4
    assert int(kpis["hard_floor_phase_stand_mix_candidates_total"]) == 6
    assert int(kpis["hard_floor_phase_stand_mix_chosen_total"]) == 1
    assert float(kpis["hard_floor_phase_score_mean"]) == 2.5
    assert float(kpis["hard_floor_base_height_gap_mm_mean"]) == 75.0
    assert int(kpis["hard_floor_base_height_gap_mm_max"]) == 90
    assert float(kpis["hard_floor_base_height_std_mm_mean"]) == 22.5
    assert int(kpis["hard_floor_inaccessible_pocket_area_mm2_max"]) == 1800
    assert float(kpis["hard_floor_boundary_connected_free_area_mm2_mean"]) == 3200.0
    assert int(kpis["hard_floor_isolated_high_spots_total"]) == 3
