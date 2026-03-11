from __future__ import annotations

from dataclasses import dataclass

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1
from sim import run as sim_run


@dataclass(frozen=True)
class PreviewSpec:
    z_mm: int
    packing_gain: float
    fragmentation: float = 0.0
    layer_id: int | None = None
    orientation_family: str | None = None
    orientation_name: str | None = None


class StatefulFakePallet:
    def __init__(
        self,
        *,
        previews_by_state: dict[tuple[int, ...], dict[int, PreviewSpec]],
        seed_placements: list[Placement],
        seed_committed_ids: list[int],
    ) -> None:
        self._previews_by_state = dict(previews_by_state)
        self.placements: list[Placement] = list(seed_placements)
        self._committed_ids = list(seed_committed_ids)
        self.bin_area_mm2 = 12_000

    def _state_key(self) -> tuple[int, ...]:
        return tuple(int(v) for v in self._committed_ids)

    def preview_place(self, box: Box) -> PlacementPreview:
        specs = self._previews_by_state.get(self._state_key(), {})
        spec = specs.get(int(box.box_id))
        if spec is None:
            return PlacementPreview(
                feasible=False,
                placement=None,
                packing_gain=0.0,
                fragmentation=0.0,
                infeasible_reason="NO_SPACE",
            )
        layer_id = int(spec.layer_id) if spec.layer_id is not None else (0 if int(spec.z_mm) == 0 else 1)
        placement = Placement(
            x_mm=0,
            y_mm=0,
            z_mm=int(spec.z_mm),
            rot90=False,
            layer_id=int(layer_id),
            length_mm=40,
            width_mm=40,
            height_mm=40,
            box_id=box.box_id,
            orientation_family=spec.orientation_family,
            orientation_name=spec.orientation_name,
        )
        return PlacementPreview(
            feasible=True,
            placement=placement,
            packing_gain=float(spec.packing_gain),
            fragmentation=float(spec.fragmentation),
            height_after_mm=int(spec.z_mm) + 40,
            infeasible_reason=None,
        )

    def commit_place(self, preview: PlacementPreview) -> Placement:
        assert preview.placement is not None
        self.placements.append(preview.placement)
        self._committed_ids.append(int(preview.placement.box_id))
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


def _placement(*, box_id: int, z_mm: int, layer_id: int, orientation_family: str | None = None) -> Placement:
    return Placement(
        x_mm=0,
        y_mm=0,
        z_mm=int(z_mm),
        rot90=False,
        layer_id=int(layer_id),
        length_mm=40,
        width_mm=40,
        height_mm=40,
        box_id=int(box_id),
        orientation_family=orientation_family,
    )


def _sim_state(*, boxes: list[Box], pallet: StatefulFakePallet) -> SchedulerSimState:
    return SchedulerSimState(
        now=0.0,
        ramps={1: list(boxes)},
        pallets={1: pallet},
        pallet_blocked=set(),
        remaining_total=len(boxes),
    )


def test_active_layer_continuation_search_forces_active_layer_when_short_sequence_exists() -> None:
    pallet = StatefulFakePallet(
        previews_by_state={
            (900,): {
                1: PreviewSpec(z_mm=240, packing_gain=9.0, layer_id=2),
                2: PreviewSpec(z_mm=120, packing_gain=1.0, layer_id=1),
            },
            (900, 2): {
                1: PreviewSpec(z_mm=240, packing_gain=8.5, layer_id=2),
                3: PreviewSpec(z_mm=120, packing_gain=1.0, layer_id=1),
            },
        },
        seed_placements=[_placement(box_id=900, z_mm=120, layer_id=1)],
        seed_committed_ids=[900],
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=3,
            enforce_active_layer_continuation_search=True,
            active_layer_search_depth=2,
            active_layer_search_width=3,
        )
    )

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2), _box(3)], pallet=pallet))
    assert plan is not None
    assert int(plan.box_id) == 2
    assert int(scheduler.active_layer_search_invocations) == 1
    assert int(scheduler.active_layer_search_successes) == 1
    assert int(scheduler.upper_layer_open_deferred_by_search) == 1


