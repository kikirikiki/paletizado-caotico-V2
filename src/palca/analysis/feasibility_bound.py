from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from ortools.sat.python import cp_model

from sim.io import load_arrivals
from sim.paths import resolve_repo_path


BASE_LENGTH_MM = 1200
BASE_WIDTH_MM = 800
ORIENTATION_ORDERS: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("LWH", (0, 1, 2)),
    ("LHW", (0, 2, 1)),
    ("WLH", (1, 0, 2)),
    ("WHL", (1, 2, 0)),
    ("HLW", (2, 0, 1)),
    ("HWL", (2, 1, 0)),
)
ALLOW_LH_BASE = os.getenv("PALCA_ALLOW_LH_BASE", "0").lower() in ("1", "true", "yes", "y")
FORBID_LH_BASE = not ALLOW_LH_BASE


@dataclass(frozen=True)
class OrientedDims:
    orient_id: int
    orient_name: str
    a_mm: int
    b_mm: int
    h_mm: int

    @property
    def area_mm2(self) -> int:
        return int(self.a_mm) * int(self.b_mm)


@dataclass(frozen=True)
class BoundItem:
    item_idx: int
    row_idx: int
    length_mm: int
    width_mm: int
    height_mm: int
    orientations: tuple[OrientedDims, ...]


@dataclass(frozen=True)
class SolveSummary:
    mode: str
    status: str
    is_sat: bool
    is_proven_optimal: bool
    selected_count: int | None
    total_height_mm: int | None
    objective_value: int | None
    layers: list[dict[str, object]]


def _solver_status_name(status: int) -> str:
    if status == cp_model.OPTIMAL:
        return "OPTIMAL"
    if status == cp_model.FEASIBLE:
        return "FEASIBLE"
    if status == cp_model.INFEASIBLE:
        return "INFEASIBLE"
    if status == cp_model.MODEL_INVALID:
        return "MODEL_INVALID"
    return "UNKNOWN"


def _sat_label(status: int) -> str:
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return "SAT"
    if status == cp_model.INFEASIBLE:
        return "UNSAT"
    return "UNKNOWN"


def _is_orientation_allowed(orient_name: str) -> bool:
    if not FORBID_LH_BASE:
        return True
    return orient_name not in ("LHW", "HLW")


def _deduplicated_orientations(length_mm: int, width_mm: int, height_mm: int) -> tuple[OrientedDims, ...]:
    raw = (int(length_mm), int(width_mm), int(height_mm))
    seen: set[tuple[int, int, int]] = set()
    output: list[OrientedDims] = []
    orient_id = 0
    for orient_name, perm in ORIENTATION_ORDERS:
        if not _is_orientation_allowed(orient_name):
            continue
        dims = (raw[perm[0]], raw[perm[1]], raw[perm[2]])
        if dims in seen:
            continue
        seen.add(dims)
        output.append(
            OrientedDims(
                orient_id=orient_id,
                orient_name=orient_name,
                a_mm=dims[0],
                b_mm=dims[1],
                h_mm=dims[2],
            )
        )
        orient_id += 1
    return tuple(output)


def _load_bound_items(excel_path: str | Path, dest: int) -> list[BoundItem]:
    arrivals = load_arrivals(excel_path)
    filtered = [a for a in arrivals if int(a.destination) == int(dest)]
    items: list[BoundItem] = []
    for idx, arrival in enumerate(filtered):
        if arrival.length_mm is None or arrival.width_mm is None or arrival.height_mm is None:
            continue
        l_mm = int(arrival.length_mm)
        w_mm = int(arrival.width_mm)
        h_mm = int(arrival.height_mm)
        if l_mm <= 0 or w_mm <= 0 or h_mm <= 0:
            continue
        items.append(
            BoundItem(
                item_idx=idx,
                row_idx=int(arrival.row_idx),
                length_mm=l_mm,
                width_mm=w_mm,
                height_mm=h_mm,
                orientations=_deduplicated_orientations(l_mm, w_mm, h_mm),
            )
        )
    return items


def _orientation_is_viable_for_enforce(
    *,
    orient: OrientedDims,
    base_length_mm: int,
    base_width_mm: int,
    hmax_mm: int,
) -> bool:
    return (
        int(orient.a_mm) > 0
        and int(orient.b_mm) > 0
        and int(orient.h_mm) > 0
        and int(orient.a_mm) <= int(base_length_mm)
        and int(orient.b_mm) <= int(base_width_mm)
        and int(orient.h_mm) <= int(hmax_mm)
    )


def _parse_seed_list(seeds_raw: str, fallback_seed: int) -> list[int]:
    parsed: list[int] = []
    seen: set[int] = set()
    for raw in str(seeds_raw).split(","):
        token = raw.strip()
        if not token:
            continue
        try:
            seed = int(token)
        except ValueError as exc:
            raise ValueError(f"Semilla invalida en --enforce-seeds: {token!r}") from exc
        if seed in seen:
            continue
        seen.add(seed)
        parsed.append(seed)
    if parsed:
        return parsed
    return [int(fallback_seed)]


