from __future__ import annotations

from dataclasses import dataclass
from types import MethodType, SimpleNamespace

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.scheduler.hard_floor_morphology import BaseProbeSlot
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
    def __init__(
        self,
        previews_by_box: dict[int, PreviewSpec],
        *,
        bin_area_mm2: int = 12_000,
        bin_length_mm: int = 120,
        bin_width_mm: int = 100,
    ) -> None:
        self._previews_by_box = dict(previews_by_box)
        self.placements: list[Placement] = []
        self.bin_area_mm2 = int(bin_area_mm2)
        self.bin_length_mm = int(bin_length_mm)
        self.bin_width_mm = int(bin_width_mm)
        self.stacking_mode = "heightfield"
        self.stand_hw_height_margin_gate_mm = 200
        self.spec = SimpleNamespace(
            bin_length_mm=int(bin_length_mm),
            bin_width_mm=int(bin_width_mm),
            offset_mm=0,
            allow_rotate=True,
            max_height_mm=2_000,
        )

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


class GateProbePallet(FakePallet):
    def __init__(self) -> None:
        super().__init__({}, bin_area_mm2=12_000, bin_length_mm=120, bin_width_mm=100)

    def _orientations(self, length_mm: int, width_mm: int, height_mm: int) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                rot90=False,
                length_mm=int(height_mm),
                width_mm=int(width_mm),
                height_mm=int(length_mm),
                name="HWL",
                family="stand_hw",
            )
        ]

    def _heightfield_xy_candidates(self, l_mm: int, w_mm: int, *, cap: int = 120) -> list[tuple[int, int]]:
        _ = (l_mm, w_mm, cap)
        return [(40, 0)]

    def preview_place(self, box: Box) -> PlacementPreview:
        if int(self.stand_hw_height_margin_gate_mm) <= 200:
            return PlacementPreview(
                feasible=False,
                placement=None,
                packing_gain=0.0,
                fragmentation=0.0,
                infeasible_reason="NO_SPACE",
            )
        placement = Placement(
            x_mm=40,
            y_mm=0,
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=int(box.height_mm),
            width_mm=int(box.width_mm),
            height_mm=int(box.length_mm),
            box_id=int(box.box_id),
            orientation_family="stand_hw",
            orientation_name="stand_hw_probe",
        )
        return PlacementPreview(
            feasible=True,
            placement=placement,
            packing_gain=2.0,
            fragmentation=0.1,
            height_after_mm=int(box.length_mm),
            infeasible_reason=None,
        )


def _box(box_id: int) -> Box:
    return Box(
        box_id=int(box_id),
        length_mm=1,
        width_mm=1,
        height_mm=1,
        timestamp=0.0,
        destination=1,
    )


