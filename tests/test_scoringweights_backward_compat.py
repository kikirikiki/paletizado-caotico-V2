from __future__ import annotations

from palca.packer.pallet_model import PalletModel
from palca.packer.scoring import ScoringWeights


def test_scoringweights_height_increase_is_preserved() -> None:
    model = PalletModel(scoring_weights=ScoringWeights(height_increase_penalty_ratio=0.33))
    assert model.scoring_weights.height_increase_penalty_ratio == 0.33


def test_legacy_scoringweights_without_height_increase_uses_default() -> None:
    class LegacyScoringWeights:
        packing_gain_weight = 1.5
        fragmentation_weight = 0.8
        tower_penalty_ratio = 0.2

    model = PalletModel(scoring_weights=LegacyScoringWeights())
    assert model.scoring_weights.height_increase_penalty_ratio == 0.0
