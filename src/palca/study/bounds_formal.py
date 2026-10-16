"""Upper bounds for one orthogonal arm6 pallet, not packing algorithms.

All lengths are integer mm. Support, accessibility, order and the stand gate
are relaxed. DFF definitions and conservative scales: Fekete & Schepers,
https://arxiv.org/abs/cs/0402044, propositions 3/4, theorem 5, corollary 8.
See docs/COTAS.md for proofs, numerical certificates and limitations.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from functools import reduce
from itertools import product
from math import ceil, gcd, isfinite, prod
from typing import Sequence

import numpy as np
from ortools.linear_solver import pywraplp
from ortools.sat.python import cp_model

from .metric import ENVELOPE_MM

SCALE = 1_000_000


@dataclass(frozen=True)
class DFF:
    family: str
    parameter: Fraction = Fraction(0)

    def __post_init__(self):
        p = self.parameter
        if self.family == "identity":
            return
        if self.family == "u" and p.denominator == 1 and p >= 1:
            return
        if self.family == "U" and 0 <= p <= Fraction(1, 2):
            return
        if self.family == "phi" and 0 < p < Fraction(1, 2):
            return
        raise ValueError("Invalid DFF family/parameter")

    def __call__(self, x: Fraction) -> Fraction:
        x = Fraction(x)
        if not 0 <= x <= 1:
            raise ValueError("DFF argument must be in [0, 1]")
        if self.family == "identity":
            return x
        if self.family == "u":
            k = int(self.parameter)
            t = (k + 1) * x
            return x if t.denominator == 1 else Fraction(t.numerator // t.denominator, k)
        eps = self.parameter
        if self.family == "U":
            return Fraction(0) if x < eps else Fraction(1) if x > 1 - eps else x
        n = (1 / eps).__floor__()
        if x > Fraction(1, 2):
            return 1 - Fraction(((1 - x) / eps).__floor__(), n)
        return Fraction(1, n) if x >= eps else Fraction(0)

    @property
    def name(self) -> str:
        return f"{self.family}({self.parameter})"


def dff_grid() -> tuple[DFF, ...]:
    """Identity, k=1..20, epsilon=j/40; phi excludes 0 and 1/2."""
    return (DFF("identity"), *(DFF("u", Fraction(k)) for k in range(1, 21)),
            *(DFF("U", Fraction(j, 40)) for j in range(1, 21)),
            *(DFF("phi", Fraction(j, 40)) for j in range(1, 20)))


def _dimensions(dims: Sequence[int]) -> tuple[int, int, int]:
    if len(dims) != 3 or any(isinstance(d, bool) or int(d) != d or d <= 0 for d in dims):
        raise ValueError("Dimensions must be three positive integer mm values")
    return tuple(int(d) for d in dims)


def orientations(dims: Sequence[int], envelope=ENVELOPE_MM) -> tuple[tuple[int, ...], ...]:
    """LxW and HxW resting faces, with both yaw rotations; never HxL."""
    length, width, height = _dimensions(dims)
    return tuple(o for o in dict.fromkeys(((length, width, height), (width, length, height),
                                          (height, width, length), (width, height, length)))
                 if all(d <= cap for d, cap in zip(o, envelope)))


def is_dominant(dims: Sequence[int]) -> bool:
    """RESEARCH §1, inclusive ranges on ORIGINAL L/W/H, not sorted sides."""
    l, w, h = dims
    return 600 <= l <= 610 and 440 <= w <= 450 and 345 <= h <= 395


@dataclass
class DFFSystem:
    types: tuple[tuple[int, ...], ...]
    counts: tuple[int, ...]
    oriented: tuple[tuple[int, ...], ...]
    owners: tuple[int, ...]
    rows: np.ndarray
    min_rows: np.ndarray
    axis_function_counts: tuple[int, ...]
    envelope: tuple[int, ...]


def build_dff_system(boxes: Sequence[Sequence[int]], envelope=ENVELOPE_MM) -> DFFSystem:
    envelope = _dimensions(envelope)
    inventory = Counter(_dimensions(d) for d in boxes)
    # Unpackable items cannot contribute to any pallet, but remain in flow volume.
    types = tuple(sorted(d for d in inventory if orientations(d, envelope)))
    if not types:
        raise ValueError("Need at least one box fitting an allowed orientation")
    counts = tuple(inventory[d] for d in types)
    oriented, owners, starts = [], [], []
    for i, dims in enumerate(types):
        starts.append(len(oriented))
        for orientation in orientations(dims, envelope):
            oriented.append(orientation)
            owners.append(i)
    axes = []
    for axis, cap in enumerate(envelope):
        # Rational evaluation, then downward rounding. Products fit signed int64.
        values = [[(f(Fraction(o[axis], cap)) * SCALE).__floor__() for o in oriented]
                  for f in dff_grid()]
        unique = np.unique(np.array(values, dtype=np.int64), axis=0)
        # Pointwise dominated axis functions cannot strengthen any product row.
        axes.append(np.array([row for i, row in enumerate(unique)
                              if not any(j != i and np.all(other >= row)
                                         for j, other in enumerate(unique))]))
    blocks = []
    for x, y in product(axes[0], axes[1]):
        blocks.append((axes[2] * (x * y)) // (SCALE * SCALE))
    rows = np.unique(np.concatenate(blocks), axis=0)
    rows = rows[np.any(rows, axis=1)]
    min_rows = np.unique(np.minimum.reduceat(rows, starts, axis=1), axis=0)
    min_rows = min_rows[np.any(min_rows, axis=1)]
    return DFFSystem(types, counts, tuple(oriented), tuple(owners), rows, min_rows,
                     tuple(len(a) for a in axes), envelope)


def _ratio(value: Fraction) -> dict:
    # float is presentation only; the fraction is the authoritative bound.
    return {"numerator": value.numerator, "denominator": value.denominator,
            "value": float(value)}


def certify_dual(rows, rhs, objective, caps, multipliers) -> tuple[Fraction, dict]:
    """Exact weak-duality certificate, valid even for an inaccurate LP solve.

    For A*x<=b, 0<=x<=caps, ANY lambda>=0 certifies
    c*x <= lambda*b + sum caps_j * max(0,c_j-(lambda*A)_j).
    Rows are integer coefficients divided by SCALE; residuals are exact.
    """
    residual = list(objective)
    value = Fraction(0)
    active = []
    for i, raw in enumerate(multipliers):
        if not isfinite(raw):
            raise ValueError("Nonfinite LP dual")
        lam = Fraction(int(max(0.0, raw) * 10**9), 10**9)
        if not lam:
            continue
        value += lam * rhs[i]
        for j, a in enumerate(rows[i]):
            residual[j] -= lam * Fraction(int(a), SCALE)
        active.append({"row": i, "multiplier": _ratio(lam)})
    correction = sum((cap * max(Fraction(0), r) for cap, r in zip(caps, residual)), Fraction(0))
    value += correction
    return value, {"active_rows": active, "positive_residual_correction": _ratio(correction),
                   "upper_bound": _ratio(value)}


def solve_dff_lp(system: DFFSystem, *, shared_orientations: bool) -> dict:
    """GLOP proposes multipliers; exact rational arithmetic certifies the bound."""
    venv = prod(system.envelope)
    if shared_orientations:
        rows = system.rows
        owners = system.owners
        caps = [system.counts[i] for i in owners]
        objective = [Fraction(prod(system.types[i]), venv) for i in owners]
        inventory_rows = np.zeros((len(system.types), len(owners)), dtype=np.int64)
        for j, i in enumerate(owners):
            inventory_rows[i, j] = SCALE
        rows = np.concatenate((rows, inventory_rows))
        rhs = [1] * len(system.rows) + list(system.counts)
    else:
        rows = system.min_rows
        caps = list(system.counts)
        objective = [Fraction(prod(d), venv) for d in system.types]
        rhs = [1] * len(rows)
    solver = pywraplp.Solver.CreateSolver("GLOP")
    if solver is None:
        raise RuntimeError("OR-Tools GLOP is required")
    solver.SetSolverSpecificParametersAsString("use_dual_simplex: true")
    variables = [solver.NumVar(0, cap, f"x{j}") for j, cap in enumerate(caps)]
    for var, c in zip(variables, objective):
        solver.Objective().SetCoefficient(var, float(c))
    solver.Objective().SetMaximization()
    # Separation over the complete row set avoids a large degenerate simplex
    # model. Every omitted constraint is checked after each restricted solve.
    constraints = {}
    while True:
        status = solver.Solve()
        if status != pywraplp.Solver.OPTIMAL:
            raise RuntimeError(f"GLOP did not solve the relaxation: {status}")
        x = np.array([v.solution_value() for v in variables])
        violation = rows @ x / SCALE - rhs
        if constraints:
            violation[list(constraints)] = 0
        candidates = np.flatnonzero(violation > 1e-8)
        if not len(candidates):
            break
        selected = candidates[np.argsort(violation[candidates])[-128:]]
        for i in selected:
            constraint = solver.RowConstraint(-solver.infinity(), rhs[i], "")
            for j in np.flatnonzero(rows[i]):
                constraint.SetCoefficient(variables[j], int(rows[i, j]) / SCALE)
            constraints[int(i)] = constraint
    multipliers = [0.0] * len(rows)
    for i, constraint in constraints.items():
        multipliers[i] = constraint.dual_value()
    bound, certificate = certify_dual(rows, rhs, objective, caps,
                                      multipliers)
    return {"upper_bound": _ratio(min(Fraction(1), bound)), "status": "OPTIMAL",
            "lp_objective": solver.Objective().Value(), "n_variables": len(variables),
            "n_constraints": len(rows), "n_constraints_added": len(constraints),
            "certificate": certificate}


def _cp_solver(seconds: float) -> cp_model.CpSolver:
    if not isfinite(seconds) or seconds <= 0:
        raise ValueError("CP-SAT time limit must be positive and finite")
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    return solver


def horizontal_bound(boxes: Sequence[Sequence[int]], envelope=ENVELOPE_MM,
                     *, seconds=30.0, finite_inventory=False) -> dict:
    """Exact 2D model of footprints, optionally respecting the box inventory.

    Integral coordinates lose no orthogonal packing: fix each pair's separation
    direction, then compact by longest paths with integral edge lengths.
    A timeout returns the solver's upper bound, never its feasible objective.
    """
    envelope = _dimensions(envelope)
    inventory = Counter(_dimensions(d) for d in boxes)
    footprints = sorted({o[:2] for d in boxes for o in orientations(d, envelope)})
    if not footprints:
        return {"upper_bound": _ratio(Fraction(0)), "status": "EMPTY"}
    unit = reduce(gcd, (*envelope[:2], *(d for fp in footprints for d in fp)))
    length, width = (d // unit for d in envelope[:2])
    catalogue = [(l // unit, w // unit, l * w // unit**2) for l, w in footprints]
    type_catalogue = [(o[0] // unit, o[1] // unit, o[0] * o[1] // unit**2, i)
                      for i, dims in enumerate(inventory) for o in orientations(dims, envelope)]
    area = length * width
    slots = area // min(a for _, _, a in catalogue)
    model = cp_model.CpModel()
    xs, ys, heights, widths, areas, rectangles = [], [], [], [], [], []
    type_choices = [[] for _ in inventory]
    for slot in range(slots):
        w = model.new_int_var(0, length, f"w{slot}")
        h = model.new_int_var(0, width, f"h{slot}")
        a = model.new_int_var(0, area, f"a{slot}")
        if finite_inventory:
            kind = model.new_int_var(-1, len(inventory) - 1, f"type{slot}")
            model.add_allowed_assignments([w, h, a, kind], [(0, 0, 0, -1), *type_catalogue])
            for i, choices in enumerate(type_choices):
                chosen = model.new_bool_var(f"type{slot}_{i}")
                model.add(kind == i).only_enforce_if(chosen)
                model.add(kind != i).only_enforce_if(chosen.Not())
                choices.append(chosen)
        else:
            model.add_allowed_assignments([w, h, a], [(0, 0, 0), *catalogue])
        present = model.new_bool_var(f"present{slot}")
        model.add(a > 0).only_enforce_if(present)
        model.add(a == 0).only_enforce_if(present.Not())
        x = model.new_int_var(0, length, f"x{slot}")
        y = model.new_int_var(0, width, f"y{slot}")
        xe = model.new_int_var(0, length, f"xe{slot}")
        ye = model.new_int_var(0, width, f"ye{slot}")
        xs.append(model.new_optional_interval_var(x, w, xe, present, f"ix{slot}"))
        ys.append(model.new_optional_interval_var(y, h, ye, present, f"iy{slot}"))
        heights.append(h)
        widths.append(w)
        areas.append(a)
        rectangles.append((x, y, w, h, a))
        if slot:
            model.add(areas[slot - 1] >= a)
    model.add_no_overlap_2d(xs, ys)
    if finite_inventory:
        for choices, count in zip(type_choices, inventory.values()):
            model.add(sum(choices) <= count)
    model.add_cumulative(xs, heights, width)
    model.add_cumulative(ys, widths, length)
    model.add(sum(areas) <= area)
    model.maximize(sum(areas))
    solver = _cp_solver(seconds)
    status = solver.solve(model)
    if status == cp_model.MODEL_INVALID:
        raise RuntimeError(model.validate())
    if status == cp_model.INFEASIBLE:
        raise RuntimeError("Empty horizontal packing must be feasible")
    # UNKNOWN may have no reliable incumbent/bound; fall back to geometric area.
    usable = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    upper = min(area, ceil(solver.best_objective_bound)) if usable else area
    return {"upper_bound": _ratio(Fraction(upper, area)), "status": solver.status_name(status),
            "area_upper_mm2": upper * unit**2,
            "area_incumbent_mm2": round(solver.objective_value) * unit**2 if usable else None,
            "optimal": status == cp_model.OPTIMAL, "coordinate_unit_mm": unit,
            "n_footprints": len(footprints), "max_rectangles": slots,
            "slice_witness": [{"x_mm": solver.value(x) * unit, "y_mm": solver.value(y) * unit,
                               "length_mm": solver.value(w) * unit, "width_mm": solver.value(h) * unit}
                              for x, y, w, h, a in rectangles if usable and solver.value(a)],
            "inventory_relaxed": not finite_inventory, "seconds": solver.wall_time}


def integer_dff_bound(system: DFFSystem, lp: dict, *, seconds=30.0) -> dict:
    """Integer orientation counts with a valid subset of DFF rows.

    Only rows active in the LP dual are needed for this extra relaxation;
    dropping all other rows can weaken but cannot invalidate its upper bound.
    """
    model = cp_model.CpModel()
    variables = [model.new_int_var(0, system.counts[i], f"n{j}")
                 for j, i in enumerate(system.owners)]
    for i, count in enumerate(system.counts):
        model.add(sum(v for v, owner in zip(variables, system.owners) if owner == i) <= count)
    row_ids = [r["row"] for r in lp["certificate"]["active_rows"] if r["row"] < len(system.rows)]
    for row_id in row_ids:
        row = system.rows[row_id]
        model.add(sum(int(a) * v for a, v in zip(row, variables) if a) <= SCALE)
    volumes = [prod(system.types[i]) for i in system.owners]
    unit = reduce(gcd, volumes)
    total = sum((v // unit) * x for v, x in zip(volumes, variables))
    capacity = prod(system.envelope) // unit
    model.add(total <= capacity)
    model.maximize(total)
    solver = _cp_solver(seconds)
    status = solver.solve(model)
    if status in (cp_model.MODEL_INVALID, cp_model.INFEASIBLE):
        raise RuntimeError(f"Invalid integer relaxation: {solver.status_name(status)}")
    usable = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    upper = min(capacity, ceil(solver.best_objective_bound)) if usable else capacity
    return {"upper_bound": _ratio(Fraction(upper * unit, prod(system.envelope))),
            "status": solver.status_name(status), "n_dff_rows": len(row_ids),
            "volume_unit_mm3": unit, "seconds": solver.wall_time,
            "relaxation_incumbent_mm3": round(solver.objective_value) * unit if usable else None}


def pallet_bound(boxes: Sequence[Sequence[int]], envelope=ENVELOPE_MM,
                 *, seconds=30.0) -> dict:
    system = build_dff_system(boxes, envelope)
    minimum = solve_dff_lp(system, shared_orientations=False)
    shared = solve_dff_lp(system, shared_orientations=True)
    horizontal = horizontal_bound(boxes, envelope, seconds=seconds, finite_inventory=True)
    # Apply a conservative scale on z only, then integrate the same exact 2D
    # area bound through the transformed unit height. This is valid for every
    # DFF, with the same orientation variables and finite inventory.
    area_upper = horizontal["area_upper_mm2"]
    lifted_rows = []
    for f in dff_grid():
        lifted_rows.append([(Fraction(o[0] * o[1], area_upper)
                             * f(Fraction(o[2], system.envelope[2])) * SCALE).__floor__()
                            for o in system.oriented])
    strengthened = DFFSystem(system.types, system.counts, system.oriented, system.owners,
                             np.unique(np.concatenate((system.rows, lifted_rows)), axis=0),
                             system.min_rows, system.axis_function_counts, system.envelope)
    lifted = solve_dff_lp(strengthened, shared_orientations=True)
    integer = integer_dff_bound(strengthened, lifted, seconds=seconds)
    bounds = {"dff_min_orientation_lp": minimum, "dff_shared_orientation_lp": shared,
              "horizontal_cuts": horizontal, "dff_horizontal_lp": lifted, "dff_integer": integer}
    best = min(bounds, key=lambda k: Fraction(bounds[k]["upper_bound"]["numerator"],
                                             bounds[k]["upper_bound"]["denominator"]))
    return {"n_boxes": len(boxes), "n_types": len(system.types),
            "n_dffs": len(dff_grid()), "unique_axis_functions": system.axis_function_counts,
            "coefficient_scale": SCALE, "bounds": bounds, "best_method": best,
            "upper_bound": bounds[best]["upper_bound"],
            "inventory": [{"dimensions_mm": d, "count": n} for d, n in zip(system.types, system.counts)]}