def _box_dims(*, box_id: int, length_mm: int, width_mm: int, height_mm: int) -> Box:
    return Box(
        box_id=int(box_id),
        length_mm=int(length_mm),
        width_mm=int(width_mm),
        height_mm=int(height_mm),
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


def test_hard_floor_phase_local_slot_probe_injects_useful_stand_candidate() -> None:
    previews = {
        1: PreviewSpec(
            z_mm=0,
            x_mm=0,
            y_mm=40,
            length_mm=40,
            width_mm=40,
            height_mm=20,
            packing_gain=1.0,
            orientation_family="planar",
            orientation_name="planar_floor",
        ),
        2: PreviewSpec(
            z_mm=120,
            x_mm=0,
            y_mm=0,
            length_mm=40,
            width_mm=40,
            height_mm=40,
            packing_gain=0.5,
            orientation_family="planar",
            orientation_name="planar_stack",
        ),
    }
    baseline_pallet = FakePallet(previews)
    probe_pallet = FakePallet(previews)
    seed_a = _placement(
        box_id=900,
        z_mm=0,
        x_mm=0,
        y_mm=0,
        length_mm=40,
        width_mm=40,
        height_mm=20,
        orientation_family="planar",
        orientation_name="seed_a",
    )
    seed_b = _placement(
        box_id=901,
        z_mm=0,
        x_mm=80,
        y_mm=0,
        length_mm=40,
        width_mm=40,
        height_mm=20,
        orientation_family="planar",
        orientation_name="seed_b",
    )
    baseline_pallet.placements.extend([seed_a, seed_b])
    probe_pallet.placements.extend([seed_a, seed_b])

    baseline = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=6,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_morphology_mode="on",
            hard_floor_phase_local_slot_probe_enabled=False,
        )
    )
    with_probe = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=6,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_morphology_mode="on",
            hard_floor_phase_local_slot_probe_enabled=True,
            hard_floor_phase_local_slot_probe_max_slots=2,
            hard_floor_phase_local_slot_probe_max_boxes_per_slot=2,
        )
    )

    def score_prefers_box_2(self: SchedulerV1, *, preview: PlacementPreview, **_: object) -> float:
        placement = getattr(preview, "placement", None)
        box_id = int(getattr(placement, "box_id", 0) or 0)
        return 50.0 if box_id == 2 else 1.0

    def next_floor_prefers_box_2(
        self: SchedulerV1,
        *,
        selected_box: Box | None = None,
        **_: object,
    ) -> float:
        if selected_box is None:
            return 0.0
        return 1.0 if int(getattr(selected_box, "box_id", 0) or 0) == 2 else 0.0

    baseline._hard_floor_phase_score_preview = MethodType(score_prefers_box_2, baseline)  # type: ignore[method-assign]
    baseline._hard_floor_phase_next_floor_ratio = MethodType(next_floor_prefers_box_2, baseline)  # type: ignore[method-assign]
    with_probe._hard_floor_phase_score_preview = MethodType(score_prefers_box_2, with_probe)  # type: ignore[method-assign]
    with_probe._hard_floor_phase_next_floor_ratio = MethodType(next_floor_prefers_box_2, with_probe)  # type: ignore[method-assign]

    def fake_slot_probe(
        self: SchedulerV1,
        *,
        box: Box,
        **_: object,
    ) -> PlacementPreview | None:
        if int(getattr(box, "box_id", 0) or 0) != 2:
            return None
        placement = Placement(
            x_mm=40,
            y_mm=0,
            z_mm=0,
            rot90=False,
            layer_id=0,
            length_mm=40,
            width_mm=40,
            height_mm=50,
            box_id=2,
            orientation_family="stand_hw",
            orientation_name="stand_hw_probe_slot",
        )
        return PlacementPreview(
            feasible=True,
            placement=placement,
            packing_gain=3.0,
            fragmentation=0.1,
            height_after_mm=50,
            infeasible_reason=None,
        )

    with_probe._hard_floor_phase_probe_preview_for_slot = MethodType(fake_slot_probe, with_probe)  # type: ignore[method-assign]

    boxes = [
        _box_dims(box_id=1, length_mm=40, width_mm=40, height_mm=20),
        _box_dims(box_id=2, length_mm=50, width_mm=40, height_mm=40),
    ]
    baseline_plan = baseline.choose_action(_sim_state(boxes=list(boxes), pallet=baseline_pallet))
    probe_plan = with_probe.choose_action(_sim_state(boxes=list(boxes), pallet=probe_pallet))

    assert baseline_plan is not None and probe_plan is not None
    assert int(baseline_plan.box_id) == 1
    assert int(probe_plan.box_id) == 2
    assert str(probe_plan.preview.placement.orientation_family) == "stand_hw"
    assert int(with_probe.hard_floor_local_slot_probe_candidates_total) >= 1


def test_hard_floor_phase_local_slot_probe_relaxes_stand_gate_only_inside_probe() -> None:
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=1,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_morphology_mode="on",
            hard_floor_phase_local_slot_probe_enabled=True,
        )
    )
    pallet = GateProbePallet()
    box = _box_dims(box_id=7, length_mm=50, width_mm=40, height_mm=40)
    slot = BaseProbeSlot(
        x_mm=40,
        y_mm=0,
        length_mm=40,
        width_mm=40,
        area_mm2=1600,
        adjacency_count=2,
        boundary_contacts=1,
    )

    preview = scheduler._hard_floor_phase_probe_preview_for_slot(
        pallet=pallet,
        box=box,
        slot=slot,
    )

    assert preview is not None
    assert bool(preview.feasible)
    assert str(preview.placement.orientation_family) == "stand_hw"
    assert int(pallet.stand_hw_height_margin_gate_mm) == 200


