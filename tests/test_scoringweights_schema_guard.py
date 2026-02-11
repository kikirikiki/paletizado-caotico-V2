from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from palca.packer.scoring import ScoringWeights


def test_scoringweights_attributes_used_in_code_exist_in_schema() -> None:
    field_names = {f.name for f in fields(ScoringWeights)}
    used: set[str] = set()
    pattern = re.compile(r"\bscoring_weights\.([a-zA-Z_][a-zA-Z0-9_]*)\b")

    for path in Path("src/palca").rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        used.update(match.group(1) for match in pattern.finditer(content))

    missing = sorted(used - field_names)
    assert not missing, f"ScoringWeights missing fields used in code: {missing}"
