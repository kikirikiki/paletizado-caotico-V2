from __future__ import annotations


def selection_dt(buffer_index: int, t_base: float, t_step: float) -> float:
    idx = int(buffer_index)
    if idx <= 0:
        return 0.0
    return float(t_base) + float(t_step) * float(idx)


def time_penalty(dt_extra: float, weight: float) -> float:
    return float(weight) * float(dt_extra)


def starvation_penalty(age: float, weight: float) -> float:
    if age <= 0:
        return 0.0
    return float(weight) * float(age)


def priority_bonus(priority_norm: float, weight: float) -> float:
    if priority_norm <= 0:
        return 0.0
    return float(weight) * float(priority_norm)