def test_hard_floor_phase_local_slot_probe_fallback_matches_baseline_when_no_hit() -> None:
    previews = {
        1: PreviewSpec(
            z_mm=0,
            x_mm=0,
            y_mm=0,
            length_mm=40,
            width_mm=40,
            height_mm=30,
            packing_gain=4.0,
            orientation_family="planar",
            orientation_name="planar_floor",
        ),
        2: PreviewSpec(
            z_mm=0,
            x_mm=40,
            y_mm=0,
            length_mm=40,
            width_mm=40,
            height_mm=30,
            packing_gain=2.0,
            orientation_family="planar",
            orientation_name="planar_floor_2",
        ),
    }
    baseline_pallet = FakePallet(previews)
    probe_pallet = FakePallet(previews)

    baseline = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_morphology_mode="on",
            hard_floor_phase_local_slot_probe_enabled=False,
        )
    )
    with_probe = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_morphology_mode="on",
            hard_floor_phase_local_slot_probe_enabled=True,
        )
    )

    def no_probe_hit(self: SchedulerV1, **_: object) -> PlacementPreview | None:
        return None

    with_probe._hard_floor_phase_probe_preview_for_slot = MethodType(no_probe_hit, with_probe)  # type: ignore[method-assign]

    boxes = [_box_dims(box_id=1, length_mm=40, width_mm=40, height_mm=30), _box_dims(box_id=2, length_mm=40, width_mm=40, height_mm=30)]
    baseline_plan = baseline.choose_action(_sim_state(boxes=list(boxes), pallet=baseline_pallet))
    probe_plan = with_probe.choose_action(_sim_state(boxes=list(boxes), pallet=probe_pallet))

    assert baseline_plan is not None and probe_plan is not None
    assert int(probe_plan.box_id) == int(baseline_plan.box_id)
    assert str(probe_plan.preview.placement.orientation_family) == str(
        baseline_plan.preview.placement.orientation_family
    )
    assert int(with_probe.hard_floor_local_slot_probe_hits_total) == 0


def test_hard_floor_phase_local_slot_probe_does_not_change_behavior_when_morphology_off() -> None:
    previews = {
        1: PreviewSpec(
            z_mm=0,
            x_mm=0,
            y_mm=0,
            length_mm=40,
            width_mm=40,
            height_mm=30,
            packing_gain=5.0,
            orientation_family="planar",
            orientation_name="planar_a",
        ),
        2: PreviewSpec(
            z_mm=0,
            x_mm=40,
            y_mm=0,
            length_mm=40,
            width_mm=40,
            height_mm=30,
            packing_gain=1.0,
            orientation_family="planar",
            orientation_name="planar_b",
        ),
    }
    baseline_pallet = FakePallet(previews)
    probe_pallet = FakePallet(previews)

    baseline = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_morphology_mode="off",
        )
    )
    with_probe = SchedulerV1(
        SchedulerConfig(
            lookahead_k=2,
            hard_floor_phase_end_step=4,
            hard_floor_phase_min_base_candidates=1,
            hard_floor_phase_morphology_mode="off",
            hard_floor_phase_local_slot_probe_enabled=True,
        )
    )

    def fail_if_probe_called(self: SchedulerV1, **_: object) -> PlacementPreview | None:
        raise AssertionError("local slot probe should not be used when morphology is off")

    with_probe._hard_floor_phase_probe_preview_for_slot = MethodType(fail_if_probe_called, with_probe)  # type: ignore[method-assign]

    boxes = [_box_dims(box_id=1, length_mm=40, width_mm=40, height_mm=30), _box_dims(box_id=2, length_mm=40, width_mm=40, height_mm=30)]
    baseline_plan = baseline.choose_action(_sim_state(boxes=list(boxes), pallet=baseline_pallet))
    probe_plan = with_probe.choose_action(_sim_state(boxes=list(boxes), pallet=probe_pallet))

    assert baseline_plan is not None and probe_plan is not None
    assert int(probe_plan.box_id) == int(baseline_plan.box_id)
    assert str(probe_plan.preview.placement.orientation_family) == str(
        baseline_plan.preview.placement.orientation_family
    )


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
