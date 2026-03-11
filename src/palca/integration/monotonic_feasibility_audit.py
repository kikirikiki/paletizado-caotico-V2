from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import csv
import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from sim.des import Arrival
from sim.io import load_arrivals

from ..domain.box import Box
from ..domain.pallet_spec import PalletSpec
from ..packer.controls import BalanceConfig, ControlConfig, LoadBearConfig, StabilityConfig
from ..packer.pallet_model import PalletModel
from ..tuning.episodes import apply_shuffle


CANONICAL_BASELINE_PROCESSED_BOXES: dict[int, int] = {
    50021: 21,
    50022: 22,
    50023: 22,
    50024: 21,
    50025: 21,
}


@dataclass(frozen=True, slots=True)
class MonotonicAuditSearchConfig:
    beam_width: int = 128
    expansion_topk: int = 18
    max_nodes: int = 20_000
    max_time_s: float = 45.0
    export_best_plan: bool = False
    max_processed_target_override: int | None = None
    enforce_monotonic: bool = True


@dataclass(frozen=True, slots=True)
class SearchStep:
    step_index: int
    episode_index: int
    queue_index: int
    box_id: int | str
    z_mm: int
    layer_id: int
    x_mm: int
    y_mm: int
    length_mm: int
    width_mm: int
    height_mm: int
    orientation_name: str | None
    orientation_family: str | None
    height_after_mm: int


@dataclass(frozen=True, slots=True)
class EpisodeSearchResult:
    processed_boxes: int
    layers_used: int
    height_mm: int
    search_nodes: int
    search_time_s: float
    steps: tuple[SearchStep, ...]


@dataclass(frozen=True, slots=True)
class SeedAuditResult:
    seed: int
    baseline_processed_boxes: int | None
    best_monotonic_processed_boxes_found: int
    monotonic_solution_exists_ge_21: bool
    monotonic_solution_exists_ge_22: bool
    best_monotonic_layers_used: int
    best_monotonic_height_mm: int
    search_nodes: int
    search_time_s: float
    best_gap_vs_baseline: int | None
    best_plan_json: str | None


@dataclass(frozen=True, slots=True)
class _ActionCandidate:
    queue_index: int
    episode_index: int
    preview: Any
    score: tuple[float, float, float, float, float, float]


@dataclass(slots=True)
class _BeamState:
    pallet: PalletModel
    queue_episode_indices: tuple[int, ...]
    next_episode_index: int
    processed_boxes: int
    max_seen_z_mm: int
    steps: tuple[SearchStep, ...]


def _load_profile(profile_path: str | Path) -> dict[str, Any]:
    path = Path(profile_path).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Perfil invalido: se esperaba objeto JSON.")
    missing = [k for k in ("excel", "params", "seeds") if k not in payload]
    if missing:
        raise ValueError(f"Perfil invalido: faltan claves {missing}.")
    params = payload.get("params")
    if not isinstance(params, dict):
        raise ValueError("Perfil invalido: 'params' debe ser objeto.")
    seeds = payload.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        raise ValueError("Perfil invalido: 'seeds' debe ser lista no vacia.")
    return {
        "profile_path": str(path),
        "profile_name": str(payload.get("profile_name", "unknown_profile")),
        "excel": str(payload["excel"]),
        "params": deepcopy(params),
        "seeds": [int(seed) for seed in seeds],
    }


def _priority_col_for_mode(priority_mode: str) -> str | None:
    mode = str(priority_mode or "none").strip().lower()
    if mode.startswith("excel"):
        parts = mode.split(":", 1)
        if len(parts) == 2 and parts[1].strip():
            return parts[1].strip()
    return None