def test_active_layer_continuation_search_allows_upper_open_when_no_continuation_exists() -> None:
    pallet = StatefulFakePallet(
        previews_by_state={
            (900,): {
                1: PreviewSpec(z_mm=240, packing_gain=9.0, layer_id=2),
                2: PreviewSpec(z_mm=120, packing_gain=1.0, layer_id=1),
            },
            (900, 2): {
                1: PreviewSpec(z_mm=240, packing_gain=8.5, layer_id=2),
            },
        },
        seed_placements=[_placement(box_id=900, z_mm=120, layer_id=1)],
        seed_committed_ids=[900],
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=3,
            enforce_active_layer_continuation_search=True,
            active_layer_search_depth=2,
            active_layer_search_width=3,
        )
    )

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2), _box(3)], pallet=pallet))
    assert plan is not None
    assert int(plan.box_id) == 1
    assert int(scheduler.active_layer_search_invocations) == 1
    assert int(scheduler.active_layer_search_failures) == 1
    assert int(scheduler.upper_layer_open_deferred_by_search) == 0


def test_active_layer_continuation_search_uses_base_z_not_layer_id_for_high_stand_hw_case() -> None:
    pallet = StatefulFakePallet(
        previews_by_state={
            (901,): {
                1: PreviewSpec(z_mm=300, packing_gain=8.0, layer_id=0),
                2: PreviewSpec(
                    z_mm=200,
                    packing_gain=1.0,
                    layer_id=0,
                    orientation_family="stand_hw",
                    orientation_name="stand_hw_lh",
                ),
            },
            (901, 2): {
                3: PreviewSpec(z_mm=200, packing_gain=1.0, layer_id=0),
            },
        },
        seed_placements=[_placement(box_id=901, z_mm=200, layer_id=0, orientation_family="stand_hw")],
        seed_committed_ids=[901],
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=3,
            enforce_active_layer_continuation_search=True,
            active_layer_search_depth=2,
            active_layer_search_width=3,
        )
    )

    plan = scheduler.choose_action(_sim_state(boxes=[_box(1), _box(2), _box(3)], pallet=pallet))
    assert plan is not None
    assert int(plan.box_id) == 2
    assert int(scheduler.upper_layer_open_deferred_by_search) == 1


def test_policy_collect_kpis_exposes_active_layer_search_counters() -> None:
    policy = PolicyPackerScheduler.from_defaults(
        enforce_active_layer_continuation_search=True,
        active_layer_search_depth=3,
        active_layer_search_width=5,
    )
    policy._scheduler.active_layer_search_invocations = 7
    policy._scheduler.active_layer_search_successes = 4
    policy._scheduler.active_layer_search_failures = 3
    policy._scheduler.upper_layer_open_deferred_by_search = 2
    policy._scheduler.active_layer_search_events = [
        {"mode": "greedy", "continuation_len_found": 2, "deferred_upper_open": True}
    ]

    kpis = policy.collect_kpis()

    assert bool(kpis["enforce_active_layer_continuation_search"]) is True
    assert int(kpis["active_layer_search_depth"]) == 3
    assert int(kpis["active_layer_search_width"]) == 5
    assert int(kpis["active_layer_search_invocations"]) == 7
    assert int(kpis["active_layer_search_successes"]) == 4
    assert int(kpis["active_layer_search_failures"]) == 3
    assert int(kpis["upper_layer_open_deferred_by_search"]) == 2
    assert isinstance(kpis["active_layer_search_events"], list)


def test_build_parser_accepts_active_layer_continuation_search_flags() -> None:
    parser = sim_run.build_parser()
    args = parser.parse_args(
        [
            "--excel",
            "dummy.xlsx",
            "--enforce-active-layer-continuation-search",
            "--active-layer-search-depth",
            "3",
            "--active-layer-search-width",
            "6",
        ]
    )
    assert bool(args.enforce_active_layer_continuation_search) is True
    assert int(args.active_layer_search_depth) == 3
    assert int(args.active_layer_search_width) == 6
