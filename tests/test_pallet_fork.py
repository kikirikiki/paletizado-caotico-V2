from __future__ import annotations

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel


def _box(box_id: int, length_mm: int, width_mm: int, height_mm: int) -> Box:
    return Box(
        box_id=box_id,
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        timestamp=0.0,
        destination=1,
    )


def _commit(model: PalletModel, box: Box) -> None:
    preview = model.preview_place(box)
    assert preview.feasible
    model.commit_place(preview)


def test_pallet_fork_isolated_from_original() -> None:
    model = PalletModel(spec=PalletSpec(overhang_mm=20), heuristic="baf")
    _commit(model, _box(1, 600, 400, 200))
    _commit(model, _box(2, 600, 400, 200))

    original_count = len(model.placements)
    original_height = model.current_height_mm()
    original_used_area = [layer.bin.used_area for layer in model.layers]

    fork = model.fork()
    _commit(fork, _box(3, 200, 200, 200))

    assert len(fork.placements) == original_count + 1
    assert len(model.placements) == original_count
    assert model.current_height_mm() == original_height
    assert [layer.bin.used_area for layer in model.layers] == original_used_area

    preview_original = model.preview_place(_box(10, 300, 200, 150))
    preview_fork = fork.preview_place(_box(10, 300, 200, 150))

    assert isinstance(preview_original.feasible, bool)
    assert isinstance(preview_fork.feasible, bool)