def _apply_arrival_transforms(
    *,
    arrivals: Sequence[Arrival],
    params: Mapping[str, Any],
    seed: int,
) -> list[Arrival]:
    time_scale = float(params.get("time_scale", 1.0) or 1.0)
    out: list[Arrival] = list(arrivals)
    if time_scale != 1.0:
        out = [
            Arrival(
                time=float(a.time) / float(time_scale),
                destination=int(a.destination),
                row_idx=int(a.row_idx),
                length_mm=a.length_mm,
                width_mm=a.width_mm,
                height_mm=a.height_mm,
                weight_kg=a.weight_kg,
                priority=a.priority,
            )
            for a in out
        ]

    forced_destination = params.get("force_destination")
    if forced_destination is not None:
        forced_dest = int(forced_destination)
        out = [
            Arrival(
                time=float(a.time),
                destination=forced_dest,
                row_idx=int(a.row_idx),
                length_mm=a.length_mm,
                width_mm=a.width_mm,
                height_mm=a.height_mm,
                weight_kg=a.weight_kg,
                priority=a.priority,
            )
            for a in out
        ]

    shuffle_window = int(params.get("shuffle_window", 0) or 0)
    shuffle_strength = float(params.get("shuffle_strength", 0.0) or 0.0)
    if shuffle_window > 1 and shuffle_strength > 0.0:
        ordered = sorted(out, key=lambda item: int(item.row_idx))
        shuffled = apply_shuffle(
            ordered,
            seed=int(seed),
            window=shuffle_window,
            strength=shuffle_strength,
        )
        out = [
            Arrival(
                time=float(a.time),
                destination=int(a.destination),
                row_idx=idx,
                length_mm=a.length_mm,
                width_mm=a.width_mm,
                height_mm=a.height_mm,
                weight_kg=a.weight_kg,
                priority=a.priority,
            )
            for idx, a in enumerate(shuffled, start=1)
        ]

    arrival_mode = str(params.get("arrival_mode", "excel")).strip().lower()
    if arrival_mode == "excel":
        out = sorted(out, key=lambda item: (float(item.time), int(item.row_idx)))
    else:
        out = sorted(out, key=lambda item: int(item.row_idx))
    return out


def build_episode_boxes_for_seed(
    *,
    excel_path: str | Path,
    params: Mapping[str, Any],
    seed: int,
) -> list[Box]:
    priority_mode = str(params.get("priority_mode", "none") or "none")
    weight_col = params.get("weight_col")
    priority_col = _priority_col_for_mode(priority_mode)
    arrivals = load_arrivals(
        excel_path=str(excel_path),
        weight_col=(None if weight_col is None else str(weight_col)),
        priority_col=priority_col,
    )
    transformed = _apply_arrival_transforms(arrivals=arrivals, params=params, seed=int(seed))
    default_l = 400
    default_w = 300
    default_h = 200
    mode_key = str(priority_mode).strip().lower()

    boxes: list[Box] = []
    for idx, item in enumerate(transformed, start=1):
        length_mm = int(item.length_mm if item.length_mm is not None else default_l)
        width_mm = int(item.width_mm if item.width_mm is not None else default_w)
        height_mm = int(item.height_mm if item.height_mm is not None else default_h)
        weight_kg = float(item.weight_kg) if item.weight_kg is not None else None
        if mode_key == "weight":
            if weight_kg is not None:
                priority = float(weight_kg)
            else:
                priority = float(length_mm * width_mm * height_mm) / 1_000_000.0
        elif mode_key.startswith("excel"):
            priority = float(item.priority) if item.priority is not None else None
        else:
            priority = None
        boxes.append(
            Box(
                box_id=int(idx),
                length_mm=length_mm,
                width_mm=width_mm,
                height_mm=height_mm,
                timestamp=float(item.time),
                destination=int(item.destination),
                weight_kg=weight_kg,
                loadbear=None,
                priority=priority,
            )
        )
    return boxes


