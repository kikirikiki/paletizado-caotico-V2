"""Part B: constructive offline witnesses, never upper-bound certificates.

The production model owns feasibility. The search owns order/branch selection.
All returned witnesses are replayed through the controls AND the frozen validator.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from math import prod
from pathlib import Path
import random
import time

import numpy as np

from palca.domain.box import Box
from palca.domain.placement import Placement, PlacementPreview
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.packer.pallet_model import PalletModel
from palca.scheduler.scheduler_v1 import SchedulerSimState
from palca.scoring.height_slack import rank_for_expansion_with_height_slack
from palca.study.metric import ENVELOPE_MM, PalletRecord, run_kpi
from palca.study.params import PRESETS, REFERENCE_EXCEL
from palca.study.validate import validate_pallet
from sim.io import load_arrivals

VOLUME = prod(ENVELOPE_MM)


def production_policy(*, tall_box_exemption=False, top_clearance_mm=0):
    """Same factory/defaults as sim.run, with full visibility and no deadline."""
    p = deepcopy(PRESETS["arm6_4orient"])
    return PolicyPackerScheduler.from_defaults(
        lookahead_k=186, time_budget_ms=0, overhang_mm=p["overhang_mm"],
        heuristic=p["heuristic"], stacking_mode=p["stacking_mode"],
        stability_mode=p["stability_mode"], min_support_ratio=p["min_support"],
        orientation_mode=p["orientation_mode"], accessibility_delta_mm=p["accessibility_delta_mm"],
        accessibility_tall_box_exemption=tall_box_exemption,
        accessibility_top_clearance_mm=top_clearance_mm,
        stand_hw_height_margin_gate_mm=p["stand_hw_height_margin_gate_mm"],
        score_mode=p["score_mode"], height_slack_mm=p["height_slack_mm"],
    )


def load_boxes(path=REFERENCE_EXCEL):
    # Stable offline IDs refer to the original input order, BEFORE any permutation.
    return tuple(Box(i, a.length_mm, a.width_mm, a.height_mm, 0.0,
                     destination=1, weight_kg=a.weight_kg)
                 for i, a in enumerate(load_arrivals(path), 1))


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def fingerprint(boxes, config):
    digest = hashlib.sha256()
    digest.update(json.dumps([asdict(b) for b in boxes], sort_keys=True).encode())
    digest.update(json.dumps(config, sort_keys=True).encode())
    # Includes feasibility, scorer, metric, validator and search code, not git status.
    root = Path(__file__).resolve().parents[2]
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def checked_preview(pallet, box, placement):
    """Replay a supplied placement with gate, dimensions, bounds and ALL controls.

    commit_place itself trusts previews; it is never a feasibility test.
    """
    if placement.box_id != box.box_id:
        raise ValueError("Placement identity differs from input")
    variants = pallet._orientations(box.length_mm, box.width_mm, box.height_mm)
    if not any((v.length_mm, v.width_mm, v.height_mm, v.name, v.family) ==
               (placement.length_mm, placement.width_mm, placement.height_mm,
                placement.orientation_name, placement.orientation_family) for v in variants):
        raise ValueError("Orientation/gate violation")
    offset = pallet.spec.offset_mm
    if (placement.x_mm < offset or placement.y_mm < offset or placement.z_mm < 0
            or placement.x_mm + placement.length_mm > offset + pallet.spec.bin_length_mm
            or placement.y_mm + placement.width_mm > offset + pallet.spec.bin_width_mm
            or placement.z_mm + placement.height_mm > pallet.spec.max_height_mm):
        raise ValueError("Envelope violation")
    if not pallet.controls.manifest.is_eligible(box, pallet):
        raise ValueError("Manifest violation")
    if pallet._height_under_footprint_mm(placement.x_mm, placement.y_mm,
                                       placement.length_mm, placement.width_mm) != placement.z_mm:
        raise ValueError("Heightfield/collision violation")
    adjusted = placement
    delta = 0.0
    for control in pallet.controls.placement_controls:
        result = control.evaluate(pallet=pallet, box=box, placement=adjusted)
        if not result.feasible:
            raise ValueError(f"Production control: {result.reason}")
        adjusted = result.placement
        delta += result.score_delta
    if adjusted != placement:
        raise ValueError("Replay placement changed during settling")
    return PlacementPreview(True, adjusted, 0.0, 0.0, score_adjustment=delta,
                            height_after_mm=max(pallet.current_height_mm(),
                                                adjusted.z_mm + adjusted.height_mm))


def summarize(pallets, boxes, *, tall_box_exemption=False, top_clearance_mm=0):
    """Reject missing/duplicate IDs, replay sequence, validate, then frozen KPI."""
    originals = {b.box_id: (b.length_mm, b.width_mm, b.height_mm) for b in boxes}
    by_id = {b.box_id: b for b in boxes}
    seen = []
    records = []
    policy = production_policy(tall_box_exemption=tall_box_exemption, top_clearance_mm=top_clearance_mm)
    for i, items in enumerate(pallets, 1):
        if not items:
            raise ValueError("Empty pallet")
        model = policy._new_pallet()
        for item in items:
            placement = Placement(**item)
            if placement.box_id not in by_id:
                raise ValueError("Unknown box")
            preview = checked_preview(model, by_id[placement.box_id], placement)
            model.commit_place(preview)
            seen.append(placement.box_id)
        violations = validate_pallet(items, originals, min_support=PRESETS["arm6_4orient"]["min_support"],
                                     run_params=asdict(policy.config))
        if violations:
            raise ValueError(f"Frozen validation failed: {violations}")
        closed = i < len(pallets)
        records.append(PalletRecord("1", i, len(items),
                                    sum(prod(originals[p["box_id"]]) for p in items),
                                    model.current_height_mm(), "SEARCH_EXHAUSTED" if closed else "END",
                                    closed, items))
    if len(seen) != len(set(seen)) or set(seen) != set(by_id):
        raise ValueError("Inventory not conserved exactly once")
    result = run_kpi(records)
    result.update(n_violations=0, n_production_violations=0, n_unplaced=0,
                  sequence=seen, pallets=pallets,
                  profile=[profile_metrics([Placement(**p) for p in items]) for items in pallets])
    return result


def quality(result):
    # Primary specification objective: official closed-pallet KPI. Total pallets
    # and all-pallet utilization are always reported, never hidden by END.
    return (result["kpi"] if result["kpi"] is not None else 0.0,
            -result["n_pallets_total"])


def decode_order(boxes, order, *, tall_box_exemption=False, top_clearance_mm=0):
    """The actual production SchedulerV1, full remaining buffer, unlimited time."""
    if sorted(order) != sorted(b.box_id for b in boxes):
        raise ValueError("Order must be a permutation of the full input")
    policy = production_policy(tall_box_exemption=tall_box_exemption, top_clearance_mm=top_clearance_mm)
    scheduler = policy._scheduler
    scheduler.config = replace(scheduler.config, lookahead_k=max(1, len(boxes)))
    lookup = {b.box_id: b for b in boxes}
    remaining = [lookup[i] for i in order]
    pallets = []
    pallet = policy._new_pallet()
    while remaining:
        plan = scheduler.choose_action(SchedulerSimState(0, {1: remaining}, {1: pallet}, set()))
        if plan is None:
            if not pallet.placements:
                raise ValueError("Input box cannot fit an empty production pallet")
            pallets.append([asdict(p) for p in pallet.placements])
            pallet = policy._new_pallet()
            continue
        pallet.commit_place(plan.preview)
        remaining.pop(plan.buffer_index)
    if pallet.placements:
        pallets.append([asdict(p) for p in pallet.placements])
    return summarize(pallets, boxes, tall_box_exemption=tall_box_exemption, top_clearance_mm=top_clearance_mm)


def production_candidates(pallet, remaining, scheduler):
    candidates = []
    seen = set()
    for idx, box in enumerate(remaining):
        key = (box.length_mm, box.width_mm, box.height_mm, box.weight_kg, box.loadbear)
        if key in seen:
            continue
        seen.add(key)
        preview = pallet.preview_place(box)
        if preview.feasible:
            terms = scheduler._score_candidate(now=0, box=box, idx=idx, preview=preview,
                                               max_priority=0, height_after_mm=preview.height_after_mm)
            candidates.append((idx, box, preview, terms))
    return rank_for_expansion_with_height_slack(
        candidates, scheduler.config.score_mode, scheduler.config.height_slack_mm,
        lambda c: c[3].height_after_mm,
        lambda c: scheduler._gain_frag_sort_key(c[3], c[1]))


def geometric_candidates(pallet, remaining):
    """Alternative locations, same production orientation gate/heightfield/controls.

    Candidate sampling is a search restriction, not a physical relaxation.
    No altered control stack or runtime global override is used.
    """
    seen = set()
    for idx, box in enumerate(remaining):
        key = (box.length_mm, box.width_mm, box.height_mm, box.weight_kg, box.loadbear)
        if key in seen:
            continue
        seen.add(key)
        for v in pallet._orientations(box.length_mm, box.width_mm, box.height_mm):
            for x, y in pallet._heightfield_xy_candidates(v.length_mm, v.width_mm, cap=120):
                x += pallet.spec.offset_mm
                y += pallet.spec.offset_mm
                z = pallet._height_under_footprint_mm(x, y, v.length_mm, v.width_mm)
                if z + v.height_mm > pallet.spec.max_height_mm:
                    continue
                p = Placement(x, y, z, v.rot90, 0, v.length_mm, v.width_mm, v.height_mm,
                              box.box_id, box.effective_weight_kg(), box.loadbear, box.priority,
                              v.name, v.family)
                try:
                    preview = checked_preview(pallet, box, p)
                except ValueError:
                    continue
                yield idx, box, preview


def profile_metrics(placements, grid_mm=20):
    """20 mm centre-sampled diagnostic, NEVER a feasibility oracle.

    Debt integrates max(neighbour height - 400 - local height, 0) over cells.
    A 200 mm neighbourhood approximates the footprint needed to fill a low zone.
    It is a heuristic proxy, not a proof that the whole column is unreachable.
    """
    xs = np.arange(grid_mm / 2, ENVELOPE_MM[0], grid_mm) - 20
    ys = np.arange(grid_mm / 2, ENVELOPE_MM[1], grid_mm) - 20
    heights = np.zeros((len(xs), len(ys)), dtype=np.int32)
    volume = 0
    for p in placements:
        mask = ((xs[:, None] >= p.x_mm) & (xs[:, None] < p.x_mm + p.length_mm)
                & (ys[None, :] >= p.y_mm) & (ys[None, :] < p.y_mm + p.width_mm))
        heights[mask] = np.maximum(heights[mask], p.z_mm + p.height_mm)
        volume += p.length_mm * p.width_mm * p.height_mm
    high = heights.copy()
    radius = 200 // grid_mm
    # Separable square maximum filter without an optional scipy dependency.
    for axis in (0, 1):
        padded = np.pad(high, [(radius, radius) if i == axis else (0, 0) for i in (0, 1)], mode="edge")
        high = np.lib.stride_tricks.sliding_window_view(padded, 2 * radius + 1, axis=axis).max(axis=-1)
    area = grid_mm * grid_mm
    profile = int(heights.sum()) * area
    peak = max((p.z_mm + p.height_mm for p in placements), default=0)
    return {"debt_mm3": int(np.maximum(high - 400 - heights, 0).sum()) * area,
            "internal_void_mm3": max(0, profile - volume),
            "headroom_below_peak_mm3": max(0, ENVELOPE_MM[0] * ENVELOPE_MM[1] * peak - profile),
            "volume_mm3": volume, "z_max_mm": peak,
            "profile_volume_mm3": profile, "grid_mm": grid_mm}


def state_value(placements, objective, debt_weight=1.0, roughness_weight=0.2):
    volume = sum(p.length_mm * p.width_mm * p.height_mm for p in placements)
    if objective == "volume":
        return volume
    m = profile_metrics(placements)
    return (volume - debt_weight * m["debt_mm3"] - m["internal_void_mm3"]
            - roughness_weight * m["headroom_below_peak_mm3"])


@dataclass
class Node:
    pallet: PalletModel
    remaining: tuple


def node_dump(node):
    return {"placements": [asdict(p) for p in node.pallet.placements],
            "remaining": [b.box_id for b in node.remaining]}


def node_load(raw, policy, lookup):
    pallet = policy._new_pallet()
    for item in raw["placements"]:
        p = Placement(**item)
        pallet.commit_place(checked_preview(pallet, lookup[p.box_id], p))
    return Node(pallet, tuple(lookup[i] for i in raw["remaining"]))


def node_key(node):
    # Geometry+inventory, ignoring commutations of independent placements.
    return (tuple(sorted((p.box_id, p.x_mm, p.y_mm, p.z_mm, p.length_mm, p.width_mm,
                          p.height_mm) for p in node.pallet.placements)),
            tuple(b.box_id for b in node.remaining))


def greedy_finish(node, scheduler):
    while node.remaining:
        candidates = production_candidates(node.pallet, node.remaining, scheduler)
        if not candidates:
            break
        idx, _, preview, _ = candidates[0]
        node.pallet.commit_place(preview)
        node.remaining = node.remaining[:idx] + node.remaining[idx + 1:]
    return node


def beam_search(boxes, *, width=16, top_m=8, objective="volume", locations="production",
                debt_weight=1.0, roughness_weight=0.2, cpu_seconds=1800, checkpoint=None,
                resume=False, progress=None, tall_box_exemption=False, top_clearance_mm=0):
    if width < 16 or top_m < 1:
        raise ValueError("Beam width must be >=16 and top_m >=1")
    if objective not in ("volume", "debt") or locations not in ("production", "all"):
        raise ValueError("Unknown beam objective/locations")
    access = dict(tall_box_exemption=tall_box_exemption, top_clearance_mm=top_clearance_mm)
    policy = production_policy(**access)
    lookup = {b.box_id: b for b in boxes}
    config = dict(width=width, top_m=top_m, objective=objective, locations=locations,
                  debt_weight=debt_weight, roughness_weight=roughness_weight, **access)
    identity = fingerprint(boxes, config)
    completed, history, elapsed_cpu, elapsed_wall = [], [], 0.0, 0.0
    beam, terminal = [Node(policy._new_pallet(), tuple(boxes))], None
    if resume and checkpoint and Path(checkpoint).exists():
        raw = json.loads(Path(checkpoint).read_text())
        if raw["fingerprint"] != identity:
            raise ValueError("Checkpoint does not match code/input/config")
        completed, history = raw["completed"], raw["history"]
        beam = [node_load(n, policy, lookup) for n in raw["beam"]]
        terminal = node_load(raw["terminal"], policy, lookup) if raw["terminal"] else None
        elapsed_cpu, elapsed_wall = raw["cpu_seconds"], raw["wall_seconds"]
    start_cpu, start_wall = time.process_time(), time.monotonic()

    def value(n):
        return state_value(n.pallet.placements, objective, debt_weight, roughness_weight)

    def save():
        if checkpoint:
            atomic_json(checkpoint, dict(fingerprint=identity, config=config, completed=completed,
                        history=history, beam=[node_dump(n) for n in beam],
                        terminal=node_dump(terminal) if terminal else None,
                        cpu_seconds=elapsed_cpu + time.process_time() - start_cpu,
                        wall_seconds=elapsed_wall + time.monotonic() - start_wall))

    budget_hit = False
    while beam:
        children = []
        exhausted = elapsed_cpu + time.process_time() - start_cpu >= cpu_seconds
        if exhausted:
            budget_hit = True
            # Keep a complete feasible incumbent, not an artificially closed partial.
            candidates = [greedy_finish(deepcopy(max(beam, key=value)), policy._scheduler)]
            if terminal:
                candidates.append(terminal)
            terminal = max(candidates, key=lambda n: state_value(n.pallet.placements, "volume"))
        else:
            for node in beam:
                if locations == "production":
                    actions = [(i, b, p) for i, b, p, _ in
                               production_candidates(node.pallet, node.remaining, policy._scheduler)][:top_m]
                else:
                    actions = list(geometric_candidates(node.pallet, node.remaining))
                    actions.sort(key=lambda c: state_value([*node.pallet.placements, c[2].placement],
                                                          objective, debt_weight, roughness_weight), reverse=True)
                    actions = actions[:top_m]
                if not actions:
                    if terminal is None or state_value(node.pallet.placements, "volume") > state_value(terminal.pallet.placements, "volume"):
                        terminal = node
                for idx, box, preview in actions:
                    pallet = deepcopy(node.pallet)
                    pallet.commit_place(preview)
                    children.append(Node(pallet, node.remaining[:idx] + node.remaining[idx + 1:]))
        if children:
            unique = {}
            for node in children:
                unique.setdefault(node_key(node), node)
            beam = sorted(unique.values(), key=value, reverse=True)[:width]
        else:
            if terminal is None or not terminal.pallet.placements:
                raise ValueError("No feasible nonempty terminal pallet")
            completed.append([asdict(p) for p in terminal.pallet.placements])
            history.append(dict(pallet=len(completed), n_boxes=len(terminal.pallet.placements),
                                utilization=state_value(terminal.pallet.placements, "volume") / VOLUME,
                                budget_fallback=exhausted))
            if progress:
                progress(history[-1])
            beam = [Node(policy._new_pallet(), terminal.remaining)] if terminal.remaining else []
            terminal = None
        save()
    result = summarize(completed, boxes, **access)
    result.update(config=config, history=history, budget_hit=budget_hit,
                  cpu_seconds=elapsed_cpu + time.process_time() - start_cpu,
                  wall_seconds=elapsed_wall + time.monotonic() - start_wall)
    return result


def order_search(boxes, *, seed=0, evaluations=12, cpu_seconds=1800,
                 checkpoint=None, resume=False, progress=None,
                 tall_box_exemption=False, top_clearance_mm=0):
    """Local permutation search with random restarts, actual production decoder."""
    if evaluations < 1 or not boxes:
        raise ValueError("Need a nonempty input and at least one evaluation")
    rng = random.Random(seed)
    order = [b.box_id for b in boxes]
    if seed:
        rng.shuffle(order)
    access = dict(tall_box_exemption=tall_box_exemption, top_clearance_mm=top_clearance_mm)
    config = dict(seed=seed, **access)
    identity = fingerprint(boxes, dict(method="order", **config))
    best, history, elapsed_cpu, elapsed_wall = None, [], 0.0, 0.0
    if resume and checkpoint and Path(checkpoint).exists():
        raw = json.loads(Path(checkpoint).read_text())
        if raw["fingerprint"] != identity:
            raise ValueError("Checkpoint does not match code/input/config")
        best, history, order = raw["best"], raw["history"], raw["order"]
        def tuples(x):
            return tuple(tuples(v) for v in x) if isinstance(x, list) else x
        rng.setstate(tuples(raw["rng"]))
        elapsed_cpu, elapsed_wall = raw["cpu_seconds"], raw["wall_seconds"]
    start_cpu, start_wall = time.process_time(), time.monotonic()
    for iteration in range(len(history), evaluations):
        if best is not None and elapsed_cpu + time.process_time() - start_cpu >= cpu_seconds:
            break
        if iteration:
            if iteration % 4 == 0:
                order = [b.box_id for b in boxes]
                rng.shuffle(order)
            else:
                order = list(best["input_order"])
                # Blocks and swaps change tie priorities throughout the full buffer.
                for _ in range(max(1, len(order) // 10)):
                    a, b = rng.randrange(len(order)), rng.randrange(len(order))
                    order[a], order[b] = order[b], order[a]
        result = decode_order(boxes, order, **access)
        result["input_order"] = list(order)
        if best is None or quality(result) > quality(best):
            best = result
        history.append(dict(evaluation=iteration, kpi=result["kpi"],
                            n_pallets_total=result["n_pallets_total"],
                            sequence_sha256=hashlib.sha256(json.dumps(result["sequence"]).encode()).hexdigest(),
                            cpu_seconds=elapsed_cpu + time.process_time() - start_cpu))
        if progress:
            progress(history[-1])
        if checkpoint:
            atomic_json(checkpoint, dict(fingerprint=identity, config=config, best=best, history=history,
                        order=order, rng=rng.getstate(),
                        cpu_seconds=elapsed_cpu + time.process_time() - start_cpu,
                        wall_seconds=elapsed_wall + time.monotonic() - start_wall))
    best.update(config=config, history=history, seed=seed, evaluations=len(history),
                cpu_seconds=elapsed_cpu + time.process_time() - start_cpu,
                wall_seconds=elapsed_wall + time.monotonic() - start_wall)
    return best



# x,y,length,width in envelope coordinates, then permitted orientation family.
# Explicit 2mm separations; every centred placement still passes the real controls.
COLUMN_TEMPLATES = (
    ((0, 0, 619, 448, 'planar'), (621, 0, 619, 448, 'planar'),
     (0, 450, 449, 390, 'stand_hw'), (451, 450, 449, 390, 'stand_hw'), (902, 450, 338, 390, 'any')),
    ((0, 0, 619, 453, 'planar'), (621, 0, 619, 453, 'planar'),
     (0, 455, 454, 385, 'stand_hw'), (456, 455, 454, 385, 'stand_hw'), (912, 455, 328, 385, 'any')),
    ((0, 0, 619, 443, 'planar'), (621, 0, 619, 443, 'planar'),
     (0, 445, 454, 395, 'stand_hw'), (456, 445, 454, 395, 'stand_hw'), (912, 445, 328, 395, 'any')),
)


def column_template(template, column_gap_mm=2):
    """Floor partition with explicit gaps; zero tiles the entire envelope.

    Box dimensions may leave natural slack inside a zone. No padding is added
    to the partition when the gap is zero. The default reproduces the old zones.
    """
    if (not isinstance(column_gap_mm, int) or column_gap_mm < 0
            or template not in range(len(COLUMN_TEMPLATES))):
        raise ValueError('Invalid column template/gap')
    old = COLUMN_TEMPLATES[template]
    length, width, _ = ENVELOPE_MM
    gap = column_gap_mm
    planar_length = (length - gap) // 2
    planar_width = old[0][3]
    standing_length = old[2][2]
    standing_y = planar_width + gap
    last_x = 2 * (standing_length + gap)
    zones = (
        (0, 0, planar_length, planar_width, 'planar'),
        (planar_length + gap, 0, length - planar_length - gap, planar_width, 'planar'),
        (0, standing_y, standing_length, width - standing_y, 'stand_hw'),
        (standing_length + gap, standing_y, standing_length, width - standing_y, 'stand_hw'),
        (last_x, standing_y, length - last_x, width - standing_y, 'any'),
    )
    if any(l <= 0 or w <= 0 for _, _, l, w, _ in zones):
        raise ValueError('Column gap leaves an empty zone')
    return zones


def column_candidates(node, zone, group_geometry=False):
    x, y, length, width, family = zone
    seen = set()
    for idx, box in enumerate(node.remaining):
        dims = (box.length_mm, box.width_mm, box.height_mm)
        # Grouping by geometry is safe ONLY for this fixed preset: loadbear,
        # balance and priority are disabled. It changes beam diversity/ties.
        key = dims if group_geometry else (*dims, box.weight_kg, box.loadbear)
        if key in seen:
            continue
        seen.add(key)
        for variant in node.pallet._orientations(*dims):
            if family != 'any' and variant.family != family:
                continue
            if variant.length_mm > length or variant.width_mm > width:
                continue
            px = x + (length - variant.length_mm) // 2 + node.pallet.spec.offset_mm
            py = y + (width - variant.width_mm) // 2 + node.pallet.spec.offset_mm
            z = node.pallet._height_under_footprint_mm(px, py, variant.length_mm, variant.width_mm)
            placement = Placement(px, py, z, variant.rot90, 0, variant.length_mm, variant.width_mm,
                                  variant.height_mm, box.box_id, box.effective_weight_kg(), box.loadbear,
                                  box.priority, variant.name, variant.family)
            try:
                preview = checked_preview(node.pallet, box, placement)
            except ValueError:
                continue
            yield idx, box, preview


def column_completion_value(node, zone):
    """Fractional height-knapsack estimate for pruning, not a certified bound.

    Its purpose is to keep four ~600mm standing boxes alive against three taller
    ones, and preserve broad support surfaces. Real expansions use the controls.
    """
    x, y, length, width, family = zone
    offset = node.pallet.spec.offset_mm
    local = [p for p in node.pallet.placements
             if x + offset <= p.x_mm + p.length_mm / 2 < x + length + offset
             and y + offset <= p.y_mm + p.width_mm / 2 < y + width + offset]
    height = max((p.z_mm + p.height_mm for p in local), default=0)
    capacity = node.pallet.spec.max_height_mm - height
    options = []
    for box in node.remaining:
        l, w, h = box.length_mm, box.width_mm, box.height_mm
        variants = []
        if family in ('planar', 'any'):
            variants.extend(((l, w, h), (w, l, h)))
        if family in ('stand_hw', 'any'):
            variants.extend(((h, w, l), (w, h, l)))
        choices = [(a * b, c) for a, b, c in variants if a <= length and b <= width and c <= capacity]
        if choices:
            options.append(max(choices))
    value = state_value(node.pallet.placements, 'volume')
    for area, h in sorted(options, reverse=True):
        if capacity <= 0:
            break
        take = min(capacity, h)
        value += area * take
        capacity -= take
    return value


def column_search(boxes, *, width=16, templates=(0, 1, 2), seed=0, group_geometry=False,
                  column_gap_mm=2, cpu_seconds=600,
                  checkpoint=None, resume=False, progress=None,
                  tall_box_exemption=False, top_clearance_mm=0, column_mixed_families=False):
    """Sequential pallets, beam within each column, best of fixed floor partitions.

    The first two zones are planar unless column_mixed_families is selected.
    The standing gate is NEVER forced open: a
    standing branch exists only after a real preceding column reached >=2000mm.
    Completed pallets are checkpointed; interruption loses at most one pallet.
    """
    if width < 1 or not templates or any(t not in range(len(COLUMN_TEMPLATES)) for t in templates):
        raise ValueError('Invalid column width/templates')
    zones_by_template = {t: column_template(t, column_gap_mm) for t in templates}
    if column_mixed_families:
        zones_by_template = {t: tuple((*zone[:4], 'any') if zone[4] == 'planar' else zone
                                     for zone in zones) for t, zones in zones_by_template.items()}
    access = dict(tall_box_exemption=tall_box_exemption, top_clearance_mm=top_clearance_mm)
    config = dict(width=width, templates=list(templates), seed=seed, group_geometry=group_geometry,
                  column_gap_mm=column_gap_mm, column_mixed_families=column_mixed_families, **access)
    identity = fingerprint(boxes, dict(method='columns', **config))
    lookup = {b.box_id: b for b in boxes}
    remaining = list(boxes)
    if seed:
        random.Random(seed).shuffle(remaining)
    remaining = tuple(remaining)
    completed, history, elapsed_cpu, elapsed_wall = [], [], 0.0, 0.0
    if resume and checkpoint and Path(checkpoint).exists():
        raw = json.loads(Path(checkpoint).read_text())
        if raw['fingerprint'] != identity:
            raise ValueError('Checkpoint does not match code/input/config')
        completed, history = raw['completed'], raw['history']
        remaining = tuple(lookup[i] for i in raw['remaining'])
        elapsed_cpu, elapsed_wall = raw['cpu_seconds'], raw['wall_seconds']
    start_cpu, start_wall = time.process_time(), time.monotonic()
    policy = production_policy(**access)

    def exhausted():
        return elapsed_cpu + time.process_time() - start_cpu >= cpu_seconds

    while remaining:
        best, selected_template = None, None
        for template in templates:
            node = Node(policy._new_pallet(), remaining)
            for zone in zones_by_template[template]:
                beam, terminal = [node], node
                while beam and not exhausted():
                    children = {}
                    for parent in beam:
                        for idx, _, preview in column_candidates(parent, zone, group_geometry):
                            pallet = deepcopy(parent.pallet)
                            pallet.commit_place(preview)
                            child = Node(pallet, parent.remaining[:idx] + parent.remaining[idx + 1:])
                            children.setdefault(node_key(child), child)
                            if state_value(pallet.placements, 'volume') > state_value(terminal.pallet.placements, 'volume'):
                                terminal = child
                    beam = sorted(children.values(), key=lambda n: column_completion_value(n, zone), reverse=True)[:width]
                node = terminal
            node = greedy_finish(node, policy._scheduler)
            if best is None or state_value(node.pallet.placements, 'volume') > state_value(best.pallet.placements, 'volume'):
                best, selected_template = node, template
            if exhausted():
                break
        if not best.pallet.placements:
            raise ValueError('Input cannot fit an empty production pallet')
        remaining = best.remaining
        completed.append([asdict(p) for p in best.pallet.placements])
        history.append(dict(pallet=len(completed), template=selected_template,
                            n_boxes=len(best.pallet.placements),
                            utilization=state_value(best.pallet.placements, 'volume') / VOLUME,
                            budget_fallback=exhausted()))
        if checkpoint:
            atomic_json(checkpoint, dict(fingerprint=identity, config=config, completed=completed,
                        remaining=[b.box_id for b in remaining], history=history,
                        cpu_seconds=elapsed_cpu + time.process_time() - start_cpu,
                        wall_seconds=elapsed_wall + time.monotonic() - start_wall))
        if progress:
            progress(history[-1])
    result = summarize(completed, boxes, **access)
    result.update(config=config, history=history, budget_hit=exhausted(),
                  cpu_seconds=elapsed_cpu + time.process_time() - start_cpu,
                  wall_seconds=elapsed_wall + time.monotonic() - start_wall)
    return result
