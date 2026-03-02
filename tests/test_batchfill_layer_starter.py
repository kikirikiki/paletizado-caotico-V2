from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerSimState, SchedulerV1


def test_batchfill_layer_starter_prefers_two_box_layer_start() -> None:
    spec = PalletSpec(length_mm=10, width_mm=10, max_height_mm=100, overhang_mm=0)
    pallet = PalletModel(spec=spec, heuristic="baf")

    base = Box(
        box_id=100,
        length_mm=10,
        width_mm=10,
        height_mm=10,
        timestamp=0.0,
        destination=1,
    )
    base_preview = pallet.preview_place(base)
    assert base_preview.feasible
    pallet.commit_place(base_preview)

    # En capa nueva: 5x10 permite colocar dos cajas; 6x10 solo una.
    box_6x10 = Box(box_id=1, length_mm=6, width_mm=10, height_mm=10, timestamp=0.0, destination=1)
    box_5x10_a = Box(box_id=2, length_mm=5, width_mm=10, height_mm=10, timestamp=0.0, destination=1)
    box_5x10_b = Box(box_id=3, length_mm=5, width_mm=10, height_mm=10, timestamp=0.0, destination=1)

    sim_state = SchedulerSimState(
        now=0.0,
        ramps={1: [box_6x10, box_5x10_a, box_5x10_b]},
        pallets={1: pallet},
        pallet_blocked=set(),
        remaining_total=3,
    )
    scheduler = SchedulerV1(
        SchedulerConfig(
            lookahead_k=3,
            micro_plan_enabled=False,
            batchfill_layer_starter=True,
        )
    )

    plan = scheduler.choose_action(sim_state)
    assert plan is not None
    assert int(plan.box_id) in {2, 3}