def _prepare_enforce_items(
    *,
    items: list[BoundItem],
    target: int,
    base_length_mm: int,
    base_width_mm: int,
    hmax_mm: int,
    enforce_cap_items: int,
    enforce_cap_slack: int,
    enforce_max_orients_per_item: int,
) -> tuple[list[BoundItem], int, int]:
    n_items = len(items)
    cap_items = int(enforce_cap_items)
    cap_slack = max(0, int(enforce_cap_slack))
    orients_cap = max(1, int(enforce_max_orients_per_item))

    if cap_items > 0:
        k_limit = min(n_items, cap_items)
    else:
        k_limit = min(n_items, max(0, int(target) + cap_slack))

    scored: list[tuple[tuple[int, int, int, int, int], BoundItem, list[OrientedDims]]] = []
    for stable_idx, item in enumerate(items):
        viable = [
            orient
            for orient in item.orientations
            if _is_orientation_allowed(orient.orient_name)
            and _orientation_is_viable_for_enforce(
                orient=orient,
                base_length_mm=base_length_mm,
                base_width_mm=base_width_mm,
                hmax_mm=hmax_mm,
            )
        ]
        viable.sort(key=lambda orient: (int(orient.area_mm2), int(orient.h_mm), int(orient.orient_id)))
        if viable:
            best = viable[0]
            score = (
                int(best.area_mm2),
                int(best.h_mm),
                int(item.item_idx),
                int(item.row_idx),
                stable_idx,
            )
        else:
            score = (
                10**15,
                10**15,
                int(item.item_idx),
                int(item.row_idx),
                stable_idx,
            )
        scored.append((score, item, viable))

    scored.sort(key=lambda row: row[0])

    capped_items: list[BoundItem] = []
    for _score, item, viable in scored[:k_limit]:
        if not viable:
            continue
        capped_items.append(
            BoundItem(
                item_idx=int(item.item_idx),
                row_idx=int(item.row_idx),
                length_mm=int(item.length_mm),
                width_mm=int(item.width_mm),
                height_mm=int(item.height_mm),
                orientations=tuple(viable[:orients_cap]),
            )
        )

    return capped_items, len(capped_items), orients_cap


def _allowed_orientations(orientations: tuple[OrientedDims, ...]) -> tuple[OrientedDims, ...]:
    return tuple(orient for orient in orientations if _is_orientation_allowed(orient.orient_name))


def _solve_layer_model(
    *,
    items: list[BoundItem],
    base_area_mm2: int,
    hmax_mm: int,
    max_layers: int,
    mode: str,
    target: int | None,
    time_limit_s: float,
    random_seed: int,
) -> SolveSummary:
    if mode not in {"target", "maximize"}:
        raise ValueError(f"Modo desconocido: {mode}")

    filtered_items = [
        BoundItem(
            item_idx=int(item.item_idx),
            row_idx=int(item.row_idx),
            length_mm=int(item.length_mm),
            width_mm=int(item.width_mm),
            height_mm=int(item.height_mm),
            orientations=_allowed_orientations(item.orientations),
        )
        for item in items
    ]
    items = [item for item in filtered_items if item.orientations]
    n_items = len(items)
    if n_items == 0:
        return SolveSummary(
            mode=mode,
            status="INFEASIBLE" if mode == "target" and (target or 0) > 0 else "OPTIMAL",
            is_sat=False if mode == "target" and (target or 0) > 0 else True,
            is_proven_optimal=True,
            selected_count=0,
            total_height_mm=0,
            objective_value=0,
            layers=[],
        )

    model = cp_model.CpModel()
    x: dict[tuple[int, int, int], cp_model.IntVar] = {}
    for i, item in enumerate(items):
        for k in range(max_layers):
            for o, _orient in enumerate(item.orientations):
                x[(i, k, o)] = model.NewBoolVar(f"x_i{i}_k{k}_o{o}")

    layer_used = [model.NewBoolVar(f"layer_used_{k}") for k in range(max_layers)]
    layer_height = [model.NewIntVar(0, hmax_mm, f"layer_height_{k}") for k in range(max_layers)]
    total_height = model.NewIntVar(0, hmax_mm, "total_height")
    selected_count = model.NewIntVar(0, n_items, "selected_count")

    for i, item in enumerate(items):
        assign_terms = [x[(i, k, o)] for k in range(max_layers) for o in range(len(item.orientations))]
        model.Add(sum(assign_terms) <= 1)

    for k in range(max_layers):
        area_terms: list[cp_model.LinearExpr] = []
        assign_terms: list[cp_model.IntVar] = []
        for i, item in enumerate(items):
            for o, orient in enumerate(item.orientations):
                var = x[(i, k, o)]
                area_terms.append(var * int(orient.area_mm2))
                assign_terms.append(var)
                model.Add(layer_height[k] >= int(orient.h_mm) * var)
                model.Add(var <= layer_used[k])
        model.Add(sum(area_terms) <= int(base_area_mm2))
        model.Add(sum(assign_terms) <= n_items * layer_used[k])
        model.Add(sum(assign_terms) >= layer_used[k])
        model.Add(layer_height[k] <= hmax_mm * layer_used[k])

    for k in range(max_layers - 1):
        model.Add(layer_used[k] >= layer_used[k + 1])
        model.Add(layer_height[k] >= layer_height[k + 1])

    model.Add(selected_count == sum(x.values()))
    model.Add(total_height == sum(layer_height))
    model.Add(total_height <= hmax_mm)

    if mode == "target":
        if target is None:
            raise ValueError("target es requerido en modo target")
        model.Add(selected_count >= int(target))
        model.Minimize(total_height)
    else:
        objective_scale = int(hmax_mm + 1)
        objective = selected_count * objective_scale - total_height
        model.Maximize(objective)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_s)
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = int(random_seed)

    status = solver.Solve(model)
    sat_status = _sat_label(status)
    is_sat = sat_status == "SAT"
    is_proven_optimal = status in (cp_model.OPTIMAL, cp_model.INFEASIBLE)

    if not is_sat:
        return SolveSummary(
            mode=mode,
            status=_solver_status_name(status),
            is_sat=False,
            is_proven_optimal=is_proven_optimal,
            selected_count=None,
            total_height_mm=None,
            objective_value=None,
            layers=[],
        )

    layer_payload: list[dict[str, object]] = []
    for k in range(max_layers):
        if solver.Value(layer_used[k]) <= 0:
            continue
        items_payload: list[dict[str, object]] = []
        for i, item in enumerate(items):
            for o, orient in enumerate(item.orientations):
                if solver.Value(x[(i, k, o)]) <= 0:
                    continue
                items_payload.append(
                    {
                        "item_idx": item.item_idx,
                        "row_idx": item.row_idx,
                        "original_dims_mm": [item.length_mm, item.width_mm, item.height_mm],
                        "orient_id": orient.orient_id,
                        "orient_name": orient.orient_name,
                        "oriented_dims_mm": [orient.a_mm, orient.b_mm, orient.h_mm],
                        "area_mm2": orient.area_mm2,
                    }
                )
        layer_payload.append(
            {
                "layer_idx": k,
                "height_mm": int(solver.Value(layer_height[k])),
                "count": len(items_payload),
                "items": items_payload,
            }
        )

    return SolveSummary(
        mode=mode,
        status=_solver_status_name(status),
        is_sat=True,
        is_proven_optimal=is_proven_optimal,
        selected_count=int(solver.Value(selected_count)),
        total_height_mm=int(solver.Value(total_height)),
        objective_value=int(solver.ObjectiveValue()),
        layers=layer_payload,
    )


