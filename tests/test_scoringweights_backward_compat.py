from __future__ import annotations

from dataclasses import dataclass

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.pallet_model import PalletModel
from palca.packer.scoring import ScoringWeights


@dataclass(frozen=True)
class _LegacyScoringWeights:
    packing_gain_weight: float = 1.0
    fragmentation_weight: float = 1.0
    tower_penalty_ratio: float = 0.1


def test_scoring_weights_exposes_new_layer_penalty_ratio() -> None:
    weights = ScoringWeights(new_layer_penalty_ratio=0.25)
    model = PalletModel(scoring_weights=weights)
    assert model.scoring_weights.new_layer_penalty_ratio == 0.25


def test_pallet_model_accepts_legacy_scoring_weights_without_new_layer_penalty_ratio() -> None:
    spec = PalletSpec(length_mm=10, width_mm=10, max_height_mm=12)
    model = PalletModel(spec=spec, scoring_weights=_LegacyScoringWeights())

    assert model.scoring_weights.new_layer_penalty_ratio == 0.0

    box = Box(box_id=1, length_mm=10, width_mm=10, height_mm=5, timestamp=0.0)
    preview = model.preview_place(box)
    assert preview.feasible
    assert preview.placement is not None
    model.commit_place(preview)


def test_scoringweights_height_increase_is_preserved() -> None:
    model = PalletModel(scoring_weights=ScoringWeights(height_increase_penalty_ratio=0.33))
    assert model.scoring_weights.height_increase_penalty_ratio == 0.33


def test_legacy_scoringweights_without_height_increase_uses_default() -> None:
    model = PalletModel(scoring_weights=_LegacyScoringWeights())
    assert model.scoring_weights.height_increase_penalty_ratio == 0.0
