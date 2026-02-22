from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ControllerMode(str, Enum):
    NORMAL = "NORMAL"
    PUSH = "PUSH"
    RESCUE = "RESCUE"


@dataclass(frozen=True)
class DecisionContext:
    last_ok: bool
    last_fail_reason: str | None
    consec_ok: int
    consec_fail: int
    pick_index: int


@dataclass(frozen=True)
class Overrides:
    score_mode: str | None = None
    height_slack_mm: int | None = None
    micro_depth: int | None = None
    micro_width: int | None = None
    micro_topk: int | None = None
    time_budget_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        raw = {
            "score_mode": self.score_mode,
            "height_slack_mm": self.height_slack_mm,
            "micro_depth": self.micro_depth,
            "micro_width": self.micro_width,
            "micro_topk": self.micro_topk,
            "time_budget_ms": self.time_budget_ms,
        }
        return {key: value for key, value in raw.items() if value is not None}


@dataclass(frozen=True)
class ControllerEvent:
    pick_index: int
    from_mode: ControllerMode
    to_mode: ControllerMode
    trigger: str
    overrides: dict[str, Any]
    note: str | None = None