def _solve_target_enforce_2d(
    *,
    items: list[BoundItem],
    base_length_mm: int,
    base_width_mm: int,
    base_area_mm2: int,
    hmax_mm: int,
    max_layers: int,
    target: int,
    time_limit_s: float,
    random_seed: int,
    enforce_mode: str = "sat",
    num_workers: int = 0,
    log_search: bool = False,
) -> dict[str, object]:
    if enforce_mode not in {"sat", "opt"}:
        raise ValueError(f"enforce_mode invalido: {enforce_mode}")

    filtered_items = [
        BoundItem(
            item_idx=int(item.item_idx),
            row_idx=int(item.row_idx),
            length_mm=int(item.length_mm),
            width_mm=int(item.width_mm),
            height_mm=int(item.height_mm),
            orientations=_allowed_orientations(item.orientations),
        )
        for item in items
    ]
    items = [item for item in filtered_items if item.orientations]
    n_items = len(items)
    if target <= 0:
        return {
            "status": "SAT",
            "solver_status": "OPTIMAL",
            "solver_status_proven": True,
            "selected_count": 0,
            "height_mm": 0,
            "used_layers": 0,
            "time_limit_s": float(time_limit_s),
            "used_no_overlap_2d": True,
            "enforce_mode": str(enforce_mode),
            "per_layer": [],
        }
    if n_items == 0 or max_layers <= 0 or int(target) > int(n_items):
        return {
            "status": "UNSAT",
            "solver_status": "INFEASIBLE",
            "solver_status_proven": True,
            "selected_count": None,
            "height_mm": None,
            "used_layers": None,
            "time_limit_s": float(time_limit_s),
            "used_no_overlap_2d": True,
            "enforce_mode": str(enforce_mode),
            "per_layer": [],
        }

    model = cp_model.CpModel()
    supports_no_overlap_2d = hasattr(model, "AddNoOverlap2D")

    present: dict[tuple[int, int, int], cp_model.IntVar] = {}
    placement: dict[tuple[int, int, int], tuple[cp_model.IntVar, cp_model.IntVar, int, int]] = {}
    layer_terms: list[list[cp_model.IntVar]] = [[] for _ in range(max_layers)]
    area_terms: list[list[cp_model.LinearExpr]] = [[] for _ in range(max_layers)]
    x_intervals_by_layer: list[list[cp_model.IntervalVar]] = [[] for _ in range(max_layers)]
    y_intervals_by_layer: list[list[cp_model.IntervalVar]] = [[] for _ in range(max_layers)]
    rects_by_layer: list[list[tuple[cp_model.IntVar, cp_model.IntVar, cp_model.IntVar, int, int]]] = [
        [] for _ in range(max_layers)
    ]

    layer_used = [model.NewBoolVar(f"used_{k}") for k in range(max_layers)]
    layer_height = [model.NewIntVar(0, hmax_mm, f"H_{k}") for k in range(max_layers)]
    total_height = model.NewIntVar(0, hmax_mm, "height_total")
    used_layers = model.NewIntVar(0, max_layers, "used_layers")
    selected_count = model.NewIntVar(0, n_items, "selected_count")

    for i, item in enumerate(items):
        assign_terms: list[cp_model.IntVar] = []
        for k in range(max_layers):
            for o, orient in enumerate(item.orientations):
                var = model.NewBoolVar(f"present_i{i}_k{k}_o{o}")
                present[(i, k, o)] = var
                assign_terms.append(var)
                layer_terms[k].append(var)
                area_terms[k].append(var * int(orient.area_mm2))
                model.Add(layer_height[k] >= int(orient.h_mm) * var)
                model.Add(var <= layer_used[k])

                w_mm = int(orient.a_mm)
                h_mm = int(orient.b_mm)
                if w_mm > base_length_mm or h_mm > base_width_mm:
                    model.Add(var == 0)
                    continue

                x_var = model.NewIntVar(0, base_length_mm - w_mm, f"x_i{i}_k{k}_o{o}")
                y_var = model.NewIntVar(0, base_width_mm - h_mm, f"y_i{i}_k{k}_o{o}")
                x_end = model.NewIntVar(w_mm, base_length_mm, f"x_end_i{i}_k{k}_o{o}")
                y_end = model.NewIntVar(h_mm, base_width_mm, f"y_end_i{i}_k{k}_o{o}")
                model.Add(x_end == x_var + w_mm)
                model.Add(y_end == y_var + h_mm)
                placement[(i, k, o)] = (x_var, y_var, w_mm, h_mm)

                x_interval = model.NewOptionalIntervalVar(x_var, w_mm, x_end, var, f"x_int_i{i}_k{k}_o{o}")
                y_interval = model.NewOptionalIntervalVar(y_var, h_mm, y_end, var, f"y_int_i{i}_k{k}_o{o}")
                x_intervals_by_layer[k].append(x_interval)
                y_intervals_by_layer[k].append(y_interval)
                rects_by_layer[k].append((var, x_var, y_var, w_mm, h_mm))
        model.Add(sum(assign_terms) <= 1)

    for k in range(max_layers):
        if supports_no_overlap_2d and x_intervals_by_layer[k]:
            model.AddNoOverlap2D(x_intervals_by_layer[k], y_intervals_by_layer[k])
        elif not supports_no_overlap_2d:
            rects = rects_by_layer[k]
            for i in range(len(rects)):
                pi, xi, yi, wi, hi = rects[i]
                for j in range(i + 1, len(rects)):
                    pj, xj, yj, wj, hj = rects[j]
                    left = model.NewBoolVar(f"left_k{k}_{i}_{j}")
                    right = model.NewBoolVar(f"right_k{k}_{i}_{j}")
                    below = model.NewBoolVar(f"below_k{k}_{i}_{j}")
                    above = model.NewBoolVar(f"above_k{k}_{i}_{j}")
                    model.AddBoolOr([left, right, below, above, pi.Not(), pj.Not()])
                    model.Add(xi + wi <= xj).OnlyEnforceIf(left)
                    model.Add(xj + wj <= xi).OnlyEnforceIf(right)
                    model.Add(yi + hi <= yj).OnlyEnforceIf(below)
                    model.Add(yj + hj <= yi).OnlyEnforceIf(above)

        model.Add(sum(area_terms[k]) <= int(base_area_mm2))
        model.Add(sum(layer_terms[k]) <= n_items * layer_used[k])
        model.Add(sum(layer_terms[k]) >= layer_used[k])
        model.Add(layer_height[k] <= hmax_mm * layer_used[k])

    for k in range(max_layers - 1):
        model.Add(layer_used[k] >= layer_used[k + 1])
        model.Add(layer_height[k] >= layer_height[k + 1])

    model.Add(selected_count == sum(present.values()))
    model.Add(total_height == sum(layer_height))
    model.Add(total_height <= hmax_mm)
    model.Add(used_layers == sum(layer_used))
    if enforce_mode == "sat":
        model.Add(selected_count == int(target))
    else:
        model.Add(selected_count >= int(target))
        model.Minimize(total_height * (max_layers + 1) + used_layers)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_s)
    if int(num_workers) <= 0:
        workers = int(os.cpu_count() or 1)
    else:
        workers = int(num_workers)
    solver.parameters.num_search_workers = max(1, workers)
    solver.parameters.random_seed = int(random_seed)
    if log_search:
        solver.parameters.log_search_progress = True

    status = solver.Solve(model)
    sat_status = _sat_label(status)
    is_sat = sat_status == "SAT"
    is_proven = status in (cp_model.OPTIMAL, cp_model.INFEASIBLE)

    result: dict[str, object] = {
        "status": sat_status,
        "solver_status": _solver_status_name(status),
        "solver_status_proven": bool(is_proven),
        "selected_count": None,
        "height_mm": None,
        "used_layers": None,
        "time_limit_s": float(time_limit_s),
        "used_no_overlap_2d": bool(supports_no_overlap_2d),
        "enforce_mode": str(enforce_mode),
        "per_layer": [],
    }
    if not is_sat:
        return result

    per_layer: list[dict[str, object]] = []
    for k in range(max_layers):
        if solver.Value(layer_used[k]) <= 0:
            continue
        coords: list[dict[str, object]] = []
        for i, item in enumerate(items):
            for o, orient in enumerate(item.orientations):
                key = (i, k, o)
                if solver.Value(present[key]) <= 0:
                    continue
                x_var, y_var, w_mm, h_mm = placement[key]
                coords.append(
                    {
                        "item_idx": int(item.item_idx),
                        "row_idx": int(item.row_idx),
                        "orient_id": int(orient.orient_id),
                        "orient_name": str(orient.orient_name),
                        "x_mm": int(solver.Value(x_var)),
                        "y_mm": int(solver.Value(y_var)),
                        "w_mm": int(w_mm),
                        "h_mm": int(h_mm),
                        "z_mm": int(orient.h_mm),
                    }
                )
        per_layer.append(
            {
                "layer_idx": int(k),
                "height_mm": int(solver.Value(layer_height[k])),
                "count": len(coords),
                "coords": coords,
            }
        )

    result["selected_count"] = int(solver.Value(selected_count))
    result["height_mm"] = int(solver.Value(total_height))
    result["used_layers"] = int(solver.Value(used_layers))
    result["per_layer"] = per_layer
    return result


