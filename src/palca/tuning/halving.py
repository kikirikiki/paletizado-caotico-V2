from __future__ import annotations

from typing import Any, Callable, Mapping

from .rank import make_rank_key


Evaluator = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def successive_halving(
    configs: list[dict[str, Any]],
    evaluator: Evaluator,
    phases: list[Mapping[str, Any]],
    eta: int = 2,
) -> dict[str, Any]:
    if eta < 2:
        raise ValueError("eta must be >= 2")
    if not phases:
        raise ValueError("phases must not be empty")

    survivors = [dict(cfg) for cfg in configs]
    history: list[dict[str, Any]] = []

    for phase_index, phase in enumerate(phases):
        phase_cfg = dict(phase)
        phase_name = str(phase_cfg.get("name", f"phase{phase_index + 1}"))
        phase_results: list[dict[str, Any]] = []

        for config in survivors:
            evaluated = evaluator(dict(config), phase_cfg)
            metrics = dict(evaluated.get("metrics", {}))
            rank_key = make_rank_key(metrics)
            row = dict(evaluated)
            row["phase"] = phase_name
            row["config"] = dict(config)
            row["rank_key"] = tuple(rank_key)
            phase_results.append(row)

        phase_results.sort(key=lambda row: tuple(row["rank_key"]))
        keep = max(1, len(phase_results) // int(eta))
        if len(phase_results) <= 1:
            keep = len(phase_results)

        selected = phase_results[:keep]
        survivors = [dict(row["config"]) for row in selected]

        history.append(
            {
                "phase": phase_name,
                "settings": phase_cfg,
                "evaluated": phase_results,
                "selected": selected,
                "keep": int(keep),
            }
        )

    finalists = history[-1]["evaluated"] if history else []
    finalists_sorted = sorted(finalists, key=lambda row: tuple(row["rank_key"]))
    best = finalists_sorted[0] if finalists_sorted else None

    return {
        "history": history,
        "finalists": finalists_sorted,
        "best": best,
    }
