#!/usr/bin/env python3
"""Sweeps aislados, reanudables y medidos con el KPI congelado.

Seeds adicionales: SHA256('palca-sweep-seed-v1:<índice>'), primeros 4 bytes
big-endian; se omiten colisiones con seeds anteriores. Índice inicial = 10.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import hashlib
from inspect import signature
import json
import multiprocessing
import os
from pathlib import Path
import random
import re
import shlex
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import yaml
from palca.study.metric import aggregate_seeds, paired_comparison, run_kpi, split_pallets
from palca.study.params import REFERENCE_SEEDS, preset_params
from palca.study.validate import validate_pallet
from scripts.kpi_from_run import original_dims_for_run
from sim.run import run_simulation

LOCK_PATH = Path("/tmp/palca_sweep.lock")
THREAD_ENV = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
SEED_RULE = "REFERENCE_SEEDS; extra: SHA256('palca-sweep-seed-v1:<index>')[:4], big-endian, index>=10, skip duplicates"


def safe_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError(f"Nombre inseguro: {value!r}")
    return value


def load_configs(path: Path) -> list[dict]:
    configs = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(configs, list) or not configs:
        raise ValueError("YAML debe ser una lista no vacía de configs")
    names = set()
    for config in configs:
        if not isinstance(config, dict) or set(config) - {"name", "preset", "overrides", "role"}:
            raise ValueError("Config inválida: se esperan name, preset, overrides, role")
        name = safe_name(config.get("name"))
        if name in names:
            raise ValueError(f"Config duplicada: {name}")
        names.add(name)
        if config.get("role") not in {"official", "contrast", "control", "bound"}:
            raise ValueError(f"Role inválido para {name}")
        overrides = config.setdefault("overrides", {})
        if not isinstance(overrides, dict) or any(not isinstance(k, str) for k in overrides):
            raise ValueError("overrides debe ser un mapping con claves string")
        if set(overrides) & {"out_path", "out_json", "dump_placements_path", "episode_seed"}:
            raise ValueError("Rutas de salida y episode_seed pertenecen al runner")
        params = preset_params(config.get("preset"), **overrides)
        if params.get("multi_ramp") or params.get("policy") != "palca" or not params.get("continuous_pallets"):
            raise ValueError("El estudio requiere loader Excel estándar, policy=palca y continuous_pallets")
    return configs


def select_seeds(count: int = 10, explicit: str | None = None) -> list[int]:
    if explicit is not None:
        try:
            seeds = [int(token.strip()) for token in explicit.split(",")]
        except ValueError as exc:
            raise ValueError("--seed-list requiere enteros separados por comas") from exc
        if not seeds or len(set(seeds)) != len(seeds) or any(s < 0 or s >= 2**32 for s in seeds):
            raise ValueError("Seeds deben ser únicas y estar en [0, 2**32)")
        return seeds
    if count < 1:
        raise ValueError("--seeds debe ser positivo")
    seeds = list(REFERENCE_SEEDS[:count])
    index = len(REFERENCE_SEEDS)
    while len(seeds) < count:
        candidate = int.from_bytes(hashlib.sha256(f"palca-sweep-seed-v1:{index}".encode()).digest()[:4], "big")
        index += 1
        if candidate not in seeds:
            seeds.append(candidate)
    return seeds


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def source_hash() -> str:
    files = sorted([*ROOT.joinpath("src").rglob("*.py"), *ROOT.joinpath("scripts").rglob("*.py"),
                    ROOT / "pyproject.toml", ROOT / "requirements.txt"])
    return digest({str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


@contextmanager
def sweep_lock(path: Path = LOCK_PATH):
    # Nunca borrar el inode: otro proceso podría conservar un descriptor abierto.
    with Path(path).open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"Ya hay un sweep activo: bloqueo {path} ocupado") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def make_task(config: dict, seed: int, folder: Path, head: str, code_hash: str) -> dict:
    params = {k: p.default for k, p in signature(run_simulation).parameters.items()
              if p.default is not p.empty}
    params.update(preset_params(config["preset"], **config["overrides"]))
    params["episode_seed"] = seed
    params["multi_ramp_seed"] = seed
    excel = Path(params["excel_path"])
    params["excel_path"] = str((ROOT / excel).resolve())
    directory = (folder / config["name"] / str(seed)).resolve()
    params.update(out_path=str(directory / "run.json"), dump_placements_path=str(directory / "dump.json"))
    input_hash = hashlib.sha256(Path(params["excel_path"]).read_bytes()).hexdigest()
    fingerprint = digest(dict(params=params, git_head=head, source_hash=code_hash, input_hash=input_hash))
    return dict(config=config["name"], seed=seed, params=params, directory=str(directory),
                git_head=head, source_hash=code_hash, input_hash=input_hash, task_hash=fingerprint)


def cached_task(task: dict) -> bool:
    path = Path(task["directory"]) / "kpi.json"
    try:
        cached = json.loads(path.read_text())
        return (cached["task_hash"] == task["task_hash"]
                and all((path.parent / name).is_file() for name in ("run.json", "dump.json")))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def run_task(task: dict) -> dict:
    started = perf_counter()
    import numpy as np

    random.seed(task["seed"])
    np.random.seed(task["seed"])
    directory = Path(task["directory"])
    directory.mkdir(parents=True, exist_ok=True)
    # Un fallo nunca debe dejar como válido el marcador de una ejecución anterior.
    (directory / "kpi.json").unlink(missing_ok=True)
    payload = run_simulation(**task["params"])
    dump = json.loads((directory / "dump.json").read_text())
    records = split_pallets(payload, dump)
    originals = original_dims_for_run(payload, task["params"]["excel_path"])
    violations = [dict(dest=rec.dest, index=rec.index, **asdict(violation))
                  for rec in records for violation in validate_pallet(
                      rec.items, originals, min_support=task["params"]["min_support"])]
    result = run_kpi(records)
    result.update({k: task[k] for k in ("config", "seed", "git_head", "source_hash", "input_hash", "task_hash", "params")})
    result.update(n_violations=len(violations), violations=violations,
                  decision_timing=payload["metrics"]["decision_timing"],
                  n_boxes_input=len(originals), n_unplaced=len(originals) - result["boxes_placed"],
                  deadline_cutoffs_count=payload["metrics"]["pallet_kpis"]["deadline_cutoffs_count"],
                  wall_time_s=perf_counter() - started, worker_pid=os.getpid(),
                  thread_env={key: os.environ.get(key) for key in THREAD_ENV})
    write_json(directory / "kpi.json", result)
    return result


def execute_tasks(tasks: list[dict], workers: int) -> dict:
    if workers < 1:
        raise ValueError("--workers debe ser positivo")
    pending = [task for task in tasks if not cached_task(task)]
    for key in THREAD_ENV:
        os.environ[key] = "1"
    if pending:
        # spawn evita heredar bibliotecas BLAS ya inicializadas por el padre.
        recycle = {"max_tasks_per_child": 1} if sys.version_info >= (3, 11) else {}
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"), **recycle) as pool:
            futures = {pool.submit(run_task, task): task for task in pending}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    raise RuntimeError(f"Fallo en {task['config']}/seed={task['seed']}: {exc}") from exc
                print(f"[sweep] {task['config']}/{task['seed']}: KPI={result['kpi']}, violaciones={result['n_violations']}", flush=True)
    return dict(executed=len(pending), skipped=len(tasks) - len(pending))


def stats(values: list) -> dict:
    defined = [value for value in values if value is not None]
    return aggregate_seeds(defined) if defined else dict(mean=None, sd=None, n=0, ci95=None, min=None, max=None)


def generate_summary(folder: Path) -> dict:
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text())
    grouped = {}
    for config in manifest["configs"]:
        rows = []
        for seed in manifest["seeds"]:
            row = json.loads((folder / config["name"] / str(seed) / "kpi.json").read_text())
            expected = manifest["task_hashes"][config["name"]][str(seed)]
            if row["task_hash"] != expected:
                raise ValueError(f"Resultado obsoleto: {config['name']}/{seed}")
            rows.append(row)
        grouped[config["name"]] = rows
    summary = {key: manifest[key] for key in ("command", "git_head", "source_hash", "date", "seeds", "seed_rule", "baseline")}
    summary.update(generated_at=datetime.now(timezone.utc).isoformat(), configs={}, comparisons={})
    fields = ("kpi", "kpi_nominal", "n_pallets_total", "n_closed", "n_unplaced", "n_violations", "wall_time_s", "deadline_cutoffs_count")
    for config in manifest["configs"]:
        rows = grouped[config["name"]]
        metrics = {field: stats([row[field] for row in rows]) for field in fields}
        metrics.update(role=config["role"], n_seeds=len(rows),
                       decision_mean_ms=stats([row["decision_timing"]["mean_ms"] for row in rows]),
                       decision_p95_ms=stats([row["decision_timing"]["p95_ms"] for row in rows]),
                       violations_total=sum(row["n_violations"] for row in rows),
                       violation_codes={},
                       undefined_kpi_seeds=[row["seed"] for row in rows if row["kpi"] is None])
        for row in rows:
            for violation in row["violations"]:
                code = violation["code"]
                metrics["violation_codes"][code] = metrics["violation_codes"].get(code, 0) + 1
        summary["configs"][config["name"]] = metrics
    baseline = manifest["baseline"]
    if baseline:
        for name, rows in grouped.items():
            if name == baseline:
                continue
            pairs = [(a, b) for a, b in zip(grouped[baseline], rows) if a["kpi"] is not None and b["kpi"] is not None]
            comparison = paired_comparison([a for a, _ in pairs], [b for _, b in pairs]) if pairs else None
            summary["comparisons"][name] = dict(result=comparison, paired_seeds=[a["seed"] for a, _ in pairs])
    write_json(folder / "summary.json", summary)
    (folder / "summary.md").write_text(render_summary(summary), encoding="utf-8")
    return summary


def number(value, scale=1) -> str:
    return "N/D" if value is None else f"{value * scale:.3f}"


def interval(ci, scale=1) -> str:
    return "N/D" if ci is None else f"[{number(ci[0], scale)}, {number(ci[1], scale)}]"


def render_summary(summary: dict) -> str:
    lines = ["# Sweep", "", f"Comando: `{summary['command']}`", f"Git HEAD: `{summary['git_head']}`",
             f"Código local SHA256: `{summary['source_hash']}`", f"Fecha UTC: {summary['date']}",
             f"Seeds: {', '.join(map(str, summary['seeds']))}", f"Regla seeds: {summary['seed_rule']}", "",
             "KPI congelado v1.0.0: solo palets cerrados (excluye END); media sin ponderar por palets, sd muestral e IC95 t de Student; unidad = seed.",
             "N/D indica KPI sin palets cerrados o IC no estimable. Cada estadístico usa seeds con valor definido (n en summary.json).", "",
             "| Config (role) | n KPI | KPI % ± sd (pp) | IC95 % | KPI nominal % | Palets total/cerrados | No colocadas | Violaciones (total) | ms/decisión media / p95 | Wall s/seed |",
             "|---|---:|---:|---|---:|---:|---:|---:|---:|---:|"]
    for name, row in summary["configs"].items():
        avg = lambda key: number(row[key]["mean"])
        lines.append(f"| {name} ({row['role']}) | {row['kpi']['n']} | {number(row['kpi']['mean'], 100)} ± {number(row['kpi']['sd'], 100)} | {interval(row['kpi']['ci95'], 100)} | {number(row['kpi_nominal']['mean'], 100)} | {avg('n_pallets_total')} / {avg('n_closed')} | {avg('n_unplaced')} | {avg('n_violations')} ({row['violations_total']}) | {avg('decision_mean_ms')} / {avg('decision_p95_ms')} | {avg('wall_time_s')} |")
    lines += ["", "Salvo KPI/sd/IC y totales explícitos, las columnas muestran medias por seed. p95 es la media de los percentiles p95 de cada seed.",
              "No colocadas = entradas Excel − colocadas (186 en el flujo de referencia).", "",
              f"Comparaciones pareadas: config − baseline `{summary['baseline']}`, en puntos porcentuales.", "",
              "| Config | n pares | Diferencia pp | IC95 pp | Significativo |", "|---|---:|---:|---|---|"]
    for name, entry in summary["comparisons"].items():
        result = entry["result"]
        if result:
            lines.append(f"| {name} | {result['n']} | {number(result['mean_diff_pp'])} | {interval(result['ci95_pp'])} | {'sí' if result['significant'] else 'no'} |")
        else:
            lines.append(f"| {name} | 0 | N/D | N/D | N/D |")
    lines += ["", "Validación física (sin relajar):"]
    for name, row in summary["configs"].items():
        lines.append(f"- {name}: {row['violations_total']} violaciones; códigos {json.dumps(row['violation_codes'], sort_keys=True)}; cortes por deadline: {row['deadline_cutoffs_count']['mean']:.3f}/seed.")
    lines += ["", "Las seeds y parámetros son fijos. Los límites de tiempo del simulador pueden hacer depender decisiones de la carga de CPU; los cortes se registran y el tiempo es observacional."]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs", type=Path, required=True)
    seeds = parser.add_mutually_exclusive_group()
    seeds.add_argument("--seeds", type=int, default=10)
    seeds.add_argument("--seed-list")
    parser.add_argument("--workers", type=int, default=15)
    parser.add_argument("--name", default="sweep")
    parser.add_argument("--baseline")
    return parser


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        configs = load_configs(args.configs)
        seeds = select_seeds(args.seeds, args.seed_list)
        if args.workers < 1:
            raise ValueError("--workers debe ser positivo")
        if args.baseline and args.baseline not in {config["name"] for config in configs}:
            raise ValueError("--baseline no figura en configs")
        folder = ROOT / "out" / "sweeps" / safe_name(args.name)
        with sweep_lock():
            head = subprocess.check_output(["git", "--no-pager", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            code_hash = source_hash()
            tasks = [make_task(config, seed, folder, head, code_hash) for config in configs for seed in seeds]
            manifest = dict(command=shlex.join(sys.orig_argv if argv is None else [sys.executable, "scripts/run_sweep.py", *argv]),
                            git_head=head, source_hash=code_hash, date=datetime.now(timezone.utc).isoformat(),
                            configs=configs, seeds=seeds, seed_rule=SEED_RULE, baseline=args.baseline,
                            workers=args.workers,
                            task_hashes={c["name"]: {str(t["seed"]): t["task_hash"] for t in tasks if t["config"] == c["name"]} for c in configs})
            write_json(folder / "manifest.json", manifest)
            counts = execute_tasks(tasks, args.workers)
            generate_summary(folder)
            print(f"[sweep] {counts}; resumen: {folder / 'summary.md'}", flush=True)
    except (OSError, ValueError, TypeError, RuntimeError, yaml.YAMLError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