def _solve_target_enforce_2d_multishot(
    *,
    items: list[BoundItem],
    base_length_mm: int,
    base_width_mm: int,
    base_area_mm2: int,
    hmax_mm: int,
    max_layers: int,
    target: int,
    time_limit_s: float,
    seeds: list[int],
    shot_time_limit_s: float,
    enforce_mode: str,
    num_workers: int,
    log_search: bool,
) -> dict[str, object]:
    if not seeds:
        raise ValueError("seeds no puede ser vacio")

    total_budget = max(0.0, float(time_limit_s))
    max_shot = float(shot_time_limit_s)
    started_at = time.monotonic()
    attempts: list[dict[str, object]] = []
    last_unknown: dict[str, object] | None = None

    for seed in seeds:
        elapsed = time.monotonic() - started_at
        remaining = total_budget - elapsed
        if remaining <= 1e-6:
            break
        if max_shot > 0.0:
            shot_limit = min(max_shot, remaining)
        else:
            shot_limit = remaining

        shot_result = _solve_target_enforce_2d(
            items=items,
            base_length_mm=base_length_mm,
            base_width_mm=base_width_mm,
            base_area_mm2=base_area_mm2,
            hmax_mm=hmax_mm,
            max_layers=max_layers,
            target=target,
            time_limit_s=shot_limit,
            random_seed=int(seed),
            enforce_mode=enforce_mode,
            num_workers=num_workers,
            log_search=log_search,
        )
        attempts.append(
            {
                "seed": int(seed),
                "time_limit_s": float(shot_limit),
                "status": str(shot_result["status"]),
                "solver_status": str(shot_result["solver_status"]),
                "solver_status_proven": bool(shot_result["solver_status_proven"]),
            }
        )

        if shot_result["status"] == "SAT":
            shot_result["time_limit_s"] = float(time_limit_s)
            shot_result["attempts"] = attempts
            return shot_result

        if shot_result["status"] == "UNSAT" and bool(shot_result["solver_status_proven"]):
            shot_result["time_limit_s"] = float(time_limit_s)
            shot_result["attempts"] = attempts
            return shot_result

        last_unknown = shot_result

    if last_unknown is None:
        return {
            "status": "UNKNOWN",
            "solver_status": "UNKNOWN",
            "solver_status_proven": False,
            "selected_count": None,
            "height_mm": None,
            "used_layers": None,
            "time_limit_s": float(time_limit_s),
            "used_no_overlap_2d": True,
            "enforce_mode": str(enforce_mode),
            "per_layer": [],
            "attempts": attempts,
        }

    last_unknown["status"] = "UNKNOWN"
    last_unknown["solver_status_proven"] = False
    last_unknown["time_limit_s"] = float(time_limit_s)
    last_unknown["attempts"] = attempts
    return last_unknown