def build_pallet_factory_from_params(params: Mapping[str, Any]) -> Callable[[], PalletModel]:
    control_config = ControlConfig(
        stability=StabilityConfig(
            mode=str(params.get("stability_mode", "ratio+corners")),
            min_support_ratio=float(params.get("min_support", 0.75)),
            eps_mm=float(params.get("stability_eps_mm", 1.0)),
            settle_snap_grid=bool(params.get("settle_snap_grid", False)),
            grid_mm=params.get("grid_mm"),
            settle_max_iter=int(params.get("settle_max_iter", 0) or 0),
            settle_timeout_ms=int(params.get("settle_timeout_ms", 0) or 0),
        ),
        loadbear=LoadBearConfig(
            heavy_bottom=bool(params.get("heavy_bottom", False)),
            max_overweight_ratio=float(params.get("max_overweight_ratio", 1.5)),
            penalty_weight=float(params.get("loadbear_penalty_weight", 1.0)),
            loadbear_factor=float(params.get("loadbear_factor", 1.0)),
        ),
        balance=BalanceConfig(
            balance_weight=float(params.get("balance_weight", 0.0)),
        ),
    )

    def _factory() -> PalletModel:
        return PalletModel(
            spec=PalletSpec(overhang_mm=int(params.get("overhang_mm", 0) or 0)),
            heuristic=str(params.get("heuristic", "baf")),
            control_config=control_config,
            stacking_mode=str(params.get("stacking_mode", "layers")),
            z_band_mm=params.get("z_band_mm"),
            orientation_mode=str(params.get("orientation_mode", "planar")),
            stand_hw_height_margin_gate_mm=int(params.get("stand_hw_height_margin_gate_mm", 400) or 0),
            coverage_grid_x=int(params.get("coverage_grid_x", 0) or 0),
            coverage_grid_y=int(params.get("coverage_grid_y", 0) or 0),
            coverage_weight=float(params.get("coverage_weight", 0.0) or 0.0),
            dominant_free_rect_weight=float(params.get("dominant_free_rect_weight", 0.0) or 0.0),
            dominant_free_rect_ratio_gate=float(params.get("dominant_free_rect_ratio_gate", 0.35) or 0.35),
        )

    return _factory


def _layers_used(pallet: PalletModel) -> int:
    if not pallet.placements:
        return 0
    z_values = {int(placement.z_mm) for placement in pallet.placements}
    return int(len(z_values))


def _state_rank_key(state: _BeamState) -> tuple[int, int, int, int]:
    height = int(state.pallet.current_height_mm())
    return (
        int(state.processed_boxes),
        -int(state.max_seen_z_mm),
        -int(height),
        -int(len(state.queue_episode_indices)),
    )


def _action_rank(preview: Any, box: Box) -> tuple[float, float, float, float, float, float]:
    placement = preview.placement
    z_mm = int(getattr(placement, "z_mm", 0) or 0)
    height_after = int(preview.height_after_mm if preview.height_after_mm is not None else 0)
    utility = float(preview.packing_gain) - float(preview.fragmentation) + float(preview.score_adjustment)
    volume = int(box.volume_mm3)
    return (
        float(-z_mm),
        float(-height_after),
        float(utility),
        float(volume),
        float(-int(box.length_mm)),
        float(-int(box.width_mm)),
    )


def _enumerate_candidates(
    *,
    state: _BeamState,
    episode_boxes: Sequence[Box],
    lookahead_k: int,
    preview_limits: Mapping[str, Any],
    cfg: MonotonicAuditSearchConfig,
) -> list[_ActionCandidate]:
    accessible = list(state.queue_episode_indices[: max(1, int(lookahead_k))])
    out: list[_ActionCandidate] = []
    for queue_index, episode_index in enumerate(accessible):
        box = episode_boxes[int(episode_index)]
        preview = state.pallet.preview_place(
            box,
            max_tries_per_item=preview_limits.get("max_tries_per_item"),
            max_candidates=preview_limits.get("max_candidates"),
            max_seconds_per_item=preview_limits.get("max_seconds_per_item"),
        )
        if not preview.feasible or preview.placement is None:
            continue
        z_mm = int(preview.placement.z_mm)
        if cfg.enforce_monotonic and z_mm < int(state.max_seen_z_mm):
            continue
        out.append(
            _ActionCandidate(
                queue_index=int(queue_index),
                episode_index=int(episode_index),
                preview=preview,
                score=_action_rank(preview, box),
            )
        )

    out.sort(key=lambda item: item.score, reverse=True)
    limit = max(1, int(cfg.expansion_topk))
    if len(out) > limit:
        return out[:limit]
    return out


