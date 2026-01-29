"""Stub for future beam search scheduler."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BeamConfig:
    width: int = 1


class BeamScheduler:
    def __init__(self, config: BeamConfig | None = None) -> None:
        self.config = config or BeamConfig()

    def choose_action(self, sim_state: object) -> object | None:
        return None