def _solve_layer_2d_packing(
    *,
    layer_idx: int,
    layer_items: list[dict[str, object]],
    base_length_mm: int,
    base_width_mm: int,
    time_limit_s: float,
    random_seed: int,
) -> dict[str, object]:
    model = cp_model.CpModel()
    supports_no_overlap_2d = hasattr(model, "AddNoOverlap2D")

    x_vars: list[cp_model.IntVar] = []
    y_vars: list[cp_model.IntVar] = []
    widths: list[int] = []
    heights: list[int] = []
    items_payload: list[dict[str, object]] = []
    x_intervals: list[cp_model.IntervalVar] = []
    y_intervals: list[cp_model.IntervalVar] = []

    for idx, item in enumerate(layer_items):
        orient_name = item.get("orient_name")
        if orient_name is not None and not _is_orientation_allowed(str(orient_name)):
            return {
                "layer_idx": int(layer_idx),
                "status": "UNSAT",
                "solver_status": "INFEASIBLE",
                "solver_status_proven": True,
                "coords": [],
                "reason": "orient_name prohibido por regla de negocio",
            }

        dims_raw = item.get("oriented_dims_mm")
        if not isinstance(dims_raw, list) or len(dims_raw) < 2:
            return {
                "layer_idx": int(layer_idx),
                "status": "UNKNOWN",
                "solver_status": "MODEL_INVALID",
                "solver_status_proven": True,
                "coords": [],
                "reason": "oriented_dims_mm invalido",
            }

        w_mm = int(dims_raw[0])
        h_mm = int(dims_raw[1])
        if w_mm <= 0 or h_mm <= 0:
            return {
                "layer_idx": int(layer_idx),
                "status": "UNSAT",
                "solver_status": "INFEASIBLE",
                "solver_status_proven": True,
                "coords": [],
                "reason": "dimension no positiva",
            }
        if w_mm > base_length_mm or h_mm > base_width_mm:
            return {
                "layer_idx": int(layer_idx),
                "status": "UNSAT",
                "solver_status": "INFEASIBLE",
                "solver_status_proven": True,
                "coords": [],
                "reason": "item excede base",
            }

        x_var = model.NewIntVar(0, base_length_mm - w_mm, f"x_l{layer_idx}_i{idx}")
        y_var = model.NewIntVar(0, base_width_mm - h_mm, f"y_l{layer_idx}_i{idx}")
        x_end = model.NewIntVar(w_mm, base_length_mm, f"x_end_l{layer_idx}_i{idx}")
        y_end = model.NewIntVar(h_mm, base_width_mm, f"y_end_l{layer_idx}_i{idx}")
        model.Add(x_end == x_var + w_mm)
        model.Add(y_end == y_var + h_mm)

        x_interval = model.NewIntervalVar(x_var, w_mm, x_end, f"x_int_l{layer_idx}_i{idx}")
        y_interval = model.NewIntervalVar(y_var, h_mm, y_end, f"y_int_l{layer_idx}_i{idx}")

        x_vars.append(x_var)
        y_vars.append(y_var)
        widths.append(w_mm)
        heights.append(h_mm)
        x_intervals.append(x_interval)
        y_intervals.append(y_interval)
        items_payload.append(item)

    if supports_no_overlap_2d:
        model.AddNoOverlap2D(x_intervals, y_intervals)
    else:
        n = len(items_payload)
        for i in range(n):
            for j in range(i + 1, n):
                left = model.NewBoolVar(f"left_l{layer_idx}_i{i}_j{j}")
                right = model.NewBoolVar(f"right_l{layer_idx}_i{i}_j{j}")
                below = model.NewBoolVar(f"below_l{layer_idx}_i{i}_j{j}")
                above = model.NewBoolVar(f"above_l{layer_idx}_i{i}_j{j}")
                model.AddBoolOr([left, right, below, above])
                model.Add(x_vars[i] + widths[i] <= x_vars[j]).OnlyEnforceIf(left)
                model.Add(x_vars[j] + widths[j] <= x_vars[i]).OnlyEnforceIf(right)
                model.Add(y_vars[i] + heights[i] <= y_vars[j]).OnlyEnforceIf(below)
                model.Add(y_vars[j] + heights[j] <= y_vars[i]).OnlyEnforceIf(above)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_s)
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = int(random_seed)

    status = solver.Solve(model)
    sat_status = _sat_label(status)
    is_sat = sat_status == "SAT"
    result: dict[str, object] = {
        "layer_idx": int(layer_idx),
        "status": sat_status,
        "solver_status": _solver_status_name(status),
        "solver_status_proven": status in (cp_model.OPTIMAL, cp_model.INFEASIBLE),
        "used_no_overlap_2d": bool(supports_no_overlap_2d),
        "coords": [],
    }
    if not is_sat:
        return result

    coords: list[dict[str, object]] = []
    for idx, item in enumerate(items_payload):
        coords.append(
            {
                "item_idx": int(item["item_idx"]),
                "row_idx": int(item["row_idx"]),
                "x_mm": int(solver.Value(x_vars[idx])),
                "y_mm": int(solver.Value(y_vars[idx])),
                "w_mm": int(widths[idx]),
                "h_mm": int(heights[idx]),
            }
        )
    result["coords"] = coords
    return result