def _apply_candidate(
    *,
    state: _BeamState,
    episode_boxes: Sequence[Box],
    candidate: _ActionCandidate,
) -> _BeamState:
    next_pallet = deepcopy(state.pallet)
    committed = next_pallet.commit_place(candidate.preview)
    next_queue = list(state.queue_episode_indices)
    next_queue.pop(int(candidate.queue_index))
    next_episode_index = int(state.next_episode_index)
    if next_episode_index < len(episode_boxes):
        next_queue.append(int(next_episode_index))
        next_episode_index += 1

    new_max_z = max(int(state.max_seen_z_mm), int(committed.z_mm))
    new_step = SearchStep(
        step_index=int(len(state.steps)),
        episode_index=int(candidate.episode_index),
        queue_index=int(candidate.queue_index),
        box_id=committed.box_id if committed.box_id is not None else candidate.episode_index,
        z_mm=int(committed.z_mm),
        layer_id=int(committed.layer_id),
        x_mm=int(committed.x_mm),
        y_mm=int(committed.y_mm),
        length_mm=int(committed.length_mm),
        width_mm=int(committed.width_mm),
        height_mm=int(committed.height_mm),
        orientation_name=committed.orientation_name,
        orientation_family=committed.orientation_family,
        height_after_mm=int(next_pallet.current_height_mm()),
    )
    return _BeamState(
        pallet=next_pallet,
        queue_episode_indices=tuple(next_queue),
        next_episode_index=int(next_episode_index),
        processed_boxes=int(state.processed_boxes + 1),
        max_seen_z_mm=int(new_max_z),
        steps=tuple([*state.steps, new_step]),
    )


def run_episode_beam_search(
    *,
    episode_boxes: Sequence[Box],
    pallet_factory: Callable[[], PalletModel],
    ramp_capacity: int,
    lookahead_k: int,
    max_processed_target: int,
    preview_limits: Mapping[str, Any] | None = None,
    cfg: MonotonicAuditSearchConfig | None = None,
) -> EpisodeSearchResult:
    config = cfg or MonotonicAuditSearchConfig()
    limits = dict(preview_limits or {})
    episode = list(episode_boxes)
    if not episode:
        return EpisodeSearchResult(
            processed_boxes=0,
            layers_used=0,
            height_mm=0,
            search_nodes=0,
            search_time_s=0.0,
            steps=tuple(),
        )

    initial_queue = tuple(range(0, min(len(episode), max(1, int(ramp_capacity)))))
    root = _BeamState(
        pallet=pallet_factory(),
        queue_episode_indices=initial_queue,
        next_episode_index=len(initial_queue),
        processed_boxes=0,
        max_seen_z_mm=0,
        steps=tuple(),
    )
    beam: list[_BeamState] = [root]
    best = root

    started_at = time.perf_counter()
    node_counter = 0
    beam_width = max(1, int(config.beam_width))
    target = max(1, int(max_processed_target))
    node_budget = max(1, int(config.max_nodes))
    time_budget_s = max(0.01, float(config.max_time_s))

    while beam:
        if best.processed_boxes >= target:
            break
        if node_counter >= node_budget:
            break
        if (time.perf_counter() - started_at) >= time_budget_s:
            break

        next_beam: list[_BeamState] = []
        for state in beam:
            if state.processed_boxes > best.processed_boxes:
                best = state
            if state.processed_boxes >= target:
                best = state
                continue
            candidates = _enumerate_candidates(
                state=state,
                episode_boxes=episode,
                lookahead_k=lookahead_k,
                preview_limits=limits,
                cfg=config,
            )
            node_counter += 1
            if not candidates:
                continue
            for candidate in candidates:
                next_state = _apply_candidate(
                    state=state,
                    episode_boxes=episode,
                    candidate=candidate,
                )
                if next_state.processed_boxes > best.processed_boxes:
                    best = next_state
                next_beam.append(next_state)
            if node_counter >= node_budget:
                break
            if (time.perf_counter() - started_at) >= time_budget_s:
                break

        if not next_beam:
            break
        next_beam.sort(key=_state_rank_key, reverse=True)
        beam = next_beam[:beam_width]

    elapsed_s = time.perf_counter() - started_at
    return EpisodeSearchResult(
        processed_boxes=int(best.processed_boxes),
        layers_used=int(_layers_used(best.pallet)),
        height_mm=int(best.pallet.current_height_mm()),
        search_nodes=int(node_counter),
        search_time_s=float(elapsed_s),
        steps=tuple(best.steps),
    )