def _verify_target_layers_2d(
    *,
    target_layers: list[dict[str, object]],
    base_length_mm: int,
    base_width_mm: int,
    time_limit_s: float,
    random_seed: int,
) -> dict[str, object]:
    per_layer: list[dict[str, object]] = []
    all_sat = True
    saw_unknown = False

    for layer in target_layers:
        layer_idx = int(layer["layer_idx"])
        items_raw = layer.get("items", [])
        if not isinstance(items_raw, list):
            layer_result = {
                "layer_idx": layer_idx,
                "status": "UNKNOWN",
                "solver_status": "MODEL_INVALID",
                "solver_status_proven": True,
                "coords": [],
                "reason": "items invalido",
            }
        else:
            layer_result = _solve_layer_2d_packing(
                layer_idx=layer_idx,
                layer_items=items_raw,
                base_length_mm=base_length_mm,
                base_width_mm=base_width_mm,
                time_limit_s=time_limit_s,
                random_seed=random_seed,
            )
        per_layer.append(layer_result)
        if layer_result["status"] != "SAT":
            all_sat = False
            if layer_result["status"] != "UNSAT":
                saw_unknown = True

    if all_sat:
        status = "SAT"
    elif saw_unknown:
        status = "UNKNOWN"
    else:
        status = "UNSAT"

    return {
        "verify_2d": bool(all_sat),
        "status": status,
        "time_limit_s": float(time_limit_s),
        "per_layer": per_layer,
    }


def run_feasibility_bound(
    *,
    excel_path: str | Path,
    dest: int,
    overhang_mm: int,
    hmax_mm: int,
    target: int,
    max_layers: int = 12,
    time_limit_s: float = 20.0,
    random_seed: int = 123,
    verify_2d: bool = False,
    verify_time_limit_s: float = 10.0,
    enforce_2d: bool = False,
    enforce_time_limit_s: float = 30.0,
    enforce_mode: str = "sat",
    enforce_cap_items: int = 0,
    enforce_cap_slack: int = 8,
    enforce_max_orients_per_item: int = 2,
    enforce_seeds: str = "123,7,99",
    enforce_shot_s: float = 25.0,
    enforce_num_workers: int = 0,
    enforce_log_search: bool = False,
) -> dict[str, object]:
    resolved_excel = resolve_repo_path(str(excel_path))
    items = _load_bound_items(resolved_excel, dest=dest)

    base_length_mm = BASE_LENGTH_MM + 2 * int(overhang_mm)
    base_width_mm = BASE_WIDTH_MM + 2 * int(overhang_mm)
    base_area_mm2 = base_length_mm * base_width_mm
    parsed_enforce_seeds = _parse_seed_list(enforce_seeds, fallback_seed=random_seed)
    enforce_items, enforce_effective_count, orients_capped = _prepare_enforce_items(
        items=items,
        target=target,
        base_length_mm=base_length_mm,
        base_width_mm=base_width_mm,
        hmax_mm=hmax_mm,
        enforce_cap_items=enforce_cap_items,
        enforce_cap_slack=enforce_cap_slack,
        enforce_max_orients_per_item=enforce_max_orients_per_item,
    )

    target_summary = _solve_layer_model(
        items=items,
        base_area_mm2=base_area_mm2,
        hmax_mm=hmax_mm,
        max_layers=max_layers,
        mode="target",
        target=target,
        time_limit_s=time_limit_s,
        random_seed=random_seed,
    )
    maximize_summary = _solve_layer_model(
        items=items,
        base_area_mm2=base_area_mm2,
        hmax_mm=hmax_mm,
        max_layers=max_layers,
        mode="maximize",
        target=None,
        time_limit_s=time_limit_s,
        random_seed=random_seed,
    )

    if verify_2d and target_summary.is_sat:
        verify_2d_result = _verify_target_layers_2d(
            target_layers=target_summary.layers,
            base_length_mm=base_length_mm,
            base_width_mm=base_width_mm,
            time_limit_s=verify_time_limit_s,
            random_seed=random_seed,
        )
    else:
        verify_2d_result = {
            "verify_2d": False,
            "status": "SKIPPED" if not verify_2d else "SKIPPED_TARGET_UNSAT",
            "time_limit_s": float(verify_time_limit_s),
            "per_layer": [],
        }

    if enforce_2d:
        enforce_2d_result = _solve_target_enforce_2d_multishot(
            items=enforce_items,
            base_length_mm=base_length_mm,
            base_width_mm=base_width_mm,
            base_area_mm2=base_area_mm2,
            hmax_mm=hmax_mm,
            max_layers=max_layers,
            target=target,
            time_limit_s=enforce_time_limit_s,
            seeds=parsed_enforce_seeds,
            shot_time_limit_s=enforce_shot_s,
            enforce_mode=enforce_mode,
            num_workers=enforce_num_workers,
            log_search=enforce_log_search,
        )
    else:
        enforce_2d_result = {
            "status": "SKIPPED",
            "solver_status": "SKIPPED",
            "solver_status_proven": False,
            "selected_count": None,
            "height_mm": None,
            "used_layers": None,
            "time_limit_s": float(enforce_time_limit_s),
            "used_no_overlap_2d": True,
            "enforce_mode": str(enforce_mode),
            "per_layer": [],
            "attempts": [],
        }

    summary: dict[str, object] = {
        "input": {
            "excel": str(resolved_excel),
            "dest": int(dest),
            "target": int(target),
            "max_layers": int(max_layers),
            "time_limit_s": float(time_limit_s),
            "verify_2d_enabled": bool(verify_2d),
            "verify_time_limit_s": float(verify_time_limit_s),
            "enforce_2d_enabled": bool(enforce_2d),
            "enforce_time_limit_s": float(enforce_time_limit_s),
            "enforce_mode": str(enforce_mode),
            "enforce_cap_items": int(enforce_cap_items),
            "enforce_cap_slack": int(enforce_cap_slack),
            "enforce_max_orients_per_item": int(enforce_max_orients_per_item),
            "enforce_seeds": parsed_enforce_seeds,
            "enforce_shot_s": float(enforce_shot_s),
            "enforce_num_workers": int(enforce_num_workers),
            "enforce_log_search": bool(enforce_log_search),
            "random_seed": int(random_seed),
            "allow_lh_base_override_env": bool(ALLOW_LH_BASE),
            "forbid_lh_base": bool(FORBID_LH_BASE),
            "base_length_mm": int(base_length_mm),
            "base_width_mm": int(base_width_mm),
            "base_area_mm2": int(base_area_mm2),
            "hmax_mm": int(hmax_mm),
            "overhang_mm": int(overhang_mm),
            "items_considered": len(items),
            "items_considered_effective_for_enforce_2d": int(enforce_effective_count),
            "orients_capped": int(orients_capped),
        },
        "target_mode": {
            "status": "SAT" if target_summary.is_sat else "UNSAT",
            "solver_status": target_summary.status,
            "solver_status_proven": target_summary.is_proven_optimal,
            "selected_count": target_summary.selected_count,
            "min_height_mm": target_summary.total_height_mm,
            "layers": target_summary.layers,
            "verify_2d": bool(verify_2d_result["verify_2d"]),
            "verify_2d_status": str(verify_2d_result["status"]),
            "verify_2d_result": verify_2d_result,
            "verify_2d_note": "post_check_only; UNSAT here does not prove global impossibility",
            "enforce_2d": bool(enforce_2d),
            "enforce_2d_status": str(enforce_2d_result["status"]),
            "enforce_2d_result": enforce_2d_result,
        },
        "maximize_mode": {
            "status": "SAT" if maximize_summary.is_sat else "UNSAT",
            "solver_status": maximize_summary.status,
            "solver_status_proven": maximize_summary.is_proven_optimal,
            "nmax": maximize_summary.selected_count,
            "height_mm": maximize_summary.total_height_mm,
            "layers": maximize_summary.layers,
        },
        "summary": {
            "status": "SAT" if target_summary.is_sat else "UNSAT",
            "nmax": maximize_summary.selected_count,
            "min_height_for_target_mm": target_summary.total_height_mm if target_summary.is_sat else None,
            "target": int(target),
            "verify_2d": bool(verify_2d_result["verify_2d"]),
            "verify_2d_status": str(verify_2d_result["status"]),
            "enforce_2d": bool(enforce_2d),
            "enforce_2d_status": str(enforce_2d_result["status"]),
        },
    }
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bound optimista por capas para alcanzabilidad en pallet")
    parser.add_argument("--excel", required=True, help="Ruta al Excel de entrada")
    parser.add_argument("--dest", type=int, required=True, help="Destino a analizar")
    parser.add_argument("--overhang", type=int, default=20, help="Overhang en mm")
    parser.add_argument("--hmax", type=int, default=2400, help="Altura maxima en mm")
    parser.add_argument("--target", type=int, default=21, help="Cantidad objetivo de cajas")
    parser.add_argument("--max-layers", "--layers", dest="max_layers", type=int, default=12, help="Maximo de capas del modelo")
    parser.add_argument("--time-limit-s", type=float, default=20.0, help="Time limit por solve en segundos")
    parser.add_argument("--random-seed", type=int, default=123, help="Semilla del solver")
    parser.add_argument("--verify-2d", action="store_true", help="Verifica packing 2D real por capa (target_mode)")
    parser.add_argument("--verify-time-limit-s", type=float, default=10.0, help="Time limit por capa para verify_2d")
    parser.add_argument("--enforce-2d", action="store_true", help="Resuelve target con modelo 2D real integrado por capas")
    parser.add_argument("--enforce-time-limit-s", type=float, default=30.0, help="Time limit total para enforce_2d")
    parser.add_argument("--enforce-mode", choices=["sat", "opt"], default="sat", help="sat: factibilidad pura, opt: minimiza altura/capas")
    parser.add_argument("--enforce-cap-items", type=int, default=0, help="Cap fijo de items para enforce_2d (0 => target + slack)")
    parser.add_argument("--enforce-cap-slack", type=int, default=8, help="Slack para cap dinamico cuando enforce-cap-items=0")
    parser.add_argument(
        "--enforce-max-orients-per-item",
        type=int,
        default=2,
        help="Max orientaciones viables por item en enforce_2d",
    )
    parser.add_argument("--enforce-seeds", default="123,7,99", help="Lista CSV de seeds para multi-shot enforce_2d")
    parser.add_argument("--enforce-shot-s", type=float, default=25.0, help="Time limit max por shot/seed en enforce_2d")
    parser.add_argument("--enforce-num-workers", type=int, default=0, help="Workers CP-SAT (0 => os.cpu_count())")
    parser.add_argument("--enforce-log-search", action="store_true", help="Activa log de busqueda CP-SAT para enforce_2d")
    parser.add_argument("--compact", action="store_true", help="Imprime resumen compacto antes del JSON")
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    report = run_feasibility_bound(
        excel_path=args.excel,
        dest=args.dest,
        overhang_mm=args.overhang,
        hmax_mm=args.hmax,
        target=args.target,
        max_layers=args.max_layers,
        time_limit_s=args.time_limit_s,
        random_seed=args.random_seed,
        verify_2d=args.verify_2d,
        verify_time_limit_s=args.verify_time_limit_s,
        enforce_2d=args.enforce_2d,
        enforce_time_limit_s=args.enforce_time_limit_s,
        enforce_mode=args.enforce_mode,
        enforce_cap_items=args.enforce_cap_items,
        enforce_cap_slack=args.enforce_cap_slack,
        enforce_max_orients_per_item=args.enforce_max_orients_per_item,
        enforce_seeds=args.enforce_seeds,
        enforce_shot_s=args.enforce_shot_s,
        enforce_num_workers=args.enforce_num_workers,
        enforce_log_search=args.enforce_log_search,
    )

    if args.compact:
        summary = report["summary"]
        target_mode = report["target_mode"]
        maximize_mode = report["maximize_mode"]
        print(
            "status={status} nmax={nmax} min_height_for_target_mm={min_height} "
            "target_solver={target_solver} maximize_solver={maximize_solver} "
            "verify_2d_postcheck={verify_2d} enforce_2d={enforce_2d}".format(
                status=summary["status"],
                nmax=summary["nmax"],
                min_height=summary["min_height_for_target_mm"],
                target_solver=target_mode["solver_status"],
                maximize_solver=maximize_mode["solver_status"],
                verify_2d=summary.get("verify_2d_status", "SKIPPED"),
                enforce_2d=summary.get("enforce_2d_status", "SKIPPED"),
            )
        )

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