def _write_best_plan(path: Path, *, seed: int, result: EpisodeSearchResult) -> str:
    payload = {
        "schema_version": 1,
        "seed": int(seed),
        "processed_boxes": int(result.processed_boxes),
        "layers_used": int(result.layers_used),
        "height_mm": int(result.height_mm),
        "search_nodes": int(result.search_nodes),
        "search_time_s": float(result.search_time_s),
        "steps": [asdict(step) for step in result.steps],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return str(path)


def run_seed_audit(
    *,
    seed: int,
    excel_path: str | Path,
    params: Mapping[str, Any],
    baseline_processed_boxes: int | None,
    search_config: MonotonicAuditSearchConfig | None = None,
    seed_output_dir: str | Path | None = None,
) -> SeedAuditResult:
    cfg = search_config or MonotonicAuditSearchConfig()
    episode_boxes = build_episode_boxes_for_seed(
        excel_path=excel_path,
        params=params,
        seed=int(seed),
    )
    pallet_factory = build_pallet_factory_from_params(params)
    ramp_capacity = max(1, int(params.get("ramp_cap", 15) or 15))
    lookahead_k = max(1, int(params.get("lookahead_k", ramp_capacity) or ramp_capacity))
    max_processed_target = (
        int(cfg.max_processed_target_override)
        if cfg.max_processed_target_override is not None
        else max(1, int(params.get("n_per_pallet", 24) or 24))
    )
    preview_limits = {
        "max_tries_per_item": int(params.get("max_tries_per_item", 0) or 0),
        "max_candidates": int(params.get("max_candidates", 0) or 0),
        "max_seconds_per_item": float(params.get("max_seconds_per_item", 0.0) or 0.0),
    }
    episode_result = run_episode_beam_search(
        episode_boxes=episode_boxes,
        pallet_factory=pallet_factory,
        ramp_capacity=ramp_capacity,
        lookahead_k=lookahead_k,
        max_processed_target=max_processed_target,
        preview_limits=preview_limits,
        cfg=cfg,
    )

    best_plan_json: str | None = None
    if cfg.export_best_plan and seed_output_dir is not None:
        best_plan_path = Path(seed_output_dir) / f"seed_{int(seed)}_best_monotonic_plan.json"
        best_plan_json = _write_best_plan(best_plan_path, seed=int(seed), result=episode_result)

    best_processed = int(episode_result.processed_boxes)
    gap = None
    if baseline_processed_boxes is not None:
        gap = int(best_processed - int(baseline_processed_boxes))

    return SeedAuditResult(
        seed=int(seed),
        baseline_processed_boxes=(
            None if baseline_processed_boxes is None else int(baseline_processed_boxes)
        ),
        best_monotonic_processed_boxes_found=best_processed,
        monotonic_solution_exists_ge_21=bool(best_processed >= 21),
        monotonic_solution_exists_ge_22=bool(best_processed >= 22),
        best_monotonic_layers_used=int(episode_result.layers_used),
        best_monotonic_height_mm=int(episode_result.height_mm),
        search_nodes=int(episode_result.search_nodes),
        search_time_s=float(episode_result.search_time_s),
        best_gap_vs_baseline=gap,
        best_plan_json=best_plan_json,
    )


def _mean(values: Iterable[int | float | None]) -> float | None:
    numbers = [float(v) for v in values if v is not None]
    if not numbers:
        return None
    return float(sum(numbers) / float(len(numbers)))


def _aggregate(seed_rows: Sequence[SeedAuditResult]) -> dict[str, Any]:
    rows = list(seed_rows)
    return {
        "seed_count": int(len(rows)),
        "seeds_ge_21_count": int(sum(1 for row in rows if row.monotonic_solution_exists_ge_21)),
        "seeds_ge_22_count": int(sum(1 for row in rows if row.monotonic_solution_exists_ge_22)),
        "best_monotonic_mean": _mean([row.best_monotonic_processed_boxes_found for row in rows]),
        "gap_vs_baseline_mean": _mean([row.best_gap_vs_baseline for row in rows]),
        "search_nodes_mean": _mean([row.search_nodes for row in rows]),
        "search_time_s_mean": _mean([row.search_time_s for row in rows]),
    }


def _write_summary_csv(path: Path, rows: Sequence[SeedAuditResult]) -> None:
    fieldnames = [
        "seed",
        "baseline_processed_boxes",
        "best_monotonic_processed_boxes_found",
        "monotonic_solution_exists_ge_21",
        "monotonic_solution_exists_ge_22",
        "best_monotonic_layers_used",
        "best_monotonic_height_mm",
        "search_nodes",
        "search_time_s",
        "best_gap_vs_baseline",
        "best_plan_json",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: item.seed):
            writer.writerow(asdict(row))


def run_monotonic_feasibility_audit(
    *,
    profile_path: str | Path,
    seeds_override: Sequence[int] | None = None,
    outdir: str | Path | None = None,
    search_config: MonotonicAuditSearchConfig | None = None,
    baseline_by_seed: Mapping[int, int] | None = None,
) -> dict[str, Any]:
    cfg = search_config or MonotonicAuditSearchConfig()
    profile = _load_profile(profile_path)
    seeds = [int(seed) for seed in (list(seeds_override) if seeds_override else profile["seeds"])]
    baseline_map = dict(CANONICAL_BASELINE_PROCESSED_BOXES)
    if baseline_by_seed:
        baseline_map.update({int(k): int(v) for k, v in baseline_by_seed.items()})

    if outdir is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_root = (
            Path("out")
            / "benchmarks"
            / str(profile["profile_name"])
            / f"monotonic_feasibility_audit_{ts}"
        )
    else:
        out_root = Path(outdir).expanduser().resolve()

    rows: list[SeedAuditResult] = []
    for seed in seeds:
        row = run_seed_audit(
            seed=int(seed),
            excel_path=str(profile["excel"]),
            params=dict(profile["params"]),
            baseline_processed_boxes=baseline_map.get(int(seed)),
            search_config=cfg,
            seed_output_dir=out_root if cfg.export_best_plan else None,
        )
        rows.append(row)

    summary_csv = out_root / "summary.csv"
    summary_json = out_root / "summary.json"
    _write_summary_csv(summary_csv, rows)
    payload = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "profile_path": str(profile["profile_path"]),
        "profile_name": str(profile["profile_name"]),
        "excel": str(profile["excel"]),
        "seeds": [int(seed) for seed in seeds],
        "search_config": asdict(cfg),
        "rows": [asdict(row) for row in sorted(rows, key=lambda item: item.seed)],
        "aggregate": _aggregate(rows),
        "files": {
            "summary_csv": str(summary_csv),
            "summary_json": str(summary_json),
        },
    }
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return payload
